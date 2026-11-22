"""Phase 3.5 natural transitions between artistic key poses (deterministic, no simulation).

A ``KeyPose`` is an ``ArtisticPose`` at a time, with a hold and optional anticipation and overshoot. Between keys every
numeric pose parameter is interpolated with an ease-in/ease-out curve. On top of the easing:
- **Weight transfer leads:** weight share, contrapposto and stance start first.
- **Torso, then arms:** the line of action and clavicles follow, then the upper arms (abduction).
- **Overlap / follow-through:** forearms (flexion), wrists and the head trail behind.
- **Anticipation:** before a large arm move, a small move the other way, early in the segment.
- **Overshoot and settle:** the move carries past the key just before arrival and settles back inside the key's hold.

Anticipation and overshoot are smooth bumps (``bump``: zero value, velocity and acceleration at both ends) added to
the eased curve, so neither starts with a velocity jump. (An earlier version used a plain sine for both; its non-zero
starting slope made the arm jump into motion, which ``ArtisticAnimationEvaluator.resolution_check`` detects.)
- **Hand switches:** each happens once per segment, when the gesture is half done, and is held for the rest of the
  segment (no flicker).

Parameters are bounded by the same limits as the poses themselves (the solver clamps anything anatomically
impossible). Feet stay planted: static poses keep their contacts, so weight transfer moves the pelvis, never the
feet.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .poses import ArtisticPose, Gesture

ANTICIPATION_SPAN = 0.35  # anticipation bump over the first 35 % of the move


def _overshoots(name: str) -> bool:
    return name.startswith("arm_") or name in ("head", "line_of_action")


# per-parameter timing offset (fraction of the segment): + leads, - trails
LEAD = {"weight_left": 0.14, "contrapposto": 0.12, "free_knee": 0.1, "line_of_action": 0.06, "clavicle_r": 0.03,
        "clavicle_l": 0.03, "arm_r_abduction": 0.0, "arm_l_abduction": 0.0, "arm_r_flexion": -0.07,
        "arm_l_flexion": -0.07, "arm_r_wrist": -0.12, "arm_l_wrist": -0.12, "head": -0.09}


def ease(u: float) -> float:
    """Quintic ease-in/ease-out (smootherstep): zero velocity *and* zero acceleration at both ends, so a move never
    starts or stops with a jolt."""
    u = min(max(u, 0.0), 1.0)
    return u * u * u * (u * (6.0 * u - 15.0) + 10.0)


def bump(x: float) -> float:
    """64 x^3 (1-x)^3 on [0, 1], 0 outside: peak 1 at x = 0.5, C2-continuous (value, slope and curvature are 0 at both
    ends)."""
    if x <= 0.0 or x >= 1.0:
        return 0.0
    return 64.0 * (x * (1.0 - x)) ** 3


OVERSHOOT_START = 0.6  # the overshoot bump starts at 60 % of the move and ends with the arrival key's hold


def overshoot_curve(s: float, hold_frac: float) -> float:
    """Overshoot envelope over the move (s in 0..1) and the arrival key's hold (s in 1..1 + hold_frac)."""
    return bump((s - OVERSHOOT_START) / (1.0 - OVERSHOOT_START + max(hold_frac, 0.05)))


def params_of(ap: ArtisticPose) -> dict[str, float]:
    p = {"weight_left": ap.weight_left if ap.weight_left is not None else 0.5, "contrapposto": ap.contrapposto,
         "line_of_action": ap.line_of_action, "head": ap.head, "free_knee": ap.free_knee,
         "clavicle_r": ap.clavicle.get("r", 0.0), "clavicle_l": ap.clavicle.get("l", 0.0)}
    for s in ("r", "l"):
        g = ap.arms.get(s) or Gesture()
        p[f"arm_{s}_abduction"], p[f"arm_{s}_flexion"], p[f"arm_{s}_wrist"] = g.abduction, g.flexion, g.wrist
    return p


def with_params(base: ArtisticPose, p: dict[str, float], hands: dict[str, str]) -> ArtisticPose:
    ap = copy.deepcopy(base)
    ap.weight_left, ap.contrapposto = p["weight_left"], p["contrapposto"]
    ap.line_of_action, ap.head, ap.free_knee = p["line_of_action"], p["head"], p["free_knee"]
    ap.clavicle = {"r": p["clavicle_r"], "l": p["clavicle_l"]}
    ap.arms = {s: Gesture("hang", abduction=p[f"arm_{s}_abduction"], flexion=p[f"arm_{s}_flexion"],
                          wrist=p[f"arm_{s}_wrist"], hand=hands[s]) for s in ("r", "l")}
    return ap


@dataclass
class KeyPose:
    t: float
    pose: ArtisticPose
    label: str
    hold: float = 0.3
    anticipation: float = 0.0
    overshoot: float = 0.0
    notes: list[str] = field(default_factory=list)


class ActingSequence:
    def __init__(self, keys: list[KeyPose], fps: float = 12.0):
        self.keys = sorted(keys, key=lambda k: k.t)
        self.fps = fps
        self.duration = self.keys[-1].t + self.keys[-1].hold

    def _move_len(self, j: int) -> float:
        """Duration of the move that arrives at key ``j``."""
        a, b = self.keys[j - 1], self.keys[j]
        return max(b.t - (a.t + a.hold), 1e-6)

    def _hold_frac(self, j: int) -> float:
        return self.keys[j].hold / self._move_len(j) if 0 < j < len(self.keys) else 0.0

    def segment(self, t: float) -> tuple[int, float]:
        """(index of the key we move away from, local progress 0..1 of the move; 1 while holding)."""
        ks = self.keys
        for i in range(len(ks) - 1):
            a, b = ks[i], ks[i + 1]
            if t < b.t:
                start = a.t + a.hold
                if t <= start:
                    return i, 0.0 if i > 0 or t < start else 0.0
                return i, (t - start) / max(b.t - start, 1e-6)
        return len(ks) - 1, 1.0

    def sample(self, t: float) -> tuple[ArtisticPose, dict[str, Any]]:
        ks = self.keys
        i, u = self.segment(t)
        if i >= len(ks) - 1:
            k = ks[-1]
            return k.pose, {"key": k.label, "u": 1.0, "settle": False}
        a, b = ks[i], ks[i + 1]
        pa, pb = params_of(a.pose), params_of(b.pose)
        out = {}
        for name, v0 in pa.items():
            v1 = pb[name]
            L = LEAD.get(name, 0.0)
            # a leading parameter finishes early (at 1 - L), a trailing one starts late (at -L); both end on the key
            uu = u / (1.0 - L) if L >= 0 else (u + L) / (1.0 + L)
            v = v0 + (v1 - v0) * ease(min(max(uu, 0.0), 1.0))
            if b.anticipation and name.startswith("arm_") and name.endswith("abduction"):
                v -= b.anticipation * (v1 - v0) * bump(u / ANTICIPATION_SPAN)
            if b.overshoot and _overshoots(name):  # carries past ``b`` before arriving
                v += b.overshoot * (v1 - v0) * overshoot_curve(u, self._hold_frac(i + 1))
            out[name] = v
        # settle: the rest of the overshoot of the key we just arrived at, inside its hold
        settle = False
        if i > 0 and a.overshoot and a.t <= t < a.t + a.hold:
            prev = params_of(ks[i - 1].pose)
            s_ = 1.0 + (t - a.t) / self._move_len(i)
            for name in out:
                if _overshoots(name):
                    out[name] += a.overshoot * (pa[name] - prev[name]) * overshoot_curve(s_, self._hold_frac(i))
            settle = True
        hands = {s: (b.pose.arms[s].hand if u >= 0.5 else a.pose.arms[s].hand) for s in ("r", "l")}
        base = b.pose if u >= 0.5 else a.pose
        return with_params(base, out, hands), {"key": f"{a.label} -> {b.label}", "u": round(u, 3), "settle": settle}

    def frames(self) -> list[tuple[float, ArtisticPose, dict[str, Any]]]:
        n = int(round(self.duration * self.fps)) + 1
        return [(k / self.fps, *self.sample(k / self.fps)) for k in range(n)]


def acting_test_keys() -> list[KeyPose]:
    """The Phase 3.5 acting test: stand, shift weight, 'turn' the head, raise an arm, point at someone, shrug, relax."""
    def h(abd, flex, wrist, hand):
        return Gesture("hang", abduction=abd, flexion=flex, wrist=wrist, hand=hand)

    def pose(label, **kw):
        base = dict(action="acting_test", title=label, base="relaxed_standing", intention=label, weight_left=0.62,
                    contrapposto=0.5, head=2.0, arms={"r": h(11.0, 5.0, 3.0, "relaxed_standing"),
                                                      "l": h(9.0, 8.0, 4.0, "relaxed_standing")},
                    clavicle={"r": -2.0, "l": -1.0})
        base.update(kw)
        return ArtisticPose(**base)
    stand = pose("stands naturally")
    # weight 0.72: the static balance reaches the pelvis shift limit (0.8 x hip separation) at ~0.73 -- a higher
    # value only saturates the solve mid-move (a kink: the pelvis stops while the weight parameter still eases)
    # the weight-side (left) arm swings a little wider, clear of the hip the contrapposto pushes out
    hip_arm = {"r": h(11.0, 5.0, 3.0, "relaxed_standing"), "l": h(15.0, 9.0, 4.0, "relaxed_standing")}
    shift = pose("shifts his weight", weight_left=0.72, contrapposto=1.0, line_of_action=-4.0, head=-3.0,
                 free_knee=14.0, arms=hip_arm)
    look = pose("turns his head (tilt toward the target; a real turn needs turned-head drawings)", weight_left=0.71,
                contrapposto=1.0, line_of_action=-4.0, head=7.0, free_knee=14.0, arms=hip_arm)
    raise_ = pose("raises one arm", weight_left=0.7, contrapposto=0.8, line_of_action=4.0, head=4.0,
                  arms={"r": h(148.0, 14.0, 0.0, "raised"), "l": h(9.0, 8.0, 4.0, "relaxed_standing")},
                  clavicle={"r": 4.0, "l": -2.0})
    point = pose("points toward another character", weight_left=0.38, contrapposto=0.7, line_of_action=-5.0, head=-4.0,
                 arms={"r": h(98.0, 6.0, -4.0, "pointing"), "l": h(9.0, 10.0, 4.0, "relaxed_standing")},
                 clavicle={"r": 4.0, "l": -2.0})
    shrug = pose("shrugs", weight_left=0.6, contrapposto=0.4, head=7.0,
                 arms={"r": h(10.0, -58.0, -18.0, "shrugging"), "l": h(9.0, -50.0, -16.0, "shrugging")},
                 clavicle={"r": 13.0, "l": 18.0})
    relax = pose("returns to a relaxed stance")
    return [KeyPose(0.0, stand, "stand", hold=0.5), KeyPose(1.0, shift, "weight shift", hold=0.35, overshoot=0.0),
            KeyPose(1.7, look, "head", hold=0.3), KeyPose(2.6, raise_, "raise arm", hold=0.35, anticipation=0.08,
                                                          overshoot=0.06),
            KeyPose(3.5, point, "point", hold=0.55, overshoot=0.05), KeyPose(4.6, shrug, "shrug", hold=0.6,
                                                                              anticipation=0.06, overshoot=0.05),
            KeyPose(5.8, relax, "relax", hold=0.6)]


__all__ = ["ActingSequence", "KeyPose", "acting_test_keys", "params_of", "with_params", "ease", "bump",
           "overshoot_curve", "LEAD"]
