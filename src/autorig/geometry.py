"""Coordinate conversion and 2D bone kinematics.

Coordinate systems
------------------
* **Canvas pixels** (the rig spec): origin at the top-left corner, +x right,
  +y down, units = pixels of a ``W x H`` canvas.
* **Moho world units** (bone layer space): origin at the frame centre, +x
  right, +y up. The frame spans y in [-1, 1] and x in [-W/H, W/H]
  (Lost Marble forum, "Show pixel coordinates" thread). A full-canvas image
  layer with the project's pixel size therefore covers exactly that area.

Bone conventions (evidence: SimplSam ss_make_bones.lua, see
docs/moho-lua-api-evidence.md)
-----------------------------------------------------------------------
* ``fAngle`` is in radians, counter-clockwise positive, relative to the parent.
* ``fPos`` of a child bone is expressed in the parent's local frame: origin at
  the parent's *base*, +x along the parent bone. A child sitting on the
  parent's tip therefore has ``fPos == (parent.fLength, 0)``.
* Root bones use bone-layer (world) coordinates directly.

Everything here is pure Python so the conventions are unit-testable in the
cloud; the generated Lua only applies the numbers computed here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .spec import RigSpec

Vec = tuple[float, float]
Matrix = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]


def wrap_angle(a: float) -> float:
    """Wrap an angle in radians to (-pi, pi]."""
    w = math.fmod(a + math.pi, 2.0 * math.pi)
    if w <= 0.0:
        w += 2.0 * math.pi
    return w - math.pi


def angle_diff(a: float, b: float) -> float:
    """Smallest signed difference a - b, in radians."""
    return wrap_angle(a - b)


def snap(v: float, eps: float = 1e-12) -> float:
    """Replace float noise like 3.7e-17 with an exact 0.0 for readable output."""
    return 0.0 if abs(v) < eps else v


def px_to_world(p: Vec, width: int, height: int) -> Vec:
    half_h = height / 2.0
    return ((p[0] - width / 2.0) / half_h, (half_h - p[1]) / half_h)


def world_to_px(p: Vec, width: int, height: int) -> Vec:
    half_h = height / 2.0
    return (p[0] * half_h + width / 2.0, half_h - p[1] * half_h)


def rotate(v: Vec, angle: float) -> Vec:
    c, s = math.cos(angle), math.sin(angle)
    return (c * v[0] - s * v[1], s * v[0] + c * v[1])


@dataclass(frozen=True)
class BoneRest:
    """Rest (setup) pose of one bone, in Moho units."""

    index: int  # == Moho bone ID (creation order)
    name: str
    parent: str | None
    parent_index: int  # -1 for roots (Moho's fParent convention)
    world_base: Vec
    world_tip: Vec
    world_angle: float
    length: float
    local_pos: Vec  # value for M_Bone.fPos
    local_angle: float  # value for M_Bone.fAngle


def compute_rest_pose(spec: RigSpec) -> list[BoneRest]:
    """Convert the spec's pixel bones into Moho rest-pose values.

    Bones are returned in spec order, which validation guarantees is
    parent-before-child; index i becomes Moho bone ID i.
    """
    w, h = spec.canvas_width, spec.canvas_height
    result: list[BoneRest] = []
    by_name: dict[str, BoneRest] = {}
    for i, b in enumerate(spec.bones):
        base = px_to_world(b.base_px, w, h)
        tip = px_to_world(b.tip_px, w, h)
        dx, dy = tip[0] - base[0], tip[1] - base[1]
        world_angle = math.atan2(dy, dx)
        length = math.hypot(dx, dy)
        if b.parent is None:
            local_pos = base
            local_angle = world_angle
            parent_index = -1
        else:
            p = by_name[b.parent]
            lp = rotate((base[0] - p.world_base[0], base[1] - p.world_base[1]), -p.world_angle)
            local_pos = (snap(lp[0]), snap(lp[1]))
            local_angle = snap(wrap_angle(world_angle - p.world_angle))
            parent_index = p.index
        rest = BoneRest(
            index=i,
            name=b.name,
            parent=b.parent,
            parent_index=parent_index,
            world_base=base,
            world_tip=tip,
            world_angle=world_angle,
            length=length,
            local_pos=local_pos,
            local_angle=local_angle,
        )
        result.append(rest)
        by_name[b.name] = rest
    return result


@dataclass(frozen=True)
class BonePose:
    name: str
    world_base: Vec
    world_angle: float
    length: float

    @property
    def world_tip(self) -> Vec:
        return (
            self.world_base[0] + self.length * math.cos(self.world_angle),
            self.world_base[1] + self.length * math.sin(self.world_angle),
        )


def forward_kinematics(rest: list[BoneRest], local_angles: dict[str, float]) -> dict[str, BonePose]:
    """World pose given per-bone local (parent-relative) angles.

    Bones missing from ``local_angles`` keep their rest local angle. Bone
    positions (fPos) and lengths are not animated in Phase 0.
    """
    poses: dict[str, BonePose] = {}
    for r in rest:
        la = local_angles.get(r.name, r.local_angle)
        if r.parent is None:
            base = r.local_pos
            angle = la
        else:
            p = poses[r.parent]
            off = rotate(r.local_pos, p.world_angle)
            base = (p.world_base[0] + off[0], p.world_base[1] + off[1])
            angle = p.world_angle + la
        poses[r.name] = BonePose(r.name, base, angle, r.length)
    return poses


def track_offset_deg(spec: RigSpec, bone: str, frame: float) -> float:
    """Rotation offset (degrees) of a bone at a frame, linear between keys.

    Exact at key frames. Between keys Moho applies its own default
    interpolation (often 'smooth'), so in-between values here are only an
    approximation used for previews.
    """
    track = spec.track_for(bone)
    if track is None or frame <= spec.rest_frame:
        return 0.0
    keys = track.keys
    if frame <= keys[0].frame:
        return keys[0].value
    if frame >= keys[-1].frame:
        return keys[-1].value
    for a, b in zip(keys, keys[1:]):
        if a.frame <= frame <= b.frame:
            t = (frame - a.frame) / (b.frame - a.frame)
            return a.value + t * (b.value - a.value)
    raise AssertionError("unreachable")


def local_angles_at(spec: RigSpec, rest: list[BoneRest], frame: float) -> dict[str, float]:
    return {r.name: r.local_angle + math.radians(track_offset_deg(spec, r.name, frame)) for r in rest}


def pose_at(spec: RigSpec, rest: list[BoneRest], frame: float) -> dict[str, BonePose]:
    return forward_kinematics(rest, local_angles_at(spec, rest, frame))


def expected_angle_keys(spec: RigSpec, rest: list[BoneRest]) -> dict[str, list[tuple[int, float]]]:
    """(frame, fAnimAngle value in radians) pairs the Moho script must create.

    Every bone gets its rest key at the rest frame (0); tracked bones also get
    one key per spec key with value = rest local angle + offset.
    """
    out: dict[str, list[tuple[int, float]]] = {}
    for r in rest:
        keys = [(spec.rest_frame, r.local_angle)]
        track = spec.track_for(r.name)
        if track is not None:
            keys.extend((k.frame, r.local_angle + math.radians(k.value)) for k in track.keys)
        out[r.name] = keys
    return out


# --- 3x3 affine helpers (row-major, column vectors) -------------------------

def mat_mul(a: Matrix, b: Matrix) -> Matrix:
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3)
    )  # type: ignore[return-value]


def mat_from_pose(base: Vec, angle: float) -> Matrix:
    c, s = math.cos(angle), math.sin(angle)
    return ((c, -s, base[0]), (s, c, base[1]), (0.0, 0.0, 1.0))


def mat_inverse_rigid(m: Matrix) -> Matrix:
    # Inverse of [R t; 0 1] is [R^T, -R^T t; 0 1]
    r00, r01, tx = m[0]
    r10, r11, ty = m[1]
    return (
        (r00, r10, -(r00 * tx + r10 * ty)),
        (r01, r11, -(r01 * tx + r11 * ty)),
        (0.0, 0.0, 1.0),
    )


def mat_apply(m: Matrix, p: Vec) -> Vec:
    return (m[0][0] * p[0] + m[0][1] * p[1] + m[0][2], m[1][0] * p[0] + m[1][1] * p[1] + m[1][2])


def world_to_px_matrix(width: int, height: int) -> Matrix:
    half_h = height / 2.0
    return ((half_h, 0.0, width / 2.0), (0.0, -half_h, half_h), (0.0, 0.0, 1.0))


def px_to_world_matrix(width: int, height: int) -> Matrix:
    half_h = height / 2.0
    return ((1.0 / half_h, 0.0, -width / 2.0 / half_h), (0.0, -1.0 / half_h, 1.0), (0.0, 0.0, 1.0))


def layer_delta_px(width: int, height: int, rest_pose: BonePose, pose: BonePose) -> Matrix:
    """Pixel-space transform moving a layer rigidly bound to a bone from its
    rest placement to the bone's current pose (what Moho layer binding does)."""
    world_delta = mat_mul(
        mat_from_pose(pose.world_base, pose.world_angle),
        mat_inverse_rigid(mat_from_pose(rest_pose.world_base, rest_pose.world_angle)),
    )
    return mat_mul(world_to_px_matrix(width, height), mat_mul(world_delta, px_to_world_matrix(width, height)))
