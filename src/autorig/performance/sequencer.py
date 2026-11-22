"""KeyPoseSequencer: key poses on a timeline, evaluated at any time.

A key holds:
- pose parameters (the ``ArtisticPose`` numeric parameters, see ``artistic.transitions.params_of``);
- hand actions per side;
- a hold;
- the timing curve of the move into it, with optional per-channel or per-group curves;
- an anticipation recipe and intensity;
- an overshoot;
- overlap overrides;
- an interpolation mode:
  - ``stop``: the move eases into the key and rests there;
  - ``path``: the motion passes through the key with continuous velocity, through a monotone Hermite over the run
    of path keys, so it never overshoots between keys.

Value of a channel inside the move from key ``a`` to key ``b``:

    v = v_a + (v_b - v_a) * curve(warp(u, overlap))       (stop -> stop)
      + anticipation bump (recipe of b, early in the move)
      + overshoot of b (around arrival, settling in b's hold)
      + settle of a's overshoot (if it is still running)

The sequencer returns raw values. The MotionLayerController applies the anatomical soft limits after composing the
layers. Evaluation is a pure function of ``t``, independent of the frame rate and of evaluation order.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .anticipation import AnticipationAndFollowThroughController
from .curves import Curve, MonotoneHermite
from .overlap import MotionOverlapController, group_of


@dataclass
class Key:
    t: float
    params: dict[str, float]
    hands: dict[str, str] = field(default_factory=dict)
    label: str = ""
    hold: float = 0.0
    curve: Curve = field(default_factory=Curve)
    curves: dict[str, Curve] = field(default_factory=dict)  # channel or group name -> curve (the move into this key)
    anticipation: str | None = None
    anticipation_intensity: float = 1.0
    overshoot: float = 0.0
    side: str | None = None  # gesture side for the recipes
    offsets: dict[str, float] = field(default_factory=dict)
    head_lead: bool = False
    interp: str = "stop"
    hand_switch_at: float = 0.5
    pose: dict[str, Any] = field(default_factory=dict)  # non-numeric ArtisticPose fields (base preset, front ...)
    id: str = ""

    def curve_for(self, param: str) -> Curve:
        return self.curves.get(param) or self.curves.get(group_of(param)) or self.curve

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "t": self.t, "params": dict(self.params), "hands": dict(self.hands), "label": self.label,
                "hold": self.hold, "curve": self.curve.to_json(),
                "curves": {k: c.to_json() for k, c in self.curves.items()}, "anticipation": self.anticipation,
                "anticipation_intensity": self.anticipation_intensity, "overshoot": self.overshoot,
                "side": self.side, "offsets": dict(self.offsets), "head_lead": self.head_lead, "interp": self.interp,
                "hand_switch_at": self.hand_switch_at, "pose": copy.deepcopy(self.pose)}

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Key":
        d = dict(d)
        d["curve"] = Curve.from_json(d.get("curve"))
        d["curves"] = {k: Curve.from_json(v) for k, v in (d.get("curves") or {}).items()}
        return Key(**{k: v for k, v in d.items() if k in Key.__dataclass_fields__})


class KeyPoseSequencer:
    def __init__(self, keys: list[Key], overlap: MotionOverlapController | None = None,
                 anticipation: AnticipationAndFollowThroughController | None = None):
        if not keys:
            raise ValueError("a sequence needs at least one key")
        self.keys = sorted(keys, key=lambda k: k.t)
        for a, b in zip(self.keys, self.keys[1:]):
            if b.t <= a.t:
                raise ValueError(f"key times must increase ({a.label!r} at {a.t}, {b.label!r} at {b.t})")
            if a.t + a.hold > b.t - 1e-6:
                raise ValueError(f"the hold of key {a.label!r} ends after the next key starts")
            if b.interp not in ("stop", "path"):
                raise ValueError(f"unknown interpolation {b.interp!r}")
        self.overlap = overlap or MotionOverlapController()
        self.antic = anticipation or AnticipationAndFollowThroughController()
        self.channels = sorted(set().union(*[set(k.params) for k in self.keys]))
        self._runs: dict[tuple[str, int], MonotoneHermite] = {}
        self._build_runs()

    # -- construction ----------------------------------------------------------------------------------------
    def _val(self, i: int, ch: str) -> float:
        """Value of channel ``ch`` at key ``i``: keys that do not set a channel keep the previous key's value."""
        for j in range(i, -1, -1):
            if ch in self.keys[j].params:
                return float(self.keys[j].params[ch])
        for j in range(i + 1, len(self.keys)):
            if ch in self.keys[j].params:
                return float(self.keys[j].params[ch])
        return 0.0

    def _build_runs(self) -> None:
        """Monotone Hermite runs across consecutive ``path`` keys (velocity continuous through them)."""
        ks = self.keys
        i = 0
        while i < len(ks) - 1:
            j = i + 1
            while j < len(ks) - 1 and ks[j].interp == "path" and ks[j].hold == 0:
                j += 1
            if j > i + 1:  # at least one interior path key
                xs = [ks[i].t + ks[i].hold] + [ks[k].t for k in range(i + 1, j + 1)]
                for ch in self.channels:
                    ys = [self._val(k, ch) for k in range(i, j + 1)]
                    h = MonotoneHermite(xs, ys, end_slopes=(0.0, 0.0))
                    for k in range(i, j):
                        self._runs[(ch, k)] = h
            i = j

    # -- timing ------------------------------------------------------------------------------------------------
    @property
    def duration(self) -> float:
        last = self.keys[-1]
        return last.t + last.hold

    def move_len(self, b: int) -> float:
        a = self.keys[b - 1]
        return max(self.keys[b].t - (a.t + a.hold), 1e-6)

    def hold_frac(self, b: int) -> float:
        return self.keys[b].hold / self.move_len(b) if b > 0 else 0.0

    def segment(self, t: float) -> tuple[int, float]:
        """(index of the key we move away from, progress 0..1 of the move; 0 while holding that key)."""
        ks = self.keys
        if t < ks[0].t:
            return 0, 0.0
        for i in range(len(ks) - 1):
            if t < ks[i + 1].t:
                start = ks[i].t + ks[i].hold
                if t <= start:
                    return i, 0.0
                return i, (t - start) / (ks[i + 1].t - start)
        return len(ks) - 1, 0.0

    # -- evaluation --------------------------------------------------------------------------------------------
    def _settle(self, ch: str, b: int, t: float) -> float:
        """Rest of key ``b``'s overshoot after arrival (``t`` >= key time)."""
        kb = self.keys[b]
        if b == 0 or kb.overshoot <= 0:
            return 0.0
        if not self.antic.follows(kb.anticipation, kb.side, ch):
            return 0.0
        move = self._val(b, ch) - self._val(b - 1, ch)
        s = 1.0 + (t - kb.t) / self.move_len(b)
        return self.antic.overshoot_term(kb.overshoot, move, s, self.hold_frac(b), group_of(ch))

    def value(self, ch: str, t: float) -> float:
        i, u = self.segment(t)
        ks = self.keys
        if i >= len(ks) - 1 or (u == 0.0 and t >= ks[i].t):
            v = self._val(min(i, len(ks) - 1), ch)
            return v + self._settle(ch, min(i, len(ks) - 1), t) if t >= ks[0].t else v
        a, b = ks[i], ks[i + 1]
        va, vb = self._val(i, ch), self._val(i + 1, ch)
        move = vb - va
        L = self.overlap.offset(ch, b.offsets, b.head_lead)
        uu = MotionOverlapController.warp(u, L)
        run = self._runs.get((ch, i))
        if run is not None:
            v = run(t)
        else:
            v = va + move * b.curve_for(ch)(uu)
        g = group_of(ch)
        v += self.antic.anticipation_term(b.anticipation, b.side, ch, g, move, uu, b.anticipation_intensity)
        if b.overshoot > 0 and self.antic.follows(b.anticipation, b.side, ch):
            v += self.antic.overshoot_term(b.overshoot, move, u, self.hold_frac(i + 1), g)
        v += self._settle(ch, i, t)  # the previous key's overshoot, if it is still settling
        return v

    def hands(self, t: float) -> dict[str, str]:
        i, u = self.segment(t)
        ks = self.keys
        if i >= len(ks) - 1:
            return dict(ks[-1].hands)
        a, b = ks[i], ks[i + 1]
        out = dict(a.hands)
        if u >= b.hand_switch_at:
            out.update(b.hands)
        return out

    def sample(self, t: float) -> dict[str, Any]:
        i, u = self.segment(t)
        ks = self.keys
        nxt = ks[min(i + 1, len(ks) - 1)]
        cur = ks[i]
        holding = u == 0.0 and t >= cur.t
        pose_src = nxt if (not holding and u >= 0.5) else cur
        settle = any(abs(self._settle(ch, i, t)) > 1e-9 for ch in self.channels) if t >= cur.t else False
        return {"params": {ch: self.value(ch, t) for ch in self.channels}, "hands": self.hands(t),
                "pose": copy.deepcopy(pose_src.pose), "segment": [i, min(i + 1, len(ks) - 1)], "u": round(u, 4),
                "label": cur.label if holding or i >= len(ks) - 1 else f"{cur.label} -> {nxt.label}",
                "holding": holding, "settle": settle}

    def stepped_boundaries(self) -> list[dict[str, Any]]:
        """Authored discontinuities (``hold`` curves): reported as intended, never as defects."""
        out = []
        for b in range(1, len(self.keys)):
            kb = self.keys[b]
            for ch in self.channels:
                if kb.curve_for(ch).stepped and self._val(b, ch) != self._val(b - 1, ch):
                    out.append({"t": kb.t, "key": kb.label, "param": ch})
        return out

    def to_json(self) -> dict[str, Any]:
        return {"keys": [k.to_json() for k in self.keys], "overlap": self.overlap.to_json()}


__all__ = ["Key", "KeyPoseSequencer"]
