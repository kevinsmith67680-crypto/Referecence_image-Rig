"""Phase 3.7 hand selection: metadata, palm orientation, hard constraints, approval modes, fallbacks, overrides,
wrist alignment, the performance resolver fix and the shrug variants.

These tests check mechanics and constraints. They never judge whether a drawing looks natural; that needs a person.
Tests that need the real schoolboy package skip when it is missing.
"""

from __future__ import annotations

import copy
import json
import shutil
import warnings
from pathlib import Path

import numpy as np
import pytest

from autorig import handmeta
from autorig.handmeta import HandOrientationAnalyzer, apply_corrections, classify, rotate
from autorig.motion.handselect import ACTIONS, OVERRIDE_RULE, HandContext, NaturalHandSelectionEngine
from autorig.motion.switches import HandVariant
from autorig.performance.resolver import ArtworkAndCorrectionResolver, is_action

ROOT = Path(__file__).resolve().parents[1]
REAL_PKG = ROOT / "projects" / "schoolboy_tpose" / "character_package"
real = pytest.mark.skipif(not (REAL_PKG / "natural_pose" / "rig.json").is_file(), reason="real package missing")
INSPECTION = ROOT / "references" / "schoolboy" / "hand_pack_v2" / "semantic_inspection.json"


def _meta(hand: str, n, tol: float = 180.0, **kw):
    return {"schema": handmeta.SCHEMA, "schema_version": handmeta.SCHEMA_VERSION, "handedness": hand,
            "palm_normal_sprite": n, "rotation_tolerance_deg": tol, "errors": [], "excluded": False,
            "approval": {"state": "PENDING_REVIEW"}, **kw}


class _Lib:
    """Synthetic library: each drawing has explicit metadata (what it shows), never inferred from its name."""

    def __init__(self, spec: dict[str, dict], status: str = "pending_review"):
        self.hands = {s: {} for s in "rl"}
        for s in "rl":
            for pose, m in spec.items():
                cid = "original" if pose == "open_palm" else f"store:lib/{s}_{pose}"
                st = "original" if pose == "open_palm" else status
                hand = m.get("hand", {"r": "right", "l": "left"}[s])
                if isinstance(hand, dict):
                    hand = hand[s]
                meta = _meta(hand, m["n"], m.get("tol", 180.0))
                self.hands[s][pose] = HandVariant(s, pose, pose, f"hand_{s}__{pose}", st, "G", {}, (0.0, 0.0), 0.4,
                                                  cid=cid, meta={"hand_meta": meta, "features": {}},
                                                  placement={"final": {"angle_deg": m.get("a0", 0.0)},
                                                             "params": {"wrist_fit": 0.85}})


CAM, UP, DOWN, EDGE = [0.0, 0.0, 1.0], [0.0, -0.8, 0.6], [0.0, 0.74, -0.67], [1.0, 0.0, 0.0]
SPEC = {"open_palm": {"n": CAM}, "relaxed": {"n": CAM, "tol": 50.0}, "fist": {"n": CAM}, "pointing": {"n": CAM},
        "thumbs_up": {"n": CAM, "tol": 45.0}, "gripping": {"n": EDGE, "tol": 100.0}, "palm_down": {"n": DOWN, "tol": 60.0},
        "cupped": {"n": UP, "tol": 35.0}}


def _ctx(side="r", action="relaxed_standing", forearm=10.0, hand=10.0, rest=0.0, **kw):
    return HandContext(side, action, forearm, hand, rest, 10.0, 5.0, (0.0, 0.0), **kw)


# -- metadata and orientation --------------------------------------------------------------------------------
def test_flat_rotation_never_turns_a_camera_facing_palm_up():
    an = HandOrientationAnalyzer()
    for rot in range(-180, 181, 15):
        r = an.analyze(_meta("right", CAM), float(rot))
        assert r["palm_class"] == "PALM_TO_CAMERA", rot
    assert not an.reachable_by_rotation(_meta("right", CAM), "PALM_UP")
    assert an.reachable_by_rotation(_meta("right", UP), "PALM_UP")
    # a palm-up drawing turned upside down shows its palm facing down (the Phase 3.6 cupped-as-stored defect)
    assert an.analyze(_meta("right", UP), 180.0)["palm_class"] == "PALM_DOWN"
    assert an.analyze(_meta("right", UP), 10.0)["palm_class"] == "PALM_UP"
    # the back of a hand seen from above, turned over, does not become a visible palm-up hand
    assert an.analyze(_meta("right", DOWN), 180.0)["palm_class"] == "AMBIGUOUS"
    assert classify(rotate(EDGE, 0.0))[0] == "EDGE_ON"


def test_image_finger_direction_is_not_the_palm_direction():
    # fingers pointing sideways (rotation 90) with the palm toward the camera are still PALM_TO_CAMERA
    assert HandOrientationAnalyzer().analyze(_meta("right", CAM), 90.0)["palm_class"] == "PALM_TO_CAMERA"


def test_arm_pose_limits_palm_up():
    ok, why = HandOrientationAnalyzer.arm_allows("PALM_UP", 8.0)
    assert not ok and "not anatomically available" in why
    assert HandOrientationAnalyzer.arm_allows("PALM_UP", 60.0)[0]


def test_metadata_validation_and_corrections():
    rec = {**_meta("up", [0, 0]), "approval": {"state": "WHATEVER"}}
    errs = handmeta.validate(rec)
    assert any("handedness" in e for e in errs) and any("palm_normal" in e for e in errs) and any("approval" in e for e in errs)
    assert handmeta.check_correction("handedness", "left") is None
    assert handmeta.check_correction("handedness", "up")
    assert handmeta.check_correction("skew", 1)
    base = {"schema": handmeta.SCHEMA, "assets": {"lib/a": {**_meta("left", UP, 35.0), "key": "lib/a", "uid": "lib/a",
                                                            "cid": "store:lib/a", "store_side": "l", "label_pose": "cupped",
                                                            "gesture": "cupped", "measured": {}, "provenance": {}}}}
    out = apply_corrections(base, {"lib/a": {"handedness": "right"}})
    a = out["assets"]["lib/a"]
    assert a["use_side"] == "r" and a["reassigned"] and a["field_sources"]["handedness"] == "user"
    assert base["assets"]["lib/a"]["handedness"] == "left", "corrections never modify the base metadata"


def test_approval_states():
    assert handmeta.approval_state("original") == "APPROVED"
    assert handmeta.approval_state("pending_review") == "PENDING_REVIEW"
    assert handmeta.approval_state("rejected") == "REJECTED"
    assert handmeta.approval_state(None) == "MISSING"


# -- hard constraints ----------------------------------------------------------------------------------------
def test_wrong_handedness_is_rejected_whatever_the_score():
    spec = {**SPEC, "cupped": {"n": UP, "tol": 35.0, "hand": {"r": "left", "l": "right"}}}
    eng = NaturalHandSelectionEngine({"gesture": 1.0})
    d = eng.select(_Lib(spec), _ctx(action="shrugging", forearm=60.0), None, True)
    rej = {r["pose"]: r for r in d["rejected"]}
    assert "cupped" in rej and any(v["constraint"] == "handedness" for v in rej["cupped"]["violations"])
    assert d["chosen"] != "cupped"


def test_palm_view_is_a_hard_constraint_for_the_expressive_shrug():
    eng = NaturalHandSelectionEngine()
    lib = _Lib(SPEC)
    d = eng.select(lib, _ctx(action="shrugging", forearm=60.0, hand=10.0), None, True)
    assert d["chosen"] == "cupped", "the palm-up drawing passes in review mode"
    rej = {r["pose"]: r for r in d["rejected"]}
    assert any(v["constraint"] == "palm" and "no flat rotation" in v["reason"] for v in rej["relaxed"]["violations"])
    assert any(v["constraint"] == "palm" for v in rej["open_palm"]["violations"])
    assert {n["pose"] for n in d["needs_artwork"]} == {"palm_up"}, "the open palm-up drawing is requested"


def test_rotation_beyond_the_drawn_tolerance_is_rejected():
    # the Phase 3.6 bug: a relaxed (hanging) drawing turned ~80 deg reads as a presenting hand
    eng = NaturalHandSelectionEngine()
    d = eng.select(_Lib(SPEC), _ctx(action="relaxed_standing", forearm=64.0, hand=80.0), None, True)
    rej = {r["pose"]: r for r in d["rejected"]}
    assert any(v["constraint"] == "rotation" for v in rej["relaxed"]["violations"])
    assert d["chosen"] == "open_palm", "a camera-facing open palm may turn freely"


def test_production_mode_uses_approved_only_and_falls_back_with_a_report():
    eng = NaturalHandSelectionEngine()
    lib = _Lib(SPEC)
    d = eng.select(lib, _ctx(action="shrugging", forearm=60.0, hand=10.0), None, False)
    assert d["mode"] == "production"
    assert d["fallback"] is not None and d["fallback"]["action"] == "shrug_subtle"
    assert d["chosen"] == "open_palm" and d["cid"] == "original"
    st = {n["pose"]: n["status"] for n in d["needs_artwork"]}
    assert st == {"palm_up": "NEEDS_ARTWORK", "cupped": "NEEDS_APPROVAL"}
    assert "NEEDS_APPROVAL: cupped" in d["fallback"]["reason"] and "NEEDS_ARTWORK: palm_up" in d["fallback"]["reason"]
    approved = _Lib(SPEC, status="approved")
    assert eng.select(approved, _ctx(action="shrugging", forearm=60.0, hand=10.0), None, False)["chosen"] == "cupped"


def test_rejected_and_excluded_drawings_are_never_used():
    lib = _Lib(SPEC, status="rejected")
    d = NaturalHandSelectionEngine().select(lib, _ctx(action="pointing"), None, True)
    assert d["chosen"] not in ("pointing",)
    lib = _Lib(SPEC, status="approved")
    lib.hands["r"]["pointing"].meta["hand_meta"]["excluded"] = True
    d = NaturalHandSelectionEngine().select(lib, _ctx(action="pointing"), None, True)
    assert d["chosen"] != "pointing" and any(v["constraint"] == "excluded" for r in d["rejected"] for v in r["violations"])


def test_invalid_metadata_rejects_instead_of_crashing():
    lib = _Lib(SPEC)
    hm = lib.hands["r"]["cupped"].meta["hand_meta"]
    hm.update(palm_normal_sprite=[0, 0], errors=["palm_normal_sprite must be three numbers with length ~1"])
    d = NaturalHandSelectionEngine().select(lib, _ctx(action="shrugging", forearm=60.0, hand=10.0), None, True)
    rej = {r["pose"]: r for r in d["rejected"]}
    kinds = {v["constraint"] for v in rej["cupped"]["violations"]}
    assert "metadata" in kinds and "palm" in kinds


def test_explanation_lists_choice_and_rejections():
    d = NaturalHandSelectionEngine().select(_Lib(SPEC), _ctx(action="shrugging", forearm=60.0, hand=10.0), None, True)
    text = "\n".join(d["explanation"])
    assert "shrugging -> cupped" in text and "rejected relaxed" in text and "NEEDS_ARTWORK: palm_up" in text


def test_override_check_reports_violations_without_hiding_them():
    lib = _Lib(SPEC)
    ev = NaturalHandSelectionEngine().evaluate(lib.hands["r"]["relaxed"], _ctx(forearm=64.0, hand=80.0), OVERRIDE_RULE,
                                               None, True)
    assert ev["rejected"] and ev["violations"][0]["constraint"] == "rotation"


def test_actions_and_resolver_split():
    assert {"shrugging", "shrug_subtle", "shrug_expressive", "hand_on_desk", "conversation"} <= set(ACTIONS)
    assert is_action("shrugging") and is_action("hand_on_desk") and not is_action("pointing") and not is_action("cupped")
    assert NaturalHandSelectionEngine().rule("shrug_expressive")["palm"] == ["PALM_UP"]


def test_resolver_writes_explicit_user_choices():
    """Regression for the Phase 3.6 bug: performance hands must reach the switch system as explicit choices."""

    class _E:
        class hand_lib:
            candidates = {"r": {}, "l": {}}

    sw, rep = ArtworkAndCorrectionResolver(_E(), allow_pending=True).resolve({"r": "pointing"})
    assert sw["hand_r"]["auto"] is False and sw["hand_r"]["user"] == "open_palm"  # missing drawing -> reference palm
    assert rep["r"]["kind"] == "pose" and "missing" in rep["r"]["note"]
    dec = {"chosen": "cupped", "fallback": None}
    sw, rep = ArtworkAndCorrectionResolver(_E(), allow_pending=True).resolve({"r": "shrugging"}, {"r": dec})
    assert sw["hand_r"]["auto"] is False and rep["r"]["kind"] == "action"


# -- real package --------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def ev():
    from autorig.garmentrig.correctives import CorrectiveArtworkStore, CorrectiveRuntime
    from autorig.motion.pipeline import engine_for_package

    eng, vis = engine_for_package(REAL_PKG)
    eng.correctives = CorrectiveRuntime(eng, CorrectiveArtworkStore(REAL_PKG))
    return eng, vis


@real
def test_semantic_inspection_and_metadata_inventory():
    insp = json.loads(INSPECTION.read_text())
    assert insp["inspected_by"].startswith("assistant") and "not a person" in insp["inspected_by"]
    inv = handmeta.load(REAL_PKG)
    assert inv is not None and inv["schema_version"] == handmeta.SCHEMA_VERSION
    A = inv["assets"]
    assert len(A) == 18
    assert not any(a["errors"] for a in A.values())
    # the cupped pair is stored on the wrong sides: the audit reassigns them, from the drawings, not the names
    for uid, side in (("schoolboy_hands_sleeveless/left_cupped", "r"), ("schoolboy_hands_sleeveless/right_cupped", "l")):
        a = A[uid]
        assert a["reassigned"] and a["use_side"] == side
        assert abs(a["rest_rotation_from_drawn_deg"]) > 170, "placed upside down on its stored side"
    for a in A.values():
        assert a["approval"]["state"] in ("APPROVED", "PENDING_REVIEW"), "nothing approved by the audit"
        if a["cid"] != "original":
            assert a["approval"]["state"] == "PENDING_REVIEW"


@real
def test_engine_places_reassigned_cupped_hands_palm_up(ev):
    eng, _ = ev
    an = HandOrientationAnalyzer()
    for s in "rl":
        v = eng.hand_lib.hands[s]["cupped"]
        hm = v.meta["hand_meta"]
        assert hm["handedness"] == {"r": "right", "l": "left"}[s]
        a0 = v.placement["final"]["angle_deg"]
        assert abs(a0) < 10, "the corrected side attaches the drawing as drawn"
        assert an.analyze(hm, a0)["palm_class"] == "PALM_UP"


@real
def test_performance_hand_requests_are_honoured(ev):
    """The Phase 3.6 regression: requested hands (pointing, desk) were replaced by the automatic relaxed hand."""
    from autorig.performance import scenarios
    from autorig.performance.director import PerformanceDirector

    eng, vis = ev
    d = PerformanceDirector(eng, vis, scenarios.acting_upgrade())
    r = d.solve(3.25)
    assert r["performance"]["hands"]["r"]["requested"] == "pointing"
    assert r["hands"]["r"]["pose"] == "pointing" and r["hands"]["r"]["source"] == "user"
    perf = scenarios.acting_upgrade()
    perf.allow_pending = False
    r = PerformanceDirector(eng, vis, perf).solve(3.25)
    assert r["hands"]["r"]["pose"] == "open_palm" and not r["hands"]["r"]["pending_review"], "production: no pending"


@real
def test_shrug_variants(ev):
    from autorig.artistic.transitions import acting_test_keys
    from autorig.performance import scenarios
    from autorig.performance.director import PerformanceDirector

    eng, vis = ev
    t = next(k.t for k in acting_test_keys() if k.label == "shrug") + 0.3
    sub = PerformanceDirector(eng, vis, scenarios.acting_upgrade()).solve(t)
    for s in "rl":
        assert sub["hands"][s]["pose"] in ("open_palm", "relaxed")
        dec = sub["performance"]["hands"][s]["decision"]
        assert dec["action"] == "shrug_subtle" and not dec["fallback"]
    perf = scenarios.acting_upgrade(shrug="expressive")
    ex = PerformanceDirector(eng, vis, perf).solve(t)
    for s in "rl":
        assert ex["hands"][s]["pose"] == "cupped" and ex["hands"][s]["pending_review"], "review preview, labelled"
        assert ex["hand_selection"]["decisions"][s]["override_check"]["palm"] == "PALM_UP"
    perf.allow_pending = False
    d = PerformanceDirector(eng, vis, perf)
    pr = d.solve(t)
    # production without approved palm-up hands: the expressive key pose is shown as SHRUG_SUBTLE (reported), never
    # as stand-in hands on the expressive arms
    assert d.substitutions and d.substitutions[0]["shown"] == "SHRUG_SUBTLE"
    assert pr["performance"]["substitutions"] == d.substitutions
    for s in "rl":
        dec = pr["performance"]["hands"][s]["decision"]
        assert dec["action"] == "shrug_subtle" and pr["hands"][s]["cid"] == "original"
    # the hand-level fallback still reports NEEDS_ARTWORK when the expressive hands are requested directly
    eng_sel = eng.hand_select.select(eng.hand_lib, HandContext("r", "shrugging", 60.0, 72.0,
                                                               float(eng.sk.bone("hand_r").rest_angle), -40.0, -8.0,
                                                               (300.0, 580.0)), None, False)
    assert eng_sel["fallback"] and {n["pose"]: n["status"] for n in eng_sel["needs_artwork"]}["palm_up"] == "NEEDS_ARTWORK"
    # modest shoulders: the clavicle parameters stay below the Phase 3.6 values (13/15)
    assert scenarios.SHRUG_SUBTLE["clavicle_r"] <= 7 and scenarios.SHRUG_EXPRESSIVE["clavicle_l"] <= 8


@real
def test_shrug_shoulders_do_not_inflate(ev):
    from autorig.artistic.transitions import acting_test_keys
    from autorig.performance import scenarios, shoulders
    from autorig.performance.director import PerformanceDirector

    eng, vis = ev
    t = next(k.t for k in acting_test_keys() if k.label == "shrug") + 0.3
    n = PerformanceDirector(eng, vis, scenarios.acting_upgrade()).solve(0.0, render=True)
    ref = shoulders.measure(n["image"], n["solve"]["joints_posed"], n["pad"])
    r = PerformanceDirector(eng, vis, scenarios.acting_upgrade()).solve(t, render=True)
    c = shoulders.check(shoulders.measure(r["image"], r["solve"]["joints_posed"], r["pad"]), ref, shrug=True)
    assert c["status"] == "PASS", c["flags"]
    old = scenarios.acting_upgrade()
    for k in old.base_keys:
        if k["label"].startswith("shrug"):
            k["params"].update({"clavicle_r": 13.0, "clavicle_l": 15.0})
    r = PerformanceDirector(eng, vis, old).solve(t, render=True)
    c = shoulders.check(shoulders.measure(r["image"], r["solve"]["joints_posed"], r["pad"]), ref, shrug=True)
    assert c["status"] == "WARNING" and any("inflated" in f for f in c["flags"]), "the 3.6 values are flagged"


@real
def test_wrist_alignment_and_arm_unchanged_across_variants(ev):
    from autorig.motion.handcheck import wrist_check

    eng, vis = ev
    joints = {}
    for pose in ("relaxed", "open_palm", "fist"):
        sw = {"hand_r": {"user": pose, "auto": False, "keys": [], "offsets": {}}}
        r = eng.pose("relaxed_standing", visible=vis, allow_pending=True, switches=sw, corrective_mode="all")
        joints[pose] = {k: tuple(np.round(r["solve"]["joints_posed"][k], 6)) for k in ("upper_arm_r", "forearm_r", "hand_r")}
        w = wrist_check(r, eng, "r")
        assert w["status"] == "PASS", (pose, w["failed"], w["checks"])
        if pose != "open_palm":
            assert w["checks"]["proportion"]["ok"] and w["checks"]["no_duplicate_cuff"]["ok"]
    assert joints["relaxed"] == joints["open_palm"] == joints["fist"], "a hand drawing never moves the arm"


@real
def test_hand_on_desk_contact_uses_the_constrained_selection(ev):
    from autorig.performance import scenarios
    from autorig.performance.director import PerformanceDirector

    eng, vis = ev
    d = PerformanceDirector(eng, vis, scenarios.classroom(eng))
    r = d.solve(3.0)
    h = r["performance"]["hands"]["r"]
    assert h["requested"] == "hand_on_desk" and h["kind"] == "action"
    assert r["hands"]["r"]["pose"] == h["pose"] and h["decision"]["chosen"] is not None
    assert r["performance"]["contact_sliding_px"] is not None


@real
def test_hand_selection_is_pure_in_time(ev):
    from autorig.performance import scenarios
    from autorig.performance.director import PerformanceDirector

    eng, vis = ev
    d = PerformanceDirector(eng, vis, scenarios.acting_upgrade(shrug="expressive"))
    ts = d.frames()[::5]
    fwd = [{s: d.solve(t)["hands"][s]["pose"] for s in "rl"} for t in ts]
    d2 = PerformanceDirector(eng, vis, scenarios.acting_upgrade(shrug="expressive"))
    back = [{s: d2.solve(t)["hands"][s]["pose"] for s in "rl"} for t in reversed(ts)]
    assert fwd == list(reversed(back)), "random access == playback"


@real
def test_project_ops_for_metadata_corrections(tmp_path):
    from autorig.editor.project import EditorProject, OpError

    dst = tmp_path / "pkg"
    shutil.copytree(REAL_PKG, dst, ignore=lambda d, n: [x for x in n if x in ("editor", "natural_pose", "hand_store",
                                                                                    "previews", "reports")])
    p = EditorProject(dst)
    p.submit({"type": "handmeta.set", "key": "schoolboy_hands_sleeveless/left_cupped", "field": "handedness",
              "value": "left"})
    assert p.state()["hand_metadata"]["corrections"]["schoolboy_hands_sleeveless/left_cupped"]["handedness"] == "left"
    with pytest.raises(OpError):
        p.submit({"type": "handmeta.set", "key": "schoolboy_hands_sleeveless/left_cupped", "field": "handedness",
                  "value": "up"})
    with pytest.raises(OpError):
        p.submit({"type": "handmeta.set", "key": "../etc", "field": "note", "value": "x"})
    p.submit({"type": "handmeta.reset", "key": "schoolboy_hands_sleeveless/left_cupped"})
    assert p.state()["hand_metadata"]["corrections"] == {}
    p.undo()
    assert p.state()["hand_metadata"]["corrections"]
    q = EditorProject(dst)
    assert q.state()["hand_metadata"]["corrections"], "persists across reload"


@real
def test_http_inspector_and_metadata(tmp_path):
    from fastapi.testclient import TestClient

    from autorig.editor.server import create_app

    dst = tmp_path / "pkg"

    def ignore(d, names):
        skip = {"editor"}
        if Path(d).name == "natural_pose":
            skip |= {"poses", "legacy", "closeups", "performance", "artistic", "garments", "correctives", "motion",
                     "ik_switch", "hand_selection", "reference_motion"}
        return [n for n in names if n in skip]

    shutil.copytree(REAL_PKG, dst, ignore=ignore)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        c = TestClient(create_app(dst))
    m = c.get("/api/hands/metadata").json()
    # the cupped pair (Phase 3.7) and, since the Phase 3.8.1 re-inspection, the relaxed pair (backs of the opposite
    # hands) are offered on their anatomical sides
    assert m["available"] and sorted(m["summary"]["reassigned"]) == sorted(
        f"schoolboy_hands_sleeveless/{s}_{p}" for s in ("left", "right") for p in ("cupped", "relaxed"))
    out = c.post("/api/hands/inspect", json={"mode": "production"}).json()
    for s in "rl":
        x = out["sides"][s]
        assert not x["active"]["pending_review"], "production never shows pending drawings"
        assert x["decision"]["explanation"]
    out = c.post("/api/hands/inspect", json={"mode": "review"}).json()
    assert out["mode"] == "review"
    assert c.get("/api/hands/closeup.png", params={"side": "r"}).status_code == 200


@real
def test_metadata_file_matches_the_json_schema():
    import jsonschema

    schema = json.loads((ROOT / "src" / "autorig" / "data" / "schemas" / "hand_asset_metadata.schema.json").read_text())
    inv = handmeta.load(REAL_PKG)
    jsonschema.validate(inv, schema)
    bad = copy.deepcopy(inv)
    next(iter(bad["assets"].values()))["approval"]["state"] = "AUTO_APPROVED"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_shoulder_measure_is_symmetric():
    """Regression: the right side's chord ran right-to-left and its hump was clamped to nonsense."""
    from autorig.performance import shoulders

    H, W = 400, 600
    img = np.zeros((H, W, 4), np.uint8)
    yy, xx = np.mgrid[0:H, 0:W]
    top = 120 + 0.25 * np.abs(xx - 300)  # sloped shoulders, symmetric about x = 300
    img[..., 3] = np.where((yy >= top) & (np.abs(xx - 300) < 200), 255, 0)
    J = {"neck": (300.0, 110.0), "clavicle_r": (270.0, 140.0), "clavicle_l": (330.0, 140.0),
         "upper_arm_r": (160.0, 160.0), "upper_arm_l": (440.0, 160.0)}
    m = shoulders.measure(img, J, 0)
    assert m["r"]["slope_deg"] == pytest.approx(m["l"]["slope_deg"], abs=0.5) and m["r"]["slope_deg"] > 10
    assert m["r"]["hump_px"] == pytest.approx(m["l"]["hump_px"], abs=1.0) and abs(m["r"]["hump_px"]) < 3
