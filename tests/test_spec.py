import copy

import pytest

from autorig.spec import SpecError, missing_assets, parse_spec

from conftest import EXAMPLE_DIR


def test_example_spec_loads(spec):
    assert spec.character_id == "phase0_test_character"
    assert [b.name for b in spec.bones] == ["torso", "head", "upper_arm", "forearm"]
    assert {p.name for p in spec.parts} == {"torso", "head", "upper_arm", "forearm"}
    assert (spec.start_frame, spec.end_frame, spec.frame_count) == (1, 36, 36)
    assert (spec.canvas_width, spec.canvas_height) == (1920, 1080)


def test_example_hierarchy(spec):
    parents = {b.name: b.parent for b in spec.bones}
    assert parents == {"torso": None, "head": "torso", "upper_arm": "torso", "forearm": "upper_arm"}


def test_every_part_bound_to_its_own_bone(spec):
    assert {p.name: p.bone for p in spec.parts} == {
        "torso": "torso", "head": "head", "upper_arm": "upper_arm", "forearm": "forearm"}


def test_back_to_front_order(spec):
    assert [p.name for p in spec.parts_back_to_front()] == ["torso", "head", "upper_arm", "forearm"]


def test_example_assets_present(spec):
    assert missing_assets(spec, EXAMPLE_DIR) == []


def test_animation_is_36_frames_and_loops(spec):
    for t in spec.tracks:
        assert t.keys[0].frame == spec.start_frame
        assert t.keys[-1].frame == spec.end_frame
        # loop seam: first and last key are both the rest pose
        assert t.keys[0].value == 0 and t.keys[-1].value == 0


def test_hash_is_key_order_independent(raw_spec, spec):
    reordered = dict(reversed(list(copy.deepcopy(raw_spec).items())))
    assert parse_spec(reordered).canonical_sha256() == spec.canonical_sha256()


def _mutate(raw, fn):
    r = copy.deepcopy(raw)
    fn(r)
    return r


@pytest.mark.parametrize(
    "mutation, needle",
    [
        (lambda r: r.pop("bones"), "'bones' is a required property"),
        (lambda r: r["bones"][0].__setitem__("name", "Torso!"), "does not match"),
        (lambda r: r["parts"][0].__setitem__("image", "../escape.png"), "does not match"),
        (lambda r: r["parts"][0].__setitem__("image", "/abs/torso.png"), "does not match"),
        (lambda r: r["animation"]["tracks"][0].__setitem__("channel", "scale"), "is not one of"),
        (lambda r: r.__setitem__("unexpected", 1), "Additional properties"),
        (lambda r: r["moho"].__setitem__("rest_frame", 1), "was expected"),
    ],
)
def test_schema_rejects(raw_spec, mutation, needle):
    with pytest.raises(SpecError) as exc:
        parse_spec(_mutate(raw_spec, mutation))
    assert any(needle in p for p in exc.value.problems), exc.value.problems


@pytest.mark.parametrize(
    "mutation, needle",
    [
        (lambda r: r["bones"].reverse(), "must be declared earlier"),
        (lambda r: r["bones"][1].__setitem__("name", "torso"), "duplicate bone name"),
        (lambda r: r["bones"][2].__setitem__("tip_px", r["bones"][2]["base_px"]), "zero-length"),
        (lambda r: r["bones"][0].__setitem__("parent", "head"), "must be declared earlier"),
        (lambda r: r["parts"][0].__setitem__("bone", "tail"), "unknown bone 'tail'"),
        (lambda r: r["parts"][1].__setitem__("z_index", 0), "duplicate z_index"),
        (lambda r: r["parts"][1].__setitem__("layer_name", "torso_art"), "duplicate layer_name"),
        (lambda r: r["parts"][1].__setitem__("layer_name", "AutoRig_Phase0"), "collides with the bone layer"),
        (lambda r: r["animation"]["tracks"][0]["keys"].append({"frame": 37, "value": 0}), "outside [1, 36]"),
        (lambda r: r["animation"]["tracks"][0]["keys"].append({"frame": 5, "value": 0}), "strictly increasing"),
        (lambda r: r["animation"]["tracks"].append(copy.deepcopy(r["animation"]["tracks"][0])), "duplicate track"),
        (lambda r: r["moho"].__setitem__("end_frame", 0), "end_frame"),
    ],
)
def test_semantics_rejects(raw_spec, mutation, needle):
    with pytest.raises(SpecError) as exc:
        parse_spec(_mutate(raw_spec, mutation))
    assert any(needle in p for p in exc.value.problems), exc.value.problems
