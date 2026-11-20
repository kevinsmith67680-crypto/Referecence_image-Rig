"""Phase 1 pipeline on the SYNTHETIC ground-truth character (fixture provider).

These tests measure accuracy against exact ground truth. They validate the
pipeline; they are NOT an evaluation of the user's real reference character,
which is not in the repository (see docs/phase1_report.md)."""

import json
import math
import shutil

import numpy as np
import pytest
from PIL import Image

from autorig.phase1 import masks as M
from autorig.phase1 import schemas
from autorig.phase1.project import AnalysisConfig, ProjectExistsError, analyze_character
from autorig.phase1.source import sha256_file
from autorig.segmentation.sam3 import FixtureSegmentationProvider
from autorig.synthetic import concept_masks, render

REQUIRED = ["source/original.png", "analysis/anatomy.json", "analysis/face.json", "analysis/live_readiness.json",
            "analysis/live_controls.json", "artwork/layers.json", "previews/original.png", "previews/segmentation.png",
            "previews/facial_segmentation.png", "previews/reconstructed.png", "previews/difference.png",
            "metadata.json"]


@pytest.fixture(scope="session")
def char():
    return render()


@pytest.fixture(scope="session")
def image_path(char, tmp_path_factory):
    p = tmp_path_factory.mktemp("src") / "synthetic.png"
    char.image.save(p)
    return p


@pytest.fixture(scope="session")
def project(char, image_path, tmp_path_factory):
    out = tmp_path_factory.mktemp("proj") / "character_project"
    before = sha256_file(image_path)
    res = analyze_character(image_path, out, FixtureSegmentationProvider(concept_masks(char)),
                            AnalysisConfig(character_id="synthetic"))
    assert sha256_file(image_path) == before
    return res


def degraded(char, image_path, tmp_path, **kw):
    out = tmp_path / "proj"
    return analyze_character(image_path, out, FixtureSegmentationProvider(concept_masks(char), **kw),
                             AnalysisConfig(character_id="degraded"))


# --- structure, schemas, provenance -------------------------------------------------------

def test_project_structure(project):
    for rel in REQUIRED:
        assert (project.root / rel).is_file(), rel
    assert any((project.root / "masks" / "body").glob("*.png")) and any((project.root / "masks" / "face").glob("*.png"))
    assert any((project.root / "artwork" / "bitmap").glob("*.png"))
    assert (project.root / "artwork" / "vector").is_dir()


def test_all_outputs_validate_against_exported_schemas(project):
    assert project.valid, project.schema_errors
    files = {"anatomy": "analysis/anatomy.json", "face": "analysis/face.json",
             "live_readiness": "analysis/live_readiness.json", "live_controls": "analysis/live_controls.json",
             "hybrid_artwork": "artwork/layers.json", "character_project": "metadata.json"}
    for name, rel in files.items():
        doc = json.loads((project.root / rel).read_text())
        assert schemas.validate(name, doc) == [], (name, schemas.validate(name, doc)[:3])


def test_exported_schema_files_in_sync():
    from pathlib import Path

    base = Path(__file__).resolve().parents[1] / "src" / "autorig" / "data" / "schemas"
    for name, schema in schemas.SCHEMAS.items():
        assert json.loads((base / f"{name}.schema.json").read_text()) == schema, name


def test_metadata_records_outputs_and_hashes(project):
    meta = project.metadata
    listed = {o["path"]: o for o in meta["outputs"]}
    for rel in REQUIRED[:-1]:
        assert rel in listed
        assert listed[rel]["sha256"] == sha256_file(project.root / rel)
    assert meta["segmentation_runs"][0]["provider"] == "fixture"
    assert meta["cost"]["this_run_estimated_usd"] == 0.0


# --- source & coordinate preservation -------------------------------------------------------

def test_source_preserved_byte_for_byte(project, image_path):
    assert (project.root / "source" / "original.png").read_bytes() == image_path.read_bytes()
    assert project.metadata["source_unchanged_after_processing"] is True
    assert project.metadata["source"]["byte_identical_copy"] is True


def test_layers_keep_coordinates_and_original_pixels(project, char):
    src = np.asarray(char.image.convert("RGBA"))
    layers = json.loads((project.root / "artwork" / "layers.json").read_text())["layers"]
    for layer in layers:
        img = np.asarray(Image.open(project.root / layer["image"]).convert("RGBA"))
        assert img.shape == src.shape, layer["id"]
        a = img[..., 3] > 0
        assert np.array_equal(img[a], src[a]), f"{layer['id']} altered source colours"
        assert layer["transform"]["scale"] == 1.0 and layer["transform"]["offset"] == [0, 0]
        mask = M.load_mask(project.root / layer["mask"])
        assert mask.shape == src.shape[:2]


def test_layers_form_a_partition_and_recompose_exactly(project):
    f = project.metadata["fidelity"]
    assert f["overlap_pixels"] == 0
    assert f["max_abs_error_inside_layers"] == 0.0
    assert f["coverage_ratio"] == 1.0 and f["uncovered_foreground_pixels"] == 0
    assert f["linework_retained_ratio"] == 1.0


# --- anatomy accuracy vs ground truth --------------------------------------------------------

def test_joint_accuracy_and_honest_flags(project, char):
    joints = project.documents["anatomy"]["joints"]
    errs = {n: math.dist(j["position"], char.joints[n]) for n, j in joints.items()}
    assert np.median(list(errs.values())) <= 10.0, errs
    for n, j in joints.items():
        if j["reliable"]:
            assert errs[n] <= 25.0, (n, errs[n], j["method"])
    # straight limbs: elbows/knees cannot be located precisely -> must be flagged, not faked
    for n in ("elbow_r", "elbow_l", "knee_r", "knee_l"):
        assert joints[n]["reliable"] is False and joints[n]["flags"]


def test_side_convention(project):
    j = project.documents["anatomy"]["joints"]
    for base in ("shoulder", "wrist", "hip", "ankle"):
        assert j[f"{base}_r"]["position"][0] < j[f"{base}_l"]["position"][0]


def test_hidden_joints_report_occlusion(project):
    j = project.documents["anatomy"]["joints"]
    assert "upper_clothing" in j["shoulder_r"]["occluded_by"]  # shoulder under the sleeve


def test_body_part_masks_vs_ground_truth(project, char):
    ious = {p: M.iou(M.load_mask(project.root / "masks" / "body" / f"{p}.png"), gt) for p, gt in char.anatomical.items()}
    assert np.mean(list(ious.values())) >= 0.88, ious
    for p in ("hair", "head", "hand_r", "hand_l", "foot_r", "foot_l", "neck"):
        assert ious[p] >= 0.95, (p, ious[p])
    for p, info in project.documents["anatomy"]["parts"].items():
        assert not info["empty"], p


def test_clothing_vs_anatomy_recorded(project):
    regions = project.documents["anatomy"]["clothing_regions"]
    assert {"torso", "upper_arm_r", "upper_arm_l"} <= set(regions["upper_clothing"])
    assert {"pelvis", "thigh_r", "thigh_l"} <= set(regions["lower_clothing"])


# --- face --------------------------------------------------------------------------------------

def test_facial_landmarks_vs_ground_truth(project, char):
    L = project.documents["face"]["landmarks"]
    gt = char.landmarks
    for key, tol in (("eye_center_r", 3), ("eye_center_l", 3), ("pupil_center_r", 3), ("pupil_center_l", 3),
                     ("brow_center_r", 5), ("brow_center_l", 5), ("nose_tip", 3), ("ear_center_r", 4),
                     ("ear_center_l", 4)):
        assert math.dist(L[key], gt[key]) <= tol, (key, L[key], gt[key])
    assert L["mouth_state"] == "closed_line"


def test_facial_masks_vs_ground_truth(project, char):
    face = project.documents["face"]["features"]
    for fid in ("eye_r", "eye_l", "brow_r", "brow_l", "nose", "mouth", "pupil_r", "pupil_l"):
        m = M.load_mask(project.root / face[fid]["mask"])
        assert M.iou(m, char.face[fid]) >= 0.95, fid


def test_missing_facial_artwork_detected(project):
    face = project.documents["face"]
    missing = {m["artwork"] for m in face["missing_artwork"]}
    assert {"closed_eye_r", "closed_eye_l", "open_mouth_with_interior", "phoneme_mouth_set",
            "three_quarter_and_profile_head_views"} <= missing
    assert face["visibility"]["mouth_interior"]["status"] == "not_visible_mouth_closed"
    assert face["visibility"]["upper_eyelid_r"]["status"] == "outline_only_not_separable"


# --- readiness & live controls -----------------------------------------------------------------

def test_readiness_never_claims_ready_without_artwork(project):
    caps = project.documents["live_readiness"]["capabilities"]
    assert caps["blinking"]["status"] == "REQUIRES_NEW_ARTWORK"
    assert caps["mouth_opening"]["status"] == "REQUIRES_NEW_ARTWORK"
    assert caps["lip_sync"]["status"] == "REQUIRES_NEW_ARTWORK"
    assert caps["head_turning"]["status"] == "REQUIRES_NEW_ARTWORK"
    assert caps["eye_direction"]["status"] == "REQUIRES_RECONSTRUCTION"
    assert caps["eyebrow_movement"]["status"] == "REQUIRES_RECONSTRUCTION"
    assert all(v["status"] != "READY" for v in caps.values())
    assert (project.root / "analysis" / "live_readiness.md").read_text().startswith("# Live animation readiness")


def test_live_control_spec(project):
    doc = project.documents["live_controls"]
    ids = [c["id"] for c in doc["controls"]]
    expected = {"head_yaw", "head_pitch", "head_roll", "eye_blink_left", "eye_blink_right", "eye_look_x",
                "eye_look_y", "brow_raise_left", "brow_raise_right", "brow_frown", "jaw_open", "mouth_width",
                "mouth_pucker", "smile_left", "smile_right", "expression_neutral", "expression_angry",
                "expression_sad", "expression_surprised", "expression_happy"}
    assert set(ids) == expected and len(ids) == len(set(ids))
    for c in doc["controls"]:
        lo, hi = c["value_range"]
        assert lo < hi and lo <= c["neutral"] <= hi
    show = doc["moho_showtime"]
    assert show["verified_mappings"] == []
    assert all(m["status"] == "proposed_unverified" for m in show["proposed_mappings"])


# --- protection of existing work ----------------------------------------------------------------

def test_refuses_to_overwrite(project, char, image_path, tmp_path):
    prov = FixtureSegmentationProvider(concept_masks(char))
    with pytest.raises(ProjectExistsError, match="overwrite=True"):
        analyze_character(image_path, project.root, prov)
    user_dir = tmp_path / "users_own_folder"
    user_dir.mkdir()
    (user_dir / "notes.txt").write_text("precious")
    with pytest.raises(ProjectExistsError, match="not an AutoRig"):
        analyze_character(image_path, user_dir, prov, AnalysisConfig(overwrite=True))
    assert (user_dir / "notes.txt").read_text() == "precious"


# --- degraded segmentations are reported, not hidden ---------------------------------------------

def test_eroded_masks_report_uncovered_pixels(char, image_path, tmp_path):
    res = degraded(char, image_path, tmp_path, degrade=lambda m: M.erode(m, 3))
    f = res.metadata["fidelity"]
    assert res.valid
    assert f["uncovered_foreground_pixels"] > 0 and f["coverage_ratio"] < 1.0
    assert f["max_abs_error_inside_layers"] == 0.0  # still no altered pixels
    diff = np.asarray(Image.open(res.root / "previews" / "difference.png"))
    assert ((diff == [255, 0, 255]).all(axis=2)).any(), "uncovered pixels must be visible in the heatmap"


def test_missing_brows_change_readiness(char, image_path, tmp_path):
    res = degraded(char, image_path, tmp_path, drop={"eyebrow"})
    caps = res.documents["live_readiness"]["capabilities"]
    assert caps["eyebrow_movement"]["status"] == "REQUIRES_NEW_ARTWORK"
    assert res.documents["face"]["visibility"]["brow_r"]["status"] == "not_detected"


def test_pupils_recovered_by_intensity_when_not_segmented(char, image_path, tmp_path):
    res = degraded(char, image_path, tmp_path, drop={"pupil"})
    f = res.documents["face"]
    assert f["features"]["pupil_r"]["method"] == "intensity_within_eye"
    assert math.dist(f["landmarks"]["pupil_center_r"], char.landmarks["pupil_center_r"]) <= 4
    assert res.documents["face"]["visibility"]["pupil_r"]["status"] == "derived_by_intensity"


def test_low_quality_facial_segmentation_rejected(char, image_path, tmp_path):
    concepts = concept_masks(char)
    concepts["eye"] = [char.anatomical["head"]]  # garbage: "eye" covering the whole head
    out = tmp_path / "proj"
    res = analyze_character(image_path, out, FixtureSegmentationProvider(concepts), AnalysisConfig())
    face = res.documents["face"]
    assert "eye_r" not in face["features"] and "eye_l" not in face["features"]
    assert any(r["label"] == "eye" and "plausible" in r["reason"] for r in face["rejected_candidates"])
    assert res.documents["live_readiness"]["capabilities"]["blinking"]["status"] == "NEEDS_REVIEW"


@pytest.mark.parametrize("name, kw", [
    ("eroded_3px", dict(degrade=lambda m: M.erode(m, 3))),
    ("eroded_6px", dict(degrade=lambda m: M.erode(m, 6))),
    ("dilated_8px", dict(degrade=lambda m: M.dilate(m, 8))),
    ("no_hand_shoe", dict(drop={"hand", "shoe"})),
])
def test_reliable_means_reliable_under_degraded_masks(char, image_path, tmp_path, name, kw):
    """Invariant: degraded evidence may make joints UNRELIABLE, never confidently wrong."""
    res = degraded(char, image_path, tmp_path, **kw)
    for n, j in res.documents["anatomy"]["joints"].items():
        if j["reliable"]:
            assert math.dist(j["position"], char.joints[n]) <= 15.0, (name, n, j["method"], j["position"])
