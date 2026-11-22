"""MotionOverlapController: body parts do not all start and stop together.

Each pose parameter belongs to a channel group. A group's offset ``L`` (a fraction of the move) shifts its timing
within a segment:
- a leading group (``L > 0``) finishes at ``1 - L`` of the move;
- a trailing group (``L < 0``) starts at ``-L`` of the move.

Every group still arrives on the key at the end of the move, so key poses and holds stay intact. Offsets are bounded
by ``MAX_OFFSET`` so the body never looks disconnected. They are editable metadata, stored in the timeline (per
performance, and per key for a single move).

Default order (Phase 3.5 leads, kept):
- the pelvis and weight lead;
- the torso and shoulders follow;
- the upper arms move on the beat;
- the forearms, hands and head trail.

The head can lead instead (``head_lead``) when the move is a change of attention (look, point), so the eyes and
head take the body to the target.

Secondary parts (hair, blazer hem) are not offset here: their lag comes from the springs of the
SecondaryMotionController.
"""

from __future__ import annotations

from typing import Any

GROUPS = ("pelvis", "torso", "shoulders", "head", "arms", "forearms", "hands")
MAX_OFFSET = 0.25
DEFAULT_OFFSETS = {"pelvis": 0.12, "torso": 0.06, "shoulders": 0.03, "head": -0.09, "arms": 0.0, "forearms": -0.07,
                   "hands": -0.12}
HEAD_LEAD = 0.1  # head offset on attention moves when the style or key asks for a head lead


def group_of(param: str) -> str:
    if param in ("weight_left", "contrapposto", "free_knee", "pelvis_dy"):
        return "pelvis"
    if param == "line_of_action":
        return "torso"
    if param.startswith("clavicle_"):
        return "shoulders"
    if param == "head":
        return "head"
    if param.endswith("_abduction"):
        return "arms"
    if param.endswith("_flexion"):
        return "forearms"
    if param.endswith("_wrist"):
        return "hands"
    return "torso"


class MotionOverlapController:
    def __init__(self, offsets: dict[str, float] | None = None):
        self.offsets = dict(DEFAULT_OFFSETS)
        for g, v in (offsets or {}).items():
            self.set(g, v)

    def set(self, group: str, value: float) -> None:
        if group not in GROUPS:
            raise ValueError(f"unknown overlap group {group!r} (known: {', '.join(GROUPS)})")
        self.offsets[group] = max(-MAX_OFFSET, min(MAX_OFFSET, float(value)))

    def offset(self, param: str, override: dict[str, float] | None = None, head_lead: bool = False) -> float:
        g = group_of(param)
        if override and g in override:
            return max(-MAX_OFFSET, min(MAX_OFFSET, float(override[g])))
        if g == "head" and head_lead:
            return HEAD_LEAD
        return self.offsets[g]

    @staticmethod
    def warp(u: float, L: float) -> float:
        """Local progress of a group with offset ``L`` at segment progress ``u`` (clamped to 0..1)."""
        if L >= 0:
            uu = u / (1.0 - L) if L < 1 else 1.0
        else:
            uu = (u + L) / (1.0 + L)
        return 0.0 if uu <= 0 else 1.0 if uu >= 1 else uu

    def to_json(self) -> dict[str, Any]:
        return {"offsets": dict(self.offsets), "max_offset": MAX_OFFSET, "head_lead": HEAD_LEAD}


__all__ = ["MotionOverlapController", "GROUPS", "DEFAULT_OFFSETS", "MAX_OFFSET", "group_of"]
