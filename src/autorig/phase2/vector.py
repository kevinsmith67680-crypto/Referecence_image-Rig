"""Editable vector correction assets (SVG), generated only where appropriate.

* Joint caps for joints whose pose sweep still shows gaps, only when the
  parent part is flat-coloured (a cap cannot fake texture).
* Eyelid guides from the Phase 1 eyelid outlines (control GUIDES, not artwork).
Every file uses the full source canvas as its viewBox (same coordinates as the
bitmap layers) and is registered as generated, never as an exact reconstruction.
No automatic tracing of the illustration is performed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _svg(w: int, h: int, title: str, body: str) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="{w}" '
            f'height="{h}" viewBox="0 0 {w} {h}">\n  <title>{title}</title>\n  <desc>GENERATED correction/guide - '
            f'not original artwork, not an exact reconstruction. Pending review.</desc>\n{body}\n</svg>\n')


def joint_cap(path: Path, w, h, joint: str, center, radius: float, fill, stroke, stroke_w) -> None:
    body = (f'  <circle id="{joint}_cap" cx="{center[0]:.2f}" cy="{center[1]:.2f}" r="{radius:.2f}" '
            f'fill="rgb{tuple(fill)}" stroke="rgb{tuple(stroke)}" stroke-width="{stroke_w:.2f}"/>')
    path.write_text(_svg(w, h, f"joint cap {joint}", body), encoding="utf-8")


def eyelid_guide(path: Path, w, h, side: str, upper: list, lower: list) -> None:
    pts = lambda P: " ".join(f"{x:.1f},{y:.1f}" for x, y in P)
    body = (f'  <polyline id="upper_eyelid_{side}" points="{pts(upper)}" fill="none" stroke="#00a0ff" stroke-width="2"/>\n'
            f'  <polyline id="lower_eyelid_{side}" points="{pts(lower)}" fill="none" stroke="#00a0ff" stroke-width="2"/>')
    path.write_text(_svg(w, h, f"eyelid guide {side}", body), encoding="utf-8")


def make_vector_assets(root: Path, w: int, h: int, joints_prep: dict[str, Any], flat_info: dict[str, Any],
                       face: dict[str, Any]) -> list[dict[str, Any]]:
    out_dir = root / "artwork" / "vector"
    out_dir.mkdir(parents=True, exist_ok=True)
    assets = []
    for joint, j in joints_prep.items():
        if "planned_vector_correction" not in j["recommended_binding"] or not j["pivot"]:
            continue
        parent = (j["parent_components"] or [None])[0]
        info = flat_info.get(parent)
        if not info:
            continue  # not flat: a vector cap would fake detail -> leave to reconstruction/manual art
        p = out_dir / f"joint_cap_{joint}.svg"
        joint_cap(p, w, h, joint, j["pivot"], j["overlap_padding_px"] / 1.6, info["fill_rgb"],
                  info.get("outline_rgb", [30, 30, 30]), info.get("outline_width", 3.0))
        assets.append({"id": f"joint_cap_{joint}", "kind": "vector_correction", "path": f"artwork/vector/{p.name}",
                       "target": parent, "joint": joint, "category": "generated_vector",
                       "exact_reconstruction": False, "editable_in_moho": True, "status": "pending_review"})
    for s in ("r", "l"):
        lines = face.get("landmarks", {}).get(f"eyelid_lines_{s}")
        if lines and lines.get("upper") and lines.get("lower"):
            p = out_dir / f"eyelid_guide_{s}.svg"
            eyelid_guide(p, w, h, s, lines["upper"], lines["lower"])
            assets.append({"id": f"eyelid_guide_{s}", "kind": "facial_control_guide", "path": f"artwork/vector/{p.name}",
                           "target": f"eye_{s}", "joint": None, "category": "generated_vector",
                           "exact_reconstruction": False, "editable_in_moho": True, "status": "pending_review"})
    return assets
