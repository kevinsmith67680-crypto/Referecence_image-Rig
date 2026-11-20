"""The read-only Moho API probe (Live Models discovery). Runs in Lua 5.4
against the strict mock: proves it is read-only, touches only allowlisted
API members, and that its introspection would surface hidden members."""

import json
import re

import pytest

from autorig.lua_codegen import PROBE_REPORT_FILENAME, PROBE_SCRIPT_FILENAME, render_lua, render_probe

from conftest import ALLOWLIST_PATH, load_mock, lua_to_py, new_lua


def _run_probe(tmp_path, env_opts=None, build_phase0_with=None, spec=None):
    lua = new_lua()
    mock = load_mock(lua)
    env = mock.new_env(lua.table_from(env_opts or {}, recursive=True))
    if build_phase0_with is not None:
        lua.execute(render_lua(spec, build_phase0_with.as_posix()))
        s = lua.globals().AutoRig_Phase0_Build
        s.Run(s, env.moho)
    before = lua_to_py(mock.snapshot(env))
    lua.execute(render_probe(tmp_path.as_posix()))
    probe = lua.globals().AutoRig_Probe
    probe.Run(probe, env.moho)
    after = lua_to_py(mock.snapshot(env))
    report = json.loads((tmp_path / PROBE_REPORT_FILENAME).read_text(encoding="utf-8"))
    return lua, before, after, report


def test_probe_compiles_and_is_named_like_its_file(tmp_path):
    src = render_probe(tmp_path.as_posix())
    lua = new_lua()
    ok, err = lua.execute("local f, e = load(...) ; return f ~= nil, e", src)
    assert ok, err
    m = re.search(r'^ScriptName = "([^"]+)"$', src, re.M)
    assert f"{m.group(1)}.lua" == PROBE_SCRIPT_FILENAME
    for fn in ("Name", "Version", "Description", "Creator", "UILabel", "IsEnabled", "Run"):
        assert f"function {m.group(1)}:{fn}(" in src


def test_probe_requires_absolute_report_dir():
    with pytest.raises(ValueError):
        render_probe("relative/dir")
    with pytest.raises(ValueError):
        render_probe("")


def test_probe_is_read_only_and_complete_on_phase0_rig(tmp_path, spec, asset_copy):
    _, before, after, report = _run_probe(
        tmp_path, {"actions": [{"name": "head_turn", "smart": True}, {"name": "wave", "smart": False}]},
        build_phase0_with=asset_copy, spec=spec)
    # nothing in the document changed (only an alert was shown)
    for key in ("layers", "start_frame", "end_frame", "dirty", "frame", "selected"):
        assert after[key] == before[key], key
    assert len(after["alerts"]) == len(before["alerts"]) + 1
    assert after["alerts"][-1]["line1"].startswith("AutoRig Probe finished (nothing was changed)")

    assert report["errors"] == [] and report["warnings"] == []
    assert report["lua_version"] == "Lua 5.4"
    assert set(report["surfaces"]) == {
        "ScriptInterface", "MohoDoc", "MohoLayer", "MohoGroupLayer", "MohoBoneLayer", "M_Skeleton",
        "M_Bone", "AnimChannel", "LM_Vector2", "MohoImageLayer"}
    layers = {l["name"]: l for l in report["document"]["layers"]}
    assert layers["Layer 1"]["actions"] == [
        {"name": "head_turn", "smart_bone": True}, {"name": "wave", "smart_bone": False}]
    assert [c["name"] for c in layers["AutoRig_Phase0"]["children"]] == [
        "torso_art", "head_art", "upper_arm_art", "forearm_art"]


def test_probe_on_empty_document_warns_but_does_not_fail(tmp_path):
    _, _, _, report = _run_probe(tmp_path)
    assert report["errors"] == []
    assert any("MohoBoneLayer" in w for w in report["warnings"])


def test_surface_introspection_finds_hidden_members():
    """If Moho's binding exposes methods through table metatables (incl.
    inheritance chains and tolua-style '.get' tables), the probe lists them
    and flags live-input-looking names."""
    lua = new_lua()
    load_mock(lua).new_env(lua.table_from({}))
    import tempfile
    lua.execute(render_probe(tempfile.gettempdir()))
    out = lua.execute("""
        local P = AutoRig_Probe
        local base = { Name = function() end }
        local derived = setmetatable({ SetLiveInput = function() end,
                                       [".get"] = { fGamepadStick = function() end } },
                                     { __index = base })
        derived.__index = derived
        local obj = setmetatable({}, derived)
        local s = P.surface(obj)
        local hits = P.array()
        P.keyword_hits("surface:test", s.members, hits)
        local locked = P.surface(setmetatable({}, { __metatable = "locked" }))
        return P.json_encode({ s = s, hits = hits, locked = locked })
    """)
    data = json.loads(out)
    names = {m["name"] for m in data["s"]["members"]}
    assert {"SetLiveInput", "fGamepadStick", "Name"} <= names
    flagged = {h["name"] for h in data["hits"]}
    assert {"SetLiveInput", "fGamepadStick"} <= flagged
    assert any("protected" in n for n in data["locked"]["notes"])


def test_probe_uses_only_allowlisted_moho_methods(tmp_path):
    allow = {m["name"] for m in json.loads(ALLOWLIST_PATH.read_text())["members"]}
    pure_lua = {"close", "write", "gsub", "format", "byte", "match", "sub"}
    script_methods = {"Name", "Version", "Description", "Creator", "UILabel", "IsEnabled", "Run"}
    src = render_probe(tmp_path.as_posix())
    called = set(re.findall(r"[A-Za-z_][A-Za-z0-9_\.\]\)]*:([A-Za-z_][A-Za-z0-9_]*)\(", src))
    assert not (called - allow - pure_lua - script_methods)


def _json_block(src):
    start = src.index("local function is_integer(n)")
    end = src.index("SELF.json_encode = json_encode")
    return src[start:end]


def test_probe_json_encoder_identical_to_phase0(tmp_path, spec):
    assert _json_block(render_probe(tmp_path.as_posix())) == _json_block(render_lua(spec, "/abs"))
