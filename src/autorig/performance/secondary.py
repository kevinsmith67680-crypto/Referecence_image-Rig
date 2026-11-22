"""SecondaryMotionController: controlled follow-through for hair and the blazer hem in any sequence.

Each part is a damped spring (natural frequency, damping ratio) that follows a driver angle derived from the body
motion:
- hair follows the head tilt plus the body's sway;
- the hem follows the pelvis roll plus the torso lean.

The part's lag is ``gain * (spring - driver)``, soft-limited to ``limit_deg``. It reaches the existing garment
terms of the structured deformer (``PoseIntent.garment``: ``hair_lag_deg``, ``hem_lag_deg``), so the hair sways
from its top (never at the hairline) and the hem swings about its pivot. No cloth simulation is used.

Determinism: the springs are integrated once, from t = 0 at rest, at a fixed internal step (``DT`` = 1/240 s,
semi-implicit Euler). The result is cached as a table, and any time is a table lookup. Scrubbing, jumping between
frames, replaying, changing the frame rate, exporting or reopening a project therefore all give the same values:
nothing depends on playback history. ``reset`` times restart a spring at rest (for cuts).

Parts that are not driven:
- the tie: NEEDS_ARTWORK. It is drawn on the torso layer and the vest behind it does not exist, so a swing would
  open a hole;
- loose sleeve sections: UNSUPPORTED. Sleeves are rigid segments in the Phase 3.3 garment rig;
- the collar, lapels, buttons, badge and pocket flaps: rigid fits, never driven.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any, Callable

import numpy as np

from .curves import ramp, soft_clamp

DT = 1.0 / 240.0
DEFAULTS: dict[str, dict[str, Any]] = {
    "hair": {"enabled": True, "freq_hz": 1.9, "damping": 0.45, "gain": 0.85, "limit_deg": 5.0,
             "driver": "head tilt + body sway", "status": "IMPLEMENTED"},
    "hem": {"enabled": True, "freq_hz": 2.4, "damping": 0.4, "gain": 1.3, "limit_deg": 4.0,
            "driver": "pelvis roll + torso lean", "status": "IMPLEMENTED"},
    "tie": {"enabled": False, "status": "NEEDS_ARTWORK",
            "reason": "the tie is drawn on the torso layer and the vest behind it is not drawn: a swinging tie would "
                      "open a hole (needs art:vest_behind_tie)"},
    "sleeve": {"enabled": False, "status": "UNSUPPORTED",
               "reason": "sleeves are rigid segments in the Phase 3.3 garment rig; there is no loose-sleeve term"},
}
RIGID_PARTS = ["collar", "lapels", "buttons", "badge", "pocket flaps"]
GARMENT_KEY = {"hair": "hair_lag_deg", "hem": "hem_lag_deg"}


def merged_settings(user: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out = copy.deepcopy(DEFAULTS)
    for part, s in (user or {}).items():
        if part not in out:
            raise ValueError(f"unknown secondary part {part!r} (known: {', '.join(out)})")
        if part in ("tie", "sleeve") and s.get("enabled"):
            raise ValueError(f"{part}: {out[part]['reason']}")
        for k, v in s.items():
            if k in ("freq_hz",) and not 0.3 <= float(v) <= 8.0:
                raise ValueError(f"{part}.freq_hz must be in [0.3, 8]")
            if k == "damping" and not 0.05 <= float(v) <= 2.0:
                raise ValueError(f"{part}.damping must be in [0.05, 2]")
            if k == "limit_deg" and not 0.0 <= float(v) <= 12.0:
                raise ValueError(f"{part}.limit_deg must be in [0, 12]")
            if k == "gain" and not 0.0 <= float(v) <= 3.0:
                raise ValueError(f"{part}.gain must be in [0, 3]")
            out[part][k] = v
    return out


class SecondaryMotionController:
    def __init__(self, settings: dict[str, Any] | None = None, intensity: float = 1.0, resets: list[float] | None = None,
                 overrides: list[dict[str, Any]] | None = None):
        self.settings = merged_settings(settings)
        self.intensity = float(intensity)
        self.resets = sorted(resets or [])
        self.overrides = list(overrides or [])  # {"part", "t0", "t1", "value", "blend"}
        self.tables: dict[str, np.ndarray] = {}
        self.drivers: dict[str, np.ndarray] = {}
        self.duration = 0.0
        self.key = ""

    def simulate(self, drivers: dict[str, Callable[[float], float]], duration: float) -> str:
        """Integrate every enabled part over [0, duration] (plus one second of settle). Returns the cache key."""
        n = int(math.ceil((duration + 1.0) / DT)) + 2
        ts = np.arange(n) * DT
        self.duration = duration
        self.tables, self.drivers = {}, {}
        for part, s in self.settings.items():
            if not s.get("enabled") or part not in drivers:
                continue
            d = np.array([drivers[part](float(min(t, duration))) for t in ts])
            w = 2 * math.pi * float(s["freq_hz"])
            z = float(s["damping"])
            x, v = d[0], 0.0
            out = np.empty(n)
            ri = 0
            for i in range(n):
                while ri < len(self.resets) and ts[i] >= self.resets[ri]:
                    x, v = d[i], 0.0  # cut: restart at rest on the current driver
                    ri += 1
                out[i] = x
                v += DT * (w * w * (d[i] - x) - 2 * z * w * v)
                x += DT * v
            self.tables[part] = out - d
            self.drivers[part] = d
        h = hashlib.sha256(json.dumps({"s": self.settings, "i": self.intensity, "r": self.resets,
                                       "d": {k: np.round(v[::24], 6).tolist() for k, v in self.drivers.items()}},
                                      sort_keys=True).encode()).hexdigest()[:16]
        self.key = h
        return h

    def raw(self, part: str, t: float) -> float:
        tab = self.tables.get(part)
        if tab is None:
            return 0.0
        x = max(0.0, t) / DT
        i = int(math.floor(x))
        if i >= len(tab) - 1:
            return float(tab[-1])
        f = x - i
        return float(tab[i] * (1 - f) + tab[i + 1] * f)

    def value(self, part: str, t: float) -> float:
        s = self.settings.get(part, {})
        if not s.get("enabled"):
            return 0.0
        lim = float(s["limit_deg"])
        v = soft_clamp(float(s["gain"]) * self.intensity * self.raw(part, t), -lim, lim, max(lim * 0.4, 1e-6))
        for o in self.overrides:  # keyframe overrides blend in and out with C2 ramps
            if o.get("part") != part:
                continue
            b = float(o.get("blend", 0.15))
            w = ramp(t, o["t0"] - b, o["t0"]) * (1.0 - ramp(t, o["t1"], o["t1"] + b))
            if w > 0:
                v = v + w * (float(o["value"]) - v)
        return v

    def garment(self, t: float) -> dict[str, float]:
        return {GARMENT_KEY[p]: self.value(p, t) for p in GARMENT_KEY if self.settings[p].get("enabled")}

    def report(self) -> dict[str, Any]:
        out = {"dt": DT, "cache_key": self.key, "intensity": self.intensity, "parts": {}, "rigid": RIGID_PARTS}
        for p, s in self.settings.items():
            e = {k: v for k, v in s.items()}
            if p in self.tables:
                vals = np.array([self.value(p, t) for t in np.arange(0, self.duration + 1e-9, 1 / 48)])
                e.update({"max_abs_deg": round(float(np.abs(vals).max()), 3),
                          "settled_at_end_deg": round(float(abs(self.value(p, self.duration))), 3)})
            out["parts"][p] = e
        return out


__all__ = ["SecondaryMotionController", "DEFAULTS", "RIGID_PARTS", "DT", "merged_settings"]
