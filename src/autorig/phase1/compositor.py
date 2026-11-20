"""Neutral-pose compositor, fidelity metrics and preview images.

Layers carry original pixels, so a perfect segmentation recomposes exactly.
Differences therefore come from coverage: character pixels no layer owns,
layer pixels outside the character, or overlaps. Coverage is measured
against an INDEPENDENT foreground estimate (alpha channel, else a
background-colour heuristic) - not against the union of the layers, which
would be circular. Metrics are diagnostics; none of them alone proves artistic
quality (see the visual previews).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from . import masks as M

PALETTE = [(230, 25, 75), (60, 180, 75), (255, 225, 25), (0, 130, 200), (245, 130, 48), (145, 30, 180),
           (70, 240, 240), (240, 50, 230), (210, 245, 60), (250, 190, 212), (0, 128, 128), (220, 190, 255),
           (170, 110, 40), (255, 250, 200), (128, 0, 0), (170, 255, 195), (128, 128, 0), (255, 215, 180),
           (0, 0, 128), (128, 128, 128), (255, 255, 255)]


def estimate_foreground(rgba: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    alpha = rgba[..., 3]
    if (alpha < 255).any():
        return alpha > 0, {"method": "alpha_channel"}
    rgb = rgba[..., :3].astype(np.int16)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    bg = np.median(border, axis=0)
    diff = np.abs(rgb - bg).max(axis=2)
    fg = diff > 24
    fg = ndimage.binary_opening(fg, iterations=1)
    holes = ndimage.binary_fill_holes(fg) & ~fg
    lab, n = ndimage.label(holes)
    limit = 0.01 * fg.size
    for i in range(1, n + 1):  # small enclosed regions (eye whites, socks) belong to the character
        comp = lab == i
        if comp.sum() <= limit:
            fg |= comp
    return fg, {"method": "background_colour_heuristic", "background_rgb": [int(v) for v in bg],
                "threshold": 24, "note": "heuristic; unreliable on busy or gradient backgrounds"}


def absorb_fringe(layer_masks: dict[str, np.ndarray], foreground: np.ndarray, max_px: float = 2.0
                  ) -> tuple[dict[str, np.ndarray], int]:
    """Give character pixels that no layer owns but that lie within ``max_px``
    of a layer (typically the anti-aliased fringe of the linework, lost by
    binary mask thresholds) to the NEAREST layer. Colours stay original; the
    partition property (no overlaps) is preserved."""
    names = list(layer_masks)
    label = np.zeros(foreground.shape, dtype=np.int32)
    for i, n in enumerate(names, start=1):
        label[layer_masks[n]] = i
    union = label > 0
    if not union.any():
        return layer_masks, 0
    dist, (iy, ix) = ndimage.distance_transform_edt(~union, return_indices=True)
    fringe = foreground & ~union & (dist <= max_px)
    nearest = label[iy, ix]
    out = {}
    for i, n in enumerate(names, start=1):
        out[n] = layer_masks[n] | (fringe & (nearest == i))
    return out, int(fringe.sum())


def compose(layers: list[tuple[str, np.ndarray]], shape: tuple[int, int]) -> np.ndarray:
    """Alpha-composite full-canvas RGBA layers back-to-front (no scaling)."""
    canvas = Image.new("RGBA", (shape[1], shape[0]), (0, 0, 0, 0))
    for _, rgba in layers:
        canvas.alpha_composite(Image.fromarray(rgba, "RGBA"))
    return np.asarray(canvas)


def _ssim_gray(a: np.ndarray, b: np.ndarray) -> float:
    a, b = a.astype(np.float64), b.astype(np.float64)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    g = lambda x: ndimage.gaussian_filter(x, 1.5)
    mu_a, mu_b = g(a), g(b)
    va, vb, cov = g(a * a) - mu_a ** 2, g(b * b) - mu_b ** 2, g(a * b) - mu_a * mu_b
    s = ((2 * mu_a * mu_b + c1) * (2 * cov + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (va + vb + c2))
    return float(s.mean())


def fidelity(source: np.ndarray, recon: np.ndarray, foreground: np.ndarray,
             layer_masks: dict[str, np.ndarray], bg_rgb=(0, 0, 0)) -> dict[str, Any]:
    stack = np.zeros(foreground.shape, dtype=np.int32)
    for m in layer_masks.values():
        stack += m
    union = stack > 0
    fg_n = M.area(foreground)
    covered = M.area(foreground & union)
    over = recon.astype(np.float64)
    a = over[..., 3:4] / 255.0
    recon_rgb = over[..., :3] * a + np.asarray(bg_rgb, dtype=np.float64) * (1 - a)
    src_rgb = source[..., :3].astype(np.float64)
    err = np.abs(recon_rgb - src_rgb).max(axis=2)
    in_fg = err[foreground] if fg_n else np.zeros(1)
    in_union = err[union] if union.any() else np.zeros(1)
    mse = float(((recon_rgb - src_rgb)[foreground] ** 2).mean()) if fg_n else 0.0
    bb = M.bbox_xyxy(foreground)
    ssim = None
    if bb:
        x0, y0, x1, y1 = bb
        gray = lambda img: img[y0:y1, x0:x1] @ np.array([0.299, 0.587, 0.114])
        ssim = round(_ssim_gray(gray(recon_rgb), gray(src_rgb)), 4)
    lum = src_rgb @ np.array([0.299, 0.587, 0.114])
    grad = np.hypot(ndimage.sobel(lum, 0), ndimage.sobel(lum, 1))
    edges = foreground & (grad > 120)
    alpha_ok = None
    if (source[..., 3] < 255).any():
        alpha_ok = bool(np.array_equal(recon[..., 3][union], source[..., 3][union]))
    return {
        "foreground_pixels": fg_n,
        "layer_union_pixels": M.area(union),
        "covered_foreground_pixels": covered,
        "coverage_ratio": round(covered / fg_n, 5) if fg_n else None,
        "uncovered_foreground_pixels": fg_n - covered,
        "layer_pixels_outside_foreground": M.area(union & ~foreground),
        "overlap_pixels": int((stack > 1).sum()),
        "pixels_differing_inside_layers": int((in_union > 0).sum()),
        "max_abs_error_inside_layers": float(in_union.max()),
        "mean_abs_error_foreground": round(float(in_fg.mean()), 4),
        "max_abs_error_foreground": float(in_fg.max()),
        "psnr_foreground_db": None if mse == 0 else round(10 * math.log10(255 ** 2 / mse), 3),
        "ssim_gray_foreground_bbox": ssim,
        "linework_pixels": M.area(edges),
        "linework_retained_ratio": round(M.area(edges & union) / M.area(edges), 5) if M.area(edges) else None,
        "alpha_preserved_inside_layers": alpha_ok,
        "note": "Pixels are copied, never resampled: inside the layers the error must be exactly 0. "
                "Foreground errors come from uncovered pixels. Metrics are diagnostics, not proof of quality.",
    }


def difference_image(source: np.ndarray, recon: np.ndarray, foreground: np.ndarray, union: np.ndarray,
                     bg_rgb=(0, 0, 0)) -> Image.Image:
    a = recon[..., 3:4].astype(np.float64) / 255.0
    recon_rgb = recon[..., :3] * a + np.asarray(bg_rgb) * (1 - a)
    err = np.abs(recon_rgb - source[..., :3].astype(np.float64)).max(axis=2) / 255.0
    lum = source[..., :3] @ np.array([0.299, 0.587, 0.114])
    out = np.stack([lum * 0.25] * 3, axis=-1)
    heat = np.stack([np.clip(err * 3, 0, 1), np.clip(err * 3 - 1, 0, 1), np.zeros_like(err)], -1) * 255
    out = np.where((err > 0)[..., None], heat, out)
    out[foreground & ~union] = (255, 0, 255)  # character pixels no layer owns
    out[union & ~foreground] = (0, 200, 255)  # layer pixels outside the estimated character
    return Image.fromarray(out.astype(np.uint8), "RGB")


def overlay(source: np.ndarray, named_masks: dict[str, np.ndarray], *, alpha=0.55,
            points: dict[str, tuple[float, float]] | None = None, lines=None, crop=None,
            scale: float = 1.0) -> Image.Image:
    base = source[..., :3].astype(np.float64).copy()
    for i, (_, m) in enumerate(named_masks.items()):
        col = np.asarray(PALETTE[i % len(PALETTE)], dtype=np.float64)
        base[m] = base[m] * (1 - alpha) + col * alpha
        edge = m & ~M.erode(m, 1)
        base[edge] = col * 0.6
    img = Image.fromarray(base.astype(np.uint8), "RGB")
    d = ImageDraw.Draw(img)
    for a, b in lines or []:
        d.line((a, b), fill=(255, 255, 255), width=5)
        d.line((a, b), fill=(20, 20, 20), width=2)
    for name, (x, y) in (points or {}).items():
        d.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(255, 255, 255), outline=(0, 0, 0), width=2)
    if crop:
        img = img.crop(crop)
    if scale != 1.0:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.Resampling.LANCZOS)
    legend_h = 18 * ((len(named_masks) + 3) // 4) + 8
    sheet = Image.new("RGB", (img.width, img.height + legend_h), (255, 255, 255))
    sheet.paste(img, (0, 0))
    d = ImageDraw.Draw(sheet)
    col_w = max(1, img.width // 4)
    for i, name in enumerate(named_masks):
        x, y = (i % 4) * col_w + 6, img.height + 4 + 18 * (i // 4)
        d.rectangle((x, y + 2, x + 12, y + 14), fill=PALETTE[i % len(PALETTE)])
        d.text((x + 16, y + 2), name, fill=(0, 0, 0))
    return sheet


def layer_sheet(layers: list[tuple[str, np.ndarray]], tile: int = 180) -> Image.Image:
    cols = 6
    rows = max(1, math.ceil(len(layers) / cols))
    sheet = Image.new("RGB", (cols * tile, rows * (tile + 16)), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    checker = Image.new("RGB", (tile, tile))
    cd = ImageDraw.Draw(checker)
    for yy in range(0, tile, 12):
        for xx in range(0, tile, 12):
            cd.rectangle((xx, yy, xx + 11, yy + 11), fill=(205, 205, 205) if (xx + yy) // 12 % 2 else (245, 245, 245))
    for i, (name, rgba) in enumerate(layers):
        x, y = (i % cols) * tile, (i // cols) * (tile + 16)
        sheet.paste(checker, (x, y))
        bb = M.bbox_xyxy(rgba[..., 3] > 0)
        if bb:
            im = Image.fromarray(rgba, "RGBA").crop(bb)
            im.thumbnail((tile - 8, tile - 8), Image.Resampling.LANCZOS)
            tmp = checker.copy()
            tmp.paste(im, ((tile - im.width) // 2, (tile - im.height) // 2), im)
            sheet.paste(tmp, (x, y))
        else:
            d.text((x + 8, y + tile // 2), "EMPTY", fill=(200, 0, 0))
        d.text((x + 4, y + tile + 2), name, fill=(0, 0, 0))
    return sheet
