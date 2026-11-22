"""Phase 3.8: new-character generalisation (tough_student).

Unit tests of the code added for an unfamiliar character (dark-background hand sheets, the spatial paper rule, opt-in
outline completion, the import report, the outline-emblem badge rule) and integration checks on the committed
tough_student package when it is present. The schoolboy regression is the rest of the suite.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from autorig.handsheet import background, background_dark, build_pack, sheet_polarity, unmatte_dark
from autorig.handstore import read_zip, validate_pack
from autorig.newchar import image_import_report
from autorig.phase1 import compositor as C

ROOT = Path(__file__).resolve().parents[1]
TS = ROOT / "references" / "tough_student"
GRID = TS / "hand_grid"
SHEET, INSPECTION = GRID / "hand_grid_black.jpg", GRID / "inspection.json"
ZIP = GRID / "AutoRig_Hand_Library_ToughStudent_v1.zip"
PKG = ROOT / "projects" / "tough_student_test" / "character_package"
SCHOOLBOY = ROOT / "projects" / "schoolboy_tpose" / "character_package"
have_grid = pytest.mark.skipif(not (SHEET.is_file() and ZIP.is_file()), reason="tough_student hand grid not present")
have_pkg = pytest.mark.skipif(not (PKG / "natural_pose" / "rig.json").is_file(), reason="tough_student package absent")


# -- dark-background hand sheets ----------------------------------------------------------------------------
def _dark_sheet(rim_gap: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """A peach disc with a 6 px black outline and a 1 px grey rim on a black sheet; a gap in the rim on the left.
    Returns the sheet and the true drawing (disc + outline)."""
    H = W = 120
    yy, xx = np.mgrid[:H, :W]
    d = np.hypot(xx - 60, yy - 60)
    rgb = np.zeros((H, W, 3), np.uint8)
    rgb[d <= 30] = (250, 190, 150)  # skin
    rgb[(d > 30) & (d <= 36)] = (6, 4, 2)  # outline
    rim = (d > 36) & (d <= 37.2)
    if rim_gap:
        rim &= ~((xx < 40) & (abs(yy - 60) < 6))
    rgb[rim] = (80, 76, 67)
    return rgb, d <= 36


def test_sheet_polarity():
    assert sheet_polarity(np.zeros((40, 40, 3), np.uint8)) == "dark"
    assert sheet_polarity(np.full((40, 40, 3), 250, np.uint8)) == "light"


def test_dark_background_keeps_the_outline_and_never_leaks_through_a_rim_gap():
    rgb, truth = _dark_sheet()
    bg, info, rim = background_dark(rgb)
    fg = ~bg
    assert info["mode"] == "dark" and info["outline_band_px"] >= 6
    assert (fg & truth).sum() == truth.sum(), "every outline and skin pixel is drawing"
    # where the rim is missing the band stops at the measured outline width: the drawing grows at most a few px
    extra = fg & ~truth & ~rim
    assert extra.sum() < 0.02 * truth.sum()
    # a global "delete dark pixels" rule would have removed the whole outline
    dark = (rgb.astype(int).sum(-1) < 60)
    assert (truth & dark).sum() > 1000 and (fg & truth & dark).sum() == (truth & dark).sum()


def test_unmatte_dark_leaves_no_light_halo():
    rgb, truth = _dark_sheet(rim_gap=False)
    bg, info, rim = background_dark(rgb)
    from scipy import ndimage

    lab, _ = ndimage.label(~bg)
    comp = lab == lab[60, 60]
    rgba, q = unmatte_dark(rgb, comp, bg, rim)
    assert q["light_px_on_edge"] == 0 and q["skin_on_edge_px"] == 0 and q["outline_coverage"] > 0.99
    assert (rgba[..., 3][truth] == 255).all()
    edge = (rgba[..., 3] > 0) & (rgba[..., 3] < 255)
    assert edge.any() and (rgba[edge][:, :3].astype(int).sum(-1) < 60).all(), "rim -> outline colour, half alpha"


def test_white_sheet_path_is_unchanged():
    rgb = np.full((60, 60, 3), 255, np.uint8)
    rgb[20:40, 20:40] = (10, 10, 10)
    bg, info = background(rgb)
    assert set(info) == {"enclosed_gap_px"} and not bg[30, 30] and bg[0, 0]


@have_grid
def test_committed_tough_student_pack_is_reproducible(tmp_path):
    insp = json.loads(INSPECTION.read_text())
    r = build_pack(SHEET, INSPECTION, tmp_path, "tough_student_hands_v1", [], ZIP.name)
    assert (tmp_path / ZIP.name).read_bytes() == ZIP.read_bytes(), "regenerate with autorig hand-sheet (pack drifted)"
    m, assets, errs, warns = validate_pack(read_zip(ZIP.read_bytes()))
    assert errs == [] and len(assets) == 16 and m["intended_use"] == insp["intended_use"]
    assert m["extraction"]["polarity"] == "dark"
    assert r["summary"]["not_mirrored_pairs"] == ["pointing"] and r["summary"]["derived_assets"] == ["left_pointing"]
    for name, c in r["cells"].items():
        q = c["quality"]
        assert q["light_px_on_edge"] == 0 and q["skin_on_edge_px"] == 0 and q["outline_coverage"] >= 0.98, name
    for a in assets:
        assert a["approval_status"] == "NEEDS_REVIEW"


@have_grid
def test_dark_extraction_matches_the_same_drawings_on_a_white_sheet():
    """The black sheet holds the sleeve-free drawings recoloured: the two independent extractions agree."""
    v2 = ROOT / "references" / "schoolboy" / "hand_pack_v2" / "sprites"
    if not v2.is_dir():
        pytest.skip("schoolboy sprites absent")
    for cell in ("r1c2", "r2c4", "r4c3"):
        a = np.asarray(Image.open(GRID / "sprites" / f"{cell}.png"))[..., 3] > 127
        b = np.asarray(Image.open(v2 / f"{cell}.png"))[..., 3] > 127
        assert abs(int(a.sum()) - int(b.sum())) / b.sum() < 0.02, cell


# -- Phase 1 spatial paper rule and opt-in outline completion -----------------------------------------------
def _paper_figure() -> np.ndarray:
    rgba = np.full((80, 80, 4), 255, np.uint8)
    rgba[..., :3] = 250
    rgba[10:70, 10:70, :3] = 8  # outline box
    rgba[16:64, 16:64, :3] = (30, 40, 90)  # navy fill
    rgba[30:44, 30:44, :3] = 252  # enclosed white (a shirt / a shoe)
    return rgba


def test_border_paper_is_spatial():
    paper, info = C.border_paper(_paper_figure())
    assert info["method"] == "border_connected_paper"
    assert paper[0, 0] and not paper[36, 36], "enclosed white is character, whatever its colour"
    rgba = _paper_figure()
    rgba[0, 0, 3] = 0
    assert C.border_paper(rgba)[0] is None, "real transparency: no paper rule"


def test_remove_border_paper_and_complete_outline():
    rgba = _paper_figure()
    paper, _ = C.border_paper(rgba)
    mask = np.zeros((80, 80), bool)
    mask[14:66, 14:66] = True  # clipped 4 px inside the outline on every side
    mask[0:3, 0:3] = True  # swallowed paper
    out, removed = C.remove_border_paper({"a": mask}, paper)
    assert removed == 9 and not out["a"][0, 0]
    done, info = C.complete_outline(out, rgba, paper, 5)
    want = np.zeros((80, 80), bool)
    want[10:70, 10:70] = True
    assert not (done["a"] & ~want).any(), "nothing outside the drawn outline"
    assert done["a"][10, 40] and done["a"][69, 40] and done["a"][40, 10] and done["a"][40, 69], "edges completed"
    left = want & ~done["a"]  # only the square corners, beyond the octagonal (Euclidean-like) 5 px reach
    assert left.sum() <= 4 * 6 and info["outline_pixels_completed"] == int(done["a"].sum() - out["a"].sum())
    # depth limit: a 6 px deep clip is not completed beyond 5 px
    short, _ = C.complete_outline({"a": mask & ~paper}, rgba, paper, 2)
    assert short["a"].sum() < want.sum()


def test_image_import_report_checks_genuine_transparency(tmp_path):
    rgba = np.full((30, 30, 4), 255, np.uint8)
    p = tmp_path / "opaque_rgba.png"
    Image.fromarray(rgba, "RGBA").save(p)
    r = image_import_report(p)
    assert r["mode"] == "RGBA" and r["alpha"]["has_alpha_band"] and not r["alpha"]["genuine_transparency"]
    rgba[0, 0, 3] = 0
    Image.fromarray(rgba, "RGBA").save(p)
    assert image_import_report(p)["alpha"]["genuine_transparency"]
    if SHEET.is_file():
        r = image_import_report(SHEET, "hand_sheet")
        assert r["background"]["polarity"] == "dark" and r["background"]["uniform"] and not r["alpha"]["has_alpha_band"]


# -- the committed new-character package ---------------------------------------------------------------------
@have_pkg
def test_new_character_background_extraction():
    from autorig.newchar import background_extraction_qa

    q = background_extraction_qa(PKG)
    assert q["background_included_px"] == 0 and q["clipped_outline_px"] == 0 and q["missing_px"] < 100
    assert q["decontaminated"]["light_fringe_ratio"] < 0.01
    assert all(r["covered_by_layers"] == 1.0 for r in q["enclosed_light_regions"]), "white clothing kept"


@have_pkg
def test_crest_is_a_rigid_chest_badge_and_schoolboy_decor_is_unchanged():
    from autorig.motion.pipeline import engine_for_package

    eng, _ = engine_for_package(PKG)
    st = eng.dfm.structure
    assert "badge" in st.decor and st.part("badge").cls == "RIGID"
    ys, xs = np.nonzero(st.decor["badge"])
    assert 590 < xs.mean() < 650 and 500 < ys.mean() < 570, "the crest on the character's left chest"
    if (SCHOOLBOY / "natural_pose" / "rig.json").is_file():
        eng2, _ = engine_for_package(SCHOOLBOY)
        assert {k: int(v.sum()) for k, v in eng2.dfm.structure.decor.items()} == {
            "badge": 2436, "button_1": 259, "button_2": 278, "tie": 1112, "pocket_flap_r": 1271,
            "pocket_flap_l": 1267}


@have_pkg
def test_cuff_cover_drops_generated_pixels_beyond_the_cuff_and_shoe_tops_take_the_shoe_colour():
    """Two defects the new character exposed: (1) Phase 2's generated forearm extension under the original hand (a
    white rounded cuff end on this character) was kept by the stored-hand cuff cover and drawn over every library
    hand; (2) the shoe-top extension sampled only dark pixels and came out dark grey under white trainers."""
    from autorig.garmentrig.correctives import ShoeTopRecovery
    from autorig.handstore import reference_wrist
    from autorig.motion.pipeline import engine_for_package

    eng, _ = engine_for_package(PKG)
    for s in ("r", "l"):
        cov = eng.variants[f"forearm_{s}__hscuff"]
        lay = eng.layers[f"forearm_{s}"]
        gen = (lay[..., 3] > 0) & (np.abs(lay[..., :3].astype(int) - eng.source[..., :3].astype(int)).max(-1) > 0)
        ref = reference_wrist(eng.sk, eng.layers, s, None, eng.source)
        W, u = np.asarray(ref["wrist_joint"]), np.asarray(ref["axis"])
        yy, xx = np.mgrid[:gen.shape[0], :gen.shape[1]]
        beyond = (xx - W[0]) * u[0] + (yy - W[1]) * u[1] > ref["cuff_edge_t_px"] + 1.0
        assert (gen & beyond).sum() > 100, "the package has a generated extension past the cuff (the case tested)"
        assert ((cov[..., 3] > 0) & gen & beyond).sum() == 0, "no generated pixel past the cuff edge is kept"
        a, imgs = ShoeTopRecovery().generate(eng, s)
        m = imgs["main"][..., 3] > 0
        lum = imgs["main"][..., :3][m].astype(float) @ np.array([0.299, 0.587, 0.114])
        assert np.median(lum) > 150, "white trainers get a light shoe-top extension"


def test_character_scoped_names_and_view_sources():
    from autorig.artistic.alternatives import ViewAwareRigController
    from autorig.refmotion.evidence import _character_id

    if (SCHOOLBOY / "metadata.json").is_file():
        assert _character_id(SCHOOLBOY) == "schoolboy"  # legacy id kept: the stored round trip keeps its name
    if (PKG / "metadata.json").is_file():
        assert _character_id(PKG) == "tough_student"
        views = ViewAwareRigController(PKG, ["torso"]).manifest()["views"]
        assert all(v.get("source_drawing") is None for v in views.values()), "no other character's sheet is cited"


def test_phase38_status_vocabulary():
    """Review/production feasibility classes map onto the Phase 3.8 vocabulary without hiding pending artwork."""
    from autorig.newchar_evidence import status_38

    assert status_38("SUPPORTED", "SUPPORTED", []) == "SUPPORTED"
    assert status_38("SUPPORTED_WITH_CORRECTIONS", "SUPPORTED_WITH_CORRECTIONS", []) == "SUPPORTED_WITH_CORRECTIONS"
    assert status_38("SUPPORTED", "SUPPORTED", ["hand:point (r)"]) == "NEEDS_APPROVAL"
    assert status_38("REQUIRES_NEW_VIEW", "REQUIRES_NEW_VIEW", []) == "REQUIRES_ALTERNATIVE_ARTWORK"
    assert status_38("SUPPORTED", "REQUIRES_ALTERNATIVE_ARTWORK", []) == "NEEDS_APPROVAL"
    assert status_38("UNSUPPORTED", "UNSUPPORTED", []) == "UNSUPPORTED"
