"""Phase 3.8.1: arms-down shoulder candidate (reviewable, never approved in code).

Diagnosis (tough student, and the same mechanism on the schoolboy). With an arm lowered from the T-pose, a dark bar
sticks up above the shoulder. It is not an original pixel: Phase 2 reconstructed the torso hidden under each upper arm
with a deterministic flat fill that continued the visible shoulder line, outline included, straight out under the
sleeve (a run about 30 px long and 4-12 px thick). In the T-pose the sleeve hides it. When the arm comes down the
sleeve rotates away and the run is exposed as a horizontal bar ending in a square corner.

The square shoulder itself is the T-pose drawing: its shoulder line is horizontal and meets the sleeve's top edge,
which becomes the outer side of a hanging arm. Rounding that corner would hide original pixels, so it needs an
arms-down shoulder drawing (artwork request ``art:arms_down_shoulder``).

``ShoulderExtensionRounding`` builds a candidate torso drawing from the character's own pixels. Original pixels are
copied unchanged. Only generated pixels in the shoulder run under each arm change: those outside a round shoulder end
are removed, and the end gets an outline in the character's measured outline colour and width. The end starts where
the original shoulder line stops (the shoulder point), and its radius is the run's width, capped at 36 px. The
candidate is stored in the alternative-artwork store (``alternative:shoulder_rounding@v<N>``, pending review) and is
selected only through the alternative-drawing mechanism: previewed with ``allow_pending``, used in production after a
person approves it. At rest the changed pixels are under the sleeve, so the T-pose composite is unchanged (tested).
"""

from __future__ import annotations

from typing import Any

import numpy as np

# arms-down presets and actions the candidate is offered for (any pose with an arm well below the horizontal)
SUPPORTED_POSES = ["relaxed_standing", "hands_at_sides", "pointing_speaking", "conversational_gesture", "hands_on_hips",
                   "one_hand_shrug", "head_tilt_compensated", "side_lean", "leaning_backward", "leaning_forward",
                   "casual_walk", "running", "natural_squat", "sitting"]
MAX_RADIUS_PX = 36.0
OUTLINE_LUM = 45.0


def _outline_colour(img: np.ndarray, mask: np.ndarray) -> np.ndarray:
    lum = img[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    dark = mask & (lum < OUTLINE_LUM)
    return np.median(img[..., :3][dark].astype(np.float32), 0) if dark.any() else np.array([25.0, 25.0, 30.0])


class ShoulderExtensionRounding:
    """Round shoulder ends for the generated torso run under each upper arm (see the module docstring)."""

    def __init__(self, outline_width: float | None = None):
        self.outline_width = outline_width

    def run_region(self, eng, side: str) -> dict[str, Any] | None:
        gen = (eng.generated or {}).get("torso")
        if gen is None or "torso" not in eng.layers:
            return None
        t = eng.layers["torso"]
        alpha = t[..., 3] > 0
        orig = alpha & ~gen
        px, py = (float(v) for v in eng.sk.bone(f"upper_arm_{side}").head)
        H, W = alpha.shape
        yy, xx = np.mgrid[:H, :W]
        zone = gen & (yy > py - 60) & (yy < py + 45) & (np.abs(xx - px) < 80)
        if zone.sum() < 20:
            return None
        zy, zx = np.nonzero(zone)
        med = 1 if side == "r" else -1  # the body's midline lies toward +x for the right arm (image left)
        x_end = int(zx.max() + 1) if side == "r" else int(zx.min() - 1)
        col = orig[:, min(max(x_end + med * 2, 0), W - 1)] & (np.arange(H) > py - 60) & (np.arange(H) < py + 45)
        if not col.any():
            return None
        y_top = int(np.nonzero(col)[0].min())
        width = int(zx.max() - zx.min() + 1)
        return {"zone": zone, "x_end": x_end, "y_top": y_top, "medial": med, "run_width_px": width,
                "radius_px": float(max(8.0, min(MAX_RADIUS_PX, width + 2))), "pivot": [round(px, 1), round(py, 1)],
                "generated_run_px": int(zone.sum())}

    def generate(self, eng) -> tuple[np.ndarray, dict[str, Any]] | None:
        from .correctives import _outline_style  # noqa: PLC0415

        t = eng.layers["torso"]
        out = t.copy()
        H, W = t.shape[:2]
        yy, xx = np.mgrid[:H, :W]
        rep: dict[str, Any] = {"sides": {}, "changed_original_px": 0}
        any_side = False
        for s in ("r", "l"):
            reg = self.run_region(eng, s)
            if reg is None:
                rep["sides"][s] = {"status": "no generated shoulder run under this arm"}
                continue
            zone, R = reg["zone"], reg["radius_px"]
            cx, cy = reg["x_end"], reg["y_top"] + R
            lat = (xx - cx) * (-reg["medial"])  # > 0 lateral of the shoulder point
            inside = (lat <= 0) | (yy >= cy) | ((lat ** 2 + (yy - cy) ** 2) <= R * R)
            drop = zone & ~inside
            ow = self.outline_width
            if ow is None:
                _, ow = _outline_style(eng.source, (t[..., 3] > 0) & ~eng.generated["torso"])
            ring = zone & inside & (lat > 0) & (yy < cy) & ((lat ** 2 + (yy - cy) ** 2) > (R - float(ow)) ** 2)
            oc = _outline_colour(t, (t[..., 3] > 0) & ~eng.generated["torso"] & (np.abs(xx - reg["pivot"][0]) < 120)
                                 & (np.abs(yy - reg["pivot"][1]) < 60))
            out[drop] = 0
            out[ring, :3] = np.clip(np.rint(oc), 0, 255).astype(np.uint8)
            out[ring, 3] = 255
            any_side = True
            rep["sides"][s] = {"status": "rounded", "shoulder_point": [reg["x_end"], reg["y_top"]],
                               "radius_px": R, "outline_width_px": float(ow),
                               "outline_rgb": [round(float(v)) for v in oc], "removed_generated_px": int(drop.sum()),
                               "outline_px": int(ring.sum()), "generated_run_px": reg["generated_run_px"]}
        if not any_side:
            return None
        gen = eng.generated["torso"]
        rep["changed_original_px"] = int(((out != t).any(-1) & (t[..., 3] > 0) & ~gen).sum())
        rep["changed_generated_px"] = int(((out != t).any(-1) & gen).sum())
        return out, rep

    def register(self, pkg, eng) -> tuple[Any, dict[str, Any]] | None:
        """Store the candidate as a pending alternative drawing of the torso (append-only; never approved here)."""
        from ..artistic.alternatives import AlternativeArtwork, AlternativeArtworkStore  # noqa: PLC0415

        g = self.generate(eng)
        if g is None:
            return None
        rgba, rep = g
        if rep["changed_original_px"]:
            raise ValueError("the shoulder candidate may only change generated pixels")
        art = AlternativeArtwork(
            art_id="shoulder_rounding", layer="torso", supported_poses=list(SUPPORTED_POSES),
            attach_bone="chest", provenance="ORIGINAL_DERIVED", method="geometric: ShoulderExtensionRounding",
            requirement="art:arms_down_shoulder",
            notes=["only the Phase 2 generated shoulder run under each arm changes; every original pixel is copied",
                   "an approximation until an arms-down shoulder is drawn (artwork request art:arms_down_shoulder)",
                   "pending review: preview only until approved"])
        store = AlternativeArtworkStore(pkg)
        added = store.add(art, rgba, eng.source.shape[:2], set(eng.layers))
        return added, rep
