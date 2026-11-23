"""Phase 4.2: joint deformation audit, hybrid native rig (split limb roots, layer order, specks), native gap fills,
corrective activation, the hybrid package and scripts under the strict mock, the Phase 4.2 gate checker and the
harness's Phase 4.2 argument checks, on both character packages.

Nothing here runs Moho. Reports come from the strict mock and "renders" from the Python simulation, so the gates must
stay MOCK_ONLY / MEASURED / NOT_EXECUTED. One test relabels mock reports as native to exercise the person-recorded
gates; those are clearly named test fixtures, never evidence.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from scipy import ndimage

from autorig.garmentrig.correctives import CorrectiveArtworkStore
from autorig.native.deform_audit import CAUSES, JOINTS, JointAudit, classify, diagnose
from autorig.native.fullbody import check_pose_limits
from autorig.native.gate42 import VERDICT_BLOCKED, VERDICT_PASS, evaluate_gate42
from autorig.native.hybrid import (KEY_TOLERANCE, SPECK_FRAC, SPECK_MAX_PX, VARIANTS, HLayer, Simulator,
                                   active_correctives, assemble, base_layers, poses_42, rest_abduction)
from autorig.native.hybrid_build import (BUILD_REPORT, BUILD_SCRIPT, VERIFY_REPORT, VERIFY_SCRIPT, build_hybrid,
                                         load_hybrid)
from autorig.native.skeleton import NativeSkeletonSpec
from autorig.report import verify_report

from conftest import load_mock, new_lua

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {
    "schoolboy": ROOT / "projects/schoolboy_tpose/character_package",
    "tough_student": ROOT / "projects/tough_student_test/character_package",
}
HARNESS = ROOT / "tools/macos/phase4_native_test.sh"


@pytest.fixture(scope="module")
def chars():
    out = {}
    for name, pkg in PACKAGES.items():
        sk = NativeSkeletonSpec.from_package(pkg)
        out[name] = (pkg, sk, base_layers(pkg, sk))
    return out


@pytest.fixture(scope="module")
def built(tmp_path_factory, chars):
    out = {}
    for name, pkg in PACKAGES.items():
        d = tmp_path_factory.mktemp(f"hy_{name}")
        out[name] = (d, build_hybrid(pkg, d, asset_root=str(d.resolve())))
    return out


def _composite(layers: list[HLayer]) -> np.ndarray:
    acc = np.zeros(layers[0].rgba.shape, np.float64)
    for l in sorted(layers, key=lambda l: l.z):
        a = l.rgba[..., 3:4] / 255.0
        acc[..., :3] = l.rgba[..., :3] * a + acc[..., :3] * (1 - a)
        acc[..., 3:4] = a + acc[..., 3:4] * (1 - a)
    return acc


# --- poses ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(PACKAGES))
def test_poses_are_the_requested_ten_and_inside_the_ranges(chars, name):
    _, sk, _ = chars[name]
    poses = poses_42(sk)
    assert [p["frame"] for p in poses] == [1, 10, 20, 30, 40, 50, 60, 70, 80, 90]
    assert [p["id"] for p in poses][1:4] == ["both_arms_lowered", "left_arm_raised", "right_arm_raised"]
    assert check_pose_limits(sk, poses) == []
    assert [p["id"] for p in poses if p["after_reopen"]] == ["torso_leaning"]
    assert all("REQUIRES_NEW_VIEW" in p["view_note"] for p in poses if "knee" in p["id"])
    assert poses[0]["offsets_deg"] == {} and poses[-1]["offsets_deg"] == {}
    lowered = poses[1]["offsets_deg"]
    assert set(lowered) == {"LEFT_UPPER_ARM", "RIGHT_UPPER_ARM"}  # no clavicle drop (it cracked the lapel)


# --- layer assembly ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(PACKAGES))
def test_split_and_specks_are_an_exact_partition(chars, name):
    pkg, sk, base = chars[name]
    layers = assemble(pkg, sk, "hybrid", base)
    for src in base[0]:
        pieces = [l for l in layers if l.source_layer == src.name]
        acc = np.zeros_like(src.rgba)
        for l in pieces:
            m = l.rgba[..., 3] > 0
            assert not (acc[..., 3][m] > 0).any(), f"{l.name} overlaps another piece"
            acc[m] = l.rgba[m]
        assert np.array_equal(acc, src.rgba), src.name
    roots = [l for l in layers if l.name.endswith("__joint")]
    assert {l.bone for l in roots} <= {"LEFT_SHOULDER", "RIGHT_SHOULDER", "PELVIS"} and len(roots) >= 3


@pytest.mark.parametrize("name", list(PACKAGES))
def test_rest_composite_is_unchanged_by_the_hybrid_steps(chars, name):
    pkg, sk, base = chars[name]
    ref = _composite(assemble(pkg, sk, "rigid_4_1", base))
    for v in ("order", "split", "hybrid"):
        diff = np.abs(_composite(assemble(pkg, sk, v, base)) - ref)
        assert diff.max() <= 1.0, (v, float(diff.max()))  # draw order only matters on shared antialiased edges


@pytest.mark.parametrize("name", list(PACKAGES))
def test_arm_chains_are_drawn_above_the_torso(chars, name):
    pkg, sk, base = chars[name]
    layers = assemble(pkg, sk, "order", base)
    torso = max(l.z for l in layers if l.bone == "CHEST")
    for l in layers:
        if l.bone.endswith(("UPPER_ARM", "FOREARM", "HAND")):
            assert l.z > torso, l.name


@pytest.mark.parametrize("name", list(PACKAGES))
def test_limb_specks_stay_with_their_neighbour(chars, name):
    pkg, sk, base = chars[name]
    layers = assemble(pkg, sk, "hybrid", base)
    for l in layers:
        if l.bone in ("ROOT", "PELVIS", "SPINE_LOWER", "SPINE_UPPER", "CHEST", "NECK", "HEAD"):
            continue
        lab, n = ndimage.label(l.rgba[..., 3] > 0, structure=np.ones((3, 3)))
        if n < 2:
            continue
        sizes = ndimage.sum(np.ones(lab.shape), lab, range(1, n + 1))
        main = sizes.max()
        for i, s in enumerate(sizes, 1):
            if s == main or s > min(SPECK_MAX_PX, SPECK_FRAC * main):
                continue
            near = ndimage.binary_dilation(lab == i, iterations=4)
            touching = [o.name for o in layers if o.bone != l.bone and (near & (o.rgba[..., 3] > 0)).any()]
            assert not touching, f"{l.name}: a {int(s)} px speck touching {touching} still moves with {l.bone}"


def test_unknown_variant_is_refused(chars):
    pkg, sk, base = chars["schoolboy"]
    with pytest.raises(ValueError):
        assemble(pkg, sk, "nonsense", base)
    assert set(VARIANTS) >= {"rigid_4_1", "order", "split", "hybrid", "hybrid_review", "hybrid_correctives"}


# --- correctives --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(PACKAGES))
def test_correctives_are_pending_beneath_and_invisible_at_rest(chars, name):
    pkg, sk, base = chars[name]
    layers = assemble(pkg, sk, "hybrid_correctives", base)
    corr = [l for l in layers if l.status.endswith("corrective")]
    assert corr and all(l.status == "pending_corrective" for l in corr)
    lowest_original = min(l.z for l in layers if l.status in ("original", "derived_partition"))
    assert all(l.z < lowest_original for l in corr)
    sim = Simulator(sk, rig=json.loads((pkg / "natural_pose/rig.json").read_text()))
    rest = {"offsets_deg": {}}
    plain = sim.render(assemble(pkg, sk, "hybrid", base), rest)
    assert np.array_equal(sim.render(layers, rest), plain)
    assert sim.active(layers, sim.world(rest)) == {l.name for l in corr
                                                    if l.extra["native_automation"] == "build_script"}


@pytest.mark.parametrize("name", list(PACKAGES))
def test_phase34_gap_fills_are_not_used_natively(chars, name):
    pkg, sk, base = chars[name]
    ctx = dict(base[1])
    layers = assemble(pkg, sk, "hybrid_correctives", (base[0], ctx))
    assert not any(l.extra.get("asset", "").startswith("armpit_gusset") for l in layers)
    assert {e["asset"] for e in ctx["excluded_correctives"]} >= {"armpit_gusset_l", "armpit_gusset_r"}


def _fill_layer(name, frac, asset="native_gap_x"):
    act = {"kind": "ramp", "driver": "bone_offset:LEFT_UPPER_ARM", "lo": 0.05, "hi": 0.15, "direction": 1.0,
           "full_deg": 60.0}
    return HLayer(name, np.zeros((2, 2, 4), np.uint8), "CHEST", -1, "CORRECTIVE", "pending_corrective", asset, "",
                  {"asset": asset, "key": name, "values": {act["driver"]: frac}, "activation": act,
                   "native_automation": "manual_switch"})


def test_switch_shows_the_largest_key_not_past_the_pose():
    layers = [_fill_layer(f"k{int(f * 100):03d}", f) for f in (0.25, 0.5, 0.75, 1.0)]
    on = lambda deg: active_correctives(layers, {"bone_offset:LEFT_UPPER_ARM": deg})  # noqa: E731
    assert on(0.0) == set() and on(-30.0) == set()
    assert on(10.0) == set()                                   # 1/6 of the pose: below the first key
    assert on(15.0) == {"k025"} and on(40.0) == {"k050"}
    assert on(45.0 - KEY_TOLERANCE * 60 + 0.1) == {"k075"}     # within the tolerance of a key
    assert on(60.0) == {"k100"} and on(80.0) == {"k100"}


@pytest.mark.parametrize("name", list(PACKAGES))
def test_driver_readings_use_the_anatomical_rest_abduction(chars, name):
    pkg, sk, _ = chars[name]
    rig = json.loads((pkg / "natural_pose/rig.json").read_text())
    sim = Simulator(sk, rig=rig)
    abd0 = rest_abduction(sk, rig)
    poses = {p["id"]: p for p in poses_42(sk)}
    r = sim.readings(sim.world(poses["left_arm_raised"]))
    assert r["abduction_l"] == pytest.approx(abd0["l"] + 60.0, abs=1e-6)
    assert r["abduction_r"] == pytest.approx(abd0["r"], abs=1e-6)
    assert r["bone_offset:LEFT_UPPER_ARM"] == pytest.approx(poses["left_arm_raised"]["offsets_deg"]["LEFT_UPPER_ARM"])
    r = sim.readings(sim.world(poses["left_elbow_90"]))
    assert r["elbow_l"] == pytest.approx(90.0, abs=1e-6)


@pytest.mark.parametrize("name", list(PACKAGES))
def test_native_fills_in_the_store_are_pending_and_hash_verified(name):
    store = CorrectiveArtworkStore(PACKAGES[name])
    assert store.verify() == []
    fills = [a for a in store.latest() if a.method == "NativeJointGapFill"]
    ids = {a.asset_id for a in fills}
    assert ids == {f"native_gap_shoulder_{s}__{p}" for s, p in (("l", "both_arms_lowered"), ("r", "both_arms_lowered"),
                                                                 ("l", "left_arm_raised"), ("r", "right_arm_raised"))} | \
        {"native_gap_hip_l__left_knee_bent", "native_gap_hip_r__right_knee_bent"}
    approvals = json.loads((PACKAGES[name] / "editor/project.json").read_text())
    text = json.dumps(approvals)
    for a in fills:
        assert a.provenance == "GEOMETRIC_CORRECTION" and a.placement.get("native") is True
        assert a.keys and all(k.get("file") for k in a.keys)
        assert f"corrective:{a.asset_id}@v{a.version}" not in text  # nothing approved or rejected in code
        assert a.moho["status"] == "proposed_unverified"


# --- audit ------------------------------------------------------------------------------------------------------

def test_cut_geometry_separates_a_knee_sliver_from_a_shoulder_cut(chars):
    pkg, sk, base = chars["schoolboy"]
    a = JointAudit.__new__(JointAudit)
    a.pkg, a.sk, a.rig, a.base = pkg, sk, json.loads((pkg / "natural_pose/rig.json").read_text()), base
    rigid = assemble(pkg, sk, "rigid_4_1", base)
    knee = a._cut_geometry(rigid, "LEFT_SHIN", JOINTS["knee_l"][1])
    shoulder = a._cut_geometry(rigid, "LEFT_UPPER_ARM", JOINTS["shoulder_l"][1])
    assert abs(knee["cut_offset_px"]) < 10 and knee["seam_along_limb_px"] > 100
    assert shoulder["cut_offset_px"] < -30  # the Phase 1 cut lies well behind the aligned shoulder pivot


def _row(**kw):
    base = {"frame": 10, "pose": "p", "hole_px": 0, "hole_px_originals_only": 0, "hidden_fill_px": None,
            "crack_px": 0, "sweep_px": 0, "occluded_px": 0, "limb_over_body_px": 0}
    base.update(kw)
    return base


def test_diagnose_decides_by_experiment():
    static = {"j": {"pivot_cut_px": 50, "cut": {"cut_offset_px": -50}, "half_thickness_px": 50, "behind_pivot_px": 900,
                    "parent_past_joint_px": 0, "corrective_store": {"approved": 0, "pending": 1}}}
    V = {"rigid_4_1": [_row(sweep_px=900, occluded_px=1000)], "order": [_row(sweep_px=900)],
         "split": [_row(hole_px=300)], "hybrid": [_row(hole_px=300, crack_px=80)],
         "hybrid_review": [_row(hole_px=40, hole_px_originals_only=300, hidden_fill_px=260)]}
    res = {"poses": [{"frame": 10}], "variants": {v: {"joints": {"j": rows}, "static": static} for v, rows in V.items()}}
    d = diagnose(res)["j"][0]["causes"]
    assert set(d) == {"LAYER_ORDER", "LAYER_CUT", "INADEQUATE_OVERLAP", "MISSING_HIDDEN_ARTWORK",
                      "MISSING_CORRECTIVE_DRAWING"}
    res["pivot_experiments"] = {"j": {"supports": "BONE_PLACEMENT", "offset_px": -50,
                                      "defect_px": {"pivot_at_cut": 10, "cut_at_pivot": 300}}}
    d = diagnose(res)["j"][0]["causes"]
    assert "BONE_PLACEMENT" in d and "LAYER_CUT" not in d
    res["poses"] = [{"frame": 10, "view_note": "depth"}]
    res["variants"]["hybrid_review"]["joints"]["j"] = [_row(hole_px=300, hole_px_originals_only=300, hidden_fill_px=0)]
    d = diagnose(res)["j"][0]["causes"]
    assert "UNSUPPORTED_VIEW" in d and "MISSING_ARTWORK" in d
    assert set(CAUSES) >= set(d)
    assert classify(_row(hole_px=200), static["j"], {}) == ["MISSING_HIDDEN_ARTWORK", "BONE_PLACEMENT",
                                                            "RIGID_BINDING_SPAN", "MISSING_CORRECTIVE_DRAWING"]


@pytest.mark.parametrize("name", list(PACKAGES))
def test_committed_audit_matches_its_schema(name):
    folder = {"schoolboy": "schoolboy_tpose", "tough_student": "tough_student_test"}[name]
    r = json.loads((ROOT / f"docs/phase4_2/audit/{folder}/joint_audit.json").read_text())
    assert r["label"].startswith("SIMULATION")
    assert set(r["variants"]) == {"rigid_4_1", "order", "split", "hybrid", "hybrid_review", "hybrid_correctives"}
    assert set(r["diagnosis"]) == set(JOINTS)
    for j in ("shoulder_l", "shoulder_r"):
        assert r["pivot_experiments"][j]["supports"] == "LAYER_CUT"
    assert (ROOT / f"docs/phase4_2/audit/{folder}/SIMULATION_joint_closeups.jpg").is_file()


# --- package, scripts, gates -------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(PACKAGES))
def test_hybrid_package_is_built_from_each_characters_own_data(built, name):
    d, man = built[name]
    other = built["tough_student" if name == "schoolboy" else "schoolboy"][1]
    assert man["layers"]["partition_exact"] is True
    assert man["variant"] == "hybrid" and "pending_corrective" not in man["layers"]["by_status"]
    assert man["moho_canvas"] != other["moho_canvas"]
    spec, raw, _ = load_hybrid(d)
    assert spec.bone_layer_name == "AutoRig_P42_Hybrid" and len(spec.bones) == 25
    lean_keys = [t for t in raw["animation"]["tracks"] if t["bone"] in ("SPINE_LOWER", "SPINE_UPPER", "CHEST")]
    assert lean_keys == []  # the lean is keyed only after reopening
    plan = json.loads((d / "smartbone_plan.json").read_text())
    manual = [a for a in plan["assets"] if a["native_automation"] == "manual_switch"]
    assert manual and all(a["approval"] == "pending" and a["driver_bone"] for a in manual)
    for a in manual:
        offs = [abs(k["bone_offset_deg"]) for k in a["keys"]]
        assert offs == sorted(offs) and all(Path(d / f["path"]).is_file() for f in a["files"].values())
    reqs = {r["id"]: r for r in json.loads((d / "artwork_requests.json").read_text())["requests"]}
    assert reqs["ART-waist"]["status"] == "MISSING"
    assert reqs["ART-knee-l"]["status"] == "REQUIRES_NEW_VIEW"
    assert reqs["ART-shoulder_lowered-l"]["status"] == "PENDING_REVIEW"
    assert all(r["status"] != "APPROVED_ARTWORK_AVAILABLE" for r in reqs.values())


def _run_mock(d: Path, man: dict):
    lua = new_lua()
    mock = load_mock(lua)
    w, h = man["moho_canvas"]
    env = mock.new_env(lua.table_from({"width": w, "height": h}, recursive=True))
    for key, name in (("build", BUILD_SCRIPT), ("verify", VERIFY_SCRIPT)):
        lua.execute((d / man["scripts"][key]).read_text(encoding="utf-8"))
        s = lua.globals()[name]
        s.Run(s, env.moho)
    return env


def _evidence(d: Path, man: dict, ev: Path) -> None:
    (ev / "renders").mkdir(parents=True, exist_ok=True)
    for f in (BUILD_REPORT, VERIFY_REPORT):
        shutil.copy(d / f, ev / f)
    for p in json.loads((d / "validation_plan.json").read_text())["poses"]:
        im = Image.open(d / man["expected"][str(p["frame"])]).convert("RGBA")
        flat = Image.new("RGBA", im.size, (236, 236, 236, 255))
        flat.alpha_composite(im)
        flat.convert("RGB").save(ev / "renders" / f"frame_{p['frame']:04d}.png")


@pytest.mark.parametrize("name", list(PACKAGES))
def test_scripts_in_the_strict_mock_and_gates_stay_blocked(built, name, tmp_path):
    d, man = built[name]
    _run_mock(d, man)
    spec, raw, _ = load_hybrid(d)
    b = json.loads((d / BUILD_REPORT).read_text())
    assert verify_report(spec, b).passed
    v = json.loads((d / VERIFY_REPORT).read_text())
    assert v["status"] == "completed" and v["kind"] == "phase4_2_hybrid_verify"
    assert {m["bone"] for m in v["moves"]} == {"SPINE_LOWER", "SPINE_UPPER", "CHEST"}
    assert all(abs(k["set"] - k["read"]) < 1e-9 for m in v["moves"] for k in m["keys"])
    assert v["actions"] == [] or isinstance(v["actions"], list)
    _evidence(d, man, tmp_path)
    res = evaluate_gate42(d, tmp_path, character_package=PACKAGES[name], renders_simulated=True)
    sts = {g["id"]: g["status"] for g in res["gates"]}
    assert res["verdict"] == VERDICT_BLOCKED
    assert not any(s == "PASS" for s in sts.values()), sts
    assert sts["4.2-I"] == "NOT_EXECUTED" and sts["4.2-J"] == "NOT_EXECUTED"
    assert evaluate_gate42(d, None)["verdict"] == VERDICT_BLOCKED


def test_person_recorded_gates_follow_the_observations(built, tmp_path):
    d, man = built["schoolboy"]
    _run_mock(d, man)
    _evidence(d, man, tmp_path)
    for f in (BUILD_REPORT, VERIFY_REPORT):  # TEST FIXTURE: mock reports relabelled as native, never evidence
        r = json.loads((tmp_path / f).read_text())
        r["moho_app_version"] = "14.5.1 (test fixture)"
        if f == VERIFY_REPORT:
            r["actions"] = [{"name": "LEFT_UPPER_ARM", "smart_bone": True}]
        (tmp_path / f).write_text(json.dumps(r))
    obs = {"smart_bone_mt1_works": "yes", "visual_review_verdict": "rejected", "visual_review_notes": "elbows"}
    (tmp_path / "observations.json").write_text(json.dumps(obs))
    res = evaluate_gate42(d, tmp_path, character_package=PACKAGES["schoolboy"])
    sts = {g["id"]: g["status"] for g in res["gates"]}
    assert sts["4.2-I"] == "PASS" and sts["4.2-J"] == "FAIL" and sts["4.2-A"] == "PASS"
    assert sts["4.2-F"] == "NOT_EXECUTED"  # no saved document: never a pass
    assert res["verdict"] == "FAIL"
    obs["visual_review_verdict"] = "accepted"
    (tmp_path / "observations.json").write_text(json.dumps(obs))
    res = evaluate_gate42(d, tmp_path, character_package=PACKAGES["schoolboy"])
    assert res["verdict"] == VERDICT_BLOCKED != VERDICT_PASS


def test_verify_template_is_phase42_and_reads_actions_in_pcall():
    t = (ROOT / "src/autorig/data/templates/phase4_2_hybrid_verify.lua").read_text()
    assert "4.1" not in t and "P41" not in t
    assert "optional(function() return list_actions(bl) end)" in t
    assert "IsSmartBoneAction" in t and "CountActions" in t


def test_review_build_adds_only_labelled_pending_underlays(tmp_path):
    man = build_hybrid(PACKAGES["tough_student"], tmp_path, review=True, render_expected=False)
    assert man["variant"] == "hybrid_correctives" and man["review_build"] is True
    st = man["layers"]["by_status"]
    assert st.get("pending_corrective", 0) > 0 and "approved_corrective" not in st
    bind = json.loads((tmp_path / "binding_plan.json").read_text())
    for l in bind["layers"]:
        if l["status"] == "pending_corrective":
            assert l["layer"].endswith("_pending") and l["binding_class"] == "CORRECTIVE"


def test_canvas_that_would_crop_is_refused(tmp_path):
    from autorig.native.hybrid_build import NativeHybridError

    with pytest.raises(NativeHybridError):
        build_hybrid(PACKAGES["schoolboy"], tmp_path, canvas=(400, 400), render_expected=False)


# --- harness --------------------------------------------------------------------------------------------------------

def _harness(*args, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(["bash", str(HARNESS), *args], capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, timeout=120)


def test_harness_phase42_arguments(tmp_path):
    fake = {"AUTORIG_HARNESS_OS": "Darwin", "AUTORIG_MOHO_SEARCH_DIRS": str(tmp_path)}
    r = _harness("--phase", "4.1", "--review", env_extra=fake)
    assert r.returncode == 5 and "--review belongs to --phase 4.2" in r.stderr
    r = _harness("--phase", "4.2", env_extra=fake)
    assert r.returncode == 3 and "Moho was not found" in r.stderr
    r = _harness("--phase", "4.2", env_extra={"AUTORIG_HARNESS_OS": "Linux"})
    assert r.returncode == 2 and "native-hybrid" in r.stderr


def test_harness_phase42_flow_names_match_the_package():
    text = HARNESS.read_text()
    for s in (BUILD_SCRIPT, VERIFY_SCRIPT, BUILD_REPORT, VERIFY_REPORT, "native-hybrid-verify", "native_runs_4_2",
              "AutoRig_P42_Hybrid.moho", "smart_bone_mt1_works", "visual_review_verdict",
              "phase4_2_verify_report_smartbone.json", "render_range \"$SAVED\" 1 90 renders"):
        assert s in text, s
