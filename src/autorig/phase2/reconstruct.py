"""Body and facial reconstruction engines.

Order of preference (cheapest and most predictable first):
1. Deterministic flat fill: when the owner's visible pixels around the hidden
   region are essentially one colour, fill the region with that colour and
   continue the part's outline along its expected-shape boundary, using the
   part's own outline colour and measured stroke width. No AI, reproducible.
2. Generative inpainting via a ReconstructionProvider, for regions whose
   surroundings are not flat (shading, texture, folds) - or that config forces.
3. Otherwise the region is left empty and flagged (never faked).

Whatever a method produces goes through ArtworkStore.add_extension, which
drops every pixel outside the region mask or on the owner's original pixels.
All results are 'pending_review' until a human approves them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage

from ..phase1 import masks as M
from ..segmentation.budget import BudgetExceeded
from ..segmentation.replicate_client import ReplicateError
from .occlusion import Region, kind
from .providers import CandidateRejected, ModelDriftError, ReconstructionProvider, ReconstructionRequest
from .store import ArtworkStore

SKIP_OWNER_KINDS = {"feature", "hair", "eye_iris"}
FACIAL_KINDS = {"head", "eye_white"}


@dataclass
class ReconstructionConfig:
    flat_share: float = 0.8  # share of the dominant colour needed to call a region "flat"
    context_px: int = 64
    max_crop: int = 1024
    n_candidates: int = 2
    seed: int = 1234
    prompt: str = ("flat 2D cartoon illustration, continue the existing artwork exactly, same flat colours, "
                   "same dark outline thickness, no texture, no new details")
    negative_prompt: str | None = "photorealistic, texture, gradient, noise, new objects, text"
    force_generative: set[str] = field(default_factory=set)
    # small crops are upscaled by an integer factor for the model (FLUX-class models need a few hundred
    # pixels to work with) and the candidates are area-averaged back to source pixels
    inpaint_min_side: int = 512
    reconstruct_unexposed: bool = False


def _quant(rgb: np.ndarray) -> np.ndarray:
    q = (rgb // 8).astype(np.int32)
    return q[..., 0] * 1024 + q[..., 1] * 32 + q[..., 2]


def dominant_color(rgba: np.ndarray, mask: np.ndarray) -> tuple[tuple[int, int, int] | None, float]:
    if not mask.any():
        return None, 0.0
    px = rgba[..., :3][mask]
    keys = _quant(px)
    vals, counts = np.unique(keys, return_counts=True)
    top = vals[np.argmax(counts)]
    colour = px[keys == top].mean(axis=0)
    # share of pixels within a small colour tolerance of the dominant colour (anti-aliasing and
    # quantisation noise merge; genuinely different colours, e.g. sock vs skin, do not)
    share = float((np.abs(px.astype(np.float32) - colour).max(axis=1) <= 28).mean())
    return tuple(int(v) for v in colour.round()), share


def outline_props(rgba: np.ndarray, owner: np.ndarray, dark: np.ndarray) -> tuple[tuple[int, int, int], float]:
    band = owner & ~M.erode(owner, 8) & dark
    col, _ = dominant_color(rgba, band) if band.any() else ((30, 30, 30), 0.0)
    if band.any():
        width = float(np.median(ndimage.distance_transform_edt(band)[band])) * 2.0
    else:
        width = 3.0
    return col or (30, 30, 30), max(1.5, min(width, 12.0))


class ReconstructionEngine:
    def __init__(self, store: ArtworkStore, source_rgba: np.ndarray, components: dict[str, np.ndarray],
                 expected: dict[str, np.ndarray | None], dark: np.ndarray,
                 provider: ReconstructionProvider | None = None, config: ReconstructionConfig | None = None):
        self.store, self.rgba, self.comp, self.expected, self.dark = store, source_rgba, components, expected, dark
        self.provider = provider
        self.cfg = config or ReconstructionConfig()

    def run(self, regions: list[Region]) -> list[dict[str, Any]]:
        results = []
        for r in sorted(regions, key=lambda r: r.id):
            results.append(self._one(r))
        return results

    def _one(self, r: Region) -> dict[str, Any]:
        base = {"region": r.id, "owner": r.owner, "facial": kind(r.owner) in FACIAL_KINDS, "exposed_by": r.exposed_by,
                "region_pixels": M.area(r.mask), "region_confidence": r.confidence, "ambiguous": r.ambiguous}
        if kind(r.owner) in SKIP_OWNER_KINDS:
            r.strategy = "not_applicable"
            return base | {"strategy": r.strategy, "extensions": []}
        if not r.exposed_by and not self.cfg.reconstruct_unexposed:
            r.strategy = "not_needed_for_planned_motion"
            return base | {"strategy": r.strategy, "extensions": []}
        owner = self.comp[r.owner]
        # anti-aliased fringe next to linework is not a "colour": sample away from lines,
        # widening the sample when the visible part is tiny (e.g. a short neck)
        ring = None
        for cand in (owner & M.dilate(r.mask, 10) & ~M.dilate(self.dark, 2), owner & ~M.dilate(self.dark, 2),
                     owner & ~M.dilate(self.dark, 1), owner & ~self.dark):
            if M.area(cand) >= 30:
                ring = cand
                break
        if ring is None:
            ring = owner
        fill, share = dominant_color(self.rgba, ring)
        flat = fill is not None and share >= self.cfg.flat_share and r.id not in self.cfg.force_generative
        if flat:
            ext, info = self._flat_fill(r, fill)
            e = self.store.add_extension(r.owner, ext, r.mask, method="deterministic_flat_fill", region_id=r.id,
                                         provenance=info | {"colour_share": round(share, 3)})
            r.strategy = "deterministic_flat_fill"
            return base | {"strategy": r.strategy, "extensions": [e.id], "colour_share": round(share, 3)}
        if self.provider is None:
            r.strategy = "generative_required_no_provider"
            return base | {"strategy": r.strategy, "extensions": [], "colour_share": round(share, 3),
                           "note": "surroundings not flat; needs generative inpainting or manual artwork"}
        return base | self._generative(r, share)

    def _flat_fill(self, r: Region, fill) -> tuple[np.ndarray, dict[str, Any]]:
        ext = np.zeros_like(self.rgba)
        ext[r.mask] = (*fill, 255)
        E = self.expected.get(r.owner)
        info: dict[str, Any] = {"fill_rgb": list(fill), "outline": None}
        if E is not None:
            col, width = outline_props(self.rgba, self.comp[r.owner], self.dark)
            w = max(1, int(round(width)))
            band = E & ~M.erode(E, w) & r.mask
            # continue only outline pieces that a VISIBLE outline of the part actually runs into
            entry = M.dilate(self.comp[r.owner] & self.dark, 2)
            keep = np.zeros_like(band)
            for comp in M.components(band):
                if (comp & entry).any():
                    keep |= comp
            info["outline_pieces_dropped_without_visible_entry"] = len(M.components(band)) - len(M.components(keep))
            band = keep
            if band.any():
                ext[band] = (*col, 255)
                info["outline"] = {"rgb": list(col), "width_px": round(width, 2), "pixels": M.area(band),
                                   "follows": "expected-shape boundary"}
        return ext, info

    def _crop_box(self, mask: np.ndarray) -> tuple[int, int, int, int] | None:
        x0, y0, x1, y1 = M.bbox_xyxy(mask)
        c = self.cfg.context_px
        x0, y0, x1, y1 = x0 - c, y0 - c, x1 + c, y1 + c
        w = ((x1 - x0 + 31) // 32) * 32
        h = ((y1 - y0 + 31) // 32) * 32
        if max(w, h) > self.cfg.max_crop:
            return None
        H, W = mask.shape
        x0 = int(min(max(0, x0), max(0, W - w)))
        y0 = int(min(max(0, y0), max(0, H - h)))
        if w > W or h > H:
            return None
        return x0, y0, x0 + w, y0 + h

    def _generative(self, r: Region, share: float) -> dict[str, Any]:
        box = self._crop_box(r.mask)
        if box is None:
            r.strategy = "generative_required_region_too_large"
            return {"strategy": r.strategy, "extensions": [], "colour_share": round(share, 3)}
        x0, y0, x1, y1 = box
        crop = self.rgba[y0:y1, x0:x1].copy()
        cmask = r.mask[y0:y1, x0:x1]
        ch, cw = cmask.shape
        s = max(1, min(-(-self.cfg.inpaint_min_side // max(ch, cw)), self.cfg.max_crop // max(ch, cw)))
        send_img, send_mask = crop, cmask
        if s > 1:  # integer factor keeps the size multiple; nearest-neighbour keeps the mask binary
            send_img = np.asarray(Image.fromarray(crop, "RGBA").resize((cw * s, ch * s), Image.Resampling.LANCZOS))
            send_mask = np.repeat(np.repeat(cmask, s, axis=0), s, axis=1)
        req = ReconstructionRequest(r.id, send_img, send_mask, self.cfg.prompt,
                                    self.cfg.negative_prompt if getattr(self.provider, "adapter", None) is None
                                    or self.provider.adapter.supports_negative_prompt else None,
                                    self.cfg.n_candidates, self.cfg.seed)
        try:
            cands = self.provider.reconstruct(req)
        except ModelDriftError:  # a changed model must stop the run, never be skipped per region
            raise
        except BudgetExceeded as exc:  # stop paying, keep going: the region is recorded, not faked
            r.strategy = "generative_skipped_budget"
            return {"strategy": r.strategy, "extensions": [], "error": str(exc), "colour_share": round(share, 3)}
        except (CandidateRejected, ValueError, ReplicateError) as exc:
            r.strategy = "generative_failed"
            return {"strategy": r.strategy, "extensions": [], "error": str(exc), "colour_share": round(share, 3)}
        ids = []
        for k, c in enumerate(cands):
            cand = c.rgba
            if s > 1:  # exact area average of each s x s block back to one source pixel
                cand = np.asarray(Image.fromarray(cand, "RGBA").resize((cw, ch), Image.Resampling.BOX))
            full = np.zeros_like(self.rgba)
            full[y0:y1, x0:x1] = cand
            changed_outside = int(((cand[..., :3] != crop[..., :3]).any(axis=2) & ~cmask).sum())
            e = self.store.add_extension(
                r.owner, full, r.mask, method=f"generative:{self.provider.name}", region_id=r.id,
                status="pending_review" if k == 0 else "alternative",
                provenance={**c.provenance, "candidate_index": k, "seed": c.seed, "crop_xyxy": list(box),
                            "pixels_changed_outside_mask_discarded": changed_outside, "prompt": req.prompt,
                            "model_input_scale": s, "model_input_size": [cw * s, ch * s],
                            "negative_prompt": req.negative_prompt})
            ids.append(e.id)
        r.strategy = "generative"
        return {"strategy": r.strategy, "extensions": ids, "candidates": len(cands), "crop_xyxy": list(box),
                "model_input_scale": s}
