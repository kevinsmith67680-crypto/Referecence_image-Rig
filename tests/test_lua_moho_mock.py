"""Run the generated Moho menu script inside Lua 5.4 against the STRICT mock
Moho API. This proves the script's logic and that it only touches allowlisted
API members. It does NOT prove Moho behaves like the mock - see
tests/native/test_moho_native.py for the on-Mac test."""

import json
import math

import pytest

from autorig.geometry import compute_rest_pose
from autorig.lua_codegen import render_lua
from autorig.report import verify_report

from conftest import ALLOWLIST_PATH, load_mock, lua_to_py, new_lua, run_in_mock

LT_IMAGE, LT_BONE = 2, 4  # mock values (real values are read at runtime, never hard-coded)


# --- the mock itself -----------------------------------------------------------

def test_mock_surface_equals_allowlist():
    lua = new_lua()
    mock = load_mock(lua)
    mock.new_env(lua.table_from({}))
    registry = lua_to_py(mock.registry)
    implemented = {(owner, name) for owner, members in registry.items() for name in members}
    allow = {(m["owner"], m["name"]) for m in json.loads(ALLOWLIST_PATH.read_text())["members"]}
    assert implemented - allow == set(), "mock implements members without documented evidence"
    assert allow - implemented == set(), "allowlist members missing from the mock"


def test_every_allowlist_member_has_evidence():
    data = json.loads(ALLOWLIST_PATH.read_text())
    sources = set(data["evidence_sources"])
    for m in data["members"]:
        assert m["evidence"], m
        for ev in m["evidence"]:
            assert ev.split(":", 1)[0] in sources, (m["name"], ev)


@pytest.mark.parametrize("snippet", [
    "moho:FileSave()",
    "moho:FileSaveAs('/tmp/x.moho')",
    "moho.document:SetFps(24)",
    "moho.frame",
    "MOHO.LT_SWITCH",
    "LM.Point",
    "moho:LayerAsImage(moho:CreateNewLayer(MOHO.LT_IMAGE, false)):SourceImage()",
    "moho:CreateNewLayer(MOHO.LT_BONE, false):Skeleton()",  # needs LayerAsBone cast first
])
def test_mock_rejects_unsupported_api(snippet):
    lua = new_lua()
    mock = load_mock(lua)
    env = mock.new_env(lua.table_from({}))
    with pytest.raises(Exception, match="UNSUPPORTED MOHO API"):
        lua.execute(f"local moho = ... ; return {snippet}", env.moho)


def test_mock_requires_image_cast_for_set_source_image():
    lua = new_lua()
    mock = load_mock(lua)
    env = mock.new_env(lua.table_from({}))
    with pytest.raises(Exception, match="UNSUPPORTED MOHO API: MohoLayer.SetSourceImage"):
        lua.execute("local moho = ... ; moho:CreateNewLayer(MOHO.LT_IMAGE, false):SetSourceImage('/x.png')",
                    env.moho)


# --- successful build ----------------------------------------------------------

def _bone_layer(snapshot, name="AutoRig_Phase0"):
    matches = [l for l in snapshot["layers"] if l["name"] == name]
    assert len(matches) == 1
    return matches[0]


def test_build_completes_and_report_passes(generated_run, spec):
    report = generated_run.report()
    assert report["status"] == "completed", report["errors"]
    result = verify_report(spec, report)
    assert result.passed, result.format()
    assert result.counts()["FAIL"] == 0 and result.counts()["WARN"] == 0


def test_layer_tree_in_mock_state(generated_run, spec):
    snap = generated_run.snapshot
    # the default "Layer 1" stays; one new top-level bone layer; images moved inside it
    assert [l["name"] for l in snap["layers"]] == ["Layer 1", "AutoRig_Phase0"]
    bl = _bone_layer(snap)
    assert bl["type"] == LT_BONE
    assert [c["name"] for c in bl["children"]] == ["torso_art", "head_art", "upper_arm_art", "forearm_art"]
    bone_ids = {b["name"]: i for i, b in enumerate(bl["bones"])}
    for child in bl["children"]:
        part = next(p for p in spec.parts if p.layer_name == child["name"])
        assert child["type"] == LT_IMAGE
        assert child["parent_bone"] == bone_ids[part.bone]
        assert child["source_image"].endswith("/" + part.image)
        assert child["source_image"].startswith("/")


def test_bones_in_mock_state(generated_run, spec):
    bl = _bone_layer(generated_run.snapshot)
    rest = compute_rest_pose(spec)
    assert [b["name"] for b in bl["bones"]] == [r.name for r in rest]
    for b, r in zip(bl["bones"], rest):
        assert b["parent"] == r.parent_index
        assert b["parent_key"] == r.parent_index
        assert (b["x"], b["y"]) == pytest.approx(r.local_pos, abs=1e-12)
        assert b["angle"] == pytest.approx(r.local_angle, abs=1e-12)
        assert b["length"] == pytest.approx(r.length, abs=1e-12)
        frames = [k[0] for k in b["angle_keys"]]
        assert frames == [0, 1, 10, 19, 28, 36]
        assert b["angle_keys"][0][1] == pytest.approx(r.local_angle)


def test_wave_peak_value_in_mock(generated_run, spec):
    bl = _bone_layer(generated_run.snapshot)
    ua = next(b for b in bl["bones"] if b["name"] == "upper_arm")
    keys = dict((k[0], k[1]) for k in ua["angle_keys"])
    assert keys[10] - keys[0] == pytest.approx(math.radians(40))


def test_document_state_after_build(generated_run):
    snap = generated_run.snapshot
    assert (snap["start_frame"], snap["end_frame"]) == (1, 36)
    assert snap["dirty"] is True
    assert snap["frame"] == 0
    assert snap["selected"] == "AutoRig_Phase0"
    assert snap["ui_updates"] >= 1
    assert len(snap["alerts"]) == 1 and snap["alerts"][0]["kind"] == 1  # info


def test_report_contents(generated_run):
    rep = generated_run.report()
    assert rep["lua_version"] == "Lua 5.4"
    assert rep["constants"] == {"LT_BONE": LT_BONE, "LT_IMAGE": LT_IMAGE}
    assert rep["bone_layer"]["children_by_index"] == ["torso_art", "head_art", "upper_arm_art", "forearm_art"]
    assert len(rep["bones"]) == 4 and len(rep["layers"]) == 4


# --- refusal / failure paths ---------------------------------------------------

def _run(spec, asset_copy, **opts):
    src = render_lua(spec, asset_copy.as_posix())
    return run_in_mock(src, {"width": 1920, "height": 1080, **opts}, asset_copy / spec.report_filename)


def _assert_untouched(run):
    assert [l["name"] for l in run.snapshot["layers"]] in (["Layer 1"], ["Layer 1", "AutoRig_Phase0"])
    assert run.snapshot["dirty"] is False
    assert run.snapshot["alerts"][0]["kind"] == 2  # warning


def test_refuses_wrong_project_size(spec, asset_copy):
    run = _run(spec, asset_copy, width=1280, height=720)
    _assert_untouched(run)
    rep = run.report()
    assert rep["status"] == "aborted"
    assert "1280x720" in rep["errors"][0]
    assert not verify_report(spec, rep).passed


def test_refuses_missing_image(spec, asset_copy):
    (asset_copy / "art" / "head.png").unlink()
    run = _run(spec, asset_copy)
    _assert_untouched(run)
    assert any("head.png" in e for e in run.report()["errors"])


def test_refuses_existing_bone_layer(spec, asset_copy):
    run = _run(spec, asset_copy, preexisting_layer="AutoRig_Phase0")
    assert run.snapshot["dirty"] is False
    assert len([l for l in run.snapshot["layers"] if l["name"] == "AutoRig_Phase0"]) == 1
    assert "already exists" in run.report()["errors"][0]


def test_reference_build_refuses_to_run(spec):
    run = run_in_mock(render_lua(spec, ""), {"width": 1920, "height": 1080})
    _assert_untouched(run)
    assert "no asset path" in run.snapshot["alerts"][0]["line2"]


def test_moho_api_error_is_reported_not_swallowed(spec, asset_copy):
    run = _run(spec, asset_copy, fail_on="SetSourceImage")
    rep = run.report()
    assert rep["status"] == "failed"
    assert "simulated Moho failure inside SetSourceImage" in rep["errors"][0]
    assert run.snapshot["alerts"][0]["kind"] == 2
    assert "FAILED" in run.snapshot["alerts"][0]["line1"]


# --- Lua JSON encoder used for the report ------------------------------------------

def test_lua_json_encoder(spec):
    lua = new_lua()
    load_mock(lua).new_env(lua.table_from({}))
    lua.execute(render_lua(spec, "/abs"))
    out = lua.execute("""
        local S = AutoRig_Phase0_Build
        return S.json_encode({
            s = 'q"uote\\\\back\\nnl\\tt\\1ctl é',
            i = 42, f = -0.125, big = 1e300, tiny = 1.5e-12,
            arr = S.array({1, 2.5, 'x'}), empty_arr = S.array(), empty_obj = {},
            nested = { inf = math.huge, nan = 0/0, yes = true, no = false },
        })
    """)
    parsed = json.loads(out)
    assert parsed["s"] == 'q"uote\\back\nnl\tt\x01ctl é'
    assert parsed["i"] == 42 and parsed["f"] == -0.125
    assert parsed["big"] == 1e300 and parsed["tiny"] == 1.5e-12
    assert parsed["arr"] == [1, 2.5, "x"]
    assert parsed["empty_arr"] == [] and parsed["empty_obj"] == {}
    assert parsed["nested"] == {"inf": None, "nan": None, "yes": True, "no": False}
