"""Phase 3.8: new-character end-to-end evidence (``autorig newchar-evidence``).

Renders the numbered evidence of docs/phase3_8/new_character_evidence/ from a package built by the normal workflow
(``analyze`` -> ``prepare`` -> ``pose-refine`` -> ``hand-sheet``/``hand-import``/``hand-audit`` -> the evidence
commands). Every image is an AutoRig render or a measurement image of the real package; nothing is drawn by hand and
no generative model is called. Pending drawings appear only in images labelled REVIEW PREVIEW. Measurements are
geometric hints; ``artistic_review_required`` is always true.

Sections (each writes its images and a record in results.json):

* ``import``: 01 (the two inputs and their import reports);
* ``background``: 02 (background-removed character on four backgrounds, edge close-ups);
* ``segmentation``: 03, 04 (Phase 1 parts and face features, garment parts, layer sheet);
* ``skeleton``: 05 (Phase 1 joints vs the rig's pivots);
* ``arms``: 06-09 (+ one arm down): T-pose, arms lowered 30/60 deg, resting, with shoulder metrics;
* ``hands``: 10-13 (extraction, both sides' drawings on the character, wrist close-ups, HandCompatibilityReport).
"""

from __future__ import annotations

import copy
import json
import math
import shutil
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT = ImageFont.load_default()
BG = (246, 246, 249)
PENDING_RED = (190, 30, 30)
GESTURES = ("relaxed", "open_palm", "fist", "pointing", "thumbs_up", "gripping", "palm_down", "cupped")
SIDE_NAME = {"r": "right", "l": "left"}
# arm configurations that make each drawing read (side-symmetric: abduction is lateral for both sides). Pointing
# extends the arm sideways (the drawings point along the hand bone); thumbs up / palm down / cupped hold the forearm
# out from the body; gripping reaches forward-out with the IK.
GESTURE_ARMS: dict[str, dict[str, Any]] = {
    "relaxed": {"abduction": 8.0, "flexion": 12.0, "wrist": 4.0},
    "open_palm": {"abduction": 25.0, "flexion": -25.0, "wrist": -5.0},
    "fist": {"abduction": 12.0, "flexion": 35.0, "wrist": 0.0},
    "pointing": {"abduction": 80.0, "flexion": 0.0, "wrist": 0.0},
    "thumbs_up": {"abduction": 25.0, "flexion": -60.0, "wrist": 0.0},
    "gripping": {"reach": (0.75, 0.05), "wrist": 0.0},
    "palm_down": {"abduction": 12.0, "flexion": 45.0, "wrist": 0.0},  # forearm down and in, as on a desk
    "cupped": {"abduction": 18.0, "flexion": -46.0, "wrist": -16.0},
}


# -- small image helpers -----------------------------------------------------------------------------------
def white(rgba: np.ndarray | Image.Image, bg=(255, 255, 255)) -> Image.Image:
    im = rgba if isinstance(rgba, Image.Image) else Image.fromarray(rgba, "RGBA")
    base = Image.new("RGBA", im.size, (*bg, 255))
    base.alpha_composite(im.convert("RGBA"))
    return base.convert("RGB")


def fit(im: Image.Image, w: int, h: int) -> Image.Image:
    k = min(w / im.width, h / im.height)
    return im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.Resampling.LANCZOS)


def labelled(im: Image.Image, lines: list[str], width: int | None = None, colours: list | None = None,
             line_h: int = 13) -> Image.Image:
    width = width or im.width
    out = Image.new("RGB", (width, im.height + 6 + line_h * len(lines)), (255, 255, 255))
    d = ImageDraw.Draw(out)
    for i, t in enumerate(lines):
        c = (colours[i] if colours and i < len(colours) and colours[i] else
             (PENDING_RED if t.startswith("!") or "PENDING" in t or "NEEDS" in t else (20, 20, 30)))
        d.text((4, 3 + line_h * i), t, fill=c, font=FONT)
    out.paste(im, ((width - im.width) // 2, 6 + line_h * len(lines)))
    return out


def grid(tiles: list[Image.Image], cols: int, title: str = "", gap: int = 8, bg=BG) -> Image.Image:
    if not tiles:
        return Image.new("RGB", (400, 60), bg)
    rows = [tiles[i:i + cols] for i in range(0, len(tiles), cols)]
    cw = max(t.width for t in tiles)
    rh = [max(t.height for t in r) for r in rows]
    top = 26 if title else 0
    out = Image.new("RGB", (cols * (cw + gap) + gap, sum(rh) + gap * (len(rows) + 1) + top), bg)
    d = ImageDraw.Draw(out)
    if title:
        d.text((gap, 7), title, fill=(20, 20, 30), font=FONT)
    y = top + gap
    for r, h in zip(rows, rh):
        for i, t in enumerate(r):
            out.paste(t, (gap + i * (cw + gap) + (cw - t.width) // 2, y))
        y += h + gap
    return out


def crop_at(img: np.ndarray, cx: float, cy: float, w: int, h: int, scale: float = 1.0) -> Image.Image:
    H, W = img.shape[:2]
    x0, y0 = int(round(cx - w / 2)), int(round(cy - h / 2))
    pad = np.zeros((h, w, 4), np.uint8)
    sx0, sy0, sx1, sy1 = max(0, x0), max(0, y0), min(W, x0 + w), min(H, y0 + h)
    if sx1 > sx0 and sy1 > sy0:
        pad[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = img[sy0:sy1, sx0:sx1]
    im = white(pad)
    return im.resize((int(w * scale), int(h * scale)), Image.Resampling.NEAREST) if scale != 1.0 else im


def figure(img: np.ndarray, h: int = 520, w: int | None = None) -> Image.Image:
    a = img[..., 3] > 0
    ys, xs = np.nonzero(a)
    if not len(xs):
        return Image.new("RGB", (w or h // 2, h), (255, 255, 255))
    x0, y0, x1, y1 = max(0, xs.min() - 12), max(0, ys.min() - 12), xs.max() + 13, ys.max() + 13
    im = white(img[y0:y1, x0:x1])
    return fit(im, w or 10_000, h)


def save(im: Image.Image, path: Path, max_w: int = 2400) -> str:
    if im.width > max_w:
        im = fit(im, max_w, 100_000)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".png":
        im.save(path, optimize=True)
    else:
        im.save(path, quality=90)
    return path.name


def _json(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


# -- engines -----------------------------------------------------------------------------------------------
def engine(pkg: Path, use: dict[str, str] | None = None):
    """``engine_for_package``; ``use`` adds hand-store active-candidate choices ("side/pose" -> cid) to the project
    state for this engine only (what the Hands tab's "use" does), without writing the project."""
    from .editor.project import EditorProject
    from .motion.pipeline import engine_for_package

    if not use:
        return engine_for_package(pkg)
    orig = EditorProject.state

    def patched(self, *a, **k):
        st = copy.deepcopy(orig(self, *a, **k))
        hs = st.get("hand_store") or {}
        hs = {**hs, "use": {**(hs.get("use") or {}), **use}}
        st["hand_store"] = hs
        return st

    EditorProject.state = patched
    try:
        return engine_for_package(pkg)
    finally:
        EditorProject.state = orig


def arm_fn(arms: dict[str, dict[str, Any]], legs_at_rest: bool = False, sk=None):
    """intent_fn: per side ArmIntent fields; ``legs_at_rest`` keeps the drawn leg stance (ankles at rest)."""
    def fn(it):
        for s, cfg in arms.items():
            a = it.arms[s]
            a.target, a.reach = None, None
            for k, v in cfg.items():
                setattr(a, k, tuple(v) if k == "reach" else v)
        if legs_at_rest and sk is not None:
            for s in ("r", "l"):
                it.legs[s].ankle = tuple(float(v) for v in sk.bone(f"foot_{s}").head)
                it.legs[s].foot_angle = 0.0
            it.pelvis_dx, it.weight_left = 0.0, 0.5
        return it
    return fn


def identity_render(eng, vis) -> dict[str, Any]:
    from .motion.solver import PoseParams

    order = [c for c in eng.dfm.layer_order if c in vis] if hasattr(eng.dfm, "layer_order") else vis
    r = eng.dfm.render(PoseParams.identity(len(eng.sk.bones)), order, 0)
    return {"image": r["image"], "pad": 0}


# -- sections ----------------------------------------------------------------------------------------------
def section_import(ctx: dict[str, Any]) -> dict[str, Any]:
    from .newchar import image_import_report

    out, refs = ctx["out"], ctx["refs"]
    reps = {role: image_import_report(p, role) for role, p in refs.items()}
    tiles = []
    for role, p in refs.items():
        r = reps[role]
        im = Image.open(p).convert("RGB")
        lines = [f"{role}: {Path(p).name}  {r['width']}x{r['height']} {r['format']} {r['mode']}",
                 f"alpha band: {r['alpha']['has_alpha_band']}  genuine transparency: {r['alpha']['genuine_transparency']}",
                 f"background: {r['background']['polarity']} {r['background']['border_median_rgb']} uniform="
                 f"{r['background']['uniform']} ({r['background']['background_ratio']:.1%} of the image)",
                 f"content bbox {r['content_bbox_xyxy']}  sha256 {r['sha256'][:16]}...",
                 "received as a chat attachment re-encoded to JPEG (the original PNG bytes were not available)"]
        tiles.append(labelled(fit(im, 620, 760), lines, 640))
    save(grid(tiles, 2, "01 Original inputs, exactly as imported (no scaling; origin top-left, +y down, source px)"),
         out / "01_original_character.png")
    (out / "import_reports.json").write_text(json.dumps(reps, indent=1, default=_json))
    return {"images": ["01_original_character.png"], "reports": reps}


def section_background(ctx: dict[str, Any]) -> dict[str, Any]:
    from .newchar import background_extraction_qa

    out, pkg = ctx["out"], ctx["pkg"]
    tmp = out / "_bg"
    q = background_extraction_qa(pkg, tmp)
    a = Image.open(tmp / "backgrounds.png")
    b = Image.open(tmp / "edge_closeups.png")
    lines = [f"character px {q['character_px']}, visible {q['visible_px']}, missing {q['missing_px']}, "
             f"background kept as character {q['background_included_px']}, clipped outline {q['clipped_outline_px']}",
             f"light fringe on the silhouette: original layers {q['light_fringe_px']} px ({q['light_fringe_ratio']:.1%}); "
             f"after edge decontamination (derived copy) {q['decontaminated']['light_fringe_px']} px",
             "white shirt, white trainers, laces, cuffs: enclosed paper-coloured regions, all covered by layers "
             f"({sum(1 for r in q['enclosed_light_regions'] if r['covered_by_layers'] == 1.0)}/"
             f"{len(q['enclosed_light_regions'])} largest regions)"]
    sheet = grid([labelled(a, lines, max(a.width, 900)), b], 1,
                 "02 Background removed (Phase 1 SAM 3 masks + spatial paper rule; Phase 3.4 edge decontamination)")
    save(sheet, out / "02_background_removed.png")
    shutil.rmtree(tmp, ignore_errors=True)
    return {"images": ["02_background_removed.png"], "qa": q}


def section_segmentation(ctx: dict[str, Any]) -> dict[str, Any]:
    from .garmentrig.structure import _palette

    out, pkg, proj = ctx["out"], ctx["pkg"], ctx["project"]
    eng, vis = ctx["eng"]
    seg = Image.open(proj / "previews" / "segmentation.png").convert("RGB")
    face = Image.open(proj / "previews" / "facial_segmentation.png").convert("RGB")
    st = eng.dfm.structure
    src = np.asarray(Image.open(pkg / "source" / "original.png").convert("RGBA"))
    ov = st.overlay(vis)
    base = Image.fromarray(src).convert("RGBA")
    base.alpha_composite(Image.fromarray(ov))
    used: dict[str, int] = {}
    for cid in vis:
        if cid in st.layer_parts:
            m = st.full_map(cid)
            for i, c in zip(*np.unique(m[m >= 0], return_counts=True)):
                used[st.parts[i].name] = used.get(st.parts[i].name, 0) + int(c)
    pal = _palette(len(st.parts))
    leg = Image.new("RGB", (360, 14 * len(used) + 12), (255, 255, 255))
    dl = ImageDraw.Draw(leg)
    for k, (n, c) in enumerate(sorted(used.items(), key=lambda t: -t[1])):
        p = st.part(n)
        dl.rectangle([4, 5 + 14 * k, 16, 15 + 14 * k], fill=tuple(int(v) for v in pal[st.index[n]]))
        dl.text((22, 5 + 14 * k), f"{n} [{p.cls[:5]}] {c}px", fill=(0, 0, 0), font=FONT)
    parts_im = Image.new("RGB", (base.width // 2 + 370, max(base.height // 2, leg.height)), (255, 255, 255))
    parts_im.paste(base.convert("RGB").resize((base.width // 2, base.height // 2), Image.Resampling.LANCZOS), (0, 0))
    parts_im.paste(leg, (base.width // 2 + 6, 0))
    facej = json.loads((proj / "analysis" / "face.json").read_text())
    anat = json.loads((proj / "analysis" / "anatomy.json").read_text())
    meta = json.loads((proj / "metadata.json").read_text())
    f = meta["fidelity"]
    lines1 = [f"Phase 1 (SAM 3): {len(anat['parts'])} body parts, coverage {f['coverage_ratio']}, overlaps "
              f"{f['overlap_pixels']}, linework retained {f['linework_retained_ratio']}",
              "warnings: " + "; ".join(meta.get("warnings", []))[:150]]
    lines2 = [f"face features: {', '.join(sorted(facej['features']))}",
              f"missing face artwork: {', '.join(m if isinstance(m, str) else m.get('id', str(m)) for m in facej.get('missing_artwork', []))[:140]}"]
    lines3 = [f"Phase 3.3 garment parts: {len(used)} in use (derived from this artwork, with the rig's uniform "
              "vocabulary: see the generalisation report)",
              "chain -> rigid 'tie' slot on the chest; open shirt -> 'sweater'; crest -> rigid 'badge' (outline-emblem rule)"]
    sheet = grid([labelled(fit(seg, 700, 1000), lines1, 760), labelled(fit(face, 560, 560), lines2, 760),
                  labelled(fit(parts_im, 760, 1000), lines3, 760)], 3,
                 "03 Segmentation: Phase 1 body parts + skeleton, face features, garment parts (overlays on the source)")
    save(sheet, out / "03_segmentation_overlay.png", 2600)
    lay = Image.open(proj / "previews" / "layers.png").convert("RGB")
    man = json.loads((pkg / "artwork" / "manifest.json").read_text())
    nlay = sum(1 for L in man["layers"] if L["category"] == "A_original")
    next_ = len(man.get("extensions", []))
    save(labelled(fit(lay, 2000, 1400), [f"{nlay} original layers (full canvas, source pixels, unscaled); {next_} "
                                         "generated extension layers beneath them (pending review)"]),
         out / "04_artwork_layers.png")
    return {"images": ["03_segmentation_overlay.png", "04_artwork_layers.png"], "garment_parts": used,
            "decor": {k: int(v.sum()) for k, v in st.decor.items()},
            "face_features": sorted(facej["features"]), "phase1_warnings": meta.get("warnings", []),
            "phase1_fidelity": {k: f[k] for k in ("coverage_ratio", "overlap_pixels", "linework_retained_ratio",
                                                  "max_abs_error_inside_layers")},
            "phase1_steps": [s for s in meta["pipeline"]["steps"] if s["step"] in
                             ("silhouette_refinement", "outline_completion", "fringe_absorption")]}


def section_skeleton(ctx: dict[str, Any]) -> dict[str, Any]:
    out, pkg, proj = ctx["out"], ctx["pkg"], ctx["project"]
    eng, vis = ctx["eng"]
    sk = eng.sk
    anat = json.loads((proj / "analysis" / "anatomy.json").read_text())
    src = Image.open(pkg / "source" / "original.png").convert("RGB")
    im = src.copy()
    d = ImageDraw.Draw(im)
    for b in sk.bones:
        h, t = tuple(map(float, b.head)), tuple(map(float, b.tail))
        d.line([h, t], fill=(20, 110, 230), width=4)
        d.ellipse([h[0] - 6, h[1] - 6, h[0] + 6, h[1] + 6], outline=(20, 110, 230), width=3)
    moved = []
    for n, j in anat["joints"].items():
        if j.get("position") is None:
            continue
        x, y = j["position"]
        d.ellipse([x - 5, y - 5, x + 5, y + 5], fill=(230, 40, 40))
        d.text((x + 7, y - 6), n, fill=(150, 0, 0), font=FONT)
    for p in eng.dfm.structure.pivots:
        if p.get("aligned"):
            moved.append(p)
            x0, y0 = p["rest_pivot"]
            x1, y1 = p["pivot"]
            d.line([(x0, y0), (x1, y1)], fill=(250, 160, 0), width=3)
            d.ellipse([x1 - 7, y1 - 7, x1 + 7, y1 + 7], outline=(250, 160, 0), width=3)
    rows = [f"{b.name}: head ({b.head[0]:.0f},{b.head[1]:.0f}) len {np.hypot(*(b.tail - b.head)):.0f}px "
            f"axis_offset {b.axis_offset:.1f}" for b in sk.bones]
    lines = ["red: Phase 1 joints (automatic, no manual landmarks); blue: the rig skeleton used for posing "
             "(shoulder pivots moved toward the armhole by the garment rig)",
             f"unreliable joints flagged by Phase 1: {', '.join(n for n, j in anat['joints'].items() if not j.get('reliable'))}"]
    pv = [f"{p['joint']}: Phase 1 {p['rest_pivot']} -> rig pivot {p['pivot']} (lifted {p.get('lift_px')} px toward "
          "the armhole top; orange)" for p in moved]
    pv += [f"pivot check: {p.get('joint') or p.get('bone')}: {p.get('status')} {p.get('issue', '')}"[:120]
           for p in eng.dfm.structure.pivots if "rest_pivot" not in p][:4]
    left = labelled(fit(im, 900, 1200), lines + pv, 980)
    table = Image.new("RGB", (520, 14 * len(rows) + 10), (255, 255, 255))
    for i, t in enumerate(rows):
        ImageDraw.Draw(table).text((4, 4 + 14 * i), t, fill=(20, 20, 30), font=FONT)
    save(grid([left, table], 2, "05 Skeleton generated from the new character (overlay on the source)"),
         out / "05_skeleton_overlay.png")
    bones = {b.name: {"parent": b.parent, "head": [round(float(v), 1) for v in b.head],
                      "tail": [round(float(v), 1) for v in b.tail], "length": round(float(np.hypot(*(b.tail - b.head))), 1),
                      "axis_offset": round(float(b.axis_offset), 2)} for b in sk.bones}
    return {"images": ["05_skeleton_overlay.png"], "bones": bones, "pivots": eng.dfm.structure.pivots,
            "phase1_joints": {n: {"position": j.get("position"), "confidence": j.get("confidence"),
                                  "reliable": j.get("reliable"), "method": j.get("method")}
                              for n, j in anat["joints"].items()}}


def _shoulders(r: dict[str, Any]) -> dict[str, Any]:
    from .performance import shoulders

    return shoulders.measure(r["image"], r["solve"]["joints_posed"], r["pad"])


def section_arms(ctx: dict[str, Any]) -> dict[str, Any]:
    """Step 7: lowering the T-pose arms. Legs keep the drawn stance so only the arms and shoulders change."""
    out = ctx["out"]
    eng, vis = ctx["eng"]
    sk = eng.sk
    rest = eng.solver.anatomical(__import__("autorig.motion.solver", fromlist=["PoseParams"]).PoseParams.identity(len(sk.bones)))
    ra = {s: rest[f"upper_arm_{s}_abduction"] for s in ("r", "l")}
    cases = [("A_tpose", "06_tpose.png", {s: ra[s] for s in "rl"}, "T-pose (rest abduction, the drawn arm angle)"),
             ("B_lowered_30", "07_arms_30_degrees.png", {s: ra[s] - 30 for s in "rl"}, "arms lowered 30 deg"),
             ("C_lowered_60", "08_arms_60_degrees.png", {s: ra[s] - 60 for s in "rl"}, "arms lowered 60 deg"),
             ("D_resting", "09_relaxed_standing.png", {s: 8.0 for s in "rl"}, "arms resting beside the body (8 deg)"),
             ("E_one_down", "09b_one_arm_down.png", {"r": 8.0, "l": ra["l"]}, "right arm down, left arm extended")]
    res: dict[str, Any] = {"rest_abduction": ra, "cases": {}}
    ref = None
    for key, fname, abd, title in cases:
        rows = []
        for mode in ("auto", "all"):
            arms = {s: {"abduction": abd[s], "flexion": 4.0 if abd[s] > 60 else 12.0, "wrist": 0.0 if abd[s] > 60 else 4.0}
                    for s in "rl"}
            t0 = time.perf_counter()
            r = eng.pose("relaxed_standing", visible=vis, intent_fn=arm_fn(arms, True, sk), corrective_mode=mode,
                         allow_pending=mode == "all", switches={f"hand_{s}": {"user": "open_palm", "auto": False}
                                                                for s in "rl"})
            dt = time.perf_counter() - t0
            sh = _shoulders(r)
            if key == "A_tpose" and mode == "auto":
                ref = sh
            ge = r.get("garment_eval") or {}
            parts = ge.get("parts") or {}
            bad = {p: v["status"] for p, v in parts.items() if v["status"] in ("FAIL", "NEEDS_ARTWORK")}
            a = r["solve"].get("anatomical") or {}
            rec = {"abduction_target": abd, "abduction_solved": {s: a.get(f"upper_arm_{s}_abduction") for s in "rl"},
                   "arm_length_px": {s: round(float(np.hypot(*(np.subtract(r["solve"]["joints_posed"][f"forearm_{s}"],
                                                                         r["solve"]["joints_posed"][f"upper_arm_{s}"])))) +
                                              float(np.hypot(*(np.subtract(r["solve"]["joints_posed"][f"hand_{s}"],
                                                                         r["solve"]["joints_posed"][f"forearm_{s}"])))), 1)
                                     for s in "rl"},
                   "shoulders": sh, "garments": {k: v["status"] for k, v in (ge.get("garments") or {}).items()},
                   "failing_parts": bad, "gaps": len(r["tears"]["exposed_gap"]),
                   "seams": len(r["tears"]["visible_seam"]), "outline_breaks": len(r["tears"]["outline_break"]),
                   "correctives_shown": [c.get("asset_id") or c.get("id") for c in (r.get("correctives") or [])
                                         if c.get("shown")] if isinstance(r.get("correctives"), list) else None,
                   "feasibility": r["feasibility"].get("class") or r["feasibility"].get("status"),
                   "render_s": round(dt, 2)}
            res["cases"].setdefault(key, {})[mode] = rec
            j = r["solve"]["joints_posed"]
            pad = r["pad"]
            full = figure(r["image"], 620)
            xr, xl = j["upper_arm_r"][0] + pad, j["upper_arm_l"][0] + pad
            ys_ = (j["upper_arm_r"][1] + j["upper_arm_l"][1]) / 2 + pad
            wbox = int(abs(xl - xr) + 240)
            close = fit(crop_at(r["image"], (xr + xl) / 2, ys_ + 70, wbox, 300, 1.0), 560, 420)
            shl = " | ".join(f"{s}: slope {sh[s].get('slope_deg')} deg, hump {sh[s].get('hump_px')} px, rise "
                             f"{round(ref[s]['top_y'] - sh[s]['top_y'], 1) if ref and sh[s].get('top_y') is not None and ref[s].get('top_y') is not None else '-'} px"
                             for s in ("r", "l") if sh[s].get("status") == "OK")
            label = ("originals only (production: approved artwork)" if mode == "auto" else
                     "REVIEW PREVIEW: pending candidate corrections drawn (not approved)")
            lines = [label, f"abduction r/l {rec['abduction_solved']['r']}/{rec['abduction_solved']['l']} deg, arm length "
                            f"r/l {rec['arm_length_px']['r']}/{rec['arm_length_px']['l']} px",
                     f"gaps {rec['gaps']}, seams {rec['seams']}, outline breaks {rec['outline_breaks']}",
                     shl[:150], "failing parts: " + (", ".join(f"{p}={s}" for p, s in bad.items())[:150] or "none")]
            rows.append(labelled(grid([full, close], 2, bg=(255, 255, 255)), lines, 1000))
        save(grid(rows, 2, f"{fname[:2]} {title}. Legs keep the drawn stance; the hands stay the original open palms."),
             out / fname)
    return {"images": [c[1] for c in cases], **res}


# -- hands ---------------------------------------------------------------------------------------------------
def _hand_meta(pkg: Path) -> dict[str, Any]:
    p = pkg / "analysis" / "hand_asset_metadata.json"
    if not p.is_file():
        return {}
    m = json.loads(p.read_text())
    assets = m["assets"]
    return {a["cid"]: a for a in (assets.values() if isinstance(assets, dict) else assets)}


def section_hands(ctx: dict[str, Any]) -> dict[str, Any]:
    """Steps 8-13: extraction QA, the 16 drawings on the character (each forced through the switch system as an
    explicit user choice, previewed because pending), wrist close-ups and the HandCompatibilityReport."""
    from .motion.handcheck import wrist_check

    out, pkg, grid_dir = ctx["out"], ctx["pkg"], ctx["hand_grid"]
    meta = _hand_meta(pkg)
    rep = json.loads((grid_dir / "extraction_report.json").read_text())
    # 10: extraction QA + each sprite on white / dark grey / checkerboard
    qa = Image.open(grid_dir / "extraction_sheet.jpg").convert("RGB")
    tiles = []
    for r in range(1, 5):
        for c in range(1, 5):
            cell = f"r{r}c{c}"
            spr = Image.open(grid_dir / "sprites" / f"{cell}.png").convert("RGBA")
            row = [white(spr), white(spr, (64, 64, 64))]
            yy, xx = np.mgrid[0:spr.height, 0:spr.width]
            chk = np.where(((yy // 8) + (xx // 8)) % 2 == 0, 236, 200).astype(np.uint8)
            cb = Image.fromarray(np.stack([chk] * 3 + [np.full_like(chk, 255)], -1), "RGBA")
            cb.alpha_composite(spr)
            row.append(cb.convert("RGB"))
            q = rep["cells"][cell]["quality"]
            strip = grid([fit(x, 150, 150) for x in row], 3, bg=(255, 255, 255), gap=3)
            tiles.append(labelled(strip, [f"{cell}: outline {q['outline_coverage']:.1%}, light-edge px "
                                          f"{q.get('light_px_on_edge')}, skin-edge px {q['skin_on_edge_px']}"], 480))
    bginfo = rep["background"]
    head = [f"sheet: black background (polarity {bginfo.get('mode')}), outline band {bginfo.get('outline_band_px')} px "
            f"(measured colour->rim distance p10/p50/p90 {bginfo.get('colour_to_rim_px_p10_p50_p90')})",
            f"16 drawings, one per cell; open wrist cuts {rep['summary']['open_cut']}, closed outlines "
            f"{rep['summary']['closed_outline']}; not mirrored: {rep['summary']['not_mirrored_pairs']}; derived "
            f"(mirror copy): {rep['summary']['derived_assets']}"]
    save(grid([labelled(fit(qa, 1300, 1700), head, 1320), grid(tiles, 2, "each sprite on white | dark grey | "
                                                                       "checkerboard (alpha)")], 2,
              "10 Hand library extraction (spatially constrained dark-background separation; no global black deletion)"),
         out / "10_hand_library_extraction.png", 2800)
    # 11/12: every drawing on the character
    eng0, vis = ctx["eng"]
    lib = "tough_student_hands_v1"
    use = {f"{s}/open_palm": f"store:{lib}/{SIDE_NAME[s]}_open_palm" for s in ("r", "l")}
    eng_op, vis_op = engine(pkg, use)
    results: dict[str, Any] = {}
    wrists: list[Image.Image] = []
    for s in ("r", "l"):
        o = "l" if s == "r" else "r"
        tiles = []
        for g in GESTURES:
            e, v = (eng_op, vis_op) if g == "open_palm" else (eng0, vis)
            arms = {s: dict(GESTURE_ARMS[g]), o: {"abduction": 8.0, "flexion": 12.0, "wrist": 4.0}}
            sw = {f"hand_{s}": {"user": g, "auto": False}, f"hand_{o}": {"user": "open_palm", "auto": False}}
            t0 = time.perf_counter()
            r = e.pose("relaxed_standing", visible=v, intent_fn=arm_fn(arms, True, e.sk), switches=sw,
                       allow_pending=True, corrective_mode="auto", evaluate_garments=False,
                       hand_actions={s: None, o: None})
            dt = time.perf_counter() - t0
            h = r["hands"][s]
            dec = r["hand_selection"]["decisions"][s]
            oc = dec.get("override_check") or {}
            wc = wrist_check(r, e, s)
            cid = h["cid"]
            m = meta.get(cid, {})
            wm = ((m.get("measured") or {}).get("wrist") or {})
            rec = {"requested": g, "shown_pose": h["pose"], "cid": cid, "layer": h["layer"], "source": h["source"],
                   "pending_review": h["pending_review"], "assigned_side": s,
                   "drawn_handedness": m.get("handedness"), "store_side": m.get("store_side"),
                   "sheet_cell": (m.get("provenance") or {}).get("sheet_cell"),
                   "derived": (m.get("provenance") or {}).get("derived"),
                   "wrist_opening": {k: wm.get(k) for k in ("opening", "centre_px", "width_px", "confidence", "manual_review")},
                   "scale": ((m.get("measured") or {}).get("rest_placement") or {}).get("scale"),
                   "override_check": oc, "aim_applied": dec.get("aim_applied"), "wrist_check": wc,
                   "render_s": round(dt, 2)}
            results[f"{s}_{g}"] = rec
            j, pad = r["solve"]["joints_posed"], r["pad"]
            hx, hy = j[f"hand_{s}"][0] + pad, j[f"hand_{s}"][1] + pad
            full = figure(r["image"], 360)
            close = crop_at(r["image"], hx, hy + 20, 150, 150, 2.0)
            ok = h["pose"] == g and cid.startswith("store:") or (g == "open_palm" and cid.startswith("store:"))
            viol = ", ".join(f"{v.get('constraint')}: {v.get('reason')}" if isinstance(v, dict) else str(v) for v in (oc.get("violations") or [])) if oc else ""
            lines = [f"{SIDE_NAME[s]} {g}: {cid.split('/')[-1] if '/' in cid else cid}"
                     + (f" ({rec['sheet_cell']}{', mirrored copy' if rec['derived'] else ''})" if rec["sheet_cell"] else ""),
                     f"assigned side {SIDE_NAME[s]}; drawn as a {rec['drawn_handedness']} hand"
                     + ("" if rec["drawn_handedness"] in (None, SIDE_NAME[s]) else " !"),
                     "PENDING REVIEW (preview only)" if h["pending_review"] else f"status: {h['source']}",
                     f"wrist pivot {wm.get('opening')} {wm.get('centre_px')} w {round(wm.get('width_px') or 0)}px"
                     + (" (manual pivot review)" if wm.get("manual_review") else ""),
                     f"compatibility: {'OK' if oc.get('ok') else 'CHECK: ' + viol[:60]}; palm {oc.get('palm')}",
                     f"rotation from drawn {oc.get('rotation_from_drawn_deg')} deg; aim "
                     f"{(dec.get('aim_applied') or {}).get('applied_deg', 0)} deg",
                     f"wrist check: {wc.get('status')} " + ", ".join(f"{k}={'ok' if v.get('ok') else 'FAIL'}"
                                                                     for k, v in (wc.get('checks') or {}).items())[:70]]
            if not ok:
                lines.insert(0, f"! requested {g}, shown {h['pose']} ({cid})")
            tile = labelled(grid([full, close], 2, bg=(255, 255, 255), gap=4), lines, 520)
            tiles.append(tile)
            wrists.append(labelled(crop_at(r["image"], hx, hy, 120, 120, 2.2),
                                   [f"{SIDE_NAME[s]} {g}", f"{wc.get('status')}"], 270))
        save(grid(tiles, 4, f"{11 if s == 'l' else 12} {SIDE_NAME[s].upper()} hand: all 8 library drawings on the "
                             "character (explicit switch choices; every drawing PENDING REVIEW, shown as a preview)"),
             out / ("11_left_hand_variants.png" if s == "l" else "12_right_hand_variants.png"), 2600)
    save(grid(wrists, 8, "13 Wrist alignment close-ups (2.2x): store drawings placed by the WristAlignmentController "
                         "(opening centre on the cuff edge, tucked under it, rotated onto the hand bone, one scale per "
                         "side)"), out / "13_wrist_alignment.png", 2600)
    store_align = {}
    for s_ in ("r", "l"):
        for pose, v in eng0.hand_lib.hands[s_].items():
            if v.available and v.cid.startswith("store:"):
                a = eng0.hand_lib.alignment_v(v, eng0.profile.midline_x)
                store_align[f"{s_}_{pose}"] = {"cid": v.cid, "status": a.get("status"), "issues": a.get("issues", [])}
    compat = hand_compatibility(pkg, eng0, grid_dir, meta)
    compat["store_alignment"] = store_align
    (out / "hand_compatibility_report.json").write_text(json.dumps(compat, indent=1, default=_json))
    # 10b: the character's own hand, two library drawings as drawn, and their skin-matched candidates
    own = np.asarray(Image.open(pkg / "artwork" / "original" / "hand_r.png").convert("RGBA"))
    ys, xs = np.nonzero(own[..., 3])
    own_c = white(own[ys.min() - 4:ys.max() + 5, xs.min() - 4:xs.max() + 5])
    row = [labelled(fit(own_c, 220, 220), ["character's own right hand", "(original pixels)"], 240)]
    for cid in (f"store:{lib}/right_open_palm", f"store:{lib}/right_fist"):
        c = compat["candidates"].get(cid)
        cell = (compat["library"].get(cid) or {}).get("sheet_cell")
        if not c or not cell:
            continue
        row.append(labelled(fit(white(Image.open(grid_dir / "sprites" / f"{cell}.png")), 220, 220),
                            [f"{cid.split('/')[-1]} as drawn", f"skin {compat['library'][cid]['skin_median_rgb']}"], 240))
        row.append(labelled(fit(white(Image.open(grid_dir / c["skin_matched"])), 220, 220),
                            [f"skin-matched CANDIDATE", f"gain {c['gain_rgb']} (not imported)"], 240))
    s_ = compat.get("summary", {})
    save(labelled(grid(row, len(row), bg=(255, 255, 255)),
                  [f"HandCompatibilityReport: character skin {s_.get('character_skin_rgb')} vs library "
                   f"{s_.get('library_skin_rgb_median')}; outline on the character {s_.get('library_outline_px_on_character_median')} "
                   f"px vs {s_.get('character_outline_px')} px; scale {s_.get('scale_on_character')}",
                   "candidates are non-destructive proposals (references/tough_student/hand_grid/candidates/); nothing "
                   "is imported or approved"]), out / "10b_hand_colour_candidates.png")
    return {"images": ["10_hand_library_extraction.png", "10b_hand_colour_candidates.png", "11_left_hand_variants.png",
                       "12_right_hand_variants.png", "13_wrist_alignment.png"], "gestures": results, "compatibility": compat,
            "extraction_summary": rep["summary"], "extraction_background": bginfo}


def _outline_width(rgba: np.ndarray) -> float | None:
    """Median thickness (px) of the dark outline on a drawing's silhouette: 2 x the distance from the band's
    centre line... measured as the mean run length of dark pixels along the inward normal (distance-transform
    estimate: max EDT inside the dark band near the edge, doubled)."""
    from scipy import ndimage

    a = rgba[..., 3] > 127
    lum = rgba[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    dark = a & (lum < 70)
    edge = a & ~ndimage.binary_erosion(a)
    band = dark & ndimage.binary_dilation(edge, iterations=12)
    if band.sum() < 50:
        return None
    lab, n = ndimage.label(band)
    dt = ndimage.distance_transform_edt(band)
    # ridge values (local maxima of the distance) approximate half the thickness
    ridge = (dt == ndimage.maximum_filter(dt, size=3)) & (dt > 0)
    return round(float(2 * np.median(dt[ridge]) - 1), 2)


def _skin(rgba: np.ndarray) -> np.ndarray | None:
    a = rgba[..., 3] > 200
    rgb = rgba[..., :3].astype(np.int16)
    lum = rgb @ np.array([0.299, 0.587, 0.114])
    sk = a & (rgb[..., 0] - rgb[..., 2] > 40) & (lum > 110)
    if sk.sum() < 50:
        return None
    return rgba[..., :3][sk].astype(np.float32)


def hand_compatibility(pkg: Path, eng, grid_dir: Path, meta: dict[str, Any]) -> dict[str, Any]:
    """HandCompatibilityReport: the library drawings against the character's own hands. Every number is measured;
    the candidate adjustments are proposals (non-destructive, written as candidate files, never imported or
    approved)."""
    from scipy import ndimage

    out: dict[str, Any] = {"schema": "autorig.hand_compatibility_report", "version": 1,
                           "character_hands": {}, "library": {}, "candidates": {}, "findings": [],
                           "artistic_review_required": True}
    lays = {s: np.asarray(Image.open(pkg / "artwork" / "original" / f"hand_{s}.png").convert("RGBA")) for s in "rl"}
    for s, L in lays.items():
        skn = _skin(L)
        a = L[..., 3] > 127
        ys, xs = np.nonzero(a)
        fa = eng.sk.bone(f"forearm_{s}")
        hb = eng.sk.bone(f"hand_{s}")
        out["character_hands"][s] = {
            "skin_median_rgb": [round(float(v)) for v in np.median(skn, 0)] if skn is not None else None,
            "skin_p10_p90_lum": [round(float(np.percentile(skn @ np.array([.299, .587, .114]), q)), 1) for q in (10, 90)]
            if skn is not None else None,
            "tone_clusters": _tones(skn), "outline_px": _outline_width(L), "area_px": int(a.sum()),
            "bbox_px": [int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)],
            "hand_bone_px": round(float(np.hypot(*(hb.tail - hb.head))), 1),
            "forearm_bone_px": round(float(np.hypot(*(fa.tail - fa.head))), 1)}
    ch_skin = np.array([v["skin_median_rgb"] for v in out["character_hands"].values() if v["skin_median_rgb"]], float)
    ch_skin = ch_skin.mean(0) if len(ch_skin) else None
    ch_out = np.mean([v["outline_px"] for v in out["character_hands"].values() if v["outline_px"]])
    cand_dir = grid_dir / "candidates"
    cand_dir.mkdir(parents=True, exist_ok=True)
    gains = None
    for cid, m in sorted(meta.items()):
        if not cid.startswith("store:"):
            continue
        cell = (m.get("provenance") or {}).get("sheet_cell")
        spr = np.asarray(Image.open(grid_dir / "sprites" / f"{cell}.png").convert("RGBA"))
        if (m.get("provenance") or {}).get("derived") == "mirror_x":
            spr = spr[:, ::-1].copy()
        skn = _skin(spr)
        rp = (m.get("measured") or {}).get("rest_placement") or {}
        scale = rp.get("scale")
        wm = (m.get("measured") or {}).get("wrist") or {}
        ow = _outline_width(spr)
        rec = {"sheet_cell": cell, "skin_median_rgb": [round(float(v)) for v in np.median(skn, 0)] if skn is not None else None,
               "tone_clusters": _tones(skn), "outline_px_sheet": ow,
               "outline_px_on_character": round(ow * scale, 2) if ow and scale else None,
               "scale_on_character": scale, "wrist_width_px_sheet": wm.get("width_px"),
               "wrist_width_px_on_character": round(wm["width_px"] * scale, 1) if wm.get("width_px") and scale else None,
               "area_px_on_character": int((spr[..., 3] > 127).sum() * scale * scale) if scale else None,
               "view": m.get("visible_surface"), "orientation": m.get("finger_dir_deg"), "wrist_opening": wm.get("opening")}
        if ch_skin is not None and skn is not None:
            med = np.median(skn, 0)
            rec["skin_delta_rgb"] = [round(float(v)) for v in (med - ch_skin)]
            rec["skin_delta_e_approx"] = round(float(np.linalg.norm(med - ch_skin)), 1)
        out["library"][cid] = rec
        if ch_skin is not None and skn is not None:
            # candidate 1: controlled colour matching of the skin tones only (per-channel gain from the library's
            # median skin to the character's); outlines, shading lines and alpha unchanged
            g = ch_skin / np.maximum(np.median(skn, 0), 1.0)
            gains = g
            rgb = spr[..., :3].astype(np.float32)
            lum = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
            is_skin = (spr[..., 3] > 0) & (rgb[..., 0] - rgb[..., 2] > 25) & (lum > 60)
            new = spr.copy()
            new[..., :3][is_skin] = np.clip(np.rint(rgb[is_skin] * g), 0, 255).astype(np.uint8)
            name = f"{cid.split('/')[-1]}__skin_matched.png"
            Image.fromarray(new, "RGBA").save(cand_dir / name, optimize=True)
            out["candidates"][cid] = {"skin_matched": f"candidates/{name}", "gain_rgb": [round(float(v), 3) for v in g],
                                      "changed_px": int(is_skin.sum()), "status": "CANDIDATE_NOT_IMPORTED",
                                      "approval": "none (a person must review, then import it as a new version)"}
    libs = list(out["library"].values())
    if libs and ch_skin is not None:
        de = [x["skin_delta_e_approx"] for x in libs if x.get("skin_delta_e_approx") is not None]
        out["summary"] = {
            "character_skin_rgb": [round(float(v)) for v in ch_skin],
            "library_skin_rgb_median": [round(float(v)) for v in np.median([x["skin_median_rgb"] for x in libs if x["skin_median_rgb"]], 0)],
            "skin_delta_e_approx_median": round(float(np.median(de)), 1) if de else None,
            "character_outline_px": round(float(ch_out), 2),
            "library_outline_px_on_character_median": round(float(np.median([x["outline_px_on_character"] for x in libs if x["outline_px_on_character"]])), 2),
            "scale_on_character": sorted({x["scale_on_character"] for x in libs if x["scale_on_character"]}),
            "character_tone_clusters": [v["tone_clusters"] for v in out["character_hands"].values()],
            "library_tone_clusters_median": float(np.median([x["tone_clusters"] for x in libs if x["tone_clusters"]])),
        }
        s = out["summary"]
        if s["skin_delta_e_approx_median"] and s["skin_delta_e_approx_median"] > 20:
            out["findings"].append(f"skin tone differs: library median {s['library_skin_rgb_median']} vs character "
                                   f"{s['character_skin_rgb']} (approx. RGB distance {s['skin_delta_e_approx_median']}); "
                                   "the library hands read paler/pinker than the character's own hands and face")
        if abs(s["library_outline_px_on_character_median"] - s["character_outline_px"]) > 1.0:
            out["findings"].append(f"outline thickness on the character: library {s['library_outline_px_on_character_median']} "
                                   f"px vs character {s['character_outline_px']} px")
        out["findings"].append("shading: library hands use two-tone cel shading (highlight + shadow); compare the "
                               "tone-cluster counts above")
    return out


def _tones(skn: np.ndarray | None) -> int | None:
    """Number of distinct skin tones (8-level luminance histogram bins holding >= 8 % of the skin pixels)."""
    if skn is None:
        return None
    lum = skn @ np.array([0.299, 0.587, 0.114])
    h, _ = np.histogram(lum, bins=8, range=(100, 260))
    return int((h >= 0.08 * len(lum)).sum())


SECTIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "import": section_import, "background": section_background, "segmentation": section_segmentation,
    "skeleton": section_skeleton, "arms": section_arms, "hands": section_hands}


def newchar_evidence(pkg: str | Path, project: str | Path, refs: dict[str, str], hand_grid: str | Path,
                     out: str | Path, sections: list[str] | None = None,
                     progress: Callable[[str], Any] = print) -> dict[str, Any]:
    pkg, project, out = Path(pkg), Path(project), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    resp = out / "results.json"
    results = json.loads(resp.read_text()) if resp.is_file() else {}
    t0 = time.perf_counter()
    eng = engine(pkg)
    ctx = {"pkg": pkg, "project": project, "refs": refs, "hand_grid": Path(hand_grid), "out": out, "eng": eng,
           "progress": progress}
    progress(f"engine built ({time.perf_counter() - t0:.1f} s)")
    for name in sections or list(SECTIONS):
        if name not in SECTIONS:
            continue
        t1 = time.perf_counter()
        progress(f"section {name} ...")
        results[name] = {**SECTIONS[name](ctx), "wall_s": round(time.perf_counter() - t1, 1)}
        progress(f"section {name} done ({results[name]['wall_s']} s)")
        resp.write_text(json.dumps(results, indent=1, default=_json))
    results["_meta"] = {"package": str(pkg), "artistic_review_required": True,
                        "note": "AutoRig renders and measurements only; pending drawings appear only in images "
                                "labelled REVIEW PREVIEW / PENDING; Moho not run"}
    resp.write_text(json.dumps(results, indent=1, default=_json))
    return results


# -- poses, gestures, garments, animation, persistence, export (sections 14-20) -------------------------------
POSES_15 = [("A", "T-pose", None), ("B", "relaxed standing", ("relaxed_standing", "default", False)),
            ("C", "weight-shifted standing", ("weight_shift", "default", False)),
            ("D", "right pointing", ("point_right", "default", False)),
            ("E", "left pointing", ("point_left", "default", False)),
            ("F", "subtle shrug", "shrug_subtle"), ("G", "expressive shrug", "shrug_expressive"),
            ("H", "hands on hips", "hands_on_hips"), ("I", "arm raised", ("arm_raised", "default", False)),
            ("J", "elbow bent (deep)", ("deep_elbow", "default", False)), ("K", "walking", ("walk", "default", False)),
            ("L", "running", ("run", "default", False)), ("M", "sitting", ("sit", "default", False)),
            ("N", "squatting", ("squat", "default", False)), ("O", "forward leaning", ("lean_forward", "default", False))]
STATUS_38 = ("SUPPORTED", "SUPPORTED_WITH_CORRECTIONS", "REQUIRES_ALTERNATIVE_ARTWORK", "NEEDS_APPROVAL", "UNSUPPORTED")


def _shrug_t() -> float:
    from .artistic.transitions import acting_test_keys

    return next(k.t for k in acting_test_keys() if k.label == "shrug") + 0.3


def _shrug(eng, vis, kind: str, review: bool) -> dict[str, Any]:
    from .performance import scenarios
    from .performance.director import PerformanceDirector

    perf = scenarios.acting_upgrade(shrug=kind)
    perf.allow_pending = review
    d = PerformanceDirector(eng, vis, perf)
    r = d.solve(_shrug_t(), render=True, corrective_mode="all" if review else "auto")
    r["_status"] = d.library.status("shrug")
    return r


def _hands_txt(r: dict[str, Any]) -> str:
    return " / ".join(f"{SIDE_NAME[s]} {r['hands'][s]['pose']}{' (PENDING)' if r['hands'][s].get('pending_review') else ''}"
                      for s in ("r", "l"))


def status_38(review_cls: str, prod_cls: str, pending: list[str]) -> str:
    """Phase 3.8 vocabulary from the artistic feasibility classes of the review and production renders."""
    if review_cls in ("REQUIRES_NEW_VIEW", "REQUIRES_ALTERNATIVE_ARTWORK"):
        return "REQUIRES_ALTERNATIVE_ARTWORK"
    if review_cls == "UNSUPPORTED":
        return "UNSUPPORTED"
    if pending or prod_cls != review_cls and prod_cls in ("REQUIRES_ALTERNATIVE_ARTWORK", "REQUIRES_NEW_VIEW"):
        return "NEEDS_APPROVAL"
    return review_cls


def section_poses(ctx: dict[str, Any]) -> dict[str, Any]:
    """Step 14: the 15 poses, each rendered in review (pending previewed, labelled) and production (approved and
    original artwork only), classified by the Phase 3.5 ArtworkFeasibilityEngine."""
    from .artistic.candidates import assess, render_pose
    from .artistic.layer_order import DynamicLayerOrderController
    from .artistic.poses import ArtisticPosePlanner, mirror, variant

    out = ctx["out"]
    eng, vis = ctx["eng"]
    planner = ArtisticPosePlanner(eng.profile)
    tiles, recs = [], {}
    for code, title, spec in POSES_15:
        t0 = time.perf_counter()
        if spec is None:
            img = identity_render(eng, vis)["image"]
            rec = {"status": "SUPPORTED", "review_class": "SUPPORTED", "production_class": "SUPPORTED",
                   "note": "the drawn pose: original pixels, bit-exact", "pending_approval": [], "missing": []}
        elif isinstance(spec, str) and spec.startswith("shrug"):
            kind = spec.split("_")[1]
            rv, pr = _shrug(eng, vis, kind, True), _shrug(eng, vis, kind, False)
            img = rv["image"]
            st = rv["_status"]
            subst = (pr.get("performance") or {}).get("substitution") or (pr.get("performance") or {}).get("substitutions")
            prod_hands = _hands_txt(pr)
            if kind == "subtle":
                rec = {"status": "SUPPORTED", "review_class": "SUPPORTED", "production_class": "SUPPORTED",
                       "note": f"SHRUG_SUBTLE: original open palms ({prod_hands})", "pending_approval": [], "missing": []}
            else:
                rec = {"status": "REQUIRES_ALTERNATIVE_ARTWORK", "review_class": "REVIEW_ONLY",
                       "production_class": "NEEDS_ARTWORK (palm_up)",
                       "note": f"review: {_hands_txt(rv)}; production shows SHRUG_SUBTLE ({prod_hands}); library "
                               f"status {st.get('status')}", "pending_approval": ["cupped (both sides)"],
                       "missing": ["palm_up open hand (both sides)"], "substitution": subst}
        elif spec == "hands_on_hips":
            rv = eng.pose("hands_on_hips", visible=vis, allow_pending=True, corrective_mode="all")
            pr = eng.pose("hands_on_hips", visible=vis, allow_pending=False, corrective_mode="auto")
            img = rv["image"]
            fr, fp = rv["feasibility"], pr["feasibility"]
            rc = fr.get("class") or fr.get("status")
            pc = fp.get("class") or fp.get("status")
            miss = [m.get("asset") for m in fr.get("missing", []) if m.get("state", "missing") == "missing"]
            pend = [m.get("asset") for m in fr.get("missing", []) if m.get("state") == "pending_review"]
            rec = {"review_class": rc, "production_class": pc, "missing": miss,
                   "pending_approval": pend + [f"hand {s}: {h['drawing']}" for s, h in rv["hands"].items()
                                               if h.get("pending_review")],
                   "note": "motion-preset feasibility (no artistic variant for this pose); production shows the "
                           "original hands"}
            rec["status"] = ("REQUIRES_ALTERNATIVE_ARTWORK" if miss else
                             "NEEDS_APPROVAL" if rec["pending_approval"] else
                             "SUPPORTED_WITH_CORRECTIONS" if pc == "SUPPORTED_WITH_CORRECTIONS" else "SUPPORTED")
        else:
            action, var, mir = spec
            ap = variant(action, var)
            if mir:
                ap = mirror(ap)
            rv = render_pose(eng, vis, ap, preview_pending=True, corrective_mode="all", planner=planner,
                             layer_order=DynamicLayerOrderController())
            pr = render_pose(eng, vis, ap, preview_pending=False, corrective_mode="auto", planner=planner,
                             layer_order=DynamicLayerOrderController())
            ar, apr = assess(eng, ap, rv), assess(eng, ap, pr)
            img = rv["image"]
            fz, fzp = ar["feasibility"], apr["feasibility"]
            rec = {"review_class": fz["class"], "production_class": fzp["class"], "missing": fz["missing"],
                   "pending_approval": fz["pending_approval"] + [f"hand {s}" for s in ar["pending_hands"]],
                   "reasons": fz["reasons"][:4], "silhouette_warnings": [w.get("kind") or w.get("type") or str(w)
                                                                       for w in ar["silhouette"].get("warnings", [])][:6],
                   "deformation_risk": ar["deformation_risk"], "hands_review": ar["hands"], "hands_production": apr["hands"]}
            rec["status"] = status_38(fz["class"], fzp["class"], rec["pending_approval"])
        rec["wall_s"] = round(time.perf_counter() - t0, 1)
        recs[code] = {"title": title, **rec}
        col = {"SUPPORTED": (20, 120, 40), "SUPPORTED_WITH_CORRECTIONS": (20, 100, 150),
               "NEEDS_APPROVAL": (190, 110, 0)}.get(rec["status"], PENDING_RED)
        lines = [f"{code}. {title}", rec["status"], f"review: {rec['review_class']}  production: {rec['production_class']}",
                 ("missing: " + ", ".join(map(str, rec["missing"])))[:70] if rec.get("missing") else "missing: none",
                 ("pending: " + ", ".join(map(str, rec["pending_approval"])))[:70] if rec.get("pending_approval") else ""]
        tiles.append(labelled(figure(img, 430), lines, 330, [None, col, None, None, (190, 110, 0)]))
        ctx["progress"](f"  pose {code} {title}: {rec['status']}")
    save(grid(tiles, 5, "17 Pose contact sheet (REVIEW PREVIEW renders: pending drawings and corrections previewed; "
                        "status from the review AND the production render)"), out / "17_pose_contact_sheet.png", 2600)
    return {"images": ["17_pose_contact_sheet.png"], "poses": recs}


def section_gestures(ctx: dict[str, Any]) -> dict[str, Any]:
    """14 (pointing: automatic hand selection in review and production) and 15 (shrugs)."""
    from .artistic.candidates import render_pose
    from .artistic.layer_order import DynamicLayerOrderController
    from .artistic.poses import ArtisticPosePlanner, mirror, variant
    from .performance import shoulders

    out = ctx["out"]
    eng, vis = ctx["eng"]
    planner = ArtisticPosePlanner(eng.profile)
    tiles, rec = [], {}
    for side, ap in (("r", variant("point_right")), ("l", mirror(variant("point_right")))):
        for review in (True, False):
            r = render_pose(eng, vis, ap, preview_pending=review, corrective_mode="all" if review else "auto",
                            planner=planner, layer_order=DynamicLayerOrderController())
            dec = r["hand_selection"]["decisions"][side]
            h = r["hands"][side]
            key = f"{SIDE_NAME[side]}_pointing_{'review' if review else 'production'}"
            rec[key] = {"action": dec.get("action"), "chosen": dec.get("chosen"), "shown": h["pose"], "cid": h["cid"],
                        "pending": h.get("pending_review"), "needs_artwork": dec.get("needs_artwork"),
                        "fallback": dec.get("fallback"), "explanation": (dec.get("explanation") or [])[:3]}
            j, pad = r["solve"]["joints_posed"], r["pad"]
            close = crop_at(r["image"], j[f"hand_{side}"][0] + pad, j[f"hand_{side}"][1] + pad, 170, 170, 1.8)
            lines = [f"{SIDE_NAME[side]} pointing, {'REVIEW PREVIEW' if review else 'PRODUCTION (approved + original only)'}",
                     f"automatic selection: action {dec.get('action')} -> {h['pose']} ({h['cid'].split('/')[-1]})"
                     + (" PENDING" if h.get("pending_review") else ""),
                     f"needs: {dec.get('needs_artwork')}  fallback: {dec.get('fallback')}"[:90]]
            tiles.append(labelled(grid([figure(r["image"], 420), close], 2, bg=(255, 255, 255), gap=4), lines, 680))
    save(grid(tiles, 2, "14 Pointing: the hand engine chooses the pending pointing drawing in review; production keeps "
                        "the original hand and reports NEEDS_APPROVAL (left pointing is a mirrored copy)"),
         out / "14_pointing.png", 2400)
    # shrugs
    stiles = []
    neutral = render_pose(eng, vis, variant("relaxed_standing"), preview_pending=False, corrective_mode="auto",
                          planner=planner, layer_order=DynamicLayerOrderController())
    nref = shoulders.measure(neutral["image"], neutral["solve"]["joints_posed"], neutral["pad"])
    for kind, review, title in (("subtle", False, "SHRUG_SUBTLE, production"),
                                ("expressive", True, "SHRUG_EXPRESSIVE, REVIEW ONLY (pending cupped hands)"),
                                ("expressive", False, "SHRUG_EXPRESSIVE, production (NEEDS_ARTWORK -> shown subtle)")):
        r = _shrug(eng, vis, kind, review)
        m = shoulders.measure(r["image"], r["solve"]["joints_posed"], r["pad"])
        rise = {s: round(nref[s]["top_y"] - m[s]["top_y"], 1) if m[s].get("top_y") is not None and nref[s].get("top_y") is not None else None
                for s in ("r", "l")}
        k = f"shrug_{kind}_{'review' if review else 'production'}"
        rec[k] = {"hands": {s: {"pose": r["hands"][s]["pose"], "cid": r["hands"][s]["cid"],
                                "pending": r["hands"][s].get("pending_review")} for s in ("r", "l")},
                  "shoulders": m, "rise_vs_neutral_px": rise, "library_status": r["_status"]}
        j, pad = r["solve"]["joints_posed"], r["pad"]
        cx = (j["upper_arm_r"][0] + j["upper_arm_l"][0]) / 2 + pad
        upper = crop_at(r["image"], cx, j["neck"][1] + pad + 150, 420, 330, 1.0)
        lines = [title, f"hands: {_hands_txt(r)}",
                 f"shoulder rise vs relaxed r/l {rise['r']}/{rise['l']} px; slope r/l {m['r'].get('slope_deg')}/"
                 f"{m['l'].get('slope_deg')} deg; hump r/l {m['r'].get('hump_px')}/{m['l'].get('hump_px')} px"]
        stiles.append(labelled(grid([figure(r["image"], 430), upper], 2, bg=(255, 255, 255), gap=4), lines, 760))
    save(grid(stiles, 3, "15 Shrug: subtle (supported) and expressive (no approved palm-up hand: review only; "
                         "production falls back to the subtle shrug and reports NEEDS_ARTWORK)"),
         out / "15_shrug.png", 2600)
    rec["shoulder_reference_relaxed"] = nref
    return {"images": ["14_pointing.png", "15_shrug.png"], **rec}


def section_garments(ctx: dict[str, Any]) -> dict[str, Any]:
    """Steps 15-16: corrective assets of the new character (pending), before/after previews, and whether the arms
    drag the lapels, chain (rigid 'tie' slot) or crest (badge)."""
    from .artistic.candidates import render_pose
    from .artistic.layer_order import DynamicLayerOrderController
    from .artistic.poses import ArtisticPosePlanner, variant

    out, pkg = ctx["out"], ctx["pkg"]
    eng, vis = ctx["eng"]
    planner = ArtisticPosePlanner(eng.profile)
    idx_p = pkg / "analysis" / "corrective_assets.json"
    assets = json.loads(idx_p.read_text())["assets"] if idx_p.is_file() else []
    thumbs = []
    latest: dict[str, dict[str, Any]] = {}
    for a in assets:  # append-only index: the last entry of an asset id is its latest version
        latest[str(a.get("asset_id"))] = a
    for a in list(latest.values())[:24]:
        p = pkg / str(a.get("file") or "")
        if p.is_file():
            im = np.asarray(Image.open(p).convert("RGBA"))
            ys, xs = np.nonzero(im[..., 3])
            if len(xs):
                cut = im[max(0, ys.min() - 4):ys.max() + 5, max(0, xs.min() - 4):xs.max() + 5]
                thumbs.append(labelled(fit(white(cut, (235, 235, 240)), 160, 120),
                                       [f"{a.get('asset_id')} ({Path(str(a.get('file'))).stem.split('@')[-1]})"[:28],
                                        str(a.get("provenance", ""))[:26], "PENDING REVIEW"], 170))
    cases = [("relaxed_standing", "feet: trouser cuff over the shoe (shoe-top extension)"),
             ("arm_raised", "armpit / shoulder transition"), ("deep_elbow", "deep elbow fold"),
             ("squat", "knee bend")]
    rows, rec = [], {"assets": [{"asset_id": a.get("asset_id"), "category": a.get("category"),
                                 "provenance": a.get("provenance"), "file": a.get("file")} for a in latest.values()],
                     "cases": {}}
    for action, why in cases:
        ap = variant(action)
        offr = render_pose(eng, vis, ap, preview_pending=True, corrective_mode="off", planner=planner,
                           layer_order=DynamicLayerOrderController())
        onr = render_pose(eng, vis, ap, preview_pending=True, corrective_mode="all", planner=planner,
                          layer_order=DynamicLayerOrderController(), evaluate=True)
        shown = sorted({str(c.get("asset_id") or c.get("id")) for c in (onr.get("correctives") or [])
                        if isinstance(c, dict) and c.get("shown")})
        ge = onr.get("garment_eval") or {}
        parts = ge.get("parts") or {}
        drag = {p: {"status": parts[p]["status"], **{k: v["value"] for k, v in parts[p]["checks"].items()
                                                      if k in ("residual_max_px", "residual_rms_px", "local_deviation_px")}}
                for p in ("lapels", "tie", "badge", "shirt_collar") if p in parts}
        rec["cases"][action] = {"why": why, "correctives_drawn": shown, "gaps_off": len(offr["tears"]["exposed_gap"]),
                                "gaps_on": len(onr["tears"]["exposed_gap"]), "chest_details": drag}
        box_c = {"relaxed_standing": ("foot_r", 0, 0), "arm_raised": ("upper_arm_r", 0, 40),
                 "deep_elbow": ("forearm_r", 0, 0), "squat": ("shin_r", 0, 0)}[action]
        j, pad = onr["solve"]["joints_posed"], onr["pad"]
        cx, cy = j[box_c[0]][0] + pad + box_c[1], j[box_c[0]][1] + pad + box_c[2]
        a = crop_at(offr["image"], cx, cy, 200, 200, 1.6)
        b = crop_at(onr["image"], cx, cy, 200, 200, 1.6)
        lines = [f"{action}: {why}", f"left: correctives off; right: REVIEW PREVIEW with pending correctives "
                                     f"({', '.join(shown)[:60] or 'none drawn'})",
                 "chest details: " + ", ".join(f"{k} {v['status']}" for k, v in drag.items())]
        rows.append(labelled(grid([figure(onr["image"], 320), a, b], 3, bg=(255, 255, 255), gap=4), lines, 1000))
    head = grid(thumbs, 8, "corrective assets generated for this character (geometric, from its own pixels; all "
                           "PENDING REVIEW)") if thumbs else Image.new("RGB", (600, 40), BG)
    save(grid([head] + rows, 1, "16 Garment corrections on the new uniform"), out / "16_garment_corrections.png", 2400)
    return {"images": ["16_garment_corrections.png"], **rec}


def acting_performance(eng):
    """The Phase 3.8 acting sequence: T-pose, lower both arms, shift weight, raise the right arm, point, hold,
    shrug (subtle), relax. Keys reuse the Phase 3.5/3.6 acting recipes; the T-pose key is the drawn arm angle."""
    from .artistic.transitions import acting_test_keys
    from .motion.solver import PoseParams
    from .performance import scenarios
    from .performance.sequencer import Key
    from .performance.director import Performance

    rest = eng.solver.anatomical(PoseParams.identity(len(eng.sk.bones)))
    ak = {k.label: scenarios.params_of(k.pose) for k in acting_test_keys()}
    stand = dict(ak["stand"])
    tpose = {**stand, "weight_left": 0.5, "contrapposto": 0.0, "head": 0.0, "free_knee": 4.0, "clavicle_r": 0.0,
             "clavicle_l": 0.0}
    for s in ("r", "l"):
        tpose.update({f"arm_{s}_abduction": round(rest[f"upper_arm_{s}_abduction"], 1),
                      f"arm_{s}_flexion": round(rest[f"forearm_{s}_flexion"], 1), f"arm_{s}_wrist": 0.0})
    R = scenarios.ACTING_RECIPES
    spec = [(0.0, tpose, {"r": "open_palm", "l": "open_palm"}, "T-pose", 0.5, (None, None, 0.0, False)),
            (1.4, stand, {"r": "relaxed", "l": "relaxed"}, "lower both arms", 0.35, (None, None, 0.0, False)),
            (2.3, ak["weight shift"], {}, "shift weight", 0.3, R["weight shift"]),
            (3.2, ak["raise arm"], {"r": "open_palm"}, "raise right arm", 0.3, R["raise arm"]),
            (4.1, ak["point"], {"r": "pointing"}, "point", 0.9, R["point"]),
            (5.6, {**stand, **scenarios.SHRUG_LOW}, {"r": "shrug_subtle", "l": "shrug_subtle"}, "shrug", 0.5, R["shrug"]),
            (6.9, stand, {"r": "relaxed", "l": "relaxed"}, "relax", 0.6, R["relax"])]
    keys = []
    for i, (t, p, hands, label, hold, rec) in enumerate(spec):
        recipe, side, ovs, head_lead = rec
        keys.append(Key(t, dict(p), dict(hands), label, hold, anticipation=recipe, side=side, overshoot=ovs,
                        head_lead=head_lead, id=f"n{i}", pose={"base": "relaxed_standing"}).to_json())
    return Performance(name="tough_student_acting", fps=12.0, base_keys=keys, idle=0.4,
                       notes=["Phase 3.8 acting test on the new character; pending hand drawings previewed and "
                              "labelled; nothing approved"])


def section_animation(ctx: dict[str, Any]) -> dict[str, Any]:
    from .performance.director import PerformanceDirector
    from .performance.evaluate import PerformanceQualityEvaluator

    out = ctx["out"]
    eng, vis = ctx["eng"]
    perf = acting_performance(eng)
    d = PerformanceDirector(eng, vis, perf)
    times = d.frames()
    if ctx.get("quick"):
        times = times[::4]
    frames, info, t0 = [], [], time.perf_counter()
    for t in times:
        r = d.solve(t, render=True, corrective_mode="all")
        frames.append(r["image"])
        pf = r.get("performance") or {}
        info.append({"t": round(t, 3), "hands": {s: {"shown": h.get("shown"), "status": h.get("status")}
                                                 for s, h in (pf.get("hands") or {}).items()},
                     "key": pf.get("key") or pf.get("segment")})
    render_s = time.perf_counter() - t0
    ys0, xs0, ys1, xs1 = 10**9, 10**9, 0, 0
    for f in frames:
        ys, xs = np.nonzero(f[..., 3])
        ys0, xs0, ys1, xs1 = min(ys0, ys.min()), min(xs0, xs.min()), max(ys1, ys.max()), max(xs1, xs.max())
    box = (max(0, xs0 - 10), max(0, ys0 - 10), xs1 + 11, ys1 + 11)
    gif = []
    keymarks = [(k["t"], k["label"]) for k in perf.base_keys]
    for f, inf in zip(frames, info):
        im = white(f[box[1]:box[3], box[0]:box[2]])
        im = fit(im, 10_000, 520)
        lab = next((l for tk, l in reversed(keymarks) if tk <= inf["t"] + 1e-6), "")
        hands = " / ".join(f"{SIDE_NAME[s]} {h['shown']}{'*' if h['status'] == 'pending_review' else ''}"
                           for s, h in inf["hands"].items())
        canvas = Image.new("RGB", (im.width, im.height + 34), (255, 255, 255))
        canvas.paste(im, (0, 34))
        dd = ImageDraw.Draw(canvas)
        dd.text((4, 3), f"{inf['t']:.2f} s  {lab}   REVIEW PREVIEW", fill=(20, 20, 30), font=FONT)
        dd.text((4, 18), f"hands {hands} (* pending review)", fill=PENDING_RED, font=FONT)
        gif.append(canvas)
    gif[0].save(out / "18_animation_preview.gif", save_all=True, append_images=gif[1:],
                duration=int(1000 / perf.fps * (4 if ctx.get("quick") else 1)), loop=0, optimize=True)
    # production status per segment: the same performance without pending drawings
    perf_p = acting_performance(eng)
    perf_p.allow_pending = False
    dp = PerformanceDirector(eng, vis, perf_p)
    segs = []
    for i, k in enumerate(perf.base_keys):
        t_mid = k["t"] + min(0.3, k.get("hold", 0.0) / 2 + 0.1)
        rp = dp.solve(t_mid)
        rr = d.solve(t_mid)
        ph = (rp.get("performance") or {}).get("hands") or {}
        rh = (rr.get("performance") or {}).get("hands") or {}
        pending = [s for s, h in rh.items() if h.get("status") == "pending_review"]
        segs.append({"key": k["label"], "t": k["t"],
                     "review_hands": {s: h.get("shown") for s, h in rh.items()},
                     "production_hands": {s: h.get("shown") for s, h in ph.items()},
                     "status": "review-only (pending drawings: " + ", ".join(f"{SIDE_NAME[s]} {rh[s].get('shown')}" for s in pending) + ")"
                     if pending else "production-supported (original/approved artwork)"})
    ev = PerformanceQualityEvaluator(d)
    motion = ev.motion()
    art = ev.artistic()
    sheet = [gif[i] for i in range(0, len(gif), max(1, len(gif) // 12))][:12]
    save(grid(sheet, 6, "18 Acting sequence, contact sheet of the GIF (AutoRig renders; REVIEW PREVIEW)"),
         out / "18_animation_contact_sheet.png", 2600)
    return {"images": ["18_animation_preview.gif", "18_animation_contact_sheet.png"], "frames": len(frames),
            "fps": perf.fps, "render_s_per_frame": round(render_s / max(len(frames), 1), 2), "segments": segs,
            "motion_quality": motion, "artistic_hints": art, "performance": perf.to_json()}


def section_reload(ctx: dict[str, Any]) -> dict[str, Any]:
    """Step 20: edit a scratch copy of the package through the editor project (the same operations the UI sends),
    save, drop every object, reopen from disk and compare the state, the files and a render."""
    import hashlib
    import tempfile

    from .editor.project import VERSION, EditorProject

    out, pkg = ctx["out"], ctx["pkg"]
    big = {"motion", "garments", "ik_switch", "correctives", "artistic", "performance", "hand_selection",
           "reference_motion", "closeups", "legacy", "poses", "corrective"}
    work = Path(tempfile.mkdtemp(prefix="p38_reload_"))
    dst = work / "character_package"
    shutil.copytree(pkg, dst, ignore=lambda d, names: [n for n in names if Path(d).name == "natural_pose" and n in big]
                    + [n for n in names if n == "editor"])
    eng0, vis0 = engine(dst)
    lib = "tough_student_hands_v1"
    hb = eng0.sk.bone("upper_arm_r")
    ops = [
        {"type": "bone.set", "name": "upper_arm_r", "fields": {"head": [float(hb.head[0]) + 3, float(hb.head[1]) - 2]}},
        {"type": "mask.edit", "layer": "torso", "mode": "subtract", "transfer_to": "upper_arm_r",
         "shape": {"type": "stroke", "points": [[float(hb.head[0]) + 30, float(hb.head[1]) + 20]], "radius": 4, "hardness": 1}},
        {"type": "weights.paint", "layer": "torso", "bone": "clavicle_r",
         "center": [float(hb.head[0]) + 60, float(hb.head[1]) + 60], "radius": 25.0, "strength": 0.4},
        {"type": "approval.set", "item": f"handstore:{lib}/right_pointing@v1", "status": "approved"},
        {"type": "approval.set", "item": f"handstore:{lib}/left_fist@v1", "status": "rejected"},
        {"type": "handstore.placement", "uid": f"{lib}/right_thumbs_up", "fields": {"dx": 2, "dy": -1, "rot": -3}},
        {"type": "handmeta.set", "key": f"{lib}/right_gripping", "field": "note", "value": "Phase 3.8 reload test"},
        {"type": "switch.set", "group": "hand_r", "variant": "fist", "auto": False},
        {"type": "correctives.mode", "mode": "all"},
        {"type": "reference.select", "ref_id": "synthetic_pointing"},
    ]
    applied, refused = [], []
    p = EditorProject(dst)
    for op in ops:
        try:
            p.submit(op)
            applied.append(op["type"])
        except Exception as exc:  # an op this package cannot take is reported, not hidden
            refused.append({"op": op["type"], "error": str(exc)[:160]})
    p.undo()
    p.redo()
    ver = p.save_version("phase 3.8 reload test")
    st1 = p.state()
    eng1, vis1 = engine(dst)
    r1 = eng1.pose("pointing_speaking", visible=vis1, allow_pending=True)
    del p, eng1
    p2 = EditorProject(dst)
    st2 = p2.state()
    eng2, vis2 = engine(dst)
    r2 = eng2.pose("pointing_speaking", visible=vis2, allow_pending=True)
    same_state = json.dumps(st1, sort_keys=True, default=str) == json.dumps(st2, sort_keys=True, default=str)
    same_px = bool(np.array_equal(r1["image"], r2["image"]))
    proj_json = json.loads((dst / "editor" / "project.json").read_text())
    absolute = [s for s in _strings(proj_json) if s.startswith("/") or s.startswith(str(work))]
    files = {"source": (dst / "source" / "original.jpg").is_file(),
             "hand_store_index": (dst / "hand_store" / "index.json").is_file(),
             "corrective_index": (dst / "analysis" / "corrective_assets.json").is_file(),
             "hand_metadata": (dst / "analysis" / "hand_asset_metadata.json").is_file(),
             "artwork_manifest": (dst / "artwork" / "manifest.json").is_file()}
    src_sha = hashlib.sha256((dst / "source" / "original.jpg").read_bytes()).hexdigest()
    from .handstore import HandStore

    verify = HandStore(dst).verify()
    rec = {"ops_applied": applied, "ops_refused": refused, "schema_version": proj_json.get("schema_version"),
           "code_schema_version": VERSION, "state_equal_after_reopen": same_state,
           "render_identical_after_reopen": same_px, "absolute_paths_in_project": absolute[:5],
           "files_present": files, "source_sha256": src_sha, "hand_store_verify": verify,
           "saved_version": ver if isinstance(ver, (str, int)) else (ver or {}).get("id"),
           "approvals_after_reopen": {k: v.get("status") for k, v in st2["approvals"].items() if k.startswith("handstore:")},
           "hands_shown": {s: r2["hands"][s]["pose"] + (" (pending)" if r2["hands"][s].get("pending_review") else "")
                           for s in ("r", "l")},
           "note": "scratch copy only: the committed package's editor project has no edits and no approvals"}
    a = figure(r1["image"], 420)
    b = figure(r2["image"], 420)
    lines = [f"ops applied through EditorProject.submit: {len(applied)} ({', '.join(applied)})"[:150],
             f"refused: {refused}"[:150], f"project schema v{rec['schema_version']} (code v{VERSION}); "
                                          f"state equal after reopen: {same_state}; render identical: {same_px}",
             f"absolute paths in project.json: {len(absolute)}; files present: {all(files.values())}; "
             f"hand store hashes verified: {verify if isinstance(verify, bool) else verify}"[:150],
             "scratch copy: the approve/reject operations are a persistence test, not artistic approvals"]
    save(labelled(grid([labelled(a, ["before closing"]), labelled(b, ["after reopening"])], 2, bg=(255, 255, 255)),
                  lines, 1000), out / "19_project_reload.png")
    shutil.rmtree(work, ignore_errors=True)
    return {"images": ["19_project_reload.png"], **rec}


def _strings(o) -> list[str]:
    if isinstance(o, str):
        return [o]
    if isinstance(o, dict):
        return [s for v in o.values() for s in _strings(v)]
    if isinstance(o, list):
        return [s for v in o for s in _strings(v)]
    return []


def section_export(ctx: dict[str, Any]) -> dict[str, Any]:
    from .editor.artwork import ArtworkModel
    from .editor.export import export_package, validate_package
    from .editor.project import EditorProject

    out, pkg = ctx["out"], ctx["pkg"]
    dest = ctx.get("export_dir") or pkg.parent / "moho_asset_package"
    r = export_package(EditorProject(pkg), ArtworkModel(pkg), dest)
    errs = validate_package(dest)
    man = r["manifest"]
    gates = r["gates"]["gates"]
    files = sorted(p.relative_to(dest).as_posix() for p in Path(dest).rglob("*") if p.is_file())
    W = 1500
    lines = [f"export: {dest.name}  overall {r['gates']['overall']}  production_ready {man['production_ready']}  "
             f"validate_package errors: {len(errs)}", f"{len(files)} files; manifest keys: "
             + ", ".join(sorted(man)[:14])]
    rows = [f"{g['level']:<13} {g['check']}: {g['detail']}"[:200] for g in gates]
    tested = ["layers, masks, bone hierarchy, pivots, weights, switch metadata, rig controls: written and validated "
              "against AutoRig's schemas (tested here)",
              "Moho behaviour of every item: NOT TESTED (native Moho not run; mappings proposed_unverified; no .moho)"]
    im = Image.new("RGB", (W, 40 + 15 * (len(lines) + len(rows) + len(tested) + 2)), (255, 255, 255))
    d = ImageDraw.Draw(im)
    y = 8
    d.text((8, y), "20 Export readiness (Moho-ready asset package; Phase 4 native exporter NOT started)",
           fill=(20, 20, 30), font=FONT)
    y += 20
    for t in lines + [""] + rows + [""] + tested:
        c = (20, 120, 40) if t.startswith("PASS") else PENDING_RED if t.startswith(("FAIL", "NEEDS", "BLOCK")) or "NOT" in t else (20, 20, 30)
        d.text((8, y), t, fill=c, font=FONT)
        y += 15
    save(im, out / "20_export_readiness.png")
    # approval and missing-artwork reports (aggregated from the export, the hand store and the evidence results)
    appr = {"generated_at_by": "autorig newchar-evidence (export section)", "rule": "approvals are user actions only",
            "items": {}}
    for k, v in (man.get("approvals") or {}).items():
        appr["items"][k] = v.get("status") if isinstance(v, dict) else v
    meta = _hand_meta(pkg)
    for cid, m in meta.items():
        if cid.startswith("store:"):
            appr["items"][m.get("approval_item") or cid] = (m.get("approval") or {}).get("state", "PENDING_REVIEW")
    idx_p = pkg / "analysis" / "corrective_assets.json"
    if idx_p.is_file():
        for a in json.loads(idx_p.read_text())["assets"]:
            ver = Path(str(a.get("file", ""))).stem.split("@")[-1]
            appr["items"].setdefault(f"corrective:{a['asset_id']}@{ver}", "pending_review")
    counts: dict[str, int] = {}
    for v in appr["items"].values():
        counts[str(v).lower()] = counts.get(str(v).lower(), 0) + 1
    appr["counts"] = counts
    (out / "approval_report.json").write_text(json.dumps(appr, indent=1, default=_json))
    res_p = out / "results.json"
    prev = json.loads(res_p.read_text()) if res_p.is_file() else {}
    missing = {"poses": {k: v.get("missing") for k, v in ((prev.get("poses") or {}).get("poses") or {}).items()
                         if v.get("missing")},
               "hands": ["palm_up (r, l): no anatomically correct open palm-up drawing",
                         "partially_closed (r, l)", "left pointing: only a mirrored copy of a right hand"],
               "facial": [x for x in (man.get("live") or {}).get("controls", []) if isinstance(x, dict)
                          and x.get("readiness") == "REQUIRES_NEW_ARTWORK"],
               "garments": ["arms-down shoulder / sleeve-cap drawing (sloped shoulder line)",
                            "deep elbow fold (> 125 deg)", "seated thighs, seat, trousers seated",
                            "torso lean forward (3/4)", "knee-drive thigh / tucked shin (running)"],
               "note": "artwork AutoRig cannot invent from the front-view reference; requests, not failures"}
    (out / "missing_artwork_report.json").write_text(json.dumps(missing, indent=1, default=_json))
    return {"images": ["20_export_readiness.png"], "export_dir": str(dest), "overall": r["gates"]["overall"],
            "approval_counts": counts,
            "production_ready": man["production_ready"], "validate_errors": errs, "gates": gates,
            "files": len(files)}


SECTIONS.update({"gestures": section_gestures, "poses": section_poses, "garments": section_garments,
                 "animation": section_animation, "reload": section_reload, "export": section_export})


def section_reference(ctx: dict[str, Any]) -> dict[str, Any]:
    """Step 18: the Phase 3.7 reference-motion evidence produced on THIS package by ``autorig reference-evidence``
    (synthetic references, labelled synthetic; the MediaPipe round trip on a render of this character), plus a
    bone-length check of a retargeted frame (retargeting moves angles only)."""
    from .performance.director import Performance, PerformanceDirector
    from .refmotion.retarget import ReferenceMotionRetargeter
    from .refmotion.store import MotionReferenceStore, default_root

    out, pkg = ctx["out"], ctx["pkg"]
    eng, vis = ctx["eng"]
    ev = pkg / "natural_pose" / "reference_motion" / "evidence"
    if not (ev / "results.json").is_file():
        return {"images": [], "status": "NOT_RUN", "reason": "run autorig reference-evidence on this package first"}
    res = json.loads((ev / "results.json").read_text())
    ims = [Image.open(ev / f"comparison_{k}.png").convert("RGB") for k in ("pointing", "shrug") if (ev / f"comparison_{k}.png").is_file()]
    if (ev / "roundtrip.png").is_file():
        ims.append(Image.open(ev / "roundtrip.png").convert("RGB"))
    save(grid([fit(i, 2200, 900) for i in ims], 1, "14b Reference-guided animation on the new character: SYNTHETIC "
                                                    "pointing and shrug references (labelled synthetic, not human "
                                                    "motion) and the MediaPipe round trip on a render of this "
                                                    "character"), out / "14b_reference_motion.png", 2400)
    # bone lengths: rest vs a retargeted frame (no bone is scaled)
    lengths = {}
    try:
        store = MotionReferenceStore(default_root())
        e = store.get("synthetic_pointing")
        rt = ReferenceMotionRetargeter().retarget(e["analysis"], {**e["track"].meta, **e["meta"]})
        d = PerformanceDirector(eng, vis, Performance.from_json(rt["performance"]))
        r = d.solve(d.duration * 0.5)
        J, T = r["solve"]["joints_posed"], r["solve"]["bone_tails_posed"]
        for b in eng.sk.bones:
            rest = float(np.hypot(*(b.tail - b.head)))
            posed = float(np.hypot(*np.subtract(T[b.name], J[b.name])))
            lengths[b.name] = round(posed - rest, 3)
    except Exception as exc:  # reported, not hidden
        lengths = {"error": str(exc)[:200]}
    refs = res.get("references", {})
    return {"images": ["14b_reference_motion.png"], "references": {
        k: {"ref_id": v.get("ref_id"), "keys": v.get("keys"), "feasibility": (v.get("feasibility") or {}).get("class"),
            "errors_rms": {c: e.get("rms") for c, e in (v.get("errors") or {}).items()},
            "depth_segments": v.get("depth_segments")} for k, v in refs.items()},
        "roundtrip": res.get("roundtrip"), "bone_length_change_px": lengths,
        "synthetic": "the pointing, shrug and walking references are synthetic; no human footage was available"}


SECTIONS["reference"] = section_reference
