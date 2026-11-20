"""Corrective artwork for natural posing: hand variants and armpit gussets.

Every asset here is pending review, and its provenance is recorded:
* **Reference-derived hands** (category R): the user's own drawings cut out of their character sheet
  (fist, thumbs-up, open hand). They are scaled to the reference hand; nothing is invented.
* **Constructed hand** (category C): a pointing hand made from the sheet's fist plus an index finger drawn
  in the character's linework style (colours and stroke width sampled from that fist).
* **Approximation** (category C): a palm-up hand for the shrug, made by foreshortening the reference's
  own open hand across its width (the palm seen edge-on).
* **Armpit gussets** (category C): when a raised arm opens the T-pose armpit corner, the opened notch, and
  only the notch, is filled behind the existing art with the neighbouring jacket colour and closed with
  the jacket's outline. The fill is computed per pose. Its outline polygon is exported in the clavicle
  bone's frame so it can become a Moho corrective shape keyed on the shoulder angle (proposed,
  unverified).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .skeleton import Skeleton

SKIN_HINT = (200, 120, 60)


def cut_out(sheet: np.ndarray, box, pad: int = 4) -> np.ndarray:
    """Cut a drawing off the sheet's near-white background: background connected to the crop border
    becomes transparent; anti-aliased edge pixels are un-mixed from the background colour."""
    x0, y0, x1, y1 = box
    crop = sheet[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad, :3].astype(np.float32)
    bg = np.median(np.concatenate([crop[0], crop[-1], crop[:, 0], crop[:, -1]]), axis=0)
    dist = np.abs(crop - bg).max(axis=2)
    near = dist < 60
    lab, _ = ndimage.label(near)
    border = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    outside = np.isin(lab, list(border))
    lum = crop @ np.array([0.299, 0.587, 0.114], np.float32)
    lbg = float(bg @ np.array([0.299, 0.587, 0.114]))
    alpha = np.where(outside, np.clip((lbg - lum) / max(lbg - 20.0, 1.0), 0.0, 1.0), 1.0)
    alpha[outside & (dist < 10)] = 0.0
    # JPEG noise leaves faint specks around the drawing: keep only the drawing itself
    alpha[alpha < 0.3] = 0.0
    lab2, n2 = ndimage.label(alpha > 0)
    if n2 > 1:
        big = np.argmax(np.bincount(lab2.ravel())[1:]) + 1
        alpha[~ndimage.binary_dilation(lab2 == big, iterations=1)] = 0.0
    a3 = np.maximum(alpha[..., None], 1e-3)
    rgb = np.where(outside[..., None], np.clip((crop - (1 - alpha[..., None]) * bg) / a3, 0, 255), crop)
    out = np.dstack([rgb, alpha * 255]).round().astype(np.uint8)
    out[alpha <= 0.0] = 0
    return out


def wrist_anchor(rgba: np.ndarray) -> np.ndarray:
    """Bottom-centre of the drawing (wrist end; the sheet's hands point up)."""
    ys, xs = np.nonzero(rgba[..., 3] > 127)
    yb = ys.max()
    sel = ys >= yb - 6
    return np.array([float(xs[sel].mean()), float(yb - 3)])


def _palette(rgba: np.ndarray) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int], float]:
    a = rgba[..., 3] > 200
    px = rgba[..., :3][a].astype(np.float32)
    lum = px @ np.array([0.299, 0.587, 0.114], np.float32)
    sat = px.max(axis=1) - px.min(axis=1)
    skin = np.median(px[(lum > 110) & (sat > 60)], axis=0)
    shade = np.median(px[(lum > 70) & (lum <= 110) & (sat > 40)], axis=0) if ((lum > 70) & (lum <= 110)).any() else skin * 0.8
    line = np.median(px[lum < 45], axis=0) if (lum < 45).any() else np.array([25, 18, 15.0])
    dark = a & (rgba[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32) < 60)
    width = float(np.median(ndimage.distance_transform_edt(dark)[dark]) * 2.0) if dark.any() else 2.5
    t = lambda v: tuple(int(round(c)) for c in v)
    return t(skin), t(shade), t(line), max(1.5, min(width, 5.0))


def pointing_from_fist(fist: np.ndarray, finger: np.ndarray, base_x: float = 0.62, base_y: float = 0.30
                       ) -> np.ndarray:
    """Compose a pointing hand from the user's own drawings: the sheet's fist with the index finger cut from
    the sheet's open hand, set behind the fist's top knuckle on the thumb side, pointing up (the hand
    direction). Nothing is drawn freehand."""
    fy, fx = np.nonzero(fist[..., 3] > 127)
    x0, x1, y0, y1 = fx.min(), fx.max(), fy.min(), fy.max()
    fw, fh = x1 - x0, y1 - y0
    # drop anything above the finger's own tip (e.g. the taller neighbouring finger's outline)
    fa = finger[..., 3] > 127
    mid = fa[:, fa.shape[1] // 3: 2 * fa.shape[1] // 3]
    if mid.any():
        tip = int(np.nonzero(mid.any(axis=1))[0].min())
        finger = finger.copy()
        finger[:max(0, tip - 1)] = 0
    # the curled fingers of the fist read chunkier than the straight fingers of the open hand: widen slightly
    fg = Image.fromarray(finger, "RGBA").convert("RGBa")
    fg = fg.resize((max(1, round(finger.shape[1] * 1.25)), max(1, round(finger.shape[0] * 1.1))),
                   Image.Resampling.LANCZOS)
    finger = np.asarray(fg.convert("RGBA"))
    gy, gx = np.nonzero(finger[..., 3] > 127)
    flen = gy.max() - gy.min()
    cx = int(round(x0 + base_x * fw))
    base = int(round(y0 + base_y * fh))
    y_paste = base - flen - gy.min()  # finger tip row lands at base - flen
    extra = max(0, -y_paste + 2)
    W = max(fist.shape[1], cx + finger.shape[1])
    canvas = Image.new("RGBA", (W, fist.shape[0] + extra), (0, 0, 0, 0))
    fcx = int(round((gx.min() + gx.max()) / 2))
    canvas.alpha_composite(Image.fromarray(finger, "RGBA"), (cx - fcx, y_paste + extra))
    canvas.alpha_composite(Image.fromarray(fist, "RGBA"), (0, extra))
    return np.asarray(canvas)


def place_hand(art: np.ndarray, anchor, scale: float, wrist, direction, mirror: bool, shape) -> np.ndarray:
    """Full-canvas RGBA rest layer: the drawing's wrist anchor on the rest wrist joint, its 'up' axis along
    the rest hand direction, scaled; mirrored for the other side."""
    u = np.asarray(direction, float) / (np.hypot(*direction) or 1.0)
    Mi = np.diag([-1.0, 1.0]) if mirror else np.eye(2)
    # forward: p = wrist + scale * Rp Mi (v - anchor); the rotation Rp sends the drawing's up (0, -1) to u and
    # its right (1, 0) - the thumb side of the sheet's right hands - to (-u_y, u_x)
    Rp = np.array([[-u[1], -u[0]], [u[0], -u[1]]])
    M = scale * Rp @ Mi
    Minv = np.linalg.inv(M)
    off = np.asarray(anchor, float) - Minv @ np.asarray(wrist, float)
    im = Image.fromarray(art, "RGBA").convert("RGBa")
    out = im.transform((shape[1], shape[0]), Image.Transform.AFFINE,
                       (Minv[0, 0], Minv[0, 1], off[0], Minv[1, 0], Minv[1, 1], off[1]),
                       resample=Image.Resampling.BICUBIC)
    return np.asarray(out.convert("RGBA"))


def _clean(layer: np.ndarray) -> np.ndarray:
    """Remove faint resampling fringe and stray specks: keep the main drawing only."""
    out = layer.copy()
    out[out[..., 3] < 40] = 0
    lab, n = ndimage.label(out[..., 3] > 0)
    if n > 1:
        sizes = np.bincount(lab.ravel())
        sizes[0] = 0
        out[~ndimage.binary_dilation(lab == int(np.argmax(sizes)), iterations=1)] = 0
    return out


def foreshorten(layer: np.ndarray, origin, direction, factor: float) -> np.ndarray:
    """Squash a full-canvas layer across the axis through ``origin`` along ``direction`` (palm seen edge-on)."""
    u = np.asarray(direction, float) / (np.hypot(*direction) or 1.0)
    n = np.array([-u[1], u[0]])
    M = np.eye(2) + (factor - 1.0) * np.outer(n, n)
    Minv = np.linalg.inv(M)
    o = np.asarray(origin, float)
    off = o - Minv @ o
    im = Image.fromarray(layer, "RGBA").convert("RGBa")
    out = im.transform((layer.shape[1], layer.shape[0]), Image.Transform.AFFINE,
                       (Minv[0, 0], Minv[0, 1], off[0], Minv[1, 0], Minv[1, 1], off[1]),
                       resample=Image.Resampling.BICUBIC)
    return np.asarray(out.convert("RGBA"))


def build_hand_variants(sk: Skeleton, layers: dict[str, np.ndarray], sources: dict[str, Any] | None,
                        base: Path) -> tuple[dict[str, np.ndarray], list[dict[str, Any]], dict[str, np.ndarray]]:
    """Returns (variant rest layers by id 'hand_<side>__<name>', manifest records, cut-out drawings)."""
    variants, records, drawings = {}, [], {}
    shape = next(iter(layers.values())).shape[:2]
    if sources:
        sheet = np.asarray(Image.open(base / sources["sheet"]).convert("RGB"))
        for name, box in sources["hands"].items():
            drawings[name] = cut_out(sheet, box)
        ref_area = float((layers["hand_r"][..., 3] > 127).sum()) if "hand_r" in layers else None
        open_area = float((drawings[sources["scale_from"]][..., 3] > 127).sum())
        k = math.sqrt(ref_area / open_area) if ref_area else 1.0
        if "fist" in drawings and "index_finger_box" in sources:
            fx0, fy0, fx1, fy1 = sources["index_finger_box"]
            finger = drawings[sources["scale_from"]][fy0:fy1, fx0:fx1].copy()
            drawings["point"] = pointing_from_fist(drawings["fist"], finger)
        for name, art in drawings.items():
            if name == sources["scale_from"]:
                continue
            anchor = wrist_anchor(art)
            for s in ("r", "l"):
                hb = sk.bone(f"hand_{s}")
                mirror = sources.get("drawn_as", "r") != s
                variants[f"hand_{s}__{name}"] = _clean(place_hand(art, anchor, k, hb.head, hb.tail - hb.head,
                                                                  mirror, shape))
            records.append({"id": f"hand__{name}", "type": "hand_variant",
                            "category": "R_composed_from_reference" if name == "point" else "R_reference_derived",
                            "source": ({"sheet": sources["sheet"], "crop_xyxy": sources["hands"]["fist"],
                                        "construction": "sheet fist + the index finger cut from the sheet's "
                                                        "open hand (both the user's drawings)"} if name == "point" else
                                       {"sheet": sources["sheet"], "crop_xyxy": sources["hands"][name]}),
                            "scale_to_reference": round(k, 3), "mirrored_for": [s for s in ("r", "l")
                                                                                if sources.get("drawn_as", "r") != s],
                            "status": "pending_review"})
    for s in ("r", "l"):  # forearm without the bits of the original hand Phase 1 left in it (beyond the wrist)
        if f"forearm_{s}" in layers:
            fa = layers[f"forearm_{s}"]
            ys, xs = np.nonzero(fa[..., 3] > 0)
            home = sk.home(f"forearm_{s}", np.stack([xs, ys], 1).astype(float))
            trimmed = fa.copy()
            hb = sk.bone(f"hand_{s}")
            depth = (xs - hb.head[0]) * hb.u[0] + (ys - hb.head[1]) * hb.u[1]
            # keep the cuff end (it runs a little past the wrist pivot); drop fragments deeper in the hand
            beyond = (home == sk.idx[f"hand_{s}"]) & (depth > 0.7 * hb.h)
            trimmed[ys[beyond], xs[beyond]] = 0
            variants[f"forearm_{s}__trim"] = trimmed
    for s in ("r", "l"):  # palm-up approximation from the reference's own open hand
        if f"hand_{s}" in layers:
            hb = sk.bone(f"hand_{s}")
            variants[f"hand_{s}__palm_up"] = _clean(foreshorten(layers[f"hand_{s}"], hb.head, hb.tail - hb.head, 0.5))
    records.append({"id": "hand__palm_up", "type": "hand_variant", "category": "C_approximation",
                    "source": {"from": "reference hand layers", "construction": "foreshortened to 50% across the "
                                                                               "hand axis (palm seen edge-on)"},
                    "status": "pending_review"})
    return variants, records, drawings


def armpit_gussets(render: dict[str, Any], tears: dict[str, list], sk: Skeleton, info: dict[str, Any],
                   dfm, pose, min_pairs: int = 6) -> list[dict[str, Any]]:
    """Bridge what a raised arm tore open at the armpit, and nothing else: each visible torn pair of
    rest-adjacent pixels is a segment between its two posed positions; the union of those segments (only
    transparent pixels, i.e. behind all art) is filled with the neighbouring jacket colour, and the part
    of it facing the background gets the jacket outline. Modifies render['image']."""
    img = render["image"]
    pad = render["pad"]
    alpha = img[..., 3] > 127
    segs = np.array(tears.get("segments", []), float).reshape(-1, 4)
    out = []
    for s in ("r", "l"):
        b = sk.bone(f"upper_arm_{s}")
        jx, jy = np.array(info["joints_posed"][b.name]) + pad
        R = 2.2 * b.h
        if not len(segs):
            continue
        mid = (segs[:, :2] + segs[:, 2:]) / 2
        near = segs[(mid[:, 0] - jx) ** 2 + (mid[:, 1] - jy) ** 2 <= R * R]
        if len(near) < min_pairs:
            continue
        canvas = Image.new("L", (alpha.shape[1], alpha.shape[0]), 0)
        d = ImageDraw.Draw(canvas)
        for ax, ay, bx, by in near:
            d.line((ax, ay, bx, by), fill=255, width=3)
        torn = np.asarray(canvas) > 0
        keep = ndimage.binary_dilation(torn, iterations=1) & ~alpha
        if keep.sum() < 4:
            continue
        ring = ndimage.binary_dilation(keep, iterations=6) & alpha
        px = img[..., :3][ring].astype(np.float32)
        lum = px @ np.array([0.299, 0.587, 0.114], np.float32)
        fill = np.median(px[lum >= np.percentile(lum, 35)], axis=0) if len(px) else np.array([42, 39, 48.0])
        line = np.median(px[lum <= np.percentile(lum, 10)], axis=0) if len(px) else np.array([8, 8, 10.0])
        width = 4
        img[keep] = (*[int(v) for v in fill], 255)
        outer = keep & ndimage.binary_dilation(~(img[..., 3] > 127), iterations=width)
        img[outer] = (*[int(v) for v in line], 255)
        ys, xs = np.nonzero(keep)
        pts = np.stack([xs - pad, ys - pad], 1).astype(float)
        poly = _hull(pts)
        Wc = dfm.world(sk.idx[f"clavicle_{s}"], pose)
        inv = np.linalg.inv(Wc)
        local = (poly @ inv[:2, :2].T + inv[:2, 2]) if len(poly) else poly
        out.append({"side": s, "pixels": int(keep.sum()), "torn_pairs": int(len(near)),
                    "fill_rgb": [int(v) for v in fill], "outline_rgb": [int(v) for v in line], "outline_px": width,
                    "polygon_posed_px": [[round(float(x), 1), round(float(y), 1)] for x, y in poly],
                    "polygon_clavicle_rest_frame_px": [[round(float(x), 1), round(float(y), 1)] for x, y in local]})
    return out


def _hull(pts: np.ndarray) -> np.ndarray:
    if len(pts) < 3:
        return pts
    from scipy.spatial import ConvexHull
    try:
        h = ConvexHull(pts)
        return pts[h.vertices]
    except Exception:  # degenerate (collinear) region
        return pts[[0, -1]]


def gusset_svg(record: dict[str, Any], width: int, height: int) -> str:
    pts = " ".join(f"{x},{y}" for x, y in record["polygon_posed_px"])
    f, l = record["fill_rgb"], record["outline_rgb"]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">'
            f'<!-- generated corrective armpit gusset (convex outline of the filled notch); pending review; '
            f'exact_reconstruction=false -->'
            f'<polygon points="{pts}" fill="rgb({f[0]},{f[1]},{f[2]})" stroke="rgb({l[0]},{l[1]},{l[2]})" '
            f'stroke-width="{record["outline_px"]}" stroke-linejoin="round"/></svg>')
