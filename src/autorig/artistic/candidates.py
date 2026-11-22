"""Phase 3.5 pose candidates: several interpretations of one action, each rendered through the one motion pipeline and
assessed separately for artwork feasibility, silhouette readability, balance, deformation risk and required artwork.

The recommendation is an explicit ordering, not a single weighted score:
1. feasibility class: SUPPORTED > SUPPORTED_WITH_CORRECTIONS > REQUIRES_ALTERNATIVE_ARTWORK > REQUIRES_NEW_VIEW >
   UNSUPPORTED;
2. no high-severity silhouette warning;
3. fewer silhouette warnings (weighted by severity);
4. lower deformation risk (open gap points + clipped joint-patch pixels).

The reason is recorded with the recommendation. A person's choice (Artistic Review) always overrides it, and chosen
poses can be saved as reusable templates.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from .feasibility import CLASSES, ArtworkFeasibilityEngine
from .poses import ArtisticPosePlanner, action_library, apply_constraints, variant
from .silhouette import SilhouetteQualityEvaluator


def render_pose(eng, vis, ap, preview_pending: bool = True, corrective_mode: str = "all", evaluate: bool = False,
                planner: ArtisticPosePlanner | None = None, extra: dict[str, Any] | None = None,
                layer_order=None) -> dict[str, Any]:
    from .layer_order import DynamicLayerOrderController  # noqa: PLC0415

    planner = planner or ArtisticPosePlanner(eng.profile)
    rq = planner.request(ap, preview_pending)
    name = rq.pop("name")
    rq["layer_order"] = layer_order if layer_order is not None else DynamicLayerOrderController()
    t0 = time.perf_counter()
    r = eng.pose(name, visible=vis, evaluate_garments=evaluate, corrective_mode=corrective_mode, **rq, **(extra or {}))
    r["wall_s"] = round(time.perf_counter() - t0, 3)
    return r


def assess(eng, ap, r: dict[str, Any], feas_engine: ArtworkFeasibilityEngine | None = None) -> dict[str, Any]:
    sil = SilhouetteQualityEvaluator().evaluate(r, ap.action, {f"gesture_{s}": g.kind for s, g in ap.arms.items()})
    fz = (feas_engine or ArtworkFeasibilityEngine()).classify(ap, r)
    info = r["solve"]
    com = info.get("com")
    sup = (info.get("support") or {}).get("x_range")
    balance = None
    if com is not None and sup:
        lo, hi = sup
        balance = round(float(max(lo - com[0], com[0] - hi, 0.0)), 1)
    R = r.get("render") or {}
    risk = int(len((r.get("tears") or {}).get("exposed_gap", []))) + int(sum((R.get("clipped_uncovered") or {}).values()) // 25)
    return {"action": ap.action, "variant": ap.variant, "feasibility": fz, "silhouette": {
        k: v for k, v in sil.items() if k != "silhouette"}, "silhouette_image": sil["silhouette"],
        "com_outside_support_px": balance, "deformation_risk": risk,
        "hands": {s: r["hands"][s]["drawing"] for s in r["hands"]},
        "pending_hands": [s for s in r["hands"] if r["hands"][s].get("pending_review")],
        "wall_s": r.get("wall_s"), "human_review": "pending"}


def rank_key(a: dict[str, Any]) -> tuple:
    sev = {"high": 3, "medium": 2, "low": 1}
    w = a["silhouette"]["warnings"]
    return (CLASSES.index(a["feasibility"]["class"]), any(x["severity"] == "high" for x in w),
            sum(sev[x["severity"]] for x in w), a["deformation_risk"])


class PoseCandidateGenerator:
    def __init__(self, eng, vis, constraints: list[dict[str, Any]] | None = None):
        self.eng, self.vis = eng, vis
        self.planner = ArtisticPosePlanner(eng.profile)
        self.lib = action_library()
        self.constraints = constraints or []
        self.feas = ArtworkFeasibilityEngine()

    def candidates(self, action: str, variants: list[str] | None = None, keep_renders: bool = True
                   ) -> dict[str, Any]:
        names = variants or list(self.lib[action]["variants"])
        out = []
        for v in names:
            ap = apply_constraints(variant(action, v, self.lib), self.constraints)
            r = render_pose(self.eng, self.vis, ap, planner=self.planner)
            a = assess(self.eng, ap, r, self.feas)
            a["pose"] = ap.to_json()
            if keep_renders:
                a["render"] = r
            out.append(a)
        order = sorted(range(len(out)), key=lambda i: rank_key(out[i]))
        best = out[order[0]]
        why = (f"{best['variant']}: {best['feasibility']['class']}, "
               f"{len(best['silhouette']['warnings'])} silhouette warning(s), deformation risk {best['deformation_risk']}")
        return {"action": action, "candidates": out, "ranking": [out[i]["variant"] for i in order],
                "recommended": best["variant"], "reason": why,
                "note": "a recommendation, not a verdict: the artist's choice overrides it (artistic_review_required)"}


def symmetric_penalty(sil_metrics: dict[str, Any]) -> float:
    return float(sil_metrics.get("symmetry_iou") or 0.0)


__all__ = ["PoseCandidateGenerator", "render_pose", "assess", "rank_key", "symmetric_penalty", "np"]
