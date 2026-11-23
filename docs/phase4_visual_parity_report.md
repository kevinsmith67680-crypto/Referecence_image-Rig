# Phase 4: visual parity report

**Status: NOT EXECUTED.** Visual parity compares Moho renders with AutoRig's own renders of the same pose. Moho has now
run (2026-11-23, build and save only), but no frame was rendered. Nothing below is a parity result.

The only native image is Moho's own thumbnail inside the saved document (`preview.jpg`, 512×288). It is a capture of
the editing viewport, with the grid, the bone overlay and Moho's own pan and zoom, so it cannot be compared pixel for
pixel. Qualitatively, it shows the arm artwork lowered along the arm bone.

## 1. Method (ready for the Mac run)

Phase 4.0 compares three Moho frames with the Python simulation of the same frames (`native/gate.compare_renders`):

| frame | expected | measures |
|---|---|---|
| 1 (rest) | `expected/expected_rest_f01.png` | **alignment**: silhouette IoU of the render vs the expected frame (threshold 0.85) |
| 12 (arm lowered, built) | `expected/expected_moved_f12.png` | **movement**: IoU of the region that changed between frames 1 and 12, render vs expected (0.60), and the changed-pixel ratio (0.5) |
| 18 (arm raised, keyed after reopening) | `expected/expected_reopen_move_f18.png` | the same, for the key added after reopening |

These checks are deliberately coarse. They prove placement and movement. They do not prove pixel parity: Moho's image
filtering, antialiasing and colour handling will differ, and their size is unknown until a render exists. A per-pixel
parity report (difference maps, per-layer error, edge offsets) is planned for Phase 4.1. It needs native renders to set
its tolerances from measurements rather than guesses.

## 2. What the cloud did, and what it does and does not show

| check | result | what it shows |
|---|---|---|
| simulated renders vs the simulation (both characters) | alignment IoU 0.98 / 0.94; changed-region IoU 1.00 / 1.00 | the comparator works on matching inputs. It says **nothing** about Moho |
| frozen arm (frame 12 = frame 1) | FAIL | the comparator catches artwork that does not follow the bone |
| art shifted by 120 px | alignment FAIL | the comparator catches a placement difference |
| half-size render (same aspect) | PASS after resize | a reduced export is accepted |
| other aspect | FAIL | a wrongly sized export is reported, not silently stretched |

Images: `docs/phase4/cloud_checks/<character>/SIMULATION_expected_frames.png`. They are labelled SIMULATION and are not
Moho screenshots.

## 3. Known limits of the simulation

- It assumes the Phase 0 conventions: a full-canvas image fills the frame, and binding is rigid about the bone's rest
  pose. A native run that breaks either shows up as an alignment or movement FAIL, not as a parity problem.
- Bicubic resampling (PIL) vs Moho's own filtering: edges will differ by a pixel or so, which the thresholds absorb.
- Generated pixels are excluded on purpose, so both the simulation and Moho show a gap under the moving arm.
