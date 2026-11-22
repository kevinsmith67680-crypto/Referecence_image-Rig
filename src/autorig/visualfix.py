"""Phase 3.8.1: non-destructive artwork candidates for the visual defects found on the new character.

Nothing here changes a package. Each function returns a *candidate* (pixels or editor-project operations) with the
measurements that justify it; a person applies it through the normal channels (Phase 2 review, the Hand Inspector's
``handstore.placement``, the Mask tool's ``mask.edit``), and the change is reviewed there:

- ``eye_white_candidate``: the generated eye white under each iris (FLUX drew it dark) refilled flat with the
  character's own visible sclera colour (a deterministic fill, like the Phase 2 flat fills);
- ``wrist_scale_candidates``: per-drawing uniform scale corrections for store hands whose wrist is narrower than the
  sleeve opening allows (``handstore.placement`` ops, fixed per drawing, never animated);
- ``hem_mask_candidates``: ``mask.edit`` strokes that move the trouser-hem outline pixels resting on each shoe from
  the shin layer to the foot layer (they separated from the shoe as small dark marks when the stance narrowed);
- ``skin_candidates``: the Phase 3.8 skin-matched hand candidates (already on disk), with their measured distance.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from scipy import ndimage

LUM = np.array([0.299, 0.587, 0.114], np.float32)


def _lum(a: np.ndarray) -> np.ndarray:
    return a[..., :3].astype(np.float32) @ LUM


def eye_white_candidate(eng) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for s in ("r", "l"):
        cid = f"eye_{s}_white"
        lay, gen = eng.layers.get(cid), (eng.generated or {}).get(cid)
        if lay is None or gen is None or not gen.any():
            out[s] = {"status": "no generated eye white"}
            continue
        orig = (lay[..., 3] > 0) & ~gen
        lum = _lum(lay)
        sclera = orig & (lum > 180)
        if not sclera.any():
            out[s] = {"status": "no visible sclera to sample"}
            continue
        colour = np.median(lay[..., :3][sclera].astype(np.float32), 0)
        cand = lay.copy()
        cand[gen, :3] = np.clip(np.rint(colour), 0, 255).astype(np.uint8)
        before = lay[..., :3][gen].astype(np.float32)
        out[s] = {"status": "candidate", "layer": cid, "generated_px": int(gen.sum()),
                  "before_median_rgb": [round(float(v)) for v in np.median(before, 0)],
                  "before_dark_fraction": round(float((before @ LUM < 80).mean()), 3),
                  "candidate_rgb": [round(float(v)) for v in colour], "sclera_samples_px": int(sclera.sum()),
                  "rgba": cand, "how": "reject the FLUX eye-white extension in review, then re-run the deterministic "
                                      "flat fill (or import this candidate as the replacement); pending review"}
    return out


def wrist_scale_candidates(eng, min_fit: float = 0.75, target: float = 0.76) -> list[dict[str, Any]]:
    ops = []
    mid = eng.profile.midline_x
    for s in ("r", "l"):
        for pose, v in eng.hand_lib.hands[s].items():
            if not v.cid.startswith("store:") or not v.placement:
                continue
            a = eng.hand_lib.alignment_v(v, mid)
            fit = (v.placement.get("params") or {}).get("wrist_fit")
            if fit is None or fit >= min_fit:
                continue
            uid = v.cid.split("store:", 1)[1]
            scale = round(target / float(fit), 3)
            ops.append({"side": s, "pose": pose, "uid": uid, "wrist_fit": round(float(fit), 3),
                        "alignment_status": a.get("status"), "proposed_scale": scale,
                        "op": {"type": "handstore.placement", "uid": uid, "fields": {"scale": scale}},
                        "note": "uniform, fixed per drawing (never animated); review it in the Hand Inspector"})
    return ops


def hem_mask_candidates(eng, reach_px: int = 3) -> dict[str, Any]:
    """Trouser-hem outline pixels resting on each shoe: original shin-layer pixels that are dark (outline) and lie
    within ``reach_px`` of the foot layer. As ``mask.edit`` strokes they move to the foot layer, so they travel
    with the shoe they outline."""
    res: dict[str, Any] = {"ops": [], "sides": {}}
    for s in ("r", "l"):
        shin, foot = eng.masks.get(f"shin_{s}"), eng.masks.get(f"foot_{s}")
        if shin is None or foot is None:
            continue
        gen = (eng.generated or {}).get(f"shin_{s}")
        own = shin & ~(gen if gen is not None else np.zeros_like(shin))
        near = ndimage.binary_dilation(foot, iterations=reach_px)
        dark = _lum(eng.source) < 90
        sel = own & near & dark
        lab, n = ndimage.label(sel)
        pts = []
        for i in range(1, n + 1):
            ys, xs = np.nonzero(lab == i)
            for y, x in zip(ys[::2], xs[::2]):  # every other pixel: a 1.5 px brush covers the run
                pts.append([float(x), float(y)])
        res["sides"][s] = {"pixels": int(sel.sum()), "components": int(n)}
        if pts:
            res["ops"].append({"type": "mask.edit", "layer": f"shin_{s}", "mode": "subtract",
                               "transfer_to": f"foot_{s}",
                               "shape": {"type": "stroke", "points": pts, "radius": 1.5, "hardness": 1.0}})
    return res


def forearm_extension_candidate(eng) -> dict[str, Any]:
    """The dotted arc over the original hand's palm when the wrist bends is the outline of Phase 2's generated forearm
    extension under the hand (a flat fill that continued the cuff and its outline; generated, pending review). The
    candidate is each forearm layer without its generated pixels beyond the measured cuff edge, i.e. the review
    decision "reject the forearm-under-hand extension" (the Phase 3.8 ``cuff_cover`` already drops them for store
    hands). Original pixels are kept."""
    from .handstore import reference_wrist  # noqa: PLC0415

    res: dict[str, Any] = {"sides": {}, "variants": {}}
    H, W = eng.source.shape[:2]
    yy, xx = np.mgrid[:H, :W]
    for s in ("r", "l"):
        lay = eng.layers.get(f"forearm_{s}")
        gen = (eng.generated or {}).get(f"forearm_{s}")
        if lay is None or gen is None or not gen.any():
            continue
        ref = reference_wrist(eng.sk, eng.layers, s, None, eng.source)
        Wj, u = np.asarray(ref["wrist_joint"], float), np.asarray(ref["axis"], float)
        beyond = (xx - Wj[0]) * u[0] + (yy - Wj[1]) * u[1] > float(ref["cuff_edge_t_px"]) + 1.0
        drop = gen & beyond
        v = lay.copy()
        v[drop] = 0
        res["variants"][f"forearm_{s}"] = v
        res["sides"][s] = {"generated_px": int(gen.sum()), "removed_beyond_cuff_px": int(drop.sum()),
                           "review": f"reject the generated forearm_{s} extension under the original hand"}
    return res


def skin_candidates(hand_grid: str | Path) -> dict[str, Any]:
    d = Path(hand_grid) / "candidates"
    files = sorted(p.name for p in d.glob("*__skin_matched.png")) if d.is_dir() else []
    return {"dir": str(d), "files": files, "count": len(files),
            "status": "candidates on disk, never imported or approved (Phase 3.8 hand compatibility report)"}


def detached_marks(img: np.ndarray, max_px: int = 300) -> list[dict[str, Any]]:
    """Small opaque components detached from the figure (e.g. hem marks beside the shoes)."""
    a = img[..., 3] > 40
    lab, n = ndimage.label(a, structure=np.ones((3, 3)))
    sizes = np.bincount(lab.ravel())
    out = []
    for i in range(1, n + 1):
        if sizes[i] < max_px:
            ys, xs = np.nonzero(lab == i)
            out.append({"px": int(sizes[i]), "x": round(float(xs.mean()), 1), "y": round(float(ys.mean()), 1)})
    return out
