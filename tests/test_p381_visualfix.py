"""Phase 3.8.1: visual defect fixes (resting hands, switch consistency, shoulder/armpit, knee limits, garment
semantics, non-destructive artwork candidates).

Unit tests run everywhere; the integration tests use the committed packages when they are present."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from autorig.handmeta import SCHEMA, SCHEMA_VERSION
from autorig.motion.handselect import HANG_DEG, HandContext, NaturalHandSelectionEngine

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "projects" / "tough_student_test" / "character_package"
SCHOOLBOY = ROOT / "projects" / "schoolboy_tpose" / "character_package"
have_pkg = pytest.mark.skipif(not (PKG / "natural_pose" / "rig.json").is_file(), reason="tough_student package absent")
have_sb = pytest.mark.skipif(not (SCHOOLBOY / "natural_pose" / "rig.json").is_file(), reason="schoolboy absent")

CAM, BACK = [0.0, 0.0, 1.0], [0.0, 0.0, -1.0]


class _V:
    def __init__(self, side, pose, normal, tol=180.0, status="pending_review"):
        self.side, self.pose, self.status = side, pose, status
        self.cid = f"store:lib/{pose}_{side}"
        self.layer, self.available = f"hand_{side}__{pose}", True
        self.meta = {"hand_meta": {"schema": SCHEMA, "schema_version": SCHEMA_VERSION,
                                   "handedness": "right" if side == "r" else "left", "palm_normal_sprite": normal,
                                   "rotation_tolerance_deg": tol, "errors": [], "excluded": False},
                     "features": {}}
        self.placement = {"final": {"angle_deg": 0.0}, "params": {"wrist_fit": 0.85}}


class _Lib:
    def __init__(self, spec):
        self.hands = {s: {p: _V(s, p, n) for p, n in spec.items()} for s in ("r", "l")}


def _ctx(forearm, action="relaxed_standing", side="r"):
    return HandContext(side, action, forearm, forearm, 0.0, 10.0, 5.0, (0.0, 0.0))


# -- resting hands ----------------------------------------------------------------------------------------------
def test_a_palm_forward_hand_is_not_a_resting_hand_on_a_hanging_arm():
    eng = NaturalHandSelectionEngine()
    d = eng.select(_Lib({"relaxed": CAM, "open_palm": CAM}), _ctx(5.0), None, True)
    assert d["chosen"] is None, "no palm-forward drawing may stand in for a resting hand"
    why = " ".join(v["reason"] for r in d["rejected"] for v in r["violations"])
    assert "hanging arm" in why and "not a resting hand" in why
    assert {n["pose"] for n in d["needs_artwork"]} >= {"relaxed", "open_palm"}, "reported, never silent"
    # the back of the hand passes and is chosen
    d2 = eng.select(_Lib({"relaxed": BACK, "open_palm": CAM}), _ctx(5.0), None, True)
    assert d2["chosen"] == "relaxed"
    # an arm that does not hang (forearm beyond HANG_DEG) keeps the Phase 3.7 behaviour
    d3 = eng.select(_Lib({"relaxed": CAM, "open_palm": CAM}), _ctx(HANG_DEG + 20.0), None, True)
    assert d3["chosen"] in ("relaxed", "open_palm")


@have_pkg
def test_relaxed_drawings_show_the_back_of_the_hand_with_the_thumb_toward_the_body():
    from autorig.motion.handcheck import switch_consistency
    from autorig.motion.pipeline import engine_for_package

    eng, _ = engine_for_package(PKG)
    r = eng.pose("relaxed_standing", allow_pending=True, skeleton_only=True)
    for s in ("r", "l"):
        h = r["hands"][s]
        v = eng.hand_lib.hands[s]["relaxed"]
        hm = v.meta["hand_meta"]
        assert h["pose"] == "relaxed" and h["pending_review"]
        assert hm["handedness"] == ("right" if s == "r" else "left") and hm["use_side"] == s
        assert hm["palm_normal_sprite"][2] < 0, "re-inspected as the back of the hand"
        dec = r["hand_selection"]["decisions"][s]
        assert dec["ranking"][0]["palm"]["palm_class"] == "BACK_TO_CAMERA"
    # the r1c1 drawing (stored in the left group) is now offered on the right
    assert eng.hand_lib.hands["r"]["relaxed"].cid.endswith("/left_relaxed")
    assert switch_consistency(r, eng, True)["ok"]
    # production: nothing is approved and the original palm-forward hand is not a resting hand -> reported fallback
    p = eng.pose("relaxed_standing", allow_pending=False, skeleton_only=True)
    for s in ("r", "l"):
        assert p["hands"][s]["cid"] == "original"
        needs = {n["pose"]: n["status"] for n in p["hand_selection"]["decisions"][s]["needs_artwork"]}
        assert needs.get("relaxed") == "NEEDS_APPROVAL" and needs.get("open_palm") == "NEEDS_ARTWORK"
    assert switch_consistency(p, eng, False)["ok"]


@have_pkg
def test_requested_and_rendered_hands_match_in_a_full_render():
    """Rendered regression: relaxed, pointing and open palm on both hands; the switch the user or the engine asked
    for is the drawing that is drawn, wrists attach, pending drawings appear only in previews."""
    from autorig.motion.handcheck import switch_consistency, wrist_check
    from autorig.motion.pipeline import engine_for_package

    eng, vis = engine_for_package(PKG)
    sw = lambda pose: {g: {"user": pose, "auto": False, "keys": [], "offsets": {}} for g in ("hand_r", "hand_l")}
    for preset, switches, want in (("relaxed_standing", None, ("relaxed", "relaxed")),
                                   ("pointing_speaking", None, ("pointing", "relaxed")),
                                   ("relaxed_standing", sw("open_palm"), ("open_palm", "open_palm"))):
        r = eng.pose(preset, visible=vis, allow_pending=True, switches=switches)
        assert (r["hands"]["r"]["pose"], r["hands"]["l"]["pose"]) == want, preset
        c = switch_consistency(r, eng, True)
        assert c["ok"], c
        for s in ("r", "l"):
            if r["hands"][s]["cid"].startswith("store:"):
                assert wrist_check(r, eng, s)["status"] == "PASS", (preset, s)
    # a user choice that production cannot show is substituted with a note, never silently
    r = eng.pose("pointing_speaking", allow_pending=False, skeleton_only=True,
                 switches={"hand_r": {"user": "fist", "auto": False, "keys": [], "offsets": {}}})
    c = switch_consistency(r, eng, False)
    assert c["ok"] and c["sides"]["r"]["substituted"] and c["sides"]["r"]["note"]
    assert r["hands"]["r"]["cid"] == "original"


# -- shoulders and armpits --------------------------------------------------------------------------------------
@have_pkg
def test_shoulder_weights_are_continuous_across_the_layer_cut():
    from autorig.motion.pipeline import engine_for_package

    eng, _ = engine_for_package(PKG)
    ws = eng.weights
    cl = lambda cid, x, y: ws.explain(cid, (x, y))["weights"].get("clavicle_l", 0.0)
    # the same garment part on both sides of the Phase 1 torso / upper-arm cut (was 0.51 vs 0.99)
    assert abs(cl("torso", 645, 400) - cl("upper_arm_l", 650, 398)) < 0.2
    assert abs(cl("torso", 655, 402) - cl("upper_arm_l", 660, 400)) < 0.1


@have_pkg
def test_shoulder_candidate_changes_only_generated_pixels_and_keeps_the_t_pose():
    from autorig.garmentrig.shoulder import ShoulderExtensionRounding
    from autorig.motion.pipeline import engine_for_package
    from autorig.motion.solver import PoseParams
    from autorig.newchar_evidence import arm_fn

    eng, vis = engine_for_package(PKG)
    rgba, rep = ShoulderExtensionRounding().generate(eng)
    assert rep["changed_original_px"] == 0 and rep["changed_generated_px"] > 500
    eng.dfm.add_variant_layer("torso__alt_test_shoulder", rgba)
    eng.variants["torso__alt_test_shoulder"] = rgba
    order = list(vis)
    a = eng.dfm.render(PoseParams.identity(len(eng.sk.bones)), order, 0)["image"]
    b = eng.dfm.render(PoseParams.identity(len(eng.sk.bones)),
                       ["torso__alt_test_shoulder" if c == "torso" else c for c in order], 0)["image"]
    assert np.array_equal(a, b), "the T-pose composite is unchanged (the changed pixels are under the sleeve)"
    # arms down: fewer dark generated pixels exposed around the shoulders (the bar)
    fn = arm_fn({"r": {"abduction": 0.0, "flexion": 0.0, "wrist": 0.0},
                 "l": {"abduction": 0.0, "flexion": 0.0, "wrist": 0.0}}, True, eng.sk)

    def bar(lv):
        r = eng.pose("relaxed_standing", visible=vis, allow_pending=True, intent_fn=fn, layer_variants=lv)
        img, pad, J = r["image"], r["pad"], r["solve"]["joints_posed"]
        lum = img[..., :3].astype(float) @ [0.299, 0.587, 0.114]
        n = 0
        for s in ("r", "l"):
            x, y = (int(v) + pad for v in J[f"upper_arm_{s}"])
            win = (slice(y - 60, y - 10), slice(x - 50, x + 50))
            n += int(((lum[win] < 45) & (img[win][..., 3] > 200) & r["render"]["generated_visible"][win]).sum())
        return n
    assert bar({"torso": "torso__alt_test_shoulder"}) < bar(None)


@have_pkg
def test_armpit_gusset_backs_the_sleeve_edge():
    from autorig.garmentrig.correctives import CorrectiveArtworkStore, CorrectiveRuntime
    from autorig.motion.pipeline import engine_for_package
    from autorig.newchar_evidence import arm_fn
    from scipy import ndimage

    eng, vis = engine_for_package(PKG)
    eng.correctives = CorrectiveRuntime(eng, CorrectiveArtworkStore(PKG))
    fn = arm_fn({"r": {"abduction": 120.0, "flexion": 0.0, "wrist": 0.0},
                 "l": {"abduction": 10.0, "flexion": 5.0, "wrist": 0.0}}, True, eng.sk)
    on = eng.pose("relaxed_standing", visible=vis, allow_pending=True, intent_fn=fn, corrective_mode="all")
    off = eng.pose("relaxed_standing", visible=vis, allow_pending=True, intent_fn=fn, corrective_mode="off")
    A, O = on["image"], off["image"]
    J, pad = on["solve"]["joints_posed"], on["pad"]
    yy, xx = np.mgrid[:A.shape[0], :A.shape[1]]
    near = np.hypot(xx - J["upper_arm_r"][0] - pad, yy - J["upper_arm_r"][1] - pad) < 160
    gus = (O[..., 3] < 128) & (A[..., 3] > 200) & near
    assert gus.sum() > 200, "the raised arm opens an armpit gap that the gusset fills"
    seam = ndimage.binary_dilation(gus, iterations=3) & (A[..., 3] > 10) & (A[..., 3] < 235)
    assert seam.sum() < 25, f"unbacked semi-transparent seam pixels: {int(seam.sum())} (Phase 3.8: 55)"


# -- knees ------------------------------------------------------------------------------------------------------
@have_pkg
def test_knee_limits_hold_in_the_weight_shift():
    from autorig.motion.pipeline import engine_for_package
    from autorig.performance import scenarios
    from autorig.performance.director import PerformanceDirector

    eng, vis = engine_for_package(PKG)
    d = PerformanceDirector(eng, vis, scenarios.acting_upgrade())
    feet0 = None
    for t in (0.0, 0.67, 1.0, 1.33, 2.33, 3.0, 4.67, 6.0):
        s = d.solve(t)["solve"]
        assert not [v for v in s["violations"] if "knee" in v], (t, s["violations"])
        feet = np.array([s["joints_posed"]["foot_r"], s["joints_posed"]["foot_l"]])
        if feet0 is None:
            feet0 = feet
        assert np.abs(feet - feet0).max() < 0.5, "planted feet do not slide"


@have_sb
def test_schoolboy_acting_is_unchanged_by_the_knee_fix():
    from autorig.motion.pipeline import engine_for_package
    from autorig.performance import scenarios
    from autorig.performance.director import PerformanceDirector

    eng, vis = engine_for_package(SCHOOLBOY)
    d = PerformanceDirector(eng, vis, scenarios.acting_upgrade())
    for t in (0.0, 1.0, 2.33, 3.67, 5.0):
        s = d.solve(t)["solve"]
        assert not s["violations"] and abs(s["balance_error_px"]) < 0.5 and not s.get("balance_limited_by_legs")


# -- garment semantics ------------------------------------------------------------------------------------------
@have_pkg
def test_garment_slots_get_measured_semantics_without_character_labels():
    from autorig.motion.pipeline import engine_for_package

    eng, _ = engine_for_package(PKG)
    st = eng.dfm.structure
    assert st.part("tie").to_json()["label"] == "necklace" and st.part("tie").cls == "RIGID"
    assert st.part("sweater").to_json()["label"] == "shirt front"
    if (SCHOOLBOY / "natural_pose" / "rig.json").is_file():
        st2 = engine_for_package(SCHOOLBOY)[0].dfm.structure
        assert st2.part("tie").to_json()["label"] == "tie" and st2.part("sweater").to_json()["label"] == "sweater"


# -- non-destructive candidates ---------------------------------------------------------------------------------
@have_pkg
def test_artwork_candidates_are_proposals_only():
    import hashlib

    from autorig import visualfix as V
    from autorig.motion.pipeline import engine_for_package

    src = (PKG / "source" / "original.jpg").read_bytes()
    eng, _ = engine_for_package(PKG)
    e = V.eye_white_candidate(eng)
    for s in ("r", "l"):
        assert e[s]["before_dark_fraction"] > 0.9 and min(e[s]["candidate_rgb"]) > 200
    w = V.wrist_scale_candidates(eng)
    assert w and all(o["op"]["type"] == "handstore.placement" and o["proposed_scale"] > 1.0 for o in w)
    h = V.hem_mask_candidates(eng)
    assert {o["transfer_to"] for o in h["ops"]} == {"foot_r", "foot_l"}
    assert hashlib.sha256((PKG / "source" / "original.jpg").read_bytes()).digest() == hashlib.sha256(src).digest()
    assert not (PKG / "editor" / "project.json").is_file() or '"ops": []' in (PKG / "editor" / "project.json").read_text()
