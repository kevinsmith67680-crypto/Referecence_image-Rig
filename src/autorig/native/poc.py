"""Phase 4.0 minimal native Moho proof: the portable package.

From a character package this builds the smallest rig that can prove, in the real
Moho application, that AutoRig's conventions hold on the character's own artwork:

* image layers of **original pixels** (category A), flattened in z order and pasted
  **unscaled** onto the Moho canvas (an integer translation, never a resample);
* a bone layer with a root ``body`` bone, a ``shoulder`` bone (the clavicle) and an
  ``arm`` bone (shoulder pivot to wrist) taken from the package skeleton
  (``natural_pose/rig.json``), so nothing is specific to one character;
* the arm artwork bound to ``arm``; the rest of the body split into the layers behind
  and in front of the arm, bound to ``body``;
* build-time keys that lower the arm (frame 12) and a key that only the
  verify-after-reopen script adds (frame 18, arm raised).

It reuses the Phase 0 spec, geometry, Lua template and report verifier unchanged; the
only new Lua is the verify-after-reopen script. Nothing here runs Moho.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .. import __version__
from ..geometry import compute_rest_pose
from ..lua_codegen import _inject, _join_posix, load_template, render_named_build_lua
from ..phase2.store import pixel_hash
from ..preview import render_frame
from ..spec import RigSpec, parse_spec

POC_SCHEMA = "autorig.native_poc"
POC_VERSION = "4.0.0"
TITLE = "Phase 4.0 native proof"
BONE_LAYER = "AutoRig_P4_Native"
BUILD_SCRIPT = "AutoRig_P4_NativeBuild"
VERIFY_SCRIPT = "AutoRig_P4_NativeVerify"
VERIFY_TEMPLATE = "data/templates/phase4_native_verify.lua"
BUILD_REPORT = "phase4_native_build_report.json"
VERIFY_REPORT = "phase4_native_verify_report.json"
SAVED_NAME = "AutoRig_P4_Native.moho"
MANIFEST = "native_manifest.json"

FRAME_REST = 1      # keyed at the rest angles: the reference render
FRAME_MOVED = 12    # build-time key: arm lowered, shoulder dropped
FRAME_END = 24
FRAME_REOPEN = 18   # keyed only by the verify script, after reopening: arm raised
LOWER_DEG = 45.0
SHOULDER_DEG = 5.0
RAISE_DEG = 30.0
MARGIN_PX = 24
DEFAULT_CANVAS = (1920, 1080)
ARM_KINDS = ("upper_arm", "forearm", "hand")
RENDER_FRAMES = {"rest": FRAME_REST, "moved": FRAME_MOVED, "reopen_move": FRAME_REOPEN}


class NativePocError(ValueError):
    pass


def _identifier(text: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")
    if not s or not s[0].isalpha():
        s = "c_" + s
    return s[:40]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bone(rig: dict[str, Any], kind: str, side: str | None) -> dict[str, Any]:
    for b in rig["bones"]:
        if b.get("kind") == kind and b.get("side") == side:
            return b
    raise NativePocError(f"the package skeleton has no {kind!r} bone for side {side!r}")


def _rotate_px(pts: np.ndarray, centre: tuple[float, float], deg_ccw: float) -> np.ndarray:
    """Rotate pixel points (x right, y down) counter-clockwise *on screen*."""
    t = math.radians(deg_ccw)
    c, s = math.cos(t), math.sin(t)
    dx, dy = pts[:, 0] - centre[0], pts[:, 1] - centre[1]
    return np.stack([centre[0] + c * dx + s * dy, centre[1] - s * dx + c * dy], axis=1)


def _alpha_bbox(rgba: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.nonzero(rgba[..., 3] > 0)
    if xs.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _flatten(images: list[Image.Image], size: tuple[int, int]) -> Image.Image:
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    for im in images:
        out = Image.alpha_composite(out, im)
    return out


def _place(src: Image.Image, canvas: tuple[int, int], ox: int, oy: int) -> Image.Image:
    """Paste ``src`` at integer offset (ox, oy) on a transparent canvas: a pure copy."""
    out = Image.new("RGBA", canvas, (0, 0, 0, 0))
    sw, sh = src.size
    x0, y0 = max(0, -ox), max(0, -oy)
    x1, y1 = min(sw, canvas[0] - ox), min(sh, canvas[1] - oy)
    if x1 > x0 and y1 > y0:
        out.paste(src.crop((x0, y0, x1, y1)), (x0 + ox, y0 + oy))
    return out


def _clipped_alpha_px(src: Image.Image, canvas: tuple[int, int], ox: int, oy: int) -> int:
    a = np.asarray(src)[..., 3] > 0
    sh, sw = a.shape
    x0, y0 = max(0, -ox), max(0, -oy)
    x1, y1 = min(sw, canvas[0] - ox), min(sh, canvas[1] - oy)
    inside = int(a[y0:y1, x0:x1].sum()) if (x1 > x0 and y1 > y0) else 0
    return int(a.sum()) - inside


def _offset(src_len: int, canvas_len: int, need: tuple[float, float]) -> int:
    """Integer offset (canvas = source + offset) that fits ``need`` when cropping."""
    if src_len <= canvas_len:
        return (canvas_len - src_len) // 2
    lo, hi = need
    span = hi - lo
    if span + 2 * MARGIN_PX <= canvas_len:
        start = lo - MARGIN_PX
    else:  # cannot fit everything: keep the needed range's start
        start = lo
    start = int(round(min(max(start, 0), src_len - canvas_len)))
    return -start


def poses(side_sign: float) -> dict[int, dict[str, float]]:
    """Rotation offsets (deg, CCW positive in Moho) per keyed frame."""
    return {
        FRAME_REST: {"shoulder": 0.0, "arm": 0.0},
        FRAME_MOVED: {"shoulder": side_sign * SHOULDER_DEG, "arm": side_sign * LOWER_DEG},
        FRAME_REOPEN: {"shoulder": 0.0, "arm": -side_sign * RAISE_DEG},
        FRAME_END: {"shoulder": 0.0, "arm": 0.0},
    }


def build_raw_spec(char_id: str, display: str, canvas: tuple[int, int], bones_px: dict[str, tuple],
                   parts: list[dict[str, Any]], side_sign: float, side: str) -> dict[str, Any]:
    p = poses(side_sign)
    build_frames = (FRAME_REST, FRAME_MOVED, FRAME_END)
    return {
        "spec_version": "0.1.0",
        "character": {
            "id": char_id,
            "display_name": display,
            "description": f"Phase 4.0 native proof: {display}, {'left' if side == 'l' else 'right'} arm on a "
                           "shoulder bone, original pixels pasted unscaled.",
        },
        "canvas": {"width_px": canvas[0], "height_px": canvas[1]},
        "moho": {
            "bone_layer_name": BONE_LAYER,
            "rest_frame": 0,
            "start_frame": FRAME_REST,
            "end_frame": FRAME_END,
            "report_filename": BUILD_REPORT,
        },
        "bones": [
            {"name": name, "parent": parent, "base_px": [round(a[0], 3), round(a[1], 3)],
             "tip_px": [round(b[0], 3), round(b[1], 3)]}
            for name, (parent, a, b) in bones_px.items()
        ],
        "parts": parts,
        "animation": {
            "name": "phase4_native_proof",
            "description": "Frame 1 rest, frame 12 arm lowered and shoulder dropped, frame 24 rest. The verify "
                           f"script adds frame {FRAME_REOPEN} (arm raised) after the document is reopened.",
            "tracks": [
                {"bone": bone, "channel": "rotation_offset_deg",
                 "keys": [{"frame": f, "value": p[f][bone]} for f in build_frames]}
                for bone in ("shoulder", "arm")
            ],
        },
    }


def lower_sign(raw: dict[str, Any]) -> float:
    """+1 when lowering the arm is a counter-clockwise rotation in Moho, else -1."""
    arm = next(t for t in raw["animation"]["tracks"] if t["bone"] == "arm")
    moved = next(k["value"] for k in arm["keys"] if k["frame"] == FRAME_MOVED)
    return 1.0 if moved > 0 else -1.0


def reopen_spec(raw: dict[str, Any]) -> RigSpec:
    """The spec as it must read after the verify script added its key."""
    raw2 = json.loads(json.dumps(raw))
    arm = next(t for t in raw2["animation"]["tracks"] if t["bone"] == "arm")
    arm["keys"].append({"frame": FRAME_REOPEN, "value": poses(lower_sign(raw))[FRAME_REOPEN]["arm"]})
    arm["keys"].sort(key=lambda k: k["frame"])
    return parse_spec(raw2)


def verify_lua_data(spec: RigSpec, raw: dict[str, Any], asset_root: str | None) -> dict[str, Any]:
    root = (asset_root or "").rstrip("/")
    if root and not root.startswith("/"):
        raise ValueError(f"asset_root must be an absolute POSIX path, got {asset_root!r}")
    rest = {r.name: r for r in compute_rest_pose(spec)}
    offset = poses(lower_sign(raw))[FRAME_REOPEN]["arm"]
    return {
        "generator": {"name": "autorig", "version": __version__, "spec_character_id": spec.character_id,
                      "spec_sha256": spec.canonical_sha256(), "script": VERIFY_SCRIPT},
        "report_path": _join_posix(root, VERIFY_REPORT) if root else "",
        "bone_layer_name": spec.bone_layer_name,
        "rest_frame": spec.rest_frame,
        "move": {"bone": "arm", "frame": FRAME_REOPEN, "offset_deg": offset,
                 "value": rest["arm"].local_angle + math.radians(offset)},
    }


def render_verify_lua(spec: RigSpec, raw: dict[str, Any], asset_root: str | None) -> str:
    return _inject(load_template(VERIFY_TEMPLATE), VERIFY_SCRIPT,
                   "Verification data generated by autorig native-poc.", verify_lua_data(spec, raw, asset_root))


def render_build_lua(spec: RigSpec, asset_root: str | None) -> str:
    return render_named_build_lua(
        spec, asset_root, BUILD_SCRIPT, TITLE,
        "Builds the AutoRig Studio Phase 4.0 native proof rig (shoulder and arm bones on the character's own "
        "pixels, bindings, test keys) and writes a verification report.")


def write_lua(out: Path, spec: RigSpec, raw: dict[str, Any], asset_root: str | None) -> dict[str, str]:
    moho = out / "moho"
    moho.mkdir(parents=True, exist_ok=True)
    files = {
        "build": moho / f"{BUILD_SCRIPT}.lua",
        "verify": moho / f"{VERIFY_SCRIPT}.lua",
    }
    files["build"].write_text(render_build_lua(spec, asset_root), encoding="utf-8")
    files["verify"].write_text(render_verify_lua(spec, raw, asset_root), encoding="utf-8")
    return {k: v.relative_to(out).as_posix() for k, v in files.items()}


def expected_renders(spec: RigSpec, raw: dict[str, Any], out: Path,
                     background=(255, 255, 255, 255)) -> dict[str, Any]:
    """Python simulation of the frames the Mac run renders (never Moho output)."""
    layers = {p.name: Image.open(out / p.image).convert("RGBA") for p in spec.parts}
    spec2 = reopen_spec(raw)
    exp = out / "expected"
    exp.mkdir(parents=True, exist_ok=True)
    frames = {
        "rest": (spec, FRAME_REST),
        "moved": (spec, FRAME_MOVED),
        "reopen_move": (spec2, FRAME_REOPEN),
    }
    files, cells = {}, []
    for key, (s, f) in frames.items():
        im = render_frame(s, layers, f, background=None)
        path = exp / f"expected_{key}_f{f:02d}.png"
        im.save(path, optimize=True)
        files[key] = path.relative_to(out).as_posix()
        cell = render_frame(s, layers, f, draw_bones=True, background=background)
        ImageDraw.Draw(cell).text((24, 20), f"SIMULATION (not Moho) - {key}, frame {f}", fill=(200, 0, 0, 255),
                                  font=ImageFont.load_default(size=40))
        cells.append(cell)
    w, h = spec.canvas_width, spec.canvas_height
    sheet = Image.new("RGBA", (w * len(cells) // 2, h // 2), background)
    for i, c in enumerate(cells):
        sheet.paste(c.resize((w // 2, h // 2), Image.Resampling.LANCZOS), (i * (w // 2), 0))
    sheet.convert("RGB").save(exp / "expected_contact_sheet.png", optimize=True)
    files["contact_sheet"] = "expected/expected_contact_sheet.png"
    return files


def build_poc(package: str | Path, out: str | Path, side: str = "l",
              canvas: tuple[int, int] | None = DEFAULT_CANVAS, asset_root: str | None = None,
              character_id: str | None = None) -> dict[str, Any]:
    """Write the Phase 4.0 native proof package and return its manifest."""
    pkg, out = Path(package), Path(out)
    if side not in ("l", "r"):
        raise NativePocError(f"side must be 'l' or 'r', got {side!r}")
    man_path, rig_path = pkg / "artwork" / "manifest.json", pkg / "natural_pose" / "rig.json"
    for p in (man_path, rig_path):
        if not p.is_file():
            raise NativePocError(f"not a character package: {p} is missing")
    manifest = json.loads(man_path.read_text(encoding="utf-8"))
    rig = json.loads(rig_path.read_text(encoding="utf-8"))
    sw, sh = int(manifest["coordinate_system"]["width"]), int(manifest["coordinate_system"]["height"])
    cw, ch = canvas if canvas is not None else (sw, sh)

    clav = _bone(rig, "clavicle", side)
    upper, fore = _bone(rig, "upper_arm", side), _bone(rig, "forearm", side)
    pelvis, chest = _bone(rig, "pelvis", None), _bone(rig, "chest", None)
    arm_bones = {_bone(rig, k, side)["name"] for k in ARM_KINDS}

    originals = sorted((l for l in manifest["layers"] if l.get("category") == "A_original"),
                       key=lambda l: l["z_index"])
    arm_layers = [l for l in originals if l.get("bone") in arm_bones]
    if not arm_layers:
        raise NativePocError(f"no original layer is bound to {sorted(arm_bones)}")
    zmin, zmax = min(l["z_index"] for l in arm_layers), max(l["z_index"] for l in arm_layers)
    back = [l for l in originals if l not in arm_layers and l["z_index"] < zmin]
    front = [l for l in originals if l not in arm_layers and l["z_index"] > zmin]
    between = [l["id"] for l in front if l["z_index"] < zmax]

    images, provenance = {}, []
    for l in originals:
        path = pkg / l["image"]
        im = Image.open(path).convert("RGBA")
        if im.size != (sw, sh):
            raise NativePocError(f"{l['image']}: {im.size} is not the package canvas {(sw, sh)}")
        arr = np.asarray(im)
        if l.get("pixel_sha256") and pixel_hash(arr) != l["pixel_sha256"]:
            raise NativePocError(f"{l['image']}: pixels differ from the manifest hash (original pixels changed)")
        images[l["id"]] = im
        provenance.append({"id": l["id"], "image": l["image"], "z_index": l["z_index"], "bone": l.get("bone"),
                           "pixel_sha256": l.get("pixel_sha256"),
                           "group": "arm" if l in arm_layers else ("body_back" if l in back else "body_front")})

    groups = {"body_back": back, "arm": arm_layers, "body_front": front}
    flat = {g: _flatten([images[l["id"]] for l in ls], (sw, sh)) for g, ls in groups.items() if ls}

    # Placement: what must stay on the canvas is the upper body and the arm's sweep.
    everything = _flatten([images[l["id"]] for l in originals], (sw, sh))
    char_box = _alpha_bbox(np.asarray(everything))
    arm_arr = np.asarray(flat["arm"])
    ys, xs = np.nonzero(arm_arr[..., 3] > 0)
    step = max(1, xs.size // 4000)
    pts = np.stack([xs[::step], ys[::step]], axis=1).astype(float)
    sign = -1.0 if (fore["tail"][0] - upper["head"][0]) > 0 else 1.0
    sweep = [pts]
    for f, off in poses(sign).items():
        moved = _rotate_px(pts, tuple(upper["head"]), off["arm"])
        sweep.append(_rotate_px(moved, tuple(clav["head"]), off["shoulder"]))
    sweep_pts = np.concatenate(sweep)
    need_x = (min(char_box[0], sweep_pts[:, 0].min()), max(char_box[2], sweep_pts[:, 0].max()))
    need_y = (min(char_box[1], sweep_pts[:, 1].min()), max(pelvis["head"][1], sweep_pts[:, 1].max()))
    ox, oy = _offset(sw, cw, need_x), _offset(sh, ch, need_y)

    def tr(p):
        return (float(p[0]) + ox, float(p[1]) + oy)

    bones_px = {
        "body": (None, tr(pelvis["head"]), tr(chest["tail"])),
        "shoulder": ("body", tr(clav["head"]), tr(clav["tail"])),
        "arm": ("shoulder", tr(upper["head"]), tr(fore["tail"])),
    }

    # Write the art (pure integer translation of flattened original pixels).
    out.mkdir(parents=True, exist_ok=True)
    (out / "art").mkdir(exist_ok=True)
    parts, art_files, clipped = [], {}, {}
    names = {"body_back": "body_back_art", "arm": f"arm_{side}_art", "body_front": "body_front_art"}
    bone_of = {"body_back": "body", "arm": "arm", "body_front": "body"}
    for z, g in enumerate(("body_back", "arm", "body_front")):
        if g not in flat:
            continue
        placed = _place(flat[g], (cw, ch), ox, oy)
        rel = f"art/{g}.png"
        placed.save(out / rel)
        art_files[g] = {"path": rel, "sha256": _sha256(out / rel),
                        "pixel_sha256": pixel_hash(np.asarray(placed)),
                        "alpha_px": int((np.asarray(placed)[..., 3] > 0).sum())}
        clipped[g] = _clipped_alpha_px(flat[g], (cw, ch), ox, oy)
        parts.append({"name": g, "layer_name": names[g], "image": rel, "bone": bone_of[g], "z_index": z})

    char_id = _identifier(character_id or pkg.resolve().parent.name)
    display = (character_id or pkg.resolve().parent.name).replace("_", " ")
    raw = build_raw_spec(f"{char_id}_p4", f"{display} (Phase 4.0 native proof)", (cw, ch), bones_px, parts,
                         sign, side)
    spec = parse_spec(raw)
    (out / "rig_spec.json").write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")

    # Fidelity of the split: flattening in groups must reproduce the full composite.
    regroup = _flatten([flat[g] for g in ("body_back", "arm", "body_front") if g in flat], (sw, sh))
    diff = np.abs(np.asarray(regroup).astype(int) - np.asarray(everything).astype(int))
    lua = write_lua(out, spec, raw, asset_root)
    exp = expected_renders(spec, raw, out)

    # How much of the arm stays on the canvas in every keyed pose (simulation).
    layers = {p.name: Image.open(out / p.image).convert("RGBA") for p in spec.parts}
    arm_px = art_files["arm"]["alpha_px"]
    on_canvas = {}
    spec2 = reopen_spec(raw)
    for key, (s, f) in {"moved": (spec, FRAME_MOVED), "reopen_move": (spec2, FRAME_REOPEN)}.items():
        only_arm = {n: (im if n == "arm" else Image.new("RGBA", im.size)) for n, im in layers.items()}
        a = np.asarray(render_frame(s, only_arm, f, background=None))[..., 3] > 127
        on_canvas[key] = round(float(a.sum()) / max(1, arm_px), 4)

    rest = {r.name: r for r in compute_rest_pose(spec)}
    man = {
        "schema": POC_SCHEMA,
        "version": POC_VERSION,
        "generator": {"name": "autorig", "version": __version__},
        "evidence_level": "portable package; no Moho behaviour verified (Moho has not run)",
        "package": {"path_name": pkg.resolve().name, "parent": pkg.resolve().parent.name,
                    "artwork_manifest_sha256": _sha256(man_path), "rig_sha256": _sha256(rig_path)},
        "character_id": spec.character_id,
        "side": side,
        "source_canvas": [sw, sh],
        "moho_canvas": [cw, ch],
        "placement": {"offset_px": [ox, oy], "rule": "canvas_px = source_px + offset; integer, no scaling",
                      "clipped_alpha_px": clipped, "arm_on_canvas_fraction": on_canvas},
        "layers": provenance,
        "groups": {g: [l["id"] for l in ls] for g, ls in groups.items()},
        "layers_between_arm_z": between,
        "art": art_files,
        "split_fidelity": {"max_abs_diff": int(diff.max()), "px_over_2": int((diff.max(axis=2) > 2).sum())},
        "excluded": {"generated_extensions": len(manifest.get("extensions", [])),
                     "reason": "Phase 2 generated pixels are pending review; the proof uses original pixels only, "
                               "so the area under the arm shows as a gap when the arm moves"},
        "bones": {
            name: {"parent": parent, "base_px": list(a), "tip_px": list(b),
                   "source": src, "moho_pos": list(rest[name].local_pos),
                   "moho_angle_rad": rest[name].local_angle, "moho_length": rest[name].length,
                   "moho_world_base": list(rest[name].world_base)}
            for (name, (parent, a, b)), src in zip(bones_px.items(), (
                f"{pelvis['name']}.head -> {chest['name']}.tail",
                f"{clav['name']}.head -> {clav['name']}.tail",
                f"{upper['name']}.head -> {fore['name']}.tail"))
        },
        "keys": {"frames": RENDER_FRAMES | {"end": FRAME_END}, "offsets_deg": {str(k): v for k, v in poses(sign).items()},
                 "lower_sign": sign},
        "scripts": lua,
        "asset_root": (asset_root or "").rstrip("/") or None,
        "reports": {"build": BUILD_REPORT, "verify": VERIFY_REPORT},
        "saved_document": SAVED_NAME,
        "expected": exp,
        "spec_sha256": spec.canonical_sha256(),
    }
    (out / MANIFEST).write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
    return man


def load_poc(poc_dir: str | Path) -> tuple[RigSpec, dict[str, Any], dict[str, Any]]:
    d = Path(poc_dir)
    raw = json.loads((d / "rig_spec.json").read_text(encoding="utf-8"))
    man = json.loads((d / MANIFEST).read_text(encoding="utf-8"))
    return parse_spec(raw), raw, man
