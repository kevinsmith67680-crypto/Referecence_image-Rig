"""PoseQualityEvaluator: measurable checks with explicit thresholds.

Every check below is computed from the solved skeleton or the rendered pixels; none is a guess. Passing all of
them means the pose is *numerically* clean (lengths, contacts, limits, seams, stretching). It does not mean it
looks good. Visual quality is never scored automatically: every pose stays ``artistic_review_required``.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..phase2.skeleton import SIDES

# name -> (threshold, unit/meaning). Documented in docs/phase3_1_natural_motion_report.md.
THRESHOLDS: dict[str, tuple[float, str]] = {
    "bone_length_rel": (0.002, "max relative change of any bone length (rigid bones: exact)"),
    "axial_scale": (0.0, "bones with an axial scale != 1 (foreshortening by scaling is not allowed)"),
    "joint_limit_violations": (0.0, "anatomical angles outside the character's limits"),
    "planted_ankle_error_px": (1.0, "weight-bearing planted ankle vs its target"),
    "planted_foot_angle_deg": (3.0, "planted shoe rotation away from flat"),
    "ik_error_px": (1.0, "reachable IK target error (arms and free legs)"),
    "ground_penetration_px": (1.5, "lowest posed shoe pixel below the ground line"),
    "planted_sole_error_px": (2.0, "planted shoe sole vs ground line"),
    "shoe_overlap_px": (0.0, "pixels where the two posed shoes overlap (legs crossing)"),
    "com_outside_support_px": (0.0, "static poses: CoM x outside the planted shoes' extent"),
    "com_outside_feet_px": (25.0, "gait frames: CoM x outside the span of both ankles"),
    "exposed_gap_pairs": (10.0, "rest-adjacent pixel pairs torn apart where the background shows"),
    "visible_seam_pairs": (15.0, "torn pairs where one torn layer is on top (visible cut)"),
    "outline_break_pairs": (12.0, "torn pairs on dark outline pixels"),
    "stretched_fraction": (0.005, "max share of a layer stretched > 2.2x in area"),
    "compressed_fraction": (0.06, "max share of a layer compressed < 0.45x in area"),
    "folded_fraction": (0.03, "max share of a layer folded (inner crease, det <= 0.05)"),
    "layer_area_change": (0.04, "max |posed/rest - 1| of a layer's opaque area, layers > 3000 px (proportions)"),
    "loop_closure_px": (0.5, "max joint distance between phase 0 and phase 1 of a cycle (minus travel)"),
    "continuity_spike_px": (4.0, "max jump of a joint: its step minus the larger neighbouring step (64 samples)"),
    "foot_slide_px": (1.0, "weight-bearing ankle drift in world space within one stance (load >= 0.8)"),
    "gait_ground_penetration_px": (1.0, "ankle below its standing height in any sample"),
    "coordination_corr": (0.9, "best corr(solved forearm swing, opposite leg) within a lag of +/-0.15 cycle"),
    "com_drift_px": (6.0, "in-place cycle: mean CoM x vs the rest midline"),
    "jerk_ratio": (60.0, "upper body relative to the pelvis: max |3rd difference| of a joint path over its range x "
                         "(2 pi / N)^3 (a pure sine = 1; foot impacts are excluded on purpose)"),
    "arm_overlap_cycles": (0.02, "lag of elbow flexion behind the planned arm swing (cycle fraction); 0 = lockstep"),
    "hand_arc_quotient": (0.04, "hand path (relative to the chest) area vs a circle of equal perimeter; 0 = a line"),
}
FLUIDITY = ("jerk_ratio", "arm_overlap_cycles", "hand_arc_quotient")


def _chk(name: str, value: float, ok: bool, detail: str = "") -> dict[str, Any]:
    thr, meaning = THRESHOLDS[name]
    return {"check": name, "value": round(float(value), 4), "threshold": thr, "pass": bool(ok), "meaning": meaning,
            "detail": detail}


class PoseQualityEvaluator:
    def __init__(self, profile, sk, rest_area: dict[str, int]):
        self.p, self.sk, self.rest_area = profile, sk, rest_area

    def frame(self, it, pose, info: dict[str, Any], render: dict[str, Any], tears: dict[str, list],
              uses_pending: list[str], generated_visible: int) -> dict[str, Any]:
        T = THRESHOLDS
        sk, p, pad = self.sk, self.p, render["pad"]
        checks = []
        J, Tl = info["joints_posed"], info["bone_tails_posed"]
        errs = []
        for b in sk.bones:
            L = float(np.hypot(*(np.subtract(Tl[b.name], J[b.name]))))
            errs.append(abs(L - b.length) / max(b.length, 1e-6))
        checks.append(_chk("bone_length_rel", max(errs), max(errs) <= T["bone_length_rel"][0]))
        nscale = int((pose.scale != 1).sum())
        checks.append(_chk("axial_scale", nscale, nscale == 0))
        v = info["violations"]
        checks.append(_chk("joint_limit_violations", len(v), not v, "; ".join(v)))
        planted = [s for s in SIDES if info.get("contact_loaded", info["contact"]).get(s)]
        a_err = max([info["legs"][s]["error_px"] for s in planted] or [0.0])
        checks.append(_chk("planted_ankle_error_px", a_err, a_err <= T["planted_ankle_error_px"][0]))
        f_ang = max([abs(info["anatomical"][f"foot_{s}_world"]) for s in planted] or [0.0])
        checks.append(_chk("planted_foot_angle_deg", f_ang, f_ang <= T["planted_foot_angle_deg"][0]))
        ik = [r["error_px"] for r in info["arms"].values() if r.get("mode") == "ik" and r.get("reachable")]
        ik += [info["legs"][s]["error_px"] for s in SIDES if s in info["legs"] and not info["legs"][s]["planted"]
               and info["legs"][s]["note"] is None]
        checks.append(_chk("ik_error_px", max(ik or [0.0]), max(ik or [0.0]) <= T["ik_error_px"][0]))
        soles = {}
        for s in SIDES:
            a = render["alphas"].get(f"foot_{s}")
            soles[s] = float(np.nonzero(a.any(axis=1))[0].max() - pad) if a is not None and a.any() else None
        pen = max([(soles[s] or 0) - p.ground_y for s in SIDES if soles[s] is not None] or [0.0])
        checks.append(_chk("ground_penetration_px", max(pen, 0.0), pen <= T["ground_penetration_px"][0]))
        sole_err = max([abs(soles[s] - p.ground_y) for s in planted if soles[s] is not None] or [0.0])
        checks.append(_chk("planted_sole_error_px", sole_err, sole_err <= T["planted_sole_error_px"][0]))
        fa, fb = render["alphas"].get("foot_r"), render["alphas"].get("foot_l")
        ov = int((fa & fb).sum()) if fa is not None and fb is not None else 0
        checks.append(_chk("shoe_overlap_px", ov, ov <= T["shoe_overlap_px"][0]))
        com = info["com"]
        if it.balance == "static" and planted and info["support"]["x_range"]:
            lo, hi = info["support"]["x_range"]
            out = max(lo - com[0], com[0] - hi, 0.0)
            checks.append(_chk("com_outside_support_px", out, out <= T["com_outside_support_px"][0],
                               f"CoM x {com[0]:.1f}, support {lo}..{hi}"))
        else:
            xs = sorted(J[f"foot_{s}"][0] for s in SIDES)
            out = max(xs[0] - com[0], com[0] - xs[1], 0.0)
            checks.append(_chk("com_outside_feet_px", out, out <= T["com_outside_feet_px"][0],
                               f"CoM x {com[0]:.1f}, ankles {xs[0]:.0f}..{xs[1]:.0f}"))
        for k, key in (("exposed_gap", "exposed_gap_pairs"), ("visible_seam", "visible_seam_pairs"),
                       ("outline_break", "outline_break_pairs")):
            n = len(tears.get(k, []))
            checks.append(_chk(key, n, n <= T[key][0]))
        st = {c: s for c, s in render["stats"].items() if not s.get("rigid")}
        for key in ("stretched_fraction", "compressed_fraction", "folded_fraction"):
            worst = max(st.items(), key=lambda kv: kv[1][key], default=(None, {key: 0.0}))
            checks.append(_chk(key, worst[1][key], worst[1][key] <= T[key][0], f"layer {worst[0]}"))
        changes = {}
        for c, a in render["alphas"].items():
            base = c.split("__")[0]
            # an alignment-offset copy (``<variant>__offN``) has the area of its variant, not of the base layer
            r0 = self.rest_area.get(c) or self.rest_area.get(c.split("__off")[0]) or self.rest_area.get(base)
            if r0 and r0 > 3000:  # thin layers (brows, nose) are dominated by resampled edge pixels
                changes[c] = abs(int(a.sum()) / r0 - 1.0)
        worst = max(changes.items(), key=lambda kv: kv[1], default=(None, 0.0))
        checks.append(_chk("layer_area_change", worst[1], worst[1] <= T["layer_area_change"][0], f"layer {worst[0]}"))
        passed = sum(c["pass"] for c in checks)
        warnings = [f"{c['check']} {c['value']} > {c['threshold']}" + (f" ({c['detail']})" if c["detail"] else "")
                    for c in checks if not c["pass"]]
        review = []
        if generated_visible:
            review.append(f"{generated_visible} unreviewed generated pixels visible")
        review += [f"uses pending-review artwork: {u}" for u in uses_pending]
        return {"checks": checks, "numerical": {"passed": passed, "total": len(checks),
                                                "all_pass": passed == len(checks)},
                "warnings": warnings, "review_items": review, "artistic_review_required": True,
                "visual_quality": "UNRATED: automated checks cannot judge appeal; review the render"}

    # -- gait cycles (solved skeleton, many samples, no rendering) ------------------------------------
    def cycle(self, frames: list[tuple[Any, dict[str, Any]]], travel: float = 0.0) -> dict[str, Any]:
        """``frames``: [(intent, solver info)] at evenly spaced phases covering [0, 1], last == phase 1."""
        T = THRESHOLDS
        names = list(frames[0][1]["joints_posed"])
        P = np.array([[f[1]["joints_posed"][n] for n in names] for f in frames], float)  # (S, J, 2)
        checks = []
        closure = P[-1] - P[0]
        closure[..., 0] -= travel
        cl = float(np.abs(np.hypot(closure[..., 0], closure[..., 1])).max())
        checks.append(_chk("loop_closure_px", cl, cl <= T["loop_closure_px"][0]))
        steps = np.hypot(*(np.diff(P, axis=0).transpose(2, 0, 1)))  # (S-1, J): per-sample joint motion
        nb = np.maximum(np.roll(steps, 1, axis=0), np.roll(steps, -1, axis=0))  # cyclic neighbours
        spike = steps - nb
        k, j = np.unravel_index(int(spike.argmax()), spike.shape)
        sp = float(spike.max())
        checks.append(_chk("continuity_spike_px", max(sp, 0.0), sp <= T["continuity_spike_px"][0],
                           f"joint {names[j]} at sample {k}: step {steps[k, j]:.2f} px vs neighbours {nb[k, j]:.2f} px; "
                           f"fastest joint step {float(steps.max()):.2f} px"))
        slide = 0.0
        for s in SIDES:
            seg = []
            for it, info in frames:
                if info.get("contact_loaded", info["contact"])[s]:
                    seg.append(info["joints_posed"][f"foot_{s}"])
                elif seg:
                    slide = max(slide, float(np.ptp(np.array(seg), axis=0).max()))
                    seg = []
            if seg:
                slide = max(slide, float(np.ptp(np.array(seg), axis=0).max()))
        checks.append(_chk("foot_slide_px", slide, slide <= T["foot_slide_px"][0]))
        pen = max(max(info["joints_posed"][f"foot_{s}"][1] - self.p.ankle_y[s] for s in SIDES) for _, info in frames)
        checks.append(_chk("gait_ground_penetration_px", max(pen, 0.0), pen <= T["gait_ground_penetration_px"][0]))
        corr, lagk = {}, {}
        for s, o in (("r", "l"), ("l", "r")):
            arm = np.array([info["anatomical"][f"forearm_{s}_flexion"] for _, info in frames[:-1]])
            leg_o = np.array([it.channels[f"leg_forward_{o}"] for it, _ in frames[:-1]])
            n = len(arm)
            best, bk = -1.0, 0
            if arm.std() > 1e-6:  # opposition may lag (follow-through): best correlation within +/-0.15 cycle
                for k in range(-int(0.15 * n), int(0.15 * n) + 1):
                    c_ = float(np.corrcoef(arm, np.roll(leg_o, k))[0, 1])
                    if c_ > best:
                        best, bk = c_, k
            corr[s], lagk[s] = (best if arm.std() > 1e-6 else 0.0), bk / n
        c = min(corr.values())
        checks.append(_chk("coordination_corr", c, c >= T["coordination_corr"][0],
                           f"right arm vs left leg {corr['r']:.3f} (lag {lagk['r']:.3f}), "
                           f"left arm vs right leg {corr['l']:.3f} (lag {lagk['l']:.3f})"))
        if travel == 0.0:
            mx = float(np.mean([info["com"][0] for _, info in frames]))
            drift = abs(mx - self.p.midline_x)
            checks.append(_chk("com_drift_px", drift, drift <= T["com_drift_px"][0]))
        checks += self.fluidity(frames, P, names)
        pel = np.array([info["joints_posed"]["pelvis"][1] for _, info in frames])
        airborne = sum(1 for _, info in frames if not any(info["contact"].values()))
        passed = sum(x["pass"] for x in checks)
        return {"checks": checks, "numerical": {"passed": passed, "total": len(checks), "all_pass": passed == len(checks)},
                "samples": len(frames), "pelvis_vertical_range_px": round(float(np.ptp(pel)), 2),
                "airborne_samples": airborne, "artistic_review_required": True}

    def fluidity(self, frames, P: np.ndarray, names: list[str]) -> list[dict[str, Any]]:
        """Smoothness, overlap and arcs over one cycle (``frames`` cover [0, 1]; the last repeats the first)."""
        from .secondary import chain_lag

        T = THRESHOLDS
        # upper body relative to the pelvis: foot strikes may be sharp (impacts), arms/torso/head should not jerk
        upper = [i for i, nm in enumerate(names) if nm.split("_")[0] in
                 ("chest", "neck", "head", "clavicle", "upper", "forearm", "hand")]
        pel = names.index("pelvis")
        Q = (P[:-1, upper] - P[:-1, pel][:, None, :])  # one period
        names = [names[i] for i in upper]
        n = len(Q)
        d3 = np.roll(Q, -2, 0) - 3 * np.roll(Q, -1, 0) + 3 * Q - np.roll(Q, 1, 0)
        rng = np.maximum(np.ptp(Q, axis=0), 1.0)  # (J, 2)
        unit = (2 * np.pi / n) ** 3
        ratio = np.abs(d3) / (rng[None] * unit)
        k, j, a = np.unravel_index(int(ratio.argmax()), ratio.shape)
        jr = float(ratio.max())
        out = [_chk("jerk_ratio", jr, jr <= T["jerk_ratio"][0], f"joint {names[j]} at sample {k}")]
        lags, quot = [], []
        for s in SIDES:
            drv = np.array([it.channels[f"arm_forward_{s}"] for it, _ in frames[:-1]])  # planned swing (driver)
            flex = np.array([info["anatomical"][f"forearm_{s}_flexion"] for _, info in frames[:-1]])
            lags.append(chain_lag(drv, flex))
            hand = np.array([np.subtract(info["joints_posed"][f"hand_{s}"], info["joints_posed"]["chest"])
                             for _, info in frames[:-1]])
            area = 0.5 * abs(float(np.dot(hand[:, 0], np.roll(hand[:, 1], -1)) - np.dot(hand[:, 1], np.roll(hand[:, 0], -1))))
            per = float(np.hypot(*np.diff(np.vstack([hand, hand[:1]]), axis=0).T).sum())
            quot.append(4 * np.pi * area / per ** 2 if per > 1e-6 else 0.0)
        lag = min(lags)
        out.append(_chk("arm_overlap_cycles", lag, lag >= T["arm_overlap_cycles"][0],
                        f"right {lags[0]:.3f}, left {lags[1]:.3f} cycle"))
        q = min(quot)
        out.append(_chk("hand_arc_quotient", q, q >= T["hand_arc_quotient"][0], f"right {quot[0]:.3f}, left {quot[1]:.3f}"))
        return out

