"""Hand Pose Store: pack import and validation, the on-disk store, cuff masking, wrist placement, engine and editor
integration, persistence, approvals and the export manifest.

Synthetic packs test validation and masking. The integration tests import the user's pack
(``references/schoolboy/hand_pack``) into a copy of the real T-pose package. They measure the preview renderer;
they are not proof of production-quality artwork, and Moho is not run.
"""
from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from autorig.handstore import (HandStore, PackError, Placement, approval_key, check_mask, check_placement, classify,
                               cuff_mask, hand_only, matte_halo, place, read_zip, validate_pack)

ROOT = Path(__file__).resolve().parents[1]
REAL_PKG = ROOT / "projects" / "schoolboy_tpose" / "character_package"
PACK = ROOT / "references" / "schoolboy" / "hand_pack" / "AutoRig_Hand_Pose_Library.zip"
real = pytest.mark.skipif(not ((REAL_PKG / "natural_pose" / "rig.json").is_file() and PACK.is_file()),
                          reason="real package or hand pack not present")
NAVY, WHITE, SKIN, BLACK = (44, 43, 55), (250, 245, 238), (238, 140, 70), (5, 5, 5)


# -- synthetic packs ---------------------------------------------------------------------------------------
def sprite(w=200, h=160, thumb=False, halo=False) -> np.ndarray:
    a = np.zeros((h, w, 4), np.uint8)
    a[10:52, 70:130] = (*NAVY, 255)  # sleeve
    a[52:62, 70:130] = (*WHITE, 255)  # cuff
    a[62:65, 70:130] = (*BLACK, 255)  # cuff bottom outline
    yy, xx = np.mgrid[0:h, 0:w]
    hand = ((xx - 100) / 34.0) ** 2 + ((yy - 100) / 38.0) ** 2 <= 1.0
    hand &= yy >= 65
    a[hand] = (*SKIN, 255)
    if thumb:  # a thumb raised in front of the cuff's right half
        a[40:66, 112:124] = (*SKIN, 255)
    if halo:
        from scipy import ndimage
        ring = ndimage.binary_dilation(a[..., 3] > 0, iterations=2) & (a[..., 3] == 0)
        a[ring] = (150, 150, 150, 230)
    return a


def png_bytes(arr: np.ndarray, mode="RGBA") -> bytes:
    b = io.BytesIO()
    Image.fromarray(arr if mode == "RGBA" else arr[..., :3], mode).save(b, "PNG")
    return b.getvalue()


def make_pack(poses=("relaxed", "fist"), sides=("left", "right"), mutate=None, extra_files=None) -> bytes:
    assets, files = [], {}
    for s in sides:
        for p in poses:
            aid = f"{s}_{p}"
            files[f"sprites/{aid}.png"] = png_bytes(sprite(thumb=(p == "thumbs_up")))
            assets.append({"id": aid, "side": s, "pose": p, "file": f"sprites/{aid}.png", "canvas_px": [200, 160],
                           "wrist_anchor_px": [100, 60], "approval_status": "NEEDS_REVIEW",
                           "base_orientation": "sleeve_top_hand_down"})
    m = {"schema_version": "1.0.0", "library_id": "synthetic_pack", "asset_count": len(assets), "pose_names": list(poses),
         "hand_sides": list(sides), "assets": assets, "all_variants_require_review": True}
    if mutate:
        mutate(m, files)
    files["manifest.json"] = json.dumps(m).encode()
    files.update(extra_files or {})
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    return b.getvalue()


def test_valid_pack_imports_with_hashes_and_stays_pending(tmp_path):
    hs = HandStore(tmp_path)
    r = hs.import_pack(make_pack(), "p.zip")
    assert r["status"] == "imported" and r["assets"] == 4
    assert HandStore(tmp_path).import_pack(make_pack(), "again.zip")["status"] == "already_imported"
    idx = json.loads((tmp_path / "hand_store" / "index.json").read_text())
    assert len(idx["assets"]) == 4 and idx["packs"][0]["zip_sha256"]
    for uid, a in idx["assets"].items():
        assert a["side"] in ("l", "r") and a["versions"][0]["sha256"]
        assert a["provenance"]["manifest_status"] == "NEEDS_REVIEW"
    assert HandStore(tmp_path).verify() == []
    assert approval_key("synthetic_pack/left_fist", 1) == "handstore:synthetic_pack/left_fist@v1"
    # tampering with a stored file is detected
    f = tmp_path / "hand_store" / idx["assets"]["synthetic_pack/left_fist"]["versions"][0]["file"]
    f.write_bytes(png_bytes(sprite()[::-1].copy()))
    assert HandStore(tmp_path).verify()


@pytest.mark.parametrize("case, phrase", [
    ("no_manifest", "manifest.json is missing"),
    ("count", "asset_count"),
    ("dup", "duplicate id"),
    ("missing_file", "is not in the archive"),
    ("size", "canvas_px declares"),
    ("rgb", "no alpha channel"),
    ("opaque", "mostly opaque"),
    ("pose", "have no switch pose"),
    ("incomplete", "incomplete pack"),
    ("anchor", "outside the"),
])
def test_broken_packs_are_rejected_with_actionable_errors(case, phrase):
    def mut(m, files):
        a0 = m["assets"][0]
        if case == "count":
            m["asset_count"] = 9
        elif case == "dup":
            m["assets"][1]["id"] = a0["id"]
        elif case == "missing_file":
            files.pop(a0["file"])
        elif case == "size":
            a0["canvas_px"] = [210, 160]
        elif case == "rgb":
            files[a0["file"]] = png_bytes(sprite(), "RGB")
        elif case == "opaque":
            s = sprite()
            s[..., 3] = 255
            files[a0["file"]] = png_bytes(s)
        elif case == "pose":
            m["pose_names"].append("jazz_hands")
        elif case == "incomplete":
            m["assets"].pop()
            files.pop("sprites/right_fist.png")
            m["asset_count"] -= 1
        elif case == "anchor":
            a0["wrist_anchor_px"] = [500, 10]
    data = make_pack(mutate=mut)
    if case == "no_manifest":
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            z.writestr("sprites/a.png", png_bytes(sprite()))
        data = b.getvalue()
    with pytest.raises(PackError) as e:
        files = read_zip(data)
        _, _, errs, _ = validate_pack(files)
        if errs:
            raise PackError(errs)
    assert any(phrase in x for x in e.value.errors), e.value.errors


def test_unsafe_and_non_zip_archives_are_rejected():
    with pytest.raises(PackError, match="not a zip"):
        read_zip(b"nope")
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("../evil.png", b"x")
    with pytest.raises(PackError, match="unsafe path"):
        read_zip(b.getvalue())


def test_replacing_a_png_adds_a_version_and_keeps_the_old(tmp_path):
    hs = HandStore(tmp_path)
    hs.import_pack(make_pack(), "p.zip")
    uid = "synthetic_pack/right_fist"
    r = hs.replace_png(uid, png_bytes(sprite(thumb=True)), "new.png")
    assert r["status"] == "replaced" and r["v"] == 2
    vs = HandStore(tmp_path).index["assets"][uid]["versions"]
    assert [v["v"] for v in vs] == [1, 2] and all((tmp_path / "hand_store" / v["file"]).is_file() for v in vs)
    with pytest.raises(PackError):
        hs.replace_png(uid, png_bytes(sprite(), "RGB"), "flat.png")


def test_cuff_mask_removes_sleeve_and_cuff_but_keeps_the_hand_and_a_raised_thumb():
    s = sprite(thumb=True)
    mask, info = cuff_mask(s)
    c = classify(s)
    assert info["found"] and info["skin_masked_px"] == 0
    h = hand_only(s, mask)
    hc = classify(h)
    assert int((hc["navy"] | hc["white"]).sum()) == 0, "no sleeve or cuff left"
    assert int((h[..., 3] > 0).sum()) >= int(c["skin"].sum()) - 5
    assert abs(info["anchor"][1] - 64) <= 2 and abs(info["anchor"][0] - 100) <= 2  # cuff bottom edge centre
    assert (s[..., 3] == sprite(thumb=True)[..., 3]).all(), "the source sprite is never modified"


def test_matte_halo_is_detected_and_removed_non_destructively():
    s = sprite(halo=True)
    halo = matte_halo(s)
    assert halo.sum() > 300
    h = hand_only(s, np.zeros(s.shape[:2], bool), defringe=True)
    assert int((h[..., 3] > 0).sum()) == int((s[..., 3] > 0).sum()) - int(halo.sum())


def test_placement_maps_the_anchor_to_the_wrist_point():
    s = sprite()
    pl = Placement((100.0, 64.0), (300.0, 200.0), 90.0, 0.5)  # sprite 'down' -> world image-left
    M = pl.matrix()
    assert M[:2, :2] @ np.array([100.0, 64.0]) + M[:2, 2] == pytest.approx([300.0, 200.0])
    assert M[:2, :2] @ np.array([100.0, 104.0]) + M[:2, 2] == pytest.approx([280.0, 200.0])
    out = place(hand_only(s, cuff_mask(s)[0]), pl, (400, 600))
    ys, xs = np.nonzero(out[..., 3] > 127)
    assert xs.max() <= 302 and xs.mean() < 300, "the hand lies along the bone direction from the wrist"


def test_operation_field_validation():
    assert not check_placement({"dx": 3, "scale": 1.1}) and check_placement({"scale": 3.0}) and check_placement({"skew": 1})
    assert not check_mask({"enabled": False, "cut_offset": 4}) and check_mask({"cut_offset": 100}) and check_mask({"x": 1})


# -- real package ------------------------------------------------------------------------------------------
def _ignore(d, names):
    skip = {"editor", "motion", "ik_switch", "hand_store"}
    if Path(d).name == "natural_pose":
        skip |= {"poses", "legacy", "closeups", "contact_sheet.png", "contact_sheet_diagnostic.png"}
    return [n for n in names if n in skip]


@pytest.fixture(scope="module")
def pkg(tmp_path_factory):
    dst = tmp_path_factory.mktemp("hs") / "pkg"
    shutil.copytree(REAL_PKG, dst, ignore=_ignore)
    HandStore(dst).import_pack(PACK.read_bytes(), PACK.name)
    return dst


@pytest.fixture(scope="module")
def eng(pkg):
    from autorig.motion.pipeline import engine_for_package
    return engine_for_package(pkg)


@real
def test_users_pack_validates_completely():
    _, assets, errs, _ = validate_pack(read_zip(PACK.read_bytes()))
    assert errs == [] and len(assets) == 16
    assert {(a["side_code"], a["switch_pose"]) for a in assets} == {
        (s, p) for s in "lr" for p in ("relaxed", "open_palm", "fist", "pointing", "thumbs_up", "gripping", "palm_down", "cupped")}


@real
def test_original_hand_stays_default_and_unreviewed_drawings_are_never_active(eng):
    e, vis = eng
    lib = e.hand_lib
    for s in "rl":
        assert lib.hands[s]["open_palm"].cid == "original"
        assert any(v.cid.startswith("store:") for v in lib.candidates[s]["open_palm"]), "imported open palm is a candidate"
        assert lib.hands[s]["relaxed"].cid.startswith("store:") and lib.hands[s]["relaxed"].status == "pending_review"
    r = e.pose("relaxed_standing", skeleton_only=True)
    assert all(h["cid"] == "original" and not h["pending_review"] for h in r["hands"].values()), "pending relaxed not shown"
    r = e.pose("relaxed_standing", skeleton_only=True, allow_pending=True)
    assert all(h["cid"].startswith("store:") and h["pending_review"] for h in r["hands"].values())
    assert r["feasibility"]["status"] == "NEEDS_ARTWORK"


@real
def test_side_mapping_and_consistent_size(eng):
    e, vis = eng
    lib = e.hand_lib
    scales = {"r": set(), "l": set()}
    for s in "rl":
        for vs in lib.candidates[s].values():
            for v in vs:
                if v.cid.startswith("store:"):
                    assert v.cid.split("/")[-1].startswith("right" if s == "r" else "left")
                    al = lib.alignment_v(v, e.profile.midline_x)
                    assert al["side_ok"] and al["method"] == "cuff_edge_match"
                    assert al["attachment_error_px"] == 0.0, "anchored exactly on the cuff edge (no correction yet)"
                    if v.pose in ("relaxed", "open_palm", "fist", "palm_down"):  # thumb/fingers beside the cuff in the others
                        assert al["status"] == "OK", al
                    scales[s].add(round(v.placement["final"]["scale"], 6))
    assert len(scales["r"]) == 1 and len(scales["l"]) == 1, "one scale per side: no size pop between drawings"


@real
def test_every_stored_hand_stays_attached_under_ik_without_duplicate_cuffs(eng):
    from autorig.handstore import posed_seam
    from autorig.motion.switches import posed_hand_alignment
    e, vis = eng
    sh = np.asarray(e.plan_and_solve("relaxed_standing")[2]["joints_posed"]["upper_arm_r"])
    L = e.profile.arm("r")
    ik = {"arm_r": {"enabled": True, "mode": "ik", "target": [float(sh[0] - 0.6 * L), float(sh[1] - 0.4 * L)]}}
    base = e.pose("relaxed_standing", ik=ik, visible=vis, skeleton_only=True)["solve"]["joints_posed"]
    for v in [v for vs in e.hand_lib.candidates["r"].values() for v in vs if v.cid.startswith("store:")]:
        r = e.pose("relaxed_standing", ik=ik, visible=vis, allow_pending=True, hand_layers={"r": v.layer})
        assert r["solve"]["joints_posed"] == base, "switching hands never moves the skeleton or the IK result"
        pa = posed_hand_alignment(e, r, "r", v.layer, v.measure["attachment"])
        sm = posed_seam(r, e, "r", v.layer)
        assert pa["sleeve_gap_px"] <= 2.0, (v.cid, pa)
        assert sm["own_white_px_near_wrist"] <= 60, (v.cid, sm)
        # Phase 3.3: the sleeve cuff is rigid on the forearm (hand switches never displace it), so the cuff end no
        # longer tilts with the wrist; the measured fill dropped from 0.63-0.77 to 0.565-0.70 with no visible change
        # (natural_pose/garments/evidence: hand_cuff_before_after.png)
        assert sm["fill"] >= 0.55, (v.cid, sm)


@real
def test_cuff_masking_is_what_prevents_a_second_cuff(eng, pkg):
    """With the tucked placement the drawing's own cuff hides under the character's sleeve, so the mask is tested
    where it matters: the drawing pulled 20 px out of the sleeve (as a user correction could do)."""
    from autorig.handstore import engine_input, posed_seam
    from autorig.motion.pipeline import MotionEngine
    e, vis = eng
    uid = "schoolboy_hand_switches_v1/right_relaxed"
    base = {k: v for k, v in e.variants.items() if "__hs." not in k and "__hscuff" not in k}
    out = {}
    for enabled in (True, False):
        hs = engine_input(pkg, {"mask": {uid: {"enabled": enabled}}, "placement": {uid: {"dx": -20.0}}}, e.sk, e.layers,
                          base, e.approvals, e.source)
        lid = next(c["layer"] for c in hs["candidates"] if c["uid"] == uid)
        e2 = MotionEngine(e.sk, e.layers, e.generated, e.source, base, e.approvals, hand_store=hs)
        out[enabled] = posed_seam(e2.pose("relaxed_standing", visible=vis, allow_pending=True, hand_layers={"r": lid}),
                                  e2, "r", lid)
    assert out[False]["own_white_px"] > 300, "unmasked: the drawing's own cuff appears below the character's"
    assert out[True]["own_white_px"] < 60, "masked: no second cuff"


@real
def test_auto_selection_never_overrides_a_manual_choice(eng):
    e, vis = eng
    uid = "schoolboy_hand_switches_v1/right_relaxed"
    hs, appr0 = e.hand_store, e.approvals
    appr = {**appr0, approval_key(uid, 1): {"status": "approved"}}  # as approval.set from the editor would record
    for c in hs["candidates"]:
        if c["uid"] == uid:
            c["status"] = "approved"
    e.set_hand_store(hs, appr)
    try:
        r = e.pose("relaxed_standing", skeleton_only=True)
        assert r["hands"]["r"]["cid"] == f"store:{uid}" and not r["hands"]["r"]["pending_review"], "approved: auto uses it"
        sw = {"hand_r": {"user": "fist", "auto": False, "keys": [], "offsets": {}}}
        r = e.pose("relaxed_standing", skeleton_only=True, switches=sw)
        assert r["hands"]["r"]["source"] == "user" and r["hands"]["r"]["pose"] != "relaxed", \
            "the user's explicit choice wins over the automatic relaxed hand"
    finally:
        for c in hs["candidates"]:
            if c["uid"] == uid:
                c["status"] = "pending_review"
        e.set_hand_store(hs, appr0)


@real
def test_switch_machinery_keeps_the_neutral_render(eng):
    from autorig.phase2.skinning import PoseParams
    e, vis = eng
    a = e.dfm.render(PoseParams.identity(len(e.sk.bones)), list(vis))["image"]
    assert sum(1 for k in e.variants if "__hs." in k) >= 16
    from autorig.motion.pipeline import MotionEngine
    e0 = MotionEngine(e.sk, e.layers, e.generated, e.source, {k: v for k, v in e.variants.items() if "__hs." not in k},
                      e.approvals)
    b = e0.dfm.render(PoseParams.identity(len(e.sk.bones)), list(vis))["image"]
    assert np.array_equal(a, b), "imported hands never change the neutral (reference) composite"


@real
def test_project_persists_hand_store_decisions_with_undo_redo(pkg):
    from autorig.editor.project import VERSION, EditorProject, OpError
    p = EditorProject(pkg)
    assert VERSION == 7  # Phase 3.5: artistic review (schema v7)
    uid = "schoolboy_hand_switches_v1/left_point"
    p.submit({"type": "handstore.use", "side": "l", "pose": "pointing", "candidate": f"store:{uid}"})
    p.submit({"type": "approval.set", "item": approval_key(uid, 1), "status": "approved"})
    p.submit({"type": "handstore.placement", "uid": uid, "fields": {"dx": 2, "rot": -3, "scale": 1.05}})
    p.submit({"type": "handstore.mask", "uid": uid, "fields": {"cut_offset": 3}})
    p.submit({"type": "handstore.rename", "uid": uid, "label": "point L (reviewed)"})
    p.submit({"type": "handstore.delete", "uid": "schoolboy_hand_switches_v1/left_cupped"})
    for bad in ({"type": "handstore.use", "side": "l", "pose": "fist", "candidate": "original"},
                {"type": "handstore.placement", "uid": uid, "fields": {"scale": 9}},
                {"type": "handstore.placement", "uid": "other_pack/x", "fields": {"dx": 1}},
                {"type": "handstore.rename", "uid": uid, "label": ""}):
        with pytest.raises(OpError):
            p.submit(bad)
    st = EditorProject(pkg).state()  # reopened from disk
    H = st["hand_store"]
    assert H["use"]["l/pointing"] == f"store:{uid}" and H["placement"][uid] == {"dx": 2.0, "rot": -3.0, "scale": 1.05}
    assert H["mask"][uid] == {"cut_offset": 3} and H["labels"][uid] == "point L (reviewed)"
    assert "schoolboy_hand_switches_v1/left_cupped" in H["deleted"]
    assert st["approvals"][approval_key(uid, 1)]["status"] == "approved"
    p.undo()
    assert "schoolboy_hand_switches_v1/left_cupped" not in p.state()["hand_store"]["deleted"]
    p.redo()
    assert "schoolboy_hand_switches_v1/left_cupped" in p.state()["hand_store"]["deleted"]
    # a v2 project (Phase 3.2) migrates: the new section gets defaults
    data = json.loads(p.path.read_text())
    data["schema_version"] = 2
    data["ops"] = [o for o in data["ops"] if not o["type"].startswith("handstore.") and not o.get("item", "").startswith("handstore:")]
    data["cursor"] = len(data["ops"])
    p.path.write_text(json.dumps(data))
    q = EditorProject(pkg)
    assert q.data["schema_version"] == 7 and 2 in q.data["migrated_from"] and q.state()["hand_store"]["use"] == {}


@real
def test_export_writes_a_switch_manifest_separating_validated_from_unimplemented(pkg):
    from autorig.editor.artwork import ArtworkModel
    from autorig.editor.export import export_package
    from autorig.editor.project import EditorProject
    proj = EditorProject(pkg)
    r = export_package(proj, ArtworkModel(pkg))
    assert r["validation_errors"] == []
    out = Path(r["out"])
    m = json.loads((out / "hand_switches.json").read_text())
    assert m["moho_mapping"]["status"] == "proposed_unverified" and m["not_implemented_in_native_moho"]
    groups = {g["alias"]: g for g in m["validated"]["groups"]}
    assert set(groups) == {"hand_right", "hand_left"}
    for g in groups.values():
        assert any(v["candidate"] == "original" for v in g["variants"])
        for v in g["variants"]:
            assert (out / v["file"]).is_file() and v["checks"]["full_canvas"] and v["checks"]["has_alpha"]
    assert m["summary"]["production_ready"] is False and r["manifest"]["production_ready"] is False
    assert "HAND_RIGHT_FIST" in m["semantic_actions"]


@real
def test_committed_hand_store_and_evidence():
    idx = REAL_PKG / "hand_store" / "index.json"
    if not idx.is_file():
        pytest.skip("hand store not imported in the committed package")
    assert HandStore(REAL_PKG).verify() == []
    ev = REAL_PKG / "hand_store" / "evidence" / "results.json"
    if not ev.is_file():
        pytest.skip("hand evidence not generated")
    r = json.loads(ev.read_text())
    assert (ev.parent / r["files"]["sheet"]).is_file() and (ev.parent / "ik_switch_sweep.gif").is_file()
    for v in r["variants"]:
        for key in ("hang", "ik"):
            assert v["measures"][key]["gap_px"] <= 2.0, v
            assert v["measures"][key]["skeleton_change_px"] == 0.0
        assert "duplicate cuff" not in v["flags"]
    assert r["ik_sweep_max"]["gap_px"] <= 2.0
