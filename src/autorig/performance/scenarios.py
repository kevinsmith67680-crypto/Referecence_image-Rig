"""The Phase 3.6 benchmark performances on the real character.

- ``acting_upgrade``: the Phase 3.5 acting test re-performed with the performance engine. It keeps the same seven
  beats, key times and holds (78 frames, 6.4 s, 12 fps), so the frames line up with the baseline. It adds:
  - planted feet and continuous weight transfer;
  - per-gesture anticipation and follow-through, with a head lead on changes of attention;
  - hair and hem follow-through, and a quiet idle.

  The shrug becomes ``shrug_low`` (shoulders up, hands low): the palm-up hands it needs do not exist, so the
  palm-up shrug is NEEDS_ARTWORK and only appears in a labelled review preview.
- ``classroom``: the character stands beside a test desk and:
  1. shifts his weight;
  2. puts his right hand on the desk (a hand contact);
  3. leans toward the desk (lateral, the supported direction);
  4. points at a classmate with the left hand while the right hand stays planted;
  5. releases the desk and returns to standing.

  A forward lean is REQUIRES_NEW_VIEW and is not rendered.
- ``secondary_demo``: a quick sideways lean, then a stop and a hold, so the hair and hem can settle. It is rendered
  with and without secondary motion.
"""

from __future__ import annotations

from typing import Any

from ..artistic.transitions import acting_test_keys, params_of
from .director import Performance
from .seat import SeatAndInteractionController
from .sequencer import Key

SHRUG_LOW = {"arm_r_abduction": 15.0, "arm_r_flexion": -8.0, "arm_r_wrist": 2.0, "arm_l_abduction": 14.0,
             "arm_l_flexion": -6.0, "arm_l_wrist": 2.0, "clavicle_r": 13.0, "clavicle_l": 15.0}
ACTING_RECIPES = {"weight shift": ("weight_shift", None, 0.0, False), "head": ("look", None, 0.0, True),
                  "raise arm": ("raise", "r", 0.06, False), "point": ("point", "r", 0.05, True),
                  "shrug": ("shrug", None, 0.05, False), "relax": (None, None, 0.0, False), "stand": (None, None, 0.0, False)}
ACTING_HANDS = {"stand": {"r": "relaxed", "l": "relaxed"}, "weight shift": {"r": "relaxed", "l": "relaxed"},
                "head": {"r": "relaxed", "l": "relaxed"}, "raise arm": {"r": "open_palm", "l": "relaxed"},
                "point": {"r": "pointing", "l": "relaxed"}, "shrug": {"r": "relaxed", "l": "relaxed"},
                "relax": {"r": "relaxed", "l": "relaxed"}}


def acting_upgrade(idle: float = 0.5, style: str = "neutral") -> Performance:
    keys = []
    for kp in acting_test_keys():
        p = params_of(kp.pose)
        label = kp.label
        if label == "shrug":
            p.update(SHRUG_LOW)
        recipe, side, ovs, head_lead = ACTING_RECIPES[label]
        k = Key(kp.t, p, dict(ACTING_HANDS[label]), label if label != "shrug" else "shrug (hands low)", kp.hold,
                anticipation=recipe, side=side, overshoot=ovs, head_lead=head_lead, id=f"k{len(keys)}",
                pose={"base": kp.pose.base})
        keys.append(k.to_json())
    return Performance(
        name="acting_test_upgrade", fps=12.0, style=style, base_keys=keys, idle=idle,
        notes=["same seven beats, key times and holds as the Phase 3.5 acting test (frame-aligned)",
               "shrug: palm-up hands do not exist (NEEDS_ARTWORK); shown as shrug_low (shoulders up, hands low)",
               "pending hand drawings (relaxed, pointing) are previewed and labelled; nothing is approved"])


def classroom(eng) -> Performance:
    sc = SeatAndInteractionController(eng)
    desk = sc.desk_beside("r")
    stand = {"weight_left": 0.55, "contrapposto": 0.4, "line_of_action": 0.0, "head": 1.5, "free_knee": 5.0,
             "clavicle_r": -2.0, "clavicle_l": -1.0, "arm_r_abduction": 11.0, "arm_r_flexion": 5.0, "arm_r_wrist": 3.0,
             "arm_l_abduction": 9.0, "arm_l_flexion": 8.0, "arm_l_wrist": 4.0}
    shift = {**stand, "weight_left": 0.4, "contrapposto": 0.7, "line_of_action": -2.0, "head": -2.0}
    lean = {**stand, "weight_left": 0.32, "contrapposto": 0.6, "line_of_action": -7.0, "head": -3.0}
    keys = [Key(0.0, stand, {"r": "relaxed", "l": "relaxed"}, "stand", 0.4, id="c0"),
            Key(1.1, shift, {}, "shift weight", 0.2, anticipation="weight_shift", id="c1"),
            Key(2.2, lean, {}, "lean toward the desk (lateral)", 2.2, anticipation="none", id="c2"),
            Key(5.4, {**stand}, {}, "stand", 0.8, id="c3")]
    contact = sc.hand_on_desk(desk, "r", start=1.75, end=4.6, acquire=0.6, release=0.55)
    return Performance(
        name="classroom_desk", fps=12.0, base_keys=[k.to_json() for k in keys], template={"stance": 0.8},
        gestures=[{"id": "point_l", "gesture": "point", "start": 2.75, "side": "l", "duration": 1.9, "torso": 0.3}],
        contacts=[contact.to_json()], props={"desks": [desk.to_json()]},
        hand_markers=[{"t": 1.35, "side": "r", "action": "palm_down", "until": 4.75}], idle=0.4,
        notes=["the desk is a test prop beside the character (simple geometry, not character artwork)",
               "the lean is lateral (supported); a forward lean toward the desk is REQUIRES_NEW_VIEW "
               "(art:torso_lean_forward) and is not rendered",
               "palm-down (right) and pointing (left, a mirrored copy) hand drawings are pending review: preview only"])


def secondary_demo(mute_secondary: bool = False) -> Performance:
    rest = {"weight_left": 0.5, "contrapposto": 0.3, "line_of_action": 0.0, "head": 0.0, "free_knee": 9.0,
            "clavicle_r": -2.0, "clavicle_l": -1.0, "arm_r_abduction": 11.0, "arm_r_flexion": 5.0, "arm_r_wrist": 3.0,
            "arm_l_abduction": 9.0, "arm_l_flexion": 8.0, "arm_l_wrist": 4.0}
    lean = {**rest, "weight_left": 0.3, "contrapposto": 0.9, "line_of_action": -8.0, "head": -5.0}
    keys = [Key(0.0, rest, {"r": "relaxed", "l": "relaxed"}, "relaxed", 0.6, id="s0"),
            Key(1.05, lean, {}, "quick lean, then stop", 2.95, anticipation="none", id="s1")]
    layers: dict[str, Any] = {"SECONDARY_MOTION": {"mute": mute_secondary}}
    return Performance(name="secondary_demo" + ("_body_only" if mute_secondary else ""), fps=12.0,
                       base_keys=[k.to_json() for k in keys], layers=layers,
                       secondary={"hem": {"gain": 1.6}, "hair": {"gain": 1.2}},
                       notes=["a quick sideways lean and a stop; the hair and blazer hem settle afterwards",
                              "tie: NEEDS_ARTWORK (vest behind it not drawn); buttons, badge, collar, lapels are rigid"])


__all__ = ["acting_upgrade", "classroom", "secondary_demo", "SHRUG_LOW"]
