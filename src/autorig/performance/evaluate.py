"""PerformanceQualityEvaluator: geometric, motion and artistic checks, kept separate. There is no single score.

MOTION:
- position and velocity continuity: ``ArtisticAnimationEvaluator.continuity_check` (parameter velocity jumps,
  solver clamps, balance saturation). Authored stepped keys are listed separately as intended;
- foot sliding: ankle travel of planted feet, at 48 Hz;
- hand-contact sliding: the contacted point vs its target while the contact is fully on;
- contact transitions: the wrist path through acquire and release must not jump (a one-step outlier against its
  neighbours, at 48 Hz);
- joint-limit violations reported by the solver.

Fast motion is not a defect: accelerations are reported for information only.

ARTISTIC (checks that help a reviewer, never a verdict):
- key-pose clarity (holds);
- anticipation (recipe and size, appropriateness);
- timing (segment lengths);
- weight transfer (centre of mass over the feet, a clear weight leg at holds);
- follow-through settling inside the holds;
- excessive secondary motion (time spent at the limit);
- hand artwork (pending or missing per frame);
- view compatibility.

GEOMETRIC (from renders, when given): exposed gaps, original-pixel protection.

``artistic_review_required`` is always true. Smooth motion is not professional acting, and only a person can
approve a performance.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..artistic.evaluate import ArtisticAnimationEvaluator
from .anticipation import MAX_FRACTION
from .overlap import group_of

SLIDE_TOL_PX = 0.5
CONTACT_TOL_PX = 1.0
JUMP_RATIO = 3.0
MIN_HOLD_S = 0.25


class PerformanceQualityEvaluator:
    def __init__(self, director):
        self.d = director

    # -- motion ------------------------------------------------------------------------------------------------
    def motion(self, fps: float = 48.0) -> dict[str, Any]:
        d = self.d
        track = d.track(fps)
        cont = ArtisticAnimationEvaluator.continuity_check(lambda t: d.pose_at(t)[2]["params"], d.duration,
                                                           lambda t: d.solve(t)["solve"], fps=12.0)
        stepped = d.base.stepped_boundaries()
        feet = {}
        for s in ("r", "l"):
            P = np.array([f["joints"][f"foot_{s}"] for f in track], float)
            planted = np.array([bool(f["performance"].get("weight", {}).get("feet", {}).get(s, {}).get("planted", True))
                                for f in track])
            step = np.hypot(*np.diff(P, axis=0).T)
            both = planted[1:] & planted[:-1]
            feet[s] = {"max_planted_step_px": round(float(step[both].max()) if both.any() else 0.0, 4),
                       "planted_travel_px": round(float(step[both].sum()) if both.any() else 0.0, 4),
                       "swing_frames": int((~planted).sum())}
        slide = max(v["max_planted_step_px"] for v in feet.values())
        contacts = [max(f["performance"]["contact_sliding_px"].values()) for f in track
                    if f["performance"]["contact_sliding_px"]]
        jumps = self._contact_jumps(track, fps)
        viol = sorted({str(v) for f in track for v in (f.get("violations") or [])})
        out = {"fps": fps, "frames": len(track), "continuity": cont, "authored_steps": stepped,
               "feet": feet, "foot_sliding_px": slide, "foot_sliding": "PASS" if slide <= SLIDE_TOL_PX else "FAIL",
               "contact_sliding_px": round(max(contacts), 4) if contacts else None,
               "contact_sliding": ("PASS" if not contacts or max(contacts) <= CONTACT_TOL_PX else "FAIL"),
               "contact_transition_jumps": jumps,
               "joint_limit_violations": viol}
        ok = (not cont["velocity_jumps"] and not cont["solver_events"] and out["foot_sliding"] == "PASS"
              and out["contact_sliding"] == "PASS" and not jumps and not viol)
        out["verdict"] = "PASS" if ok else "REVIEW"
        out["note"] = "deterministic motion checks; fast deliberate motion is not flagged"
        return out

    def _contact_jumps(self, track: list[dict[str, Any]], fps: float) -> list[dict[str, Any]]:
        out = []
        for c in self.d.contacts.specs:
            if not c.part.startswith(("hand", "elbow")):
                continue
            s = c.part[-1]
            W = np.array([f["joints"][f"hand_{s}"] for f in track], float)
            v = np.hypot(*np.diff(W, axis=0).T)
            for a, b in ((c.start - c.acquire, c.start), (c.end, c.end + c.release)):
                i0, i1 = max(int(a * fps) - 2, 1), min(int(b * fps) + 2, len(v) - 2)
                for i in range(i0, i1):
                    nb = max(v[i - 1], v[i + 1], 0.25)
                    if v[i] > JUMP_RATIO * nb:
                        out.append({"contact": c.id, "t": round(i / fps, 3), "step_px": round(float(v[i]), 3),
                                    "neighbours_px": round(float(nb), 3)})
        return out

    # -- artistic ------------------------------------------------------------------------------------------------
    def artistic(self, fps: float = 12.0, frames: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        d = self.d
        keys = d.base.keys
        checks: list[dict[str, Any]] = []
        for i, k in enumerate(keys):
            if k.interp == "stop" and k.hold < MIN_HOLD_S and 0 < i < len(keys) - 1:
                checks.append({"check": "key_pose_hold", "key": k.label, "hold_s": k.hold, "level": "info",
                               "message": "short hold: the pose may not read (fine for a passing key)"})
        for b in range(1, len(keys)):
            kb, ka = keys[b], keys[b - 1]
            seg = d.base.move_len(b)
            if seg < 0.25:
                checks.append({"check": "timing", "key": kb.label, "move_s": round(seg, 3), "level": "warning",
                               "message": "very fast move: check that it reads"})
            moves = {ch: d.base._val(b, ch) - d.base._val(b - 1, ch) for ch in d.base.channels}
            big = max((abs(v) for ch, v in moves.items() if group_of(ch) in ("arms", "forearms")), default=0.0)
            has_antic = bool(d.antic.recipe(kb.anticipation)["anticipation"])
            if not has_antic and big >= 60:
                checks.append({"check": "anticipation", "key": kb.label, "level": "info", "arm_move_deg": round(big, 1),
                               "message": "large arm move without anticipation (an artist choice; consider a recipe)"})
            if has_antic and big < 8 and abs(moves.get("weight_left", 0)) < 0.05:
                checks.append({"check": "anticipation", "key": kb.label, "level": "warning",
                               "message": "anticipation on a subtle move"})
            if kb.overshoot > MAX_FRACTION:
                checks.append({"check": "follow_through", "key": kb.label, "level": "warning",
                               "message": f"overshoot {kb.overshoot} is above the cap {MAX_FRACTION} (capped)"})
        settle = []
        for b in range(1, len(keys)):
            kb = keys[b]
            if kb.overshoot > 0 and kb.hold > 0:
                end = kb.t + kb.hold - 1e-3
                dev = max(abs(d.base.value(ch, end) - d.base._val(b, ch)) for ch in d.base.channels)
                settle.append({"key": kb.label, "residual_at_hold_end": round(dev, 4)})
        sec = d.secondary.report()
        sat = {}
        for p in ("hair", "hem"):
            s = sec["parts"].get(p, {})
            if s.get("enabled"):
                lim = float(s["limit_deg"])
                vals = np.array([abs(d.secondary.value(p, t)) for t in np.arange(0, d.duration, 1 / 48)])
                sat[p] = round(float((vals > 0.9 * lim).mean()), 3)
        for p, share in sat.items():
            if share > 0.25:
                checks.append({"check": "secondary_excess", "part": p, "level": "warning",
                               "message": f"{p} spends {share:.0%} of the time near its limit"})
        hands = {"pending_frames": 0, "missing_frames": 0, "poses": {}}
        weight = {"com_outside_max_px": 0.0, "unclear_weight_holds": []}
        tr = frames if frames is not None else d.track(fps)
        for f in tr:
            hp = f["performance"]["hands"]
            if any(v["status"] == "pending_review" for v in hp.values()):
                hands["pending_frames"] += 1
            if any(v["status"] == "missing" for v in hp.values()):
                hands["missing_frames"] += 1
            for s, v in hp.items():
                hands["poses"].setdefault(f"{v['shown']}:{v['status']}", 0)
                hands["poses"][f"{v['shown']}:{v['status']}"] += 1
            com, sup = f.get("com"), (f.get("support") or {}).get("x_range")
            if com is not None and sup:
                weight["com_outside_max_px"] = max(weight["com_outside_max_px"],
                                                   round(float(max(sup[0] - com[0], com[0] - sup[1], 0.0)), 2))
        for k in keys:
            if k.hold >= MIN_HOLD_S and abs(k.params.get("weight_left", 0.5) - 0.5) < 0.05 and "neutral" not in k.label:
                weight["unclear_weight_holds"].append(k.label)
        return {"checks": checks, "follow_through_settle": settle, "secondary": {"time_near_limit": sat, **sec},
                "hands": hands, "weight": weight,
                "gestures": {g: self.d.library.status(g, "r") for g in self.d.library.names()},
                "artistic_review_required": True,
                "note": "checks for a reviewer; artistic quality is approved by a person, never by these numbers"}

    # -- geometric ------------------------------------------------------------------------------------------------
    @staticmethod
    def geometric(renders: list[dict[str, Any]]) -> dict[str, Any]:
        gaps = [len((r.get("tears") or {}).get("exposed_gap", [])) for r in renders]
        clipped = [int(sum(((r.get("render") or {}).get("clipped_uncovered") or {}).values())) for r in renders]
        return {"frames": len(renders), "exposed_gaps_max": max(gaps) if gaps else 0,
                "clipped_open_px_max": max(clipped) if clipped else 0}


__all__ = ["PerformanceQualityEvaluator", "SLIDE_TOL_PX", "CONTACT_TOL_PX"]
