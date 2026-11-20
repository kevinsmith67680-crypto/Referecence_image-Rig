"""Natural pose refinement: skeleton, skinned joints, IK/balance solver and pose evaluation.

Unit scenes are synthetic; integration checks use the committed T-pose package (real artwork, real SAM 3
segmentation). These are preview-renderer checks, not Moho checks."""

import json
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from autorig.phase2.natural import legacy_render, load, natural_order
from autorig.phase2.pose_eval import closeups, evaluate
from autorig.phase2.posing import PRESET_BY_NAME, PRESETS, Preset, PoseSolver, two_bone_ik, wrap
from autorig.phase2.skeleton import HEAD_LAYERS, LIMITS, SIDES, Bone, Skeleton, build_skeleton, layer_class
from autorig.phase2.skinning import Deformer, PoseParams

TPOSE = Path(__file__).resolve().parents[1] / "projects" / "schoolboy_tpose" / "character_package"


# --- unit scene: a two-bone limb (upper + lower) cut into two layers at the joint -----------------------

def _limb_scene():
    H, W = 120, 220
    rgba_a, rgba_b = np.zeros((H, W, 4), np.uint8), np.zeros((H, W, 4), np.uint8)
    rgba_a[40:80, 20:110] = (200, 60, 60, 255)
    rgba_b[40:80, 110:200] = (60, 60, 200, 255)
    rgba_a[40:42, 20:110] = rgba_a[78:80, 20:110] = (10, 10, 10, 255)  # outlines along the limb
    rgba_b[40:42, 110:200] = rgba_b[78:80, 110:200] = (10, 10, 10, 255)
    J0, J1, J2 = np.array([20.0, 60]), np.array([110.0, 60]), np.array([200.0, 60])
    bones = [Bone("pelvis", None, "pelvis", None, J0 - [10, 0], J0, 20, "test"),
             Bone("thigh_r", "pelvis", "thigh", "r", J0, J1, 20, "test"),
             Bone("shin_r", "thigh_r", "shin", "r", J1, J2, 20, "test")]
    sk = Skeleton(bones, 100.0, 0.0, [])
    return sk, {"thigh_r": rgba_a, "shin_r": rgba_b}


def test_identity_pose_is_bit_exact():
    sk, layers = _limb_scene()
    d = Deformer(sk, layers)
    r = d.render(PoseParams.identity(3), ["thigh_r", "shin_r"])
    ref = np.zeros_like(layers["thigh_r"])
    for a in layers.values():
        ref[a[..., 3] > 0] = a[a[..., 3] > 0]
    assert np.array_equal(r["image"], ref)


def test_rigid_quarter_turn_keeps_original_pixels():
    sk, layers = _limb_scene()
    d = Deformer(sk, layers)
    pose = PoseParams(np.array([0.0, 90.0, 0.0]), np.ones(3))
    r = d.render(pose, ["thigh_r", "shin_r"], pad=120)
    # deep in the lower limb (far from the joint band) pixels are moved rigidly: same colour values
    J = d.world(2, pose)[:2, :2] @ np.array([170.0, 60.0]) + d.world(2, pose)[:2, 2]
    x, y = int(round(J[0])) + 120, int(round(J[1])) + 120
    assert tuple(r["image"][y, x]) == (60, 60, 200, 255)


def test_bent_joint_has_no_seam_where_rigid_layers_tear():
    sk, layers = _limb_scene()
    d = Deformer(sk, layers)
    pose = PoseParams(np.array([0.0, 0.0, 70.0]), np.ones(3))
    r = d.render(pose, ["thigh_r", "shin_r"], pad=60)
    dark = np.zeros((120, 220), bool)
    tears = d.tears(pose, r, dark)
    assert len(tears["exposed_gap"]) + len(tears["visible_seam"]) <= 2  # skinned band: continuous
    # the same bend with rigid layers opens a wedge on the outer side
    rigid = Image.new("RGBA", (340, 240), (0, 0, 0, 0))
    for cid, ang in (("thigh_r", 0.0), ("shin_r", 70.0)):
        lay = Image.new("RGBA", (340, 240), (0, 0, 0, 0))
        lay.paste(Image.fromarray(layers[cid]), (60, 60))
        T = d._bone_matrix(2, pose, 1.0) if ang else np.eye(3)
        T = np.array([[1, 0, 60], [0, 1, 60], [0, 0, 1.0]]) @ T @ np.array([[1, 0, -60], [0, 1, -60], [0, 0, 1.0]])
        inv = np.linalg.inv(T)
        rigid.alpha_composite(lay.transform(lay.size, Image.Transform.AFFINE, tuple(inv[:2].ravel()),
                                            resample=Image.Resampling.BILINEAR))
    from scipy import ndimage
    ra = np.asarray(rigid)[..., 3] > 127
    sa = r["image"][..., 3] > 127
    hull_gap = lambda a: int((ndimage.binary_closing(a, iterations=6) & ~a)[40:200, 100:240].sum())
    assert hull_gap(sa) < hull_gap(ra)


def test_skinning_folds_inner_side_under_child():
    sk, layers = _limb_scene()
    d = Deformer(sk, layers)
    st = d.render_layer("shin_r", PoseParams(np.array([0.0, 0.0, 120.0]), np.ones(3)))[3]
    assert st["rigid"] is False and st["det_min"] < 1.0 and st["blended_fraction"] > 0


# --- solver primitives ----------------------------------------------------------------------------------

def test_two_bone_ik_reaches_and_bends_to_the_preferred_side():
    mid, end, ok = two_bone_ik((0, 0), (0, 150), 100, 100, lambda k: -k[0])  # prefer knee to the left
    assert ok and np.allclose(end, (0, 150), atol=1e-6)
    assert math.isclose(np.hypot(*mid), 100, rel_tol=1e-9) and math.isclose(np.hypot(*(end - mid)), 100, rel_tol=1e-9)
    assert mid[0] < 0
    _, end2, ok2 = two_bone_ik((0, 0), (0, 500), 100, 100, lambda k: 0)
    assert not ok2 and np.hypot(*end2) <= 200  # unreachable: reported, never stretched


# --- integration on the real T-pose package ---------------------------------------------------------------

@pytest.fixture(scope="module")
def rig():
    data = load(TPOSE)
    sk = build_skeleton(data["joints"], {c: a[..., 3] > 0 for c, a in data["layers"].items()})
    dfm = Deformer(sk, data["layers"], data["generated"], reference=data["source"])
    return data, sk, dfm, PoseSolver(sk, dfm)


def test_skeleton_adds_spine_clavicles_and_refined_shoulders(rig):
    _, sk, _, _ = rig
    for n in ("pelvis", "spine", "chest", "neck", "head", "clavicle_r", "clavicle_l"):
        assert n in sk.idx
    sh = sk.bone("upper_arm_r")
    # pivot moved from the sleeve toward the armpit and onto the arm's medial line
    assert "armpit" in sh.method and abs(sh.head[1] - 465) < 12
    # pixels of the arm layer that lie medial of the shoulder belong to the body, not the arm
    pts = np.array([[455.0, 470.0], [300.0, 470.0]])
    homes = sk.home("upper_arm_r", pts)
    assert sk.names[homes[0]] in ("chest", "spine") and sk.names[homes[1]] == "upper_arm_r"
    # facial layers ride the head bone only (facial controls stay independent of body motion)
    for c in HEAD_LAYERS:
        assert layer_class(c) == "head"


@pytest.mark.parametrize("name", [p.name for p in PRESETS])
def test_presets_respect_limits_lengths_ground_and_balance(rig, name):
    _, sk, dfm, solver = rig
    pose, info = solver.solve(PRESET_BY_NAME[name])
    assert info["violations"] == [], info["violations"]  # no hyperextension or impossible joints
    for i, b in enumerate(sk.bones):  # bone lengths preserved (scaled bones: exactly the declared scale)
        L = np.hypot(*(np.array(info["bone_tails_posed"][b.name]) - info["joints_posed"][b.name]))
        assert abs(L - b.length * pose.scale[i]) < 0.2, b.name  # reported positions are rounded to 0.1 px
    for s in info["support"]["planted"]:  # planted feet sit exactly on their ground targets, flat
        assert np.hypot(*(np.array(info["joints_posed"][f"foot_{s}"]) - info["feet_targets"][s])) < 0.5
        assert abs(solver.rot(pose.theta, f"foot_{s}")) < 1e-6
    lo, hi = info["support"]["x_range"]
    assert lo <= info["com"][0] <= hi and abs(info["balance_error_px"]) <= 3.0
    a = solver.anatomical(pose)
    for s in SIDES:
        assert a[f"shin_{s}_flexion"] >= -LIMITS["shin"]["valgus_max"]  # knees never bend medially
        assert abs(a[f"forearm_{s}_flexion"]) <= 150


def test_out_of_range_input_is_clamped_and_reported(rig):
    _, _, _, solver = rig
    pr = Preset("bad", "bad", "test", arms={"r": {"abduction": 200.0, "flexion": 175.0}}, clavicle={"l": 60.0})
    pose, info = solver.solve(pr)
    v = " ".join(info["violations"])
    assert "upper_arm_r abduction 200 clamped" in v and "forearm_r flexion 175 clamped" in v
    assert "clavicle_l elevation 60 clamped" in v
    assert solver.anatomical(pose)["upper_arm_r_abduction"] <= LIMITS["upper_arm"]["abduction"][1] + 0.5


def test_squat_sequence_keeps_feet_planted_and_lowers_pelvis(rig):
    _, sk, _, solver = rig
    feet, pelvis = [], []
    for drop in (0.0, 0.08, 0.16, 0.24):
        pose, info = solver.solve(Preset(**{**PRESET_BY_NAME["natural_squat"].__dict__, "pelvis_drop": drop}))
        feet.append([info["joints_posed"][f"foot_{s}"] for s in SIDES])
        pelvis.append(info["joints_posed"]["pelvis"][1])
        assert all(solver.anatomical(pose)[f"shin_{s}_flexion"] >= 0 for s in SIDES)
    assert np.abs(np.array(feet) - feet[0]).max() < 1e-6  # no foot sliding
    assert all(b > a for a, b in zip(pelvis, pelvis[1:]))  # pelvis lowers (y grows)


def test_weight_shift_moves_centre_of_mass_over_the_stance_foot(rig):
    _, _, _, solver = rig
    xs = []
    for wl in (0.5, 0.8):
        pose, info = solver.solve(Preset(**{**PRESET_BY_NAME["relaxed_standing"].__dict__, "weight_left": wl}))
        xs.append(info["com"][0])
        fc = info["support"]["foot_centres"]
        assert abs(info["com"][0] - ((1 - wl) * fc["r"][0] + wl * fc["l"][0])) <= 3.0
    assert xs[1] > xs[0]  # character's left foot is on the image right


@pytest.mark.parametrize("name", ["relaxed_standing", "casual_walk", "head_tilt_compensated"])
def test_rendered_natural_poses_are_clean(rig, name, tmp_path):
    data, sk, dfm, solver = rig
    pr = PRESET_BY_NAME[name]
    pose, info = solver.solve(pr)
    r = dfm.render(pose, natural_order(data["order"], pr), pad=160)
    lum = data["source"][..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    tears = dfm.tears(pose, r, lum < 80)
    ev = evaluate(sk, solver, pose, info, r, pr, tears, 1)
    m = ev["metrics"]
    assert m["seams"]["exposed_gap_pairs"] + m["seams"]["visible_seam_pairs"] <= 8, m["seams"]
    assert m["seams"]["figure_pieces"] == 1  # nothing detaches
    assert m["ground_contact"]["pass"] and m["centre_of_mass"]["pass"] and m["limb_alignment"]["pass"]
    assert m["artwork_fidelity"]["rigidly_moved_share"] > 0.55  # most pixels move rigidly (exact look)
    assert ev["status"] != "NEEDS_ARTWORK" and ev["confidence"] < 1.0
    # visual check artefact: close-ups of shoulders/elbows/hips/knees (before above, after below)
    old = legacy_render(data, solver, pose, 160)
    img = closeups(r["image"], old, sk, info, 160)
    img.save(tmp_path / f"{name}.png")
    assert img.width > 800 and img.height > 200


def test_poses_needing_new_artwork_are_labelled_not_passed(rig):
    data, sk, dfm, solver = rig
    for name in ("arms_crossed", "running", "pointing_speaking"):
        pr = PRESET_BY_NAME[name]
        pose, info = solver.solve(pr)
        r = {"image": np.zeros((10, 10, 4), np.uint8), "alphas": {}, "stats": {}, "generated_visible":
             np.zeros((10, 10), bool), "pad": 0}
        ev = evaluate(sk, solver, pose, info, r, pr, None, 1)
        assert ev["status"] == "NEEDS_ARTWORK" and ev["needs_artwork"]


def test_committed_refinement_outputs_are_present_and_consistent():
    out = TPOSE / "natural_pose"
    presets = json.loads((out / "presets.json").read_text())
    report = json.loads((out / "report.json").read_text())
    rig = json.loads((out / "rig.json").read_text())
    assert report["neutral_pose_exact"] is True
    assert set(presets) == {p.name for p in PRESETS}
    assert all(v["status"] in ("OK_PENDING_REVIEW", "NEEDS_REVIEW", "NEEDS_ARTWORK") for v in presets.values())
    assert all(v["confidence"] < 1.0 for v in presets.values())
    assert all(s["max_planted_foot_drift_px"] == 0 for s in report["sequences"].values())
    assert {"shoulder", "elbow", "wrist", "hip", "knee", "neck"} <= set(rig["corrections"])
    assert "proposed_unverified" in rig["moho"]["status"]
    for f in ("contact_sheet.png", "contact_sheet_diagnostic.png"):
        im = Image.open(out / f)
        assert im.width >= 2000 and im.height >= 3000
    for p in PRESETS:
        assert (out / "poses" / f"{p.name}.png").is_file() and (out / "closeups" / f"{p.name}.jpg").is_file()
