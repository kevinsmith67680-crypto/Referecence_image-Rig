"""Phase 3.5 art-directed joint corrections (elbow and knee), replacing the Phase 3.4 round caps.

A bent sleeve or trouser leg is not a ball. ``joint_fold_fill`` builds the correction per pose from the posed bones:
* **Outer contour:** the outer edges of the two segments (bone axes offset by the measured half-width) are extended
  to their corner and the corner is softened with a short fillet (a fraction of the width, not a disc). The sleeve
  keeps its width (no balloon) and the outline stays the character's black outline, drawn only where it borders the
  background.
* **Fill:** the fabric colour diffused from the surrounding drawn fabric.
* **Compression folds:** tapered strokes fan out from the inner crease toward the outer side. Count, length,
  width, spread and curvature come from the garment's material: stiff blazer wool gets two broad folds at the elbow,
  trouser twill gets three thinner, longer ones at the knee. They grow with the bend.

The folds are an overlay. They draw over the same limb's own fabric layers near the joint, and never over hands,
cuffs, decor or other parts. The overlay mask is recorded so that the leakage check can separate it from a real
protected-pixel violation. Like every corrective asset, the overlay is pending review until a person approves it.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy import ndimage

MATERIALS = {
    # folds: count, length (x half-width), base width px, spread (deg), curvature (x half-width)
    "blazer_wool": {"count": 2, "length": (1.05, 0.75), "width": 2.6, "spread": 28.0, "curve": 0.18, "alpha": 0.85,
                    "fillet": 0.85, "corner_max": 1.45},
    "trouser_twill": {"count": 3, "length": (1.25, 0.95, 0.7), "width": 2.0, "spread": 34.0, "curve": 0.22,
                      "alpha": 0.8, "fillet": 0.95, "corner_max": 1.3},
}


def _unit(v):
    n = float(np.hypot(*v))
    return np.asarray(v, float) / n if n > 1e-9 else np.asarray(v, float)


def _perp(v):
    return np.array([-v[1], v[0]])


def _poly_mask(shape, pts) -> np.ndarray:
    from PIL import Image, ImageDraw  # noqa: PLC0415

    im = Image.new("L", (shape[1], shape[0]), 0)
    ImageDraw.Draw(im).polygon([tuple(map(float, p)) for p in pts], fill=1)
    return np.asarray(im, bool)


def _stroke(shape, pts: np.ndarray, w0: float, w1: float) -> np.ndarray:
    """Antialiased tapered polyline alpha (width w0 at the start, w1 at the end)."""
    H, W = shape
    out = np.zeros((H, W), np.float32)
    n = len(pts)
    yy, xx = np.mgrid[:H, :W]
    for i in range(n - 1):
        p, q = pts[i], pts[i + 1]
        wd = w0 + (w1 - w0) * (i + 0.5) / max(n - 1, 1)
        x0, x1 = int(max(0, min(p[0], q[0]) - wd - 2)), int(min(W, max(p[0], q[0]) + wd + 3))
        y0, y1 = int(max(0, min(p[1], q[1]) - wd - 2)), int(min(H, max(p[1], q[1]) + wd + 3))
        if x1 <= x0 or y1 <= y0:
            continue
        X, Y = xx[y0:y1, x0:x1], yy[y0:y1, x0:x1]
        d = q - p
        L2 = float(d @ d) or 1e-9
        t = np.clip(((X - p[0]) * d[0] + (Y - p[1]) * d[1]) / L2, 0, 1)
        dist = np.hypot(X - (p[0] + t * d[0]), Y - (p[1] + t * d[1]))
        out[y0:y1, x0:x1] = np.maximum(out[y0:y1, x0:x1], np.clip(wd / 2 + 0.5 - dist, 0, 1))
    return out


NOTCH_PX = 4  # notches narrower than about 2 x NOTCH_PX at the joint are closed


def close_notches(solid: np.ndarray, allowed: np.ndarray, near: np.ndarray, radius: int = NOTCH_PX) -> np.ndarray:
    """Pixels of narrow notches in a silhouette: a morphological closing of ``solid`` with a disc of ``radius``,
    limited to ``allowed`` (empty pixels / the limb's own joint patch) and ``near`` (the joint). A gap wider than
    about 2 x radius is not touched, so this repairs a notch, it never fills a silhouette out."""
    if radius <= 0 or not solid.any():
        return np.zeros_like(solid)
    st = np.hypot(*np.mgrid[-radius:radius + 1, -radius:radius + 1]) <= radius
    pad = radius + 1
    closed = ndimage.binary_closing(np.pad(solid, pad), structure=st)[pad:-pad, pad:-pad]
    return closed & ~solid & allowed & near


def joint_fold_fill(window: np.ndarray, origin, P, a, b, w: float, params: dict[str, Any], weight: float,
                    fabric_mask: np.ndarray, over_mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """``window``: premultiplied canvas RGBA crop; ``origin``: its (x, y) in the canvas; ``P``: posed joint;
    ``a``/``b``: unit directions from the joint toward the parent and the child; ``w``: half-width at the joint;
    ``fabric_mask``: pixels of the limb's own fabric layers (fold overlay allowed); ``over_mask``: pixels the fill may
    cover (empty or the limb's own joint patch). Returns (premultiplied RGBA to composite *over*, overlay mask, info)."""
    from ..garmentrig.correctives import _outline_lum, local_fabric_fill  # noqa: PLC0415

    H, W = window.shape[:2]
    ox, oy = origin
    P = np.asarray(P, float) - [ox, oy]
    a, b = _unit(a), _unit(b)
    bend = float(np.degrees(np.arccos(np.clip(-(a @ b), -1.0, 1.0))))  # 0 = straight limb
    info = {"bend_deg": round(bend, 1)}
    out = np.zeros((H, W, 4), np.float32)
    if bend < 8.0 or weight <= 1e-3:
        return out, np.zeros((H, W), bool), info
    m = _unit(a + b)  # inner bisector
    na = _perp(a) if _perp(a) @ (-m) > 0 else -_perp(a)
    nb = _perp(b) if _perp(b) @ (-m) > 0 else -_perp(b)
    A0, B0 = P + na * w, P + nb * w
    # outer corner: intersection of the two outer edges, clamped for deep bends
    M = np.array([a, -b]).T
    mat = MATERIALS.get(params.get("material", "blazer_wool"), MATERIALS["blazer_wool"])
    X = P - m * w * 1.4
    if abs(np.linalg.det(M)) > 1e-6:
        t, u = np.linalg.solve(M, B0 - A0)
        Xc = A0 + t * a
        if np.hypot(*(Xc - P)) < 2.2 * w:
            X = Xc
    # the corner never pokes out further than corner_max half-widths (a soft knee/elbow, not a beak)
    dX = X - P
    if np.hypot(*dX) > mat["corner_max"] * w:
        X = P + dX / np.hypot(*dX) * mat["corner_max"] * w
    rf = mat["fillet"] * w
    A1 = X + _unit(A0 - X) * min(rf, float(np.hypot(*(A0 - X))))
    B1 = X + _unit(B0 - X) * min(rf, float(np.hypot(*(B0 - X))))
    tt = np.linspace(0, 1, 12)[:, None]
    corner = (1 - tt) ** 2 * A1 + 2 * (1 - tt) * tt * X + tt ** 2 * B1
    poly = np.vstack([P, A0, A1, corner, B1, B0])
    region = _poly_mask((H, W), poly) & over_mask
    # fill colour from the surrounding fabric; outline only toward the background
    opaque = (window[..., 3] > 0.5) & ~region
    fill = local_fabric_fill(window, opaque, region, np.asarray(params.get("fill_rgb", [40, 38, 48]), np.float32),
                             ring=10, dark_lum=_outline_lum(params) + 12.0)
    outline = np.asarray(params.get("outline_rgb", [20, 20, 24]), np.float32)
    bg = (window[..., 3] < 0.5) & ~region
    wd = float(params.get("outline_width_px", 3.0))
    a_fill = region.astype(np.float32)
    rgb = fill.copy()
    if bg.any():
        d = ndimage.distance_transform_edt(~bg)
        k = np.clip(wd + 0.5 - d, 0, 1) * region
        rgb = rgb * (1 - k[..., None]) + outline * k[..., None]
    # compression folds from the inner crease (the inner edges' intersection), fanning toward the outer side
    Ia, Ib = P - na * w, P - nb * w
    C = P + m * w * 0.9
    if abs(np.linalg.det(M)) > 1e-6:
        t2, _ = np.linalg.solve(M, Ib - Ia)
        Cc = Ia + t2 * a
        if np.hypot(*(Cc - P)) < 1.6 * w:
            C = Cc
    grow = float(np.clip((bend - 15.0) / 75.0, 0.0, 1.0))
    folds = np.zeros((H, W), np.float32)
    n = mat["count"]
    for i in range(n):
        ang = math.radians(mat["spread"] * ((i - (n - 1) / 2) / max(n - 1, 1)) * 2.0 if n > 1 else 0.0)
        d0 = -m
        dirv = np.array([d0[0] * math.cos(ang) - d0[1] * math.sin(ang), d0[0] * math.sin(ang) + d0[1] * math.cos(ang)])
        L = mat["length"][min(i, len(mat["length"]) - 1)] * w * grow
        if L < 3:
            continue
        start = C + dirv * 0.15 * w
        end = start + dirv * L
        ctrl = (start + end) / 2 + _perp(dirv) * mat["curve"] * w * (1 if i % 2 == 0 else -1)
        s = np.linspace(0, 1, 10)[:, None]
        pts = (1 - s) ** 2 * start + 2 * (1 - s) * s * ctrl + s ** 2 * end
        folds = np.maximum(folds, _stroke((H, W), pts, mat["width"] * (0.6 + 0.4 * grow), 0.5))
    folds *= mat["alpha"] * float(weight)
    overlay = (folds > 0.02) & fabric_mask & ~region
    fold_in_fill = folds * region
    folds = folds * (fabric_mask | region)
    # compose: the fill (over empty pixels / own patch) plus the fold strokes (over the limb's own fabric)
    a_out = np.clip(np.maximum(a_fill * float(weight), folds), 0, 1)
    col = rgb * (a_fill * float(weight))[..., None] + outline[None, None] * folds[..., None]
    col = col / np.maximum(a_out[..., None], 1e-6)
    out[..., :3] = col / 255.0 * a_out[..., None]
    out[..., 3] = a_out
    # background notches the clamped corner does not reach (between the two limb pieces at the elbow point), coloured
    # from the nearest drawn pixel (the outline there). The closing runs on the limb's own silhouette only, so a thin
    # gap between the limb and another part (arm and torso) is never bridged.
    yy, xx = np.mgrid[:H, :W]
    near = np.hypot(xx - P[0], yy - P[1]) < 1.9 * w
    solid = (window[..., 3] > 0.5) | (a_out > 0.5)
    limb = (fabric_mask & (window[..., 3] > 0.5)) | (region & (a_fill > 0))
    notch = close_notches(limb, over_mask & ~region & ~solid, near, int(params.get("notch_px", NOTCH_PX)))
    if notch.any():
        comp = out + window * (1.0 - out[..., 3:4])  # what the fill composites to (premultiplied)
        iy, ix = ndimage.distance_transform_edt(~solid, return_distances=False, return_indices=True)
        src = comp[iy, ix]
        rgb_n = src[..., :3] / np.maximum(src[..., 3:4], 1e-6)
        out[notch, :3] = rgb_n[notch] * float(weight)
        out[notch, 3] = float(weight)
    info.update({"fill_px": int(region.sum()), "overlay_px": int(overlay.sum()), "folds": n,
                 "fold_in_fill_px": int((fold_in_fill > 0.02).sum()), "notch_px": int(notch.sum()),
                 "corner": [round(float(v), 1) for v in X + [ox, oy]]})
    return out, overlay | (region & (a_fill > 0)) | notch, info


class ArtDirectedJointCorrection:
    """Generator of the procedural elbow/knee fold corrections (stored as corrective assets, pending review). They
    supersede the Phase 3.4 round caps of the same joint when shown."""

    def generate(self, eng, joint: str, side: str):
        from ..garmentrig.correctives import CorrectiveAsset, _fabric_colour, _outline_style  # noqa: PLC0415
        from ..phase2.skeleton import _extent  # noqa: PLC0415

        sk, src, masks = eng.sk, eng.source, eng.masks
        child, parent, layers, lo, hi, cat, mat = {
            "elbow": (f"forearm_{side}", f"upper_arm_{side}", [f"upper_arm_{side}", f"forearm_{side}"], 40.0, 90.0,
                      "elbows", "blazer_wool"),
            "knee": (f"shin_{side}", f"thigh_{side}", [f"thigh_{side}", f"shin_{side}"], 15.0, 60.0, "knees",
                     "trouser_twill")}[joint]
        limb = np.zeros(src.shape[:2], bool)
        for c in layers:
            if c in masks:
                limb |= masks[c]
        bc = sk.bone(child)
        n = np.array([-bc.u[1], bc.u[0]])
        w = 0.5 * (_extent(limb, bc.head, n) + _extent(limb, bc.head, -n))
        if w < 4:
            return None
        outline, width = _outline_style(src, limb)
        local = limb & (np.hypot(*(np.mgrid[:limb.shape[0], :limb.shape[1]][::-1] - np.asarray(bc.head)[:, None, None]))
                        < 2.5 * w)
        fill = _fabric_colour(src, local)
        return CorrectiveAsset(
            asset_id=f"{joint}_fold_{side}", category=cat, body_part=f"{joint}_{side}", side=side,
            method="ArtDirectedJointCorrection", provenance="GEOMETRIC_CORRECTION", attach_bone=parent,
            placement={"mode": "joint_fold", "group": f"{'arm' if joint == 'elbow' else 'leg'}_{side}"},
            drivers=[f"{joint}_{side}"], activation={"kind": "ramp", "driver": f"{joint}_{side}", "lo": lo, "hi": hi},
            target_pose={f"{joint}_{side}": f"eased {lo}-{hi} deg; shape and folds follow the bend"},
            compatible_layers=layers, priority=1, exclusive_group=f"{joint}_{side}",
            params={"procedural": True, "half_width_px": round(float(w), 2), "material": mat,
                    "fill_rgb": [round(float(v)) for v in fill], "outline_rgb": [round(float(v)) for v in outline],
                    "outline_width_px": width, "parent": parent, "child": child, "fabric_layers": layers,
                    "supersedes": [f"{joint}_cap_{side}"], "folds": MATERIALS[mat]},
            source_refs=[f"layer:{c} (fabric colour, outline, width at the joint)" for c in layers],
            moho={"class": "baked", "mapping": "bake key drawings of the fold correction (switch layer on the parent bone, "
                                               "Smart Bone action on the joint angle)", "status": "proposed_unverified"},
            requirement_id=f"REQ-{joint}_cap_{side}", character=eng.profile.view), {}


__all__ = ["joint_fold_fill", "ArtDirectedJointCorrection", "MATERIALS"]
