# Sleeve-free hand library and natural hand selection — report

**Verdict: PARTIAL.**

All 14 quality gates of the request pass on their measurements, with rendered evidence for each (below).

The task is still not production-complete, for four reasons:

1. **No drawing is approved.** All 16 hands are AI-generated concepts. They stay *pending review* and are only
   previewed (`allow_pending`). Without approvals the character keeps its original hands.
2. **The left pointing hand is a mirrored copy.** The sheet's two pointing drawings are the same (right) hand.
3. **Six wrist pivots are estimates.** Pointing, grip and cupped are drawn with closed outlines at the wrist, so their
   pivots need a person to review them.
4. **Moho has not run.** The Moho Switch Layer mapping stays `proposed_unverified`.

Visual quality is for you to judge; no score here claims it.

Evidence: `projects/schoolboy_tpose/character_package/hand_store/evidence_v2/` (regenerate with
`autorig hand-replace-evidence projects/schoolboy_tpose/character_package`, about 5 min). Extraction:
`references/schoolboy/hand_pack_v2/`.

## 1. The hand-switch architecture before this change (Step 1)

| Part | Where | What it did |
|---|---|---|
| Switch groups | `motion/switches.py` `ArtworkSwitchController` | `hand_r` / `hand_l`, one variant per pose, pivot = hand bone head. Resolution: user > keyframe > auto > default (`open_palm`). State in the editor project (`switch.set|key|offset`). |
| Candidates | `HandPoseLibrary` | Per side and pose: `original` (open palm only), `phase2:<drawing>` (hands cut from the character sheet, carrying part of their cuff: 5–20 own white px near the wrist; `forearm_<s>__trim` cover), `store:<lib>/<asset>` (the Hand Pose Store pack `schoolboy_hand_switches_v1`: drawings *with blazer sleeves*, cut at their own cuff, `forearm_<s>__hscuff` cover). Active = user's `use` > approved > original > list order. |
| Automatic choice | `feasibility.choose_hands` | The planner's preference list per preset (`ArmIntent.hand`), first approved / pending-if-previewing. |
| IK | `motion/ik.py`, `MotionEngine.apply_ik` | Hands rode on the solved hand bone; switching never touched the skeleton. |
| Persistence | `editor/project.py` (schema v3) | `switches`, `hand_store` (use, placement, mask, labels, deleted, versions), approvals `handstore:<uid>@v<N>`. |
| References to hand assets | `rig_controls` (switch groups, live actions `HAND_*`), `hand_switches.json` export, `posesvc` thumbnails/previews, evidence (`hand_store/evidence/`, `natural_pose/ik_switch/`) | Nothing referenced a hand by file path; everything goes through candidate ids and layer ids. |

The visible problems: v1 hands brought their own sleeve and cuff. They were masked, but thumbs-up, grip and cupped
still drifted 8–25 px, and the wrist was 1.4–1.6× the sleeve opening. The Phase 2 sheet fists and pointing hand
showed a doubled cuff line and an old-hand halo (before/after, §11).

Nothing was deleted. The v1 pack, its store files and the Phase 2 drawings are all still there (rollback, §4).

## 2. Inventory and extraction (Step 2)

`autorig hand-sheet` (`src/autorig/handsheet.py`) extracted the user's sheet
`references/schoolboy/hand_pack_v2/hand_sheet_sleeveless.jpg` (1254×1254 JPEG, sha256 `bbec2fab…`).

**Background.** Near-white, low-saturation pixels (lum > 242, sat < 25) connected to the sheet border, plus enclosed
near-white gaps of at least 40 px. Dark outlines can never qualify.

**Edges.** Antialiased edge pixels are unmatted against white: alpha comes from luminance, colour from the outline.

**Crop.** Each sprite is a tight crop plus a 4 px transparent margin, at sheet scale (no scaling, no rotation).

**Order.** There is exactly one drawing per grid cell, in reading order, and all 16 cells are filled.

| Cell | Gesture | Drawn as | Wrist opening | Conf. | Sprite px | Asset |
|---|---|---|---|---|---|---|
| r1c1 | relaxed | left | open cut (top) | 0.91 | 186×254 | `left_relaxed` |
| r1c2 | open palm | left | open cut (top) | 0.89 | 275×259 | `left_open_palm` |
| r1c3 | fist | left | open cut (top) | 1.00 | 195×206 | `left_fist` |
| r1c4 | pointing | **right** | closed outline (right side) | 0.35 | 257×191 | `left_pointing` = **mirror** of r1c4 |
| r2c1 | thumbs up | left | open cut (left side) | 1.00 | 200×258 | `left_thumbs_up` |
| r2c2 | gripping | left | closed outline (outer back of the C) | 0.35 | 233×217 | `left_gripping` |
| r2c3 | palm down | left | open cut (top) | 0.72 | 300×192 | `left_palm_down` |
| r2c4 | cupped | left | closed outline (right side) | 0.35 | 241×171 | `left_cupped` |
| r3c1 | relaxed | right | open cut (top) | 1.00 | 188×254 | `right_relaxed` |
| r3c2 | open palm | right | open cut (top) | 0.97 | 276×262 | `right_open_palm` |
| r3c3 | fist | right | open cut (top) | 1.00 | 197×209 | `right_fist` |
| r3c4 | pointing | right | closed outline (right side) | 0.35 | 268×184 | `right_pointing` |
| r4c1 | thumbs up | right | open cut (right side) | 1.00 | 203×263 | `right_thumbs_up` |
| r4c2 | gripping | right | closed outline (outer back of the C) | 0.35 | 239×223 | `right_gripping` |
| r4c3 | palm down | right | open cut (top) | 1.00 | 304×193 | `right_palm_down` |
| r4c4 | cupped | right | closed outline (left side) | 0.35 | 255×177 | `right_cupped` |

**Quality, measured on every sprite:**
- the outline covers 100 % of the visible edge;
- 0 skin pixels on the edge without an outline (no cut-off fingers or thumbs);
- 0 dark outline pixels made transparent;
- stray specks removed: 0–2 px per sprite;
- no drawing touches the sheet edge.

QA sheet: [`extraction_sheet.jpg`](../references/schoolboy/hand_pack_v2/extraction_sheet.jpg). It shows every sprite
on a checkerboard, its wrist line and the axis into the hand.

**Wrist openings.** The sheet draws most wrists as a short stub: a gently bowed cut between two near-parallel sides.
The detector takes the outline within 9 px of the extreme on the inspected side, and calls it an open cut when the
outline turns at least 52° into the sides at both ends. Measured: stubs turned 57–69°, closed rounded outlines 41–46°.
A drawing without a cut gets a chord at that side's open-palm wrist width; confidence 0.35, flagged for manual pivot
review.

**Inspection record.** `inspection.json` is the assistant's visual reading of the sheet, not a person's review: gesture,
wrist side, palm or back view, and thumb direction where it is unambiguous. Every value in it is cross-checked by a
measurement where one exists.

## 3. Left/right classification

The row labels were not trusted:

| Gesture | Silhouette IoU same / mirrored | Interior lines same / mirrored | Mirror pair? |
|---|---|---|---|
| relaxed | 0.84 / 0.97 | 0.15 / 0.75 | yes |
| open palm | 0.61 / 0.92 | 0.14 / 0.43 | yes |
| fist | 0.93 / 0.99 | 0.20 / 0.74 | yes |
| **pointing** | **0.84 / 0.52** | **0.46 / 0.21** | **no: the same drawing twice** |
| thumbs up | 0.78 / 0.94 | 0.04 / 0.55 | yes |
| gripping | 0.46 / 0.94 | 0.05 / 0.41 | yes |
| palm down | 0.61 / 0.95 | 0.05 / 0.47 | yes |
| cupped | 0.56 / 0.97 | 0.10 / 0.63 | yes |

- **Which group is left.** The open palm's thumb side was measured as outline mass beyond the wrist half-width, at
  10–55 % of the hand length. Row 1 has its thumb on the image right (ratio 1.46); row 3 has it on the image left
  (ratio 1.65). The rule for a palm view, in image coordinates: right hand ⟺ cross(fingers, thumb) > 0. So rows 1–2 are
  the **left** hand and rows 3–4 the **right** hand, which matches the character's original hands (thumbs lateral,
  palms forward).
- **Per-drawing thumb evidence.** The same rule applied to the fist and the thumbs-up agrees with its row group.
- **The pointing hands.** Both point image-left with the thumb starting beside the index on top and folding down over
  the curled fingers, palm toward the viewer. That makes both of them **right** hands.
  - r3c4 is the right pointing hand.
  - r1c4 is a mislabelled right hand: it is kept as an extracted sprite and flagged ("drawn as a right hand, but its row
    group is the left side").
  - The left pointing asset is its horizontal mirror (`derived: mirror_x`), flagged and pending review. A drawn left
    pointing hand would replace it.

## 4. Replacing the active library (Step 3)

- `autorig hand-import` imported `AutoRig_Hand_Library_Sleeveless_v2.zip` as library `schoolboy_hands_sleeveless`.
  It is a deterministic zip: a rebuild is byte-identical, which a test enforces.
  - Files: `hand_store/packs/schoolboy_hands_sleeveless/v1/hands/{left,right}/{relaxed,open_palm,fist,pointing,
    thumbs_up,gripping,palm_down,cupped}.png`, i.e. the suggested folder structure inside the existing store.
  - Every asset carries its wrist geometry, features, handedness, flags and provenance in the store index.
- **Library status** (`handstore.library_status`):
  - The pack's manifest says it `supersedes` `schoolboy_hand_switches_v1`, so v1 is **archived** by default.
  - The Phase 2 sheet hands (`phase2_sheet_hands`) are also sleeve-attached, and are archived while a sleeve-free
    library is active.
  - Archived drawings stay in the store (hash-verified) but are no longer switch candidates.
  - A library is archived only while a library that supersedes it is active. Archiving the new library therefore
    restores v1 and the sheet hands (rollback).
  - Rollback is a project operation (`handstore.library`): in the Hands tab, or with `autorig hand-library <pkg>
    --restore schoolboy_hand_switches_v1` (or `--archive schoolboy_hands_sleeveless`).
- **Default choice per pose:** approved > original > sleeve-free store drawing (newest library first) > the rest. The
  character's own hand stays the open-palm default, as the Hand Pose Store rules require.
- **Old gesture identifiers.** `gesture_id_map` maps every archived v1 drawing and every Phase 2 drawing to the active
  sleeve-free drawing of the same side and pose (e.g. `phase2:fist` → `store:schoolboy_hands_sleeveless/right_fist`).
  A user's `use` choice pointing at an archived drawing follows the map, and the change is reported (`use_remapped`).
  Switch groups, keys and live actions refer to poses, not files, so existing animation references keep working.
- **Missing artwork.** `partially_closed` has no drawing on either side (NEEDS_ARTWORK, open-palm fallback). `palm_up`
  had only the archived Phase 2 approximation; shrug and explaining fall back to the open palm or cupped hand.

## 5. Wrist alignment (Step 4): `WristAlignmentController` (`src/autorig/wristalign.py`)

For each sleeve-free drawing the controller uses these geometry inputs:

- **Opening:** the drawing's wrist line `wrist_edge_px` (two points), detected at extraction.
- **Centre:** the line's midpoint, which is the pivot.
- **Natural orientation:** the unit axis from the opening into the hand, `wrist_axis`.

It then places the drawing as follows:

- **Alignment:** the opening centre goes onto the character's own shirt-cuff edge (`reference_wrist`, from the
  skeleton and the original pixels), tucked `TUCK_PX` = 6 px under the cuff along the hand bone.
- **Rotation:** natural angle = hand-bone angle − angle of the drawing's axis, plus the user's rotation offset. The
  rotation is about the opening centre, never the image centre; a test checks that the anchor stays fixed under
  rotation.
- **Proportions:** one uniform scale per side and library, chosen so the open palm shows the original hand's visible
  area: right 0.5424, left 0.5432 (sheet px → character px). The fallback rule (cuff width / median opening width) is
  reported too. Measured wrist fit is 0.74–0.89× the sleeve opening, so the hand comes out of a slightly wider sleeve
  with no side overhang.
- **Gaps and overlap:**
  - The 6 px tuck closes the gap.
  - Pixels more than tuck + 2 px behind the opening are trimmed (0–41 px per drawing), so a bent wrist cannot swing
    the closed caps out beside the sleeve.
  - The forearm cover (`forearm_<s>__hscuff`) is drawn over the hand.
- **The sleeve is not modified.** The cover only *removes* old-hand pixels past the white cuff: 925/946 skin and 8–9
  dark outline pixels per side, and 0 sleeve-fabric pixels (measured). The cleanup also strips the old hand's
  outline ticks at the cuff corners with a 5×5 morphological opening; the earlier, larger hands had hidden them.
- **Saved parameters.** Each candidate's `placement.params` records: opening, confidence, manual-review flag, edge,
  axis, pivot, natural angle, rotation offset, tuck, trimmed px, wrist width and fit, and attachment bone. They are
  exported in `hand_switches.json` and `rig_controls.hand_selection`.
- **Per-variant corrections** (editor, `handstore.placement`): dx, dy (world px); rot (deg); uniform scale;
  **overlap** (px added to the tuck, −6…24); **pivot_x/pivot_y** (sprite px, ±60, the manual wrist pivot).
  They are stored in the project (undo/redo, reload).

Per-drawing results (rest pose, `results.json` → `library_sheet`):

| Side | Pose | Opening | Wrist fit | Natural angle (°) | Trimmed px |
|---|---|---|---|---|---|
| r | relaxed / open palm / fist / palm down | open cut | 0.82 / 0.78 / 0.89 / 0.83 | 89.0 / 90.7 / 90.4 / 93.1 | 0 |
| r | thumbs up | open cut (side) | 0.74 | 2.6 | 0 |
| r | pointing / gripping / cupped | closed outline (review) | 0.79 / 0.81 / 0.79 | 2.6 / 2.6 / 182.6 | 41 / 0 / 0 |
| l | relaxed / open palm / fist / palm down | open cut | 0.82 / 0.79 / 0.88 / 0.81 | −84.1 / −90.1 / −88.1 / −91.4 | 0 |
| l | thumbs up | open cut (side) | 0.75 | −4.0 | 0 |
| l | pointing / gripping / cupped | closed outline (review) | 0.80 / 0.79 / 0.81 | −2.0 / −2.0 / −182.0 | 15 / 0 / 20 |

The T-pose source has horizontal arms, so the hand bones point sideways at rest. That is why hanging-hand drawings get
about ±90° and side-cut drawings about 0°.

**Rigid hands.** Stored hand drawings are bound rigidly to the hand bone: `GarmentSkeleton.RIGID` gives every pixel the
hand bone's full transform, with no wrist blend band. Measured blended fraction is 0 and the Jacobian determinant is
exactly 1.00 (no stretch). The sleeve follows the forearm and the hand follows the wrist, so there are no double
transforms. The character's original hand keeps its blended wrist band (skin continuity).

## 6. Natural hand selection (Steps 5–7): `NaturalHandSelectionEngine` (`src/autorig/motion/handselect.py`)

**Where it runs.** `MotionEngine.select_hands` runs right after the skeletal solve. The engine ranks the active drawing
of every pose for the side's action and fills the *automatic* slot of the existing switch groups. Resolution stays
user > keyframe > auto > default, so an artist's explicit choice is never replaced because another drawing scores
higher. There is no second switch engine.

**Inputs** (`HandContext`): action (from the preset, or the user's gesture intention); forearm and hand world angles;
elbow flexion; wrist bend; wrist position and movement since the last frame; view; target. Plus the candidates with
their approval, handedness, wrist attachment (alignment parameters), features (index tip, thumb tip, grip centre) and
the previous state (`HandSelectionState`).

**Criteria and weights** (`WEIGHTS`; per character in `natural_pose/hand_rules.json` or `handselect.weights`):

| Criterion | Weight | Measured as |
|---|---|---|
| gesture compatibility | 0.34 | position in the action's preference list (1.0, 0.8, 0.62, 0.48; unlisted 0.15; forbidden = excluded) |
| anatomical plausibility | 0.14 | drawn for this hand (wrong hand 0.2, mirrored copy 0.85) × the wrist rotation the gesture needs fits the wrist limit |
| wrist alignment | 0.12 | closed-outline pivot under review 0.7; wrist fit outside 0.8–1.2 penalised |
| visual silhouette | 0.10 | index toward the target / thumb up after the allowed wrist turn (cos of the residual); grip drawing with a measured grip centre |
| viewing angle | 0.08 | how far the drawing is turned from how it was drawn, against per-pose tolerances (`UPRIGHT_TOL`, e.g. cupped 70°, thumbs-up 60°, fist 170°) |
| pose continuity | 0.10 | same drawing as the previous frame |
| artwork fidelity | 0.04 | original 1.0, Phase 2 sheet 0.8, sleeve-free store 0.7, mirrored 0.6, sleeve-attached 0.5 |
| approved availability | 0.08 | original/approved 1.0, pending 0.6 (pending drawings are candidates only when previewing) |

Ties break deterministically by preference order, pose name, then candidate id. The score ranks the drawings a
character has for an action; it does not measure visual quality (the UI and the rig spec say so).

**Rules** (`ACTIONS`):

| Action | Prefer | Forbid | Extra |
|---|---|---|---|
| relaxed_standing | relaxed, open palm, partially closed | pointing, thumbs-up, gripping, fist | neutral wrists |
| walking | relaxed, partially closed, open palm | pointing, thumbs-up, gripping, palm down | symmetric |
| running | fist, relaxed, partially closed | pointing, thumbs-up, gripping, palm down, cupped | symmetric, no twist |
| pointing | pointing | — | wrist aims the index (≤ 45°) |
| shrugging | open palm, cupped, palm up | pointing, fist, thumbs-up, gripping | symmetric |
| explaining | open palm, cupped, palm up, relaxed | thumbs-up, gripping | varies between beats |
| angry | fist, pointing | thumbs-up, cupped, open palm | pointing only with a target |
| gripping | gripping, fist | pointing, thumbs-up, open palm | grip centre on the interaction target (arm IK) |
| thumbs_up | thumbs-up | — | wrist turns the thumb up (≤ 45°) |
| hand_on_hip | relaxed, cupped, fist | pointing, thumbs-up, gripping | max 70° from how drawn |
| resting / raised | palm down, relaxed / open palm, relaxed | — | — |

Presets map to actions (`PRESET_ACTIONS`): standing, walking, running, pointing (right arm), conversational
(explaining), shrug, hands on hips, sitting/squat (resting) and arm raise (raised). Gestures without a preset (thumbs
up, gripping, angry, pointing with either arm) come from the user's gesture intention (`handselect.action`, per side,
with an optional target).

**Temporal consistency:**
- A held gesture is not switched while the action is unchanged.
- Otherwise a new drawing must beat the current one by `HYSTERESIS` = 0.12, after `MIN_HOLD_S` = 0.4 s.
- An action change is the meaningful moment and switches at once. A flickering action cannot switch the hand faster
  than every `MIN_HOLD_ACTION_S` = 0.25 s.
- Explaining resets continuity at each new beat and penalises repeating the last gesture (`VARIETY_PENALTY` = 0.15).
- Running, walking and shrugging share a gesture across both hands when that costs each side at most 0.05.
- Switch keyframes and explicit choices are untouched.

**Wrist aim:**
- **Pointing at a target.** The arm IK does the gross aim and the wrist the fine aim: full wrist aim when the forearm
  is within 25° of the goal, none beyond 70°. The aim is clamped to the wrist limit (±60° bend) and the action's
  maximum turn, and the residual is reported.
- **Thumbs-up** turns the thumb up the same way.
- **Sequences.** The aim eases at most 180°/s, so the wrist stays continuous while the artwork switches discretely.
- **Gripping with a target.** The arm's IK target moves so the grip drawing's grip centre (not the wrist) lands on the
  object; fixed-point passes through the one arm IK solver.

## 7. IK integration (Step 8)

- **Hands follow the solved wrist.** Each drawing is a layer bound to the hand bone, with the same pivot for every
  variant.
- **Switching changes no bone.** Measured on `pointing_speaking` with five drawings, with and without aim:
  - every joint except the hand bone's own tail is unchanged (0.0 px);
  - bone lengths are unchanged (≤ 0.007 px rounding);
  - the forearm never moves.
- **FK→IK matching** (`MotionEngine.match_ik`) jump: 0.014 px for both arms.
- **Gripping** puts the grip centre 0.4 px from the object (IK status ok).
- **Pointing:**
  - The right arm (preset reach) and the left arm (user IK target, clamped at full reach) both point.
  - The wrist aims the index exactly (residual 0.0° in the static tests).
- **Thumbs-up** with an IK arm (forearm out to the side) turns the thumb up with a 0.0° residual.
- There is no second arm-control system: one IK solver, one switch controller.

## 8. Garment integration (Step 9)

- **Layer order.** The forearm (sleeve) is drawn over the hand, so the cuff overlap is kept.
- **Same cover for every drawing.** All stored drawings share one cover (`forearm_<s>__hscuff`). Its alpha is
  identical whichever drawing is active, and the upper-arm pixels are identical too.
- **Hands are rigid.** They are never weighted like clothing (see §5).
- **No transparent gaps:** the posed seam at the cuff is covered in every test.
- **No sleeve outlines inside the hands:** 0 own white cuff pixels near the wrist in every new-hand test.
- **Clothing and face unchanged.** Every test compares the render against the same pose with the original hands.
  - Pixels changed on the torso, legs, face or hair away from the arms: **0** in all nine static tests.
  - Up to 10 px change along the arms. That is the forearm under any switch drawing: Phase 2's trimmed forearm, used
    since Phase 3.2, plus the wrist cleanup.

## 9. Editor (Step 10)

- **Hands tab:**
  - **Hand libraries:** status, reason, Archive / Restore (rollback) / default.
  - Each candidate shows a preview **attached to the character** (`/api/handstore/preview`) next to its source sprite.
  - **Sleeve-free wrist panel:** opening, confidence, manual-review flag, pivot, natural angle, rotation offset, tuck,
    fit. Controls: **wrist pivot** x/y ±2, **overlap** ±1, dx/dy/rot/scale.
  - Flags (closed outline, mirrored copy) are listed.
  - Approval status with approve/reject; a NEEDS_ARTWORK warning.
- **Switches tab** (per hand):
  - an **automatic selection** toggle and **Reset to automatic**;
  - the selector, as on-model thumbnails of every variant;
  - the **gesture intention** selector;
  - the engine's **decision**: chosen drawing, reason, held/overridden, wrist aim, missing preferred drawings, and the
    score table per criterion.
  - Approve/Reject now uses the active drawing's own approval item; it was hard-wired to the Phase 2 key.
- **Persistence:** project schema **v4** (`hand_store.libraries`, `hand_selection` with `handselect.action|weights`),
  migrated from v3 unchanged.
- **Browser test.** The Playwright e2e test (step 6e) does the following in headless Chromium:
  1. imports the sleeve-free pack through the UI;
  2. sees v1 and the sheet hands archived;
  3. adjusts a pivot and the overlap;
  4. sets a gesture intention (and the engine follows it);
  5. toggles auto off and resets it;
  6. archives and restores the new library (rollback);
  7. checks the pivot after a server restart.

  Screenshots: `docs/hand_replacement/screenshots/`.

## 10. Moho compatibility (Step 11)

**Rig controls 1.1.0** (schema `data/schemas/rig_controls.schema.json`) gains `hand_selection`. It contains:
- per group: bone, pivot, default, the user override (user/auto) and timeline keys;
- variants with status, approval item, library, sleeve, wrist pivot, attachment bone, placement (final + params),
  features, handedness and provenance;
- rules, weights, temporal constants, state, libraries and the gesture id map;
- `runs_in: AutoRig (Python); not in Moho`.

**`hand_switches.json` 1.1.0** adds libraries, the gesture id map, alignment params and the selection rules.

What a later Moho exporter would have to translate (`moho_translation`, all `proposed_unverified`):

| AutoRig rule | Moho control |
|---|---|
| explicit choice, switch keyframes | Switch Layer keyframes |
| automatic choice (hysteresis, holds, symmetry) | baked into Switch Layer keyframes at export; no runtime equivalent assumed |
| wrist aim (pointing, thumbs-up) | hand bone rotation keys |
| per-variant pivot, rotation offset, overlap, scale | the variant layer's own transform inside the switch layer (origin at the wrist pivot) |
| gesture actions for live puppeteering | Actions on Live Mode inputs (input names not discovered; none invented) |
| gripping target | arm IK bone target offset by the grip centre |

No `.moho` file is written and no Moho API was added. Creating Switch Layers from these images is listed under
`not_implemented_in_native_moho`.

## 11. Visual test results (Step 12)

Each test render shows the full figure, both hands close up and the engine's decision:

| # | Test | Right hand | Left hand | Notes |
|---|---|---|---|---|
| 1 | [Standing, relaxed](../projects/schoolboy_tpose/character_package/hand_store/evidence_v2/tests/01_relaxed_standing.png) | relaxed | relaxed | neutral wrists |
| 2 | [Walking](../projects/schoolboy_tpose/character_package/hand_store/evidence_v2/tests/02_walking.png) | relaxed | relaxed | 17-frame cycle: 0 switches |
| 3 | [Running with fists](../projects/schoolboy_tpose/character_package/hand_store/evidence_v2/tests/03_running_fists.png) | fist | fist | 17-frame cycle: 0 switches |
| 4 | [Pointing, right hand](../projects/schoolboy_tpose/character_package/hand_store/evidence_v2/tests/04_pointing_right_hand.png) | pointing | relaxed | wrist aim −10.9°, residual 0.0° |
| 5 | [Pointing, left hand](../projects/schoolboy_tpose/character_package/hand_store/evidence_v2/tests/05_pointing_left_hand.png) | relaxed | pointing (mirrored copy) | arm IK at full reach; aim −10.4°, residual 0.0°; curled fingers flare 6 px past the cuff side |
| 6 | [Shrugging](../projects/schoolboy_tpose/character_package/hand_store/evidence_v2/tests/06_shrugging_open_palms.png) | open palm (original) | open palm (original) | the original open palm is the default until a new open palm is approved |
| 7 | [Gripping an object](../projects/schoolboy_tpose/character_package/hand_store/evidence_v2/tests/07_gripping_object.png) | gripping | relaxed | grip centre 0.4 px from the target (the rod is an annotation) |
| 8 | [Thumbs up](../projects/schoolboy_tpose/character_package/hand_store/evidence_v2/tests/08_thumbs_up.png) | thumbs up | relaxed | forearm out to the side by IK; thumb turned up, residual 0.0° |
| 9 | [Hands near the hips](../projects/schoolboy_tpose/character_package/hand_store/evidence_v2/tests/09_hands_near_hips.png) | relaxed | relaxed | |
| 10 | [Switching during arm movement](../projects/schoolboy_tpose/character_package/hand_store/evidence_v2/switch_sequence.png) ([GIF](../projects/schoolboy_tpose/character_package/hand_store/evidence_v2/switch_sequence.gif)) | relaxed → pointing → relaxed | relaxed | see below |

Every check passed for every hand in tests 1–9:
- handedness;
- gesture;
- wrist orientation;
- attachment centred on the cuff (within 25 % of its width);
- no sleeve duplication (≤ 40 own white px; measured 0);
- no large gap (covered);
- overlap (the hand under the cover; flare ≤ 8 px);
- no scaling distortion (one scale per side; rigid, det 1.00);
- no inappropriate switching.

The original open palm (test 6) is the character's unchanged artwork, so the attachment checks do not apply to it.

Test 10, switching during arm movement: 2 switches (relaxed → pointing at frame 10 as the raise begins, pointing → relaxed at frame 27 as it ends); wrist anchor change at a switch 0.42 px; wrist path max step 81 px/frame (the IK target eases); wrist aim max step 15°/frame (limit 15°). The wrist anchor stays where the previous drawing's was (≤ 0.65 px
change at a switch). The aim is rate-limited and weighted by how well the forearm already points at the target, so the
wrist stays near neutral while the arm rises. In the flicker test the action toggles every frame for 24 frames; the
hand switched 7 times (allowed ≤ 9).

Contact sheets:
[all 16 hands attached](../projects/schoolboy_tpose/character_package/hand_store/evidence_v2/library_attached.jpg)
(hanging-wrist close-up plus the drawing in an action) and
[automatic selection across actions](../projects/schoolboy_tpose/character_package/hand_store/evidence_v2/auto_selection.jpg).

## 12. Before and after

[`before_after.jpg`](../projects/schoolboy_tpose/character_package/hand_store/evidence_v2/before_after.jpg) shows the
same poses with the character's original hand, the archived sleeve-attached setup (restored temporarily: v1 + Phase 2
sheet hands, exactly as before) and the sleeve-free library:

- **Running fists.** Before, the Phase 2 sheet fists show a halo and a doubled cuff line (5–8 own white px near the
  wrist, 14 px past the cuff side). After, the sleeve-free fists show 0 white px and 0 overhang.
- **Pointing.** Before, the sheet "point" drawing is a fist-like hand with a second cuff (20 white px). After, it is a
  pointing hand aimed along the arm.
- **Relaxed.** Both the v1 (masked) and the sleeve-free drawings attach cleanly. The sleeve-free one needs no cuff mask
  and keeps its own outline at the wrist.

Measured improvement: own white cuff pixels near the wrist (sum over the sheet) 33 → 0; largest
overhang 14 → 0 px. Whether it *looks* better is for you to judge.

## 13. Quality gates (Step 13)

| Gate | Requirement | Status | Evidence |
|---|---|---|---|
| G1 | All 16 hand assets are extracted correctly | **PASS** | 16 drawings, outline 100 %, 0 lost outline px, 0 cut-off skin; QA sheet; library sheet |
| G2 | Both hands have functioning switch groups | **PASS** | both groups hold 7 sleeve-free drawings + the original open palm; user/key/auto/reset verified |
| G3 | The old sleeve-attached variants are no longer active | **PASS** | no active candidate is a v1 or Phase 2 drawing (both libraries archived, restorable) |
| G4 | All new hands attach naturally at the wrist | **PASS** | every test and every library drawing: covered, centred, 0 own white px, flare ≤ 8 px (tests/*.png, library_attached.jpg) |
| G5 | The forearm remains unchanged during hand switching | **PASS** | 0.0 px joint movement except the hand bone; identical cover alpha across drawings; 0 sleeve px removed |
| G6 | Automatic hand selection produces sensible results | **PASS** | all 9 static tests chose the expected gesture with the right hand (auto_selection.jpg) |
| G7 | Manual selection overrides automatic behaviour | **PASS** | explicit choice and keyframe win over auto; reset returns to auto; approved-only shows the original |
| G8 | Hand switching does not create excessive popping | **PASS** | anchor change ≤ 0.42 px at switches; aim ≤ 15°/frame; flicker 7/24 |
| G9 | Existing IK continues working | **PASS** | FK→IK jump 0.014 px; IK pointing/thumbs-up/grip attached; grip 0.4 px; no bone length change |
| G10 | Garment deformation remains unaffected | **PASS** | store hands rigid (det 1.00, blend 0); torso/legs/face 0 px changed in all tests; cover over hand |
| G11 | Projects save and reload correctly | **PASS** | library, placement (pivot/overlap/rot), action, switch ops reload identically; undo/redo |
| G12 | Visual comparisons demonstrate improvement | **PASS** | duplicate-cuff white px 33 → 0; overhang 14 → 0 px (before_after.jpg) |
| G13 | Missing or unsuitable artwork is reported | **PASS** | partially_closed missing; 6 estimated pivots + mirrored left pointing flagged; all 16 sleeve-free drawings pending review |
| G14 | Existing character source artwork is preserved | **PASS** | source sha256 matches; every stored file hash-verified (v1 and v2) |

**PASS means the measurement and its rendered evidence exist and meet the stated criterion.** It does not mean a person
approved the look.

## 14. Remaining defects

1. **No approvals.** All 16 drawings are pending review, so without approvals the character still shows its original
   hands. Approve per drawing in the Hands or Switches tab (`approval.set handstore:schoolboy_hands_sleeveless/<asset>@v1`).
2. **Left pointing is a mirror** of a right-hand drawing (the sheet has no left pointing hand). Its thumb and finger
   overlap are mirrored too. A drawn left pointing hand should replace it (`Replace PNG` keeps it as a new version).
3. **Estimated pivots.** Pointing, grip and cupped have closed wrist outlines, so their pivots are chord estimates
   (confidence 0.35). They look attached in every render, but you should check them with the pivot and overlap
   controls.
4. **Missing drawings.**
   - `partially_closed` does not exist on either side (walking and running fall back to relaxed).
   - `palm_up` has only the archived Phase 2 approximation.
   - The new open palm is not used automatically until approved, because the original stays the default.
5. **Silhouette fringe.** The smaller hands reveal light antialiased fringe pixels on the original blazer and trouser
   silhouette beside the hips. The bigger original hand covered them at rest. They are original pixels, not generated,
   and also appear whenever the original hand moves away.
6. **Pointing-left flare.** The curled fingers of the pointing-left hand flare 6 px past the sleeve side at the cuff
   line. This is within the 8 px tolerance and visible as a natural flare, but you should review it.
7. **Front view only.** Aim and readability come from the 2D drawings. The thumbs-up drawing reads best with the
   forearm out to the side; a raised forearm turns it beyond the wrist limit, which is reported as residual.
8. **Moho not run.** Switch Layers, Smart Bones and Live Mode inputs are unverified, and no `.moho` is written.

## 15. Commands and tests

```bash
.venv/bin/python -m autorig hand-sheet references/schoolboy/hand_pack_v2/hand_sheet_sleeveless.jpg --inspection references/schoolboy/hand_pack_v2/inspection.json --out references/schoolboy/hand_pack_v2 --library-id schoolboy_hands_sleeveless --supersedes schoolboy_hand_switches_v1 --zip-name AutoRig_Hand_Library_Sleeveless_v2.zip
```

```bash
.venv/bin/python -m autorig hand-import projects/schoolboy_tpose/character_package references/schoolboy/hand_pack_v2/AutoRig_Hand_Library_Sleeveless_v2.zip
```

```bash
.venv/bin/python -m autorig hand-library projects/schoolboy_tpose/character_package --restore schoolboy_hand_switches_v1
```

```bash
.venv/bin/python -m autorig hand-replace-evidence projects/schoolboy_tpose/character_package
```

Tests:

- **Full Python suite:** 450 passed, 2 skipped (the native Moho tests: NOT EXECUTED, Moho is not available here).
  This includes 20 new tests in `tests/test_hand_replacement.py`:
  - extraction quality, handedness and wrist openings on the real sheet;
  - pack reproducibility;
  - wrist-edge validation and the synthetic wrist detector;
  - alignment geometry: rotation about the opening, pivot, overlap, trim;
  - selection rules, determinism, per-character weights, temporal rules, beats and symmetry;
  - library archive and rollback;
  - on the real package: old variants inactive, presets, rigid hands and unchanged forearm, grip and IK, project v4,
    rig controls, committed evidence.
- **Editor unit tests (vitest):** 11 passed.
- **Playwright e2e in headless Chromium:** passed, including the new step 6e (import, archive and restore, pivot and
  overlap, gesture intention, auto toggle and reset, reload).
