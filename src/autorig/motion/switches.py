"""Artwork switch groups, the hand pose library and pose-aware correction switches (Phase 3.2).

A *switch group* is a set of alternative drawings for one body part. Exactly one variant is active at a time. All
variants of a group hang from the same bone and share its pivot (for hands: the wrist joint), so switching never
moves the parent limb. Groups are stored independently of Moho; a later phase can translate them to Moho Switch
Layers (the mapping is ``proposed_unverified``).

Resolution order for the active variant: explicit user choice > switch keyframe > automatic choice (only while
the group is in auto mode) > the group default (the original drawing).

Artwork rules carried over from earlier phases:
* the reference hand is the only original hand drawing (pose ``open_palm``);
* every other hand drawing is reference-derived or constructed and stays ``pending_review`` until a person
  approves it; pending art is only *previewed* (``allow_pending``), never treated as approved;
* a pose the package has no drawing for is NEEDS_ARTWORK with a named fallback; nothing is distorted to fake it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image

from ..phase2.skeleton import SIDE_SIGN, SIDES, Skeleton, ang

# semantic hand pose -> drawing name in the package (layer hand_<side>__<drawing>; "open" is the reference hand)
HAND_POSES: dict[str, str] = {
    "relaxed": "relaxed", "open_palm": "open", "fist": "fist", "pointing": "point", "thumbs_up": "thumbs_up",
    "gripping": "grip", "palm_down": "palm_down", "partially_closed": "partial",
    "palm_up": "palm_up",  # extra: the Phase 2 palm-up approximation (foreshortened open hand)
    "cupped": "cupped",  # extra: cupped hand (imported concept packs); not the same as partially closed
}
REQUIRED_HAND_POSES = ("relaxed", "open_palm", "fist", "pointing", "thumbs_up", "gripping", "palm_down",
                       "partially_closed")
DRAWING_TO_POSE = {d: p for p, d in HAND_POSES.items()}
FALLBACK_POSE = "open_palm"

# face (optional channel): only the reference mouth exists; the others are listed so they are not forgotten
MOUTH_SHAPES = ("neutral", "open", "smile", "o", "closed_mbp")

# alignment tolerances (Step 8). Attachment error and wrist-width ratio are measured from pixels at rest; the
# posed checks render the hand with the forearm and measure the gap in the composited result.
ALIGN = {
    "attachment_error_px": 6.0,  # proximal-band centroid of the variant vs the reference hand
    "width_ratio": (0.75, 1.35),  # wrist width of the variant / the reference hand
    "orientation_delta_deg": 35.0,  # hand body direction relative to the hand bone, variant vs reference
    "sleeve_gap_px": 2.0,  # posed: the hand must touch (or tuck under) the sleeve cuff
    "posed_attachment_drift_px": 4.0,  # posed: visible attachment vs the bone-carried rest one (the cuff drawn
    # over the wrist deforms with the elbow, so even the reference hand moves ~3 px in relaxed standing)
}


def _status(approvals: dict[str, Any], key: str) -> str:
    s = (approvals.get(key) or {}).get("status", "pending_review")
    return {"approved": "approved", "rejected": "rejected"}.get(s, "pending_review")


def offset_layer(rgba: np.ndarray, pivot, dx: float = 0.0, dy: float = 0.0, rot: float = 0.0) -> np.ndarray:
    """Rigid local transform of a full-canvas variant about the shared pivot: rotate by ``rot`` (deg, same sense
    as ``ang``: clockwise on screen) then translate. Never scales."""
    if abs(dx) < 1e-9 and abs(dy) < 1e-9 and abs(rot) < 1e-9:
        return rgba
    t = math.radians(rot)
    c, s = math.cos(t), math.sin(t)  # forward p' = pivot + R (p - pivot) + d with R = [[c, -s], [s, c]]
    px, py = map(float, pivot)
    # PIL maps output to input: p = pivot + R^T (p' - pivot - d)
    a, b = c, s
    d_, e = -s, c
    ox = px - (a * (px + dx) + b * (py + dy))
    oy = py - (d_ * (px + dx) + e * (py + dy))
    im = Image.fromarray(rgba, "RGBA").convert("RGBa")
    out = im.transform((rgba.shape[1], rgba.shape[0]), Image.Transform.AFFINE, (a, b, ox, d_, e, oy),
                       resample=Image.Resampling.BICUBIC)
    return np.asarray(out.convert("RGBA"))


# -- hand library --------------------------------------------------------------------------------------
@dataclass
class HandVariant:
    side: str
    pose: str
    drawing: str  # canonical drawing key of the pose (HAND_POSES); the layer says which drawing it actually is
    layer: str | None  # deformer layer id (hand_r, hand_r__fist, hand_r__hs.<lib>.<asset>.v1 ...); None when missing
    status: str  # original | approved | pending_review | rejected | missing
    category: str
    provenance: dict[str, Any]
    wrist_pivot: tuple[float, float]
    confidence: float
    rotation_offset_deg: float = 0.0
    offset_px: tuple[float, float] = (0.0, 0.0)
    bbox: list[int] | None = None
    orientation_deg: float | None = None  # hand body direction relative to the hand bone (deg)
    measure: dict[str, Any] = field(default_factory=dict)
    cid: str = ""  # candidate id: original | phase2:<drawing> | store:<library_id>/<asset_id>
    label: str = ""
    approval_item: str | None = None
    placement: dict[str, Any] | None = None  # stored drawings: automatic and corrected wrist placement
    rank: int = 3  # default-choice rank after approved (0) and original (1): sleeve-free store 2, others 3
    meta: dict[str, Any] = field(default_factory=dict)  # store drawings: library, sleeve, features, view, handedness

    @property
    def available(self) -> bool:
        return self.layer is not None

    def to_json(self) -> dict[str, Any]:
        return {"side": self.side, "pose": self.pose, "drawing": self.drawing, "layer": self.layer,
                "status": self.status, "category": self.category, "provenance": self.provenance,
                "wrist_pivot": [round(v, 2) for v in self.wrist_pivot], "confidence": self.confidence,
                "rotation_offset_deg": self.rotation_offset_deg, "offset_px": list(self.offset_px),
                "bbox": self.bbox, "orientation_deg": None if self.orientation_deg is None else round(self.orientation_deg, 2),
                "measure": self.measure, "view": "front",  # supported viewing orientation (the reference is front)
                "needs_artwork": self.layer is None, "cid": self.cid, "label": self.label or self.pose,
                "approval_item": self.approval_item, "placement": self.placement, "meta": self.meta}


EXTRA_HAND_POSES = ("palm_up", "cupped")
# default choice per side/pose: approved, then the original hand, then sleeve-free store drawings (newest active
# library first), then the remaining candidates (Phase 2 sheet hands and sleeve-attached store drawings)
CANDIDATE_RANK = {"approved": 0, "original": 1}


def _attachment(sk: Skeleton, mask: np.ndarray, origin, side: str) -> np.ndarray | None:
    """Visible wrist attachment of a hand mask: centroid of the proximal band along the hand bone axis."""
    hb = sk.bone(f"hand_{side}")
    Wp = np.asarray(hb.head, float)
    u = (np.asarray(hb.tail, float) - Wp) / max(hb.length, 1e-6)
    n = np.array([-u[1], u[0]])
    ys, xs = np.nonzero(mask)
    if len(xs) < 10:
        return None
    P = np.stack([xs + origin[0], ys + origin[1]], 1).astype(float)
    t, q = (P - Wp) @ u, (P - Wp) @ n
    t0 = float(np.percentile(t, 0.5))
    sel = t <= t0 + max(4.0, 0.15 * hb.length)
    return Wp + u * float(t[sel].mean()) + n * float(q[sel].mean())


def best_translation(sk: Skeleton, side: str, rgba: np.ndarray, cover: np.ndarray, target, radius: int = 12
                     ) -> tuple[float, float, float] | None:
    """Integer translation (|dx|, |dy| <= radius) that brings the drawing's *visible* wrist attachment closest to
    ``target``. Searched on the measure itself: the sleeve cuff hides a different part of the drawing at every
    offset, so a plain difference vector is wrong. Returns (dx, dy, error)."""
    a = rgba[..., 3] > 127
    ys, xs = np.nonzero(a)
    if not len(xs):
        return None
    m = radius + 4
    H, W = a.shape
    x0, y0 = max(0, xs.min() - m), max(0, ys.min() - m)
    x1, y1 = min(W, xs.max() + m + 1), min(H, ys.max() + m + 1)
    A, Cv = a[y0:y1, x0:x1], cover[y0:y1, x0:x1]
    target = np.asarray(target, float)
    best = None
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            S = np.zeros_like(A)
            S[max(0, dy):A.shape[0] + min(0, dy), max(0, dx):A.shape[1] + min(0, dx)] = \
                A[max(0, -dy):A.shape[0] - max(0, dy), max(0, -dx):A.shape[1] - max(0, dx)]
            att = _attachment(sk, S & ~Cv, (x0, y0), side)
            if att is None:
                continue
            err = float(np.hypot(*(att - target)))
            if best is None or err < best[2] - 1e-9 or (abs(err - best[2]) < 1e-9 and abs(dx) + abs(dy) < abs(best[0]) + abs(best[1])):
                best = (float(dx), float(dy), err)
    return best


class HandPoseLibrary:
    """Hand drawings per side and semantic pose, with wrist attachment measured from the pixels.

    Each side/pose can hold several *candidates*: the reference hand (open palm only), the Phase 2 drawing and
    drawings from the Hand Pose Store. One candidate is *active* per side/pose: the user's choice
    (``use["<side>/<pose>"] = cid``), else an approved one, else the original, else the first in that order. Being
    active does not approve anything: a pending active drawing is still only previewed."""

    def __init__(self, sk: Skeleton, layers: dict[str, np.ndarray], variants: dict[str, np.ndarray],
                 approvals: dict[str, Any] | None = None, manifest: dict[str, Any] | None = None,
                 candidates: list[dict[str, Any]] | None = None, use: dict[str, str] | None = None,
                 phase2_active: bool = True):
        self.sk, self.layers, self.variants = sk, layers, variants
        approvals = approvals or {}
        use = use or {}
        recs = {r["id"]: r for r in (manifest or {}).get("assets", [])}
        self._auto: dict[tuple, Any] = {}
        self.hands: dict[str, dict[str, HandVariant]] = {s: {} for s in SIDES}
        self.candidates: dict[str, dict[str, list[HandVariant]]] = {s: {} for s in SIDES}
        self.ref_measure: dict[str, dict[str, Any]] = {}
        for s in SIDES:
            hb = sk.bone(f"hand_{s}")
            pivot = (float(hb.head[0]), float(hb.head[1]))
            for pose, drawing in HAND_POSES.items():
                cands: list[HandVariant] = []
                if drawing == "open" and f"hand_{s}" in layers:
                    cands.append(HandVariant(s, pose, drawing, f"hand_{s}", "original", "O_original",
                                             {"from": "reference image (original pixels)"}, pivot, 1.0,
                                             cid="original", label="original"))
                if drawing != "open" and f"hand_{s}__{drawing}" in variants and phase2_active:
                    r = recs.get(f"hand__{drawing}", {})
                    cat = r.get("category", "unknown")
                    conf = {"R_reference_derived": 0.8, "R_composed_from_reference": 0.6,
                            "C_approximation": 0.35}.get(cat, 0.3)
                    prov = {k: r[k] for k in ("source", "scale_to_reference", "mirrored_for") if k in r}
                    cands.append(HandVariant(s, pose, drawing, f"hand_{s}__{drawing}",
                                             _status(approvals, f"corrective:hand__{drawing}"), cat, prov, pivot, conf,
                                             cid=f"phase2:{drawing}", label=f"{pose} (sheet)",
                                             approval_item=f"corrective:hand__{drawing}"))
                for c in candidates or []:
                    if c["side"] == s and c["pose"] == pose and c["layer"] in variants:
                        free = c.get("sleeve") == "none"
                        cands.append(HandVariant(s, pose, drawing, c["layer"], c["status"], c["category"],
                                                 c["provenance"], pivot, 0.4, cid=c["cid"], label=c.get("label", ""),
                                                 approval_item=c.get("approval_item"), placement=c.get("placement"),
                                                 rank=2 if free else 3,
                                                 meta={k: c.get(k) for k in ("library_id", "sleeve", "features", "view",
                                                                             "handedness", "derived", "flags")}))
                for v in cands:
                    rgba, cover = self._rgba_cover_v(v)
                    v.measure = self.measure(rgba, s, cover)
                    v.bbox = v.measure.get("bbox")
                    v.orientation_deg = v.measure.get("orientation_deg")
                self.candidates[s][pose] = cands
                self.hands[s][pose] = self._choose(cands, use.get(f"{s}/{pose}")) or \
                    HandVariant(s, pose, drawing, None, "missing", "none", {}, pivot, 0.0, cid="")
            if self.hands[s].get(FALLBACK_POSE) and self.hands[s][FALLBACK_POSE].available:
                orig = next((v for v in self.candidates[s][FALLBACK_POSE] if v.cid == "original"), None)
                self.ref_measure[s] = (orig or self.hands[s][FALLBACK_POSE]).measure

    @staticmethod
    def _choose(cands: list[HandVariant], preferred: str | None) -> HandVariant | None:
        if not cands:
            return None
        if preferred:
            for v in cands:
                if v.cid == preferred:
                    return v
        usable = [v for v in cands if v.status != "rejected"] or cands
        return sorted(usable, key=lambda v: (CANDIDATE_RANK.get(v.status, v.rank), usable.index(v)))[0]

    def candidate(self, side: str, cid: str) -> HandVariant | None:
        for vs in self.candidates[side].values():
            for v in vs:
                if v.cid == cid:
                    return v
        return None

    # -- measurement ----------------------------------------------------------------------------------
    def measure(self, rgba: np.ndarray, side: str, cover: np.ndarray | None = None) -> dict[str, Any]:
        """Wrist attachment of a hand drawing, measured on its *visible* pixels (``cover``: the sleeve/forearm
        layer composited over it): centroid of the proximal band (within 15 % of the hand length from the
        visible wrist end, along the hand bone axis), the band width, the body centroid and the body direction
        relative to the bone. ``hidden_px`` counts the drawing's pixels tucked under the sleeve."""
        hb = self.sk.bone(f"hand_{side}")
        W = np.asarray(hb.head, float)
        u = (np.asarray(hb.tail, float) - W) / max(hb.length, 1e-6)
        n = np.array([-u[1], u[0]])
        a = rgba[..., 3] > 127
        hidden = int((a & cover).sum()) if cover is not None else 0
        if cover is not None:
            a = a & ~cover
        ys, xs = np.nonzero(a)
        if len(xs) == 0:
            return {"empty": True}
        P = np.stack([xs, ys], 1).astype(float)
        t = (P - W) @ u
        q = (P - W) @ n
        t0 = float(np.percentile(t, 0.5))
        band = max(4.0, 0.15 * hb.length)
        sel = t <= t0 + band
        att = W + u * float(t[sel].mean()) + n * float(q[sel].mean())
        width = float(np.percentile(q[sel], 98) - np.percentile(q[sel], 2))
        cen = P.mean(0)
        orient = float(((ang(cen - att) - ang(u)) + 180.0) % 360.0 - 180.0)
        return {"attachment": [round(float(v), 2) for v in att], "wrist_width_px": round(width, 2),
                "centroid": [round(float(v), 2) for v in cen], "orientation_deg": round(orient, 2),
                "area_px": int(len(xs)), "hidden_px": hidden, "proximal_t_px": round(t0, 2),
                "bbox": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]}

    def _rgba_cover_v(self, v: HandVariant):
        side = v.side
        rgba = self.layers[v.layer] if v.cid == "original" else self.variants[v.layer]
        cover = None
        if v.cid.startswith("store:"):
            cover = self.variants.get(f"forearm_{side}__hscuff")
        if cover is None and v.cid != "original":
            cover = self.variants.get(f"forearm_{side}__trim")
        if cover is None:
            cover = self.layers.get(f"forearm_{side}")
        return rgba, (cover[..., 3] > 127 if cover is not None else np.zeros(rgba.shape[:2], bool))

    def _rgba_cover(self, side: str, pose: str):
        return self._rgba_cover_v(self.hands[side][pose])

    def auto_align_v(self, v: HandVariant, radius: int = 12) -> dict[str, Any] | None:
        """Alignment tool: the integer translation (|dx|, |dy| <= radius px, no rotation, no scaling) that brings
        the candidate's *visible* wrist attachment closest to the reference hand's (``best_translation``). Cached."""
        key = (v.side, v.cid, v.layer, radius)
        if key in self._auto:
            return self._auto[key]
        ref = self.ref_measure.get(v.side)
        if v is None or not v.available or ref is None or v.cid == "original":
            return None
        rgba, cover = self._rgba_cover_v(v)
        best = best_translation(self.sk, v.side, rgba, cover, ref["attachment"], radius)
        out = None if best is None else {"offset_px": [best[0], best[1]], "rot_deg": 0.0,
                                         "error_after_px": round(best[2], 2),
                                         "error_before_px": round(float(np.hypot(*(np.subtract(v.measure["attachment"], ref["attachment"])))), 2)}
        self._auto[key] = out
        return out

    def auto_align(self, side: str, pose: str, radius: int = 12) -> dict[str, Any] | None:
        v = self.hands[side].get(pose)
        if v is None or not v.available or pose == FALLBACK_POSE and v.cid == "original":
            return None
        return self.auto_align_v(v, radius)

    def _attachment(self, mask: np.ndarray, origin, side: str) -> np.ndarray | None:
        return _attachment(self.sk, mask, origin, side)

    def alignment_v(self, v: HandVariant, midline_x: float) -> dict[str, Any]:
        """Rest-frame alignment of a candidate against the reference hand (Step 8). Stored drawings are placed by
        matching their own cuff edge to the character's cuff (handstore): their check is that anchor (residual =
        the user's correction), the wrist fit in the sleeve opening and the side; the visible-band distance to the
        reference hand is reported for information only (it measures hand shape, not misplacement)."""
        if v.cid.startswith("store:") and v.placement:
            return self._store_alignment(v, midline_x)
        side, pose = v.side, v.pose
        r = self.ref_measure.get(side)
        if not v.available:
            return {"side": side, "pose": pose, "status": "NEEDS_ARTWORK", "issues": ["drawing missing"]}
        if r is None:
            return {"side": side, "pose": pose, "status": "UNCHECKED", "issues": ["no reference hand"]}
        m = v.measure
        att_err = float(np.hypot(*(np.subtract(m["attachment"], r["attachment"]))))
        ratio = m["wrist_width_px"] / max(r["wrist_width_px"], 1e-6)
        dori = abs(((m["orientation_deg"] - r["orientation_deg"]) + 180) % 360 - 180)
        side_ok = (np.sign(m["centroid"][0] - midline_x) == np.sign(r["centroid"][0] - midline_x)
                   and np.sign(m["attachment"][0] - midline_x) == np.sign(r["attachment"][0] - midline_x))
        issues = []
        if att_err > ALIGN["attachment_error_px"]:
            issues.append(f"wrist attachment {att_err:.1f} px from the reference hand's (> {ALIGN['attachment_error_px']})")
        lo, hi = ALIGN["width_ratio"]
        if not lo <= ratio <= hi:
            issues.append(f"wrist width ratio {ratio:.2f} outside [{lo}, {hi}] (scale mismatch)")
        if dori > ALIGN["orientation_delta_deg"] and pose not in ("pointing", "thumbs_up", "fist", "gripping",
                                                                   "palm_down", "cupped"):
            issues.append(f"hand body direction differs by {dori:.0f} deg")
        if not side_ok:
            issues.append("left/right swap: the drawing sits on the other side of the body")
        return {"side": side, "pose": pose, "cid": v.cid, "layer": v.layer, "status": "OK" if not issues else "MISMATCH",
                "attachment_error_px": round(att_err, 2), "width_ratio": round(ratio, 3),
                "orientation_delta_deg": round(dori, 1), "side_ok": bool(side_ok), "issues": issues,
                "auto_align": self.auto_align_v(v) if v.cid != "original" else None}

    def _store_alignment(self, v: HandVariant, midline_x: float) -> dict[str, Any]:
        pl, r = v.placement, self.ref_measure.get(v.side)
        corr = pl.get("correction") or {}
        resid = float(math.hypot(float(corr.get("dx", 0.0)), float(corr.get("dy", 0.0))))
        mi, ref = pl.get("mask") or {}, pl.get("reference_wrist") or {}
        fit = (mi.get("wrist_width_px", 0.0) * pl["final"]["scale"] / ref["cuff_width_px"]) if ref.get("cuff_width_px") else None
        m = v.measure
        side_ok = bool(r is None or np.sign(m["centroid"][0] - midline_x) == np.sign(r["centroid"][0] - midline_x))
        issues = []
        if resid > ALIGN["attachment_error_px"]:
            issues.append(f"wrist anchor moved {resid:.1f} px off the cuff edge by the correction (> {ALIGN['attachment_error_px']})")
        lo, hi = ALIGN["width_ratio"]
        if fit is not None and not lo <= fit <= hi:
            issues.append(f"wrist width {fit:.2f} x the sleeve opening (outside [{lo}, {hi}])")
        if not side_ok:
            issues.append("left/right swap: the drawing sits on the other side of the body")
        if not mi.get("found", True):
            issues.append("no sleeve/cuff found in the drawing: anchor from the manifest estimate")
        band = float(np.hypot(*(np.subtract(m["attachment"], r["attachment"])))) if r else None
        return {"side": v.side, "pose": v.pose, "cid": v.cid, "layer": v.layer, "method": "cuff_edge_match",
                "status": "OK" if not issues else "MISMATCH", "attachment_error_px": round(resid, 2),
                "wrist_fit": None if fit is None else round(fit, 3), "width_ratio": None if fit is None else round(fit, 3),
                "visible_band_vs_reference_px": None if band is None else round(band, 2),
                "side_ok": side_ok, "issues": issues, "auto_align": self.auto_align_v(v)}

    def alignment(self, side: str, pose: str, midline_x: float) -> dict[str, Any]:
        v = self.hands[side].get(pose)
        if v is None:
            return {"side": side, "pose": pose, "status": "NEEDS_ARTWORK", "issues": ["drawing missing"]}
        return self.alignment_v(v, midline_x)

    def missing(self) -> list[dict[str, Any]]:
        return [{"side": s, "pose": p, "drawing": HAND_POSES[p], "status": "NEEDS_ARTWORK",
                 "fallback": FALLBACK_POSE}
                for s in SIDES for p in REQUIRED_HAND_POSES if not self.hands[s].get(p) or
                not self.hands[s][p].available]

    def to_json(self, midline_x: float) -> dict[str, Any]:
        return {"poses": list(REQUIRED_HAND_POSES), "extra": list(EXTRA_HAND_POSES),
                "variants": {s: {p: v.to_json() for p, v in self.hands[s].items()} for s in SIDES},
                "alignment": {s: {p: self.alignment(s, p, midline_x) for p in self.hands[s]} for s in SIDES},
                "candidates": {s: {p: [v.to_json() | {"active": v is self.hands[s][p]} for v in vs]
                                   for p, vs in self.candidates[s].items() if vs} for s in SIDES},
                "missing": self.missing(), "tolerances": {k: list(v) if isinstance(v, tuple) else v
                                                          for k, v in ALIGN.items()}}


def posed_hand_alignment(eng, res: dict[str, Any], side: str, layer: str, rest_attachment) -> dict[str, Any]:
    """Posed check on a rendered result: the hand layer must touch the forearm (sleeve cuff) and its attachment
    must sit where the hand bone carries the rest attachment (it follows the solved/IK limb)."""
    r = res["render"]
    pad = r["pad"]
    alphas = r["alphas"]
    fa = next((alphas[c] for c in (f"forearm_{side}__hscuff", f"forearm_{side}__trim", f"forearm_{side}") if c in alphas), None)
    ha = alphas.get(layer)
    if fa is None or ha is None or not ha.any():
        return {"side": side, "layer": layer, "status": "UNCHECKED", "issues": ["layer not rendered"]}
    from scipy import ndimage

    dist = ndimage.distance_transform_edt(~fa)
    gap = float(dist[ha].min())
    pose = res["pose"]
    hi = eng.sk.idx[f"hand_{side}"]
    Wm = eng.solver.K.world(hi, pose)
    carried = Wm[:2, :2] @ np.asarray(rest_attachment, float) + Wm[:2, 2] + pad
    # posed attachment measured from the rendered alpha along the posed hand bone
    hb = eng.sk.bone(f"hand_{side}")
    Wp = Wm[:2, :2] @ np.asarray(hb.head, float) + Wm[:2, 2] + pad
    u = Wm[:2, :2] @ ((np.asarray(hb.tail, float) - hb.head) / max(hb.length, 1e-6))
    n = np.array([-u[1], u[0]])
    vis = ha & ~fa  # visible hand pixels, as measured at rest (the sleeve cuff is composited over the wrist)
    if not vis.any():
        return {"side": side, "layer": layer, "status": "MISMATCH", "issues": ["hand fully covered"]}
    ys, xs = np.nonzero(vis)
    P = np.stack([xs, ys], 1).astype(float)
    t = (P - Wp) @ u
    q = (P - Wp) @ n
    t0 = float(np.percentile(t, 0.5))
    sel = t <= t0 + max(4.0, 0.15 * hb.length)
    att = Wp + u * float(t[sel].mean()) + n * float(q[sel].mean())
    drift = float(np.hypot(*(att - carried)))
    issues = []
    if gap > ALIGN["sleeve_gap_px"]:
        issues.append(f"hand detached from the sleeve by {gap:.1f} px")
    if drift > ALIGN["posed_attachment_drift_px"]:
        issues.append(f"posed attachment drifts {drift:.1f} px from the bone-carried wrist")
    return {"side": side, "layer": layer, "status": "OK" if not issues else "MISMATCH",
            "sleeve_gap_px": round(gap, 2), "posed_attachment_drift_px": round(drift, 2), "issues": issues}


# -- switch groups ---------------------------------------------------------------------------------------
@dataclass
class SwitchVariant:
    id: str
    layer: str | None
    status: str  # original | approved | pending_review | rejected | missing
    category: str = "O_original"
    provenance: dict[str, Any] = field(default_factory=dict)
    label: str = ""

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "layer": self.layer, "status": self.status, "category": self.category,
                "provenance": self.provenance, "label": self.label or self.id,
                "needs_artwork": self.layer is None and self.status == "missing"}


@dataclass
class SwitchGroup:
    id: str
    kind: str  # hand | correction | face
    bone: str
    pivot: tuple[float, float]
    variants: dict[str, SwitchVariant]
    default: str
    channel: str = "body"  # body | face (live puppeteering keeps them separate)
    replaces: str | None = None  # the base layer the active variant replaces in the composite
    alias: str | None = None  # readable name (hand_right / hand_left) used in switch manifests
    moho: dict[str, Any] = field(default_factory=lambda: {"target": "Switch Layer", "status": "proposed_unverified"})

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "alias": self.alias, "kind": self.kind, "bone": self.bone, "pivot": [round(v, 2) for v in self.pivot],
                "default": self.default, "channel": self.channel, "replaces": self.replaces,
                "variants": [v.to_json() for v in self.variants.values()], "moho": self.moho}


def default_group_state() -> dict[str, Any]:
    return {"user": None, "auto": True, "keys": [], "offsets": {}}


class ArtworkSwitchController:
    """Switch groups plus their state (user choice, auto flag, keyframes, per-variant offsets)."""

    def __init__(self, groups: dict[str, SwitchGroup], state: dict[str, Any] | None = None):
        self.groups = groups
        self.state: dict[str, dict[str, Any]] = {g: default_group_state() for g in groups}
        for g, st in (state or {}).items():
            if g in self.state:
                self.state[g].update({k: v for k, v in st.items() if k in ("user", "auto", "keys", "offsets")})

    @classmethod
    def build(cls, lib: HandPoseLibrary, sk: Skeleton, layers: dict[str, np.ndarray],
              corrections: "CorrectionSwitchController | None" = None,
              state: dict[str, Any] | None = None) -> "ArtworkSwitchController":
        groups: dict[str, SwitchGroup] = {}
        for s in SIDES:
            hb = sk.bone(f"hand_{s}")
            vs = {}
            for pose in list(REQUIRED_HAND_POSES) + list(EXTRA_HAND_POSES):
                v = lib.hands[s].get(pose)
                if v is None:
                    continue
                vs[pose] = SwitchVariant(pose, v.layer, v.status, v.category, v.provenance, pose.replace("_", " "))
            groups[f"hand_{s}"] = SwitchGroup(f"hand_{s}", "hand", f"hand_{s}", (float(hb.head[0]), float(hb.head[1])),
                                              vs, FALLBACK_POSE, replaces=f"hand_{s}",
                                              alias="hand_right" if s == "r" else "hand_left")
        if corrections is not None:
            for c in corrections.triggers:
                st = corrections.asset_state(c)
                vs = {"off": SwitchVariant("off", None, "original", label="no correction"),
                      "on": SwitchVariant("on", c["asset"] if st != "missing" else None, st,
                                          "C_corrective_shape" if st != "missing" else "none",
                                          {"asset": c["asset"]}, c["title"])}
                groups[f"corr_{c['id']}"] = SwitchGroup(f"corr_{c['id']}", "correction", c["bone"],
                                                        tuple(c.get("pivot") or (0.0, 0.0)), vs, "off")
        if "mouth" in layers:
            mb = sk.bone("head")
            vs = {m: SwitchVariant(m, "mouth" if m == "neutral" else None,
                                   "original" if m == "neutral" else "missing", label=m) for m in MOUTH_SHAPES}
            groups["face_mouth"] = SwitchGroup("face_mouth", "face", "head", (float(mb.head[0]), float(mb.head[1])),
                                               vs, "neutral", channel="face", replaces="mouth")
        return cls(groups, state)

    # -- edits (the editor stores them as project operations; see editor/project.py) ----------------------
    def set(self, group: str, variant: str | None, auto: bool | None = None) -> None:
        g = self._g(group)
        if variant is not None and variant not in g.variants:
            raise KeyError(f"{group}: unknown variant {variant}")
        st = self.state[group]
        st["user"] = variant
        st["auto"] = (variant is None) if auto is None else bool(auto)

    def key(self, group: str, t: float, variant: str | None) -> None:
        """Add (variant) or delete (None) a switch keyframe at time ``t`` (frame or gait phase). Step-held."""
        g = self._g(group)
        if variant is not None and variant not in g.variants:
            raise KeyError(f"{group}: unknown variant {variant}")
        keys = [k for k in self.state[group]["keys"] if abs(k[0] - t) > 1e-9]
        if variant is not None:
            keys.append([float(t), variant])
        self.state[group]["keys"] = sorted(keys)

    def set_offset(self, group: str, variant: str, dx: float = 0.0, dy: float = 0.0, rot: float = 0.0) -> None:
        self._g(group)
        self.state[group]["offsets"][variant] = {"dx": float(dx), "dy": float(dy), "rot": float(rot)}

    def offset(self, group: str, variant: str) -> dict[str, float]:
        return {"dx": 0.0, "dy": 0.0, "rot": 0.0, **self.state[group]["offsets"].get(variant, {})}

    def _g(self, group: str) -> SwitchGroup:
        if group not in self.groups:
            raise KeyError(f"unknown switch group {group}")
        return self.groups[group]

    # -- resolution ---------------------------------------------------------------------------------------
    def resolve(self, group: str, t: float | None = None, auto: str | None = None,
                allow_pending: bool = False) -> dict[str, Any]:
        g = self._g(group)
        st = self.state[group]
        keyed = None
        if st["keys"] and t is not None:
            # step-held keys; a cyclic time before the first key holds the last key (loops)
            before = [k for k in st["keys"] if k[0] <= t + 1e-9]
            keyed = (before[-1] if before else st["keys"][-1])[1]
        if st["user"] is not None and not st["auto"]:
            req, src = st["user"], "user"
        elif keyed is not None:
            req, src = keyed, "keyframe"
        elif st["auto"] and auto is not None and auto in g.variants:
            req, src = auto, "auto"
        else:
            req, src = g.default, "default"
        v = g.variants[req]
        active, note, needs = req, None, None
        if v.layer is None or v.status == "missing":
            active, needs = g.default, req
            note = f"{group}: '{req}' has no drawing (NEEDS_ARTWORK); showing '{g.default}'"
        elif v.status == "rejected":
            active = g.default
            note = f"{group}: '{req}' was rejected in review; showing '{g.default}'"
        elif v.status == "pending_review" and not allow_pending:
            active = g.default
            note = f"{group}: '{req}' is pending review (preview it with allow_pending); showing '{g.default}'"
        av = g.variants[active]
        return {"group": group, "requested": req, "active": active, "source": src, "layer": av.layer,
                "status": av.status, "pending_review": av.status == "pending_review",
                "needs_artwork": needs, "note": note, "offset": self.offset(group, active)}

    def to_json(self) -> dict[str, Any]:
        return {"groups": {g: grp.to_json() for g, grp in self.groups.items()}, "state": self.state}


# -- pose-aware correction switches ----------------------------------------------------------------------
# Discrete corrections with hysteresis. ``measure`` names an anatomical reading of the solved pose
# (SkeletalPoseSolver.anatomical); a trigger switches on at ``on`` and off below ``on - hysteresis``.
# Continuous corrections stay the Phase 3.1 rotation-dependent band rules (garments.BAND_RULES) and are reported
# as parameters.
CORRECTION_TRIGGERS: list[dict[str, Any]] = [
    {"id": "shoulder_raise_{s}", "title": "shoulder raise (armpit gusset)", "bone": "upper_arm_{s}",
     "measure": "abduction_{s}", "on": 120.0, "hysteresis": 8.0, "asset": "gusset", "priority": 1,
     "compatible": ["elbow_deep_{s}", "wrist_rotation_{s}"], "binding": "clavicle_{s} rest frame"},
    {"id": "elbow_deep_{s}", "title": "deep elbow bend (inner-elbow drawing)", "bone": "forearm_{s}",
     "measure": "elbow_{s}", "on": 100.0, "hysteresis": 8.0, "asset": "elbow_deep_{s}", "priority": 2,
     "compatible": ["shoulder_raise_{s}", "sleeve_crease_{s}"], "binding": "forearm_{s}",
     # band deformation alone folds the sleeve cleanly at ~120 deg (ik_switch/closeups/05_running_fists.jpg) and
     # breaks it at 130 deg (ik_switch/closeups/08_deep_elbow_bend.jpg): above 125 deg the drawing is required
     "required_above": 125.0},
    {"id": "sleeve_crease_{s}", "title": "sleeve creases", "bone": "forearm_{s}", "measure": "elbow_{s}",
     "on": 60.0, "hysteresis": 6.0, "asset": "sleeve_crease_{s}", "priority": 3,
     "compatible": ["elbow_deep_{s}"], "binding": "forearm_{s}"},
    {"id": "wrist_rotation_{s}", "title": "wrist rotation (turned-wrist drawing)", "bone": "hand_{s}",
     "measure": "wrist_{s}", "on": 40.0, "hysteresis": 5.0, "asset": "wrist_turn_{s}", "priority": 3,
     "compatible": ["shoulder_raise_{s}"], "binding": "hand_{s}"},
    {"id": "hip_flexion_{s}", "title": "hip flexion (front-view thigh drawing)", "bone": "thigh_{s}",
     "measure": "hip_{s}", "on": 40.0, "hysteresis": 5.0, "asset": "hip_flex_{s}", "priority": 2,
     "compatible": ["knee_deep_{s}", "trouser_fold_{s}"], "binding": "thigh_{s}"},
    {"id": "knee_deep_{s}", "title": "knee flexion (bent-knee drawing)", "bone": "shin_{s}", "measure": "knee_{s}",
     "on": 45.0, "hysteresis": 5.0, "asset": "knee_deep_{s}", "priority": 2,
     "compatible": ["hip_flexion_{s}", "trouser_fold_{s}"], "binding": "shin_{s}"},
    {"id": "trouser_fold_{s}", "title": "trouser folds at the knee", "bone": "shin_{s}", "measure": "knee_{s}",
     "on": 25.0, "hysteresis": 4.0, "asset": "trouser_fold_{s}", "priority": 3,
     "compatible": ["knee_deep_{s}"], "binding": "shin_{s}"},
]


class CorrectionSwitchController:
    def __init__(self, sk: Skeleton, approvals: dict[str, Any] | None = None, corrective_ids: list[str] | None = None):
        self.sk = sk
        self.approvals = approvals or {}
        self.corrective_ids = list(corrective_ids or [])
        self.triggers: list[dict[str, Any]] = []
        for t in CORRECTION_TRIGGERS:
            for s in SIDES:
                c = {k: (v.format(s=s) if isinstance(v, str) else
                         [x.format(s=s) for x in v] if isinstance(v, list) else v) for k, v in t.items()}
                c["side"] = s
                c["pivot"] = [float(x) for x in sk.bone(c["bone"]).head]
                self.triggers.append(c)
        self.on: dict[str, bool] = {}

    def asset_state(self, c: dict[str, Any]) -> str:
        if c["asset"] == "gusset":  # procedural per-pose fills; approval is per preset and side
            ids = [i for i in self.corrective_ids if i.startswith("gusset__") and i.endswith(f"__{c['side']}")]
            if not ids:
                return "missing"
            sts = [_status(self.approvals, f"corrective:{i}") for i in ids]
            return "approved" if all(x == "approved" for x in sts) else "pending_review"
        if f"corrective:{c['asset']}" in self.approvals:
            return _status(self.approvals, f"corrective:{c['asset']}")
        return "missing"

    def evaluate(self, readings: dict[str, float], allow_pending: bool = False, stateful: bool = True,
                 overrides: dict[str, bool] | None = None) -> list[dict[str, Any]]:
        """Per trigger: on/off with hysteresis (``stateful`` keeps the previous state between calls, for drags and
        cycles), the asset state and whether the correction is applied or reported as NEEDS_ARTWORK."""
        out = []
        for c in self.triggers:
            val = readings.get(c["measure"], 0.0)
            was = self.on.get(c["id"], False) if stateful else False
            on = val >= c["on"] or (was and val >= c["on"] - c["hysteresis"])
            if overrides and c["id"] in overrides:
                on = bool(overrides[c["id"]])
            self.on[c["id"]] = on
            st = self.asset_state(c)
            applied = on and (st == "approved" or (st == "pending_review" and allow_pending))
            out.append({"id": c["id"], "title": c["title"], "side": c["side"], "trigger_joint": c["bone"],
                        "value_deg": round(val, 2), "range": [c["on"], 180.0], "hysteresis_deg": c["hysteresis"],
                        "on": on, "asset": c["asset"], "asset_state": st, "applied": applied,
                        "priority": c["priority"], "compatible": c["compatible"], "binding": c["binding"],
                        "needs_artwork": bool(on and st == "missing"),
                        "required": bool(c.get("required_above") is not None and val >= c["required_above"]),
                        "pending_review": bool(applied and st == "pending_review")})
        return out

    def reset(self) -> None:
        self.on.clear()

    def to_json(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in c.items()} | {"asset_state": self.asset_state(c)} for c in self.triggers]


def correction_readings_from_pose(solver, pose) -> dict[str, float]:
    """Anatomical readings straight from bone directions (independent of the report format)."""
    K, sk, I = solver.K, solver.sk, solver.I
    th = pose.theta
    out = {}
    for s in SIDES:
        sg = SIDE_SIGN[s]
        out[f"abduction_{s}"] = sg * float(K.rot(th, f"upper_arm_{s}") + sk.bones[I[f"upper_arm_{s}"]].anat_angle)
        a = float(((out[f"abduction_{s}"] + 180) % 360) - 180)
        out[f"abduction_{s}"] = a + 360.0 if a <= -90.0 else a  # overhead arms keep increasing (no wrap)
        fo, up, ha = sk.bones[I[f"forearm_{s}"]], sk.bones[I[f"upper_arm_{s}"]], sk.bones[I[f"hand_{s}"]]
        rest_rel = ((fo.rest_angle - up.anat_angle + 180) % 360) - 180
        out[f"elbow_{s}"] = abs(float(((th[I[f"forearm_{s}"]] + rest_rel + 180) % 360) - 180))
        hr = ((ha.rest_angle - fo.rest_angle + 180) % 360) - 180
        out[f"wrist_{s}"] = abs(float(((th[I[f"hand_{s}"]] + hr + 180) % 360) - 180))
        th_w = float(K.rot(th, f"thigh_{s}") + sk.bones[I[f"thigh_{s}"]].rest_angle)
        out[f"hip_{s}"] = abs(float(((th_w + 180) % 360) - 180))
        sh, tg = sk.bones[I[f"shin_{s}"]], sk.bones[I[f"thigh_{s}"]]
        kr = ((sh.rest_angle - tg.rest_angle + 180) % 360) - 180
        out[f"knee_{s}"] = abs(float(((th[I[f"shin_{s}"]] + kr + 180) % 360) - 180))
    return out
