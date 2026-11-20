"""Score hidden-region reconstruction against the SYNTHETIC character's exact
ground truth: its drawing layers contain the complete shapes behind every
occlusion. Valid ONLY for the synthetic fixture; it proves nothing about
real cartoon artwork or generative models."""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

from ..phase1 import masks as M
from ..synthetic import render

# Phase 2 component -> synthetic drawing layers (back to front) that make up the complete part
GT_LAYERS = {
    "torso": ["shirt_body"], "neck": ["neck"], "pelvis": ["shorts"],
    "head_base": ["ear_r", "ear_l", "head_skin"],
    "eye_r_white": ["eye_white_r"], "eye_l_white": ["eye_white_l"],
}
for s in ("r", "l"):
    GT_LAYERS.update({f"upper_arm_{s}": [f"upper_arm_skin_{s}", f"sleeve_{s}"], f"forearm_{s}": [f"forearm_{s}"],
                      f"hand_{s}": [f"hand_{s}"], f"thigh_{s}": [f"thigh_{s}", "shorts"], f"shin_{s}": [f"shin_{s}"],
                      f"foot_{s}": [f"shoe_{s}"]})


def gt_component(ch, cid: str) -> np.ndarray | None:
    names = GT_LAYERS.get(cid)
    if not names:
        return None
    canvas = Image.new("RGBA", ch.size, (0, 0, 0, 0))
    for layer in ch.layers:
        if layer.name in names:
            arr = ch.layer_rgba[layer.name].copy()
            if layer.name == "shorts" and cid.startswith("thigh_"):  # only this thigh's side of the garment
                xs = np.arange(arr.shape[1])[None, :]
                keep = xs < 500 if cid.endswith("_r") else xs >= 500
                arr[~np.broadcast_to(keep, arr.shape[:2])] = 0
            canvas.alpha_composite(Image.fromarray(arr, "RGBA"))
    return np.asarray(canvas)


def evaluate_against_synthetic(store, regions) -> dict[str, Any]:
    ch = render()
    per_region, tot = {}, {"ext": 0, "hit": 0, "gt": 0, "covered": 0, "colour_err_sum": 0.0, "colour_n": 0}
    for r in regions:
        exts = [e for e in store.extensions if e.region_id == r.id and e.status in ("pending_review", "approved")]
        gt = gt_component(ch, r.owner)
        if not exts or gt is None:
            continue
        ext = np.zeros(r.mask.shape, bool)
        rgba = np.zeros((*r.mask.shape, 4), np.uint8)
        for e in exts:
            a = store.extension_rgba(e)
            m = a[..., 3] > 0
            ext |= m
            rgba[m] = a[m]
        gt_a = gt[..., 3] > 127
        gt_hidden = gt_a & r.mask
        hit = ext & gt_a
        err = np.abs(rgba[..., :3].astype(int) - gt[..., :3].astype(int)).max(axis=2)[hit]
        per_region[r.id] = {"generated_pixels": M.area(ext), "precision": round(M.area(hit) / max(M.area(ext), 1), 4),
                            "recall_within_region": round(M.area(ext & gt_hidden) / max(M.area(gt_hidden), 1), 4),
                            "mean_colour_error": round(float(err.mean()), 2) if err.size else None,
                            "pixels_colour_error_gt_32": int((err > 32).sum())}
        tot["ext"] += M.area(ext)
        tot["hit"] += M.area(hit)
        tot["gt"] += M.area(gt_hidden)
        tot["covered"] += M.area(ext & gt_hidden)
        tot["colour_err_sum"] += float(err.sum())
        tot["colour_n"] += int(err.size)
    return {"note": "synthetic fixture ground truth only; not evidence about real artwork",
            "regions": per_region,
            "overall": {"precision": round(tot["hit"] / max(tot["ext"], 1), 4),
                        "recall_within_regions": round(tot["covered"] / max(tot["gt"], 1), 4),
                        "mean_colour_error": round(tot["colour_err_sum"] / max(tot["colour_n"], 1), 2)}}
