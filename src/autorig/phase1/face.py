"""FacialFeatureAnalyzer: facial feature masks, landmarks and quality gating.

Segmentation of small features runs on an upscaled crop of the head (see
pipeline.segment_region); results come back in ORIGINAL image coordinates.
Every candidate mask passes quality gates (score, inside the head, plausible
size relative to the head); failures are rejected WITH reasons, never used
silently. Pupils/irises missing from segmentation are recovered by an
intensity method inside the accepted eye mask (recorded as such).

Nothing hidden is invented: eyelids are only inferred as outline polylines of
the visible eye, a closed line-drawn mouth has no separable lips/interior, and
anything needed for animation but absent is listed as missing artwork.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import ndimage

from . import masks as M

# plausible area as a fraction of the head (wide on purpose: cartoons vary)
AREA_BOUNDS = {
    "eye": (0.002, 0.18), "eyebrow": (0.0003, 0.08), "pupil": (0.00003, 0.04), "iris": (0.0001, 0.08),
    "nose": (0.0001, 0.08), "mouth": (0.0001, 0.15), "ear": (0.002, 0.25),
}
PAIRED = {"eye": "eye", "eyebrow": "brow", "pupil": "pupil", "iris": "iris", "ear": "ear"}
SINGLE = ("nose", "mouth")


@dataclass
class Feature:
    id: str
    label: str
    mask: np.ndarray
    score: float
    method: str
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class FaceAnalysis:
    head_bbox: list[int] | None
    midline_x: float | None
    features: dict[str, Feature]
    rejected: list[dict[str, Any]]
    landmarks: dict[str, Any]
    visibility: dict[str, dict[str, Any]]
    missing_artwork: list[dict[str, Any]]
    notes: list[str]


def _gate(label: str, mask: np.ndarray, score: float, head_area: int, head_box, min_score: float) -> str | None:
    if score < min_score:
        return f"score {score:.3f} < {min_score}"
    if head_box is not None and M.fraction_inside(mask, head_box) < 0.7:
        return "less than 70% of the mask lies inside the head"
    lo, hi = AREA_BOUNDS.get(label, (0.0, 1.0))
    frac = M.area(mask) / max(head_area, 1)
    if not (lo <= frac <= hi):
        return f"area {frac:.4f} of head outside plausible range [{lo}, {hi}]"
    return None


def _luma(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def pupil_from_intensity(rgb: np.ndarray, eye: np.ndarray) -> np.ndarray:
    """Darkest compact blob inside the eye (pupil candidate)."""
    inner = M.erode(eye, 3)  # skip the eye outline linework
    if M.area(inner) < 10:
        return np.zeros_like(eye)
    lum = _luma(rgb.astype(np.float32))
    vals = lum[inner]
    thr = min(70.0, float(np.percentile(vals, 8)) + 10.0)
    dark = inner & (lum <= thr)
    rim = inner & ~M.erode(inner, 2)  # remnants of the eye's outline ring touch this rim
    comps = [c for c in M.components(dark) if M.area(c) >= 6 and not (c & rim).any()]
    if not comps:
        return np.zeros_like(eye)
    best = max(comps, key=M.area)
    return ndimage.binary_fill_holes(best)


def _eyelid_lines(eye: np.ndarray, n: int = 7) -> dict[str, list[list[float]]]:
    bb = M.bbox_xyxy(eye)
    xs = np.linspace(bb[0] + 0.5, bb[2] - 1.5, n)
    up, lo = [], []
    for x in xs:
        col = np.nonzero(eye[:, int(round(x))])[0]
        if len(col):
            up.append([round(float(x), 1), float(col.min())])
            lo.append([round(float(x), 1), float(col.max())])
    return {"upper": up, "lower": lo}


def analyze_face(source_rgba: np.ndarray, head_region: np.ndarray, hair: np.ndarray, labeled: dict[str, list],
                 *, min_score: float = 0.5) -> FaceAnalysis:
    rgb = source_rgba[..., :3]
    head_full = head_region | hair
    head_box = M.bbox_xyxy(head_full)
    head_area = M.area(head_region) or 1
    hc = M.centroid(head_region)
    midline = hc[0] if hc else None
    notes: list[str] = []
    feats: dict[str, Feature] = {}
    rejected: list[dict[str, Any]] = []
    box = None
    if head_box:
        pad_x, pad_y = 0.08 * (head_box[2] - head_box[0]), 0.08 * (head_box[3] - head_box[1])
        box = (head_box[0] - pad_x, head_box[1] - pad_y, head_box[2] + pad_x, head_box[3] + pad_y)

    for label in list(PAIRED) + list(SINGLE):
        good = []
        for lm in labeled.get(label, []):
            reason = _gate(label, lm.mask, lm.score, head_area, box, min_score)
            if reason:
                rejected.append({"label": label, "reason": reason, **lm.provenance})
            else:
                good.append(lm)
        if label in PAIRED and midline is not None:
            by_side: dict[str, Any] = {}
            for lm in sorted(good, key=lambda g: g.score, reverse=True):
                side = "r" if M.centroid(lm.mask)[0] < midline else "l"
                if side in by_side:
                    rejected.append({"label": label, "reason": f"second candidate on side '{side}'", **lm.provenance})
                    continue
                by_side[side] = lm
            for side, lm in by_side.items():
                fid = f"{PAIRED[label]}_{side}"
                feats[fid] = Feature(fid, label, lm.mask, lm.score, "segmentation", lm.provenance)
        elif good:
            lm = max(good, key=lambda g: g.score)
            for other in good:
                if other is not lm:
                    rejected.append({"label": label, "reason": "lower-scoring duplicate", **other.provenance})
            feats[label] = Feature(label, label, lm.mask, lm.score, "segmentation", lm.provenance)

    # pupils/irises must lie inside their eye; recover missing pupils by intensity
    for side in ("r", "l"):
        eye = feats.get(f"eye_{side}")
        for sub in ("pupil", "iris"):
            f = feats.get(f"{sub}_{side}")
            if f is not None and eye is not None and M.area(f.mask & ~M.dilate(eye.mask, 2)) > 0.2 * M.area(f.mask):
                rejected.append({"label": sub, "reason": "not inside the accepted eye", **f.provenance})
                del feats[f"{sub}_{side}"]
        if eye is not None and f"pupil_{side}" not in feats:
            pm = pupil_from_intensity(rgb, eye.mask)
            if M.area(pm):
                feats[f"pupil_{side}"] = Feature(f"pupil_{side}", "pupil", pm, 0.6, "intensity_within_eye",
                                                 {"derived_from": f"eye_{side}"})
                notes.append(f"pupil_{side} recovered from pixel intensity inside eye_{side}")

    # landmarks (original image pixels)
    L: dict[str, Any] = {}
    c = lambda fid: M.centroid(feats[fid].mask) if fid in feats else None
    for side in ("r", "l"):
        if f"eye_{side}" in feats:
            L[f"eye_center_{side}"] = c(f"eye_{side}")
            L[f"eyelid_lines_{side}"] = _eyelid_lines(feats[f"eye_{side}"].mask) | {
                "status": "inferred_from_visible_eye_outline (no separate eyelid artwork)"}
        if f"pupil_{side}" in feats:
            L[f"pupil_center_{side}"] = c(f"pupil_{side}")
        if f"brow_{side}" in feats:
            bm = feats[f"brow_{side}"].mask
            ys, xs = np.nonzero(bm)
            inner_x = xs.max() if side == "r" else xs.min()
            outer_x = xs.min() if side == "r" else xs.max()
            L[f"brow_center_{side}"] = c(f"brow_{side}")
            L[f"brow_inner_{side}"] = [float(inner_x), float(ys[xs == inner_x].mean())]
            L[f"brow_outer_{side}"] = [float(outer_x), float(ys[xs == outer_x].mean())]
        if f"ear_{side}" in feats:
            L[f"ear_center_{side}"] = c(f"ear_{side}")
    mouth_closed = None
    if "mouth" in feats:
        mm = feats["mouth"].mask
        ys, xs = np.nonzero(mm)
        L["mouth_center"] = c("mouth")
        L["mouth_corner_r"] = [float(xs.min()), float(ys[xs == xs.min()].mean())]
        L["mouth_corner_l"] = [float(xs.max()), float(ys[xs == xs.max()].mean())]
        thickness = float(np.median(ndimage.distance_transform_edt(mm)[mm])) * 2
        width = float(xs.max() - xs.min() + 1)
        interior = M.area(ndimage.binary_fill_holes(mm) & ~mm)
        mouth_closed = interior < 0.02 * M.area(mm) and thickness < 0.15 * width
        L["mouth_state"] = "closed_line" if mouth_closed else "open_or_filled_shape"
    if "nose" in feats:
        L["nose_tip"] = c("nose")
    if head_region.any():
        ys, xs = np.nonzero(head_region & ~hair)
        band = np.abs(xs - (midline or xs.mean())) < 0.08 * (head_box[2] - head_box[0])
        if band.any():
            L["chin"] = [float(xs[band][np.argmax(ys[band])]), float(ys[band].max())]
    eye_y = np.mean([L[k][1] for k in ("eye_center_r", "eye_center_l") if k in L]) if any(
        k in L for k in ("eye_center_r", "eye_center_l")) else None
    if "ear_center_r" in L and "ear_center_l" in L:
        L["jaw_pivot"] = {"position": [float(midline), float((L["ear_center_r"][1] + L["ear_center_l"][1]) / 2)],
                          "method": "ear_line_at_midline", "status": "approximate"}
    elif eye_y is not None and "mouth_center" in L:
        L["jaw_pivot"] = {"position": [float(midline), float((eye_y + L["mouth_center"][1]) / 2)],
                          "method": "between_eye_line_and_mouth", "status": "approximate"}
    L = {k: ([round(v[0], 2), round(v[1], 2)] if isinstance(v, (tuple, list)) and len(v) == 2
              and all(isinstance(t, float) for t in v) else v) for k, v in L.items()}

    # visibility of every requested component
    V: dict[str, dict[str, Any]] = {}

    def vis(name, status, **kw):
        V[name] = {"status": status, **kw}

    vis("head_silhouette", "visible" if head_region.any() else "missing")
    vis("hair", "visible" if hair.any() else "not_detected")
    for side in ("r", "l"):
        for fid in (f"brow_{side}", f"eye_{side}", f"ear_{side}"):
            vis(fid, "visible" if fid in feats else "not_detected",
                confidence=round(feats[fid].score, 3) if fid in feats else 0.0)
        for lid in (f"upper_eyelid_{side}", f"lower_eyelid_{side}"):
            vis(lid, "outline_only_not_separable" if f"eye_{side}" in feats else "not_detected")
        for fid in (f"iris_{side}", f"pupil_{side}"):
            vis(fid, ("visible" if feats[fid].method == "segmentation" else "derived_by_intensity")
                if fid in feats else "not_detected")
    vis("nose", "visible" if "nose" in feats else "not_detected")
    if "mouth" in feats:
        vis("upper_lip", "not_separable" if mouth_closed else "needs_review")
        vis("lower_lip", "not_separable" if mouth_closed else "needs_review")
        vis("mouth_interior", "not_visible_mouth_closed" if mouth_closed else "needs_review")
    else:
        for n in ("upper_lip", "lower_lip", "mouth_interior"):
            vis(n, "not_detected")
    vis("chin", "estimated_from_head_outline" if "chin" in L else "not_detected")
    vis("jaw_region", "estimated_pivot_only" if "jaw_pivot" in L else "not_detected")

    # missing artwork for future animation (never invented here)
    miss: list[dict[str, Any]] = []

    def need(item, needed_for, reason):
        miss.append({"artwork": item, "needed_for": needed_for, "reason": reason})

    for side in ("r", "l"):
        if f"eye_{side}" in feats:
            need(f"closed_eye_{side}", ["blinking"], "reference shows the eye open only")
            if f"pupil_{side}" in feats:
                need(f"sclera_fill_{side}", ["eye_direction"],
                     "pixels under the pupil/iris are hidden; moving the pupil reveals them")
        else:
            need(f"eye_{side}", ["blinking", "eye_direction"], "eye not detected / not separable")
        if f"brow_{side}" in feats:
            need(f"skin_under_brow_{side}", ["eyebrow_movement"], "moving the brow reveals unpainted skin")
        else:
            need(f"brow_{side}", ["eyebrow_movement"], "eyebrow not detected (may be hidden by hair)")
    if "mouth" in feats and mouth_closed:
        need("open_mouth_with_interior", ["mouth_opening", "lip_sync"], "reference mouth is a closed line")
    if "mouth" in feats:
        need("phoneme_mouth_set", ["lip_sync"], "only one (neutral) mouth shape exists")
        need("skin_under_mouth", ["mouth_opening"], "changing mouth shape reveals unpainted skin")
    else:
        need("mouth", ["mouth_opening", "lip_sync"], "mouth not detected")
    need("three_quarter_and_profile_head_views", ["head_turning"], "single front view only")
    need("back_of_hair_and_hidden_head_outline", ["head_turning", "head_tilting"], "hidden by the front view")
    need("neck_extension_under_chin", ["head_tilting"], "neck pixels under the head are hidden")
    need("expression_variants (brows, eyes, mouth)", ["facial_expressions"], "only the neutral expression exists")
    return FaceAnalysis(head_bbox=head_box, midline_x=midline, features=feats, rejected=rejected, landmarks=L,
                        visibility=V, missing_artwork=miss, notes=notes)
