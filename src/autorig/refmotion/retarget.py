"""ReferenceMotionRetargeter: analysed reference motion -> a Performance for the one motion pipeline.

Mapping:
- the analyzer's channels are front-view anatomical angles. The character gets its own neutral plus ``intensity``
  times the reference's change from its own first frame (``rig = neutral + k * (ref - ref0)``). Human proportions are
  never transferred: bone lengths, pivots and the character's neutral stay the rig's, and only angles move;
- keys are placed on the analyzer's phase boundaries and hold intervals, not on every frame, so the sequencer's
  easing gives natural timing; ``timing_scale`` stretches the times;
- hand intentions from the reference metadata become hand *actions* on the keys where that arm is away from
  neutral. The NaturalHandSelectionEngine then chooses the drawings under its hard constraints (palm, handedness,
  approval), so a palm-up reference never forces an unsuitable drawing;
- limits, contacts, balance, the garment rig and switches are the existing pipeline's
  (``PerformanceDirector`` -> ``MotionEngine.pose``). Parameters are soft-clamped to the anatomical ranges there.

Feasibility:
- segments the analyzer marks as moving in depth (foreshortening) cannot be shown by the front-view art. Their
  in-plane component is transferred, nothing is stretched, and the report names the missing view/artwork:
  REQUIRES_ALTERNATIVE_ARTWORK (forearms toward the camera), REQUIRES_NEW_VIEW (stride, knee drive);
- a locomotion reference (walking) keeps only its in-place components: weight shift and pelvis roll. The stride is
  REQUIRES_NEW_VIEW and is never claimed as transferred.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .analyze import CHANNELS

NEUTRAL = {"weight_left": 0.5, "contrapposto": 0.3, "line_of_action": 0.0, "head": 0.0, "free_knee": 6.0,
           "clavicle_r": -2.0, "clavicle_l": -1.0, "arm_r_abduction": 11.0, "arm_r_flexion": 5.0, "arm_r_wrist": 3.0,
           "arm_l_abduction": 9.0, "arm_l_flexion": 6.0, "arm_l_wrist": 4.0}
DEPTH_ARTWORK = {"forearm": ("REQUIRES_ALTERNATIVE_ARTWORK", "art:forearm_foreshortened_{s}"),
                 "upper_arm": ("REQUIRES_ALTERNATIVE_ARTWORK", "art:upper_arm_foreshortened_{s}"),
                 "shin": ("REQUIRES_NEW_VIEW", "view:leg_sagittal (knee drive / stride)"),
                 "thigh": ("REQUIRES_NEW_VIEW", "view:leg_sagittal (knee drive / stride)")}
ARM_AWAY_DEG = 15.0


class ReferenceMotionRetargeter:
    def __init__(self, neutral: dict[str, float] | None = None):
        self.neutral = {**NEUTRAL, **(neutral or {})}

    def target_curves(self, analysis: dict[str, Any], intensity: float = 1.0,
                      channels: list[str] | None = None) -> dict[str, np.ndarray]:
        ch = {k: np.asarray(v, float) for k, v in analysis["channels"].items()}
        out = {}
        for c in channels or CHANNELS:
            if c not in ch:
                continue
            x = ch[c]
            if c == "pelvis_roll":
                continue  # carried by contrapposto + weight_left in the rig
            if c == "weight_left":
                out[c] = np.clip(self.neutral[c] + intensity * (x - x[0]), 0.16, 0.84)
            else:
                out[c] = self.neutral.get(c, 0.0) + intensity * (x - x[0])
        if "pelvis_roll" in ch:
            pr = ch["pelvis_roll"]
            out["contrapposto"] = np.clip(self.neutral["contrapposto"] + intensity * np.abs(pr - pr[0]) / 7.0, 0.0, 1.0)
        return out

    def key_times(self, analysis: dict[str, Any]) -> list[float]:
        ts = {0.0, float(analysis["duration"])}
        for ph in analysis["timing"]["phases"]:
            ts |= {float(ph["t0"]), float(ph["t1"])}
        for h in analysis["timing"]["holds"]:
            ts |= {float(h[0]), float(h[1])}
        out = []
        for t in sorted(ts):
            if not out or t - out[-1] >= 0.1:
                out.append(t)
        return out

    def feasibility(self, analysis: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
        items = []
        for seg in analysis.get("depth_segments", []):
            kind, s = seg.rsplit("_", 1)
            cls, need = DEPTH_ARTWORK.get(kind, ("REQUIRES_NEW_VIEW", f"view:{seg}"))
            frac = analysis["foreshortening"][seg]["foreshortened_frac"]
            items.append({"segment": seg, "class": cls, "needs": need.replace("{s}", s), "frames_frac": frac,
                          "handling": "in-plane component transferred; the limb is not stretched or shortened"})
        loco = meta.get("category") == "locomotion"
        if loco:
            items.append({"segment": "legs", "class": "REQUIRES_NEW_VIEW", "needs": "view:leg_sagittal (stride)",
                          "frames_frac": 1.0, "handling": "only the in-place weight shift and pelvis roll are "
                                                             "transferred; the stride is not rendered"})
        order = ["SUPPORTED", "SUPPORTED_WITH_CORRECTIONS", "REQUIRES_ALTERNATIVE_ARTWORK", "REQUIRES_NEW_VIEW"]
        cls = max([i["class"] for i in items] or ["SUPPORTED"], key=order.index)
        return {"class": cls, "items": items, "locomotion": loco}

    def retarget(self, analysis: dict[str, Any], meta: dict[str, Any], intensity: float = 1.0,
                 timing_scale: float = 1.0, name: str | None = None, segment: tuple[float, float] | None = None,
                 hand_actions: dict[str, str] | None = None, allow_pending: bool = True) -> dict[str, Any]:
        from ..performance.director import Performance  # noqa: PLC0415
        from ..performance.sequencer import Key  # noqa: PLC0415

        fps = float(analysis["fps"])
        curves = self.target_curves(analysis, intensity)
        n = len(next(iter(curves.values())))
        t = np.arange(n) / fps
        t0, t1 = segment if segment else (0.0, float(t[-1]))
        times = [x for x in self.key_times(analysis) if t0 - 1e-9 <= x <= t1 + 1e-9]
        if not times or times[0] > t0 + 1e-9:
            times = [t0] + times
        if times[-1] < t1 - 1e-9:
            times.append(t1)
        acts = {**(meta.get("hand_actions") or {}), **(hand_actions or {})}
        keys = []
        for i, tk in enumerate(times):
            k = int(round(tk * fps))
            k = min(max(k, 0), n - 1)
            params = {c: round(float(v[k]), 3) for c, v in curves.items()}
            params["free_knee"] = self.neutral["free_knee"]
            hands = {}
            for s in ("r", "l"):
                a = acts.get(s)
                if not a:
                    continue
                away = abs(params.get(f"arm_{s}_abduction", 0) - self.neutral[f"arm_{s}_abduction"]) > ARM_AWAY_DEG or \
                    abs(params.get(f"arm_{s}_flexion", 0) - self.neutral[f"arm_{s}_flexion"]) > ARM_AWAY_DEG
                hands[s] = a if (away or meta.get("category") == "locomotion") else "relaxed_standing"
            # a hold is the pair of keys at its two ends (same pose): no extra hold time is added
            keys.append(Key(round((tk - t0) * timing_scale, 4), params, hands, f"ref {i}", 0.0,
                            anticipation="none", id=f"r{i}").to_json())
        cleaned = keys
        perf = Performance(name=name or f"reference_{meta.get('name', 'motion')}", fps=12.0, base_keys=cleaned,
                           allow_pending=allow_pending,
                           notes=[f"retargeted from reference {meta.get('name')!r} ({(meta.get('provenance') or {}).get('kind', 'unknown')}"
                                  f" provenance); intensity {intensity}, timing x{timing_scale}",
                                  "joint angles only: the character's proportions, pivots and neutral are kept"])
        feas = self.feasibility(analysis, meta)
        if feas["locomotion"]:
            perf.notes.append("locomotion: only the in-place weight shift / pelvis roll is transferred "
                              "(stride REQUIRES_NEW_VIEW)")
        return {"performance": perf.to_json(), "keys": len(cleaned), "feasibility": feas,
                "settings": {"intensity": intensity, "timing_scale": timing_scale, "segment": [t0, t1],
                             "hand_actions": acts},
                "target_curves": {c: np.round(v, 3).tolist() for c, v in curves.items()}}

    @staticmethod
    def compare(director, target: dict[str, list[float]], fps: float, timing_scale: float = 1.0,
                t0: float = 0.0) -> dict[str, Any]:
        """RMS / max error per channel between the retargeted performance's parameters and the target curves
        (the performance is sampled at the reference frames)."""
        out = {}
        n = len(next(iter(target.values())))
        ts = [(i / fps - t0) * timing_scale for i in range(n)]
        vals = [director.layers.params_at(max(0.0, t))[0] for t in ts]
        for c, tgt in target.items():
            got = np.array([v.get(c, np.nan) for v in vals], float)
            ok = ~np.isnan(got)
            if not ok.any():
                continue
            e = got[ok] - np.asarray(tgt, float)[ok]
            out[c] = {"rms": round(float(np.sqrt(np.mean(e ** 2))), 3), "max": round(float(np.abs(e).max()), 3)}
        return out


__all__ = ["ReferenceMotionRetargeter", "NEUTRAL"]
