"""Phase 2 package built from the committed Phase 1 SYNTHETIC example (fixture
segmentation, deterministic reconstruction). Proves pipeline behaviour on
labelled synthetic data only - not real cartoon artwork, not generative models."""

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from autorig.phase1 import masks as M
from autorig.phase1.source import sha256_file
from autorig.phase2 import review as R
from autorig.phase2 import schemas
from autorig.phase2.pipeline import PackageError, Phase2Config, prepare_character
from autorig.phase2.store import ArtworkStore

P1 = Path(__file__).resolve().parents[1] / "examples" / "phase1_synthetic_character" / "character_project"
POSE_FILES = ["neutral", "arm_45", "arm_90", "left_arm_90", "elbows_bent", "walking", "sitting", "head_tilt_left",
              "head_tilt_right"]


@pytest.fixture(scope="session")
def pkg(tmp_path_factory):
    out = tmp_path_factory.mktemp("p2") / "character_package"
    before = {p: sha256_file(p) for p in P1.rglob("*") if p.is_file()}
    res = prepare_character(P1, out, None, Phase2Config(sweep_step=45, synthetic_ground_truth=True))
    after = {p: sha256_file(p) for p in P1.rglob("*") if p.is_file()}
    assert before == after, "Phase 1 project must not be modified"
    return res


def test_valid_and_structured(pkg):
    root = pkg["root"]
    assert pkg["valid"], pkg["errors"]
    for rel in ["source/original.png", "analysis/anatomy.json", "analysis/face.json", "analysis/occlusion.json",
                "analysis/joint_preparation.json", "analysis/live_controls.json", "analysis/live_artwork_manifest.json",
                "artwork/manifest.json", "reports/reconstruction_quality.json", "reports/visual_review.html",
                "reports/approvals.json", "metadata.json"] + [f"previews/{p}.png" for p in POSE_FILES]:
        assert (root / rel).is_file(), rel
    for d in ("artwork/original", "artwork/reconstructed", "artwork/vector", "masks/source", "masks/reconstruction",
              "masks/provenance"):
        assert (root / d).is_dir() and any((root / d).iterdir()), d
    for name, rel in (("occlusion", "analysis/occlusion.json"), ("joint_preparation", "analysis/joint_preparation.json"),
                      ("live_artwork_manifest", "analysis/live_artwork_manifest.json"),
                      ("artwork_manifest", "artwork/manifest.json"),
                      ("reconstruction_quality", "reports/reconstruction_quality.json"),
                      ("character_package", "metadata.json")):
        assert schemas.validate(name, json.loads((root / rel).read_text())) == [], name


def test_original_source_and_pixels_preserved(pkg):
    root = pkg["root"]
    assert (root / "source" / "original.png").read_bytes() == (P1 / "source" / "original.png").read_bytes()
    q = pkg["docs"]["reconstruction_quality"]["original_pixel_preservation"]
    assert q["source_unchanged"] and q["originals_verified"] and q["neutral_originals_exact"]
    assert q["generated_pixels_visible_in_neutral"] == 0
    assert pkg["store"].verify() == []


def test_layer_dimensions_and_alignment(pkg):
    root = pkg["root"]
    W, H = Image.open(root / "source" / "original.png").size
    for d in ("artwork/original", "artwork/reconstructed", "masks/reconstruction", "masks/provenance"):
        for p in (root / d).glob("*.png"):
            assert Image.open(p).size == (W, H), p
    svg = (root / "artwork" / "vector").glob("*.svg")
    for p in svg:
        assert f'viewBox="0 0 {W} {H}"' in p.read_text()


def test_reconstruction_masks_aligned_and_provenance(pkg):
    store = pkg["store"]
    regions = {r.id: r for r in pkg["regions"]}
    for e in store.extensions:
        prov = M.load_mask(store.root / e.provenance_mask)
        assert np.array_equal(prov, store.extension_rgba(e)[..., 3] > 0)
        assert not (prov & store.protected(e.component)).any()
        assert not (prov & ~regions[e.region_id].mask).any()
        assert e.status == "pending_review" and e.method == "deterministic_flat_fill"


def test_occlusion_graph_known_relations(pkg):
    occ = pkg["docs"]["occlusion"]
    front = {(e["front"], e["back"]) for e in occ["graph"]["edges"]}
    for pair in [("hair", "head_base"), ("upper_arm_r", "torso"), ("upper_arm_l", "torso"), ("head_base", "neck"),
                 ("foot_r", "shin_r"), ("eye_r_iris", "eye_r_white"), ("brow_l", "head_base"), ("hand_r", "forearm_r")]:
        assert pair in front, pair
    amb = {(e["front"], e["back"]) for e in occ["graph"]["edges"] if e["ambiguous"]}
    assert ("forearm_r", "upper_arm_r") in amb  # weak evidence must be flagged, not asserted
    order = occ["graph"]["z_order_back_to_front"]
    assert order.index("foot_r") > order.index("shin_r") and order.index("hair") > order.index("head_base")
    ids = {r["id"] for r in occ["regions"]}
    assert {"torso__under__upper_arm_r", "neck__under__head_base", "head_base__under__hair",
            "eye_r_white__under__eye_r_iris", "head_base__under__brow_r"} <= ids
    assert any("brow" in f and "hair" in f for f in occ["flags"])


def test_complex_regions_are_not_faked(pkg):
    strat = {r["region"]: r["strategy"] for r in pkg["recon"]}
    # sleeve+skin and sock+skin surroundings are not flat -> no deterministic fill, flagged instead
    assert strat["upper_arm_r__under__forearm_r"] == "generative_required_no_provider"
    assert strat["shin_r__under__foot_r"] == "generative_required_no_provider"
    flags = " ".join(f["item"] for f in pkg["docs"]["reconstruction_quality"]["flags_for_human_review"])
    assert "upper_arm_r__under__forearm_r" in flags


def test_hidden_region_reconstruction_vs_ground_truth(pkg):
    gt = pkg["docs"]["reconstruction_quality"]["synthetic_ground_truth"]["overall"]
    assert gt["precision"] >= 0.9 and gt["recall_within_regions"] >= 0.99 and gt["mean_colour_error"] <= 12


def test_joint_preparation(pkg):
    jp = pkg["docs"]["joint_preparation"]["joints"]
    assert len(jp) == 13
    for name, j in jp.items():
        assert j["pivot"] is not None and j["child_components"] and j["recommended_binding"]
        if not j["pivot_reliable"]:
            assert j["status"] == "needs_review"
    assert jp["wrist_r"]["recommended_binding"][0] == "rigid_layer_attachment"
    assert any("deformable" in b for b in jp["elbow_r"]["recommended_binding"])


def test_pose_previews_and_gap_detection(pkg):
    gaps = pkg["docs"]["reconstruction_quality"]["gaps"]["per_pose"]
    assert gaps["neutral"] == {"originals_only": 0, "with_extensions": 0}
    for pose in ("arm_90", "left_arm_90", "sitting", "head_tilt_left"):
        assert gaps[pose]["with_extensions"] < gaps[pose]["originals_only"], pose
    assert gaps["elbows_bent"]["with_extensions"] > 0  # unfilled elbow regions are reported, not hidden
    img = np.asarray(Image.open(pkg["root"] / "previews" / "arm_90.png"))
    assert img.shape[2] == 4 and (img[..., 3] == 0).any()  # transparent preview


def test_facial_completeness_and_live_manifest(pkg):
    live = pkg["docs"]["live_artwork_manifest"]
    c = live["controls"]
    assert len(c) == 15
    assert c["eye_blink_left"]["artwork_readiness"] == "REQUIRES_NEW_ARTWORK"
    assert c["jaw_open"]["artwork_readiness"] == "REQUIRES_NEW_ARTWORK"
    assert c["head_yaw"]["artwork_readiness"] == "REQUIRES_NEW_ARTWORK"
    assert c["eye_look_x"]["artwork_readiness"] == "NEEDS_REVIEW"  # sclera fill exists but is unreviewed
    assert all(v["user_approval_status"] == "not_reviewed" for v in c.values())
    assert {s["id"] for s in live["switch_drawing_slots"]} >= {"eye_l_closed", "eye_r_closed", "mouth_open",
                                                              "head_turn_left"}
    assert all(s["status"] == "missing" for s in live["switch_drawing_slots"])
    phase1_ids = {x["id"] for x in json.loads((pkg["root"] / "analysis" / "live_controls.json").read_text())["controls"]}
    assert set(c) <= phase1_ids  # compatible with the Phase 1 LiveControlSpec


def test_vectors_are_labelled_generated(pkg):
    vecs = pkg["docs"]["artwork_manifest"]["vectors"]
    assert vecs and all(v["exact_reconstruction"] is False and v["status"] == "pending_review" for v in vecs)


def test_approval_workflow_and_rollback(pkg, tmp_path):
    root = tmp_path / "copy"
    shutil.copytree(pkg["root"], root)
    doc = json.loads((root / "reports" / "approvals.json").read_text())
    assert not any(i["status"] == "approved" for i in doc["items"])
    with pytest.raises(ValueError, match="unreviewed"):
        R.approve_phase(root)
    ext = next(i["id"] for i in doc["items"] if i["type"] == "generated_extension")
    R.apply_decision(root, ext, "approve")
    store = ArtworkStore(root, 1000, 1500)
    assert next(e for e in store.extensions if e.id == ext).status == "approved"
    with pytest.raises(ValueError, match="cannot be approved"):
        R.apply_decision(root, "switch:eye_l_closed", "approve")
    comp = ext.split("__")[0]
    orig_before = store.originals[comp]["pixel_sha256"]
    R.apply_decision(root, comp, "restore_original") if comp in [i["id"] for i in doc["items"]] else None
    store.rollback(comp)
    store.save()
    store = ArtworkStore(root, 1000, 1500)
    assert not any(e.component == comp for e in store.extensions)
    assert store.originals[comp]["pixel_sha256"] == orig_before and store.verify() == []


def test_refuses_foreign_or_existing_output(pkg, tmp_path):
    with pytest.raises(PackageError, match="overwrite"):
        prepare_character(P1, pkg["root"], None, Phase2Config(sweep_step=90))
    foreign = tmp_path / "mine"
    foreign.mkdir()
    (foreign / "notes.txt").write_text("keep")
    with pytest.raises(PackageError, match="not an AutoRig"):
        prepare_character(P1, foreign, None, Phase2Config(sweep_step=90, overwrite=True))
    assert (foreign / "notes.txt").read_text() == "keep"


def test_reproducible(pkg, tmp_path):
    again = prepare_character(P1, tmp_path / "again", None, Phase2Config(sweep_step=45))
    a, b = pkg["store"], again["store"]
    assert {c: r["pixel_sha256"] for c, r in a.originals.items()} == {c: r["pixel_sha256"] for c, r in b.originals.items()}
    ea = {e.id: (e.pixels, e.provenance.get("fill_rgb")) for e in a.extensions}
    eb = {e.id: (e.pixels, e.provenance.get("fill_rgb")) for e in b.extensions}
    assert ea == eb
    for e in a.extensions:
        assert np.array_equal(a.extension_rgba(e), b.extension_rgba(next(x for x in b.extensions if x.id == e.id)))
