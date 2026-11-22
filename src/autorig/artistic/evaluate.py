"""Phase 3.5 ArtisticAnimationEvaluator: animation-quality checks kept separate from the geometric garment evaluator
(gaps, clipped pixels, mesh validity).

Per frame (deterministic, from the render and the solve):
- readability and silhouette clarity (``SilhouetteQualityEvaluator``: hands on the contour, arm separation,
  tangencies, symmetry, weight clarity, head counterbalance, disconnected outline);
- balance (centre of mass outside the support span);
- proportion consistency (posed bone lengths vs rest: bones are never scaled, so this must stay ~0);
- garment volume (blazer + trouser area vs rest; an unnatural swell or shrink is flagged);
- artwork orientation (the hand drawing matches the gesture asked for).

Per sequence:
- continuity (``continuity_check``): velocity jumps in the pose-parameter curves, and solver kinks (a joint limit
  clamp starting or stopping, the static balance saturating);
- acceleration peaks (informational): accelerations above 4x the sequence median, i.e. the fast moves to look at;
- smoothness: jerk;
- hand-switch flicker: a drawing held for fewer frames than the hold;
- garment-volume jumps: a frame-to-frame area change above 0.08 that its neighbouring frames do not share (a pop); a
  steady change during a fast move is reported as ``garment_volume_motion``, and an area change caused by a switched
  drawing (a cuff-trimmed forearm) as ``drawing_switch_area_changes``.

Optional image-based review can be attached (``image_review``), but no score here is proof of professional
quality. ``artistic_review_required`` is always true, and human approval is a separate acceptance step.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .silhouette import SilhouetteQualityEvaluator

GESTURE_HANDS = {"pointing": {"point", "pointing"}, "raised": {"open", "open_palm", "relaxed"},
                 "shrugging": {"open", "open_palm", "cupped", "palm_up"}, "relaxed_standing": {"relaxed", "open"},
                 "running": {"fist", "relaxed"}, "resting": {"palm_down", "relaxed", "open"}}
JUMP_FRAC = 0.1  # a parameter velocity jump larger than 10 % of its range per second is a discontinuity
BALANCE_SATURATION_PX = 0.5  # static balance error above this: the pelvis shift limit was hit
ACCEL_FLOOR = 1500.0  # px/s^2: accelerations below this are never listed as peaks
VOLUME_STEP = 0.08  # frame-to-frame garment area change considered large
VOLUME_POP_RATIO = 2.5  # ... and a pop when it is this many times larger than both neighbouring changes
GARMENT_LAYERS = ("torso", "pelvis", "upper_arm_r", "upper_arm_l", "forearm_r", "forearm_l", "thigh_r", "thigh_l",
                  "shin_r", "shin_l")


class ArtisticAnimationEvaluator:
    def __init__(self, eng):
        self.eng = eng
        self.rest_len = {b.name: float(np.hypot(*(np.asarray(b.tail) - np.asarray(b.head)))) for b in eng.sk.bones}
        self.rest_area = {c: int(m.sum()) for c, m in eng.masks.items() if c in GARMENT_LAYERS}
        self.sil = SilhouetteQualityEvaluator()

    def frame(self, res: dict[str, Any], action: str = "", gestures: dict[str, str] | None = None,
              hand_actions: dict[str, str] | None = None) -> dict[str, Any]:
        s = res["solve"]
        J, T = s["joints_posed"], s["bone_tails_posed"]
        sil = self.sil.evaluate(res, action, gestures or {})
        prop = 0.0
        for b, L0 in self.rest_len.items():
            if b in J and b in T and L0 > 1:
                prop = max(prop, abs(float(np.hypot(T[b][0] - J[b][0], T[b][1] - J[b][1])) / L0 - 1.0))
        al = res["render"]["alphas"]
        vol, var = {}, {}
        for c, a0 in self.rest_area.items():
            ks = [k for k in al if k.split("__")[0] == c]
            a = sum(int(al[k].sum()) for k in ks)
            if a0 > 3000 and a:
                vol[c] = round(a / a0, 3)
                var[c] = "+".join(sorted(ks))
        com, sup = s.get("com"), (s.get("support") or {}).get("x_range")
        bal = round(float(max(sup[0] - com[0], com[0] - sup[1], 0.0)), 1) if com is not None and sup else None
        orient = {}
        for side, act in (hand_actions or {}).items():
            d = res["hands"].get(side, {}).get("drawing")
            ok = act not in GESTURE_HANDS or d in GESTURE_HANDS[act]
            orient[side] = {"asked": act, "drawn": d, "ok": bool(ok)}
        warns = list(sil["warnings"])
        if prop > 0.02:
            warns.append({"code": "proportion_change", "severity": "high", "message": f"bone length changed {prop:.1%}"})
        # drawn area vs the T-pose rest drawing: only gross changes are flagged per frame (a hanging arm legitimately
        # draws less sleeve than the T-pose); frame-to-frame jumps are checked over the sequence
        bad = {c: v for c, v in vol.items() if v < 0.6 or v > 1.4}
        if bad:
            warns.append({"code": "garment_volume", "severity": "medium", "message": f"garment area changes {bad}"})
        if bal is not None and bal > 25:
            warns.append({"code": "off_balance", "severity": "medium", "message": f"centre of mass {bal} px outside the feet"})
        for side, o in orient.items():
            if not o["ok"]:
                warns.append({"code": "hand_artwork_mismatch", "side": side, "severity": "medium",
                              "message": f"{side}: '{o['asked']}' gesture drawn with the '{o['drawn']}' hand"})
        return {"silhouette": sil["metrics"], "warnings": warns, "max_bone_length_change": round(prop, 4),
                "garment_area_ratio": vol, "garment_variants": var, "com_outside_support_px": bal, "hand_orientation": orient,
                "silhouette_image": sil["silhouette"], "artistic_review_required": True}

    @staticmethod
    def continuity_check(params_at, duration: float, solve_at=None, fps: float = 12.0, h: float = 1e-3,
                         solve_factor: int = 4) -> dict[str, Any]:
        """Where can the motion kink? Two sources, checked separately:

        1. the pose-parameter curves (``params_at(t)`` -> {name: value}): sampled every ``h`` seconds; a velocity
           change between two consecutive samples above JUMP_FRAC x the parameter's range per second is a candidate,
           and it is a velocity jump only if it does not shrink when re-measured at ``h / 8`` (a smooth curve changes
           its velocity by acceleration x step, so fast smooth motion shrinks; a jump stays);
        2. the solver (``solve_at(t)`` -> solve info, skeleton only, at ``solve_factor`` x fps): a joint limit clamp
           that starts or stops while the body moves, or the static balance saturating (the pelvis hits its shift
           limit, so the centre of mass stops following the weight) -- both make the motion bend sharply.

        Joint accelerations are not used for the verdict: a fast but smooth move has large accelerations, and a
        fixed frame rate cannot resolve a short anticipation."""
        n = int(round(duration / h)) + 1
        samples = [params_at(k * h) for k in range(n)]
        names = sorted(set.intersection(*[set(x) for x in samples]))
        V = np.array([[x[nm] for nm in names] for x in samples], float)
        rng = np.maximum(V.max(axis=0) - V.min(axis=0), 1e-9)
        dv = np.abs(np.diff(V, 2, axis=0)) / h  # velocity change per step (units/s)
        jumps, smooth_fast = [], 0
        for k, j in zip(*np.nonzero(dv > JUMP_FRAC * rng)):
            # resolution test (Phase 3.6): a smooth but fast curve changes its velocity by acceleration x step, so the
            # change shrinks at a finer step; a real velocity jump does not. Re-measure at h / 8 around the sample.
            tk, nm, h2 = (k + 1) * h, names[j], h / 8.0
            p0, p1, p2 = (float(params_at(tk + d)[nm]) for d in (-h2, 0.0, h2))
            fine = abs((p2 - p1) - (p1 - p0)) / h2
            if fine < 0.5 * float(dv[k, j]):
                smooth_fast += 1
                continue
            jumps.append({"t": round(float(tk), 3), "param": nm, "velocity_jump": round(float(dv[k, j]), 3),
                          "threshold": round(float(JUMP_FRAC * rng[j]), 3)})
        events = []
        if solve_at is not None:
            f = fps * solve_factor
            prev = None
            for k in range(int(round(duration * f)) + 1):
                t = k / f
                si = solve_at(t)
                cl = set(si.get("clamps") or [])
                err = abs(float(si.get("balance_error_px") or 0.0))
                if err > BALANCE_SATURATION_PX:
                    events.append({"t": round(t, 3), "event": "balance_saturated", "balance_error_px": round(err, 2)})
                if prev is not None and cl != prev:
                    events.append({"t": round(t, 3), "event": "clamp_change", "started": sorted(cl - prev),
                                   "stopped": sorted(prev - cl)})
                prev = cl
        return {"param_step_s": h, "jump_fraction_of_range_per_s": JUMP_FRAC, "velocity_jumps": jumps[:30],
                "fast_smooth_samples": smooth_fast,
                "velocity_jump_count": len(jumps), "solver_events": events[:30], "solver_event_count": len(events),
                "solver_fps": fps * solve_factor if solve_at is not None else None}

    def sequence(self, frames: list[dict[str, Any]], fps: float, hold_frames: int = 3,
                 continuity: dict[str, Any] | None = None) -> dict[str, Any]:
        """``frames``: [{"joints": {name: [x, y]}, "hands": {side: drawing}, "garment_area_ratio": {...}}];
        ``continuity``: the result of ``continuity_check`` for the same motion (needed for a continuity verdict)."""
        names = sorted(set.intersection(*[set(f["joints"]) for f in frames])) if frames else []
        P = np.array([[f["joints"][n] for n in names] for f in frames], float) if names else np.zeros((0, 0, 2))
        out: dict[str, Any] = {"frames": len(frames), "fps": fps}
        if len(frames) >= 4 and names:
            v = np.diff(P, axis=0) * fps
            acc = np.linalg.norm(np.diff(v, axis=0), axis=2) * fps
            jerk = np.linalg.norm(np.diff(np.diff(v, axis=0), axis=0), axis=2) * fps * fps
            med = float(np.median(acc[acc > 1e-6])) if (acc > 1e-6).any() else 0.0
            peaks = [(int(i) + 1, names[j], round(float(acc[i, j]), 1)) for i, j in zip(*np.nonzero(acc > 4 * max(med, 1e-6)))
                     if acc[i, j] > ACCEL_FLOOR]
            out.update({"max_speed_px_s": round(float(np.linalg.norm(v, axis=2).max()), 1),
                        "median_accel_px_s2": round(med, 1), "accel_peaks": peaks[:20], "accel_peak_count": len(peaks),
                        "max_jerk_px_s3": round(float(jerk.max()), 1) if jerk.size else 0.0})
        flick = []
        for side in ("r", "l"):
            seq = [f["hands"].get(side) for f in frames]
            run = 1
            for k in range(1, len(seq)):
                if seq[k] == seq[k - 1]:
                    run += 1
                else:
                    if run < hold_frames and k - run > 0:
                        flick.append({"side": side, "frame": k, "drawing": seq[k - 1], "held_frames": run})
                    run = 1
        out["hand_switch_flicker"] = flick
        out["hand_switches"] = {s: sum(1 for k in range(1, len(frames)) if frames[k]["hands"].get(s) !=
                                       frames[k - 1]["hands"].get(s)) for s in ("r", "l")}
        vols = [f.get("garment_area_ratio", {}) for f in frames]
        vars_ = [f.get("garment_variants", {}) for f in frames]
        jumps, motion, switches = [], [], []

        def dv(k, c):
            if 0 < k < len(vols) and c in vols[k] and c in vols[k - 1] and vars_[k].get(c) == vars_[k - 1].get(c):
                return abs(vols[k][c] - vols[k - 1][c])
            return 0.0
        for k in range(1, len(vols)):
            for c in sorted(set(vols[k]) & set(vols[k - 1])):
                d = abs(vols[k][c] - vols[k - 1][c])
                if d <= VOLUME_STEP:
                    continue
                rec = {"frame": k, "layer": c, "from": vols[k - 1][c], "to": vols[k][c]}
                if vars_[k].get(c) != vars_[k - 1].get(c):  # a switch drawing (e.g. a cuff-trimmed forearm)
                    switches.append(rec)
                elif d > VOLUME_POP_RATIO * max(dv(k - 1, c), dv(k + 1, c), 1e-6):
                    jumps.append(rec)
                else:
                    motion.append(rec)
        out["drawing_switch_area_changes"] = switches[:20]
        out["garment_volume_motion"] = motion[:20]
        out["garment_volume_jumps"] = jumps[:20]
        cc = continuity or {}
        out["continuity_check"] = continuity
        out["continuity"] = ("PASS" if continuity is not None and not cc.get("velocity_jumps")
                             and not cc.get("solver_events") and not flick and not jumps else "REVIEW")
        if continuity is None:
            out["continuity_note"] = "no continuity check supplied: continuity cannot be judged from the frames alone"
        out["artistic_review_required"] = True
        out["note"] = "deterministic checks only; artistic quality needs a person"
        return out


__all__ = ["ArtisticAnimationEvaluator", "GESTURE_HANDS", "JUMP_FRAC"]
