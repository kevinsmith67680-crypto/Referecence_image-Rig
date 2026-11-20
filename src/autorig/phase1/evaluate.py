"""Score a character project of the SYNTHETIC test character against its exact
ground truth (joints, part masks, facial masks/landmarks, fidelity). Works for
any provider, so the same command scores real SAM 3 output once inference runs."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from . import masks as M
from ..synthetic import render


def evaluate_synthetic(project_dir: str | Path) -> dict[str, Any]:
    root = Path(project_dir)
    ch = render()
    anatomy = json.loads((root / "analysis" / "anatomy.json").read_text())
    face = json.loads((root / "analysis" / "face.json").read_text())
    meta = json.loads((root / "metadata.json").read_text())
    joints = {}
    for n, j in anatomy["joints"].items():
        err = None if j["position"] is None else round(math.dist(j["position"], ch.joints[n]), 2)
        joints[n] = {"error_px": err, "reliable": j["reliable"], "method": j["method"]}
    errs = [v["error_px"] for v in joints.values() if v["error_px"] is not None]
    rel = [v["error_px"] for v in joints.values() if v["reliable"] and v["error_px"] is not None]
    parts = {p: round(M.iou(M.load_mask(root / "masks" / "body" / f"{p}.png"), gt), 4)
             for p, gt in ch.anatomical.items()}
    fmasks = {}
    for fid, gt in ch.face.items():
        f = face["features"].get(fid)
        fmasks[fid] = None if f is None else round(M.iou(M.load_mask(root / f["mask"]), gt), 4)
    lms = {}
    for k, gt in ch.landmarks.items():
        v = face["landmarks"].get(k)
        lms[k] = None if not (isinstance(v, list) and len(v) == 2) else round(math.dist(v, gt), 2)
    out = {
        "note": "synthetic ground-truth evaluation - validates the pipeline, not the user's reference character",
        "provider": meta["segmentation_runs"][0]["provider"],
        "image_height_px": ch.size[1],
        "joints": joints,
        "joint_error_px": {"median": round(float(np.median(errs)), 2) if errs else None,
                           "max": max(errs) if errs else None,
                           "max_reliable": max(rel) if rel else None,
                           "missing": [n for n, v in joints.items() if v["error_px"] is None],
                           "unreliable": [n for n, v in joints.items() if not v["reliable"]]},
        "body_part_iou": parts, "body_part_iou_mean": round(float(np.mean(list(parts.values()))), 4),
        "facial_mask_iou": fmasks, "facial_landmark_error_px": lms,
        "fidelity": {k: meta["fidelity"][k] for k in ("coverage_ratio", "overlap_pixels", "max_abs_error_inside_layers",
                                                      "psnr_foreground_db", "ssim_gray_foreground_bbox",
                                                      "linework_retained_ratio")},
    }
    (root / "evaluation_synthetic.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out
