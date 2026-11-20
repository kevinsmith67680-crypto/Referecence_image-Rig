"""Procedural artwork for the Phase 0 test character.

Each part is rendered as a *full-canvas* transparent RGBA PNG (same pixel
size as the Moho project) with the part painted at its final position. Moho
displays a project-sized image so that it fills the frame, so all layers line
up with each other and with bones computed by :mod:`autorig.geometry` without
any per-layer offsets. A matching SVG (vector source) is written per part.

Part geometry is derived from the rig spec's bones so the joints of the art
always coincide with the bone pivots. Joint ends are round caps centred on the
pivot, the classic cut-out construction that keeps parts overlapping cleanly
while they rotate.

This module is specific to the synthetic Phase 0 character. In later phases
art layers come from segmentation of a reference image instead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw

from .spec import RigSpec

RGBA = tuple[int, int, int, int]
Vec = tuple[float, float]

SUPERSAMPLE = 3
OUTLINE_W = 6.0

OUTLINE: RGBA = (31, 42, 68, 255)
SHIRT: RGBA = (63, 127, 217, 255)
SLEEVE: RGBA = (47, 104, 191, 255)
SKIN: RGBA = (242, 201, 160, 255)
HAIR: RGBA = (92, 60, 40, 255)
BUTTON: RGBA = (236, 242, 252, 255)
CHEEK: RGBA = (232, 150, 140, 255)


# --- primitives --------------------------------------------------------------

@dataclass(frozen=True)
class Circle:
    center: Vec
    r: float
    fill: RGBA


@dataclass(frozen=True)
class RoundedRect:
    box: tuple[float, float, float, float]
    radius: float
    fill: RGBA


@dataclass(frozen=True)
class Capsule:
    p0: Vec
    p1: Vec
    width: float
    fill: RGBA


@dataclass(frozen=True)
class Chord:
    box: tuple[float, float, float, float]
    start: float  # degrees, clockwise from 3 o'clock (PIL convention, y down)
    end: float
    fill: RGBA


@dataclass(frozen=True)
class Arc:
    box: tuple[float, float, float, float]
    start: float
    end: float
    color: RGBA
    width: float


Outlined = Circle | RoundedRect | Capsule
Detail = Circle | Chord | Arc


@dataclass(frozen=True)
class PartRecipe:
    outlined: tuple[Outlined, ...]  # merged silhouette: outline pass, then fill pass
    details: tuple[Detail, ...] = ()  # drawn on top, no outline


def build_recipes(spec: RigSpec) -> dict[str, PartRecipe]:
    """Recipes for the Phase 0 test character, keyed by part name."""
    torso = spec.bone("torso")
    head = spec.bone("head")
    upper = spec.bone("upper_arm")
    fore = spec.bone("forearm")

    tx = torso.base_px[0]
    torso_box = (tx - 120, torso.tip_px[1] - 15, tx + 120, torso.base_px[1] + 15)

    hx = head.base_px[0]
    head_c = (hx, (head.base_px[1] + head.tip_px[1]) / 2.0)
    head_r = 125.0
    head_box = (head_c[0] - head_r, head_c[1] - head_r, head_c[0] + head_r, head_c[1] + head_r)
    neck_box = (hx - 22, head.base_px[1] - 40, hx + 22, head.base_px[1] + 20)

    fdx, fdy = fore.tip_px[0] - fore.base_px[0], fore.tip_px[1] - fore.base_px[1]
    flen = math.hypot(fdx, fdy)
    hand_c = (fore.tip_px[0] + 20 * fdx / flen, fore.tip_px[1] + 20 * fdy / flen)

    return {
        "torso": PartRecipe(
            outlined=(RoundedRect(torso_box, 70, SHIRT),),
            details=tuple(
                Circle((tx, torso_box[1] + 95 + 70 * i), 9, BUTTON) for i in range(3)
            ),
        ),
        "head": PartRecipe(
            outlined=(RoundedRect(neck_box, 10, SKIN), Circle(head_c, head_r, SKIN)),
            details=(
                Chord(head_box, 198, 342, HAIR),
                Circle((head_c[0] - 45, head_c[1] + 5), 13, OUTLINE),
                Circle((head_c[0] + 45, head_c[1] + 5), 13, OUTLINE),
                Circle((head_c[0] - 75, head_c[1] + 45), 16, CHEEK),
                Circle((head_c[0] + 75, head_c[1] + 45), 16, CHEEK),
                Arc((head_c[0] - 55, head_c[1] + 5, head_c[0] + 55, head_c[1] + 80), 25, 155, OUTLINE, 7),
            ),
        ),
        "upper_arm": PartRecipe(outlined=(Capsule(upper.base_px, upper.tip_px, 72, SLEEVE),)),
        "forearm": PartRecipe(
            outlined=(Capsule(fore.base_px, fore.tip_px, 60, SKIN), Circle(hand_c, 44, SKIN)),
        ),
    }


# --- raster rendering --------------------------------------------------------

def _s(v: float) -> float:
    return v * SUPERSAMPLE


def _draw_outlined(draw: ImageDraw.ImageDraw, prim: Outlined, grow: float, color: RGBA) -> None:
    if isinstance(prim, Circle):
        cx, cy, r = _s(prim.center[0]), _s(prim.center[1]), _s(prim.r + grow)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    elif isinstance(prim, RoundedRect):
        x0, y0, x1, y1 = prim.box
        draw.rounded_rectangle(
            (_s(x0 - grow), _s(y0 - grow), _s(x1 + grow), _s(y1 + grow)),
            radius=_s(prim.radius + grow),
            fill=color,
        )
    elif isinstance(prim, Capsule):
        half = prim.width / 2.0 + grow
        p0 = (_s(prim.p0[0]), _s(prim.p0[1]))
        p1 = (_s(prim.p1[0]), _s(prim.p1[1]))
        draw.line((p0, p1), fill=color, width=max(1, round(_s(2 * half))))
        for c in (p0, p1):
            r = _s(half)
            draw.ellipse((c[0] - r, c[1] - r, c[0] + r, c[1] + r), fill=color)
    else:  # pragma: no cover - guarded by type
        raise TypeError(prim)


def _draw_detail(draw: ImageDraw.ImageDraw, prim: Detail) -> None:
    if isinstance(prim, Circle):
        _draw_outlined(draw, prim, 0.0, prim.fill)
    elif isinstance(prim, Chord):
        draw.chord(tuple(_s(v) for v in prim.box), prim.start, prim.end, fill=prim.fill)
    elif isinstance(prim, Arc):
        draw.arc(tuple(_s(v) for v in prim.box), prim.start, prim.end, fill=prim.color,
                 width=max(1, round(_s(prim.width))))
    else:  # pragma: no cover
        raise TypeError(prim)


def render_part(recipe: PartRecipe, width: int, height: int) -> Image.Image:
    big = Image.new("RGBA", (width * SUPERSAMPLE, height * SUPERSAMPLE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    for prim in recipe.outlined:
        _draw_outlined(draw, prim, OUTLINE_W, OUTLINE)
    for prim in recipe.outlined:
        _draw_outlined(draw, prim, 0.0, prim.fill)
    for prim in recipe.details:
        _draw_detail(draw, prim)
    return big.resize((width, height), Image.Resampling.LANCZOS)


# --- SVG (vector source) -----------------------------------------------------

def _hex(c: RGBA) -> str:
    return "#{:02x}{:02x}{:02x}".format(*c[:3])


def _f(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def _svg_outlined(prim: Outlined, grow: float, color: RGBA) -> str:
    if isinstance(prim, Circle):
        return f'<circle cx="{_f(prim.center[0])}" cy="{_f(prim.center[1])}" r="{_f(prim.r + grow)}" fill="{_hex(color)}"/>'
    if isinstance(prim, RoundedRect):
        x0, y0, x1, y1 = prim.box
        return (
            f'<rect x="{_f(x0 - grow)}" y="{_f(y0 - grow)}" width="{_f(x1 - x0 + 2 * grow)}" '
            f'height="{_f(y1 - y0 + 2 * grow)}" rx="{_f(prim.radius + grow)}" fill="{_hex(color)}"/>'
        )
    if isinstance(prim, Capsule):
        return (
            f'<line x1="{_f(prim.p0[0])}" y1="{_f(prim.p0[1])}" x2="{_f(prim.p1[0])}" y2="{_f(prim.p1[1])}" '
            f'stroke="{_hex(color)}" stroke-width="{_f(prim.width + 2 * grow)}" stroke-linecap="round"/>'
        )
    raise TypeError(prim)  # pragma: no cover


def _arc_point(box: tuple[float, float, float, float], deg: float) -> Vec:
    cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
    rx, ry = (box[2] - box[0]) / 2.0, (box[3] - box[1]) / 2.0
    t = math.radians(deg)
    return (cx + rx * math.cos(t), cy + ry * math.sin(t))


def _svg_detail(prim: Detail) -> str:
    if isinstance(prim, Circle):
        return _svg_outlined(prim, 0.0, prim.fill)
    rx = (prim.box[2] - prim.box[0]) / 2.0
    ry = (prim.box[3] - prim.box[1]) / 2.0
    a = _arc_point(prim.box, prim.start)
    b = _arc_point(prim.box, prim.end)
    large = 1 if (prim.end - prim.start) % 360 > 180 else 0
    arc = f"M {_f(a[0])} {_f(a[1])} A {_f(rx)} {_f(ry)} 0 {large} 1 {_f(b[0])} {_f(b[1])}"
    if isinstance(prim, Chord):
        return f'<path d="{arc} Z" fill="{_hex(prim.fill)}"/>'
    if isinstance(prim, Arc):
        return (
            f'<path d="{arc}" fill="none" stroke="{_hex(prim.color)}" '
            f'stroke-width="{_f(prim.width)}" stroke-linecap="round"/>'
        )
    raise TypeError(prim)  # pragma: no cover


def render_svg(name: str, recipe: PartRecipe, width: int, height: int) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f"  <title>{escape(name)} (AutoRig Phase 0 vector source)</title>",
        '  <g id="outline">',
        *("    " + _svg_outlined(p, OUTLINE_W, OUTLINE) for p in recipe.outlined),
        "  </g>",
        '  <g id="fill">',
        *("    " + _svg_outlined(p, 0.0, p.fill) for p in recipe.outlined),
        "  </g>",
        '  <g id="details">',
        *("    " + _svg_detail(p) for p in recipe.details),
        "  </g>",
        "</svg>",
        "",
    ]
    return "\n".join(lines)


def generate_artwork(spec: RigSpec, out_root: str | Path) -> list[Path]:
    """Write every part's PNG (and SVG if the spec names one). Returns paths written."""
    out_root = Path(out_root)
    recipes = build_recipes(spec)
    written: list[Path] = []
    for part in spec.parts:
        recipe = recipes[part.name]
        png_path = out_root / part.image
        png_path.parent.mkdir(parents=True, exist_ok=True)
        render_part(recipe, spec.canvas_width, spec.canvas_height).save(png_path, optimize=True)
        written.append(png_path)
        if part.vector_source:
            svg_path = out_root / part.vector_source
            svg_path.parent.mkdir(parents=True, exist_ok=True)
            svg_path.write_text(render_svg(part.name, recipe, spec.canvas_width, spec.canvas_height),
                                encoding="utf-8")
            written.append(svg_path)
    return written
