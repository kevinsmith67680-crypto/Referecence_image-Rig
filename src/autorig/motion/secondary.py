"""Secondary motion for gait cycles: overlap and follow-through.

Every driven part (forearm, hand, chest, head, blazer hem, hair) follows its target through a damped spring, so
it lags its driver, overshoots when the driver reverses, and settles. The steady state of a linear spring under a
periodic target is computed exactly in the frequency domain: each harmonic k of the target (frequency k / T) is
multiplied by the spring's transfer function H(f) = w^2 / (w^2 - (2 pi f)^2 + 2 i zeta w (2 pi f)). The response
is therefore itself periodic: the cycle still loops seamlessly, and frames can still be evaluated independently.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

SAMPLES = 256


@dataclass(frozen=True)
class Spring:
    freq_hz: float  # natural frequency
    damping: float  # damping ratio (1 = critical)

    def response(self, target: np.ndarray, period_s: float) -> np.ndarray:
        """Periodic steady-state response to ``target`` sampled evenly over one cycle of ``period_s`` seconds."""
        n = len(target)
        spec = np.fft.rfft(target)
        f = np.arange(len(spec)) / period_s
        w = 2 * math.pi * self.freq_hz
        s = 2j * math.pi * f
        H = w * w / (s * s + 2 * self.damping * w * s + w * w)
        return np.fft.irfft(spec * H, n)


# springs per channel (natural frequency relative to a ~1.3 Hz run cadence; lighter parts are looser)
SPRINGS = {
    "abduction": Spring(6.0, 0.7),  # upper arm: driven from the shoulder, little lag
    "flexion": Spring(3.6, 0.5),  # forearm: lags the upper arm, overshoots at the reversal
    "hand": Spring(4.5, 0.4),  # hand trails the forearm
    "chest": Spring(3.2, 0.6),  # shoulders answer the pelvis a little later
    "head": Spring(2.6, 0.7),  # head stabilises with a soft lag
    "hem": Spring(2.4, 0.35),  # blazer skirt swings behind the pelvis roll
    "hair": Spring(1.9, 0.3),  # hair trails the head and the body's sway
}


def periodic(fn, samples: int = SAMPLES) -> np.ndarray:
    return np.array([fn(i / samples) for i in range(samples)], float)


def lookup(table: np.ndarray, phase: float) -> float:
    """Linear interpolation in a periodic table sampled at i / len(table)."""
    n = len(table)
    x = (phase % 1.0) * n
    i = int(math.floor(x))
    t = x - i
    return float(table[i % n] * (1 - t) + table[(i + 1) % n] * t)


def chain_lag(driver: np.ndarray, follower: np.ndarray) -> float:
    """Cycle fraction by which ``follower`` lags ``driver`` (circular cross-correlation peak), in [-0.5, 0.5)."""
    a, b = driver - driver.mean(), follower - follower.mean()
    if a.std() < 1e-9 or b.std() < 1e-9:
        return 0.0
    c = np.fft.irfft(np.conj(np.fft.rfft(a)) * np.fft.rfft(b), len(a))
    k = int(np.argmax(c))
    lag = k / len(a)
    return lag - 1.0 if lag >= 0.5 else lag
