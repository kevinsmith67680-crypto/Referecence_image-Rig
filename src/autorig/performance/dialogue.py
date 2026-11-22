"""DialogueActingController: body acting for dialogue, driven by semantic events.

A future speech or audio system (not part of this phase: no speech recognition, no facial tracking) can trigger
gestures by event:

| Event | Body acting |
|---|---|
| SPEECH_EMPHASIS | a small beat: a forearm flick on the dominant side, no anticipation |
| QUESTION | an open explaining gesture (needs cupped or palm-up hands) |
| DISAGREEMENT | the disagree gesture |
| SURPRISE | the surprise reaction |
| POINT_AT_TARGET | a point toward the target's side, the head leading |
| LOOK_AT_TARGET | a head tilt and a small weight shift toward the target |
| RETURN_TO_IDLE | release everything and settle into the idle |

Between events the character idles with controlled movement on ROOT_MOVEMENT: a slow breath in the shoulders and a
small weight sway. It is deterministic and low amplitude. Listening poses have the head tilted toward the speaker
and the weight settled. Turn-taking (``speaking`` and ``listening`` spans) switches between them with eased
transitions. Gestures keep their holds, and events closer together than their gesture length are queued, not
stacked.

The controller writes only body channels. Mouth, eyebrows and blinks stay on FUTURE_FACIAL_CONTROLS, untouched.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .curves import Curve
from .gestures import GesturePerformanceLibrary
from .layers import Clip
from .sequencer import Key

EVENTS = ("SPEECH_EMPHASIS", "QUESTION", "DISAGREEMENT", "SURPRISE", "POINT_AT_TARGET", "LOOK_AT_TARGET",
          "RETURN_TO_IDLE")
EVENT_GESTURE = {"QUESTION": "explain", "DISAGREEMENT": "disagree", "SURPRISE": "surprise",
                 "POINT_AT_TARGET": "point"}
BREATH_PERIOD_S = 3.6
BREATH_DEG = 0.8
SWAY_WEIGHT = 0.02


@dataclass
class DialogueEvent:
    t: float
    event: str
    side: str = "r"  # target / dominant side
    target: list[float] | None = None

    def __post_init__(self):
        if self.event not in EVENTS:
            raise ValueError(f"unknown dialogue event {self.event!r} (known: {', '.join(EVENTS)})")


class DialogueActingController:
    def __init__(self, library: GesturePerformanceLibrary, style: str = "neutral", dominant: str = "r"):
        self.lib, self.style, self.dominant = library, style, dominant

    def idle(self, duration: float, start: float = 0.0, amount: float = 1.0) -> Clip:
        """Breathing (shoulders) and a slow weight sway, as additive ROOT_MOVEMENT keys (smooth, deterministic)."""
        keys, n = [], max(2, int(duration / (BREATH_PERIOD_S / 4)) + 1)
        for i in range(n):
            t = i * duration / (n - 1)
            ph = 2 * math.pi * t / BREATH_PERIOD_S
            br = amount * BREATH_DEG * 0.5 * (1 - math.cos(ph))
            sw = amount * SWAY_WEIGHT * math.sin(2 * math.pi * t / (2.6 * BREATH_PERIOD_S))
            keys.append(Key(t, {"clavicle_r": br, "clavicle_l": br, "weight_left": sw}, {}, "", 0.0, Curve("linear"),
                            interp="path" if 0 < i < n - 1 else "stop"))
        keys[0].params = {k: 0.0 for k in keys[0].params}
        keys[-1].params = {k: 0.0 for k in keys[-1].params}
        return Clip("idle", "ROOT_MOVEMENT", start, keys, blend_in=0.4, blend_out=0.4, anchor_start=False,
                    anchor_end=False, meta={"kind": "idle"})

    def plan(self, events: list[DialogueEvent], duration: float,
             turns: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Clips for a dialogue beat sheet. ``turns``: [{"t0", "t1", "role": "speaking"|"listening",
        "toward": side}]."""
        clips: list[Clip] = [self.idle(duration)]
        busy_until = 0.0
        queued = []
        for ev in sorted(events, key=lambda e: e.t):
            t = max(ev.t, busy_until)
            if t != ev.t:
                queued.append({"event": ev.event, "asked": ev.t, "placed": round(t, 3)})
            if ev.event == "SPEECH_EMPHASIS":
                s = self.dominant
                k = [Key(0.0, {}, {}, "start"),
                     Key(0.22, {f"arm_{s}_flexion": 24.0, f"arm_{s}_abduction": 16.0}, {}, "beat", hold=0.08,
                         anticipation="none"),
                     Key(0.6, {}, {}, "back")]
                clips.append(Clip(f"emph_{t:.2f}", "UPPER_BODY_GESTURES", t, k, mask=[f"arm_{s}_flexion",
                                                                                   f"arm_{s}_abduction"],
                                  gesture="speech_emphasis", side=s, meta={"event": ev.event}))
                busy_until = t + 0.6
            elif ev.event == "LOOK_AT_TARGET":
                sg = -1.0 if ev.side == "r" else 1.0
                hk = [Key(0.0, {"head": 0.0}), Key(0.35, {"head": 6.0 * sg}, hold=0.6, head_lead=True),
                      Key(1.5, {"head": 0.0})]
                rk = [Key(0.0, {"weight_left": 0.0}), Key(0.5, {"weight_left": 0.06 * sg}, hold=0.5),
                      Key(1.5, {"weight_left": 0.0})]
                clips += [Clip(f"look_{t:.2f}:head", "HEAD_MOVEMENT", t, hk, gesture="look_at", side=ev.side,
                               anchor_start=False, anchor_end=False, meta={"event": ev.event}),
                          Clip(f"look_{t:.2f}:root", "ROOT_MOVEMENT", t, rk, gesture="look_at", side=ev.side,
                               anchor_start=False, anchor_end=False, meta={"event": ev.event})]
                busy_until = t + 0.5
            elif ev.event == "RETURN_TO_IDLE":
                clips += self.lib.build("return_neutral", t, ev.side, self.style)
                busy_until = t + 1.2
            else:
                g = EVENT_GESTURE[ev.event]
                cs = self.lib.build(g, t, ev.side, self.style)
                clips += cs
                busy_until = max(c.end for c in cs)
        for tr in turns or []:
            if tr.get("role") == "listening":
                sg = -1.0 if tr.get("toward", "r") == "r" else 1.0
                d = tr["t1"] - tr["t0"]
                hk = [Key(0.0, {"head": 0.0}), Key(min(0.6, d / 3), {"head": 4.0 * sg}, hold=max(0.0, d - 1.4)),
                      Key(d, {"head": 0.0})]
                clips.append(Clip(f"listen_{tr['t0']:.2f}", "HEAD_MOVEMENT", tr["t0"], hk, gesture="listening",
                                  anchor_start=False, anchor_end=False, meta={"turn": "listening"}))
        return {"clips": clips, "queued": queued, "events": [e.__dict__ for e in events],
                "facial_channels_touched": False}


__all__ = ["DialogueActingController", "DialogueEvent", "EVENTS"]
