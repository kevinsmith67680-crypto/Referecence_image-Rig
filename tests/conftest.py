"""Shared fixtures: the Phase 0 example spec and a Lua 5.4 runtime with the
strict mock Moho API (tests/lua/mock_moho.lua)."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from autorig.lua_codegen import render_lua
from autorig.spec import RigSpec, load_spec

REPO = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = REPO / "examples" / "phase0_test_character"
SPEC_PATH = EXAMPLE_DIR / "rig_spec.json"
MOCK_PATH = REPO / "tests" / "lua" / "mock_moho.lua"
ALLOWLIST_PATH = REPO / "src" / "autorig" / "data" / "moho_api_allowlist.json"


@pytest.fixture(scope="session")
def spec() -> RigSpec:
    return load_spec(SPEC_PATH)


@pytest.fixture(scope="session")
def raw_spec() -> dict[str, Any]:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


@pytest.fixture()
def asset_copy(tmp_path: Path) -> Path:
    """A writable copy of the example assets (the Lua script writes its report there).
    The directory name contains a space and non-ASCII characters on purpose,
    like a typical external-SSD path (/Volumes/My SSD/...)."""
    dest = tmp_path / "Ext SSD é" / "phase0_test_character"
    shutil.copytree(EXAMPLE_DIR / "art", dest / "art")
    shutil.copy(SPEC_PATH, dest / "rig_spec.json")
    return dest


def lua_to_py(obj: Any) -> Any:
    from lupa.lua54 import lua_type

    if lua_type(obj) != "table":
        return obj
    keys = list(obj.keys())
    if keys and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
        return [lua_to_py(obj[i]) for i in range(1, len(keys) + 1)]
    return {k: lua_to_py(v) for k, v in obj.items()}


@dataclass
class MohoRun:
    lua: Any
    env: Any
    script: Any
    snapshot: dict[str, Any]
    report_path: Path | None

    def report(self) -> dict[str, Any]:
        assert self.report_path is not None
        return json.loads(self.report_path.read_text(encoding="utf-8"))


def new_lua():
    from lupa.lua54 import LuaRuntime

    return LuaRuntime(unpack_returned_tuples=True)


def load_mock(lua) -> Any:
    return lua.execute(MOCK_PATH.read_text(encoding="utf-8"))


def run_in_mock(lua_source: str, env_opts: dict[str, Any] | None = None,
                report_path: Path | None = None) -> MohoRun:
    """Execute a generated menu script exactly like Moho would: load the file
    (defines the script table), then call Script:Run(moho)."""
    lua = new_lua()
    mock = load_mock(lua)
    opts = lua.table_from(env_opts or {}, recursive=True)
    env = mock.new_env(opts)
    lua.execute(lua_source)
    script = lua.globals().AutoRig_Phase0_Build
    script.Run(script, env.moho)
    snap = lua_to_py(mock.snapshot(env))
    return MohoRun(lua=lua, env=env, script=script, snapshot=snap, report_path=report_path)


@pytest.fixture()
def generated_run(spec: RigSpec, asset_copy: Path) -> MohoRun:
    src = render_lua(spec, asset_copy.as_posix())
    return run_in_mock(src, {"width": 1920, "height": 1080}, asset_copy / spec.report_filename)
