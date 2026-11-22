"""WeightTransferController: continuous weight transfer with planted feet.

Fixes and guards against two Phase 3.5 problems:

1. **Planted feet slid.** ``ArtisticPosePlanner.apply`` moves the free foot in, turns it out and softens its knee as
   soon as that leg's weight share drops below 0.42. It is a threshold, so the feet jumped 13.4 px in one frame
   whenever the weight crossed over. In a performance:
   - the feet are locked where the performance starts, and move only during an explicit ``StepEvent`` (lift, swing,
     land);
   - free-leg softening (knee, turn-out) is a smooth function of the leg's weight share.
2. **The pelvis stopped mid-move.** The static balance searches the pelvis inside a window of +-0.8 hip separations
   around ``PoseIntent.pelvis_dx`` (the solver's limit). With ``pelvis_dx = 0``, weights beyond about 0.73
   saturated, and the pelvis stopped while the weight still eased. The controller centres that window on a
   calibrated estimate of the balanced pelvis offset, so the balance converges inside the window for every weight
   share. The balance result itself is the solver's and is unchanged: centring only moves the search window.

Requested weights are also soft-clamped (C1) inside the planner's range, so a curve that overshoots the range bends
smoothly instead of kinking at the clip.

Steps (front view, in-plane only: side steps, stance changes) take the weight fully onto the support leg before
lift-off. The swing foot follows an eased path with a C2 lift bump, and lands planted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ..artistic.transitions import bump
from ..motion.solver import LAT
from .curves import ease, soft_clamp

W_LO, W_HI, W_KNEE = 0.16, 0.84, 0.05
FREE_START, FREE_FULL = 0.5, 0.36  # free-leg softening ramps in as the leg's share falls from 0.5 to 0.36
TURN_OUT_DEG = 6.0


def smooth01(x: float) -> float:
    x = 0.0 if x <= 0 else 1.0 if x >= 1 else x
    return x * x * (3 - 2 * x)


@dataclass
class StepEvent:
    side: str
    t0: float  # lift-off
    t1: float  # landing
    dx: float  # ankle x change (px)
    lift: float = 14.0  # peak ankle lift (px)
    pre: float = 0.35  # weight transfer before lift-off (s)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class WeightTransferController:
    def __init__(self, eng, profile=None):
        self.eng = eng
        self.p = profile or eng.profile
        self.locks: dict[str, tuple[float, float]] = {}
        self.base_knee: dict[str, float] = {}
        self.steps: list[StepEvent] = []
        self._slope: float | None = None
        self._dx0 = 0.0

    # -- calibration -----------------------------------------------------------------------------------------
    def calibrate(self, solve_fn) -> dict[str, Any]:
        """``solve_fn(weight_left) -> solve info`` (skeleton-only). Fits the balanced pelvis x as a function of the
        weight share from two well-inside samples (the relation is linear there)."""
        a, b = solve_fn(0.5), solve_fn(0.62)
        xa = float(a["pelvis_shift_px"][0])
        xb = float(b["pelvis_shift_px"][0])
        self._dx0, self._slope = xa, (xb - xa) / 0.12
        return {"pelvis_dx_at_0.5": round(xa, 2), "slope_px_per_weight": round(self._slope, 2)}

    def pelvis_window_centre(self, wl: float) -> float:
        if self._slope is None:
            return 0.0
        return self._dx0 + self._slope * (wl - 0.5)

    # -- weights and feet ---------------------------------------------------------------------------------------
    @staticmethod
    def soft_weight(w: float) -> float:
        return soft_clamp(w, W_LO, W_HI, W_KNEE)

    @staticmethod
    def free_share(w_side: float) -> float:
        return smooth01((FREE_START - w_side) / (FREE_START - FREE_FULL))

    def lock_feet(self, it, base_it=None) -> None:
        """Remember the planted ankles of the first frame (the performance's stance) and the stance knee minimum."""
        for s, leg in it.legs.items():
            self.locks[s] = (float(leg.ankle[0]), float(leg.ankle[1]))
            b = base_it.legs.get(s) if base_it is not None else None
            self.base_knee[s] = float(b.knee_min if b is not None else leg.knee_min)

    def step_weight(self, t: float, wl: float) -> float:
        """Weight share forced by step events: all the weight on the support leg from ``pre`` before lift-off until
        landing, eased in and out."""
        for st in self.steps:
            target = 1.0 if st.side == "r" else 0.0  # stepping with the right foot -> weight fully on the left
            if st.t0 - st.pre <= t <= st.t1 + st.pre:
                k = ease((t - (st.t0 - st.pre)) / st.pre) if t < st.t0 else (
                    1.0 - ease((t - st.t1) / st.pre) if t > st.t1 else 1.0)
                wl = wl + (target - wl) * k
        return wl

    def stepping(self, t: float) -> bool:
        return any(st.t0 - st.pre <= t <= st.t1 + st.pre for st in self.steps)

    def ankle_at(self, s: str, t: float) -> tuple[tuple[float, float], bool]:
        """(ankle position, planted) of foot ``s`` at time ``t``."""
        x, y = self.locks[s]
        for st in sorted(self.steps, key=lambda e: e.t0):
            if st.side != s:
                continue
            if t >= st.t1:
                x += st.dx
            elif t > st.t0:
                u = (t - st.t0) / (st.t1 - st.t0)
                return (x + st.dx * ease(u), y - st.lift * bump(u)), False
        return (x, y), True

    def apply(self, it, t: float, wl: float, free_knee: float, base_it=None) -> dict[str, Any]:
        """Stage-1 intent edits: weight share, balance window, locked or stepping feet, continuous free-leg softening.
        ``base_it``: the preset's intent before the pose builder (its knee limits are the stance values)."""
        p = self.p
        if not self.locks:
            self.lock_feet(it, base_it)
        static = it.balance == "static"
        w_step = self.step_weight(t, wl)
        wl_eff = w_step if self.stepping(t) else self.soft_weight(w_step)
        if static:
            it.weight_left = wl_eff
            it.pelvis_dx = self.pelvis_window_centre(wl_eff)
        rep = {"weight_left": round(wl_eff, 4), "feet": {}}
        for s, leg in it.legs.items():
            w_side = wl_eff if s == "l" else 1.0 - wl_eff
            k = self.free_share(w_side)
            (ax, ay), planted = self.ankle_at(s, t)
            leg.ankle = (ax, ay)
            leg.planted = planted
            leg.load = 1.0 if planted else 0.0
            b = base_it.legs.get(s) if base_it is not None else None
            base_min = float(b.knee_min) if b is not None else self.base_knee.get(s, leg.knee_min)
            base_max = float(b.knee_max) if b is not None else leg.knee_max
            leg.knee_min = base_min + (max(free_knee, base_min) - base_min) * k
            fk = p.front_knee
            leg.knee_max = max(base_max, fk["stance"] + (fk["swing"] - fk["stance"]) * k, leg.knee_min + 2.0)
            leg.foot_angle = LAT[s] * TURN_OUT_DEG * k
            rep["feet"][s] = {"ankle": [round(ax, 2), round(ay, 2)], "planted": planted, "free_share": round(k, 3),
                              "knee_min": round(leg.knee_min, 2)}
        return rep

    def to_json(self) -> dict[str, Any]:
        return {"locks": {s: list(v) for s, v in self.locks.items()}, "steps": [s.to_json() for s in self.steps],
                "soft_weight_range": [W_LO, W_HI, W_KNEE], "free_ramp": [FREE_START, FREE_FULL],
                "pelvis_window": {"dx_at_0.5": self._dx0, "slope": self._slope}}


def com_report(solve: dict[str, Any]) -> dict[str, Any]:
    com = solve.get("com")
    sup = (solve.get("support") or {}).get("x_range")
    out = {"com_x": round(float(com[0]), 2) if com is not None else None, "support": sup,
           "balance_error_px": round(float(solve.get("balance_error_px") or 0.0), 3)}
    if com is not None and sup:
        out["outside_px"] = round(float(max(sup[0] - com[0], com[0] - sup[1], 0.0)), 2)
    return out


__all__ = ["WeightTransferController", "StepEvent", "com_report", "W_LO", "W_HI"]
