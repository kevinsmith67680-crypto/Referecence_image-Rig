import json
import math
import re

import pytest

from autorig.lua_codegen import (
    DATA_PLACEHOLDER,
    SCRIPT_FILENAME,
    build_lua_data,
    load_template,
    lua_string,
    render_lua,
    to_lua,
)

from conftest import ALLOWLIST_PATH, EXAMPLE_DIR, lua_to_py, new_lua


def lua_eval(lua, expr_src):
    return lua.execute("return " + expr_src)


@pytest.mark.parametrize("s", [
    "", "plain", 'quote " and \\ backslash', "/Volumes/My SSD/AutoRig Studio/é ü 日本",
    "tab\tnew\nline\r", "\x00nul and \x7f del", "]] ]=] [[", "--[[ not a comment",
])
def test_lua_string_roundtrip_bytes(s):
    lua = new_lua()
    literal = lua_string(s)
    assert "\n" not in literal and "\r" not in literal
    # the byte length Lua itself sees must equal the UTF-8 length
    assert lua.execute(f"local s = {literal}; return #s") == len(s.encode("utf-8"))
    # and decoding those bytes as UTF-8 gives the original string back
    assert lua_eval(lua, literal) == s


def test_to_lua_roundtrip_nested():
    data = {"a": 1, "b": [1.5, -2.0, 3], "c": {"d": "x", "e": [{"f": True}, {"g": False}]},
            "nil_key": None, "end": "keyword key", "with space": 2}
    lua = new_lua()
    back = lua_to_py(lua_eval(lua, to_lua(data)))
    expected = {k: v for k, v in data.items() if v is not None}
    assert back == expected


def test_to_lua_rejects_non_finite():
    with pytest.raises(ValueError):
        to_lua({"x": math.nan})
    with pytest.raises(ValueError):
        to_lua([math.inf])


def test_float_precision_preserved(spec):
    data = build_lua_data(spec, "/abs")
    lua = new_lua()
    back = lua_to_py(lua_eval(lua, to_lua(data)))
    for b_py, b_lua in zip(data["bones"], back["bones"]):
        assert b_lua["angle"] == b_py["angle"]  # bit-exact via repr()
        assert b_lua["pos"] == b_py["pos"]


def test_template_has_single_placeholder_and_compiles():
    tpl = load_template()
    assert tpl.count(DATA_PLACEHOLDER) == 1
    lua = new_lua()
    ok, err = lua.execute("local f, e = load(...) ; return f ~= nil, e", tpl)
    assert ok, err


def test_generated_script_compiles_lua54(spec):
    lua = new_lua()
    assert lua.execute("return _VERSION") == "Lua 5.4"
    ok, err = lua.execute("local f, e = load(...) ; return f ~= nil, e", render_lua(spec, "/Volumes/SSD/x"))
    assert ok, err


def test_script_name_matches_file_name(spec):
    src = render_lua(spec, "/abs")
    m = re.search(r'^ScriptName = "([^"]+)"$', src, re.M)
    assert m and f"{m.group(1)}.lua" == SCRIPT_FILENAME
    for fn in ("Name", "Version", "Description", "Creator", "UILabel", "IsEnabled", "Run"):
        assert f"function {m.group(1)}:{fn}(" in src


def test_asset_root_must_be_absolute(spec):
    with pytest.raises(ValueError):
        build_lua_data(spec, "relative/path")


def test_absolute_paths_baked(spec):
    data = build_lua_data(spec, "/Volumes/My SSD/AutoRig/examples/phase0_test_character/")
    assert data["asset_root"] == "/Volumes/My SSD/AutoRig/examples/phase0_test_character"
    assert data["report_path"].endswith("/phase0_test_character/phase0_moho_report.json")
    assert [l["image_path"] for l in data["layers"]] == [
        f"/Volumes/My SSD/AutoRig/examples/phase0_test_character/art/{n}.png"
        for n in ("torso", "head", "upper_arm", "forearm")
    ]


def test_layers_emitted_back_to_front_with_bindings(spec):
    data = build_lua_data(spec, "/abs")
    assert [(l["name"], l["bone"]) for l in data["layers"]] == [
        ("torso_art", "torso"), ("head_art", "head"), ("upper_arm_art", "upper_arm"), ("forearm_art", "forearm")]


def test_animation_keys_cover_36_frames(spec):
    data = build_lua_data(spec, "/abs")
    for track in data["angle_keys"]:
        frames = [k["frame"] for k in track["keys"]]
        assert frames[0] == 1 and frames[-1] == 36
        assert 0 not in frames  # rest key is written with the bone itself


def test_committed_reference_script_in_sync(spec):
    committed = (EXAMPLE_DIR / "moho" / SCRIPT_FILENAME).read_text(encoding="utf-8")
    assert committed == render_lua(spec, ""), "run: python -m autorig build-example examples/phase0_test_character/rig_spec.json"


def test_no_api_outside_allowlist_static(spec):
    """Static belt-and-braces check on top of the strict mock: method calls on
    Moho objects (`x:Method(`) must be allowlisted or known pure-Lua names."""
    allow = {m["name"] for m in json.loads(ALLOWLIST_PATH.read_text())["members"]}
    pure_lua = {"close", "write", "gsub", "format", "byte", "match", "sub"}
    src = render_lua(spec, "/abs")
    called = set(re.findall(r"[A-Za-z_][A-Za-z0-9_\.\]\)]*:([A-Za-z_][A-Za-z0-9_]*)\(", src))
    script_methods = {"Name", "Version", "Description", "Creator", "UILabel", "IsEnabled", "Run"}
    unknown = called - allow - pure_lua - script_methods
    assert not unknown, f"calls not in allowlist: {sorted(unknown)}"
    for forbidden in ("FileSave", "FileSaveAs", "FileNew", "SetKeyInterp", "SourceImage()", "PrepUndo"):
        assert forbidden not in src
