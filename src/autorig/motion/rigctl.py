"""Rig controls spec ``autorig.rig_controls`` v1: IK chains, targets, poles, constraints, FK/IK state, foot
contacts, switch groups and variants, correction triggers, alignment offsets, missing artwork and the semantic
live action map (Phase 3.2).

The spec is engine-side and Moho-independent. The ``moho`` blocks only say what each control is *proposed* to
become in Moho (bone targets, Switch Layers, Smart Bone actions); all of them stay ``proposed_unverified`` until
Moho runs (Phase 0 is not verified natively). Live inputs are semantic actions; no Moho Live Mode input name is
written here, because none has been discovered yet (see docs/live-models.md).
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

import jsonschema

from ..phase2.skeleton import SIDES
from .ik import CONTACT_STATES, LAT
from .switches import HAND_POSES, MOUTH_SHAPES

SCHEMA = "autorig.rig_controls"
VERSION = "1.0.0"
LIMBS = ("arm_r", "arm_l", "leg_r", "leg_l")
IK_MODES = ("fk", "ik")
PROPOSED = "proposed_unverified"


def default_ik_state() -> dict[str, Any]:
    """IK state of an editor project (schema v2). Everything off: the planner drives the limbs."""
    st: dict[str, Any] = {}
    for limb in LIMBS:
        st[limb] = {"enabled": False, "mode": "fk", "target": None, "pole": None, "bend": None,
                    "lock": limb.startswith("leg"), "max_flex": None, "torso_follow": False, "fk": None,
                    "end_angle": None}  # wrist / foot world orientation (deg from rest); None = planner
    st["pelvis_offset"] = [0.0, 0.0]
    return st


def check_ik_fields(limb: str, f: dict[str, Any]) -> list[str]:
    errs = []
    if limb not in LIMBS and limb != "pelvis":
        return [f"unknown limb {limb}"]
    if limb == "pelvis":
        po = f.get("pelvis_offset")
        if po is None or len(po) != 2 or not all(isinstance(v, (int, float)) for v in po):
            errs.append("pelvis_offset must be [dx, dy]")
        return errs
    allowed = {"enabled", "mode", "target", "pole", "bend", "lock", "max_flex", "torso_follow", "fk", "end_angle"}
    bad = set(f) - allowed
    if bad:
        errs.append(f"fields not editable: {sorted(bad)}")
    if "mode" in f and f["mode"] not in IK_MODES:
        errs.append("mode must be fk or ik")
    for k in ("target", "pole"):
        if k in f and f[k] is not None and (len(f[k]) != 2 or not all(isinstance(v, (int, float)) for v in f[k])):
            errs.append(f"{k} must be [x, y] or null")
    if "bend" in f and f["bend"] not in (None, 1, -1):
        errs.append("bend must be 1, -1 or null")
    if "end_angle" in f and f["end_angle"] is not None and not -180 <= float(f["end_angle"]) <= 180:
        errs.append("end_angle must be in [-180, 180] deg")
    if "max_flex" in f and f["max_flex"] is not None and not 0 < float(f["max_flex"]) <= 179:
        errs.append("max_flex must be in (0, 179]")
    if "fk" in f and f["fk"] is not None:
        if limb.startswith("leg"):
            errs.append("legs have no FK angles (FK legs follow the planner)")
        elif set(f["fk"]) - {"abduction", "flexion"}:
            errs.append("fk takes abduction and flexion")
    return errs


def group_variants(group: str) -> tuple[str, ...] | None:
    """Static variant ids of a switch group id (validation without the package)."""
    if group in ("hand_r", "hand_l"):
        return tuple(HAND_POSES)
    if group.startswith("corr_"):
        return ("off", "on")
    if group == "face_mouth":
        return MOUTH_SHAPES
    return None


# -- live action map (semantic; body and face channels separate) --------------------------------------------
LIVE_ACTIONS: dict[str, dict[str, Any]] = {
    **{f"HAND_{'RIGHT' if s == 'r' else 'LEFT'}_{n.upper()}": {"channel": "body", "kind": "switch",
                                                               "group": f"hand_{s}", "variant": p}
       for s in SIDES for n, p in (("point", "pointing"), ("fist", "fist"), ("open", "open_palm"),
                                   ("relaxed", "relaxed"), ("thumbs_up", "thumbs_up"), ("grip", "gripping"))},
    "HAND_RIGHT_AUTO": {"channel": "body", "kind": "switch_auto", "group": "hand_r"},
    "HAND_LEFT_AUTO": {"channel": "body", "kind": "switch_auto", "group": "hand_l"},
    "ARM_RIGHT_REACH": {"channel": "body", "kind": "ik_reach", "limb": "arm_r", "reach": [0.55, 0.55],
                        "note": "value 0..1 blends from the hanging wrist to the reach target"},
    "ARM_LEFT_REACH": {"channel": "body", "kind": "ik_reach", "limb": "arm_l", "reach": [0.55, 0.55]},
    "ARM_RIGHT_RELEASE": {"channel": "body", "kind": "ik_off", "limb": "arm_r"},
    "ARM_LEFT_RELEASE": {"channel": "body", "kind": "ik_off", "limb": "arm_l"},
    "FOOT_LEFT_PLANT": {"channel": "body", "kind": "contact", "limb": "leg_l", "lock": True},
    "FOOT_RIGHT_PLANT": {"channel": "body", "kind": "contact", "limb": "leg_r", "lock": True},
    "FOOT_LEFT_LIFT": {"channel": "body", "kind": "contact", "limb": "leg_l", "lock": False, "lift_px": 40.0},
    "FOOT_RIGHT_LIFT": {"channel": "body", "kind": "contact", "limb": "leg_r", "lock": False, "lift_px": 40.0},
    "GESTURE_SHRUG": {"channel": "body", "kind": "preset", "preset": "one_hand_shrug"},
    "GESTURE_POINT": {"channel": "body", "kind": "preset", "preset": "pointing_speaking"},
    "GESTURE_WAVE": {"channel": "body", "kind": "ik_oscillation", "limb": "arm_r", "reach": [0.45, 0.80],
                     "amplitude": [0.14, 0.03], "period_s": 0.8, "hand": "open_palm",
                     "note": "the wrist target swings sideways about a raised hand; the elbow bend side is held"},
    "GESTURE_IDLE": {"channel": "body", "kind": "preset", "preset": "relaxed_standing"},
    "HEAD_TILT": {"channel": "face", "kind": "head", "param": "tilt", "range_deg": [-20.0, 20.0]},
    "MOUTH_OPEN": {"channel": "face", "kind": "switch", "group": "face_mouth", "variant": "open"},
    "MOUTH_NEUTRAL": {"channel": "face", "kind": "switch", "group": "face_mouth", "variant": "neutral"},
}


def _shoulder(sk, s: str):
    return sk.bone(f"upper_arm_{s}").head


def _arm_len(sk, s: str) -> float:
    return sk.bone(f"upper_arm_{s}").length + sk.bone(f"forearm_{s}").length


def action_ops(action: str, sk, value: float = 1.0, t: float = 0.0, current: dict[str, Any] | None = None
               ) -> list[dict[str, Any]]:
    """Editor operations that perform a semantic action (the editor submits them, so undo works). ``value``
    is the action strength 0..1; ``t`` the time in seconds (oscillations). Targets use the rest skeleton."""
    if action not in LIVE_ACTIONS:
        raise KeyError(f"unknown live action {action}")
    a = LIVE_ACTIONS[action]
    k = a["kind"]
    value = float(min(max(value, 0.0), 1.0))
    if k == "switch":
        return [{"type": "switch.set", "group": a["group"], "variant": a["variant"], "auto": False}]
    if k == "switch_auto":
        return [{"type": "switch.set", "group": a["group"], "variant": None, "auto": True}]
    if k == "preset":
        return [{"type": "pose.set", "preset": a["preset"], "overrides": {}}]
    if k == "ik_off":
        return [{"type": "ik.set", "limb": a["limb"], "fields": {"enabled": False, "mode": "fk"}}]
    if k in ("ik_reach", "ik_oscillation"):
        s = a["limb"][-1]
        sh = _shoulder(sk, s)
        L = _arm_len(sk, s)
        hang = sh + [LAT[s] * 0.15 * L, 0.95 * L]
        lat, up = a["reach"]
        if k == "ik_oscillation":
            w = math.sin(2 * math.pi * t / a["period_s"])
            lat += a["amplitude"][0] * w
            up += a["amplitude"][1] * w
        reach = sh + [LAT[s] * lat * L, -up * L]
        tgt = hang + value * (reach - hang)
        ops = [{"type": "ik.set", "limb": a["limb"],
                "fields": {"enabled": True, "mode": "ik", "target": [round(float(v), 2) for v in tgt],
                           "bend": (current or {}).get(a["limb"], {}).get("bend") or (1 if s == "r" else -1)}}]
        if a.get("hand"):
            ops.append({"type": "switch.set", "group": f"hand_{s}", "variant": a["hand"], "auto": False})
        return ops
    if k == "contact":
        s = a["limb"][-1]
        ank = sk.bone(f"foot_{s}").head
        tgt = [float(ank[0]), float(ank[1]) - (0.0 if a["lock"] else value * a.get("lift_px", 40.0))]
        return [{"type": "ik.set", "limb": a["limb"],
                 "fields": {"enabled": True, "mode": "ik", "lock": a["lock"], "target": tgt}}]
    if k == "head":
        return [{"type": "live.head", "tilt": (a["range_deg"][0] + value * (a["range_deg"][1] - a["range_deg"][0]))}]
    raise ValueError(k)


# -- spec -----------------------------------------------------------------------------------------------
def build_rig_controls(eng, ik_state: dict[str, Any] | None = None, switch_state: dict[str, Any] | None = None
                       ) -> dict[str, Any]:
    """Rig controls document for an engine (character) and the editor's IK/switch state."""
    from .ik import ArmIKController, LegIKController

    ik_state = copy.deepcopy(ik_state) if ik_state else default_ik_state()
    chains = []
    for limb in LIMBS:
        kind, s = limb.split("_")
        ctl = (ArmIKController if kind == "arm" else LegIKController)(eng.sk, eng.solver.K, s)
        chains.append({"id": limb, "kind": kind, "side": s, "bones": ctl.bones, "root_parent": ctl.parent,
                       "effector": f"{ctl.bones[1]}.tail", "end_bone": ctl.bones[2],
                       "lengths_px": [round(ctl.solver.l1, 2), round(ctl.solver.l2, 2)],
                       "constraints": {"min_flex_deg": ctl.solver.min_flex, "max_flex_deg": ctl.solver.max_flex,
                                       "reach_px": [round(ctl.solver.min_reach, 2), round(ctl.solver.max_reach, 2)],
                                       "no_axial_scaling": True},
                       "pole_default": "elbow down and out" if kind == "arm" else "knee lateral (front view)",
                       "moho": {"target": "bone target + IK (Moho bone constraints)", "status": PROPOSED}})
    ctl = eng.switch_controller(switch_state)
    lib = eng.hand_lib.to_json(eng.profile.midline_x)
    missing = [{"kind": "hand", **m} for m in lib["missing"]]
    for c in eng.corr_switch.to_json():
        if c["asset_state"] == "missing":
            missing.append({"kind": "correction", "id": c["id"], "asset": c["asset"], "status": "NEEDS_ARTWORK",
                            "fallback": "rotation-dependent band deformation"})
    for g in ctl.groups.values():
        if g.kind == "face":
            missing += [{"kind": "face", "group": g.id, "variant": v.id, "status": "NEEDS_ARTWORK",
                         "fallback": g.default} for v in g.variants.values() if v.status == "missing"]
    doc = {
        "schema": SCHEMA, "schema_version": VERSION,
        "coordinate_system": "source px, origin top-left, +y down; angles deg clockwise-on-screen from straight down",
        "ik": {"chains": chains, "state": ik_state, "solver": "analytic two-bone; pole target; flexion limits; "
               "reach clamped (no segment scaling); clavicle compensation for arms"},
        "foot_contacts": {"states": list(CONTACT_STATES), "rule": "PLANTED feet are world-locked; LIFTING/LANDING "
                          "are the first/last 15 % of a swing or an unloaded stance foot; FREE outside gaits",
                          "locks": {s: bool(ik_state[f"leg_{s}"].get("lock")) for s in SIDES}},
        "switches": {"groups": [g.to_json() for g in ctl.groups.values()], "state": ctl.state,
                     "resolution": "user > keyframe > automatic preset choice (auto mode only) > default",
                     "hand_library": {"poses": lib["poses"], "variants": lib["variants"],
                                      "alignment": lib["alignment"], "tolerances": lib["tolerances"]}},
        "corrections": {"triggers": eng.corr_switch.to_json(),
                        "continuous": "rotation-dependent blend bands (motion/garments.py BAND_RULES)",
                        "moho": {"target": "Smart Bone actions", "status": PROPOSED}},
        "missing_artwork": missing,
        "live": {"actions": LIVE_ACTIONS, "channels": ["body", "face"],
                 "inputs": {"status": "unassigned", "reason": "Moho 14.5 Live Mode input identifiers have not been "
                            "discovered yet (docs/live-models.md); no names are invented"},
                 "moho": {"status": PROPOSED}},
        "moho": {"status": PROPOSED, "note": "no .moho is written in Phase 3.2"},
    }
    return json.loads(json.dumps(doc, default=_json_default))


def _json_default(o):
    import numpy as np

    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))


NUM = {"type": "number"}
PT = {"type": "array", "items": NUM, "minItems": 2, "maxItems": 2}
PT_OR_NULL = {"oneOf": [PT, {"type": "null"}]}
LIMB_STATE = {"type": "object", "required": ["enabled", "mode", "target", "pole"],
              "properties": {"enabled": {"type": "boolean"}, "mode": {"enum": list(IK_MODES)},
                             "target": PT_OR_NULL, "pole": PT_OR_NULL, "bend": {"enum": [1, -1, None]},
                             "lock": {"type": "boolean"}, "max_flex": {"oneOf": [NUM, {"type": "null"}]},
                             "torso_follow": {"type": "boolean"}, "end_angle": {"oneOf": [NUM, {"type": "null"}]},
                             "fk": {"oneOf": [{"type": "null"}, {"type": "object", "properties": {
                                 "abduction": NUM, "flexion": NUM}, "additionalProperties": False}]}},
              "additionalProperties": False}
VARIANT = {"type": "object", "required": ["id", "layer", "status", "needs_artwork"],
           "properties": {"id": {"type": "string"}, "layer": {"oneOf": [{"type": "string"}, {"type": "null"}]},
                          "status": {"enum": ["original", "approved", "pending_review", "rejected", "missing"]},
                          "needs_artwork": {"type": "boolean"}}}
GROUP_STATE = {"type": "object", "required": ["user", "auto", "keys", "offsets"],
               "properties": {"user": {"oneOf": [{"type": "string"}, {"type": "null"}]}, "auto": {"type": "boolean"},
                              "keys": {"type": "array", "items": {"type": "array", "prefixItems": [NUM, {"type": "string"}],
                                                                  "minItems": 2, "maxItems": 2}},
                              "offsets": {"type": "object", "additionalProperties": {
                                  "type": "object", "required": ["dx", "dy", "rot"],
                                  "properties": {"dx": NUM, "dy": NUM, "rot": NUM}}}}}
PROPOSED_BLOCK = {"type": "object", "required": ["status"], "properties": {"status": {"const": PROPOSED}}}
RIG_CONTROLS_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "$id": f"{SCHEMA}-{VERSION}",
    "title": f"AutoRig Studio rig_controls {VERSION}", "type": "object",
    "required": ["schema", "schema_version", "coordinate_system", "ik", "foot_contacts", "switches", "corrections",
                 "missing_artwork", "live", "moho"],
    "properties": {
        "schema": {"const": SCHEMA}, "schema_version": {"const": VERSION},
        "coordinate_system": {"type": "string"},
        "ik": {"type": "object", "required": ["chains", "state"], "properties": {
            "chains": {"type": "array", "minItems": 4, "items": {
                "type": "object", "required": ["id", "kind", "side", "bones", "lengths_px", "constraints", "moho"],
                "properties": {"id": {"enum": list(LIMBS)}, "kind": {"enum": ["arm", "leg"]}, "side": {"enum": ["r", "l"]},
                               "bones": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
                               "lengths_px": {"type": "array", "items": {"type": "number", "exclusiveMinimum": 0},
                                              "minItems": 2, "maxItems": 2},
                               "constraints": {"type": "object", "required": ["min_flex_deg", "max_flex_deg", "reach_px",
                                                                              "no_axial_scaling"],
                                               "properties": {"no_axial_scaling": {"const": True}}},
                               "moho": PROPOSED_BLOCK}}},
            "state": {"type": "object", "required": [*LIMBS, "pelvis_offset"],
                      "properties": {**{lb: LIMB_STATE for lb in LIMBS}, "pelvis_offset": PT}}}},
        "foot_contacts": {"type": "object", "required": ["states", "locks"], "properties": {
            "states": {"const": list(CONTACT_STATES)},
            "locks": {"type": "object", "required": list(SIDES), "additionalProperties": {"type": "boolean"}}}},
        "switches": {"type": "object", "required": ["groups", "state", "hand_library"], "properties": {
            "groups": {"type": "array", "items": {
                "type": "object", "required": ["id", "kind", "bone", "pivot", "default", "channel", "variants", "moho"],
                "properties": {"kind": {"enum": ["hand", "correction", "face"]}, "pivot": PT,
                               "channel": {"enum": ["body", "face"]},
                               "variants": {"type": "array", "minItems": 1, "items": VARIANT},
                               "moho": PROPOSED_BLOCK}}},
            "state": {"type": "object", "additionalProperties": GROUP_STATE}}},
        "corrections": {"type": "object", "required": ["triggers", "moho"], "properties": {
            "triggers": {"type": "array", "items": {"type": "object", "required": [
                "id", "bone", "measure", "on", "hysteresis", "asset", "priority", "compatible", "binding", "asset_state"],
                "properties": {"on": NUM, "hysteresis": {"type": "number", "minimum": 0},
                               "asset_state": {"enum": ["approved", "pending_review", "rejected", "missing"]}}}},
            "moho": PROPOSED_BLOCK}},
        "missing_artwork": {"type": "array", "items": {"type": "object", "required": ["kind", "status"],
                                                        "properties": {"status": {"const": "NEEDS_ARTWORK"}}}},
        "live": {"type": "object", "required": ["actions", "channels", "inputs", "moho"], "properties": {
            "actions": {"type": "object", "additionalProperties": {
                "type": "object", "required": ["channel", "kind"],
                "properties": {"channel": {"enum": ["body", "face"]}}}},
            "inputs": {"type": "object", "required": ["status"], "properties": {"status": {"const": "unassigned"}}},
            "moho": PROPOSED_BLOCK}},
        "moho": PROPOSED_BLOCK,
    },
}


def validate(doc: Any) -> list[str]:
    v = jsonschema.Draft202012Validator(RIG_CONTROLS_SCHEMA)
    errs = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in sorted(v.iter_errors(doc), key=lambda e: list(e.absolute_path))]
    # semantic checks the schema cannot express
    if isinstance(doc, dict) and not errs:
        for g in doc["switches"]["groups"]:
            ids = {v["id"] for v in g["variants"]}
            if g["default"] not in ids:
                errs.append(f"switches/{g['id']}: default {g['default']} is not a variant")
            st = doc["switches"]["state"].get(g["id"])
            if st:
                if st["user"] is not None and st["user"] not in ids:
                    errs.append(f"switches/{g['id']}: user choice {st['user']} is not a variant")
                for t, v in st["keys"]:
                    if v not in ids:
                        errs.append(f"switches/{g['id']}: key at {t} uses unknown variant {v}")
                for v in g["variants"]:
                    if v["status"] == "missing" and v["layer"] is not None:
                        errs.append(f"switches/{g['id']}/{v['id']}: missing variant must not name a layer")
        for limb in LIMBS:
            s = doc["ik"]["state"][limb]
            if s["enabled"] and s["mode"] == "ik" and s["target"] is None:
                errs.append(f"ik/state/{limb}: IK enabled without a target")
    return errs


def export_schema(out_dir: str | Path) -> Path:
    p = Path(out_dir) / "rig_controls.schema.json"
    p.write_text(json.dumps(RIG_CONTROLS_SCHEMA, indent=2) + "\n", encoding="utf-8")
    return p
