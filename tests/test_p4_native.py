"""Phase 4.0 native proof: the portable package, the two menu scripts under the strict
mock, the HARD GATE 4.0 verifier and the macOS harness's failure paths.

Nothing here runs Moho. Every report below comes from the strict mock and every
"render" from the Python simulation; the gate must label them MOCK_ONLY and stay
BLOCKED. One test feeds the verifier fabricated "native" inputs to prove its
acceptance path works; those inputs are test fixtures, never evidence.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from autorig.lua_codegen import SCRIPT_NAME, render_lua
from autorig.native.gate import (VERDICT_BLOCKED, VERDICT_PASS, check_move, check_reopened, compare_renders,
                                 evaluate_gate, origin)
from autorig.native.poc import (BONE_LAYER, BUILD_REPORT, BUILD_SCRIPT, FRAME_MOVED, FRAME_REOPEN, FRAME_REST,
                                SAVED_NAME, VERIFY_REPORT, VERIFY_SCRIPT, build_poc, load_poc, render_build_lua,
                                render_verify_lua)
from autorig.report import verify_report

from conftest import load_mock, lua_to_py, new_lua

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "schoolboy": ROOT / "projects/schoolboy_tpose/character_package",
    "tough_student": ROOT / "projects/tough_student_test/character_package",
}
HARNESS = ROOT / "tools/macos/phase4_native_test.sh"


@pytest.fixture(scope="module")
def pocs(tmp_path_factory):
    out = {}
    for name, pkg in PACKAGES.items():
        d = tmp_path_factory.mktemp(f"poc_{name}")
        man = build_poc(pkg, d, asset_root=str(d.resolve()))
        out[name] = (d, man)
    return out


def _run_build_and_verify(d: Path, man: dict, reopen: bool = True):
    lua = new_lua()
    mock = load_mock(lua)
    env = mock.new_env(lua.table_from({"width": 1920, "height": 1080}, recursive=True))
    lua.execute((d / man["scripts"]["build"]).read_text(encoding="utf-8"))
    s = lua.globals()[BUILD_SCRIPT]
    s.Run(s, env.moho)
    if reopen:
        lua.execute((d / man["scripts"]["verify"]).read_text(encoding="utf-8"))
        v = lua.globals()[VERIFY_SCRIPT]
        v.Run(v, env.moho)
    return lua, mock, env


def _simulated_renders(d: Path, man: dict, ev: Path, bg=(236, 236, 236)):
    (ev / "renders").mkdir(parents=True, exist_ok=True)
    for key, f in (("rest", FRAME_REST), ("moved", FRAME_MOVED), ("reopen_move", FRAME_REOPEN)):
        im = Image.open(d / man["expected"][key]).convert("RGBA")
        canvas = Image.new("RGBA", im.size, bg + (255,))
        canvas.alpha_composite(im)
        canvas.convert("RGB").save(ev / "renders" / f"frame_{f:04d}.png")


def _evidence(d: Path, man: dict, tmp: Path) -> Path:
    _run_build_and_verify(d, man)
    ev = tmp / "ev"
    ev.mkdir()
    for f in (BUILD_REPORT, VERIFY_REPORT):
        shutil.copy(d / f, ev / f)
    _simulated_renders(d, man, ev)
    return ev


# --- the package ---------------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(PACKAGES))
def test_package_uses_original_pixels_unscaled(pocs, name):
    d, man = pocs[name]
    pkg = PACKAGES[name]
    am = json.loads((pkg / "artwork/manifest.json").read_text())
    by_id = {l["id"]: l for l in am["layers"]}
    ox, oy = man["placement"]["offset_px"]
    assert all(by_id[l["id"]]["category"] == "A_original" for l in man["layers"])
    assert man["excluded"]["generated_extensions"] == len(am["extensions"])
    sw, sh = man["source_canvas"]
    for group, ids in man["groups"].items():
        if not ids:
            continue
        flat = Image.new("RGBA", (sw, sh))
        for i in ids:
            flat = Image.alpha_composite(flat, Image.open(pkg / by_id[i]["image"]).convert("RGBA"))
        art = np.asarray(Image.open(d / man["art"][group]["path"]).convert("RGBA"))
        assert art.shape[:2] == (1080, 1920)
        src = np.asarray(flat)
        x0, y0 = max(0, -ox), max(0, -oy)
        x1, y1 = min(sw, 1920 - ox), min(sh, 1080 - oy)
        # a pure integer translation: every kept source pixel is byte-identical
        assert np.array_equal(art[y0 + oy:y1 + oy, x0 + ox:x1 + ox], src[y0:y1, x0:x1])
    assert man["split_fidelity"] == {"max_abs_diff": 0, "px_over_2": 0}
    assert man["placement"]["clipped_alpha_px"]["arm"] == 0
    assert min(man["placement"]["arm_on_canvas_fraction"].values()) >= 0.99


@pytest.mark.parametrize("name", list(PACKAGES))
def test_bones_come_from_the_package_skeleton(pocs, name):
    d, man = pocs[name]
    rig = json.loads((PACKAGES[name] / "natural_pose/rig.json").read_text())
    bones = {b["name"]: b for b in rig["bones"]}
    spec, raw, _ = load_poc(d)
    ox, oy = man["placement"]["offset_px"]
    got = {b["name"]: b for b in raw["bones"]}
    pairs = {"body": (bones["pelvis"]["head"], bones["chest"]["tail"]),
             "shoulder": (bones["clavicle_l"]["head"], bones["clavicle_l"]["tail"]),
             "arm": (bones["upper_arm_l"]["head"], bones["forearm_l"]["tail"])}
    for name_, (a, b) in pairs.items():
        assert got[name_]["base_px"] == pytest.approx([a[0] + ox, a[1] + oy], abs=1e-3)
        assert got[name_]["tip_px"] == pytest.approx([b[0] + ox, b[1] + oy], abs=1e-3)
    assert [b["parent"] for b in raw["bones"]] == [None, "body", "shoulder"]
    assert {p["bone"] for p in raw["parts"] if p["name"] == "arm"} == {"arm"}
    # the arm is lowered at frame 12 (its tip goes down on screen) and raised at frame 18
    sign = man["keys"]["lower_sign"]
    dx = got["arm"]["tip_px"][0] - got["arm"]["base_px"][0]
    assert sign == (-1.0 if dx > 0 else 1.0)


def test_right_arm_and_source_canvas(tmp_path):
    man = build_poc(PACKAGES["schoolboy"], tmp_path, side="r", canvas=None)
    assert man["moho_canvas"] == man["source_canvas"]
    assert man["placement"]["offset_px"] == [0, 0]
    assert man["keys"]["lower_sign"] == 1.0  # the right arm points to image left
    assert man["asset_root"] is None


# --- the scripts under the strict mock -------------------------------------------------------------

def test_renamed_build_script_keeps_phase0_logic():
    spec, _, _ = load_poc_spec()
    src = render_build_lua(spec, "/Volumes/SSD/x")
    assert SCRIPT_NAME not in src and "AutoRig Phase 0" not in src
    assert f'ScriptName = "{BUILD_SCRIPT}"' in src
    # same template body: only names and labels differ
    p0 = render_lua(spec, "/Volumes/SSD/x")
    assert len(src.splitlines()) == len(p0.splitlines())


def load_poc_spec():
    from autorig.spec import load_spec
    return load_spec(ROOT / "examples/phase0_test_character/rig_spec.json"), None, None


@pytest.mark.parametrize("name", list(PACKAGES))
def test_build_script_passes_phase0_verifier_in_mock(pocs, name):
    d, man = pocs[name]
    lua, mock, env = _run_build_and_verify(d, man, reopen=False)
    spec, _, _ = load_poc(d)
    rep = json.loads((d / BUILD_REPORT).read_text())
    v = verify_report(spec, rep)
    assert v.passed, v.format()
    assert origin(rep) == "mock"
    snap = lua_to_py(mock.snapshot(env))
    bone_layer = next(l for l in snap["layers"] if l["name"] == BONE_LAYER)
    assert [c["name"] for c in bone_layer["children"]] == [p.layer_name for p in spec.parts_back_to_front()]


@pytest.mark.parametrize("name", list(PACKAGES))
def test_verify_script_after_simulated_reopen(pocs, name):
    d, man = pocs[name]
    lua, mock, env = _run_build_and_verify(d, man)
    spec, raw, _ = load_poc(d)
    rep = json.loads((d / VERIFY_REPORT).read_text())
    assert rep["status"] == "completed", rep["errors"]
    assert all(c["status"] == "PASS" for c in check_reopened(spec, raw, rep))
    assert all(c["status"] in ("PASS", "INFO") for c in check_move(spec, raw, rep))
    assert rep["move"]["frame"] == FRAME_REOPEN
    assert rep["api_surface"]["ScriptInterface"]["called"] is False
    alerts = lua_to_py(env.alerts)
    assert alerts[-1]["kind"] == 1 and "keyed at frame 18" in alerts[-1]["line1"]


def test_verify_script_without_the_rig_changes_nothing(pocs):
    d, man = pocs["schoolboy"]
    lua = new_lua()
    mock = load_mock(lua)
    env = mock.new_env(lua.table_from({"width": 1920, "height": 1080}, recursive=True))
    (d / VERIFY_REPORT).unlink(missing_ok=True)
    lua.execute((d / man["scripts"]["verify"]).read_text(encoding="utf-8"))
    v = lua.globals()[VERIFY_SCRIPT]
    v.Run(v, env.moho)
    rep = json.loads((d / VERIFY_REPORT).read_text())
    assert rep["status"] == "aborted"
    assert BONE_LAYER in rep["errors"][0]
    snap = lua_to_py(mock.snapshot(env))
    assert [l["name"] for l in snap["layers"]] == ["Layer 1"]


def test_reference_verify_script_refuses_to_run(pocs):
    d, man = pocs["schoolboy"]
    spec, raw, _ = load_poc(d)
    lua = new_lua()
    mock = load_mock(lua)
    env = mock.new_env(lua.table_from({}))
    lua.execute(render_verify_lua(spec, raw, None))
    v = lua.globals()[VERIFY_SCRIPT]
    v.Run(v, env.moho)
    alerts = lua_to_py(env.alerts)
    assert "reference build" in alerts[0]["line2"]


# --- HARD GATE 4.0 ---------------------------------------------------------------------------------

def test_gate_never_passes_on_mock_evidence(pocs, tmp_path):
    d, man = pocs["schoolboy"]
    ev = _evidence(d, man, tmp_path)
    res = evaluate_gate(d, ev, renders_simulated=True)
    assert res["verdict"] == VERDICT_BLOCKED and not res["passed"]
    status = {i["id"]: i["status"] for i in res["items"]}
    assert status == {"native_document": "MOCK_ONLY", "bone_moves_artwork": "MOCK_ONLY",
                      "document_saved": "NOT_EXECUTED", "document_reopened": "MOCK_ONLY",
                      "bone_functional_after_reopen": "MOCK_ONLY"}
    assert res["checks"]["renders"]["status"] == "PASS"  # the comparison itself works on matching frames


def _fake_native(ev: Path, spec) -> None:
    """TEST FIXTURE ONLY: relabel mock reports as native and write a minimal saved file,
    to exercise the verifier's acceptance path. Never evidence."""
    for f in (BUILD_REPORT, VERIFY_REPORT):
        r = json.loads((ev / f).read_text())
        r["moho_app_version"] = "test-fixture-native"
        (ev / f).write_text(json.dumps(r))
    names = [spec.bone_layer_name] + [b.name for b in spec.bones] + [p.layer_name for p in spec.parts]
    with zipfile.ZipFile(ev / SAVED_NAME, "w") as z:
        z.writestr("Project.mohoproj", json.dumps({"layers": [{"name": n} for n in names]}))


def test_gate_acceptance_path_with_fixture_inputs(pocs, tmp_path):
    d, man = pocs["tough_student"]
    ev = _evidence(d, man, tmp_path)
    spec, _, _ = load_poc(d)
    _fake_native(ev, spec)
    res = evaluate_gate(d, ev, renders_simulated=False)
    assert res["verdict"] == VERDICT_PASS, [(i["id"], i["status"], i["detail"]) for i in res["items"]]


def test_gate_detects_a_frozen_arm(pocs, tmp_path):
    d, man = pocs["schoolboy"]
    ev = _evidence(d, man, tmp_path)
    spec, _, _ = load_poc(d)
    _fake_native(ev, spec)
    shutil.copy(ev / "renders/frame_0001.png", ev / "renders/frame_0012.png")
    shutil.copy(ev / "renders/frame_0001.png", ev / "renders/frame_0018.png")
    res = evaluate_gate(d, ev)
    status = {i["id"]: i["status"] for i in res["items"]}
    assert status["bone_moves_artwork"] == "FAIL"
    assert status["bone_functional_after_reopen"] == "FAIL"
    assert res["verdict"] == VERDICT_BLOCKED


def test_gate_detects_misaligned_art(pocs, tmp_path):
    d, man = pocs["schoolboy"]
    ev = _evidence(d, man, tmp_path)
    for f in (1, 12, 18):  # e.g. Moho fitting the image differently: everything shifted
        p = ev / "renders" / f"frame_{f:04d}.png"
        im = Image.open(p)
        shifted = Image.new("RGB", im.size, (236, 236, 236))
        shifted.paste(im.crop((0, 0, im.width - 120, im.height)), (120, 0))
        shifted.save(p)
    cmp = compare_renders(d, {k: ev / "renders" / f"frame_{f:04d}.png"
                              for k, f in (("rest", 1), ("moved", 12), ("reopen_move", 18))})
    assert cmp["alignment"]["status"] == "FAIL"


def test_gate_accepts_half_size_renders_and_rejects_other_aspects(pocs, tmp_path):
    d, man = pocs["schoolboy"]
    ev = _evidence(d, man, tmp_path)
    paths = {k: ev / "renders" / f"frame_{f:04d}.png" for k, f in (("rest", 1), ("moved", 12), ("reopen_move", 18))}
    for p in paths.values():
        Image.open(p).resize((960, 540), Image.Resampling.LANCZOS).save(p)
    assert compare_renders(d, paths)["status"] == "PASS"
    Image.open(paths["rest"]).resize((800, 800)).save(paths["rest"])
    assert compare_renders(d, paths)["status"] == "FAIL"


def test_native_verify_cli_reports_blocked(pocs, tmp_path):
    from autorig.cli import main

    d, man = pocs["schoolboy"]
    ev = _evidence(d, man, tmp_path)
    assert main(["native-verify", str(d), "--evidence", str(ev)]) == 1
    res = json.loads((ev / "gate_4_0.json").read_text())
    assert res["verdict"] == VERDICT_BLOCKED


# --- the macOS harness ---------------------------------------------------------------------------------

def _harness(*args, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(["bash", str(HARNESS), *args], capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, timeout=120)


def test_harness_refuses_outside_macos():
    r = _harness(env_extra={"AUTORIG_HARNESS_OS": "Linux"})
    assert r.returncode == 2 and "must run on macOS" in r.stderr


def test_harness_fails_loudly_without_moho(tmp_path):
    r = _harness(env_extra={"AUTORIG_HARNESS_OS": "Darwin", "AUTORIG_MOHO_SEARCH_DIRS": str(tmp_path)})
    assert r.returncode == 3 and "Moho was not found" in r.stderr


def test_harness_refuses_app_bundle_as_content_folder(tmp_path):
    (tmp_path / "Moho.app").mkdir()
    r = _harness("--custom-content", "/Applications/Moho.app/Contents",
                 env_extra={"AUTORIG_HARNESS_OS": "Darwin", "AUTORIG_MOHO_SEARCH_DIRS": str(tmp_path)})
    assert r.returncode == 5 and "refusing to write inside an application bundle" in r.stderr


def test_harness_is_interactive_only(tmp_path):
    (tmp_path / "Moho.app").mkdir()
    r = _harness(env_extra={"AUTORIG_HARNESS_OS": "Darwin", "AUTORIG_MOHO_SEARCH_DIRS": str(tmp_path),
                            "PYTHON": str(ROOT / ".venv/bin/python") if (ROOT / ".venv/bin/python").exists() else "python3"})
    assert r.returncode == 5 and "interactive" in r.stderr


def test_harness_is_bash32_compatible():
    text = HARNESS.read_text()
    subprocess.run(["bash", "-n", str(HARNESS)], check=True)
    for construct in ("declare -A", "mapfile", "readarray", ",,}", "^^}", "|&", "&>>", "local -n"):
        assert construct not in text, construct
    writes = [l for l in text.splitlines() if l.strip().startswith(("cp ", "mkdir ", "rm ", "mv "))]
    assert writes and not any("/Applications" in l or "MOHO_APP" in l for l in writes)
