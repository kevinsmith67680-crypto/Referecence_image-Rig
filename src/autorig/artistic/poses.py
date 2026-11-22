"""Phase 3.5 artistic pose planning: whole-body poses built before any artwork is deformed.

An ``ArtisticPose`` describes the pose the way an animator thinks about it:
- the action and intention;
- the weight-bearing leg and the amount of contrapposto (hip roll toward the weight leg, shoulders countering);
- the line of action (a sideways spine and chest curve);
- head counterbalance;
- per-arm gestures with the hand drawing they need;
- the stance;
- the view it assumes;
- controlled cartoon exaggeration.

``ArtisticPosePlanner`` turns it into the existing ``PoseIntent`` (stage 1 of the motion pipeline) and hands it to the
one skeletal solver through ``MotionEngine.pose(intent_fn=...)``. No second solver is added and no bone is scaled.

Hand drawings that are still pending review are only *previewed* (``allow_pending``); a pose that needs them is
reported as such by the feasibility engine, never as approved.
"""

from __future__ import annotations

import copy
import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from ..motion.intent import ArmIntent, PoseIntent
from ..phase2.skeleton import SIDES

LAT = {"r": -1.0, "l": 1.0}  # image x direction of the character's side
OTHER = {"r": "l", "l": "r"}


@dataclass
class Gesture:
    """One arm's gesture. ``kind``:
    - ``hang``: FK abduction/flexion/wrist (degrees);
    - ``reach``: wrist at (lateral, up) fractions of arm length from the shoulder;
    - ``hip``: wrist on the side of the waist, elbow out;
    - ``knee``: hand resting on the same side's knee, never the crotch;
    - ``chin``: hand toward the chin (deep elbow);
    - ``gait``: the gait's own arm swing, restyled for the front view.

    ``hand``: the hand-selection action (``motion/handselect.py`` ACTIONS)."""

    kind: str = "hang"
    abduction: float = 10.0
    flexion: float = 4.0
    wrist: float = 2.0
    reach: tuple[float, float] = (0.9, 0.3)
    elbow: str = "down"
    hand: str = "relaxed_standing"
    hand_dir: float | None = None
    max_flex: float | None = None


@dataclass
class ArtisticPose:
    action: str
    title: str
    base: str  # existing preset the intent starts from (gaits keep their phase logic)
    intention: str
    weight_left: float | None = 0.5  # share of weight on the character's left leg (None: keep the base's)
    contrapposto: float = 0.0  # 0..1: hip roll toward the weight leg, shoulders counter
    line_of_action: float = 0.0  # deg: sideways curve of spine + chest (+ = toward the character's left)
    head: float = 0.0  # head world tilt (deg, + = toward the character's left)
    clavicle: dict[str, float] = field(default_factory=dict)
    arms: dict[str, Gesture] = field(default_factory=dict)
    stance: float = 1.0  # ankle separation factor of the natural stance
    free_knee: float = 9.0  # soft knee of the unweighted leg (deg)
    free_foot_in: float = 0.12  # unweighted foot moves this fraction of the hip separation toward the midline
    pelvis_dy: float | None = None  # fraction of leg length (squat/sit)
    knee_max: float | None = None
    phase: float | None = None
    view: str = "FRONT"
    exaggeration: float = 0.0  # 0 = grounded, 1 = cartoon push (scales roll, curve, head, gesture)
    front: list[str] = field(default_factory=list)  # layers drawn last (depth intent)
    depth: dict[str, str] = field(default_factory=dict)  # e.g. {"arm_r": "behind_body"} (DynamicLayerOrderController)
    variant: str = "default"
    requires: list[str] = field(default_factory=list)  # artwork the intended pose needs (beyond what renders)
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["arms"] = {s: asdict(g) for s, g in self.arms.items()}
        return d

    @staticmethod
    def from_json(d: dict[str, Any]) -> "ArtisticPose":
        d = dict(d)
        d["arms"] = {s: Gesture(**{**g, "reach": tuple(g.get("reach", (0.9, 0.3)))}) for s, g in d.get("arms", {}).items()}
        return ArtisticPose(**d)


class ArtisticPosePlanner:
    """Builds ``PoseIntent`` objects from ``ArtisticPose`` descriptions (stage 1 only)."""

    def __init__(self, profile):
        self.p = profile

    # -- intent construction ---------------------------------------------------------------------------------
    def intent_fn(self, ap: ArtisticPose):
        return lambda it: self.apply(it, ap)

    def apply(self, it: PoseIntent, ap: ArtisticPose) -> PoseIntent:
        p = self.p
        it = copy.deepcopy(it)
        x = 1.0 + 0.6 * ap.exaggeration
        static = it.balance == "static"
        if static and ap.weight_left is not None:
            wl = float(np.clip(0.5 + (ap.weight_left - 0.5) * (1.0 + 0.3 * ap.exaggeration), 0.15, 0.85))
            it.weight_left = wl
            sep = p.stance_natural * ap.stance
            for s in SIDES:
                leg = it.legs.get(s)
                if leg is None:
                    continue
                w = wl if s == "l" else 1.0 - wl
                dx = LAT[s] * sep / 2
                if w < 0.42:  # the free leg: soft knee, foot a little in and turned out
                    leg.knee_max = max(leg.knee_max, p.front_knee["swing"])
                    leg.knee_min = max(leg.knee_min, ap.free_knee)
                    dx -= LAT[s] * ap.free_foot_in * p.hip_sep
                    leg.foot_angle = LAT[s] * 6.0
                if ap.knee_max is not None:
                    leg.knee_max = ap.knee_max
                leg.ankle = (p.midline_x + dx, leg.ankle[1])
            # contrapposto: the hip over the weight leg rises (+ roll raises the character's right hip)
            it.pelvis_roll = -(wl - 0.5) / 0.35 * 7.0 * ap.contrapposto * x
        if ap.pelvis_dy is not None:
            L = (p.leg("r") + p.leg("l")) / 2
            it.pelvis_dy = ap.pelvis_dy * L
        if ap.line_of_action or ap.contrapposto:
            it.spine = 0.55 * ap.line_of_action * x
            # the chest counters the pelvis (shoulders tilt the other way) and carries the rest of the curve
            it.chest = 0.45 * ap.line_of_action * x - 0.8 * it.pelvis_roll * ap.contrapposto
        it.head = ap.head * x
        it.neck_share = 0.45
        if ap.clavicle:
            it.clavicle = {**it.clavicle, **{s: v * (1.0 + 0.3 * ap.exaggeration) for s, v in ap.clavicle.items()}}
        for s, g in ap.arms.items():
            it.arms[s] = self._arm(it, s, g, x)
        if ap.front:
            it.front = list(it.front) + [f for f in ap.front if f not in it.front]
        it.notes = list(it.notes) + [f"artistic: {ap.action}/{ap.variant}"] + list(ap.notes)
        it.title = ap.title
        return it

    def _arm(self, it: PoseIntent, s: str, g: Gesture, x: float) -> ArmIntent:
        p = self.p
        prev = it.arms.get(s) or ArmIntent()
        if g.kind == "gait":  # front-view arm swing: forward swing lifts the forearm; the arm never crosses the body
            fwd = float(it.channels.get(f"arm_forward_{s}", 0.0))
            return ArmIntent(abduction=g.abduction + 2.0 * abs(fwd), flexion=g.flexion + 22.0 * max(fwd, 0.0) * x,
                             wrist=g.wrist, hand=prev.hand)
        if g.kind == "reach":
            return ArmIntent(reach=g.reach, elbow=g.elbow, wrist=g.wrist, max_flex=g.max_flex, hand=prev.hand)
        if g.kind == "hip":
            tgt = (p.midline_x + LAT[s] * (p.waist_half - 0.05 * p.hip_sep), p.waist_y - 0.2 * (p.hip_y - p.waist_y))
            sh = p.shoulder.get(s)
            if sh is not None:
                v = np.subtract(tgt, sh)
                d, R = float(np.hypot(*v)), 0.9 * p.arm(s)
                if d > R:
                    tgt = tuple(np.asarray(sh, float) + v * (R / d))
            return ArmIntent(target=tuple(float(v) for v in tgt), elbow="out", hand_dir=LAT[s] * 25.0, hand=prev.hand)
        if g.kind == "knee":
            return ArmIntent(target_joint=f"knee_{s}", target_offset=(LAT[s] * 0.25 * p.hand_len.get(s, 60.0),
                                                                      -0.55 * p.hand_len.get(s, 60.0)),
                             elbow="out", abduction=g.abduction, flexion=g.flexion, wrist=g.wrist, hand=prev.hand)
        if g.kind == "chin":
            return ArmIntent(abduction=g.abduction, flexion=g.flexion, wrist=g.wrist, max_flex=g.max_flex,
                             hand=prev.hand)
        return ArmIntent(abduction=g.abduction, flexion=g.flexion * (1.0 if g.flexion < 0 else 1.0), wrist=g.wrist,
                         hand_dir=g.hand_dir, hand=prev.hand)

    # -- request for MotionEngine.pose ------------------------------------------------------------------------
    def request(self, ap: ArtisticPose, preview_pending: bool = True) -> dict[str, Any]:
        """Keyword arguments for ``MotionEngine.pose`` (besides ``visible``)."""
        acts = {s: g.hand for s, g in ap.arms.items() if g.kind != "gait"}
        return {"name": ap.base, "phase": ap.phase, "intent_fn": self.intent_fn(ap), "hand_actions": acts or None,
                "allow_pending": preview_pending, "depth": dict(ap.depth) or None}


# -- the action library ------------------------------------------------------------------------------------------
def _hang(abd=10.0, flex=4.0, wrist=2.0, hand="relaxed_standing"):
    return Gesture("hang", abduction=abd, flexion=flex, wrist=wrist, hand=hand)


def mirror(ap: ArtisticPose) -> ArtisticPose:
    """Left/right mirror of a pose (weight, roll, curve, head, arms, clavicles)."""
    m = copy.deepcopy(ap)
    if m.weight_left is not None:
        m.weight_left = 1.0 - m.weight_left
    m.line_of_action, m.head = -m.line_of_action, -m.head
    m.clavicle = {OTHER[s]: v for s, v in ap.clavicle.items()}
    m.arms = {OTHER[s]: copy.deepcopy(g) for s, g in ap.arms.items()}
    m.front = [f.replace("_r", "_X").replace("_l", "_r").replace("_X", "_l") for f in ap.front]
    return m


def action_library() -> dict[str, dict[str, Any]]:
    """The 12 Phase 3.5 actions with their candidate variants. Each entry: base pose + variant overrides."""
    relaxed = ArtisticPose(
        "relaxed_standing", "Relaxed standing", "relaxed_standing",
        "at ease; weight a little on the left leg, arms hanging free of the body, relaxed hands",
        weight_left=0.6, contrapposto=0.5, line_of_action=-2.0, head=2.5,
        arms={"r": _hang(11.0, 5.0, 3.0), "l": _hang(9.0, 9.0, 4.0)}, clavicle={"r": -2.0, "l": -1.0})
    weight = ArtisticPose(
        "weight_shift", "Weight-shifted standing", "relaxed_standing",
        "classic contrapposto: weight on the left leg, left hip up, right knee soft, shoulders countering",
        weight_left=0.8, contrapposto=1.0, line_of_action=-4.0, head=-4.0, free_knee=14.0,
        arms={"r": _hang(12.0, 8.0, 3.0), "l": Gesture("hip", hand="hand_on_hip")}, clavicle={"r": -3.0, "l": 2.0})
    point_r = ArtisticPose(
        "point_right", "Pointing (right hand)", "pointing_speaking",
        "points at someone to his right: arm extended, weight toward the target, head turned with it",
        weight_left=0.35, contrapposto=0.6, line_of_action=-5.0, head=-4.0,
        arms={"r": Gesture("reach", reach=(0.94, 0.22), elbow="down", wrist=-2.0, hand="pointing"),
              "l": _hang(9.0, 10.0, 4.0)}, clavicle={"r": 4.0, "l": -2.0})
    shrug = ArtisticPose(
        "shrug", "Shrugging", "one_hand_shrug",
        "'I don't know': shoulders up (one higher), elbows in, forearms out, palms up, head sinks and tilts",
        weight_left=0.6, contrapposto=0.4, head=7.0,
        arms={"r": Gesture("hang", abduction=10.0, flexion=-58.0, wrist=-18.0, hand="shrugging"),
              "l": Gesture("hang", abduction=9.0, flexion=-50.0, wrist=-16.0, hand="shrugging")},
        clavicle={"r": 13.0, "l": 18.0})
    raise_r = ArtisticPose(
        "arm_raised", "Arm raised above the shoulder", "relaxed_standing",
        "raises the right hand high (asking a question): weight on the left leg, torso leans away from the arm",
        weight_left=0.68, contrapposto=0.7, line_of_action=4.0, head=3.0,
        arms={"r": Gesture("hang", abduction=150.0, flexion=14.0, wrist=0.0, hand="raised"),
              "l": _hang(9.0, 8.0, 4.0)}, clavicle={"r": 4.0, "l": -2.0})
    deep_elbow = ArtisticPose(
        "deep_elbow", "Deep elbow bend (hand to chin)", "relaxed_standing",
        "thinking: right hand toward the chin; the forearm folds well past 125 deg",
        weight_left=0.6, contrapposto=0.4, head=-3.0,
        arms={"r": Gesture("chin", abduction=22.0, flexion=118.0, wrist=-6.0, max_flex=120.0, hand="resting"),
              "l": _hang(9.0, 10.0, 4.0)},
        requires=["corrective:elbow_deep_r (sleeve fold for > 125 deg)", "hand:fist or thinking hand (approved)"],
        notes=["rendered at the supported limit (118-120 deg); the intended 140 deg needs the deep-elbow drawing"])
    walk = ArtisticPose(
        "walk", "Walking", "casual_walk", "front-view walk: arms swing beside the body, relaxed hands",
        weight_left=None, arms={s: Gesture("gait", abduction=9.0, flexion=6.0, wrist=3.0, hand="walking") for s in SIDES},
        phase=0.25, requires=["side or 3/4 view for a readable stride (front view: in-plane motion only)"])
    run = ArtisticPose(
        "run", "Running", "running", "front-view jog: elbows bent, fists pumping beside the body",
        weight_left=None, arms={s: Gesture("gait", abduction=22.0, flexion=40.0, wrist=0.0, hand="running") for s in SIDES},
        phase=0.25, requires=["art:thigh_knee_up / art:shin_tucked (knee drive toward the viewer)",
                              "side or 3/4 view for a readable sprint"])
    sit = ArtisticPose(
        "sit", "Sitting", "sitting", "sits upright on a chair, hands resting on the knees (fallback: front squat-sit)",
        weight_left=0.5, arms={s: Gesture("knee", abduction=20.0, flexion=40.0, wrist=6.0, hand="resting") for s in SIDES},
        stance=1.6, requires=["art:thigh_foreshortened (seated thighs toward the viewer)", "art:seat",
                              "corrective:trousers_seated"])
    squat = ArtisticPose(
        "squat", "Squatting", "natural_squat", "squats with forearms resting on the knees, weight slightly to one side",
        weight_left=0.56, contrapposto=0.2, head=2.0, stance=1.3, pelvis_dy=0.17,
        arms={s: Gesture("knee", abduction=22.0, flexion=48.0, wrist=6.0, hand="resting") for s in SIDES})
    lean = ArtisticPose(
        "lean_forward", "Leaning forward", "leaning_forward",
        "leans toward the viewer (curious); front-view art can only lean sideways",
        weight_left=0.5, arms={s: _hang(10.0, 8.0, 3.0) for s in SIDES},
        requires=["art:torso_lean_forward / corrective:torso_lean_3q (3/4 or foreshortened torso)"])
    behind = ArtisticPose(
        "hand_behind_back", "Hand behind the back", "relaxed_standing",
        "right hand tucked behind the back (depth: the arm goes behind the body, the shoulder cap stays in front)",
        weight_left=0.65, contrapposto=0.6, head=-3.0, depth={"arm_r": "behind_body"},
        arms={"r": Gesture("hang", abduction=6.0, flexion=62.0, wrist=10.0, hand="relaxed_standing"),
              "l": _hang(10.0, 6.0, 3.0)})
    cross = ArtisticPose(
        "hand_across", "Hand across the body", "relaxed_standing",
        "right hand reaches across to the left elbow (depth: in front of the left arm)",
        weight_left=0.45, contrapposto=0.4, head=4.0,
        arms={"r": Gesture("hang", abduction=-4.0, flexion=118.0, wrist=-4.0, max_flex=120.0, hand="relaxed_standing"),
              "l": _hang(10.0, 8.0, 3.0)})
    point_l = mirror(point_r)
    point_l.action, point_l.title = "point_left", "Pointing (left hand)"
    point_l.intention = "points at someone to his left; the left pointing drawing is a mirrored copy (flagged)"

    variants: dict[str, dict[str, Any]] = {
        "relaxed_standing": {"base": relaxed, "variants": {
            "default": {}, "arms_freer": {"arms": {"r": _hang(14.0, 3.0, 2.0), "l": _hang(12.0, 6.0, 3.0)}},
            "symmetric_reference": {"weight_left": 0.5, "contrapposto": 0.0, "line_of_action": 0.0, "head": 0.0,
                                    "arms": {"r": _hang(9.0, 14.0, 6.0), "l": _hang(9.0, 14.0, 6.0)}}}},
        "weight_shift": {"base": weight, "variants": {
            "default": {}, "both_hanging": {"arms": {"r": _hang(12.0, 6.0, 3.0), "l": _hang(14.0, 4.0, 2.0)}},
            "exaggerated": {"exaggeration": 0.8}}},
        "point_right": {"base": point_r, "variants": {
            "default": {}, "bent_elbow": {"arms": {"r": Gesture("reach", reach=(0.82, 0.30), elbow="down", wrist=-6.0,
                                                                hand="pointing"), "l": _hang(9.0, 10.0, 4.0)}},
            "lean_in": {"line_of_action": -9.0, "weight_left": 0.3, "exaggeration": 0.4},
            "raised_shoulder": {"clavicle": {"r": 10.0, "l": -2.0}}}},
        "point_left": {"base": point_l, "variants": {
            "default": {}, "bent_elbow": {"arms": {"l": Gesture("reach", reach=(0.82, 0.30), elbow="down", wrist=-6.0,
                                                                hand="pointing"), "r": _hang(9.0, 10.0, 4.0)}},
            "lean_in": {"line_of_action": 9.0, "weight_left": 0.7, "exaggeration": 0.4}}},
        "shrug": {"base": shrug, "variants": {"default": {}, "big": {"exaggeration": 0.8},
                                              "symmetric": {"clavicle": {"r": 16.0, "l": 16.0}, "head": 0.0}}},
        "arm_raised": {"base": raise_r, "variants": {
            "default": {}, "straight_arm": {"arms": {"r": Gesture("hang", abduction=160.0, flexion=4.0, wrist=0.0,
                                                                   hand="raised"), "l": _hang(9.0, 8.0, 4.0)}},
            "no_lean": {"line_of_action": 0.0, "contrapposto": 0.3}}},
        "deep_elbow": {"base": deep_elbow, "variants": {"default": {}}},
        "walk": {"base": walk, "variants": {"default": {}, "phase_0.75": {"phase": 0.75}}},
        "run": {"base": run, "variants": {"default": {}, "phase_0.6": {"phase": 0.6}}},
        "sit": {"base": sit, "variants": {"default": {}}},
        "squat": {"base": squat, "variants": {"default": {}, "hands_hanging": {"arms": {
            s: Gesture("hang", abduction=26.0, flexion=30.0, wrist=6.0, hand="relaxed_standing") for s in SIDES}}}},
        "lean_forward": {"base": lean, "variants": {"default": {}}},
        "hand_behind_back": {"base": behind, "variants": {"default": {}}},
        "hand_across": {"base": cross, "variants": {"default": {}}},
    }
    return variants


def variant(action: str, name: str = "default", lib: dict[str, Any] | None = None) -> ArtisticPose:
    lib = lib or action_library()
    entry = lib[action]
    ap = copy.deepcopy(entry["base"])
    for k, v in entry["variants"].get(name, {}).items():
        if k == "arms":
            ap.arms = {**ap.arms, **copy.deepcopy(v)}
        else:
            setattr(ap, k, copy.deepcopy(v))
    ap.variant = name
    return ap


def apply_constraints(ap: ArtisticPose, constraints: list[dict[str, Any]]) -> ArtisticPose:
    """User feedback stored as structured constraints (Artistic Review): each is {"action", "param", "delta"} or
    {"action", "param", "value"}. Params: weight_left, contrapposto, line_of_action, head, exaggeration,
    stance, free_knee, clavicle_<s>, arm_<s>_abduction|flexion|wrist, arm_<s>_reach_lateral|up."""
    ap = copy.deepcopy(ap)
    for c in constraints:
        if c.get("action") not in (None, "*", ap.action):
            continue
        prm, val, delta = c["param"], c.get("value"), float(c.get("delta", 0.0))

        def upd(cur):
            return float(val) if val is not None else float(cur or 0.0) + delta
        if prm.startswith("clavicle_"):
            s = prm[-1]
            ap.clavicle[s] = upd(ap.clavicle.get(s, 0.0))
        elif prm.startswith("arm_"):
            _, s, *rest = prm.split("_")
            g = ap.arms.get(s)
            if g is None:
                continue
            key = "_".join(rest)
            if key in ("reach_lateral", "reach_up"):
                i = 0 if key == "reach_lateral" else 1
                r = list(g.reach)
                r[i] = upd(r[i])
                g.reach = (r[0], r[1])
            elif hasattr(g, key):
                setattr(g, key, upd(getattr(g, key)))
        elif hasattr(ap, prm):
            setattr(ap, prm, upd(getattr(ap, prm)))
    return ap


ACTIONS_12 = ["relaxed_standing", "weight_shift", "point_right", "point_left", "shrug", "arm_raised", "deep_elbow",
              "walk", "run", "sit", "squat", "lean_forward"]

__all__ = ["ArtisticPose", "Gesture", "ArtisticPosePlanner", "action_library", "variant", "mirror", "apply_constraints",
           "ACTIONS_12", "math"]
