"""Standalone pose-preview engine (asset-preparation testing, NOT a Moho rig).

Components are rotated rigidly around Phase 1 joint pivots along a simple bone
hierarchy and composited in the occlusion-derived order. Diagnostics per pose
and joint: exposed gaps (pixels the expected part shapes should cover but the
posed artwork leaves transparent), contour breaks (extra dark-outline pieces),
overlap, clipping at the canvas border, and area distortion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from ..phase1 import masks as M

HEAD_GROUP = ["head_base", "hair", "nose", "mouth", "brow_r", "brow_l", "eye_r_white", "eye_r_iris",
              "eye_l_white", "eye_l_iris", "eye_r", "eye_l"]
RIG: dict[str, tuple[str | None, str | None, list[str]]] = {"body": (None, None, ["torso", "pelvis", "neck"]),
                                                             "head": ("neck", "body", HEAD_GROUP)}
for s in ("r", "l"):
    RIG.update({f"upper_arm_{s}": (f"shoulder_{s}", "body", [f"upper_arm_{s}"]),
                f"forearm_{s}": (f"elbow_{s}", f"upper_arm_{s}", [f"forearm_{s}"]),
                f"hand_{s}": (f"wrist_{s}", f"forearm_{s}", [f"hand_{s}"]),
                f"thigh_{s}": (f"hip_{s}", "body", [f"thigh_{s}"]),
                f"shin_{s}": (f"knee_{s}", f"thigh_{s}", [f"shin_{s}"]),
                f"foot_{s}": (f"ankle_{s}", f"shin_{s}", [f"foot_{s}"])})
JOINT_BONE = {pivot: bone for bone, (pivot, _, _) in RIG.items() if pivot}

# Angles in degrees, image coordinates (y down): positive = clockwise on screen.
# Character's right arm is on the image left, so raising it is clockwise.
POSES: dict[str, dict[str, float]] = {
    "neutral": {},
    "arm_45": {"upper_arm_r": 45},
    "arm_90": {"upper_arm_r": 90},
    "left_arm_90": {"upper_arm_l": -90},
    "elbows_bent": {"forearm_r": -70, "forearm_l": 70},
    "walking": {"thigh_r": 12, "shin_r": -10, "thigh_l": -12, "shin_l": 10, "upper_arm_r": -12, "upper_arm_l": 12},
    "sitting": {"thigh_r": 35, "shin_r": -50, "thigh_l": -35, "shin_l": 50},
    "head_tilt_left": {"head": -15},
    "head_tilt_right": {"head": 15},
}
POSE_NOTES = {"sitting": "front-view approximation of a sitting preparation (2D rotation cannot foreshorten)",
              "walking": "small front-view leg/arm swings"}


def bone_of(component: str) -> str | None:
    for b, (_, _, comps) in RIG.items():
        if component in comps:
            return b
    return None


def rot_about(p, deg) -> np.ndarray:
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    x, y = p
    return np.array([[c, -s, x - c * x + s * y], [s, c, y - s * x - c * y], [0, 0, 1.0]])


def world_transforms(pivots: dict[str, tuple[float, float]], angles: dict[str, float]) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}

    def get(b):
        if b in out:
            return out[b]
        pivot, parent, _ = RIG[b]
        T = np.eye(3) if parent is None else get(parent)
        if pivot and angles.get(b) and pivots.get(pivot) is not None:
            p = T @ np.array([*pivots[pivot], 1.0])
            T = rot_about((p[0], p[1]), angles[b]) @ T
        out[b] = T
        return T

    for b in RIG:
        get(b)
    return out


def apply(img: np.ndarray, T: np.ndarray, nearest=False) -> np.ndarray:
    if np.allclose(T, np.eye(3)):
        return img
    inv = np.linalg.inv(T)
    mode = "L" if img.ndim == 2 else "RGBA"
    pil = Image.fromarray(img.astype(np.uint8) if img.ndim == 3 else np.where(img, 255, 0).astype(np.uint8), mode)
    res = pil.transform(pil.size, Image.Transform.AFFINE, tuple(inv[:2].ravel()),
                        resample=Image.Resampling.NEAREST if nearest else Image.Resampling.BICUBIC)
    arr = np.asarray(res)
    return arr >= 128 if img.ndim == 2 else arr


@dataclass
class PoseResult:
    name: str
    image: np.ndarray
    diagnostics: dict[str, Any]
    gap_mask: np.ndarray


class PoseEngine:
    def __init__(self, layers: dict[str, np.ndarray], order: list[str], pivots: dict[str, tuple[float, float] | None],
                 expected: dict[str, np.ndarray | None], dark_of: dict[str, np.ndarray], radius: dict[str, float]):
        self.layers, self.order, self.pivots = layers, order, pivots
        self.expected, self.dark_of, self.radius = expected, dark_of, radius
        any_layer = next(iter(layers.values()))
        self.shape = any_layer.shape[:2]

    def render(self, name: str, angles: dict[str, float]) -> PoseResult:
        T = world_transforms(self.pivots, angles)
        canvas = Image.new("RGBA", (self.shape[1], self.shape[0]), (0, 0, 0, 0))
        posed_alpha: dict[str, np.ndarray] = {}
        clipping, distortion = {}, {}
        for cid in self.order:
            if cid not in self.layers:
                continue
            b = bone_of(cid)
            Tc = T.get(b, np.eye(3)) if b else np.eye(3)
            arr = apply(self.layers[cid], Tc)
            canvas.alpha_composite(Image.fromarray(arr, "RGBA"))
            a0, a1 = M.area(self.layers[cid][..., 3] > 127), M.area(arr[..., 3] > 127)
            posed_alpha[cid] = arr[..., 3] > 127
            if a0 and not np.allclose(Tc, np.eye(3)):
                # area lost at the canvas border vs resampling distortion
                pts = np.argwhere(self.layers[cid][..., 3] > 127)[:, ::-1]
                moved = (Tc[:2, :2] @ pts.T).T + Tc[:2, 2]
                outside = int(((moved[:, 0] < 0) | (moved[:, 0] >= self.shape[1]) | (moved[:, 1] < 0)
                               | (moved[:, 1] >= self.shape[0])).sum())
                if outside:
                    clipping[cid] = outside
                ratio = (a1 + outside) / a0
                if abs(ratio - 1) > 0.03:
                    distortion[cid] = round(ratio, 3)
        img = np.asarray(canvas)
        opaque = img[..., 3] > 127
        joints, gap_total = {}, np.zeros(self.shape, bool)
        for bone, ang in angles.items():
            if not ang or bone not in RIG:
                continue
            pivot_name, parent, _ = RIG[bone]
            p0 = self.pivots.get(pivot_name)
            if p0 is None:
                joints[pivot_name] = {"status": "pivot_missing"}
                continue
            Tp = T[parent] if parent else np.eye(3)
            p = Tp @ np.array([*p0, 1.0])
            R = 1.6 * self.radius.get(bone, 30.0)
            yy, xx = np.ogrid[:self.shape[0], :self.shape[1]]
            disc = (xx - p[0]) ** 2 + (yy - p[1]) ** 2 <= R * R
            exp = np.zeros(self.shape, bool)
            for cid, E in self.expected.items():
                if E is None or cid not in self.layers:
                    continue
                cb = bone_of(cid)
                if cb in (bone, parent):
                    exp |= apply(E, T.get(cb, np.eye(3)), nearest=True)
            gaps = disc & exp & ~opaque
            gap_total |= gaps
            dark = np.zeros(self.shape, bool)
            for cid, dm in self.dark_of.items():
                cb = bone_of(cid)
                dark |= apply(dm, T.get(cb, np.eye(3)), nearest=True) if cb else dm
            n_dark = len([c for c in M.components(dark & disc) if M.area(c) >= 6])
            child_ids = RIG[bone][2]
            ov = np.zeros(self.shape, np.int32)
            for cid in child_ids + (RIG[parent][2] if parent else []):
                if cid in posed_alpha:
                    ov += posed_alpha[cid] & disc
            joints[pivot_name] = {"angle_deg": ang, "gap_pixels": M.area(gaps), "disc_pixels": M.area(disc),
                                  "dark_contour_pieces": n_dark, "overlap_pixels": int((ov > 1).sum()),
                                  "pivot_posed": [round(float(p[0]), 1), round(float(p[1]), 1)]}
        diag = {"pose": name, "angles": angles, "note": POSE_NOTES.get(name), "joints": joints,
                "gap_pixels_total": M.area(gap_total), "clipping_pixels": clipping, "area_distortion": distortion}
        return PoseResult(name, img, diag, gap_total)


def diagnostic_image(res: PoseResult, crop=None) -> Image.Image:
    h, w = res.image.shape[:2]
    bg = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    d = ImageDraw.Draw(bg)
    for yy in range(0, h, 20):
        for xx in range(0, w, 20):
            if (xx + yy) // 20 % 2:
                d.rectangle((xx, yy, xx + 19, yy + 19), fill=(232, 232, 232, 255))
    bg.alpha_composite(Image.fromarray(res.image, "RGBA"))
    arr = np.asarray(bg).copy()
    arr[res.gap_mask] = (255, 0, 0, 255)
    img = Image.fromarray(arr, "RGBA").convert("RGB")
    d = ImageDraw.Draw(img)
    for j, info in res.diagnostics["joints"].items():
        if "pivot_posed" in info:
            x, y = info["pivot_posed"]
            d.ellipse((x - 7, y - 7, x + 7, y + 7), outline=(0, 120, 255), width=3)
    d.text((10, 10), f"{res.name}: gaps {res.diagnostics['gap_pixels_total']} px (red)", fill=(0, 0, 0))
    return img.crop(crop) if crop else img
