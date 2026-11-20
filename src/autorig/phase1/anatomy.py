"""CharacterAnatomyAnalyzer: approximate skeletal joints for a cartoon character.

Pose estimation is pluggable (:class:`PoseEstimator`). Estimators run in
priority order and the first *reliable* estimate per joint wins:

* :class:`ManualLandmarkEstimator` - human-provided positions (confidence 1.0).
* :class:`MaskGeometryPoseEstimator` - derives joints from segmentation masks
  (contact zones between touching parts, limb medial paths, clothing-aware
  proximal-joint search). It assumes NO human proportions; every joint
  carries a method, confidence and flags, and weak ones are marked unreliable.
* A vision-language-model estimator can be added by implementing
  ``PoseEstimator`` (not implemented in Phase 1: no verified VLM API in use).

Coordinates: original image pixels. Side convention: front-facing character,
so the character's LEFT (suffix ``_l``) is on the IMAGE RIGHT.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra

from . import masks as M

JOINT_PARENTS: dict[str, str | None] = {
    "pelvis": None, "torso": "pelvis", "neck": "torso", "head": "neck",
    "shoulder_r": "torso", "elbow_r": "shoulder_r", "wrist_r": "elbow_r", "hand_r": "wrist_r",
    "shoulder_l": "torso", "elbow_l": "shoulder_l", "wrist_l": "elbow_l", "hand_l": "wrist_l",
    "hip_r": "pelvis", "knee_r": "hip_r", "ankle_r": "knee_r", "foot_r": "ankle_r",
    "hip_l": "pelvis", "knee_l": "hip_l", "ankle_l": "knee_l", "foot_l": "ankle_l",
}
JOINTS = list(JOINT_PARENTS)

# Anatomical body parts and the bone segment(s) that define them.
PART_SEGMENTS: dict[str, list[tuple[str, str]]] = {
    "torso": [("pelvis", "neck")],
    "pelvis": [("pelvis", "hip_r"), ("pelvis", "hip_l")],
    "upper_arm_r": [("shoulder_r", "elbow_r")], "forearm_r": [("elbow_r", "wrist_r")],
    "upper_arm_l": [("shoulder_l", "elbow_l")], "forearm_l": [("elbow_l", "wrist_l")],
    "thigh_r": [("hip_r", "knee_r")], "shin_r": [("knee_r", "ankle_r")],
    "thigh_l": [("hip_l", "knee_l")], "shin_l": [("knee_l", "ankle_l")],
}
PART_PIVOT_JOINT = {
    "hair": "neck", "head": "neck", "neck": "neck", "torso": "pelvis", "pelvis": "pelvis",
    "upper_arm_r": "shoulder_r", "forearm_r": "elbow_r", "hand_r": "wrist_r",
    "upper_arm_l": "shoulder_l", "forearm_l": "elbow_l", "hand_l": "wrist_l",
    "thigh_r": "hip_r", "shin_r": "knee_r", "foot_r": "ankle_r",
    "thigh_l": "hip_l", "shin_l": "knee_l", "foot_l": "ankle_l",
}
RELIABLE = 0.5


@dataclass
class JointEstimate:
    name: str
    x: float | None
    y: float | None
    confidence: float
    method: str
    flags: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    occluded_by: list[str] = field(default_factory=list)
    visible: bool | None = None

    @property
    def reliable(self) -> bool:
        return self.x is not None and self.confidence >= RELIABLE

    @property
    def xy(self) -> tuple[float, float] | None:
        return None if self.x is None else (self.x, self.y)

    def record(self) -> dict[str, Any]:
        d = asdict(self)
        d["position"] = None if self.x is None else [round(self.x, 2), round(self.y, 2)]
        del d["x"], d["y"]
        d["confidence"] = round(self.confidence, 3)
        d["reliable"] = self.reliable
        d["parent"] = JOINT_PARENTS.get(self.name)
        return d


@dataclass
class LabeledMask:
    label: str
    mask: np.ndarray
    score: float
    provenance: dict[str, Any]


@dataclass
class PoseContext:
    shape: tuple[int, int]  # (H, W)
    labeled: dict[str, list[LabeledMask]]  # label -> instances (sides assigned below)
    sides: dict[str, dict[str, LabeledMask]]  # label -> {"r": ..., "l": ...}
    silhouette: np.ndarray
    midline_x: float
    head_region: np.ndarray

    def union(self, *labels: str) -> np.ndarray:
        out = np.zeros(self.shape, dtype=bool)
        for lab in labels:
            for lm in self.labeled.get(lab, []):
                out |= lm.mask
        return out

    def best_score(self, *labels: str) -> float:
        scores = [lm.score for lab in labels for lm in self.labeled.get(lab, [])]
        return max(scores) if scores else 0.0


class PoseEstimator(Protocol):
    name: str

    def estimate(self, ctx: PoseContext) -> dict[str, JointEstimate]: ...


# --- instance collection ------------------------------------------------------------

def collect_instances(run, labels: list[str], shape: tuple[int, int], *, min_score: float = 0.5,
                      min_area_frac: float = 0.00005) -> tuple[dict[str, list[LabeledMask]], list[dict]]:
    """Pool instances per label, drop weak/tiny ones and near-duplicates.
    Returns (accepted, rejected-with-reasons)."""
    min_area = max(4, int(min_area_frac * shape[0] * shape[1]))
    accepted: dict[str, list[LabeledMask]] = {lab: [] for lab in labels}
    rejected: list[dict] = []
    for res in run.results:
        lab = res.prompt.name
        for inst in res.instances:
            prov = {"prompt_label": lab, "prompt_text": res.prompt.text, "prompt_index": inst.prompt_index,
                    "instance_index": inst.instance_index, "score": round(inst.score, 4)}
            if inst.mask.shape != shape:
                rejected.append(prov | {"reason": f"mask shape {inst.mask.shape} != image {shape}"})
                continue
            if inst.score < min_score:
                rejected.append(prov | {"reason": f"score {inst.score:.3f} < {min_score}"})
                continue
            if M.area(inst.mask) < min_area:
                rejected.append(prov | {"reason": f"area {M.area(inst.mask)} px < {min_area}"})
                continue
            dup = next((lm for lm in accepted.setdefault(lab, []) if M.iou(lm.mask, inst.mask) > 0.85), None)
            if dup is not None:
                if inst.score > dup.score:
                    accepted[lab].remove(dup)
                    rejected.append(dup.provenance | {"reason": "duplicate (IoU>0.85) of a higher-scoring instance"})
                else:
                    rejected.append(prov | {"reason": "duplicate (IoU>0.85) of a higher-scoring instance"})
                    continue
            accepted[lab].append(LabeledMask(lab, inst.mask, inst.score, prov))
    return accepted, rejected


def assign_sides(instances: list[LabeledMask], midline_x: float) -> tuple[dict[str, LabeledMask], list[str]]:
    """Two-sided parts: image-left instance -> '_r' (character's right)."""
    flags: list[str] = []
    pieces: list[LabeledMask] = []
    for lm in instances:
        comps = [c for c in M.components(lm.mask) if M.area(c) > 0.15 * M.area(lm.mask)]
        xs = [M.centroid(c)[0] for c in comps]
        if len(comps) >= 2 and min(xs) < midline_x < max(xs):  # one instance spanning both sides
            flags.append(f"{lm.label}: one instance covered both sides; split into components")
            for c in comps:
                pieces.append(LabeledMask(lm.label, c, lm.score, lm.provenance | {"split_component": True}))
        else:
            pieces.append(lm)
    if len(pieces) > 2:
        flags.append(f"{pieces[0].label}: {len(pieces)} instances; kept the two largest")
        pieces = sorted(pieces, key=lambda p: M.area(p.mask), reverse=True)[:2]
    out: dict[str, LabeledMask] = {}
    for lm in sorted(pieces, key=lambda p: M.centroid(p.mask)[0]):
        side = "r" if M.centroid(lm.mask)[0] < midline_x else "l"
        if side in out:
            flags.append(f"{lm.label}: two instances on the same side; side assignment ambiguous")
            side = "l" if side == "r" else "r"
            if side in out:
                continue
        out[side] = lm
    return out, flags


# --- limb geometry ------------------------------------------------------------------------

def _graph(small: np.ndarray, weights: np.ndarray):
    h, w = small.shape
    idx = -np.ones(small.shape, dtype=np.int64)
    ys, xs = np.nonzero(small)
    idx[ys, xs] = np.arange(len(xs))
    rows, cols, vals = [], [], []
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        y2, x2 = ys + dy, xs + dx
        ok = (y2 >= 0) & (y2 < h) & (x2 >= 0) & (x2 < w)
        ok[ok] = small[y2[ok], x2[ok]]
        a, b = idx[ys[ok], xs[ok]], idx[y2[ok], x2[ok]]
        step = math.hypot(dx, dy)
        wv = step * 0.5 * (weights[ys[ok], xs[ok]] + weights[y2[ok], x2[ok]])
        rows += [a, b]
        cols += [b, a]
        vals += [wv, wv]
    n = len(xs)
    g = coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n)).tocsr()
    return g, idx, ys, xs


@dataclass
class LimbAxis:
    path: list[tuple[float, float]]  # full-res points from proximal extreme to distal end
    radius: float  # median half-width (px)


def limb_axis(limb: np.ndarray, distal_xy: tuple[float, float], max_side: int = 160) -> LimbAxis | None:
    """Medial path from the limb's geodesically farthest point (proximal
    extreme) to ``distal_xy``. Computed on a downscaled crop for speed."""
    bb = M.bbox_xyxy(limb)
    if bb is None:
        return None
    x0, y0, x1, y1 = bb
    pad = 2
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(limb.shape[1], x1 + pad), min(limb.shape[0], y1 + pad)
    crop = limb[y0:y1, x0:x1]
    f = max(1.0, max(crop.shape) / max_side)
    sw, sh = max(1, round(crop.shape[1] / f)), max(1, round(crop.shape[0] / f))
    small = M.resize_mask_area(crop, (sw, sh))
    if small.sum() < 3:
        small = M.resize_mask_nearest(crop, (sw, sh))
    if small.sum() < 3:
        return None
    small = M.largest_component(small)
    dt = ndimage.distance_transform_edt(small)
    g_unit, idx, ys, xs = _graph(small, np.ones_like(dt))
    to_full = lambda sx, sy: (x0 + (sx + 0.5) * crop.shape[1] / sw, y0 + (sy + 0.5) * crop.shape[0] / sh)
    dx_s = (distal_xy[0] - x0) * sw / crop.shape[1]
    dy_s = (distal_xy[1] - y0) * sh / crop.shape[0]
    d_node = int(np.argmin((xs - dx_s) ** 2 + (ys - dy_s) ** 2))
    geo = dijkstra(g_unit, indices=d_node)
    geo[~np.isfinite(geo)] = -1
    p_node = int(np.argmax(geo))
    g_med, *_ = _graph(small, 1.0 / (dt ** 2 + 0.25))
    _, pred = dijkstra(g_med, indices=p_node, return_predecessors=True)
    path_nodes, node = [], d_node
    while node != p_node and node >= 0:
        path_nodes.append(node)
        node = pred[node]
    path_nodes.append(p_node)
    path_nodes.reverse()  # proximal -> distal
    path = [to_full(xs[n], ys[n]) for n in path_nodes]
    radius = float(np.median(dt[ys[path_nodes], xs[path_nodes]])) * f
    return LimbAxis(path=path, radius=max(radius, 1.0))


def _arc_point(path, start_xy, distance):
    """Point at arc length ``distance`` along ``path`` starting from its first point."""
    acc, prev = 0.0, path[0]
    for p in path[1:]:
        seg = math.dist(prev, p)
        if acc + seg >= distance and seg > 0:
            t = (distance - acc) / seg
            return (prev[0] + t * (p[0] - prev[0]), prev[1] + t * (p[1] - prev[1]))
        acc += seg
        prev = p
    return path[-1]


def _proximal_axis(axis: LimbAxis) -> tuple[tuple[float, float], tuple[float, float]]:
    """Line fitted (PCA) to the proximal half of the medial path. The geodesic
    extreme is usually a corner of the limb's end, so the raw path bends toward
    it; the fitted line does not. Returns (start point on the line at the
    proximal end, unit direction pointing proximally)."""
    pts = np.asarray(axis.path, dtype=float)
    seg = np.r_[0.0, np.cumsum(np.hypot(*np.diff(pts, axis=0).T))]
    total = seg[-1] or 1.0
    sel = pts[(seg >= min(axis.radius, 0.25 * total)) & (seg <= 0.5 * total)]
    if len(sel) < 2:
        sel = pts
    c = sel.mean(axis=0)
    _, _, vt = np.linalg.svd(sel - c, full_matrices=False)
    v = vt[0]
    if np.dot(pts[0] - c, v) < 0:
        v = -v
    t = max(float(np.dot(sel - c, v).max()), 0.0)
    start = c + v * t
    return (float(start[0]), float(start[1])), (float(v[0]), float(v[1]))


def _march(region: np.ndarray, start, direction, max_steps: int = 4000, grace: int = 4):
    """Walk from ``start`` along ``direction`` while inside ``region``; returns the
    last inside point. Up to ``grace`` initial steps may be outside (start on a boundary)."""
    h, w = region.shape
    x, y = start
    last, entered = start, False
    for step in range(max_steps):
        x += direction[0]
        y += direction[1]
        xi, yi = int(round(x)), int(round(y))
        inside = 0 <= xi < w and 0 <= yi < h and bool(region[yi, xi])
        if inside:
            entered, last = True, (x, y)
        elif entered or step >= grace:
            break
    return last


def _occluders(ctx: PoseContext, xy, own_labels: set[str]) -> list[str]:
    if xy is None:
        return []
    xi, yi = int(round(xy[0])), int(round(xy[1]))
    if not (0 <= yi < ctx.shape[0] and 0 <= xi < ctx.shape[1]):
        return []
    out = []
    for lab, items in ctx.labeled.items():
        if lab in own_labels or lab == "person":
            continue
        if any(lm.mask[yi, xi] for lm in items):
            out.append(lab)
    return sorted(out)


class ManualLandmarkEstimator:
    """Human-provided joints: ``{"joints": {"elbow_r": [x, y], ...}}`` (pixels)."""

    name = "manual"

    def __init__(self, joints: dict[str, tuple[float, float]], source: str = "manual"):
        unknown = set(joints) - set(JOINTS)
        if unknown:
            raise ValueError(f"unknown joint names: {sorted(unknown)}")
        self.joints = joints
        self.source = source

    @classmethod
    def from_file(cls, path: str | Path) -> "ManualLandmarkEstimator":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls({k: tuple(v) for k, v in data["joints"].items()}, source=str(path))

    def estimate(self, ctx: PoseContext) -> dict[str, JointEstimate]:
        return {k: JointEstimate(k, float(x), float(y), 1.0, "manual", sources=[self.source])
                for k, (x, y) in self.joints.items()}


def contact_tolerance(shape: tuple[int, int]) -> int:
    """Gap tolerated between parts that should touch (masks rarely meet exactly)."""
    return max(4, round(0.006 * max(shape)))


class MaskGeometryPoseEstimator:
    name = "mask_geometry"

    def estimate(self, ctx: PoseContext) -> dict[str, JointEstimate]:
        J: dict[str, JointEstimate] = {}
        head_score = ctx.best_score("head", "face")
        # head & neck
        c = M.centroid(ctx.head_region)
        near = M.dilate(ctx.head_region, 8)
        hair = np.zeros(ctx.shape, dtype=bool)
        for comp in M.components(ctx.union("hair")):  # whole hair pieces touching the head
            if (comp & near).any():
                hair |= comp
        hb = M.bbox_xyxy(ctx.head_region | hair)
        if c and hb:
            J["head"] = JointEstimate("head", (hb[0] + hb[2]) / 2, (hb[1] + hb[3]) / 2, head_score * 0.85,
                                      "head_and_hair_bbox_center", sources=["head", "face", "hair"])
        tol = contact_tolerance(ctx.shape)
        below = ctx.union("neck", "upper_clothing")
        zone = M.contact_zone(ctx.head_region, below, tol)
        if M.area(zone) >= 3:
            J["neck"] = JointEstimate("neck", *M.centroid(zone), head_score * 0.85, "contact:head/neck|clothing",
                                      sources=["head", "neck", "upper_clothing"])
        elif c:
            ys, xs = np.nonzero(ctx.head_region)
            bottom = ys.max()
            J["neck"] = JointEstimate("neck", float(xs[ys >= bottom - 2].mean()), float(bottom), head_score * 0.4,
                                      "head_bottom_fallback", flags=["no head/body contact found"], sources=["head"])
        # torso region = upper clothing minus arms/hands (central component)
        torso_region = ctx.union("upper_clothing") & ~ctx.union("arm", "hand")
        if M.area(torso_region):
            torso_region = M.largest_component(torso_region)
            J["torso"] = JointEstimate("torso", *M.centroid(torso_region), ctx.best_score("upper_clothing") * 0.8,
                                       "torso_region_centroid", sources=["upper_clothing", "arm"])
        # limbs
        for side in ("r", "l"):
            self._limb(ctx, J, side, limb="arm", distal="hand", covering="upper_clothing",
                       names=("shoulder", "elbow", "wrist", "hand"), proximal_rule="exit")
            self._limb(ctx, J, side, limb="leg", distal="foot", covering="lower_clothing",
                       names=("hip", "knee", "ankle", "foot"), proximal_rule="midpoint")
        # pelvis
        hr, hl = J.get("hip_r"), J.get("hip_l")
        if hr and hl and hr.x is not None and hl.x is not None:
            J["pelvis"] = JointEstimate("pelvis", (hr.x + hl.x) / 2, (hr.y + hl.y) / 2,
                                        min(hr.confidence, hl.confidence) * 0.9, "midpoint_of_hips",
                                        sources=["hip_r", "hip_l"])
        else:
            low = ctx.union("lower_clothing")
            bb = M.bbox_xyxy(low)
            if bb:
                top = low.copy()
                top[(bb[1] + bb[3]) // 2:, :] = False
                J["pelvis"] = JointEstimate("pelvis", *M.centroid(top), ctx.best_score("lower_clothing") * 0.5,
                                            "lower_clothing_upper_half_centroid", flags=["hips not both found"])
        for name in JOINTS:
            if name not in J:
                J[name] = JointEstimate(name, None, None, 0.0, "not_found", flags=["no evidence for this joint"])
        return J

    def _limb(self, ctx, J, side, *, limb, distal, covering, names, proximal_rule):
        prox_n, mid_n, dist_n, end_n = (f"{n}_{side}" for n in names)
        limb_lm = ctx.sides.get(limb, {}).get(side)
        dist_lm = ctx.sides.get(distal, {}).get(side)
        if dist_lm is not None:
            J[end_n] = JointEstimate(end_n, *M.centroid(dist_lm.mask), dist_lm.score * 0.9, f"{distal}_centroid",
                                     sources=[distal])
        if limb_lm is None:
            return
        score = limb_lm.score
        tol = contact_tolerance(ctx.shape)
        big = [c for c in M.components(limb_lm.mask) if M.area(c) >= 0.1 * M.area(limb_lm.mask)]
        fragmented = len(big) > 1
        if fragmented:
            score *= 0.6  # the limb mask is broken: every joint derived from it is less trustworthy
        # distal joint (wrist/ankle): contact with hand/foot (small gaps tolerated). SAM's "arm" often
        # INCLUDES the hand: then the overlap is the whole hand, so use the boundary of limb-minus-hand
        limb_c, reach, split = limb_lm.mask, tol, False
        if dist_lm is not None and M.area(limb_lm.mask & dist_lm.mask) > 0.2 * M.area(dist_lm.mask):
            # subtract a slightly grown hand/foot (the limb mask is often 1-2 px wider around the
            # fingers) and keep the main piece, so no thin ring around the hand pulls the joint inward
            rest = limb_lm.mask & ~M.dilate(dist_lm.mask, 2)
            if M.area(rest):
                limb_c, split = M.largest_component(rest), True
                # a cuff or band between them may leave a gap: bridge it when it is small
                gap = float(ndimage.distance_transform_edt(~dist_lm.mask)[limb_c].min())
                if gap <= 3 * tol:
                    reach = max(tol, int(np.ceil(gap)) + 2)
        if dist_lm is not None and M.area(M.contact_zone(limb_c, dist_lm.mask, reach)) >= 3:
            zone = M.contact_zone(limb_c, dist_lm.mask, reach)
            if split:  # both sides of the boundary, so the joint sits in the middle of any gap
                zone = zone | M.contact_zone(dist_lm.mask, limb_c, reach)
            dxy = M.centroid(zone)
            J[dist_n] = JointEstimate(dist_n, *dxy, min(score, dist_lm.score) * 0.95, f"contact:{limb}/{distal}",
                                      sources=[limb, distal])
            hint = dxy
        else:
            hint = M.centroid(dist_lm.mask) if dist_lm is not None else None
        if hint is None:  # no distal evidence: use the limb end farthest from the body midline/top
            ys, xs = np.nonzero(limb_lm.mask)
            k = int(np.argmax(ys))
            hint = (float(xs[k]), float(ys[k]))
        axis = limb_axis(limb_lm.mask, hint)
        if axis is None:
            return
        if dist_n not in J:
            J[dist_n] = JointEstimate(dist_n, *axis.path[-1], score * 0.5, "limb_path_end",
                                      flags=[f"no {distal} contact; distal end of {limb} mask used"], sources=[limb])
        # proximal joint (shoulder/hip)
        P, d = _proximal_axis(axis)
        cover = ctx.union(covering)
        flags: list[str] = []
        near_end = M.dilate(np.zeros(ctx.shape, dtype=bool), 0)
        px_, py_ = int(round(axis.path[0][0])), int(round(axis.path[0][1]))
        r_end = int(max(tol, 1.5 * axis.radius))
        near_end[max(0, py_ - r_end):py_ + r_end + 1, max(0, px_ - r_end):px_ + r_end + 1] = True
        cover_tol = int(max(tol, axis.radius))  # clothing over a joint may leave a gap up to ~limb width
        if M.area(cover) and M.area(M.contact_zone(limb_lm.mask & near_end, cover, cover_tol)) >= 3:
            region = M.dilate(cover | limb_lm.mask, max(1, (cover_tol + 1) // 2))  # bridge the gap
            exit_pt = _march(region, P, d)
            if proximal_rule == "exit":
                # inset by the covering region's local half-width just inside the exit point
                dt_region = ndimage.distance_transform_edt(region)
                inset = axis.radius
                for k in range(1, int(4 * axis.radius) + 1):
                    qx, qy = int(round(exit_pt[0] - d[0] * k)), int(round(exit_pt[1] - d[1] * k))
                    if 0 <= qy < region.shape[0] and 0 <= qx < region.shape[1]:
                        inset = max(inset, min(float(dt_region[qy, qx]), k))
                prox = (exit_pt[0] - d[0] * inset, exit_pt[1] - d[1] * inset)
                method, factor = f"march_through_{covering}_to_exit_minus_local_halfwidth", 0.5
            else:
                prox = ((exit_pt[0] + P[0]) / 2, (exit_pt[1] + P[1]) / 2)
                method, factor = f"midpoint_of_{covering}_span_along_limb_axis", 0.5
                # The path's proximal end curls toward the crotch, so for spread legs the march runs
                # inward and both hips converge. Keep that height, take x from the straight middle
                # of the leg's medial line (identical for vertical legs).
                line = _axis_line(axis, 0.25, 0.6)
                if line is not None and abs(line[1][1]) > 0.5:
                    (cx, cy), (vx, vy) = line
                    prox = (cx + (prox[1] - cy) / vy * vx, prox[1])
                    method += "+x_from_leg_midline"
            flags.append(f"{prox_n} hidden under {covering}: position extrapolated, always needs review")
            sources = [limb, covering]
        else:
            body = np.zeros(ctx.shape, dtype=bool)  # real parts only - never the coarse "person" mask
            for lab, items in ctx.labeled.items():
                if lab not in ("person", limb):
                    for other in items:
                        body |= other.mask
            attached = M.area(M.contact_zone(limb_lm.mask & near_end, body, tol)) >= 3
            prox = P
            if attached:
                method, factor, sources = "limb_proximal_extreme_inset_by_radius", 0.7, [limb]
            else:
                method, factor, sources = "limb_proximal_extreme_unattached", 0.35, [limb]
                flags.append(f"the {limb}'s proximal end touches nothing: the joint may be hidden or the mask "
                             "incomplete; verify or provide a manual landmark")
        if fragmented:
            flags.append(f"{limb} mask is fragmented into {len(big)} pieces; joints derived from it are uncertain")
        J[prox_n] = JointEstimate(prox_n, *prox, score * factor, method, flags=flags, sources=sources)
        # middle joint (elbow/knee): a real bend of the medial path, else an ambiguous midpoint
        a, b = J[prox_n].xy, J[dist_n].xy
        bend = _path_bend(axis)
        if bend is not None and bend[1] >= 30.0:
            J[mid_n] = JointEstimate(mid_n, *bend[0], score * 0.75, "medial_path_bend", sources=[limb],
                                     flags=[f"bend {bend[1]:.0f} deg"])
        elif bend is not None and bend[1] >= 15.0 and 0.3 <= _path_fraction(axis, bend[0]) <= 0.7:
            J[mid_n] = JointEstimate(mid_n, *bend[0], score * 0.45, "medial_path_weak_bend", sources=[limb],
                                     flags=[f"weak bend {bend[1]:.0f} deg: position uncertain; verify"])
        else:
            mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            snap = min(axis.path, key=lambda p: math.dist(p, mid))
            J[mid_n] = JointEstimate(mid_n, *snap, score * 0.4, "straight_limb_midpoint",
                                     flags=["limb is nearly straight (no bend >= 15 deg): middle joint position "
                                            "is ambiguous; verify or provide a manual landmark"], sources=[limb])


def _path_fraction(axis: LimbAxis, xy) -> float:
    """Position of the path point nearest ``xy`` as a fraction of the path length (0 = proximal)."""
    pts = np.asarray(axis.path, dtype=float)
    seg = np.r_[0.0, np.cumsum(np.hypot(*np.diff(pts, axis=0).T))]
    k = int(np.argmin(np.hypot(pts[:, 0] - xy[0], pts[:, 1] - xy[1])))
    return float(seg[k] / (seg[-1] or 1.0))


def _axis_line(axis: LimbAxis, lo: float, hi: float):
    """PCA line through the medial path between fractions ``lo`` and ``hi`` of its length.
    Returns (centre, unit direction) or None."""
    pts = np.asarray(axis.path, dtype=float)
    seg = np.r_[0.0, np.cumsum(np.hypot(*np.diff(pts, axis=0).T))]
    total = seg[-1]
    sel = pts[(seg >= lo * total) & (seg <= hi * total)]
    if total <= 0 or len(sel) < 3:
        return None
    c = sel.mean(axis=0)
    _, _, vt = np.linalg.svd(sel - c, full_matrices=False)
    return (float(c[0]), float(c[1])), (float(vt[0][0]), float(vt[0][1]))


def _path_bend(axis: LimbAxis) -> tuple[tuple[float, float], float] | None:
    """Strongest turning of the medial path, ignoring both curled ends.
    Returns (point, turning angle in degrees) or None if the path is too short."""
    pts = np.asarray(axis.path, dtype=float)
    seg = np.r_[0.0, np.cumsum(np.hypot(*np.diff(pts, axis=0).T))]
    total = seg[-1]
    trim = 1.5 * axis.radius
    w = max(2.0 * axis.radius, 0.15 * total)
    if total < 2 * trim + 2 * w * 0.5:
        return None
    at = lambda s_: pts[int(np.clip(np.searchsorted(seg, s_), 0, len(pts) - 1))]
    best, best_ang = None, 0.0
    for s_ in np.linspace(trim + w / 2, total - trim - w / 2, 40):
        p, q, r = at(s_ - w / 2), at(s_), at(s_ + w / 2)
        v1, v2 = q - p, r - q
        n1, n2 = np.hypot(*v1), np.hypot(*v2)
        if n1 == 0 or n2 == 0:
            continue
        ang = float(np.degrees(np.arccos(np.clip(np.dot(v1, v2) / (n1 * n2), -1, 1))))
        if ang > best_ang:
            best, best_ang = (float(q[0]), float(q[1])), ang
    return (best, best_ang) if best is not None else None


class CharacterAnatomyAnalyzer:
    def __init__(self, estimators: list[PoseEstimator] | None = None):
        self.estimators = estimators or [MaskGeometryPoseEstimator()]

    def build_context(self, shape, labeled: dict[str, list[LabeledMask]], silhouette: np.ndarray):
        head_region = np.zeros(shape, dtype=bool)
        for lab in ("head", "face"):
            for lm in labeled.get(lab, []):
                head_region |= lm.mask
        for lm in labeled.get("hair", []):
            head_region &= ~lm.mask
        if M.area(head_region):
            head_region = M.largest_component(head_region)
        hc = M.centroid(head_region)
        sc = M.centroid(silhouette)
        midline = hc[0] if hc else (sc[0] if sc else shape[1] / 2)
        sides, flags = {}, []
        for lab in ("arm", "hand", "leg", "foot", "sleeve"):
            sides[lab], f = assign_sides(labeled.get(lab, []), midline)
            flags += f
        ctx = PoseContext(shape=shape, labeled=labeled, sides=sides, silhouette=silhouette, midline_x=midline,
                          head_region=head_region)
        return ctx, flags

    def analyze(self, ctx: PoseContext) -> tuple[dict[str, JointEstimate], dict[str, dict[str, JointEstimate]]]:
        per_est = {e.name: e.estimate(ctx) for e in self.estimators}
        final: dict[str, JointEstimate] = {}
        for name in JOINTS:
            cands = [(e.name, per_est[e.name].get(name)) for e in self.estimators]
            cands = [(n, c) for n, c in cands if c is not None and c.x is not None]
            chosen = next((c for _, c in cands if c.reliable), None)
            if chosen is None and cands:
                chosen = max((c for _, c in cands), key=lambda c: c.confidence)
            final[name] = chosen or JointEstimate(name, None, None, 0.0, "not_found", flags=["no estimator found it"])
        # occlusion / visibility
        own = {"head": {"head", "face", "hair"}, "neck": {"neck", "head", "face"}, "torso": {"upper_clothing"},
               "pelvis": {"lower_clothing"}}
        for name, j in final.items():
            if j.x is None:
                continue
            base = name.rsplit("_", 1)[0]
            labels = own.get(name) or {
                "shoulder": {"arm"}, "elbow": {"arm"}, "wrist": {"arm", "hand"}, "hand": {"hand"},
                "hip": {"leg"}, "knee": {"leg"}, "ankle": {"leg", "foot"}, "foot": {"foot"}}.get(base, set())
            j.occluded_by = _occluders(ctx, j.xy, labels)
            xi, yi = int(round(j.x)), int(round(j.y))
            inside = 0 <= yi < ctx.shape[0] and 0 <= xi < ctx.shape[1] and bool(ctx.silhouette[yi, xi])
            j.visible = inside and not j.occluded_by
        return final, per_est


def validate_joints(joints: dict[str, JointEstimate], shape: tuple[int, int],
                    silhouette: np.ndarray) -> list[dict[str, str]]:
    """Body-landmark validation: problems are reported, never silently fixed."""
    issues: list[dict[str, str]] = []
    h, w = shape
    dist_out = ndimage.distance_transform_edt(~silhouette) if silhouette.any() else None
    for name, j in joints.items():
        if j.x is None:
            issues.append({"joint": name, "severity": "error", "issue": "missing"})
            continue
        if not (0 <= j.x < w and 0 <= j.y < h):
            issues.append({"joint": name, "severity": "error", "issue": "outside the image"})
            continue
        if dist_out is not None and dist_out[int(j.y), int(j.x)] > 0.03 * max(h, w):
            issues.append({"joint": name, "severity": "warning", "issue": "far outside the character silhouette"})
        if not j.reliable:
            issues.append({"joint": name, "severity": "warning",
                           "issue": f"unreliable (confidence {j.confidence:.2f}, {j.method})"})
    for child, parent in JOINT_PARENTS.items():
        if parent and joints[child].x is not None and joints[parent].x is not None:
            if math.dist(joints[child].xy, joints[parent].xy) < 1.0:
                issues.append({"joint": child, "severity": "error", "issue": f"zero-length bone to {parent}"})
    for base in ("shoulder", "elbow", "wrist", "hand", "hip", "knee", "ankle", "foot"):
        r, l = joints[f"{base}_r"], joints[f"{base}_l"]
        if r.reliable and l.reliable and r.x > l.x:
            issues.append({"joint": base, "severity": "warning",
                           "issue": "character-right joint is right of character-left joint (crossed limbs or "
                                    "side swap) - review"})
    return issues
