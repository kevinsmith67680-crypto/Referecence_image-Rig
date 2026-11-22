"""AnticipationAndFollowThroughController: preparation before a big move and follow-through after it.

Anticipation is a small move the other way, early in the segment: a C2 ``bump`` over the first ``span`` of the move.
Its velocity and acceleration are zero where it starts and ends, so the body never jumps into motion. Follow-through
is an overshoot past the key that settles inside the key's hold, using the same bump.

Recipes are per gesture, not global. Not every move gets one: subtle dialogue gestures, returns to neutral and small
weight shifts default to none.

Amounts are fractions of each channel's own move (the preparation goes against the move), and are capped:
- at most ``MAX_FRACTION`` of the move;
- at most the channel's absolute cap (degrees).

Intensity multiplies everything: it is set per key or clip, scaled by the acting style, and 0 disables it.
"""

from __future__ import annotations

import fnmatch
from typing import Any

from ..artistic.transitions import bump, overshoot_curve

MAX_FRACTION = 0.15
ABS_CAP = {"arms": 12.0, "forearms": 10.0, "hands": 8.0, "shoulders": 4.0, "torso": 3.0, "head": 3.0, "pelvis": 0.04}
SPAN = 0.32  # anticipation occupies the first 32 % of the move

# recipe: channel pattern ({s} = gesture side) -> fraction of that channel's move, against the move
RECIPES: dict[str, dict[str, Any]] = {
    "point": {"note": "torso and shoulder prepare, the arm pulls back slightly, then extends and settles",
              "anticipation": {"line_of_action": 0.15, "clavicle_{s}": 0.12, "arm_{s}_abduction": 0.08,
                               "arm_{s}_flexion": 0.06},
              "follow": ["arm_{s}_abduction", "arm_{s}_flexion", "arm_{s}_wrist", "head"]},
    "raise": {"note": "the arm dips before it rises; the shoulder follows through",
              "anticipation": {"arm_{s}_abduction": 0.08, "clavicle_{s}": 0.1},
              "follow": ["arm_{s}_abduction", "arm_{s}_flexion", "clavicle_{s}"]},
    "shrug": {"note": "shoulders dip, then rise; elbows lift out; a brief hold; controlled relaxation",
              "anticipation": {"clavicle_r": 0.12, "clavicle_l": 0.12, "arm_r_flexion": 0.05, "arm_l_flexion": 0.05},
              "follow": ["clavicle_r", "clavicle_l", "head"]},
    "weight_shift": {"note": "a small push off the support leg before the weight travels",
                     "anticipation": {"weight_left": 0.06},
                     "follow": ["line_of_action", "head"]},
    "wave": {"note": "the arm draws back before the hand comes up",
             "anticipation": {"arm_{s}_abduction": 0.06},
             "follow": ["arm_{s}_flexion", "arm_{s}_wrist"]},
    "look": {"note": "no counter-move: the head leads (overlap), the torso follows", "anticipation": {},
             "follow": ["head"]},
    "surprise": {"note": "a quick drop before the startle", "anticipation": {"clavicle_r": 0.1, "clavicle_l": 0.1},
                 "follow": ["arm_r_abduction", "arm_l_abduction", "head"]},
    "none": {"note": "subtle gesture: no anticipation", "anticipation": {}, "follow": []},
}


class AnticipationAndFollowThroughController:
    def __init__(self, recipes: dict[str, dict[str, Any]] | None = None):
        self.recipes = {**RECIPES, **(recipes or {})}

    def recipe(self, name: str | None) -> dict[str, Any]:
        return self.recipes.get(name or "none", self.recipes["none"])

    @staticmethod
    def _expand(pattern: str, side: str | None) -> str:
        return pattern.replace("{s}", side or "r")

    def anticipation_term(self, recipe: str | None, side: str | None, param: str, group: str, move: float, u: float,
                          intensity: float) -> float:
        """Offset added to ``param`` at segment progress ``u`` (against the move)."""
        if intensity <= 0 or move == 0:
            return 0.0
        rec = self.recipe(recipe)["anticipation"]
        frac = 0.0
        for pat, f in rec.items():
            if fnmatch.fnmatchcase(param, self._expand(pat, side)):
                frac = float(f)
                break
        if frac == 0.0:
            return 0.0
        frac = min(frac * intensity, MAX_FRACTION)
        amp = min(abs(move) * frac, ABS_CAP.get(group, 5.0))
        return -amp * (1.0 if move > 0 else -1.0) * bump(u / SPAN)

    def follows(self, recipe: str | None, side: str | None, param: str) -> bool:
        return any(fnmatch.fnmatchcase(param, self._expand(p, side)) for p in self.recipe(recipe)["follow"])

    @staticmethod
    def overshoot_term(amount: float, move: float, s: float, hold_frac: float, group: str) -> float:
        """Overshoot of ``amount`` (fraction of the move), carried past the key around arrival and settled inside the
        hold. ``s``: progress of the move, continuing past 1 through the hold."""
        if amount <= 0 or move == 0:
            return 0.0
        amp = min(abs(move) * min(amount, MAX_FRACTION), ABS_CAP.get(group, 5.0))
        return amp * (1.0 if move > 0 else -1.0) * overshoot_curve(s, hold_frac)

    def to_json(self) -> dict[str, Any]:
        return {"recipes": self.recipes, "max_fraction": MAX_FRACTION, "abs_cap": ABS_CAP, "span": SPAN}


__all__ = ["AnticipationAndFollowThroughController", "RECIPES", "MAX_FRACTION", "ABS_CAP", "SPAN"]
