import math
import random

import pytest

from autorig.geometry import (
    BonePose,
    angle_diff,
    compute_rest_pose,
    expected_angle_keys,
    forward_kinematics,
    layer_delta_px,
    mat_apply,
    pose_at,
    px_to_world,
    track_offset_deg,
    world_to_px,
    wrap_angle,
)
from autorig.spec import parse_spec


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


def test_px_world_mapping_matches_moho_frame():
    # Moho frame: y in [-1, 1], x in [-aspect, aspect], origin at centre, y up.
    assert px_to_world((960, 540), 1920, 1080) == (0.0, 0.0)
    assert px_to_world((0, 0), 1920, 1080) == pytest.approx((-1920 / 1080, 1.0))
    assert px_to_world((1920, 1080), 1920, 1080) == pytest.approx((1920 / 1080, -1.0))


def test_px_world_roundtrip():
    rng = random.Random(1)
    for _ in range(200):
        p = (rng.uniform(-100, 2000), rng.uniform(-100, 1200))
        q = world_to_px(px_to_world(p, 1920, 1080), 1920, 1080)
        assert q == pytest.approx(p, abs=1e-9)


@pytest.mark.parametrize("a", [0, 1, -1, math.pi, -math.pi, 3 * math.pi, -7.5, 100.0])
def test_wrap_angle_range(a):
    w = wrap_angle(a)
    assert -math.pi < w <= math.pi
    assert close(math.cos(w), math.cos(a)) and close(math.sin(w), math.sin(a))


def test_rest_pose_values(spec):
    rest = {r.name: r for r in compute_rest_pose(spec)}
    torso, head, upper, fore = rest["torso"], rest["head"], rest["upper_arm"], rest["forearm"]
    # root bone: world coordinates, pointing straight up
    assert torso.parent_index == -1
    assert torso.local_angle == pytest.approx(math.pi / 2)
    assert torso.local_pos == pytest.approx((0.0, (540 - 800) / 540))
    assert torso.length == pytest.approx(330 / 540)
    # head sits exactly on the torso tip: fPos == (parent length, 0), same direction
    assert head.parent_index == 0
    assert head.local_pos == pytest.approx((torso.length, 0.0))
    assert head.local_angle == pytest.approx(0.0)
    # forearm sits on the upper-arm tip
    assert fore.parent_index == 2
    assert fore.local_pos == pytest.approx((upper.length, 0.0))
    assert fore.local_angle == pytest.approx(math.atan2(90, 180))  # elbow bend (deg ~26.57)
    # upper arm starts at the shoulder, expressed in the torso's frame
    assert upper.local_pos == pytest.approx(((800 - 520) / 540, -(1060 - 960) / 540))


def test_fk_at_rest_reproduces_spec_joints(spec):
    rest = compute_rest_pose(spec)
    poses = forward_kinematics(rest, {})
    for r in rest:
        assert poses[r.name].world_base == pytest.approx(r.world_base, abs=1e-12)
        assert poses[r.name].world_tip == pytest.approx(r.world_tip, abs=1e-12)


def _random_spec(rng):
    bones = [{"name": "b0", "parent": None, "base_px": [rng.uniform(200, 1700), rng.uniform(200, 900)],
              "tip_px": [rng.uniform(200, 1700), rng.uniform(200, 900)]}]
    for i in range(1, 12):
        parent = bones[rng.randrange(len(bones))]
        bones.append({"name": f"b{i}", "parent": parent["name"],
                      "base_px": [rng.uniform(0, 1920), rng.uniform(0, 1080)],
                      "tip_px": [rng.uniform(0, 1920), rng.uniform(0, 1080)]})
    return parse_spec({
        "spec_version": "0.1.0",
        "character": {"id": "random", "display_name": "r"},
        "canvas": {"width_px": 1920, "height_px": 1080},
        "moho": {"bone_layer_name": "B", "rest_frame": 0, "start_frame": 1, "end_frame": 36,
                 "report_filename": "r.json"},
        "bones": bones,
        "parts": [{"name": "p", "layer_name": "p", "image": "p.png", "bone": "b0", "z_index": 0}],
        "animation": {"name": "a", "tracks": []},
    })


@pytest.mark.parametrize("seed", range(25))
def test_local_transform_inverts_fk_for_random_hierarchies(seed):
    """Property: converting world joints to parent-local fPos/fAngle and
    running FK must give back the world joints, for arbitrary trees
    (children not attached to parent tips, any angles)."""
    spec = _random_spec(random.Random(seed))
    rest = compute_rest_pose(spec)
    poses = forward_kinematics(rest, {})
    for r in rest:
        assert poses[r.name].world_base == pytest.approx(r.world_base, abs=1e-9)
        assert poses[r.name].world_tip == pytest.approx(r.world_tip, abs=1e-9)


def test_joint_continuity_every_frame(spec):
    rest = compute_rest_pose(spec)
    for f in range(0, 37):
        p = pose_at(spec, rest, f)
        assert p["forearm"].world_base == pytest.approx(p["upper_arm"].world_tip, abs=1e-12)
        assert p["head"].world_base == pytest.approx(p["torso"].world_tip, abs=1e-12)


def test_track_sampling_exact_at_keys(spec):
    for t in spec.tracks:
        for k in t.keys:
            assert track_offset_deg(spec, t.bone, k.frame) == k.value
    assert track_offset_deg(spec, "upper_arm", 0) == 0.0  # rest frame
    assert track_offset_deg(spec, "upper_arm", 5.5) == pytest.approx(40 * 4.5 / 9)


def test_expected_angle_keys(spec):
    rest = {r.name: r for r in compute_rest_pose(spec)}
    keys = expected_angle_keys(spec, list(rest.values()))
    ua = dict(keys["upper_arm"])
    assert sorted(ua) == [0, 1, 10, 19, 28, 36]
    assert ua[0] == rest["upper_arm"].local_angle
    assert ua[10] == pytest.approx(rest["upper_arm"].local_angle + math.radians(40))
    assert ua[36] == pytest.approx(ua[0])


def test_upper_arm_raises_at_frame_10(spec):
    """Positive offsets rotate counter-clockwise on screen (Moho y-up):
    the hand must move UP (smaller pixel y) at the wave peak."""
    rest = compute_rest_pose(spec)
    hand_rest = world_to_px(pose_at(spec, rest, 1)["forearm"].world_tip, 1920, 1080)
    hand_peak = world_to_px(pose_at(spec, rest, 10)["forearm"].world_tip, 1920, 1080)
    assert hand_peak[1] < hand_rest[1] - 200


def test_layer_delta_identity_at_rest(spec):
    rest = compute_rest_pose(spec)
    for r in rest:
        bp = BonePose(r.name, r.world_base, r.world_angle, r.length)
        m = layer_delta_px(1920, 1080, bp, bp)
        assert mat_apply(m, (123.0, 456.0)) == pytest.approx((123.0, 456.0))


def test_layer_delta_keeps_pivot_fixed(spec):
    rest = compute_rest_pose(spec)
    r = next(x for x in rest if x.name == "upper_arm")
    rest_pose = BonePose(r.name, r.world_base, r.world_angle, r.length)
    turned = BonePose(r.name, r.world_base, r.world_angle + 0.7, r.length)
    m = layer_delta_px(1920, 1080, rest_pose, turned)
    pivot = world_to_px(r.world_base, 1920, 1080)
    assert mat_apply(m, pivot) == pytest.approx(pivot, abs=1e-9)


def test_angle_diff():
    assert angle_diff(math.pi - 0.01, -math.pi + 0.01) == pytest.approx(-0.02)
