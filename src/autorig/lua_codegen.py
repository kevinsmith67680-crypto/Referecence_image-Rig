"""Generate the self-contained Moho Lua menu script from a rig spec.

The template (``data/templates/phase0_build.lua``) contains all logic; this
module only computes the numbers (via :mod:`autorig.geometry`) and injects
them as a Lua table literal in place of the ``-- @@AUTORIG_DATA@@`` line.
"""

from __future__ import annotations

import math
import re
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any

from . import __version__
from .geometry import compute_rest_pose, expected_angle_keys
from .spec import RigSpec

TEMPLATE_RESOURCE = "data/templates/phase0_build.lua"
DATA_PLACEHOLDER = "-- @@AUTORIG_DATA@@"
SCRIPT_NAME = "AutoRig_Phase0_Build"
SCRIPT_FILENAME = f"{SCRIPT_NAME}.lua"

PROBE_TEMPLATE_RESOURCE = "data/templates/probe_moho.lua"
PROBE_SCRIPT_NAME = "AutoRig_Probe"
PROBE_SCRIPT_FILENAME = f"{PROBE_SCRIPT_NAME}.lua"
PROBE_REPORT_FILENAME = "autorig_probe_report.json"

_LUA_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LUA_KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function", "goto", "if",
    "in", "local", "nil", "not", "or", "repeat", "return", "then", "true", "until", "while",
}


def lua_string(s: str) -> str:
    """Encode a Python str as a Lua 5.x string literal.

    Printable ASCII is kept; quotes/backslashes are escaped; every other byte
    of the UTF-8 encoding becomes a decimal ``\\ddd`` escape, which Lua 5.1-5.4
    decode back to the identical bytes (paths with spaces/accents are safe).
    """
    out = ['"']
    for byte in s.encode("utf-8"):
        ch = chr(byte)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif 0x20 <= byte < 0x7F:
            out.append(ch)
        else:
            out.append(f"\\{byte:03d}")
    out.append('"')
    return "".join(out)


def lua_number(x: float | int) -> str:
    if isinstance(x, bool):
        raise TypeError("bool is not a number here")
    if isinstance(x, int):
        return str(x)
    if not math.isfinite(x):
        raise ValueError(f"non-finite number cannot be emitted to Lua: {x!r}")
    text = repr(float(x))
    return text


def to_lua(value: Any, indent: str = "") -> str:
    """Serialize None/bool/int/float/str/list/tuple/dict to a Lua literal.

    dict keys must be strings; keys whose value is None are omitted (Lua
    tables cannot hold nil). Lists become 1-based sequences.
    """
    inner = indent + "\t"
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return lua_number(value)
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, (list, tuple)):
        if not value:
            return "{}"
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
            return "{ " + ", ".join(lua_number(v) for v in value) + " }"
        items = [inner + to_lua(v, inner) for v in value]
        return "{\n" + ",\n".join(items) + ",\n" + indent + "}"
    if isinstance(value, dict):
        items = []
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"Lua table keys must be str, got {type(k).__name__}")
            if v is None:
                continue
            key = k if (_LUA_IDENT.match(k) and k not in _LUA_KEYWORDS) else f"[{lua_string(k)}]"
            items.append(f"{inner}{key} = {to_lua(v, inner)}")
        if not items:
            return "{}"
        return "{\n" + ",\n".join(items) + ",\n" + indent + "}"
    raise TypeError(f"cannot serialize {type(value).__name__} to Lua")


def _join_posix(root: str, rel: str) -> str:
    return str(PurePosixPath(root) / PurePosixPath(rel))


def build_lua_data(spec: RigSpec, asset_root: str | None) -> dict[str, Any]:
    """Everything the Lua runtime needs, fully precomputed.

    ``asset_root`` must be an absolute POSIX path on the machine that runs
    Moho (e.g. ``/Volumes/SSD/AutoRig/examples/phase0_test_character``), or
    None/"" for a reference build that refuses to run.
    """
    root = (asset_root or "").rstrip("/")
    if root and not root.startswith("/"):
        raise ValueError(f"asset_root must be an absolute POSIX path, got {asset_root!r}")
    rest = compute_rest_pose(spec)
    keys = expected_angle_keys(spec, rest)
    bones = [
        {
            "name": r.name,
            "parent": r.parent,
            "pos": [r.local_pos[0], r.local_pos[1]],
            "angle": r.local_angle,
            "length": r.length,
            "world_base": [r.world_base[0], r.world_base[1]],
            "world_tip": [r.world_tip[0], r.world_tip[1]],
        }
        for r in rest
    ]
    layers = [
        {
            "name": p.layer_name,
            "part": p.name,
            "bone": p.bone,
            "image_rel": p.image,
            "image_path": _join_posix(root, p.image) if root else "",
        }
        for p in spec.parts_back_to_front()
    ]
    angle_keys = []
    for r in rest:
        if spec.track_for(r.name) is None:
            continue
        animated = [(f, v) for (f, v) in keys[r.name] if f != spec.rest_frame]
        angle_keys.append({"bone": r.name, "keys": [{"frame": f, "value": v} for f, v in animated]})
    return {
        "generator": {
            "name": "autorig",
            "version": __version__,
            "spec_character_id": spec.character_id,
            "spec_sha256": spec.canonical_sha256(),
        },
        "asset_root": root,
        "report_path": _join_posix(root, spec.report_filename) if root else "",
        "canvas": {"width": spec.canvas_width, "height": spec.canvas_height},
        "bone_layer_name": spec.bone_layer_name,
        "rest_frame": spec.rest_frame,
        "start_frame": spec.start_frame,
        "end_frame": spec.end_frame,
        "bones": bones,
        "layers": layers,
        "angle_keys": angle_keys,
    }


def load_template(resource: str = TEMPLATE_RESOURCE) -> str:
    return resources.files("autorig").joinpath(resource).read_text(encoding="utf-8")


def _inject(template: str, script_name: str, comment: str, data: dict[str, Any]) -> str:
    if template.count(DATA_PLACEHOLDER) != 1:
        raise RuntimeError("template must contain exactly one data placeholder line")
    return template.replace(DATA_PLACEHOLDER, f"-- {comment}\n{script_name}.DATA = {to_lua(data)}")


def render_lua(spec: RigSpec, asset_root: str | None) -> str:
    return _inject(load_template(), SCRIPT_NAME,
                   "Rig data generated from the rig spec (all geometry precomputed in Python).",
                   build_lua_data(spec, asset_root))


def render_probe(report_dir: str | None) -> str:
    """The read-only Moho API probe. ``report_dir`` is an absolute POSIX
    directory on the Mac where Moho writes ``autorig_probe_report.json``."""
    root = (report_dir or "").rstrip("/")
    if not root.startswith("/"):
        raise ValueError(f"report_dir must be an absolute POSIX path, got {report_dir!r}")
    data = {
        "generator": {"name": "autorig", "version": __version__, "script": PROBE_SCRIPT_NAME},
        "report_path": _join_posix(root, PROBE_REPORT_FILENAME),
    }
    return _inject(load_template(PROBE_TEMPLATE_RESOURCE), PROBE_SCRIPT_NAME,
                   "Probe settings generated by autorig.", data)


def write_lua(spec: RigSpec, asset_root: str | None, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_lua(spec, asset_root), encoding="utf-8")
    return out_path
