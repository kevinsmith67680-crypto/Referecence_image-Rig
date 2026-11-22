# Phase 3.8: rigging a new, previously unseen character with AutoRig

**Question:** can AutoRig rig a new character end to end with its normal workflow?

**Answer:** **yes, technically, with gaps.**
- The new character went through every stage: import, background extraction, segmentation, reconstruction, skeleton,
  garment rig, a 16-drawing hand library, poses, an acting sequence, reference motion, the editor, save/reload and
  export.
- The renders are produced by the same code that rigs the schoolboy. Ten character-specific assumptions were found;
  eight were fixed in a general way and documented.
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
- shoe tops (v2 after the light-shoe fix);
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

TBD_ANIMATION

## 17. Reference-motion testing (step 18)

TBD_REFERENCE

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

TBD_REGRESSION

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
arm/hand split, and the trouser hem at the shoe.

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

## 25. Verdict

TBD_VERDICT
