"""Phase 4.1: the full-body native rig package (`autorig native-fullbody`).

From a character package this writes everything the Mac needs to build the complete rig in Moho:
the character's own layers (one full-canvas image per layer, unscaled), a Phase 0 rig spec with the 25-bone
``NativeSkeletonSpec``, the binding plan, twelve mechanical test poses as rotation keys, three Lua menu scripts
(build, verify-after-reopen, opt-in IK/constraints), simulated expected frames (labelled SIMULATION), the original
reference, and a manifest with hashes. Preparing the package is not native execution: every native claim needs
the Mac evidence checked by `autorig native-fullbody-verify`.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .. import __version__
from ..geometry import compute_rest_pose, layer_delta_px, px_to_world, rotate, world_to_px
from ..lua_codegen import _inject, _join_posix, load_template, render_named_build_lua
from ..phase2.store import pixel_hash
from ..spec import RigSpec, parse_spec
from .binding import NativeBindingStrategy, binding_report, metadata_items
from .skeleton import NativeSkeletonSpec, Placement, _sign_out, _sign_up, validate

SCHEMA = "autorig.native_fullbody"
VERSION = "4.1.0"
TITLE = "Phase 4.1 full-body rig"
BONE_LAYER = "AutoRig_P41_FullBody"
BUILD_SCRIPT = "AutoRig_P41_FullBodyBuild"
VERIFY_SCRIPT = "AutoRig_P41_FullBodyVerify"
IK_SCRIPT = "AutoRig_P41_IKConstraints"
VERIFY_TEMPLATE = "data/templates/phase4_1_fullbody_verify.lua"
IK_TEMPLATE = "data/templates/phase4_1_ik_constraints.lua"
BUILD_REPORT = "phase4_1_build_report.json"
VERIFY_REPORT = "phase4_1_verify_report.json"
IK_REPORT = "phase4_1_ik_report.json"
SAVED_NAME = "AutoRig_P41_FullBody.moho"
SAVED_IK_NAME = "AutoRig_P41_FullBody_IK.moho"
MANIFEST = "native_fullbody_manifest.json"
END_FRAME = 110
IK_END_FRAME = 160
PELVIS_SHIFT_FRAC = 0.04     # of the character height, toward image left
ARM_FRAMES = {"LEFT": 120, "RIGHT": 130}
LEG_FRAMES = {"LEFT": 140, "RIGHT": 150}
CLAMP_FRAME = 160
CLAMP_EXCESS_DEG = 30.0
CANVAS_MARGIN = 24
CLOSEUP_JOINTS = {
    "shoulder": ("LEFT_UPPER_ARM", "base", 20), "elbow": ("LEFT_FOREARM", "base", 40),
    "wrist": ("LEFT_HAND", "base", 40), "hip": ("LEFT_THIGH", "base", 60),
    "knee": ("RIGHT_SHIN", "base", 70), "ankle": ("LEFT_FOOT", "base", 60),
}


class NativeFullbodyError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# -- test poses ------------------------------------------------------------------------------------------------

def test_poses(sk: NativeSkeletonSpec) -> list[dict[str, Any]]:
    """Twelve mechanical test poses: rotations (degrees, CCW positive in Moho) of semantic bones. Signs come from
    each bone's own direction, so the same table poses any character. Pose 9 (pelvis shift) is a position key that
    the verify script adds after reopening."""
    b = sk.bone
    up = {S: _sign_up(b(f"{S}_UPPER_ARM")) for S in ("LEFT", "RIGHT")}
    out = {S: _sign_out(b(f"{S}_THIGH"), sk.midline_x) for S in ("LEFT", "RIGHT")}
    height = sk.ground_y - min(b("HEAD").tip_px[1], b("HEAD").base_px[1])
    poses = [
        (1, "neutral", "Neutral standing", {}),
        (10, "left_arm_raised", "Left arm raised", {"LEFT_UPPER_ARM": 60 * up["LEFT"]}),
        (20, "right_arm_raised", "Right arm raised", {"RIGHT_UPPER_ARM": 60 * up["RIGHT"]}),
        (30, "both_arms_lowered", "Both arms lowered",
         {"LEFT_UPPER_ARM": -65 * up["LEFT"], "RIGHT_UPPER_ARM": -65 * up["RIGHT"],
          "LEFT_SHOULDER": -4 * up["LEFT"], "RIGHT_SHOULDER": -4 * up["RIGHT"]}),
        (40, "left_elbow_bent", "Left elbow bent", {"LEFT_UPPER_ARM": -20 * up["LEFT"], "LEFT_FOREARM": 80 * up["LEFT"]}),
        (50, "right_elbow_bent", "Right elbow bent",
         {"RIGHT_UPPER_ARM": -20 * up["RIGHT"], "RIGHT_FOREARM": 80 * up["RIGHT"]}),
        (60, "left_leg_lifted", "Left leg lifted (sideways)", {"LEFT_THIGH": 30 * out["LEFT"], "LEFT_FOOT": -30 * out["LEFT"]}),
        (70, "right_knee_bent", "Right knee bent (in-plane mechanical test)",
         {"RIGHT_THIGH": 15 * out["RIGHT"], "RIGHT_SHIN": -30 * out["RIGHT"], "RIGHT_FOOT": 15 * out["RIGHT"]}),
        (80, "pelvis_shifted", "Pelvis shifted (position key added after reopening)", {}),
        (90, "torso_leaned", "Torso leaned", {"SPINE_LOWER": 4.0, "SPINE_UPPER": 4.0, "CHEST": 6.0}),
        (100, "head_tilted", "Head tilted", {"NECK": 8.0, "HEAD": 12.0}),
        (110, "return_to_neutral", "Return to neutral", {}),
    ]
    out_list = []
    for frame, pid, label, offs in poses:
        entry = {"frame": frame, "id": pid, "label": label, "offsets_deg": {k: float(v) for k, v in offs.items()},
                 "pos_offset_px": {}, "after_reopen": False}
        if pid == "pelvis_shifted":
            entry["pos_offset_px"] = {"PELVIS": [round(-PELVIS_SHIFT_FRAC * height, 3), 0.0]}
            entry["after_reopen"] = True
        if pid == "right_knee_bent":
            entry["note"] = "front-view art: real knee flexion is depth motion (REQUIRES_NEW_VIEW); this is an in-plane joint test"
        out_list.append(entry)
    return out_list



def pose_extent(sk: NativeSkeletonSpec, layers: list[tuple[np.ndarray, str]], poses: list[dict[str, Any]],
                source: tuple[int, int]) -> tuple[float, float, float, float]:
    """Bounding box (character px) of every layer in every test pose and IK test, from the layers' own alpha
    boxes moved by their bones (no rendering)."""
    from ..geometry import BonePose

    w, h = source
    raw = {"spec_version": "0.1.0", "character": {"id": "extent", "display_name": "extent"},
           "canvas": {"width_px": w, "height_px": h},
           "moho": {"bone_layer_name": BONE_LAYER, "rest_frame": 0, "start_frame": 1, "end_frame": END_FRAME,
                    "report_filename": BUILD_REPORT},
           "bones": sk.spec_bones(Placement((w, h))), "parts": [{"name": "x", "layer_name": "x", "image": "x.png",
                                                                 "bone": "ROOT", "z_index": 0}],
           "animation": {"name": "extent", "tracks": []}}
    spec = parse_spec(raw)
    rest = {r.name: BonePose(r.name, r.world_base, r.world_angle, r.length) for r in compute_rest_pose(spec)}
    boxes = []
    for arr, bone in layers:
        ys, xs = np.nonzero(arr[..., 3] > 0)
        boxes.append((bone, [(xs.min(), ys.min()), (xs.max() + 1, ys.min()), (xs.min(), ys.max() + 1),
                             (xs.max() + 1, ys.max() + 1)]))
    worlds = [pose_world(spec, sk, p["offsets_deg"], p["pos_offset_px"]) for p in poses]
    ik = ik_plan(sk, spec)
    by = {r.name: r for r in compute_rest_pose(spec)}
    for tm in ik["target_moves"]:
        eff = next(b.ik["effector"] for b in sk.bones if b.id == tm["bone"])
        t0 = world_to_px(by[tm["bone"]].world_base, w, h)
        worlds.append(pose_world(spec, sk, {}, None, {eff: (t0[0] + tm["move_px"][0], t0[1] + tm["move_px"][1])}))
    x0 = y0 = math.inf
    x1 = y1 = -math.inf
    for world in worlds:
        for bone, corners in boxes:
            base, ang = world[bone]
            m = np.asarray(layer_delta_px(w, h, rest[bone], BonePose(bone, base, ang, rest[bone].length)), float)
            for cx, cy in corners:
                px = m[0][0] * cx + m[0][1] * cy + m[0][2]
                py = m[1][0] * cx + m[1][1] * cy + m[1][2]
                x0, y0, x1, y1 = min(x0, px), min(y0, py), max(x1, px), max(y1, py)
    return (x0, y0, x1, y1)

def check_pose_limits(sk: NativeSkeletonSpec, poses: list[dict[str, Any]]) -> list[str]:
    problems = []
    for p in poses:
        for bid, deg in p["offsets_deg"].items():
            lim = sk.bone(bid).limits_deg
            if lim and not (lim[0] - 1e-6 <= deg <= lim[1] + 1e-6):
                problems.append(f"pose {p['id']}: {bid} {deg:+.1f} deg is outside its range {lim}")
    return problems


def build_raw_spec(sk: NativeSkeletonSpec, placement: Placement, parts: list[dict[str, Any]],
                   poses: list[dict[str, Any]], char_id: str, display: str) -> dict[str, Any]:
    frames = [p["frame"] for p in poses]
    posed = sorted({bid for p in poses for bid in p["offsets_deg"]}, key=sk.ids().index)
    return {
        "spec_version": "0.1.0",
        "character": {"id": char_id, "display_name": display,
                      "description": "Phase 4.1 full-body native rig: semantic skeleton from the package, the "
                                     "character's own layers unscaled, rigid layer binding, mechanical test poses."},
        "canvas": {"width_px": placement.canvas[0], "height_px": placement.canvas[1]},
        "moho": {"bone_layer_name": BONE_LAYER, "rest_frame": 0, "start_frame": 1, "end_frame": END_FRAME,
                 "report_filename": BUILD_REPORT},
        "bones": sk.spec_bones(placement),
        "parts": parts,
        "animation": {
            "name": "phase4_1_test_poses",
            "description": "Twelve mechanical test poses at frames " + ", ".join(map(str, frames)) +
                           "; every posed bone is keyed at every pose frame so poses do not bleed into each other.",
            "tracks": [{"bone": bid, "channel": "rotation_offset_deg",
                        "keys": [{"frame": p["frame"], "value": round(p["offsets_deg"].get(bid, 0.0), 6)}
                                 for p in poses]} for bid in posed],
        },
    }


# -- Moho values for the scripts --------------------------------------------------------------------------------

def _rest(spec: RigSpec) -> dict[str, Any]:
    return {r.name: r for r in compute_rest_pose(spec)}


def _world_delta_to_local(rest, bone: str, d_world: tuple[float, float]) -> tuple[float, float]:
    parent = rest[bone].parent
    if parent is None:
        return d_world
    return rotate(d_world, -rest[parent].world_angle)


def pelvis_keys(spec: RigSpec, poses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Position keys for the pelvis shift (parent-local Moho units), with rest keys around it."""
    rest = _rest(spec)
    h = spec.canvas_height
    out = []
    for i, p in enumerate(poses):
        if not p["pos_offset_px"]:
            continue
        for bid, (dx, dy) in p["pos_offset_px"].items():
            dw = (dx / (h / 2.0), -dy / (h / 2.0))
            dl = _world_delta_to_local(rest, bid, dw)
            lp = rest[bid].local_pos
            keys = []
            if i > 0:
                keys.append({"frame": poses[i - 1]["frame"], "x": lp[0], "y": lp[1]})
            keys.append({"frame": p["frame"], "x": lp[0] + dl[0], "y": lp[1] + dl[1]})
            if i + 1 < len(poses):
                keys.append({"frame": poses[i + 1]["frame"], "x": lp[0], "y": lp[1]})
            out.append({"bone": bid, "kind": "pos", "keys": keys})
    return out


def ik_plan(sk: NativeSkeletonSpec, spec: RigSpec) -> dict[str, Any]:
    """Data for the opt-in IK / constraints script: target links, absolute constraint angles (rest + range; the
    interpretation is unverified), target moves and a constraint clamp test."""
    rest = _rest(spec)
    ids = sk.ids()
    links = [{"effector": b.id, "effector_id": ids.index(b.id), "target": b.ik["target"],
              "target_id": ids.index(b.ik["target"])}
             for b in sk.bones if b.ik and b.ik.get("role") == "effector"]
    cons = []
    for b in sk.bones:
        if b.limits_deg:
            r = rest[b.id].local_angle
            cons.append({"bone": b.id, "bone_id": ids.index(b.id), "min": r + math.radians(b.limits_deg[0]),
                         "max": r + math.radians(b.limits_deg[1]), "range_deg": list(b.limits_deg),
                         "interpretation": "absolute parent-relative angle (rest + range): UNVERIFIED"})
    h = spec.canvas_height
    moves = []
    height_px = sk.ground_y - min(sk.bone("HEAD").tip_px[1], sk.bone("HEAD").base_px[1])
    for S, f in ARM_FRAMES.items():
        ua, fa = sk.bone(f"{S}_UPPER_ARM"), sk.bone(f"{S}_FOREARM")
        reach = ua.length_px + fa.length_px
        sgn = 1.0 if fa.tip_px[0] >= ua.base_px[0] else -1.0
        moves.append((f"{S}_HAND_TARGET", f, (-sgn * 0.3 * reach, -0.35 * reach)))
    for S, f in LEG_FRAMES.items():
        moves.append((f"{S}_FOOT_TARGET", f, (0.0, -0.12 * height_px)))
    target_keys = []
    for tid, f, (dx, dy) in moves:
        lp = rest[tid].local_pos
        dl = _world_delta_to_local(rest, tid, (dx / (h / 2.0), -dy / (h / 2.0)))
        target_keys.append({"bone": tid, "kind": "pos", "move_px": [round(dx, 3), round(dy, 3)], "keys": [
            {"frame": f - 10, "x": lp[0], "y": lp[1]},
            {"frame": f, "x": lp[0] + dl[0], "y": lp[1] + dl[1]},
            {"frame": f + 10, "x": lp[0], "y": lp[1]}]})
    head = sk.bone("HEAD")
    over = head.limits_deg[1] + CLAMP_EXCESS_DEG
    clamp = {"bone": "HEAD", "bone_id": ids.index("HEAD"), "frame": CLAMP_FRAME, "offset_deg": over,
             "value": rest["HEAD"].local_angle + math.radians(over), "limit_deg": head.limits_deg[1],
             "rest_key_frame": CLAMP_FRAME - 10, "rest_value": rest["HEAD"].local_angle}
    return {"links": links, "constraints": cons, "target_moves": target_keys, "clamp_test": clamp,
            "end_frame": IK_END_FRAME}


# -- simulation (labelled SIMULATION; never Moho output) ------------------------------------------------------------

def _two_bone(base, l1, l2, target, bend_sign) -> tuple[float, float]:
    dx, dy = target[0] - base[0], target[1] - base[1]
    d = min(max(math.hypot(dx, dy), abs(l1 - l2) + 1e-6), l1 + l2 - 1e-6)
    a = math.atan2(dy, dx)
    cos_b = (l1 * l1 + d * d - l2 * l2) / (2 * l1 * d)
    b = math.acos(max(-1.0, min(1.0, cos_b)))
    a1 = a + bend_sign * b
    elbow = (base[0] + l1 * math.cos(a1), base[1] + l1 * math.sin(a1))
    a2 = math.atan2(target[1] - elbow[1], target[0] - elbow[0])
    return a1, a2


def pose_world(spec: RigSpec, sk: NativeSkeletonSpec, offsets_deg: dict[str, float],
               pos_offset_px: dict[str, list[float]] | None = None,
               ik_targets_px: dict[str, tuple[float, float]] | None = None) -> dict[str, tuple]:
    """World (Moho units) base and angle per bone for a pose; optional analytic 2-bone IK toward targets (canvas
    px) with the elbow / knee bending the way the joint ranges allow."""
    rest = compute_rest_pose(spec)
    by = {r.name: r for r in rest}
    h = spec.canvas_height
    local = {r.name: r.local_angle + math.radians(offsets_deg.get(r.name, 0.0)) for r in rest}
    world: dict[str, tuple] = {}

    def fk():
        world.clear()
        for r in rest:
            lp = r.local_pos
            if r.parent is None:
                base, ang = lp, local[r.name]
            else:
                pb, pa = world[r.parent]
                off = rotate(lp, pa)
                base, ang = (pb[0] + off[0], pb[1] + off[1]), pa + local[r.name]
            if pos_offset_px and r.name in pos_offset_px:
                dx, dy = pos_offset_px[r.name]
                base = (base[0] + dx / (h / 2.0), base[1] - dy / (h / 2.0))
            world[r.name] = (base, ang)
    fk()
    for eff_id, tgt in (ik_targets_px or {}).items():
        eff = sk.bone(eff_id)
        root_id = eff.parent
        rb = world[root_id][0]
        t = px_to_world(tgt, spec.canvas_width, h)
        lim = eff.limits_deg or (0.0, 0.0)
        bend = 1.0 if abs(lim[1]) >= abs(lim[0]) else -1.0
        a1, a2 = _two_bone(rb, by[root_id].length, by[eff_id].length, t, -bend)
        parent_ang = world[by[root_id].parent][1]
        local[root_id] = a1 - parent_ang
        local[eff_id] = a2 - a1
        fk()
    return world


def render_pose(spec: RigSpec, sk: NativeSkeletonSpec, layers: dict[str, Image.Image], world: dict[str, tuple],
                background=None) -> Image.Image:
    from ..geometry import BonePose
    from ..preview import _invert_affine_px

    w, h = spec.canvas_width, spec.canvas_height
    rest = {r.name: BonePose(r.name, r.world_base, r.world_angle, r.length) for r in compute_rest_pose(spec)}
    canvas = Image.new("RGBA", (w, h), background or (0, 0, 0, 0))
    for part in spec.parts_back_to_front():
        base, ang = world[part.bone]
        m = layer_delta_px(w, h, rest[part.bone], BonePose(part.bone, base, ang, rest[part.bone].length))
        if np.allclose(np.asarray(m, float), np.eye(3), atol=1e-9):
            canvas.alpha_composite(layers[part.name])  # the bone is at rest: no resampling
            continue
        moved = layers[part.name].transform((w, h), Image.Transform.AFFINE, _invert_affine_px(m),
                                            resample=Image.Resampling.BICUBIC)
        canvas.alpha_composite(moved)
    return canvas


def expected_frames(out: Path, spec: RigSpec, sk: NativeSkeletonSpec, poses: list[dict[str, Any]],
                    ik: dict[str, Any]) -> dict[str, Any]:
    layers = {p.name: Image.open(out / p.image).convert("RGBA") for p in spec.parts}
    exp = out / "expected"
    exp.mkdir(parents=True, exist_ok=True)
    files, cells = {}, []
    font = ImageFont.load_default(size=max(18, spec.canvas_height // 40))
    for p in poses:
        world = pose_world(spec, sk, p["offsets_deg"], p["pos_offset_px"])
        im = render_pose(spec, sk, layers, world)
        rel = f"expected/pose_f{p['frame']:03d}_{p['id']}.png"
        im.save(out / rel, optimize=True)
        files[str(p["frame"])] = rel
        cell = Image.new("RGBA", im.size, (236, 236, 236, 255))
        cell.alpha_composite(im)
        ImageDraw.Draw(cell).text((12, 10), f"SIMULATION (not Moho)\n{p['frame']}: {p['label']}",
                                  fill=(200, 0, 0, 255), font=font)
        cells.append(cell)
    rest = compute_rest_pose(spec)
    by = {r.name: r for r in rest}
    for tm in ik["target_moves"]:
        tid, f = tm["bone"], tm["keys"][1]["frame"]
        eff = next(b.ik["effector"] for b in sk.bones if b.id == tid)
        tgt_rest = world_to_px(by[tid].world_base, spec.canvas_width, spec.canvas_height)
        tgt = (tgt_rest[0] + tm["move_px"][0], tgt_rest[1] + tm["move_px"][1])
        world = pose_world(spec, sk, {}, None, {eff: tgt})
        im = render_pose(spec, sk, layers, world)
        rel = f"expected/ik_f{f:03d}_{tid.lower()}.png"
        im.save(out / rel, optimize=True)
        files[str(f)] = rel
    c = ik["clamp_test"]
    for key, deg in (("clamped", c["limit_deg"]), ("unclamped", c["offset_deg"])):
        im = render_pose(spec, sk, layers, pose_world(spec, sk, {"HEAD": deg}))
        rel = f"expected/clamp_f{c['frame']:03d}_{key}.png"
        im.save(out / rel, optimize=True)
        files[f"{c['frame']}_{key}"] = rel
    cw, ch = spec.canvas_width // 3, spec.canvas_height // 3
    cols = 4
    sheet = Image.new("RGB", (cw * cols, ch * math.ceil(len(cells) / cols)), (255, 255, 255))
    for i, cell in enumerate(cells):
        sheet.paste(cell.convert("RGB").resize((cw, ch), Image.Resampling.LANCZOS), ((i % cols) * cw, (i // cols) * ch))
    sheet.save(exp / "SIMULATION_pose_sheet.png", optimize=True)
    files["sheet"] = "expected/SIMULATION_pose_sheet.png"
    return files


def closeup_boxes(spec: RigSpec, sk: NativeSkeletonSpec, poses: list[dict[str, Any]]) -> dict[str, Any]:
    """Crop boxes (canvas px) around each joint in the pose that moves it, for native close-ups."""
    rest = {r.name: r for r in compute_rest_pose(spec)}
    out = {}
    size = int(0.22 * spec.canvas_height)
    for joint, (bone, end, frame) in CLOSEUP_JOINTS.items():
        pose = next(p for p in poses if p["frame"] == frame)
        world = pose_world(spec, sk, pose["offsets_deg"], pose["pos_offset_px"])
        base, ang = world[bone]
        p = world_to_px(base, spec.canvas_width, spec.canvas_height)
        x0 = int(max(0, min(spec.canvas_width - size, p[0] - size / 2)))
        y0 = int(max(0, min(spec.canvas_height - size, p[1] - size / 2)))
        out[joint] = {"bone": bone, "frame": frame, "box": [x0, y0, x0 + size, y0 + size]}
    return out


# -- scripts --------------------------------------------------------------------------------------------------------

def _named(template: str, script: str, data: dict[str, Any]) -> str:
    return _inject(load_template(template), script, "Data generated by autorig native-fullbody.", data)


def verify_lua_data(spec: RigSpec, poses, asset_root: str | None) -> dict[str, Any]:
    root = (asset_root or "").rstrip("/")
    return {"generator": {"name": "autorig", "version": __version__, "spec_character_id": spec.character_id,
                          "spec_sha256": spec.canonical_sha256(), "script": VERIFY_SCRIPT},
            "report_path": _join_posix(root, VERIFY_REPORT) if root else "",
            "bone_layer_name": spec.bone_layer_name, "rest_frame": spec.rest_frame,
            "moves": pelvis_keys(spec, poses)}


def ik_lua_data(spec: RigSpec, ik: dict[str, Any], asset_root: str | None) -> dict[str, Any]:
    root = (asset_root or "").rstrip("/")
    return {"generator": {"name": "autorig", "version": __version__, "spec_character_id": spec.character_id,
                          "spec_sha256": spec.canonical_sha256(), "script": IK_SCRIPT},
            "report_path": _join_posix(root, IK_REPORT) if root else "",
            "bone_layer_name": spec.bone_layer_name, "rest_frame": spec.rest_frame, **ik}


def write_scripts(out: Path, spec: RigSpec, poses, ik, asset_root: str | None) -> dict[str, str]:
    moho = out / "moho"
    moho.mkdir(parents=True, exist_ok=True)
    files = {
        "build": (moho / f"{BUILD_SCRIPT}.lua", render_named_build_lua(
            spec, asset_root, BUILD_SCRIPT, TITLE,
            "Builds the AutoRig Studio Phase 4.1 full-body rig (25 semantic bones, the character's own layers bound "
            "to their bones, twelve test poses) and writes a verification report.")),
        "verify": (moho / f"{VERIFY_SCRIPT}.lua", _named(VERIFY_TEMPLATE, VERIFY_SCRIPT,
                                                         verify_lua_data(spec, poses, asset_root))),
        "ik": (moho / f"{IK_SCRIPT}.lua", _named(IK_TEMPLATE, IK_SCRIPT, ik_lua_data(spec, ik, asset_root))),
    }
    for path, text in files.values():
        path.write_text(text, encoding="utf-8")
    return {k: p.relative_to(out).as_posix() for k, (p, _) in files.items()}


# -- the package ------------------------------------------------------------------------------------------------------

def _validate_package(pkg: Path) -> list[str]:
    problems = []
    for rel in ("artwork/manifest.json", "natural_pose/rig.json", "editor/project.json", "source"):
        if not (pkg / rel).exists():
            problems.append(f"missing {rel}")
    return problems


def build_fullbody(package: str | Path, out: str | Path, canvas: tuple[int, int] | None = None,
                   asset_root: str | None = None, character_id: str | None = None,
                   include_pending_extensions: bool = False, config: dict[str, Any] | None = None,
                   render_expected: bool = True) -> dict[str, Any]:
    from ..editor.artwork import ArtworkModel
    from ..editor.project import EditorProject

    pkg, out = Path(package), Path(out)
    problems = _validate_package(pkg)
    if problems:
        raise NativeFullbodyError(problems)
    sk = NativeSkeletonSpec.from_package(pkg, character_id, config)
    problems = validate(sk)
    if problems:
        raise NativeFullbodyError(problems)
    rig = json.loads((pkg / "natural_pose/rig.json").read_text(encoding="utf-8"))
    struct_path = pkg / "natural_pose/garments/structure.json"
    structure = json.loads(struct_path.read_text(encoding="utf-8")) if struct_path.is_file() else None
    manifest = json.loads((pkg / "artwork/manifest.json").read_text(encoding="utf-8"))
    man_layers = {l["id"]: l for l in manifest.get("layers", [])}
    proj, art = EditorProject(pkg), ArtworkModel(pkg)
    st = proj.state()
    masks = art.masks(st["mask_ops"])
    rejected = {k for k, v in st["approvals"].items() if v.get("status") == "rejected"}
    sw, sh = art.W, art.H
    order = [L for L in st["layers"] if L["kind"] == "bitmap" and L["visible"] and L["id"] in masks]
    if not order:
        raise NativeFullbodyError(["no visible bitmap layers in the editor project"])
    poses = test_poses(sk)
    problems = check_pose_limits(sk, poses)
    if problems:
        raise NativeFullbodyError(problems)

    strategy = NativeBindingStrategy(sk, rig, structure)
    collected = []   # (name, rgba, status, binding)
    z = 0
    for L in order:
        cid = L["id"]
        rgba = art.layer_rgba(cid, masks, rejected, with_extensions=False)
        if not (rgba[..., 3] > 0).any():
            continue
        ys, xs = np.nonzero(rgba[..., 3] > 0)
        b = strategy.plan_layer(cid, (man_layers.get(cid) or {}).get("bone") or L.get("bone"), z,
                                (float(xs.mean()), float(ys.mean())))
        if include_pending_extensions:
            ext = art.layer_rgba(cid, masks, rejected, with_extensions=True)
            ext[(rgba[..., 3] > 0)] = 0
            if (ext[..., 3] > 0).any():
                collected.append((f"{cid}__ext_pending", ext, "pending_extension", b))
        collected.append((cid, rgba, "original", b))

    # Placement: the canvas holds the character in every test pose (integer offset, never a scale).
    bbox = pose_extent(sk, [(c[1], c[3].bone) for c in collected], poses, (sw, sh))
    need = (min(0, bbox[0] - CANVAS_MARGIN), min(0, bbox[1] - CANVAS_MARGIN),
            max(sw, bbox[2] + CANVAS_MARGIN), max(sh, bbox[3] + CANVAS_MARGIN))
    auto = (int(math.ceil((need[2] - need[0]) / 2.0) * 2), int(math.ceil((need[3] - need[1]) / 2.0) * 2))
    if canvas is None:
        cw, ch = auto
        offset = (int(-math.floor(need[0])), int(-math.floor(need[1])))
    else:
        cw, ch = canvas
        if cw < auto[0] or ch < auto[1]:
            raise NativeFullbodyError([f"canvas {cw}x{ch} cannot hold the test poses without cropping; it needs at "
                                       f"least {auto[0]}x{auto[1]} (omit --canvas to size it automatically)"])
        offset = (int(-math.floor(need[0])) + (cw - auto[0]) // 2, int(-math.floor(need[1])) + (ch - auto[1]) // 2)
    placement = Placement((cw, ch), offset)

    out.mkdir(parents=True, exist_ok=True)
    (out / "art").mkdir(exist_ok=True)
    parts, bindings, art_files = [], [], {}
    for z, (name, arr, status, b) in enumerate(collected):
        cid = b.layer
        img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        img.paste(Image.fromarray(arr, "RGBA"), placement.offset)
        rel = f"art/{name}.png"
        img.save(out / rel)
        art_files[name] = {"path": rel, "sha256": _sha256(out / rel), "pixel_sha256": pixel_hash(np.asarray(img)),
                           "alpha_px": int((np.asarray(img)[..., 3] > 0).sum()), "status": status,
                           "original_pixel_sha256": (man_layers.get(cid) or {}).get("pixel_sha256"),
                           "matches_original": status == "original" and pixel_hash(arr) ==
                           (man_layers.get(cid) or {}).get("pixel_sha256")}
        entry = asdict(b) if status == "original" else {
            **asdict(b), "layer": name, "status": status,
            "reason": f"pending generated pixels beneath {cid} (review build only)"}
        entry["z_index"] = z
        bindings.append(entry)
        parts.append({"name": name.lower(), "layer_name": name, "image": rel, "bone": b.bone, "z_index": z})
        sk.bone(b.bone).bindings.append(name)

    char_id = sk.character_id.lower().replace("-", "_")
    raw = build_raw_spec(sk, placement, parts, poses, f"{char_id}_p41"[:64],
                         f"{sk.character_id.replace('_', ' ')} (Phase 4.1 full-body rig)")
    spec = parse_spec(raw)
    (out / "rig_spec.json").write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    (out / "native_skeleton.json").write_text(json.dumps(sk.to_json(), indent=2, default=list) + "\n", encoding="utf-8")
    meta = metadata_items(pkg)
    brep = binding_report([])
    brep["layers"] = bindings
    brep["class_counts"] = {}
    for e in bindings:
        brep["class_counts"][e["binding_class"]] = brep["class_counts"].get(e["binding_class"], 0) + 1
    brep["metadata_only"] = {k: len(v) for k, v in meta.items()}
    (out / "binding_plan.json").write_text(json.dumps({**brep, "switchable": meta["SWITCHABLE"],
                                                       "corrective": meta["CORRECTIVE"]}, indent=2) + "\n",
                                           encoding="utf-8")
    ik = ik_plan(sk, spec)
    scripts = write_scripts(out, spec, poses, ik, asset_root)
    exp = expected_frames(out, spec, sk, poses, ik) if render_expected else {}
    ref = out / "reference"
    ref.mkdir(exist_ok=True)
    src = next((p for p in (pkg / "source").iterdir() if p.name.startswith("original")), None)
    if src is not None:
        shutil.copy2(src, ref / src.name)
    hand = hand_readiness(pkg, sk, meta["SWITCHABLE"])
    closeups = closeup_boxes(spec, sk, poses)
    plan = {"poses": poses, "render_range": [1, END_FRAME], "ik_render_range": [ARM_FRAMES["LEFT"], IK_END_FRAME],
            "closeups": closeups, "pelvis_after_reopen": pelvis_keys(spec, poses)}
    (out / "validation_plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    approvals = st.get("approvals") or {}
    man = {
        "schema": SCHEMA, "version": VERSION, "generator": {"name": "autorig", "version": __version__},
        "evidence_level": "portable package; native behaviour is verified only by a Moho run on the Mac",
        "package": {"path_name": pkg.resolve().name, "parent": pkg.resolve().parent.name,
                    "artwork_manifest_sha256": _sha256(pkg / "artwork/manifest.json"),
                    "rig_sha256": _sha256(pkg / "natural_pose/rig.json"),
                    "editor_project_sha256": _sha256(pkg / "editor/project.json")},
        "character_id": sk.character_id, "spec_character_id": spec.character_id,
        "source_canvas": [sw, sh], "moho_canvas": [cw, ch],
        "placement": {"offset_px": list(placement.offset), "scale": 1,
                      "rule": "canvas_px = source_px + offset; integer, never scaled"},
        "layers": {"count": len(parts), "originals": sum(1 for e in bindings if e.get("status") == "original"),
                   "pending_extensions": sum(1 for e in bindings if e.get("status") == "pending_extension"),
                   "all_originals_unchanged": all(f["matches_original"] for f in art_files.values()
                                                  if f["status"] == "original"),
                   "files": art_files},
        "bones": len(sk.bones), "controls": sk.controls(), "binding_counts": brep["class_counts"],
        "approvals": {"pending_or_missing_reviews": sum(1 for v in approvals.values()
                                                       if v.get("status") not in ("approved",)),
                      "switchable_metadata": len(meta["SWITCHABLE"]), "corrective_metadata": len(meta["CORRECTIVE"]),
                      "note": "nothing is approved by this command"},
        "hand_readiness": hand,
        "scripts": scripts, "asset_root": (asset_root or "").rstrip("/") or None,
        "reports": {"build": BUILD_REPORT, "verify": VERIFY_REPORT, "ik": IK_REPORT},
        "saved_documents": {"rig": SAVED_NAME, "ik_experiment": SAVED_IK_NAME},
        "expected": exp, "spec_sha256": spec.canonical_sha256(),
        "reference": [p.relative_to(out).as_posix() for p in sorted(ref.iterdir())],
    }
    files = sorted(p for p in out.rglob("*") if p.is_file() and p.name != MANIFEST)
    man["file_hashes"] = {p.relative_to(out).as_posix(): _sha256(p) for p in files
                          if not p.relative_to(out).as_posix().startswith("moho/") or not asset_root}
    (out / MANIFEST).write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
    return man


def hand_readiness(pkg: Path, sk: NativeSkeletonSpec, switchable: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for S, s in (("LEFT", "l"), ("RIGHT", "r")):
        hb = sk.bone(f"{S}_HAND")
        variants = [v for v in switchable if (v.get("use_side") or v.get("side")) == s]
        out[S] = {"bone": hb.id, "wrist_pivot_px": list(hb.base_px), "hand_length_px": round(hb.length_px, 2),
                  "bound_layers": list(hb.bindings), "variants": len(variants),
                  "approved_variants": sum(1 for v in variants if v.get("approval") == "approved"),
                  "native": "the original hand is bound to the HAND bone; Switch Layers (Phase 4.3) attach at the same "
                            "pivot; variants stay pending until a person approves them"}
    return out


def load_fullbody(d: str | Path) -> tuple[RigSpec, dict[str, Any], dict[str, Any], NativeSkeletonSpec | None]:
    d = Path(d)
    raw = json.loads((d / "rig_spec.json").read_text(encoding="utf-8"))
    man = json.loads((d / MANIFEST).read_text(encoding="utf-8"))
    return parse_spec(raw), raw, man, None
