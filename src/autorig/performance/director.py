"""PerformanceDirector: evaluates a performance timeline into frames, through the one motion pipeline.

Per frame ``t`` (a pure function of ``t``):
1. MotionLayerController composes the layers into pose parameters and hand poses (joint space, soft limits).
2. The parameters become an ``ArtisticPose``; ``ArtisticPosePlanner.apply`` builds the stage-1 intent from it.
3. WeightTransferController locks the feet, softens the free leg continuously and centres the balance window.
4. SecondaryMotionController writes the hair and hem lag (``PoseIntent.garment``).
5. If a hand or elbow contact is active, a skeleton-only "free" solve of the same frame gives the free wrist, elbow
   and hand angle. ContactConstraintController then sets the IK targets, blended by the contact weight.
6. One ``MotionEngine.pose(intent_fn=...)`` call solves (the one solver), deforms (the structured garment rig),
   resolves hand switches (user poses from the ArtworkAndCorrectionResolver) and composites with correctives.

Nothing here keeps state between frames:
- the feet are locked from the t = 0 intent, computed at construction;
- the secondary springs are a precomputed table;
- the layer order is evaluated fresh for each frame;
- the hand poses are explicit.

Random access, scrubbing and replay therefore give identical frames.
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..artistic.poses import ArtisticPose, ArtisticPosePlanner, Gesture
from .anticipation import AnticipationAndFollowThroughController
from .contacts import ContactConstraintController, ContactSpec
from .dialogue import DialogueActingController, DialogueEvent
from .gestures import GesturePerformanceLibrary
from .layers import Clip, MotionLayerController
from .overlap import MotionOverlapController
from .resolver import ArtworkAndCorrectionResolver
from .secondary import SecondaryMotionController
from .seat import Desk, Seat
from .sequencer import Key, KeyPoseSequencer
from .styles import style as get_style
from .weight import StepEvent, WeightTransferController

SCHEMA = "autorig.performance"
VERSION = "1.0.0"
POSE_FIELDS = ("weight_left", "contrapposto", "line_of_action", "head", "free_knee", "clavicle_r", "clavicle_l",
               "pelvis_dy")


@dataclass
class Performance:
    """A performance timeline (JSON-persistable; stored in the editor project as ``performance``)."""

    name: str = "performance"
    fps: float = 12.0
    style: str = "neutral"
    style_overrides: dict[str, Any] = field(default_factory=dict)
    template: dict[str, Any] = field(default_factory=dict)  # ArtisticPose fields (base preset, stance, view ...)
    base_keys: list[dict[str, Any]] = field(default_factory=list)  # BODY_BASE keys (Key JSON)
    gestures: list[dict[str, Any]] = field(default_factory=list)  # {id, gesture, start, side, anticipation, ...}
    clips: list[dict[str, Any]] = field(default_factory=list)  # explicit clips (Clip JSON)
    contacts: list[dict[str, Any]] = field(default_factory=list)
    objects: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    props: dict[str, Any] = field(default_factory=dict)  # {"desks": [...], "seats": [...]}
    offsets: dict[str, float] = field(default_factory=dict)
    secondary: dict[str, Any] = field(default_factory=dict)  # part settings + "overrides" + "resets"
    layers: dict[str, dict[str, Any]] = field(default_factory=dict)
    hand_markers: list[dict[str, Any]] = field(default_factory=list)
    corrections: dict[str, Any] = field(default_factory=lambda: {"mode": "all", "overrides": {}})
    events: list[dict[str, Any]] = field(default_factory=list)  # dialogue events
    turns: list[dict[str, Any]] = field(default_factory=list)
    idle: float = 0.0  # idle (breath and sway) amount on ROOT_MOVEMENT
    allow_pending: bool = True  # preview pending drawings (labelled); production would set False
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "version": VERSION, **copy.deepcopy(self.__dict__)}

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Performance":
        d = {k: v for k, v in d.items() if k in Performance.__dataclass_fields__}
        return Performance(**copy.deepcopy(d))


def default_template() -> dict[str, Any]:
    return {"action": "performance", "title": "Performance", "base": "relaxed_standing", "intention": "",
            "weight_left": 0.6, "contrapposto": 0.5, "head": 2.0, "free_knee": 9.0,
            "clavicle": {"r": -2.0, "l": -1.0}}


class PerformanceDirector:
    def __init__(self, eng, vis: list[str], perf: Performance, planner: ArtisticPosePlanner | None = None):
        self.eng, self.vis, self.perf = eng, vis, perf
        self.planner = planner or ArtisticPosePlanner(eng.profile)
        self.style = get_style(perf.style, perf.style_overrides)
        self.overlap = MotionOverlapController(perf.offsets)
        self.antic = AnticipationAndFollowThroughController()
        self.resolver = ArtworkAndCorrectionResolver(eng, perf.allow_pending, (perf.corrections or {}).get("mode", "all"))
        self.library = GesturePerformanceLibrary(self.resolver)
        keys = [Key.from_json(k) for k in perf.base_keys] or [Key(0.0, {})]
        self.substitutions = []
        for k in keys:
            k.head_lead = k.head_lead or self.style.head_lead
            alt = (k.pose or {}).get("alternative")
            if alt and not perf.allow_pending and self._needs_missing_palm(k.hands):
                # production: a key pose that needs palm-up hands nobody approved is shown as its supported
                # alternative (arms and hands), and the substitution is reported (Phase 3.7)
                self.substitutions.append({"key": k.id, "label": k.label, "shown": alt.get("label", "alternative"),
                                           "reason": "NEEDS_ARTWORK: no approved palm-up hand drawing"})
                k.params = {**k.params, **alt.get("params", {})}
                k.hands = dict(alt.get("hands", k.hands))
        self.base = KeyPoseSequencer(keys, self.overlap, self.antic)
        clips = [Clip.from_json(c) for c in perf.clips]
        for g in perf.gestures:
            name = g["gesture"]
            st = self.library.status(name, g.get("side", "r"))
            if not perf.allow_pending and st["status"] == "NEEDS_ARTWORK" and st.get("alternative"):
                # production: a gesture whose artwork is missing is replaced as a whole (arms and hands) by its
                # supported alternative, never shown with stand-in hands on the original arm pose (Phase 3.7)
                self.substitutions.append({"gesture": name, "start": g["start"], "shown": st["alternative"],
                                           "reason": f"NEEDS_ARTWORK: {', '.join(st['missing'])}"})
                name = st["alternative"]
            clips += self.library.build(name, float(g["start"]), g.get("side", "r"), self.style,
                                        g.get("anticipation"), g.get("overshoot"), g.get("amplitude"),
                                        g.get("duration"), g.get("id"), g.get("torso"))
        if perf.events:
            dlg = DialogueActingController(self.library, perf.style)
            plan = dlg.plan([DialogueEvent(**e) for e in perf.events], self.base.duration, perf.turns)
            clips += plan["clips"]
            self.dialogue = plan
        else:
            self.dialogue = None
        if perf.idle > 0:
            clips.append(DialogueActingController(self.library).idle(self.base.duration, amount=perf.idle))
        if self.style.posture:
            clips += self._posture_clips()
        self.layers = MotionLayerController(self.base, clips, perf.layers, eng.profile.limits, self.overlap,
                                            self.antic, perf.hand_markers)
        self.contacts = ContactConstraintController([ContactSpec.from_json(c) for c in perf.contacts], perf.objects)
        self.desks = [Desk(**{k: v for k, v in d.items() if k != "note"}) for d in perf.props.get("desks", [])]
        self.seats = [Seat(**{k: v for k, v in d.items() if k != "note"}) for d in perf.props.get("seats", [])]
        self.weight = WeightTransferController(eng)
        self.weight.steps = [StepEvent(**s) for s in perf.steps]
        self.timing: dict[str, float] = {}
        self._init_rest()
        sec = dict(perf.secondary or {})
        overrides, resets = sec.pop("overrides", []), sec.pop("resets", [])
        self.secondary = SecondaryMotionController(sec, self.style.secondary, resets, overrides)
        if not self.layers.settings["SECONDARY_MOTION"]["mute"]:
            t0 = time.perf_counter()
            self.secondary.simulate(self._drivers(), self.duration)
            self.timing["secondary_simulate_s"] = round(time.perf_counter() - t0, 3)

    # -- construction helpers -------------------------------------------------------------------------------------
    def _posture_clips(self) -> list[Clip]:
        """The style's posture offsets: the head tilt on HEAD_MOVEMENT, everything else on ROOT_MOVEMENT."""
        d = self.duration
        out = []
        for layer, chans in (("ROOT_MOVEMENT", [k for k in self.style.posture if k != "head"]),
                             ("HEAD_MOVEMENT", [k for k in self.style.posture if k == "head"])):
            if not chans:
                continue
            post = {k: float(self.style.posture[k]) for k in chans}
            ks = [Key(0.0, {k: 0.0 for k in post}), Key(min(0.8, d / 2), post, hold=max(0.0, d - min(0.8, d / 2)))]
            out.append(Clip(f"style_posture:{layer.lower()}", layer, 0.0, ks, anchor_start=False, anchor_end=False,
                            blend_in=0.0, blend_out=0.0, meta={"style": self.style.name}))
        return out

    def _init_rest(self) -> None:
        """Lock the feet from the t = 0 intent and calibrate the balance window (two skeleton-only solves)."""
        ap = self.pose_at(0.0)[0]
        rq = self.planner.request(ap)
        name = rq.pop("name")
        rq.pop("depth", None)
        cap: dict[str, Any] = {}

        def capture(it):
            cap["base"] = copy.deepcopy(it)
            out = self.planner.apply(it, ap)
            cap["it"] = copy.deepcopy(out)
            return out
        self.eng.pose(name, visible=self.vis, skeleton_only=True, **{**rq, "intent_fn": capture})
        self.weight.lock_feet(cap["it"], cap["base"])
        self._base_name = name

        def solve_w(w):
            ap2 = copy.deepcopy(ap)
            ap2.weight_left = w

            def fn(it):
                b = copy.deepcopy(it)
                out = self.planner.apply(it, ap2)
                for s, leg in out.legs.items():  # locked feet for the calibration too
                    leg.ankle = self.weight.locks[s]
                    if s in b.legs:
                        leg.knee_min = b.legs[s].knee_min
                return out
            kw = {k: v for k, v in self.planner.request(ap2).items() if k not in ("name", "depth")}
            kw["intent_fn"] = fn
            return self.eng.pose(name, visible=self.vis, skeleton_only=True, **kw)["solve"]
        self.calibration = self.weight.calibrate(solve_w)

    def _drivers(self):
        slope = self.weight._slope or 0.0

        def roll(p):
            return -(p.get("weight_left", 0.5) - 0.5) / 0.35 * 7.0 * p.get("contrapposto", 0.0)

        def hem(t):
            p = self.layers.params_at(t)[0]
            return roll(p) + 0.4 * p.get("line_of_action", 0.0)

        def hair(t):
            p = self.layers.params_at(t)[0]
            sway = np.degrees(slope * (p.get("weight_left", 0.5) - 0.5) / 300.0)
            return p.get("head", 0.0) + 0.5 * p.get("line_of_action", 0.0) + float(sway)
        return {"hem": hem, "hair": hair}

    # -- evaluation -------------------------------------------------------------------------------------------------
    @property
    def duration(self) -> float:
        ends = [self.base.duration] + [c.end for c in self.layers.clips] if hasattr(self, "layers") else [
            self.base.duration]
        return float(max(ends + [c.get("end", 0) + c.get("release", 0) for c in self.perf.contacts]))

    def frames(self, fps: float | None = None) -> list[float]:
        f = fps or self.perf.fps
        n = int(round(self.duration * f)) + 1
        return [k / f for k in range(n)]

    def pose_at(self, t: float) -> tuple[ArtisticPose, dict[str, str], dict[str, Any]]:
        params, hands, info = self.layers.params_at(t)
        tpl = {**default_template(), **(self.perf.template or {})}
        sample_pose = self.base.sample(t)["pose"]
        tpl.update({k: v for k, v in sample_pose.items() if k in ArtisticPose.__dataclass_fields__})
        ap = ArtisticPose(**{k: v for k, v in tpl.items() if k in ArtisticPose.__dataclass_fields__ and k != "arms"})
        for k in ("weight_left", "contrapposto", "line_of_action", "head", "free_knee"):
            if k in params:
                setattr(ap, k, float(params[k]))
        if "pelvis_dy" in params:
            ap.pelvis_dy = float(params["pelvis_dy"])
        ap.clavicle = {s: float(params.get(f"clavicle_{s}", ap.clavicle.get(s, 0.0))) for s in ("r", "l")}
        ap.free_foot_in = 0.0  # feet are locked by the WeightTransferController
        ap.arms = {s: Gesture("hang", abduction=float(params.get(f"arm_{s}_abduction", 10.0)),
                              flexion=float(params.get(f"arm_{s}_flexion", 6.0)),
                              wrist=float(params.get(f"arm_{s}_wrist", 3.0)), hand="relaxed_standing")
                   for s in ("r", "l")}
        info["params"] = params
        return ap, hands, info

    def _intent_fn(self, ap: ArtisticPose, t: float, params: dict[str, float], rep: dict[str, Any],
                   contacts: bool, free: dict[str, Any] | None):
        muted = self.layers.settings

        def fn(it):
            base = copy.deepcopy(it)
            out = self.planner.apply(it, ap)
            rep["weight"] = self.weight.apply(out, t, ap.weight_left, ap.free_knee, base)
            if not muted["SECONDARY_MOTION"]["mute"]:
                out.garment = {**out.garment, **self.secondary.garment(t)}
            if contacts and free is not None and not muted["CONTACT_CONSTRAINTS"]["mute"]:
                rep["contacts"] = self.contacts.apply_arms(out, t, free, self.eng.profile)
            return out
        return fn

    def _needs_missing_palm(self, hands: dict[str, str]) -> bool:
        """True when a hand action needs a palm view that no approved drawing on that side can show."""
        for s, a in (hands or {}).items():
            need = (self.eng.hand_select.rule(a).get("palm") or []) if a else []
            if "PALM_UP" in need and len(need) == 1:
                ok = [c for c in self.resolver.palm_capable(s, "PALM_UP") if c["status"] in ("approved", "original")]
                if not ok:
                    return True
        return False

    # -- hand plan (Phase 3.7) -------------------------------------------------------------------------------------
    PLAN_STEP = 1.0 / 48.0

    def hand_plan(self) -> dict[str, list[dict[str, Any]]]:
        """Per side, the intervals of constant requested hand. An *action* interval is resolved once by the
        NaturalHandSelectionEngine on its middle frame (the held gesture: hard constraints on the solved pose) and
        held. Computed lazily once, so every frame stays a pure function of time."""
        if getattr(self, "_hand_plan", None) is not None:
            return self._hand_plan
        from .resolver import is_action  # noqa: PLC0415

        n = int(round(self.duration / self.PLAN_STEP)) + 1
        ts = [k * self.PLAN_STEP for k in range(n)]
        raw = [self.layers.params_at(t)[1] for t in ts]
        plan: dict[str, list[dict[str, Any]]] = {}
        for s in ("r", "l"):
            ivs: list[dict[str, Any]] = []
            for t, h in zip(ts, raw):
                v = h.get(s)
                if v is None:
                    continue
                if ivs and ivs[-1]["value"] == v and abs(ivs[-1]["t1"] - (t - self.PLAN_STEP)) < 1e-9:
                    ivs[-1]["t1"] = t
                else:
                    ivs.append({"value": v, "t0": t, "t1": t, "kind": "action" if is_action(v) else "pose"})
            plan[s] = ivs
        self._hand_plan = plan
        for s, ivs in plan.items():
            for iv in ivs:
                if iv["kind"] == "action":
                    tm = 0.5 * (iv["t0"] + iv["t1"])
                    r = self.solve(tm, hand_actions={s: iv["value"]}, plan_hands=False)
                    dec = dict(r["hand_selection"]["decisions"][s])
                    dec["decided_at"] = round(tm, 4)
                    iv["decision"] = dec
        return plan

    def hands_at(self, t: float, hands: dict[str, str]) -> dict[str, dict[str, Any]]:
        """The planned decision of each side's action interval containing ``t``."""
        plan = self.hand_plan()
        out = {}
        for s, v in hands.items():
            for iv in plan.get(s, []):
                if iv["value"] == v and iv["t0"] - self.PLAN_STEP <= t <= iv["t1"] + self.PLAN_STEP and "decision" in iv:
                    out[s] = iv["decision"]
                    break
        return out

    def solve(self, t: float, render: bool = False, corrective_mode: str | None = None, evaluate: bool = False,
              extra: dict[str, Any] | None = None, hand_actions: dict[str, str] | None = None,
              plan_hands: bool = True) -> dict[str, Any]:
        """Solve (and optionally render) frame ``t``. Returns the MotionEngine result plus a ``performance`` report.
        ``plan_hands=False`` (internal): no hand switches; ``hand_actions`` go to the selection engine instead."""
        t0 = time.perf_counter()
        ap, hands, info = self.pose_at(t)
        t_layers = time.perf_counter() - t0
        if not plan_hands or self.layers.settings["HAND_SWITCHES"]["mute"]:
            sw, hand_rep = {}, {}
        else:
            sw, hand_rep = self.resolver.resolve(hands, self.hands_at(t, hands))
        rep: dict[str, Any] = {}
        rq = {k: v for k, v in self.planner.request(ap, self.perf.allow_pending).items() if k not in ("name", "depth")}
        if hand_actions:
            rq["hand_actions"] = {**(rq.get("hand_actions") or {}), **hand_actions}
        free = None
        need = self.contacts.needs_free_solve(t)
        t1 = time.perf_counter()
        if need:
            free = self.eng.pose(self._base_name, visible=self.vis, skeleton_only=True,
                                 **{**rq, "intent_fn": self._intent_fn(ap, t, info["params"], {}, False, None)})["solve"]
        fn = self._intent_fn(ap, t, info["params"], rep, need, free)
        kw = {**rq, "intent_fn": fn, "switches": sw}
        if render:
            from ..artistic.layer_order import DynamicLayerOrderController  # noqa: PLC0415

            mode = corrective_mode or (self.perf.corrections or {}).get("mode", "all")
            kw.update({"layer_order": DynamicLayerOrderController(), "evaluate_garments": evaluate,
                       "corrective_mode": mode,
                       "corrective_overrides": (self.perf.corrections or {}).get("overrides") or None})
            if self.layers.settings["GARMENT_CORRECTIONS"]["mute"]:
                kw["corrective_mode"] = "off"
            res = self.eng.pose(self._base_name, visible=self.vis, **{**kw, **(extra or {})})
        else:
            res = self.eng.pose(self._base_name, visible=self.vis, skeleton_only=True, **{**kw, **(extra or {})})
        t2 = time.perf_counter()
        res["performance"] = {"t": round(t, 4), "hands": hand_rep, "layers": info["active_clips"],
                              "substitutions": self.substitutions,
                              "soft_limited": info["soft_limited"], "facial": info["facial"], **rep,
                              "secondary": {k: round(v, 3) for k, v in self.secondary.garment(t).items()},
                              "contact_sliding_px": self.contacts.sliding(t, res["solve"]),
                              "timing_s": {"layers": round(t_layers, 4), "solve_or_render": round(t2 - t1, 4),
                                           "free_solve": need}}
        return res

    def track(self, fps: float | None = None) -> list[dict[str, Any]]:
        """Skeleton-only solves for every frame (fast: wireframe preview, motion checks, curves)."""
        out = []
        for t in self.frames(fps):
            r = self.solve(t)
            s = r["solve"]
            out.append({"t": round(t, 4), "joints": s["joints_posed"], "tails": s["bone_tails_posed"],
                        "params": {k: round(float(v), 4) for k, v in self.pose_at(t)[2]["params"].items()},
                        "performance": r["performance"], "clamps": s.get("clamps"),
                        "balance_error_px": s.get("balance_error_px"), "violations": s.get("violations"),
                        "com": s.get("com"), "support": s.get("support"), "contact": s.get("contact")})
        return out


__all__ = ["Performance", "PerformanceDirector", "default_template", "SCHEMA", "VERSION"]
