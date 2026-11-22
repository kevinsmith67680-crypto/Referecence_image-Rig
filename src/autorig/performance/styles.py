"""ActingStyleProfile: how a character moves, never what the character looks like.

A profile scales the timing and amplitude of the performance:
- gesture amplitude and torso involvement;
- durations and holds;
- anticipation and overshoot;
- whether the head leads changes of attention;
- secondary-motion intensity.

A profile may also add a small posture offset on the ROOT_MOVEMENT layer (shoulders, weight, head tilt). It never
changes the skeleton, the bone lengths, the artwork or the garment rig: the anatomy is the same in every style
(tested). Profiles are optional and editable.

The descriptions are about motion only and avoid stereotypes. Every value is a plain multiplier the user can change.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class ActingStyleProfile:
    name: str
    description: str
    amplitude: float = 1.0  # gesture size (deltas from the reference pose)
    duration: float = 1.0  # move durations
    anticipation: float = 1.0
    overshoot: float = 1.0
    hold: float = 1.0
    torso: float = 1.0  # torso and weight involvement in gestures
    head_lead: bool = False  # the head leads changes of attention
    secondary: float = 1.0  # secondary-motion intensity
    posture: dict[str, float] = field(default_factory=dict)  # additive ROOT_MOVEMENT offsets (deg / weight share)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    def with_changes(self, **kw) -> "ActingStyleProfile":
        return replace(self, **kw)


PROFILES: dict[str, ActingStyleProfile] = {
    "neutral": ActingStyleProfile("neutral", "balanced, restrained and natural"),
    "confident": ActingStyleProfile(
        "confident", "upright torso, decisive gestures, controlled movement, stronger holds",
        amplitude=1.05, duration=0.92, anticipation=0.9, overshoot=0.8, hold=1.3, torso=0.6, head_lead=True,
        secondary=0.8, posture={"clavicle_r": -1.0, "clavicle_l": -1.0}),
    "arrogant": ActingStyleProfile(
        "arrogant", "relaxed hip-shot posture, slow deliberate gestures, more head and shoulder acting",
        amplitude=1.1, duration=1.2, anticipation=0.7, overshoot=0.6, hold=1.4, torso=1.3, head_lead=False,
        secondary=0.9, posture={"contrapposto": 0.25, "head": 2.5, "weight_left": 0.05}),
    "shy": ActingStyleProfile(
        "shy", "smaller gestures, arms closer to the body, restrained movement",
        amplitude=0.65, duration=1.1, anticipation=0.5, overshoot=0.4, hold=0.85, torso=0.6, head_lead=False,
        secondary=0.7, posture={"clavicle_r": 2.5, "clavicle_l": 2.5, "head": -2.0}),
    "energetic": ActingStyleProfile(
        "energetic", "faster gestures, stronger anticipation, more follow-through",
        amplitude=1.15, duration=0.75, anticipation=1.4, overshoot=1.5, hold=0.8, torso=1.2, head_lead=True,
        secondary=1.4),
}


def style(name: str | None, overrides: dict[str, Any] | None = None) -> ActingStyleProfile:
    base = PROFILES.get(name or "neutral")
    if base is None:
        raise ValueError(f"unknown acting style {name!r} (known: {', '.join(PROFILES)})")
    if overrides:
        bad = [k for k in overrides if k not in ActingStyleProfile.__dataclass_fields__ or k == "name"]
        if bad:
            raise ValueError(f"unknown style fields {bad}")
        base = base.with_changes(**overrides)
    return base


__all__ = ["ActingStyleProfile", "PROFILES", "style"]
