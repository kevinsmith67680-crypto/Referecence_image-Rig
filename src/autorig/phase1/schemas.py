"""JSON Schemas (draft 2020-12) for every Phase 1 output document.

Source of truth is this module; ``export()`` writes them to
``src/autorig/data/schemas/`` (a test keeps the files in sync).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

V = "1.0.0"
POINT = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
POINT_OR_NULL = {"oneOf": [POINT, {"type": "null"}]}
BBOX = {"oneOf": [{"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4}, {"type": "null"}]}
CONF = {"type": "number", "minimum": 0, "maximum": 1}
IMAGE = {"type": "object", "required": ["width", "height", "coordinate_system", "source_sha256"],
         "properties": {"width": {"type": "integer", "minimum": 1}, "height": {"type": "integer", "minimum": 1},
                        "coordinate_system": {"const": "pixels; origin top-left; +x right; +y down"},
                        "source_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}}
READINESS = {"enum": ["READY", "REQUIRES_RECONSTRUCTION", "REQUIRES_NEW_ARTWORK", "UNSUPPORTED", "NEEDS_REVIEW"]}


def _doc(name: str, props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": f"autorig.{name}-{V}",
            "title": f"AutoRig Studio {name} {V}", "type": "object",
            "required": ["schema", "schema_version", *required],
            "properties": {"schema": {"const": f"autorig.{name}"}, "schema_version": {"const": V}, **props}}


JOINT = {"type": "object",
         "required": ["name", "position", "confidence", "reliable", "method", "flags", "occluded_by", "visible", "parent"],
         "properties": {"name": {"type": "string"}, "position": POINT_OR_NULL, "confidence": CONF,
                        "reliable": {"type": "boolean"}, "method": {"type": "string"},
                        "flags": {"type": "array", "items": {"type": "string"}},
                        "sources": {"type": "array", "items": {"type": "string"}},
                        "occluded_by": {"type": "array", "items": {"type": "string"}},
                        "visible": {"type": ["boolean", "null"]}, "parent": {"type": ["string", "null"]}}}

ANATOMY = _doc("anatomy", {
    "image": IMAGE,
    "side_convention": {"type": "string"},
    "facing_assumption": {"enum": ["front"]},
    "midline_x": {"type": "number"},
    "joints": {"type": "object", "additionalProperties": JOINT, "minProperties": 20},
    "bones": {"type": "array", "items": {"type": "object", "required": ["name", "from", "to", "length_px"],
                                         "properties": {"length_px": {"type": ["number", "null"]}}}},
    "parts": {"type": "object", "additionalProperties": {
        "type": "object", "required": ["pixels", "bbox_xyxy", "empty", "method", "mask", "pivot"],
        "properties": {"pixels": {"type": "integer", "minimum": 0}, "bbox_xyxy": BBOX, "empty": {"type": "boolean"},
                       "pivot": {"type": "object", "required": ["position", "joint", "status"]}}}},
    "occlusion": {"type": "object", "required": ["joints_occluded", "instance_overlaps"]},
    "validation": {"type": "object", "required": ["issues", "unreliable_joints"]},
    "segmentation": {"type": "object", "required": ["accepted_instances", "rejected_instances", "run"]},
    "estimators": {"type": "array", "items": {"type": "string"}, "minItems": 1},
}, ["image", "side_convention", "joints", "bones", "parts", "occlusion", "validation", "segmentation", "estimators"])

FACE = _doc("face", {
    "image": IMAGE,
    "head_bbox_xyxy": BBOX,
    "midline_x": {"type": ["number", "null"]},
    "features": {"type": "object", "additionalProperties": {
        "type": "object", "required": ["label", "method", "score", "pixels", "bbox_xyxy", "centroid", "mask"],
        "properties": {"score": CONF, "pixels": {"type": "integer", "minimum": 1}, "centroid": POINT}}},
    "rejected_candidates": {"type": "array", "items": {"type": "object", "required": ["label", "reason"]}},
    "landmarks": {"type": "object"},
    "eye_centers": {"type": "object"}, "eyebrow_positions": {"type": "object"}, "mouth": {"type": "object"},
    "jaw_pivot": {"type": ["object", "null"]},
    "visibility": {"type": "object", "additionalProperties": {"type": "object", "required": ["status"]}},
    "segmentation_confidence": {"type": "object", "additionalProperties": CONF},
    "missing_artwork": {"type": "array", "items": {"type": "object", "required": ["artwork", "needed_for", "reason"]}},
    "segmentation": {"type": "object"},
}, ["image", "head_bbox_xyxy", "features", "landmarks", "eye_centers", "eyebrow_positions", "mouth", "jaw_pivot",
    "visibility", "segmentation_confidence", "missing_artwork"])

READINESS_DOC = _doc("live_readiness", {
    "capabilities": {"type": "object", "required": ["blinking", "eyebrow_movement", "mouth_opening", "lip_sync",
                                                     "eye_direction", "head_tilting", "head_turning",
                                                     "facial_expressions"],
                     "additionalProperties": {"type": "object", "required": ["status", "reasons", "required_artwork"],
                                              "properties": {"status": READINESS}}},
    "summary": {"type": "object"}, "policy": {"type": "string"},
}, ["capabilities", "summary", "policy"])

CONTROL = {"type": "object",
           "required": ["id", "description", "value_range", "neutral", "animation_target_type",
                        "artwork_dependencies", "missing_artwork", "readiness", "confidence"],
           "properties": {"id": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
                          "value_range": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                          "neutral": {"type": "number"},
                          "animation_target_type": {"enum": ["smart_bone", "action", "bone_rotation", "switch_layer"]},
                          "artwork_dependencies": {"type": "array", "items": {"type": "string"}},
                          "missing_artwork": {"type": "array", "items": {"type": "string"}},
                          "readiness": READINESS, "confidence": CONF}}
LIVE_CONTROLS = _doc("live_controls", {
    "spec_version": {"const": "1.0.0"},
    "controls": {"type": "array", "items": CONTROL, "minItems": 20},
    "moho_showtime": {"type": "object", "required": ["verified_mappings", "proposed_mappings", "verification_status"],
                      "properties": {"verified_mappings": {"type": "array"},
                                     "proposed_mappings": {"type": "array", "items": {
                                         "type": "object", "required": ["control", "device", "status"],
                                         "properties": {"status": {"const": "proposed_unverified"}}}}}},
    "adapter_design": {"type": "object", "required": ["stages", "implementation_status"]},
}, ["spec_version", "controls", "moho_showtime", "adapter_design"])

LAYER = {"type": "object",
         "required": ["id", "kind", "role", "image", "mask", "bbox_xyxy", "z_index", "pixels", "pivot", "bone",
                      "provenance", "transform", "facial_control_dependencies", "vector_replacement", "status"],
         "properties": {
             "kind": {"const": "extracted_original_pixels"},
             "role": {"enum": ["body_part", "facial_feature", "head_base"]},
             "z_index": {"type": "integer"}, "pixels": {"type": "integer", "minimum": 0}, "bbox_xyxy": BBOX,
             "pivot": {"type": "object", "required": ["position", "status", "source"],
                       "properties": {"position": POINT_OR_NULL, "status": {"enum": ["proposed", "missing"]}}},
             "bone": {"type": "object", "required": ["name", "parent_bone"]},
             "transform": {"type": "object", "required": ["scale", "offset"],
                           "properties": {"scale": {"const": 1.0}, "offset": {"const": [0, 0]}}},
             "vector_replacement": {"type": ["object", "null"]},
             "status": {"enum": ["extracted", "empty"]}}}
LAYERS = _doc("hybrid_artwork", {
    "contract_version": {"const": V},
    "coordinate_system": {"type": "object", "required": ["units", "origin", "width", "height"]},
    "original_bitmap": {"type": "object", "required": ["path", "sha256", "role"],
                        "properties": {"role": {"const": "reference_only_never_modified"}}},
    "layers": {"type": "array", "items": LAYER, "minItems": 1},
    "masks": {"type": "array", "items": {"type": "object", "required": ["id", "path", "kind", "pixels"]}},
    "generated_artwork": {"type": "array", "items": {"type": "object", "required": ["id", "kind", "replaces_or_extends",
                                                                                     "provenance", "approved"]}},
    "vector_corrections": {"type": "array", "items": {"type": "object", "required": ["id", "kind", "path", "target_layer",
                                                                                      "editable_in_moho"]}},
    "categories": {"type": "object"},
}, ["contract_version", "coordinate_system", "original_bitmap", "layers", "masks", "generated_artwork",
    "vector_corrections", "categories"])

METADATA = _doc("character_project", {
    "autorig_version": {"type": "string"}, "created_at": {"type": "string"},
    "source": {"type": "object", "required": ["sha256", "width", "height", "project_path", "byte_identical_copy"]},
    "source_unchanged_after_processing": {"type": "boolean"},
    "pipeline": {"type": "object", "required": ["steps", "config"]},
    "segmentation_runs": {"type": "array", "items": {"type": "object", "required": ["provider", "model", "version"]}},
    "cost": {"type": "object", "required": ["this_run_estimated_usd", "basis"]},
    "fidelity": {"type": "object", "required": ["coverage_ratio", "overlap_pixels", "max_abs_error_inside_layers"]},
    "outputs": {"type": "array", "items": {"type": "object", "required": ["path", "sha256", "bytes"]}},
    "schema_validation": {"type": "object"},
    "warnings": {"type": "array", "items": {"type": "string"}},
    "limitations": {"type": "array", "items": {"type": "string"}},
}, ["autorig_version", "created_at", "source", "source_unchanged_after_processing", "pipeline",
    "segmentation_runs", "cost", "fidelity", "outputs", "warnings", "limitations"])

SCHEMAS: dict[str, dict[str, Any]] = {
    "anatomy": ANATOMY, "face": FACE, "live_readiness": READINESS_DOC, "live_controls": LIVE_CONTROLS,
    "hybrid_artwork": LAYERS, "character_project": METADATA,
}


def validate(name: str, doc: Any) -> list[str]:
    v = jsonschema.Draft202012Validator(SCHEMAS[name])
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in sorted(v.iter_errors(doc), key=lambda e: list(e.absolute_path))]


def export(out_dir: str | Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, schema in SCHEMAS.items():
        p = out_dir / f"{name}.schema.json"
        p.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        paths.append(p)
    return paths
