"""GarmentCorrectionController: angle-range corrective artwork for garments.

Subtle joint movement is handled by the continuous deformation (weight mesh + rigid segments). Large movement
needs drawings: an armpit gusset for a raised arm, an inner-elbow fold, a bent knee, front-view hip flexion,
seated trousers, a 3/4 torso for leaning toward or away from the viewer, and a running knee drive. Each
correction stores the following:

* ``trigger``: the measured joint or pose reading, and its ``angle_range`` ([continuous_max, full]). Below
  ``continuous_max`` the deformation alone is used. Between ``on`` and ``full`` a drawing blends in.
* ``asset``: the corrective drawing it needs, its ``attachment`` (bone and pivot) and ``layer_order``.
* ``method``: ``blend`` (crossfade over the range) or ``switch`` (replaces a drawing).
* ``approval``: the asset's state. Corrective artwork is never approved in code; pending drawings are only
  previewed with ``allow_pending``.
* ``missing_dependencies``: everything the correction still needs.

It extends the Phase 3.2 ``CorrectionSwitchController`` (same triggers, approvals and hysteresis; there is no
second switch engine). The blend weight is eased (smoothstep over the range, so nothing pops). The on/off state
keeps the Phase 3.2 hysteresis, so a joint hovering at the threshold does not flicker.
"""

from __future__ import annotations

from typing import Any

from ..motion.switches import CorrectionSwitchController
from ..phase2.skeleton import SIDES, Skeleton, smoothstep

# extra garment metadata for the Phase 3.2 triggers: continuous_max, full, method, layer_order, garment parts
GARMENT_META: dict[str, dict[str, Any]] = {
    "shoulder_raise": {"continuous_max": 110.0, "full": 140.0, "method": "switch",
                       "layer_order": {"above": "torso", "below": "upper_arm_{s}"},
                       "parts": ["sleeve_upper_{s}", "blazer_body", "sleeve_cap_{s}"], "region": "armpit_{s}",
                       "why": "the joint patch cannot stretch the sleeve into the armpit (stretch limit); the "
                              "wedge needs an armpit gusset drawing"},
    "elbow_deep": {"continuous_max": 95.0, "full": 135.0, "method": "blend",
                   "layer_order": {"above": "forearm_{s}"}, "parts": ["elbow_{s}"], "region": "elbow_{s}",
                   "why": "beyond ~125 deg the outer elbow patch is clipped and the inner fold is a hard overlap"},
    "sleeve_crease": {"continuous_max": 60.0, "full": 100.0, "method": "blend",
                      "layer_order": {"above": "forearm_{s}"}, "parts": ["elbow_{s}"], "region": "elbow_{s}",
                      "why": "fold lines at the inner elbow (optional)"},
    "wrist_rotation": {"continuous_max": 40.0, "full": 70.0, "method": "switch",
                       "layer_order": {"replaces": "hand_{s}"}, "parts": ["hand_{s}"], "region": "hand_{s}",
                       "why": "a turned wrist needs a different hand drawing (the cuff stays on the forearm)"},
    "hip_flexion": {"continuous_max": 35.0, "full": 70.0, "method": "switch",
                    "layer_order": {"replaces": "thigh_{s}"}, "parts": ["trouser_thigh_{s}", "trouser_hip_{s}"],
                    "region": "hip_{s}", "why": "a thigh raised toward the viewer needs a foreshortened drawing"},
    "knee_deep": {"continuous_max": 40.0, "full": 90.0, "method": "blend",
                  "layer_order": {"above": "shin_{s}"}, "parts": ["knee_{s}"], "region": "knee_{s}",
                  "why": "a deep knee bend folds the trousers (inner crease, outer stretch)"},
    "trouser_fold": {"continuous_max": 25.0, "full": 50.0, "method": "blend",
                     "layer_order": {"above": "shin_{s}"}, "parts": ["knee_{s}"], "region": "knee_{s}",
                     "why": "fold lines at the knee (optional)"},
}

# per-side garment triggers that Phase 3.2 did not have
SIDE_TRIGGERS: list[dict[str, Any]] = [
    {"id": "shoulder_lift_collar_{s}", "title": "raised shoulder: collar, lapel and shoulder drawing",
     "bone": "clavicle_{s}", "measure": "clavicle_{s}", "on": 15.0, "hysteresis": 2.0,
     "asset": "raised_shoulder_collar_{s}", "priority": 2, "compatible": ["shoulder_raise_{s}"],
     "binding": "clavicle_{s}", "continuous_max": 12.0, "full": 30.0, "method": "switch",
     "layer_order": {"replaces": "torso (shoulder and lapel region)"},
     "parts": ["lapels", "shirt_collar", "sweater", "blazer_shoulder_{s}", "sleeve_cap_{s}", "blazer_body"],
     "region": "collar_{s}",
     "why": "a shoulder raised beyond ~15 deg (scapulohumeral rhythm of an overhead arm) compresses the lapel tip "
            "and collar wing next to the sternoclavicular pivot; the drawn shoulder line must change"},
    {"id": "hem_on_shoe_{s}", "title": "trouser hem over a turned shoe (shoe top under the hem)",
     "bone": "foot_{s}", "measure": "ankle_{s}", "on": 6.0, "hysteresis": 1.5,
     "asset": "foot_extension_under_hem_{s}", "priority": 3, "compatible": [], "binding": "foot_{s}",
     "continuous_max": 4.0, "full": 15.0, "method": "reconstruction", "layer_order": {"below": "shin_{s}"},
     "parts": ["trouser_cuff_{s}"], "region": "ankle_{s}",
     "why": "the hem follows the shoe only partly (a full follow shears it); the corner that lifts shows "
            "background because the shoe's top under the hem was never drawn (a foot-layer extension, reviewed)"},
]

# pose-level garment corrections (readings come from the pose spec / intent)
POSE_TRIGGERS: list[dict[str, Any]] = [
    {"id": "torso_lean_sagittal", "title": "torso lean toward/away from the viewer (3/4 torso drawing)",
     "bone": "chest", "measure": "torso_lean_sagittal", "on": 0.5, "hysteresis": 0.0,
     "asset": "torso_lean_3q", "priority": 1, "compatible": [], "binding": "chest", "required_above": 0.5,
     "continuous_max": 0.0, "full": 1.0, "method": "switch", "layer_order": {"replaces": "torso"},
     "parts": ["blazer_body", "lapels", "sweater", "shirt_collar"], "region": "torso",
     "why": "a front-view torso cannot lean toward or away from the viewer without a 3/4 drawing"},
    {"id": "sitting_trousers", "title": "seated trousers (thighs toward the viewer)", "bone": "pelvis",
     "measure": "sitting", "on": 0.5, "hysteresis": 0.0, "asset": "trousers_seated", "priority": 1,
     "compatible": [], "binding": "pelvis", "required_above": 0.5, "continuous_max": 0.0, "full": 1.0,
     "method": "switch", "layer_order": {"replaces": "thigh_r,thigh_l"},
     "parts": ["trouser_thigh_r", "trouser_thigh_l", "blazer_hem"], "region": "lap",
     "why": "seated thighs point at the viewer; the standing trouser drawing cannot be bent into them"},
    {"id": "running_knee_drive", "title": "running knee drive (foreshortened thigh)", "bone": "pelvis",
     "measure": "running", "on": 0.5, "hysteresis": 0.0, "asset": "knee_drive", "priority": 3,
     "compatible": [], "binding": "thigh", "continuous_max": 0.0, "full": 1.0, "method": "switch",
     "layer_order": {"replaces": "thigh_{swing}"}, "parts": ["trouser_thigh_r", "trouser_thigh_l"],
     "region": "thighs", "why": "a strong knee drive is a depth motion (optional; the front-view gait is shown)"},
]


class GarmentCorrectionController(CorrectionSwitchController):
    def __init__(self, sk: Skeleton, approvals: dict[str, Any] | None = None,
                 corrective_ids: list[str] | None = None):
        super().__init__(sk, approvals, corrective_ids)
        for c in self.triggers:
            base = c["id"].rsplit("_", 1)[0]
            meta = GARMENT_META.get(base, {})
            s = c["side"]
            for k, v in meta.items():
                if isinstance(v, str):
                    c[k] = v.format(s=s)
                elif isinstance(v, list):
                    c[k] = [x.format(s=s) for x in v]
                elif isinstance(v, dict):
                    c[k] = {kk: vv.format(s=s) for kk, vv in v.items()}
                else:
                    c[k] = v
        for t in SIDE_TRIGGERS:
            for s in SIDES:
                c = {k: (v.format(s=s) if isinstance(v, str) else [x.format(s=s) for x in v] if isinstance(v, list)
                         else {kk: vv.format(s=s) for kk, vv in v.items()} if isinstance(v, dict) else v)
                     for k, v in t.items()}
                c["side"] = s
                c["pivot"] = [float(x) for x in sk.bone(c["bone"]).head]
                self.triggers.append(c)
        for t in POSE_TRIGGERS:
            c = dict(t)
            c["side"] = None
            c["pivot"] = [float(x) for x in sk.bone(c["bone"]).head] if c["bone"] in sk.idx else None
            self.triggers.append(c)

    def evaluate(self, readings: dict[str, float], allow_pending: bool = False, stateful: bool = True,
                 overrides: dict[str, bool] | None = None) -> list[dict[str, Any]]:
        out = super().evaluate(readings, allow_pending, stateful, overrides)
        for rec, c in zip(out, self.triggers):
            val = readings.get(c["measure"], 0.0)
            cont, full = c.get("continuous_max", c["on"]), c.get("full", c["on"] + 20.0)
            w = float(smoothstep(c["on"] - c["hysteresis"], max(full, c["on"] + 1e-3), val)) if rec["on"] else 0.0
            st = rec["asset_state"]
            if val < c["on"] and not rec["on"]:
                handling = "continuous deformation"
            elif rec["applied"]:
                handling = f"drawing ({c.get('method', 'switch')}, weight {w:.2f})"
            else:
                handling = "deformation fallback: drawing missing" if st == "missing" else \
                    "deformation fallback: drawing pending review (preview with allow_pending)"
            deps = []
            if st == "missing":
                deps.append(f"corrective:{c['asset']}")
            elif st == "pending_review":
                deps.append(f"approval of corrective:{c['asset']}")
            rec.update({"trigger": c["measure"], "angle_range": [cont, full], "continuous_max": cont,
                        "blend_weight": round(w, 3) if rec["applied"] else 0.0, "target_blend": round(w, 3),
                        "method": c.get("method", "switch"),
                        "attachment": {"bone": c.get("bone"), "pivot": c.get("pivot")},
                        "layer_order": c.get("layer_order", {}), "garment_parts": c.get("parts", []),
                        "region": c.get("region"), "approval": st, "missing_dependencies": deps,
                        "handling": handling, "why": c.get("why", "")})
            if rec["side"] is None:
                rec["range"] = [c["on"], full]
        return out

    def to_json(self) -> list[dict[str, Any]]:
        out = []
        for c in self.triggers:
            out.append({k: v for k, v in c.items()} | {"asset_state": self.asset_state(c)})
        return out


def pose_readings(spec, it, readings: dict[str, float], pose=None, sk=None) -> dict[str, float]:
    """Pose-level readings for the garment triggers (from the preset spec, the intent and the pose): the shoe's
    rotation relative to the shin (``ankle_{s}``), and sagittal lean / sitting / running flags."""
    out = dict(readings)
    if pose is not None and sk is not None:
        for s in SIDES:
            if f"clavicle_{s}" in sk.idx:
                sg = 1.0 if s == "r" else -1.0
                out[f"clavicle_{s}"] = sg * float(pose.theta[sk.idx[f"clavicle_{s}"]])
            if f"foot_{s}" in sk.idx:
                out[f"ankle_{s}"] = abs(float(((pose.theta[sk.idx[f"foot_{s}"]] + 180) % 360) - 180))
        # frontal torso bend (spine + chest, deg; + = toward the character's right side as drawn)
        out["torso_bend"] = float(sum(pose.theta[sk.idx[b]] for b in ("spine", "chest") if b in sk.idx))
    name = getattr(spec, "name", "")
    out["torso_lean_sagittal"] = 1.0 if name in ("leaning_forward", "leaning_backward") else 0.0
    out["sitting"] = 1.0 if name == "sitting" else 0.0
    out["running"] = 1.0 if name == "running" else 0.0
    return out


def asset_state_for_side(ctrl: GarmentCorrectionController, asset: str, side: str | None) -> str:
    for c in ctrl.triggers:
        if c["asset"] == asset and c.get("side") == side:
            return ctrl.asset_state(c)
    return "missing"


__all__ = ["GarmentCorrectionController", "GARMENT_META", "POSE_TRIGGERS", "pose_readings", "SIDES"]
