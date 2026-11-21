"""MotionPlanner: intended action -> PoseIntent.

Static presets are written as intent: foot placement from the character's own track widths, weight split,
hand targets or arm shape, torso and head intent. Gaits (walk, run) are driven by a normalised phase in
[0, 1). The phase decides contact, passing, flight and recovery for each foot, the pelvis path (lateral sway,
roll, vertical oscillation) and the opposing arm swing. Nothing is a free per-bone rotation.

Front-view gait: the reference is a front view, so stride length and knee drive happen *toward the viewer*
and cannot be drawn without foreshortened artwork. The supported front-view gait uses in-plane motion only:
weight shift, pelvis roll, vertical oscillation (flight), foot lift through a limited lateral knee bend, and
arm swing as elbow flexion. A deeper knee drive is reported as NEEDS_ARTWORK, never faked by scaling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from ..phase2.skeleton import SIDE_SIGN, SIDES
from .intent import ArmIntent, LegIntent, PoseIntent
from .profile import CharacterProfile
from .secondary import SAMPLES, SPRINGS, lookup

LAT = {"r": -1.0, "l": 1.0}  # image x direction of the character's side


@dataclass
class Requirement:
    asset: str  # e.g. "hand:point", "corrective:gusset", "art:thigh_knee_up"
    why: str
    optional: bool = False

    def to_json(self) -> dict[str, Any]:
        return {"asset": self.asset, "why": self.why, "optional": self.optional}


@dataclass
class PresetSpec:
    name: str
    title: str
    intent: str
    kind: str  # "static" | "gait"
    build: Callable[..., PoseIntent]
    ik: str
    balance: str
    torso: str
    garment: str
    requires: list[Requirement] = field(default_factory=list)
    view: str = "front"
    depth_motion: bool = False  # True: the action happens toward the viewer (sagittal) -> needs artwork
    fallback: str | None = None
    legacy: str | None = None  # Phase 2 preset name this replaces

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "title": self.title, "intent": self.intent, "kind": self.kind,
                "ik_constraints": self.ik, "balance": self.balance, "torso_compensation": self.torso,
                "garment_corrections": self.garment, "required_artwork": [r.to_json() for r in self.requires],
                "supported_view": self.view, "depth_motion": self.depth_motion, "fallback": self.fallback}


# -- helpers -------------------------------------------------------------------------------------------------
# Step 9 hand choices: standing and walking prefer a relaxed hand; the reference (open) hand is the fallback when
# the relaxed drawing does not exist (reported, never faked)
RELAXED_HANDS = ("relaxed", "open")


def _feet(p: CharacterProfile, sep: float, dx: float = 0.0, weight_left: float = 0.5, **leg) -> dict[str, LegIntent]:
    """Planted feet on the ground line. In a weight shift the unweighted (relaxed) leg may bend further."""
    out = {}
    for s in SIDES:
        kw = dict(leg)
        w = weight_left if s == "l" else 1.0 - weight_left
        if w < 0.45 and "knee_max" not in kw:
            kw["knee_max"] = p.front_knee["swing"]
        out[s] = LegIntent(True, (p.midline_x + dx + LAT[s] * sep / 2, p.ankle_y[s]), **kw)
    return out


def _arms(**kw) -> dict[str, ArmIntent]:
    return {s: ArmIntent(**kw) for s in SIDES}


def smooth_step01(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * (3.0 - 2.0 * u)


def smooth_pulse(u: float) -> float:
    """0 at u=0 and u=1, 1 at u=0.5; zero slope at both ends (C1 joins with stance)."""
    return math.sin(math.pi * min(max(u, 0.0), 1.0)) ** 2


# -- static presets ------------------------------------------------------------------------------------------
def relaxed(p, **_):
    return PoseIntent("relaxed_standing", "Relaxed standing", "even weight, arms hanging with soft elbows",
                      legs=_feet(p, p.stance_natural, knee_min=4.0),
                      arms=_arms(abduction=9.0, flexion=14.0, wrist=6.0, hand=list(RELAXED_HANDS)),
                      clavicle={"r": -2.0, "l": -2.0})


def hands_at_sides(p, **_):
    return PoseIntent("hands_at_sides", "Hands at sides", "attention stance, arms close to the body",
                      legs=_feet(p, p.track_min + 0.15 * p.hip_sep, knee_min=3.0),
                      arms=_arms(abduction=4.0, flexion=5.0, wrist=3.0, hand=list(RELAXED_HANDS)),
                      clavicle={"r": -3.0, "l": -3.0})


def hands_on_hips(p, **_):
    arms = {}
    for s in SIDES:  # wrist on the side of the waist, a little above the hip line: the elbow must come out
        tgt = (p.midline_x + LAT[s] * (p.waist_half - 0.1 * p.hip_sep), p.waist_y - 0.35 * (p.hip_y - p.waist_y))
        sh = p.shoulder.get(s)
        if sh is not None:  # keep the wrist within 90 % of the arm's reach (the elbow comes out, the shoulder stays)
            v = np.subtract(tgt, sh)
            d, R = float(np.hypot(*v)), 0.9 * p.arm(s)
            if d > R:
                tgt = tuple(np.asarray(sh, float) + v * (R / d))
        arms[s] = ArmIntent(target=tgt, elbow="out", hand_dir=LAT[s] * 20.0, hand=["fist", "open"])
    return PoseIntent("hands_on_hips", "Hands on hips", "wrists on the hips, elbows out, chest open",
                      legs=_feet(p, p.stance_natural + 0.2 * p.hip_sep, knee_min=4.0), arms=arms,
                      clavicle={"r": 2.0, "l": 2.0}, spine=0.0)


def pointing(p, **_):
    return PoseIntent("pointing_speaking", "Pointing", "right arm points to the side, weight on the left leg",
                      legs=_feet(p, p.stance_natural, weight_left=0.6, knee_min=4.0), weight_left=0.6, pelvis_roll=-3.0,
                      head=-5.0,
                      arms={"r": ArmIntent(reach=(0.92, 0.32), elbow="down", wrist=-4.0, hand=["point", "open"]),
                            "l": ArmIntent(abduction=10.0, flexion=16.0, wrist=6.0)},
                      clavicle={"r": 4.0, "l": -2.0})


def conversational(p, **_):
    return PoseIntent("conversational_gesture", "Conversational gesture",
                      "right forearm raised with an open palm, weight on the right leg",
                      legs=_feet(p, p.stance_natural, weight_left=0.36, knee_min=4.0), weight_left=0.36, pelvis_roll=3.0,
                      head=-6.0,
                      arms={"r": ArmIntent(abduction=26.0, flexion=-74.0, wrist=-12.0),
                            "l": ArmIntent(abduction=10.0, flexion=16.0, wrist=6.0)},
                      clavicle={"r": 5.0, "l": -2.0})


def shrug(p, **_):
    return PoseIntent("one_hand_shrug", "Shrugging", "both shoulders raised, forearms out, palms open",
                      legs=_feet(p, p.stance_natural, knee_min=4.0), head=6.0,
                      arms=_arms(abduction=20.0, flexion=-88.0, wrist=-10.0, hand=["open", "palm_up"]),
                      clavicle={"r": 16.0, "l": 14.0}, scapulohumeral=False)


def arm_raise(p, **_):
    return PoseIntent("both_hands_raised", "Arm raise", "both arms raised, open palms, shoulders follow",
                      legs=_feet(p, p.stance_natural, knee_min=4.0),
                      arms=_arms(abduction=158.0, flexion=-12.0, wrist=0.0), clavicle={"r": 4.0, "l": 4.0})


def head_tilt(p, **_):
    return PoseIntent("head_tilt_compensated", "Head tilt", "head tilts to the left, shoulders answer slightly",
                      legs=_feet(p, p.stance_natural, knee_min=4.0), head=14.0, chest=-3.0,
                      arms=_arms(abduction=9.0, flexion=14.0, wrist=6.0, hand=list(RELAXED_HANDS)),
                      clavicle={"l": -3.0, "r": 4.0})


def squat(p, **_):
    L = (p.leg("r") + p.leg("l")) / 2
    return PoseIntent("natural_squat", "Squatting", "pelvis lowered, knees tracking out over the feet",
                      legs=_feet(p, p.stance_natural * 1.55, knee_min=4.0, knee_max=p.front_knee["squat"]),
                      pelvis_dy=0.20 * L, arms=_arms(abduction=24.0, flexion=62.0, wrist=8.0),
                      clavicle={"r": 2.0, "l": 2.0})


def sitting(p, **_):
    L = (p.leg("r") + p.leg("l")) / 2
    return PoseIntent("sitting", "Sitting", "seated on a low seat; front-view approximation (wide knees)",
                      legs=_feet(p, p.stance_natural * 2.0, knee_min=4.0, knee_max=p.front_knee["squat"] + 15),
                      pelvis_dy=0.33 * L,
                      arms={s: ArmIntent(target_joint=f"knee_{s}", target_offset=(0.0, -0.45 * p.hand_len[s]),
                                         elbow="out", hand=["palm_down", "open"],
                                         abduction=20.0, flexion=50.0, wrist=8.0)  # FK fallback if out of reach
                            for s in SIDES},
                      notes=["front-view approximation: thighs pointing at the viewer need foreshortened drawings"])


def side_lean(p, direction: float = 1.0, **_):
    """Lateral lean (in-plane): the supported alternative to leaning toward or away from the viewer."""
    return PoseIntent("side_lean", "Side lean", "torso bends sideways; head stays near upright",
                      legs=_feet(p, p.stance_natural, knee_min=4.0), spine=-8.0 * direction, chest=-6.0 * direction,
                      head=4.0 * direction, weight_left=0.5 - 0.15 * direction,
                      arms={"r": ArmIntent(abduction=9.0 + 6 * direction, flexion=12.0, hand=list(RELAXED_HANDS)),
                            "l": ArmIntent(abduction=9.0 - 6 * direction, flexion=12.0, hand=list(RELAXED_HANDS))})


def lean_forward(p, **_):
    it = side_lean(p, 1.0)
    it.name, it.title = "leaning_forward", "Leaning forward"
    it.intent = "torso leans toward the viewer (needs 3/4 or foreshortened torso art); shown: lateral-lean fallback"
    it.notes.append("rendered fallback: side lean (in-plane); the requested lean is toward the viewer")
    return it


def lean_backward(p, **_):
    it = side_lean(p, -1.0)
    it.name, it.title = "leaning_backward", "Leaning backward"
    it.intent = "torso leans away from the viewer (needs 3/4 or foreshortened torso art); shown: lateral-lean fallback"
    it.notes.append("rendered fallback: side lean (in-plane); the requested lean is away from the viewer")
    return it


# -- gaits ---------------------------------------------------------------------------------------------------
GAIT = {
    # duty: stance share per foot; bob: flight rise / leg; sway: lateral pelvis / track; lift: swing lift / leg
    "run": {"duty": 0.30, "bob": 0.05, "compress": 0.004, "sway": 0.16, "roll": -5.0, "lift": 0.10,
            "flex": 50.0, "flex_amp": 20.0, "abd": 16.0, "abd_amp": 6.0, "clav": 3.0, "chest_counter": 0.6,
            "hem_lag": 2.5, "head_lag": 1.2},
    "walk": {"duty": 0.60, "bob": 0.0, "compress": 0.0, "sway": 0.30, "roll": -2.0, "lift": 0.02,
             "flex": 16.0, "flex_amp": 12.0, "abd": 9.0, "abd_amp": 2.5, "clav": 1.0, "chest_counter": 0.8,
             "hem_lag": 1.5, "head_lag": 0.6},
}


def gait_params(kind: str, speed: float = 1.0, exaggeration: float = 1.0) -> dict[str, float]:
    g = dict(GAIT[kind])
    s = float(np.clip(speed, 0.5, 1.6))
    e = float(np.clip(exaggeration, 0.0, 1.6))
    if kind == "run":
        g["duty"] = float(np.clip(g["duty"] - 0.08 * (s - 1), 0.22, 0.42))
    else:
        g["duty"] = float(np.clip(g["duty"] - 0.05 * (s - 1), 0.52, 0.68))
    amp = e * (0.75 + 0.25 * s)
    for k in ("bob", "compress", "sway", "roll", "lift", "flex_amp", "abd_amp", "clav", "hem_lag", "head_lag"):
        g[k] *= amp
    g["speed"], g["exaggeration"] = s, e
    return g


# Run arm styles. A front view cannot draw the forearm pointing toward the viewer, so the pump is shown in-plane:
# with fists the forearm folds up toward the chest (reads as a sprint arm); with open hands that reads as
# clutching, so the approved open-hand style keeps the hands low, swinging beside the hips.
ARM_STYLE = {"fist": {"flex": 80.0, "flex_amp": 38.0, "abd": 20.0, "abd_amp": 6.0, "wrist": 8.0},
             "open": {"flex": 24.0, "flex_amp": 16.0, "abd": 14.0, "abd_amp": 5.0, "wrist": 14.0}}


CYCLE_SECONDS = {"run": 0.75, "walk": 1.1}  # one gait cycle at speed 1
ARM_SKEW = 0.28  # > 0: the forward arm swing is quicker than the return (phase warp)
LIFT_SKEW = 0.22  # > 0: the foot lifts quickly and lowers more slowly into the next contact
ARC = {"run": 5.0, "walk": 8.0}  # elbow moves out on the forward swing, in on the return: the hand travels an arc


def warp(u: float, skew: float) -> float:
    """Monotonic phase warp on [0, 1) (fixed ends): positive skew makes the first half faster."""
    return u + skew * math.sin(2 * math.pi * u) / (2 * math.pi)


def gait(p: CharacterProfile, kind: str, phase: float, speed: float = 1.0, exaggeration: float = 1.0,
         travel: float = 0.0, cycles: float = 0.0, arm_style: str = "fist", fluid: bool = True) -> PoseIntent:
    """One frame of a front-view gait. ``phase`` in [0, 1): right-foot contact at 0, left-foot contact at 0.5.
    ``travel``: root advance per cycle along image x (px); 0 = in place. Planted feet stay fixed in world space.
    ``fluid``: eased timing, hand arcs and spring follow-through (overlap); False reproduces the plain cycle."""
    if not fluid:
        return _gait_core(p, kind, phase, speed, exaggeration, travel, cycles, arm_style, shaped=False)
    it = _gait_core(p, kind, phase, speed, exaggeration, travel, cycles, arm_style, shaped=True)
    tab = secondary_tables(p, kind, speed, exaggeration, arm_style)
    ph = phase % 1.0
    for s in SIDES:
        a = it.arms[s]
        a.abduction, a.flexion, a.wrist = (lookup(tab[f"abd_{s}"], ph), lookup(tab[f"flex_{s}"], ph),
                                           lookup(tab[f"wrist_{s}"], ph))
    it.chest = lookup(tab["chest"], ph)
    it.head = lookup(tab["head"], ph)
    it.garment = {"hem_lag_deg": lookup(tab["hem"], ph), "hair_lag_deg": lookup(tab["hair"], ph)}
    it.channels["fluid"] = True
    it.smooth_support = True
    it.notes.append("fluid: eased timing, hand arcs, spring follow-through on forearms, hands, chest, head, hem, hair")
    return it


_TABLES: dict[tuple, dict[str, np.ndarray]] = {}


def secondary_tables(p: CharacterProfile, kind: str, speed: float, exaggeration: float, arm_style: str
                     ) -> dict[str, np.ndarray]:
    """Periodic follow-through tables: the shaped (eased) cycle's driven channels passed through their springs."""
    key = (id(p), p.midline_x, kind, round(speed, 4), round(exaggeration, 4), arm_style)
    if key in _TABLES:
        return _TABLES[key]
    N = SAMPLES
    raw = [_gait_core(p, kind, i / N, speed, exaggeration, 0.0, 0.0, arm_style, shaped=True) for i in range(N)]
    T = CYCLE_SECONDS[kind] / float(np.clip(speed, 0.5, 1.6))
    sp = SPRINGS
    out: dict[str, np.ndarray] = {}
    for s in SIDES:
        sg = SIDE_SIGN[s]
        abd = sp["abduction"].response(np.array([r.arms[s].abduction for r in raw]), T)
        flex = sp["flexion"].response(np.array([r.arms[s].flexion for r in raw]), T)
        wf = sg * (abd - flex)  # forearm world direction (deg from straight down)
        wh = sp["hand"].response(wf, T)  # the hand trails the forearm
        out[f"abd_{s}"], out[f"flex_{s}"] = abd, flex
        out[f"wrist_{s}"] = np.array([r.arms[s].wrist for r in raw]) - sg * (wh - wf)
    roll = np.array([r.pelvis_roll for r in raw])
    chest_raw = -np.array([r.chest_counter for r in raw]) * roll
    chest = sp["chest"].response(chest_raw, T)
    out["chest"] = chest
    out["head"] = sp["head"].response(0.35 * (roll + chest), T)  # the head partly follows the shoulders, softly
    out["hem"] = 1.3 * (sp["hem"].response(roll, T) - roll)  # skirt angle relative to the pelvis
    sway_deg = np.degrees(np.array([r.channels["pelvis_dx"] for r in raw]) / 300.0)
    drv = out["head"] + sway_deg
    out["hair"] = sp["hair"].response(drv, T) - drv  # hair relative to the head
    _TABLES[key] = out
    if len(_TABLES) > 64:
        _TABLES.pop(next(iter(_TABLES)))
    return out


def _gait_core(p: CharacterProfile, kind: str, phase: float, speed: float = 1.0, exaggeration: float = 1.0,
               travel: float = 0.0, cycles: float = 0.0, arm_style: str = "fist", shaped: bool = False) -> PoseIntent:
    g = gait_params(kind, speed, exaggeration)
    d = g["duty"]
    L = (p.leg("r") + p.leg("l")) / 2
    track = p.track_run if kind == "run" else p.track_walk
    ph = phase % 1.0
    t = cycles + phase  # unwrapped (for moving cycles)
    legs, contact, chan = {}, {}, {}
    if kind == "walk":
        knee_cap = p.front_knee["walk_swing"]
    else:
        knee_cap = p.front_knee["swing_max"] if g["exaggeration"] > 1.0 else p.front_knee["swing"]
    ds = max(0.0, d - 0.5)  # double-support window (walk); < 0 means flight (run)
    w_half = ph % 0.5
    for s, off in (("r", 0.0), ("l", 0.5)):
        psi = (ph - off) % 1.0
        base_x = p.midline_x + LAT[s] * track / 2
        k = math.floor(t - off)  # index of the current step cycle for this foot
        land = off + k  # unwrapped time of the last landing of this foot
        x0, x1 = base_x + travel * land, base_x + travel * (land + 1.0)
        if psi < d:
            # load ramps in over the double support after landing and out over the one before toe-off
            load = 1.0 if ds <= 0 else smooth_step01(psi / ds) * (1.0 - smooth_step01((psi - (d - ds)) / ds))
            legs[s] = LegIntent(True, (x0, p.ankle_y[s]), knee_max=p.front_knee["stance"], knee_min=3.0,
                                load=load, ground=(x0, p.ankle_y[s]))
            contact[s], lift = True, 0.0
        else:
            u = (psi - d) / (1.0 - d)
            ue = warp(u, LIFT_SKEW) if shaped else u  # quick lift, gentler set-down
            lift = g["lift"] * L * smooth_pulse(ue)
            x = x0 + (x1 - x0) * (0.5 - 0.5 * math.cos(math.pi * u)) + LAT[s] * 0.05 * track * smooth_pulse(u)
            load = 0.0
            if ds <= 0:  # run: during flight, height authority passes from the leaving leg to the landing leg
                fl = (w_half - d) / (0.5 - d) if w_half >= d else None
                if fl is not None:
                    load = (1.0 - smooth_step01(fl)) if u < 0.5 else smooth_step01(fl)
            legs[s] = LegIntent(False, (x, p.ankle_y[s] - lift), foot_follow=0.35, knee_max=knee_cap, knee_min=3.0,
                                load=load, ground=(x0 if u < 0.5 else x1, p.ankle_y[s]),
                                hang_knee=3.0 + (knee_cap - 3.0) * smooth_pulse(ue),
                                clearance=3.0 * smooth_pulse(ue) + 0.15 * lift)
            contact[s] = False
        chan[f"lift_{s}"] = lift
        chan[f"load_{s}"] = load
        chan[f"stance_psi_{s}"] = psi if psi < d else None
        chan[f"swing_u_{s}"] = (psi - d) / (1.0 - d) if psi >= d else None
        chan[f"leg_forward_{s}"] = math.cos(2 * math.pi * (ph - off))  # +1 at this foot's contact (leg in front)
    # pelvis path. Negative roll = the swing-side hip lifts. A real gait drops the swing hip and clears the foot
    # with sagittal knee flexion, which a front view cannot draw; the hip hitch is the front-view substitute.
    dx = -g["sway"] * track * math.cos(2 * math.pi * (ph - d / 2))
    roll = g["roll"] * math.cos(2 * math.pi * (ph - d / 2))
    w = w_half
    if w < d:  # stance of one foot: slight compression
        dy = g["compress"] * L * smooth_pulse(w / d)
    else:  # flight (run) or double support (walk: bob 0)
        dy = -g["bob"] * L * smooth_pulse((w - d) / (0.5 - d))
    arms, clav = {}, {}
    if kind == "run":
        a = ARM_STYLE[arm_style]
        amp = g["exaggeration"] * (0.75 + 0.25 * g["speed"])
        g["flex"], g["flex_amp"], g["abd"], g["abd_amp"] = a["flex"], a["flex_amp"] * amp, a["abd"], a["abd_amp"] * amp
    for s, off in (("r", 0.0), ("l", 0.5)):
        fwd = -chan[f"leg_forward_{s}"]  # opposition: this arm is forward when this leg is back
        arc = 0.0
        if shaped:  # quicker forward swing, slower return; the elbow arcs out going forward, in coming back
            wa = warp((ph - off) % 1.0, ARM_SKEW)
            fwd = -math.cos(2 * math.pi * wa)
            arc = ARC[kind] * g["exaggeration"] * math.sin(2 * math.pi * wa)
        chan[f"arm_forward_{s}"] = fwd
        arms[s] = ArmIntent(abduction=g["abd"] - g["abd_amp"] * fwd + arc, flexion=g["flex"] + g["flex_amp"] * fwd,
                            wrist=ARM_STYLE[arm_style]["wrist"] if kind == "run" else 6.0,
                            hand=(["fist", "relaxed", "open"] if arm_style == "fist" else list(RELAXED_HANDS))
                            if kind == "run" else list(RELAXED_HANDS))
        # forward-arm shoulder lift; the fluid cycle uses a smooth positive part (max(.,0) has a kink -> jerk)
        clav[s] = g["clav"] * (((1.0 + fwd) / 2.0) ** 2 if shaped else max(fwd, 0.0))
    hem_lag = g["hem_lag"] * math.sin(2 * math.pi * (ph - d / 2))  # roll velocity (derivative of cos) -> lag
    head = g["head_lag"] * math.sin(2 * math.pi * (ph - d / 2))
    planted = [s for s in SIDES if contact[s]]
    standing = {s: (p.midline_x + LAT[s] * track / 2, p.ankle_y[s]) for s in SIDES}
    phase_name = ("contact" if min((ph % 0.5), 0.5 - (ph % 0.5)) < 0.03 else
                  "passing" if planted and abs((ph % 0.5) - d / 2) < 0.06 else
                  "stance" if planted else "flight" if kind == "run" else "double support")
    it = PoseIntent(f"{'running' if kind == 'run' else 'casual_walk'}", "Running" if kind == "run" else "Walking",
                    f"front-view {kind} cycle, phase {ph:.3f} ({phase_name})", legs=legs, arms=arms,
                    pelvis_dx=dx + travel * t, pelvis_dy=dy, pelvis_roll=roll, chest_counter=g["chest_counter"],
                    head=head, clavicle=clav, balance="dynamic", standing_feet=standing,
                    garment={"hem_lag_deg": hem_lag}, phase=ph, contact=contact,
                    channels={**chan, "duty": d, "pelvis_dy": dy, "pelvis_dx": dx, "roll": roll,
                              "phase_name": phase_name})
    chan["arm_style"] = arm_style if kind == "run" else "walk"
    it.notes.append(f"{kind}: duty {d:.2f}, speed {g['speed']:.2f}, exaggeration {g['exaggeration']:.2f}; "
                    "stride and knee drive toward the viewer are not drawn (front view)")
    return it


def run_frame(p, phase: float = 0.25, speed: float = 1.0, exaggeration: float = 1.0, travel: float = 0.0,
              arm_style: str = "fist", fluid: bool = True, **_):
    return gait(p, "run", phase, speed, exaggeration, travel, arm_style=arm_style, fluid=fluid)


def walk_frame(p, phase: float = 0.25, speed: float = 1.0, exaggeration: float = 1.0, travel: float = 0.0,
               fluid: bool = True, **_):
    return gait(p, "walk", phase, speed, exaggeration, travel, fluid=fluid)


HAND = "hand drawing from the character sheet (pending review until approved)"
GARMENT_STD = "rotation-dependent shoulder/elbow/hip/knee bands; blazer hem follows the pelvis, not the thighs"
LIBRARY: list[PresetSpec] = [
    PresetSpec("relaxed_standing", "Relaxed standing", "even weight, arms hanging with soft elbows", "static",
               relaxed, "legs: IK to planted feet (knee lateral, soft)", "static: CoM over both feet",
               "chest counter-rolls the pelvis", GARMENT_STD),
    PresetSpec("hands_at_sides", "Hands at sides", "attention stance", "static", hands_at_sides,
               "legs: IK to planted feet", "static: CoM over both feet", "none", GARMENT_STD),
    PresetSpec("hands_on_hips", "Hands on hips", "wrists on the hips, elbows out", "static", hands_on_hips,
               "arms: IK wrists to hip targets, elbows out; legs: IK", "static", "clavicles slightly raised",
               GARMENT_STD, [Requirement("hand:fist", "a knuckles-on-hip hand; " + HAND, optional=True)]),
    PresetSpec("pointing_speaking", "Pointing", "right arm points to the side", "static", pointing,
               "right arm: IK reach target (elbow down); legs: IK", "static: 60 % weight on the left foot",
               "pelvis roll, chest counter-roll, head turns toward the point", GARMENT_STD,
               [Requirement("hand:point", "index-finger pointing hand; " + HAND)], fallback="conversational_gesture",
               legacy="pointing_speaking"),
    PresetSpec("conversational_gesture", "Conversational gesture", "forearm raised with an open palm", "static",
               conversational, "arms: FK shape; legs: IK", "static: 64 % weight on the right foot",
               "pelvis roll, chest counter-roll", GARMENT_STD),
    PresetSpec("one_hand_shrug", "Shrugging", "shoulders up, forearms out, palms open", "static", shrug,
               "arms: FK; clavicles elevated", "static", "clavicle elevation", GARMENT_STD,
               [Requirement("hand:palm_up", "palm-up hands; " + HAND, optional=True)], legacy="one_hand_shrug"),
    PresetSpec("casual_walk", "Walking", "front-view walk cycle (phase-driven)", "gait", walk_frame,
               "stance legs: IK to world-locked feet; swing legs: IK to the lifted foot path",
               "dynamic: lateral sway over the stance foot", "pelvis roll, chest counter-roll, head stabilised",
               GARMENT_STD + "; hem lag", legacy="casual_walk"),
    PresetSpec("running", "Running", "front-view run cycle (phase-driven)", "gait", run_frame,
               "stance legs: IK to world-locked feet; swing legs: IK, knee <= front-view limit",
               "dynamic: CoM between the feet, flight phases", "pelvis roll and bob, chest counter-roll",
               GARMENT_STD + "; hem lag",
               [Requirement("hand:fist", "clenched running fists; " + HAND, optional=True),
                Requirement("art:thigh_knee_up", "foreshortened knee-up thighs for a full knee drive", optional=True),
                Requirement("art:shin_tucked", "tucked shins with visible soles for the recovery phase", optional=True)],
               legacy="running"),
    PresetSpec("sitting", "Sitting", "seated", "static", sitting, "legs: IK, knees out", "static",
               "none", GARMENT_STD,
               [Requirement("art:thigh_foreshortened", "thighs pointing at the viewer (front-view sitting)"),
                Requirement("art:seat", "a seat or chair")], depth_motion=True, fallback="natural_squat"),
    PresetSpec("natural_squat", "Squatting", "pelvis lowered, knees tracking out", "static", squat,
               "legs: IK, knees lateral up to the squat limit", "static: CoM over both feet",
               "none", GARMENT_STD + "; trouser knee folds"),
    PresetSpec("leaning_forward", "Leaning forward", "torso toward the viewer", "static", lean_forward,
               "spine/chest bend", "static", "head counter-tilt", GARMENT_STD,
               [Requirement("art:torso_lean_forward", "3/4 or foreshortened torso leaning toward the viewer")],
               depth_motion=True, fallback="side_lean"),
    PresetSpec("leaning_backward", "Leaning backward", "torso away from the viewer", "static", lean_backward,
               "spine/chest bend", "static", "head counter-tilt", GARMENT_STD,
               [Requirement("art:torso_lean_back", "3/4 or foreshortened torso leaning away from the viewer")],
               depth_motion=True, fallback="side_lean"),
    PresetSpec("both_hands_raised", "Arm raise", "both arms raised", "static", arm_raise,
               "arms: FK; clavicles follow abduction (scapulohumeral rhythm)", "static", "clavicle elevation",
               GARMENT_STD, [Requirement("corrective:gusset", "armpit gussets if the armpit corner tears",
                                         optional=True)], legacy="both_hands_raised"),
    PresetSpec("head_tilt_compensated", "Head tilt", "head tilt with shoulder answer", "static", head_tilt,
               "neck/head share the tilt", "static", "chest counter-tilt", GARMENT_STD + "; neck band",
               legacy="head_tilt_compensated"),
    PresetSpec("side_lean", "Side lean", "lateral torso lean (in-plane)", "static", side_lean,
               "spine/chest bend sideways", "static: pelvis shifts against the lean", "head counter-tilt",
               GARMENT_STD),
]
SPEC_BY_NAME = {s.name: s for s in LIBRARY}


class MotionPlanner:
    def __init__(self, profile: CharacterProfile):
        self.p = profile

    def plan(self, name: str, phase: float | None = None, speed: float = 1.0, exaggeration: float = 1.0,
             travel: float = 0.0, arm_style: str = "fist", fluid: bool = True) -> PoseIntent:
        spec = SPEC_BY_NAME[name]
        if spec.kind == "gait":
            return spec.build(self.p, phase=0.25 if phase is None else phase, speed=speed,
                              exaggeration=exaggeration, travel=travel, arm_style=arm_style, fluid=fluid)
        return spec.build(self.p)

    def cycle(self, name: str, n: int = 8, speed: float = 1.0, exaggeration: float = 1.0,
              travel: float = 0.0) -> list[PoseIntent]:
        return [self.plan(name, i / n, speed, exaggeration, travel) for i in range(n)]
