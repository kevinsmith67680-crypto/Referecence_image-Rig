"""NaturalHandSelectionEngine: the automatic choice of a hand drawing for a pose (Phase 3.2 switch groups).

The engine only fills the *automatic* slot of a hand switch group. Resolution stays: explicit user choice > switch
keyframe > automatic choice (this engine, while the group is in auto mode) > the group default. An artist's choice
is never replaced because another drawing scores higher, and the score is never presented as proof of visual
quality: it ranks the drawings the character has for the action, from measurable criteria.

Inputs per side (``HandContext``): the action (gesture intention; from the preset or the user's override), the
solved arm (forearm and hand world angles, elbow flexion, wrist bend), the hand's movement direction between
frames, the view, an optional target (pointing, gripping), the available candidates (one active drawing per pose,
from ``HandPoseLibrary``: user's choice > approved > original > sleeve-free store > others), their review status,
their wrist attachment (WristAlignmentController / rest alignment) and the current and previous hand state.

Criteria (each 0..1, weighted mean; weights in ``WEIGHTS``, per-character overrides in
``<package>/natural_pose/hand_rules.json`` and in the editor project):

* gesture: the action's preference order (``ACTIONS``); forbidden gestures are excluded;
* anatomy: the drawing is that side's hand (handedness measured at extraction; a mirrored copy scores less), and
  the wrist rotation the gesture needs stays inside the wrist limit;
* wrist: attachment quality (open wrist cut vs closed outline, rest alignment status, wrist fit in the sleeve);
* silhouette: the gesture's readable feature in this pose (index toward the target, thumb up, grip on the target);
* view: how far the drawing is turned from the way it was drawn (a cupped palm turned sideways stops reading as
  cupped); tolerances per pose in ``UPRIGHT_TOL``;
* continuity: the same drawing as the previous frame;
* fidelity: original pixels > reference-derived sheet hands > sleeve-free concept drawings > mirrored or
  sleeve-attached ones;
* approval: approved or original > pending review (pending drawings are candidates only when previewing).

Temporal rules (``HandSelectionState`` per side): a held gesture is not switched while the action is unchanged and
the drawing is still eligible; otherwise a new drawing must beat the current one by ``HYSTERESIS`` after at least
``MIN_HOLD_S``; an action change is the meaningful moment and switches at once (after ``MIN_HOLD_ACTION_S``, so a
flickering action cannot pop the hand). Explaining varies the gesture between beats. Symmetric actions (standing,
walking, running, shrugging: coordinated arms) use the same gesture on both hands when one is available to both and
costs each side at most SYMMETRY_TOLERANCE.

Phase 3.7: hard constraints run before any score (``constraints``), so a high score never makes an unsuitable
drawing acceptable. A candidate is rejected, with the reason recorded, when:
- it is missing, rejected in review, excluded by the user, or pending review outside a review preview;
- it is the wrong hand: the drawing's handedness (``HandAssetMetadata``, else the extractor's measurement) differs
  from the side;
- its gesture is forbidden for the action, or the action needs a target it does not have;
- it would show the wrong palm view. The action's ``palm`` classes are checked by ``HandOrientationAnalyzer`` on the
  drawing as placed in this pose. A flat rotation cannot turn a camera-facing palm upward, so such a drawing is
  rejected, never rotated into a fake fit;
- it is turned further from how it was drawn than its rotation tolerance allows (a relaxed hand turned 80 deg reads
  as a presenting hand; a cupped hand turned 180 deg is palm-down);
- its metadata is invalid while the action needs that metadata.

When nothing passes, the decision is NEEDS_ARTWORK (the drawing to request is named). The engine then falls back to
the action's ``fallback`` action, which is reported and never silent.

``select`` explains every decision: the chosen drawing with its criteria, each rejected candidate with the
constraint it failed, the missing drawings, the approval mode (``production``: approved drawings and the original
hand only; ``review``: pending drawings may be previewed and are labelled) and the fallback.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..handmeta import HandOrientationAnalyzer, approval_state
from ..phase2.skeleton import SIDE_SIGN, SIDES

# The gesture rule dominates (the user's per-action rules), then anatomy, attachment and readability. Approval and
# fidelity separate drawings of the same gesture: an approved drawing beats a pending one; when pending art is
# previewed it still loses to the original hand unless its gesture fits the action better.
# Phase 3.7 adds "palm" (how well the shown palm matches the action's preferred palm view) and "proportion" (the
# drawing's wrist width against the sleeve opening). Handedness moved to the hard constraints; "anatomy" now scores
# the wrist-axis fit of the gesture's aim.
WEIGHTS: dict[str, float] = {"gesture": 0.32, "palm": 0.08, "anatomy": 0.08, "wrist": 0.10, "silhouette": 0.08,
                             "view": 0.08, "proportion": 0.04, "continuity": 0.10, "fidelity": 0.04, "approval": 0.08}
CAMERA_PALMS = ["PALM_TO_CAMERA", "BACK_TO_CAMERA", "EDGE_ON"]  # what a hanging/forward hand may show

# Step 6 rules. prefer: best first; forbid: never chosen automatically for the action; aim: the feature the wrist
# turns toward the goal (within the wrist limit); max_turn: largest extra wrist rotation the action allows;
# symmetric: both hands share the gesture when possible; vary: alternate gestures between beats.
ACTIONS: dict[str, dict[str, Any]] = {
    "relaxed_standing": {"prefer": ["relaxed", "open_palm", "partially_closed"],
                         "forbid": ["pointing", "thumbs_up", "gripping", "fist"], "max_turn": 0.0, "palm": CAMERA_PALMS,
                         "why": "relaxed hands, neutral wrists"},
    "walking": {"prefer": ["relaxed", "partially_closed", "open_palm"],
                "forbid": ["pointing", "thumbs_up", "gripping", "palm_down"], "max_turn": 0.0, "symmetric": True,
                "palm": CAMERA_PALMS,
                "why": "relaxed or partially closed hands; no pointing or thumbs-up while walking"},
    "running": {"prefer": ["fist", "relaxed", "partially_closed"],
                "forbid": ["pointing", "thumbs_up", "gripping", "palm_down", "cupped"], "max_turn": 0.0,
                "palm": CAMERA_PALMS,
                "symmetric": True, "why": "loose fists or relaxed hands, the same on both arms, no wrist twist"},
    "pointing": {"prefer": ["pointing"], "forbid": [], "aim": "index_tip", "max_turn": 45.0,
                 "why": "pointing hand, index toward the target (arm IK reaches, the wrist aims)"},
    # Phase 3.7: "shrugging" is the expressive palm-up shrug. Only drawings that show the palm up in the solved pose
    # pass; a camera-facing hand turned sideways (the Phase 3.6 preview) is rejected. Fallback: the subtle shrug.
    "shrugging": {"prefer": ["palm_up", "cupped"], "forbid": ["pointing", "fist", "thumbs_up", "gripping"],
                  "palm": ["PALM_UP"], "max_turn": 0.0, "symmetric": True, "fallback": "shrug_subtle",
                  "why": "expressive shrug: palms up (an open palm-up drawing; a cupped palm-up drawing as an "
                         "approximation)"},
    "shrug_expressive": {"alias": "shrugging"},
    "shrug_subtle": {"prefer": ["open_palm", "relaxed"], "forbid": ["pointing", "fist", "thumbs_up", "gripping", "cupped"],
                     "palm": ["PALM_TO_CAMERA", "EDGE_ON"], "max_turn": 0.0, "symmetric": True,
                     "why": "subtle shrug: hands low, palms turned toward the camera or edge-on"},
    "explaining": {"prefer": ["open_palm", "cupped", "palm_up", "relaxed"], "forbid": ["thumbs_up", "gripping"],
                   "palm": ["PALM_TO_CAMERA", "PALM_UP", "EDGE_ON"], "palm_strict": False, "max_turn": 0.0, "vary": True,
                   "why": "open or cupped hands, varied between beats"},
    "conversation": {"alias": "explaining"},
    "hand_on_desk": {"prefer": ["palm_down", "relaxed", "open_palm"], "forbid": ["pointing", "thumbs_up", "cupped"],
                     "palm": ["PALM_DOWN", "BACK_TO_CAMERA", "PALM_TO_CAMERA"], "max_turn": 0.0,
                     "why": "hand resting on the desk top: palm down (seen from above) preferred"},
    "angry": {"prefer": ["fist", "pointing"], "forbid": ["thumbs_up", "cupped", "open_palm"], "max_turn": 30.0,
              "pointing_needs_target": True, "aim": "index_tip",
              "why": "fists; pointing only when the action has a target"},
    "gripping": {"prefer": ["gripping", "fist"], "forbid": ["pointing", "thumbs_up", "open_palm"], "grip": True,
                 "max_turn": 0.0, "why": "grip hand on the interaction target"},
    "thumbs_up": {"prefer": ["thumbs_up"], "forbid": [], "aim": "thumb_tip", "aim_dir": (0.0, -1.0), "max_turn": 45.0,
                  "why": "thumbs-up, thumb turned to read upward"},
    "hand_on_hip": {"prefer": ["relaxed", "cupped", "fist"], "forbid": ["pointing", "thumbs_up", "gripping"],
                    "max_turn": 0.0, "max_view_dev": 70.0,
                    "why": "relaxed or cupped hands on the hips, no extreme rotation"},
    "resting": {"prefer": ["palm_down", "relaxed", "open_palm"], "forbid": ["pointing", "thumbs_up"], "max_turn": 0.0,
                "why": "hands resting (on the knees)"},
    "raised": {"prefer": ["open_palm", "relaxed"], "forbid": ["pointing", "thumbs_up", "gripping"], "max_turn": 0.0,
               "palm": ["PALM_TO_CAMERA", "BACK_TO_CAMERA"],
               "why": "raised open hands"},
}

# an explicit (user / keyframe) choice is checked with no gesture preference: only the action-independent constraints
OVERRIDE_RULE: dict[str, Any] = {"prefer": [], "forbid": [], "max_turn": 0.0, "why": "explicit choice"}

# preset -> action (per side where the preset uses the arms differently)
PRESET_ACTIONS: dict[str, Any] = {
    "relaxed_standing": "relaxed_standing", "hands_at_sides": "relaxed_standing",
    "head_tilt_compensated": "relaxed_standing", "side_lean": "relaxed_standing",
    "leaning_forward": "relaxed_standing", "leaning_backward": "relaxed_standing",
    "hands_on_hips": "hand_on_hip", "pointing_speaking": {"r": "pointing", "l": "relaxed_standing"},
    "conversational_gesture": {"r": "explaining", "l": "relaxed_standing"}, "one_hand_shrug": "shrugging",
    "casual_walk": "walking", "running": "running", "sitting": "resting", "natural_squat": "resting",
    "both_hands_raised": "raised",
}

# how far (deg) a drawing may be turned from the way it was drawn before it stops reading as that gesture: full
# score up to half, zero at the tolerance. Hanging-hand drawings (wrist at the top) tolerate most.
UPRIGHT_TOL = {"relaxed": 130.0, "open_palm": 160.0, "fist": 170.0, "pointing": 180.0, "thumbs_up": 60.0,
               "gripping": 100.0, "palm_down": 80.0, "cupped": 70.0, "palm_up": 70.0, "partially_closed": 130.0}
GESTURE_SCORE = (1.0, 0.8, 0.62, 0.48)  # by position in the action's preference list
UNLISTED_SCORE = 0.15
FIDELITY = {"original": 1.0, "phase2": 0.8, "store_sleeve_free": 0.7, "store_attached": 0.5, "derived_mirror": 0.6}
PENDING_APPROVAL_SCORE = 0.6  # pending drawings are candidates only when previewing (allow_pending)
HYSTERESIS = 0.12
MIN_HOLD_S = 0.4
MIN_HOLD_ACTION_S = 0.25  # an action that flickers cannot switch the hand more than 4x per second
VARIETY_PENALTY = 0.15
SYMMETRY_TOLERANCE = 0.05  # per side: a shared gesture may cost each side at most this much
WRIST_LIMIT_DEG = 60.0  # skeleton LIMITS["hand"]["bend"]
AIM_RATE_DEG_S = 180.0  # in sequences the wrist aim eases toward its goal at most this fast (continuous wrist)
AIM_FULL_DEG, AIM_NONE_DEG = 25.0, 70.0  # target aims: full wrist aim when the forearm is within 25 deg of the goal


def ease_aim(target: float, state: "HandSelectionState | None", t: float | None) -> tuple[float, bool]:
    """Rate-limit the wrist aim between frames (artwork switches are discrete, the wrist is not): from the aim of
    the previous frame toward ``target`` at AIM_RATE_DEG_S. Without a state or time (a single pose) and on the first
    frame of a sequence the full aim applies. Returns (applied, limited)."""
    if state is None or t is None:
        return target, False
    if state.aim_t is None:
        out = target
    else:
        step = AIM_RATE_DEG_S * max(0.0, t - state.aim_t)
        out = state.aim + max(-step, min(step, target - state.aim))
    state.aim, state.aim_t = out, t
    return out, abs(out - target) > 1e-6


def _wrap(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def _ang(v) -> float:
    return math.degrees(math.atan2(-float(v[0]), float(v[1])))


def _dir(deg: float) -> tuple[float, float]:
    t = math.radians(deg)
    return (-math.sin(t), math.cos(t))


def default_selection_state() -> dict[str, Any]:
    """Editor project (schema v4) state: per-side action override and target, per-character weight overrides."""
    return {"actions": {"r": None, "l": None}, "targets": {"r": None, "l": None}, "weights": {}}


def load_rules(pkg: str | Path | None) -> dict[str, Any]:
    """Per-character overrides (``<package>/natural_pose/hand_rules.json``): {"weights": {...}, "actions":
    {action: {prefer, forbid, max_turn ...}}}. Missing file: no overrides."""
    if pkg is None:
        return {}
    f = Path(pkg) / "natural_pose" / "hand_rules.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}


@dataclass
class HandContext:
    side: str
    action: str
    forearm_deg: float  # world angle of the posed forearm bone (ang: 0 = hanging, clockwise on screen)
    hand_deg: float  # world angle of the posed hand bone
    hand_rest_deg: float  # rest world angle of the hand bone
    elbow_flex_deg: float
    wrist_bend_deg: float  # anatomical wrist bend (+ toward the midline), limit +-WRIST_LIMIT_DEG
    wrist: tuple[float, float]  # posed wrist joint (rest-canvas px)
    view: str = "front"
    target: tuple[float, float] | None = None
    movement: tuple[float, float] | None = None  # wrist displacement since the previous frame (px)
    t: float | None = None  # seconds
    beat: int | None = None  # explaining: gesture beat index

    def to_json(self) -> dict[str, Any]:
        r = lambda v: None if v is None else [round(float(x), 2) for x in v]
        return {"side": self.side, "action": self.action, "forearm_deg": round(self.forearm_deg, 2),
                "hand_deg": round(self.hand_deg, 2), "elbow_flex_deg": round(self.elbow_flex_deg, 2),
                "wrist_bend_deg": round(self.wrist_bend_deg, 2), "wrist": r(self.wrist), "view": self.view,
                "target": r(self.target), "movement": r(self.movement), "t": self.t, "beat": self.beat}


@dataclass
class HandSelectionState:
    pose: str | None = None
    cid: str | None = None
    since: float | None = None
    score: float = 0.0
    action: str | None = None
    beat: int | None = None
    history: list[str] = field(default_factory=list)
    aim: float = 0.0  # wrist aim applied last frame (deg)
    aim_t: float | None = None

    def to_json(self) -> dict[str, Any]:
        return {"pose": self.pose, "cid": self.cid, "since": self.since, "score": round(self.score, 4),
                "action": self.action, "beat": self.beat, "history": self.history[-8:], "aim": round(self.aim, 3)}


class NaturalHandSelectionEngine:
    def __init__(self, weights: dict[str, float] | None = None, actions: dict[str, dict[str, Any]] | None = None):
        self.weights = {**WEIGHTS, **(weights or {})}
        self.analyzer = HandOrientationAnalyzer()
        self.actions = {k: dict(v) for k, v in ACTIONS.items()}
        for k, v in (actions or {}).items():
            self.actions[k] = {**self.actions.get(k, {"prefer": [], "forbid": []}), **v}

    @classmethod
    def for_character(cls, rules: dict[str, Any] | None, project_weights: dict[str, float] | None = None
                      ) -> "NaturalHandSelectionEngine":
        rules = rules or {}
        return cls({**rules.get("weights", {}), **(project_weights or {})}, rules.get("actions"))

    # -- per-candidate criteria ---------------------------------------------------------------------------
    @staticmethod
    def _source(v) -> str:
        if v.cid == "original":
            return "original"
        if v.cid.startswith("phase2:"):
            return "phase2"
        if (v.meta or {}).get("derived"):
            return "derived_mirror"
        return "store_sleeve_free" if (v.meta or {}).get("sleeve") == "none" else "store_attached"

    @staticmethod
    def drawing_angle(v) -> float | None:
        """Rest placement angle of a stored drawing (sprite 'down' -> world); None when unknown."""
        pl = v.placement or {}
        fin = pl.get("final") or {}
        return fin.get("angle_deg")

    def aim(self, v, ctx: HandContext, rule: dict[str, Any]) -> dict[str, Any] | None:
        """Extra wrist rotation that turns the gesture's feature toward its goal, clamped to the action's max_turn
        and the wrist limit. Goal: pointing -> the target (or along the forearm without one); thumbs-up -> up."""
        feat = rule.get("aim")
        if not feat:
            return None
        f = ((v.meta or {}).get("features") or {}).get(f"{feat}_dir")
        a0 = self.drawing_angle(v)
        if f is None or a0 is None:
            return {"feature": feat, "measured": False, "note": "no measured feature direction for this drawing"}
        cur = _wrap(_ang(f) + a0 + (ctx.hand_deg - ctx.hand_rest_deg))  # world ang of the feature now
        if rule.get("aim_dir") is not None:
            goal = _ang(rule["aim_dir"])
        elif ctx.target is not None:
            goal = _ang((ctx.target[0] - ctx.wrist[0], ctx.target[1] - ctx.wrist[1]))
        else:
            goal = ctx.forearm_deg
        need_full = need = _wrap(goal - cur)
        weight = 1.0
        if rule.get("aim_dir") is None:
            # aiming at a target: the arm (IK) does the gross aim, the wrist the fine aim. While the forearm still
            # points far from the goal (an arm on its way up) the wrist stays near neutral instead of twisting
            dev = abs(_wrap(goal - ctx.forearm_deg))
            u = min(max((dev - AIM_FULL_DEG) / (AIM_NONE_DEG - AIM_FULL_DEG), 0.0), 1.0)
            weight = 1.0 - u * u * (3.0 - 2.0 * u)
            need = need * weight
        sg = SIDE_SIGN[ctx.side]
        # anatomical bend after a world rotation d: bend' = bend - sg * d; keep |bend'| <= WRIST_LIMIT_DEG
        lo = min((ctx.wrist_bend_deg - WRIST_LIMIT_DEG) * sg, (ctx.wrist_bend_deg + WRIST_LIMIT_DEG) * sg)
        hi = max((ctx.wrist_bend_deg - WRIST_LIMIT_DEG) * sg, (ctx.wrist_bend_deg + WRIST_LIMIT_DEG) * sg)
        mt = float(rule.get("max_turn", 0.0))
        apply = max(lo, min(hi, max(-mt, min(mt, need))))
        f2 = lambda x: round(float(x), 2)
        return {"feature": feat, "measured": True, "goal_deg": f2(goal), "feature_deg": f2(cur), "weight": round(weight, 3),
                "needed_deg": f2(need_full), "weighted_need_deg": f2(need), "applied_deg": f2(apply),
                "residual_deg": f2(_wrap(need_full - apply)),
                "wrist_bend_after_deg": f2(ctx.wrist_bend_deg - sg * apply)}

    # -- Phase 3.7 hard constraints ----------------------------------------------------------------------------
    @staticmethod
    def hand_meta(v) -> dict[str, Any] | None:
        return (v.meta or {}).get("hand_meta")

    def rotation_from_drawn(self, v, ctx: HandContext, turn: float = 0.0) -> float | None:
        """In-plane rotation of the drawing away from how it was drawn, in this pose: store drawings from their rest
        placement angle; the original hand from the rest (T-pose) hand bone it was drawn on; sheet hands (drawn
        hanging) from straight down."""
        a0 = self.drawing_angle(v)
        if a0 is not None:
            return _wrap(a0 + (ctx.hand_deg - ctx.hand_rest_deg) + turn)
        if v.cid == "original":
            return _wrap(ctx.hand_deg - ctx.hand_rest_deg + turn)
        return _wrap(ctx.hand_deg + turn)

    @staticmethod
    def palm_strict(rule: dict[str, Any]) -> bool:
        """A palm requirement narrower than 'anything a hanging or forward hand shows' needs verified metadata."""
        need = rule.get("palm") or []
        if not need or rule.get("palm_strict") is False:
            return False
        return bool(rule.get("palm_strict")) or not set(need) <= set(CAMERA_PALMS)

    def constraints(self, v, ctx: HandContext, rule: dict[str, Any], allow_pending: bool,
                    aim: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """Hard constraints of one candidate. Returns (violations, palm analysis). Any violation rejects the
        candidate whatever its score would be."""
        out: list[dict[str, Any]] = []
        side_name = "right" if ctx.side == "r" else "left"
        hm = self.hand_meta(v)
        add = lambda c, why: out.append({"constraint": c, "reason": why})
        if not v.available or v.status == "missing" or v.layer is None:
            add("missing", "no drawing (NEEDS_ARTWORK)")
            return out, None
        if v.status == "rejected":
            add("approval", "rejected in review: never used")
        elif v.status == "pending_review" and not allow_pending:
            add("approval", "pending review: production mode uses approved drawings and the original hand only")
        if hm is not None and hm.get("excluded"):
            add("excluded", "excluded by the user in the hand inspector")
        if v.pose in rule.get("forbid", []):
            add("gesture", f"{v.pose} is not used for {ctx.action}")
        if v.pose == "pointing" and rule.get("pointing_needs_target") and ctx.target is None:
            add("gesture", "pointing needs a target for this action")
        # handedness: the inspected/corrected metadata, else the extractor's measurement
        hand = (hm or {}).get("handedness")
        src = "metadata"
        if hand is None:
            hand = ((v.meta or {}).get("handedness") or {}).get("anatomical")
            src = "extractor"
        if hand is not None and hand != side_name:
            add("handedness", f"drawn as a {hand} hand ({src}); this is the {side_name} hand")
        if hm is not None and hm.get("errors"):
            add("metadata", "invalid metadata: " + "; ".join(hm["errors"]))
            hm = {k: v for k, v in hm.items() if k not in ("palm_normal_sprite", "rotation_tolerance_deg")}
        # palm view and rotation from the drawn orientation (as placed in this pose)
        turn = float((aim or {}).get("applied_deg") or 0.0)
        rot = self.rotation_from_drawn(v, ctx, turn)
        pa = None
        if hm is not None:
            pa = self.analyzer.analyze(hm, rot, ctx.forearm_deg)
            tol = hm.get("rotation_tolerance_deg")
            if tol is not None and abs(rot) > float(tol) + 1e-9:
                add("rotation", f"turned {abs(rot):.0f} deg from how it was drawn (tolerance {float(tol):.0f} deg): "
                                "a flat rotation does not change the drawing's perspective")
        need = rule.get("palm")
        if need:
            cls = (pa or {}).get("palm_class", "AMBIGUOUS")
            unknown = hm is None or hm.get("palm_normal_sprite") is None
            if unknown and not self.palm_strict(rule):
                pass  # a generic camera-palm action: an unverified drawing passes (scored lower, flagged)
            elif cls not in need:
                if unknown:
                    add("palm", f"palm view unknown (no metadata); {ctx.action} needs {' or '.join(need)}")
                else:
                    reach = [c for c in need if self.analyzer.reachable_by_rotation(hm, c)]
                    why = f"shows {cls} in this pose; {ctx.action} needs {' or '.join(need)}"
                    if not reach:
                        why += " - no flat rotation of this drawing shows that (needs a different drawing)"
                    add("palm", why)
        return out, pa

    def score(self, v, ctx: HandContext, rule: dict[str, Any], state: HandSelectionState | None,
              allow_pending: bool) -> dict[str, Any] | None:
        """Criteria of one candidate (None: not eligible). ``v``: a HandVariant (active drawing of a pose)."""
        r = self.evaluate(v, ctx, rule, state, allow_pending)
        return None if r["rejected"] else r

    def evaluate(self, v, ctx: HandContext, rule: dict[str, Any], state: HandSelectionState | None,
                 allow_pending: bool) -> dict[str, Any]:
        """Constraints, then criteria. Rejected candidates keep their violations (for the explanation)."""
        pose = v.pose
        why: list[str] = []
        aim = self.aim(v, ctx, rule) if v.available else None
        viol, pa = self.constraints(v, ctx, rule, allow_pending, aim)
        base = {"pose": pose, "cid": v.cid, "status": v.status, "approval_state": approval_state(v.status),
                "source": self._source(v), "palm": pa}
        if viol:
            return {**base, "rejected": True, "violations": viol, "total": None}
        prefer = rule.get("prefer", [])
        s: dict[str, float] = {}
        s["gesture"] = GESTURE_SCORE[min(prefer.index(pose), len(GESTURE_SCORE) - 1)] if pose in prefer else UNLISTED_SCORE
        # palm: the action's palm preference order (first listed = 1.0)
        need = rule.get("palm")
        cls = (pa or {}).get("palm_class")
        if need and cls not in (None, "AMBIGUOUS"):
            s["palm"] = 1.0 if cls == need[0] else 0.8
        else:  # no requirement, or a permissive one with an unverified drawing (flagged in the notes, not scored down)
            s["palm"] = 1.0
        hd = (v.meta or {}).get("handedness") or {}
        side_name = "right" if v.side == "r" else "left"
        anat = 1.0
        if hd and hd.get("drawn_as") and hd.get("drawn_as") != side_name:
            anat, _ = 0.85, why.append("mirrored copy of the other hand's drawing")
        if aim and aim.get("measured"):
            anat *= max(0.0, 1.0 - abs(aim["residual_deg"]) / 120.0) if abs(aim["needed_deg"]) > float(rule.get("max_turn", 0)) else 1.0
        s["anatomy"] = round(anat, 4)
        # wrist attachment
        pl = (v.placement or {}).get("params") or {}
        wq = 1.0
        prop = 1.0
        if pl:
            if pl.get("manual_review"):
                wq *= 0.7
                why.append(f"wrist pivot estimated ({pl.get('opening', '?').replace('_', ' ')}), review")
            fit = pl.get("wrist_fit")
            if fit is not None and not 0.7 <= fit <= 1.3:
                prop = max(0.3, 1.0 - (abs(fit - 1.0) - 0.3) * 2.0)
                why.append(f"wrist {fit:.2f}x the sleeve opening")
            if fit is not None and not 0.8 <= fit <= 1.2:
                wq *= max(0.3, 1.0 - (abs(fit - 1.0) - 0.2) * 2.0)
        elif v.cid.startswith("phase2:"):
            wq = 0.8  # sheet hands: placed by the Phase 2 offsets, attachment not re-measured
        s["wrist"] = round(wq, 4)
        s["proportion"] = round(prop, 4)
        # silhouette: the gesture's readable feature
        sil = 1.0
        if aim is not None:
            if aim.get("measured"):
                sil = max(0.0, math.cos(math.radians(min(abs(aim["residual_deg"]), 90.0))))
            else:
                sil = 0.6
        if rule.get("grip") and pose == "gripping":
            gc = ((v.meta or {}).get("features") or {}).get("grip_center")
            sil = 1.0 if gc is not None else 0.6
        s["silhouette"] = round(sil, 4)
        # view: how far the drawing is turned from the way it was drawn (soft, within the hard tolerance)
        turn = (aim or {}).get("applied_deg", 0.0) or 0.0
        a0 = self.drawing_angle(v)
        if a0 is not None:
            dev = abs(_wrap(a0 + (ctx.hand_deg - ctx.hand_rest_deg) + turn))
        else:  # reference hand / sheet hands: drawn hanging, turned with the hand bone
            dev = abs(_wrap(ctx.hand_deg + turn))
        hm = self.hand_meta(v)
        tol = float(rule.get("max_view_dev", (hm or {}).get("rotation_tolerance_deg") or UPRIGHT_TOL.get(pose, 120.0)))
        if hm is not None and v.cid == "original":
            dev = abs(self.rotation_from_drawn(v, ctx, turn))
        view = 1.0 if dev <= tol / 2 else max(0.0, 1.0 - (dev - tol / 2) / max(tol / 2, 1e-6))
        if view < 0.5:
            why.append(f"turned {dev:.0f} deg from how it was drawn")
        s["view"] = round(view, 4)
        s["continuity"] = 1.0 if state is None or state.pose is None or state.pose == pose else 0.0
        src = self._source(v)
        s["fidelity"] = FIDELITY[src]
        s["approval"] = 1.0 if v.status in ("original", "approved") else PENDING_APPROVAL_SCORE
        s = {k: round(float(x), 4) for k, x in s.items()}
        wsum = sum(self.weights.get(k, 0.0) for k in s)
        total = sum(self.weights.get(k, 0.0) * s[k] for k in s) / max(wsum, 1e-9)
        if v.status == "pending_review":
            why.append("pending review: preview only")
        if need and cls in (None, "AMBIGUOUS"):
            why.append("palm view not verified (no hand metadata)")
        return {**base, "rejected": False, "violations": [], "scores": s, "total": round(float(total), 4), "aim": aim,
                "view_dev_deg": round(float(dev), 1), "notes": why}

    # -- selection ------------------------------------------------------------------------------------------
    def rule(self, action: str) -> dict[str, Any]:
        r = self.actions.get(action) or self.actions["relaxed_standing"]
        own: dict[str, Any] = {}
        seen = set()
        while r.get("alias") and r["alias"] not in seen:  # alias actions share their target's rule
            own = {**{k: v for k, v in r.items() if k != "alias"}, **own}
            seen.add(r["alias"])
            r = self.actions.get(r["alias"]) or self.actions["relaxed_standing"]
        return {**r, **own} if own else r

    def rank(self, lib, ctx: HandContext, state: HandSelectionState | None, allow_pending: bool) -> list[dict[str, Any]]:
        return [r for r in self.rank_all(lib, ctx, state, allow_pending) if not r["rejected"]]

    def rank_all(self, lib, ctx: HandContext, state: HandSelectionState | None, allow_pending: bool) -> list[dict[str, Any]]:
        """Every candidate of the side (active drawing per pose), eligible ones ranked first."""
        rule = self.rule(ctx.action)
        out = [self.evaluate(v, ctx, rule, state, allow_pending) for pose, v in lib.hands[ctx.side].items()]
        ok = [r for r in out if not r["rejected"]]
        bad = [r for r in out if r["rejected"]]
        prefer = rule.get("prefer", [])
        if rule.get("vary") and state is not None and state.beat is not None and ctx.beat is not None \
                and ctx.beat != state.beat and state.pose is not None:
            # a new gesture beat: continuity no longer favours the last gesture, repeating it costs VARIETY_PENALTY
            for r in ok:
                r["scores"]["continuity"] = 1.0
                wsum = max(sum(self.weights.get(k, 0.0) for k in r["scores"]), 1e-9)
                r["total"] = round(sum(self.weights.get(k, 0.0) * r["scores"][k] for k in r["scores"]) / wsum, 4)
                if r["pose"] == state.pose:
                    r["total"] = round(r["total"] - VARIETY_PENALTY, 4)
                    r["notes"].append("same gesture as the previous beat")
        ok.sort(key=lambda r: (-r["total"], prefer.index(r["pose"]) if r["pose"] in prefer else 99, r["pose"], r["cid"]))
        bad.sort(key=lambda r: (prefer.index(r["pose"]) if r["pose"] in prefer else 99, r["pose"]))
        return ok + bad

    def needs_artwork(self, lib, ctx: HandContext, rule: dict[str, Any], ranked_all: list[dict[str, Any]],
                      chosen: str | None) -> list[dict[str, Any]]:
        """Preferred gestures (better than the chosen one) that no drawing can satisfy here, with the reason."""
        prefer = rule.get("prefer", [])
        upto = prefer.index(chosen) if chosen in prefer else len(prefer)
        out = []
        for p in prefer[:upto]:
            r = next((x for x in ranked_all if x["pose"] == p), None)
            if r is None or any(v["constraint"] == "missing" for v in r.get("violations", [])):
                out.append({"pose": p, "status": "NEEDS_ARTWORK", "reason": "no drawing", "palm": rule.get("palm")})
            elif r["rejected"]:
                kinds = {v["constraint"] for v in r["violations"]}
                st = "NEEDS_APPROVAL" if kinds == {"approval"} and r["status"] == "pending_review" else "NEEDS_ARTWORK"
                out.append({"pose": p, "status": st, "reason": "; ".join(v["reason"] for v in r["violations"]),
                            "palm": rule.get("palm"), "cid": r["cid"]})
        return out

    def select(self, lib, ctx: HandContext, state: HandSelectionState | None = None,
               allow_pending: bool = False, _depth: int = 0) -> dict[str, Any]:
        """The automatic choice for one side, with the temporal rules applied against ``state`` (updated in
        place when given). Returns the chosen pose (None: nothing eligible -> the group default), the ranking,
        every rejected candidate with its reason, missing drawings and the fallback used."""
        ranked_all = self.rank_all(lib, ctx, state, allow_pending)
        ranked = [r for r in ranked_all if not r["rejected"]]
        rule = self.rule(ctx.action)
        best = ranked[0] if ranked else None
        arm_ok = {c: self.analyzer.arm_allows(c, ctx.forearm_deg) for c in (rule.get("palm") or [])}
        decision = {"side": ctx.side, "action": ctx.action, "rule": rule.get("why"), "ranking": ranked[:6],
                    "rejected": [{"pose": r["pose"], "cid": r["cid"], "status": r["status"],
                                  "violations": r["violations"], "palm": (r.get("palm") or {}).get("palm_class")}
                                 for r in ranked_all if r["rejected"]],
                    "mode": "review" if allow_pending else "production",
                    "palm_required": rule.get("palm"),
                    "arm_palm_reachable": {c: ok for c, (ok, _) in arm_ok.items()} or None,
                    "context": ctx.to_json(), "chosen": best["pose"] if best else None,
                    "cid": best["cid"] if best else None, "score": best["total"] if best else None,
                    "reason": "highest score after the hard constraints" if best else "no eligible drawing",
                    "aim": best.get("aim") if best else None, "held": False, "fallback": None}
        decision["needs_artwork"] = self.needs_artwork(lib, ctx, rule, ranked_all, decision["chosen"])
        if best is None and rule.get("fallback") and _depth < 2:
            fb_ctx = HandContext(**{**ctx.__dict__, "action": rule["fallback"]})
            fb = self.select(lib, fb_ctx, None, allow_pending, _depth + 1)
            na = [n["pose"] for n in decision["needs_artwork"] if n["status"] == "NEEDS_ARTWORK"]
            ap_ = [n["pose"] for n in decision["needs_artwork"] if n["status"] == "NEEDS_APPROVAL"]
            what = "; ".join(x for x in (f"NEEDS_ARTWORK: {', '.join(na)}" if na else "",
                                         f"NEEDS_APPROVAL: {', '.join(ap_)}" if ap_ else "") if x) or "-"
            decision["fallback"] = {"action": rule["fallback"], "chosen": fb["chosen"], "cid": fb["cid"],
                                    "reason": f"{ctx.action}: no drawing passes the hard constraints ({what}); "
                                              f"showing the {rule['fallback']} choice"}
            if fb["chosen"] is not None:
                best = next((r for r in fb["ranking"] if r["pose"] == fb["chosen"]), None)
                decision.update(chosen=fb["chosen"], cid=fb["cid"], score=fb["score"], aim=fb.get("aim"),
                                reason=decision["fallback"]["reason"])
        if state is not None and state.pose is not None and best is not None and decision["chosen"] != state.pose \
                and decision["fallback"] is None:
            prev = next((r for r in ranked if r["pose"] == state.pose), None)
            held_for = None if (ctx.t is None or state.since is None) else ctx.t - state.since
            same_action = state.action == ctx.action
            new_beat = bool(rule.get("vary")) and ctx.beat is not None and ctx.beat != state.beat
            keep, why = False, None
            if not same_action:
                # a flickering action: hold the previous drawing if it still passes its own action's constraints
                pv = lib.hands[ctx.side].get(state.pose)
                held_ok = None
                if pv is not None and held_for is not None and held_for < MIN_HOLD_ACTION_S and state.action:
                    pctx = HandContext(**{**ctx.__dict__, "action": state.action})
                    held_ok = self.evaluate(pv, pctx, self.rule(state.action), None, allow_pending)
                    held_ok = None if held_ok["rejected"] else held_ok
                if held_ok is not None:
                    keep, why = True, f"action changed {held_for:.2f}s after the last switch (< {MIN_HOLD_ACTION_S}s)"
                    prev = held_ok
                else:
                    why = "action changed: switch at the action boundary"
            elif new_beat:
                why = "new gesture beat"
            elif prev is None:
                why = "the previous drawing is no longer eligible (it fails a hard constraint here)"
            elif state.pose in rule.get("prefer", []):
                keep, why = True, "held gesture: the action is unchanged"
            elif best["total"] < prev["total"] + HYSTERESIS:
                keep, why = True, (f"hysteresis: {best['pose']} {best['total']:.2f} does not beat "
                                   f"{prev['pose']} {prev['total']:.2f} by {HYSTERESIS}")
            elif held_for is not None and held_for < MIN_HOLD_S:
                keep, why = True, f"minimum hold {MIN_HOLD_S}s not reached"
            else:
                why = "clearly better drawing"
            if keep:
                decision.update(chosen=state.pose, cid=prev["cid"], score=prev["total"], reason=why,
                                aim=prev.get("aim"), held=True)
            else:
                decision["reason"] = why
        elif state is not None and state.pose is not None and best is not None and decision["fallback"] is None:
            decision["reason"] = "unchanged"
        if state is not None and decision["chosen"] is not None:
            if decision["chosen"] != state.pose:
                state.since = ctx.t
                state.history.append(decision["chosen"])
            state.pose, state.cid, state.score = decision["chosen"], decision["cid"], float(decision["score"] or 0.0)
            state.action = ctx.action
            state.beat = ctx.beat
        decision["explanation"] = explain(decision)
        return decision

    def select_pair(self, lib, ctxs: dict[str, HandContext], states: dict[str, HandSelectionState] | None,
                    allow_pending: bool = False) -> dict[str, dict[str, Any]]:
        """Both sides; symmetric actions share a gesture when one is eligible for both and within
        SYMMETRY_TOLERANCE of the sides' best scores."""
        states = states or {}
        out = {s: self.select(lib, ctxs[s], states.get(s), allow_pending) for s in SIDES if s in ctxs}
        if len(out) == 2 and all(self.rule(ctxs[s].action).get("symmetric") for s in SIDES) \
                and ctxs["r"].action == ctxs["l"].action and out["r"]["chosen"] != out["l"]["chosen"]:
            common: dict[str, float] = {}
            for p in {r["pose"] for r in out["r"]["ranking"]} & {r["pose"] for r in out["l"]["ranking"]}:
                common[p] = sum(next(r["total"] for r in out[s]["ranking"] if r["pose"] == p) for s in SIDES)
            if common:
                p = max(sorted(common), key=lambda k: common[k])
                cost = {s: out[s]["ranking"][0]["total"] - next(r["total"] for r in out[s]["ranking"] if r["pose"] == p)
                        for s in SIDES}
                if max(cost.values()) <= SYMMETRY_TOLERANCE:
                    for s in SIDES:
                        r = next(r for r in out[s]["ranking"] if r["pose"] == p)
                        out[s].update(chosen=p, cid=r["cid"], score=r["total"], aim=r.get("aim"),
                                      reason=f"symmetric action: both hands use {p}")
                        st = states.get(s)
                        if st is not None:
                            if st.pose != p and st.history and st.history[-1] != p:
                                st.history.append(p)
                            st.pose, st.cid, st.score = p, r["cid"], r["total"]
        return out


def explain(decision: dict[str, Any]) -> list[str]:
    """Plain-language explanation of a decision: what was chosen and why, what was rejected and why."""
    d = decision
    lines = []
    if d.get("chosen"):
        lines.append(f"{d['side']}: {d['action']} -> {d['chosen']} ({d['cid']}) - {d['reason']} [mode: {d['mode']}]")
    else:
        lines.append(f"{d['side']}: {d['action']} -> no drawing passes the hard constraints [mode: {d['mode']}]")
    for r in d.get("rejected", []):
        lines.append(f"  rejected {r['pose']} ({r['cid'] or 'missing'}): " + "; ".join(v["reason"] for v in r["violations"]))
    for n in d.get("needs_artwork", []):
        lines.append(f"  {n['status']}: {n['pose']} - {n['reason']}")
    if d.get("fallback"):
        lines.append(f"  fallback: {d['fallback']['reason']}")
    return lines


def context_from_pose(solver, sk, pose, it, side: str, action: str, target=None, previous_wrist=None,
                      t: float | None = None, beat: int | None = None, view: str = "front") -> HandContext:
    """HandContext from a solved pose (SkeletalPoseSolver.anatomical and the hand bone's posed joint)."""
    th = pose.theta
    K = solver.K
    w = lambda n: _wrap(sk.bone(n).rest_angle + K.rot(th, n))
    a = solver.anatomical(pose)
    hb = sk.bone(f"hand_{side}")
    wrist = K.point(sk.idx[f"hand_{side}"], hb.head, pose)
    mv = None if previous_wrist is None else (float(wrist[0] - previous_wrist[0]), float(wrist[1] - previous_wrist[1]))
    return HandContext(side, action, float(w(f"forearm_{side}")), float(w(f"hand_{side}")), float(_wrap(hb.rest_angle)),
                       float(a[f"forearm_{side}_flexion"]), float(a[f"hand_{side}_bend"]), (float(wrist[0]), float(wrist[1])),
                       view, None if target is None else (float(target[0]), float(target[1])), mv, t, beat)


def action_for(preset: str, side: str, override: str | None = None) -> str:
    if override:
        return override
    a = PRESET_ACTIONS.get(preset, "relaxed_standing")
    return a.get(side, "relaxed_standing") if isinstance(a, dict) else a


def rules_json(engine: NaturalHandSelectionEngine) -> dict[str, Any]:
    """The selection rules as data (rig controls spec, report)."""
    return {"weights": engine.weights, "actions": engine.actions, "preset_actions": PRESET_ACTIONS,
            "upright_tolerance_deg": UPRIGHT_TOL, "gesture_scores": list(GESTURE_SCORE), "unlisted_score": UNLISTED_SCORE,
            "fidelity": FIDELITY, "pending_approval_score": PENDING_APPROVAL_SCORE, "temporal": {"hysteresis": HYSTERESIS, "min_hold_s": MIN_HOLD_S,
                                               "min_hold_action_s": MIN_HOLD_ACTION_S, "variety_penalty": VARIETY_PENALTY,
                                               "symmetry_tolerance": SYMMETRY_TOLERANCE},
            "wrist_limit_deg": WRIST_LIMIT_DEG, "aim_rate_deg_s": AIM_RATE_DEG_S,
            "aim_forearm_window_deg": [AIM_FULL_DEG, AIM_NONE_DEG],
            "resolution": "explicit user choice > switch keyframe > this engine (auto mode only) > group default",
            "hard_constraints": ["missing", "approval (production: approved + original only; review: + pending)",
                                 "excluded", "gesture (forbidden / needs target)", "handedness", "metadata",
                                 "palm view (HandOrientationAnalyzer, flat rotation only)",
                                 "rotation from the drawn orientation <= the drawing's tolerance"],
            "approval_states": ["APPROVED", "PENDING_REVIEW", "REJECTED", "MISSING"],
            "disclaimer": "scores rank the drawings the character has for an action; they do not measure visual quality"}
