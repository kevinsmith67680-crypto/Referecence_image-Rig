"""MotionLayerController: independently controllable motion channels, composed in a fixed order.

Composition order (top of the list first; each step sees the result of the ones before):

| Layer | Mode | Channels |
|---|---|---|
| BODY_BASE | override (the base key track) | every pose parameter |
| ROOT_MOVEMENT | additive (idle sway, breathing, style posture) | pelvis/weight/torso/shoulders |
| UPPER_BODY_GESTURES | override, masked, weighted, anchored clips | arms, shoulders, torso |
| HEAD_MOVEMENT | additive (look-at, emphasis) | head |
| CONTACT_CONSTRAINTS | IK targets after the parameters | hand/elbow/foot/pelvis chains |
| HAND_SWITCHES | discrete per side: gesture clips, then artist markers | hand actions |
| GARMENT_CORRECTIONS | render settings | corrective mode and overrides |
| SECONDARY_MOTION | garment terms from the springs | hair, hem |
| FUTURE_FACIAL_CONTROLS | isolated: facial channel names only | brows, blinks, mouth, jaw |

How the layers combine:
- **Joint space.** Values are joint parameters: angles in degrees and weight shares. An override blends
  ``v + w * (clip - v)``. An additive layer adds ``w * delta``. Positions are never averaged. Contacts act after
  the parameters, so a gesture can never move a contacted hand.
- **Anchors.** An override clip may anchor its first and last keys to the stack below it (filled in at the clip's
  start and end times). The gesture then leaves and returns to the current pose. Because the blend weight ramps with
  a C2 curve, the composed motion keeps continuous velocity at clip boundaries.
- **Soft limits.** After composition, every parameter is soft-clamped to its anatomical range: overshoot bends
  smoothly toward the limit and never crosses it.
- **Facial isolation.** The facial layer may only hold facial channels. It never changes a body parameter and is
  reported separately, ready for a future face rig or tracker.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .anticipation import AnticipationAndFollowThroughController
from .curves import ramp, soft_clamp
from .overlap import MotionOverlapController
from .sequencer import Key, KeyPoseSequencer

LAYERS = ("BODY_BASE", "ROOT_MOVEMENT", "UPPER_BODY_GESTURES", "HEAD_MOVEMENT", "CONTACT_CONSTRAINTS",
          "HAND_SWITCHES", "GARMENT_CORRECTIONS", "SECONDARY_MOTION", "FUTURE_FACIAL_CONTROLS")
POSE_LAYERS = ("BODY_BASE", "ROOT_MOVEMENT", "UPPER_BODY_GESTURES", "HEAD_MOVEMENT")
DEFAULT_MODE = {"ROOT_MOVEMENT": "additive", "UPPER_BODY_GESTURES": "override", "HEAD_MOVEMENT": "additive",
                "FUTURE_FACIAL_CONTROLS": "override"}
FACIAL_CHANNELS = ("brow_l", "brow_r", "blink_l", "blink_r", "mouth_open", "mouth_smile", "jaw", "eye_look_x",
                   "eye_look_y")
LAYER_MASK = {
    "ROOT_MOVEMENT": ("weight_left", "contrapposto", "free_knee", "pelvis_dy", "line_of_action", "clavicle_r",
                      "clavicle_l"),
    "HEAD_MOVEMENT": ("head",),
}
# anatomical ranges of the pose parameters (soft-clamped with a knee), from the solver limits where they exist
RANGES = {"weight_left": (0.16, 0.84, 0.05), "contrapposto": (0.0, 1.3, 0.1), "line_of_action": (-14.0, 14.0, 3.0),
          "head": (-22.0, 22.0, 4.0), "free_knee": (0.0, 30.0, 4.0), "pelvis_dy": (0.0, 0.42, 0.04),
          "clavicle_r": (-10.0, 26.0, 4.0), "clavicle_l": (-10.0, 26.0, 4.0)}
ARM_RANGES = {"abduction": (-30.0, 176.0, 8.0), "flexion": (-140.0, 140.0, 10.0), "wrist": (-58.0, 58.0, 8.0)}


def param_range(ch: str, limits: dict[str, Any] | None = None) -> tuple[float, float, float] | None:
    if ch in RANGES:
        return RANGES[ch]
    if ch.startswith("arm_"):
        kind = ch.rsplit("_", 1)[1]
        lo, hi, knee = ARM_RANGES.get(kind, (-180.0, 180.0, 10.0))
        if limits and kind == "abduction" and "upper_arm" in limits and "abduction" in limits["upper_arm"]:
            l0, l1 = limits["upper_arm"]["abduction"][:2]
            lo, hi = max(lo, float(l0) + 2.0), min(hi, float(l1) - 2.0)
        return lo, hi, knee
    return None


@dataclass
class Clip:
    id: str
    layer: str
    start: float
    keys: list[Key]  # times relative to ``start``
    mode: str | None = None
    mask: list[str] | None = None
    weight: float = 1.0
    blend_in: float = 0.12
    blend_out: float = 0.12
    gesture: str | None = None
    side: str | None = None
    anchor_start: bool = True
    anchor_end: bool = True
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.layer not in POSE_LAYERS + ("FUTURE_FACIAL_CONTROLS",):
            raise ValueError(f"clips go on a pose layer or the facial layer, not {self.layer!r}")
        self.mode = self.mode or DEFAULT_MODE.get(self.layer, "override")
        if self.mode not in ("override", "additive"):
            raise ValueError(f"unknown blend mode {self.mode!r}")
        chans = sorted(set().union(*[set(k.params) for k in self.keys])) if self.keys else []
        if self.layer == "FUTURE_FACIAL_CONTROLS":
            bad = [c for c in chans if c not in FACIAL_CHANNELS]
            if bad:
                raise ValueError(f"the facial layer may only hold facial channels, not {bad}")
        elif any(c in FACIAL_CHANNELS for c in chans):
            raise ValueError("facial channels belong on FUTURE_FACIAL_CONTROLS")
        allowed = LAYER_MASK.get(self.layer)
        if allowed is not None:
            bad = [c for c in chans if c not in allowed]
            if bad:
                raise ValueError(f"{self.layer} cannot drive {bad} (allowed: {', '.join(allowed)})")
        self.mask = list(self.mask) if self.mask else chans

    @property
    def end(self) -> float:
        return self.start + (max(k.t + k.hold for k in self.keys) if self.keys else 0.0)

    def envelope(self, t: float) -> float:
        if t < self.start or t > self.end:
            return 0.0
        return self.weight * ramp(t, self.start, self.start + self.blend_in) * (
            1.0 - ramp(t, self.end - self.blend_out, self.end))

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "layer": self.layer, "start": self.start, "keys": [k.to_json() for k in self.keys],
                "mode": self.mode, "mask": self.mask, "weight": self.weight, "blend_in": self.blend_in,
                "blend_out": self.blend_out, "gesture": self.gesture, "side": self.side,
                "anchor_start": self.anchor_start, "anchor_end": self.anchor_end, "meta": copy.deepcopy(self.meta)}

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Clip":
        d = dict(d)
        d["keys"] = [Key.from_json(k) for k in d.get("keys", [])]
        return Clip(**{k: v for k, v in d.items() if k in Clip.__dataclass_fields__})


class MotionLayerController:
    def __init__(self, base: KeyPoseSequencer, clips: list[Clip] | None = None,
                 settings: dict[str, dict[str, Any]] | None = None, limits: dict[str, Any] | None = None,
                 overlap: MotionOverlapController | None = None,
                 anticipation: AnticipationAndFollowThroughController | None = None,
                 hand_markers: list[dict[str, Any]] | None = None):
        self.base = base
        self.settings = {name: {"mute": False, "weight": 1.0} for name in LAYERS}
        for name, s in (settings or {}).items():
            if name not in LAYERS:
                raise ValueError(f"unknown motion layer {name!r}")
            self.settings[name].update(s)
        self.limits = limits
        self.overlap = overlap or base.overlap
        self.antic = anticipation or base.antic
        order = {n: i for i, n in enumerate(LAYERS)}
        self.clips = sorted(clips or [], key=lambda c: (order[c.layer], c.start, c.id))
        self.hand_markers = sorted(hand_markers or [], key=lambda m: m["t"])
        self._seq: dict[str, KeyPoseSequencer] = {}

    # -- clip sequencers with anchors filled from the stack below -------------------------------------------------
    def _clip_seq(self, idx: int) -> KeyPoseSequencer:
        c = self.clips[idx]
        if c.id in self._seq:
            return self._seq[c.id]
        keys = [copy.deepcopy(k) for k in c.keys]
        if c.mode == "override" and keys:
            if c.anchor_start:
                below = self._compose(c.start, idx)[0]
                keys[0].params = {ch: below.get(ch, 0.0) for ch in c.mask}
            if c.anchor_end:
                below = self._compose(c.end, idx)[0]
                keys[-1].params = {ch: below.get(ch, 0.0) for ch in c.mask}
        seq = KeyPoseSequencer(keys, self.overlap, self.antic)
        self._seq[c.id] = seq
        return seq

    def _compose(self, t: float, upto: int | None = None) -> tuple[dict[str, float], dict[str, str], dict[str, Any]]:
        s = self.base.sample(t)
        v = dict(s["params"]) if not self.settings["BODY_BASE"]["mute"] else dict(self.base.sample(0.0)["params"])
        hands = dict(s["hands"])
        facial: dict[str, float] = {}
        active = []
        n = len(self.clips) if upto is None else upto
        for i in range(n):
            c = self.clips[i]
            st = self.settings[c.layer]
            if st["mute"] or not (c.start <= t <= c.end):
                continue
            w = c.envelope(t) * float(st["weight"])
            if w <= 0:
                continue
            seq = self._clip_seq(i)
            cs = seq.sample(t - c.start)
            if c.layer == "FUTURE_FACIAL_CONTROLS":
                for ch in c.mask:
                    facial[ch] = facial.get(ch, 0.0) + w * (cs["params"].get(ch, 0.0) - facial.get(ch, 0.0))
                continue
            for ch in c.mask:
                cv = cs["params"].get(ch)
                if cv is None:
                    continue
                if c.mode == "additive":
                    v[ch] = v.get(ch, 0.0) + w * cv
                else:
                    cur = v.get(ch, 0.0)
                    v[ch] = cur + w * (cv - cur)
            if c.layer == "UPPER_BODY_GESTURES" and w >= 0.5 and cs["hands"]:
                hands.update({sd: a for sd, a in cs["hands"].items() if c.side in (None, sd)})
            active.append({"clip": c.id, "layer": c.layer, "w": round(w, 4), "gesture": c.gesture})
        if upto is None and not self.settings["HAND_SWITCHES"]["mute"]:
            for m in self.hand_markers:  # artist overrides win
                if m["t"] <= t and (m.get("until") is None or t < m["until"]):
                    hands[m["side"]] = m["action"]
        return v, hands, {"active_clips": active, "facial": facial}

    def params_at(self, t: float) -> tuple[dict[str, float], dict[str, str], dict[str, Any]]:
        v, hands, info = self._compose(t)
        clamped = {}
        for ch, x in v.items():
            r = param_range(ch, self.limits)
            if r is not None:
                y = soft_clamp(x, *r)
                if abs(y - x) > 1e-6:
                    clamped[ch] = round(x - y, 4)
                v[ch] = y
        info["soft_limited"] = clamped
        return v, hands, info

    def to_json(self) -> dict[str, Any]:
        return {"order": list(LAYERS), "settings": self.settings, "clips": [c.to_json() for c in self.clips],
                "hand_markers": self.hand_markers, "facial_channels": list(FACIAL_CHANNELS)}


__all__ = ["MotionLayerController", "Clip", "LAYERS", "FACIAL_CHANNELS", "param_range", "RANGES"]
