"""PoseEstimationProvider: frames -> LandmarkTrack, with per-landmark confidence.

Providers:
- ``MediaPipePoseProvider``: MediaPipe Tasks Pose Landmarker (Apache-2.0), plus the Hand Landmarker when its model is
  present. It runs locally on the CPU and is practical on an M1 Mac (arm64 wheels exist). This environment measured
  ~0.05 s per frame with the full model on a 1448 px frame. Video mode uses temporal tracking.
- ``SkeletalProvider``: a skeletal file is already a track (pass-through).
- ``FixtureProvider``: deterministic landmarks for tests.

Model files are not downloaded during animation. ``autorig reference-models`` downloads them once into
``.autorig_cache/models/`` (git-ignored) and checks their SHA-256 (``MODELS``).

Evaluated, not integrated: RTMPose/MMPose, which needs PyTorch + mmcv/mmpose, or ONNX Runtime with rtmlib, plus a
person detector. It would be heavier to install and the arm64 mmcv wheels are uncertain. MediaPipe is the default.
Other providers plug in by implementing ``estimate``.

Uncertainty handling: a landmark below ``min_visibility`` keeps its value, but its visibility is stored, and the
analyzer down-weights or ignores it. Frames with no detected person get visibility 0 and are interpolated by the
analyzer. Low-confidence output is never treated as accurate anatomy.
"""

from __future__ import annotations

import hashlib
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .track import JOINTS, MEDIAPIPE, LandmarkTrack, J

CACHE = Path(".autorig_cache") / "models"
MODELS = {
    "pose_landmarker_full": {
        "url": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/"
               "pose_landmarker_full.task",
        "sha256": "4eaa5eb7a98365221087693fcc286334cf0858e2eb6e15b506aa4a7ecdcec4ad", "bytes": 9398198},
    "pose_landmarker_lite": {
        "url": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/"
               "pose_landmarker_lite.task",
        "sha256": "59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a", "bytes": 5777746},
    "hand_landmarker": {
        "url": "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/"
               "hand_landmarker.task",
        "sha256": "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1", "bytes": 7819105},
}


def model_path(name: str, cache: Path | None = None) -> Path:
    return (cache or CACHE) / f"{name}.task"


def ensure_models(names: list[str] | None = None, cache: Path | None = None, download: bool = False) -> dict[str, Any]:
    """Check (and, with ``download``, fetch) the model files. Returns name -> {path, ok, sha256}."""
    out = {}
    for n in names or list(MODELS):
        p = model_path(n, cache)
        if not p.is_file() and download:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".part")
            urllib.request.urlretrieve(MODELS[n]["url"], tmp)  # noqa: S310 - fixed https URL
            tmp.replace(p)
        ok, h = False, None
        if p.is_file():
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            ok = h == MODELS[n]["sha256"]
        out[n] = {"path": str(p), "ok": ok, "sha256": h, "expected": MODELS[n]["sha256"]}
    return out


def mediapipe_available() -> str | None:
    """None when usable, else the reason."""
    try:
        import mediapipe  # noqa: F401, PLC0415
        from mediapipe.tasks.python import vision  # noqa: F401, PLC0415
    except Exception as e:  # noqa: BLE001 - any import failure (missing package, missing libEGL)
        return f"mediapipe unavailable: {type(e).__name__}: {e}"
    return None


class PoseEstimationProvider:
    name = "base"
    local = True

    def estimate(self, frames: list[np.ndarray], fps: float, progress: Callable[[str], Any] | None = None
                 ) -> LandmarkTrack:
        raise NotImplementedError

    def info(self) -> dict[str, Any]:
        return {"name": self.name, "local": self.local}


class MediaPipePoseProvider(PoseEstimationProvider):
    name = "mediapipe_pose_landmarker"

    def __init__(self, model: str = "pose_landmarker_full", hands: bool = True, cache: Path | None = None,
                 min_detection: float = 0.3, video_mode: bool = True):
        why = mediapipe_available()
        if why:
            raise RuntimeError(why)
        st = ensure_models([model] + (["hand_landmarker"] if hands else []), cache)
        if not st[model]["ok"]:
            raise RuntimeError(f"model {model} missing or wrong hash at {st[model]['path']} (run autorig reference-models)")
        self.model, self.cache, self.min_detection, self.video_mode = model, cache, min_detection, video_mode
        self.hands = hands and st.get("hand_landmarker", {}).get("ok", False)
        self.timing: dict[str, float] = {}

    def info(self) -> dict[str, Any]:
        import mediapipe as mp  # noqa: PLC0415

        return {"name": self.name, "local": True, "model": self.model, "hands": self.hands,
                "mediapipe_version": getattr(mp, "__version__", "?"), "timing": self.timing}

    def estimate(self, frames: list[np.ndarray], fps: float, progress: Callable[[str], Any] | None = None
                 ) -> LandmarkTrack:
        import mediapipe as mp  # noqa: PLC0415
        from mediapipe.tasks import python as mpt  # noqa: PLC0415
        from mediapipe.tasks.python import vision  # noqa: PLC0415

        mode = vision.RunningMode.VIDEO if self.video_mode else vision.RunningMode.IMAGE
        opts = vision.PoseLandmarkerOptions(
            base_options=mpt.BaseOptions(model_asset_path=str(model_path(self.model, self.cache))), running_mode=mode,
            num_poses=1, min_pose_detection_confidence=self.min_detection, min_tracking_confidence=0.3)
        det = vision.PoseLandmarker.create_from_options(opts)
        hdet = None
        if self.hands:
            hopts = vision.HandLandmarkerOptions(
                base_options=mpt.BaseOptions(model_asset_path=str(model_path("hand_landmarker", self.cache))),
                running_mode=mode, num_hands=2, min_hand_detection_confidence=0.3)
            hdet = vision.HandLandmarker.create_from_options(hopts)
        T = len(frames)
        H, W = frames[0].shape[:2]
        data = np.zeros((T, len(JOINTS), 4))
        hands = {"r": np.zeros((T, 21, 4)), "l": np.zeros((T, 21, 4))}
        t0 = time.perf_counter()
        try:
            for i, f in enumerate(frames):
                img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(f[..., :3]))
                ts = int(round(i * 1000.0 / fps))
                r = det.detect_for_video(img, ts) if self.video_mode else det.detect(img)
                if r.pose_landmarks:
                    lm = r.pose_landmarks[0]
                    for name, k in MEDIAPIPE.items():
                        p = lm[k]
                        data[i, J[name]] = [p.x * W, p.y * H, p.z * W, float(getattr(p, "visibility", 1.0) or 0.0)]
                if hdet is not None:
                    hr = hdet.detect_for_video(img, ts) if self.video_mode else hdet.detect(img)
                    for lms, hd in zip(hr.hand_landmarks or [], hr.handedness or []):
                        # MediaPipe hand labels assume a mirrored (selfie) image: "Left" is the subject's right
                        side = "r" if hd[0].category_name == "Left" else "l"
                        hands[side][i] = [[p.x * W, p.y * H, p.z * W, float(hd[0].score)] for p in lms]
                if progress and i % 30 == 0:
                    progress(f"pose {i}/{T}")
        finally:
            det.close()
            if hdet is not None:
                hdet.close()
        dt = time.perf_counter() - t0
        self.timing = {"frames": T, "total_s": round(dt, 3), "per_frame_s": round(dt / max(T, 1), 4)}
        return LandmarkTrack(fps, data, W, H, {"provider": self.info()},
                             hands if self.hands and any(h[..., 3].any() for h in hands.values()) else None)


class SkeletalProvider(PoseEstimationProvider):
    name = "skeletal_file"

    def __init__(self, track: LandmarkTrack):
        self.track = track

    def estimate(self, frames, fps, progress=None) -> LandmarkTrack:
        return self.track


class FixtureProvider(PoseEstimationProvider):
    """Landmarks from a function of the frame index (tests)."""
    name = "fixture"

    def __init__(self, fn: Callable[[int], np.ndarray]):
        self.fn = fn

    def estimate(self, frames, fps, progress=None) -> LandmarkTrack:
        data = np.stack([self.fn(i) for i in range(len(frames))])
        H, W = frames[0].shape[:2] if frames else (0, 0)
        return LandmarkTrack(fps, data, W, H, {"provider": {"name": self.name}})


PROVIDERS = {"mediapipe": MediaPipePoseProvider, "skeletal": SkeletalProvider, "fixture": FixtureProvider}
EVALUATED = {
    "mediapipe_pose_landmarker": {"licence": "Apache-2.0", "runtime": "CPU (TFLite/XNNPACK), arm64 wheels",
                                  "landmarks": "33 body + 21 per hand (Hand Landmarker)", "status": "integrated"},
    "rtmpose_mmpose": {"licence": "Apache-2.0", "runtime": "PyTorch + mmcv (or ONNX via rtmlib) + a person detector",
                       "landmarks": "17 COCO / 133 whole-body", "status": "evaluated, not integrated (heavy "
                       "dependencies; arm64 mmcv builds uncertain); fits the PoseEstimationProvider interface"},
    "movenet": {"licence": "Apache-2.0", "runtime": "TFLite", "landmarks": "17", "status": "not integrated (no hands, "
                "fewer landmarks than MediaPipe)"},
}

__all__ = ["PoseEstimationProvider", "MediaPipePoseProvider", "SkeletalProvider", "FixtureProvider", "MODELS",
           "ensure_models", "mediapipe_available", "model_path", "EVALUATED", "CACHE"]
