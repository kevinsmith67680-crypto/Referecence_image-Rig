"""GesturePerformanceLibrary: reusable gesture clips with their acting metadata.

Each gesture defines:
- its emotional tone and key poses (relative times);
- its timing, curves, anticipation recipe and follow-through;
- the views it supports and the artwork it requires (hand poses per side, view drawings);
- its contacts, hand switch choices and secondary-motion settings;
- its approval status, computed from the character's actual artwork.

A gesture becomes clips on the motion layers:
- the arms and shoulders: an anchored override clip on UPPER_BODY_GESTURES;
- the head: an additive delta on HEAD_MOVEMENT;
- weight and torso involvement: an additive delta on ROOT_MOVEMENT, scaled by the style's ``torso``.

Amplitude scales the arm targets about the neutral hang (``NEUTRAL``). Duration and holds scale the timing.

Status is never production-ready from code. A gesture whose required hand drawing is pending is NEEDS_APPROVAL; a
missing drawing makes it NEEDS_ARTWORK; a missing view (head pitch or yaw) makes it REQUIRES_NEW_VIEW. The shrug
needs palm-up hands, which do not exist, so it is NEEDS_ARTWORK. ``shrug_low`` (shoulders up, hands kept low) is
the supported alternative. The palm-up shrug can be previewed with the pending cupped hands, labelled for review.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .curves import Curve
from .layers import Clip
from .sequencer import Key
from .styles import ActingStyleProfile, style as get_style

NEUTRAL = {"abduction": 10.0, "flexion": 6.0, "wrist": 3.0, "clavicle": 0.0}
OTHER = {"r": "l", "l": "r"}


@dataclass
class GestureDef:
    name: str
    title: str
    tone: str
    duration: float
    arms: list[dict[str, Any]] = field(default_factory=list)  # keys: at, params ({s}/{o}), hold, hands, label, ...
    head: list[dict[str, Any]] = field(default_factory=list)  # additive deltas (deg, + toward the gesture side)
    root: list[dict[str, Any]] = field(default_factory=list)  # additive deltas (+ toward the gesture side)
    sided: bool = True
    views: list[str] = field(default_factory=lambda: ["FRONT"])
    hands_required: dict[str, list[str]] = field(default_factory=dict)  # "{s}"/"{o}"/"both" -> poses (any one)
    view_required: list[str] = field(default_factory=list)  # e.g. art:head_pitch (missing views)
    contacts: list[dict[str, Any]] = field(default_factory=list)
    secondary: dict[str, float] = field(default_factory=dict)
    curve: str = "ease_in_out"
    alternative: str | None = None
    notes: str = ""

    def to_json(self) -> dict[str, Any]:
        d = copy.deepcopy(self.__dict__)
        return d


def _sgn(side: str) -> float:
    """Character-left-positive sign of the gesture side (``line_of_action``/``head`` convention)."""
    return -1.0 if side == "r" else 1.0


def _sub(params: dict[str, Any], s: str) -> dict[str, float]:
    o = OTHER[s]
    return {k.replace("{s}", s).replace("{o}", o): float(v) for k, v in params.items()}


def _arm_hold(s: str, abd: float, flex: float, wrist: float, clav: float | None = None) -> dict[str, float]:
    d = {f"arm_{s}_abduction": abd, f"arm_{s}_flexion": flex, f"arm_{s}_wrist": wrist}
    if clav is not None:
        d[f"clavicle_{s}"] = clav
    return d


LIBRARY: dict[str, GestureDef] = {
    "point": GestureDef(
        "point", "Pointing", "assertive attention: 'that one'", 1.7,
        arms=[{"at": 0.0, "label": "start"},
              {"at": 0.38, "params": {"arm_{s}_abduction": 96, "arm_{s}_flexion": 8, "arm_{s}_wrist": -4,
                                      "clavicle_{s}": 4}, "hold": 0.3, "hands": {"{s}": "pointing"}, "label": "point",
               "anticipation": "point", "overshoot": 0.06},
              {"at": 1.0, "label": "return", "hands": {"{s}": "relaxed"}}],
        head=[{"at": 0.0, "head": 0.0}, {"at": 0.3, "head": 4.0, "hold": 0.38}, {"at": 1.0, "head": 0.0}],
        root=[{"at": 0.0}, {"at": 0.36, "line_of_action": 4.0, "weight_left": 0.08, "hold": 0.32}, {"at": 1.0}],
        hands_required={"{s}": ["pointing"]}, secondary={"hair": 1.0, "hem": 1.0}),
    "explain": GestureDef(
        "explain", "Explaining", "open, reasonable: 'look, it is like this'", 2.0,
        arms=[{"at": 0.0, "label": "start"},
              {"at": 0.28, "params": {**_arm_hold("r", 24, -58, -8), **_arm_hold("l", 22, -54, -8)},
               "hands": {"r": "cupped", "l": "cupped"}, "label": "open", "overshoot": 0.04, "anticipation": "none"},
              {"at": 0.48, "params": {**_arm_hold("r", 26, -68, -12), **_arm_hold("l", 24, -64, -12)}, "interp": "path",
               "label": "beat 1"},
              {"at": 0.68, "params": {**_arm_hold("r", 23, -56, -8), **_arm_hold("l", 21, -52, -8)}, "label": "beat 2",
               "hold": 0.1},
              {"at": 1.0, "label": "return", "hands": {"r": "relaxed", "l": "relaxed"}}],
        head=[{"at": 0.0, "head": 0.0}, {"at": 0.45, "head": 2.0}, {"at": 1.0, "head": 0.0}],
        sided=False, hands_required={"both": ["cupped", "palm_up"]}),
    "shrug": GestureDef(
        "shrug", "Shrugging (palms up)", "uncertain: 'I don't know'", 1.8,
        arms=[{"at": 0.0, "label": "start"},
              {"at": 0.35, "params": {**_arm_hold("r", 18, -46, -16, 14), **_arm_hold("l", 18, -46, -16, 16)},
               "hands": {"r": "palm_up", "l": "palm_up"}, "label": "shrug", "anticipation": "shrug", "overshoot": 0.05,
               "hold": 0.3},
              {"at": 1.0, "label": "relax", "hands": {"r": "relaxed", "l": "relaxed"}}],
        head=[{"at": 0.0, "head": 0.0}, {"at": 0.35, "head": 6.0, "hold": 0.3}, {"at": 1.0, "head": 0.0}],
        sided=False, hands_required={"both": ["palm_up"]}, alternative="shrug_low",
        notes="palm-up hands do not exist: NEEDS_ARTWORK. Not disguised by wrist rotation; see shrug_low"),
    "shrug_low": GestureDef(
        "shrug_low", "Shrug (hands low)", "uncertain, understated", 1.6,
        arms=[{"at": 0.0, "label": "start"},
              {"at": 0.35, "params": {**_arm_hold("r", 15, -8, 2, 13), **_arm_hold("l", 14, -6, 2, 15)},
               "label": "shoulders up", "anticipation": "shrug", "overshoot": 0.05, "hold": 0.3},
              {"at": 1.0, "label": "relax"}],
        head=[{"at": 0.0, "head": 0.0}, {"at": 0.35, "head": 5.0, "hold": 0.3}, {"at": 1.0, "head": 0.0}],
        sided=False, hands_required={},
        notes="supported alternative: the shoulders carry the shrug, the hands stay relaxed at the sides"),
    "wave": GestureDef(
        "wave", "Waving", "friendly greeting", 2.2,
        arms=[{"at": 0.0, "label": "start"},
              {"at": 0.25, "params": _arm_hold("{s}", 138, 38, 0, 5), "hands": {"{s}": "open_palm"}, "label": "up",
               "anticipation": "wave", "overshoot": 0.04},
              {"at": 0.38, "params": {"arm_{s}_wrist": -16}, "interp": "path", "label": "wave 1"},
              {"at": 0.5, "params": {"arm_{s}_wrist": 16}, "interp": "path", "label": "wave 2"},
              {"at": 0.62, "params": {"arm_{s}_wrist": -14}, "interp": "path", "label": "wave 3"},
              {"at": 0.72, "params": {"arm_{s}_wrist": 0}, "label": "settle"},
              {"at": 1.0, "label": "return", "hands": {"{s}": "relaxed"}}],
        head=[{"at": 0.0, "head": 0.0}, {"at": 0.25, "head": 3.0}, {"at": 1.0, "head": 0.0}],
        hands_required={"{s}": ["open_palm"]}),
    "disagree": GestureDef(
        "disagree", "Disagreeing", "polite refusal: 'no, not quite'", 1.8,
        arms=[{"at": 0.0, "label": "start"},
              {"at": 0.3, "params": _arm_hold("{s}", 32, -34, 10), "hands": {"{s}": "palm_down"}, "label": "palm out",
               "anticipation": "none"},
              {"at": 0.62, "params": _arm_hold("{s}", 44, -30, 14), "label": "sweep", "hold": 0.1},
              {"at": 1.0, "label": "return", "hands": {"{s}": "relaxed"}}],
        head=[{"at": 0.0, "head": 0.0}, {"at": 0.25, "head": 3.0, "interp": "path"}, {"at": 0.42, "head": -3.0,
                                                                                     "interp": "path"},
              {"at": 0.6, "head": 2.0}, {"at": 1.0, "head": 0.0}],
        hands_required={"{s}": ["palm_down"]},
        notes="the head tilts side to side; a real head shake is a yaw and needs turned-head drawings"),
    "nod": GestureDef(
        "nod", "Nodding", "agreement", 1.0, arms=[],
        head=[{"at": 0.0, "head": 0.0}, {"at": 0.3, "head": 1.5, "interp": "path"}, {"at": 0.6, "head": -1.0},
              {"at": 1.0, "head": 0.0}],
        sided=False, view_required=["art:head_pitch_down", "art:head_pitch_up"],
        notes="a nod is a head pitch: the front-view head can only tilt in the picture plane (REQUIRES_NEW_VIEW); "
              "the preview is a small tilt, labelled"),
    "surprise": GestureDef(
        "surprise", "Reacting with surprise", "startled", 1.4,
        arms=[{"at": 0.0, "label": "start"},
              {"at": 0.22, "params": {**_arm_hold("r", 34, -36, -10, 8), **_arm_hold("l", 34, -36, -10, 8)},
               "hands": {"r": "open_palm", "l": "open_palm"}, "label": "startle", "anticipation": "surprise",
               "overshoot": 0.08, "hold": 0.3},
              {"at": 1.0, "label": "recover", "hands": {"r": "relaxed", "l": "relaxed"}}],
        head=[{"at": 0.0, "head": 0.0}, {"at": 0.22, "head": 4.0, "hold": 0.3}, {"at": 1.0, "head": 0.0}],
        root=[{"at": 0.0}, {"at": 0.22, "contrapposto": -0.2, "hold": 0.3}, {"at": 1.0}],
        sided=False, hands_required={"both": ["open_palm"]}),
    "lean_towards": GestureDef(
        "lean_towards", "Leaning towards someone (sideways)", "engaged, confiding", 2.0, arms=[],
        head=[{"at": 0.0, "head": 0.0}, {"at": 0.3, "head": 5.0, "hold": 0.4}, {"at": 1.0, "head": 0.0}],
        root=[{"at": 0.0}, {"at": 0.35, "weight_left": 0.16, "line_of_action": 8.0, "contrapposto": 0.2, "hold": 0.35},
              {"at": 1.0}],
        notes="lateral lean (front view); a lean toward the viewer needs art:torso_lean_forward (REQUIRES_NEW_VIEW)"),
    "raise_hand": GestureDef(
        "raise_hand", "Raising a hand", "asking to speak", 1.9,
        arms=[{"at": 0.0, "label": "start"},
              {"at": 0.32, "params": _arm_hold("{s}", 166, 10, 0, 6), "hands": {"{s}": "open_palm"}, "label": "raised",
               "anticipation": "raise", "overshoot": 0.06, "hold": 0.36},
              {"at": 1.0, "label": "return", "hands": {"{s}": "relaxed"}}],
        root=[{"at": 0.0}, {"at": 0.32, "line_of_action": -3.0, "hold": 0.36}, {"at": 1.0}],
        hands_required={"{s}": ["open_palm"]}),
    "return_neutral": GestureDef(
        "return_neutral", "Returning to a neutral stance", "settling", 1.2,
        arms=[{"at": 0.0, "label": "start"},
              {"at": 1.0, "params": {**_arm_hold("r", 11, 5, 3, -2), **_arm_hold("l", 9, 8, 4, -1)},
               "hands": {"r": "relaxed", "l": "relaxed"}, "label": "neutral", "anchor": False}],
        sided=False, hands_required={"both": ["relaxed", "open_palm"]}),
}


class GesturePerformanceLibrary:
    def __init__(self, resolver=None, extra: dict[str, GestureDef] | None = None):
        self.gestures = {**LIBRARY, **(extra or {})}
        self.resolver = resolver

    def names(self) -> list[str]:
        return list(self.gestures)

    # -- status --------------------------------------------------------------------------------------------------
    def status(self, name: str, side: str = "r") -> dict[str, Any]:
        g = self.gestures[name]
        if g.view_required:
            return {"status": "REQUIRES_NEW_VIEW", "missing": list(g.view_required), "pending": [],
                    "production_ready": False, "note": g.notes}
        pending, missing = [], []
        if self.resolver is not None:
            for who, poses in g.hands_required.items():
                sides = ["r", "l"] if who == "both" else [side if who == "{s}" else OTHER[side]]
                for s in sides:
                    st = [self.resolver.status(s, p) for p in poses]
                    if any(x in ("approved", "original") for x in st):
                        continue
                    if any(x == "pending_review" for x in st):
                        pending.append(f"hand:{'/'.join(poses)} ({s})")
                    else:
                        missing.append(f"hand:{'/'.join(poses)} ({s})")
        cls = "NEEDS_ARTWORK" if missing else "NEEDS_APPROVAL" if pending else "SUPPORTED"
        return {"status": cls, "missing": missing, "pending": pending, "production_ready": False,
                "alternative": g.alternative if missing else None,
                "note": g.notes or ("uses only original artwork; still needs a person to approve the performance"
                                    if cls == "SUPPORTED" else "")}

    def catalog(self) -> dict[str, Any]:
        out = {}
        for n, g in self.gestures.items():
            out[n] = {**g.to_json(), "status_r": self.status(n, "r"), "status_l": self.status(n, "l")}
        return out

    # -- clips -----------------------------------------------------------------------------------------------------
    def build(self, name: str, start: float, side: str = "r", st: ActingStyleProfile | str | None = None,
              anticipation: float | None = None, overshoot: float | None = None, amplitude: float | None = None,
              duration: float | None = None, clip_id: str | None = None, torso: float | None = None) -> list[Clip]:
        """Clips for one performance of gesture ``name`` starting at ``start``."""
        g = self.gestures[name]
        sty = st if isinstance(st, ActingStyleProfile) else get_style(st)
        amp = sty.amplitude * (1.0 if amplitude is None else amplitude)
        dur = (duration if duration is not None else g.duration) * sty.duration
        ant = sty.anticipation * (1.0 if anticipation is None else anticipation)
        ovs = sty.overshoot * (1.0 if overshoot is None else overshoot)
        sgn = _sgn(side)
        cid = clip_id or f"{name}_{side}_{start:.2f}"
        clips: list[Clip] = []

        def neutral(ch: str) -> float:
            for k, v in NEUTRAL.items():
                if ch.endswith(k):
                    return v
            return 0.0

        if g.arms:
            keys, ats = [], []
            for i, kd in enumerate(g.arms):
                params = _sub(kd.get("params", {}), side)
                params = {ch: neutral(ch) + amp * (v - neutral(ch)) for ch, v in params.items()}
                hands = {k.replace("{s}", side).replace("{o}", OTHER[side]): v for k, v in kd.get("hands", {}).items()}
                hold = kd.get("hold", 0.0) * dur * sty.hold
                keys.append(Key(kd["at"] * dur, params, hands, kd.get("label", ""), 0.0, Curve(g.curve),
                                anticipation=kd.get("anticipation"), anticipation_intensity=ant,
                                overshoot=kd.get("overshoot", 0.0) * ovs, side=side, interp=kd.get("interp", "stop"),
                                head_lead=sty.head_lead))
                ats.append(hold)
            for i in range(len(keys) - 1):  # holds may not run into the next key
                keys[i].hold = min(ats[i], 0.8 * (keys[i + 1].t - keys[i].t))
            chans = sorted(set().union(*[set(k.params) for k in keys]))
            for k in keys:  # keys that set nothing carry every channel of the clip (anchors fill the ends)
                if not k.params:
                    k.params = {}
            anchor_end = g.arms[-1].get("anchor", True)
            clips.append(Clip(f"{cid}:arms", "UPPER_BODY_GESTURES", start, keys, mask=chans, gesture=name,
                              side=side if g.sided else None, anchor_end=anchor_end,
                              meta={"tone": g.tone, "style": sty.name}))
        if g.head:
            hk = []
            for kd in g.head:
                hk.append(Key(kd["at"] * dur, {"head": sgn * amp * kd.get("head", 0.0)}, {}, "",
                              kd.get("hold", 0.0) * dur * sty.hold, Curve(g.curve), interp=kd.get("interp", "stop"),
                              head_lead=sty.head_lead))
            _fit_holds(hk)
            clips.append(Clip(f"{cid}:head", "HEAD_MOVEMENT", start, hk, gesture=name, side=side, anchor_start=False,
                              anchor_end=False))
        if g.root:
            rk = []
            for kd in g.root:
                d = {}
                for ch in ("weight_left", "line_of_action", "contrapposto"):
                    if ch in kd:
                        scale = sty.torso * amp * (1.0 if torso is None else torso)
                        d[ch] = (sgn * kd[ch] if ch != "contrapposto" else kd[ch]) * scale
                rk.append(Key(kd["at"] * dur, d or {"line_of_action": 0.0}, {}, "", kd.get("hold", 0.0) * dur * sty.hold,
                              Curve(g.curve)))
            chans = sorted(set().union(*[set(k.params) for k in rk]))
            for k in rk:
                k.params = {ch: k.params.get(ch, 0.0) for ch in chans}
            _fit_holds(rk)
            clips.append(Clip(f"{cid}:root", "ROOT_MOVEMENT", start, rk, gesture=name, side=side, anchor_start=False,
                              anchor_end=False))
        return clips


def _fit_holds(keys: list[Key]) -> None:
    for i in range(len(keys) - 1):
        keys[i].hold = min(keys[i].hold, 0.8 * (keys[i + 1].t - keys[i].t))
    keys[-1].hold = keys[-1].hold if keys else 0.0


__all__ = ["GesturePerformanceLibrary", "GestureDef", "LIBRARY", "NEUTRAL"]
