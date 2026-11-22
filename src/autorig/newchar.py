"""Phase 3.8: new-character import diagnostics and background-extraction QA.

* ``image_import_report(path)`` records what an input image actually is: dimensions, colour mode, whether a nominal
  alpha channel holds *genuine* transparency (any alpha < 255) or is opaque, the background (border statistics,
  uniformity, polarity), the drawn content's bounding box, the file hash and the coordinate system. Nothing is
  scaled: AutoRig works at the source's pixel size.
* ``background_extraction_qa(package)`` checks the background-removed character, i.e. the neutral composite of the
  package's original layers (Phase 1/2 pixels, copied, never resampled). The reference "character" is found
  spatially, not by colour alone: the paper is the paper-coloured region *connected to the image border*, so white
  clothing, white shoes and eye whites enclosed by outlines are character even though their colour matches the
  paper. The composite is shown over white, black, mid-grey and a checkerboard, and measured for:

  - ``missing_px``: character pixels (not border-connected paper) that no layer covers, split into clearly drawn
    pixels and light enclosed pixels (which may be background seen through a gap);
  - ``background_included_px``: layer pixels that are border-connected paper (background kept as character);
  - ``light_fringe_px``: silhouette pixels that are light and lighter than the pixels just inside them (the white
    paper blended into the antialiased outline: a halo on dark backgrounds);
  - ``clipped_outline_px``: dark source pixels within 3 px outside the silhouette (outline cut off by the mask).

  Metrics are diagnostics: the four renders must still be looked at (``visual_inspection_required`` is True).
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

PAPER_TOL = 24  # max channel difference from the border median for a pixel to be paper-coloured
LIGHT_LUM = 150.0  # a silhouette pixel brighter than this, and LIGHT_STEP brighter than the pixels inside it, is fringe
LIGHT_STEP = 40.0
DARK_LUM = 60.0  # source pixels darker than this are outline


def _lum(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


def image_import_report(path: str | Path, role: str = "character") -> dict[str, Any]:
    """What the image is (see the module doc). ``role`` is only recorded."""
    path = Path(path)
    data = path.read_bytes()
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        fmt, mode, size, info = im.format, im.mode, im.size, dict(im.info)
        has_alpha_band = "A" in im.getbands() or "transparency" in info
        rgba = np.asarray(im.convert("RGBA"))
    a = rgba[..., 3]
    genuine_transparency = bool((a < 255).any())
    rgb = rgba[..., :3]
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]]).astype(np.int16)
    med = np.median(border, 0)
    dev = np.abs(border - med).max(1)
    blum = float(_lum(med[None].astype(np.float32))[0])
    diff = np.abs(rgb.astype(np.int16) - med).max(-1)
    paper = diff <= PAPER_TOL
    lab, _ = ndimage.label(paper)
    edge_labels = set(np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]])) - {0}
    bg = np.isin(lab, sorted(edge_labels))
    content = ~bg
    ys, xs = np.nonzero(content)
    bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if len(xs) else None
    return {
        "schema": "autorig.image_import_report", "schema_version": 1, "role": role, "file": path.name,
        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "format": fmt, "mode": mode,
        "width": size[0], "height": size[1],
        "jpeg": {"progressive": bool(info.get("progressive") or info.get("progression")),
                 "lossy": True} if fmt == "JPEG" else None,
        "alpha": {"has_alpha_band": has_alpha_band, "genuine_transparency": genuine_transparency,
                  "transparent_px": int((a == 0).sum()), "partial_px": int(((a > 0) & (a < 255)).sum()),
                  "note": "nominal RGBA is not treated as transparent unless some alpha < 255"},
        "background": {"border_median_rgb": [int(v) for v in med], "border_luminance": round(blum, 1),
                       "polarity": "dark" if blum < 60 else "light",
                       "border_max_deviation_p95": int(np.percentile(dev, 95)),
                       "uniform": bool(np.percentile(dev, 95) <= PAPER_TOL),
                       "opaque": not genuine_transparency,
                       "border_connected_background_px": int(bg.sum()),
                       "background_ratio": round(float(bg.mean()), 4),
                       "rule": f"pixels within {PAPER_TOL} of the border median and connected to the border"},
        "content_bbox_xyxy": bbox,
        "content_px": int(content.sum()),
        "scaling": {"applied": False, "rule": "assets keep the source's pixel size (no resampling)"},
        "coordinate_system": {"origin": "top-left", "x": "right", "y": "down", "units": "source pixels"},
    }


def _layers(package: Path) -> dict[str, np.ndarray]:
    """The original (category A) layers in z order."""
    man = json.loads((package / "artwork" / "manifest.json").read_text(encoding="utf-8"))
    return {L["id"]: np.asarray(Image.open(package / L["image"]).convert("RGBA"))
            for L in sorted(man["layers"], key=lambda r: r["z_index"]) if L.get("category") == "A_original"}


def _stack(layers: dict[str, np.ndarray]) -> np.ndarray:
    first = next(iter(layers.values()))
    canvas = Image.new("RGBA", (first.shape[1], first.shape[0]), (0, 0, 0, 0))
    for rgba in layers.values():
        canvas.alpha_composite(Image.fromarray(rgba, "RGBA"))
    return np.asarray(canvas)


def _composite(package: Path) -> tuple[np.ndarray, list[str]]:
    layers = _layers(package)
    return _stack(layers), list(layers)


def _fringe(comp: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Light silhouette pixels (see the module doc), measured on the composite's own colours; returns
    (fringe mask, silhouette mask)."""
    alpha = comp[..., 3]
    vis = alpha > 0
    lum = _lum(comp)
    bd = vis & ~ndimage.binary_erosion(vis)
    inner = ndimage.binary_erosion(vis, iterations=2) & ~ndimage.binary_erosion(vis, iterations=4)
    num = ndimage.uniform_filter(np.where(inner, lum, 0.0), 7)
    den = ndimage.uniform_filter(inner.astype(np.float32), 7)
    inside = np.where(den > 0, num / np.maximum(den, 1e-6), 255.0)
    shown = lum * (alpha / 255.0)  # over black: a semi-transparent edge pixel shows darker
    return bd & (shown > LIGHT_LUM) & (shown - inside > LIGHT_STEP), bd


def _checker(w: int, h: int, s: int = 12) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    c = np.where(((yy // s) + (xx // s)) % 2 == 0, 236, 200).astype(np.uint8)
    return np.stack([c, c, c, np.full_like(c, 255)], -1)


def over(rgba: np.ndarray, bg: tuple[int, int, int] | str) -> Image.Image:
    h, w = rgba.shape[:2]
    base = Image.fromarray(_checker(w, h)) if bg == "checker" else Image.new("RGBA", (w, h), (*bg, 255))
    base.alpha_composite(Image.fromarray(rgba, "RGBA"))
    return base.convert("RGB")


def background_extraction_qa(package: str | Path, out_dir: str | Path | None = None) -> dict[str, Any]:
    """Measure and render the background-removed character (see the module doc). Writes ``backgrounds.png`` and
    ``edge_closeups.png`` into ``out_dir`` when given."""
    package = Path(package)
    src = np.asarray(Image.open(package / "source" / "original.png").convert("RGB"))
    from .garmentrig.correctives import EdgeDecontamination

    layers = _layers(package)
    names = list(layers)
    comp = _stack(layers)
    decon_layers, decon_info = EdgeDecontamination(width=2).apply(layers, np.asarray(
        Image.open(package / "source" / "original.png").convert("RGBA")))
    decon = _stack(decon_layers)
    alpha = comp[..., 3]
    vis = alpha > 0
    rgb16 = src.astype(np.int16)
    border = np.concatenate([rgb16[0], rgb16[-1], rgb16[:, 0], rgb16[:, -1]])
    med = np.median(border, 0)
    paper = np.abs(rgb16 - med).max(-1) <= PAPER_TOL
    lab, _ = ndimage.label(paper)
    edge_labels = set(np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]])) - {0}
    bg = np.isin(lab, sorted(edge_labels))
    character = ~bg
    lum = _lum(src)
    missing = character & ~vis
    enclosed_light = paper & ~bg  # paper-coloured but enclosed: shirt, shoes, eye whites, or background seen in a gap
    elab, en = ndimage.label(enclosed_light)
    regions = []
    for i, sl in enumerate(ndimage.find_objects(elab), start=1):
        m = elab[sl] == i
        a = int(m.sum())
        if a < 30:
            continue
        cov = float((vis[sl] & m).sum()) / a
        regions.append({"bbox_xyxy": [int(sl[1].start), int(sl[0].start), int(sl[1].stop), int(sl[0].stop)],
                        "px": a, "covered_by_layers": round(cov, 3)})
    regions.sort(key=lambda r: -r["px"])
    fringe, bd = _fringe(comp)
    fringe_d, _ = _fringe(decon)
    near_out = ndimage.binary_dilation(vis, iterations=3) & ~vis
    clipped = near_out & (lum < DARK_LUM)
    res = {
        "schema": "autorig.background_extraction_qa", "schema_version": 1, "layers": names,
        "method": "neutral composite of the original layers (Phase 1 SAM 3 masks, Phase 2 store) vs the spatial "
                  "character mask (everything not border-connected paper)",
        "paper_rgb": [int(v) for v in med],
        "character_px": int(character.sum()), "visible_px": int(vis.sum()),
        "missing_px": int(missing.sum()),
        "missing_drawn_px": int((missing & ~paper).sum()),
        "missing_enclosed_light_px": int((missing & paper).sum()),
        "background_included_px": int((vis & bg).sum()),
        "silhouette_px": int(bd.sum()),
        "light_fringe_px": int(fringe.sum()),
        "light_fringe_ratio": round(float(fringe.sum()) / max(int(bd.sum()), 1), 4),
        "decontaminated": {"tool": "garmentrig.correctives.EdgeDecontamination (derived copy; originals untouched)",
                           **decon_info, "light_fringe_px": int(fringe_d.sum()),
                           "light_fringe_ratio": round(float(fringe_d.sum()) / max(int(bd.sum()), 1), 4)},
        "clipped_outline_px": int(clipped.sum()),
        "enclosed_light_regions": regions[:12],
        "alpha_values": sorted({int(v) for v in np.unique(alpha)})[:6],
        "visual_inspection_required": True,
    }
    if out_dir is not None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        ys, xs = np.nonzero(vis)
        x0, y0, x1, y1 = max(0, xs.min() - 12), max(0, ys.min() - 12), xs.max() + 13, ys.max() + 13
        crop = decon[y0:y1, x0:x1]
        tiles = [("white", over(crop, (255, 255, 255))), ("black", over(crop, (0, 0, 0))),
                 ("mid-grey", over(crop, (128, 128, 128))), ("checkerboard", over(crop, "checker"))]
        k = 560 / crop.shape[0]
        font = ImageFont.load_default()
        tw = int(crop.shape[1] * k)
        sheet = Image.new("RGB", (4 * (tw + 10) + 10, 600), (245, 245, 248))
        d = ImageDraw.Draw(sheet)
        for i, (lbl, im) in enumerate(tiles):
            sheet.paste(im.resize((tw, 560), Image.Resampling.LANCZOS), (10 + i * (tw + 10), 32))
            d.text((10 + i * (tw + 10), 12), lbl, fill=(20, 20, 30), font=font)
        d.text((10, 588), "derived layers with edge decontamination (originals untouched)", fill=(80, 80, 90), font=font)
        sheet.save(out / "backgrounds.png")
        # full-resolution close-ups on black (halo) and white (clipping) at 3x: hair tips, hand, shoe
        boxes = _closeup_boxes(vis)
        cl = []
        for name, (bx0, by0, bx1, by1) in boxes:
            w3, h3 = (bx1 - bx0) * 3, (by1 - by0) * 3
            pair = Image.new("RGB", (3 * w3 + 16, h3 + 18), (245, 245, 248))
            views = [over(comp[by0:by1, bx0:bx1], (0, 0, 0)), over(decon[by0:by1, bx0:bx1], (0, 0, 0)),
                     over(decon[by0:by1, bx0:bx1], "checker")]
            for j, v in enumerate(views):
                pair.paste(v.resize((w3, h3), Image.Resampling.NEAREST), (j * (w3 + 8), 18))
            ImageDraw.Draw(pair).text((2, 3), f"{name} {bx0},{by0}-{bx1},{by1} (3x: original layers on black | "
                                              "decontaminated on black | decontaminated on checkerboard)",
                                      fill=(20, 20, 30), font=font)
            cl.append(pair)
        W = max(p.width for p in cl)
        H = sum(p.height + 6 for p in cl)
        sheet2 = Image.new("RGB", (W, H), (245, 245, 248))
        y = 0
        for p in cl:
            sheet2.paste(p, (0, y))
            y += p.height + 6
        sheet2.save(out / "edge_closeups.png")
        res["images"] = ["backgrounds.png", "edge_closeups.png"]
    return res


def _closeup_boxes(vis: np.ndarray, size: int = 110) -> list[tuple[str, tuple[int, int, int, int]]]:
    """Three silhouette regions picked from the mask itself: the top (hair), the leftmost point (a hand on a T-pose)
    and the bottom-left (a shoe)."""
    ys, xs = np.nonzero(vis)
    H, W = vis.shape

    def box(cx: float, cy: float) -> tuple[int, int, int, int]:
        x0 = int(min(max(cx - size / 2, 0), W - size))
        y0 = int(min(max(cy - size / 2, 0), H - size))
        return x0, y0, x0 + size, y0 + size

    top = int(np.argmin(ys))
    left = int(np.argmin(xs))
    bl = int(np.argmax(ys - 0.3 * xs))
    return [("top", box(xs[top], ys[top] + size * 0.35)), ("leftmost", box(xs[left] + size * 0.35, ys[left])),
            ("bottom-left", box(xs[bl] + size * 0.2, ys[bl] - size * 0.35))]
