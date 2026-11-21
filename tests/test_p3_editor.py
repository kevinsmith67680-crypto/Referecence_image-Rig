"""Phase 3 editor backend: event-sourced project, mask partition, joints, vectors, pose preview, comparison,
readiness, export and quality gates, on the user's real T-pose character package (not a synthetic fixture)."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from autorig.editor.artwork import ArtworkModel
from autorig.editor.export import export_package, quality_gates, rejected_ids, to_moho, validate_package
from autorig.editor.posesvc import PoseError, PoseService, bone_warnings
from autorig.editor.project import VERSION, EditorProject, OpError, migrate
from autorig.editor.readiness import face_panel, live_panel
from autorig.editor.vectors import parse_path_d, path_d, to_svg

ROOT = Path(__file__).resolve().parents[1]
REAL_PKG = ROOT / "projects" / "schoolboy_tpose" / "character_package"
pytestmark = pytest.mark.skipif(not (REAL_PKG / "natural_pose" / "rig.json").is_file(),
                                reason="real T-pose character package not present")
BLAZER = [[428, 402], [470, 402], [470, 584], [428, 584]]  # blazer pixels SAM 3 assigned to the right upper arm


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _ignore(d, names):
    skip = {"editor"}
    if Path(d).name == "natural_pose":
        skip |= {"poses", "legacy", "closeups", "contact_sheet.png", "contact_sheet_diagnostic.png"}
    return [n for n in names if n in skip]


@pytest.fixture(scope="module")
def base_copy(tmp_path_factory) -> Path:
    dst = tmp_path_factory.mktemp("p3") / "base"
    shutil.copytree(REAL_PKG, dst, ignore=_ignore)
    return dst


@pytest.fixture
def pkg(base_copy, tmp_path) -> Path:
    dst = tmp_path / "character_package"
    shutil.copytree(base_copy, dst)
    return dst


@pytest.fixture(scope="module")
def shared(base_copy):
    """Read-only model shared by tests that do not edit the project."""
    return base_copy, ArtworkModel(base_copy)


def _masks(proj, art):
    return art.masks(proj.state()["mask_ops"])


def _fid(proj, art):
    st = proj.state()
    masks = art.masks(st["mask_ops"])
    order = [L["id"] for L in st["layers"] if L["kind"] == "bitmap" and L["visible"] and L["id"] in masks]
    return art.fidelity(order, masks, rejected_ids(st))


# -- loading ---------------------------------------------------------------------------------------------
def test_real_project_loads_with_hierarchy_bones_and_vectors(shared):
    pkg, art = shared
    st = EditorProject(pkg).state()
    bitmap = [L for L in st["layers"] if L["kind"] == "bitmap"]
    switch = [L for L in st["layers"] if L["kind"] == "switch"]
    assert len(bitmap) >= 25 and switch, "real layers and corrective hand variants"
    assert all(not L["visible"] for L in switch), "corrective variants start hidden (pending review)"
    assert {"CHARACTER", "Head", "Torso", "Right_Arm", "Left_Arm"} <= set(st["groups"])
    assert all(L["group"] in st["groups"] for L in st["layers"])
    assert len(st["bones"]) == 19
    assert sum(1 for b in st["bones"].values() if not b["parent"]) == 1
    assert st["vectors"], "Phase 2 vector guides are seeded as editable vectors"
    assert (art.W, art.H) == (1086, 1448)
    assert _fid(EditorProject(pkg), art)["neutral_exact"], "untouched project reproduces the reference exactly"


def test_project_records_source_hash_and_persists_atomically(pkg):
    proj = EditorProject(pkg)
    meta = json.loads((pkg / "metadata.json").read_text())
    assert proj.data["source_sha256"] == meta["source"]["sha256"]
    assert (pkg / "editor" / "project.json").is_file()
    assert not list((pkg / "editor").glob("*.tmp")), "no leftover temp files"


# -- layer operations ------------------------------------------------------------------------------------
def test_layer_ops_validate_and_persist(pkg):
    proj = EditorProject(pkg)
    ids = [L["id"] for L in proj.state()["layers"]]
    i = ids.index("forearm_r")
    proj.submit({"type": "layer.reorder", "id": "forearm_r", "to_index": i + 1})
    proj.submit({"type": "layer.set", "id": "hair", "fields": {"visible": False, "opacity": 0.5, "edge_feather": 1.0}})
    proj.submit({"type": "layer.duplicate", "id": "nose"})
    proj.submit({"type": "group.create", "id": "Accessories", "parent": "Torso", "layers": ["neck"]})
    with pytest.raises(OpError):
        proj.submit({"type": "layer.set", "id": "hair", "fields": {"opacity": 1.7}})
    with pytest.raises(OpError):
        proj.submit({"type": "layer.set", "id": "hair", "fields": {"file": "x.png"}})  # not editable
    with pytest.raises(OpError):
        proj.submit({"type": "layer.set", "id": "hair", "fields": {"bone": "tail_bone"}})
    proj.submit({"type": "layer.set", "id": "mouth", "fields": {"locked": True}})
    with pytest.raises(OpError):
        proj.submit({"type": "mask.edit", "layer": "mouth", "mode": "add",
                     "shape": {"type": "stroke", "points": [[540, 300]], "radius": 5, "hardness": 1}})
    n_ops = proj.cursor
    reopened = EditorProject(pkg).state()
    assert EditorProject(pkg).cursor == n_ops, "invalid operations never enter the log"
    assert [L["id"] for L in reopened["layers"]].index("forearm_r") == i + 1
    hair = next(L for L in reopened["layers"] if L["id"] == "hair")
    assert (hair["visible"], hair["opacity"], hair["edge_feather"]) == (False, 0.5, 1.0)
    dup = next(L for L in reopened["layers"] if L["id"] == "nose__copy")
    assert dup["duplicate_of"] == "nose" and not dup["visible"]
    assert next(L for L in reopened["layers"] if L["id"] == "neck")["group"] == "Accessories"
    proj.submit({"type": "group.ungroup", "id": "Accessories"})
    assert next(L for L in proj.state()["layers"] if L["id"] == "neck")["group"] == "Torso"


def test_reorder_that_covers_originals_is_detected(pkg):
    """Moving the forearm in front of the hand lets its generated (hidden-part) extension cover the hand's
    original pixels; fidelity and gate 9 must catch it, and undo must restore the exact neutral pose."""
    proj, art = EditorProject(pkg), ArtworkModel(pkg)
    i = [L["id"] for L in proj.state()["layers"]].index("forearm_r")
    proj.submit({"type": "layer.reorder", "id": "forearm_r", "to_index": i + 1})
    f = _fid(proj, art)
    assert not f["neutral_exact"] and f["max_abs_error_in_covered"] > 0 and f["gap_px"] == 0
    g = {x["id"]: x for x in quality_gates(proj, art)["gates"]}
    assert g[9]["level"] == "FAIL"
    heat = art.diff_heatmap([L["id"] for L in proj.state()["layers"] if L["kind"] == "bitmap" and L["visible"]],
                            _masks(proj, art), rejected_ids(proj.state()))
    lit = heat[..., 3] > 0
    assert lit.any() and not (lit & ~art.footprint).any(), "heatmap marks only character pixels"
    proj.undo()
    assert _fid(proj, art)["neutral_exact"]


# -- masks -----------------------------------------------------------------------------------------------
def test_mask_transfer_keeps_the_partition_and_neutral_fidelity(pkg):
    proj, art = EditorProject(pkg), ArtworkModel(pkg)
    base = _masks(proj, art)
    proj.submit({"type": "mask.edit", "layer": "upper_arm_r", "mode": "subtract", "transfer_to": "torso",
                 "shape": {"type": "polygon", "points": BLAZER}})
    m = _masks(proj, art)
    moved = base["upper_arm_r"] & ~m["upper_arm_r"]
    assert moved.sum() > 1000
    assert (m["torso"] & moved).sum() == moved.sum(), "removed pixels go to the transfer target"
    v = art.validate_masks(m)
    assert v["unassigned_px"] == 0 and v["overlap_px"] == 0 and v["reassigned_px"] == moved.sum()
    assert _fid(proj, art)["neutral_exact"]
    # a brush stroke takes pixels back from whichever layer owns them (no overlap)
    proj.submit({"type": "mask.edit", "layer": "upper_arm_r", "mode": "add", "transfer_to": None,
                 "shape": {"type": "stroke", "points": [[445, 470], [455, 480]], "radius": 8, "hardness": 1.0}})
    m2 = _masks(proj, art)
    assert m2["upper_arm_r"].sum() > m["upper_arm_r"].sum()
    assert art.validate_masks(m2)["overlap_px"] == 0
    # refine and reset
    proj.submit({"type": "mask.refine", "layer": "upper_arm_r", "radius": 1})
    assert art.validate_masks(_masks(proj, art))["unassigned_px"] == 0
    proj.submit({"type": "mask.reset", "layer": "upper_arm_r"})
    m3 = _masks(proj, art)
    assert np.array_equal(m3["upper_arm_r"], base["upper_arm_r"])
    assert _fid(proj, art)["neutral_exact"]


def test_subtract_without_transfer_is_reported_as_a_gap(pkg):
    proj, art = EditorProject(pkg), ArtworkModel(pkg)
    proj.submit({"type": "mask.edit", "layer": "upper_arm_r", "mode": "subtract", "transfer_to": None,
                 "shape": {"type": "polygon", "points": BLAZER}})
    assert art.validate_masks(_masks(proj, art))["unassigned_px"] > 1000
    f = _fid(proj, art)
    assert f["gap_px"] > 1000 and not f["neutral_exact"]
    g = {x["id"]: x for x in quality_gates(proj, art)["gates"]}
    assert g[8]["level"] == "NEEDS_REVIEW"
    prov = art.provenance_overlay(_masks(proj, art))
    assert (prov[..., 0] == 255).any(), "gaps are drawn in the provenance overlay"


def test_invalid_mask_ops_are_rejected(pkg):
    proj = EditorProject(pkg)
    for op in ({"type": "mask.edit", "layer": "torso", "mode": "blur", "shape": {"type": "stroke", "points": [[1, 1]]}},
               {"type": "mask.edit", "layer": "torso", "mode": "add", "shape": {"type": "polygon", "points": [[1, 1], [2, 2]]}},
               {"type": "mask.edit", "layer": "torso", "mode": "subtract", "transfer_to": "torso",
                "shape": {"type": "polygon", "points": BLAZER}},
               {"type": "mask.edit", "layer": "nope", "mode": "add", "shape": {"type": "stroke", "points": [[1, 1]]}}):
        with pytest.raises(OpError):
            proj.submit(op)
    assert proj.cursor == 0


# -- joints ----------------------------------------------------------------------------------------------
def test_joint_edits_and_warnings(pkg):
    proj, art = EditorProject(pkg), ArtworkModel(pkg)
    base = proj.base["bones"]
    assert not [w for w in bone_warnings(proj.state(), base, None, art.footprint) if w["level"] == "error"]
    h = base["upper_arm_r"]["head"]
    proj.submit({"type": "bone.set", "name": "upper_arm_r", "fields": {"head": [h[0] + 6, h[1] - 3]}})
    assert proj.state()["bones"]["upper_arm_r"]["edited"]
    # zero-length bone
    t = base["forearm_l"]["head"]
    proj.submit({"type": "bone.set", "name": "forearm_l", "fields": {"tail": t}})
    issues = {(w["bone"], w["issue"]) for w in bone_warnings(proj.state(), base, None, art.footprint)}
    assert ("forearm_l", "zero-length bone") in issues
    with pytest.raises(PoseError):
        PoseService(pkg, art).skeleton(proj.state(), _masks(proj, art))
    proj.undo()
    # joint far outside the artwork, extreme limits, implausible length
    proj.submit({"type": "bone.set", "name": "hand_l", "fields": {"head": [1080, 40]}})
    proj.submit({"type": "bone.set", "name": "shin_r", "fields": {"limits": {"flexion": [-300, 10]}}})
    proj.submit({"type": "bone.set", "name": "thigh_r", "fields": {"length": 900}})
    txt = " | ".join(f"{w['bone']}: {w['issue']}" for w in bone_warnings(proj.state(), base, None, art.footprint))
    assert "hand_l: joint" in txt and "outside the artwork" in txt
    assert "shin_r: extreme flexion limit" in txt
    assert "thigh_r: length" in txt and "left/right thigh lengths differ" in txt
    # hierarchy: cycle (accepted as an edit, reported, and the pose preview refuses it)
    proj.submit({"type": "bone.set", "name": "pelvis", "fields": {"parent": "shin_r"}})
    errs = [w for w in bone_warnings(proj.state(), base, None, None) if w["level"] == "error"]
    assert any("cycle" in w["issue"] for w in errs) and any("exactly one root" in w["issue"] for w in errs)
    with pytest.raises(PoseError):
        PoseService(pkg, art).skeleton(proj.state(), _masks(proj, art))
    # missing parent and layers without a binding (state loaded from elsewhere)
    st = json.loads(json.dumps(proj.base))
    st["bones"]["hand_r"]["parent"] = "ghost"
    st["layers"][0]["bone"] = None
    txt = " | ".join(w["issue"] for w in bone_warnings(st, base, None, None))
    assert "missing parent bone 'ghost'" in txt and "no valid bone binding" in txt
    for bad in ({"parent": "ghost"}, {"parent": "pelvis"}, {"limits": {"flexion": [10, -10]}}, {"head": [9000, 0]}):
        with pytest.raises(OpError):
            proj.submit({"type": "bone.set", "name": "pelvis", "fields": bad})
    proj.submit({"type": "bone.reset", "name": "upper_arm_r"})
    assert proj.state()["bones"]["upper_arm_r"]["head"] == base["upper_arm_r"]["head"]


# -- vectors ---------------------------------------------------------------------------------------------
def test_vectors_round_trip_and_edit(pkg):
    proj = EditorProject(pkg)
    v = next(iter(proj.state()["vectors"].values()))
    d = path_d(v)
    nodes = parse_path_d(d)
    assert len(nodes) == len(v["nodes"])
    for a, b in zip(nodes, v["nodes"]):
        assert abs(a["x"] - b["x"]) < 0.01 and abs(a["y"] - b["y"]) < 0.01
    assert d in to_svg(v, 1086, 1448)
    nv = {"id": "cap_elbow_r", "nodes": [{"x": 300, "y": 360}, {"x": 330, "y": 360, "in": [320, 350]}, {"x": 315, "y": 390}],
          "closed": True, "fill": "#222", "stroke": "#000", "stroke_width": 2, "target": "forearm_r", "purpose": "joint cap"}
    proj.submit({"type": "vector.create", "vector": nv})
    proj.submit({"type": "vector.update", "id": "cap_elbow_r", "fields": {"nodes": nv["nodes"][:2] + [{"x": 316, "y": 392}]}})
    assert proj.state()["vectors"]["cap_elbow_r"]["nodes"][2] == {"x": 316, "y": 392}
    assert proj.state()["vectors"]["cap_elbow_r"]["origin"] == "user"
    gen = next(k for k, x in proj.state()["vectors"].items() if x.get("origin") == "phase2_generated")
    proj.submit({"type": "vector.update", "id": gen, "fields": {"fill": "#ff0000", "origin": "user"}})
    assert proj.state()["vectors"][gen]["origin"] == "phase2_generated", "origin cannot be rewritten"
    assert proj.state()["vectors"][gen]["edited_by_user"]
    with pytest.raises(OpError):
        proj.submit({"type": "vector.update", "id": "cap_elbow_r", "fields": {"nodes": [{"x": 1, "y": 1}]}})
    proj.submit({"type": "vector.delete", "id": "cap_elbow_r"})
    assert "cap_elbow_r" not in proj.state()["vectors"]


# -- pose preview (the Phase 2 solver, not a second one) --------------------------------------------------
def test_pose_preview_reuses_phase2_solver(pkg):
    proj, art = EditorProject(pkg), ArtworkModel(pkg)
    ps = PoseService(pkg, art)
    st, masks = proj.state(), _masks(proj, art)
    rest = ps.render(st, masks, set(), "rest", {}, pad=40)
    img = rest["image"][40:40 + art.H, 40:40 + art.W]
    order = [L["id"] for L in st["layers"] if L["kind"] == "bitmap" and L["visible"] and L["id"] in masks]
    comp = art.composite(order, masks)
    fp = art.footprint
    assert np.array_equal(img[fp][:, :3], comp[fp][:, :3]), "identity pose is bit-exact with the neutral composite"
    r = ps.render(st, masks, set(), "relaxed_standing", {"head": 12}, pad=40)
    assert r["status"] in ("OK_PENDING_REVIEW", "NEEDS_REVIEW", "NEEDS_ARTWORK")
    assert r["local_rotation_deg"]["head"] != 0
    assert "upper_arm_r" in r["joints_posed"]
    with pytest.raises(PoseError):
        ps.render(st, masks, set(), "moonwalk", {})
    with pytest.raises(PoseError):
        ps.render(st, masks, set(), "rest", {"tail": 4})
    # user limits reach the solver
    proj.submit({"type": "bone.set", "name": "upper_arm_r", "fields": {"limits": {"abduction": [-10, 10]}}})
    _, _, solver = ps.engine(proj.state(), _masks(proj, art), set(), "both_hands_raised")
    assert any(v.get("abduction") == (-10.0, 10.0) for v in solver.limit_overrides.values())


def test_joint_correction_and_defect_metadata(pkg):
    proj = EditorProject(pkg)
    proj.submit({"type": "correction.set", "joint": "upper_arm_r", "preset": "both_hands_raised", "fields": {"band": [30, 40]}})
    proj.submit({"type": "correction.set", "joint": "forearm_l", "fields": {"band": [12, 18], "note": "elbow crease"}})
    proj.submit({"type": "correction.gussets", "enabled": False})
    proj.submit({"type": "defect.set", "id": "both_hands_raised:upper_arm_r", "joint": "upper_arm_r",
                 "preset": "both_hands_raised", "status": "accepted", "note": "fix in Moho with Smart Bone"})
    with pytest.raises(OpError):
        proj.submit({"type": "defect.set", "id": "x", "status": "ignored"})
    c = EditorProject(pkg).state()["corrections"]
    assert c["joints"]["upper_arm_r"]["per_pose"]["both_hands_raised"]["band"] == [30, 40]
    assert c["joints"]["forearm_l"]["band"] == [12, 18] and c["gussets"] is False


# -- history, versions, migration ------------------------------------------------------------------------
def test_undo_redo_versions_and_reopen(pkg):
    proj = EditorProject(pkg)
    r0 = proj.revision()
    proj.submit({"type": "layer.set", "id": "hair", "fields": {"opacity": 0.8}})
    proj.submit({"type": "layer.set", "id": "hair", "fields": {"opacity": 0.6}})
    v1 = proj.save_version("two edits")
    proj.undo()
    assert next(L for L in proj.state()["layers"] if L["id"] == "hair")["opacity"] == 0.8
    proj.redo()
    assert next(L for L in proj.state()["layers"] if L["id"] == "hair")["opacity"] == 0.6
    proj.undo()
    proj.submit({"type": "layer.set", "id": "nose", "fields": {"visible": False}})  # truncates the redo tail
    assert len(proj.ops) == 2 and proj.cursor == 2
    reopened = EditorProject(pkg)
    assert reopened.revision() == proj.revision() and len(reopened.data["versions"]) == 1
    reopened.restore_version(v1["n"])
    assert reopened.revision() == v1["revision"]
    assert next(L for L in reopened.state()["layers"] if L["id"] == "hair")["opacity"] == 0.6
    assert (pkg / "editor" / "versions" / "v1.json").is_file()
    while reopened.cursor:
        reopened.undo()
    assert reopened.revision() == r0


def test_migration_refuses_newer_schema(pkg):
    assert migrate({"schema_version": VERSION, "ops": []})["schema_version"] == VERSION
    with pytest.raises(ValueError):
        migrate({"schema_version": VERSION + 1})
    EditorProject(pkg)
    p = pkg / "editor" / "project.json"
    d = json.loads(p.read_text())
    d["schema_version"] = VERSION + 1
    p.write_text(json.dumps(d))
    with pytest.raises(ValueError):
        EditorProject(pkg)


# -- readiness -------------------------------------------------------------------------------------------
def test_face_and_live_readiness_never_overclaim(shared):
    pkg, art = shared
    proj = EditorProject(pkg)
    st, masks = proj.state(), _masks(proj, art)
    face = face_panel(pkg, st, masks)
    caps = {c["capability"]: c["readiness"] for c in face["capabilities"]}
    assert caps["left_eye_blink"] == "REQUIRES_NEW_ARTWORK", "no closed-eye artwork exists in a neutral image"
    feats = {f["id"]: f for f in face["features"]}
    assert feats["eye_r_white"]["present"] and feats["mouth"]["present"]
    assert not feats["eyelids"]["present"], "eyelids are reported missing, not invented"
    live = live_panel(pkg, st, masks)
    assert live["controls"] and all(not c["live_ready"] for c in live["controls"])
    assert all(c["moho_mapping"] == "proposed_unverified" for c in live["controls"])
    assert all(not g["live_ready"] for g in live["gamepad_gestures"])
    assert "not implemented" in live["webcam_tracking"]


# -- export and quality gates ----------------------------------------------------------------------------
def test_export_package_is_valid_and_not_production_ready(pkg, tmp_path):
    proj, art = EditorProject(pkg), ArtworkModel(pkg)
    src = pkg / json.loads((pkg / "metadata.json").read_text())["source"]["path"]
    src_sha = _sha(src)
    originals = {p: _sha(p) for p in (pkg / "artwork").rglob("*.png")}
    proj.submit({"type": "mask.edit", "layer": "upper_arm_r", "mode": "subtract", "transfer_to": "torso",
                 "shape": {"type": "polygon", "points": BLAZER}})
    ext = next(e for L in proj.state()["layers"] for e in L.get("extensions", []))
    proj.submit({"type": "approval.set", "item": ext, "status": "rejected"})
    res = export_package(proj, art, tmp_path / "export")
    out = Path(res["out"])
    assert res["validation_errors"] == [] and validate_package(out) == []
    m = json.loads((out / "manifest.json").read_text())
    assert m["schema"] == "autorig.moho_asset_package" and m["production_ready"] is False
    assert _sha(out / m["source"]["file"]) == src_sha
    for L in m["layers"]:
        assert Image.open(out / L["file"]).size == (art.W, art.H)
    rej_layer = next(L for L in m["layers"] if ext in L["approval"])
    assert ext not in rej_layer["generated_extensions"], "rejected generated pixels are not exported"
    assert m["provenance"]["reassigned_px"] > 1000 and m["provenance"]["unassigned_px"] == 0
    b = {x["name"]: x for x in m["bones"]}
    assert sum(1 for x in m["bones"] if not x["parent"]) == 1
    hx, hy = proj.state()["bones"]["upper_arm_r"]["head"]
    assert b["upper_arm_r"]["head_px"] == proj.state()["bones"]["upper_arm_r"]["head"]
    assert to_moho(art.W / 2, art.H / 2, art.W, art.H) == [0.0, 0.0] and to_moho(0, 0, art.W, art.H)[1] == 1.0
    assert all((out / f).is_file() for f in m["files"])
    assert (out / "reports" / "quality_gates.json").is_file()
    assert list((out / "vectors").glob("*.svg"))
    assert "Smart Bone" in m["deformation"]["moho_translation"] and "proposed_unverified" in m["deformation"]["moho_translation"]
    assert not list(out.rglob("*.moho")), "no native Moho file is generated in Phase 3"
    assert _sha(src) == src_sha and all(_sha(p) == h for p, h in originals.items()), "originals untouched"
    assert art.store.verify() == []


def test_quality_gates_block_production_until_everything_is_approved(pkg):
    proj, art = EditorProject(pkg), ArtworkModel(pkg)
    q = quality_gates(proj, art)
    g = {x["id"]: x["level"] for x in q["gates"]}
    assert all(g[i] == "PASS" for i in (1, 4, 5, 8, 9))
    assert g[12] == "NEEDS_REVIEW" and q["production_ready"] is False
    st = proj.state()
    used = {e for L in st["layers"] if L["visible"] and L["kind"] == "bitmap" for e in L.get("extensions", [])}
    items = sorted(used | {k for k in st["approvals"] if k.startswith(("corrective:", "vector:"))} | {"sign_off:neutral_fidelity"})
    for k in items:
        proj.submit({"type": "approval.set", "item": k, "status": "approved"})
    q2 = quality_gates(proj, art)
    g2 = {x["id"]: x["level"] for x in q2["gates"]}
    assert g2[12] == "PASS"
    assert g2[11] == "NEEDS_REVIEW", "facial capabilities still need new artwork"
    assert q2["production_ready"] is False, "missing artwork blocks production readiness even with approvals"


# -- HTTP API --------------------------------------------------------------------------------------------
def test_http_api_round_trip(pkg, tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import warnings

    from fastapi.testclient import TestClient

    from autorig.editor.server import create_app
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        c = TestClient(create_app(pkg))
    p = c.get("/api/project").json()
    assert p["project"]["cursor"] == 0 and p["mask_validation"]["unassigned_px"] == 0
    r = c.post("/api/op", json={"type": "mask.edit", "layer": "upper_arm_r", "mode": "subtract", "transfer_to": "torso",
                                "shape": {"type": "polygon", "points": BLAZER}})
    assert r.status_code == 200 and r.json()["mask_validation"]["reassigned_px"] > 1000
    assert c.post("/api/op", json={"type": "layer.set", "id": "hair", "fields": {"opacity": 3}}).status_code == 400
    assert c.post("/api/undo").json()["project"]["cursor"] == 0
    assert c.post("/api/redo").json()["project"]["cursor"] == 1
    for url in ("/api/source.png", "/api/layer/torso.png", "/api/mask/torso.png?base=true",
                "/api/compare/diff.png", "/api/compare/provenance.png", "/api/compare/neutral.png"):
        resp = c.get(url)
        assert resp.status_code == 200 and resp.headers["content-type"] == "image/png", url
        assert Image.open(io.BytesIO(resp.content)).size == (1086, 1448)
    assert c.get("/api/layer/nope.png").status_code == 404
    assert c.get("/api/compare/metrics").json()["fidelity"]["neutral_exact"] is True
    assert any(x["name"] == "both_hands_raised" for x in c.get("/api/presets").json())
    pose = c.post("/api/pose", json={"preset": "rest", "overrides": {"head": 10}}).json()
    assert pose["status"] and Image.open(io.BytesIO(base64.b64decode(pose["image"].split(",")[-1]))).size[0] > 1086
    assert c.post("/api/pose", json={"preset": "nope"}).status_code == 400
    assert c.get("/api/face").json()["capabilities"]
    assert all(not x["live_ready"] for x in c.get("/api/live").json()["controls"])
    assert c.get("/api/gates").json()["production_ready"] is False
    v = c.post("/api/versions", json={"label": "api"}).json()
    assert v["n"] == 1 and len(c.get("/api/versions").json()) == 1
    exp = c.post("/api/export", json={"name": "api_export"}).json()
    assert exp["validation_errors"] == [] and exp["layers"] >= 25
    assert Path(exp["out"]) == pkg / "editor" / "exports" / "api_export"
    assert c.post("/api/export", json={"name": "../../etc"}).status_code == 400
    assert c.post("/api/export", json={"name": "api_export"}).status_code == 200, "an earlier export may be replaced"


def test_export_never_deletes_foreign_directories(pkg, tmp_path):
    keep = tmp_path / "my_files"
    keep.mkdir()
    (keep / "notes.txt").write_text("precious")
    with pytest.raises(ValueError):
        export_package(EditorProject(pkg), ArtworkModel(pkg), keep)
    assert (keep / "notes.txt").read_text() == "precious"
