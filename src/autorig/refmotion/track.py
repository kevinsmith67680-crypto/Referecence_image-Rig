"""LandmarkTrack: a 2D (+ relative depth) body landmark trajectory, and procedurally authored synthetic references.

Coordinates: pixels of the reference frame (x right, y down), ``z`` relative depth (smaller = closer to the camera,
MediaPipe convention, in the same pixel scale), ``visibility`` 0..1. Sides are the *subject's* sides, which is the
rig's convention (``r`` = the character's right = image left for a front view).

The synthetic references are not motion capture. ``SyntheticPerformer`` is a simple 3D stick figure with average
adult proportions (height 1.0), animated by hand-authored joint-angle keys and projected orthographically onto a
front camera. They exist because no permission-cleared human reference video is available in this environment. They
are labelled ``provenance.kind = "synthetic"`` everywhere and are never presented as real human motion.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

SCHEMA = "autorig.reference_track"
VERSION = 1
JOINTS = ["nose", "ear_r", "ear_l", "shoulder_r", "shoulder_l", "elbow_r", "elbow_l", "wrist_r", "wrist_l",
          "index_r", "index_l", "thumb_r", "thumb_l", "hip_r", "hip_l", "knee_r", "knee_l", "ankle_r", "ankle_l",
          "heel_r", "heel_l", "toe_r", "toe_l"]
J = {n: i for i, n in enumerate(JOINTS)}
# MediaPipe Pose (33 landmarks) -> these joints. MediaPipe left/right are the subject's.
MEDIAPIPE = {"nose": 0, "ear_l": 7, "ear_r": 8, "shoulder_l": 11, "shoulder_r": 12, "elbow_l": 13, "elbow_r": 14,
             "wrist_l": 15, "wrist_r": 16, "index_l": 19, "index_r": 20, "thumb_l": 21, "thumb_r": 22, "hip_l": 23,
             "hip_r": 24, "knee_l": 25, "knee_r": 26, "ankle_l": 27, "ankle_r": 28, "heel_l": 29, "heel_r": 30,
             "toe_l": 31, "toe_r": 32}


@dataclass
class LandmarkTrack:
    fps: float
    data: np.ndarray  # (T, len(JOINTS), 4): x, y, z, visibility
    width: int = 0
    height: int = 0
    meta: dict[str, Any] = field(default_factory=dict)
    hands: dict[str, np.ndarray] | None = None  # side -> (T, 21, 4) MediaPipe hand landmarks (optional)

    @property
    def frames(self) -> int:
        return int(self.data.shape[0])

    @property
    def duration(self) -> float:
        return (self.frames - 1) / self.fps if self.frames > 1 else 0.0

    def p(self, name: str) -> np.ndarray:
        """(T, 2) pixel positions of a joint."""
        return self.data[:, J[name], :2]

    def vis(self, name: str) -> np.ndarray:
        return self.data[:, J[name], 3]

    def to_json(self) -> dict[str, Any]:
        out = {"schema": SCHEMA, "version": VERSION, "fps": self.fps, "joints": JOINTS, "width": self.width,
               "height": self.height, "meta": self.meta,
               "frames": np.round(self.data, 3).tolist()}
        if self.hands:
            out["hands"] = {s: np.round(a, 3).tolist() for s, a in self.hands.items()}
        return out

    @staticmethod
    def from_json(d: dict[str, Any]) -> "LandmarkTrack":
        if d.get("schema") != SCHEMA:
            raise ValueError(f"not a reference track (schema {d.get('schema')!r})")
        names = d["joints"]
        arr = np.asarray(d["frames"], float)
        if arr.ndim != 3 or arr.shape[2] != 4:
            raise ValueError("frames must be (T, joints, 4)")
        data = np.zeros((arr.shape[0], len(JOINTS), 4))
        for i, n in enumerate(names):
            if n in J:
                data[:, J[n]] = arr[:, i]
        hands = {s: np.asarray(a, float) for s, a in (d.get("hands") or {}).items()} or None
        return LandmarkTrack(float(d["fps"]), data, int(d.get("width", 0)), int(d.get("height", 0)),
                             dict(d.get("meta") or {}), hands)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_json()) + "\n", encoding="utf-8")

    @staticmethod
    def load(path: str | Path) -> "LandmarkTrack":
        return LandmarkTrack.from_json(json.loads(Path(path).read_text(encoding="utf-8")))

    def segment(self, t0: float, t1: float) -> "LandmarkTrack":
        i0, i1 = max(0, int(round(t0 * self.fps))), min(self.frames, int(round(t1 * self.fps)) + 1)
        hands = {s: a[i0:i1] for s, a in self.hands.items()} if self.hands else None
        return LandmarkTrack(self.fps, self.data[i0:i1].copy(), self.width, self.height,
                             {**self.meta, "segment": [t0, t1]}, hands)


# -- synthetic performer ----------------------------------------------------------------------------------------
PROPORTIONS = {"shoulder_half": 0.115, "hip_half": 0.085, "upper_arm": 0.165, "forearm": 0.145, "hand": 0.10,
               "thigh": 0.245, "shin": 0.245, "foot": 0.11, "neck": 0.05, "head": 0.12, "pelvis_y": 0.48,
               "shoulder_y": 0.80}


def _rot(axis: str, deg: float) -> np.ndarray:
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def _smooth(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * u * (u * (6 * u - 15) + 10)


class SyntheticPerformer:
    """3D stick figure (y up, x = the subject's left, z = toward the camera), animated by keys of named angles:
    ``abd_s`` (arm abduction in the coronal plane), ``fwd_s`` (shoulder flexion forward, toward the camera),
    ``elbow_s`` (elbow flexion), ``forearm_out_s`` (forearm rotation outward about the upper arm, for shrugs),
    ``palm_up_s`` (0/1 supination), ``clav_s`` (shoulder elevation, metres), ``head_roll``, ``head_yaw``,
    ``lean`` (torso roll), ``hip_shift`` (pelvis x), ``pelvis_roll``, ``hip_flex_s``/``knee_s`` (sagittal leg
    swing), ``stance`` (foot half-spacing)."""

    def __init__(self, keys: list[tuple[float, dict[str, float]]], fps: float = 30.0, px: float = 900.0):
        self.keys = sorted(keys, key=lambda k: k[0])
        self.fps, self.px = fps, px
        self.names = sorted({n for _, k in self.keys for n in k})

    def value(self, t: float, n: str) -> float:
        ks = [(tk, float(k.get(n, 0.0))) for tk, k in self.keys]  # every key is a full pose (unset = 0)
        if not ks:
            return 0.0
        if t <= ks[0][0]:
            return ks[0][1]
        for (t0, a), (t1, b) in zip(ks, ks[1:]):
            if t <= t1:
                return a + (b - a) * _smooth((t - t0) / max(t1 - t0, 1e-9))
        return ks[-1][1]

    def pose3d(self, t: float) -> dict[str, np.ndarray]:
        P = PROPORTIONS
        v = lambda n: self.value(t, n)
        pel = np.array([v("hip_shift"), P["pelvis_y"], 0.0])
        R_pel = _rot("z", v("pelvis_roll"))
        R_tor = _rot("z", v("lean"))
        out: dict[str, np.ndarray] = {}
        for s, sx in (("l", 1.0), ("r", -1.0)):
            hip = pel + R_pel @ np.array([sx * P["hip_half"], 0, 0])
            # sagittal leg swing: hip flexion rotates the thigh toward the camera (z), knee bends back
            R_th = _rot("x", -v(f"hip_flex_{s}"))
            knee = hip + R_th @ np.array([0, -P["thigh"], 0])
            R_sh = R_th @ _rot("x", v(f"knee_{s}"))
            ank = knee + R_sh @ np.array([0, -P["shin"], 0])
            stance = v("stance") or 0.0
            # planted feet: the pelvis shift moves the hips over the feet, not the feet with it
            ank[0] += sx * stance - v("hip_shift")
            knee[0] += sx * stance * 0.5 - 0.5 * v("hip_shift")
            out[f"hip_{s}"], out[f"knee_{s}"], out[f"ankle_{s}"] = hip, knee, ank
            out[f"heel_{s}"] = ank + np.array([0, -0.02, -0.03])
            out[f"toe_{s}"] = ank + R_sh @ np.array([0, -0.02, P["foot"]])
        chest = pel + R_tor @ np.array([0, P["shoulder_y"] - P["pelvis_y"], 0])
        neck = chest + R_tor @ np.array([0, P["neck"], 0])
        R_head = R_tor @ _rot("z", v("head_roll")) @ _rot("y", v("head_yaw"))
        head = neck + R_head @ np.array([0, P["head"] * 0.55, 0])
        out["nose"] = head + R_head @ np.array([0, 0, 0.05])
        for s, sx in (("l", 1.0), ("r", -1.0)):
            out[f"ear_{s}"] = head + R_head @ np.array([sx * 0.06, 0.01, -0.01])
            sh = chest + R_tor @ np.array([sx * P["shoulder_half"], v(f"clav_{s}"), 0])
            # arm: abduction in the coronal plane (z axis), forward flexion (x axis, toward the camera)
            R_ua = R_tor @ _rot("z", sx * v(f"abd_{s}")) @ _rot("x", -v(f"fwd_{s}"))
            el = sh + R_ua @ np.array([0, -P["upper_arm"], 0])
            # elbow flexion bends the forearm forward (toward the camera); forearm_out rotates the bent forearm
            # outward about the upper arm (a shrug's opening forearms)
            R_fa = R_ua @ _rot("y", sx * v(f"forearm_out_{s}")) @ _rot("x", -v(f"elbow_{s}"))
            wr = el + R_fa @ np.array([0, -P["forearm"], 0])
            R_h = R_fa @ _rot("z", sx * v(f"wrist_{s}"))
            idx = wr + R_h @ np.array([0, -P["hand"], 0])
            sup = v(f"palm_up_{s}")
            th_dir = R_h @ np.array([-sx * 0.035 * (1 - sup), -0.03, 0.035 * sup])
            out[f"shoulder_{s}"], out[f"elbow_{s}"], out[f"wrist_{s}"] = sh, el, wr
            out[f"index_{s}"], out[f"thumb_{s}"] = idx, wr + th_dir
        return out

    def track(self, duration: float, meta: dict[str, Any], width: int = 1080, height: int = 1080) -> LandmarkTrack:
        n = int(round(duration * self.fps)) + 1
        data = np.zeros((n, len(JOINTS), 4))
        cx, base = width / 2, height * 0.95
        for i in range(n):
            P3 = self.pose3d(i / self.fps)
            for name, p in P3.items():
                # front camera: the subject faces the camera, so the subject's left (+x) is image right
                data[i, J[name]] = [cx + p[0] * self.px, base - p[1] * self.px, -p[2] * self.px, 1.0]
        return LandmarkTrack(self.fps, data, width, height, meta)


SYNTH_PROVENANCE = {"kind": "synthetic", "source": "procedurally authored by AutoRig (SyntheticPerformer keys); no "
                    "human motion capture, no third-party footage", "usage_permission": "generated in this project; "
                    "no third-party rights", "note": "a stand-in for a permitted human reference, labelled synthetic"}
REST = {"abd_r": 8.0, "abd_l": 8.0, "elbow_r": 8.0, "elbow_l": 8.0, "stance": 0.02}


def synthetic_reference(name: str, fps: float = 30.0) -> LandmarkTrack:
    """The three Phase 3.7 test references: ``pointing``, ``shrug``, ``walking`` (front camera)."""
    if name == "pointing":
        k = [(0.0, {**REST}), (0.45, {**REST, "abd_r": 2.0, "elbow_r": 20.0, "lean": 1.5}),  # anticipation: pull in
             (0.85, {**REST, "abd_r": 86.0, "fwd_r": 8.0, "elbow_r": 4.0, "wrist_r": 0.0, "head_roll": 4.0,
                     "lean": -3.0, "hip_shift": -0.012}),
             (0.98, {**REST, "abd_r": 91.0, "fwd_r": 8.0, "elbow_r": 2.0, "head_roll": 5.0, "lean": -3.5,
                     "hip_shift": -0.014}),  # overshoot
             (1.15, {**REST, "abd_r": 87.0, "fwd_r": 8.0, "elbow_r": 4.0, "head_roll": 4.0, "lean": -3.0,
                     "hip_shift": -0.012}),
             (1.85, {**REST, "abd_r": 87.0, "fwd_r": 8.0, "elbow_r": 4.0, "head_roll": 4.0, "lean": -3.0,
                     "hip_shift": -0.012}),  # hold
             (2.6, {**REST})]
        meta = {"category": "gesture", "gesture": "pointing", "hand_actions": {"r": "pointing", "l": "relaxed_standing"},
                "emotion": "assertive", "intensity": "medium", "view": "front"}
        dur = 2.8
    elif name == "shrug":
        up = {**REST, "clav_r": 0.022, "clav_l": 0.022, "abd_r": 14.0, "abd_l": 14.0, "elbow_r": 70.0, "elbow_l": 70.0,
              "forearm_out_r": 55.0, "forearm_out_l": 55.0, "palm_up_r": 1.0, "palm_up_l": 1.0, "head_roll": -6.0}
        k = [(0.0, {**REST}), (0.3, {**REST, "clav_r": -0.004, "clav_l": -0.004}),  # small dip (anticipation)
             (0.7, up), (0.82, {**up, "clav_r": 0.025, "clav_l": 0.025}), (0.95, up),
             (1.35, up), (2.0, {**REST})]
        meta = {"category": "gesture", "gesture": "shrug", "hand_actions": {"r": "shrugging", "l": "shrugging"},
                "palm": "PALM_UP", "emotion": "uncertain", "intensity": "expressive", "view": "front"}
        dur = 2.2
    elif name == "walking":
        k = []
        for c in range(3):
            t0 = c * 1.0
            k += [(t0, {**REST, "hip_flex_r": 22.0, "knee_r": 8.0, "hip_flex_l": -14.0, "knee_l": 12.0,
                        "fwd_r": -14.0, "fwd_l": 16.0, "elbow_l": 20.0, "pelvis_roll": 3.0, "hip_shift": 0.012}),
                  (t0 + 0.25, {**REST, "hip_flex_r": 2.0, "knee_r": 4.0, "hip_flex_l": 18.0, "knee_l": 55.0,
                               "fwd_r": 0.0, "fwd_l": 0.0, "pelvis_roll": 0.0, "hip_shift": 0.018}),
                  (t0 + 0.5, {**REST, "hip_flex_l": 22.0, "knee_l": 8.0, "hip_flex_r": -14.0, "knee_r": 12.0,
                              "fwd_l": -14.0, "fwd_r": 16.0, "elbow_r": 20.0, "pelvis_roll": -3.0, "hip_shift": -0.012}),
                  (t0 + 0.75, {**REST, "hip_flex_l": 2.0, "knee_l": 4.0, "hip_flex_r": 18.0, "knee_r": 55.0,
                               "fwd_r": 0.0, "fwd_l": 0.0, "pelvis_roll": 0.0, "hip_shift": -0.018})]
        k.append((3.0, k[0][1]))
        meta = {"category": "locomotion", "gesture": "walking", "hand_actions": {"r": "walking", "l": "walking"},
                "emotion": "neutral", "intensity": "medium", "view": "front", "cyclic": True, "cycle_s": 1.0}
        dur = 3.0
    else:
        raise ValueError(f"unknown synthetic reference {name!r}")
    tr = SyntheticPerformer(k, fps).track(dur, {"name": name, "provenance": SYNTH_PROVENANCE, **meta})
    return tr


__all__ = ["LandmarkTrack", "JOINTS", "J", "MEDIAPIPE", "SyntheticPerformer", "synthetic_reference",
           "SYNTH_PROVENANCE", "SCHEMA"]
