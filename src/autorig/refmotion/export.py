"""Phase 3.7 Moho-oriented metadata (``proposed_unverified``) and the benchmark (``autorig phase37-bench``).

The metadata describes what a Phase 4 importer would need to rebuild the hand switches and reference-driven gestures
in Moho:
- switch layers per side: every drawing with its layer, approval, handedness, palm class at rest and rotation
  tolerance. A production switch contains approved drawings and the original only;
- wrist bone attachment: the pivot of each drawing in rest pixels and in the Moho frame (``autorig.geometry``
  conventions);
- gesture Actions: one proposed Action per gesture, with bone-angle keyframes taken from skeleton-only solves at the
  performance's key times (reference keyframes);
- overrides: the user's explicit switch choices;
- pose-specific art: drawings still needed (NEEDS_ARTWORK) and approvals pending.

Conversion and baking: the PerformanceDirector timeline can be baked to per-frame bone angles (``theta_deg``,
already exported by Phase 3.6 ``animation_metadata``). Switch keys become Moho switch-layer keys at the decided frames.
No Moho API name is used here and no file is written for Moho: everything is ``proposed_unverified`` until the
Phase 0 importer has run in Moho.
"""

from __future__ import annotations

import platform
import time
from typing import Any

import numpy as np

from ..geometry import px_to_world
from ..handmeta import HandOrientationAnalyzer, approval_state

STATUS = "proposed_unverified"


def hand_switch_metadata(eng, user_switches: dict[str, Any] | None = None) -> dict[str, Any]:
    H, W = next(iter(eng.layers.values())).shape[:2]
    an = HandOrientationAnalyzer()
    out: dict[str, Any] = {"status": STATUS, "native_moho": "not_implemented", "sides": {}}
    for s in ("r", "l"):
        rows = []
        for pose, cands in eng.hand_lib.candidates[s].items():
            for v in cands:
                if v.layer is None:
                    continue
                hm = (v.meta or {}).get("hand_meta") or {}
                a0 = ((v.placement or {}).get("final") or {}).get("angle_deg")
                piv = ((v.placement or {}).get("params") or {}).get("pivot_px")
                wrist = eng.sk.bone(f"hand_{s}").head
                rows.append({"pose": pose, "cid": v.cid, "layer": v.layer, "approval": approval_state(v.status),
                             "handedness": hm.get("handedness"), "reassigned": hm.get("reassigned", False),
                             "palm_at_rest": an.analyze(hm, a0 or 0.0)["palm_class"] if hm else "AMBIGUOUS",
                             "rotation_tolerance_deg": hm.get("rotation_tolerance_deg"),
                             "attachment": {"bone": f"hand_{s}", "wrist_px": [round(float(x), 2) for x in wrist],
                                            "wrist_moho": [round(float(x), 5) for x in px_to_world(tuple(wrist), W, H)],
                                            "drawing_pivot_px": piv, "rest_angle_deg": a0},
                             "production_eligible": approval_state(v.status) == "APPROVED"})
        out["sides"][s] = {"group": f"hand_{s}", "variants": rows,
                           "production_switch": [r["pose"] for r in rows if r["production_eligible"]],
                           "override": (user_switches or {}).get(f"hand_{s}")}
    return out


def gesture_actions(director, name: str) -> dict[str, Any]:
    """A proposed Moho Action from a performance: bone angles at the key times (skeleton-only solves)."""
    keys = []
    times = sorted({round(float(k.t), 4) for k in director.base.keys})
    names = [b.name for b in director.eng.sk.bones]
    for t in times:
        r = director.solve(t)
        th = r["pose"].theta
        keys.append({"t": t, "frame_24fps": int(round(t * 24)),
                     "bones_deg": {n: round(float(th[i]), 3) for i, n in enumerate(names) if abs(th[i]) > 1e-6},
                     "hands": {s: r["hands"][s]["pose"] for s in ("r", "l")},
                     "hand_status": {s: approval_state(r["hands"][s].get("status")) for s in ("r", "l")}})
    return {"action": f"AR_{name}", "status": STATUS, "keys": keys,
            "baking": "per-frame theta_deg via Phase 3.6 animation_metadata; switch keys at the decided frames",
            "convention": "degrees relative to rest, about the bone head (rig 'ang' convention; Moho sign and "
                          "units to be verified in Phase 4)"}


def phase37_metadata(eng, vis, user_switches: dict[str, Any] | None = None) -> dict[str, Any]:
    from ..performance import scenarios  # noqa: PLC0415
    from ..performance.director import PerformanceDirector  # noqa: PLC0415

    acts = {}
    for name, perf in (("shrug_subtle", scenarios.acting_upgrade()),
                       ("shrug_expressive", scenarios.acting_upgrade(shrug="expressive"))):
        perf.allow_pending = False
        acts[name] = gesture_actions(PerformanceDirector(eng, vis, perf), name)
    return {"schema": "autorig.phase37_moho_metadata", "version": "1.0.0", "status": STATUS,
            "switches": hand_switch_metadata(eng, user_switches), "actions": acts,
            "pose_specific_art": {"needs_artwork": ["hand:palm_up_r", "hand:palm_up_l", "hand:relaxed_partially_closed",
                                                    "art:forearm_foreshortened_{r,l}", "view:leg_sagittal",
                                                    "art:shoulder_arms_down_{r,l} (optional: sloped shoulder line)"],
                                  "needs_approval": "every stored hand drawing (16) is PENDING_REVIEW"},
            "disclaimer": "no Moho API names, no Moho file; nothing here has run in Moho"}


def benchmark(eng, vis, n: int = 20) -> dict[str, Any]:
    """Timings on this machine: hand selection, IK/skeleton solve, retarget, critic, render."""
    from ..artistic.transitions import acting_test_keys  # noqa: PLC0415
    from ..performance import scenarios  # noqa: PLC0415
    from ..performance.director import Performance, PerformanceDirector  # noqa: PLC0415
    from .analyze import ReferenceMotionAnalyzer  # noqa: PLC0415
    from .critic import VisualAnimationCritic  # noqa: PLC0415
    from .retarget import ReferenceMotionRetargeter  # noqa: PLC0415
    from .track import synthetic_reference  # noqa: PLC0415

    t = next(k.t for k in acting_test_keys() if k.label == "shrug") + 0.3
    d = PerformanceDirector(eng, vis, scenarios.acting_upgrade(shrug="expressive"))
    d.solve(t)
    r = d.solve(t)
    from ..motion.handselect import context_from_pose  # noqa: PLC0415

    def time_it(fn, k=n):
        ts = []
        for _ in range(k):
            t0 = time.perf_counter()
            fn()
            ts.append(time.perf_counter() - t0)
        return {"median_ms": round(1000 * float(np.median(ts)), 3), "p90_ms": round(1000 * float(np.percentile(ts, 90)), 3)}

    it, pose = r["intent"] if "intent" in r else None, r["pose"]
    ctxs = {s: context_from_pose(eng.solver, eng.sk, pose, it, s, "shrugging") for s in ("r", "l")}
    out = {"machine": {"platform": platform.platform(), "processor": platform.processor() or platform.machine(),
                       "python": platform.python_version()},
           "hand_selection_pair": time_it(lambda: eng.hand_select.select_pair(eng.hand_lib, ctxs, None, True)),
           "skeleton_solve_with_hands": time_it(lambda: d.solve(t), 10)}
    tr = synthetic_reference("pointing")
    out["analysis_synthetic_2.8s"] = time_it(lambda: ReferenceMotionAnalyzer().analyze(tr), 5)
    an = ReferenceMotionAnalyzer().analyze(tr)
    out["retarget"] = time_it(lambda: ReferenceMotionRetargeter().retarget(an, tr.meta), 5)
    t0 = time.perf_counter()
    rr = d.solve(t, render=True)
    out["full_render_s"] = round(time.perf_counter() - t0, 3)
    out["critic_rules_per_frame"] = time_it(lambda: VisualAnimationCritic().critique(
        {"eng": eng, "result": rr, "params": {}, "mode": "review"}), 5)
    out["note"] = ("pose estimation timing comes from the MediaPipe round trip (reference-evidence); it runs at "
                   "import only, never during interactive animation. This is the cloud sandbox CPU, not the user's M1.")
    del Performance
    return out


__all__ = ["phase37_metadata", "hand_switch_metadata", "gesture_actions", "benchmark", "STATUS"]
