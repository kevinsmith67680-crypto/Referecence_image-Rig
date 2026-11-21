"""Derived artwork: user mask corrections applied to the source pixels without touching any file.

Base masks are the Phase 2 originals (each opaque pixel belongs to exactly one layer). A mask operation
moves source pixels between layers, so each source pixel stays in at most one layer:
* add: pixels inside the shape and the character footprint move into the layer, leaving their old owner;
* subtract: pixels leave the layer and go to ``transfer_to``, or become *unassigned*, which is a neutral
  gap and is reported;
* refine: smooth the layer edge by 1 px; changed pixels move to or from the neighbouring layers.

A derived layer is the source RGB at its mask, drawn over the component's generated extensions (minus
any the user rejected). The neutral composite therefore equals the source wherever pixels are assigned.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy import ndimage

from ..phase2.store import ArtworkStore


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


class ArtworkModel:
    def __init__(self, pkg: Path):
        self.pkg = Path(pkg)
        src = Image.open(self.pkg / "source" / "original.png")
        self.W, self.H = src.size
        self.source = np.asarray(src.convert("RGBA"))
        self.store = ArtworkStore(self.pkg, self.W, self.H)
        self.base_masks = {c: self.store.original(c)[..., 3] > 0 for c in self.store.originals}
        self.footprint = np.zeros((self.H, self.W), bool)
        for m in self.base_masks.values():
            self.footprint |= m
        self.base_owner = self._owner(self.base_masks)
        self.base_pieces = {}
        for c, m in self.base_masks.items():
            lab, n = ndimage.label(m)
            self.base_pieces[c] = int((np.bincount(lab.ravel())[1:] >= 20).sum()) if n else 0
        self.ext = {}  # component -> [(ext id, rgba)]
        for e in self.store.extensions:
            if e.status in ("pending_review", "approved"):
                self.ext.setdefault(e.component, []).append((e.id, e))
        self._mask_cache: dict[str, dict[str, np.ndarray]] = {}
        self._gen_cache: dict[str, np.ndarray] = {}

    # -- masks -------------------------------------------------------------------------------------
    def _owner(self, masks: dict[str, np.ndarray]) -> np.ndarray:
        own = np.full((self.H, self.W), -1, np.int16)
        self.ids = list(self.base_masks)
        for i, c in enumerate(self.ids):
            own[masks[c]] = i
        return own

    def masks(self, mask_ops: list[dict[str, Any]]) -> dict[str, np.ndarray]:
        key = _digest(mask_ops)
        if key in self._mask_cache:
            return self._mask_cache[key]
        # reuse the longest cached prefix
        masks = None
        for k in range(len(mask_ops) - 1, -1, -1):
            pk = _digest(mask_ops[:k])
            if pk in self._mask_cache:
                masks = {c: m.copy() for c, m in self._mask_cache[pk].items()}
                start = k
                break
        if masks is None:
            masks, start = {c: m.copy() for c, m in self.base_masks.items()}, 0
        for op in mask_ops[start:]:
            self.apply(masks, op)
        if len(self._mask_cache) > 24:
            self._mask_cache.pop(next(iter(self._mask_cache)))
        self._mask_cache[key] = masks
        return masks

    def region(self, shape: dict[str, Any]) -> np.ndarray:
        img = Image.new("L", (self.W, self.H), 0)
        d = ImageDraw.Draw(img)
        pts = [tuple(map(float, p)) for p in shape["points"]]
        if shape["type"] == "polygon":
            d.polygon(pts, fill=255)
        else:
            r = float(shape.get("radius", 10.0))
            h = float(np.clip(shape.get("hardness", 1.0), 0.0, 1.0))
            re_ = max(0.5, r * (0.5 + 0.5 * h))  # 50% contour of a soft brush: binary masks keep the partition
            for p in pts:
                d.ellipse((p[0] - re_, p[1] - re_, p[0] + re_, p[1] + re_), fill=255)
            if len(pts) > 1:
                d.line(pts, fill=255, width=int(round(2 * re_)))
        return np.asarray(img) > 127

    def apply(self, masks: dict[str, np.ndarray], op: dict[str, Any]) -> None:
        t, A = op["type"], op["layer"]
        if t == "mask.reset":  # back to the Phase 2 mask; pixels it claims come back from other layers
            base = self.base_masks[A]
            for c in masks:
                if c != A:
                    masks[c] &= ~base
            masks[A] = base.copy()
            return
        if t == "mask.refine":
            m = masks[A]
            sm = ndimage.binary_closing(ndimage.binary_opening(m, iterations=1), iterations=1) & self.footprint
            removed, added = m & ~sm, sm & ~m
            for c in masks:
                if c != A:
                    masks[c] &= ~added
            masks[A] = sm
            if removed.any():  # hand removed edge pixels to the neighbouring layer with most contact
                best, best_n = None, -1
                for c in masks:
                    if c == A:
                        continue
                    n = int((ndimage.binary_dilation(masks[c], iterations=2) & removed).sum())
                    if n > best_n:
                        best, best_n = c, n
                if best is not None and best_n > 0:
                    masks[best] |= removed
            return
        R = self.region(op["shape"]) & self.footprint
        if op["mode"] == "add":
            for c in masks:
                if c != A:
                    masks[c] &= ~R
            masks[A] |= R
        else:
            moved = masks[A] & R
            masks[A] &= ~R
            if op.get("transfer_to"):
                masks[op["transfer_to"]] |= moved

    # -- derived artwork -------------------------------------------------------------------------------
    def layer_rgba(self, cid: str, masks: dict[str, np.ndarray], rejected: set[str] = frozenset(),
                   feather: float = 0.0, with_extensions: bool = True) -> np.ndarray:
        out = np.zeros((self.H, self.W, 4), np.uint8)
        if with_extensions:
            for eid, e in self.ext.get(cid, []):
                if eid in rejected:
                    continue
                ea = self.store.extension_rgba(e)
                m = ea[..., 3] > 0
                out[m] = ea[m]
        m = masks[cid]
        out[m] = self.source[m]
        out[m, 3] = 255
        if feather > 0:
            a = Image.fromarray(out[..., 3]).filter(ImageFilter.GaussianBlur(feather))
            out[..., 3] = np.minimum(out[..., 3], np.asarray(a))
        return out

    def generated_mask(self, cid: str, masks: dict[str, np.ndarray], rejected: set[str]) -> np.ndarray:
        g = np.zeros((self.H, self.W), bool)
        for eid, e in self.ext.get(cid, []):
            if eid not in rejected:
                g |= self.store.extension_rgba(e)[..., 3] > 0
        return g & ~masks[cid]

    def composite(self, order: list[str], masks: dict[str, np.ndarray], rejected: set[str] = frozenset(),
                  visible: dict[str, bool] | None = None, opacity: dict[str, float] | None = None) -> np.ndarray:
        canvas = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        for c in order:
            if c not in masks or (visible and not visible.get(c, True)):
                continue
            arr = self.layer_rgba(c, masks, rejected)
            if opacity and opacity.get(c, 1.0) < 1.0:
                arr = arr.copy()
                arr[..., 3] = (arr[..., 3] * opacity[c]).astype(np.uint8)
            canvas.alpha_composite(Image.fromarray(arr, "RGBA"))
        return np.asarray(canvas)

    # -- validation and fidelity -----------------------------------------------------------------------
    def validate_masks(self, masks: dict[str, np.ndarray]) -> dict[str, Any]:
        union = np.zeros((self.H, self.W), np.int16)
        for m in masks.values():
            union += m
        unassigned = self.footprint & (union == 0)
        overlap = union > 1
        owner = self._owner(masks)
        changed = (owner != self.base_owner) & self.footprint
        layers = {}
        for c, m in masks.items():
            base = int(self.base_masks[c].sum())
            area = int(m.sum())
            lab, n = ndimage.label(m)
            frags = int((np.bincount(lab.ravel())[1:] >= 20).sum()) if n else 0
            warn = []
            if area == 0:
                warn.append("empty layer")
            if base and abs(area - base) / base > 0.25:
                warn.append(f"area changed {100 * (area - base) / base:+.0f}% vs the segmentation")
            if frags > max(1, self.base_pieces[c]):
                warn.append(f"{frags} separate pieces (segmentation had {self.base_pieces[c]})")
            layers[c] = {"pixels": area, "base_pixels": base, "pieces": frags, "warnings": warn}
        return {"unassigned_px": int(unassigned.sum()), "overlap_px": int(overlap.sum()),
                "reassigned_px": int(changed.sum()), "layers": layers,
                "valid": not overlap.any(), "warnings": ([f"{int(unassigned.sum())} character pixels are in no "
                                                          "layer (neutral-pose gap)"] if unassigned.any() else [])}

    def fidelity(self, order: list[str], masks: dict[str, np.ndarray], rejected: set[str] = frozenset()
                 ) -> dict[str, Any]:
        comp = self.composite(order, masks, rejected)
        fp = self.footprint
        covered = comp[..., 3] > 127
        diff = np.abs(comp[..., :3].astype(np.int16) - self.source[..., :3].astype(np.int16)).max(axis=2)
        inside = fp & covered
        return {"footprint_px": int(fp.sum()), "covered_px": int(inside.sum()),
                "gap_px": int((fp & ~covered).sum()), "max_abs_error_in_covered": int(diff[inside].max() if inside.any() else 0),
                "mean_abs_error_in_covered": round(float(diff[inside].mean()) if inside.any() else 0.0, 4),
                "neutral_exact": bool((fp & ~covered).sum() == 0 and (diff[inside].max() if inside.any() else 0) == 0)}

    def diff_heatmap(self, order, masks, rejected=frozenset()) -> np.ndarray:
        comp = self.composite(order, masks, rejected)
        d = np.abs(comp[..., :3].astype(np.int16) - self.source[..., :3].astype(np.int16)).max(axis=2)
        d[~self.footprint] = 0                      # background is not part of the character (transparent in layers)
        gap = self.footprint & (comp[..., 3] <= 127)
        d[gap] = 0
        out = np.zeros((self.H, self.W, 4), np.uint8)
        hit = d > 0                                   # yellow (small error) -> red (large error)
        out[hit, 0] = 255
        out[hit, 1] = np.clip(255 - d[hit] * 2, 0, 255).astype(np.uint8)
        out[hit, 3] = 240
        out[gap] = (255, 0, 255, 255)                 # uncovered character pixels: magenta
        return out

    def provenance_overlay(self, masks, rejected=frozenset(), order=None) -> np.ndarray:
        out = np.zeros((self.H, self.W, 4), np.uint8)
        gen = np.zeros((self.H, self.W), bool)
        for c in masks:
            gen |= self.generated_mask(c, masks, rejected)
        owner = self._owner(masks)
        changed = (owner != self.base_owner) & self.footprint & (owner >= 0)
        union = np.zeros((self.H, self.W), bool)
        for m in masks.values():
            union |= m
        out[gen & ~union] = (255, 0, 255, 150)  # generated pixels (hidden under other art in neutral)
        out[changed] = (0, 200, 255, 170)  # original pixels re-assigned by a mask correction
        out[self.footprint & ~union] = (255, 0, 0, 230)  # unassigned: neutral gap
        return out


def png_bytes(arr: np.ndarray) -> bytes:
    import io
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG", compress_level=3)
    return buf.getvalue()
