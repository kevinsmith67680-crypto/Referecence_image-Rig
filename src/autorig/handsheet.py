"""Hand-pose sheet extraction: a sheet of sleeve-free hand drawings -> a validated hand pack.

The sheet is a grid of hand drawings on a white background, rows grouped by side (for the schoolboy sheet: rows 1-2
one side, rows 3-4 the other; per group the gestures relaxed, open palm, fist, pointing / thumbs up, gripping,
palm down, cupped). The sheet's labels are *checked against the artwork*, not trusted:

* order: exactly one drawing per grid cell, in reading order;
* handedness: a gesture's two drawings must mirror each other (silhouette and interior-line IoU, mirrored vs not);
  the open palm's thumb side (measured) decides which row group is the anatomical left hand; per-drawing thumb
  evidence from the inspection record is checked with the same rule (right hand <=> cross(fingers, thumb) > 0 in
  image coordinates for a palm view, < 0 for a back view). A pair that is *not* mirrored is reported: both drawings
  are the same hand, and the side that lacks a drawing gets a horizontally mirrored copy (derived, flagged);
* wrist opening: a straight wrist cut near the inspected side (``open_cut``); a hand drawn with a closed outline
  there gets a chord at that side's wrist width (``closed_outline``, low confidence, manual pivot review).

Pixels: the background is near-white, low-saturation pixels connected to the sheet border (plus enclosed
near-white gaps), so dark outlines can never be taken for background. Antialiased edges are unmatted against white
(alpha from luminance, colour = the outline colour). No scaling and no rotation: each sprite is a tight crop of the
sheet (plus a small transparent margin) and keeps the sheet's pixel size.

The inspection record (``inspection.json``) is the assistant's visual reading of the sheet (gesture, wrist side,
view, thumb direction where unambiguous). It is input data with its own provenance; every value it gives is
cross-checked by a measurement where one exists.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage
from scipy.spatial import ConvexHull

BG_LUM, BG_SAT = 242.0, 25.0  # near-white, low-saturation pixels may be background (never dark outlines)
HOLE_MIN_PX = 40  # enclosed near-white regions at least this large are gaps (background), not highlights
MIN_HAND_PX = 2000
MARGIN = 4  # transparent margin around each cropped sprite (px)
OPEN_CUT_MIN = 0.22  # a straight wrist run >= this x sqrt(area) is an open wrist cut
SEARCH_DEG = 40  # the wrist detector searches within this angle of the inspected side
SIDE_DEG = {"top": 270.0, "bottom": 90.0, "left": 180.0, "right": 0.0, "top_left": 225.0, "top_right": 315.0}
THUMB_VEC = {"top": (0.0, -1.0), "bottom": (0.0, 1.0), "left": (-1.0, 0.0), "right": (1.0, 0.0)}
VIEW_SIGN = {"palm": 1, "back": -1}
GESTURES = ("relaxed", "open_palm", "fist", "pointing", "thumbs_up", "gripping", "palm_down", "cupped")
PACK_POSE = {"relaxed": "relaxed", "open_palm": "open_palm", "fist": "fist", "pointing": "point",
             "thumbs_up": "thumbs_up", "gripping": "grip", "palm_down": "palm_down", "cupped": "cupped"}
QUALITY = {"outline_coverage_min": 0.95, "skin_on_edge_max_px": 12}


def _lum(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# -- background and cells ---------------------------------------------------------------------------------
def background(rgb: np.ndarray) -> tuple[np.ndarray, dict[str, int]]:
    """Background mask: near-white low-saturation pixels connected to the border, plus enclosed near-white gaps of
    at least HOLE_MIN_PX (white seen between fingers). Dark pixels never qualify."""
    lum = _lum(rgb)
    sat = rgb.max(-1).astype(np.int16) - rgb.min(-1).astype(np.int16)
    cand = (lum > BG_LUM) & (sat < BG_SAT)
    lab, n = ndimage.label(cand)
    border = set(np.unique(np.r_[lab[0], lab[-1], lab[:, 0], lab[:, -1]])) - {0}
    bg = np.isin(lab, sorted(border))
    holes = 0
    sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
    for i in range(1, n + 1):
        if i not in border and sizes[i - 1] >= HOLE_MIN_PX:
            bg |= lab == i
            holes += int(sizes[i - 1])
    return bg, {"enclosed_gap_px": holes}


def grid_cells(fg: np.ndarray, rows: int, cols: int) -> tuple[list[dict[str, Any]], list[str]]:
    """The drawings in reading order, one per cell. Errors when a cell is empty, shared or holds two drawings."""
    lab, n = ndimage.label(fg)
    objs = ndimage.find_objects(lab)
    sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
    big = [i for i in range(n) if sizes[i] >= MIN_HAND_PX]
    H, W = fg.shape
    errs: list[str] = []
    cells: dict[tuple[int, int], list[int]] = {}
    for i in big:
        cy, cx = ndimage.center_of_mass(lab == i + 1)
        r, c = min(rows - 1, int(cy / (H / rows))), min(cols - 1, int(cx / (W / cols)))
        cells.setdefault((r, c), []).append(i)
    out = []
    for r in range(rows):
        for c in range(cols):
            ids = cells.get((r, c), [])
            name = f"r{r + 1}c{c + 1}"
            if len(ids) != 1:
                errs.append(f"cell {name}: {len(ids)} drawings (one expected)")
                continue
            i = ids[0]
            sl = objs[i]
            out.append({"cell": name, "row": r + 1, "col": c + 1, "label": i + 1,
                        "bbox": [int(sl[1].start), int(sl[0].start), int(sl[1].stop), int(sl[0].stop)],
                        "area_px": int(sizes[i]),
                        "touches_sheet_edge": bool(sl[0].start == 0 or sl[1].start == 0 or sl[0].stop == H or sl[1].stop == W)})
    stray = [int(sizes[i]) for i in range(n) if sizes[i] < MIN_HAND_PX]
    return out, errs + ([] if len(big) == rows * cols else [f"{len(big)} drawings found, {rows * cols} expected"]) + \
        ([] if not stray else [])  # stray specks are reported per sprite, not as errors


# -- per-drawing pixels -----------------------------------------------------------------------------------
def unmatte(rgb: np.ndarray, comp: np.ndarray, bg: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    """RGBA of one drawing (same size as ``rgb``). Interior pixels keep their colour (alpha 255). Pixels on the
    drawing's edge (touching the background) and the 1 px background ring around it are unmatted against white:
    alpha = (255 - lum) / (255 - lum_outline), colour = the outline colour (every edge of these drawings is its
    dark outline). Ring pixels below alpha 0.06 stay transparent (JPEG noise)."""
    lum = _lum(rgb)
    dark = comp & (lum < 60)
    F = np.median(rgb[dark], 0) if dark.sum() >= 20 else np.array([20.0, 16.0, 12.0])
    lf = float(F @ np.array([0.299, 0.587, 0.114]))
    edge = comp & ndimage.binary_dilation(bg, iterations=1)
    ring = bg & ndimage.binary_dilation(comp, iterations=1)
    a = np.zeros(lum.shape, np.float32)
    a[comp] = 1.0
    zone = edge | ring
    est = np.clip((255.0 - lum) / max(255.0 - lf, 1.0), 0.0, 1.0)
    a[zone] = est[zone]
    a[ring & (a < 0.06)] = 0.0
    out = np.zeros(rgb.shape[:2] + (4,), np.uint8)
    out[..., :3] = rgb[..., :3]
    out[zone, :3] = np.round(F).astype(np.uint8)
    out[zone & (lum < lf), :3] = rgb[zone & (lum < lf), :3]  # darker than the outline: keep as drawn
    out[zone & (lum < lf), 3] = 255
    out[..., 3] = np.where(zone & (lum < lf), 255, np.round(a * 255)).astype(np.uint8)
    # quality: the visible boundary must be dark outline all around (a cut-off finger shows skin on the edge)
    vis = out[..., 3] >= 128
    bd = vis & ~ndimage.binary_erosion(vis)
    bd_band = ndimage.binary_dilation(bd, iterations=2) & vis
    rgb16 = rgb.astype(np.int16)
    skin = (rgb16[..., 0] - rgb16[..., 2] > 60) & (lum > 90)
    # an edge pixel is outlined when dark outline pixels are within 2 px of it
    outlined = ndimage.binary_dilation(lum < 110, iterations=2)
    cov = float((bd & outlined).sum()) / max(int(bd.sum()), 1)
    info = {"outline_rgb": [round(float(v), 1) for v in F], "outline_coverage": round(cov, 4),
            "skin_on_edge_px": int((bd & ~outlined & skin).sum()), "edge_px_unmatted": int(zone.sum()),
            "ring_px_with_alpha": int((ring & (a > 0)).sum()), "dark_px": int(dark.sum()),
            "dark_px_kept": int((dark & (out[..., 3] == 255)).sum()),
            "dark_px_lost": int((dark & (out[..., 3] < 128)).sum()), "edge_band_px": int(bd_band.sum())}
    return out, info


def crop(rgba: np.ndarray, margin: int = MARGIN) -> tuple[np.ndarray, list[int]]:
    ys, xs = np.nonzero(rgba[..., 3] > 0)
    x0, y0 = max(0, xs.min() - margin), max(0, ys.min() - margin)
    x1, y1 = min(rgba.shape[1], xs.max() + 1 + margin), min(rgba.shape[0], ys.max() + 1 + margin)
    out = np.zeros((y1 - y0 + 2 * margin, x1 - x0 + 2 * margin, 4), np.uint8)  # margin guaranteed on all sides
    out[margin:margin + y1 - y0, margin:margin + x1 - x0] = rgba[y0:y1, x0:x1]
    sub = out[..., 3] > 0
    ys2, xs2 = np.nonzero(sub)
    t0, l0 = max(0, ys2.min() - margin), max(0, xs2.min() - margin)
    out = out[t0:ys2.max() + 1 + margin, l0:xs2.max() + 1 + margin]
    return out, [int(x0 - margin + l0), int(y0 - margin + t0)]


# -- geometry ------------------------------------------------------------------------------------------------
def _boundary(mask: np.ndarray) -> np.ndarray:
    b = mask & ~ndimage.binary_erosion(mask)
    ys, xs = np.nonzero(b)
    return np.stack([xs, ys], 1).astype(float)


def _runs(a: np.ndarray, gap: float = 3.0) -> tuple[float, float, float]:
    """Longest contiguous run of sorted 1-D positions (gaps <= ``gap``): (length, start, end)."""
    if len(a) == 0:
        return 0.0, 0.0, 0.0
    a = np.sort(a)
    best, s = (0.0, a[0], a[0]), a[0]
    for i in range(1, len(a) + 1):
        if i == len(a) or a[i] - a[i - 1] > gap:
            L = a[i - 1] - s
            if L > best[0]:
                best = (float(L), float(s), float(a[i - 1]))
            if i < len(a):
                s = a[i]
    return best


def _chord(mask: np.ndarray, origin: np.ndarray, t: np.ndarray, half: float) -> tuple[float, float, float]:
    """Longest run of mask pixels on the line origin + s*t, |s| <= half (0.5 px steps): (length, s0, s1)."""
    ss = np.arange(-half, half, 0.5)
    pts = origin[None, :] + ss[:, None] * t[None, :]
    xi, yi = np.rint(pts[:, 0]).astype(int), np.rint(pts[:, 1]).astype(int)
    ok = (xi >= 0) & (yi >= 0) & (xi < mask.shape[1]) & (yi < mask.shape[0])
    hit = np.zeros(len(ss), bool)
    hit[ok] = mask[yi[ok], xi[ok]]
    L, s0, s1 = _runs(ss[hit], 0.75)
    return L, s0, s1


DIRS8 = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]  # clockwise on screen


def trace_contour(mask: np.ndarray) -> np.ndarray:
    """Outer contour of the largest component (holes filled), ordered clockwise on screen (Moore-neighbour
    tracing from the top-left pixel). Returns (N, 2) pixel coordinates (x, y)."""
    lab, n = ndimage.label(mask)
    if n == 0:
        return np.zeros((0, 2))
    k = 1 + int(np.argmax(ndimage.sum(mask, lab, range(1, n + 1))))
    m = ndimage.binary_fill_holes(lab == k)
    H, W = m.shape
    ys, xs = np.nonzero(m)
    i = np.lexsort((xs, ys))[0]
    start = (int(xs[i]), int(ys[i]))
    cur, b = start, 4  # backtrack: the pixel to the west is background
    pts = [start]
    for _ in range(8 * int(m.sum()) + 16):
        for k8 in range(1, 9):
            d = (b + k8) % 8
            nx, ny = cur[0] + DIRS8[d][0], cur[1] + DIRS8[d][1]
            if 0 <= nx < W and 0 <= ny < H and m[ny, nx]:
                pd = (b + k8 - 1) % 8
                px, py = cur[0] + DIRS8[pd][0], cur[1] + DIRS8[pd][1]
                b = DIRS8.index((px - nx, py - ny))
                cur = (nx, ny)
                break
        else:
            break
        if cur == start:
            break
        pts.append(cur)
    return np.asarray(pts, float)


CAP_DEPTH = 9.0  # px: the wrist cap is the outline within this depth of the extreme on the wrist side
CORNER_REACH = 24  # contour steps beyond a cap end where the side direction is read
# an open wrist cut turns at least this much into the hand's sides at both ends. Measured on the schoolboy sheet
# (CAP_DEPTH 9, reach 24): wrist stubs 57-69 deg, closed rounded outlines (pointing, grip, cupped) 41-46 deg
CORNER_MIN_DEG = 52.0


def _cap(C: np.ndarray, n: np.ndarray) -> tuple[int, int] | None:
    """Indices (on the tripled contour, middle copy) of the contiguous contour run around the extreme point in
    direction ``n`` whose points lie within CAP_DEPTH of the supporting line."""
    N = len(C)
    proj = C @ n
    k = int(np.argmax(proj))
    m = proj[k]
    i0 = k
    while i0 > k - N + 1 and proj[(i0 - 1) % N] >= m - CAP_DEPTH:
        i0 -= 1
    i1 = k
    while i1 < k + N - 1 and proj[(i1 + 1) % N] >= m - CAP_DEPTH:
        i1 += 1
    if i1 - i0 >= N - 2:
        return None
    return i0 + N, i1 + N


def _side_turns(C: np.ndarray, i0: int, i1: int, reach: int = CORNER_REACH) -> tuple[float, float]:
    """How sharply the contour turns into the hand's sides beyond each cap end: the angle between the cap chord
    and the contour direction from the cap end to the point ``reach`` steps further (deg; 90 = a square corner)."""
    D = np.vstack([C, C, C])
    a, b = D[i0], D[i1]
    t = (b - a) / max(float(np.hypot(*(b - a))), 1e-6)
    out = []
    for p, q, sgn in ((a, D[i0 - reach], -1.0), (b, D[i1 + reach], 1.0)):
        v = (q - p) * sgn
        v = v / max(float(np.hypot(*v)), 1e-6)
        out.append(math.degrees(math.acos(float(np.clip(v @ t, -1.0, 1.0)))))
    return out[0], out[1]


def wrist_opening(mask: np.ndarray, prior: str, target_width: float | None = None) -> dict[str, Any]:
    """Wrist opening of a sleeve-free hand drawing near the inspected side ``prior``.

    These drawings end in a short wrist stub: a gently bowed cut between two near-parallel sides. For each
    direction d within SEARCH_DEG of the prior, the *cap* is the contiguous outline within CAP_DEPTH px of the
    extreme in direction d; it is an open cut when the outline turns at least CORNER_MIN_DEG into the hand's
    sides at both cap ends (a rounded closed outline turns gradually). The widest such cap wins; the
    cut line is the chord between its corners, the axis points from the cut into the hand. A drawing without one
    has a closed (rounded) outline there: the opening is the chord perpendicular to the prior direction at the
    depth where the drawing reaches ``target_width`` (the same side's open-palm cut width), capped at 0.3 x size;
    confidence 0.35, manual pivot review. Returns end points, centre, axis, width, method and confidence."""
    C = trace_contour(mask)
    size = math.sqrt(float(mask.sum()))
    d0 = SIDE_DEG[prior]
    D3 = np.vstack([C, C, C])
    best, widest = None, None
    for d in np.arange(d0 - SEARCH_DEG, d0 + SEARCH_DEG + 0.5, 2.0):
        n = np.array([math.cos(math.radians(d)), math.sin(math.radians(d))])
        cap = _cap(C, n)
        if cap is None:
            continue
        i0, i1 = cap
        a, b = D3[i0], D3[i1]
        L = float(np.hypot(*(b - a)))
        turns = _side_turns(C, i0, i1)
        rec = (L, float(d), n, a, b, turns)
        if widest is None or L > widest[0]:
            widest = rec
        if min(turns) >= CORNER_MIN_DEG and (best is None or (round(L, 1), -abs(d - d0)) > (round(best[0], 1), -abs(best[1] - d0))):
            best = rec
    if best is not None and best[0] >= 0.25 * size:
        L, d, n, a, b, turns = best
        t = (b - a) / L
        nn = np.array([t[1], -t[0]])
        if nn @ n < 0:
            nn = -nn
        # confidence from the corner margin: 0.5 at the threshold, 1.0 at 12 deg above it
        conf = round(min(1.0, 0.5 + 0.5 * (min(turns) - CORNER_MIN_DEG) / 12.0) * min(1.0, L / (0.4 * size)), 3)
        return {"opening": "open_cut", "method": "wrist_stub_cap", "p0": a.tolist(), "p1": b.tolist(),
                "centre": ((a + b) / 2).tolist(), "axis": (-nn).tolist(), "width_px": L,
                "corner_turns_deg": [round(v, 1) for v in turns], "search_direction_deg": round(d % 360, 1),
                "size_px": round(size, 2), "direction_deg": round(math.degrees(math.atan2(nn[1], nn[0])) % 360, 2),
                "confidence": conf, "manual_review": conf < 0.6}
    n0 = np.array([math.cos(math.radians(d0)), math.sin(math.radians(d0))])
    t = np.array([-n0[1], n0[0]])
    proj = C @ n0
    pmax = float(proj.max())
    cen = np.asarray(np.nonzero(mask)[::-1], float).mean(1)
    base = cen + n0 * (pmax - float(cen @ n0))
    tw = target_width or 0.45 * size
    chosen, depth = None, 0.0
    while depth <= 0.3 * size:
        o = base - n0 * depth
        Lc, s0, s1 = _chord(mask, o, t, 2 * size)
        if Lc >= tw:
            chosen = (depth, o, s0, s1, Lc)
            break
        depth += 0.5
    if chosen is None:
        o = base - n0 * 0.3 * size
        Lc, s0, s1 = _chord(mask, o, t, 2 * size)
        chosen = (0.3 * size, o, s0, s1, Lc)
    depth, o, s0, s1, Lc = chosen
    p0, p1 = o + t * s0, o + t * s1
    why = "no outline cap on that side" if widest is None else \
        f"widest cap {widest[0]:.0f} px turns only {widest[5][0]:.0f}/{widest[5][1]:.0f} deg into the sides"
    return {"opening": "closed_outline", "method": "chord_at_wrist_width", "p0": p0.tolist(), "p1": p1.tolist(),
            "centre": ((p0 + p1) / 2).tolist(), "axis": (-n0).tolist(), "width_px": float(Lc), "depth_px": round(depth, 2),
            "target_width_px": None if target_width is None else round(target_width, 2),
            "why_closed": why, "size_px": round(size, 2), "direction_deg": round(d0 % 360, 2), "confidence": 0.35,
            "manual_review": True}


def features(mask: np.ndarray, wrist: dict[str, Any], kind: str | None) -> dict[str, Any]:
    """Readable gesture feature in sprite px: index_tip (farthest outline point from the wrist), thumb_tip (the
    topmost outline point, the thumbs-up drawings are drawn upright), grip_center (centroid of the C's cavity:
    convex hull minus the drawing), palm_center (the drawing's centroid). ``*_dir`` is the unit vector from the
    wrist centre to the feature."""
    P = _boundary(mask)
    c = np.asarray(wrist["centre"], float)
    out: dict[str, Any] = {}
    ys, xs = np.nonzero(mask)
    out["centroid"] = [round(float(xs.mean()), 2), round(float(ys.mean()), 2)]
    if kind == "index_tip":
        p = P[np.argmax(np.hypot(*(P - c).T))]
    elif kind == "thumb_tip":
        top = P[:, 1].min()
        p = P[P[:, 1] <= top + 1.0].mean(0)
    elif kind == "grip_center":
        hull = ConvexHull(P)
        im = Image.new("L", (mask.shape[1], mask.shape[0]), 0)
        ImageDraw.Draw(im).polygon([tuple(P[i]) for i in hull.vertices], fill=1)
        cav = (np.asarray(im) > 0) & ~ndimage.binary_dilation(mask, iterations=2)
        lab, n = ndimage.label(cav)
        if n == 0:
            return out
        k = 1 + int(np.argmax(ndimage.sum(cav, lab, range(1, n + 1))))
        cy, cx = ndimage.center_of_mass(lab == k)
        p = np.array([cx, cy])
        out["grip_cavity_px"] = int((lab == k).sum())
    elif kind == "palm_center":
        p = np.array([xs.mean(), ys.mean()])
    else:
        return out
    v = p - c
    out[kind] = [round(float(p[0]), 2), round(float(p[1]), 2)]
    out[f"{kind}_dir"] = [round(float(v[0] / max(np.hypot(*v), 1e-6)), 4), round(float(v[1] / max(np.hypot(*v), 1e-6)), 4)]
    return out


# -- handedness ---------------------------------------------------------------------------------------------
def _norm_mask(m: np.ndarray, S: int = 160) -> np.ndarray:
    return np.asarray(Image.fromarray((m * 255).astype(np.uint8)).resize((S, S), Image.Resampling.BILINEAR)) > 127


def _iou_best(a: np.ndarray, b: np.ndarray, r: int = 6) -> float:
    best = 0.0
    for dy in range(-r, r + 1, 2):
        for dx in range(-r, r + 1, 2):
            bb = np.roll(np.roll(b, dy, 0), dx, 1)
            best = max(best, float((a & bb).sum()) / max(float((a | bb).sum()), 1.0))
    return best


def mirror_test(rgba_a: np.ndarray, rgba_b: np.ndarray) -> dict[str, Any]:
    """Is drawing b the mirror image of drawing a? Silhouette and interior-line IoU (each normalised to its
    bounding box, best over small shifts), with b as drawn and mirrored. Interior lines (finger separations,
    creases) decide when the silhouettes are similar both ways."""
    def parts(rgba):
        al = rgba[..., 3] > 127
        lum = _lum(rgba)
        inner = (lum < 70) & ndimage.binary_erosion(al, iterations=9)
        ys, xs = np.nonzero(al)
        sl = (slice(ys.min(), ys.max() + 1), slice(xs.min(), xs.max() + 1))
        return al[sl], ndimage.binary_dilation(inner, iterations=2)[sl]
    fa, ia = parts(rgba_a)
    fb, ib = parts(rgba_b)
    FA, FB, IA, IB = _norm_mask(fa), _norm_mask(fb), _norm_mask(ia), _norm_mask(ib)
    r = {"silhouette_same": _iou_best(FA, FB), "silhouette_mirror": _iou_best(FA, FB[:, ::-1]),
         "lines_same": _iou_best(IA, IB), "lines_mirror": _iou_best(IA, IB[:, ::-1])}
    score_m = r["silhouette_mirror"] + r["lines_mirror"]
    score_s = r["silhouette_same"] + r["lines_same"]
    out = {k: round(v, 3) for k, v in r.items()}
    out["mirrored"] = bool(score_m > score_s)
    out["margin"] = round(abs(score_m - score_s), 3)
    return out


def open_palm_thumb(mask: np.ndarray, wrist: dict[str, Any]) -> dict[str, Any]:
    """Thumb side of an open palm, measured: outline mass beyond the wrist's half-width on either side of the
    wrist axis, within 10-55 % of the hand length. sigma = +1: the thumb is on the +n side, n = perp(axis) =
    (-u_y, u_x) in image coordinates. For a palm view, right hand <=> cross(fingers, thumb) = sigma > 0."""
    c, u = np.asarray(wrist["centre"]), np.asarray(wrist["axis"])
    n = np.array([-u[1], u[0]])
    ys, xs = np.nonzero(mask)
    P = np.stack([xs, ys], 1).astype(float)
    t, q = (P - c) @ u, (P - c) @ n
    Ln = float(t.max())
    half = wrist["width_px"] / 2 * 1.1
    band = (t >= 0.10 * Ln) & (t <= 0.55 * Ln)
    pos, neg = int((band & (q > half)).sum()), int((band & (q < -half)).sum())
    sigma = 1 if pos > neg else -1
    T = sigma * n
    return {"thumb_vector": [round(float(T[0]), 3), round(float(T[1]), 3)], "mass_pos_px": pos, "mass_neg_px": neg,
            "sigma": sigma, "ratio": round(max(pos, neg) / max(min(pos, neg), 1), 2)}


def handedness_from(fingers, thumb, view: str) -> str | None:
    """Anatomical hand from the finger direction, thumb direction and view (image coordinates, y down)."""
    vs = VIEW_SIGN.get(view)
    if vs is None:
        return None
    cr = float(fingers[0] * thumb[1] - fingers[1] * thumb[0])
    if abs(cr) < 0.2:
        return None
    return "right" if cr * vs > 0 else "left"


# -- pack building -------------------------------------------------------------------------------------------
def _png(rgba: np.ndarray) -> bytes:
    b = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(b, "PNG", optimize=False, compress_level=9)
    return b.getvalue()


def _mirror_geom(g: dict[str, Any], W: int) -> dict[str, Any]:
    """Mirror sprite-pixel geometry (x -> W - 1 - x; vectors flip x)."""
    out = {}
    for k, v in g.items():
        if isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
            if k.endswith("_dir") or k in ("axis", "thumb_vector"):
                out[k] = [-v[0], v[1]]
            else:
                out[k] = [W - 1 - v[0], v[1]]
        elif isinstance(v, dict):
            out[k] = _mirror_geom(v, W)
        else:
            out[k] = v
    if "direction_deg" in out and isinstance(out["direction_deg"], (int, float)):
        out["direction_deg"] = round((180.0 - out["direction_deg"]) % 360, 2)
    return out


def extract_sheet(sheet: str | Path, inspection: dict[str, Any]) -> dict[str, Any]:
    """Everything measured on the sheet: per cell the sprite (RGBA), its crop box, pixel quality, wrist opening,
    gesture feature; per gesture the mirror test; per row group the anatomical side."""
    data = Path(sheet).read_bytes()
    rgb = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
    g = inspection["grid"]
    bg, bginfo = background(rgb)
    fg = ~bg
    cells, errs = grid_cells(fg, int(g["rows"]), int(g["cols"]))
    lab, _ = ndimage.label(fg)
    out: dict[str, Any] = {"sheet_sha256": _sha(data), "size": [rgb.shape[1], rgb.shape[0]], "errors": errs,
                           "background": bginfo, "cells": {}}
    for c in cells:
        x0, y0, x1, y1 = c["bbox"]
        pad = MARGIN + 3
        X0, Y0, X1, Y1 = max(0, x0 - pad), max(0, y0 - pad), min(rgb.shape[1], x1 + pad), min(rgb.shape[0], y1 + pad)
        sub_lab = lab[Y0:Y1, X0:X1]
        comp = sub_lab == c["label"]
        stray = (sub_lab != c["label"]) & (sub_lab != 0)
        rgba, q = unmatte(rgb[Y0:Y1, X0:X1], comp, bg[Y0:Y1, X0:X1] | stray)
        rgba[stray] = 0
        spr, off = crop(rgba)
        ins = inspection["cells"].get(c["cell"], {})
        flags = []
        if c["touches_sheet_edge"]:
            flags.append("drawing touches the sheet edge (possibly cut off)")
        if q["outline_coverage"] < QUALITY["outline_coverage_min"]:
            flags.append(f"outline covers only {q['outline_coverage']:.1%} of the edge")
        if q["skin_on_edge_px"] > QUALITY["skin_on_edge_max_px"]:
            flags.append(f"{q['skin_on_edge_px']} skin pixels on the edge without outline (cut-off part?)")
        out["cells"][c["cell"]] = {**c, "sprite": spr, "crop_origin_in_sheet": [X0 + off[0], Y0 + off[1]],
                                   "quality": {**q, "stray_px_removed": int(stray.sum())}, "inspection": ins,
                                   "flags": flags}
    # wrist openings: open cuts first (their widths set each group's target width for closed outlines)
    groups = [list(map(int, gr)) for gr in g["groups"]]
    for name, rec in sorted(out["cells"].items(), key=lambda kv: 0 if kv[1]["inspection"].get("gesture") == "open_palm" else 1):
        mask = rec["sprite"][..., 3] > 127
        grp = next(i for i, gr in enumerate(groups) if rec["row"] in gr)
        palm = next((r for r in out["cells"].values() if r["inspection"].get("gesture") == "open_palm" and
                     any(r["row"] in gr for gr in [groups[grp]]) and "wrist" in r), None)
        target = palm["wrist"]["width_px"] if palm and palm["wrist"]["opening"] == "open_cut" else None
        w = wrist_opening(mask, rec["inspection"].get("wrist_side", "top"), target)
        rec["wrist"] = w
        rec["group"] = grp
        rec["features"] = features(mask, w, rec["inspection"].get("feature"))
        if w["manual_review"]:
            rec["flags"].append(f"wrist opening is a {w['opening'].replace('_', ' ')} ({w['method']}); pivot needs manual review")
    # handedness: mirror pairs per gesture, the open palm's thumb, per-drawing thumb evidence
    order = inspection["user_labels"]["gesture_order"]
    per_group = len(order)
    hand: dict[str, Any] = {"pairs": {}, "groups": {}, "per_cell": {}}
    by_gesture: dict[str, list[str]] = {}
    for name, rec in out["cells"].items():
        by_gesture.setdefault(rec["inspection"].get("gesture"), []).append(name)
    for gest in order:
        names = sorted(by_gesture.get(gest, []), key=lambda n: out["cells"][n]["group"])
        if len(names) == 2:
            hand["pairs"][gest] = {"cells": names, **mirror_test(out["cells"][names[0]]["sprite"], out["cells"][names[1]]["sprite"])}
        else:
            out["errors"].append(f"gesture {gest}: {len(names)} drawings (2 expected)")
    for grp in range(len(groups)):
        palm = next((n for n, r in out["cells"].items() if r["group"] == grp and r["inspection"].get("gesture") == "open_palm"), None)
        if palm is None:
            continue
        r = out["cells"][palm]
        th = open_palm_thumb(r["sprite"][..., 3] > 127, r["wrist"])
        side = handedness_from(r["wrist"]["axis"], th["thumb_vector"], r["inspection"].get("view", "palm"))
        hand["groups"][grp] = {"rows": groups[grp], "open_palm_cell": palm, "thumb": th, "side": side,
                               "rule": "palm view: right hand <=> cross(fingers, thumb) > 0 (image coordinates)"}
    for name, r in out["cells"].items():
        ins = r["inspection"]
        grp_side = hand["groups"].get(r["group"], {}).get("side")
        gest = ins.get("gesture")
        pair = hand["pairs"].get(gest, {})
        evidence = []
        own = None
        if ins.get("thumb") in THUMB_VEC:
            fingers = r["features"].get("index_tip_dir") or r["wrist"]["axis"]
            own = handedness_from(fingers, THUMB_VEC[ins["thumb"]], ins.get("view", "palm"))
            evidence.append({"method": "thumb_rule", "fingers": [round(float(v), 3) for v in fingers],
                             "thumb": ins["thumb"], "view": ins.get("view"), "result": own})
        if gest == "open_palm":
            own = hand["groups"][r["group"]]["side"]
            evidence.append({"method": "open_palm_thumb_measured", "result": own})
        if pair:
            evidence.append({"method": "mirror_pair", "mirrored": pair["mirrored"], "margin": pair["margin"]})
        if own is None:
            if pair.get("mirrored", True):
                own = grp_side
                evidence.append({"method": "row_group (mirrored pair)", "result": own})
        agrees = own == grp_side
        hand["per_cell"][name] = {"side": own, "row_group_side": grp_side, "agrees_with_row_label": agrees,
                                  "evidence": evidence}
        r["hand"] = own
        if not agrees:
            r["flags"].append(f"drawn as a {own} hand, but its row group is the {grp_side} side")
    out["handedness"] = hand
    out["per_group"] = per_group
    return out


def build_pack(sheet: str | Path, inspection_path: str | Path, out_dir: str | Path, library_id: str,
               supersedes: list[str] | None = None, zip_name: str | None = None,
               write_pack_dir: bool = False) -> dict[str, Any]:
    """Extract the sheet and write: ``sprites/<cell>.png`` (every drawing as extracted, at sheet scale), the pack
    zip (``manifest.json`` + ``hands/<side>/<gesture>.png`` + README, deterministic bytes) for ``autorig
    hand-import``, ``extraction_report.json`` and ``extraction_sheet.jpg``; ``write_pack_dir`` also unpacks the
    pack into ``pack/``."""
    sheet, out = Path(sheet), Path(out_dir)
    inspection = json.loads(Path(inspection_path).read_text(encoding="utf-8"))
    ex = extract_sheet(sheet, inspection)
    if ex["errors"]:
        raise ValueError("sheet extraction failed: " + "; ".join(ex["errors"]))
    (out / "sprites").mkdir(parents=True, exist_ok=True)
    for name, r in ex["cells"].items():
        (out / "sprites" / f"{name}.png").write_bytes(_png(r["sprite"]))
    # slot assignment: (anatomical side, gesture) -> cell; a missing slot takes the mirror of the drawing its own
    # row group holds for that gesture (derived, flagged)
    groups = ex["handedness"]["groups"]
    group_side = {g: v["side"] for g, v in groups.items()}
    slots: dict[tuple[str, str], dict[str, Any]] = {}
    for name, r in sorted(ex["cells"].items()):
        key = (r["hand"], r["inspection"]["gesture"])
        if key in slots:  # two drawings of the same hand: the one in that side's row group is the primary
            if group_side.get(r["group"]) == r["hand"]:
                slots[key] = {"cell": name, "derived": None}
        else:
            slots[key] = {"cell": name, "derived": None}
    for name, r in sorted(ex["cells"].items()):
        side = group_side.get(r["group"])
        key = (side, r["inspection"]["gesture"])
        if r["hand"] != side and key not in slots:
            slots[key] = {"cell": name, "derived": "mirror_x"}
    assets, files, report_assets = [], {}, []
    for (side, gest), slot in sorted(slots.items(), key=lambda kv: (kv[0][0], GESTURES.index(kv[0][1]))):
        r = ex["cells"][slot["cell"]]
        spr = r["sprite"]
        geom = {"wrist": r["wrist"], "features": r["features"]}
        flags = list(r["flags"])
        if slot["derived"] == "mirror_x":
            spr = spr[:, ::-1].copy()
            geom = _mirror_geom(geom, spr.shape[1])
            flags = [f for f in flags if "row group" not in f]
            flags.append(f"derived: horizontal mirror of {slot['cell']} (drawn as a {r['hand']} hand); the sheet has no "
                         f"{side} {gest.replace('_', ' ')} drawing")
        w = geom["wrist"]
        aid = f"{side}_{gest}"
        rel = f"hands/{side}/{gest}.png"
        png = _png(spr)
        files[rel] = png
        a = {"id": aid, "side": side, "pose": PACK_POSE[gest], "file": rel, "canvas_px": [spr.shape[1], spr.shape[0]],
             "base_orientation": "wrist_edge",
             "wrist_anchor_px": [round(v, 2) for v in w["centre"]],
             "wrist_edge_px": [[round(v, 2) for v in w["p0"]], [round(v, 2) for v in w["p1"]]],
             "wrist_axis": [round(v, 4) for v in w["axis"]],
             "wrist_opening": w["opening"], "wrist_method": w["method"], "wrist_confidence": w["confidence"],
             "wrist_anchor_status": "DETECTED" if not w["manual_review"] else "ESTIMATED_NEEDS_REVIEW",
             "features": geom["features"], "view": r["inspection"].get("view"),
             "sheet_cell": slot["cell"], "derived": slot["derived"],
             "handedness": {"anatomical": r["hand"] if slot["derived"] is None else side,
                            "drawn_as": r["hand"], "row_group_side": group_side.get(r["group"])},
             "sprite_sha256": _sha(png), "sleeve": "none", "approval_status": "NEEDS_REVIEW",
             "artwork_source": "user-supplied sleeve-free hand sheet (AI-generated concept drawings)",
             "flags": flags, "manual_review": bool(flags)}
        assets.append(a)
        report_assets.append({k: a[k] for k in ("id", "sheet_cell", "derived", "wrist_opening", "wrist_confidence",
                                                "flags", "manual_review")})
    manifest = {"library_id": library_id, "schema": "autorig.hand_pack", "schema_version": 2,
                "asset_count": len(assets), "pose_names": [PACK_POSE[g] for g in GESTURES],
                "hand_sides": ["left", "right"], "sleeve": "none", "supersedes": list(supersedes or []),
                "all_variants_require_review": True,
                "source_provenance": "Generated visual concepts supplied by the user as a sheet (no sleeves); extracted "
                                     "by autorig handsheet, not drawn from the original reference artwork",
                "intended_use": "sleeve-free hand switch drawings for the schoolboy character",
                "sheet": {"file": sheet.name, "sha256": ex["sheet_sha256"], "size": ex["size"]},
                "extraction": {"tool": "autorig.handsheet", "background": f"lum > {BG_LUM}, saturation < {BG_SAT}, connected to the border",
                               "unmatte": "alpha from luminance against white, colour = outline colour",
                               "scale": 1.0, "margin_px": MARGIN},
                "assets": assets}
    files["manifest.json"] = json.dumps(manifest, indent=1).encode()
    files["README.md"] = (
        f"# {library_id}\n\nSleeve-free hand drawings extracted from `{sheet.name}` (sha256 {ex['sheet_sha256'][:16]}...).\n"
        "Every drawing is pending review. The wrist opening, handedness and flags in manifest.json are measured by\n"
        "`autorig hand-sheet`; see extraction_report.json in the source folder.\n").encode()
    if write_pack_dir:
        for rel, b in files.items():
            (out / "pack" / rel).parent.mkdir(parents=True, exist_ok=True)
            (out / "pack" / rel).write_bytes(b)
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in sorted(files):
            zi = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            z.writestr(zi, files[rel])
    zpath = out / (zip_name or f"{library_id}.zip")
    zpath.write_bytes(zbuf.getvalue())
    report = {"schema": "autorig.hand_sheet_extraction", "sheet": sheet.name, "sheet_sha256": ex["sheet_sha256"],
              "inspection": Path(inspection_path).name, "library_id": library_id, "zip": zpath.name,
              "zip_sha256": _sha(zbuf.getvalue()), "background": ex["background"],
              "quality_thresholds": QUALITY, "open_cut_min": OPEN_CUT_MIN, "search_deg": SEARCH_DEG,
              "cells": {n: {k: v for k, v in r.items() if k not in ("sprite", "label")} |
                        {"sprite_px": [r["sprite"].shape[1], r["sprite"].shape[0]]}
                        for n, r in ex["cells"].items()},
              "handedness": ex["handedness"], "assets": report_assets,
              "summary": {"drawings": len(ex["cells"]), "assets": len(assets),
                          "open_cut": sum(1 for r in ex["cells"].values() if r["wrist"]["opening"] == "open_cut"),
                          "closed_outline": sum(1 for r in ex["cells"].values() if r["wrist"]["opening"] == "closed_outline"),
                          "mirrored_pairs": sum(1 for p in ex["handedness"]["pairs"].values() if p["mirrored"]),
                          "not_mirrored_pairs": [g for g, p in ex["handedness"]["pairs"].items() if not p["mirrored"]],
                          "derived_assets": [a["id"] for a in assets if a["derived"]],
                          "manual_review": [a["id"] for a in assets if a["manual_review"]]}}
    (out / "extraction_report.json").write_text(json.dumps(report, indent=1, default=_json_default))
    qa_sheet(ex, out / "extraction_sheet.jpg")
    return report


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


def _checker(w: int, h: int, s: int = 8) -> Image.Image:
    yy, xx = np.mgrid[0:h, 0:w]
    c = np.where(((yy // s) + (xx // s)) % 2 == 0, 236, 206).astype(np.uint8)
    return Image.fromarray(np.stack([c, c, c], -1))


def qa_sheet(ex: dict[str, Any], path: Path) -> None:
    """Visual QA: every extracted sprite on a checkerboard (alpha visible), its wrist opening (red: open cut,
    orange: closed-outline chord), the axis into the hand (blue), the gesture feature (green), handedness and flags."""
    font = ImageFont.load_default()
    tiles = []
    for name in sorted(ex["cells"], key=lambda n: (ex["cells"][n]["row"], ex["cells"][n]["col"])):
        r = ex["cells"][name]
        spr = r["sprite"]
        S = 1.25
        im = _checker(spr.shape[1], spr.shape[0])
        im.paste(Image.fromarray(spr, "RGBA"), (0, 0), Image.fromarray(spr, "RGBA"))
        im = im.resize((int(spr.shape[1] * S), int(spr.shape[0] * S)), Image.Resampling.LANCZOS)
        d = ImageDraw.Draw(im)
        w = r["wrist"]
        col = (220, 20, 20) if w["opening"] == "open_cut" else (245, 140, 0)
        p0, p1, c = np.asarray(w["p0"]) * S, np.asarray(w["p1"]) * S, np.asarray(w["centre"]) * S
        d.line([tuple(p0), tuple(p1)], fill=col, width=4)
        d.line([tuple(c), tuple(c + np.asarray(w["axis"]) * 40)], fill=(30, 60, 230), width=3)
        for k in ("index_tip", "thumb_tip", "grip_center", "palm_center"):
            if k in r["features"]:
                f = np.asarray(r["features"][k]) * S
                d.ellipse([f[0] - 6, f[1] - 6, f[0] + 6, f[1] + 6], outline=(20, 160, 40), width=3)
        tile = Image.new("RGB", (330, 420), (255, 255, 255))
        k = min(1.0, 320 / im.width, 330 / im.height)
        im2 = im.resize((int(im.width * k), int(im.height * k)), Image.Resampling.LANCZOS)
        tile.paste(im2, ((330 - im2.width) // 2, 4))
        d = ImageDraw.Draw(tile)
        ins = r["inspection"]
        lines = [f"{name}  {ins.get('gesture')}  -> {r.get('hand')} hand",
                 f"wrist: {w['opening']} conf {w['confidence']}  w {w['width_px']:.0f}px",
                 f"outline {r['quality']['outline_coverage']:.1%}  stray {r['quality']['stray_px_removed']}px"]
        lines += [f"! {f[:52]}" for f in r["flags"][:3]]
        for i, t in enumerate(lines):
            d.text((6, 342 + 13 * i), t, fill=(180, 30, 30) if t.startswith("!") else (20, 20, 30), font=font)
        tiles.append(tile)
    cols = 4
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 336 + 6, rows * 426 + 40), (250, 250, 250))
    ImageDraw.Draw(sheet).text((8, 10), "Hand sheet extraction QA: sprites on checkerboard (alpha), red = open wrist cut, "
                                         "orange = closed-outline chord (manual review), blue = axis into the hand, green = feature",
                               fill=(20, 20, 30), font=font)
    for i, t in enumerate(tiles):
        sheet.paste(t, (6 + (i % cols) * 336, 34 + (i // cols) * 426))
    sheet.save(path, quality=90)
