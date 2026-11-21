"""Vector correction shapes: cubic Bezier paths in source-pixel coordinates.

A vector is ``{id, name, purpose, target, nodes: [{x, y, hin: [dx, dy], hout: [dx, dy]}], closed, fill,
stroke, stroke_width, visible, origin, status}``. ``hin`` and ``hout`` are control-handle offsets from
the node. Phase 2 SVG corrections (joint caps, eyelid guides) become editable seeds and keep their
'generated, pending review' status. Vectors never replace bitmap artwork.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

K = 0.5523  # cubic Bezier circle constant


def circle_nodes(cx: float, cy: float, r: float) -> list[dict[str, Any]]:
    k = K * r
    return [{"x": cx + r, "y": cy, "hin": [0, -k], "hout": [0, k]},
            {"x": cx, "y": cy + r, "hin": [k, 0], "hout": [-k, 0]},
            {"x": cx - r, "y": cy, "hin": [0, k], "hout": [0, -k]},
            {"x": cx, "y": cy - r, "hin": [-k, 0], "hout": [k, 0]}]


def _rgb(s: str | None, default: str) -> str:
    return (s or default).replace(" ", "")


def seed_vectors(pkg: Path, art: dict[str, Any], W: int, H: int) -> dict[str, dict[str, Any]]:
    out = {}
    for v in art.get("vectors", []):
        p = pkg / v["path"]
        if not p.is_file():
            continue
        root = ET.parse(p).getroot()
        for el in root.iter():
            tag = el.tag.split("}")[-1]
            nodes, closed = None, True
            if tag == "circle":
                nodes = circle_nodes(float(el.get("cx")), float(el.get("cy")), float(el.get("r")))
            elif tag in ("polyline", "polygon"):
                pts = [tuple(map(float, xy.split(","))) for xy in el.get("points", "").split()]
                nodes = [{"x": x, "y": y, "hin": [0, 0], "hout": [0, 0]} for x, y in pts]
                closed = tag == "polygon"
            if nodes:
                vid = el.get("id") or v["id"]
                out[vid] = {"id": vid, "name": vid, "purpose": "joint correction" if "cap" in vid else "eyelid guide",
                            "target": v.get("target"), "nodes": nodes, "closed": closed,
                            "fill": _rgb(el.get("fill"), "none") if closed else "none",
                            "stroke": _rgb(el.get("stroke"), "rgb(20,20,20)"),
                            "stroke_width": float(el.get("stroke-width", 2.5)), "visible": True,
                            "origin": "phase2_generated", "status": v.get("status", "pending_review"),
                            "source_file": v["path"]}
    return out


def path_d(v: dict[str, Any]) -> str:
    n = v["nodes"]
    if not n:
        return ""
    parts = [f"M {n[0]['x']:.2f} {n[0]['y']:.2f}"]
    segs = list(zip(n, n[1:])) + ([(n[-1], n[0])] if v.get("closed") else [])
    for a, b in segs:
        c1 = (a["x"] + a.get("hout", [0, 0])[0], a["y"] + a.get("hout", [0, 0])[1])
        c2 = (b["x"] + b.get("hin", [0, 0])[0], b["y"] + b.get("hin", [0, 0])[1])
        parts.append(f"C {c1[0]:.2f} {c1[1]:.2f} {c2[0]:.2f} {c2[1]:.2f} {b['x']:.2f} {b['y']:.2f}")
    if v.get("closed"):
        parts.append("Z")
    return " ".join(parts)


def to_svg(v: dict[str, Any], W: int, H: int) -> str:
    gen = v.get("origin") != "user"
    desc = ("GENERATED correction, edited in the AutoRig editor" if gen else "user-authored correction") + \
           f"; status {v.get('status')}; not original artwork"
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="{W}" '
            f'height="{H}" viewBox="0 0 {W} {H}">\n  <title>{v["id"]}</title>\n  <desc>{desc}</desc>\n'
            f'  <path id="{v["id"]}" d="{path_d(v)}" fill="{v.get("fill", "none")}" stroke="{v.get("stroke")}" '
            f'stroke-width="{v.get("stroke_width", 2)}" stroke-linejoin="round" stroke-linecap="round"/>\n</svg>\n')


def bounds(v: dict[str, Any]) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for n in v["nodes"]:
        for dx, dy in ((0, 0), tuple(n.get("hin", [0, 0])), tuple(n.get("hout", [0, 0]))):
            xs.append(n["x"] + dx)
            ys.append(n["y"] + dy)
    return min(xs), min(ys), max(xs), max(ys)


def parse_path_d(d: str) -> list[dict[str, Any]]:
    """Inverse of path_d for M/C/Z paths (round-trip of exported SVG)."""
    toks = re.findall(r"[MCZ]|-?\d+(?:\.\d+)?", d)
    nodes, i, cur = [], 0, None
    while i < len(toks):
        t = toks[i]
        if t == "M":
            cur = {"x": float(toks[i + 1]), "y": float(toks[i + 2]), "hin": [0, 0], "hout": [0, 0]}
            nodes.append(cur)
            i += 3
        elif t == "C":
            c1x, c1y, c2x, c2y, x, y = map(float, toks[i + 1:i + 7])
            cur["hout"] = [c1x - cur["x"], c1y - cur["y"]]
            if math.isclose(x, nodes[0]["x"]) and math.isclose(y, nodes[0]["y"]) and len(nodes) > 1:
                nodes[0]["hin"] = [c2x - x, c2y - y]
            else:
                cur = {"x": x, "y": y, "hin": [c2x - x, c2y - y], "hout": [0, 0]}
                nodes.append(cur)
            i += 7
        else:
            i += 1
    return nodes
