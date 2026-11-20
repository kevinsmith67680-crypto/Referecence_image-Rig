"""Unit tests for Phase 1 building blocks (synthetic inputs only)."""

import math

import numpy as np
import pytest

from autorig.phase1 import compositor as C
from autorig.phase1 import masks as M
from autorig.phase1.anatomy import (JointEstimate, LabeledMask, _path_bend, assign_sides, collect_instances,
                                    limb_axis, validate_joints, JOINT_PARENTS)
from autorig.segmentation.types import ConceptResult, Instance, Prompt, SegmentationRun


def box_mask(shape, x0, y0, x1, y1):
    m = np.zeros(shape, bool)
    m[y0:y1, x0:x1] = True
    return m


def test_empty_mask_helpers():
    e = np.zeros((5, 7), bool)
    assert M.bbox_xyxy(e) is None and M.centroid(e) is None and M.area(e) == 0
    assert M.iou(e, e) == 0.0 and M.fraction_inside(e, (0, 0, 7, 5)) == 0.0


def test_bbox_is_exclusive_and_in_image_coordinates():
    m = box_mask((10, 20), 3, 2, 8, 6)  # non-square image, x != y
    assert M.bbox_xyxy(m) == [3, 2, 8, 6]
    assert M.centroid(m) == pytest.approx((5.0, 3.5))


def test_extract_layer_copies_pixels_exactly():
    rng = np.random.default_rng(0)
    src = rng.integers(0, 256, (12, 9, 4), dtype=np.uint8)
    m = box_mask((12, 9), 2, 3, 6, 9)
    out = M.extract_layer(src, m)
    assert np.array_equal(out[m], src[m]) and (out[~m] == 0).all()


def test_mask_png_roundtrip(tmp_path):
    m = box_mask((31, 17), 1, 2, 9, 20)
    M.save_mask(m, tmp_path / "m.png")
    assert np.array_equal(M.load_mask(tmp_path / "m.png"), m)


def _run(shape, label_masks):
    results = []
    for k, (label, masks, score) in enumerate(label_masks):
        insts = [Instance(m, score, (0, 0, 1, 1), k, j) for j, m in enumerate(masks)]
        results.append(ConceptResult(Prompt(text=label, label=label), insts))
    return SegmentationRun("test", "0" * 64, (shape[1], shape[0]), results)


def test_collect_rejects_empty_tiny_weak_wrongly_shaped_and_duplicates():
    shape = (100, 120)
    a = box_mask(shape, 10, 10, 50, 50)
    a2 = a.copy()
    a2[10, 10] = False  # near-duplicate
    run = _run(shape, [("arm", [a, a2, np.zeros(shape, bool), box_mask(shape, 0, 0, 1, 1)], 0.9),
                       ("hand", [box_mask(shape, 60, 60, 90, 90)], 0.2),
                       ("leg", [np.ones((5, 5), bool)], 0.9)])
    acc, rej = collect_instances(run, ["arm", "hand", "leg"], shape, min_score=0.5)
    assert len(acc["arm"]) == 1 and acc["hand"] == [] and acc["leg"] == []
    reasons = " ".join(r["reason"] for r in rej)
    assert "duplicate" in reasons and "area" in reasons and "score" in reasons and "shape" in reasons


def test_overlapping_layers_absorb_fringe_keeps_partition():
    shape = (40, 40)
    a, b = box_mask(shape, 5, 5, 20, 30), box_mask(shape, 20, 5, 35, 30)
    fg = box_mask(shape, 4, 4, 36, 31)  # 1px fringe around both
    out, n = C.absorb_fringe({"a": a, "b": b}, fg, max_px=2)
    assert n > 0
    assert not (out["a"] & out["b"]).any()
    assert ((out["a"] | out["b"]) == fg).all()


def test_side_assignment_and_split_of_spanning_instance():
    shape = (60, 100)
    both = box_mask(shape, 10, 10, 30, 50) | box_mask(shape, 70, 10, 90, 50)
    sides, flags = assign_sides([LabeledMask("arm", both, 0.9, {})], midline_x=50)
    assert set(sides) == {"r", "l"} and M.centroid(sides["r"].mask)[0] < 50 and flags


def test_bend_detection_on_l_shaped_limb():
    shape = (200, 200)
    limb = box_mask(shape, 40, 20, 70, 140) | box_mask(shape, 40, 110, 170, 140)  # vertical then horizontal
    axis = limb_axis(limb, (165, 125))
    bend = _path_bend(axis)
    assert bend is not None and bend[1] >= 30
    assert math.dist(bend[0], (55, 125)) < 30  # the corner


def test_straight_limb_has_no_strong_bend():
    limb = box_mask((200, 60), 20, 10, 40, 190)
    axis = limb_axis(limb, (30, 185))
    bend = _path_bend(axis)
    assert bend is None or bend[1] < 15


def test_validate_joints_flags_problems():
    shape = (100, 100)
    sil = box_mask(shape, 20, 20, 80, 80)
    J = {n: JointEstimate(n, 50.0, 50.0, 0.9, "t") for n in JOINT_PARENTS}
    J["head"] = JointEstimate("head", 150.0, 10.0, 0.9, "t")       # outside image
    J["hand_r"] = JointEstimate("hand_r", None, None, 0.0, "not_found")
    J["shoulder_r"] = JointEstimate("shoulder_r", 70.0, 30.0, 0.9, "t")
    J["shoulder_l"] = JointEstimate("shoulder_l", 30.0, 30.0, 0.9, "t")  # crossed
    issues = validate_joints(J, shape, sil)
    text = str(issues)
    assert "outside the image" in text and "missing" in text and "zero-length" in text and "crossed" in text


def test_foreground_estimate_alpha_and_background():
    rgba = np.zeros((20, 30, 4), np.uint8)
    rgba[5:15, 10:20] = (200, 50, 50, 255)
    fg, info = C.estimate_foreground(rgba)
    assert info["method"] == "alpha_channel" and fg.sum() == 100
    opaque = np.full((100, 120, 4), 255, np.uint8)
    opaque[..., :3] = (240, 240, 240)
    opaque[20:60, 30:70, :3] = (20, 20, 20)
    opaque[35:41, 45:51, :3] = (245, 245, 245)  # small enclosed bg-coloured area (e.g. an eye white)
    fg, info = C.estimate_foreground(opaque)
    assert info["method"] == "background_colour_heuristic" and fg[38, 48] and not fg[0, 0]


def test_fidelity_detects_altered_pixels():
    src = np.zeros((10, 10, 4), np.uint8)
    src[2:8, 2:8] = (100, 150, 200, 255)
    m = box_mask((10, 10), 2, 2, 8, 8)
    recon = M.extract_layer(src, m)
    recon[4, 4, 0] = 0  # tamper
    f = C.fidelity(src, recon, m, {"x": m})
    assert f["pixels_differing_inside_layers"] == 1 and f["max_abs_error_inside_layers"] == 100.0
