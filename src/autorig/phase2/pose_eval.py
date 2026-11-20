"""Numerical and visual checks for natural poses (Phase 2 refinement)."""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .skeleton import SIDES, Skeleton

STATUS_OK = "OK_PENDING_REVIEW"  # automatic checks pass; still needs human review (and Moho)
STATUS_REVIEW = "NEEDS_REVIEW"
STATUS_ARTWORK = "NEEDS_ARTWORK"
JOINT_GROUPS = {"shoulder": "upper_arm", "elbow": "forearm", "hip": "thigh", "knee": "shin"}


def union_alpha(render: dict[str, Any]) -> np.ndarray:
    return render["image"][..., 3] > 127


def evaluate(sk: Skeleton, solver, pose, info: dict[str, Any], render: dict[str, Any], preset,
             tears: dict[str, list] | None = None, rest_pieces: int = 1, resolved: dict[str, str] | None = None,
             pending_corrective: list[str] | None = None) -> dict[str, Any]:
    """``resolved``: needs_artwork items now covered by corrective artwork (item -> asset id); they leave the
    NEEDS_ARTWORK list but the pose stays NEEDS_REVIEW until that artwork is approved.
    ``pending_corrective``: corrective assets used by this pose that still await review."""
    pad = render["pad"]
    alpha = union_alpha(render)
    m: dict[str, Any] = {}
    defects: list[str] = []
    # joint limits
    m["joint_limits"] = {"violations": info["violations"], "pass": not info["violations"]}
    defects += [f"joint limit: {v}" for v in info["violations"]]
    # bone lengths (rigid bones exactly; axial scale is foreshortening, reported separately)
    errs, scaled = {}, {}
    for i, b in enumerate(sk.bones):
        h, t = np.array(info["joints_posed"][b.name]), np.array(info["bone_tails_posed"][b.name])
        L = float(np.hypot(*(t - h)))
        expect = b.length * pose.scale[i]
        errs[b.name] = round(abs(L - expect) / max(expect, 1e-6), 5)
        if pose.scale[i] != 1:
            scaled[b.name] = round(float(pose.scale[i]), 3)
    m["bone_length"] = {"max_relative_error": max(errs.values()), "axially_scaled_bones": scaled,
                        "pass": max(errs.values()) < 0.005}
    # ground contact and foot targets for planted feet
    planted = info["support"]["planted"]
    ground = {}
    for s in SIDES:
        a = render["alphas"].get(f"foot_{s}")
        sole = float(np.nonzero(a.any(axis=1))[0].max() - pad) if a is not None and a.any() else None
        tgt, ank = np.array(info["feet_targets"][s]), np.array(info["joints_posed"][f"foot_{s}"])
        ground[s] = {"planted": s in planted, "sole_y": sole,
                     "sole_error_px": None if sole is None else round(sole - sk.ground_y, 2),
                     "ankle_target_error_px": round(float(np.hypot(*(ank - tgt))), 2)}
    g_ok = all(abs(v["sole_error_px"] or 0) <= 2.0 and v["ankle_target_error_px"] <= 1.0
               for s, v in ground.items() if v["planted"])
    m["ground_contact"] = {"feet": ground, "ground_y": round(sk.ground_y, 1), "pass": g_ok}
    if not g_ok:
        defects.append("planted foot off the ground or off its target")
    # centre of mass over the support
    com = info["com"]
    lo, hi = info["support"]["x_range"]
    m["centre_of_mass"] = {"com": [round(float(com[0]), 1), round(float(com[1]), 1)],
                           "support_x": [lo, hi], "target_x": round(info["target_x"], 1),
                           "balance_error_px": round(float(info["balance_error_px"]), 2),
                           "weight_left": info["support"]["weight_left"],
                           "inside_support": bool(lo <= com[0] <= hi),
                           "pass": bool(lo <= com[0] <= hi) and abs(info["balance_error_px"]) <= 3.0}
    if not m["centre_of_mass"]["pass"]:
        defects.append(f"balance: CoM x {com[0]:.0f} vs target {info['target_x']:.0f} (support {lo}..{hi})")
    # seams: rest-adjacent pixels pulled apart where it can be seen (Deformer.tears)
    tears = tears or {"exposed_gap": [], "visible_seam": [], "outline_break": []}
    yy, xx = np.ogrid[:alpha.shape[0], :alpha.shape[1]]
    seams = {}
    for grp, bk in JOINT_GROUPS.items():
        for s_ in SIDES:
            b = sk.bone(f"{bk}_{s_}")
            x, y = info["joints_posed"][b.name]
            R2 = (1.8 * b.h) ** 2
            cnt = {k: sum(1 for (tx, ty) in v if (tx - x - pad) ** 2 + (ty - y - pad) ** 2 <= R2)
                   for k, v in tears.items() if k in ("exposed_gap", "visible_seam", "outline_break")}
            seams[f"{grp}_{s_}"] = cnt
    lab, n = ndimage.label(alpha)
    sizes = np.bincount(lab.ravel())[1:] if n else np.array([])
    pieces = int((sizes >= 40).sum())
    holes = ndimage.binary_fill_holes(alpha) & ~alpha
    m["seams"] = {"exposed_gap_pairs": len(tears["exposed_gap"]), "visible_seam_pairs": len(tears["visible_seam"]),
                  "outline_break_pairs": len(tears["outline_break"]), "joints": seams,
                  "figure_pieces": pieces, "rest_figure_pieces": rest_pieces,
                  "negative_space_px": int(holes.sum()),
                  "note": "pairs are rest-adjacent pixels 2 px apart torn > 5 px; negative space between limbs and "
                          "body is information, not a defect",
                  "pass": len(tears["exposed_gap"]) + len(tears["visible_seam"]) <= 20 and pieces <= rest_pieces}
    corrective = []
    for k, v in sorted(seams.items(), key=lambda kv: -sum(kv[1].values())):
        n_ = v["exposed_gap"] + v["visible_seam"]
        if n_ > 8:
            defects.append(f"visible seam at {k}: {n_} torn pixel pairs ({v['outline_break']} on outlines)")
            what = {"shoulder": "armpit gusset", "elbow": "elbow crease", "hip": "crotch/hip gusset",
                    "knee": "knee crease"}[k.split("_")[0]]
            corrective.append(f"corrective shape at {k}: {what} (the existing artwork cannot cover this bend)")
    if pieces > rest_pieces:
        defects.append(f"figure splits into {pieces} pieces (rest: {rest_pieces})")
    # deformation quality
    st = {c: v for c, v in render["stats"].items() if not v.get("rigid")}
    fold = max((v["folded_fraction"] for v in st.values()), default=0.0)
    comp = max((v["compressed_fraction"] for v in st.values()), default=0.0)
    stretch = max((v["stretched_fraction"] for v in st.values()), default=0.0)
    m["deformation"] = {"layers_deformed": sorted(st), "max_folded_fraction": fold,
                        "max_compressed_fraction": comp, "max_stretched_fraction": stretch,
                        "note": "folds are inner creases where the child is drawn over its parent",
                        "pass": fold <= 0.08 and stretch <= 0.05}
    if fold > 0.08:
        defects.append(f"joint fold/overlap: {fold:.1%} of a layer folds (inner crease)")
    if stretch > 0.05:
        defects.append(f"strong stretch: {stretch:.1%} of a layer stretched > 2.2x")
    # rigid share of the artwork (pixels moved without any blending keep their exact original look)
    areas = {c: solver.dfm.info[c]["area"] for c in render["stats"]}
    tot = sum(areas.values()) or 1
    blended = sum(areas[c] * render["stats"][c].get("blended_fraction", 0.0) for c in areas)
    m["artwork_fidelity"] = {"rigidly_moved_share": round(1 - blended / tot, 4),
                             "blended_share": round(blended / tot, 4),
                             "generated_pixels_visible": int(render["generated_visible"].sum()),
                             "note": "blended pixels are original pixels resampled inside joint bands; "
                                     "generated pixels are unreviewed Phase 2 extensions"}
    if m["artwork_fidelity"]["generated_pixels_visible"] > 0:
        defects.append(f"{m['artwork_fidelity']['generated_pixels_visible']} unreviewed generated pixels visible")
    # limb alignment: posed joints must stay inside the posed artwork
    sil = ndimage.binary_dilation(alpha, iterations=4)
    outside = [n for n, (x, y) in info["joints_posed"].items()
               if n not in ("pelvis",) and not (0 <= int(y + pad) < sil.shape[0] and 0 <= int(x + pad) < sil.shape[1]
                                                 and sil[int(y + pad), int(x + pad)])]
    m["limb_alignment"] = {"joints_outside_artwork": outside, "pass": not outside}
    if outside:
        defects.append(f"bones leave the artwork at {', '.join(outside)}")
    for n in info["ik_notes"]:
        defects.append(f"IK: {n}")
    # status and confidence (never 1.0: nothing here has been reviewed by a person or run in Moho)
    resolved = resolved or {}
    needs_art = [n for n in preset.needs_artwork if n not in resolved] + corrective
    for item, asset in resolved.items():
        defects.append(f"corrective artwork pending review: {asset} (for: {item})")
    for asset in pending_corrective or []:
        defects.append(f"corrective artwork pending review: {asset}")
    conf = 0.9 - 0.2 * len(needs_art) - 0.08 * len([d for d in defects if "unreviewed" not in d])
    conf -= 0.05 if m["artwork_fidelity"]["generated_pixels_visible"] else 0.0
    status = STATUS_ARTWORK if needs_art else (STATUS_REVIEW if [d for d in defects if "unreviewed" not in d]
                                                else STATUS_OK)
    return {"metrics": m, "defects": defects, "needs_artwork": needs_art, "status": status,
            "confidence": round(float(np.clip(conf, 0.05, 0.9)), 2)}


def on_bg(img: np.ndarray, color=(255, 255, 255)) -> Image.Image:
    im = Image.fromarray(img, "RGBA")
    bg = Image.new("RGBA", im.size, (*color, 255))
    bg.alpha_composite(im)
    return bg.convert("RGB")


def diagnostic(img: np.ndarray, sk: Skeleton, info: dict[str, Any], pad: int, gaps: np.ndarray | None = None
               ) -> Image.Image:
    """Bones, pivots, ground line, CoM and support over the pose (engineering view only)."""
    base = on_bg(img)
    arr = np.asarray(base).copy()
    if gaps is not None:
        arr[gaps] = (255, 0, 0)
    out = Image.fromarray(arr)
    d = ImageDraw.Draw(out)
    J, T = info["joints_posed"], info["bone_tails_posed"]
    for b in sk.bones:
        h, t = J[b.name], T[b.name]
        d.line((h[0] + pad, h[1] + pad, t[0] + pad, t[1] + pad), fill=(0, 140, 255), width=3)
    for n, (x, y) in J.items():
        d.ellipse((x + pad - 6, y + pad - 6, x + pad + 6, y + pad + 6), outline=(255, 255, 255), fill=(0, 90, 220))
    gy = sk.ground_y + pad
    d.line((0, gy, out.width, gy), fill=(0, 170, 0), width=2)
    lo, hi = info["support"]["x_range"]
    d.line((lo + pad, gy + 6, hi + pad, gy + 6), fill=(0, 120, 0), width=6)
    cx, cy = info["com"]
    d.line((cx + pad, cy + pad, cx + pad, gy), fill=(230, 120, 0), width=2)
    d.ellipse((cx + pad - 9, cy + pad - 9, cx + pad + 9, cy + pad + 9), outline=(230, 120, 0), width=3)
    return out


def gap_mask(render: dict[str, Any], tears: dict[str, list] | None = None) -> np.ndarray:
    """Visible tears (exposed gaps and seams), dilated for display."""
    m = np.zeros(render["image"].shape[:2], bool)
    for k in ("exposed_gap", "visible_seam"):
        for x, y in (tears or {}).get(k, []):
            m[y, x] = True
    return ndimage.binary_dilation(m, iterations=2)


def closeups(after: np.ndarray, before: np.ndarray | None, sk: Skeleton, info: dict[str, Any], pad: int,
             zoom: float = 2.0) -> Image.Image:
    """Shoulders, elbows, hips and knees: improved pose (bottom) under the original engine (top)."""
    cells = []
    a_img, b_img = on_bg(after, (214, 230, 214)), (on_bg(before, (214, 230, 214)) if before is not None else None)
    for grp, bk in JOINT_GROUPS.items():
        for s in SIDES:
            b = sk.bone(f"{bk}_{s}")
            x, y = info["joints_posed"][b.name]
            r = int(1.7 * max(b.h, 30))
            box = (int(x + pad - r), int(y + pad - r), int(x + pad + r), int(y + pad + r))
            size = (int(2 * r * zoom), int(2 * r * zoom))
            col = Image.new("RGB", (size[0], size[1] * (2 if b_img else 1) + 22), "white")
            ImageDraw.Draw(col).text((4, 4), f"{grp} {s}", fill=(0, 0, 0))
            y0 = 22
            if b_img:
                col.paste(b_img.crop(box).resize(size, Image.LANCZOS), (0, y0))
                y0 += size[1]
            col.paste(a_img.crop(box).resize(size, Image.LANCZOS), (0, y0))
            cells.append(col)
    h = max(c.height for c in cells)
    w = sum(c.width for c in cells) + 6 * (len(cells) - 1)
    sheet = Image.new("RGB", (w, h), "white")
    x = 0
    for c in cells:
        sheet.paste(c, (x, 0))
        x += c.width + 6
    return sheet
