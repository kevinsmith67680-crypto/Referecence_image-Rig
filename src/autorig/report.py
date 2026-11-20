"""Verify the JSON report written by the generated Lua script inside Moho.

The Lua script reads the rig back from Moho's own objects (bone fields,
matrices, keyframes, layer bindings) and writes it to JSON. This module
compares that read-back with what the rig spec says must exist. It is the
oracle for the native (on-Mac) Phase 0 test.

Each check yields PASS, FAIL, WARN or INFO; the overall verdict is PASS only
when there is no FAIL.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .geometry import angle_diff, compute_rest_pose, expected_angle_keys
from .spec import RigSpec

POS_TOL = 1e-4  # Moho units (~0.05 px on a 1080 px tall canvas)
ANGLE_TOL = 1e-4  # radians
WORLD_TOL = 2e-3  # Moho units (~1 px); matrices go through more float math


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # PASS | FAIL | WARN | INFO
    detail: str


@dataclass(frozen=True)
class Verification:
    checks: list[Check]

    @property
    def passed(self) -> bool:
        return not any(c.status == "FAIL" for c in self.checks)

    def counts(self) -> dict[str, int]:
        out = {"PASS": 0, "FAIL": 0, "WARN": 0, "INFO": 0}
        for c in self.checks:
            out[c.status] += 1
        return out

    def format(self) -> str:
        width = max(len(c.name) for c in self.checks) if self.checks else 10
        lines = [f"{c.status:<4}  {c.name:<{width}}  {c.detail}" for c in self.checks]
        n = self.counts()
        verdict = "PASS" if self.passed else "FAIL"
        lines.append("")
        lines.append(
            f"VERDICT: {verdict}  ({n['PASS']} pass, {n['FAIL']} fail, {n['WARN']} warn, {n['INFO']} info)"
        )
        return "\n".join(lines)


def _close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


def _vec_close(a: Any, b: tuple[float, float], tol: float) -> bool:
    return (
        isinstance(a, (list, tuple))
        and len(a) == 2
        and all(isinstance(v, (int, float)) for v in a)
        and _close(a[0], b[0], tol)
        and _close(a[1], b[1], tol)
    )


def expected_state(spec: RigSpec) -> dict[str, Any]:
    """Moho-space values the script must produce (also exported for manual or
    MohoMCP-based cross-checking as examples/*/expected/expected_rig.json)."""
    rest = compute_rest_pose(spec)
    keys = expected_angle_keys(spec, rest)
    by_name = {r.name: r for r in rest}
    return {
        "document": {
            "width": spec.canvas_width,
            "height": spec.canvas_height,
            "start_frame": spec.start_frame,
            "end_frame": spec.end_frame,
        },
        "bone_layer_name": spec.bone_layer_name,
        "bones": [
            {
                "id": r.index,
                "name": r.name,
                "parent_id": r.parent_index,
                "pos": list(r.local_pos),
                "angle_rad": r.local_angle,
                "angle_deg": math.degrees(r.local_angle),
                "length": r.length,
                "world_base": list(r.world_base),
                "world_tip": list(r.world_tip),
                "angle_keys": [[f, v] for f, v in keys[r.name]],
            }
            for r in rest
        ],
        "layers_back_to_front": [
            {
                "name": p.layer_name,
                "image": p.image,
                "bone": p.bone,
                "parent_bone_id": by_name[p.bone].index,
            }
            for p in spec.parts_back_to_front()
        ],
    }


def verify_report(spec: RigSpec, report: dict[str, Any]) -> Verification:
    checks: list[Check] = []

    def add(name: str, ok: bool, detail_ok: str, detail_bad: str, bad_status: str = "FAIL") -> None:
        checks.append(Check(name, "PASS" if ok else bad_status, detail_ok if ok else detail_bad))

    status = report.get("status")
    add("report.status", status == "completed", "completed", f"status={status!r}")
    errors = report.get("errors") or []
    add("report.errors", not errors, "none", "; ".join(map(str, errors)))
    for w in report.get("warnings") or []:
        checks.append(Check("report.warning", "WARN", str(w)))

    lua_version = str(report.get("lua_version", ""))
    checks.append(
        Check("env.lua_version", "INFO" if "5.4" in lua_version else "WARN",
              f"{lua_version or 'unknown'} (Moho 14 is expected to embed Lua 5.4)")
    )
    checks.append(Check("env.moho_app_version", "INFO", str(report.get("moho_app_version", "unknown"))))

    gen = report.get("generator") or {}
    add("provenance.spec_sha256", gen.get("spec_sha256") == spec.canonical_sha256(),
        "report was produced from this exact spec",
        "report was produced from a DIFFERENT spec version - regenerate the Lua script", "WARN")

    if status != "completed":
        return Verification(checks)

    exp = expected_state(spec)
    doc = report.get("document") or {}
    add("document.size", (doc.get("width"), doc.get("height")) == (spec.canvas_width, spec.canvas_height),
        f"{doc.get('width')}x{doc.get('height')}",
        f"got {doc.get('width')}x{doc.get('height')}, expected {spec.canvas_width}x{spec.canvas_height}")
    add("document.frame_range", (doc.get("start_frame"), doc.get("end_frame")) == (spec.start_frame, spec.end_frame),
        f"{doc.get('start_frame')}..{doc.get('end_frame')} ({spec.frame_count} frames)",
        f"got {doc.get('start_frame')}..{doc.get('end_frame')}, expected {spec.start_frame}..{spec.end_frame}")
    checks.append(Check("document.fps", "INFO", str(doc.get("fps"))))

    consts = report.get("constants") or {}
    lt_bone, lt_image = consts.get("LT_BONE"), consts.get("LT_IMAGE")
    bl = report.get("bone_layer") or {}
    add("bone_layer.name", bl.get("name") == spec.bone_layer_name, str(bl.get("name")),
        f"got {bl.get('name')!r}, expected {spec.bone_layer_name!r}")
    add("bone_layer.type", lt_bone is not None and bl.get("type") == lt_bone,
        f"LayerType()={bl.get('type')} == MOHO.LT_BONE", f"LayerType()={bl.get('type')} but MOHO.LT_BONE={lt_bone}")

    # --- bones ---------------------------------------------------------------
    bones = report.get("bones") or []
    exp_bones = exp["bones"]
    add("bones.count", len(bones) == len(exp_bones), str(len(bones)),
        f"got {len(bones)}, expected {len(exp_bones)}")
    for eb in exp_bones:
        name = eb["name"]
        rb = next((b for b in bones if b.get("name") == name), None)
        if rb is None:
            checks.append(Check(f"bone[{name}]", "FAIL", "missing in Moho"))
            continue
        add(f"bone[{name}].id", rb.get("id") == eb["id"], str(rb.get("id")),
            f"got {rb.get('id')}, expected {eb['id']}")
        add(f"bone[{name}].parent", rb.get("parent_id") == eb["parent_id"], str(rb.get("parent_id")),
            f"got {rb.get('parent_id')}, expected {eb['parent_id']}")
        add(f"bone[{name}].fPos", _vec_close(rb.get("pos"), tuple(eb["pos"]), POS_TOL),
            f"{rb.get('pos')}", f"got {rb.get('pos')}, expected {eb['pos']}")
        angle = rb.get("angle")
        add(f"bone[{name}].fAngle",
            isinstance(angle, (int, float)) and abs(angle_diff(angle, eb["angle_rad"])) <= ANGLE_TOL,
            f"{angle}", f"got {angle}, expected {eb['angle_rad']}")
        length = rb.get("length")
        add(f"bone[{name}].fLength", isinstance(length, (int, float)) and _close(length, eb["length"], POS_TOL),
            f"{length}", f"got {length}, expected {eb['length']}")
        ok_world = _vec_close(rb.get("world_base"), tuple(eb["world_base"]), WORLD_TOL) and _vec_close(
            rb.get("world_tip"), tuple(eb["world_tip"]), WORLD_TOL)
        add(f"bone[{name}].world_matrix", ok_world,
            "fMovedMatrix places base/tip where the art's joints are",
            f"Moho world base/tip {rb.get('world_base')}/{rb.get('world_tip')} != expected "
            f"{eb['world_base']}/{eb['world_tip']} - the parent-local fPos convention may differ from "
            "the documented assumption; bones will not line up with the art")

        got_keys = {}
        for pair in rb.get("angle_keys") or []:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                got_keys[int(round(pair[0]))] = pair[1]
        missing, wrong = [], []
        for frame, value in eb["angle_keys"]:
            if frame not in got_keys:
                missing.append(frame)
            elif abs(angle_diff(got_keys[frame], value)) > ANGLE_TOL:
                wrong.append(f"f{frame}: {got_keys[frame]:.6f} != {value:.6f}")
        expected_frames = {f for f, _ in eb["angle_keys"]}
        extra = sorted(set(got_keys) - expected_frames)
        ok_keys = not missing and not wrong
        add(f"bone[{name}].angle_keys", ok_keys,
            f"{len(expected_frames)} keys at frames {sorted(expected_frames)}",
            f"missing frames {missing}; wrong values {wrong}")
        if extra:
            checks.append(Check(f"bone[{name}].extra_keys", "WARN", f"unexpected keys at frames {extra}"))

    # --- layers & bindings -------------------------------------------------------
    layers = report.get("layers") or []
    exp_layers = exp["layers_back_to_front"]
    add("layers.count", len(layers) == len(exp_layers), str(len(layers)),
        f"got {len(layers)}, expected {len(exp_layers)}")
    for el in exp_layers:
        rl = next((x for x in layers if x.get("name") == el["name"]), None)
        if rl is None:
            checks.append(Check(f"layer[{el['name']}]", "FAIL", "missing in Moho"))
            continue
        add(f"layer[{el['name']}].type", lt_image is not None and rl.get("type") == lt_image,
            "image layer", f"LayerType()={rl.get('type')} but MOHO.LT_IMAGE={lt_image}")
        add(f"layer[{el['name']}].binding", rl.get("parent_bone_id") == el["parent_bone_id"],
            f"bound to bone {el['parent_bone_id']} '{el['bone']}'",
            f"LayerParentBone()={rl.get('parent_bone_id')}, expected {el['parent_bone_id']} ('{el['bone']}')")
        src = str(rl.get("source_image_set", ""))
        add(f"layer[{el['name']}].image", src.endswith(el["image"]), src,
            f"source {src!r} does not end with {el['image']!r}")

    children = bl.get("children_by_index")
    expected_names = [el["name"] for el in exp_layers]
    if not isinstance(children, list):
        checks.append(Check("bone_layer.children", "WARN", "children could not be read back"))
    else:
        add("bone_layer.children", sorted(children) == sorted(expected_names),
            f"contains all {len(expected_names)} art layers",
            f"children {children} != expected {expected_names}")
        if children == expected_names or children == expected_names[::-1]:
            front = expected_names[-1]
            checks.append(Check("bone_layer.z_order", "INFO",
                                f"index order {children}; expected '{front}' frontmost - confirm visually"))
        else:
            checks.append(Check("bone_layer.z_order", "WARN",
                                f"index order {children} matches neither stacking direction of {expected_names}"))
    return Verification(checks)


def load_report(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as fh:
        return json.load(fh)
