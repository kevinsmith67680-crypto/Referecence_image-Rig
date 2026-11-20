"""verify-report must accept a correct Moho read-back and catch every class of
defect it is responsible for (it is the oracle of the native test)."""

import copy
import math

import pytest

from autorig.report import expected_state, verify_report


def perfect_report(spec):
    exp = expected_state(spec)
    return {
        "autorig_report_version": "0.1.0",
        "status": "completed",
        "generator": {"name": "autorig", "spec_sha256": spec.canonical_sha256()},
        "lua_version": "Lua 5.4",
        "moho_app_version": "14.4",
        "errors": [],
        "warnings": [],
        "document": {"width": 1920, "height": 1080, "fps": 24, "start_frame": 1, "end_frame": 36},
        "constants": {"LT_BONE": 4, "LT_IMAGE": 2},
        "bone_layer": {"name": "AutoRig_Phase0", "type": 4,
                       "children_by_index": [l["name"] for l in exp["layers_back_to_front"]]},
        "bones": [
            {"id": b["id"], "name": b["name"], "parent_id": b["parent_id"], "pos": b["pos"],
             "angle": b["angle_rad"], "length": b["length"], "world_base": b["world_base"],
             "world_tip": b["world_tip"], "angle_keys": b["angle_keys"]}
            for b in exp["bones"]
        ],
        "layers": [
            {"name": l["name"], "type": 2, "parent_bone_id": l["parent_bone_id"],
             "source_image_set": "/Volumes/SSD/x/" + l["image"]}
            for l in exp["layers_back_to_front"]
        ],
    }


def statuses(result):
    return {c.name: c.status for c in result.checks}


def test_perfect_report_passes(spec):
    r = verify_report(spec, perfect_report(spec))
    assert r.passed, r.format()
    assert "VERDICT: PASS" in r.format()


def test_angle_wraparound_is_accepted(spec):
    rep = perfect_report(spec)
    rep["bones"][2]["angle"] += 2 * math.pi  # same orientation, different representation
    assert verify_report(spec, rep).passed


def test_reverse_child_order_is_info_not_failure(spec):
    rep = perfect_report(spec)
    rep["bone_layer"]["children_by_index"].reverse()
    r = verify_report(spec, rep)
    assert r.passed and statuses(r)["bone_layer.z_order"] == "INFO"


@pytest.mark.parametrize("mutate, failing_check", [
    (lambda r: r.__setitem__("status", "aborted"), "report.status"),
    (lambda r: r["errors"].append("boom"), "report.errors"),
    (lambda r: r["document"].__setitem__("width", 1280), "document.size"),
    (lambda r: r["document"].__setitem__("end_frame", 72), "document.frame_range"),
    (lambda r: r["bone_layer"].__setitem__("type", 3), "bone_layer.type"),
    (lambda r: r["bones"].pop(), "bones.count"),
    (lambda r: r["bones"][3].__setitem__("parent_id", 0), "bone[forearm].parent"),
    (lambda r: r["bones"][1]["pos"].__setitem__(0, 0.5), "bone[head].fPos"),
    (lambda r: r["bones"][2].__setitem__("angle", 0.0), "bone[upper_arm].fAngle"),
    (lambda r: r["bones"][0].__setitem__("length", 1.0), "bone[torso].fLength"),
    (lambda r: r["bones"][3]["world_tip"].__setitem__(1, 0.9), "bone[forearm].world_matrix"),
    (lambda r: r["bones"][2]["angle_keys"].pop(2), "bone[upper_arm].angle_keys"),
    (lambda r: r["bones"][2]["angle_keys"][2].__setitem__(1, 9.0), "bone[upper_arm].angle_keys"),
    (lambda r: r["layers"][1].__setitem__("parent_bone_id", 0), "layer[head_art].binding"),
    (lambda r: r["layers"][1].__setitem__("parent_bone_id", -1), "layer[head_art].binding"),
    (lambda r: r["layers"][0].__setitem__("type", 1), "layer[torso_art].type"),
    (lambda r: r["layers"][3].__setitem__("source_image_set", "/x/art/hand.png"), "layer[forearm_art].image"),
    (lambda r: r["bone_layer"]["children_by_index"].pop(), "bone_layer.children"),
])
def test_defects_are_caught(spec, mutate, failing_check):
    rep = perfect_report(spec)
    mutate(rep)
    r = verify_report(spec, rep)
    assert not r.passed
    assert statuses(r).get(failing_check) == "FAIL", r.format()


def test_missing_layer_is_failure(spec):
    rep = perfect_report(spec)
    rep["layers"] = [l for l in rep["layers"] if l["name"] != "forearm_art"]
    st = statuses(verify_report(spec, rep))
    assert st["layer[forearm_art]"] == "FAIL"


def test_extra_keys_warn(spec):
    rep = perfect_report(spec)
    rep["bones"][0]["angle_keys"].append([20, 1.0])
    r = verify_report(spec, rep)
    assert r.passed and statuses(r)["bone[torso].extra_keys"] == "WARN"


def test_spec_mismatch_warns(spec):
    rep = perfect_report(spec)
    rep["generator"]["spec_sha256"] = "0" * 64
    r = verify_report(spec, rep)
    assert statuses(r)["provenance.spec_sha256"] == "WARN"


def test_unexpected_lua_version_warns(spec):
    rep = perfect_report(spec)
    rep["lua_version"] = "Lua 5.1"
    assert statuses(verify_report(spec, rep))["env.lua_version"] == "WARN"


def test_garbage_report_does_not_crash(spec):
    for rep in ({}, {"status": "completed"}, {"status": "completed", "bones": [{}], "layers": [{}]}):
        r = verify_report(spec, copy.deepcopy(rep))
        assert not r.passed
