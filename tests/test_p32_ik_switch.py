"""Phase 3.2: two-bone IK, arm/leg controllers, foot contacts, artwork switch groups, hand library, correction
switches, rig controls spec, editor project v2 and live actions.

Unit tests use synthetic geometry. The integration tests run on a copy of the user's real T-pose package; they
check geometry, contacts, persistence and pixel measurements of the preview renderer. They are not evidence of
production-quality animation, and Moho is not run.
"""
from __future__ import annotations

import copy
import json
import math
import shutil
from pathlib import Path

import numpy as np
import pytest

from autorig.motion.ik import CONTACT_STATES, FootContactController, TwoBoneIKSolver
from autorig.motion.solver import SOFT_SUPPORT_PX
from autorig.motion.rigctl import (LIVE_ACTIONS, RIG_CONTROLS_SCHEMA, action_ops, build_rig_controls, check_ik_fields,
                                   default_ik_state, validate)
from autorig.motion.switches import (FALLBACK_POSE, HAND_POSES, REQUIRED_HAND_POSES, ArtworkSwitchController,
                                     SwitchGroup, SwitchVariant, offset_layer)
from autorig.phase2.skeleton import SIDES

ROOT = Path(__file__).resolve().parents[1]
REAL_PKG = ROOT / "projects" / "schoolboy_tpose" / "character_package"
real = pytest.mark.skipif(not (REAL_PKG / "natural_pose" / "rig.json").is_file(),
                          reason="real T-pose character package not present")


# -- TwoBoneIKSolver (synthetic geometry) -----------------------------------------------------------------
def _flex(res):
    a, b = res.mid - res.root, res.end - res.mid
    return math.degrees(math.atan2(abs(a[0] * b[1] - a[1] * b[0]), float(a @ b)))


def test_ik_reaches_reachable_targets_and_keeps_lengths():
    rng = np.random.default_rng(3)
    for _ in range(500):
        l1, l2 = rng.uniform(20, 200, 2)
        sol = TwoBoneIKSolver(l1, l2, 0.0, 179.0)
        root = rng.uniform(-100, 100, 2)
        d = rng.uniform(sol.min_reach + 1e-3, sol.max_reach - 1e-3)
        a = rng.uniform(0, 2 * math.pi)
        tgt = root + d * np.array([math.cos(a), math.sin(a)])
        r = sol.solve(root, tgt, root + rng.uniform(-300, 300, 2))
        assert r.status == "ok" and r.error_px < 1e-6
        assert np.hypot(*(r.mid - r.root)) == pytest.approx(l1, abs=1e-9)
        assert np.hypot(*(r.end - r.mid)) == pytest.approx(l2, abs=1e-9)
        assert _flex(r) == pytest.approx(r.flexion_deg, abs=1e-6)


def test_pole_picks_the_bend_side_and_bend_sign_without_pole():
    sol = TwoBoneIKSolver(100, 100)
    for pole, side in (((50, 200), 1), ((50, -200), -1)):
        r = sol.solve((0, 0), (150, 0), pole)
        u = np.array([1.0, 0.0])
        assert np.sign(u[0] * r.mid[1] - u[1] * r.mid[0]) == side == r.bend_sign
    assert sol.solve((0, 0), (150, 0), None, bend_sign=-1).mid[1] < 0


def test_unreachable_targets_are_clamped_not_stretched():
    sol = TwoBoneIKSolver(100, 80, 5.0, 150.0)
    far = sol.solve((0, 0), (500, 0), (0, 100))
    assert far.status == "clamped_max_reach" and not far.reached
    assert np.hypot(*far.end) == pytest.approx(sol.max_reach) and far.error_px == pytest.approx(500 - sol.max_reach)
    assert far.flexion_deg == pytest.approx(5.0, abs=1e-6)
    near = sol.solve((0, 0), (1, 0), (0, 100))
    assert near.status == "clamped_min_reach" and near.flexion_deg == pytest.approx(150.0, abs=1e-6)
    for r in (far, near):
        assert np.hypot(*(r.mid - r.root)) == pytest.approx(100) and np.hypot(*(r.end - r.mid)) == pytest.approx(80)
    deg = sol.solve((0, 0), (0, 0))
    assert deg.status == "degenerate" and np.isfinite(deg.mid).all()


def test_ik_is_deterministic_mirrored_and_stable_near_full_extension():
    sol = TwoBoneIKSolver(120, 110, 1.6, 150)
    a = sol.solve((10, 5), (100, 180), (200, 50))
    b = sol.solve((10, 5), (100, 180), (200, 50))
    assert np.array_equal(a.mid, b.mid) and np.array_equal(a.end, b.end)
    m = sol.solve((-10, 5), (-100, 180), (-200, 50))  # mirrored limb
    assert m.mid == pytest.approx(a.mid * [-1, 1]) and m.end == pytest.approx(a.end * [-1, 1])
    prev = None
    for d in np.linspace(200, 260, 601):  # through the max reach (229.9)
        r = sol.solve((0, 0), (0, d), (100, 100))
        if prev is not None:
            assert np.hypot(*(r.mid - prev)) < 2.0, "the middle joint never snaps near full extension"
        prev = r.mid


def test_ik_rejects_bad_parameters():
    with pytest.raises(ValueError):
        TwoBoneIKSolver(0, 10)
    with pytest.raises(ValueError):
        TwoBoneIKSolver(10, 10, 50, 20)


# -- switch groups (synthetic) ------------------------------------------------------------------------------
def _group():
    vs = {"open_palm": SwitchVariant("open_palm", "hand_r", "original"),
          "fist": SwitchVariant("fist", "hand_r__fist", "pending_review"),
          "pointing": SwitchVariant("pointing", "hand_r__point", "approved"),
          "gripping": SwitchVariant("gripping", None, "missing")}
    return {"hand_r": SwitchGroup("hand_r", "hand", "hand_r", (0.0, 0.0), vs, "open_palm")}


def test_switch_resolution_order_and_availability():
    c = ArtworkSwitchController(_group())
    assert c.resolve("hand_r", None, None)["active"] == "open_palm"
    r = c.resolve("hand_r", None, "pointing")
    assert (r["active"], r["source"]) == ("pointing", "auto")
    c.key("hand_r", 0.5, "pointing")
    c.key("hand_r", 0.0, "open_palm")
    assert c.resolve("hand_r", 0.7, "fist")["source"] == "keyframe" and c.resolve("hand_r", 0.7)["active"] == "pointing"
    assert c.resolve("hand_r", 0.2)["active"] == "open_palm"
    c.set("hand_r", "fist")  # explicit user choice wins over keys and auto
    r = c.resolve("hand_r", 0.7, "pointing")
    assert r["source"] == "user" and r["active"] == "open_palm" and "pending review" in r["note"]
    r = c.resolve("hand_r", 0.7, "pointing", allow_pending=True)
    assert r["active"] == "fist" and r["pending_review"]
    c.set("hand_r", "gripping")
    r = c.resolve("hand_r", 0.7, allow_pending=True)
    assert r["active"] == "open_palm" and r["needs_artwork"] == "gripping"
    c.set("hand_r", None)  # back to auto
    assert c.resolve("hand_r", None, "pointing")["source"] == "auto"
    with pytest.raises(KeyError):
        c.set("hand_r", "nope")
    with pytest.raises(KeyError):
        c.key("hand_x", 0.0, "fist")


def test_offset_layer_is_rigid_about_the_pivot_and_never_scales():
    a = np.zeros((200, 200, 4), np.uint8)
    a[90:110, 120:150] = 255
    area = int((a[..., 3] > 127).sum())
    piv = (100.0, 100.0)
    for dx, dy, rot in ((5, -3, 0), (0, 0, 30), (4, 2, -45)):
        b = offset_layer(a, piv, dx, dy, rot)
        assert abs(int((b[..., 3] > 127).sum()) - area) <= 0.05 * area
    b = offset_layer(a, piv, 0, 0, 90)  # +90 = clockwise on screen: right of the pivot -> below it
    ys, xs = np.nonzero(b[..., 3] > 127)
    assert xs.mean() == pytest.approx(100, abs=1.5) and ys.mean() > 120
    assert offset_layer(a, piv) is a


def test_foot_contact_state_machine():
    from autorig.motion.intent import LegIntent, PoseIntent
    fc = FootContactController()
    it = PoseIntent("x", "x", "x", legs={"r": LegIntent(True, (0, 0)), "l": LegIntent(False, (0, 0))})
    assert fc.state(it, "r") == "PLANTED" and fc.state(it, "l") == "FREE"
    it.balance = "dynamic"
    it.channels = {"duty": 0.6, "stance_psi_r": 0.1, "swing_u_l": 0.05}
    it.legs["r"].load = 0.3
    assert fc.state(it, "r") == "LANDING" and fc.state(it, "l") == "LIFTING"
    it.channels.update(stance_psi_r=0.5, swing_u_l=0.5)
    assert fc.state(it, "r") == "LIFTING" and fc.state(it, "l") == "SWINGING"
    it.channels["swing_u_l"] = 0.95
    assert fc.state(it, "l") == "LANDING"
    assert set(CONTACT_STATES) == {"PLANTED", "LIFTING", "SWINGING", "LANDING", "FREE"}


def test_ik_field_validation():
    assert not check_ik_fields("arm_r", {"enabled": True, "mode": "ik", "target": [1, 2], "bend": -1})
    assert check_ik_fields("arm_x", {})
    assert check_ik_fields("leg_l", {"fk": {"abduction": 3}})
    assert check_ik_fields("arm_r", {"mode": "both"}) and check_ik_fields("arm_r", {"scale": 2})
    assert check_ik_fields("pelvis", {"pelvis_offset": [1]})


def test_schema_file_in_sync_and_no_moho_api_in_the_motion_core():
    f = ROOT / "src" / "autorig" / "data" / "schemas" / "rig_controls.schema.json"
    assert json.loads(f.read_text()) == RIG_CONTROLS_SCHEMA
    allow = json.loads((ROOT / "src" / "autorig" / "data" / "moho_api_allowlist.json").read_text())
    names = {m["name"] for m in allow["members"] if len(m["name"]) > 4}
    assert names
    for src in (ROOT / "src" / "autorig" / "motion").glob("*.py"):
        text = src.read_text()
        # Lua-style calls into Moho (``obj:Method(``, ``moho.``/``MOHO.``/``LM.`` namespaces) never appear in the core
        hits = [n for n in names if f":{n}(" in text] + [ns for ns in ("moho.", "MOHO.", "LM.") if ns in text]
        assert not hits, f"{src.name} uses Moho API names {hits[:5]}"
    for a in LIVE_ACTIONS.values():
        assert a["channel"] in ("body", "face") and not any(k in a for k in ("input", "moho_input"))


# -- real package --------------------------------------------------------------------------------------------
def _ignore(d, names):
    skip = {"editor", "motion", "ik_switch"}
    if Path(d).name == "natural_pose":
        skip |= {"poses", "legacy", "closeups", "contact_sheet.png", "contact_sheet_diagnostic.png"}
    return [n for n in names if n in skip]


@pytest.fixture(scope="module")
def pkg(tmp_path_factory):
    dst = tmp_path_factory.mktemp("p32") / "pkg"
    shutil.copytree(REAL_PKG, dst, ignore=_ignore)
    return dst


@pytest.fixture(scope="module")
def eng(pkg):
    from autorig.motion.pipeline import engine_for_package
    return engine_for_package(pkg)


def _lengths_ok(e, info):
    J, T = info["joints_posed"], info["bone_tails_posed"]
    for b in e.sk.bones:
        assert np.hypot(T[b.name][0] - J[b.name][0], T[b.name][1] - J[b.name][1]) == pytest.approx(b.length, abs=0.02)


@real
def test_arm_ik_on_the_real_skeleton_reaches_and_keeps_lengths(eng):
    e, _ = eng
    sh = np.asarray(e.plan_and_solve("relaxed_standing")[2]["joints_posed"]["upper_arm_r"])  # posed shoulder
    L = e.profile.arm("r")
    for lat, up in ((0.6, 0.4), (0.3, 0.8), (0.8, -0.2), (0.1, -0.6)):
        tgt = [float(sh[0] - lat * L), float(sh[1] - up * L)]
        _, pose, info = e.plan_and_solve("relaxed_standing", ik={"arm_r": {"enabled": True, "mode": "ik", "target": tgt}})
        a = info["arms"]["r"]
        assert a["status"] == "ok" and a["error_px"] < 0.01
        assert np.all(pose.scale == 1.0)
        _lengths_ok(e, info)
    far = [float(sh[0] - 0.6 * L), float(sh[1] - 1.2 * L)]  # overhead, out of reach: the shoulder rises toward it
    _, pose, info = e.plan_and_solve("relaxed_standing", ik={"arm_r": {"enabled": True, "mode": "ik", "target": far}})
    assert info["arms"]["r"]["status"] == "clamped_max_reach"
    assert any("clavicle" in n for n in info["notes"]), "shoulder compensation reported"
    _lengths_ok(e, info)


@real
@pytest.mark.parametrize("preset", ["relaxed_standing", "pointing_speaking", "both_hands_raised", "hands_on_hips"])
def test_fk_ik_switching_matches_without_jumps(eng, preset):
    e, _ = eng
    for limb in ("arm_r", "arm_l"):
        r = e.match_ik(limb, "ik", preset)
        assert r["state"]["mode"] == "ik" and r["jump_px"] < 0.05, r
        back = e.match_ik(limb, "fk", preset, ik={limb: r["state"]})
        assert back["state"]["mode"] == "fk" and back["jump_px"] < 0.05, back


@real
def test_held_bend_side_never_inverts_the_elbow_during_a_sweep(eng):
    e, _ = eng
    sh = np.asarray(e.sk.bone("upper_arm_r").head)
    L = e.profile.arm("r")
    prev_side, prev_j = None, None
    for i in range(121):
        a = math.radians(-20 + 200 * i / 120)
        tgt = sh + 0.8 * L * np.array([-math.sin(a), math.cos(a)])
        _, _, info = e.plan_and_solve("relaxed_standing", ik={"arm_r": {"enabled": True, "mode": "ik",
                                                                         "target": list(map(float, tgt)), "bend": 1}})
        w = np.subtract(info["arms"]["r"]["wrist"], info["joints_posed"]["upper_arm_r"])
        el = np.subtract(info["arms"]["r"]["elbow"], info["joints_posed"]["upper_arm_r"])
        side = np.sign(w[0] * el[1] - w[1] * el[0])
        assert prev_side is None or side == prev_side
        J = info["joints_posed"]
        if prev_j is not None:
            assert max(np.hypot(J[n][0] - prev_j[n][0], J[n][1] - prev_j[n][1]) for n in J) < 10.0, "no joint jumps"
        prev_side, prev_j = side, J


@real
def test_leg_ik_locked_feet_stay_put_while_the_pelvis_moves(eng):
    e, _ = eng
    _, _, i0 = e.plan_and_solve("relaxed_standing")
    feet = {s: i0["joints_posed"][f"foot_{s}"] for s in SIDES}
    for po in ([0, 0], [18, 0], [-25, 12], [0, 30]):
        ik = {f"leg_{s}": {"enabled": True, "mode": "ik", "target": feet[s], "lock": True} for s in SIDES}
        ik["pelvis_offset"] = po
        _, pose, info = e.plan_and_solve("relaxed_standing", ik=ik)
        for s in SIDES:
            c = info["contacts"][s]
            assert c["state"] == "PLANTED" and c["lock_error_px"] < 0.01, (po, c)
        if po[0]:  # a user pelvis offset replaces the automatic balance shift; the feet stay locked
            assert info["pelvis_shift_px"][0] == pytest.approx(po[0], abs=0.5)
        assert np.all(pose.scale == 1.0)
    # lift one foot: it is FREE, the other stays planted; a below-ground target is clamped and reported
    ik = {"leg_l": {"enabled": True, "mode": "ik", "target": [feet["l"][0], feet["l"][1] - 60], "lock": False}}
    _, _, info = e.plan_and_solve("relaxed_standing", ik=ik)
    assert info["contacts"]["l"]["state"] == "FREE" and info["contacts"]["r"]["state"] == "PLANTED"
    assert info["joints_posed"]["foot_l"][1] == pytest.approx(feet["l"][1] - 60, abs=0.5)
    ik = {"leg_l": {"enabled": True, "mode": "ik", "target": [feet["l"][0], feet["l"][1] + 40], "lock": False}}
    _, _, info = e.plan_and_solve("relaxed_standing", ik=ik)
    assert any("below the ground" in n for n in info["ik_notes"])
    assert info["joints_posed"]["foot_l"][1] <= feet["l"][1] + 0.5, "no ground penetration"


@real
@pytest.mark.parametrize("name", ["casual_walk", "running"])
def test_gait_contacts_cycle_through_the_states_without_sliding(eng, name):
    e, _ = eng
    seen = {s: [] for s in SIDES}
    for i in range(48):
        _, _, info = e.plan_and_solve(name, i / 48)
        for s in SIDES:
            c = info["contacts"][s]
            seen[s].append(c["state"])
            if c["state"] == "PLANTED":  # world-locked: no slide; the smooth support may lift it a little
                assert c["slide_px"] < 0.05 and -0.05 < c["lift_px"] <= SOFT_SUPPORT_PX + 0.05, (name, i, s, c)
    for s in SIDES:
        assert {"PLANTED", "SWINGING"} <= set(seen[s]) and ({"LIFTING", "LANDING"} & set(seen[s]))
        assert "FREE" not in seen[s]


@real
def test_sitting_hands_on_knees_is_reported_unreachable_not_stretched(eng):
    e, _ = eng
    _, pose, info = e.plan_and_solve("sitting")
    assert any("out of reach" in n for n in info["notes"])
    assert np.all(pose.scale == 1.0)
    _lengths_ok(e, info)


@real
def test_hand_library_inventory_and_alignment_from_pixels(eng):
    e, _ = eng
    lib = e.hand_lib
    for s in SIDES:
        assert set(REQUIRED_HAND_POSES) <= set(lib.hands[s])
        assert lib.hands[s]["open_palm"].status == "original"
        for p in ("fist", "pointing", "thumbs_up", "palm_up"):
            assert lib.hands[s][p].status == "pending_review", "nothing is approved in code"
        for p in ("relaxed", "gripping", "palm_down", "partially_closed"):
            assert not lib.hands[s][p].available
    missing = {(m["side"], m["pose"]) for m in lib.missing()}
    assert missing == {(s, p) for s in SIDES for p in ("relaxed", "gripping", "palm_down", "partially_closed")}
    for s in SIDES:
        for p in ("fist", "pointing", "thumbs_up", "palm_up"):
            al = lib.alignment(s, p, e.profile.midline_x)
            assert al["side_ok"], "no left/right swap"
            assert al["attachment_error_px"] is not None
        assert "scale mismatch" in " ".join(lib.alignment(s, "palm_up", e.profile.midline_x)["issues"])
        aa = lib.auto_align(s, "fist")
        assert aa["error_after_px"] <= aa["error_before_px"] and aa["error_after_px"] < 2.0


@real
def test_switch_resolution_in_poses_follows_rules(eng):
    e, vis = eng
    r = e.pose("relaxed_standing", skeleton_only=True)
    assert all(h["pose"] == FALLBACK_POSE for h in r["hands"].values())
    assert {f["side"] for f in r["feasibility"]["hand_fallbacks"]} == set(SIDES), "missing relaxed hand reported"
    sw = {"hand_r": {"user": "fist", "auto": False, "keys": [], "offsets": {}}}
    r = e.pose("relaxed_standing", skeleton_only=True, switches=sw)
    assert r["hands"]["r"]["pose"] == FALLBACK_POSE and "pending review" in r["hands"]["r"]["note"]
    r = e.pose("relaxed_standing", skeleton_only=True, switches=sw, allow_pending=True)
    assert r["hands"]["r"]["pose"] == "fist" and r["feasibility"]["status"] == "NEEDS_ARTWORK"
    sw = {"hand_l": {"user": "gripping", "auto": False, "keys": [], "offsets": {}}}
    r = e.pose("relaxed_standing", skeleton_only=True, switches=sw, allow_pending=True)
    assert r["hands"]["l"]["needs_artwork"] == "gripping" and r["feasibility"]["status"] == "NEEDS_ARTWORK"
    # explicit choices survive presets that prefer other hands; keyframes switch over a gait
    sw = {"hand_r": {"user": None, "auto": True, "keys": [[0.0, "open_palm"], [0.5, "fist"]], "offsets": {}}}
    assert e.pose("running", 0.25, skeleton_only=True, switches=sw, allow_pending=True)["hands"]["r"]["pose"] == "open_palm"
    assert e.pose("running", 0.75, skeleton_only=True, switches=sw, allow_pending=True)["hands"]["r"]["pose"] == "fist"


@real
def test_posed_hand_switch_stays_attached_under_ik(eng):
    from autorig.motion.switches import posed_hand_alignment
    e, vis = eng
    sh = np.asarray(e.sk.bone("upper_arm_r").head)
    ik = {"arm_r": {"enabled": True, "mode": "ik", "target": [float(sh[0]) - 120, float(sh[1]) - 150]}}
    sw = {"hand_r": {"user": "fist", "auto": False, "keys": [], "offsets": {}}}
    r = e.pose("relaxed_standing", ik=ik, switches=sw, allow_pending=True, visible=vis)
    assert "hand_r__fist" in r["order"]
    pa = posed_hand_alignment(e, r, "r", "hand_r__fist", e.hand_lib.hands["r"]["fist"].measure["attachment"])
    assert pa["status"] == "OK", pa
    # an alignment offset becomes its own rigid variant layer about the wrist pivot
    aa = e.hand_lib.auto_align("r", "fist")
    sw["hand_r"]["offsets"] = {"fist": {"dx": aa["offset_px"][0], "dy": aa["offset_px"][1], "rot": 0.0}}
    r2 = e.pose("relaxed_standing", ik=ik, switches=sw, allow_pending=True, visible=vis)
    lay = next(c for c in r2["order"] if c.startswith("hand_r"))
    assert lay.startswith("hand_r__fist__off")
    assert r2["solve"]["joints_posed"] == r["solve"]["joints_posed"], "switching never moves the skeleton"


@real
def test_correction_triggers_have_hysteresis_and_report_missing_art(eng):
    e, _ = eng
    cs = e.corr_switch
    cs.reset()
    seq = [90, 105, 96, 93, 91, 99, 101]
    states = [next(c for c in cs.evaluate({"elbow_r": v}, stateful=True) if c["id"] == "elbow_deep_r")["on"] for v in seq]
    assert states == [False, True, True, True, False, False, True]
    cs.reset()
    sh = np.asarray(e.sk.bone("upper_arm_r").head)
    L = e.profile.arm("r")
    ik = {"arm_r": {"enabled": True, "mode": "ik", "target": [float(sh[0] - 0.12 * L), float(sh[1] + 0.36 * L)]}}
    r = e.pose("relaxed_standing", ik=ik, skeleton_only=True)
    on = {c["id"]: c for c in r["correction_switches"] if c["on"]}
    assert "elbow_deep_r" in on and on["elbow_deep_r"]["needs_artwork"]
    assert r["feasibility"]["status"] == "NEEDS_ARTWORK", "a broken deep-elbow fold is not passed"
    r = e.pose("both_hands_raised", skeleton_only=True)
    on = {c["id"]: c for c in r["correction_switches"] if c["on"]}
    assert on["shoulder_raise_r"]["asset_state"] == "pending_review" and not on["shoulder_raise_r"]["applied"]


@real
def test_rig_controls_spec_validates_and_catches_errors(eng):
    e, _ = eng
    doc = build_rig_controls(e, default_ik_state(), {})
    assert validate(doc) == []
    assert all(c["constraints"]["no_axial_scaling"] for c in doc["ik"]["chains"])
    assert doc["moho"]["status"] == doc["live"]["moho"]["status"] == "proposed_unverified"
    kinds = {m["kind"] for m in doc["missing_artwork"]}
    assert {"hand", "correction", "face"} <= kinds
    bad = copy.deepcopy(doc)
    bad["switches"]["state"]["hand_r"] = {"user": "nope", "auto": False, "keys": [], "offsets": {}}
    assert any("user choice" in x for x in validate(bad))
    bad = copy.deepcopy(doc)
    bad["ik"]["state"]["arm_r"].update(enabled=True, mode="ik", target=None)
    assert validate(bad)
    bad = copy.deepcopy(doc)
    bad["moho"]["status"] = "verified"
    assert validate(bad)


@real
def test_project_v2_ops_persist_undo_and_migrate_from_v1(pkg):
    from autorig.editor.project import VERSION, EditorProject, OpError
    p = EditorProject(pkg)
    assert VERSION == 2 and p.state()["ik"] == default_ik_state()
    p.submit({"type": "ik.set", "limb": "arm_r", "fields": {"enabled": True, "mode": "ik", "target": [300, 400], "bend": 1}})
    p.submit({"type": "ik.set", "limb": "pelvis", "fields": {"pelvis_offset": [5, 3]}})
    p.submit({"type": "switch.set", "group": "hand_r", "variant": "fist", "auto": False})
    p.submit({"type": "switch.key", "group": "hand_l", "t": 0.5, "variant": "pointing"})
    p.submit({"type": "switch.offset", "group": "hand_r", "variant": "fist", "dx": 3, "dy": 1, "rot": 0})
    for bad in ({"type": "ik.set", "limb": "arm_r", "fields": {"target": None, "enabled": True, "mode": "ik"}},
                {"type": "switch.set", "group": "hand_r", "variant": "claw"},
                {"type": "switch.offset", "group": "hand_r", "variant": "fist", "dx": 1, "scale": 1.2},
                {"type": "ik.set", "limb": "tail", "fields": {}}):
        with pytest.raises(OpError):
            p.submit(bad)
    st = EditorProject(pkg).state()  # reopened from disk
    assert st["ik"]["arm_r"]["target"] == [300.0, 400.0] and st["ik"]["pelvis_offset"] == [5.0, 3.0]
    assert st["switches"]["hand_r"]["user"] == "fist" and st["switches"]["hand_l"]["keys"] == [[0.5, "pointing"]]
    assert st["switches"]["hand_r"]["offsets"]["fist"] == {"dx": 3.0, "dy": 1.0, "rot": 0.0}
    p.undo()
    assert "fist" not in p.state()["switches"]["hand_r"]["offsets"]
    p.redo()
    p.submit({"type": "ik.reset", "limb": "all"})
    assert p.state()["ik"] == default_ik_state()
    # a v1 project (Phase 3.1 era) loads: its ops replay unchanged and the new sections get defaults
    data = json.loads(p.path.read_text())
    data["schema_version"] = 1
    data["ops"] = [o for o in data["ops"] if not o["type"].startswith(("ik.", "switch."))]
    data["cursor"] = len(data["ops"])
    p.path.write_text(json.dumps(data))
    q = EditorProject(pkg)
    assert q.data["schema_version"] == 2 and 1 in q.data["migrated_from"]
    assert q.state()["ik"] == default_ik_state() and q.state()["switches"] == {}


@real
def test_live_actions_produce_valid_project_ops(pkg, eng):
    from autorig.editor.project import EditorProject, apply_op
    e, _ = eng
    st = copy.deepcopy(EditorProject(pkg).state())
    for name, a in LIVE_ACTIONS.items():
        ops = action_ops(name, e.sk, 1.0, 0.3, st["ik"])
        assert ops, name
        for op in ops:
            if op["type"] == "live.head":
                assert a["channel"] == "face"
                continue
            apply_op(st, op)
    assert st["switches"]["face_mouth"]["user"] == "neutral"
    w0 = action_ops("GESTURE_WAVE", e.sk, 1.0, 0.0)[0]["fields"]["target"]
    w1 = action_ops("GESTURE_WAVE", e.sk, 1.0, 0.2)[0]["fields"]["target"]
    assert w0 != w1, "the wave oscillates over time"


@real
def test_export_includes_validated_rig_controls(pkg):
    from autorig.editor.artwork import ArtworkModel
    from autorig.editor.export import VERSION, export_package
    from autorig.editor.project import EditorProject
    proj = EditorProject(pkg)
    proj.submit({"type": "ik.set", "limb": "arm_l", "fields": {"enabled": True, "mode": "ik", "target": [700, 380]}})
    r = export_package(proj, ArtworkModel(pkg))
    assert r["validation_errors"] == [] and VERSION == "1.1.0"
    out = Path(r["out"])
    rc = json.loads((out / "rig_controls.json").read_text())
    assert validate(rc) == [] and rc["ik"]["state"]["arm_l"]["target"] == [700.0, 380.0]
    assert r["manifest"]["rig_controls"]["schema"] == "autorig.rig_controls"
    assert r["manifest"]["production_ready"] is False


@real
def test_committed_ik_switch_evidence_is_consistent():
    out = REAL_PKG / "natural_pose" / "ik_switch"
    if not (out / "results.json").is_file():
        pytest.skip("ik-demo evidence not generated")
    r = json.loads((out / "results.json").read_text())
    for f in ("01_standing.jpg", "02_right_arm_ik_reach.jpg", "03_both_arms_raised_ik.jpg", "04_pointing_hand.jpg",
              "05_running_hand_switches.jpg", "06_walking_planted_feet.jpg", "07_sitting_foot_constraints.jpg",
              "08_deep_elbow_bend.jpg", "09_hand_switch_sheet.jpg", "10_ik_diagnostic_diag.jpg", "ik_reach.gif"):
        assert (out / f).is_file(), f
    q = r["ik_sequence"]
    assert q["elbow_side_flips"] == 0 and q["max_wrist_error_px"] < 0.01
    assert q["max_step_over_target_step"] < 2.0
    assert r["renders"]["04_pointing_hand"]["status"] == "NEEDS_ARTWORK", "pending pointing hand is not passed"
    assert r["renders"]["08_deep_elbow_bend"]["status"] == "NEEDS_ARTWORK"
    for w in r["renders"]["06_walking"]:
        for c in w["contacts"].values():
            if c["state"] == "PLANTED":
                assert c["slide_px"] < 0.05
    assert r["auto_align_right_fist"]["rest_error_after_px"] < r["auto_align_right_fist"]["rest_error_before_px"]
    assert set(HAND_POSES) >= {t["pose"] for t in r["hand_switch_table"]}


@real
def test_switch_machinery_leaves_the_neutral_render_untouched(eng):
    from autorig.phase2.skinning import PoseParams
    e, vis = eng
    ident = PoseParams.identity(len(e.sk.bones))
    a = e.dfm.render(ident, list(vis))["image"]
    e.dfm.add_variant_layer("hand_r__fist__offtest",
                            offset_layer(e.variants["hand_r__fist"], e.sk.bone("hand_r").head, 2.0, 1.0, 0.0))
    b = e.dfm.render(ident, list(vis))["image"]
    assert np.array_equal(a, b), "registering a switch drawing never changes the original layers"
    r = e.pose("relaxed_standing", visible=vis, skeleton_only=True)
    assert all(h["pose"] == FALLBACK_POSE for h in r["hands"].values()), "default hands are the reference drawings"


@real
def test_moderate_ik_reach_keeps_garments_continuous(eng):
    e, vis = eng
    sh = np.asarray(e.plan_and_solve("relaxed_standing")[2]["joints_posed"]["upper_arm_r"])
    L = e.profile.arm("r")
    ik = {"arm_r": {"enabled": True, "mode": "ik", "target": [float(sh[0] - 0.6 * L), float(sh[1] - 0.4 * L)]}}
    r = e.pose("relaxed_standing", ik=ik, visible=vis)
    checks = {c["check"]: c for c in r["quality"]["checks"]}
    for k in ("bone_length_rel", "axial_scale", "ik_error_px", "ground_penetration_px", "planted_ankle_error_px"):
        assert checks[k]["pass"], checks[k]
    # the IK result went through the garment engine (rotation-dependent bands are active at the bent elbow)
    assert any("forearm_r" in str(c) or "upper_arm_r" in str(c) for c in r["corrections"])
    seams = {k: checks[k]["value"] for k in ("exposed_gap_pairs", "visible_seam_pairs", "outline_break_pairs")}
    assert seams["exposed_gap_pairs"] <= checks["exposed_gap_pairs"]["threshold"] * 4, seams  # measured, reported
