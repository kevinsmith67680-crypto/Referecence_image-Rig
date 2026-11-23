"""Phase 4.1: full-body native rig package, skeleton, coordinates, bindings, scripts under the strict mock,
the Phase 4.1 gate checker and the harness's Phase 4.1 argument checks.

Nothing here runs Moho. Every report below is written by the strict mock and every "render" is the Python
simulation, so the gates must stay MOCK_ONLY / NOT_EXECUTED. One test feeds the checker fabricated "native"
inputs to prove its acceptance path; those are test fixtures, never evidence.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from autorig.geometry import compute_rest_pose, forward_kinematics
from autorig.native.binding import CLASSES
from autorig.native.fullbody import (BUILD_REPORT, BUILD_SCRIPT, IK_REPORT, IK_SCRIPT, VERIFY_REPORT, VERIFY_SCRIPT,
                                     NativeFullbodyError, build_fullbody, check_pose_limits, load_fullbody,
                                     test_poses as fb_poses)
from autorig.native.gate41 import VERDICT_BLOCKED, VERDICT_PASS, evaluate_gate41
from autorig.native.skeleton import TARGETS, NativeSkeletonSpec, Placement, validate
from autorig.phase2.store import pixel_hash
from autorig.report import verify_report

from conftest import load_mock, lua_to_py, new_lua

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "schoolboy": ROOT / "projects/schoolboy_tpose/character_package",
    "tough_student": ROOT / "projects/tough_student_test/character_package",
}
SEMANTIC = ["ROOT", "PELVIS", "SPINE_LOWER", "SPINE_UPPER", "CHEST", "NECK", "HEAD"] + [
    f"{S}_{p}" for S in ("LEFT", "RIGHT") for p in ("SHOULDER", "UPPER_ARM", "FOREARM", "HAND")] + [
    f"{S}_{p}" for S in ("LEFT", "RIGHT") for p in ("THIGH", "SHIN", "FOOT")]
HARNESS = ROOT / "tools/macos/phase4_native_test.sh"


@pytest.fixture(scope="module")
def skeletons():
    return {n: NativeSkeletonSpec.from_package(p) for n, p in PACKAGES.items()}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = {}
    for name, pkg in PACKAGES.items():
        d = tmp_path_factory.mktemp(f"fb_{name}")
        man = build_fullbody(pkg, d, asset_root=str(d.resolve()))
        out[name] = (d, man)
    return out


# --- skeleton ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(PACKAGES))
def test_skeleton_has_every_semantic_bone_and_control(skeletons, name):
    sk = skeletons[name]
    ids = sk.ids()
    assert set(SEMANTIC) <= set(ids) and set(TARGETS) <= set(ids)
    assert len(ids) == 25 and validate(sk) == []
    parents = {b.id: b.parent for b in sk.bones}
    assert parents["PELVIS"] == "ROOT" and parents["LEFT_THIGH"] == "PELVIS" and parents["RIGHT_SHIN"] == "RIGHT_THIGH"
    assert parents["LEFT_SHOULDER"] == "CHEST" and parents["LEFT_HAND"] == "LEFT_FOREARM"
    assert parents["NECK"] == "CHEST" and parents["HEAD"] == "NECK"
    assert all(parents[t] == "ROOT" for t in TARGETS)
    assert set(sk.controls()) == {"ROOT_CONTROL", "PELVIS_CONTROL", "HEAD_CONTROL", *TARGETS}


def test_descendants_follow_the_hierarchy(skeletons):
    sk = skeletons["schoolboy"]
    left = set(sk.descendants("LEFT_UPPER_ARM"))
    assert left == {"LEFT_FOREARM", "LEFT_HAND"}
    assert not any(b.startswith("RIGHT_") for b in left)
    assert set(sk.descendants("PELVIS")) >= {"LEFT_FOOT", "RIGHT_FOOT", "HEAD", "LEFT_HAND"}
    assert not set(sk.descendants("PELVIS")) & set(TARGETS)


@pytest.mark.parametrize("name", list(PACKAGES))
def test_bones_come_from_the_package_rendering_skeleton(skeletons, name):
    rig = json.loads((PACKAGES[name] / "natural_pose/rig.json").read_text())
    struct = json.loads((PACKAGES[name] / "natural_pose/garments/structure.json").read_text())
    pkg_bones = {b["name"]: b for b in rig["bones"]}
    sk = skeletons[name]
    assert sk.bone("PELVIS").base_px == pytest.approx(tuple(pkg_bones["pelvis"]["head"]), abs=1e-3)
    assert sk.bone("LEFT_SHIN").tip_px == pytest.approx(tuple(pkg_bones["shin_l"]["tail"]), abs=1e-3)
    aligned = {p["bone"]: p["pivot"] for p in struct["pivots"] if p.get("joint", "").startswith("shoulder_")}
    assert sk.bone("LEFT_UPPER_ARM").base_px == pytest.approx(tuple(aligned["upper_arm_l"]), abs=0.15)
    assert sk.bone("RIGHT_SHOULDER").tip_px == sk.bone("RIGHT_UPPER_ARM").base_px
    assert sk.bone("ROOT").base_px == pytest.approx((rig["midline_x"], rig["ground_y"]), abs=1e-3)


def test_proportions_are_each_characters_own(skeletons):
    a, b = skeletons["schoolboy"], skeletons["tough_student"]
    diffs = [abs(a.bone(i).length_px - b.bone(i).length_px) for i in SEMANTIC]
    assert max(diffs) > 5.0  # different characters, different measured lengths
    assert a.bone("LEFT_THIGH").length_px != b.bone("LEFT_THIGH").length_px


@pytest.mark.parametrize("name", list(PACKAGES))
def test_limits_contain_rest_and_every_test_pose(skeletons, name):
    sk = skeletons[name]
    for b in sk.bones:
        if b.limits_deg:
            assert b.limits_deg[0] <= 0.0 <= b.limits_deg[1], b.id
    assert check_pose_limits(sk, fb_poses(sk)) == []
    # stylised front-view elbow: bends toward the head; hyperextension per config
    fa = sk.bone("LEFT_FOREARM").limits_deg
    assert fa[1] >= 140 and fa[0] == pytest.approx(-10)


def test_character_configuration_overrides_a_limit(skeletons, tmp_path):
    sk = NativeSkeletonSpec.from_package(PACKAGES["schoolboy"], config={"limits_deg": {"LEFT_UPPER_ARM": [-5, 5]}})
    assert sk.bone("LEFT_UPPER_ARM").limits_deg == (-5.0, 5.0)
    problems = check_pose_limits(sk, fb_poses(sk))
    assert any("LEFT_UPPER_ARM" in p for p in problems)
    with pytest.raises(NativeFullbodyError):
        build_fullbody(PACKAGES["schoolboy"], tmp_path / "x", config={"limits_deg": {"LEFT_UPPER_ARM": [-5, 5]}},
                       render_expected=False)


# --- coordinates ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("canvas,offset", [((1086, 1448), (0, 0)), ((1270, 1486), (87, 38)), ((640, 480), (-200, 10)),
                                           ((4000, 1000), (1500, -300))])
def test_placement_round_trip(canvas, offset):
    pl = Placement(canvas, offset)
    for p in [(0.0, 0.0), (542.2, 753.8), (1085.5, 1447.5), (-10.0, 3.3)]:
        assert pl.from_canvas(pl.to_canvas(p)) == pytest.approx(p)
        assert pl.from_moho(pl.to_moho(p)) == pytest.approx(p, abs=1e-9)


@pytest.mark.parametrize("canvas,offset", [((1086, 1448), (0, 0)), ((1270, 1486), (87, 38)), ((2200, 2000), (500, 300))])
def test_parent_relative_values_reproduce_world_and_scale_once(skeletons, canvas, offset):
    from autorig.spec import parse_spec

    sk = skeletons["schoolboy"]
    pl = Placement(canvas, offset)
    raw = {"spec_version": "0.1.0", "character": {"id": "t", "display_name": "t"},
           "canvas": {"width_px": canvas[0], "height_px": canvas[1]},
           "moho": {"bone_layer_name": "B", "rest_frame": 0, "start_frame": 1, "end_frame": 2, "report_filename": "r.json"},
           "bones": sk.spec_bones(pl), "parts": [{"name": "p", "layer_name": "p", "image": "p.png", "bone": "ROOT",
                                                  "z_index": 0}], "animation": {"name": "a", "tracks": []}}
    rest = compute_rest_pose(parse_spec(raw))
    fk = forward_kinematics(rest, {})
    h = canvas[1] / 2.0
    for r in rest:
        assert fk[r.name].world_base == pytest.approx(r.world_base, abs=1e-9)
        # length in Moho units times half the frame height = the bone's pixel length: one scale, applied once
        assert r.length * h == pytest.approx(sk.bone(r.name).length_px, abs=2e-3)
        assert pl.from_moho(r.world_base) == pytest.approx(sk.bone(r.name).base_px, abs=2e-3)


# --- package --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(PACKAGES))
def test_package_artwork_is_original_unscaled_and_complete(built, name):
    d, man = built[name]
    pkg = PACKAGES[name]
    am = json.loads((pkg / "artwork/manifest.json").read_text())
    by = {l["id"]: l for l in am["layers"]}
    assert man["layers"]["count"] == len(am["layers"]) and man["layers"]["all_originals_unchanged"]
    ox, oy = man["placement"]["offset_px"]
    sw, sh = man["source_canvas"]
    cw, ch = man["moho_canvas"]
    assert man["placement"]["scale"] == 1 and cw >= sw and ch >= sh
    for lid, f in man["layers"]["files"].items():
        art = np.asarray(Image.open(d / f["path"]).convert("RGBA"))
        assert art.shape[:2] == (ch, cw)
        src = np.asarray(Image.open(pkg / by[lid]["image"]).convert("RGBA"))
        assert pixel_hash(src) == by[lid]["pixel_sha256"]
        assert np.array_equal(art[oy:oy + sh, ox:ox + sw], src)
        assert not art[..., 3].any() or (art[..., 3] > 0).sum() == (src[..., 3] > 0).sum()  # nothing cropped
    assert man["reference"] and (d / man["reference"][0]).is_file()


@pytest.mark.parametrize("name", list(PACKAGES))
def test_binding_plan(built, name):
    d, man = built[name]
    plan = json.loads((d / "binding_plan.json").read_text())
    by = {l["layer"]: l for l in plan["layers"]}
    assert all(l["binding_class"] in CLASSES and l["native_method"] == "layer_binding" for l in plan["layers"])
    assert by["torso"]["bone"] == "CHEST" and by["pelvis"]["bone"] == "PELVIS" and by["neck"]["bone"] == "NECK"
    assert all(by[c]["bone"] == "HEAD" and by[c]["binding_class"] == "RIGID"
               for c in ("head_base", "hair", "mouth", "eye_l_iris"))
    assert by["hand_l"]["bone"] == "LEFT_HAND" and by["hand_l"]["binding_class"] == "SWITCHABLE"
    assert by["upper_arm_r"]["bone"] == "RIGHT_UPPER_ARM" and by["upper_arm_r"]["binding_class"] == "FLEXIBLE"
    assert by["foot_l"]["bone"] == "LEFT_FOOT" and by["foot_l"]["binding_class"] == "RIGID"
    assert [l["z_index"] for l in plan["layers"]] == list(range(len(plan["layers"])))
    assert plan["rules"] == {"one_bone_per_layer": True, "global_flexible_binding": False, "weights_imported": False,
                             "weights_note": plan["rules"]["weights_note"]}
    assert plan["metadata_only"]["SWITCHABLE"] > 0
    assert all(s["approval"] != "approved" for s in plan["switchable"])


@pytest.mark.parametrize("name", list(PACKAGES))
def test_hand_attachment_ready_for_switch_layers(built, skeletons, name):
    d, man = built[name]
    sk = skeletons[name]
    for S in ("LEFT", "RIGHT"):
        h = man["hand_readiness"][S]
        assert h["bone"] == f"{S}_HAND" and tuple(h["wrist_pivot_px"]) == sk.bone(f"{S}_HAND").base_px
        assert h["bound_layers"] == [f"hand_{S[0].lower()}"] and h["approved_variants"] == 0


def test_every_test_pose_stays_on_the_canvas(built):
    for d, man in built.values():
        spec, raw, _, _ = load_fullbody(d)
        for f, rel in man["expected"].items():
            if f == "sheet":
                continue
            a = np.asarray(Image.open(d / rel).convert("RGBA"))[..., 3]
            assert not a[0].any() and not a[-1].any() and not a[:, 0].any() and not a[:, -1].any(), f


def test_export_is_reproducible(built, tmp_path):
    d, man = built["schoolboy"]
    man2 = build_fullbody(PACKAGES["schoolboy"], tmp_path / "again", asset_root=str(d.resolve()), render_expected=False)
    for rel in ("rig_spec.json", "native_skeleton.json", "binding_plan.json", "validation_plan.json"):
        assert (d / rel).read_bytes() == (tmp_path / "again" / rel).read_bytes(), rel
    assert {k: v["pixel_sha256"] for k, v in man["layers"]["files"].items()} == \
           {k: v["pixel_sha256"] for k, v in man2["layers"]["files"].items()}
    assert man["spec_sha256"] == man2["spec_sha256"]


def test_invalid_packages_are_rejected(tmp_path):
    with pytest.raises(NativeFullbodyError) as e:
        build_fullbody(tmp_path / "nope", tmp_path / "out")
    assert any("missing" in p for p in e.value.problems)
    with pytest.raises(NativeFullbodyError) as e:
        build_fullbody(PACKAGES["schoolboy"], tmp_path / "small", canvas=(1086, 1448), render_expected=False)
    assert "cropping" in str(e.value)


# --- scripts under the strict mock -----------------------------------------------------------------------------

def _run_all(d: Path, man: dict, scripts=("build", "verify", "ik")):
    lua = new_lua()
    mock = load_mock(lua)
    w, h = man["moho_canvas"]
    env = mock.new_env(lua.table_from({"width": w, "height": h}, recursive=True))
    names = {"build": BUILD_SCRIPT, "verify": VERIFY_SCRIPT, "ik": IK_SCRIPT}
    for key in scripts:
        lua.execute((d / man["scripts"][key]).read_text(encoding="utf-8"))
        s = lua.globals()[names[key]]
        s.Run(s, env.moho)
    return lua, mock, env


@pytest.mark.parametrize("name", list(PACKAGES))
def test_full_body_scripts_in_the_strict_mock(built, name):
    d, man = built[name]
    lua, mock, env = _run_all(d, man)
    spec, raw, _, _ = load_fullbody(d)
    b = json.loads((d / BUILD_REPORT).read_text())
    v = verify_report(spec, b)
    assert v.passed, [c for c in v.checks if c.status == "FAIL"][:5]
    assert len(b["bones"]) == 25 and len(b["layers"]) == man["layers"]["count"]
    vr = json.loads((d / VERIFY_REPORT).read_text())
    assert vr["status"] == "completed" and vr["moves"][0]["bone"] == "PELVIS"
    assert all(k["set"] == pytest.approx(k["read"]) for k in vr["moves"][0]["keys"])
    assert all(bn["optional"]["constraints"] in (False, True) for bn in vr["bones"])
    ik = json.loads((d / IK_REPORT).read_text())
    assert ik["status"] == "completed" and ik["failed_operations"] == 0
    names = {o["name"].split(":")[0] for o in ik["operations"]}
    assert names == {"target", "constraint", "move", "clamp_test", "end_frame"}
    snap = lua_to_py(mock.snapshot(env))
    rig = next(l for l in snap["layers"] if l["name"] == spec.bone_layer_name)
    assert [c["name"] for c in rig["children"]] == [p.layer_name for p in spec.parts_back_to_front()]


def test_verify_without_the_rig_aborts(built):
    d, man = built["schoolboy"]
    lua = new_lua()
    mock = load_mock(lua)
    env = mock.new_env(lua.table_from({"width": 100, "height": 100}, recursive=True))
    lua.execute((d / man["scripts"]["verify"]).read_text(encoding="utf-8"))
    s = lua.globals()[VERIFY_SCRIPT]
    s.Run(s, env.moho)
    vr = json.loads((d / VERIFY_REPORT).read_text())
    assert vr["status"] == "aborted" and "AutoRig_P41_FullBody" in vr["errors"][0]


# --- the Phase 4.1 gates ----------------------------------------------------------------------------------------

def _evidence(d: Path, man: dict, tmp: Path, ik: bool = True) -> Path:
    _run_all(d, man, ("build", "verify", "ik") if ik else ("build", "verify"))
    ev = tmp / "ev"
    ev.mkdir()
    for f in (BUILD_REPORT, VERIFY_REPORT) + ((IK_REPORT,) if ik else ()):
        shutil.copy(d / f, ev / f)
    for sub, keys in (("renders", [k for k in man["expected"] if k.isdigit() and int(k) <= 110]),
                      ("renders_ik", [k for k in man["expected"] if k.isdigit() and int(k) > 110] + ["160_clamped"])):
        (ev / sub).mkdir()
        for k in keys:
            im = Image.open(d / man["expected"][k]).convert("RGBA")
            flat = Image.new("RGBA", im.size, (234, 234, 234, 255))
            flat.alpha_composite(im)
            flat.convert("RGB").save(ev / sub / f"frame_{int(k.split('_')[0]):04d}.png")
    return ev


def test_gates_never_pass_on_mock_evidence(built, tmp_path):
    d, man = built["schoolboy"]
    ev = _evidence(d, man, tmp_path)
    res = evaluate_gate41(d, ev, character_package=PACKAGES["schoolboy"], renders_simulated=True)
    assert res["verdict"] == VERDICT_BLOCKED
    assert {g["status"] for g in res["gates"]} <= {"MOCK_ONLY", "NOT_EXECUTED"}
    st = {g["id"]: g["status"] for g in res["gates"]}
    assert st["4.1-H"] == "NOT_EXECUTED"   # no saved document
    assert st["4.1-D"] == "NOT_EXECUTED"   # nobody dragged a target


def test_gates_with_no_evidence_are_not_executed(built):
    d, man = built["tough_student"]
    res = evaluate_gate41(d, None)
    assert all(g["status"] == "NOT_EXECUTED" for g in res["gates"])


def _fixture_saved(ev: Path, spec) -> None:
    """TEST FIXTURE ONLY: a saved project in the observed Moho 14.5.1 layout, built from the spec."""
    import zipfile
    from test_p4_native import _fixture_project
    with zipfile.ZipFile(ev / "AutoRig_P41_FullBody.moho", "w") as z:
        z.writestr("Project.mohoproj", json.dumps(_fixture_project(spec)))


def test_gate_acceptance_path_with_fixture_inputs(built, tmp_path):
    d, man = built["tough_student"]
    ev = _evidence(d, man, tmp_path)
    spec, raw, _, _ = load_fullbody(d)
    for f in (BUILD_REPORT, VERIFY_REPORT, IK_REPORT):
        r = json.loads((ev / f).read_text())
        r["moho_app_version"] = "test-fixture-native"
        (ev / f).write_text(json.dumps(r))
    _fixture_saved(ev, spec)
    (ev / "observations.json").write_text(json.dumps({"ik_drag_arms": "yes", "ik_drag_legs": "yes",
                                                      "constraint_drag_stops_at_limit": "yes"}))
    res = evaluate_gate41(d, ev, character_package=PACKAGES["tough_student"], renders_simulated=False)
    assert res["verdict"] == VERDICT_PASS, [(g["id"], g["status"], g["detail"][:120]) for g in res["gates"]]


def test_gate_detects_frozen_artwork_and_wrong_character(built, tmp_path):
    d, man = built["schoolboy"]
    ev = _evidence(d, man, tmp_path)
    for f in (BUILD_REPORT, VERIFY_REPORT, IK_REPORT):
        r = json.loads((ev / f).read_text())
        r["moho_app_version"] = "test-fixture-native"
        (ev / f).write_text(json.dumps(r))
    shutil.copy(ev / "renders/frame_0001.png", ev / "renders/frame_0010.png")   # left arm did not move
    res = evaluate_gate41(d, ev, character_package=PACKAGES["tough_student"])
    st = {g["id"]: g["status"] for g in res["gates"]}
    assert st["4.1-C"] == "FAIL" and st["4.1-J"] == "FAIL"
    assert st["4.1-L"] == "FAIL"   # the skeleton re-derived from another character does not match


# --- harness ------------------------------------------------------------------------------------------------------

def _harness(*args, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(["bash", str(HARNESS), *args], capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, timeout=120)


def test_harness_phase_arguments(tmp_path):
    fake = {"AUTORIG_HARNESS_OS": "Darwin", "AUTORIG_MOHO_SEARCH_DIRS": str(tmp_path)}
    r = _harness("--phase", "4.3", env_extra=fake)  # 4.2 exists since Phase 4.2; 4.3 is the unknown phase now
    assert r.returncode == 5 and "--phase must be 4.0 or 4.1" in r.stderr
    r = _harness("--with-ik", env_extra=fake)
    assert r.returncode == 5 and "--with-ik belongs to --phase 4.1" in r.stderr
    r = _harness("--phase", "4.1", env_extra=fake)
    assert r.returncode == 3 and "Moho was not found" in r.stderr


def test_harness_phase41_flow_names_match_the_package():
    text = HARNESS.read_text()
    for s in (BUILD_SCRIPT, VERIFY_SCRIPT, IK_SCRIPT, BUILD_REPORT, VERIFY_REPORT, IK_REPORT, "native-fullbody-verify",
              "native_runs_4_1", "AutoRig_P41_FullBody.moho"):
        assert s in text, s
    assert 'rm -f "${WORK:?}/${stale:?}"' in text
