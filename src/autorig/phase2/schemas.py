"""JSON Schemas for Phase 2 documents (draft 2020-12)."""

from __future__ import annotations

from typing import Any

import jsonschema

from ..phase1.schemas import BBOX, CONF, POINT, POINT_OR_NULL, READINESS, _doc

V = "1.0.0"


def doc(name, props, req):
    d = _doc(name, props, req)
    d["$id"] = f"autorig.{name}-{V}"
    return d


EDGE = {"type": "object", "required": ["front", "back", "confidence", "method", "ambiguous", "evidence"],
        "properties": {"confidence": CONF, "ambiguous": {"type": "boolean"}}}
REGION = {"type": "object",
          "required": ["id", "owner", "occluded_by", "mask", "pixels", "bbox_xyxy", "exposed_by", "method",
                       "confidence", "ambiguous", "strategy"],
          "properties": {"pixels": {"type": "integer", "minimum": 1}, "confidence": CONF, "bbox_xyxy": BBOX,
                         "strategy": {"enum": ["deterministic_flat_fill", "generative", "generative_required_no_provider",
                                               "generative_required_region_too_large", "generative_failed",
                                               "not_needed_for_planned_motion", "not_applicable", "pending"]}}}
OCCLUSION = doc("occlusion", {
    "image": {"type": "object"},
    "graph": {"type": "object", "required": ["nodes", "edges", "z_order_back_to_front"],
              "properties": {"edges": {"type": "array", "items": EDGE}}},
    "regions": {"type": "array", "items": REGION},
    "new_artwork_required": {"type": "array", "items": {"type": "object", "required": ["asset", "reason"]}},
    "flags": {"type": "array", "items": {"type": "string"}},
    "overrides_applied": {"type": "object"},
}, ["image", "graph", "regions", "new_artwork_required", "flags", "overrides_applied"])

JOINT = {"type": "object", "required": ["pivot", "pivot_reliable", "child_components", "overlap_padding_px", "sweep",
                                        "safe_range_deg", "recommended_binding", "status"],
         "properties": {"pivot": POINT_OR_NULL, "recommended_binding": {"type": "array", "minItems": 1}}}
JOINT_PREP = doc("joint_preparation", {"joints": {"type": "object", "additionalProperties": JOINT, "minProperties": 13},
                                       "notes": {"type": "array"}}, ["joints"])

CTRL = {"type": "object", "required": ["required_image_layers", "required_switch_drawings", "pivot_or_deformation_region",
                                       "safe_range", "artwork_readiness", "missing_assets", "user_approval_status"],
        "properties": {"artwork_readiness": READINESS,
                       "user_approval_status": {"enum": ["not_reviewed", "approved", "rejected"]}}}
LIVE_MANIFEST = doc("live_artwork_manifest", {
    "controls": {"type": "object", "additionalProperties": CTRL, "minProperties": 15},
    "switch_drawing_slots": {"type": "array"}, "software_mappings": {"type": "string"}},
    ["controls", "switch_drawing_slots", "software_mappings"])

ART_LAYER = {"type": "object", "required": ["id", "category", "image", "z_index", "bone", "pivot", "provenance"],
             "properties": {"category": {"enum": ["A_original"]}, "pivot": POINT_OR_NULL}}
ART_EXT = {"type": "object", "required": ["id", "component", "category", "image", "provenance_mask", "method", "status",
                                          "pixels", "provenance"],
           "properties": {"category": {"const": "B_generated"},
                          "status": {"enum": ["pending_review", "approved", "rejected", "superseded", "alternative"]}}}
ARTWORK = doc("artwork_manifest", {
    "contract_version": {"const": "2.0.0"},
    "coordinate_system": {"type": "object", "required": ["width", "height", "origin"]},
    "original_bitmap": {"type": "object", "required": ["path", "sha256"]},
    "layers": {"type": "array", "items": ART_LAYER, "minItems": 1},
    "extensions": {"type": "array", "items": ART_EXT},
    "corrections": {"type": "array"}, "vectors": {"type": "array"},
    "masks": {"type": "object", "required": ["source", "reconstruction", "provenance"]},
}, ["contract_version", "coordinate_system", "original_bitmap", "layers", "extensions", "corrections", "vectors", "masks"])

QUALITY = doc("reconstruction_quality", {
    "original_pixel_preservation": {"type": "object", "required": ["source_unchanged", "originals_verified",
                                                                    "neutral_originals_exact",
                                                                    "generated_pixels_visible_in_neutral"]},
    "neutral_similarity": {"type": "object"}, "alpha_coverage": {"type": "object"},
    "gaps": {"type": "object"}, "joint_seams": {"type": "object"}, "layer_alignment": {"type": "object"},
    "reconstruction_size": {"type": "object"}, "provenance": {"type": "object"},
    "outline_discontinuities": {"type": "object"}, "pose_stability": {"type": "object"},
    "flags_for_human_review": {"type": "array"}, "images": {"type": "object"},
    "synthetic_ground_truth": {"type": ["object", "null"]},
}, ["original_pixel_preservation", "gaps", "joint_seams", "layer_alignment", "reconstruction_size", "provenance",
    "flags_for_human_review", "images"])

PACKAGE = doc("character_package", {
    "phase": {"const": 2}, "source": {"type": "object", "required": ["sha256", "byte_identical_copy"]},
    "phase1_project": {"type": "object"}, "providers": {"type": "object"}, "cost": {"type": "object"},
    "verification": {"type": "object", "required": ["store_problems", "source_unchanged"]},
    "evidence_level": {"type": "object", "required": ["real_reference_evaluated", "generative_inpainting_tested"]},
    "schema_validation": {"type": "object"}, "outputs": {"type": "array"}, "warnings": {"type": "array"},
}, ["phase", "source", "phase1_project", "providers", "cost", "verification", "evidence_level", "outputs"])

SCHEMAS = {"occlusion": OCCLUSION, "joint_preparation": JOINT_PREP, "live_artwork_manifest": LIVE_MANIFEST,
           "artwork_manifest": ARTWORK, "reconstruction_quality": QUALITY, "character_package": PACKAGE}


def validate(name: str, d: Any) -> list[str]:
    v = jsonschema.Draft202012Validator(SCHEMAS[name])
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in sorted(v.iter_errors(d), key=lambda e: list(e.absolute_path))]
