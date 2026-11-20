"""OcclusionAnalyzer: front/back relations and hidden regions that animation exposes.

Geometry and image evidence first, no generative AI:
* Contact zones between part masks define candidate edges.
* Linework ownership: in cartoon art the occluding part owns the outline drawn
  along a shared boundary (the hidden part's outline stops there). Dark
  pixels in the contact band are counted per side.
* Category priors (hair over head, sleeve/arm over torso, ...) back up a
  missing or weak cue; a cue/prior conflict is flagged as ambiguous.
* Expected (complete) part shapes: limb capsules along Phase 1 bones, convex
  hulls for compact parts, an ellipse fitted to the visible skull outline for
  the head base, a capsule for the neck. These are MODELS, recorded with
  confidence, never presented as ground truth.
* Reconstruction region = expected shape of the back part that lies under the
  front part (near the joint for limb chains), excluding visible pixels.
Manual overrides can force edge directions and exclude or replace regions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from scipy import linalg, ndimage
from scipy.spatial import ConvexHull

from ..phase1 import masks as M

LIMBS = {"upper_arm": ("shoulder", "elbow"), "forearm": ("elbow", "wrist"),
         "thigh": ("hip", "knee"), "shin": ("knee", "ankle")}
HULL_KINDS = {"torso", "pelvis", "hand", "foot"}
JOINT_OF_PAIR = {("torso", "upper_arm"): "shoulder", ("upper_arm", "forearm"): "elbow", ("forearm", "hand"): "wrist",
                 ("pelvis", "thigh"): "hip", ("thigh", "shin"): "knee", ("shin", "foot"): "ankle",
                 ("neck", "head"): "neck", ("torso", "neck"): "neck"}
ARM_CHAIN = {"upper_arm", "forearm", "hand"}
PRIORS: dict[frozenset, tuple[str, float]] = {}
for a, b, front, conf in [
    ("hair", "head", "hair", 0.9), ("hair", "feature", "hair", 0.7), ("feature", "head", "feature", 0.95),
    ("head", "neck", "head", 0.9), ("torso", "neck", "torso", 0.6), ("hair", "neck", "hair", 0.6),
    ("upper_arm", "torso", "upper_arm", 0.75), ("forearm", "torso", "forearm", 0.75), ("hand", "torso", "hand", 0.75),
    ("forearm", "upper_arm", "forearm", 0.55), ("hand", "forearm", "hand", 0.6), ("torso", "pelvis", "torso", 0.55),
    ("pelvis", "thigh", "pelvis", 0.7), ("shin", "thigh", "shin", 0.5), ("foot", "shin", "foot", 0.7),
    ("eye_iris", "eye_white", "eye_iris", 0.95), ("hand", "pelvis", "hand", 0.6), ("hand", "thigh", "hand", 0.6),
    ("forearm", "pelvis", "forearm", 0.6), ("upper_arm", "pelvis", "upper_arm", 0.6),
]:
    PRIORS[frozenset((a, b))] = (front, conf)

FEATURE_CONTROLS = {"brow": ["brow_raise_left", "brow_raise_right", "brow_frown"],
                    "eye": ["eye_blink_left", "eye_blink_right"], "mouth": ["jaw_open", "mouth_width", "smile_left",
                                                                            "smile_right", "mouth_pucker"],
                    "nose": ["head_yaw"]}


def kind(cid: str) -> str:
    if cid.endswith("_white") and cid.startswith("eye"):
        return "eye_white"
    if cid.endswith("_iris") and cid.startswith("eye"):
        return "eye_iris"
    base = cid.rsplit("_", 1)[0] if cid[-2:] in ("_r", "_l") else cid
    if base in ("eye", "brow", "nose", "mouth"):
        return "feature"
    if base == "head_base":
        return "head"
    return base


def side(cid: str) -> str | None:
    for s in ("_r", "_l"):
        if cid.endswith(s) or f"{s}_" in cid:
            return s[1]
    return None


@dataclass
class Region:
    id: str
    owner: str
    occluded_by: list[str]
    mask: np.ndarray
    joint: str | None
    exposed_by: list[str]
    method: str
    confidence: float
    ambiguous: bool
    strategy: str = "pending"
    notes: list[str] = field(default_factory=list)


def _capsule(shape, a, b, r) -> np.ndarray:
    img = Image.new("L", (shape[1], shape[0]), 0)
    d = ImageDraw.Draw(img)
    d.line((a, b), fill=255, width=max(1, int(round(2 * r))))
    for c in (a, b):
        d.ellipse((c[0] - r, c[1] - r, c[0] + r, c[1] + r), fill=255)
    return np.asarray(img) > 0


def _tapered(shape, a, b, ra, rb, n=24) -> np.ndarray:
    """Union of discs whose radius goes from ra at a to rb at b (a tapered capsule)."""
    img = Image.new("L", (shape[1], shape[0]), 0)
    d = ImageDraw.Draw(img)
    for t in np.linspace(0, 1, n):
        x, y, r = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]), ra + t * (rb - ra)
        d.ellipse((x - r, y - r, x + r, y + r), fill=255)
    return np.asarray(img) > 0


def _hull(m: np.ndarray) -> np.ndarray | None:
    ys, xs = np.nonzero(m & ~M.erode(m, 1))
    if len(xs) < 3:
        return None
    pts = np.c_[xs, ys]
    try:
        hull = ConvexHull(pts)
    except Exception:
        return None
    img = Image.new("L", (m.shape[1], m.shape[0]), 0)
    ImageDraw.Draw(img).polygon([tuple(map(float, pts[i])) for i in hull.vertices], fill=255)
    return (np.asarray(img) > 0) | m


def fit_ellipse(xs: np.ndarray, ys: np.ndarray, shape) -> np.ndarray | None:
    """Direct least-squares ellipse fit (Fitzgibbon et al. 1999) -> filled mask."""
    if len(xs) < 12:
        return None
    mx, my, s = xs.mean(), ys.mean(), max(xs.std(), ys.std(), 1.0)
    x, y = (xs - mx) / s, (ys - my) / s
    D = np.vstack([x * x, x * y, y * y, x, y, np.ones_like(x)]).T
    S = D.T @ D
    C = np.zeros((6, 6))
    C[0, 2] = C[2, 0] = 2
    C[1, 1] = -1
    try:
        w, v = linalg.eig(S, C)
    except Exception:
        return None
    w = np.real(w)
    v = np.real(v)
    ok = [i for i in range(6) if np.isfinite(w[i]) and w[i] > 0 and 4 * v[0, i] * v[2, i] - v[1, i] ** 2 > 0]
    if not ok:
        return None
    a, b, c, d, e, f = v[:, ok[0]]
    H, W = shape
    gy, gx = np.mgrid[0:H, 0:W]
    X, Y = (gx - mx) / s, (gy - my) / s
    val = a * X * X + b * X * Y + c * Y * Y + d * X + e * Y + f
    cx = (b * e - 2 * c * d) / (4 * a * c - b * b)
    cy = (b * d - 2 * a * e) / (4 * a * c - b * b)
    inside_sign = np.sign(a * cx * cx + b * cx * cy + c * cy * cy + d * cx + e * cy + f)
    m = np.sign(val) == inside_sign
    if m.sum() > 0.6 * m.size:  # degenerate fit
        return None
    return m


class OcclusionAnalyzer:
    def __init__(self, source_rgba: np.ndarray, components: dict[str, np.ndarray], z_index: dict[str, int],
                 joints: dict[str, dict[str, Any]], face_masks: dict[str, np.ndarray], overrides: dict | None = None):
        self.rgba = source_rgba
        self.comp = components
        self.z = z_index
        self.joints = joints
        self.face_masks = face_masks
        self.overrides = overrides or {}
        self.shape = source_rgba.shape[:2]
        self.tol = max(3, round(0.004 * max(self.shape)))
        lum = source_rgba[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
        fg = np.zeros(self.shape, bool)
        for m in components.values():
            fg |= m
        dark = lum < min(100.0, 0.5 * float(np.median(lum[fg]))) if fg.any() else lum < 60
        # linework = dark AND thin; thick dark areas (navy shorts, black shoes, dark hair) are fills
        thick_core = ndimage.distance_transform_edt(dark) > 4
        thick = M.dilate(thick_core, 5) & dark
        self.dark_all = dark
        self.dark = dark & ~thick
        self.flags: list[str] = []

    # -- expected shapes ----------------------------------------------------------------------
    def joint_xy(self, name):
        j = self.joints.get(name)
        return None if not j or j.get("position") is None else tuple(j["position"])

    def radius(self, cid: str, a, b) -> float:
        m = self.comp[cid]
        dt = ndimage.distance_transform_edt(m)
        samples = []
        for t in np.linspace(0, 1, 25):
            x, y = a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])
            xi, yi = int(round(x)), int(round(y))
            if 0 <= yi < m.shape[0] and 0 <= xi < m.shape[1] and m[yi, xi]:
                samples.append(dt[yi, xi])
        return float(np.percentile(samples, 75)) if len(samples) >= 3 else 0.8 * float(dt.max())

    def end_radius(self, cid: str, p) -> float | None:
        """Half-width of the part near one of its joints (median of the distance
        transform over the part's pixels within ~40 px of the joint)."""
        m = self.comp[cid]
        dt = ndimage.distance_transform_edt(m)
        yy, xx = np.ogrid[:m.shape[0], :m.shape[1]]
        near = m & ((xx - p[0]) ** 2 + (yy - p[1]) ** 2 <= 40 ** 2)
        if near.sum() < 20:
            return None
        return float(np.percentile(dt[near], 90))

    def expected_shape(self, cid: str) -> tuple[np.ndarray | None, str, float]:
        k, s = kind(cid), side(cid)
        m = self.comp[cid]
        if k in LIMBS and s:
            a, b = (self.joint_xy(f"{j}_{s}") for j in LIMBS[k])
            if a is None or b is None:
                return None, "missing_joints", 0.0
            ra, rb = self.end_radius(cid, a), self.end_radius(cid, b)
            if ra is None or rb is None:
                r = self.radius(cid, a, b)
                ra, rb = ra or r, rb or r
            return _tapered(self.shape, a, b, ra, rb) | m, f"tapered_capsule(r={ra:.1f}->{rb:.1f})", 0.75
        if k in HULL_KINDS:
            h = _hull(m)
            return (h, "convex_hull", 0.7) if h is not None else (None, "hull_failed", 0.0)
        if k == "neck":
            n, hd = self.joint_xy("neck"), self.joint_xy("head")
            bb = M.bbox_xyxy(m)
            if n is None or hd is None or bb is None:
                return None, "missing_neck_evidence", 0.0
            w = bb[2] - bb[0]
            u = np.array(hd) - np.array(n)
            u = u / (np.linalg.norm(u) or 1)
            top, bottom = np.array(n) + u * 1.5 * w, np.array(n) - u * 1.0 * w
            return _capsule(self.shape, tuple(top), tuple(bottom), w / 2) | m, "neck_capsule_from_visible_width", 0.5
        if k == "head":
            others = np.zeros(self.shape, bool)
            for c2, m2 in self.comp.items():
                if c2 != cid:
                    others |= m2
            ears = self.face_masks.get("ear_r", np.zeros(self.shape, bool)) | self.face_masks.get("ear_l", np.zeros(self.shape, bool))
            edge = m & ~M.erode(m, 1)
            outline = edge & ~M.dilate(others, 2) & ~M.dilate(ears, 3)
            ys, xs = np.nonzero(outline)
            e = fit_ellipse(xs.astype(float), ys.astype(float), self.shape)
            if e is None:
                self.flags.append("head outline ellipse fit failed: forehead/skull under hair not estimated")
                return None, "ellipse_fit_failed", 0.0
            return e | m, "ellipse_fit_to_visible_skull_outline", 0.55
        if k == "eye_white":
            eye = self.face_masks.get(cid.replace("_white", ""))
            return (eye, "whole_eye_mask", 0.85) if eye is not None else (None, "no_eye_mask", 0.0)
        return None, "no_shape_model (front-most or not reconstructable)", 0.0

    # -- graph ------------------------------------------------------------------------------------
    def edges(self) -> list[dict[str, Any]]:
        ids = sorted(self.comp)
        dil = {c: M.dilate(self.comp[c], self.tol) for c in ids}
        forced = {frozenset((o["a"], o["b"])): o["front"] for o in self.overrides.get("edges", [])}
        out = []
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                zone = (dil[a] & self.comp[b]) | (dil[b] & self.comp[a])
                if M.area(zone) < 6:
                    continue
                # occlusion cue uses ALL dark pixels: the dark mass at a boundary belongs to the occluder
                da, db = M.area(zone & self.comp[a] & self.dark_all), M.area(zone & self.comp[b] & self.dark_all)
                prior = PRIORS.get(frozenset((kind(a), kind(b))))
                prior_front = None if prior is None else (a if kind(a) == prior[0] else b)
                ev: dict[str, Any] = {"contact_pixels": M.area(zone), "line_pixels": {a: da, b: db}}
                ambiguous, method = False, ""
                if frozenset((a, b)) in forced:
                    front, conf, method = forced[frozenset((a, b))], 1.0, "manual_override"
                else:
                    cue_front, strength = None, 0.0
                    if da + db >= 8:
                        r = da / (da + db)
                        strength = abs(r - 0.5) * 2
                        if strength >= 0.3:
                            cue_front = a if r > 0.5 else b
                    if cue_front and prior_front:
                        if cue_front == prior_front:
                            front, conf, method = cue_front, min(0.98, max(0.5 + 0.5 * strength, prior[1]) + 0.1), "linework+prior"
                        else:
                            front, conf, method, ambiguous = cue_front, 0.5, "linework_conflicts_prior", True
                    elif cue_front:
                        front, conf, method = cue_front, 0.5 + 0.4 * strength, "linework"
                    elif prior_front:
                        front, conf, method = prior_front, prior[1], "prior"
                        ambiguous = prior[1] < 0.6
                    else:
                        front = a if self.z.get(a, 0) >= self.z.get(b, 0) else b
                        conf, method, ambiguous = 0.3, "phase1_z_order_fallback", True
                back = b if front == a else a
                pair = tuple(sorted((kind(a), kind(b))))
                jbase = JOINT_OF_PAIR.get(pair) or JOINT_OF_PAIR.get(pair[::-1])
                s = side(a) or side(b)
                joint = None
                if jbase:
                    joint = jbase if jbase == "neck" else (f"{jbase}_{s}" if s else None)
                z_ok = self.z.get(front, 0) > self.z.get(back, 0)
                out.append({"front": front, "back": back, "confidence": round(conf, 3), "method": method,
                            "ambiguous": ambiguous, "joint": joint, "evidence": ev,
                            "consistent_with_phase1_z_order": z_ok})
        return out

    # -- regions ------------------------------------------------------------------------------------
    def regions(self, edges: list[dict[str, Any]]) -> list[Region]:
        shapes = {c: self.expected_shape(c) for c in self.comp}
        excluded = set(self.overrides.get("exclude_regions", []))
        out: list[Region] = []
        for e in edges:
            back, front = e["back"], e["front"]
            E, method, shape_conf = shapes[back]
            if E is None:
                continue
            hidden = E & self.comp[front] & ~self.comp[back]
            kb, kf = kind(back), kind(front)
            joint = e["joint"]
            exposed: list[str] = []
            if joint and joint != "neck" and not (kb == "torso" and kf in ARM_CHAIN):
                # limb chains: only the area around the joint is exposed by bending
                jx = self.joint_xy(joint)
                if jx is not None:
                    rr = 1.6 * max(self.radius(back, jx, jx) if kb in LIMBS else 0, 6 * self.tol)
                    disc = np.zeros(self.shape, bool)
                    yy, xx = np.ogrid[:self.shape[0], :self.shape[1]]
                    disc = (xx - jx[0]) ** 2 + (yy - jx[1]) ** 2 <= rr * rr
                    hidden &= disc
                exposed = [joint]
            elif kb == "torso" and kf in ARM_CHAIN:
                exposed = [f"shoulder_{side(front)}"] + ([f"elbow_{side(front)}"] if kf != "upper_arm" else [])
            elif kb == "neck" or (kb == "head" and kf == "neck"):
                exposed = ["neck"]
            elif kb == "head" and kf in ("feature", "eye_white", "eye_iris"):
                exposed = FEATURE_CONTROLS.get(front.split("_")[0], [])
            elif kb == "head" and kf == "hair":
                exposed = ["head_yaw", "head_pitch", "hair_secondary_motion"]
            elif kb == "eye_white":
                exposed = ["eye_look_x", "eye_look_y"]
            if M.area(hidden) < 4:
                continue
            rid = f"{back}__under__{front}"
            if rid in excluded:
                continue
            notes = []
            if not exposed:
                notes.append("not exposed by any planned joint or facial control")
            if e["ambiguous"]:
                notes.append("front/back relation ambiguous")
            out.append(Region(rid, back, [front], hidden, joint, exposed, method,
                              round(min(e["confidence"], shape_conf), 3), e["ambiguous"] or shape_conf < 0.6, notes=notes))
        for rid, path_mask in self.overrides.get("region_masks", {}).items():  # manual replacement masks
            for r in out:
                if r.id == rid:
                    r.mask = path_mask & ~self.comp[r.owner]
                    r.method, r.confidence = "manual_mask", 1.0
                    r.notes.append("mask replaced manually")
        # brows/ears touching hair: they may continue underneath -> needs review, no fill
        hair = self.comp.get("hair")
        if hair is not None:
            for fid in ("brow_r", "brow_l", "ear_r", "ear_l"):
                m = self.comp.get(fid, self.face_masks.get(fid))
                if m is not None and M.area(M.contact_zone(m, hair, self.tol)) >= 6:
                    self.flags.append(f"{fid} touches the hair: it may continue under the hair (cannot be inferred)")
        return out


def resolve_z_order(components: list[str], edges: list[dict[str, Any]], fallback: dict[str, float],
                    min_conf: float = 0.5) -> tuple[list[str], list[dict[str, Any]]]:
    """Back-to-front order honouring confident occlusion edges (front drawn later).
    Cycles are broken by dropping the least confident edge; ties use Phase 1 order."""
    use = sorted([e for e in edges if e["confidence"] >= min_conf], key=lambda e: -e["confidence"])
    dropped: list[dict[str, Any]] = []
    while True:
        after = {c: set() for c in components}  # back -> fronts
        indeg = {c: 0 for c in components}
        for e in use:
            if e["front"] not in after[e["back"]]:
                after[e["back"]].add(e["front"])
                indeg[e["front"]] += 1
        order, ready = [], sorted([c for c in components if indeg[c] == 0], key=lambda c: fallback.get(c, 0))
        while ready:
            c = ready.pop(0)
            order.append(c)
            for f in sorted(after[c], key=lambda c2: fallback.get(c2, 0)):
                indeg[f] -= 1
                if indeg[f] == 0:
                    ready.append(f)
                    ready.sort(key=lambda c2: fallback.get(c2, 0))
        if len(order) == len(components):
            return order, dropped
        weakest = use.pop()  # least confident remaining edge
        dropped.append(weakest)
