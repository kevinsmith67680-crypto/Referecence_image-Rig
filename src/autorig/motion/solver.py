"""SkeletalPoseSolver: PoseIntent -> anatomically constrained bone rotations.

* Fixed segment lengths. The solver only rotates bones and translates the root; it never scales a bone, so
  limb lengths and proportions are preserved exactly.
* Legs: analytic two-bone IK to the intended ankle with a lateral knee (front view). The knee flexion is
  bounded per leg (`LegIntent.knee_max`). A target closer than the limit allows stops the foot short, and an
  unreachable target straightens the leg; both are reported, never stretched.
* Pelvis: height from foot contact. The standing height is the highest pelvis at which every planted leg
  still reaches its ankle with the soft-knee minimum. `pelvis_dy` lowers it, bounded by the knee limit; a
  raise above standing height is allowed only when no foot is planted (flight). Lateral position comes from
  static balance (CoM over the weighted support) or from the planned path (gaits).
* Torso: pelvis roll; spine bend; chest counter-roll so the shoulders stay near level; clavicles follow arm
  abduction above the horizontal (scapulohumeral rhythm) and drop slightly when the arms come down from the
  T-pose; the head keeps its intended world angle (stabilisation), with the neck taking a share.
* Arms: FK shape (abduction, elbow flexion, wrist) or IK to a wrist target, with an elbow-side preference.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy.optimize import brentq

from ..phase2.posing import wrap
from ..phase2.skeleton import SIDE_SIGN, SIDES, Skeleton, ang
from ..phase2.skinning import PoseParams
from .ik import TwoBoneIKSolver, solve_axis_aware
from .intent import ArmIntent, PoseIntent
from .profile import CharacterProfile

LAT = {"r": -1.0, "l": 1.0}
MIN_FLEX = 1.6  # deg: limbs never solve exactly straight (the knee/elbow position is unstable at 0 deg)
BALANCE_TOL_PX = 0.02  # static balance: CoM-over-support tolerance (tight, so FK/IK switches do not move the body)
UNLOAD_PX = 5.0  # how far an unloading (or just landing) planted foot may rise in a gait
SOFT_SUPPORT_PX = 1.5  # softness of the smooth max over planted feet (fluid gaits)
LOADED = 0.8  # a planted foot counts as weight-bearing contact for the metrics at this load or more


def _rot(theta_deg: float, pivot) -> np.ndarray:
    t = math.radians(theta_deg)
    c, s = math.cos(t), math.sin(t)
    x, y = pivot
    return np.array([[c, -s, x - c * x + s * y], [s, c, y - s * x - c * y], [0, 0, 1.0]])


class Kinematics:
    """Rigid forward kinematics of the skeleton (rotation about rest pivots, root translation)."""

    def __init__(self, sk: Skeleton):
        self.sk = sk
        self.chains = []
        for b in range(len(sk.bones)):
            chain, j = [], b
            while j > 0:
                chain.append(j)
                j = sk.parent[j]
            self.chains.append(list(reversed(chain)))

    def root(self, pose: PoseParams) -> np.ndarray:
        r = _rot(pose.theta[0], self.sk.J[0])
        r[:2, 2] += pose.shift
        return r

    def world(self, b: int, pose: PoseParams) -> np.ndarray:
        T = self.root(pose)
        for j in self.chains[b]:
            T = T @ _rot(pose.theta[j], self.sk.J[j])
        return T

    def point(self, b: int, p, pose: PoseParams) -> np.ndarray:
        W = self.world(b, pose)
        return W[:2, :2] @ np.asarray(p, float) + W[:2, 2]

    def rot(self, th: np.ndarray, name: str) -> float:
        j, total = self.sk.idx[name], 0.0
        while j >= 0:
            total += th[j]
            j = self.sk.parent[j]
        return total

    def joints(self, pose: PoseParams) -> dict[str, Any]:
        heads, tails = {}, {}
        for i, b in enumerate(self.sk.bones):
            W = self.world(i, pose)
            heads[b.name] = W[:2, :2] @ b.head + W[:2, 2]
            tails[b.name] = W[:2, :2] @ b.tail + W[:2, 2]
        r = lambda v: [round(float(v[0]), 2), round(float(v[1]), 2)]
        return {"joints_posed": {k: r(v) for k, v in heads.items()},
                "bone_tails_posed": {k: r(v) for k, v in tails.items()}}


def unwrap_abduction(abd: float) -> float:
    """Upper-arm abduction in (-90, 270]: an arm raised past vertical keeps increasing instead of wrapping to -180
    (the shoulder rhythm would otherwise flip from elevation to depression overhead)."""
    return abd + 360.0 if abd <= -90.0 else abd


class SkeletalPoseSolver:
    def __init__(self, sk: Skeleton, profile: CharacterProfile, masses: dict[int, tuple[float, np.ndarray]],
                 limit_overrides: dict[tuple[str, str], dict[str, tuple[float, float]]] | None = None):
        self.sk, self.p, self.masses = sk, profile, masses
        self.K = Kinematics(sk)
        self.I = sk.idx
        self.limit_overrides = limit_overrides or {}

    # -- limits ----------------------------------------------------------------------------------------
    def limit(self, kind: str, key: str, side: str = "") -> tuple[float, float]:
        return tuple(self.limit_overrides.get((kind, side), {}).get(key) or self.p.limits[kind][key])

    def _clamp(self, kind, key, v, clamps, side=""):
        lo, hi = self.limit(kind, key, side)
        if v < lo - 1e-9 or v > hi + 1e-9:
            clamps.append(f"{kind}{'_' + side if side else ''} {key} {v:.1f} clamped to [{lo}, {hi}]")
        return float(np.clip(v, lo, hi))

    # -- solve -----------------------------------------------------------------------------------------
    def solve(self, it: PoseIntent) -> tuple[PoseParams, dict[str, Any]]:
        sk, I, K = self.sk, self.I, self.K
        N = len(sk.bones)
        th = np.zeros(N)
        clamps: list[str] = []
        notes: list[str] = list(it.notes)
        self._notes: list[str] = []
        self._clav_prev: dict[str, float] = {}
        self._joint_targets: dict[str, np.ndarray] = {}
        self._arm_notes: dict[str, str] = {}  # last call wins (the clavicle search re-solves the arm)
        self._joint_fk: dict[str, bool] = {}  # arms whose joint target is out of reach (FK fallback)
        th[0] = self._clamp("pelvis", "tilt", it.pelvis_roll, clamps)
        th[I["spine"]] = self._clamp("spine", "bend", it.spine, clamps)
        chest = -it.chest_counter * (th[0] + th[I["spine"]]) if it.chest is None else it.chest
        th[I["chest"]] = self._clamp("chest", "bend", chest, clamps)
        rel = it.head - K.rot(th, "chest")
        th[I["neck"]] = self._clamp("neck", "bend", it.neck_share * rel, clamps)
        th[I["head"]] = self._clamp("head", "bend", rel - th[I["neck"]], clamps)

        planted = [s for s in SIDES if s in it.legs and it.legs[s].planted]
        sx = it.pelvis_dx + it.root[0]
        info: dict[str, Any] = {}
        iters = 24 if it.balance == "static" and planted else 1
        clav: dict[str, float] = {}
        for _ in range(iters):
            sy, hinfo = self._pelvis_height(it, th, sx, planted)
            pose = PoseParams(th, np.ones(N), np.array([sx, sy + it.root[1]]))
            leg_rep = self._legs(it, pose)  # before the arms: arm targets may come from solved joints (knees)
            clav = self._clavicles(it, th, np.array([sx, sy + it.root[1]]), clamps)
            arm_rep = self._arms(it, pose, clamps)
            com = self.com(pose)
            target_x, support = self._support(pose, it, planted)
            err = target_x - com[0] if (it.balance == "static" and planted) else 0.0
            info = {"com": com, "target_x": target_x, "support": support, "balance_error_px": err,
                    "pelvis_height": hinfo, "legs": leg_rep, "arms": arm_rep}
            if abs(err) < BALANCE_TOL_PX:
                break
            lim = 0.8 * self.p.hip_sep
            sx = float(np.clip(sx + 0.9 * err, it.pelvis_dx - lim, it.pelvis_dx + lim))
        pose = PoseParams(th.copy(), np.ones(N), pose.shift.copy())
        notes += sorted(set(self._notes)) + [self._arm_notes[k] for k in sorted(self._arm_notes)]
        info.update(K.joints(pose))
        anat = self.anatomical(pose)
        info.update({"clamps": sorted(set(clamps)), "violations": self.check_limits(pose, it, anat),
                     "anatomical": anat, "clavicle_elevation": clav, "notes": notes,
                     "pelvis_shift_px": [round(float(pose.shift[0]), 2), round(float(pose.shift[1]), 2)],
                     "contact": {s: (s in planted) for s in SIDES},
                     "contact_loaded": {s: (s in planted and it.legs[s].load >= LOADED) for s in SIDES}})
        return pose, info

    @staticmethod
    def rhythm(abd: float) -> float:
        """Scapulohumeral rhythm: clavicle elevation (deg) for an upper-arm abduction (deg)."""
        return 0.33 * max(0.0, abd - 90.0) - 0.045 * max(0.0, 90.0 - abd)

    def _clavicles(self, it: PoseIntent, th: np.ndarray, shift, clamps: list[str]) -> dict[str, float]:
        """Clavicle elevations. FK arms: rhythm of the commanded abduction. IK arms: fixed point of
        rhythm(abduction of the solved upper arm), so an IK arm that reproduces an FK arm gets the same clavicle
        (FK<->IK switching without a jump)."""
        I = self.I
        shift = np.array([float(shift), 0.0]) if np.ndim(shift) == 0 else np.asarray(shift, float)
        out = {}
        for s in SIDES:
            a = it.arms.get(s, ArmIntent())
            base = it.clavicle.get(s, 0.0)
            ci = I[f"clavicle_{s}"]
            if not it.scapulohumeral:
                e = base
            elif (a.target is None and a.reach is None and a.target_joint is None) or self._joint_unreachable(a, s, th, shift):
                e = base + self.rhythm(a.abduction)
            else:
                lo, hi = self.limit("clavicle", "elevation", s)

                def ik_at(e):
                    th[ci] = SIDE_SIGN[s] * float(np.clip(e, lo, hi))
                    shp, _, _, ikr = self._arm_ik(a, s, th, PoseParams(th, np.ones(len(th)), shift))
                    return shp, ikr

                def h(e):  # fixed-point residual: rhythm(abduction of the solved arm) - elevation
                    shp, ikr = ik_at(e)
                    off = self.sk.bones[I[f"upper_arm_{s}"]].axis_offset  # anatomical axis, not the pivot line
                    return base + self.rhythm(unwrap_abduction(SIDE_SIGN[s] * (ang(ikr.mid - shp) + off))) - e

                # root by bracketing (plain iteration oscillates: the residual's slope is below -1 near extension);
                # the balance loop re-solves with a slightly moved pelvis, so try a narrow bracket first
                prev = self._clav_prev.get(s)
                if prev is not None and lo < prev - 0.25 and prev + 0.25 < hi and h(prev - 0.25) > 0 > h(prev + 0.25):
                    lo_b, hi_b = prev - 0.25, prev + 0.25
                    e = float(brentq(h, lo_b, hi_b, xtol=1e-10))
                    h_lo = h_hi = None
                else:
                    h_lo, h_hi = h(lo), h(hi)
                if h_lo is None:
                    pass
                elif h_lo <= 0:
                    e = lo
                elif h_hi >= 0:
                    e = hi
                else:
                    e = float(brentq(h, lo, hi, xtol=1e-10))
                shp, ikr = ik_at(e)
                if ikr.status == "clamped_max_reach":
                    # shoulder compensation: move the clavicle (within its limits) toward the target until it is
                    # in reach; elevation for high targets, depression for low ones
                    tgt = ikr.target
                    dist = lambda x: float(np.hypot(*(tgt - ik_at(x)[0])))
                    e0 = e
                    end = hi if dist(hi) < dist(lo) else lo
                    if dist(end) < dist(e0) - 1e-6:
                        if ik_at(end)[1].status != "clamped_max_reach":
                            a_in, a_out = end, e0  # bisect between the unreachable start and the reachable end
                            for _ in range(30):
                                mid = 0.5 * (a_in + a_out)
                                if ik_at(mid)[1].status == "clamped_max_reach":
                                    a_out = mid
                                else:
                                    a_in = mid
                            e = a_in
                        else:
                            e = end
                        verb = "raised" if e > e0 else "lowered"
                        self._notes.append(f"arm_{s}: clavicle {verb} {abs(e - e0):.1f} deg toward the target"
                                           + ("" if ik_at(e)[1].status == "ok" else " (still out of reach)"))
                self._clav_prev[s] = e
            out[s] = self._clamp("clavicle", "elevation", e, clamps, s)
            th[ci] = SIDE_SIGN[s] * out[s]
        return out

    def _joint_unreachable(self, a: ArmIntent, s: str, th: np.ndarray, shift) -> bool:
        """A joint target (hands on knees) that even the thigh puts out of reach: the arm keeps its FK pose instead
        of straining straight toward the hip (that tears the armpit). Recorded per solve."""
        if a.target_joint is None:
            return False
        if s not in self._joint_fk:
            self._arm_ik(a, s, th, PoseParams(th, np.ones(len(th)), shift))
            self._joint_fk[s] = "out of reach" in self._arm_notes.get(s, "") and "thigh are" in self._arm_notes.get(s, "")
            if self._joint_fk[s]:
                self._arm_notes[s] = self._arm_notes[s].replace("the arm reaches toward the hip",
                                                                "the arm keeps its relaxed FK pose")
        return self._joint_fk[s]

    def _arm_ik(self, a: ArmIntent, s: str, th: np.ndarray, pose: PoseParams):
        """Shoulder, target, pole and IK result of an IK arm in the given pose (the clavicle sets the shoulder)."""
        I, sk, K = self.I, self.sk, self.K
        up, fo = I[f"upper_arm_{s}"], I[f"forearm_{s}"]
        Wc = K.world(I[f"clavicle_{s}"], pose)
        shp = Wc[:2, :2] @ sk.J[up] + Wc[:2, 2]
        L1, L2 = sk.bones[up].length, sk.bones[fo].length
        if a.target is not None:
            target = np.asarray(a.target, float)
        elif a.target_joint is not None:
            target = np.asarray(self._joint_targets[a.target_joint], float) + np.asarray(a.target_offset, float)
            jside = a.target_joint.rsplit("_", 1)[1]
            hip = self._joint_targets.get(f"hip_{jside}")
            reach = 0.97 * TwoBoneIKSolver(L1, L2, MIN_FLEX, 179.0).max_reach
            if a.target_joint.startswith("knee_") and hip is not None and np.hypot(*(target - shp)) > reach:
                # the knee is out of reach (front-view sitting keeps the knees low): rest the hand on the thigh
                # instead, at the farthest reachable point of the hip->knee line
                lo_t, hi_t = 0.0, 1.0
                for _ in range(40):
                    m = 0.5 * (lo_t + hi_t)
                    if np.hypot(*(hip + m * (target - hip) - shp)) <= reach:
                        lo_t = m
                    else:
                        hi_t = m
                if np.hypot(*(hip - shp)) > reach:
                    self._arm_notes[s] = (f"arm_{s}: {a.target_joint} and the thigh are out of reach (arm "
                                       f"{L1 + L2:.0f} px, shoulder to hip {np.hypot(*(hip - shp)):.0f} px): the arm "
                                       "reaches toward the hip; hands on knees need a seated drawing")
                    target = hip
                else:
                    self._arm_notes[s] = (f"arm_{s}: {a.target_joint} out of reach; hand rests on the thigh "
                                       f"({100 * lo_t:.0f} % of the way to the knee)")
                    target = hip + lo_t * (target - hip)
        else:
            lat, upv = a.reach
            target = shp + np.array([LAT[s] * lat * (L1 + L2), -upv * (L1 + L2)])
        mid = (shp + target) / 2
        if a.pole is not None:
            pole = np.asarray(a.pole, float)
        elif a.elbow == "down":
            pole = mid + np.array([0.0, 1000.0])
        else:
            pole = mid + np.array([LAT[s] * 1000.0, 0.0])
        flex_hi = abs(self.limit("forearm", "flexion", s)[1])
        if a.max_flex is not None:
            flex_hi = max(MIN_FLEX, min(flex_hi, float(a.max_flex)))
        return shp, target, pole, solve_axis_aware(L1, L2, MIN_FLEX, min(flex_hi, 179.0), sk.bones[up].axis_offset,
                                                   shp, target, pole)

    # -- pelvis --------------------------------------------------------------------------------------
    def _need(self, s, th, sx, ankle, knee_deg) -> float:
        """Root y-shift that puts leg ``s`` exactly at the chord for ``knee_deg`` above ``ankle``."""
        R0 = self.K.root(PoseParams(th, np.ones(len(th)), np.array([sx, 0.0])))
        hip = R0[:2, :2] @ self.sk.bone(f"thigh_{s}").head + R0[:2, 2]
        l1, l2 = self.p.thigh[s], self.p.shin[s]
        b = math.radians(knee_deg)
        chord = math.sqrt(l1 * l1 + l2 * l2 + 2 * l1 * l2 * math.cos(b))
        dx = hip[0] - ankle[0]
        if abs(dx) >= chord:
            return float(ankle[1] - hip[1])
        return float(ankle[1] - math.sqrt(chord * chord - dx * dx) - hip[1])

    def _pelvis_height(self, it: PoseIntent, th, sx, planted) -> tuple[float, dict[str, Any]]:
        if it.balance == "dynamic":
            # gaits. Planted feet: every loaded foot must reach its ground target; a foot that is unloading or
            # just loading may rise up to UNLOAD_PX (heel-off / heel-strike), which keeps the hand-over continuous.
            # Airborne: the standing height passes from the leaving leg to the landing leg with their loads.
            w = {s: max(it.legs[s].load, 0.0) for s in SIDES if s in it.legs}
            if planted:
                needs = [self._need(s, th, sx, it.legs[s].ankle, it.legs[s].knee_min) - (1.0 - w[s]) * UNLOAD_PX
                         for s in planted]
                if it.smooth_support and len(needs) > 1:
                    # load-weighted soft max: no kink when the binding foot changes, and a foot that is just loading
                    # (or unloading) enters the max gradually instead of with a step
                    k = SOFT_SUPPORT_PX
                    m = max(needs)
                    ws = [max(w[s], 1e-4) for s in planted]
                    ref = m + k * math.log(sum(wi * math.exp((n - m) / k) for wi, n in zip(ws, needs)) / max(ws))
                else:
                    ref = max(needs)
            else:
                tot = sum(w.values()) or 1.0
                ref = sum(w[s] * self._need(s, th, sx, it.legs[s].ground or it.legs[s].ankle, it.legs[s].knee_min)
                          for s in w) / tot
            sy = ref + it.pelvis_dy
            return sy, {"standing_shift_y": round(ref, 2), "requested_dy": round(it.pelvis_dy, 2),
                        "applied_dy": round(it.pelvis_dy, 2), "limited": False, "loads": w}
        if planted:
            hi = max(self._need(s, th, sx, it.legs[s].ankle, it.legs[s].knee_min) for s in planted)
            lo = min(self._need(s, th, sx, it.legs[s].ankle, it.legs[s].knee_max) for s in planted)
            want = hi + max(it.pelvis_dy, 0.0)
            sy = float(np.clip(want, hi, max(lo, hi)))
            rep = {"standing_shift_y": round(hi, 2), "requested_dy": round(it.pelvis_dy, 2),
                   "applied_dy": round(sy - hi, 2), "limited": bool(abs(sy - want) > 0.5 or it.pelvis_dy < -0.5)}
            return sy, rep
        feet = it.standing_feet or {s: it.legs[s].ankle for s in SIDES if s in it.legs}
        ref = max(self._need(s, th, sx, feet[s], 3.0) for s in feet)
        sy = ref + it.pelvis_dy
        return sy, {"standing_shift_y": round(ref, 2), "requested_dy": round(it.pelvis_dy, 2),
                    "applied_dy": round(it.pelvis_dy, 2), "limited": False, "airborne": True}

    # -- legs ---------------------------------------------------------------------------------------
    def _legs(self, it: PoseIntent, pose: PoseParams) -> dict[str, Any]:
        th, I, sk = pose.theta, self.I, self.sk
        R0 = self.K.root(pose)
        rep = {}
        for s in SIDES:
            leg = it.legs.get(s)
            t, sh, ft = I[f"thigh_{s}"], I[f"shin_{s}"], I[f"foot_{s}"]
            if leg is None:
                continue
            hip = R0[:2, :2] @ sk.J[t] + R0[:2, 2]
            l1, l2 = self.p.thigh[s], self.p.shin[s]
            target = np.asarray(leg.ankle, float)
            note = None
            if not leg.planted and leg.hang_knee is not None:
                # swing leg: the knee follows its smooth schedule (it would snap if derived from distance near full
                # extension); the foot hangs at the track x. A ground guard bends the knee further only when the
                # hanging foot would dip below its clearance line.
                gy = (leg.ground or leg.ankle)[1]
                clear_y = gy - leg.clearance  # planner ramps the clearance in and out with the swing
                knee_deg = leg.hang_knee
                ch = self._chord(l1, l2, knee_deg)
                dx = float(np.clip(target[0] - hip[0], -0.98 * ch, 0.98 * ch))
                ay = hip[1] + math.sqrt(max(ch * ch - dx * dx, 0.0))
                if ay > clear_y:
                    need = float(np.hypot(target[0] - hip[0], clear_y - hip[1]))
                    knee_deg = max(knee_deg, self._knee_for(l1, l2, need))
                    ch = self._chord(l1, l2, knee_deg)
                    dx = float(np.clip(target[0] - hip[0], -0.98 * ch, 0.98 * ch))
                    ay = hip[1] + math.sqrt(max(ch * ch - dx * dx, 0.0))
                    if knee_deg > leg.knee_max + 0.5:
                        note = f"knee {knee_deg:.0f} deg (> {leg.knee_max:.0f}) to keep the swing foot off the ground"
                goal = np.array([hip[0] + dx, ay])
            else:
                d = float(np.hypot(*(target - hip)))
                goal = target
                if not leg.planted:
                    cmin = self._chord(l1, l2, leg.knee_max)
                    if d < cmin - 1e-6:
                        goal = hip + (target - hip) / max(d, 1e-6) * cmin
                        note = f"knee limit {leg.knee_max:.0f} deg: foot stops {cmin - d:.1f} px short of its target"
                if d > l1 + l2:
                    note = f"target out of reach by {d - (l1 + l2):.1f} px: leg straightened toward it"
            mid = (hip + goal) / 2
            pole = np.asarray(leg.pole, float) if leg.pole is not None else mid + np.array([LAT[s] * 1000.0, 0.0])
            ikr = TwoBoneIKSolver(l1, l2, MIN_FLEX, 179.0).solve(hip, goal, pole)  # knee lateral unless a pole says so
            knee, ankle = ikr.mid, ikr.end
            th[t] = wrap(ang(knee - hip) - sk.bones[t].rest_angle - th[0])
            th[sh] = wrap(ang(ankle - knee) - sk.bones[sh].rest_angle - th[0] - th[t])
            shin_world = th[0] + th[t] + th[sh]
            want = leg.foot_angle + leg.foot_follow * wrap(shin_world)
            th[ft] = wrap(want - shin_world)
            req = np.asarray(leg.ankle, float)
            rep[s] = {"planted": leg.planted, "target": [round(float(v), 2) for v in req],
                      "goal": [round(float(v), 2) for v in goal], "ankle": [round(float(v), 2) for v in ankle],
                      "error_px": round(float(np.hypot(*(ankle - (goal if not leg.planted else req)))), 3),
                      "requested_minus_achieved_lift_px": round(float(ankle[1] - req[1]), 2),
                      "knee": [round(float(v), 2) for v in knee], "note": note}
            self._joint_targets[f"knee_{s}"] = knee
            self._joint_targets[f"hip_{s}"] = hip
        return rep

    @staticmethod
    def _chord(l1: float, l2: float, knee_deg: float) -> float:
        return math.sqrt(l1 * l1 + l2 * l2 + 2 * l1 * l2 * math.cos(math.radians(knee_deg)))

    @staticmethod
    def _knee_for(l1: float, l2: float, chord: float) -> float:
        c = (chord * chord - l1 * l1 - l2 * l2) / (2 * l1 * l2)
        return math.degrees(math.acos(float(np.clip(c, -1.0, 1.0))))

    # -- arms ---------------------------------------------------------------------------------------
    def _arms(self, it: PoseIntent, pose: PoseParams, clamps: list[str]) -> dict[str, Any]:
        th, I, sk, K = pose.theta, self.I, self.sk, self.K
        rep = {}
        for s in SIDES:
            a = it.arms.get(s, ArmIntent())
            up, fo, ha, cl = I[f"upper_arm_{s}"], I[f"forearm_{s}"], I[f"hand_{s}"], f"clavicle_{s}"
            sg = SIDE_SIGN[s]
            r: dict[str, Any] = {"mode": "fk"}
            if a.target is not None or a.reach is not None or (a.target_joint is not None and not self._joint_fk.get(s)):
                shp, target, pole, ikr = self._arm_ik(a, s, th, pose)
                elbow, wrist, ok = ikr.mid, ikr.end, ikr.reached
                th[up] = wrap(ang(elbow - shp) - sk.bones[up].rest_angle - K.rot(th, cl))
                th[fo] = wrap(ang(wrist - elbow) - sk.bones[fo].rest_angle - K.rot(th, f"upper_arm_{s}"))
                r = {"mode": "ik", "target": [round(float(v), 2) for v in target],
                     "wrist": [round(float(v), 2) for v in wrist], "elbow": [round(float(v), 2) for v in elbow],
                     "pole": [round(float(v), 2) for v in pole], "status": ikr.status,
                     "flexion_deg": round(ikr.flexion_deg, 2),
                     "error_px": round(float(np.hypot(*(wrist - target))), 3), "reachable": bool(ok)}
            else:
                abd = self._clamp("upper_arm", "abduction", a.abduction, clamps, s)
                th[up] = wrap(sg * abd - sk.bones[up].anat_angle - K.rot(th, cl))
                flex = self._clamp("forearm", "flexion", a.flexion, clamps, s)
                rest_rel = wrap(sk.bones[fo].rest_angle - sk.bones[up].anat_angle)
                th[fo] = wrap(-sg * flex - rest_rel)
            if a.hand_dir is not None:
                th[ha] = wrap(a.hand_dir - sk.bones[ha].rest_angle - K.rot(th, f"forearm_{s}"))
            else:
                wr = self._clamp("hand", "bend", a.wrist, clamps, s)
                th[ha] = wrap(-sg * wr - wrap(sk.bones[ha].rest_angle - sk.bones[fo].rest_angle))
            rep[s] = r
        return rep

    # -- balance -------------------------------------------------------------------------------------
    def com(self, pose: PoseParams) -> np.ndarray:
        tot, acc = 0.0, np.zeros(2)
        for b, (m, c) in self.masses.items():
            acc += m * self.K.point(b, c, pose)
            tot += m
        return acc / max(tot, 1.0)

    def foot_centre(self, s: str, pose: PoseParams) -> np.ndarray:
        fb = self.I[f"foot_{s}"]
        mid = (self.sk.J[fb] + self.sk.bones[fb].tail) / 2
        return self.K.point(fb, mid, pose)

    def _support(self, pose, it: PoseIntent, planted) -> tuple[float, dict[str, Any]]:
        cen = {s: self.foot_centre(s, pose) for s in SIDES}
        wl = it.weight_left if len(planted) == 2 else (1.0 if planted == ["l"] else 0.0 if planted == ["r"] else 0.5)
        target = (1 - wl) * cen["r"][0] + wl * cen["l"][0]
        sh = self.p.shoe
        ext = []
        for s in planted:
            ax = self.K.point(self.I[f"foot_{s}"], self.sk.J[self.I[f"foot_{s}"]], pose)[0]
            lo, hi = sorted([ax + LAT[s] * sh[s]["lateral"], ax - LAT[s] * sh[s]["medial"]])
            ext += [lo, hi]
        x_range = [round(float(min(ext)), 1), round(float(max(ext)), 1)] if ext else None
        return float(target), {"planted": list(planted), "weight_left": wl, "x_range": x_range,
                               "foot_centres": {s: [round(float(v), 1) for v in cen[s]] for s in SIDES}}

    # -- anatomy report ------------------------------------------------------------------------------
    def anatomical(self, pose: PoseParams) -> dict[str, float]:
        th, sk, I, K = pose.theta, self.sk, self.I, self.K
        w = lambda n: wrap(sk.bone(n).anat_angle + K.rot(th, n))
        out = {"pelvis_tilt": th[0], "spine_bend": th[I["spine"]], "chest_bend": th[I["chest"]],
               "neck_bend": th[I["neck"]], "head_bend": th[I["head"]], "head_world": K.rot(th, "head")}
        for s in SIDES:
            sg = SIDE_SIGN[s]
            out[f"clavicle_{s}_elevation"] = sg * th[I[f"clavicle_{s}"]]
            out[f"upper_arm_{s}_abduction"] = sg * w(f"upper_arm_{s}")
            out[f"forearm_{s}_flexion"] = -sg * wrap(w(f"forearm_{s}") - w(f"upper_arm_{s}"))
            out[f"hand_{s}_bend"] = -sg * wrap(w(f"hand_{s}") - w(f"forearm_{s}"))
            out[f"thigh_{s}_abduction"] = sg * w(f"thigh_{s}")
            out[f"knee_{s}_flexion"] = -sg * wrap(w(f"shin_{s}") - w(f"thigh_{s}"))  # + = knee bends laterally
            out[f"foot_{s}_world"] = K.rot(th, f"foot_{s}")
        return {k: round(float(v), 3) for k, v in out.items()}

    def check_limits(self, pose: PoseParams, it: PoseIntent, a: dict[str, float]) -> list[str]:
        L = self.limit
        rng = {"pelvis_tilt": L("pelvis", "tilt"), "spine_bend": L("spine", "bend"), "chest_bend": L("chest", "bend"),
               "neck_bend": L("neck", "bend"), "head_bend": L("head", "bend")}
        bad = []
        for s in SIDES:
            rng[f"clavicle_{s}_elevation"] = L("clavicle", "elevation", s)
            rng[f"upper_arm_{s}_abduction"] = L("upper_arm", "abduction", s)
            rng[f"forearm_{s}_flexion"] = L("forearm", "flexion", s)
            rng[f"hand_{s}_bend"] = (-90.0, 90.0) if it.arms.get(s, ArmIntent()).hand_dir is not None else L("hand", "bend", s)
            rng[f"thigh_{s}_abduction"] = L("thigh", "abduction", s)
            leg = it.legs.get(s)
            kmax = leg.knee_max if leg else self.p.front_knee["stance"]
            rng[f"knee_{s}_flexion"] = (-2.0, kmax)
        for k, (lo, hi) in rng.items():
            if not lo - 0.5 <= a[k] <= hi + 0.5:
                bad.append(f"{k} {a[k]:.1f} outside [{lo}, {hi}]")
        return bad


def masses_from_masks(sk: Skeleton, masks: dict[str, np.ndarray]) -> dict[int, tuple[float, np.ndarray]]:
    """Mass proxy per home bone: opaque rest pixel count and centroid (same as ``Deformer.masses``)."""
    acc: dict[int, list] = {}
    for cid, m in masks.items():
        ys, xs = np.nonzero(m)
        if not len(xs):
            continue
        pts = np.stack([xs, ys], 1).astype(np.float64)
        hb = sk.home(cid, pts)
        for b in np.unique(hb):
            sel = hb == b
            mm = float(sel.sum())
            c = pts[sel].mean(0)
            if b in acc:
                m0, c0 = acc[b]
                acc[b] = [m0 + mm, (c0 * m0 + c * mm) / (m0 + mm)]
            else:
                acc[b] = [mm, c]
    return {int(b): (v[0], v[1]) for b, v in acc.items()}
