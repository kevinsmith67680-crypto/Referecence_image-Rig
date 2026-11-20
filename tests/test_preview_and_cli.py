import json

import pytest
from PIL import Image, ImageChops

from autorig.cli import main
from autorig.geometry import compute_rest_pose, pose_at, world_to_px
from autorig.preview import load_layers, render_frame

from conftest import EXAMPLE_DIR, SPEC_PATH


@pytest.fixture(scope="module")
def layers(spec):
    return load_layers(spec, EXAMPLE_DIR)


def test_rest_frame_equals_plain_composite(spec, layers):
    base = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    for p in spec.parts_back_to_front():
        base.alpha_composite(layers[p.name])
    rendered = render_frame(spec, layers, 0, background=None)
    diff = ImageChops.difference(base, rendered).convert("L")
    assert diff.getextrema()[1] <= 2  # resampling noise only


@pytest.mark.parametrize("frame", [10, 19, 28])
def test_elbow_stays_connected_when_posed(spec, layers, frame):
    """Pixel-level joint check: at the posed elbow position both the moved
    upper arm and the moved forearm art are opaque."""
    rest = compute_rest_pose(spec)
    elbow = world_to_px(pose_at(spec, rest, frame)["forearm"].world_base, 1920, 1080)
    x, y = round(elbow[0]), round(elbow[1])
    only = lambda name: {k: (v if k == name else Image.new("RGBA", v.size)) for k, v in layers.items()}
    upper = render_frame(spec, only("upper_arm"), frame, background=None)
    fore = render_frame(spec, only("forearm"), frame, background=None)
    assert upper.getchannel("A").getpixel((x, y)) > 200
    assert fore.getchannel("A").getpixel((x, y)) > 200


def test_committed_preview_files(spec):
    gif = Image.open(EXAMPLE_DIR / "preview" / "phase0_preview.gif")
    assert gif.n_frames == spec.frame_count == 36
    sheet = Image.open(EXAMPLE_DIR / "preview" / "contact_sheet.png")
    assert sheet.width > 1000


def test_committed_expected_json_in_sync(spec):
    from autorig.report import expected_state

    committed = json.loads((EXAMPLE_DIR / "expected" / "expected_rig.json").read_text())
    assert committed == json.loads(json.dumps(expected_state(spec)))


def test_cli_validate(capsys):
    assert main(["validate", str(SPEC_PATH)]) == 0
    assert "spec OK" in capsys.readouterr().out


def test_cli_build_lua_requires_assets(tmp_path, capsys):
    out = tmp_path / "AutoRig_Phase0_Build.lua"
    assert main(["build-lua", str(SPEC_PATH), "--asset-root", str(tmp_path), "--out", str(out)]) == 1
    assert main(["build-lua", str(SPEC_PATH), "--out", str(out)]) == 0
    text = out.read_text()
    assert str(EXAMPLE_DIR.resolve().as_posix()) in text


def test_cli_verify_report_exit_codes(tmp_path, spec):
    from test_report import perfect_report

    good = tmp_path / "good.json"
    good.write_text(json.dumps(perfect_report(spec)))
    assert main(["verify-report", str(SPEC_PATH), str(good)]) == 0
    bad_report = perfect_report(spec)
    bad_report["layers"][0]["parent_bone_id"] = 3
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(bad_report))
    assert main(["verify-report", str(SPEC_PATH), str(bad)]) == 1


def test_cli_invalid_spec_exit_code(tmp_path, raw_spec):
    raw_spec = dict(raw_spec)
    raw_spec.pop("parts")
    p = tmp_path / "bad_spec.json"
    p.write_text(json.dumps(raw_spec))
    assert main(["validate", str(p)]) == 2
