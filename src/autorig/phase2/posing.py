"""Anatomically informed pose solver and natural pose presets (Phase 2 refinement).

Presets are written in anatomical terms, independent of the rest pose, so the same preset works on
a T-pose reference and on an arms-down one:
* arm abduction from hanging straight down;
* elbow flexion, positive = forearm toward the midline when the arm hangs;
* clavicle elevation (shrug), pelvis tilt, spine bends and head tilt;
* stance width as a multiple of hip width;
* weight distribution between the feet.

The solver turns them into local bone rotations (`PoseParams`):
* FK for torso, head and arms; analytic two-bone IK for planted feet and reach targets;
* pelvis height from leg reach (feet stay on the ground, knees keep a soft bend);
* horizontal pelvis shift until the centre of mass sits over the support at the requested weight
  split;
* torso counter-rotation that levels the shoulders against pelvis tilt;
* joint limits, clamped or reported as violations, never silently exceeded.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .skeleton import LIMITS, SIDE_SIGN, SIDES, Skeleton, ang
from .skinning import Deformer, PoseParams


def wrap(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


@dataclass
class Preset:
    name: str
    title: str
    intent: str
    pelvis_tilt: float = 0.0  # image deg; + = clockwise (raises the character's right hip)
    pelvis_drop: float = 0.0  # extra pelvis lowering, fraction of leg length
    weight_left: float = 0.5  # share of body weight on the character's left foot
    stance: float = 1.6  # ankle separation / hip-joint separation
    knee_min_bend: float = 4.0  # softest knee bend of a planted leg (deg)
    spine: float = 0.0
    chest: float | None = None  # None = counter-rotate so the shoulder line stays near level
    neck: float | None = None
    head: float = 0.0  # head world tilt, image deg
    clavicle: dict[str, float] = field(default_factory=dict)  # side -> elevation (deg)
    arms: dict[str, dict[str, Any]] = field(default_factory=dict)
    feet: dict[str, dict[str, Any]] = field(default_factory=dict)  # side -> lift (frac leg), dx (frac hip sep)
    scale: dict[str, float] = field(default_factory=dict)  # bone -> axial scale (foreshortening, flagged)
    front: list[str] = field(default_factory=list)  # layers composited last
    hands: dict[str, str] = field(default_factory=dict)  # side -> hand switch drawing (corrective artwork)
    resolved_by: dict[str, str] = field(default_factory=dict)  # needs_artwork item -> hand drawing providing it
    arms_behind_head: bool = True
    needs_artwork: list[str] = field(default_factory=list)
    notes: str = ""

    def to_json(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


RELAXED = {"abduction": 11.0, "flexion": 14.0, "wrist": 6.0}

PRESETS: list[Preset] = [
    Preset("relaxed_standing", "Relaxed standing", "even weight, arms hanging with soft elbows and wrists",
           stance=1.7, knee_min_bend=5, arms={"r": RELAXED, "l": RELAXED}),
    Preset("hands_at_sides", "Standing, hands at sides", "neutral attention stance, arms close to the body",
           stance=1.25, knee_min_bend=3, arms={s: {"abduction": 5.0, "flexion": 5.0, "wrist": 3.0} for s in SIDES}),
    Preset("pointing_speaking", "Pointing while speaking", "right arm reaches out to point, weight on the left leg",
           weight_left=0.68, pelvis_tilt=-3.0, head=-5.0, clavicle={"r": 6.0},
           arms={"r": {"reach": (0.9, 0.38), "elbow": "down", "wrist": -6.0}, "l": RELAXED}, hands={"r": "point"},
           needs_artwork=["hand_r: index-finger pointing hand (only an open hand exists)"],
           resolved_by={"hand_r: index-finger pointing hand (only an open hand exists)": "point"}),
    Preset("one_hand_shrug", "One-handed shrug", "right shoulder raised, forearm out, open palm",
           head=7.0, clavicle={"r": 16.0, "l": 3.0},
           arms={"r": {"abduction": 26.0, "flexion": -100.0, "wrist": -12.0}, "l": RELAXED}, hands={"r": "palm_up"},
           needs_artwork=["hand_r: palm-up hand (the open hand faces the viewer)"],
           resolved_by={"hand_r: palm-up hand (the open hand faces the viewer)": "palm_up"}),
    Preset("both_hands_raised", "Both hands raised", "arms up, shoulders lifted, open palms",
           stance=1.7, clavicle={"r": 22.0, "l": 22.0},
           arms={s: {"abduction": 156.0, "flexion": -14.0, "wrist": 0.0} for s in SIDES}),
    Preset("arms_crossed", "Arms crossed", "forearms folded across the chest",
           stance=1.5, arms={s: {"abduction": 4.0, "flexion": 122.0, "wrist": 10.0} for s in SIDES},
           front=["forearm_l", "hand_l"],
           needs_artwork=["crossed forearms with tucked hands: no suitable artwork, the open hands stay visible"]),
    Preset("casual_walk", "Casual walking stance", "weight on the left leg, right leg passing forward",
           weight_left=0.72, pelvis_tilt=-4.0, stance=1.15,
           feet={"r": {"planted": False, "fk": {"thigh_abd": 3.0, "knee_flex": 3.0, "thigh_scale": 0.93,
                                                "shin_scale": 0.97}}},
           arms={"r": {"abduction": 8.0, "flexion": 24.0, "wrist": 6.0},
                 "l": {"abduction": 9.0, "flexion": 9.0, "wrist": 4.0}},
           notes="front view: the stride happens in depth; shown as weight shift, hip drop and a lifted foot"),
    Preset("running", "Running stance", "right knee driving forward, elbows bent, weight on the left leg",
           weight_left=1.0, pelvis_tilt=-5.0, pelvis_drop=0.05, stance=0.9, knee_min_bend=14,
           feet={"r": {"planted": False, "fk": {"thigh_abd": 5.0, "knee_flex": 6.0, "thigh_scale": 0.6}}},
           arms={"r": {"abduction": 30.0, "flexion": 55.0, "wrist": 8.0},
                 "l": {"abduction": 28.0, "flexion": 50.0, "wrist": 8.0}},
           hands={"r": "fist", "l": "fist"},
           needs_artwork=["fists (only open hands exist)",
                          "foreshortened forward thigh (approximated by axial scaling 0.6)"],
           resolved_by={"fists (only open hands exist)": "fist"}),
    Preset("sitting", "Sitting", "seated on a low seat, knees apart, hands resting near the knees",
           pelvis_drop=0.40, stance=2.3, knee_min_bend=10,
           arms={s: {"abduction": 22.0, "flexion": 48.0, "wrist": 8.0} for s in SIDES},
           needs_artwork=["seat/chair", "foreshortened thighs for a front-on chair sit (shown as a wide-knee seat)"]),
    Preset("natural_squat", "Natural squat", "pelvis lowered, knees tracking out over the feet, arms forward for balance",
           pelvis_drop=0.24, stance=2.1, knee_min_bend=8,
           arms={s: {"abduction": 28.0, "flexion": 58.0, "wrist": 8.0} for s in SIDES}),
    Preset("head_tilt_compensated", "Head tilt with torso compensation", "head tilts to the left, shoulders answer",
           head=14.0, chest=-3.0, clavicle={"l": -3.0, "r": 4.0}, arms={"r": RELAXED, "l": RELAXED}),
    Preset("conversational_gesture", "Conversational gesture", "right forearm up and open, weight on the right leg",
           weight_left=0.32, pelvis_tilt=3.0, head=-6.0, clavicle={"r": 7.0},
           arms={"r": {"abduction": 30.0, "flexion": -78.0, "wrist": -14.0}, "l": RELAXED}),
]
PRESET_BY_NAME = {p.name: p for p in PRESETS}


def two_bone_ik(root, target, l1: float, l2: float, prefer) -> tuple[np.ndarray, np.ndarray, bool]:
    """Analytic two-bone IK. ``prefer(candidate_joint) -> score`` chooses the bend side.
    Returns (middle joint, end point, reachable)."""
    root, target = np.asarray(root, float), np.asarray(target, float)
    d = target - root
    dist = float(np.hypot(*d)) or 1e-6
    reach = l1 + l2
    lo = abs(l1 - l2) + 1e-3
    dc = float(np.clip(dist, lo, reach * 0.9999))
    a = math.acos(np.clip((l1 * l1 + dc * dc - l2 * l2) / (2 * l1 * dc), -1.0, 1.0))
    base = math.atan2(d[1], d[0])
    cands = [root + l1 * np.array([math.cos(base + s * a), math.sin(base + s * a)]) for s in (1, -1)]
    mid = max(cands, key=prefer)
    end = root + d / dist * dc
    return mid, end, dist <= reach * 0.9999 and dist >= lo


class PoseSolver:
    def __init__(self, sk: Skeleton, dfm: Deformer):
        self.sk, self.dfm = sk, dfm
        self.masses = dfm.masses()
        I = sk.idx
        self.hip_sep = float(abs(sk.bone("thigh_l").head[0] - sk.bone("thigh_r").head[0])) or 100.0
        self.leg_len = {s: sk.bone(f"thigh_{s}").length + sk.bone(f"shin_{s}").length for s in SIDES}
        self.I = I
        # per-bone constraint overrides from the editor: {(kind, side): {key: (lo, hi)}}
        self.limit_overrides: dict[tuple[str, str], dict[str, tuple[float, float]]] = {}

    def rot(self, th: np.ndarray, name: str) -> float:
        """World rotation (deg, relative to rest) of a bone."""
        j, total = self.I[name], 0.0
        while j >= 0:
            total += th[j]
            j = self.sk.parent[j]
        return total

    def solve(self, pr: Preset) -> tuple[PoseParams, dict[str, Any]]:
        sk, I = self.sk, self.I
        N = len(sk.bones)
        th, sc = np.zeros(N), np.ones(N)
        violations: list[str] = []
        for b, k in pr.scale.items():
            sc[I[b]] = k
        th[0] = self._clamp("pelvis", "tilt", pr.pelvis_tilt, violations)
        th[I["spine"]] = self._clamp("spine", "bend", pr.spine, violations)
        chest = -0.8 * (th[0] + th[I["spine"]]) if pr.chest is None else pr.chest
        th[I["chest"]] = self._clamp("chest", "bend", chest, violations)
        head_rel = pr.head - self.rot(th, "chest")
        neck = 0.4 * head_rel if pr.neck is None else pr.neck
        th[I["neck"]] = self._clamp("neck", "bend", neck, violations)
        th[I["head"]] = self._clamp("head", "bend", head_rel - th[I["neck"]], violations)
        for s in SIDES:
            th[I[f"clavicle_{s}"]] = SIDE_SIGN[s] * self._clamp("clavicle", "elevation", pr.clavicle.get(s, 0.0),
                                                                 violations, s)
        ik_notes: list[str] = []
        sx = 0.0
        feet_w = self._feet_targets(pr)
        info: dict[str, Any] = {}
        for _ in range(12):
            sy = self._pelvis_height(pr, th, sc, sx, feet_w)
            shift = np.array([sx, sy])
            pose = PoseParams(th, sc, shift)
            self._legs(pr, pose, feet_w, ik_notes)
            self._arms(pr, pose, violations, ik_notes)
            com = self.com(pose)
            target_x, support = self._support(pose, pr, feet_w)
            err = target_x - com[0]
            info = {"com": com, "target_x": target_x, "support": support, "balance_error_px": err}
            if abs(err) < 0.5:
                break
            sx = float(np.clip(sx + 0.9 * err, -1.3 * self.hip_sep, 1.3 * self.hip_sep))
        pose = PoseParams(th.copy(), sc.copy(), np.array([sx, self._pelvis_height(pr, th, sc, sx, feet_w)]))
        violations += self.check_limits(pose)
        info.update(self.joints_world(pose))
        info.update({"violations": sorted(set(violations)), "ik_notes": sorted(set(ik_notes)), "feet_targets":
                     {s: [round(float(v), 1) for v in feet_w[s]] for s in SIDES},
                     "pelvis_shift_px": [round(sx, 1), round(float(pose.shift[1]), 1)]})
        return pose, info

    # -- pieces -------------------------------------------------------------------------------------
    def _clamp(self, kind: str, key: str, v: float, violations: list[str], side: str = "") -> float:
        lo, hi = self.limit_overrides.get((kind, side), {}).get(key) or LIMITS[kind][key]
        if v < lo or v > hi:
            violations.append(f"{kind}{'_' + side if side else ''} {key} {v:.0f} clamped to [{lo}, {hi}]")
        return float(np.clip(v, lo, hi))

    def _feet_targets(self, pr: Preset) -> dict[str, np.ndarray]:
        out = {}
        for s in SIDES:
            f = pr.feet.get(s, {})
            side_x = -1.0 if s == "r" else 1.0
            A = self.sk.bone(f"foot_{s}").head
            x = self.sk.midline_x + side_x * (pr.stance * self.hip_sep / 2 + f.get("dx", 0.0) * self.hip_sep)
            y = A[1] - f.get("lift", 0.0) * self.leg_len[s]
            out[s] = np.array([x, y])
        return out

    def _planted(self, pr: Preset) -> list[str]:
        return [s for s in SIDES if pr.feet.get(s, {}).get("planted", True)]

    def _pelvis_height(self, pr, th, sc, sx, feet_w) -> float:
        R0 = self.dfm.root_matrix(PoseParams(th, sc, np.array([sx, 0.0])))
        need = []
        for s in self._planted(pr) or list(SIDES):
            hip = R0[:2, :2] @ self.sk.bone(f"thigh_{s}").head + R0[:2, 2]
            l1 = self.sk.bone(f"thigh_{s}").length * sc[self.I[f"thigh_{s}"]]
            l2 = self.sk.bone(f"shin_{s}").length * sc[self.I[f"shin_{s}"]]
            b = math.radians(pr.knee_min_bend)
            chord = math.sqrt(l1 * l1 + l2 * l2 + 2 * l1 * l2 * math.cos(b))
            dx = hip[0] - feet_w[s][0]
            dy = math.sqrt(max(chord * chord - dx * dx, 1.0))
            need.append(feet_w[s][1] - dy - hip[1])
        L = sum(self.leg_len.values()) / 2
        return max(need) + pr.pelvis_drop * L

    def _legs(self, pr: Preset, pose: PoseParams, feet_w, notes: list[str]) -> None:
        th, sc, I = pose.theta, pose.scale, self.I
        R0 = self.dfm.root_matrix(pose)
        for s in SIDES:
            t, sh, ft = I[f"thigh_{s}"], I[f"shin_{s}"], I[f"foot_{s}"]
            fk = pr.feet.get(s, {}).get("fk")
            if fk:  # free leg posed directly (front view: a forward knee is a foreshortened thigh)
                sg = SIDE_SIGN[s]
                sc[t], sc[sh] = fk.get("thigh_scale", 1.0), fk.get("shin_scale", 1.0)
                th[t] = wrap(sg * fk.get("thigh_abd", 0.0) - self.sk.bones[t].rest_angle - th[0])
                rest_rel = wrap(self.sk.bones[sh].rest_angle - self.sk.bones[t].rest_angle)
                th[sh] = wrap(-sg * fk.get("knee_flex", 0.0) - rest_rel)
                th[ft] = wrap(pr.feet[s].get("rot", 0.0) - (th[0] + th[t] + th[sh]))
                continue
            hip = R0[:2, :2] @ self.sk.J[t] + R0[:2, 2]
            l1, l2 = self.sk.bones[t].length * sc[t], self.sk.bones[sh].length * sc[sh]
            side_x = -1.0 if s == "r" else 1.0
            mid_line = (hip + feet_w[s]) / 2
            knee, ankle, ok = two_bone_ik(hip, feet_w[s], l1, l2, lambda k: (k[0] - mid_line[0]) * side_x)
            if not ok:
                notes.append(f"leg_{s}: ankle target out of reach; leg straightened toward it")
            th[t] = wrap(ang(knee - hip) - self.sk.bones[t].rest_angle - th[0])
            th[sh] = wrap(ang(ankle - knee) - self.sk.bones[sh].rest_angle - th[0] - th[t])
            frot = pr.feet.get(s, {}).get("rot", 0.0)
            th[ft] = wrap(frot - (th[0] + th[t] + th[sh]))

    def _arms(self, pr: Preset, pose: PoseParams, violations: list[str], notes: list[str]) -> None:
        th, I, sk = pose.theta, self.I, self.sk
        for s in SIDES:
            spec = pr.arms.get(s, RELAXED)
            up, fo, ha, cl = I[f"upper_arm_{s}"], I[f"forearm_{s}"], I[f"hand_{s}"], f"clavicle_{s}"
            sg = SIDE_SIGN[s]
            if "reach" in spec:
                W = self.dfm.world(I[cl], pose)
                sh = W[:2, :2] @ sk.J[up] + W[:2, 2]
                L1, L2 = sk.bones[up].length, sk.bones[fo].length
                lat, upv = spec["reach"]
                side_x = -1.0 if s == "r" else 1.0
                target = sh + np.array([side_x * lat * (L1 + L2), -upv * (L1 + L2)])
                pref = (lambda e: e[1]) if spec.get("elbow", "down") == "down" else (lambda e: (e[0] - sh[0]) * side_x)
                elbow, wrist, ok = two_bone_ik(sh, target, L1, L2, pref)
                if not ok:
                    notes.append(f"arm_{s}: reach target out of range; arm extended toward it")
                th[up] = wrap(ang(elbow - sh) - sk.bones[up].rest_angle - self.rot(th, cl))
                th[fo] = wrap(ang(wrist - elbow) - sk.bones[fo].rest_angle - self.rot(th, f"upper_arm_{s}"))
            else:
                abd = self._clamp("upper_arm", "abduction", spec.get("abduction", 10.0), violations, s)
                th[up] = wrap(sg * abd - sk.bones[up].rest_angle - self.rot(th, cl))
                flex = self._clamp("forearm", "flexion", spec.get("flexion", 10.0), violations, s)
                rest_rel = wrap(sk.bones[fo].rest_angle - sk.bones[up].rest_angle)
                th[fo] = wrap(-sg * flex - rest_rel)
            wr = self._clamp("hand", "bend", spec.get("wrist", 0.0), violations, s)
            rest_rel_h = wrap(sk.bones[ha].rest_angle - sk.bones[fo].rest_angle)
            th[ha] = wrap(-sg * wr - rest_rel_h)

    # -- balance ----------------------------------------------------------------------------------
    def com(self, pose: PoseParams) -> np.ndarray:
        tot, acc = 0.0, np.zeros(2)
        for b, (m, c) in self.masses.items():
            W = self.dfm.world(b, pose)
            acc += m * (W[:2, :2] @ c + W[:2, 2])
            tot += m
        return acc / max(tot, 1.0)

    def _support(self, pose: PoseParams, pr: Preset, feet_w) -> tuple[float, dict[str, Any]]:
        centres = {}
        for s in SIDES:
            fb = self.I[f"foot_{s}"]
            W = self.dfm.world(fb, pose)
            mid = (self.sk.J[fb] + self.sk.bones[fb].tail) / 2
            centres[s] = W[:2, :2] @ mid + W[:2, 2]
        planted = self._planted(pr)
        wl = pr.weight_left if len(planted) == 2 else (1.0 if planted == ["l"] else 0.0)
        target = (1 - wl) * centres["r"][0] + wl * centres["l"][0]
        xs = [centres[s][0] for s in planted] or [target]
        half_foot = {s: abs(self.sk.bones[self.I[f"foot_{s}"]].tail[0] - self.sk.J[self.I[f"foot_{s}"]][0]) + 20
                     for s in SIDES}
        lo = min(centres[s][0] - half_foot[s] for s in planted) if planted else target
        hi = max(centres[s][0] + half_foot[s] for s in planted) if planted else target
        return float(target), {"planted": planted, "x_range": [round(float(lo), 1), round(float(hi), 1)],
                               "weight_left": wl, "foot_centres": {s: [round(float(v), 1) for v in centres[s]]
                                                                   for s in SIDES}}

    # -- reporting --------------------------------------------------------------------------------
    def joints_world(self, pose: PoseParams) -> dict[str, Any]:
        heads, tails = {}, {}
        for i, b in enumerate(self.sk.bones):
            W = self.dfm.world(i, pose)
            heads[b.name] = W[:2, :2] @ b.head + W[:2, 2]
            tails[b.name] = W[:2, :2] @ b.tail + W[:2, 2]
        return {"joints_posed": {k: [round(float(v[0]), 1), round(float(v[1]), 1)] for k, v in heads.items()},
                "bone_tails_posed": {k: [round(float(v[0]), 1), round(float(v[1]), 1)] for k, v in tails.items()}}

    def anatomical(self, pose: PoseParams) -> dict[str, float]:
        th, sk, I = pose.theta, self.sk, self.I
        w = lambda n: wrap(sk.bone(n).rest_angle + self.rot(th, n))
        out = {"pelvis_tilt": th[0], "spine_bend": th[I["spine"]], "chest_bend": th[I["chest"]],
               "neck_bend": th[I["neck"]], "head_bend": th[I["head"]]}
        for s in SIDES:
            sg = SIDE_SIGN[s]
            out[f"clavicle_{s}_elevation"] = sg * th[I[f"clavicle_{s}"]]
            out[f"upper_arm_{s}_abduction"] = sg * w(f"upper_arm_{s}")
            out[f"forearm_{s}_flexion"] = -sg * wrap(w(f"forearm_{s}") - w(f"upper_arm_{s}"))
            out[f"hand_{s}_bend"] = -sg * wrap(w(f"hand_{s}") - w(f"forearm_{s}"))
            out[f"thigh_{s}_abduction"] = sg * w(f"thigh_{s}")
            out[f"shin_{s}_flexion"] = -sg * wrap(w(f"shin_{s}") - w(f"thigh_{s}"))  # + = knee bends laterally
            out[f"foot_{s}_bend"] = th[I[f"foot_{s}"]]
        return {k: round(float(v), 2) for k, v in out.items()}

    def check_limits(self, pose: PoseParams) -> list[str]:
        a, bad = self.anatomical(pose), []
        rng = {"pelvis_tilt": LIMITS["pelvis"]["tilt"], "spine_bend": LIMITS["spine"]["bend"],
               "chest_bend": LIMITS["chest"]["bend"], "neck_bend": LIMITS["neck"]["bend"],
               "head_bend": LIMITS["head"]["bend"]}
        for s in SIDES:
            rng[f"clavicle_{s}_elevation"] = LIMITS["clavicle"]["elevation"]
            rng[f"upper_arm_{s}_abduction"] = LIMITS["upper_arm"]["abduction"]
            rng[f"forearm_{s}_flexion"] = LIMITS["forearm"]["flexion"]
            rng[f"hand_{s}_bend"] = LIMITS["hand"]["bend"]
            rng[f"thigh_{s}_abduction"] = LIMITS["thigh"]["abduction"]
            rng[f"foot_{s}_bend"] = LIMITS["foot"]["bend"]
            knee = a[f"shin_{s}_flexion"]
            lo_k, hi_k = LIMITS["shin"]["flexion"]
            if knee < -LIMITS["shin"]["valgus_max"]:
                bad.append(f"knee_{s} bends medially by {-knee:.0f} deg (knock-knee / impossible in front view)")
            if abs(knee) > hi_k:
                bad.append(f"knee_{s} flexion {abs(knee):.0f} deg exceeds {hi_k}")
        for (kind, side), lim in self.limit_overrides.items():
            for key, r in lim.items():
                k = f"{kind}_{side}_{key}" if side else f"{kind}_{key}"
                if k in a and k != f"shin_{side}_flexion":
                    rng[k] = tuple(r)
        for k, (lo, hi) in rng.items():
            if not lo - 0.5 <= a[k] <= hi + 0.5:
                bad.append(f"{k} {a[k]:.0f} outside [{lo}, {hi}]")
        return bad
