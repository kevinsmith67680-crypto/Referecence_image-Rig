# Phase 3.8: rigging a new, previously unseen character with AutoRig

**Question:** can AutoRig rig a new character end to end with its normal workflow?

**Answer:** **yes, technically, with gaps.**
- The new character went through every stage: import, background extraction, segmentation, reconstruction, skeleton,
  garment rig, a 16-drawing hand library, poses, an acting sequence, reference motion, the editor, save/reload and
  export.
- The renders are produced by the same code that rigs the schoolboy. The new character exposed
  character-specific assumptions: nine were fixed in a general way (each checked against the schoolboy), one help text
  was corrected, and eight are documented architectural issues.
- It is **not** production-ready, and nothing is artistically approved. The arms-down shoulders, the trouser cuffs over
  the white trainers and the hand palette need artwork or review. Several gestures need drawings that do not exist.
- Moho was not run.

**Verdict: PARTIAL** (§25).

Companion reports:
- [hand library](phase3_8_hand_library_report.md);
- [segmentation](phase3_8_segmentation_report.md);
- [rigging quality](phase3_8_rigging_quality_report.md);
- [generalisation](phase3_8_generalisation_report.md);
- [export readiness](phase3_8_export_readiness.md).

Evidence: `docs/phase3_8/new_character_evidence/` (01–20, `results.json`) and `docs/phase3_8/screenshots/` (browser).

## 1. Test environment

- Claude Code on the web, cloud Linux container: 4 CPUs, 15 GB RAM shared by every job.
- Python 3.12 venv; Node 22; Chromium + Playwright (pre-installed); ffmpeg; MediaPipe Tasks (CPU).
- Replicate through the network proxy: `vufinder/sam3` pinned `1bf97763…`, `black-forest-labs/flux-fill-dev`.
- Moho is not available here. The user runs Moho Pro 14 on an M1 Mac.

## 2. Source commit

Started from `claude/eloquent-cori-iv4ab1` at **e80bc4f** (Phase 3.7 plus the $5 budget change). The Phase 3.8 work
is committed on the same branch.

## 3. Input artwork

| | character | hand library |
|---|---|---|
| user's file | "Tough Student in School Uniform(1).png" | "Cartoon Hand Gesture Grid(1).png" |
| as received here | chat attachment, re-encoded to JPEG | chat attachment, re-encoded to JPEG |
| stored (immutable, chmod 444) | `references/tough_student/tpose_front.jpg` | `references/tough_student/hand_grid/hand_grid_black.jpg` |
| size / mode | 1086×1448 RGB, no alpha | 1254×1254 RGB, no alpha |
| sha256 | `4ccb022f…1e1000` | `47c59800…6b88` |
| background | opaque near-white (253/251/252) | opaque black (0/0/0) |

**Blocker, partial.** The PNG bytes were not available in this environment; only the chat's JPEG re-encodes were.
They are the same images (same character, same 16 hands), not substitutes. JPEG ringing around the outlines is in the
data. To rerun on the originals, put them in `references/tough_student/` and `references/tough_student/hand_grid/`;
the commands in §5 and the hand report §1 regenerate everything.

**Finding:** the hand grid is the schoolboy's sleeve-free sheet redrawn. The silhouette IoU is 0.979–0.989 per cell;
it is recoloured with peach skin and cel shading on black. The 16 hands are not new shapes, and the sheet's labelling
problems carried over.

## 4. Import results (step 2)

Each image's import report is in `import_reports.json` and `01_original_character.png`. It covers:
- dimensions and mode;
- whether a nominal alpha band holds *genuine* transparency (neither does: both are opaque, with no alpha band);
- the border background statistics, the content bbox, the hash, no scaling, and the coordinate system.

## 5. Background removal (step 3)

`02_background_removed.png` shows the composite on white, black, mid-grey and a checkerboard, plus 3× close-ups.

| | value |
|---|---|
| missing pixels | 14 |
| paper kept as character | 0 |
| clipped outline | 0 |
| white shirt, trainers, laces, cuffs kept | 12/12 regions |
| light fringe after edge decontamination | 4 px |

This needed two Phase 1 fixes: the spatial paper rule (default) and opt-in outline completion. The first run had
5,434 missing px (clipped outline under the soles and hands) and 452 px of paper kept in the hair gaps. Inspected at
full resolution: the hair tips, the crest, the chain, the laces and the trainer outlines are intact.

## 6. Segmentation (steps 4–5)

- **Phase 1:** all 17 body parts and the face features (eyes, pupils, brows, nose, mouth, both ears) were found
  automatically. Coverage 0.99997, linework 0.9998, no overlaps.
- **Garment rig:** 41 parts. The chain and crest are rigid on the chest.
- **Uncertainties:**
  - the uniform vocabulary (chain → "tie", shirt → "sweater", trim → "white");
  - the hand outline left in the forearm layer;
  - the trouser-hem outline at the trainers;
  - the eye whites under the irises, which FLUX redrew dark.
- **Detail preservation:** only hidden regions were reconstructed; 0 generated pixels are visible in neutral.

Details: [segmentation report](phase3_8_segmentation_report.md).

## 7. Skeleton (step 6)

`05_skeleton_overlay.png`: 19 bones, all generated from the new character.
- The shoulder pivots are lifted about 30 px to the top of the armhole, not at the sleeve centre.
- The elbows and knees are mid-limb estimates (flagged "verify").
- The IK keeps bone lengths: the arm length stays 257.0 / 253.2 px through every arm test.

## 8. Pivot accuracy

There is no ground truth for this character, so accuracy is judged against the artwork:
- the wrists sit at the cuff edges;
- the ankles sit at the trainer tops;
- the hips sit at the thigh tops under the hem;
- the shoulder pivots sit at the shoulder point.

The elbows and knees cannot be located on straight limbs. A person should confirm them in the Joints tool (the e2e
test showed a pivot edit persisting).

## 9. Garment rigging (step 15)

Rigging quality report §4:
- The structured rig runs unchanged.
- The arms do not drag the lapels or the chain in relaxed poses: deviations ≤ 1.4 px, and the chain and crest are
  rigid.
- Raising an arm deforms the lapel and collar by 3–4 px (NEEDS_ARTWORK).
- Shoes grade FAIL in 8 of 12 garment tests (the schoolboy: WARNING) because of the trouser-cuff marks.

## 10. Hand extraction (step 8)

16/16 hands extracted, using a new dark-background mode:
- 0 light-edge pixels and 0 skin-edge pixels;
- outline coverage 98.8–100 %;
- no scaling.

Details: [hand library report](phase3_8_hand_library_report.md) §1.

## 11. Handedness classification (step 9)

Measured and inspected; the inspection was the assistant's, not a person's.
- Rows 1–2 are left hands, rows 3–4 right.
- The pointing pair is two right hands; `left_pointing` is a flagged mirrored copy.
- The cupped pair is stored on opposite sides and is reassigned.
- 6 closed-outline wrists need a manual pivot check.

These are stored in HandAssetMetadata.

## 12. Gesture selection (step 11)

The hands are registered in the existing `hand_r` and `hand_l` switch groups. The Phase 3.7 engine chooses:
- review mode: the pending pointing drawing;
- production: the original hand, reported as NEEDS_APPROVAL (`14_pointing.png`).

No silent fallback; `palm_up` and `partially_closed` are reported missing.

## 13. Wrist alignment (step 12)

All 16 drawings are attached under the white cuffs, and all 16 wrist checks PASS (`13_wrist_alignment.png`).

The first run exposed a duplicated-cuff defect (a white flat-fill blob), which is fixed.

The store-alignment check finds 7/14 active wrists at 0.71–0.74× the sleeve opening against a minimum of 0.75×.
A per-variant scale correction is available; none was applied.

## 14. Corrective artwork (step 16)

Generated from the character's own pixels, all pending:
- shoe tops (v3 after the light-shoe fix; v1 dark grey, v2 with dark streaks);
- caps, folds, gussets;
- edge decontamination.

Gap points drop 59 → 21 (arm raised) and 82 → 40 (squat). Protected-pixel violations: 0 in all 15 poses. Rigging
quality report §5.

## 15. Pose previews (step 14)

`17_pose_contact_sheet.png`, all 15 poses:

| status | poses |
|---|---|
| SUPPORTED | 2: T-pose, subtle shrug |
| NEEDS_APPROVAL | 7: relaxed, weight shift, pointing ×2, hands on hips, arm raised, squat |
| REQUIRES_ALTERNATIVE_ARTWORK | 6: expressive shrug, deep elbow, walking, running, sitting, forward lean |
| UNSUPPORTED | 0 |

Arm lowering (step 7) is technically sound, but the arms-down shoulders read square and padded. The missing asset is
an arms-down shoulder drawing.

## 16. Animation tests (step 17)

`18_animation_preview.gif` and `18_animation_contact_sheet.png` are AutoRig renders labelled REVIEW PREVIEW: 91 frames
at 12 fps, about 3.0 s per frame on the cloud CPU.

The sequence is one Phase 3.6 performance. It goes through the one pipeline (`PerformanceDirector` →
`MotionEngine.pose`): no second solver, no scaled bone. The T-pose key is the character's own rest angles.

| t (s) | key | review preview hands | production hands | status |
|---|---|---|---|---|
| 0.0 | T-pose | original | original | production-supported |
| 1.4 | lower both arms | relaxed (pending) | original open hand | review only |
| 2.3 | shift weight | — | — | production-supported |
| 3.2 | raise right arm | original | original | production-supported |
| 4.1 | point (hold 0.9 s) | right pointing (pending) | original open hand | review only |
| 5.6 | shrug (SHRUG_SUBTLE, `shrug_low`) | original | original | production-supported |
| 6.9 | relax | relaxed (pending) | original open hand | review only |

The expressive palm-up shrug is not in the sequence. It needs palm-up drawings that do not exist (NEEDS_ARTWORK), and
the subtle shrug is shown instead.

Motion checks (`PerformanceQualityEvaluator`, 48 Hz, 361 frames):

| check | result |
|---|---|
| velocity jumps / solver clamp and balance events | 0 / 0 |
| foot sliding (planted feet) | 0.01 px, PASS |
| contact transition jumps | none |
| joint limits | **`knee_l_flexion` 10.8–18.5° outside the 10° stance limit** |
| verdict | **REVIEW** |

The knee readings are visible in the contact sheet: at 2.33 s and 2.92 s (weight on the left leg) the left knee bends
inward. The cause is diagnosed in the generalisation report §3.7. At weight 0.73 the static balance shifts this
character's pelvis 94 px sideways (schoolboy: 77 px). The pelvis height then follows the free leg's soft knee, and
the loaded leg must bend past its limit to reach its locked ankle. The schoolboy has the same conflict at ≤ 0.26 px,
within tolerance. The general fix, letting the loaded leg's limit win, changes the schoolboy's output and was not
applied in this phase.

The same verdict appears in the standard Phase 3.6 evidence run on this package (`autorig performance-evidence`):

| scenario | new character | schoolboy |
|---|---|---|
| acting test | REVIEW (knee_l 10.5–17.9°) | PASS |
| classroom desk contact | PASS (contact sliding 0.07 px) | PASS |
| secondary motion (hair, hem) | settles to 0° at the end | settles to 0° |

Phase 3.1 gait cycles (`autorig motion`) were also run. The six fluid running and walking cycles pass every check on
both characters, with one exception: the new character's `casual_walk_moving` has a jerk ratio of 60.4 against a
threshold of 60 (left clavicle; schoolboy 52.8). The plain (non-fluid) comparison cycles fail `arm_overlap_cycles` on
both characters, as on the schoolboy before; the new character's plain walk also fails `continuity_spike_px` and
`jerk_ratio`. Gaits remain front-view approximations: walking and running are REQUIRES_ALTERNATIVE_ARTWORK (§15).

Artistic hint (information only): "lower both arms" is a 77° arm move without anticipation. Visually, the arms-down
shoulders still read square and padded, and the trouser-cuff marks show between the trainers in most frames. Nothing
here is an artistic approval.

## 17. Reference-motion testing (step 18)

No human video was available, so the references are the Phase 3.7 **synthetic** ones, labelled synthetic in every
image (`14b_reference_motion.png`) and never presented as human motion. `autorig reference-evidence` ran on this
package.

| reference (synthetic) | keys | feasibility on this character | note |
|---|---|---|---|
| pointing | 12 | SUPPORTED | right abduction tracked to 2.1° RMS; review preview with the pending pointing hand |
| shrug | 8 | REQUIRES_ALTERNATIVE_ARTWORK | the forearms come toward the camera (depth): palm-up drawings needed |
| walking | 19 | REQUIRES_NEW_VIEW | forearms and shins move in depth; not stretched |

Retargeting moves angles only. In a retargeted pointing frame, the 19 bone lengths change by −0.008 to +0.007 px
(rounding), so no bone is scaled.

**Video round trip.** The run used a real model locally: an AutoRig render of this character (acting test
2.4–6.0 s, 44 frames) was encoded to MP4, decoded with ffmpeg, run through MediaPipe Pose on the CPU (0.065 s per
frame), analysed and stored as `motion_references/tough_student_roundtrip` (append-only, named after this character
by fix #8).

| | new character | schoolboy |
|---|---|---|
| mean landmark visibility | 0.983 | 0.985 |
| shoulder/torso width ratio → view | 0.641 → **three_quarter**, `usable: false` | 0.675 → front, usable |
| RMS error vs the known parameters: right abduction / right flexion / right clavicle / head | 12.2° / 15.6° / 22.5° / 6.4° | 13.7° / 18.8° / 16.7° / 8.9° |

Two findings:
- The angle errors have the same size on both characters. They measure MediaPipe on cartoon proportions, not
  human-reference accuracy.
- **The view check rejects this front-view render as three-quarter.** It compares the detected shoulder width with
  the shoulder-to-hip distance against a fixed 0.65 (`refmotion/importer.QUALITY`), based on adult human proportions
  (0.75–0.9 when facing the camera). On this character MediaPipe measures 0.641; on the schoolboy, 0.675, which
  passes. A human clip is judged by the human's proportions, so this affects only round trips on cartoon renders. It
  is a fixed-ratio assumption of the same kind as generalisation report §3.3: documented, not changed.

## 18. Editor functionality (step 19)

A new browser test, `editor/e2e/newchar.e2e.mjs`, ran the editor on a scratch copy of the new package in headless
Chromium. It **passed** (147 s). Screenshots are in `docs/phase3_8/screenshots/01..12`. Every step is checked through
the persisted state, not the UI alone:

| # | user action | checked in the data |
|---|---|---|
| 1 | view the imported artwork | 27 layers listed |
| 2 | edit a segmentation mask (polygon, transfer to torso) | `mask_ops` +1, 1,866 px moved, no gap, neutral still exact |
| 3 | adjust a bone pivot (drag) | `bones.upper_arm_r.head` moved by the drag |
| 4 | modify mesh weights | `garment_weights.ops` +1 |
| 5 | inspect garment parts | the crest at its centroid explains as part `badge`, class RIGID |
| 6 | view generated corrections | 13 assets, all `pending_review` |
| 7 | approve / reject hand drawings; adjust a wrist offset | approval `approved`; `rejected` for another drawing; placement `dx` 1 |
| 8 | inspect hand metadata | production decision explained; 18 metadata records |
| 9–10 | switch a hand; preview poses | `switches.hand_r.user = fist`; pose panel shows a feasibility class |
| 11–12 | edit an animation curve; preview motion | key curve `bezier`; playback; a rendered frame |
| 13 | import (select) reference motion and retarget | result SUPPORTED, 12 keys |
| 15 | undo / redo | cursor −1 / +1 |
| 14, 16 | save a version, close (stop the server), reopen | pivot, mask, weights, approval, offset, switch, curve and reference selection all persist; source unchanged |
| export | from the editor | validation errors 0, `production_ready: false` |

The approvals in this test are persistence checks on a scratch copy, not artistic approvals.

## 19. Project persistence (step 20)

`19_project_reload.png`:
- 10 operations were applied through `EditorProject.submit` on a scratch copy: bone, mask, weights, approve, reject,
  wrist placement, hand metadata, switch, corrective mode, reference.
- Then undo/redo and a saved version.
- After dropping every object and reopening from disk:
  - project state identical;
  - render pixel-identical;
  - schema v9 = code v9;
  - no absolute paths in `project.json`;
  - every referenced file present;
  - hand-store hashes verified;
  - source sha256 unchanged.

## 20. Regression results (step 22)

| suite | before (clean e80bc4f) | after (Phase 3.8) |
|---|---|---|
| backend `pytest` | 591 passed, 2 skipped (native Moho: NOT EXECUTED), 18 min 14 s | FINAL_SUITE |
| frontend `vitest` | 11 passed | 11 passed (editor sources unchanged; `dist` not rebuilt) |
| schoolboy editor e2e (`e2e/editor.e2e.mjs`, Playwright, headless Chromium) | passed (Phase 3.7) | **passed** (583 s; screenshots written to scratch, so the committed ones are unchanged) |
| new-character editor e2e (`e2e/newchar.e2e.mjs`) | — | **passed** (147 s) |

No existing assertion was weakened or removed. The 15 new tests are in `tests/test_p38_newchar.py`.

Every fix that touches shared code has a check that the schoolboy's outputs are unchanged:

| fix | schoolboy check |
|---|---|
| spatial paper rule, opt-in outline completion (Phase 1) | Phase 1 tests unchanged and passing, including the eroded-segmentation test; committed Phase 1 projects are not re-run |
| dark-sheet mode, manifest text | the committed v2 sleeve-free pack rebuilds byte for byte (test) |
| `cuff_cover` drops generated pixels | `forearm_r__hscuff` / `forearm_l__hscuff` pixel-identical (checked before/after) |
| shoe tops take the dominant shoe tone | dark shoes keep the old rule (test); no new schoolboy versions |
| outline-emblem badge | all six schoolboy decorations identical (test pins badge, buttons, tie, pocket flaps) |
| `ik-demo` active fist, round-trip name, view sources, historical-copy guard | names and sources unchanged (test) |

The schoolboy's committed evidence was not regenerated by this phase. The Phase 3.6 acting test still grades PASS on
the schoolboy and REVIEW on the new character (§16).

## 21. Generalisation findings (step 23)

Full list in the [generalisation report](phase3_8_generalisation_report.md).

Fixed, each checked against the schoolboy:
1. Phase 1 colour-threshold foreground (clipped outlines, paper in the hair gaps).
2. White-only hand sheets.
3. The schoolboy text in the pack manifest.
4. Generated pixels kept in the stored-hand cuff cover (the duplicated-cuff blob).
5. Dark-only shoe colours.
6. Solid-gold-only badges.
7. The `ik-demo` hard-coded sheet fist.
8. The round trip named after the schoolboy.
9. The view manifest citing the schoolboy's sheet.

Documented: the fixed garment palette and vocabulary, absolute pixel thresholds, the Phase 2 wrist fill, the Phase 1
arm/hand split, the trouser hem at the shoe, the pelvis height when the soft knee and the knee limit conflict (the
acting test's REVIEW), and the fixed front-view ratio for reference clips.

## 22. Native Moho readiness

Export readiness report:
- **Ready as a validated asset package:** 0 validation errors, 9/12 gates PASS, `production_ready: false`.
- **Not a Moho rig:** the Phase 4 exporter was not started, and the Phase 0 native verification is NOT EXECUTED.
- Every Moho mapping (rig controls, switch layers, garment weights) remains `proposed_unverified`.

## 23. Remaining artwork requirements

`missing_artwork_report.json`:
- **Hands:**
  - a palm-up open hand per side (the expressive shrug);
  - a partially-closed hand;
  - a drawn left pointing hand.
- **Body:**
  - an arms-down shoulder / sleeve cap per side;
  - a deep-elbow sleeve fold;
  - a raised-shoulder collar;
  - seated thighs, seat and trousers;
  - a torso lean forward (3/4);
  - a knee-drive thigh and a tucked shin;
  - side and 3/4 views for walking and running.
- **Face:** blinks, mouth shapes, brow skin, turned heads (the Phase 1 facial readiness list).

## 24. Critical defects

None blocks the technical workflow. These block production quality:
1. **Arms-down shoulders** read square and padded; the cap bulges 16–21 px mid-lowering. Missing shoulder drawing.
2. **Trouser-cuff marks** beside the inner trainers whenever the stance narrows. Segmentation; mask edit or hem rule.
3. **Eye whites under the irises** were drawn dark by FLUX. Reject or flat-fill before eye controls.
4. **Hand palette:** the library skin is paler than the character's; wrists are slightly narrow. Skin-matched
   candidates exist; review and a scale correction are needed.
5. **Dotted hand-outline arc** over the original palm when the wrist bends. Mask edit.
6. **Garment labels** misname the chain and shirt, and the trim is not found as trim (architecture).
7. **Loaded knee bends during weight shifts** (10.8–18.5° against a 10° limit; acting test REVIEW). The solver's
   pelvis-height priority; a general fix is proposed, not applied (generalisation report §3.7).

## 25. Verdict

**PARTIAL.**

AutoRig rigged a previously unseen character end to end with its normal workflow, and every stage produced real,
inspectable output. It is not a full technical PASS: 5 of the 24 acceptance criteria are only partly met (below). It
is not production-ready either. No drawing, corrective or generated pixel has been approved by a person, artwork is
missing, and Moho was not run.

Technical completion and artistic readiness are separate:

| | status |
|---|---|
| technical workflow on a new character | complete, with the partial items below |
| artistic production readiness | **not ready**: 16 hand drawings, 13+ corrective assets and the generated extensions are pending; arms-down shoulder, palm-up hands, deep elbow, seated, side/3/4 and facial artwork are missing |
| native Moho | **NOT EXECUTED** (Phase 0 native verification outstanding; Phase 4 not started) |

Acceptance criteria (step 26):

| # | criterion | result | evidence / reason |
|---|---|---|---|
| 1 | both actual input files import | **PARTIAL** | both imported and analysed, but as the chat's JPEG re-encodes. The PNG bytes were not available (§3), so this is a blocker for byte-exact inputs |
| 2 | background extraction succeeds | PASS | 14 px missing, 0 paper kept, 0 clipped outline; 4 px fringe after decontamination (§5) |
| 3 | segmentation usable | PASS | all 17 parts and the face found automatically; uncertainties listed (§6) |
| 4 | skeleton generated from the new character | PASS | 19 bones from this artwork (§7) |
| 5 | major pivots plausible | PASS | wrists, ankles, hips, shoulders at the artwork's joints; elbows and knees are mid-limb estimates for a person to confirm (§8) |
| 6 | T-pose arms repositioned without severe deformation | **PARTIAL** | lengths constant and motion continuous, but the arms-down shoulders read square and padded; the cap bulges 16–21 px mid-lowering. Needs an arms-down shoulder drawing (§15) |
| 7 | hand sprites correctly extracted | PASS | 16/16, no halo, no cut-off parts (§10) |
| 8 | handedness and gestures verified | **PARTIAL** | measured and inspected by the assistant, not by a person. The pointing pair is two right hands (mirrored copy); the cupped pair is reassigned (§11) |
| 9 | wrist alignment works | PASS | 16/16 wrist checks. Warning: 7/14 drawings are 0.71–0.74× the sleeve opening (§13) |
| 10 | all 16 variants previewed | PASS | `11_*`, `12_*`, `13_*` |
| 11 | approval states respected | PASS | pending drawings only in labelled review previews; production uses the original hands |
| 12 | appropriate hands for supported gestures | PASS | constraint-first selection; pointing previews the pending pointing drawing (§12) |
| 13 | missing gestures reported | PASS | palm-up and partially-closed reported as NEEDS_ARTWORK |
| 14 | garment deformation works for the new uniform | **PARTIAL** | the rig runs and the chain and crest stay rigid, but shoes FAIL in 8/12 tests (trouser-cuff marks) and the labels misname the chain and shirt (§9) |
| 15 | corrective requirements identified | PASS | assets generated (pending); missing drawings named (§14, §23) |
| 16 | supported poses rendered | PASS | 15 poses with statuses (§15) |
| 17 | acting sequence generated | **PARTIAL** | generated and rendered; the motion check is REVIEW for the loaded knee (10.8–18.5° vs 10°), visible in the weight-shift frames (§16) |
| 18 | editor modifies the new project | PASS | `newchar.e2e.mjs` passed (§18) |
| 19 | save and reload | PASS | state and render identical after reopen (§19) |
| 20 | schoolboy functionality intact | PASS | FINAL_SUITE; schoolboy e2e passed; schoolboy outputs pinned (§20) |
| 21 | Moho-ready metadata exported | PASS | 0 validation errors; mappings `proposed_unverified`; no `.moho` (§22) |
| 22 | tests pass without weakening | PASS | no assertion weakened; 15 new tests (§20) |
| 23 | real visual evidence | PASS | `new_character_evidence/01–20`, browser screenshots; no placeholders |
| 24 | outstanding approvals documented | PASS | `approval_report.json`, `missing_artwork_report.json`, §23 |

AI inference was available: SAM 3 and FLUX Fill ran on Replicate ($1.58 of the $5.00 ceiling used), and MediaPipe ran
locally. No stage is BLOCKED for lack of inference. Native Moho is NOT EXECUTED.

**Answer to the phase's question.** AutoRig can rig a new, previously unseen character with its normal workflow and
no per-character code. It needed nine general fixes, each checked against the schoolboy. What stands between this
rig and a production rig is artwork and review, not code: an arms-down shoulder drawing, palm-up hands, the
trouser-cuff mask edit, the eye-white rejection, and a person's approval of the hands and correctives. Two code
issues remain open: the pelvis-height priority behind the knee REVIEW, and the fixed garment palette and vocabulary.
Phase 4 has not been started.
