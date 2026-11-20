"""Mask utilities. Masks are full-canvas boolean numpy arrays (H, W) in the
original image coordinate system; nothing is ever rescaled per part."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage


def empty_like(shape: tuple[int, int]) -> np.ndarray:
    return np.zeros(shape, dtype=bool)


def area(m: np.ndarray) -> int:
    return int(np.count_nonzero(m))


def bbox_xyxy(m: np.ndarray) -> list[int] | None:
    """Inclusive-exclusive pixel bbox [x0, y0, x1, y1], or None for an empty mask."""
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]


def centroid(m: np.ndarray) -> tuple[float, float] | None:
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return None
    return (float(xs.mean()), float(ys.mean()))


def iou(a: np.ndarray, b: np.ndarray) -> float:
    union = np.count_nonzero(a | b)
    return float(np.count_nonzero(a & b) / union) if union else 0.0


def dice(a: np.ndarray, b: np.ndarray) -> float:
    s = np.count_nonzero(a) + np.count_nonzero(b)
    return float(2 * np.count_nonzero(a & b) / s) if s else 1.0


def dilate(m: np.ndarray, px: int) -> np.ndarray:
    if px <= 0:
        return m.copy()
    return ndimage.binary_dilation(m, structure=ndimage.generate_binary_structure(2, 1), iterations=px)


def erode(m: np.ndarray, px: int) -> np.ndarray:
    if px <= 0:
        return m.copy()
    return ndimage.binary_erosion(m, structure=ndimage.generate_binary_structure(2, 1), iterations=px)


def components(m: np.ndarray) -> list[np.ndarray]:
    lab, n = ndimage.label(m, structure=np.ones((3, 3), dtype=bool))
    return [lab == i for i in range(1, n + 1)]


def largest_component(m: np.ndarray) -> np.ndarray:
    comps = components(m)
    return max(comps, key=area) if comps else m.copy()


def contact_zone(a: np.ndarray, b: np.ndarray, px: int = 3) -> np.ndarray:
    """Pixels of ``a`` within ``px`` of ``b`` (where two parts touch)."""
    return a & dilate(b, px)


def fraction_inside(m: np.ndarray, box: list[int] | tuple[int, ...]) -> float:
    a = area(m)
    if not a:
        return 0.0
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    return float(np.count_nonzero(m[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]) / a)


def touches_border(m: np.ndarray) -> bool:
    return bool(m[0].any() or m[-1].any() or m[:, 0].any() or m[:, -1].any())


def save_mask(m: np.ndarray, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.where(m, 255, 0).astype(np.uint8), mode="L").save(path, optimize=True)


def load_mask(path: str | Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("L")) >= 128


def extract_layer(source_rgba: np.ndarray, m: np.ndarray) -> np.ndarray:
    """Full-canvas RGBA with the ORIGINAL pixel values inside the mask and fully
    transparent elsewhere. Colours are copied, never resampled or re-drawn."""
    out = np.zeros_like(source_rgba)
    out[m] = source_rgba[m]
    return out


def resize_mask_nearest(m: np.ndarray, size_wh: tuple[int, int]) -> np.ndarray:
    im = Image.fromarray(np.where(m, 255, 0).astype(np.uint8), mode="L")
    return np.asarray(im.resize(size_wh, Image.Resampling.NEAREST)) >= 128


def resize_mask_area(m: np.ndarray, size_wh: tuple[int, int]) -> np.ndarray:
    """Downscale with area averaging and a 50% coverage threshold."""
    im = Image.fromarray(np.where(m, 255, 0).astype(np.uint8), mode="L")
    return np.asarray(im.resize(size_wh, Image.Resampling.BOX)) >= 128
