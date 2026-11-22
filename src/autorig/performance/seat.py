"""SeatAndInteractionController: classroom props (desk, seat) and what the front-view artwork can do with them.

Props are simple test geometry: a desk top (a contact surface) and a seat height. They are drawn as plain outlined
shapes, behind the character, and labelled as test props. They are not character artwork.

What is supported, and what is not, is decided by the artwork, not by the constraint:
- a hand on a desk beside the character: SUPPORTED (a hand contact through the IK solver);
- leaning toward someone: SUPPORTED as a lateral lean; a forward lean is REQUIRES_NEW_VIEW;
- elbows on a desk:
  - standing, it means bending forward: REQUIRES_NEW_VIEW;
  - seated, the seated artwork is missing: REQUIRES_ALTERNATIVE_ARTWORK;
- holding an object: SUPPORTED_WITH_CORRECTIONS (hand contacts; the gripping hand drawing is still pending review);
- sitting on a chair, and pointing while sitting: REQUIRES_ALTERNATIVE_ARTWORK. The front-view thighs and trousers
  cannot show a seated leg (needed: ``art:thigh_foreshortened``, ``art:seat``, ``corrective:trousers_seated``). A
  seat constraint only decides where the pelvis would be. It does not make the crouch look seated, and the
  diagnostic says so instead of rendering a distorted seated pose as a success.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from .contacts import ATTACH, ContactSpec

SEAT_ARTWORK = ["art:thigh_foreshortened", "art:seat", "corrective:trousers_seated"]
PROP_NOTE = "test prop (simple geometry, not character artwork)"


@dataclass
class Desk:
    id: str
    x0: float
    x1: float
    top_y: float
    depth_px: float = 30.0  # visible thickness of the top
    leg_to_y: float | None = None  # floor line for the legs

    def point(self, fraction: float) -> list[float]:
        return [float(self.x0 + fraction * (self.x1 - self.x0)), float(self.top_y)]

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "note": PROP_NOTE}


@dataclass
class Seat:
    x: float
    top_y: float
    width: float = 160.0

    def to_json(self) -> dict[str, Any]:
        return {**asdict(self), "note": PROP_NOTE}


def draw_props(img: np.ndarray, pad: int, desks: list[Desk], floor_y: float | None = None) -> np.ndarray:
    """The character image composited over its props (props behind the character). Returns RGBA."""
    H, W = img.shape[:2]
    bg = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(bg)
    for k in desks:
        x0, x1, y = k.x0 + pad, k.x1 + pad, k.top_y + pad
        fy = (k.leg_to_y if k.leg_to_y is not None else (floor_y if floor_y is not None else y + 330)) + pad
        for lx in (x0 + 14, x1 - 30):
            d.rectangle([lx, y + k.depth_px, lx + 16, fy], fill=(150, 112, 74, 255), outline=(70, 50, 30, 255), width=3)
        d.rectangle([x0, y, x1, y + k.depth_px], fill=(186, 140, 92, 255), outline=(70, 50, 30, 255), width=3)
    out = bg.copy()
    out.alpha_composite(Image.fromarray(img, "RGBA"))
    return np.asarray(out)


class SeatAndInteractionController:
    def __init__(self, eng, profile=None):
        self.eng = eng
        self.p = profile or eng.profile

    def desk_beside(self, side: str = "r", reach: float = 0.93, out: float = 0.45) -> Desk:
        """A desk beside the character whose top is where the palm lands when the arm reaches ``reach`` of its length,
        ``out`` of its length to the side (a comfortable, slightly bent reach)."""
        p = self.p
        sh = np.asarray(p.shoulder.get(side, [p.midline_x, p.waist_y]), float)
        arm, hl = p.arm(side), p.hand_len.get(side, 70.0)
        sgn = -1.0 if side == "r" else 1.0
        dx = out * arm
        dy = float(np.sqrt(max((reach * arm) ** 2 - dx * dx, 1.0)))
        palm_x, top = float(sh[0] + sgn * dx), float(sh[1] + dy + ATTACH["fingertips"] * hl)
        inner, outer = palm_x - sgn * 0.18 * arm, palm_x + sgn * 1.25 * arm
        return Desk("desk", min(inner, outer), max(inner, outer), top, leg_to_y=p.ground_y)

    def hand_on_desk(self, desk: Desk, side: str, start: float, end: float, acquire: float = 0.35,
                     release: float = 0.35, priority: int = 20) -> ContactSpec:
        """Fingertips on the desk top (front view: the hand hangs onto the surface), 0.18 arm lengths from the inner edge
        (where ``desk_beside`` aimed the reach)."""
        arm = self.p.arm(side)
        x = desk.x1 - 0.18 * arm if side == "r" else desk.x0 + 0.18 * arm
        return ContactSpec(f"hand_{side}_desk", "hand_desk", f"hand_{side}", {"point": [float(x), float(desk.top_y)]},
                           start, end, acquire=acquire, release=release, orientation=0.0, attach="fingertips",
                           priority=priority)

    def seat_diagnostic(self, seat: Seat) -> dict[str, Any]:
        """Where the pelvis would go for a seat, and why the front view cannot show it convincingly."""
        p = self.p
        L = (p.leg("r") + p.leg("l")) / 2
        drop = float(seat.top_y - p.hip_y)
        return {"class": "REQUIRES_ALTERNATIVE_ARTWORK", "needs": SEAT_ARTWORK, "pelvis_dy_fraction": round(drop / L, 3),
                "seat": seat.to_json(), "fallback": "labelled preview of the squat-like crouch (not a seated pose)",
                "why": ("a seated thigh points toward the viewer: the front-view thigh would have to be foreshortened. "
                        "Bones are never scaled, so the legs splay sideways into a wide crouch instead. A seat "
                        "constraint only decides where the pelvis goes; it does not fix the drawing")}

    def interactions(self) -> list[dict[str, Any]]:
        return [
            {"interaction": "sit on a chair", "class": "REQUIRES_ALTERNATIVE_ARTWORK", "needs": SEAT_ARTWORK},
            {"interaction": "rest a hand on a desk (beside him)", "class": "SUPPORTED",
             "note": "hand contact through the IK solver; palm-down hand drawing pending review (preview only)"},
            {"interaction": "lean towards another character", "class": "SUPPORTED",
             "note": "lateral lean only; a forward lean is REQUIRES_NEW_VIEW (art:torso_lean_forward)"},
            {"interaction": "elbows on a desk", "class": "REQUIRES_NEW_VIEW",
             "note": "standing, it needs a forward bend; seated, the seated artwork is missing"},
            {"interaction": "hold an object", "class": "SUPPORTED_WITH_CORRECTIONS",
             "note": "one- or two-hand object contacts, moving targets; gripping hand pending review"},
            {"interaction": "point at someone while sitting", "class": "REQUIRES_ALTERNATIVE_ARTWORK",
             "needs": SEAT_ARTWORK},
        ]


__all__ = ["Desk", "Seat", "SeatAndInteractionController", "draw_props", "SEAT_ARTWORK"]
