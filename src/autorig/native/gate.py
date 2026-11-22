"""Phase 4.0 hard gate: check the evidence returned by the native Mac run.

The gate passes only when all five requirements are met with evidence produced by the
real Moho application:

1. ``native_document``: a native document was created (the build script's report from
   Moho, checked by the Phase 0 verifier);
2. ``bone_moves_artwork``: a bone genuinely moves the artwork (Moho renders of frames 1
   and 12 differ where, and only where, the moved arm should differ);
3. ``document_saved``: the document saved (the ``.moho`` file exists and contains the rig);
4. ``document_reopened``: the reopened document still holds the rig (the verify script's
   report from the reopened file: bones, bindings, keys);
5. ``bone_functional_after_reopen``: the bone still works after reopening (a new key read
   back by the verify script, and the Moho render of that frame shows the raised arm).

Reports written by the strict mock (``moho_app_version`` contains ``mock``) or renders
from the Python simulation never pass an item: they are labelled MOCK_ONLY.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..geometry import angle_diff, compute_rest_pose, expected_angle_keys
from ..report import ANGLE_TOL, POS_TOL, verify_report
from ..spec import RigSpec
from .poc import (BONE_LAYER, BUILD_REPORT, FRAME_MOVED, FRAME_REOPEN, FRAME_REST, MANIFEST, SAVED_NAME,
                  VERIFY_REPORT, load_poc, reopen_spec)

GATE_ID = "4.0"
VERDICT_PASS = "GATE 4.0 PASSED"
VERDICT_BLOCKED = "BLOCKED — NATIVE VALIDATION REQUIRED"

# Render comparison thresholds (documented in docs/phase4_native_rig_report.md).
FG_DIFF = 40          # per-channel difference from the background that counts as figure
MOVE_DIFF = 48        # per-channel difference between two frames that counts as change
ALIGN_IOU_MIN = 0.85  # rest render silhouette vs expected silhouette
MOVE_IOU_MIN = 0.60   # changed region of the render vs the expected changed region
MOVE_RATIO_MIN = 0.5  # changed render pixels / expected changed pixels (a frozen arm gives ~0)
ASPECT_TOL = 0.01

REQUIREMENTS = {
    "native_document": "A real native Moho document has been created",
    "bone_moves_artwork": "At least one bone genuinely moves artwork",
    "document_saved": "The document saves successfully",
    "document_reopened": "The document reopens successfully",
    "bone_functional_after_reopen": "The bone remains functional after reopening",
}


def origin(report: dict[str, Any] | None) -> str:
    """'native', 'mock' or 'missing'. A mock run never counts as native evidence."""
    if not report:
        return "missing"
    version = str(report.get("moho_app_version", ""))
    if "mock" in version.lower() or report.get("simulated") is True:
        return "mock"
    return "native"


def _check(name: str, ok: bool, detail: str, status_bad: str = "FAIL") -> dict[str, Any]:
    return {"name": name, "status": "PASS" if ok else status_bad, "detail": detail}


def _keys_of(bone: dict[str, Any]) -> dict[int, float]:
    out = {}
    for pair in bone.get("angle_keys") or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            out[int(round(pair[0]))] = float(pair[1])
    return out


def check_reopened(spec: RigSpec, raw: dict[str, Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    """The verify script's read-back of the reopened document vs the build spec."""
    checks = [_check("report.status", report.get("status") == "completed", str(report.get("status"))),
              _check("report.errors", not report.get("errors"), "; ".join(map(str, report.get("errors") or [])) or "none")]
    if report.get("status") != "completed":
        return checks
    consts = report.get("constants") or {}
    bl = report.get("bone_layer") or {}
    checks.append(_check("bone_layer.found", bl.get("name") == spec.bone_layer_name and bl.get("type") == consts.get("LT_BONE"),
                         f"{bl.get('name')!r} type {bl.get('type')}"))
    rest = compute_rest_pose(spec)
    keys = expected_angle_keys(spec, rest)
    bones = {b.get("name"): b for b in report.get("bones") or []}
    checks.append(_check("bones.count", len(bones) == len(rest), f"{len(bones)} of {len(rest)}"))
    for r in rest:
        b = bones.get(r.name)
        if b is None:
            checks.append(_check(f"bone[{r.name}]", False, "missing after reopening"))
            continue
        pos = b.get("pos") or [math.nan, math.nan]
        ok = (b.get("id") == r.index and b.get("parent_id") == r.parent_index
              and abs(pos[0] - r.local_pos[0]) <= POS_TOL and abs(pos[1] - r.local_pos[1]) <= POS_TOL
              and abs(angle_diff(float(b.get("angle", math.nan)), r.local_angle)) <= ANGLE_TOL
              and abs(float(b.get("length", math.nan)) - r.length) <= POS_TOL)
        checks.append(_check(f"bone[{r.name}].rest", ok, f"id {b.get('id')}, parent {b.get('parent_id')}, "
                                                         f"pos {pos}, angle {b.get('angle')}, length {b.get('length')}"))
        got = _keys_of(b)
        missing = [f for f, v in keys[r.name] if f not in got or abs(angle_diff(got[f], v)) > ANGLE_TOL]
        checks.append(_check(f"bone[{r.name}].keys_preserved", not missing,
                             f"{len(keys[r.name])} build keys preserved" if not missing else f"lost or changed at {missing}"))
    by_name = {r.name: r.index for r in rest}
    layers = {l.get("name"): l for l in report.get("layers") or []}
    for p in spec.parts:
        l = layers.get(p.layer_name)
        ok = l is not None and l.get("type") == consts.get("LT_IMAGE") and l.get("parent_bone_id") == by_name[p.bone]
        checks.append(_check(f"layer[{p.layer_name}].binding", ok,
                             "missing" if l is None else f"type {l.get('type')}, bound to bone {l.get('parent_bone_id')} "
                                                         f"(expected {by_name[p.bone]} '{p.bone}')"))
    return checks


def check_move(spec: RigSpec, raw: dict[str, Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    """The new key the verify script set on the reopened bone."""
    mv = report.get("move") or {}
    rest = {r.name: r for r in compute_rest_pose(spec)}
    spec2 = reopen_spec(raw)
    expected = dict(expected_angle_keys(spec2, compute_rest_pose(spec2))["arm"])[FRAME_REOPEN]
    checks = [
        _check("move.frame", mv.get("frame") == FRAME_REOPEN, str(mv.get("frame"))),
        _check("move.value_read", isinstance(mv.get("value_read"), (int, float))
               and abs(angle_diff(mv["value_read"], expected)) <= ANGLE_TOL,
               f"read {mv.get('value_read')} rad, expected {expected} rad"),
    ]
    keys = {int(round(k[0])): k[1] for k in mv.get("keys_after") or [] if isinstance(k, list) and len(k) == 2}
    checks.append(_check("move.key_listed", FRAME_REOPEN in keys and abs(angle_diff(keys[FRAME_REOPEN], expected)) <= ANGLE_TOL,
                         f"keys after: {sorted(keys)}"))
    obs = mv.get("observed_after_set_cur_frame") or {}
    a = obs.get("angle")
    follows = isinstance(a, (int, float)) and abs(angle_diff(a, expected)) <= 1e-3
    checks.append({"name": "move.fields_follow_frame", "status": "INFO",
                   "detail": ("bone fAngle follows the current frame after SetCurFrame" if follows else
                              f"fAngle after SetCurFrame = {a} (rest {rest['arm'].local_angle}); not evidence either way")})
    return checks


def check_saved(spec: RigSpec, path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return [_check("saved.exists", False, f"{path} not found", "NOT_EXECUTED")]
    checks = [_check("saved.exists", path.stat().st_size > 0, f"{path.name}, {path.stat().st_size} bytes")]
    try:
        from ..mohofile import grep, load_project
        proj = load_project(path)
    except Exception as exc:  # the format is not evidenced; parsing is supporting evidence only
        checks.append({"name": "saved.parsed", "status": "WARN", "detail": f"not parsed: {exc}"})
        return checks
    names = [spec.bone_layer_name] + [b.name for b in spec.bones] + [p.layer_name for p in spec.parts]
    found = {n: bool(grep(proj.data, f"^{re.escape(n)}$")) for n in names}
    checks.append({"name": "saved.contains_rig", "status": "PASS" if all(found.values()) else "WARN",
                   "detail": f"names found in the saved project: {found}"})
    return checks


# --- renders ------------------------------------------------------------------------------

def _load_render(path: Path, size: tuple[int, int]) -> np.ndarray:
    im = Image.open(path)
    im.load()
    rgba = im.convert("RGBA")
    if rgba.size != size:
        if abs(rgba.size[0] / rgba.size[1] - size[0] / size[1]) > ASPECT_TOL:
            raise ValueError(f"{path.name}: {rgba.size} does not have the canvas aspect {size}")
        rgba = rgba.resize(size, Image.Resampling.LANCZOS)
    return np.asarray(rgba).astype(float)


def _flatten_on(rgba: np.ndarray, bg: np.ndarray) -> np.ndarray:
    a = rgba[..., 3:4] / 255.0
    return rgba[..., :3] * a + bg[None, None, :] * (1.0 - a)


def _background(rgba: np.ndarray) -> np.ndarray:
    if (rgba[..., 3] < 250).mean() > 0.05:  # exported with alpha: compare on white
        return np.array([255.0, 255.0, 255.0])
    border = np.concatenate([rgba[0, :, :3], rgba[-1, :, :3], rgba[:, 0, :3], rgba[:, -1, :3]])
    return np.median(border, axis=0)


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 0.0


def compare_renders(poc_dir: Path, renders: dict[str, Path | None]) -> dict[str, Any]:
    """Compare Moho renders of frames 1, 12 and 18 with the expected (simulated) frames."""
    spec, raw, man = load_poc(poc_dir)
    size = (spec.canvas_width, spec.canvas_height)
    exp = {k: np.asarray(Image.open(poc_dir / man["expected"][k]).convert("RGBA")).astype(float)
           for k in ("rest", "moved", "reopen_move")}
    out: dict[str, Any] = {"thresholds": {"FG_DIFF": FG_DIFF, "MOVE_DIFF": MOVE_DIFF, "ALIGN_IOU_MIN": ALIGN_IOU_MIN,
                                          "MOVE_IOU_MIN": MOVE_IOU_MIN, "MOVE_RATIO_MIN": MOVE_RATIO_MIN}}
    if renders.get("rest") is None or not Path(renders["rest"]).is_file():
        out["status"] = "NOT_EXECUTED"
        out["detail"] = "no render of frame 1"
        return out
    try:
        r_rest = _load_render(Path(renders["rest"]), size)
    except (OSError, ValueError) as exc:
        out["status"] = "FAIL"
        out["alignment"] = {"status": "FAIL", "detail": str(exc)}
        return out
    bg = _background(r_rest)
    flat_rest = _flatten_on(r_rest, bg)
    fg_render = np.abs(flat_rest - bg).max(axis=2) > FG_DIFF
    fg_exp = exp["rest"][..., 3] > 127
    iou = _iou(fg_render, fg_exp)
    out["alignment"] = {"status": "PASS" if iou >= ALIGN_IOU_MIN else "FAIL", "silhouette_iou": round(iou, 4),
                        "background": [round(float(v), 1) for v in bg]}
    e_rest = _flatten_on(exp["rest"], bg)
    for key in ("moved", "reopen_move"):
        p = renders.get(key)
        if p is None or not Path(p).is_file():
            out[key] = {"status": "NOT_EXECUTED", "detail": "no render"}
            continue
        try:
            r = _flatten_on(_load_render(Path(p), size), bg)
        except (OSError, ValueError) as exc:
            out[key] = {"status": "FAIL", "detail": str(exc)}
            continue
        d_render = np.abs(r - flat_rest).max(axis=2) > MOVE_DIFF
        d_exp = np.abs(_flatten_on(exp[key], bg) - e_rest).max(axis=2) > MOVE_DIFF
        iou_m = _iou(d_render, d_exp)
        ratio = float(d_render.sum()) / max(1.0, float(d_exp.sum()))
        ok = iou_m >= MOVE_IOU_MIN and ratio >= MOVE_RATIO_MIN
        out[key] = {"status": "PASS" if ok else "FAIL", "changed_iou": round(iou_m, 4),
                    "changed_ratio": round(ratio, 4), "changed_px": int(d_render.sum()),
                    "expected_changed_px": int(d_exp.sum())}
    statuses = [out[k]["status"] for k in ("alignment", "moved", "reopen_move") if k in out]
    out["status"] = "PASS" if all(s == "PASS" for s in statuses) else (
        "FAIL" if "FAIL" in statuses else "NOT_EXECUTED")
    return out


# --- the gate -------------------------------------------------------------------------------

def _load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _item(key: str, status: str, evidence: list[str], detail: str) -> dict[str, Any]:
    return {"id": key, "requirement": REQUIREMENTS[key], "status": status, "evidence": evidence, "detail": detail}


def evaluate_gate(poc_dir: str | Path, evidence_dir: str | Path | None = None, *,
                  build_report: str | Path | None = None, verify_report_path: str | Path | None = None,
                  saved: str | Path | None = None, renders: dict[str, str | Path | None] | None = None,
                  renders_simulated: bool = False) -> dict[str, Any]:
    """Evaluate HARD GATE 4.0 from the files of one native run.

    ``evidence_dir`` holds the harness layout (reports, the saved ``.moho``, ``renders/``,
    ``environment.json``, ``observations.json``); explicit paths override it.
    ``renders_simulated`` marks renders that did not come from Moho (cloud tests).
    """
    poc = Path(poc_dir)
    ev = Path(evidence_dir) if evidence_dir else None
    spec, raw, man = load_poc(poc)

    def pick(explicit, name):
        if explicit is not None:
            return Path(explicit)
        return (ev / name) if ev else None

    b_path, v_path = pick(build_report, BUILD_REPORT), pick(verify_report_path, VERIFY_REPORT)
    s_path = pick(saved, SAVED_NAME)
    rnd = dict(renders or {})
    if ev:
        for key, f in (("rest", FRAME_REST), ("moved", FRAME_MOVED), ("reopen_move", FRAME_REOPEN)):
            rnd.setdefault(key, ev / "renders" / f"frame_{f:04d}.png")
    build, verify = _load_json(b_path), _load_json(v_path)
    env = _load_json(ev / "environment.json") if ev else None
    obs = _load_json(ev / "observations.json") if ev else None

    items = []
    # 1. native document
    b_origin = origin(build)
    phase0 = verify_report(spec, build) if build else None
    b_ok = phase0 is not None and phase0.passed
    if build is None:
        items.append(_item("native_document", "NOT_EXECUTED", [], f"no build report ({b_path})"))
    else:
        status = ("PASS" if b_origin == "native" else "MOCK_ONLY") if b_ok else "FAIL"
        fails = [f"{c.name}: {c.detail}" for c in phase0.checks if c.status == "FAIL"]
        items.append(_item("native_document", status, [str(b_path)],
                           f"origin {b_origin}, Moho {build.get('moho_app_version')}, Lua {build.get('lua_version')}; "
                           + ("Phase 0 verifier: 0 FAIL" if b_ok else f"failures: {fails[:5]}")))

    # renders (used by items 2 and 5)
    render_cmp = compare_renders(poc, rnd)
    r_origin = "simulation" if renders_simulated else "moho"

    def render_status(key):
        st = (render_cmp.get(key) or {}).get("status", "NOT_EXECUTED")
        if render_cmp.get("alignment", {}).get("status") == "FAIL" and st == "PASS":
            st = "FAIL"
        if st == "PASS" and r_origin != "moho":
            st = "MOCK_ONLY"
        return st

    # 2. a bone moves artwork
    st2 = render_status("moved")
    if st2 == "PASS" and b_origin != "native":
        st2 = "MOCK_ONLY"
    items.append(_item("bone_moves_artwork", st2,
                       [str(rnd.get("rest")), str(rnd.get("moved"))],
                       f"renders ({r_origin}) of frames {FRAME_REST} and {FRAME_MOVED}: "
                       f"alignment {render_cmp.get('alignment')}, change {render_cmp.get('moved')}"))

    # 3. saved
    saved_checks = check_saved(spec, s_path)
    exists = saved_checks[0]["status"] == "PASS"
    v_origin = origin(verify)
    reopen_checks = check_reopened(spec, raw, verify) if verify else []
    reopen_ok = bool(reopen_checks) and all(c["status"] == "PASS" for c in reopen_checks)
    if not exists:
        st3 = "NOT_EXECUTED"
    else:
        contains = any(c["name"] == "saved.contains_rig" and c["status"] == "PASS" for c in saved_checks)
        st3 = "PASS" if (contains or (reopen_ok and v_origin == "native")) else "FAIL"
        if st3 == "PASS" and v_origin == "mock" and not contains:
            st3 = "MOCK_ONLY"
    items.append(_item("document_saved", st3, [str(s_path)], "; ".join(f"{c['name']}={c['status']}" for c in saved_checks)))

    # 4. reopened
    if verify is None:
        st4, d4 = "NOT_EXECUTED", f"no verify report ({v_path})"
    else:
        fails = [f"{c['name']}: {c['detail']}" for c in reopen_checks if c["status"] != "PASS"]
        st4 = ("PASS" if v_origin == "native" else "MOCK_ONLY") if reopen_ok else "FAIL"
        d4 = f"origin {v_origin}; " + ("rig, bindings and keys preserved" if reopen_ok else f"failures: {fails[:5]}")
    items.append(_item("document_reopened", st4, [str(v_path)], d4))

    # 5. functional after reopening
    move_checks = check_move(spec, raw, verify) if (verify and verify.get("status") == "completed") else []
    move_ok = bool(move_checks) and all(c["status"] in ("PASS", "INFO") for c in move_checks)
    st_r = render_status("reopen_move")
    if verify is None:
        st5 = "NOT_EXECUTED"
    elif not move_ok:
        st5 = "FAIL"
    elif st_r in ("FAIL", "NOT_EXECUTED"):
        st5 = st_r
    elif v_origin != "native" or st_r == "MOCK_ONLY":
        st5 = "MOCK_ONLY"
    else:
        st5 = "PASS"
    items.append(_item("bone_functional_after_reopen", st5, [str(v_path), str(rnd.get("reopen_move"))],
                       f"new key: {[(c['name'], c['status']) for c in move_checks]}; "
                       f"render of frame {FRAME_REOPEN}: {render_cmp.get('reopen_move')}"))

    passed = all(i["status"] == "PASS" for i in items)
    return {
        "gate": GATE_ID,
        "verdict": VERDICT_PASS if passed else VERDICT_BLOCKED,
        "passed": passed,
        "items": items,
        "checks": {
            "phase0_build": [{"name": c.name, "status": c.status, "detail": c.detail} for c in phase0.checks] if phase0 else [],
            "reopened": reopen_checks,
            "move": move_checks,
            "saved": saved_checks,
            "renders": render_cmp,
        },
        "evidence_origin": {"build_report": b_origin, "verify_report": v_origin, "renders": r_origin},
        "environment": env,
        "observations": obs,
        "api_surface": (verify or {}).get("api_surface"),
        "poc": {"dir": str(poc), "character_id": man["character_id"], "side": man["side"],
                "spec_sha256": man["spec_sha256"], "bone_layer": BONE_LAYER, "manifest": MANIFEST},
    }


def format_gate(result: dict[str, Any]) -> str:
    lines = [f"HARD GATE {result['gate']} ({result['poc']['character_id']}, side {result['poc']['side']})"]
    for i in result["items"]:
        lines.append(f"  [{i['status']:<12}] {i['requirement']}")
        lines.append(f"                 {i['detail'][:300]}")
    lines.append(f"Evidence origin: {result['evidence_origin']}")
    lines.append(f"VERDICT: {result['verdict']}")
    return "\n".join(lines)
