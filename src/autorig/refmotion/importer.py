"""ReferenceMotionImporter: reference media -> frames or a skeletal track, with metadata and quality flags.

Inputs:
- MP4, MOV and other video, decoded by the system ``ffmpeg`` binary (subprocess, raw RGB frames; no Python codec
  dependency);
- GIF, through PIL;
- image sequences: a directory or a glob of PNG/JPG files, in name order;
- skeletal files: AutoRig ``autorig.reference_track`` JSON, or BVH. BVH is forward-kinematics evaluated and projected
  orthographically on a front camera; the root faces +Z by default.

Every import needs metadata: provenance and a usage permission statement. A reference without a permission
statement is refused. The importer records fps, duration, size, camera orientation (front / three-quarter / side /
back, estimated from the landmarks once poses exist) and subject scale. Quality flags report poor visibility,
occlusion, an unsupported view and low resolution.
"""

from __future__ import annotations

import glob
import json
import math
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .track import JOINTS, LandmarkTrack, J

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MAX_FRAMES = 900
QUALITY = {"min_visibility": 0.5, "occluded_frac": 0.3, "min_subject_px": 240, "side_ratio": 0.35,
           "three_quarter_ratio": 0.65}


class ImportError_(ValueError):
    pass


@dataclass
class ReferenceMedia:
    kind: str  # video | gif | images | skeletal
    fps: float
    frames: list[np.ndarray] = field(default_factory=list)  # RGB uint8 (empty for skeletal)
    track: LandmarkTrack | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        n = len(self.frames) if self.frames else (self.track.frames if self.track else 0)
        return (n - 1) / self.fps if n > 1 else 0.0


def ffmpeg_available() -> str | None:
    return shutil.which("ffmpeg")


def _probe(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {}
    r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate,nb_frames,duration", "-of", "json", str(path)],
                       capture_output=True, text=True, timeout=60)
    try:
        s = json.loads(r.stdout)["streams"][0]
    except (ValueError, KeyError, IndexError):
        return {}
    num, _, den = s.get("r_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    return {"width": int(s["width"]), "height": int(s["height"]), "fps": fps,
            "duration": float(s.get("duration") or 0.0)}


def decode_video(path: Path, fps: float | None = None, max_frames: int = MAX_FRAMES, max_side: int = 960
                 ) -> tuple[list[np.ndarray], float, dict[str, Any]]:
    exe = ffmpeg_available()
    if not exe:
        raise ImportError_("ffmpeg is not installed: video references cannot be decoded (install ffmpeg, or import "
                           "an image sequence)")
    info = _probe(path)
    src_fps = info.get("fps") or 30.0
    out_fps = min(fps or src_fps, src_fps, 30.0)
    w, h = info.get("width", 0), info.get("height", 0)
    if not w:
        raise ImportError_(f"cannot read video stream of {path.name}")
    s = min(1.0, max_side / max(w, h))
    W, H = int(w * s) // 2 * 2, int(h * s) // 2 * 2
    cmd = [exe, "-v", "error", "-i", str(path), "-vf", f"fps={out_fps},scale={W}:{H}", "-frames:v", str(max_frames),
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    r = subprocess.run(cmd, capture_output=True, timeout=600)
    if r.returncode != 0:
        raise ImportError_(f"ffmpeg failed: {r.stderr.decode(errors='replace')[:300]}")
    n = len(r.stdout) // (W * H * 3)
    arr = np.frombuffer(r.stdout[:n * W * H * 3], np.uint8).reshape(n, H, W, 3)
    return [a.copy() for a in arr], out_fps, {**info, "decoded_size": [W, H], "decoded_frames": n}


def encode_video(frames: list[np.ndarray], path: Path, fps: float) -> Path:
    """RGB frames -> H.264 MP4 (used to build round-trip test references from AutoRig renders)."""
    exe = ffmpeg_available()
    if not exe:
        raise ImportError_("ffmpeg is not installed")
    h, w = frames[0].shape[:2]
    cmd = [exe, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(path)]
    p = subprocess.run(cmd, input=b"".join(np.ascontiguousarray(f[..., :3]).tobytes() for f in frames),
                       capture_output=True, timeout=600)
    if p.returncode != 0:
        raise ImportError_(f"ffmpeg encode failed: {p.stderr.decode(errors='replace')[:300]}")
    return path


def decode_gif(path: Path, max_frames: int = MAX_FRAMES) -> tuple[list[np.ndarray], float]:
    from PIL import Image, ImageSequence  # noqa: PLC0415

    im = Image.open(path)
    frames, durs = [], []
    for f in ImageSequence.Iterator(im):
        frames.append(np.asarray(f.convert("RGB")).copy())
        durs.append(f.info.get("duration", 100) or 100)
        if len(frames) >= max_frames:
            break
    return frames, 1000.0 / float(np.median(durs))


def load_images(spec: str | Path, fps: float) -> list[np.ndarray]:
    from PIL import Image  # noqa: PLC0415

    p = Path(spec)
    files = sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGE_EXT) if p.is_dir() else sorted(
        Path(x) for x in glob.glob(str(spec)))
    if not files:
        raise ImportError_(f"no images found for {spec}")
    return [np.asarray(Image.open(f).convert("RGB")) for f in files[:MAX_FRAMES]]


# -- BVH ----------------------------------------------------------------------------------------------------------
BVH_MAP = {"head": "nose", "neck": None, "leftshoulder": None, "leftarm": "shoulder_l", "leftforearm": "elbow_l",
           "lefthand": "wrist_l", "rightarm": "shoulder_r", "rightforearm": "elbow_r", "righthand": "wrist_r",
           "leftupleg": "hip_l", "leftleg": "knee_l", "leftfoot": "ankle_l", "lefttoebase": "toe_l",
           "rightupleg": "hip_r", "rightleg": "knee_r", "rightfoot": "ankle_r", "righttoebase": "toe_r",
           "lefthandindex1": "index_l", "righthandindex1": "index_r", "lefthandthumb1": "thumb_l",
           "righthandthumb1": "thumb_r"}


def parse_bvh(text: str) -> tuple[list[dict[str, Any]], np.ndarray, float]:
    """Minimal BVH reader: joints (name, parent, offset, channels) and the motion matrix."""
    tok = text.split()
    i, joints, stack = 0, [], []
    while i < len(tok) and tok[i] != "MOTION":
        t = tok[i]
        if t in ("ROOT", "JOINT"):
            joints.append({"name": tok[i + 1], "parent": stack[-1] if stack else None, "offset": None, "channels": []})
            i += 2
        elif t == "End":
            joints.append({"name": f"{joints[stack[-1]]['name']}_end", "parent": stack[-1], "offset": None,
                           "channels": [], "end": True})
            i += 2
        elif t == "{":
            stack.append(len(joints) - 1)
            i += 1
        elif t == "}":
            stack.pop()
            i += 1
        elif t == "OFFSET":
            joints[stack[-1]]["offset"] = [float(x) for x in tok[i + 1:i + 4]]
            i += 4
        elif t == "CHANNELS":
            n = int(tok[i + 1])
            joints[stack[-1]]["channels"] = tok[i + 2:i + 2 + n]
            i += 2 + n
        else:
            i += 1
    if i >= len(tok):
        raise ImportError_("BVH: no MOTION section")
    nf = int(tok[i + 2])
    ft = float(tok[i + 5])
    vals = np.array([float(x) for x in tok[i + 6:]], float)
    nch = sum(len(j["channels"]) for j in joints)
    if nch == 0 or len(vals) < nf * nch:
        raise ImportError_("BVH: motion data shorter than declared")
    return joints, vals[:nf * nch].reshape(nf, nch), 1.0 / ft


def _rotm(axis: str, deg: float) -> np.ndarray:
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return {"X": np.array([[1, 0, 0], [0, c, -s], [0, s, c]]), "Y": np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
            "Z": np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])}[axis]


def bvh_track(text: str, width: int = 1080, height: int = 1080, meta: dict[str, Any] | None = None) -> LandmarkTrack:
    joints, mot, fps = parse_bvh(text)
    T = mot.shape[0]
    pos = np.zeros((T, len(joints), 3))
    for f in range(T):
        col = 0
        R = [None] * len(joints)
        for k, j in enumerate(joints):
            off = np.array(j["offset"] or [0, 0, 0], float)
            loc = np.eye(3)
            tr = np.zeros(3)
            for ch in j["channels"]:
                v = mot[f, col]
                col += 1
                if ch.endswith("position"):
                    tr["XYZ".index(ch[0])] = v
                else:
                    loc = loc @ _rotm(ch[0], v)
            if j["parent"] is None:
                R[k] = loc
                pos[f, k] = off + tr
            else:
                p = j["parent"]
                pos[f, k] = pos[f, p] + R[p] @ off
                R[k] = R[p] @ loc
    lo, hi = pos[..., 1].min(), pos[..., 1].max()
    scale = 0.85 * height / max(hi - lo, 1e-6)
    data = np.zeros((T, len(JOINTS), 4))
    found = set()
    for k, j in enumerate(joints):
        key = j["name"].lower().replace("_", "").replace(":", "").split("mixamorig")[-1]
        tgt = BVH_MAP.get(key)
        if tgt:
            found.add(tgt)
            P = pos[:, k]
            # front camera looking down -Z at a subject facing +Z: the subject's left (+X in BVH) is image right
            data[:, J[tgt]] = np.stack([width / 2 + P[:, 0] * scale, height * 0.95 - (P[:, 1] - lo) * scale,
                                        -P[:, 2] * scale, np.ones(T)], 1)
    for s in ("r", "l"):  # ears from the head when the rig has none
        if f"ear_{s}" not in found and "nose" in found:
            data[:, J[f"ear_{s}"]] = data[:, J["nose"]] + np.array([(-1 if s == "r" else 1) * 0.06 * scale, 0, 0, 0])
    tr = LandmarkTrack(fps, data, width, height, {**(meta or {}), "bvh_joints_mapped": sorted(found)})
    missing = [n for n in ("shoulder_r", "shoulder_l", "elbow_r", "elbow_l", "wrist_r", "wrist_l", "hip_r", "hip_l")
               if n not in found]
    if missing:
        raise ImportError_(f"BVH: could not map {', '.join(missing)} (joint names follow Mixamo/CMU conventions)")
    return tr


# -- importer ------------------------------------------------------------------------------------------------------
class ReferenceMotionImporter:
    def __init__(self, max_frames: int = MAX_FRAMES):
        self.max_frames = max_frames

    @staticmethod
    def check_meta(meta: dict[str, Any]) -> list[str]:
        errs = []
        prov = meta.get("provenance") or {}
        if not (prov.get("usage_permission") or meta.get("usage_permission")):
            errs.append("usage_permission is required: state who owns the footage and that it may be used as an "
                        "animation reference")
        if not (prov.get("source") or meta.get("source")):
            errs.append("provenance source is required (where the reference came from)")
        return errs

    def load(self, path: str | Path, meta: dict[str, Any], fps: float | None = None) -> ReferenceMedia:
        errs = self.check_meta(meta)
        if errs:
            raise ImportError_("; ".join(errs))
        p = Path(path)
        ext = p.suffix.lower()
        if ext in VIDEO_EXT:
            frames, f, info = decode_video(p, fps, self.max_frames)
            return ReferenceMedia("video", f, frames, None, {**meta, "media": info, "file": p.name})
        if ext == ".gif":
            frames, f = decode_gif(p, self.max_frames)
            return ReferenceMedia("gif", fps or f, frames, None, {**meta, "file": p.name})
        if ext == ".json":
            tr = LandmarkTrack.load(p)
            tr.meta = {**tr.meta, **meta}
            return ReferenceMedia("skeletal", tr.fps, [], tr, {**meta, "file": p.name})
        if ext == ".bvh":
            tr = bvh_track(p.read_text(encoding="utf-8", errors="replace"), meta=meta)
            return ReferenceMedia("skeletal", tr.fps, [], tr, {**meta, "file": p.name})
        if p.is_dir() or any(ch in str(path) for ch in "*?"):
            frames = load_images(path, fps or 24.0)
            return ReferenceMedia("images", fps or 24.0, frames, None, {**meta, "file": str(path)})
        raise ImportError_(f"unsupported reference format: {p.name}")

    @staticmethod
    def quality(tr: LandmarkTrack) -> dict[str, Any]:
        """Visibility, occlusion, view and subject scale from a landmark track."""
        flags = []
        core = ["shoulder_r", "shoulder_l", "elbow_r", "elbow_l", "wrist_r", "wrist_l", "hip_r", "hip_l"]
        vis = np.stack([tr.vis(n) for n in core], 1)
        mean_vis = float(vis.mean()) if vis.size else 0.0
        occl = {n: float((tr.vis(n) < QUALITY["min_visibility"]).mean()) for n in core}
        if mean_vis < QUALITY["min_visibility"]:
            flags.append(f"poor visibility (mean {mean_vis:.2f})")
        for n, fr in occl.items():
            if fr > QUALITY["occluded_frac"]:
                flags.append(f"{n} occluded or uncertain in {fr:.0%} of frames")
        sw = np.linalg.norm(tr.p("shoulder_l") - tr.p("shoulder_r"), axis=1)
        torso = np.linalg.norm((tr.p("shoulder_l") + tr.p("shoulder_r")) / 2 - (tr.p("hip_l") + tr.p("hip_r")) / 2, axis=1)
        ratio = float(np.median(sw / np.maximum(torso, 1e-6)))
        # a front-facing adult: shoulder width ~0.75-0.9 of the shoulder-hip distance; side view -> near 0
        if ratio < QUALITY["side_ratio"]:
            view = "side"
        elif ratio < QUALITY["three_quarter_ratio"]:
            view = "three_quarter"
        else:
            view = "front"
        # facing away: the subject's right shoulder appears on the image right
        if view == "front" and float(np.median(tr.p("shoulder_r")[:, 0] - tr.p("shoulder_l")[:, 0])) > 0:
            view = "back"
        if view != "front":
            flags.append(f"unsupported view: {view} (the rig is a front view)")
        ys = tr.data[:, :, 1]
        subj = float(np.median(ys.max(1) - ys.min(1)))
        if subj < QUALITY["min_subject_px"]:
            flags.append(f"subject only {subj:.0f} px tall")
        return {"mean_visibility": round(mean_vis, 3), "occluded_frac": {k: round(v, 3) for k, v in occl.items()},
                "shoulder_torso_ratio": round(ratio, 3), "view": view, "subject_px": round(subj, 1),
                "flags": flags, "usable": view == "front" and mean_vis >= QUALITY["min_visibility"]}


__all__ = ["ReferenceMotionImporter", "ReferenceMedia", "ImportError_", "decode_video", "encode_video", "bvh_track",
           "parse_bvh", "ffmpeg_available", "QUALITY"]
