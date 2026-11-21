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

from ..phase2.posing import two_bone_ik, wrap
from ..phase2.skeleton import SIDE_SIGN, SIDES, Skeleton, ang
from ..phase2.skinning import PoseParams
from .intent import ArmIntent, PoseIntent
from .profile import CharacterProfile

LAT = {"r": -1.0, "l": 1.0}
UNLOAD_PX = 5.0  # how far an unloading (or just landing) planted foot may rise in a gait
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
        th[0] = self._clamp("pelvis", "tilt", it.pelvis_roll, clamps)
        th[I["spine"]] = self._clamp("spine", "bend", it.spine, clamps)
        chest = -it.chest_counter * (th[0] + th[I["spine"]]) if it.chest is None else it.chest
        th[I["chest"]] = self._clamp("chest", "bend", chest, clamps)
        abd_est = {s: self._abduction_estimate(it.arms.get(s, ArmIntent()), s) for s in SIDES}
        clav = {}
        for s in SIDES:
            e = it.clavicle.get(s, 0.0)
            if it.scapulohumeral:
                a = abd_est[s]
                e += 0.33 * max(0.0, a - 90.0) - 0.045 * max(0.0, 90.0 - a)
            clav[s] = self._clamp("clavicle", "elevation", e, clamps, s)
            th[I[f"clavicle_{s}"]] = SIDE_SIGN[s] * clav[s]
        rel = it.head - K.rot(th, "chest")
        th[I["neck"]] = self._clamp("neck", "bend", it.neck_share * rel, clamps)
        th[I["head"]] = self._clamp("head", "bend", rel - th[I["neck"]], clamps)

        planted = [s for s in SIDES if s in it.legs and it.legs[s].planted]
        sx = it.pelvis_dx + it.root[0]
        info: dict[str, Any] = {}
        iters = 14 if it.balance == "static" and planted else 1
        for _ in range(iters):
            sy, hinfo = self._pelvis_height(it, th, sx, planted)
            pose = PoseParams(th, np.ones(N), np.array([sx, sy + it.root[1]]))
            leg_rep = self._legs(it, pose)
            arm_rep = self._arms(it, pose, clamps)
            com = self.com(pose)
            target_x, support = self._support(pose, it, planted)
            err = target_x - com[0] if (it.balance == "static" and planted) else 0.0
            info = {"com": com, "target_x": target_x, "support": support, "balance_error_px": err,
                    "pelvis_height": hinfo, "legs": leg_rep, "arms": arm_rep}
            if abs(err) < 0.3:
                break
            lim = 0.8 * self.p.hip_sep
            sx = float(np.clip(sx + 0.9 * err, it.pelvis_dx - lim, it.pelvis_dx + lim))
        pose = PoseParams(th.copy(), np.ones(N), pose.shift.copy())
        info.update(K.joints(pose))
        anat = self.anatomical(pose)
        info.update({"clamps": sorted(set(clamps)), "violations": self.check_limits(pose, it, anat),
                     "anatomical": anat, "clavicle_elevation": clav, "notes": notes,
                     "pelvis_shift_px": [round(float(pose.shift[0]), 2), round(float(pose.shift[1]), 2)],
                     "contact": {s: (s in planted) for s in SIDES},
                     "contact_loaded": {s: (s in planted and it.legs[s].load >= LOADED) for s in SIDES}})
        return pose, info

    def _abduction_estimate(self, a: ArmIntent, s: str) -> float:
        if a.target is None and a.reach is None:
            return a.abduction
        if a.reach is not None:
            lat, up = a.reach
            return math.degrees(math.atan2(lat, -up)) if (lat or up) else 0.0
        sh = self.sk.bone(f"upper_arm_{s}").head
        v = np.asarray(a.target, float) - sh
        return SIDE_SIGN[s] * ang(v)

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
                ref = max(self._need(s, th, sx, it.legs[s].ankle, it.legs[s].knee_min) - (1.0 - w[s]) * UNLOAD_PX
                          for s in planted)
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
            knee, ankle, ok = two_bone_ik(hip, goal, l1, l2, lambda k: (k[0] - mid[0]) * LAT[s])
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
            if a.target is not None or a.reach is not None:
                Wc = K.world(I[cl], pose)
                shp = Wc[:2, :2] @ sk.J[up] + Wc[:2, 2]
                L1, L2 = sk.bones[up].length, sk.bones[fo].length
                if a.target is not None:
                    target = np.asarray(a.target, float)
                else:
                    lat, upv = a.reach
                    target = shp + np.array([LAT[s] * lat * (L1 + L2), -upv * (L1 + L2)])
                if a.elbow == "down":
                    pref = lambda e: e[1]
                else:
                    pref = lambda e: (e[0] - shp[0]) * LAT[s]
                elbow, wrist, ok = two_bone_ik(shp, target, L1, L2, pref)
                th[up] = wrap(ang(elbow - shp) - sk.bones[up].rest_angle - K.rot(th, cl))
                th[fo] = wrap(ang(wrist - elbow) - sk.bones[fo].rest_angle - K.rot(th, f"upper_arm_{s}"))
                r = {"mode": "ik", "target": [round(float(v), 2) for v in target],
                     "wrist": [round(float(v), 2) for v in wrist],
                     "error_px": round(float(np.hypot(*(wrist - target))), 3), "reachable": bool(ok)}
            else:
                abd = self._clamp("upper_arm", "abduction", a.abduction, clamps, s)
                th[up] = wrap(sg * abd - sk.bones[up].rest_angle - K.rot(th, cl))
                flex = self._clamp("forearm", "flexion", a.flexion, clamps, s)
                rest_rel = wrap(sk.bones[fo].rest_angle - sk.bones[up].rest_angle)
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
        w = lambda n: wrap(sk.bone(n).rest_angle + K.rot(th, n))
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
