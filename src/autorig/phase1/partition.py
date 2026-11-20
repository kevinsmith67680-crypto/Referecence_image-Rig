"""Anatomy-guided partition of the character into body-part masks.

Segmentation gives *visible regions* (hair, head, a shirt that covers the
torso and both upper arms, bare-skin arms, shorts covering pelvis and thighs,
...). Animation needs *anatomical parts* (torso vs upper arm, pelvis vs
thigh) whose boundaries pass through clothing at the joints.

Rules:
* hair, head, neck, hands and feet come directly from their masks;
* every other character pixel goes to the nearest bone segment (from the
  anatomy joints), constrained by the region it belongs to: a pixel of the
  right "arm" mask may only become upper_arm_r / forearm_r, a shirt pixel only
  torso or an upper arm, a shorts pixel only pelvis or a thigh.
The result is a partition: no pixel belongs to two parts, none is dropped
silently (unassignable pixels are reported). Original pixels are never
altered; only membership is decided here.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy import ndimage

from . import masks as M
from .anatomy import PART_SEGMENTS, JointEstimate, PoseContext

BODY_PARTS = ["hair", "head", "neck", "torso", "pelvis", "upper_arm_r", "forearm_r", "hand_r",
              "upper_arm_l", "forearm_l", "hand_l", "thigh_r", "shin_r", "foot_r", "thigh_l", "shin_l", "foot_l"]


LIMB_PARTS = {"upper_arm_r", "forearm_r", "upper_arm_l", "forearm_l", "thigh_r", "shin_r", "thigh_l", "shin_l"}


def _seg_dist(px: np.ndarray, py: np.ndarray, a, b) -> tuple[np.ndarray, np.ndarray]:
    """Distance to segment ab and the unclipped projection parameter t."""
    ax, ay = a
    bx, by = b
    vx, vy = bx - ax, by - ay
    L2 = vx * vx + vy * vy
    if L2 == 0:
        return np.hypot(px - ax, py - ay), np.zeros_like(px)
    t_raw = ((px - ax) * vx + (py - ay) * vy) / L2
    t = np.clip(t_raw, 0.0, 1.0)
    return np.hypot(px - (ax + t * vx), py - (ay + t * vy)), t_raw


def _inscribed_radius(m: np.ndarray) -> float:
    return float(ndimage.distance_transform_edt(m).max()) if m.any() else 0.0


def _part_radii(ctx: PoseContext) -> dict[str, float]:
    """Thickness of each bone part, from its own visible region (no proportions assumed)."""
    radii: dict[str, float] = {}
    torso = ctx.union("upper_clothing") & ~ctx.union("arm", "hand")
    if torso.any():
        radii["torso"] = _inscribed_radius(M.largest_component(torso))
    radii["pelvis"] = 0.5 * _inscribed_radius(ctx.union("lower_clothing"))
    for side in ("r", "l"):
        arm = ctx.sides.get("arm", {}).get(side)
        leg = ctx.sides.get("leg", {}).get(side)
        if arm is not None:
            radii[f"upper_arm_{side}"] = radii[f"forearm_{side}"] = 0.9 * _inscribed_radius(arm.mask)
        if leg is not None:
            radii[f"thigh_{side}"] = radii[f"shin_{side}"] = 0.9 * _inscribed_radius(leg.mask)
    return radii


def partition(ctx: PoseContext, joints: dict[str, JointEstimate]) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    shape = ctx.shape
    parts = {p: np.zeros(shape, dtype=bool) for p in BODY_PARTS}
    hair = ctx.union("hair")
    parts["hair"] = hair & ctx.silhouette
    parts["head"] = ctx.head_region & ~hair
    parts["neck"] = ctx.union("neck") & ~parts["head"] & ~hair
    taken = parts["hair"] | parts["head"] | parts["neck"]
    # visible clothing boundary: sleeves belong to the upper arms (guarded against a whole-shirt "sleeve")
    shirt_area = M.area(ctx.union("upper_clothing")) or 1
    sleeve_flags = []
    for side in ("r", "l"):
        lm = ctx.sides.get("sleeve", {}).get(side)
        if lm is None:
            continue
        if M.area(lm.mask) > 0.4 * shirt_area:
            sleeve_flags.append(f"sleeve_{side} ignored: covers >40% of the shirt (not a sleeve)")
            continue
        parts[f"upper_arm_{side}"] |= lm.mask & ~taken
        taken |= parts[f"upper_arm_{side}"]
    for side in ("r", "l"):
        for label, part in (("hand", f"hand_{side}"), ("foot", f"foot_{side}")):
            lm = ctx.sides.get(label, {}).get(side)
            if lm is not None:
                parts[part] = lm.mask & ~taken
                taken |= parts[part]
    rest = ctx.silhouette & ~taken

    # which bone parts exist (both joints of every segment located)
    usable = {part: segs for part, segs in PART_SEGMENTS.items()
              if all(joints[a].x is not None and joints[b].x is not None for a, b in segs)}
    ys, xs = np.nonzero(rest)
    px, py = xs.astype(np.float32), ys.astype(np.float32)
    names = list(usable)
    radii = _part_radii(ctx)
    dist = np.full((len(names), len(xs)), np.inf, dtype=np.float32)
    for i, part in enumerate(names):
        r = radii.get(part, 0.0)
        for a, b in usable[part]:
            d, t = _seg_dist(px, py, joints[a].xy, joints[b].xy)
            if part in LIMB_PARTS:  # thickness only alongside the limb, not beyond its proximal joint
                d = d - r * (t >= 0.0)
            else:
                d = d - r
            dist[i] = np.minimum(dist[i], d)

    # region constraints (limb masks first, then clothing)
    allowed = np.ones_like(dist, dtype=bool)
    groups = []
    for side in ("r", "l"):
        for label, cands in (("arm", {f"upper_arm_{side}", f"forearm_{side}"}),
                             ("leg", {f"thigh_{side}", f"shin_{side}"})):
            lm = ctx.sides.get(label, {}).get(side)
            if lm is not None:
                groups.append((lm.mask[ys, xs], cands, f"{label}_{side}"))
    groups.append((ctx.union("upper_clothing")[ys, xs], {"torso", "upper_arm_r", "upper_arm_l"}, "upper_clothing"))
    groups.append((ctx.union("lower_clothing")[ys, xs], {"pelvis", "thigh_r", "thigh_l"}, "lower_clothing"))
    constrained = np.zeros(len(xs), dtype=bool)
    for member, cands, _ in groups:
        sel = member & ~constrained
        rows = np.array([n in cands for n in names], dtype=bool)
        if rows.any():
            allowed[:, sel] = rows[:, None]
            constrained |= sel
    dist = np.where(allowed, dist, np.inf)
    unassigned = np.zeros(shape, dtype=bool)
    if names and len(xs):
        best = np.argmin(dist, axis=0)
        ok = np.isfinite(dist[best, np.arange(len(xs))])
        for i, part in enumerate(names):
            sel = ok & (best == i)
            parts[part][ys[sel], xs[sel]] = True
        unassigned[ys[~ok], xs[~ok]] = True
    else:
        unassigned = rest

    info: dict[str, Any] = {"parts": {}, "unassigned_pixels": M.area(unassigned), "clothing_regions": {},
                            "flags": sleeve_flags}
    clothing = {"upper_clothing": ctx.union("upper_clothing"), "lower_clothing": ctx.union("lower_clothing")}
    for part, m in parts.items():
        a = M.area(m)
        seg_joints = [j for ab in PART_SEGMENTS.get(part, []) for j in ab]
        conf = min([joints[j].confidence for j in seg_joints] or [1.0]) if a else 0.0
        info["parts"][part] = {
            "pixels": a, "bbox_xyxy": M.bbox_xyxy(m), "empty": a == 0,
            "method": "direct_mask" if part not in PART_SEGMENTS else "nearest_bone_segment_within_region",
            "joint_confidence": round(conf, 3), "truncated_by_image_border": M.touches_border(m) if a else False,
            "clothing_coverage": {k: round(M.area(m & c) / a, 3) for k, c in clothing.items() if a and M.area(m & c)},
        }
    for k, c in clothing.items():
        total = M.area(c)
        if total:
            info["clothing_regions"][k] = {p: round(M.area(m & c) / total, 3) for p, m in parts.items() if M.area(m & c)}
    return parts, info | {"unassigned_mask": unassigned}
