"""`.moho` / `.mohoproj` inspection and diff tooling.

The fixtures below are SYNTHETIC. Their key names (e.g. "live_inputs") are
invented for the test only and say nothing about Moho's real format, which is
exactly what this tool is meant to discover from files saved by Moho 14.5."""

import json
import re
import zipfile

import pytest

from autorig.cli import main
from autorig.mohofile import Change, MohoFileError, diff, grep, load_project, summarize

BASE = {
    "version": 1,
    "layers": [
        {"name": "AutoRig_Phase0", "type": "BoneLayer",
         "skeleton": {"bones": [{"name": "head", "angle": 0.0}]},
         "actions": [{"name": "head", "smart_bone": True}]},
    ],
    "thumbnail_time": "2026-11-20T10:00:00",
}


def write_moho(path, project, extra=None, name="Project.mohoproj"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(name, json.dumps(project))
        zf.writestr("thumbnail.jpg", b"\xff\xd8fake")
        for n, data in (extra or {}).items():
            zf.writestr(n, data)
    return path


def mapped():
    import copy

    b = copy.deepcopy(BASE)
    b["layers"][0]["actions"][0]["live_inputs"] = [{"device": "camera", "input": "SYNTHETIC_HEAD_TURN"}]
    b["thumbnail_time"] = "2026-11-20T10:05:00"
    return b


def test_load_zip_and_bare_project(tmp_path):
    z = load_project(write_moho(tmp_path / "a.moho", BASE))
    assert z.data == BASE and z.project_member == "Project.mohoproj"
    assert ("thumbnail.jpg", 6) in z.archive_members
    bare = tmp_path / "a.mohoproj"
    bare.write_text(json.dumps(BASE), encoding="utf-8")
    assert load_project(bare).data == BASE


@pytest.mark.parametrize("builder, needle", [
    (lambda p: write_moho(p, BASE, extra={"Other.mohoproj": "{}"}), "exactly one .mohoproj"),
    (lambda p: zipfile.ZipFile(p, "w").close() or p, "exactly one .mohoproj"),
])
def test_bad_archives(tmp_path, builder, needle):
    path = builder(tmp_path / "bad.moho")
    with pytest.raises(MohoFileError, match=needle):
        load_project(path)


def test_invalid_json(tmp_path):
    p = tmp_path / "x.mohoproj"
    p.write_bytes(b"{not json")
    with pytest.raises(MohoFileError, match="not valid UTF-8 JSON"):
        load_project(p)


def test_summarize_and_grep(tmp_path):
    proj = load_project(write_moho(tmp_path / "a.moho", BASE))
    text = "\n".join(summarize(proj))
    assert "Project.mohoproj" in text and "layers: [array, 1 items]" in text
    hits = dict(grep(proj.data, "smart|HEAD"))
    assert "$.layers[0].actions[0].smart_bone" in hits
    assert "$.layers[0].skeleton.bones[0].name" in hits  # value "head" matches case-insensitively


def test_diff_locates_a_single_hand_made_change():
    changes = diff(BASE, mapped(), ignore=re.compile(r"thumbnail_time"))
    assert changes == [Change("added", "$.layers[0].actions[0].live_inputs",
                              new=[{"device": "camera", "input": "SYNTHETIC_HEAD_TURN"}])]


def test_diff_kinds_and_lists():
    a = {"x": 1, "y": [1, 2, 3], "z": {"k": "v"}, "gone": True}
    b = {"x": 2, "y": [1, 2], "z": {"k": "v", "new": None}, "t": 1.0}
    got = {(c.kind, c.path) for c in diff(a, b)}
    assert got == {("changed", "$.x"), ("removed", "$.y[2]"), ("added", "$.z.new"),
                   ("removed", "$.gone"), ("added", "$.t")}
    assert diff({"n": 1}, {"n": 1.0})[0].kind == "changed"  # type changes are reported
    assert diff(BASE, BASE) == []


def test_cli_inspect_grep_diff(tmp_path, capsys):
    a = write_moho(tmp_path / "before.moho", BASE)
    b = write_moho(tmp_path / "after.moho", mapped())
    assert main(["moho-file", "inspect", str(a)]) == 0
    assert "top-level keys" in capsys.readouterr().out
    assert main(["moho-file", "inspect", str(b), "--grep", "input"]) == 0
    assert "live_inputs" in capsys.readouterr().out
    assert main(["moho-file", "diff", str(a), str(b), "--ignore", "thumbnail_time"]) == 0
    out = capsys.readouterr().out
    assert "+ $.layers[0].actions[0].live_inputs" in out and "(1 changes)" in out
    assert main(["moho-file", "inspect", str(tmp_path / "missing.moho")]) == 1


def test_cli_build_probe(tmp_path, capsys):
    out = tmp_path / "AutoRig_Probe.lua"
    assert main(["build-probe", "--report-dir", str(tmp_path), "--out", str(out)]) == 0
    assert tmp_path.as_posix() in out.read_text()
    assert main(["build-probe", "--report-dir", str(tmp_path / "nope"), "--out", str(out)]) == 1
