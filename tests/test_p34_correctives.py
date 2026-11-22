"""Phase 3.4 corrective artwork: store and provenance, generators, original-pixel protection, gap and seam
reduction on the real character, pose-space activation, evaluator calibration, decontamination, editor ops/API.

Numerical checks only: the generated drawings are pending review and their visual quality is judged by a person
(evidence in natural_pose/correctives/)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from autorig.garmentrig.correctives import (CorrectiveArtworkPlanner, CorrectiveArtworkStore, CorrectiveAsset,
                                            CorrectiveRuntime, EdgeDecontamination, PoseSpaceCorrectionEngine,
                                            approval_state, generate_all)
from autorig.garmentrig.corrective_evidence import CorrectiveQualityEvaluator, light_specks
from autorig.phase2.skinning import PoseParams

ROOT = Path(__file__).resolve().parents[1]
REAL_PKG = ROOT / "projects" / "schoolboy_tpose" / "character_package"
real = pytest.mark.skipif(not (REAL_PKG / "natural_pose" / "rig.json").is_file(), reason="real package missing")


def _ignore(d, names):
    skip = {"editor", "motion", "ik_switch", "garments", "evidence", "evidence_v2",
            "correctives", "corrective_assets.json", "corrective_requirements.json", "pose_space_correctives.json",
            "corrective_quality.json"}
    if Path(d).name == "natural_pose":
        skip |= {"poses", "legacy", "closeups", "contact_sheet.png", "contact_sheet_diagnostic.png"}
    return [n for n in names if n in skip]


@pytest.fixture(scope="module")
def pkg(tmp_path_factory):
    dst = tmp_path_factory.mktemp("p34") / "pkg"
    shutil.copytree(REAL_PKG, dst, ignore=_ignore)
    return dst


@pytest.fixture(scope="module")
def engvis(pkg):
    from autorig.motion.pipeline import engine_for_package
    eng, vis = engine_for_package(pkg)
    store = CorrectiveArtworkStore(pkg)
    made = generate_all(eng, store, vis, progress=lambda *_: None)
    eng.correctives = CorrectiveRuntime(eng, CorrectiveArtworkStore(pkg))
    return eng, vis, made


def _fk(**sides):
    from autorig.garmentrig.evidence import fk_state
    return fk_state(sides)


def _pose(eng, vis, preset="relaxed_standing", mode="all", decont=False, evaluate=False, **kw):
    ov = None if decont else {"edge_decontamination": False}
    return eng.pose(preset, visible=vis, evaluate_garments=evaluate, corrective_mode=mode, corrective_overrides=ov, **kw)


# -- store and provenance ------------------------------------------------------------------------------------------
def test_store_is_append_only_and_verifies_hashes(tmp_path):
    st = CorrectiveArtworkStore(tmp_path)
    img = np.zeros((8, 8, 4), np.uint8)
    img[2:6, 2:6] = (10, 20, 30, 255)
    mk = lambda: CorrectiveAsset(asset_id="t", category="knees", body_part="knee_r", side="r", method="m",
                                 provenance="GEOMETRIC_CORRECTION", attach_bone="thigh_r",
                                 placement={"mode": "after_patches", "group": "leg_r"}, drivers=["knee_r"],
                                 activation={"kind": "ramp", "driver": "knee_r", "lo": 10, "hi": 20})
    a1 = st.add(mk(), {"main": img})
    a2 = st.add(mk(), {"main": img})
    assert (a1.version, a2.version) == (1, 2) and a1.file != a2.file
    assert [a.version for a in CorrectiveArtworkStore(tmp_path).latest()] == [2]
    assert st.verify() == []
    assert approval_state({}, a2) == "pending_review", "nothing is approved by default"
    assert approval_state({"corrective:t@v1": {"status": "approved"}}, a2) == "pending_review", \
        "each version is reviewed separately"
    (tmp_path / a1.file).write_bytes(b"tampered")
    assert st.verify() == [a1.file]


@real
def test_generators_build_pending_geometric_assets(engvis, pkg):
    eng, vis, made = engvis
    ids = {a.asset_id for a in made}
    assert {"shoe_top_r", "shoe_top_l", "elbow_cap_r", "elbow_cap_l", "knee_cap_r", "knee_cap_l",
            "armpit_gusset_r", "armpit_gusset_l", "edge_decontamination"} <= ids
    for a in made:
        assert a.provenance == "GEOMETRIC_CORRECTION", "no AI model is called and nothing is marked original"
        assert approval_state(eng.approvals, a) == "pending_review"
        assert a.moho.get("status") == "proposed_unverified"
    arm = next(a for a in made if a.asset_id == "armpit_gusset_r")
    assert arm.params["procedural"] and len(arm.keys) >= 3, "baked key previews for review and the Moho bake"
    idx = json.loads((pkg / "analysis" / "corrective_assets.json").read_text())
    assert all((pkg / f).is_file() for a in idx["assets"] for f in a["sha256"])


@real
def test_pending_correctives_are_only_previewed(engvis):
    eng, vis, _ = engvis
    r = _pose(eng, vis, mode="auto", ik=_fk(r=(130.0, 5.0)))
    assert not any(c["shown"] for c in r["correctives"]), "auto mode shows approved assets only"
    r = _pose(eng, vis, mode="auto", allow_pending=True, ik=_fk(r=(130.0, 5.0)))
    assert any(c["shown"] and c["asset_id"] == "armpit_gusset_r" for c in r["correctives"])


# -- original pixels -----------------------------------------------------------------------------------------------
@real
@pytest.mark.parametrize("preset,kw", [("relaxed_standing", {}), ("relaxed_standing", {"ik": "arm120"}),
                                       ("natural_squat", {}), ("relaxed_standing", {"ik": "elbow95"})])
def test_correctives_never_change_original_pixels(engvis, preset, kw):
    eng, vis, _ = engvis
    ik = {"arm120": _fk(r=(120.0, 5.0)), "elbow95": _fk(r=(15.0, 95.0))}.get(kw.get("ik"))
    off = _pose(eng, vis, preset, mode="off", ik=ik)
    on = _pose(eng, vis, preset, mode="all", ik=ik)
    q = CorrectiveQualityEvaluator().evaluate(off, on, None)
    assert q["checks"]["B_leakage"]["protected_violation_px"] == 0
    assert q["review_required"] is True


@real
def test_identity_pose_stays_bit_exact_and_decontamination_reproduces_the_source(engvis):
    eng, vis, _ = engvis
    P = PoseParams.identity(len(eng.sk.bones))
    order = [c for c in vis if c in eng.dfm.info]
    r0 = eng.dfm.render(P, order, 0)
    assert np.array_equal(r0["image"][r0["image"][..., 3] == 255][:, :3],
                          eng.source[r0["image"][..., 3] == 255][:, :3]), "identity render equals the source"
    dec = eng.correctives.decont_pre()
    saved = {c: eng.dfm.info[c]["pre"] for c in dec}
    try:
        for c, p in dec.items():
            eng.dfm.info[c]["pre"] = p
        r1 = eng.dfm.render(P, order, 0)
    finally:
        for c, p in saved.items():
            eng.dfm.info[c]["pre"] = p
    _, rep = EdgeDecontamination().apply(dict(eng.layers), eng.source)
    B = np.array(rep["background_rgb"], float)
    over = lambda im: im[..., :3] * (im[..., 3:4] / 255.0) + B * (1 - im[..., 3:4] / 255.0)
    d = np.abs(over(r0["image"].astype(float)) - over(r1["image"].astype(float))).max(-1)
    assert d.max() <= 6.0, "over the paper the decontaminated layers reproduce the source (rounding only)"
    assert (d > 0).sum() > 1000, "and the edges did change"


@real
def test_decontamination_removes_light_specks_over_dark(engvis):
    eng, vis, _ = engvis
    a = _pose(eng, vis, ik=_fk(r=(15.0, 95.0)))
    b = _pose(eng, vis, ik=_fk(r=(15.0, 95.0)), decont=True)
    assert light_specks(b["image"]) < 0.5 * light_specks(a["image"])


# -- defects on the real character -----------------------------------------------------------------------------
@real
def test_armpit_gusset_closes_the_raised_arm_gap_and_the_gap_is_still_detected_without_it(engvis):
    eng, vis, _ = engvis
    off = _pose(eng, vis, mode="off", ik=_fk(r=(120.0, 5.0)))
    on = _pose(eng, vis, mode="all", ik=_fk(r=(120.0, 5.0)))
    assert len(off["tears"]["exposed_gap"]) >= 15, "gap sensitivity is unchanged: the real gap is still found"
    assert sum(off["render"]["clipped_uncovered"].values()) > 800
    assert len(on["tears"]["exposed_gap"]) <= 3
    assert sum(on["render"]["clipped_uncovered"].values()) <= 150
    info = on["render"]["gap_fill_info"]["armpit_gusset_r"]
    assert info["chord_px"] > 6 and info["px"] > 300


@real
def test_both_arms_raised_gaps_are_filled(engvis):
    eng, vis, _ = engvis
    off = _pose(eng, vis, "both_hands_raised", mode="off")
    on = _pose(eng, vis, "both_hands_raised", mode="all")
    assert len(off["tears"]["exposed_gap"]) > 30
    assert len(on["tears"]["exposed_gap"]) <= 5


@real
def test_elbow_cap_removes_the_clipped_outer_elbow(engvis):
    eng, vis, _ = engvis
    off = _pose(eng, vis, mode="off", ik=_fk(r=(15.0, 95.0)))
    on = _pose(eng, vis, mode="all", ik=_fk(r=(15.0, 95.0)))
    assert off["render"]["clipped_uncovered"].get("elbow_r", 0) > 300
    assert on["render"]["clipped_uncovered"].get("elbow_r", 0) <= 60


@real
def test_deep_elbow_still_needs_artwork(engvis):
    eng, vis, _ = engvis
    r = _pose(eng, vis, mode="all", ik=_fk(r=(15.0, 135.0)))
    assert r["feasibility"]["status"] == "NEEDS_ARTWORK", "a cap is not the missing deep-elbow drawing"


@real
def test_knee_crease_seams_and_squat_gaps(engvis):
    eng, vis, _ = engvis
    on = _pose(eng, vis, "natural_squat", mode="all")
    assert len(on["tears"]["visible_seam"]) <= 15, "the shin segment no longer cuts straight across the knee"
    sit = _pose(eng, vis, "sitting", mode="all")
    assert sit["feasibility"]["status"] == "NEEDS_ARTWORK", "seated thighs point at the viewer: still a drawing"


@real
def test_shoe_top_is_drawn_beneath_the_foot_and_the_trouser(engvis):
    eng, vis, _ = engvis
    r = _pose(eng, vis, mode="all")
    shown = {c["asset_id"] for c in r["correctives"] if c["shown"]}
    assert {"shoe_top_r", "shoe_top_l"} <= shown
    assert r["render"]["protected_overlap_prevented_px"] < 50


@real
def test_head_tilt_keeps_jaw_fragments_on_the_head(engvis):
    from scipy import ndimage
    eng, vis, _ = engvis
    r = _pose(eng, vis, "side_lean", mode="off")
    a = r["image"][..., 3] >= 128
    lab, n = ndimage.label(a)
    sizes = ndimage.sum(a, lab, range(1, n + 1))
    assert sizes.sum() - sizes.max() < 20, "no detached outline fragments next to the jaw"
    J, pad = r["solve"]["joints_posed"]["neck"], r["pad"]
    near = [p for p in r["tears"]["exposed_gap"] if np.hypot(p[0] - pad - J[0], p[1] - pad - J[1]) < 140]
    assert len(near) <= 12, f"neck/jaw gap points: {len(near)} (Phase 3.3 baseline: 23 at the side lean)"


# -- evaluator calibration (Step 2) --------------------------------------------------------------------------------
@real
def test_standing_collar_is_not_a_failure_and_categories_are_recorded(engvis):
    eng, vis, _ = engvis
    r = _pose(eng, vis, mode="off", evaluate=True)
    parts = r["garment_eval"]["parts"]
    for p in ("lapels", "shirt_collar"):
        assert parts[p]["status"] != "FAIL", (p, parts[p]["checks"])
        assert parts[p]["review_required"] is True and "categories" in parts[p]
    r = _pose(eng, vis, mode="off", evaluate=True, ik=_fk(r=(120.0, 5.0)))
    assert r["garment_eval"]["parts"]["sleeve_upper_r"]["status"] in ("NEEDS_ARTWORK", "FAIL"), \
        "the genuine armpit gap is still reported when no corrective is shown"


# -- pose-space activation ------------------------------------------------------------------------------------------
def _asset(aid, kind, **act):
    return CorrectiveAsset(asset_id=aid, category="knees", body_part="x", side="r", method="m",
                           provenance="GEOMETRIC_CORRECTION", attach_bone="thigh_r",
                           placement={"mode": "after_patches", "group": "leg_r"}, drivers=[act.get("driver", "")],
                           activation={"kind": kind, **act})


def test_pose_space_ramp_is_zero_at_neutral_smooth_and_monotonic():
    a = _asset("k", "ramp", driver="knee_r", lo=30.0, hi=50.0)
    eng = PoseSpaceCorrectionEngine()
    ws = [eng.activate([a], {"knee_r": v})[0]["weight"] for v in np.linspace(0, 80, 81)]
    assert ws[0] == 0.0 and ws[-1] == 1.0 and all(b >= a_ - 1e-12 for a_, b in zip(ws, ws[1:]))
    assert max(abs(b - a_) for a_, b in zip(ws, ws[1:])) < 0.1, "no popping: per-degree change is small"


def test_pose_space_keyed_rbf_priority_exclusive_and_overrides():
    k = _asset("g", "keyed", driver="abduction_r", lo=100.0, hi=112.0, scales={"abduction_r": 15.0})
    k.keys = [{"key_id": f"a{v}", "values": {"abduction_r": float(v)}} for v in (115, 130, 145)]
    k.exclusive_group = "armpit_r"
    k2 = _asset("g2", "ramp", driver="abduction_r", lo=100.0, hi=110.0)
    k2.exclusive_group, k2.priority = "armpit_r", 3
    eng = PoseSpaceCorrectionEngine()
    r = {x["asset_id"]: x for x in eng.activate([k, k2], {"abduction_r": 130.0})}
    assert r["g"]["keys"][0]["key_id"] == "a130" and abs(sum(x["w"] for x in r["g"]["keys"]) - 1) < 0.2
    assert r["g2"]["weight"] == 0.0 and "suppressed_by_exclusive_group" in r["g2"]["flags"]
    assert eng.activate([k], {"abduction_r": 0.0})[0]["weight"] == 0.0, "neutral: weight 0"
    far = eng.activate([k], {"abduction_r": 190.0})[0]
    assert "unsupported_combination" in far["flags"]
    ov = eng.activate([k], {"abduction_r": 130.0}, {"g": False})[0]
    assert ov["weight"] == 0.0 and "artist_override" in ov["flags"]


@real
def test_user_settings_disable_and_scale_correctives(engvis):
    eng, vis, _ = engvis
    base = _pose(eng, vis, ik=_fk(r=(15.0, 95.0)))
    off = _pose(eng, vis, ik=_fk(r=(15.0, 95.0)), corrective_settings={"elbow_cap_r": {"enabled": False}})
    half = _pose(eng, vis, ik=_fk(r=(15.0, 95.0)), corrective_settings={"elbow_cap_r": {"strength": 0.5}})
    w = lambda r: next((c["weight"] for c in r["correctives"] if c["asset_id"] == "elbow_cap_r"), 0.0)
    assert w(base) == pytest.approx(1.0) and w(off) == 0.0 and w(half) == pytest.approx(0.5)


@real
def test_rendering_is_deterministic(engvis):
    eng, vis, _ = engvis
    a = _pose(eng, vis, ik=_fk(r=(130.0, 20.0)), decont=True)
    b = _pose(eng, vis, ik=_fk(r=(130.0, 20.0)), decont=True)
    assert np.array_equal(a["image"], b["image"])


@real
def test_armpit_sweep_has_no_popping(engvis):
    eng, vis, _ = engvis
    px = [_pose(eng, vis, ik=_fk(r=(float(a), 5.0)))["render"]["corrective_px"] for a in range(90, 151, 10)]
    d = np.abs(np.diff(px))
    assert d.max() <= max(1500, 4 * np.median(d) + 1), px


# -- planner -----------------------------------------------------------------------------------------------------
@real
def test_planner_records_requirements_without_inventing_artwork(engvis):
    eng, vis, _ = engvis
    from autorig.motion.switches import correction_readings_from_pose
    ins = []
    for name, kw in (("arm120", {"ik": _fk(r=(120.0, 5.0))}), ("elbow135", {"ik": _fk(r=(15.0, 135.0))})):
        r = _pose(eng, vis, mode="off", **kw)
        ins.append({"pose": name, "result": {**r, "readings": correction_readings_from_pose(eng.solver, r["pose"])}})
    reqs = {r["id"]: r for r in CorrectiveArtworkPlanner().plan(ins)}
    assert "REQ-armpit_r" in reqs and reqs["REQ-armpit_r"]["correction_type"] == "geometric_extension"
    assert any(r["correction_type"] == "replacement_drawing" for r in reqs.values())
    assert all(r["treated_as_original"] is False for r in reqs.values())


# -- editor ---------------------------------------------------------------------------------------------------------
def test_project_ops_and_migration(tmp_path):
    from autorig.editor.project import OpError, _apply_correctives, default_corrective_state, migrate
    st = {"correctives": default_corrective_state()}
    _apply_correctives(st, {"type": "correctives.set", "asset": "knee_cap_r", "strength": 0.4, "lo": 20, "hi": 40})
    _apply_correctives(st, {"type": "correctives.mode", "mode": "all"})
    assert st["correctives"]["settings"]["knee_cap_r"] == {"strength": 0.4, "lo": 20.0, "hi": 40.0}
    for bad in ({"type": "correctives.set", "asset": "x", "strength": 2},
                {"type": "correctives.set", "asset": "x", "lo": 50, "hi": 40},
                {"type": "correctives.mode", "mode": "always"}):
        with pytest.raises(OpError):
            _apply_correctives(st, bad)
    _apply_correctives(st, {"type": "correctives.reset", "asset": "knee_cap_r"})
    assert st["correctives"]["settings"] == {}
    assert migrate({"schema_version": 5, "ops": []})["schema_version"] == 7  # migrates through v6 to v7


@real
def test_http_correctives_api_approval_and_replacement(engvis, pkg):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import base64
    import io
    import warnings

    from fastapi.testclient import TestClient
    from PIL import Image

    from autorig.editor.server import create_app
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        c = TestClient(create_app(pkg))
    info = c.get("/api/correctives").json()
    a = next(x for x in info["assets"] if x["asset_id"] == "knee_cap_r")
    assert a["state"] == "pending_review" and a["replaceable"]
    assert c.get("/api/correctives/image", params={"path": a["images"][0]["file"]}).status_code == 200
    assert c.get("/api/correctives/image", params={"path": "../metadata.json"}).status_code == 404
    r = c.post("/api/op", json={"type": "approval.set", "item": a["approval_key"], "status": "approved"})
    assert r.status_code == 200
    assert next(x for x in c.get("/api/correctives").json()["assets"] if x["asset_id"] == "knee_cap_r")["state"] == "approved"
    assert c.post("/api/op", json={"type": "correctives.set", "asset": "knee_cap_r", "strength": 0.5}).status_code == 200
    assert c.post("/api/undo").status_code == 200
    # manual artwork: same canvas size, new version, ARTIST_EDITED, pending again
    img = np.asarray(Image.open(pkg / a["images"][0]["file"]).convert("RGBA")).copy()
    img[..., :3] = 255 - img[..., :3]
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, "PNG")
    rr = c.post("/api/correctives/replace", json={"asset": "knee_cap_r",
                                                  "png": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()})
    assert rr.status_code == 200, rr.json()
    assert rr.json()["provenance"] == "ARTIST_EDITED"
    a2 = next(x for x in c.get("/api/correctives").json()["assets"] if x["asset_id"] == "knee_cap_r")
    assert a2["version"] == a["version"] + 1 and a2["state"] == "pending_review"
    bad = c.post("/api/correctives/replace", json={"asset": "armpit_gusset_r", "png": base64.b64encode(buf.getvalue()).decode()})
    assert bad.status_code == 400, "procedural assets are edited through their settings"
    p = c.post("/api/pose", json={"preset": "relaxed_standing", "corrective_mode": "all", "corrective_compare": True,
                                  "allow_pending": True}).json()
    assert p["corrective_before"].startswith("data:image/png") and "correctives" in p
