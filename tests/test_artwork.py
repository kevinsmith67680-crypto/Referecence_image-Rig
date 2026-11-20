import xml.etree.ElementTree as ET

import pytest
from PIL import Image, ImageChops

from autorig.artwork import build_recipes, render_part

from conftest import EXAMPLE_DIR

PARTS = ["torso", "head", "upper_arm", "forearm"]


@pytest.fixture(scope="module")
def layers(spec):
    return {p.name: Image.open(EXAMPLE_DIR / p.image) for p in spec.parts}


@pytest.mark.parametrize("name", PARTS)
def test_layer_is_full_canvas_rgba(layers, spec, name):
    img = layers[name]
    assert img.format == "PNG"
    assert img.mode == "RGBA"
    assert img.size == (spec.canvas_width, spec.canvas_height)


@pytest.mark.parametrize("name", PARTS)
def test_layer_background_is_transparent(layers, name):
    a = layers[name].getchannel("A")
    w, h = a.size
    for xy in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, 5)]:
        assert a.getpixel(xy) == 0
    coverage = (w * h - a.histogram()[0]) / (w * h)
    assert 0.003 < coverage < 0.2, coverage  # a part, not empty and not the whole frame


@pytest.mark.parametrize("name", PARTS)
def test_bone_pivot_lies_inside_its_own_art(layers, spec, name):
    part = next(p for p in spec.parts if p.name == name)
    bone = spec.bone(part.bone)
    x, y = (round(v) for v in bone.base_px)
    assert layers[name].getchannel("A").getpixel((x, y)) == 255


@pytest.mark.parametrize("child, parent", [("head", "torso"), ("upper_arm", "torso"), ("forearm", "upper_arm")])
def test_joints_overlap_parent_art(layers, spec, child, parent):
    """The child's pivot must also be covered by the parent's art, otherwise
    a gap opens at the joint as soon as the child rotates."""
    x, y = (round(v) for v in spec.bone(child).base_px)
    assert layers[parent].getchannel("A").getpixel((x, y)) == 255


def test_layers_are_distinct(layers):
    names = list(layers)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert ImageChops.difference(layers[a], layers[b]).getbbox() is not None


@pytest.mark.parametrize("name", PARTS)
def test_committed_png_matches_generator(layers, spec, name):
    """Committed art is reproducible from code (small tolerance for
    Pillow/zlib version differences between machines)."""
    fresh = render_part(build_recipes(spec)[name], spec.canvas_width, spec.canvas_height)
    diff = ImageChops.difference(fresh, layers[name].convert("RGBA"))
    hist = diff.convert("L").histogram()
    changed = sum(hist[8:])
    assert changed / (spec.canvas_width * spec.canvas_height) < 0.001


@pytest.mark.parametrize("name", PARTS)
def test_svg_vector_source(spec, name):
    part = next(p for p in spec.parts if p.name == name)
    root = ET.parse(EXAMPLE_DIR / part.vector_source).getroot()
    assert root.tag.endswith("svg")
    assert root.get("viewBox") == f"0 0 {spec.canvas_width} {spec.canvas_height}"
    groups = [g.get("id") for g in root if g.tag.endswith("g")]
    assert groups == ["outline", "fill", "details"]
