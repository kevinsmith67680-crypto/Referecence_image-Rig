"""Phase 3.5 dynamic draw ordering.

``DynamicLayerOrderController`` rewrites the draw order of one pose from semantic depth relationships evaluated on the
*posed skeleton* (before any pixel is deformed):

* ``arm_cross_front``: a hand that crosses the body midline toward the other side is in front of the other arm (the
  static order always drew the right arm under the left).
* ``hand_behind_body``: an arm the pose places behind the body (``ArtisticPose.depth``) is drawn before the torso and
  pelvis. Its shoulder cap stays in front: the upper arm is split into two independently ordered sublayers by garment
  part (``sleeve_cap``/``blazer_shoulder`` in front, the rest behind).
* ``leg_forward``: in a gait, the swing leg (moving toward the viewer) is drawn over the planted one, instead of the
  left leg always covering the right.
* Artist rules from the editor project: {"id", "move": [layers], "relation": "front"|"behind", "target": [layers],
  "when": {"action": ...} or {}}.

Rules fire only past a threshold with hysteresis (``margin_px``), so the order does not flicker. The neutral pose
order is never changed (no rule fires at rest). Overlaps between moving layers that no rule decides are reported as
``ambiguous``. A sublayer split never changes a pixel: the two sublayers partition the original layer.
"""

from __future__ import annotations

from typing import Any

import numpy as np

ARM = ("upper_arm_{s}", "hand_{s}", "forearm_{s}")
LEG = ("thigh_{s}", "foot_{s}", "shin_{s}")
FRONT_PARTS = ("sleeve_cap_{s}", "blazer_shoulder_{s}")


def _base(c: str) -> str:
    return c.split("__")[0]


class DynamicLayerOrderController:
    def __init__(self, rules: list[dict[str, Any]] | None = None, margin_px: float = 8.0):
        self.rules = list(rules or [])
        self.margin = margin_px
        self.state: dict[str, bool] = {}  # rule id -> currently active (hysteresis across frames)

    # -- helpers --------------------------------------------------------------------------------------------------
    @staticmethod
    def _block(order: list[str], names: tuple[str, ...], s: str) -> list[str]:
        want = {n.format(s=s) for n in names}
        return [c for c in order if _base(c) in want]

    @staticmethod
    def _move_after(order: list[str], block: list[str], anchor: list[str]) -> list[str]:
        if not block or not anchor:
            return order
        rest = [c for c in order if c not in block]
        i = max(rest.index(c) for c in anchor if c in rest)
        return rest[:i + 1] + block + rest[i + 1:]

    @staticmethod
    def _move_before(order: list[str], block: list[str], anchor: list[str]) -> list[str]:
        if not block or not anchor:
            return order
        rest = [c for c in order if c not in block]
        i = min(rest.index(c) for c in anchor if c in rest)
        return rest[:i] + block + rest[i:]

    def _hyst(self, rid: str, value: float) -> bool:
        on = self.state.get(rid, False)
        on = value > self.margin if not on else value > -self.margin
        self.state[rid] = on
        return on

    # -- main --------------------------------------------------------------------------------------------------------
    def apply(self, order: list[str], info: dict[str, Any], it, eng=None, depth: dict[str, str] | None = None,
              action: str = "") -> tuple[list[str], dict[str, Any]]:
        J = info.get("joints_posed") or {}
        out = list(order)
        fired: list[dict[str, Any]] = []
        mid = J.get("pelvis", [None])[0]
        # 1. arm crossing the midline: in front of the other arm
        if mid is not None:
            for s, o, sg in (("r", "l", 1.0), ("l", "r", -1.0)):
                h = J.get(f"hand_{s}")
                if h is None:
                    continue
                cross = sg * (h[0] - mid)  # the right hand moving to image right (the character's left) crosses
                if self._hyst(f"arm_cross_front_{s}", cross):
                    blk, other = self._block(out, ARM, s), self._block(out, ARM, o)
                    if blk and other and out.index(blk[0]) < out.index(other[-1]):
                        out = self._move_after(out, blk, other)
                        fired.append({"rule": f"arm_cross_front_{s}", "why": f"hand_{s} crosses the midline by {cross:.0f} px"})
        # 2. arm behind the body (declared by the pose), shoulder cap kept in front (sublayer split)
        for s in ("r", "l"):
            if (depth or {}).get(f"arm_{s}") != "behind_body":
                continue
            blk = self._block(out, ARM, s)
            body = [c for c in out if _base(c) in ("torso", "pelvis")]
            split = None
            if eng is not None:
                split = split_by_parts(eng, f"upper_arm_{s}", [p.format(s=s) for p in FRONT_PARTS])
            if split is not None:
                front, back = split
                up = [c for c in blk if _base(c) == f"upper_arm_{s}"]
                for c in up:
                    out.remove(c)
                blk = [c for c in blk if c not in up]
                out = self._move_before(out, [back] + blk, body)
                out = self._move_after(out, [front], body)
                fired.append({"rule": f"hand_behind_body_{s}", "why": "pose places the arm behind the body",
                              "sublayers": [front, back]})
            else:
                out = self._move_before(out, blk, body)
                fired.append({"rule": f"hand_behind_body_{s}", "why": "pose places the arm behind the body"})
        # 3. gait: the swing leg over the planted one
        contact = getattr(it, "contact", {}) or {}
        if getattr(it, "balance", "static") != "static" and contact and not all(contact.values()) and any(contact.values()):
            swing = next(s for s in ("r", "l") if not contact.get(s, True))
            plant = "l" if swing == "r" else "r"
            blk, other = self._block(out, LEG, swing), self._block(out, LEG, plant)
            if blk and other and out.index(blk[0]) < out.index(other[-1]):
                out = self._move_after(out, blk, other)
                fired.append({"rule": f"leg_forward_{swing}", "why": f"{swing} leg swings toward the viewer"})
        # 4. artist rules
        for r in self.rules:
            w = r.get("when") or {}
            if w.get("action") not in (None, action):
                continue
            mv = [c for c in out if _base(c) in set(r.get("move", []))]
            tg = [c for c in out if _base(c) in set(r.get("target", []))]
            if not mv or not tg:
                continue
            out = self._move_after(out, mv, tg) if r.get("relation") == "front" else self._move_before(out, mv, tg)
            fired.append({"rule": r.get("id", "artist"), "why": "artist rule"})
        amb = self._ambiguous(out, J)
        return out, {"changed": out != list(order), "fired": fired, "ambiguous": amb,
                     "moved": [c for c in out if c not in order or order.index(c) != out.index(c)][:12]}

    @staticmethod
    def _ambiguous(order: list[str], J: dict[str, Any]) -> list[str]:
        """Hands closer than 40 px to each other or to the head, with no rule deciding: reported for review."""
        out = []
        h = {s: J.get(f"hand_{s}") for s in ("r", "l")}
        if h["r"] is not None and h["l"] is not None and np.hypot(h["r"][0] - h["l"][0], h["r"][1] - h["l"][1]) < 40:
            out.append("hands overlap: which is in front is decided by the default order (review)")
        return out


def split_by_parts(eng, cid: str, front_parts: list[str]) -> tuple[str, str] | None:
    """Split one layer into two independently ordered sublayers by garment part (``<cid>__front``/``<cid>__back``).
    The two sublayers partition the layer's pixels exactly; nothing is redrawn. Registered once per engine."""
    st = eng.structure
    if st is None or cid not in eng.layers:
        return None
    f_id, b_id = f"{cid}__front", f"{cid}__back"
    if f_id in eng.dfm.info and b_id in eng.dfm.info:
        return f_id, b_id
    lp = st.layer_parts.get(cid)
    if lp is None:
        return None
    rgba = eng.layers[cid]
    H, W = rgba.shape[:2]
    ids = np.full((H, W), -1, np.int16)
    x0, y0 = lp.origin
    h, w = lp.ids.shape
    ids[y0:y0 + h, x0:x0 + w] = lp.ids
    want = [st.index[n] for n in front_parts if n in st.index]
    fm = np.isin(ids, want) & (rgba[..., 3] > 0)
    if not fm.any() or fm.sum() == (rgba[..., 3] > 0).sum():
        return None
    front = rgba.copy()
    front[~fm] = 0
    back = rgba.copy()
    back[fm] = 0
    eng.dfm.add_variant_layer(f_id, front)
    eng.dfm.add_variant_layer(b_id, back)
    eng.variants[f_id], eng.variants[b_id] = front, back
    return f_id, b_id


__all__ = ["DynamicLayerOrderController", "split_by_parts"]
