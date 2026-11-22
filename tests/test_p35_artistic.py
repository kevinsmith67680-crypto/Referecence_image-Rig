"""Phase 3.5 artistic posing:
- planner (whole-body intents);
- pose candidates and silhouette diagnostics;
- weight and balance, IK integration;
- artwork-aware feasibility;
- alternative artwork store and switching;
- dynamic layer order (sublayers, hysteresis);
- views and limb configurations;
- transitions and acting sequences;
- the animation evaluator;
- art-directed joint folds, shoulder controller;
- original-pixel protection, feedback parsing;
- editor ops, migration and HTTP API.

These are numerical and behavioural checks. Visual quality is judged by a person; nothing here approves a pose or
a drawing."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from autorig.artistic.alternatives import (AlternativeArtwork, AlternativeArtworkController, AlternativeArtworkStore,
                                           ViewAwareRigController, identity_check, limb_config_doc, substitute)
from autorig.artistic.candidates import PoseCandidateGenerator, assess, rank_key, render_pose
from autorig.artistic.evaluate import ArtisticAnimationEvaluator
from autorig.artistic.feasibility import ArtworkFeasibilityEngine
from autorig.artistic.feedback import parse_feedback
from autorig.artistic.layer_order import DynamicLayerOrderController, split_by_parts
from autorig.artistic.poses import ArtisticPosePlanner, apply_constraints, mirror, variant
from autorig.artistic.silhouette import SilhouetteQualityEvaluator
from autorig.artistic.transitions import ActingSequence, acting_test_keys, bump, ease, params_of

ROOT = Path(__file__).resolve().parents[1]
REAL_PKG = ROOT / "projects" / "schoolboy_tpose" / "character_package"
real = pytest.mark.skipif(not (REAL_PKG / "natural_pose" / "rig.json").is_file(), reason="real package missing")


def _ignore(d, names):
    skip = {"editor", "motion", "ik_switch", "garments", "evidence", "evidence_v2", "correctives", "artistic",
            "alternatives"}
    if Path(d).name == "natural_pose":
        skip |= {"poses", "legacy", "closeups", "contact_sheet.png", "contact_sheet_diagnostic.png"}
    return [n for n in names if n in skip]


@pytest.fixture(scope="module")
def pkg(tmp_path_factory):
    dst = tmp_path_factory.mktemp("p35") / "pkg"
    shutil.copytree(REAL_PKG, dst, ignore=_ignore)
    for f in ("corrective_assets.json", "alternative_artwork.json"):
        p = dst / "analysis" / f
        if p.exists():
            p.unlink()
    return dst


@pytest.fixture(scope="module")
def ev(pkg):
    from autorig.artistic.evidence import ensure_assets
    from autorig.garmentrig.correctives import (CorrectiveArtworkStore, CorrectiveRuntime, generate_all)
    from autorig.motion.pipeline import engine_for_package
    eng, vis = engine_for_package(pkg)
    generate_all(eng, CorrectiveArtworkStore(pkg), vis, progress=lambda *_: None)
    ensure_assets(eng, vis, pkg, lambda *_: None)
    eng.correctives = CorrectiveRuntime(eng, CorrectiveArtworkStore(pkg))
    return eng, vis, ArtisticPosePlanner(eng.profile)


# -- planner ----------------------------------------------------------------------------------------------------
@real
def test_planner_builds_a_whole_body_contrapposto(ev):
    eng, vis, P = ev
    ap = variant("weight_shift")
    r = render_pose(eng, vis, ap, planner=P)
    it = r["intent"]
    assert it.weight_left > 0.7 and it.pelvis_roll < -3.0, "weight on the left leg raises the left hip"
    assert it.chest is not None and np.sign(it.chest) != np.sign(it.pelvis_roll), "the shoulders counter the hips"
    assert it.legs["r"].knee_min >= 9.0, "the free leg is soft"
    assert any("artistic: weight_shift" in n for n in it.notes)
    lim = r["solve"]["violations"]
    assert not [v for v in lim if "hard" in str(v)], lim


@real
def test_artistic_pose_is_less_symmetric_and_hands_read_on_the_silhouette(ev):
    eng, vis, P = ev
    base = eng.pose("relaxed_standing", visible=vis, evaluate_garments=False, corrective_mode="all")
    art = render_pose(eng, vis, variant("relaxed_standing", "arms_freer"), planner=P)
    sb = SilhouetteQualityEvaluator().evaluate(base, "relaxed_standing")
    sa = SilhouetteQualityEvaluator().evaluate(art, "relaxed_standing", {"gesture_r": "hang", "gesture_l": "hang"})
    codes_b = {w["code"] for w in sb["warnings"]}
    assert "mannequin_symmetry" in codes_b and "hand_inside_silhouette" in codes_b, codes_b
    assert sa["metrics"]["symmetry_iou"] < sb["metrics"]["symmetry_iou"] - 0.05
    assert not {"mannequin_symmetry", "hand_inside_silhouette"} & {w["code"] for w in sa["warnings"]}
    assert art["hands"]["r"]["drawing"] == "relaxed" and art["hands"]["r"]["pending_review"], \
        "the relaxed drawing is previewed, never approved"


def test_mirror_and_constraints():
    ap = variant("point_right")
    m = mirror(ap)
    assert m.weight_left == pytest.approx(1 - ap.weight_left) and m.arms["l"].kind == "reach"
    c = apply_constraints(ap, [{"action": "point_right", "param": "arm_r_reach_lateral", "delta": -0.1},
                               {"action": "other", "param": "head", "delta": 50},
                               {"action": "*", "param": "clavicle_r", "delta": -3.0}])
    assert c.arms["r"].reach[0] == pytest.approx(ap.arms["r"].reach[0] - 0.1)
    assert c.head == ap.head and c.clavicle["r"] == pytest.approx(ap.clavicle["r"] - 3.0)


# -- candidates, balance, IK -------------------------------------------------------------------------------------
@real
def test_candidates_are_assessed_separately_and_ranked(ev):
    eng, vis, _ = ev
    res = PoseCandidateGenerator(eng, vis).candidates("point_right")
    assert len(res["candidates"]) >= 3 and res["recommended"] in res["ranking"]
    keys = [rank_key(c) for c in res["candidates"]]
    assert sorted(keys)[0] == rank_key(next(c for c in res["candidates"] if c["variant"] == res["recommended"]))
    for c in res["candidates"]:
        assert c["feasibility"]["class"] and "warnings" in c["silhouette"] and c["human_review"] == "pending"
        assert c["com_outside_support_px"] is None or c["com_outside_support_px"] < 25, "balanced"
        assert c["hands"]["r"] == "point", "the pointing drawing is chosen for the gesture"


@real
def test_user_ik_still_applies_on_top_of_an_artistic_intent(ev):
    from autorig.garmentrig.evidence import fk_state
    eng, vis, P = ev
    ap = variant("relaxed_standing")
    rq = P.request(ap)
    name = rq.pop("name")
    r = eng.pose(name, visible=vis, skeleton_only=True, ik=fk_state({"r": (120.0, 5.0)}), **rq)
    from autorig.motion.switches import correction_readings_from_pose
    assert correction_readings_from_pose(eng.solver, r["pose"])["abduction_r"] == pytest.approx(120.0, abs=3.0)


# -- feasibility ------------------------------------------------------------------------------------------------
@real
@pytest.mark.parametrize("action,expected", [("sit", "REQUIRES_ALTERNATIVE_ARTWORK"), ("lean_forward", "REQUIRES_NEW_VIEW"),
                                             ("walk", "REQUIRES_NEW_VIEW"), ("deep_elbow", "REQUIRES_ALTERNATIVE_ARTWORK"),
                                             ("relaxed_standing", "SUPPORTED_WITH_CORRECTIONS")])
def test_feasibility_classifies_before_deformation(ev, action, expected):
    eng, vis, P = ev
    ap = variant(action)
    rq = P.request(ap)
    name = rq.pop("name")
    rq.pop("depth", None)
    r = eng.pose(name, visible=vis, skeleton_only=True, **rq)  # no pixel deformation
    fz = ArtworkFeasibilityEngine().classify(ap, r)
    assert fz["class"] in (expected, "SUPPORTED") if expected == "SUPPORTED_WITH_CORRECTIONS" else fz["class"] == expected
    if action == "relaxed_standing":
        assert fz["pending_approval"], "pending hand drawings are listed, not hidden"


# -- alternative artwork --------------------------------------------------------------------------------------------
@real
def test_alternative_artwork_store_validates_and_switches(ev, pkg):
    eng, vis, P = ev
    st = AlternativeArtworkStore(pkg)
    H, W = eng.source.shape[:2]
    rgba = eng.layers["thigh_r"].copy()
    rgba[..., :3] = 255 - rgba[..., :3]  # an obviously different test drawing
    mk = lambda **kw: AlternativeArtwork(**{"art_id": "test_thigh", "layer": "thigh_r", "supported_poses": ["sit"],
                                            "attach_bone": "thigh_r", **kw})
    with pytest.raises(ValueError):
        st.add(mk(), rgba[:10], (H, W), set(eng.layers))
    with pytest.raises(ValueError):
        st.add(mk(scale=0.8), rgba, (H, W), set(eng.layers))
    with pytest.raises(ValueError):
        st.add(mk(layer="nope"), rgba, (H, W), set(eng.layers))
    a1 = st.add(mk(), rgba, (H, W), set(eng.layers))
    a2 = st.add(mk(), rgba, (H, W), set(eng.layers))
    assert (a1.version, a2.version) == (1, 2) and st.verify() == []
    ctl = AlternativeArtworkController(eng, AlternativeArtworkStore(pkg))
    chosen, rep = ctl.select("sit", "FRONT", {}, allow_pending=False)
    assert chosen == {}, "pending drawings are not used without a preview request"
    chosen, _ = ctl.select("sit", "FRONT", {}, allow_pending=True)
    assert chosen == {"thigh_r": "thigh_r__alt_test_thigh"}
    chosen, _ = ctl.select("sit", "FRONT", {"alternative:test_thigh@v2": {"status": "rejected"}}, allow_pending=True)
    assert chosen == {}, "rejected drawings never show"
    chosen, _ = ctl.select("walk", "FRONT", {}, allow_pending=True, user={"thigh_r": "test_thigh"})
    assert chosen == {"thigh_r": "thigh_r__alt_test_thigh"}, "the user's choice overrides the automatic one"
    assert substitute(["thigh_r", "foot_r", "torso"], chosen)[0] == "thigh_r__alt_test_thigh"
    r = eng.pose("relaxed_standing", visible=vis, evaluate_garments=False, corrective_mode="off",
                 layer_variants=chosen)
    assert "thigh_r__alt_test_thigh" in r["render"]["order"] and "thigh_r" not in r["render"]["order"]
    assert identity_check(rgba, eng.source, eng.masks["thigh_r"])["ok"] is False, "the inverted drawing is off-model"


# -- dynamic layer order ---------------------------------------------------------------------------------------------
@real
def test_layer_order_neutral_unchanged_and_sublayers_partition(ev):
    eng, vis, P = ev
    lo = DynamicLayerOrderController()
    r = eng.pose("relaxed_standing", visible=vis, skeleton_only=False, evaluate_garments=False, corrective_mode="off",
                 layer_order=lo)
    assert r["layer_order"]["changed"] is False, "no rule fires in a neutral pose"
    split = split_by_parts(eng, "upper_arm_r", ["sleeve_cap_r", "blazer_shoulder_r"])
    assert split is not None
    f, b = split
    a0 = eng.layers["upper_arm_r"][..., 3] > 0
    af, ab = eng.variants[f][..., 3] > 0, eng.variants[b][..., 3] > 0
    assert not (af & ab).any() and np.array_equal(af | ab, a0), "the two sublayers partition the layer exactly"
    ap = variant("hand_behind_back")
    rb = render_pose(eng, vis, ap, planner=P, layer_order=DynamicLayerOrderController())
    order = rb["render"]["order"]
    assert order.index("upper_arm_r__back") < order.index("torso") < order.index("upper_arm_r__front")


def test_layer_order_hysteresis_prevents_flicker():
    lo = DynamicLayerOrderController(margin_px=8.0)
    seq = [lo._hyst("x", v) for v in (0, 5, 9, 7, -3, -7, -9, 2, 9)]
    assert seq == [False, False, True, True, True, True, False, False, True]


# -- views, limbs --------------------------------------------------------------------------------------------------
def test_view_manifest_and_limb_configs(tmp_path):
    m = ViewAwareRigController(tmp_path, ["torso"]).manifest()
    assert [v for v, d in m["views"].items() if d["available"]] == ["FRONT"]
    assert "character_sheet" in (m["views"]["SIDE_LEFT"]["source_drawing"] or "")
    doc = limb_config_doc()
    assert doc["verified"] == [] and all(v["status"] == "proposed_unverified" for v in doc["moho"].values())


# -- transitions and the animation evaluator ------------------------------------------------------------------------
def test_transitions_ease_lead_and_hold_hand_switches():
    assert ease(0) == 0 and ease(1) == 1 and 0 < ease(0.3) < 0.3
    seq = ActingSequence(acting_test_keys(), fps=12)
    for k in seq.keys:  # at the end of each hold the pose equals its key
        ap, _ = seq.sample(k.t + k.hold - 1e-6)
        assert params_of(ap)["arm_r_abduction"] == pytest.approx(params_of(k.pose)["arm_r_abduction"], abs=2.5)
    fr = seq.frames()
    hands = [f[1].arms["r"].hand for f in fr]
    runs = [sum(1 for _ in g) for _, g in __import__("itertools").groupby(hands)]
    assert min(runs) >= 3, "hand drawings are held, never flicker"
    a, b = seq.keys[0], seq.keys[1]
    t = a.t + a.hold + 0.3 * (b.t - a.t - a.hold)
    p = params_of(seq.sample(t)[0])
    pa, pb = params_of(a.pose), params_of(b.pose)
    wt = (p["weight_left"] - pa["weight_left"]) / (pb["weight_left"] - pa["weight_left"])
    hd = (p["head"] - pa["head"]) / (pb["head"] - pa["head"])
    assert wt > hd, "weight transfer leads, the head follows through"


def test_transitions_start_and_arrive_without_velocity_jumps():
    """Anticipation and overshoot are C2 bumps: every parameter leaves a hold and enters the next with ~zero speed."""
    assert bump(0) == 0 and bump(1) == 0 and bump(0.5) == pytest.approx(1.0)
    assert ease(1e-3) < 1e-8 and 1 - ease(1 - 1e-3) < 1e-8, "quintic ease: zero velocity and acceleration at the ends"
    seq = ActingSequence(acting_test_keys(), fps=12)
    h = 1e-4
    for i in range(len(seq.keys) - 1):
        a = seq.keys[i]
        t0 = a.t + a.hold  # the move starts
        p0, p1 = params_of(seq.sample(t0 + h)[0]), params_of(seq.sample(t0 + 2 * h)[0])
        for name in p0:
            assert abs(p1[name] - p0[name]) / h < 2.0, f"{a.label}: {name} jumps into motion"
    k = next(kp for kp in seq.keys if kp.overshoot)  # the move carries past the key and settles inside the hold
    j = seq.keys.index(k)
    pk, pprev = params_of(k.pose), params_of(seq.keys[j - 1].pose)
    peak = max(params_of(seq.sample(k.t + d)[0])["arm_r_abduction"] for d in (-0.08, -0.04, 0.0, 0.04))
    assert (peak - pk["arm_r_abduction"]) * np.sign(pk["arm_r_abduction"] - pprev["arm_r_abduction"]) > 0
    end = params_of(seq.sample(k.t + k.hold - 1e-6)[0])["arm_r_abduction"]
    assert end == pytest.approx(pk["arm_r_abduction"], abs=0.05), "settled by the end of the hold"


def test_continuity_check_catches_velocity_jumps_and_solver_kinks():
    E = ArtisticAnimationEvaluator

    def smooth(t):  # a fast eased move: large but smooth accelerations
        return {"abd": 9.0 + 139.0 * ease(min(max((t - 0.5) / 0.6, 0.0), 1.0))}

    def snap(t):  # the same move with a sine anticipation: the velocity jumps at t = 0.5
        u = min(max((t - 0.5) / 0.6, 0.0), 1.0)
        return {"abd": 9.0 + 139.0 * (ease(u) - 0.08 * np.sin(np.pi * u / 0.35) * (u < 0.35))}

    ok, bad = E.continuity_check(smooth, 2.0), E.continuity_check(snap, 2.0)
    assert ok["velocity_jump_count"] == 0
    assert bad["velocity_jump_count"] > 0 and bad["velocity_jumps"][0]["t"] == pytest.approx(0.5, abs=0.01)
    seq = ActingSequence(acting_test_keys(), fps=12)  # the acting test's own parameter curves are smooth
    assert E.continuity_check(lambda t: params_of(seq.sample(t)[0]), seq.duration)["velocity_jump_count"] == 0
    kinks = E.continuity_check(smooth, 1.0, solve_at=lambda t: {"clamps": ["elbow"] if t > 0.6 else [],
                                                                "balance_error_px": 3.0 if t > 0.8 else 0.0})
    assert {e["event"] for e in kinks["solver_events"]} == {"clamp_change", "balance_saturated"}


def test_animation_evaluator_sequence_checks(ev):
    eng, vis, P = ev
    E = ArtisticAnimationEvaluator(eng)
    smooth = [{"joints": {"a": [100 + 2 * k, 50.0]}, "hands": {"r": "relaxed", "l": "relaxed"},
               "garment_area_ratio": {"torso": 1.0}} for k in range(12)]
    res = E.continuity_check(lambda t: {"a": 100 + 24 * t}, 11 / 12)
    assert E.sequence(smooth, 12)["continuity"] == "REVIEW", "no continuity verdict without the continuity check"
    assert E.sequence(smooth, 12, continuity=res)["continuity"] == "PASS"
    flick = [dict(f, hands={"r": "point" if k == 5 else "relaxed", "l": "relaxed"}) for k, f in enumerate(smooth)]
    assert E.sequence(flick, 12, continuity=res)["hand_switch_flicker"], "a one-frame drawing is flagged"
    # garment area: a steady change during a move is motion, a one-frame change is a pop, a switch is separate
    vols = [1.0, 1.0, 0.9, 0.8, 0.7, 0.7, 0.7, 0.85, 0.85, 0.85, 0.85, 0.85]
    var = ["sleeve"] * 9 + ["sleeve+cuff"] * 3
    for k in range(9, 12):
        vols[k] = 0.95
    fr = [dict(f, garment_area_ratio={"upper_arm_r": vols[k]}, garment_variants={"upper_arm_r": var[k]})
          for k, f in enumerate(smooth)]
    q = E.sequence(fr, 12, continuity=res)
    assert [j["frame"] for j in q["garment_volume_jumps"]] == [7], "the one-frame pop at frame 7"
    assert [j["frame"] for j in q["garment_volume_motion"]] == [2, 3, 4]
    assert [j["frame"] for j in q["drawing_switch_area_changes"]] == [9]
    assert q["continuity"] == "REVIEW"


@real
def test_frame_evaluation_keeps_proportions(ev):
    eng, vis, P = ev
    r = render_pose(eng, vis, variant("shrug"), planner=P)
    f = ArtisticAnimationEvaluator(eng).frame(r, "shrug", {}, {"r": "shrugging", "l": "shrugging"})
    assert f["max_bone_length_change"] < 0.01, "bones are never scaled"
    assert f["artistic_review_required"] is True


# -- art-directed joints, shoulder, protection ---------------------------------------------------------------------
@real
def test_joint_folds_supersede_caps_and_never_leak(ev):
    from autorig.garmentrig.corrective_evidence import CorrectiveQualityEvaluator
    from autorig.garmentrig.evidence import fk_state
    eng, vis, P = ev
    kw = {"ik": fk_state({"r": (15.0, 100.0)})}
    off = eng.pose("relaxed_standing", visible=vis, evaluate_garments=False, corrective_mode="off", **kw)
    on = eng.pose("relaxed_standing", visible=vis, evaluate_garments=False, corrective_mode="all",
                  corrective_overrides={"edge_decontamination": False}, **kw)
    rep = {c["asset_id"]: c for c in on["correctives"]}
    assert rep["elbow_fold_r"]["weight"] > 0.5 and "superseded" in rep["elbow_cap_r"]["flags"]
    info = on["render"]["joint_fold_info"]["elbow_fold_r"]
    assert info["folds"] == 2 and info["fill_px"] > 200 and info["notch_px"] >= 0
    q = CorrectiveQualityEvaluator().evaluate(off, on, None)
    assert q["checks"]["B_leakage"]["protected_violation_px"] == 0
    assert q["checks"]["B_leakage"]["fold_overlay_px"] > 0, "the fold strokes are reported as an overlay"


def test_close_notches_repairs_narrow_gaps_only():
    from autorig.artistic.joint_art import close_notches
    solid = np.zeros((60, 80), bool)
    solid[20:40, 5:38] = solid[20:40, 41:75] = True  # a 3 px notch (columns 38-40) between two pieces
    allowed, near = np.ones_like(solid), np.ones_like(solid)
    n = close_notches(solid, allowed, near, 4)
    assert n[25:35, 38:41].all(), "the narrow notch is closed"
    assert not (n & solid).any()
    wide = np.zeros_like(solid)
    wide[20:40, 5:25] = wide[20:40, 50:75] = True  # a 25 px gap is a real gap, not a notch
    assert not close_notches(wide, allowed, near, 4).any()
    assert not close_notches(solid, np.zeros_like(solid), near, 4).any(), "never outside the allowed pixels"


@real
def test_artistic_poses_never_change_original_pixels(ev):
    from autorig.garmentrig.corrective_evidence import CorrectiveQualityEvaluator
    eng, vis, P = ev
    for action, v in (("point_right", "bent_elbow"), ("squat", "default")):
        ap = variant(action, v)
        off = render_pose(eng, vis, ap, planner=P, corrective_mode="off")
        on = render_pose(eng, vis, ap, planner=P, extra={"corrective_overrides": {"edge_decontamination": False}})
        assert CorrectiveQualityEvaluator().evaluate(off, on, None)["checks"]["B_leakage"]["protected_violation_px"] == 0


@real
def test_shoulder_controller_caps_the_armpit_bulge(ev):
    from autorig.garmentrig.evidence import fk_state
    eng, vis, P = ev
    a = next(x for x in eng.correctives.assets if x.asset_id == "armpit_gusset_r")
    assert a.version == 2 and a.params["shoulder_controller"] and a.params["crease"]
    r = eng.pose("relaxed_standing", visible=vis, evaluate_garments=False, corrective_mode="all",
                 ik=fk_state({"r": (130.0, 5.0)}))
    info = r["render"]["gap_fill_info"]["armpit_gusset_r"]
    T, S, C = (np.array(info[k]) for k in ("T", "S", "C"))
    bulge = np.hypot(*(C - (T + S) / 2)) / max(info["chord_px"], 1)
    assert not info["convex"] or bulge <= 0.10, "outward bulge capped at 6 % (+ up to 12 % near vertical)"


@real
def test_artistic_render_is_deterministic(ev):
    eng, vis, P = ev
    a = render_pose(eng, vis, variant("point_left"), planner=P)
    b = render_pose(eng, vis, variant("point_left"), planner=P)
    assert np.array_equal(a["image"], b["image"])


# -- feedback, editor ops, API ---------------------------------------------------------------------------------------
def test_feedback_phrases_become_constraints():
    r = parse_feedback("Move the hand farther from the torso", "point_right")
    assert r["constraints"] == [{"param": "arm_r_abduction", "delta": 5.0, "action": "point_right",
                                 "source_text": "Move the hand farther from the torso"}]
    r = parse_feedback("Shoulder looks too large on the left", "shrug")
    assert r["constraints"][0]["param"] == "clavicle_l" and r["constraints"][0]["delta"] < 0
    r = parse_feedback("Elbow looks too round", "deep_elbow")
    assert r["constraints"][0]["kind"] == "corrective" and r["constraints"][0]["asset"] == "elbow_fold_r"
    r = parse_feedback("Make the running stance less stiff", "run")
    assert {c["param"] for c in r["constraints"]} == {"contrapposto", "exaggeration"}
    assert parse_feedback("I like the colour of the tie", "")["understood"] is False


def test_artistic_project_ops_and_migration():
    from autorig.editor.project import OpError, _apply_artistic, default_artistic_state, migrate
    st = {"artistic": default_artistic_state()}
    _apply_artistic(st, {"type": "artistic.select", "action": "point_right", "variant": "bent_elbow"})
    _apply_artistic(st, {"type": "artistic.feedback", "action": "point_right", "text": "hand out",
                         "constraints": [{"param": "arm_r_abduction", "delta": 5.0, "action": "point_right"}]})
    _apply_artistic(st, {"type": "artistic.template.save", "name": "my point", "pose": variant("point_right").to_json()})
    _apply_artistic(st, {"type": "artistic.cycle.revise", "cycle": "walk", "note": "arms too stiff"})
    _apply_artistic(st, {"type": "artistic.layer_rule", "rule": {"id": "r1", "move": ["hair"], "target": ["head_base"],
                                                                "relation": "front"}})
    A = st["artistic"]
    assert A["selections"]["point_right"] == {"variant": "bent_elbow"} and len(A["constraints"]) == 1
    assert A["templates"][0]["name"] == "my point" and A["cycle_revisions"][0]["status"] == "needs_revision"
    for bad in ({"type": "artistic.select"}, {"type": "artistic.template.save", "name": "x", "pose": {}},
                {"type": "artistic.layer_rule", "rule": {"id": "r", "move": ["a"], "target": ["b"], "relation": "up"}},
                {"type": "artistic.constraint.remove", "index": 9}):
        with pytest.raises(OpError):
            _apply_artistic(st, bad)
    assert migrate({"schema_version": 6, "ops": []})["schema_version"] == 7


@real
def test_http_artistic_api(ev, pkg):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import warnings

    from fastapi.testclient import TestClient

    from autorig.editor.server import create_app
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        c = TestClient(create_app(pkg))
    info = c.get("/api/artistic").json()
    assert "point_right" in info["actions"] and info["selections"] == {}
    r = c.post("/api/artistic/candidates", json={"action": "weight_shift"}).json()
    assert len(r["candidates"]) >= 3 and r["candidates"][0]["image"].startswith("data:image/png")
    assert all(x["state"] == "pending_review" for x in r["candidates"])
    key = r["candidates"][0]["approval_key"]
    assert c.post("/api/op", json={"type": "approval.set", "item": key, "status": "approved"}).status_code == 200
    assert c.post("/api/op", json={"type": "artistic.select", "action": "weight_shift",
                                   "variant": r["candidates"][0]["variant"]}).status_code == 200
    r2 = c.post("/api/artistic/candidates", json={"action": "weight_shift"}).json()
    assert next(x for x in r2["candidates"] if x["approval_key"] == key)["state"] == "approved"
    assert c.post("/api/artistic/candidates", json={"action": "nope"}).status_code == 400
    p = c.post("/api/artistic/feedback/parse", json={"text": "arm higher", "action": "arm_raised"}).json()
    assert p["understood"] and p["constraints"][0]["param"] == "arm_r_abduction"
    assert c.post("/api/undo").status_code == 200
