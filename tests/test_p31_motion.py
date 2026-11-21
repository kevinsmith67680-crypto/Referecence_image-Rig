"""Phase 3.1 natural motion: planner, skeletal solver, gait cycles, garment deformation, feasibility, quality.

Runs on a copy of the user's real T-pose package (real artwork). Preview-renderer checks only: Moho is not run.
"""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

import numpy as np
import pytest

from autorig.motion.feasibility import NEEDS_ARTWORK, UNSUPPORTED, WITH_CORRECTIONS, PoseFeasibilityEvaluator
from autorig.motion.garments import RID
from autorig.motion.planner import SPEC_BY_NAME, PresetSpec, gait
from autorig.motion.quality import THRESHOLDS
from autorig.phase2.skeleton import SIDES
from autorig.phase2.skinning import PoseParams

ROOT = Path(__file__).resolve().parents[1]
REAL_PKG = ROOT / "projects" / "schoolboy_tpose" / "character_package"
pytestmark = pytest.mark.skipif(not (REAL_PKG / "natural_pose" / "rig.json").is_file(),
                                reason="real T-pose character package not present")
STATIC = ["relaxed_standing", "hands_at_sides", "hands_on_hips", "pointing_speaking", "conversational_gesture",
          "one_hand_shrug", "both_hands_raised", "head_tilt_compensated", "natural_squat", "side_lean"]


def _ignore(d, names):
    skip = {"editor", "motion"}
    if Path(d).name == "natural_pose":
        skip |= {"poses", "legacy", "closeups", "contact_sheet.png", "contact_sheet_diagnostic.png"}
    return [n for n in names if n in skip]


@pytest.fixture(scope="module")
def eng(tmp_path_factory):
    from autorig.motion.pipeline import engine_for_package
    dst = tmp_path_factory.mktemp("p31") / "pkg"
    shutil.copytree(REAL_PKG, dst, ignore=_ignore)
    return engine_for_package(dst)


def test_profile_comes_from_this_characters_art(eng):
    e, _ = eng
    p = e.profile
    assert p.view == "front"
    # big cartoon shoes: the no-overlap track is wider than the hip joints (the Phase 3 running used 0.9 x hips)
    assert p.track_min > p.hip_sep
    assert p.track_min == pytest.approx(p.shoe["r"]["medial"] + p.shoe["l"]["medial"] + 6.0)
    assert p.stance_natural >= p.track_min and p.track_run >= p.track_min


@pytest.mark.parametrize("name", STATIC)
def test_static_presets_keep_lengths_contacts_and_limits(eng, name):
    e, _ = eng
    it, pose, info = e.plan_and_solve(name)
    assert np.all(pose.scale == 1.0), "no axial scaling, ever"
    for b in e.sk.bones:
        L = np.hypot(*np.subtract(info["bone_tails_posed"][b.name], info["joints_posed"][b.name]))
        assert L == pytest.approx(b.length, abs=0.02)  # joints are reported to 0.01 px
    for s in SIDES:
        if info["contact"][s]:
            assert info["legs"][s]["error_px"] <= 1.0
            assert abs(info["anatomical"][f"foot_{s}_world"]) <= 3.0
    assert not info["violations"], info["violations"]
    sep = abs(info["joints_posed"]["foot_l"][0] - info["joints_posed"]["foot_r"][0])
    assert sep >= e.profile.track_min - 1.0, "feet never closer than the shoes allow (no crossing)"


def test_static_balance_puts_the_com_over_the_feet(eng):
    e, _ = eng
    for name in ("relaxed_standing", "pointing_speaking", "conversational_gesture", "natural_squat"):
        _, _, info = e.plan_and_solve(name)
        lo, hi = info["support"]["x_range"]
        assert lo <= info["com"][0] <= hi, name


@pytest.mark.parametrize("kind,travel", [("run", 0.0), ("run", 40.0), ("walk", 0.0), ("walk", 40.0)])
def test_gait_cycles_pass_every_cycle_check(eng, kind, travel):
    e, _ = eng
    c = e.cycle("running" if kind == "run" else "casual_walk", 64, travel=travel)
    assert c["numerical"]["all_pass"], [x for x in c["checks"] if not x["pass"]]
    names = {x["check"] for x in c["checks"]}
    assert {"loop_closure_px", "continuity_spike_px", "foot_slide_px", "gait_ground_penetration_px",
            "coordination_corr"} <= names


def test_run_cycle_phases_flight_and_no_crossing(eng):
    e, _ = eng
    L = (e.profile.leg("r") + e.profile.leg("l")) / 2
    pelvis, airborne = [], 0
    for i in range(64):
        it = gait(e.profile, "run", i / 64)
        pose, info = e.solver.solve(it)
        assert np.all(pose.scale == 1.0)
        pelvis.append(info["joints_posed"]["pelvis"][1])
        airborne += not any(info["contact"].values())
        sep = abs(info["joints_posed"]["foot_l"][0] - info["joints_posed"]["foot_r"][0])
        assert sep >= e.profile.track_min - 1.0
        if i == 0:
            assert info["contact"]["r"] and not info["contact"]["l"], "right-foot contact at phase 0"
    assert airborne >= 8, "the run has flight phases"
    assert np.ptp(pelvis) >= 0.03 * L, "visible vertical oscillation"
    names = {gait(e.profile, "run", i / 16).channels["phase_name"] for i in range(16)}
    assert {"contact", "passing", "flight"} <= names


def test_walk_has_double_support_and_no_flight(eng):
    e, _ = eng
    both = sum(all(gait(e.profile, "walk", i / 32).contact.values()) for i in range(32))
    none = sum(not any(gait(e.profile, "walk", i / 32).contact.values()) for i in range(32))
    assert both > 0 and none == 0


def test_speed_and_exaggeration_change_the_cycle(eng):
    e, _ = eng
    slow = e.cycle("running", 64, speed=0.7, exaggeration=0.6)
    fast = e.cycle("running", 64, speed=1.4, exaggeration=1.3)
    assert fast["pelvis_vertical_range_px"] > slow["pelvis_vertical_range_px"]
    assert slow["numerical"]["all_pass"] and fast["numerical"]["all_pass"]


def test_blazer_hem_hangs_from_the_pelvis_not_the_thighs(eng):
    e, _ = eng
    g = e.garments
    assert g.hem_override.sum() > 1000, "hem trim cut into the thigh layers is found"
    assert (g.labels == RID["blazer_hem"]).sum() > 10000
    inf = e.dfm.info["thigh_r"]
    x0, y0 = inf["origin"]
    ys, xs = np.nonzero(g.hem_override[y0:y0 + inf["alpha"].shape[0], x0:x0 + inf["alpha"].shape[1]] & inf["alpha0"])
    assert len(ys)
    pts = np.stack([xs + x0, ys + y0], 1).astype(float)[:50]
    # Phase 3.3: the hem pixels are the 'blazer_hem' garment part, weighted to the pelvis only (no thigh, no shin)
    assert {e.structure.parts[i].name for i in e.structure.ids_at("thigh_r", pts)} == {"blazer_hem"}
    for b in (e.sk.idx["thigh_r"], e.sk.idx["shin_r"]):
        assert not inf["wm"].influence(b, pts).any()
    # rotate only the right thigh: hem pixels in that layer stay put
    N = len(e.sk.bones)
    th = np.zeros(N)
    th[e.sk.idx["thigh_r"]] = 15.0
    moved = e.dfm.forward("thigh_r", pts, PoseParams(th, np.ones(N), np.zeros(2)))
    assert np.abs(moved - pts).max() < 1e-6


def test_identity_pose_is_bit_exact_with_the_garment_engine(eng):
    e, vis = eng
    N = len(e.sk.bones)
    order = [c for c in vis]
    corr = e.gdef.corrections(PoseParams.identity(N), {})
    r = e.dfm.render_with(PoseParams.identity(N), order, corr, pad=0)
    for c in order:
        inf = e.dfm.info[c]
        x0, y0 = inf["origin"]
        a = r["alphas"][c][y0:y0 + inf["alpha"].shape[0], x0:x0 + inf["alpha"].shape[1]]
        assert np.array_equal(a, inf["pre"][..., 3] > 0.5)


def test_rotation_dependent_corrections_scale_with_the_joint(eng):
    e, _ = eng
    _, p_relax, _ = e.plan_and_solve("relaxed_standing")
    _, p_squat, _ = e.plan_and_solve("natural_squat")
    i = e.sk.idx["shin_r"]
    # the band widening belongs to the legacy deformer (garment_mode="legacy"); the structured deformer keeps
    # fixed narrow transitions and draws rigid segments instead, so it reports no band rules
    assert e.garment_mode == "structured" and not e.gdef.bands
    assert not any(r["rule"].startswith("band:") for r in e.gdef.corrections(p_squat, {})["active"])
    from autorig.motion.garments import GarmentDeformationEngine
    legacy = GarmentDeformationEngine(e.gsk, e.garments, bands=True)
    c0 = legacy.corrections(p_relax, {})
    c1 = legacy.corrections(p_squat, {})
    assert c1["rc"][i] > c0["rc"][i] >= e.gsk.base_rc[i]
    assert any(r["rule"] == "band:shin_r" for r in c1["active"])
    lag = e.gdef.corrections(p_relax, {"hem_lag_deg": 2.0})
    assert any(t.name == "blazer_hem_lag" for t in lag["terms"])


def test_feasibility_never_treats_pending_art_as_approved(eng):
    e, vis = eng
    run_open = e.pose("running", 0.25, visible=vis)
    assert run_open["feasibility"]["status"] == WITH_CORRECTIONS
    assert all(h["drawing"] == "open" for h in run_open["hands"].values()), "approved open-hand running style"
    run_fist = e.pose("running", 0.25, visible=vis, allow_pending=True)
    assert run_fist["feasibility"]["status"] == NEEDS_ARTWORK
    assert any(m["asset"] == "hand:fist" and m["state"] == "pending_review" for m in run_fist["feasibility"]["missing"])
    assert any("pending-review" in r for r in run_fist["quality"]["review_items"])
    point = e.pose("pointing_speaking", visible=vis)
    assert point["feasibility"]["status"] == NEEDS_ARTWORK
    assert point["feasibility"]["alternative"] == "conversational_gesture"
    for name, fb in (("sitting", "natural_squat"), ("leaning_forward", "side_lean")):
        f = e.feas.evaluate(SPEC_BY_NAME[name], e.planner.plan(name), {s: {"drawing": "open"} for s in SIDES}, [])
        assert f["status"] == NEEDS_ARTWORK and f["alternative"] == fb
    side = PresetSpec("side_run", "Side-view run", "", "gait", lambda p, **_: None, "", "", "", "", view="side")
    assert e.feas.evaluate(side, e.planner.plan("running", 0.0), {}, [])["status"] == UNSUPPORTED
    # approving the fists (as the user would in the editor) unlocks the fist style
    inv = e.inventory
    saved = {s: dict(inv.hands[s]) for s in SIDES}
    try:
        for s in SIDES:
            inv.hands[s]["fist"] = "approved"
        f2 = e.pose("running", 0.25, visible=vis)
        assert f2["feasibility"]["status"] == WITH_CORRECTIONS and f2["hands"]["r"]["drawing"] == "fist"
    finally:
        for s in SIDES:
            inv.hands[s] = saved[s]


def test_rendered_run_frame_metrics(eng):
    e, vis = eng
    r = e.pose("running", 0.375, visible=vis)
    checks = {c["check"]: c for c in r["quality"]["checks"]}
    assert set(checks) <= set(THRESHOLDS)
    for k in ("bone_length_rel", "axial_scale", "joint_limit_violations", "ground_penetration_px",
              "shoe_overlap_px", "exposed_gap_pairs", "outline_break_pairs"):
        assert checks[k]["pass"], (k, checks[k])
    assert r["quality"]["artistic_review_required"] is True
    assert r["quality"]["visual_quality"].startswith("UNRATED")
    old = e.legacy("running")
    ra, la = old["render"]["alphas"]["foot_r"], old["render"]["alphas"]["foot_l"]
    assert (ra & la).sum() > 1000, "the Phase 3 running pose crossed the legs (regression baseline)"
    assert np.any(old["pose"].scale != 1.0), "the Phase 3 running pose used axial scaling"


def test_live_head_composes_without_touching_the_body(eng):
    e, _ = eng
    _, _, body = e.plan_and_solve("running", 0.25)
    _, _, live = e.plan_and_solve("running", 0.25, live_head={"tilt": 10.0, "turn": 15.0})
    for n in body["joints_posed"]:
        if n in ("head",):
            continue
        assert np.allclose(body["joints_posed"][n], live["joints_posed"][n]), n
    assert body["bone_tails_posed"]["head"] != live["bone_tails_posed"]["head"]
    assert live["live_head"]["tilt_applied"] == pytest.approx(10.0)
    assert "not applied" in live["live_head"]["turn"]


def test_thresholds_are_documented():
    for k, (v, meaning) in THRESHOLDS.items():
        assert isinstance(v, float) and meaning
    report = ROOT / "docs" / "phase3_1_natural_motion_report.md"
    if report.is_file():
        text = report.read_text()
        assert all(k in text for k in THRESHOLDS), [k for k in THRESHOLDS if k not in text]


def test_committed_motion_evidence_is_consistent():
    out = REAL_PKG / "natural_pose" / "motion"
    if not (out / "results.json").is_file():
        pytest.skip("motion evidence not generated")
    r = json.loads((out / "results.json").read_text())
    assert {"running@0.000", "running@0.875", "casual_walk@0.250", "relaxed_standing"} <= set(r["presets"])
    for k, v in r["presets"].items():
        assert v["quality"]["artistic_review_required"] is True
        assert v["feasibility"]["status"] in ("SUPPORTED", "SUPPORTED_WITH_CORRECTIONS", "NEEDS_ARTWORK", "UNSUPPORTED")
    assert r["presets"]["running@0.000"]["feasibility"]["status"] == "SUPPORTED_WITH_CORRECTIONS"
    assert r["presets"]["running_fists_preview@0.000"]["feasibility"]["status"] == "NEEDS_ARTWORK"
    assert all(c["numerical"]["all_pass"] for k, c in r["cycles"].items() if not k.endswith("_plain"))
    assert not r["cycles"]["running_plain"]["numerical"]["all_pass"], "plain cycles keep their lockstep arms (baseline)"
    for f in ("contact_sheet.jpg", "run_cycle.jpg", "before_after.jpg", "diagnostics_running.jpg",
              "garment_regions.png", "profile.json", "garments.json", "hand_paths.png",
              "animations/run_fluid.gif", "animations/run_plain_vs_fluid.gif", "animations/walk_fluid.gif"):
        assert (out / f).is_file(), f


# -- fluid motion: overlap, follow-through, timing, arcs, animated cycles ------------------------------------------
def test_spring_response_is_periodic_and_lags():
    from autorig.motion.secondary import SAMPLES, Spring, chain_lag
    t = np.arange(SAMPLES) / SAMPLES
    x = np.sin(2 * np.pi * t)
    y = Spring(3.6, 0.5).response(x, 0.75)
    assert np.isclose(y[0], Spring(3.6, 0.5).response(np.roll(x, 5), 0.75)[5])  # shift-invariant, periodic
    lag = chain_lag(x, y)
    assert 0.0 < lag < 0.25, lag  # follows, does not lead
    assert 0.8 < np.ptp(y) / np.ptp(x) < 1.6  # below resonance: similar amplitude, slight overshoot allowed
    assert chain_lag(x, x) == 0.0


@pytest.mark.parametrize("name", ["running", "casual_walk"])
def test_fluid_cycles_add_overlap_and_arcs_and_pass_every_check(eng, name):
    e, _ = eng
    fluid = e.cycle(name, 64, fluid=True)
    plain = e.cycle(name, 64, fluid=False)
    assert fluid["numerical"]["all_pass"], [c for c in fluid["checks"] if not c["pass"]]
    f = {c["check"]: c["value"] for c in fluid["checks"]}
    p = {c["check"]: c["value"] for c in plain["checks"]}
    assert p["arm_overlap_cycles"] == 0.0, "the plain cycle moves the arm joints in lockstep"
    assert f["arm_overlap_cycles"] >= 0.03
    assert f["hand_arc_quotient"] > p["hand_arc_quotient"]
    assert f["jerk_ratio"] <= p["jerk_ratio"] + 1e-6
    assert f["foot_slide_px"] <= 1.0 and f["loop_closure_px"] <= 0.5


def test_fluid_frame_has_follow_through_terms_and_clean_render(eng):
    e, vis = eng
    r = e.pose("running", 0.375, visible=vis)
    rules = {c["rule"] for c in r["corrections"]}
    assert {"secondary:blazer_hem_lag", "secondary:hair_sway"} <= rules
    checks = {c["check"]: c for c in r["quality"]["checks"]}
    for k in ("exposed_gap_pairs", "outline_break_pairs", "stretched_fraction", "shoe_overlap_px", "axial_scale"):
        assert checks[k]["pass"], (k, checks[k])
    plain = e.plan_and_solve("running", 0.375, fluid=False)[2]
    fl = e.plan_and_solve("running", 0.375, fluid=True)[2]
    assert plain["joints_posed"]["hand_r"] != fl["joints_posed"]["hand_r"]
    for s in SIDES:  # feet and contacts are the same: follow-through never moves a planted foot
        assert plain["contact"][s] == fl["contact"][s]
        if plain["contact"][s]:
            assert np.allclose(plain["joints_posed"][f"foot_{s}"], fl["joints_posed"][f"foot_{s}"], atol=1.0)


def test_animated_cycle_export(eng, tmp_path):
    from PIL import Image as PILImage
    from autorig.motion.pipeline import render_cycle, save_animation
    e, vis = eng
    res = render_cycle(e, "running", 4, vis)
    assert len(res["frames"]) == 4 and res["duration_ms"] == pytest.approx(750 / 4, abs=0.1)
    assert all(f.shape == res["frames"][0].shape for f in res["frames"])
    for ext in ("gif", "webp"):
        save_animation(res["frames"], res["duration_ms"], tmp_path / f"c.{ext}", scale=0.25)
        with PILImage.open(tmp_path / f"c.{ext}") as im:
            assert im.n_frames == 4 and im.info.get("loop", 0) == 0
