"""TimingCurveEngine: normalised timing curves (progress 0..1 -> 0..1) and helpers.

Curve kinds:
- ``linear``;
- ``ease_in`` (cubic: starts at rest, arrives moving);
- ``ease_out`` (cubic: leaves moving, arrives at rest);
- ``ease_in_out`` (quintic smootherstep: zero velocity and acceleration at both ends, the default);
- ``ease_in_out_cubic`` (smoothstep: zero velocity at both ends);
- ``bezier`` with control points ``(x1, y1, x2, y2)``, as CSS ``cubic-bezier``. ``x`` is monotone time, and ``y``
  may leave 0..1 for an authored overshoot, which the sequencer's soft limits keep inside the anatomical range;
- ``hold`` (a stepped key: the value stays until the end of the segment, then switches. It is an authored
  discontinuity, reported as such and never as a defect);
- ``custom``: an editable monotone cubic Hermite (Fritsch-Carlson) through artist points. It is C1 and never
  overshoots between points.

Anticipation and overshoot use the C2 ``bump`` from ``artistic/transitions.py`` (one implementation). A plain sine
starts with a velocity jump, which is the Phase 3.5 defect, so it is never used here.

Every curve is a function of continuous time, so evaluation does not depend on the frame rate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from ..artistic.transitions import bump, ease

KINDS = ("linear", "ease_in", "ease_out", "ease_in_out", "ease_in_out_cubic", "bezier", "hold", "custom")
# end velocities (du/dt at u = 0 and u = 1) of the named kinds, used for boundary-continuity reports
END_SLOPES = {"linear": (1.0, 1.0), "ease_in": (0.0, 3.0), "ease_out": (3.0, 0.0), "ease_in_out": (0.0, 0.0),
              "ease_in_out_cubic": (0.0, 0.0)}


def _clamp01(u: float) -> float:
    return 0.0 if u <= 0.0 else 1.0 if u >= 1.0 else u


def smoothstep(u: float) -> float:
    u = _clamp01(u)
    return u * u * (3.0 - 2.0 * u)


def _bezier_axis(s: float, p1: float, p2: float) -> float:
    m = 1.0 - s
    return 3.0 * m * m * s * p1 + 3.0 * m * s * s * p2 + s * s * s


def _bezier_daxis(s: float, p1: float, p2: float) -> float:
    m = 1.0 - s
    return 3.0 * m * m * p1 + 6.0 * m * s * (p2 - p1) + 3.0 * s * s * (1.0 - p2)


def bezier_value(u: float, x1: float, y1: float, x2: float, y2: float) -> float:
    """CSS-style cubic Bezier from (0, 0) to (1, 1): solve x(s) = u (Newton, bisection fallback), return y(s)."""
    u = _clamp01(u)
    if u in (0.0, 1.0):
        return u
    s = u
    for _ in range(8):
        x = _bezier_axis(s, x1, x2) - u
        d = _bezier_daxis(s, x1, x2)
        if abs(x) < 1e-12:
            break
        if abs(d) < 1e-9:
            break
        s = min(1.0, max(0.0, s - x / d))
    if abs(_bezier_axis(s, x1, x2) - u) > 1e-9:
        lo, hi = 0.0, 1.0
        for _ in range(60):
            s = 0.5 * (lo + hi)
            if _bezier_axis(s, x1, x2) < u:
                lo = s
            else:
                hi = s
    return _bezier_axis(s, y1, y2)


class MonotoneHermite:
    """Monotone piecewise cubic Hermite through ``(xs, ys)`` (Fritsch-Carlson tangents). It is C1, and between two
    points it stays inside their value range, so it never overshoots. Optional fixed end slopes."""

    def __init__(self, xs: list[float], ys: list[float], end_slopes: tuple[float | None, float | None] = (None, None)):
        if len(xs) != len(ys) or len(xs) < 2:
            raise ValueError("MonotoneHermite needs at least two points")
        if any(b <= a for a, b in zip(xs, xs[1:])):
            raise ValueError("MonotoneHermite x values must increase strictly")
        self.x, self.y = [float(v) for v in xs], [float(v) for v in ys]
        n = len(xs)
        d = [(self.y[i + 1] - self.y[i]) / (self.x[i + 1] - self.x[i]) for i in range(n - 1)]
        m = [0.0] * n
        m[0], m[-1] = d[0], d[-1]
        for i in range(1, n - 1):
            m[i] = 0.0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2.0
        for i in range(n - 1):
            if d[i] == 0.0:
                m[i] = m[i + 1] = 0.0
                continue
            a, b = m[i] / d[i], m[i + 1] / d[i]
            r = a * a + b * b
            if r > 9.0:
                t = 3.0 / math.sqrt(r)
                m[i], m[i + 1] = t * a * d[i], t * b * d[i]
        if end_slopes[0] is not None:
            m[0] = float(end_slopes[0])
        if end_slopes[1] is not None:
            m[-1] = float(end_slopes[1])
        self.m = m

    def __call__(self, x: float) -> float:
        xs = self.x
        if x <= xs[0]:
            return self.y[0]
        if x >= xs[-1]:
            return self.y[-1]
        lo, hi = 0, len(xs) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if xs[mid] <= x:
                lo = mid
            else:
                hi = mid
        h = xs[hi] - xs[lo]
        t = (x - xs[lo]) / h
        t2, t3 = t * t, t * t * t
        return ((2 * t3 - 3 * t2 + 1) * self.y[lo] + (t3 - 2 * t2 + t) * h * self.m[lo]
                + (-2 * t3 + 3 * t2) * self.y[hi] + (t3 - t2) * h * self.m[hi])


@dataclass
class Curve:
    """A timing curve for one segment (or one channel of one segment)."""

    kind: str = "ease_in_out"
    params: list[float] = field(default_factory=list)  # bezier: x1, y1, x2, y2; custom: u0, v0, u1, v1, ...

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown curve kind {self.kind!r} (known: {', '.join(KINDS)})")
        if self.kind == "bezier":
            if len(self.params) != 4:
                raise ValueError("bezier curves need [x1, y1, x2, y2]")
            x1, _, x2, _ = self.params
            if not (0.0 <= x1 <= 1.0 and 0.0 <= x2 <= 1.0):
                raise ValueError("bezier x1 and x2 must lie in [0, 1] (time must move forward)")
        if self.kind == "custom":
            pts = list(zip(self.params[0::2], self.params[1::2]))
            if len(self.params) % 2 or not pts:
                raise ValueError("custom curves need [u, v] pairs")
            xs = [0.0] + [float(u) for u, _ in pts] + [1.0]
            ys = [0.0] + [float(v) for _, v in pts] + [1.0]
            self._h = MonotoneHermite(xs, ys)

    def __call__(self, u: float) -> float:
        k = self.kind
        if k == "hold":
            return 1.0 if u >= 1.0 else 0.0
        u = _clamp01(u)
        if k == "linear":
            return u
        if k == "ease_in":
            return u * u * u
        if k == "ease_out":
            v = 1.0 - u
            return 1.0 - v * v * v
        if k == "ease_in_out":
            return ease(u)
        if k == "ease_in_out_cubic":
            return smoothstep(u)
        if k == "bezier":
            return bezier_value(u, *self.params)
        return self._h(u)

    @property
    def stepped(self) -> bool:
        return self.kind == "hold"

    def end_slopes(self, h: float = 1e-5) -> tuple[float, float]:
        if self.kind in END_SLOPES:
            return END_SLOPES[self.kind]
        if self.kind == "hold":
            return (0.0, 0.0)
        return ((self(h) - self(0.0)) / h, (self(1.0) - self(1.0 - h)) / h)

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "params": [float(v) for v in self.params]}

    @staticmethod
    def from_json(d: Any) -> "Curve":
        if d is None:
            return Curve()
        if isinstance(d, str):
            return Curve(d)
        if isinstance(d, Curve):
            return d
        return Curve(d.get("kind", "ease_in_out"), list(d.get("params", [])))


def soft_clamp(x: float, lo: float, hi: float, knee: float) -> float:
    """Identity well inside [lo, hi]; inside ``knee`` of a limit the value bends smoothly (C1) toward the limit and
    never crosses it. Used so authored overshoot never leaves the anatomical range and never kinks at the limit."""
    if hi - lo <= 2 * knee or knee <= 0:
        return min(max(x, lo), hi)
    a, b = lo + knee, hi - knee
    if a <= x <= b:
        return x
    if x > b:
        return b + knee * math.tanh((x - b) / knee)
    return a - knee * math.tanh((a - x) / knee)


def ramp(t: float, t0: float, t1: float) -> float:
    """Quintic (C2) ramp from 0 at ``t0`` to 1 at ``t1``."""
    if t1 <= t0:
        return 1.0 if t >= t1 else 0.0
    return ease((t - t0) / (t1 - t0))


__all__ = ["Curve", "KINDS", "MonotoneHermite", "bezier_value", "bump", "ease", "ramp", "smoothstep", "soft_clamp"]
