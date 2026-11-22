"""Phase 3.6 performance engine:
- timing curves;
- key-pose sequencing;
- anticipation and overlap;
- weight transfer;
- contacts;
- secondary motion;
- motion layers, styles, gestures and dialogue;
- the director;
- quality evaluation;
- export and live specs;
- project ops and the HTTP API.

Tests that need the real schoolboy package skip when it is missing. These tests check mechanics and constraints;
they do not show that the animation is artistically good (only a person can approve that).
"""

from __future__ import annotations

import copy
import json
import math
import shutil
from pathlib import Path

import numpy as np
import pytest

from autorig.artistic.evaluate import ArtisticAnimationEvaluator
from autorig.artistic.transitions import acting_test_keys
from autorig.performance.anticipation import ABS_CAP, MAX_FRACTION, AnticipationAndFollowThroughController
from autorig.performance.contacts import ContactConstraintController, ContactSpec
from autorig.performance.curves import Curve, MonotoneHermite, bezier_value, ease, soft_clamp
from autorig.performance.dialogue import DialogueActingController, DialogueEvent
from autorig.performance.director import Performance, PerformanceDirector
from autorig.performance.evaluate import PerformanceQualityEvaluator
from autorig.performance.export import COMPAT_CLASSES, animation_metadata, compatibility, live_control_spec
from autorig.performance.gestures import GesturePerformanceLibrary
from autorig.performance.layers import FACIAL_CHANNELS, Clip, MotionLayerController
from autorig.performance.overlap import MAX_OFFSET, MotionOverlapController
from autorig.performance.resolver import ArtworkAndCorrectionResolver
from autorig.performance import scenarios
from autorig.performance.secondary import SecondaryMotionController, merged_settings
from autorig.performance.seat import SeatAndInteractionController, Seat
from autorig.performance.sequencer import Key, KeyPoseSequencer
from autorig.performance.styles import PROFILES, style
from autorig.performance.weight import StepEvent

ROOT = Path(__file__).resolve().parents[1]
REAL_PKG = ROOT / "projects" / "schoolboy_tpose" / "character_package"
real = pytest.mark.skipif(not (REAL_PKG / "natural_pose" / "rig.json").is_file(), reason="real package missing")


@pytest.fixture(scope="module")
def ev():
    from autorig.garmentrig.correctives import CorrectiveArtworkStore, CorrectiveRuntime
    from autorig.motion.pipeline import engine_for_package
    eng, vis = engine_for_package(REAL_PKG)
    eng.correctives = CorrectiveRuntime(eng, CorrectiveArtworkStore(REAL_PKG))
    return eng, vis


def _vel(f, t, h=1e-4):
    return (f(t + h) - f(t - h)) / (2 * h)


# -- ANIMATION CURVES ---------------------------------------------------------------------------------------------
def test_curves_ends_monotonicity_and_hold():
    for k in ("linear", "ease_in", "ease_out", "ease_in_out", "ease_in_out_cubic"):
        c = Curve(k)
        assert c(0) == 0 and c(1) == 1
        assert all(c(a) <= c(b) + 1e-12 for a, b in zip(np.linspace(0, 1, 50), np.linspace(0, 1, 50)[1:]))
    e = Curve("ease_in_out")
    assert _vel(e, 1e-3) < 1e-4 and _vel(e, 1 - 1e-3) < 1e-4, "quintic: zero velocity at both ends"
    assert Curve("hold")(0.999) == 0 and Curve("hold")(1.0) == 1 and Curve("hold").stepped
    b = Curve("bezier", [0.3, -0.2, 0.6, 1.25])
    vals = [b(u) for u in np.linspace(0, 1, 101)]
    assert max(vals) > 1.0 and min(vals) < 0.0, "authored Bezier overshoot and anticipation are possible"
    assert abs(bezier_value(0.5, 0.25, 0.25, 0.75, 0.75) - 0.5) < 1e-9
    c = Curve("custom", [0.3, 0.5, 0.7, 0.8])
    vals = [c(u) for u in np.linspace(0, 1, 201)]
    assert all(b2 >= a2 - 1e-12 for a2, b2 in zip(vals, vals[1:])) and max(vals) <= 1.0 + 1e-12, "monotone, no overshoot"
    with pytest.raises(ValueError):
        Curve("bezier", [1.5, 0, 0.5, 1])
    with pytest.raises(ValueError):
        Curve("wobble")
    assert Curve.from_json(Curve("bezier", [0.2, 0, 0.8, 1]).to_json()).params == [0.2, 0, 0.8, 1]


def test_monotone_hermite_and_soft_clamp():
    h = MonotoneHermite([0, 1, 2, 3], [0, 10, 10, 30])
    xs = np.linspace(0, 3, 301)
    ys = [h(x) for x in xs]
    assert all(b >= a - 1e-9 for a, b in zip(ys, ys[1:])) and max(ys[100:200]) <= 10 + 1e-9, "no overshoot between keys"
    assert abs(_vel(h, 1.0 - 1e-3) - _vel(h, 1.0 + 1e-3)) < 0.05, "C1 through interior points"
    xs = np.linspace(150, 220, 300)
    ys = [soft_clamp(x, -40, 178, 8) for x in xs]
    assert max(ys) < 178 and all(b >= a for a, b in zip(ys, ys[1:])), "never crosses the limit, keeps monotone"
    assert soft_clamp(100, -40, 178, 8) == 100


def _arm_keys():
    return [Key(0.0, {"arm_r_abduction": 10.0, "line_of_action": 0.0}, {"r": "relaxed"}, "rest", 0.5, id="a"),
            Key(1.4, {"arm_r_abduction": 140.0, "line_of_action": 4.0}, {"r": "open_palm"}, "up", 0.4,
                anticipation="raise", side="r", overshoot=0.06, id="b"),
            Key(2.4, {"arm_r_abduction": 60.0, "line_of_action": -2.0}, {"r": "pointing"}, "pass", 0.0, interp="path",
                id="c"),
            Key(3.1, {"arm_r_abduction": 20.0, "line_of_action": 0.0}, {"r": "relaxed"}, "down", 0.5, id="d")]


def test_sequencer_velocity_continuity_at_segment_boundaries():
    S = KeyPoseSequencer(_arm_keys())
    f = lambda t: S.value("arm_r_abduction", t)  # noqa: E731
    for tb in (0.5, 1.4, 1.8, 2.4, 3.1):  # move starts, arrivals, hold ends, the pass-through key
        assert abs(_vel(f, tb - 2e-3) - _vel(f, tb + 2e-3)) < 3.0, f"velocity jump at {tb}"
    assert abs(_vel(f, 0.5 + 1e-3)) < 2.0, "the anticipation starts at rest (C2 bump, not a sine)"
    assert abs(_vel(f, 2.4)) > 20, "a path key keeps its velocity (no stop)"
    res = ArtisticAnimationEvaluator.continuity_check(lambda t: {"a": f(t)}, S.duration)
    assert res["velocity_jump_count"] == 0


def test_sequencer_anticipation_overshoot_hold_and_hands():
    S = KeyPoseSequencer(_arm_keys())
    v = [S.value("arm_r_abduction", t) for t in np.linspace(0.5, 0.75, 30)]
    assert min(v) < 10.0, "the arm dips before it rises"
    assert 10.0 - min(v) <= min(MAX_FRACTION * 130.0, ABS_CAP["arms"]) + 1e-9, "anticipation is capped"
    peak = max(S.value("arm_r_abduction", t) for t in np.linspace(1.2, 1.7, 60))
    assert 140.0 < peak <= 140.0 + 0.06 * 130.0 + 1e-6, "controlled overshoot past the key"
    assert abs(S.value("arm_r_abduction", 1.4 + 0.4 - 1e-6) - 140.0) < 1e-3, "settled by the end of the hold"
    assert S.hands(0.2) == {"r": "relaxed"} and S.hands(1.6) == {"r": "open_palm"}
    off = KeyPoseSequencer([Key(0, {"x": 0}), Key(1, {"x": 10}, anticipation=None)])
    assert all(off.value("x", t) >= -1e-12 for t in np.linspace(0, 1, 50)), "no anticipation without a recipe"


def test_frame_rate_independence_and_order_independence():
    S = KeyPoseSequencer(_arm_keys())
    a = {round(t, 6): S.value("arm_r_abduction", t) for t in np.arange(0, 3.6, 1 / 12)}
    b = {round(t, 6): S.value("arm_r_abduction", t) for t in np.arange(0, 3.6, 1 / 24)}
    assert all(abs(a[k] - b[k]) < 1e-12 for k in a if k in b)
    rev = [S.value("arm_r_abduction", t) for t in reversed(list(np.arange(0, 3.6, 1 / 12)))][::-1]
    assert np.allclose(rev, [S.value("arm_r_abduction", t) for t in np.arange(0, 3.6, 1 / 12)])


def test_overlap_offsets_are_bounded_and_keep_key_poses():
    O = MotionOverlapController({"head": -0.9, "pelvis": 0.2})
    assert O.offsets["head"] == -MAX_OFFSET and O.offsets["pelvis"] == 0.2
    with pytest.raises(ValueError):
        O.set("tail", 0.1)
    assert O.warp(1.0, 0.2) == 1.0 and O.warp(1.0, -0.2) == 1.0, "every group arrives on the key"
    assert O.warp(0.5, 0.2) > O.warp(0.5, 0.0) > O.warp(0.5, -0.2), "leads are ahead, trails behind"
    assert O.offset("head", head_lead=True) > 0 and O.offset("head") < 0


def test_anticipation_controller_recipes_and_disable():
    A = AnticipationAndFollowThroughController()
    assert A.anticipation_term("point", "r", "arm_r_abduction", "arms", 90.0, 0.16, 1.0) < 0
    assert A.anticipation_term("point", "r", "arm_r_abduction", "arms", 90.0, 0.16, 0.0) == 0, "intensity 0 disables"
    assert A.anticipation_term("none", "r", "arm_r_abduction", "arms", 90.0, 0.16, 1.0) == 0
    assert A.anticipation_term("point", "r", "arm_l_abduction", "arms", 90.0, 0.16, 1.0) == 0, "only the gesture side"
    assert not A.recipe("look")["anticipation"], "attention moves lead with the head instead"


# -- CONTACTS -------------------------------------------------------------------------------------------------------
def test_contact_spec_weights_priority_and_targets():
    c = ContactSpec("d", "hand_desk", "hand_r", {"point": [100, 200]}, 1.0, 2.0, acquire=0.4, release=0.5)
    assert c.weight(0.5) == 0 and c.weight(1.0) == 1 and c.weight(2.0) == 1 and c.weight(2.6) == 0
    assert 0 < c.weight(0.8) < 1 and c.chain == "arm_r"
    hi = ContactSpec("o", "hand_object", "hand_r", {"keys": [[0, 0, 0], [2, 20, 10]]}, 0.0, 3.0, priority=50)
    C = ContactConstraintController([c, hi])
    act = C.active(1.5, "arm_r")
    assert act[0][0].id == "o" and len(act) == 1, "the higher priority contact takes the chain"
    assert np.allclose(C.point(hi, 2.0), [20, 10]) and np.allclose(C.point(hi, 0.0), [0, 0])
    for bad in ({"kind": "hand_flying"}, {"part": "tail"}, {"start": 2.0, "end": 1.0}, {"strength": 2.0}):
        with pytest.raises(ValueError):
            ContactSpec(**{**dict(id="x", kind="hand_desk", part="hand_r", target={"point": [0, 0]}, start=0, end=1),
                           **bad})


@real
def test_hand_to_desk_contact_holds_blends_and_releases(ev):
    eng, vis = ev
    d = PerformanceDirector(eng, vis, scenarios.classroom(eng))
    m = PerformanceQualityEvaluator(d).motion(fps=24.0)
    assert m["contact_sliding"] == "PASS" and m["contact_sliding_px"] < 1.0
    assert m["contact_transition_jumps"] == [], "no jump at activation or release"
    assert m["foot_sliding_px"] == 0.0 and m["joint_limit_violations"] == []
    c = d.contacts.specs[0]
    free = d.solve(c.start - c.acquire - 0.02)["solve"]["joints_posed"]["hand_r"]
    first = d.solve(c.start - c.acquire + 0.01)["solve"]["joints_posed"]["hand_r"]
    assert math.hypot(first[0] - free[0], first[1] - free[1]) < 3.0, "contact acquisition starts where the hand is"


@real
def test_moving_target_and_two_hand_object(ev):
    eng, vis = ev
    perf = scenarios.secondary_demo()
    sh = eng.profile.shoulder
    obj = {"ball": {"keys": [[0.4, sh["r"][0] - 90, sh["r"][1] + 190], [2.4, sh["r"][0] - 40, sh["r"][1] + 165]]}}
    perf.objects = obj
    perf.contacts = [ContactSpec("grip_r", "two_hand_object", "hand_r", {"object": "ball", "offset": [0, 0]}, 0.8, 2.6,
                                 attach="palm").to_json()]
    d = PerformanceDirector(eng, vis, perf)
    for t in (1.0, 1.6, 2.2):
        r = d.solve(t)
        assert r["performance"]["contact_sliding_px"]["grip_r"] < 1.0, f"follows the moving target at {t}"
    perf.objects = {"ball": {"keys": [[0.0, sh["r"][0] - 5, sh["r"][1] + 40]]}}  # inside the shoulder: unreachable
    r = PerformanceDirector(eng, vis, perf).solve(1.6)
    assert r["performance"]["contact_sliding_px"]["grip_r"] > 1.0 and not r["solve"]["arms"]["r"]["reachable"], \
        "an unreachable target is clamped by the IK and reported, never stretched"


@real
def test_elbow_contact_lands_on_the_reach_circle(ev):
    eng, vis = ev
    perf = scenarios.secondary_demo()
    sh = eng.profile.shoulder["r"]
    perf.contacts = [ContactSpec("elbow", "elbow_desk", "elbow_r", {"point": [sh[0] - 60, sh[1] + 110]}, 0.5, 2.0,
                                 forearm_dir=-60.0).to_json()]
    d = PerformanceDirector(eng, vis, perf)
    r = d.solve(1.2)
    e = r["performance"]["contacts"]["r"]["contacts"][0]["elbow_target"]
    J = r["solve"]["joints_posed"]["forearm_r"]
    assert math.hypot(J[0] - e[0], J[1] - e[1]) < 2.0


# -- WEIGHT TRANSFER ------------------------------------------------------------------------------------------------
@real
def test_planted_feet_never_slide_unlike_the_phase35_baseline(ev):
    eng, vis = ev
    from autorig.artistic.poses import ArtisticPosePlanner
    from autorig.artistic.transitions import ActingSequence
    pl = ArtisticPosePlanner(eng.profile)
    base = []
    for t, ap, _ in ActingSequence(acting_test_keys(), 12.0).frames():
        rq = pl.request(ap)
        nm = rq.pop("name")
        rq.pop("depth", None)
        base.append(eng.pose(nm, visible=vis, skeleton_only=True, **rq)["solve"]["bone_tails_posed"]["shin_r"])
    step = np.hypot(*np.diff(np.array(base), axis=0).T).max()
    assert step > 10, "the Phase 3.5 baseline slides a planted foot (detected)"
    d = PerformanceDirector(eng, vis, scenarios.acting_upgrade())
    m = PerformanceQualityEvaluator(d).motion(fps=24.0)
    assert m["foot_sliding_px"] == 0.0 and m["verdict"] == "PASS"


@real
def test_weight_beyond_old_saturation_keeps_moving_and_steps_lift(ev):
    eng, vis = ev
    perf = scenarios.secondary_demo()
    keys = [Key.from_json(k) for k in perf.base_keys]
    keys[1].params["weight_left"] = 0.83
    perf.base_keys = [k.to_json() for k in keys]
    d = PerformanceDirector(eng, vis, perf)
    xs = [d.solve(t)["solve"]["joints_posed"]["pelvis"][0] for t in np.linspace(0.6, 1.05, 10)]
    assert all(b > a for a, b in zip(xs, xs[1:])), "the pelvis keeps travelling (no mid-move stop)"
    res = ArtisticAnimationEvaluator.continuity_check(lambda t: d.pose_at(t)[2]["params"], d.duration,
                                                      lambda t: d.solve(t)["solve"], fps=12.0)
    assert res["solver_event_count"] == 0, "no balance saturation"
    d.weight.steps = [StepEvent("r", 1.6, 2.1, -20.0)]
    mid = d.solve(1.85)["solve"]
    end = d.solve(2.6)["solve"]
    lock = d.weight.locks["r"]
    assert not d.weight.ankle_at("r", 1.85)[1] and mid["joints_posed"]["foot_r"][1] < lock[1] - 5, "the foot lifts"
    assert abs(end["joints_posed"]["foot_r"][0] - (lock[0] - 20.0)) < 1.0, "and lands planted 20 px over"


# -- SECONDARY MOTION -------------------------------------------------------------------------------------------------
def _drv(t):
    return 8.0 * ease(min(max((t - 0.5) / 0.4, 0.0), 1.0))


def test_secondary_is_deterministic_frame_rate_and_order_independent():
    a = SecondaryMotionController()
    a.simulate({"hair": _drv, "hem": _drv}, 3.0)
    b = SecondaryMotionController()
    b.simulate({"hair": _drv, "hem": _drv}, 3.0)
    ts = np.arange(0, 3.0, 1 / 12)
    fwd = [a.value("hair", t) for t in ts]
    rnd = {t: b.value("hair", t) for t in np.random.default_rng(1).permutation(ts)}
    assert fwd == [rnd[t] for t in ts] and a.key == b.key, "random access == forward playback"
    assert [a.value("hair", t) for t in np.arange(0, 3.0, 1 / 24)][::2] == fwd, "same values at any frame rate"


def test_secondary_damping_limits_reset_overrides_and_blocked_parts():
    c = SecondaryMotionController({"hair": {"limit_deg": 2.0, "gain": 3.0}})
    c.simulate({"hair": _drv, "hem": _drv}, 4.0)
    v = np.array([c.value("hair", t) for t in np.arange(0, 4.0, 1 / 48)])
    assert np.abs(v).max() <= 2.0 and np.abs(v).max() > 0.5, "lags, but never beyond its limit"
    assert abs(c.value("hair", 4.0)) < 0.2 * np.abs(v).max(), "settles after the body stops"
    r = SecondaryMotionController(resets=[0.7])
    r.simulate({"hair": _drv}, 2.0)
    assert abs(r.value("hair", 0.7)) < 1e-9, "a reset restarts at rest"
    o = SecondaryMotionController(overrides=[{"part": "hem", "t0": 1.0, "t1": 2.0, "value": 1.5}])
    o.simulate({"hem": _drv, "hair": _drv}, 3.0)
    assert abs(o.value("hem", 1.5) - 1.5) < 1e-9
    with pytest.raises(ValueError, match="vest"):
        merged_settings({"tie": {"enabled": True}})
    with pytest.raises(ValueError):
        merged_settings({"hair": {"damping": 9.0}})


@real
def test_secondary_moves_hair_and_hem_but_keeps_rigid_parts(ev):
    eng, vis = ev
    on = PerformanceDirector(eng, vis, scenarios.secondary_demo())
    off = PerformanceDirector(eng, vis, scenarios.secondary_demo(mute_secondary=True))
    t = 1.35
    assert abs(on.secondary.value("hem", t)) > 0.2 or abs(on.secondary.value("hair", t)) > 0.2
    a = on.solve(t, render=True, corrective_mode="off")
    b = off.solve(t, render=True, corrective_mode="off")
    diff = np.any(a["image"] != b["image"], axis=2)
    assert diff.sum() > 50, "secondary motion is visible"
    struct = eng.structure
    fm = struct.full_map("torso")  # part index per torso pixel
    checked = []
    for i, p in enumerate(struct.parts):
        if p.name in ("badge", "button_1", "button_2", "tie", "lapels", "shirt_collar"):
            ys, xs = np.nonzero(fm == i)
            if len(ys) == 0:
                continue
            pad = a["pad"]
            assert not diff[ys + pad, xs + pad].any(), f"{p.name} stays put (rigid, not driven by secondary motion)"
            checked.append(p.name)
    assert {"badge", "button_1", "tie", "lapels"} <= set(checked)


# -- MOTION LAYERS -----------------------------------------------------------------------------------------------------
def _base():
    return KeyPoseSequencer([Key(0.0, {"arm_r_abduction": 10.0, "head": 0.0, "weight_left": 0.5}, {"r": "relaxed"},
                                 "a", 0.2), Key(3.0, {"arm_r_abduction": 10.0, "head": 0.0, "weight_left": 0.5})])


def test_layers_compose_override_additive_masks_and_mute():
    g = Clip("wave", "UPPER_BODY_GESTURES", 0.5, [Key(0.0, {}), Key(0.6, {"arm_r_abduction": 140.0}, {"r": "open_palm"},
                                                                    hold=0.4), Key(1.6, {})],
             mask=["arm_r_abduction"], side="r")
    h = Clip("look", "HEAD_MOVEMENT", 0.4, [Key(0.0, {"head": 0.0}), Key(0.5, {"head": 6.0}, hold=0.5),
                                              Key(1.6, {"head": 0.0})], anchor_start=False, anchor_end=False)
    L = MotionLayerController(_base(), [g, h])
    p, hands, info = L.params_at(1.3)
    assert p["arm_r_abduction"] > 130 and p["head"] > 5.0, "a head clip coexists with an upper-body gesture"
    assert abs(p["weight_left"] - 0.5) < 1e-9, "masks: neither clip touches the weight"
    assert hands["r"] == "open_palm"
    p0 = L.params_at(0.5)[0]
    assert abs(p0["arm_r_abduction"] - 10.0) < 1e-9, "the anchored clip starts at the current pose"
    f = lambda t: L.params_at(t)[0]["arm_r_abduction"]  # noqa: E731
    assert abs(_vel(f, 0.5 - 2e-3) - _vel(f, 0.5 + 2e-3)) < 2.0, "no velocity jump at the clip start"
    muted = MotionLayerController(_base(), [g, h], {"UPPER_BODY_GESTURES": {"mute": True}})
    assert abs(muted.params_at(1.3)[0]["arm_r_abduction"] - 10.0) < 1e-9
    half = MotionLayerController(_base(), [g], {"UPPER_BODY_GESTURES": {"weight": 0.5}})
    assert 60 < half.params_at(1.3)[0]["arm_r_abduction"] < 90, "layer weights blend in joint space"
    with pytest.raises(ValueError):
        MotionLayerController(_base(), [], {"NOPE": {}})


def test_layer_soft_limits_and_facial_isolation():
    big = Clip("up", "UPPER_BODY_GESTURES", 0.0, [Key(0.0, {}), Key(0.5, {"arm_r_abduction": 260.0}, hold=0.5),
                                                  Key(1.5, {})], mask=["arm_r_abduction"])
    p, _, info = MotionLayerController(_base(), [big]).params_at(0.8)
    assert p["arm_r_abduction"] < 176.0 and "arm_r_abduction" in info["soft_limited"]
    face = Clip("blink", "FUTURE_FACIAL_CONTROLS", 0.0, [Key(0.0, {"blink_l": 0.0}), Key(0.2, {"blink_l": 1.0},
                                                                                        hold=0.4)],
                anchor_start=False, anchor_end=False)
    L = MotionLayerController(_base(), [face])
    p, _, info = L.params_at(0.35)
    assert info["facial"]["blink_l"] == pytest.approx(1.0) and "blink_l" not in p
    assert p == MotionLayerController(_base(), []).params_at(0.35)[0], "the facial layer never moves the body"
    with pytest.raises(ValueError):
        Clip("bad", "FUTURE_FACIAL_CONTROLS", 0.0, [Key(0.0, {"head": 1.0})])
    with pytest.raises(ValueError):
        Clip("bad", "UPPER_BODY_GESTURES", 0.0, [Key(0.0, {FACIAL_CHANNELS[0]: 1.0})])
    with pytest.raises(ValueError):
        Clip("bad", "HEAD_MOVEMENT", 0.0, [Key(0.0, {"arm_r_abduction": 1.0})])


@real
def test_contacts_survive_gesture_layers(ev):
    eng, vis = ev
    perf = scenarios.classroom(eng)
    perf.gestures.append({"id": "wave_r", "gesture": "wave", "start": 2.4, "side": "r"})
    d = PerformanceDirector(eng, vis, perf)
    assert d.solve(3.0)["performance"]["contact_sliding_px"]["hand_r_desk"] < 1.0, \
        "a gesture on the contacted arm cannot pull the hand off the desk"


# -- STYLES, GESTURES, DIALOGUE ---------------------------------------------------------------------------------------
@real
def test_styles_change_timing_and_amplitude_not_anatomy(ev):
    eng, vis = ev
    lib = GesturePerformanceLibrary(ArtworkAndCorrectionResolver(eng))
    shy, en = lib.build("point", 0.0, "r", "shy"), lib.build("point", 0.0, "r", "energetic")
    assert shy[0].end > en[0].end, "the shy point is slower"
    up = lambda cs: max(k.params.get("arm_r_abduction", 0) for k in cs[0].keys)  # noqa: E731
    assert up(en) > up(shy), "and smaller"
    rest = [(b.name, tuple(b.head), tuple(b.tail)) for b in eng.sk.bones]
    for s in PROFILES:
        perf = scenarios.acting_upgrade(style=s)
        d = PerformanceDirector(eng, vis, perf)
        d.solve(3.6)
        assert [(b.name, tuple(b.head), tuple(b.tail)) for b in eng.sk.bones] == rest
    with pytest.raises(ValueError):
        style("grumpy")
    assert style("neutral", {"amplitude": 0.5}).amplitude == 0.5


@real
def test_gesture_library_status_is_honest(ev):
    eng, vis = ev
    lib = GesturePerformanceLibrary(ArtworkAndCorrectionResolver(eng))
    assert lib.status("shrug")["status"] == "NEEDS_ARTWORK" and lib.status("shrug")["alternative"] == "shrug_low"
    assert lib.status("shrug_low")["status"] == "SUPPORTED"
    assert lib.status("point")["status"] == "NEEDS_APPROVAL" and "pointing" in lib.status("point")["pending"][0]
    assert lib.status("wave")["status"] == "SUPPORTED", "the reference open palm is original artwork"
    assert lib.status("nod")["status"] == "REQUIRES_NEW_VIEW"
    assert all(not lib.status(n)["production_ready"] for n in lib.names())
    assert len([n for n in lib.names() if n != "shrug_low"]) == 10
    for n in lib.names():
        for c in lib.build(n, 1.0, "l"):
            assert c.start == 1.0 and c.end > 1.0


def test_dialogue_events_plan_body_only():
    lib = GesturePerformanceLibrary()
    plan = DialogueActingController(lib).plan(
        [DialogueEvent(0.5, "LOOK_AT_TARGET", "r"), DialogueEvent(0.6, "SPEECH_EMPHASIS"),
         DialogueEvent(1.0, "POINT_AT_TARGET", "r"), DialogueEvent(1.2, "SURPRISE"), DialogueEvent(5.0, "RETURN_TO_IDLE")],
        8.0, [{"t0": 6.0, "t1": 8.0, "role": "listening", "toward": "l"}])
    assert plan["queued"], "events closer than a gesture are queued, not stacked"
    assert not plan["facial_channels_touched"]
    assert all(c.layer != "FUTURE_FACIAL_CONTROLS" for c in plan["clips"])
    L = MotionLayerController(KeyPoseSequencer([Key(0.0, {"head": 0.0, "arm_r_abduction": 10.0,
                                                           "arm_r_flexion": 6.0})]), plan["clips"])
    assert L.params_at(0.9)[0]["head"] < 0, "looks toward the target (character's right)"
    with pytest.raises(ValueError):
        DialogueEvent(0.0, "SING")


def test_resolver_holds_prevent_flicker():
    times = [i / 12 for i in range(8)]
    seq = [{"r": "relaxed"}, {"r": "pointing"}, {"r": "relaxed"}, {"r": "pointing"}, {"r": "pointing"},
           {"r": "pointing"}, {"r": "pointing"}, {"r": "pointing"}]
    out, delayed = ArtworkAndCorrectionResolver.enforce_holds(times, seq)
    runs = [len(list(g)) for _, g in __import__("itertools").groupby(x["r"] for x in out)]
    assert min(runs[:-1]) >= 3 and delayed


@real
def test_seat_and_interactions_report_missing_artwork(ev):
    eng, vis = ev
    sc = SeatAndInteractionController(eng)
    d = sc.seat_diagnostic(Seat(eng.profile.midline_x, eng.profile.hip_y + 160))
    assert d["class"] == "REQUIRES_ALTERNATIVE_ARTWORK" and "art:thigh_foreshortened" in d["needs"]
    rows = {r["interaction"]: r["class"] for r in sc.interactions()}
    assert rows["sit on a chair"] == "REQUIRES_ALTERNATIVE_ARTWORK"
    assert rows["rest a hand on a desk (beside him)"] == "SUPPORTED"
    desk = sc.desk_beside("r")
    assert desk.x1 < eng.profile.midline_x and desk.top_y < eng.profile.hip_y + 200


# -- DIRECTOR, PERSISTENCE, EXPORT -------------------------------------------------------------------------------------
@real
def test_acting_upgrade_is_frame_aligned_and_honest(ev):
    eng, vis = ev
    perf = scenarios.acting_upgrade()
    assert [k["t"] for k in perf.base_keys] == [k.t for k in acting_test_keys()]
    assert [k["hold"] for k in perf.base_keys] == [k.hold for k in acting_test_keys()]
    d = PerformanceDirector(eng, vis, perf)
    assert len(d.frames()) == 78
    shrug = next(k for k in perf.base_keys if k["label"].startswith("shrug"))
    assert shrug["params"]["arm_r_flexion"] == scenarios.SHRUG_LOW["arm_r_flexion"], "no palm-up disguise"
    r = d.solve(3.75)
    assert r["performance"]["hands"]["r"]["status"] == "pending_review", "pending drawings are labelled"
    assert Performance.from_json(json.loads(json.dumps(perf.to_json()))).to_json() == perf.to_json()


@real
def test_original_pixels_protected_in_a_performance_frame(ev):
    eng, vis = ev
    from autorig.garmentrig.corrective_evidence import CorrectiveQualityEvaluator
    d = PerformanceDirector(eng, vis, scenarios.classroom(eng))
    off = d.solve(3.4, render=True, corrective_mode="off")
    on = d.solve(3.4, render=True, extra={"corrective_overrides": {"edge_decontamination": False}})
    assert CorrectiveQualityEvaluator().evaluate(off, on, None)["checks"]["B_leakage"]["protected_violation_px"] == 0


@real
def test_export_metadata_compatibility_and_live_spec(ev):
    eng, vis = ev
    d = PerformanceDirector(eng, vis, scenarios.classroom(eng))
    md = animation_metadata(d, fps=6.0)
    assert md["status"] == "proposed_unverified" and len(md["frames"]) == len(d.frames(6.0))
    assert md["frames"][0]["theta_deg"] and md["hand_switch_keys"]["r"][1]["pose"] == "palm_down"
    assert {r["class"] for r in compatibility()["rows"]} <= set(COMPAT_CLASSES) and compatibility()["verified"] == []
    text = json.dumps(md)
    for api_name in ("LayerAs", "SetValue", "fAnimAngle", "SetLayerParentBone", "Skeleton()"):
        assert api_name not in text, "no Moho API names are invented"
    live = live_control_spec(d)
    assert {"GESTURE_POINT", "GESTURE_SHRUG", "GESTURE_WAVE", "LOOK_AT_TARGET", "HAND_PLANT", "HAND_RELEASE",
            "RETURN_TO_IDLE"} <= set(live["events"]) and live["webcam_tracking"].startswith("not implemented")
    assert live["events"]["GESTURE_POINT"]["autorig_status"] == "NEEDS_APPROVAL"


def test_performance_project_ops_undo_and_migration():
    from autorig.editor.project import OpError, _apply_performance, default_performance_state, migrate
    st = {"performance": default_performance_state()}
    with pytest.raises(OpError):
        _apply_performance(st, {"type": "performance.key.remove", "id": "k0"})
    _apply_performance(st, {"type": "performance.set", "doc": scenarios.acting_upgrade().to_json()})
    doc = st["performance"]["doc"]
    k = copy.deepcopy(doc["base_keys"][3])
    k["overshoot"] = 0.1
    _apply_performance(st, {"type": "performance.key.set", "key": k})
    _apply_performance(st, {"type": "performance.curve.set", "key_id": k["id"], "curve": {"kind": "bezier",
                                                                                           "params": [0.2, 0, 0.6, 1.1]}})
    _apply_performance(st, {"type": "performance.gesture.add", "gesture": {"id": "w", "gesture": "wave", "start": 1.0,
                                                                          "side": "l"}})
    _apply_performance(st, {"type": "performance.gesture.update", "id": "w", "fields": {"anticipation": 0.0}})
    _apply_performance(st, {"type": "performance.contact.add", "contact": ContactSpec(
        "c", "hand_hip", "hand_l", {"point": [600, 700]}, 1.0, 2.0).to_json()})
    _apply_performance(st, {"type": "performance.style", "style": "confident"})
    _apply_performance(st, {"type": "performance.secondary", "part": "hair", "settings": {"damping": 0.5}})
    _apply_performance(st, {"type": "performance.layer", "layer": "HEAD_MOVEMENT", "mute": True, "weight": 0.5})
    _apply_performance(st, {"type": "performance.offsets", "group": "head", "value": 0.1})
    _apply_performance(st, {"type": "performance.marker.add", "marker": {"t": 1.0, "side": "r", "action": "fist"}})
    _apply_performance(st, {"type": "performance.save_as", "name": "take 1"})
    doc = st["performance"]["doc"]
    assert doc["base_keys"][3]["curve"]["kind"] == "bezier" and doc["gestures"][0]["anticipation"] == 0.0
    assert doc["style"] == "confident" and doc["layers"]["HEAD_MOVEMENT"] == {"mute": True, "weight": 0.5}
    assert "take 1" in st["performance"]["library"]
    for bad in ({"type": "performance.gesture.add", "gesture": {"id": "x", "gesture": "moonwalk", "start": 0}},
                {"type": "performance.secondary", "part": "tie", "settings": {"enabled": True}},
                {"type": "performance.style", "style": "grumpy"},
                {"type": "performance.offsets", "group": "tail", "value": 0.1},
                {"type": "performance.key.set", "key": {**k, "t": doc["base_keys"][1]["t"], "id": "same_time"}},
                {"type": "performance.layer", "layer": "HEAD_MOVEMENT", "weight": 3},
                {"type": "performance.contact.add", "contact": {"id": "z", "kind": "hand_desk", "part": "hand_q",
                                                                 "target": {"point": [0, 0]}, "start": 0, "end": 1}}):
        with pytest.raises(OpError):
            _apply_performance(st, bad)
    assert migrate({"schema_version": 7, "ops": []})["schema_version"] == 8


@real
def test_http_performance_api_roundtrip(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import warnings

    from fastapi.testclient import TestClient

    from autorig.editor.server import create_app
    skip = {"motion", "ik_switch", "garments", "correctives", "artistic", "performance", "poses", "legacy",
            "closeups"}

    def ignore(d, names):
        p = Path(d)
        if p == REAL_PKG:
            return [n for n in names if n == "editor"]
        if p.name == "natural_pose":
            return [n for n in names if n in skip]
        if p.name == "hand_store":
            return [n for n in names if n.startswith("evidence")]
        return []
    dst = tmp_path / "pkg"
    shutil.copytree(REAL_PKG, dst, ignore=ignore)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        c = TestClient(create_app(dst))
    info = c.get("/api/performance").json()
    assert info["doc"] is None and "point" in info["gestures"] and "neutral" in info["styles"]
    assert c.post("/api/performance/track", json={}).status_code == 400, "no performance yet"
    doc = c.post("/api/performance/scenario", json={"name": "acting_upgrade"}).json()["doc"]
    assert c.post("/api/op", json={"type": "performance.set", "doc": doc}).status_code == 200
    tr = c.post("/api/performance/track", json={}).json()
    assert len(tr["frames"]) == 78 and tr["max_foot_step_px"] == {"r": 0.0, "l": 0.0}
    assert tr["frames"][45]["hands"]["r"]["status"] == "pending_review"
    assert c.post("/api/op", json={"type": "performance.style", "style": "energetic"}).status_code == 200
    assert c.post("/api/undo").status_code == 200
    assert c.get("/api/performance").json()["doc"]["style"] == "neutral", "undo restores the style"
    img = c.get("/api/performance/frame.png", params={"t": 3.7})
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG"
    c2 = TestClient(create_app(dst))
    assert c2.get("/api/performance").json()["doc"]["name"] == "acting_test_upgrade", "saved and reloaded"
