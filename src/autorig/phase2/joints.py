"""JointPreparationEngine: make reconstructed parts rotation-ready.

For every joint: pivot (+ reliability), attached parent/child components,
overlap padding, front/back relation, the reconstruction extensions that
cover the joint, a range-of-motion sweep (gaps with originals only vs with
extensions, contour breaks, overlaps) and recommended binding modes.
Native Moho binding is NOT done here.
"""

from __future__ import annotations

from typing import Any

from .pose import RIG, JOINT_BONE, PoseEngine

RANGES = {"shoulder": 90, "elbow": 90, "wrist": 30, "hip": 45, "knee": 60, "ankle": 30, "neck": 20}
GAP_TOLERANCE = 25  # pixels; above this a pose shows a visible hole at the joint


def _base(joint: str) -> str:
    return joint.rsplit("_", 1)[0] if joint[-2:] in ("_r", "_l") else joint


def descendants(bone: str) -> list[str]:
    out = [bone]
    for b, (_, parent, _) in RIG.items():
        if parent == bone:
            out += descendants(b)
    return out


def prepare_joints(engine_orig: PoseEngine, engine_ext: PoseEngine, anatomy_joints: dict[str, dict],
                   order: list[str], edges: list[dict], extensions: list[dict], step: int = 15) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for joint, bone in JOINT_BONE.items():
        j = anatomy_joints.get(joint, {})
        parent = RIG[bone][1]
        child_comps = [c for b in descendants(bone) for c in RIG[b][2] if c in engine_ext.layers]
        parent_comps = [c for c in RIG[parent][2] if c in engine_ext.layers] if parent else []
        rng = RANGES[_base(joint)]
        sweep = []
        for ang in range(-rng, rng + 1, step):
            if ang == 0:
                continue
            o = engine_orig.render(f"{joint}@{ang}", {bone: ang}).diagnostics["joints"].get(joint, {})
            e = engine_ext.render(f"{joint}@{ang}", {bone: ang}).diagnostics["joints"].get(joint, {})
            sweep.append({"angle_deg": ang, "gaps_original_only": o.get("gap_pixels"),
                          "gaps_with_extensions": e.get("gap_pixels"), "dark_contour_pieces": e.get("dark_contour_pieces"),
                          "overlap_pixels": e.get("overlap_pixels")})

        def safe(key):
            ok = [abs(s["angle_deg"]) for s in sweep if (s[key] or 0) <= GAP_TOLERANCE]
            bad = [abs(s["angle_deg"]) for s in sweep if (s[key] or 0) > GAP_TOLERANCE]
            lim = min(bad) - step if bad else rng
            return max(0, lim) if ok or not bad else 0

        rel = [e for e in edges if {e["front"], e["back"]} & set(child_comps) and {e["front"], e["back"]} & set(parent_comps)]
        exts = [x["id"] for x in extensions if x["region_id"] and any(c in x["region_id"] for c in child_comps + parent_comps)
                and (x.get("joint") == joint or x["component"] in parent_comps + child_comps[:1])]
        issues = []
        if not j.get("reliable", False):
            issues.append(f"pivot unreliable in Phase 1 ({j.get('method')}): verify before binding")
        max_gap = max((s["gaps_with_extensions"] or 0) for s in sweep) if sweep else 0
        if max_gap > GAP_TOLERANCE:
            issues.append(f"gaps up to {max_gap} px remain within ±{rng} deg even with extensions")
        b = _base(joint)
        if b in ("wrist", "ankle", "neck"):
            binding = ["rigid_layer_attachment"]
        else:
            binding = ["layer_overlap_joint"]
        if max_gap > GAP_TOLERANCE:
            binding.append("planned_vector_correction")
        if b in ("elbow", "knee"):
            binding.append("planned_deformable_mesh (bend smoothing; Smart Bone/mesh in a later phase)")
        out[joint] = {
            "pivot": j.get("position"), "pivot_reliable": bool(j.get("reliable")), "pivot_method": j.get("method"),
            "bone": bone, "parent_components": parent_comps, "child_components": child_comps,
            "overlap_padding_px": round(1.6 * engine_ext.radius.get(bone, 30.0), 1),
            "front_back": [{"front": e["front"], "back": e["back"], "confidence": e["confidence"],
                            "ambiguous": e["ambiguous"]} for e in rel],
            "draw_order_back_to_front": [c for c in order if c in parent_comps + child_comps],
            "rotation_safe_extensions": exts,
            "range_tested_deg": [-rng, rng], "step_deg": step, "sweep": sweep,
            "safe_range_deg": {"original_only": safe("gaps_original_only"), "with_extensions": safe("gaps_with_extensions")},
            "gap_tolerance_px": GAP_TOLERANCE, "recommended_binding": binding, "issues": issues,
            "status": "needs_review" if issues else "prepared_pending_review",
        }
    return out
