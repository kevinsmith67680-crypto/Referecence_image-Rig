# Phase 3.2 report: inverse kinematics, switch layers and articulated controls

**Verdict: PARTIAL.** The IK, foot-contact, switch, correction and editor machinery is implemented, tested and
exercised on the user's real schoolboy character. The renders show real gains: arms reach targets, feet stay
planted, and hands switch without leaving the sleeve. But no generated or reference-derived drawing has been
reviewed by a person. Four of the eight required hand poses, and every elbow, knee, hip, wrist, trouser and sleeve
correction drawing, do not exist. A deep elbow bend still breaks the sleeve. Moho has not run. Details are in §16.

> **Update after this report:** the user's hand pack was imported as a Hand Pose Store
> ([hand_pose_store_integration.md](hand_pose_store_integration.md)). Relaxed, gripping and palm-down drawings, and a
> cupped one, now exist for both sides as pending candidates. Partially closed is still missing. The tables below
> describe the state when Phase 3.2 was reported.

Everything below was measured in the cloud sandbox (x86 CPU, headless Chromium) on a copy of
`projects/schoolboy_tpose/character_package`. Phase 0 is still not verified in Moho, and that carries forward.

## 1. Existing architecture audit

The audit is in [phase3_2_implementation_plan.md](phase3_2_implementation_plan.md). In short, Phase 3.1 already had:
* the planner → solver → garment engine → evaluator split;
* one bare two-bone IK function (no poles, limits or reach report);
* phase-driven gaits with planted feet;
* hand switching by file name only (no groups, offsets, keys, alignment checks or user choice);
* pending-review hand drawings for fist, pointing, thumbs-up and palm-up.

Phase 3.2 keeps that architecture and inserts the new stages into it. It adds no second solver and no duplicate
bones. The processing order in `MotionEngine.pose` is now:

intent → contact and IK target planning (`apply_ik`) → skeletal solve → correction selection → garment deformation →
switch resolution → composition → evaluation.

## 2. IK solver implementation

**`motion/ik.py` `TwoBoneIKSolver`.** An analytic 2D solver. Conventions:
* source pixels, x right, y down;
* angles in degrees from straight down, clockwise on screen;
* flexion 0 = straight.

Behaviour:
* A pole target picks the bend side. Without a pole, `bend_sign` picks it.
* Flexion limits define the reach interval `[chord(max_flex), chord(min_flex)]`.
* An unreachable target is clamped onto that interval and reported as `clamped_max_reach` or `clamped_min_reach`.
  A target on the root is reported as `degenerate`.
* Segment lengths never change and nothing is scaled.
* The solver is deterministic and mirror-symmetric.
* The motion solver uses a 1.6° minimum flexion, so the middle joint never snaps at full extension.
* Tested on 500 random reachable targets: end error < 1e-6 px, lengths exact to 1e-9.

**Controllers.** `ArmIKController` and `LegIKController` drive the existing `upper_arm/forearm/hand` and
`thigh/shin/foot` chains. The skeletal solver uses the same `TwoBoneIKSolver` for every leg and IK arm.

**User IK.** User IK state lives in the editor project. Per limb:
* `enabled` and `mode` (planner / fk / ik);
* `target`, `pole` and `bend` (a held bend side, so the elbow never inverts while dragging);
* `lock` (legs);
* `max_flex`;
* `torso_follow` (arms);
* `end_angle` (wrist or foot orientation);
* `fk` (arm angles).

A `pelvis_offset` moves the pelvis while locked feet stay put. `MotionEngine.apply_ik` turns this state into
intent. Leg IK keeps the gait's load and ground data, so the pelvis height stays continuous. Ankle targets below the
ground are clamped and reported. A locked foot is placed on the ground.

**Shoulder and clavicle.**
* The clavicle elevation of an IK arm is the fixed point of the shoulder rhythm. It is found by a bracketed root
  search with a warm start.
* Overhead abduction is unwrapped past 180°. Before that fix, a dense sweep showed a 61.7 px joint jump when the arm
  passed overhead.
* Across 656 tested targets (both arms, four radii, both bend sides) the fixed point has exactly one root.
* When a target is out of reach, the clavicle rises or drops toward it, within its limits, and a note says so.

**FK/IK matching.** `MotionEngine.match_ik`:
* FK→IK: target = current wrist, pole = current elbow, bend side held.
* IK→FK: FK abduction and flexion read back from the IK pose.

Measured joint jumps when switching, on four presets and both arms: at most 0.02 px. The tests assert < 0.05 px.

Legs have no FK angles: in FK mode they follow the planner. `match_ik` reports the jump instead of hiding it.

**Continuity.** A dense IK sweep of the right wrist (241 targets over 200°, bend held) gave:
* 0 elbow flips;
* wrist error 0.0 px;
* largest joint step 3.29 px for a 2.89 px target step (ratio 1.14).

## 3. Foot contact implementation

`FootContactController` labels each foot PLANTED, LIFTING, SWINGING, LANDING or FREE.

How the states are assigned:
* PLANTED: a loaded stance foot, or any planted foot outside a gait.
* LANDING / LIFTING: an unloaded stance foot in the first / second half of its stance.
* LIFTING / LANDING: the first / last 15 % of a swing.
* SWINGING: the rest of a swing.
* FREE: a foot outside a gait that is not planted, such as an unlocked foot lifted by IK.

Each planted foot reports its world lock and the error, split into a horizontal `slide_px` and a vertical `lift_px`.
The Phase 3.1 smooth support lets a loaded foot rise by at most 1.5 px; it never slides.

Measured:

| Situation | Result |
|---|---|
| Static: both feet locked, pelvis offset by (18, 0), (−25, 12) and (0, 30) | lock error < 0.01 px |
| Walk, 48 phases | planted slide ≤ 0.005 px |
| Run, 48 phases | planted slide ≤ 0.005 px |
| Every contact state | reached in both gaits; no FREE inside a gait |
| Ankle target 40 px below the ground | clamped to the ground and reported |

Swing trajectories, clearance and landing are the Phase 3.1 planner's: clearance ramp, swing-knee schedule and
loads. They are configured through speed, exaggeration and `motion_profile.json`. There is no separate trajectory
editor.

Contact markers are drawn in the editor's IK tab and in the diagnostic renders: a coloured dot per state and a bar
for a world lock.

## 4. Switch system implementation

**`motion/switches.py` `ArtworkSwitchController`.** Groups:
* `hand_r` and `hand_l`: nine variants each.
* Fourteen correction groups `corr_<trigger>_<side>`, with variants off and on. They cover the elbow, knee and
  shoulder corrections the request names, plus hip, wrist, trouser-fold and sleeve-crease triggers.
* `face_mouth`: the mouth, on the face channel, kept separate from the body.

Every group has one active variant, a shared attachment bone and pivot, per-variant rigid offsets (dx, dy and
rotation about the pivot; never scale), provenance, approval status and missing-asset detection.

The active variant is resolved in this order:
1. the user's explicit choice;
2. a switch keyframe (step-held, evaluated at the gait phase or a given time);
3. the automatic preset choice (only while the group is in auto mode);
4. the original drawing.

A pending drawing is shown only with `allow_pending`. A rejected or missing one falls back to the original with a
note, and the pose becomes NEEDS_ARTWORK.

**Storage.** State lives in the editor project (schema v2, see §8). Editing operations:
* `switch.set`;
* `switch.key`;
* `switch.offset`, which refuses any `scale` value other than 1.

Undo, redo and persistence come from the existing operation log.

**Rendering.** An offset becomes its own rigid variant layer about the wrist pivot (`Deformer.add_variant_layer`).
Registering it leaves the neutral render bit-identical, and switching never moves the skeleton. Both are tested.

**Approval state.** It comes from one place, the feasibility inventory. The tests found a case where the inventory
and the hand library disagreed, which gave a fist-style run with open hands; this is fixed.

## 5. Hand variant availability

Columns:
* **Rest error**: visible wrist attachment versus the reference hand, measured at rest on the pixels not covered by
  the sleeve cuff.
* **Width ratio**: wrist width of the variant divided by the reference hand's.
* **Posed gap / drift**: measured in a relaxed-standing render. Gap is the distance to the sleeve; drift is how far
  the wrist attachment moved from where the hand bone carries it.
* **Auto-align**: the error after the alignment tool's best translation (≤ 12 px, no rotation, no scale).

| Pose | Side | Status | Rest error (px) | Width ratio | Posed gap / drift (px) | Auto-align → (px) |
|---|---|---|---|---|---|---|
| open palm | r / l | **original** (reference) | 0 / 0 | 1.0 | 0 / 2.96, 0 / 1.74 | – |
| fist | r | pending review | **9.93** (flagged) | 0.92 | 0 / 0.26 | 0.73 |
| fist | l | pending review | 1.59 | 0.92 | 0 / 0.44 | 0.44 |
| pointing | r | pending review | **11.29** (flagged) | 0.93 | 0 / 0.31 | 0.75 |
| pointing | l | pending review | 1.80 | 0.93 | 0 / 0.31 | 0.39 |
| thumbs up | r | pending review | 4.87 | 0.87 | 0 / 0.91 | 1.89 |
| thumbs up | l | pending review | **7.86** (flagged) | 0.85 | 0 / 2.01 | 0.32 |
| palm up (extra) | r / l | pending review, approximation | **20.12 / 5.43**, width **0.55 / 0.52** (scale mismatch flagged) | | 0 / 0.17, 0 / 0.04 | 2.06 / 4.77 |
| relaxed, gripping, palm down, partially closed | r / l | **missing → NEEDS_ARTWORK**, fallback: reference hand | – | – | – | – |

Tolerances: rest error 6 px; width ratio 0.75–1.35; posed gap 2 px; posed drift 4 px. Even the reference hand drifts
about 3 px when the arm hangs, because the cuff over the wrist deforms. No variant swaps sides.

The palm-up hand is the Phase 2 foreshortened approximation. It is flagged, never used by default, and the shrug now
prefers the open palm as requested.

The first auto-align suggestion was a plain difference vector, and it made the right fist worse (9.9 → 15.3 px):
the cuff hides a different part of the drawing at every offset. It was replaced by a search on the measure itself.
In the editor, auto-align is a button. Its offsets are not applied unless the user clicks it.

## 6. Corrective artwork switching

`CorrectionSwitchController`. Each trigger has:
* a triggering joint and an angle reading taken from the solved bones;
* an "on" threshold and hysteresis;
* an asset, a binding, a priority, compatibility and an approval state.

| Trigger | On at | Hysteresis | Asset in the package |
|---|---|---|---|
| shoulder raise (armpit gusset) | abduction 120° | 8° | procedural gussets, **pending review** |
| deep elbow bend | 100° | 8° | **missing** |
| sleeve creases | 60° | 6° | **missing** |
| wrist rotation | 40° | 5° | **missing** |
| hip flexion | 40° | 5° | **missing** |
| knee flexion | 45° | 5° | **missing** |
| trouser folds | 25° | 4° | **missing** |

**Hysteresis.** The elbow reading sequence 90 → 105 → 96 → 93 → 91 → 99 → 101 switches
off → on → on → on → off → off → on (tested). The state carries over between frames in cycle renders and drags.

**Continuous corrections.** These stay the Phase 3.1 rotation-dependent blend bands. They are reported with the
discrete ones.

**Missing drawings.** A triggered correction without a drawing is listed as a recommended asset. It becomes
**required**, making the pose NEEDS_ARTWORK, for a deep elbow bend above 125°. The evidence for that threshold:
* the band-only fold is clean at about 120° (`ik_switch/closeups/05_running_fists.jpg`);
* it breaks at 130° (`ik_switch/closeups/08_deep_elbow_bend.jpg`).

In the editor, any correction group can be forced on or off.

## 7. Garment deformation integration

IK never bypasses the garment engine. A solved pose always goes through `GarmentDeformationEngine.corrections`
(blend bands, hem and hair terms) before rendering, and no bone is ever scaled.

Measured quality checks on the real art (out of 17):

| Pose | Passed |
|---|---|
| Standing | 15 |
| Right-arm IK reach | 15 |
| Both arms raised by IK | 14 (armpit, gussets pending) |
| Deep elbow at 130° | **10** (sleeve fold) |

Regenerating the Phase 3.1 evidence with the Phase 3.2 solver:
* every fluid cycle check still passes (run and walk 9/9; moving 8/8);
* the plain baselines keep their known failures.

One regression appeared and was fixed. Aiming the sitting hands at the knees pulled the arms straight inward, and the
sitting preset dropped from 13/17 to 11/17 checks (armpit tears). The knees are out of reach for this character, so
the arms now keep their relaxed FK pose with a note. The preset is back to 13/17.

## 8. Editor functionality

**Project.** `autorig.editor_project` schema v2. The base state gains `ik` and `switches`. New operations:
* `ik.set`, `ik.reset`;
* `switch.set`, `switch.key`, `switch.offset`.

Version-1 projects migrate: their operations replay unchanged and the new sections get defaults (tested). Every
operation is validated before it enters the log.

**IK tab.**
* Per limb: planner / FK / IK mode, matched on switching.
* Numeric target, default or custom pole, elbow flip, torso follow, foot lock, maximum flexion, wrist or foot
  orientation.
* Reach readout, contact state and lock error.
* Pelvis offset sliders and "reset all".

**Canvas.**
* Square handles move wrist and ankle targets; diamonds move poles.
* While dragging, only the skeleton is solved, with one request in flight.
* On release, one operation is committed and the full artwork follows.

**Switches tab.**
* Thumbnails per hand variant, with status, a NEEDS ARTWORK placeholder and the measured wrist error.
* Auto and explicit choice, approve and reject (user actions only), alignment report, auto-align, nudge buttons,
  keys at the current phase.
* Correction groups with their current trigger state and force on/off.
* The face channel and semantic live-action buttons.

**Browser test** (Playwright, real backend, real package copy). The run:
* switches the right arm to IK;
* drags the wrist target with 30 human-paced moves (18 skeleton updates);
* switches the left leg to IK, unlocks it and lifts the ankle;
* checks FREE for the lifted foot, and PLANTED with < 0.05 px lock error for the other;
* picks a pending fist (shown as the reference hand with a "pending review" note), previews it, auto-aligns it;
* picks a missing grip (NEEDS_ARTWORK);
* runs a live action;
* restarts the server and checks that the IK target and the hand choice persist;
* then runs the Phase 3 / 3.1 steps and the export.

The test found two real UI bugs, both fixed:
* a null `max_flex` crashed leg IK;
* skeleton previews arriving during a drag remounted the handle and cut the drag short.

Screenshots are in `docs/phase3_2/screenshots/` (01–05). Drag latencies are in `ik_drag_latency_ms.json` there.

## 9. Moho export compatibility

**`autorig.rig_controls` v1** (`motion/rigctl.py`, JSON Schema `data/schemas/rig_controls.schema.json`, kept in sync
by a test). It holds:
* the four IK chains, with bones, lengths, reach and flexion constraints, and `no_axial_scaling: true`;
* the IK state;
* the contact states and locks;
* the switch groups, variants and state;
* the correction triggers;
* the hand library with alignment;
* the missing artwork (24 drawings);
* the live action map.

It is validated against the schema and semantic checks before export. The export manifest goes from 1.0.0 to 1.1.0
(additive) and writes `rig_controls.json`.

Every Moho mapping is `proposed_unverified`:
* switch groups → Switch Layers;
* chains → bone targets and IK;
* triggers → Smart Bone actions.

No Moho API was added to the allowlist, no `.moho` is written, and the motion code calls no Moho API (tested).

**Deviation from the request.** The request asked to extend "the existing versioned rig.json schema". The Phase 0
`rig_spec` is the Moho build contract checked against the strict mock, so unverified IK and switch fields were not
added to it. The controls are a versioned sibling document referenced by the export manifest.

## 10. Live puppeteering compatibility

The semantic actions include:
* hand switches per side (point, fist, open, relaxed, thumbs up, grip, auto);
* `ARM_RIGHT_REACH` / `ARM_LEFT_REACH` (strength 0..1) and release;
* `FOOT_LEFT_PLANT` / `FOOT_RIGHT_PLANT` / lift;
* `GESTURE_SHRUG`, `GESTURE_POINT`, `GESTURE_WAVE` (an IK oscillation with a held elbow), `GESTURE_IDLE`;
* face-channel `HEAD_TILT`, `MOUTH_OPEN` and `MOUTH_NEUTRAL`.

`action_ops` turns an action into ordinary editor operations, so undo works. Every action yields valid operations
(tested).

Body and face channels are separate. No Moho or device input name appears in the core: inputs are marked
"unassigned" until the Moho 14.5 Live Mode identifiers are discovered (LM track). No webcam tracking was implemented.

## 11. Test results

| Suite | Result |
|---|---|
| `tests/test_p32_ik_switch.py` | 31 passed |
| Full Python suite | 403 passed, 2 skipped (native Moho tests: NOT EXECUTED) |
| Editor unit tests (vitest) | 11 passed |
| Browser end-to-end (Playwright, headless Chromium) | passed |

The Phase 3.2 suite covers:
* IK: accuracy, lengths, poles, limits, unreachable and degenerate targets, determinism, mirroring, extension
  stability, FK/IK matching, the sweep;
* legs: locks under pelvis moves, lifting, ground clamping, contact cycles in walk and run, sitting reach;
* switches: resolution order, pending / missing / rejected handling, keys, rigid offsets, hand inventory and
  alignment, posed attachment under IK, neutral render untouched;
* corrections: hysteresis, required elbow drawing;
* schema: sync, validation errors, migration from v1, operations / undo / reopen;
* live actions, export with `rig_controls`, and the committed evidence.

These are geometric and pixel measurements of the preview renderer on real art. They are not proof of
production-quality animation, and nothing was checked in Moho (native tests: NOT EXECUTED).

## 12. Visual examples

All files are in `projects/schoolboy_tpose/character_package/natural_pose/ik_switch/`. Clean and `_diag` versions
are provided; diagnostics show bones, IK targets, poles, reach circles and contact states. Close-ups of wrist,
elbow, sleeve cuff, knees and ankles are in `closeups/`.

1. `01_standing`: relaxed standing. The preferred relaxed hand is missing, so the reference hand is shown (reported).
2. `02_right_arm_ik_reach`: wrist on target (0 px), elbow 80°, 15/17 checks.
3. `03_both_arms_raised_ik`: both wrists on target. The shoulder-raise trigger is on; the gusset is pending, so
   armpit tears remain (14/17).
4. `04_pointing_hand`: PREVIEW of the pending pointing hand on the IK pointing arm. Approved-only, it stays
   NEEDS_ARTWORK.
5. `05_running_hand_switches`: approved-only (open hands) next to a PREVIEW of pending fists, same phase.
6. `06_walking_planted_feet`: six walk phases with contact states; planted slide 0.005 px.
7. `07_sitting_foot_constraints`: feet locked. Hands on knees are out of reach for this character (arm 240–248 px,
   shoulder to hip about 298 px), so the arms stay relaxed (reported). NEEDS_ARTWORK (seat and foreshortened thighs).
8. `08_deep_elbow_bend`: 130° by IK. **No corrective elbow drawing exists**: band deformation only, the fold breaks,
   NEEDS_ARTWORK.
9. `09_hand_switch_sheet`: every hand pose for both sides, with rest and posed measurements and NEEDS_ARTWORK cells.
10. `10_ik_diagnostic`: arm target with an explicit pole, right foot lifted (FREE), left foot locked, pelvis shifted.
11. `ik_reach.gif`: 20 full-artwork frames of the wrist sweeping 200° with the bend held. Continuity is measured on
    241 skeleton samples (§2).

Nobody has reviewed these images. `artistic_review_required` stays true.

## 13. Performance measurements

Cloud sandbox CPU, x86. **Not measured on the user's M1.**

| Measurement | Result |
|---|---|
| `TwoBoneIKSolver.solve` | 23 µs |
| Skeleton solve, no IK | 3.8 ms |
| Skeleton solve with an IK arm (clavicle fixed point inside the balance loop) | 11.0 ms |
| Switch resolution | 0.044 ms |
| Correction selection | 0.1 ms |
| Skeleton-only pose request in the engine | 6.7 ms (about 150 per second) |
| Deformation and tear analysis | 1.76–1.98 s |
| Full artwork frame | 1.96–2.18 s (about 0.5 fps) |
| Editor IK drag, input to skeleton preview (headless Chromium) | 19 updates in a 1.8 s drag: median 15 ms, p90 63 ms, max 892 ms |
| Skeleton playback in the editor | 54–60 fps |

Judged against the 30 fps target: skeleton previews while dragging run well above 30 updates per second in the
cloud. Full-artwork previews do not; they follow about 2 s after release. The worst drag update waits behind a full
render, because one engine lock serves both.

## 14. Remaining defects

* **No hand drawing is approved.** Fist, pointing, thumbs-up and palm-up are pending review.
* **Missing hand poses.** Relaxed, gripping, palm-down and partially-closed hands are missing on both sides.
* **Hand alignment.** The right fist and pointing hand sit 9.9 / 11.3 px off and the left thumbs-up 7.9 px off until
  the user applies auto-align. Rotation alignment is manual.
* **Palm-up** is a foreshortened approximation (width ratio about 0.5).
* **Hand halo.** A faint dotted halo shows around the reference-derived hand cut-outs (visible in
  `09_hand_switch_sheet.jpg`). It comes from the Phase 2 cut-out.
* **No correction drawings exist.** Above 125° the elbow fold breaks (`08`). Armpit gussets are procedural and
  pending, so raised arms still tear.
* **Front view only.** Knees bend laterally, so a lifted leg reads as slightly bowed. There is no sagittal knee
  drive or foreshortening.
* **Sitting.** Hands cannot rest on the knees or thighs with this character's proportions. The seat and
  foreshortened thigh drawings are missing.
* **Drag latency.** The first drag update can wait up to about 0.9 s behind a running full render (single lock).
* **No 30 fps artwork.** The CPU deformer renders full artwork at about 2 s per frame.
* **Legs have no FK angles.** Returning a leg from IK to the planner can jump; the jump is reported, not hidden.
* **No input bindings.** Live actions have no device bindings (Live Mode identifiers undiscovered).
* **Moho never ran**, and Phase 0 is not natively verified.
* **The run cycle is not production-ready.** IK solving alone does not make it so; it still needs timing review and
  correction artwork.

## 15. Recommendations for Phase 4

Do not start Phase 4 until these are done:

1. Run the Phase 0 native verification on the Mac. It remains the gate for any Moho export.
2. Review the hand variants in the Switches tab (approve or reject). Apply auto-align where it helps, then draw the
   missing relaxed, gripping, palm-down and partially-closed hands through the reviewed artwork workflow.
3. Draw corrective art for the deep elbow (above about 120°), the knees and the armpits, and review the gussets.
4. Gather evidence for Moho Switch Layers, bone targets and IK, and Smart Bone actions. Record it in the allowlist,
   the evidence document and the mock, as CLAUDE.md requires, before translating `rig_controls.json` to Moho.
5. Finish the Live Mode discovery (D0–D6) before binding live actions to inputs.
6. For a 30 fps artwork preview on the M1: a GPU or WebGL deformer, or cached per-layer meshes, and a solve lock
   separate from the render lock.

## 16. Verdict: PARTIAL

| # | Acceptance criterion | Result | Evidence |
|---|---|---|---|
| 1 | Arm IK works on both sides | PASS | tests; `02`, `03`, `10` |
| 2 | Leg IK works on both sides | PASS | tests (locks, lift, clamp); `10`; e2e |
| 3 | Bone lengths remain consistent | PASS | lengths exact; no scaling anywhere |
| 4 | Elbows and knees bend predictably | PASS (front view) | held bend side, 0 flips in sweeps; knees lateral only |
| 5 | Wrist targets can be dragged interactively | PASS | e2e drag; skeleton preview median 15 ms |
| 6 | Ankle targets can be dragged interactively | PASS | e2e drag with unlock and lift |
| 7 | Feet remain planted when locked | PASS | lock error < 0.01 px; gait slide ≤ 0.005 px |
| 8 | FK/IK switching causes no major jumps | PASS | ≤ 0.02 px measured |
| 9 | Hand variants switch without wrist misalignment | **PARTIAL** | posed gap 0 px and drift ≤ 2 px for all; rest errors 9.9 / 11.3 / 7.9 px on three variants until auto-align is applied; all pending |
| 10 | Missing hand artwork is explicitly reported | PASS | NEEDS_ARTWORK with fallback, in the editor, sheet and spec |
| 11 | Existing garment deformation remains functional | PASS, with defect | Phase 3.1 cycles unchanged; deep elbow breaks |
| 12 | Presets use IK and switches appropriately | **PARTIAL** | pointing (IK + pointing hand), run (fist or relaxed), shrug (open palm), standing (relaxed → fallback); sitting hands unreachable; approved-only renders show the reference hand |
| 13 | Project changes survive saving and reopening | PASS | tests; e2e server restart |
| 14 | Original neutral-pose artwork remains faithful | PASS | neutral render bit-identical; e2e fidelity exact; source hash unchanged |
| 15 | Rig metadata is compatible with the future Moho exporter | **PARTIAL** | schema-validated spec; mappings `proposed_unverified`; no verified exporter |
| 16 | Automated tests pass | PASS | §11 |
| 17 | Visual demonstrations show actual improvement | **PARTIAL** | real renders show IK reach, planted feet and attached hand switches; unreviewed; pending art; deep-elbow defect |

The machinery works and is measured. Production quality depends on artwork review and drawings that do not exist
yet, and on Moho verification. Phase 4 was not started.
