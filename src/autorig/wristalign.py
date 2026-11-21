"""WristAlignmentController: sleeve-free hand drawings at the character's wrist.

A sleeve-free drawing carries its own wrist opening (``autorig.handsheet``: the chord across the wrist stub, or a
chord at the wrist width when the outline is closed there) and the unit axis from that opening into the hand. The
controller maps it onto the character:

* wrist point: the character's own shirt-cuff edge (``handstore.reference_wrist``: skeleton + original pixels);
* rotation: the drawing's wrist axis onto the hand bone (``natural`` orientation; the drawing continues the
  forearm), plus the user's rotation offset. Rotations are about the wrist opening's centre (the anchor), never
  about the image centre;
* scale: one uniform scale per side and library (no per-pose size pop, no distortion);
* overlap: the opening is tucked ``tuck`` px under the cuff so no gap can open; drawing pixels more than
  ``tuck + 2`` px behind the opening (the closed cap, the stub's top outline) are trimmed. They lie under the sleeve
  and would otherwise swing out beside it when the wrist bends. The sleeve is never modified.

User corrections (editor project, ``handstore.placement``): dx, dy (px, world), rot (deg), scale (multiplier),
overlap (px added to the tuck) and pivot_x, pivot_y (sprite px: moves the wrist opening's centre on the drawing,
the manual pivot). The automatic parameters and the corrected ones are both reported.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from .handstore import TUCK_PX, Placement, place

TRIM_MARGIN_PX = 2.0  # drawing pixels kept behind the tucked opening, beyond the tuck itself (world px)


def _ang(v) -> float:
    """``phase2.skeleton.ang``: degrees from straight down, clockwise on screen."""
    return math.degrees(math.atan2(-float(v[0]), float(v[1])))


class WristAlignmentController:
    def __init__(self, ref: dict[str, dict[str, Any]]):
        """``ref``: per side ``handstore.reference_wrist`` (cuff edge, axis, cuff width, angle)."""
        self.ref = ref

    # -- the drawing's opening -------------------------------------------------------------------------------
    @staticmethod
    def opening(geom: dict[str, Any], corr: dict[str, Any] | None = None) -> dict[str, Any]:
        p0, p1 = (np.asarray(p, float) for p in geom["wrist_edge_px"])
        axis = np.asarray(geom["wrist_axis"], float)
        axis = axis / max(float(np.hypot(*axis)), 1e-9)
        c = (p0 + p1) / 2
        corr = corr or {}
        c = c + np.array([float(corr.get("pivot_x", 0.0)), float(corr.get("pivot_y", 0.0))])
        return {"centre": c, "axis": axis, "p0": p0, "p1": p1, "width_px": float(np.hypot(*(p1 - p0))),
                "opening": geom.get("wrist_opening", "open_cut"), "confidence": float(geom.get("wrist_confidence", 1.0))}

    def natural_angle(self, side: str, geom: dict[str, Any]) -> float:
        """Placement angle that turns the drawing's wrist axis onto the character's hand bone."""
        return float(self.ref[side]["angle_deg"]) - _ang(geom["wrist_axis"])

    # -- placement -----------------------------------------------------------------------------------------
    def tuck(self, corr: dict[str, Any] | None = None) -> float:
        return max(0.0, TUCK_PX + float((corr or {}).get("overlap", 0.0)))

    def placement(self, side: str, geom: dict[str, Any], scale: float, corr: dict[str, Any] | None = None
                  ) -> tuple[Placement, Placement]:
        """(automatic, corrected) placement. Automatic: opening centre -> cuff edge centre, tucked TUCK_PX along
        the bone; natural angle; the side scale. Corrected: the user's pivot, overlap, dx/dy, rot, scale."""
        ref = self.ref[side]
        u = np.asarray(ref["axis"], float)
        op0 = self.opening(geom)
        auto = Placement(tuple(op0["centre"]), tuple(np.asarray(ref["edge"], float) - TUCK_PX * u),
                         self.natural_angle(side, geom), float(scale))
        corr = corr or {}
        op = self.opening(geom, corr)
        point = np.asarray(ref["edge"], float) - self.tuck(corr) * u + \
            np.array([float(corr.get("dx", 0.0)), float(corr.get("dy", 0.0))])
        final = Placement(tuple(op["centre"]), tuple(point), auto.angle + float(corr.get("rot", 0.0)),
                          auto.scale * float(corr.get("scale", 1.0)))
        return auto, final

    def trim(self, sprite: np.ndarray, geom: dict[str, Any], pl: Placement, tuck: float) -> tuple[np.ndarray, int]:
        """Remove drawing pixels more than (tuck + TRIM_MARGIN_PX) world px behind the opening (along the
        drawing's axis). They are under the sleeve; trimming them keeps a bent wrist from showing them beside it."""
        op = self.opening(geom)
        H, W = sprite.shape[:2]
        yy, xx = np.mgrid[0:H, 0:W]
        anchor = np.asarray(pl.anchor, float)
        t = (xx - anchor[0]) * op["axis"][0] + (yy - anchor[1]) * op["axis"][1]
        cut = t < -(tuck + TRIM_MARGIN_PX) / max(pl.scale, 1e-6)
        n = int((cut & (sprite[..., 3] > 0)).sum())
        if not n:
            return sprite, 0
        out = sprite.copy()
        out[cut, 3] = 0
        return out, n

    def layer(self, side: str, sprite: np.ndarray, geom: dict[str, Any], scale: float,
              corr: dict[str, Any] | None, shape) -> tuple[np.ndarray, dict[str, Any]]:
        """Full-canvas rest layer of the drawing at the wrist, and the alignment parameters (saved with the
        candidate, exported in hand_switches.json)."""
        auto, final = self.placement(side, geom, scale, corr)
        tuck = self.tuck(corr)
        trimmed, n_trim = self.trim(sprite, geom, final, tuck)
        rgba = place(trimmed, final, shape)
        op = self.opening(geom, corr)
        ref = self.ref[side]
        fit = op["width_px"] * final.scale / max(float(ref["cuff_width_px"]), 1e-6)
        params = {"method": "wrist_edge_match", "opening": op["opening"], "opening_confidence": op["confidence"],
                  "manual_review": op["opening"] != "open_cut" or op["confidence"] < 0.6,
                  "wrist_edge_px": [[round(float(v), 2) for v in op["p0"]], [round(float(v), 2) for v in op["p1"]]],
                  "wrist_axis": [round(float(v), 4) for v in op["axis"]],
                  "pivot_px": [round(float(v), 2) for v in op["centre"]],
                  "natural_angle_deg": round(self.natural_angle(side, geom), 3),
                  "rotation_offset_deg": round(float((corr or {}).get("rot", 0.0)), 3),
                  "tuck_px": round(tuck, 2), "trimmed_px": n_trim,
                  "wrist_width_px": round(op["width_px"], 2), "wrist_fit": round(fit, 3),
                  "attachment_bone": f"hand_{side}", "rotation_about": "wrist opening centre (anchor)"}
        return rgba, {"auto": auto.to_json(), "final": final.to_json(), "params": params}

    # -- scale -----------------------------------------------------------------------------------------------
    def side_scale(self, side: str, palm: np.ndarray | None, palm_geom: dict[str, Any] | None,
                   target_area: float | None, cover: np.ndarray | None, shape,
                   widths: list[float]) -> tuple[float, dict[str, Any]]:
        """One scale per side and library. Primary (``open_palm_area``): the library's open palm, placed at the
        wrist, shows the visible area of the character's original open hand (iterated, sleeve composited over
        both), as for the first store library. Fallback (``wrist_width``): the cuff width / the median opening
        width of the side's drawings. Both are reported."""
        ref = self.ref[side]
        w = float(np.median(widths)) if widths else 0.0
        width_scale = float(ref["cuff_width_px"]) / w if w > 0 else 1.0
        scale, method = width_scale, "wrist_width"
        if palm is not None and palm_geom is not None and target_area and cover is not None:
            for _ in range(4):
                auto, _ = self.placement(side, palm_geom, scale)
                spr, _ = self.trim(palm, palm_geom, auto, TUCK_PX)
                placed = place(spr, auto, shape)
                area = float(((placed[..., 3] > 127) & ~cover).sum())
                if area <= 0:
                    break
                scale *= math.sqrt(target_area / area)
            method = "open_palm_area"
        return scale, {"scale": round(scale, 5), "method": method, "wrist_width_scale": round(width_scale, 5),
                       "median_opening_px": round(w, 2), "cuff_width_px": round(float(ref["cuff_width_px"]), 2),
                       "open_palm_wrist_fit": None if palm_geom is None else
                       round(self.opening(palm_geom)["width_px"] * scale / float(ref["cuff_width_px"]), 3)}
