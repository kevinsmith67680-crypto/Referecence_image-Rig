"""ReferenceMotionAnalyzer: a LandmarkTrack -> rig-oriented motion features (raw and derived kept apart).

Raw data:
- the landmark track with visibility;
- confidence-weighted smoothing (``smooth``): Gaussian in time, weights = visibility; landmarks below
  ``MIN_VIS`` are interpolated from their neighbours and flagged.

Derived channels, front view, in the rig's conventions so they can drive ``PerformanceDirector`` parameters:
- ``arm_<s>_abduction``: upper arm away from the body, relative to the torso axis, + outward;
- ``arm_<s>_flexion``: the rig's elbow parameter (forearm angle = abduction - flexion, + toward the body);
- ``arm_<s>_wrist``: hand direction (wrist to index) against the forearm;
- ``clavicle_<s>``: change in shoulder elevation (deg) from the reference's own first frame;
- ``head``: head tilt (deg, + toward image right), relative to the first frame;
- ``line_of_action``: torso lean (shoulder midpoint over hip midpoint, + toward image right);
- ``pelvis_roll`` and ``weight_left`` (the hip midpoint between the ankles, 0..1).

Also derived:
- timing: speed per channel, holds (speed below 8 % of the peak for >= 0.15 s), phase boundaries (speed minima),
  anticipation (a counter-movement before the main move), overshoot and settle, acceleration peaks;
- foot contacts (ankle near its lowest and nearly still);
- arm coordination (left/right speed correlation and lag);
- head lead (head onset before the arm onset);
- asymmetry.

Foreshortening: a segment's 2D length against its longest 2D length in the clip (and the depth difference when the
provider gives depth). Below ``FORESHORTENED`` the segment moves in depth and the front-view rig cannot show that
component. It is reported, never "fixed" by stretching.

User annotations (labels on phases, notes) are stored with the reference, not in the analysis.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..phase2.skeleton import SIDE_SIGN
from .track import LandmarkTrack

MIN_VIS = 0.5
HOLD_FRAC, HOLD_MIN_S = 0.08, 0.15
FORESHORTENED = 0.8
DEPTH_SYNTH, DEPTH_ESTIMATED = 0.5, 0.6  # sin of the out-of-plane angle (exact depth / estimated MediaPipe depth)
SEGMENTS = {"upper_arm_r": ("shoulder_r", "elbow_r"), "upper_arm_l": ("shoulder_l", "elbow_l"),
            "forearm_r": ("elbow_r", "wrist_r"), "forearm_l": ("elbow_l", "wrist_l"),
            "thigh_r": ("hip_r", "knee_r"), "thigh_l": ("hip_l", "knee_l"), "shin_r": ("knee_r", "ankle_r"),
            "shin_l": ("knee_l", "ankle_l")}
CHANNELS = ["arm_r_abduction", "arm_r_flexion", "arm_r_wrist", "arm_l_abduction", "arm_l_flexion", "arm_l_wrist",
            "clavicle_r", "clavicle_l", "head", "line_of_action", "pelvis_roll", "weight_left"]


def _wrap(a):
    return (np.asarray(a) + 180.0) % 360.0 - 180.0


def ang(v: np.ndarray) -> np.ndarray:
    """Rig 'ang' convention: degrees from straight down (image +y), + toward image left."""
    return np.degrees(np.arctan2(-v[..., 0], v[..., 1]))


def unwrap_deg(a: np.ndarray) -> np.ndarray:
    return np.degrees(np.unwrap(np.radians(a)))


def smooth(track: LandmarkTrack, sigma_s: float = 0.04) -> tuple[np.ndarray, np.ndarray]:
    """Confidence-weighted Gaussian smoothing of x, y, z. Returns (smoothed (T, J, 3), low-confidence mask (T, J))."""
    d = track.data
    T = d.shape[0]
    w = np.clip(d[..., 3], 0.0, 1.0)
    low = w < MIN_VIS
    w = np.where(low, 1e-3, w)
    sig = max(sigma_s * track.fps, 0.5)
    r = int(math.ceil(3 * sig))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sig) ** 2)
    out = np.zeros(d[..., :3].shape)
    for j in range(d.shape[1]):
        for c in range(3):
            num = np.convolve(np.pad(d[:, j, c] * w[:, j], r, mode="edge"), k, mode="valid")
            den = np.convolve(np.pad(w[:, j], r, mode="edge"), k, mode="valid")
            out[:, j, c] = num[:T] / np.maximum(den[:T], 1e-9)
    return out, low


class ReferenceMotionAnalyzer:
    def __init__(self, sigma_s: float = 0.04):
        self.sigma_s = sigma_s

    def channels(self, track: LandmarkTrack, P: np.ndarray | None = None) -> dict[str, np.ndarray]:
        from .track import J  # noqa: PLC0415

        if P is None:
            P, _ = smooth(track, self.sigma_s)
        p = lambda n: P[:, J[n], :2]
        sh_mid = (p("shoulder_r") + p("shoulder_l")) / 2
        hip_mid = (p("hip_r") + p("hip_l")) / 2
        torso = ang(sh_mid - hip_mid)  # ~180 (up)
        lean = _wrap(torso - 180.0)
        ch: dict[str, np.ndarray] = {"line_of_action": -lean}  # + toward image right (rig spine convention)
        ch["line_of_action"] = _wrap(180.0 - torso)
        for s in ("r", "l"):
            sg = SIDE_SIGN[s]
            ua = unwrap_deg(ang(p(f"elbow_{s}") - p(f"shoulder_{s}")))
            fa = unwrap_deg(ang(p(f"wrist_{s}") - p(f"elbow_{s}")))
            hd = unwrap_deg(ang(p(f"index_{s}") - p(f"wrist_{s}")))
            # the torso tilt is the body frame the abduction is measured from
            abd = sg * (ua - _wrap(torso - 180.0))
            ch[f"arm_{s}_abduction"] = abd
            ch[f"arm_{s}_flexion"] = abd - sg * (fa - _wrap(torso - 180.0))
            ch[f"arm_{s}_wrist"] = sg * (fa - hd)
            half = np.linalg.norm(p(f"shoulder_{s}") - sh_mid, axis=1)
            # shoulder height along the torso axis (a lean rotates both shoulders and does not count as elevation)
            ax = sh_mid - hip_mid
            ax = ax / np.maximum(np.linalg.norm(ax, axis=1, keepdims=True), 1e-6)
            h = np.sum((p(f"shoulder_{s}") - hip_mid) * ax, axis=1)
            ch[f"clavicle_{s}"] = np.degrees(np.arctan2(h - h[0], np.maximum(np.median(half), 1e-6)))
        ear = p("ear_l") - p("ear_r")
        head_roll = np.degrees(np.arctan2(ear[:, 1], np.maximum(np.abs(ear[:, 0]), 1e-6)))
        shl = p("shoulder_l") - p("shoulder_r")
        sh_roll = np.degrees(np.arctan2(shl[:, 1], np.maximum(np.abs(shl[:, 0]), 1e-6)))
        hr = _wrap(head_roll - sh_roll)
        ch["head"] = -(hr - hr[0])  # image-right ear lower -> tilt toward image right (+)
        hip = p("hip_l") - p("hip_r")
        ch["pelvis_roll"] = np.degrees(np.arctan2(-hip[:, 1], np.maximum(np.abs(hip[:, 0]), 1e-6)))
        ank_r, ank_l = p("ankle_r"), p("ankle_l")
        span = np.maximum(np.abs(ank_l[:, 0] - ank_r[:, 0]), 0.35 * np.linalg.norm(shl, axis=1))
        ch["weight_left"] = np.clip(0.5 + (hip_mid[:, 0] - (ank_r[:, 0] + ank_l[:, 0]) / 2) / span, 0.0, 1.0)
        return {k: np.asarray(v, float) for k, v in ch.items()}

    def foreshortening(self, track: LandmarkTrack, P: np.ndarray) -> dict[str, Any]:
        from .track import J  # noqa: PLC0415

        out = {}
        depth_ok = bool((track.meta.get("provider") or {}).get("name") != "fixture") and np.abs(P[..., 2]).max() > 1e-6
        thr = DEPTH_SYNTH if (track.meta.get("provenance") or {}).get("kind") == "synthetic" else DEPTH_ESTIMATED
        for seg, (a, b) in SEGMENTS.items():
            L = np.linalg.norm(P[:, J[b], :2] - P[:, J[a], :2], axis=1)
            ref = float(np.percentile(L, 98)) if len(L) else 0.0
            ratio = L / max(ref, 1e-6)
            dzz = np.abs(P[:, J[b], 2] - P[:, J[a], 2])
            dz = dzz / np.maximum(np.hypot(L, dzz), 1e-6)  # sine of the angle out of the image plane
            fs = ratio < FORESHORTENED
            if depth_ok:
                fs = fs | (dz > thr)
            out[seg] = {"min_ratio": round(float(ratio.min()), 3), "foreshortened_frac": round(float(fs.mean()), 3),
                        "max_depth_ratio": round(float(dz.max()), 3), "frames": np.nonzero(fs)[0].tolist()[:400]}
        return out

    @staticmethod
    def timing(t: np.ndarray, ch: dict[str, np.ndarray], fps: float) -> dict[str, Any]:
        arms = ["arm_r_abduction", "arm_r_flexion", "arm_l_abduction", "arm_l_flexion", "clavicle_r", "clavicle_l",
                "head", "line_of_action"]
        V = np.stack([np.gradient(ch[c], 1.0 / fps) for c in arms], 1)
        speed = np.linalg.norm(V, axis=1)
        peak = float(speed.max()) if len(speed) else 0.0
        still = speed < HOLD_FRAC * max(peak, 1e-6)
        holds, i = [], 0
        while i < len(still):
            if still[i]:
                j = i
                while j + 1 < len(still) and still[j + 1]:
                    j += 1
                if (j - i + 1) / fps >= HOLD_MIN_S:
                    holds.append([round(float(t[i]), 3), round(float(t[j]), 3)])
                i = j + 1
            else:
                i += 1
        # phase boundaries: local speed minima between moves
        mins = [k for k in range(1, len(speed) - 1) if speed[k] <= speed[k - 1] and speed[k] <= speed[k + 1]
                and speed[k] < 0.35 * peak]
        bounds = sorted({0, len(t) - 1, *mins})
        merged = [bounds[0]]
        for b in bounds[1:]:
            if (b - merged[-1]) / fps >= 0.12:
                merged.append(b)
        phases = [{"t0": round(float(t[a]), 3), "t1": round(float(t[b]), 3),
                   "peak_speed": round(float(speed[a:b + 1].max()), 2),
                   "kind": "hold" if speed[a:b + 1].max() < 0.2 * peak else "move"} for a, b in zip(merged, merged[1:])]
        A = np.gradient(speed, 1.0 / fps)
        acc_peaks = [round(float(t[k]), 3) for k in range(1, len(A) - 1)
                     if abs(A[k]) >= abs(A[k - 1]) and abs(A[k]) >= abs(A[k + 1]) and abs(A[k]) > 0.5 * np.abs(A).max()]
        return {"speed": np.round(speed, 3).tolist(), "peak_speed": round(peak, 2), "holds": holds, "phases": phases,
                "accel_peaks_s": acc_peaks[:20]}

    @staticmethod
    def accents(t: np.ndarray, x: np.ndarray, fps: float) -> dict[str, Any]:
        """Anticipation, overshoot/settle of one channel's main move."""
        if len(x) < 5:
            return {}
        v = np.gradient(x, 1.0 / fps)
        k = int(np.argmax(np.abs(v)))
        main = np.sign(v[k]) or 1.0
        x0 = x[0]
        # anticipation: before the main move, the channel first goes the other way
        pre = x[:k + 1]
        ant = float(np.max((x0 - pre) * main)) if len(pre) else 0.0
        # overshoot: after the peak velocity, beyond the value it settles at (the first slow stretch after the move)
        vmax = abs(v[k])
        slow = [i for i in range(k + 1, len(v)) if abs(v[i]) < 0.1 * vmax]
        if slow:
            i1 = slow[0]
            j1 = min(len(x), i1 + max(2, int(0.25 * fps)))
            # the overshoot peak comes before the settle point; the hold after it gives the settle value
            k2 = int(np.argmax((x[k:i1 + 1] - x[i1]) * main)) + k
            settle = float(np.median(x[k2:j1][-max(2, int(0.15 * fps)):]))
            ext = float(np.max((x[k:j1] - settle) * main))
        else:
            settle, ext = float(x[-1]), 0.0
        return {"main_move_t": round(float(t[k]), 3), "direction": int(main), "anticipation": round(max(ant, 0.0), 3),
                "overshoot": round(max(ext, 0.0), 3), "settle_value": round(settle, 3)}

    def contacts(self, P: np.ndarray, fps: float) -> dict[str, Any]:
        from .track import J  # noqa: PLC0415

        out = {}
        for s in ("r", "l"):
            y = P[:, J[f"ankle_{s}"], 1]
            x = P[:, J[f"ankle_{s}"], 0]
            sp = np.hypot(np.gradient(x, 1.0 / fps), np.gradient(y, 1.0 / fps))
            low = y >= np.percentile(y, 70) - 2.0
            c = low & (sp < max(np.percentile(sp, 40), 1e-6) * 1.5)
            out[s] = {"contact_frac": round(float(c.mean()), 3), "frames": np.nonzero(c)[0].tolist()[:600]}
        return out

    def analyze(self, track: LandmarkTrack) -> dict[str, Any]:
        P, low = smooth(track, self.sigma_s)
        t = np.arange(track.frames) / track.fps
        ch = self.channels(track, P)
        fs = self.foreshortening(track, P)
        tm = self.timing(t, ch, track.fps)
        acc = {c: self.accents(t, ch[c], track.fps) for c in CHANNELS if c in ch}
        sp = lambda c: np.abs(np.gradient(ch[c], 1.0 / track.fps))
        onset = lambda v: (float(t[int(np.argmax(v > 0.2 * v.max()))]) if v.max() > 1e-6 else None)
        arm_on = [onset(sp(f"arm_{s}_abduction") + sp(f"arm_{s}_flexion")) for s in ("r", "l")]
        arm_on = [a for a in arm_on if a is not None]
        head_on = onset(sp("head"))
        vr, vl = sp("arm_r_abduction") + sp("arm_r_flexion"), sp("arm_l_abduction") + sp("arm_l_flexion")
        corr = float(np.corrcoef(vr, vl)[0, 1]) if vr.std() > 1e-6 and vl.std() > 1e-6 else 0.0
        lags = range(-int(0.5 * track.fps), int(0.5 * track.fps) + 1)
        lag = max(lags, key=lambda L: float(np.dot(np.roll(vr, L), vl))) / track.fps if vr.std() > 1e-6 else 0.0
        asym = {k: round(float(np.mean(np.abs(ch[f"arm_r_{k}"] - ch[f"arm_l_{k}"]))), 2) for k in ("abduction", "flexion")}
        depth = sorted({seg for seg, f in fs.items() if f["foreshortened_frac"] > 0.1})
        return {
            "fps": track.fps, "frames": track.frames, "duration": round(track.duration, 3),
            "channels": {k: np.round(v, 3).tolist() for k, v in ch.items()},
            "low_confidence_frac": {"overall": round(float(low.mean()), 3)},
            "foreshortening": fs, "depth_segments": depth,
            "timing": tm, "accents": acc,
            "contacts": self.contacts(P, track.fps),
            "coordination": {"arm_speed_correlation": round(corr, 3), "arm_lag_s": round(float(lag), 3),
                             "asymmetry_deg": asym},
            "head_lead_s": (round(min(arm_on) - head_on, 3) if (arm_on and head_on is not None) else None),
            "raw_kept": True,
        }


__all__ = ["ReferenceMotionAnalyzer", "smooth", "CHANNELS", "SEGMENTS", "FORESHORTENED"]
