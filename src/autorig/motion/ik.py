"""Two-bone inverse kinematics, arm/leg controllers and foot contacts (Phase 3.2).

Conventions (shared with ``autorig.phase2.skeleton``):
* points are source pixels, x to the right, y down;
* a *direction angle* ``ang(v)`` is in degrees from straight down, positive toward image left (clockwise on
  screen);
* *flexion* is the bend at the middle joint: 0 = straight limb, 180 = fully folded;
* the *bend side* is the sign of the 2D cross product ``u x (mid - root)``, where ``u`` is the unit vector from the
  root to the end effector.
A pole target picks the side: the middle joint goes to the side of the root→target line where the pole lies.

The solver only rotates bones. It never changes a segment length. An unreachable target is clamped to the reach
interval implied by the flexion limits, and the result says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..phase2.posing import wrap
from ..phase2.skeleton import SIDE_SIGN, SIDES, Skeleton, ang

LAT = {"r": -1.0, "l": 1.0}  # image x direction of the character's side
CONTACT_STATES = ("PLANTED", "LIFTING", "SWINGING", "LANDING", "FREE")


def _rotate(v: np.ndarray, deg: float) -> np.ndarray:
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return np.array([c * v[0] - s * v[1], s * v[0] + c * v[1]])


def _cross(a, b) -> float:
    return float(a[0] * b[1] - a[1] * b[0])


@dataclass(frozen=True)
class IKResult:
    root: np.ndarray
    mid: np.ndarray
    end: np.ndarray
    target: np.ndarray
    status: str  # "ok" | "clamped_max_reach" | "clamped_min_reach" | "degenerate"
    error_px: float  # end-effector distance to the target
    flexion_deg: float
    bend_sign: int
    reach: tuple[float, float]

    @property
    def reached(self) -> bool:
        return self.status == "ok"

    def to_json(self) -> dict[str, Any]:
        r = lambda v: [round(float(v[0]), 3), round(float(v[1]), 3)]
        return {"mid": r(self.mid), "end": r(self.end), "target": r(self.target), "status": self.status,
                "error_px": round(self.error_px, 4), "flexion_deg": round(self.flexion_deg, 3),
                "bend_sign": self.bend_sign, "reach": [round(self.reach[0], 2), round(self.reach[1], 2)]}


class TwoBoneIKSolver:
    """Analytic 2D two-bone IK with a pole target, flexion limits and reach clamping."""

    def __init__(self, l1: float, l2: float, min_flex: float = 0.0, max_flex: float = 179.0):
        if l1 <= 0 or l2 <= 0:
            raise ValueError("segment lengths must be positive")
        if not 0.0 <= min_flex <= max_flex <= 180.0:
            raise ValueError("flexion limits must satisfy 0 <= min <= max <= 180")
        self.l1, self.l2, self.min_flex, self.max_flex = float(l1), float(l2), float(min_flex), float(max_flex)

    def chord(self, flex_deg: float) -> float:
        """Root-to-end distance at a given flexion (law of cosines; interior angle = 180 - flexion)."""
        return math.sqrt(self.l1 ** 2 + self.l2 ** 2 + 2 * self.l1 * self.l2 * math.cos(math.radians(flex_deg)))

    @property
    def max_reach(self) -> float:
        return self.chord(self.min_flex)

    @property
    def min_reach(self) -> float:
        return self.chord(self.max_flex)

    def flexion_for(self, dist: float) -> float:
        c = (dist * dist - self.l1 ** 2 - self.l2 ** 2) / (2 * self.l1 * self.l2)
        return math.degrees(math.acos(float(np.clip(c, -1.0, 1.0))))

    def solve(self, root, target, pole=None, bend_sign: int = 1) -> IKResult:
        root, target = np.asarray(root, float), np.asarray(target, float)
        d = target - root
        dist = float(np.hypot(*d))
        status = "ok"
        if dist < 1e-9:
            u = np.array([0.0, 1.0])
            status = "degenerate"
        else:
            u = d / dist
        lo, hi = self.min_reach, self.max_reach
        dc = float(np.clip(dist, lo, hi))
        if status == "ok":
            if dist > hi + 1e-9:
                status = "clamped_max_reach"
            elif dist < lo - 1e-9:
                status = "clamped_min_reach"
        side = int(bend_sign) if bend_sign else 1
        if pole is not None:
            c = _cross(u, np.asarray(pole, float) - root)
            if abs(c) > 1e-9:
                side = 1 if c > 0 else -1
        # angle at the root between the root->end line and the upper segment
        cos_a = (self.l1 ** 2 + dc * dc - self.l2 ** 2) / (2 * self.l1 * dc)
        a = math.degrees(math.acos(float(np.clip(cos_a, -1.0, 1.0))))
        mid = root + self.l1 * _rotate(u, side * a)
        end = root + u * dc
        return IKResult(root, mid, end, target, status, float(np.hypot(*(end - target))),
                        self.flexion_for(dc), side, (lo, hi))


def solve_axis_aware(l1: float, l2: float, lo: float, hi: float, axis_offset: float, root, target, pole=None,
                     bend_sign: int = 1) -> IKResult:
    """Two-bone IK whose flexion limits [lo, hi] apply to the *anatomical* elbow, for an upper arm whose pivot
    line is tilted off its anatomical axis by ``axis_offset`` (deg; Phase 3.3 garment-aligned shoulder pivots).
    The anatomical bend is the line bend minus the offset, signed toward the bend side. So the line limits are
    shifted per side: the limb never shows a reverse bend at full reach, and it bends exactly as far as the
    anatomical limit allows."""
    if abs(axis_offset) < 1e-9:
        return TwoBoneIKSolver(l1, l2, lo, hi).solve(root, target, pole, bend_sign)
    root, target = np.asarray(root, float), np.asarray(target, float)
    d = target - root
    dist = float(np.hypot(*d))
    u = d / dist if dist > 1e-9 else np.array([0.0, 1.0])
    side = int(bend_sign) if bend_sign else 1
    if pole is not None:
        c = _cross(u, np.asarray(pole, float) - root)
        if abs(c) > 1e-9:
            side = 1 if c > 0 else -1
    probe = TwoBoneIKSolver(l1, l2, 30.0, 150.0).solve(np.zeros(2), np.array([0.0, 0.7 * (l1 + l2)]), None, side)
    rho = ((ang(probe.end - probe.mid) - ang(probe.mid - probe.root) + 180) % 360) - 180
    sigma = 1.0 if rho >= 0 else -1.0
    lo_l = float(np.clip(lo + sigma * axis_offset, 0.0, 179.0))
    hi_l = float(np.clip(hi + sigma * axis_offset, lo_l, 179.0))
    return TwoBoneIKSolver(l1, l2, lo_l, hi_l).solve(root, target, pole, side)


# -- controllers on the existing skeleton -----------------------------------------------------------------
class LimbIK:
    """IK for one limb chain of the existing skeleton (no duplicate bones). ``kind`` is "arm" or "leg"."""

    def __init__(self, sk: Skeleton, kinematics, side: str, kind: str, limits: dict[str, Any] | None = None):
        self.sk, self.K, self.side, self.kind = sk, kinematics, side, kind
        names = ("upper_arm", "forearm", "hand") if kind == "arm" else ("thigh", "shin", "foot")
        self.bones = [f"{n}_{side}" for n in names]
        self.i0, self.i1, self.i2 = (sk.idx[b] for b in self.bones)
        self.parent = sk.bones[self.i0].parent  # clavicle or pelvis
        lim = limits or {}
        self.solver = TwoBoneIKSolver(sk.bones[self.i0].length, sk.bones[self.i1].length,
                                      float(lim.get("min_flex", 0.0)), float(lim.get("max_flex", 150.0 if kind == "arm" else 120.0)))
        self.axis_offset = float(sk.bones[self.i0].axis_offset)  # anatomical limits via solve_axis_aware

    def root(self, pose) -> np.ndarray:
        return self.K.point(self.sk.idx[self.parent], self.sk.J[self.i0], pose)

    def default_pole(self, pose, target=None) -> np.ndarray:
        """Anatomical default: elbow down and out, knee out (front view)."""
        r = self.root(pose)
        L = self.solver.l1 + self.solver.l2
        lat = LAT[self.side]
        return r + (np.array([0.35 * lat, 1.0]) if self.kind == "arm" else np.array([1.0 * lat, 0.4])) * L

    def apply(self, pose, target, pole=None) -> IKResult:
        """Rotate the chain so its end effector reaches ``target`` (world). Lengths are untouched."""
        th, sk = pose.theta, self.sk
        r = self.root(pose)
        pole = self.default_pole(pose) if pole is None else np.asarray(pole, float)
        res = solve_axis_aware(self.solver.l1, self.solver.l2, self.solver.min_flex, self.solver.max_flex,
                               self.axis_offset, r, target, pole)
        parent_rot = self.K.rot(th, self.parent)
        th[self.i0] = wrap(ang(res.mid - r) - sk.bones[self.i0].rest_angle - parent_rot)
        th[self.i1] = wrap(ang(res.end - res.mid) - sk.bones[self.i1].rest_angle - parent_rot - th[self.i0])
        return res

    def orient_end(self, pose, world_angle_offset: float) -> None:
        """End bone (hand/foot) world rotation relative to its rest orientation."""
        th = pose.theta
        chain = self.K.rot(th, self.bones[1])
        th[self.i2] = wrap(world_angle_offset - chain)

    def end_world_rotation(self, pose) -> float:
        return self.K.rot(pose.theta, self.bones[2])

    def effector(self, pose) -> np.ndarray:
        return self.K.point(self.i1, self.sk.bones[self.i1].tail, pose)

    def middle(self, pose) -> np.ndarray:
        return self.K.point(self.i0, self.sk.bones[self.i0].tail, pose)

    def fk_to_ik(self, pose) -> dict[str, Any]:
        """IK target and pole that reproduce the current FK pose exactly (no jump when switching to IK)."""
        return {"target": [float(v) for v in self.effector(pose)], "pole": [float(v) for v in self.middle(pose)],
                "end_rotation": float(self.end_world_rotation(pose))}

    def ik_to_fk(self, pose_ik, pose_fk_base) -> dict[str, float]:
        """Per-bone rotation deltas between an IK pose and an FK pose (diagnostic; arms use ``fk_params``)."""
        return {b: float(wrap(pose_ik.theta[i] - pose_fk_base.theta[i])) for b, i in
                zip(self.bones, (self.i0, self.i1, self.i2))}

    def fk_params(self, pose) -> dict[str, float]:
        """FK arm parameters (the solver's FK convention) that reproduce the limb of ``pose``: upper-arm abduction
        (world direction, + lateral) and signed elbow flexion (+ = forearm toward the midline when hanging).
        Solving the same intent with these FK values gives the same rotations (no jump when switching to FK)."""
        if self.kind != "arm":
            raise ValueError("FK parameters are defined for arms; legs follow the planner in FK mode")
        th, sk = pose.theta, self.sk
        sg = SIDE_SIGN[self.side]
        b0, b1 = sk.bones[self.i0], sk.bones[self.i1]
        abd = sg * wrap(th[self.i0] + b0.anat_angle + self.K.rot(th, self.parent))
        rest_rel = wrap(b1.rest_angle - b0.anat_angle)
        flex = -sg * wrap(th[self.i1] + rest_rel)
        return {"abduction": float(abd), "flexion": float(flex)}


class ArmIKController(LimbIK):
    def __init__(self, sk, kinematics, side, limits=None):
        super().__init__(sk, kinematics, side, "arm", limits)


class LegIKController(LimbIK):
    def __init__(self, sk, kinematics, side, limits=None):
        super().__init__(sk, kinematics, side, "leg", limits)


# -- foot contacts ---------------------------------------------------------------------------------------
class FootContactController:
    """Contact state per foot from the intent: PLANTED (weight-bearing, world-locked), LIFTING (unloading or
    early swing), SWINGING, LANDING (late swing or loading), FREE (not in contact outside a gait)."""

    LOADED = 0.8
    EDGE = 0.15  # share of the swing counted as lifting / landing

    def state(self, it, side: str) -> str:
        leg = it.legs.get(side)
        if leg is None:
            return "FREE"
        u = it.channels.get(f"swing_u_{side}")
        if leg.planted:
            if it.balance != "dynamic" or leg.load >= self.LOADED:
                return "PLANTED"
            psi = it.channels.get(f"stance_psi_{side}", 0.0)
            duty = it.channels.get("duty", 1.0)
            return "LANDING" if psi < duty / 2 else "LIFTING"
        if u is None:
            return "FREE"
        return "LIFTING" if u < self.EDGE else "LANDING" if u > 1 - self.EDGE else "SWINGING"

    def report(self, it, info) -> dict[str, Any]:
        """Per foot: state, ankle, world lock and its error, split into horizontal slide and vertical lift (a gait's
        smooth support lets a loaded foot rise by at most SOFT_SUPPORT_PX; it never slides)."""
        out = {}
        for s in SIDES:
            st = self.state(it, s)
            leg = it.legs.get(s)
            a = info["joints_posed"][f"foot_{s}"]
            locked = bool(leg and st == "PLANTED")
            out[s] = {"state": st, "ankle": a, "lock": list(leg.ankle) if locked else None,
                      "lock_error_px": round(float(np.hypot(a[0] - leg.ankle[0], a[1] - leg.ankle[1])), 3) if locked else None,
                      "slide_px": round(abs(float(a[0] - leg.ankle[0])), 3) if locked else None,
                      "lift_px": round(float(leg.ankle[1] - a[1]), 3) if locked else None}
        return out
