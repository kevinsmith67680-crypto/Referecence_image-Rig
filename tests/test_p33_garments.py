"""Phase 3.3 garment rig: structure, pivots, weight meshes, structured deformer, corrections, evaluator, editor ops.

Real-character tests use a copy of the schoolboy T-pose package; the synthetic Phase 2 package checks that the
garment rig also builds for another character. Mathematical checks never claim visual quality (the renders are
in natural_pose/garments/ for review)."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest

from autorig.garmentrig.deform import clip_stretch, local_stretch
from autorig.garmentrig.evaluate import THRESHOLDS
from autorig.garmentrig.structure import FLEXIBLE, LIMITED, RIGID, align_pivots
from autorig.garmentrig.weights import validate_mesh
from autorig.phase2.skinning import PoseParams

ROOT = Path(__file__).resolve().parents[1]
REAL_PKG = ROOT / "projects" / "schoolboy_tpose" / "character_package"
SYN_PKG = ROOT / "examples" / "phase2_synthetic_character" / "character_package"
real = pytest.mark.skipif(not (REAL_PKG / "natural_pose" / "rig.json").is_file(), reason="real package missing")


def _ignore(d, names):
    skip = {"editor", "motion", "ik_switch", "garments", "evidence", "evidence_v2"}
    if Path(d).name == "natural_pose":
        skip |= {"poses", "legacy", "closeups", "contact_sheet.png", "contact_sheet_diagnostic.png"}
    return [n for n in names if n in skip]


@pytest.fixture(scope="module")
def pkg(tmp_path_factory):
    dst = tmp_path_factory.mktemp("p33") / "pkg"
    shutil.copytree(REAL_PKG, dst, ignore=_ignore)
    return dst


@pytest.fixture(scope="module")
def eng(pkg):
    from autorig.motion.pipeline import engine_for_package
    return engine_for_package(pkg)[0]


def _fk(**sides):
    from autorig.garmentrig.evidence import fk_state
    return fk_state(sides)


# -- pure maths -------------------------------------------------------------------------------------------------
def test_local_stretch_and_clip_on_synthetic_maps():
    h, w = 40, 60
    yy, xx = np.mgrid[:h, :w].astype(float)
    ident = np.stack([xx, yy], -1)
    s = local_stretch(ident)
    assert np.allclose(s[1:-1, 1:-1], 1.0)
    # a 5x horizontal stretch of the drawn pixels: the rest position changes 5x slower than the output
    stretched = np.stack([xx / 5.0, yy], -1)
    assert np.allclose(local_stretch(stretched)[1:-1, 1:-1], 5.0)
    lay = np.ones((h, w, 4), np.float32)
    out, mask = clip_stretch(lay, stretched, 3.0)
    assert mask[5:-5, 5:-5].all() and out[10, 10, 3] == 0.0
    out, mask = clip_stretch(lay, ident, 3.0)
    assert not mask.any() and np.array_equal(out, lay)


# -- real character ---------------------------------------------------------------------------------------------
@real
def test_shoulder_pivots_are_aligned_and_validated(eng):
    rep = {r["joint"]: r for r in eng.pivot_report}
    for s in "rl":
        r = rep[f"shoulder_{s}"]
        assert r["aligned"] and 20 <= r["lift_px"] <= 0.6 * r["half_thickness_px"] + 1
        ua = eng.sk.bone(f"upper_arm_{s}")
        assert np.allclose(ua.head, r["pivot"], atol=0.1)
        assert np.allclose(eng.sk.bone(f"clavicle_{s}").tail, ua.head)  # chain stays connected
        assert abs(ua.axis_offset) > 5  # anatomical axis kept (old joint -> elbow)
    for r in eng.pivot_report:
        if "checks" in r:
            assert r["checks"]["inside_silhouette"] and not r["checks"]["at_layer_bbox_centre"], r


@real
def test_editor_edited_bones_keep_their_pivot(eng):
    import copy
    sk = copy.deepcopy(eng.sk)
    for b in sk.bones:
        if b.name in ("upper_arm_r",):
            b.method = "editor"
    sk2, rep = align_pivots(sk, eng.masks)
    r = next(x for x in rep if x.get("joint") == "shoulder_r")
    assert r["aligned"] is False and np.allclose(sk2.bone("upper_arm_r").head, sk.bone("upper_arm_r").head)


@real
def test_garment_parts_classes_and_restrictions(eng):
    st = eng.structure
    names = {p.name: p for p in st.parts}
    for n in ("blazer_body", "lapels", "shirt_collar", "badge", "tie", "button_1", "button_2", "sleeve_upper_r",
              "elbow_r", "sleeve_fore_r", "shirt_cuff_r", "trouser_thigh_r", "knee_r", "trouser_cuff_r", "shoe_r",
              "blazer_hem", "waistband", "pocket_flap_r"):
        assert n in names, n
    assert names["badge"].cls == RIGID and names["button_1"].cls == RIGID and names["shoe_r"].cls == RIGID
    assert names["blazer_body"].cls == LIMITED and names["lapels"].cls == LIMITED and names["waistband"].cls == LIMITED
    assert names["sleeve_upper_r"].cls == FLEXIBLE and names["knee_r"].cls == FLEXIBLE and names["elbow_r"].fold
    # semantic boundaries differ from layer boundaries: the blazer hem lives in the pelvis and both thigh layers
    assert {"pelvis", "thigh_r", "thigh_l"} <= set(names["blazer_hem"].layers)
    # the lapels and blazer body may never follow an arm bone
    assert not any(b.startswith(("upper_arm", "forearm")) for b in names["lapels"].bones + names["blazer_body"].bones)
    js = st.to_json()
    assert js["schema"] == "autorig.garment_structure" and js["attachments"] and js["fold_regions"]


@real
def test_weight_meshes_are_normalised_restricted_and_locked(eng):
    v = eng.weights.validate()
    assert v["ok"], v["issues"][:5]
    m = eng.weights.meshes["torso"]
    rng = np.random.default_rng(1)
    pts = np.stack([rng.uniform(m.origin[0], m.origin[0] + m.spacing * (m.shape[1] - 1), 500),
                    rng.uniform(m.origin[1], m.origin[1] + m.spacing * (m.shape[0] - 1), 500)], 1)
    w = m.weights_at(pts)
    assert np.allclose(w.sum(0), 1.0, atol=1e-5) and (w >= -1e-7).all()  # bilinear keeps normalisation
    # rigid decorative parts are locked to one weight vector
    bi = eng.structure.index["badge"]
    sel = (m.part == bi) & m.active
    assert sel.any() and m.locked[sel].all()
    assert np.ptp(m.w[:, sel], axis=1).max() < 1e-6


@real
def test_weight_ops_respect_locks_and_restrictions_and_replay_deterministically(eng):
    from autorig.garmentrig.weights import apply_op
    import copy
    ws = eng.weights
    m = copy.deepcopy(ws.meshes["torso"])
    badge = eng.structure.part("badge")
    # a brush only over the locked badge changes nothing (and says so)
    with pytest.raises(ValueError):
        apply_op(m, {"type": "weights.paint", "layer": "torso", "bone": "chest", "center": badge.centroid,
                     "radius": 3.0, "strength": 1.0}, eng.sk, eng.structure)
    # painting an arm bone on the blazer body is refused (restriction zone)
    body = eng.structure.part("blazer_body")
    with pytest.raises(ValueError):
        apply_op(m, {"type": "weights.paint", "layer": "torso", "bone": "upper_arm_r", "center": body.centroid,
                     "radius": 30.0, "strength": 1.0, "part": "blazer_body"}, eng.sk, eng.structure)
    bi = eng.structure.index["blazer_body"]
    vy, vx = np.argwhere((m.part == bi) & m.active & ~m.locked)[len(np.argwhere((m.part == bi) & m.active)) // 3]
    spot = [float(v) for v in m.vertex_xy()[vy, vx]]
    apply_op(m, {"type": "weights.paint", "layer": "torso", "bone": "clavicle_r", "center": spot,
                 "radius": 30.0, "strength": 0.8}, eng.sk, eng.structure)
    assert validate_mesh(m, eng.structure, eng.sk) == []
    k = m.bones.index("clavicle_r")
    k0 = ws.meshes["torso"].bones.index("clavicle_r")
    c = np.array([spot])
    assert m.weights_at(c)[k, 0] > ws.meshes["torso"].weights_at(c)[k0, 0] + 0.05
    # deterministic replay: the same ops give bit-identical weights
    ops = [{"type": "weights.smooth", "layer": "upper_arm_r", "center": list(eng.structure.part("elbow_r").centroid),
            "radius": 20.0, "strength": 0.5}]
    lay = {c: inf["rgba"] for c, inf in eng.dfm.info.items()}
    ws.set_ops(ops, lay)
    a = ws.meshes["upper_arm_r"].w.copy()
    ws.set_ops(ops, lay)
    assert np.array_equal(a, ws.meshes["upper_arm_r"].w)
    ws.set_ops([], lay)
    eng.dfm.refresh_weights()
    ex = ws.explain("upper_arm_r", eng.structure.part("elbow_r").centroid)
    assert ex["part"] == "elbow_r" and "forearm_r" in ex["allowed_bones"] and ex["why"]


@real
def test_identity_and_source_untouched(eng, pkg):
    import hashlib
    from PIL import Image
    src = (pkg / "source" / "original.png").read_bytes()
    N = len(eng.sk.bones)
    r = eng.dfm.render(PoseParams.identity(N), [c for c in eng.layers], 0)
    for c in eng.layers:
        inf = eng.dfm.info[c]
        x0, y0 = inf["origin"]
        a = r["alphas"][c][y0:y0 + inf["alpha"].shape[0], x0:x0 + inf["alpha"].shape[1]]
        assert np.array_equal(a, inf["pre"][..., 3] > 0.5)
    assert hashlib.sha256((pkg / "source" / "original.png").read_bytes()).digest() == hashlib.sha256(src).digest()
    del Image


@real
def test_neutral_pose_hidden_pixels_go_under_their_occluder_and_seams_close(eng):
    vis = [c for c in eng.layers]
    r = eng.pose("relaxed_standing", visible=[c for c in vis if c in eng.layers])
    pieces = {p["piece"] for p in r["render"]["pieces"]}
    assert "hidden_under:foot_r" in pieces and "hidden_under:foot_l" in pieces  # trouser extension under the shoe
    assert {"segment:sleeve_upper_r", "segment:sleeve_fore_r", "segment:trouser_shin_r", "fit:badge"} <= pieces
    assert len(r["tears"]["exposed_gap"]) <= 8 and len(r["tears"]["visible_seam"]) <= 4
    ge = r["garment_eval"]
    assert ge["visual_review_required"] is True and ge["garments"]["tie"]["status"] == "PASS"
    for s in "rl":  # cuffs stay rigid on the forearm
        assert ge["cuffs"][s]["displacement_px"] <= THRESHOLDS["cuff_displacement_px"][0]
    for n in ("badge", "button_1", "button_2", "tie"):
        assert ge["parts"][n]["checks"]["residual_max_px"]["grade"] == "PASS", (n, ge["parts"][n])


@real
def test_elbow_sweep_moderate_bend_is_clean_and_deep_bend_needs_artwork(eng):
    vis = [c for c in eng.layers]
    r45 = eng.pose("relaxed_standing", visible=vis, ik=_fk(r=(15.0, 45.0)))
    e45 = r45["garment_eval"]["parts"]["elbow_r"]
    assert e45["status"] in ("PASS", "WARNING") and e45["checks"]["clipped_patch_px"]["value"] <= 60
    r135 = eng.pose("relaxed_standing", visible=vis, ik=_fk(r=(15.0, 135.0)))
    assert r135["feasibility"]["status"] == "NEEDS_ARTWORK"
    assert r135["garment_eval"]["parts"]["elbow_r"]["status"] == "NEEDS_ARTWORK"


@real
def test_raised_arms_report_the_gusset_instead_of_smearing(eng):
    vis = [c for c in eng.layers]
    r = eng.pose("both_hands_raised", visible=vis)
    assert r["feasibility"]["status"] == "NEEDS_ARTWORK"
    p = r["garment_eval"]["parts"]["sleeve_upper_r"]
    assert p["status"] == "NEEDS_ARTWORK" and any("gusset" in m for m in p["missing_artwork"])
    assert r["render"]["clipped_uncovered"].get("sleeve_upper_r", 0) > 500  # the wedge is left open, not smeared


@real
def test_garment_correction_controller_ranges_blend_and_never_approves(eng):
    cs = eng.corr_switch
    cs.reset()
    rec = {c["id"]: c for c in cs.evaluate({"elbow_r": 110.0, "ankle_r": 9.0, "sitting": 1.0}, stateful=False)}
    e = rec["elbow_deep_r"]
    assert e["on"] and e["angle_range"] == [95.0, 135.0] and e["method"] == "blend"
    assert 0.0 < e["target_blend"] < 1.0 and e["blend_weight"] == 0.0  # missing drawing: never applied
    assert e["approval"] == "missing" and e["missing_dependencies"] == ["corrective:elbow_deep_r"]
    assert rec["hem_on_shoe_r"]["on"] and rec["sitting_trousers"]["required"]
    off = {c["id"]: c for c in cs.evaluate({"elbow_r": 30.0}, stateful=False)}
    assert off["elbow_deep_r"]["handling"] == "continuous deformation"
    # smooth: the target blend rises monotonically over the range (no popping)
    vals = [next(c for c in cs.evaluate({"elbow_r": v}, stateful=False) if c["id"] == "elbow_deep_r")["target_blend"]
            for v in np.linspace(100, 140, 9)]
    assert all(b >= a for a, b in zip(vals, vals[1:])) and vals[-1] == 1.0


@real
def test_hand_switch_never_moves_the_cuff(eng):
    vis = [c for c in eng.layers]
    store = [v for vs in eng.hand_lib.candidates["r"].values() for v in vs if v.cid.startswith("store:")]
    assert store
    r = eng.pose("relaxed_standing", visible=vis, allow_pending=True, hand_layers={"r": store[0].layer})
    assert r["garment_eval"]["cuffs"]["r"]["displacement_px"] <= THRESHOLDS["cuff_displacement_px"][0]


@real
def test_weight_edits_are_project_ops_and_survive_reload(pkg):
    from autorig.editor.project import VERSION, EditorProject, OpError
    p = EditorProject(pkg)
    assert VERSION == 5
    n0 = len(p.state()["garment_weights"]["ops"])
    p.submit({"type": "weights.paint", "layer": "torso", "bone": "clavicle_r", "center": [470.0, 520.0],
              "radius": 25.0, "strength": 0.4})
    with pytest.raises(OpError):
        p.submit({"type": "weights.paint", "layer": "torso", "bone": "clavicle_r", "radius": 25.0})  # no center
    q = EditorProject(pkg)
    ops = q.state()["garment_weights"]["ops"]
    assert len(ops) == n0 + 1 and ops[-1]["bone"] == "clavicle_r"
    q.undo()
    assert len(q.state()["garment_weights"]["ops"]) == n0


@real
def test_legacy_mode_is_still_available_for_comparison(pkg):
    from autorig.motion.pipeline import engine_for_package
    e, vis = engine_for_package(pkg, garment_mode="legacy")
    assert e.structure is None and e.gdef.bands
    r = e.pose("relaxed_standing", visible=vis)
    assert "garment_eval" not in r and r["image"].shape[2] == 4


@pytest.mark.skipif(not (SYN_PKG / "metadata.json").is_file(), reason="synthetic package missing")
def test_synthetic_character_builds_a_valid_garment_rig(tmp_path):
    from autorig.motion.pipeline import engine_for_package
    dst = tmp_path / "syn"
    shutil.copytree(SYN_PKG, dst, ignore=_ignore)
    e, vis = engine_for_package(dst)
    assert e.weights.validate()["ok"]
    r = e.pose("relaxed_standing", visible=vis)
    assert r["garment_eval"]["available"] and r["image"][..., 3].any()
