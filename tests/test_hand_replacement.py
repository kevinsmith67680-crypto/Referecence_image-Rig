"""Sleeve-free hand replacement: sheet extraction, wrist alignment, library archive, natural hand selection."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from autorig.handsheet import (build_pack, extract_sheet, handedness_from, mirror_test, trace_contour,
                               wrist_opening)
from autorig.handstore import (PHASE2_LIBRARY, HandStore, Placement, check_placement, library_status, read_zip,
                               validate_pack)
from autorig.motion.handselect import (ACTIONS, MIN_HOLD_ACTION_S, HandContext, HandSelectionState,
                                       NaturalHandSelectionEngine)
from autorig.motion.switches import HandVariant
from autorig.wristalign import WristAlignmentController

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "references" / "schoolboy" / "hand_pack_v2"
SHEET, INSPECTION = V2 / "hand_sheet_sleeveless.jpg", V2 / "inspection.json"
ZIP = V2 / "AutoRig_Hand_Library_Sleeveless_v2.zip"
V1 = ROOT / "references" / "schoolboy" / "hand_pack" / "AutoRig_Hand_Pose_Library.zip"
REAL_PKG = ROOT / "projects" / "schoolboy_tpose" / "character_package"
LIB = "schoolboy_hands_sleeveless"
have_sheet = pytest.mark.skipif(not SHEET.is_file(), reason="sleeve-free hand sheet not present")
real = pytest.mark.skipif(not ((REAL_PKG / "natural_pose" / "rig.json").is_file() and ZIP.is_file() and V1.is_file()),
                          reason="real schoolboy package or hand packs not present")


# -- extraction -------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def sheet():
    return extract_sheet(SHEET, json.loads(INSPECTION.read_text()))


@have_sheet
def test_sheet_has_16_clean_drawings_in_reading_order(sheet):
    assert sheet["errors"] == [] and len(sheet["cells"]) == 16
    for name, c in sheet["cells"].items():
        q, spr = c["quality"], c["sprite"]
        assert q["outline_coverage"] >= 0.95 and q["skin_on_edge_px"] <= 12, (name, q)
        assert q["dark_px_lost"] == 0 and q["dark_px_kept"] >= 0.99 * q["dark_px"], \
            "dark outline pixels are never taken for background (edge pixels keep partial alpha)"
        assert not c["touches_sheet_edge"]
        al = spr[..., 3]
        assert al[0].max() == 0 and al[-1].max() == 0 and al[:, 0].max() == 0 and al[:, -1].max() == 0, "margin is clear"
        ys, xs = np.nonzero(al)
        assert ys.min() <= 6 and xs.min() <= 6 and al.shape[0] - ys.max() <= 7 and al.shape[1] - xs.max() <= 7, "tight crop"
        x0, y0, x1, y1 = c["bbox"]
        assert spr.shape[1] <= x1 - x0 + 16 and spr.shape[0] <= y1 - y0 + 16, "no scaling: sheet pixel size"
        semi = ((al > 0) & (al < 255)).sum()
        assert semi > 50, "antialiased edge kept (partial alpha)"


@have_sheet
def test_handedness_is_measured_not_taken_from_the_row_labels(sheet):
    h = sheet["handedness"]
    assert {g["side"] for g in h["groups"].values()} == {"left", "right"}
    assert h["groups"][0]["side"] == "left" and h["groups"][1]["side"] == "right", "open-palm thumb rule"
    mirrored = {g: p["mirrored"] for g, p in h["pairs"].items()}
    assert sum(mirrored.values()) == 7 and mirrored["pointing"] is False, "the two pointing drawings are the same hand"
    assert h["per_cell"]["r1c4"]["side"] == "right" and not h["per_cell"]["r1c4"]["agrees_with_row_label"]
    assert all(c["agrees_with_row_label"] for n, c in h["per_cell"].items() if n != "r1c4")


@have_sheet
def test_wrist_openings_open_cuts_and_closed_outlines(sheet):
    kinds = {n: c["wrist"]["opening"] for n, c in sheet["cells"].items()}
    closed = {n for n, k in kinds.items() if k == "closed_outline"}
    assert closed == {"r1c4", "r2c2", "r2c4", "r3c4", "r4c2", "r4c4"}, "pointing, grip and cupped have no wrist cut"
    for n, c in sheet["cells"].items():
        w = c["wrist"]
        assert abs(np.hypot(*w["axis"]) - 1) < 1e-6
        if w["opening"] == "closed_outline":
            assert w["manual_review"] and w["confidence"] < 0.5
    top = sheet["cells"]["r1c2"]["wrist"]
    assert top["axis"][1] > 0.95, "open palm: wrist at the top, the hand goes down"
    side = sheet["cells"]["r2c1"]["wrist"]
    assert side["axis"][0] > 0.95, "thumbs-up: wrist cut on the left"


def test_wrist_detector_on_synthetic_shapes():
    m = np.zeros((220, 200), bool)
    m[20:60, 60:140] = True  # the wrist stub: flat top, parallel sides
    yy, xx = np.mgrid[0:220, 0:200]
    m |= ((xx - 100) / 70.0) ** 2 + ((yy - 120) / 80.0) ** 2 <= 1.0  # the hand below it
    w = wrist_opening(m, "top")
    assert w["opening"] == "open_cut" and abs(w["centre"][1] - 20) <= 10 and w["axis"][1] > 0.95
    c = ((xx - 100) / 60.0) ** 2 + ((yy - 110) / 90.0) ** 2 <= 1.0  # rounded everywhere
    w2 = wrist_opening(c, "top", target_width=80)
    assert w2["opening"] == "closed_outline" and w2["manual_review"] and abs(w2["width_px"] - 80) <= 3
    C = trace_contour(m)
    assert len(C) > 100 and m[int(C[0][1]), int(C[0][0])]


def test_handedness_rule():
    # palm toward the viewer, fingers down: right hand -> thumb on the image left
    assert handedness_from((0, 1), (-1, 0), "palm") == "right"
    assert handedness_from((0, 1), (1, 0), "palm") == "left"
    assert handedness_from((0, 1), (1, 0), "back") == "right"
    # palm view, index pointing image-left, thumb on top -> right hand (the sheet's pointing drawings)
    assert handedness_from((-1, 0), (0, -1), "palm") == "right"
    a = np.zeros((60, 40, 4), np.uint8)
    a[10:50, 5:20, 3] = 255
    a[10:20, 5:35, 3] = 255
    a[..., :3] = 200
    r = mirror_test(a, a[:, ::-1].copy())
    assert r["mirrored"] is True


@have_sheet
def test_committed_pack_is_reproducible_and_validates(tmp_path):
    r = build_pack(SHEET, INSPECTION, tmp_path, LIB, ["schoolboy_hand_switches_v1"], ZIP.name)
    assert (tmp_path / ZIP.name).read_bytes() == ZIP.read_bytes(), "regenerate with autorig hand-sheet (pack drifted)"
    m, assets, errs, warns = validate_pack(read_zip(ZIP.read_bytes()))
    assert errs == [] and len(assets) == 16 and m["supersedes"] == ["schoolboy_hand_switches_v1"]
    left_point = next(a for a in assets if a["id"] == "left_pointing")
    assert left_point["derived"] == "mirror_x" and left_point["handedness"]["drawn_as"] == "right"
    assert r["summary"]["derived_assets"] == ["left_pointing"]
    for a in assets:
        assert a["base_orientation"] == "wrist_edge" and a["sleeve"] == "none" and a["approval_status"] == "NEEDS_REVIEW"


def _pack(complete_sides=("left",)) -> bytes:
    import io
    import zipfile
    assets, files = [], {}
    a = np.zeros((160, 120, 4), np.uint8)
    a[20:150, 20:100] = (220, 140, 90, 255)
    buf = io.BytesIO()
    Image.fromarray(a, "RGBA").save(buf, "PNG")
    for s in ("left", "right"):
        aid = f"{s}_relaxed"
        files[f"hands/{s}/relaxed.png"] = buf.getvalue()
        rec = {"id": aid, "side": s, "pose": "relaxed", "file": f"hands/{s}/relaxed.png", "canvas_px": [120, 160],
               "wrist_anchor_px": [60, 20], "base_orientation": "wrist_edge"}
        if s in complete_sides:
            rec.update(wrist_edge_px=[[20, 20], [100, 20]], wrist_axis=[0, 1])
        assets.append(rec)
    m = {"library_id": "syn_free", "asset_count": 2, "pose_names": ["relaxed"], "hand_sides": ["left", "right"],
         "assets": assets}
    files["manifest.json"] = json.dumps(m).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n, d in files.items():
            z.writestr(n, d)
    return out.getvalue()


def test_wrist_edge_assets_must_declare_their_opening():
    _, assets, errs, _ = validate_pack(read_zip(_pack(("left", "right"))))
    assert errs == [] and len(assets) == 2
    _, _, errs, _ = validate_pack(read_zip(_pack()))
    assert errs and all("wrist_edge" in e or "wrist_axis" in e for e in errs)
    assert any("right" in e for e in errs) and not any("left_" in e and "wrist" in e for e in errs)


# -- alignment ------------------------------------------------------------------------------------------------
def test_wrist_alignment_rotates_about_the_opening_and_trims_behind_it():
    ref = {"r": {"axis": [0.0, 1.0], "edge": [100.0, 200.0], "cuff_width_px": 40.0, "angle_deg": 0.0}}
    wac = WristAlignmentController(ref)
    geom = {"wrist_edge_px": [[10, 30], [50, 30]], "wrist_axis": [0.0, 1.0], "wrist_opening": "open_cut",
            "wrist_confidence": 1.0}
    auto, final = wac.placement("r", geom, 0.5)
    M = final.matrix()
    p = M[:2, :2] @ np.array([30.0, 30.0]) + M[:2, 2]
    assert np.allclose(p, [100.0, 200.0 - 6.0]), "opening centre goes TUCK px under the cuff edge"
    _, rot = wac.placement("r", geom, 0.5, {"rot": 30.0})
    M2 = rot.matrix()
    assert np.allclose(M2[:2, :2] @ np.array([30.0, 30.0]) + M2[:2, 2], p), "rotation is about the wrist, not the centre"
    _, piv = wac.placement("r", geom, 0.5, {"pivot_x": 4.0, "overlap": 3.0})
    M3 = piv.matrix()
    assert np.allclose(M3[:2, :2] @ np.array([34.0, 30.0]) + M3[:2, 2], [100.0, 191.0]), "manual pivot and overlap"
    side = {"wrist_edge_px": [[30, 10], [30, 50]], "wrist_axis": [1.0, 0.0]}  # a side cut: the hand goes right
    assert abs(wac.natural_angle("r", side) - 90.0) < 1e-6, "R(90) turns the drawing's +x axis onto the bone (down)"
    spr = np.zeros((80, 60, 4), np.uint8)
    spr[0:80, 10:50] = 255
    trimmed, n = wac.trim(spr, geom, auto, 6.0)
    assert n > 0 and trimmed[30:, :, 3].max() == 255 and trimmed[:5, :, 3].max() == 0
    assert not check_placement({"pivot_x": 5, "overlap": 2}) and check_placement({"overlap": 50})
    pl = Placement((0, 0), (0, 0), 0, 0.5)
    assert np.allclose(np.linalg.svd(pl.matrix()[:2, :2])[1], [0.5, 0.5]), "uniform scale only"


# -- selection engine (synthetic library) -------------------------------------------------------------------------
class _Lib:
    def __init__(self, poses, status="pending_review"):
        self.hands = {s: {} for s in "rl"}
        for s in "rl":
            for p in poses:
                cid = "original" if p == "open_palm" else f"store:lib/{s}_{p}"
                st = "original" if p == "open_palm" else status
                hd = {"anatomical": "right" if s == "r" else "left", "drawn_as": "right" if s == "r" else "left"}
                self.hands[s][p] = HandVariant(s, p, p, f"hand_{s}__{p}", st, "G", {}, (0.0, 0.0), 0.4, cid=cid,
                                               meta={"handedness": hd, "sleeve": "none", "features": {}},
                                               placement={"final": {"angle_deg": 0.0}, "params": {"wrist_fit": 0.8}})


def _ctx(side="r", action="relaxed_standing", t=None, **kw):
    return HandContext(side, action, 0.0, 0.0, 0.0, 10.0, 5.0, (0.0, 0.0), t=t, **kw)


ALL = ["relaxed", "open_palm", "fist", "pointing", "thumbs_up", "gripping", "palm_down", "cupped"]


def test_rules_choose_natural_hands_and_forbid_the_wrong_gestures():
    eng, lib = NaturalHandSelectionEngine(), _Lib(ALL)
    pick = lambda a, ap=True, **kw: eng.select(lib, _ctx(action=a, **kw), None, ap)["chosen"]
    assert pick("relaxed_standing") == "relaxed"
    assert pick("walking") == "relaxed"
    assert pick("running") == "fist"
    assert pick("pointing") == "pointing"
    assert pick("gripping") == "gripping"
    assert pick("thumbs_up") == "thumbs_up"
    assert pick("resting") == "palm_down"
    assert pick("angry") == "fist"
    for a in ("walking", "running"):
        ranked = eng.rank(lib, _ctx(action=a), None, True)
        assert not {"pointing", "thumbs_up"} & {r["pose"] for r in ranked}, a
    # pending drawings are candidates only when previewing: otherwise the original hand
    assert pick("relaxed_standing", ap=False) == "open_palm"
    approved = _Lib(ALL, "approved")
    assert eng.select(approved, _ctx(action="running"), None, False)["chosen"] == "fist"
    assert set(ACTIONS) >= {"relaxed_standing", "walking", "running", "pointing", "shrugging", "explaining", "angry",
                            "gripping", "thumbs_up", "hand_on_hip"}


def test_selection_is_deterministic_and_weights_are_adjustable_per_character():
    lib = _Lib(ALL)
    a = NaturalHandSelectionEngine().rank(lib, _ctx(action="shrugging"), None, True)
    b = NaturalHandSelectionEngine().rank(lib, _ctx(action="shrugging"), None, True)
    assert a == b
    e2 = NaturalHandSelectionEngine.for_character({"actions": {"shrugging": {"prefer": ["cupped", "open_palm"]}}})
    assert e2.select(lib, _ctx(action="shrugging"), None, True)["chosen"] == "cupped"
    e3 = NaturalHandSelectionEngine.for_character({}, {"gesture": 0.0, "fidelity": 1.0})
    assert e3.weights["gesture"] == 0.0 and e3.select(lib, _ctx(action="running"), None, True)["chosen"] == "open_palm"


def test_temporal_rules_hold_gestures_and_switch_at_action_boundaries():
    eng, lib = NaturalHandSelectionEngine(), _Lib(ALL)
    st = HandSelectionState()
    assert eng.select(lib, _ctx(action="running", t=0.0), st, True)["chosen"] == "fist"
    d = eng.select(lib, _ctx(action="running", t=0.05), st, True)
    assert d["chosen"] == "fist"
    # the action changes: the switch happens at once (after the minimum hold)
    d = eng.select(lib, _ctx(action="pointing", t=1.0), st, True)
    assert d["chosen"] == "pointing" and "action changed" in d["reason"]
    # a flickering action cannot pop the hand faster than MIN_HOLD_ACTION_S
    d = eng.select(lib, _ctx(action="running", t=1.0 + MIN_HOLD_ACTION_S / 2), st, True)
    assert d["chosen"] == "pointing" and d["held"]
    # a held gesture stays while the action is unchanged, even if another drawing scores higher
    st2 = HandSelectionState(pose="relaxed", cid="store:lib/r_relaxed", since=0.0, action="walking")
    lib.hands["r"]["open_palm"].status = "original"
    d = eng.select(lib, _ctx(action="walking", t=5.0), st2, True)
    assert d["chosen"] == "relaxed"


def test_explaining_varies_between_beats_and_running_hands_match():
    eng, lib = NaturalHandSelectionEngine(), _Lib(ALL, "approved")
    st = HandSelectionState()
    seq = [eng.select(lib, _ctx(action="explaining", t=float(b), beat=b), st, True)["chosen"] for b in range(4)]
    assert len(set(seq)) >= 2 and all(seq[i] != seq[i + 1] for i in range(3)), seq
    out = eng.select_pair(lib, {s: _ctx(s, "running") for s in "rl"}, None, True)
    assert out["r"]["chosen"] == out["l"]["chosen"] == "fist"


# -- library status and project ------------------------------------------------------------------------------------
@pytest.mark.skipif(not (ZIP.is_file() and V1.is_file()), reason="hand packs not present")
def test_superseded_library_and_sheet_hands_are_archived_with_rollback(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    hs = HandStore(pkg)
    hs.import_pack(V1.read_bytes(), V1.name)
    assert library_status(hs, {})["schoolboy_hand_switches_v1"]["status"] == "active"
    assert library_status(hs, {})[PHASE2_LIBRARY]["status"] == "active", "no sleeve-free library yet"
    hs.import_pack(ZIP.read_bytes(), ZIP.name)
    st = library_status(hs, {})
    assert st["schoolboy_hand_switches_v1"]["status"] == "archived" and st[LIB]["status"] == "active"
    assert st[PHASE2_LIBRARY]["status"] == "archived"
    st = library_status(hs, {"libraries": {"schoolboy_hand_switches_v1": "active", PHASE2_LIBRARY: "active"}})
    assert st["schoolboy_hand_switches_v1"]["status"] == "active" and st[PHASE2_LIBRARY]["user"]
    st = library_status(hs, {"libraries": {LIB: "archived"}})  # rollback: archiving the replacement restores v1
    assert st["schoolboy_hand_switches_v1"]["status"] == "active" and st[PHASE2_LIBRARY]["status"] == "active"
    assert hs.verify() == [] and len(hs.assets()) == 32, "nothing deleted; both libraries stored"
    g = hs.geometry(f"{LIB}/right_pointing")
    assert g["wrist_opening"] == "closed_outline" and g["features"]["index_tip"]


# -- real package -------------------------------------------------------------------------------------------------
def _ignore(d, names):
    skip = {"editor", "motion", "ik_switch", "hand_store"}
    if Path(d).name == "natural_pose":
        skip |= {"poses", "legacy", "closeups", "contact_sheet.png", "contact_sheet_diagnostic.png"}
    return [n for n in names if n in skip]


@pytest.fixture(scope="module")
def pkg(tmp_path_factory):
    dst = tmp_path_factory.mktemp("hr") / "pkg"
    shutil.copytree(REAL_PKG, dst, ignore=_ignore)
    HandStore(dst).import_pack(V1.read_bytes(), V1.name)
    HandStore(dst).import_pack(ZIP.read_bytes(), ZIP.name)
    return dst


@pytest.fixture(scope="module")
def eng(pkg):
    from autorig.motion.pipeline import engine_for_package
    return engine_for_package(pkg)


@real
def test_old_sleeve_attached_variants_are_not_active(eng):
    e, _ = eng
    for s in "rl":
        for pose, v in e.hand_lib.hands[s].items():
            if v.available:
                assert v.cid == "original" or v.cid.startswith(f"store:{LIB}/"), (s, pose, v.cid)
        cands = [c.cid for cs in e.hand_lib.candidates[s].values() for c in cs]
        assert not any(c.startswith(("phase2:", "store:schoolboy_hand_switches_v1")) for c in cands)
        assert e.hand_lib.hands[s]["open_palm"].cid == "original", "the original hand stays the open-palm default"
    ids = {m["old"] for m in e.hand_store["gesture_id_map"]}
    assert "store:schoolboy_hand_switches_v1/right_fist" in ids and "phase2:fist" in ids


@real
def test_presets_get_natural_hands_and_explicit_choices_win(eng):
    e, _ = eng
    want = {"relaxed_standing": "relaxed", "casual_walk": "relaxed", "running": "fist", "sitting": "palm_down",
            "hands_on_hips": "relaxed"}
    for name, pose in want.items():
        r = e.pose(name, allow_pending=True, skeleton_only=True)
        assert all(r["hands"][s]["pose"] == pose for s in "rl"), (name, {s: r["hands"][s]["pose"] for s in "rl"})
    r = e.pose("pointing_speaking", allow_pending=True, skeleton_only=True)
    assert r["hands"]["r"]["pose"] == "pointing"
    aim = r["hand_selection"]["decisions"]["r"]["aim_applied"]
    assert aim["measured"] and abs(aim["residual_deg"]) <= 1.0 and abs(aim["wrist_bend_after_deg"]) <= 60
    sw = {"hand_r": {"user": "cupped", "auto": False, "keys": [], "offsets": {}}}
    r = e.pose("running", 0.25, allow_pending=True, skeleton_only=True, switches=sw)
    assert r["hands"]["r"]["pose"] == "cupped" and r["hands"]["r"]["source"] == "user"
    r = e.pose("running", 0.25, skeleton_only=True)  # nothing approved: the original hand, never pending art
    assert all(r["hands"][s]["cid"] == "original" for s in "rl")


@real
def test_switching_hands_never_moves_the_forearm_and_hands_are_rigid(eng):
    e, vis = eng
    ref = e.pose("conversational_gesture", allow_pending=True, skeleton_only=True, hand_aim=False)["solve"]
    for pose in ("relaxed", "fist", "pointing", "cupped"):
        sw = {"hand_r": {"user": pose, "auto": False, "keys": [], "offsets": {}}}
        r = e.pose("conversational_gesture", allow_pending=True, visible=vis, switches=sw)
        J = r["solve"]["joints_posed"]
        for b in J:
            if b != "hand_r":
                assert np.allclose(J[b], ref["joints_posed"][b]), (pose, b)
        lay = r["hands"]["r"]["layer"]
        st = r["render"]["stats"][lay]
        assert st["blended_fraction"] == 0.0 and abs(st["det_min"] - 1) < 0.02 and abs(st["det_max"] - 1) < 0.02
        assert r["order"].index(lay) < r["order"].index("forearm_r__hscuff"), "the sleeve covers the hand"


@real
def test_gripping_lands_on_the_target_and_hands_stay_attached_under_ik(eng):
    from autorig.handstore import posed_seam
    e, vis = eng
    rest = e.pose("relaxed_standing", skeleton_only=True)
    # Phase 3.3: with the shoulder pivot at the shoulder point the relaxed arm hangs at full reach, so the target is
    # lateral of the shoulder (forearm pointing sideways, the orientation the gripping drawing was drawn in);
    # a target below the hanging hand is out of reach and the engine rightly prefers the fist there
    sh = np.asarray(rest["solve"]["joints_posed"]["upper_arm_r"])
    tgt = (sh + np.array([-0.7, 0.3]) * e.profile.arm("r")).tolist()
    r = e.pose("relaxed_standing", allow_pending=True, visible=vis, hand_actions={"r": "gripping"}, hand_targets={"r": tgt})
    assert r["hands"]["r"]["pose"] == "gripping" and r["hand_selection"]["grip"]["r"]["error_px"] <= 1.5
    sm = posed_seam(r, e, "r", r["hands"]["r"]["layer"])
    assert sm["covered"] and max(sm["overhang_px"]) <= 4 and sm["own_white_px_near_wrist"] <= 40


@real
def test_project_v4_persists_library_and_selection_ops(pkg):
    from autorig.editor.project import VERSION, EditorProject, OpError
    assert VERSION == 6  # Phase 3.4: corrective settings (schema v6)
    p = EditorProject(pkg)
    p.submit({"type": "handstore.library", "library": "schoolboy_hand_switches_v1", "status": "active"})
    p.submit({"type": "handstore.placement", "uid": f"{LIB}/right_cupped", "fields": {"pivot_x": 2, "overlap": 3, "rot": -5}})
    p.submit({"type": "handselect.action", "side": "r", "action": "thumbs_up", "target": None})
    p.submit({"type": "handselect.weights", "weights": {"gesture": 0.5}})
    for bad in ({"type": "handstore.library", "library": "nope", "status": "archived"},
                {"type": "handstore.library", "library": LIB, "status": "deleted"},
                {"type": "handselect.action", "side": "r", "action": "dance"},
                {"type": "handselect.weights", "weights": {"beauty": 1.0}}):
        with pytest.raises(OpError):
            p.submit(bad)
    q = EditorProject(pkg)
    st = q.state()
    assert st["hand_store"]["libraries"] == {"schoolboy_hand_switches_v1": "active"}
    assert st["hand_store"]["placement"][f"{LIB}/right_cupped"] == {"pivot_x": 2.0, "overlap": 3.0, "rot": -5.0}
    assert st["hand_selection"]["actions"]["r"] == "thumbs_up" and st["hand_selection"]["weights"] == {"gesture": 0.5}
    for _ in range(4):
        q.undo()
    assert q.state()["hand_store"]["libraries"] == {} and q.state()["hand_selection"]["actions"]["r"] is None


@real
def test_rig_controls_store_the_hand_selection_section(eng):
    from autorig.motion.rigctl import build_rig_controls, validate
    e, _ = eng
    doc = build_rig_controls(e)
    assert validate(doc) == [] and doc["schema_version"] == "1.1.0"
    hs = doc["hand_selection"]
    assert hs["runs_in"].startswith("AutoRig") and hs["moho"]["status"] == "proposed_unverified"
    g = hs["groups"]["hand_right"]
    assert g["bone"] == "hand_r" and any(v["cid"].startswith(f"store:{LIB}/") and v["placement"]["params"]["wrist_axis"]
                                         for v in g["variants"])
    assert any("Switch Layer keyframes" in m["moho"] for m in hs["moho_translation"])


@real
def test_committed_replacement_evidence():
    ev = REAL_PKG / "hand_store" / "evidence_v2" / "results.json"
    if not ev.is_file():
        pytest.skip("replacement evidence not generated")
    r = json.loads(ev.read_text())
    assert len(r["tests"]) == 10 and len(r["gates"]) == 14
    for f in ("library_attached.jpg", "auto_selection.jpg", "before_after.jpg", "switch_sequence.gif"):
        assert (ev.parent / f).is_file(), f
    for t in r["tests"]:
        assert (ev.parent / t["file"]).is_file()
    assert len(r["library_sheet"]["drawings"]) == 16
    assert HandStore(REAL_PKG).verify() == []
