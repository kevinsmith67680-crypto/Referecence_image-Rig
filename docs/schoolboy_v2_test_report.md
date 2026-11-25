# AutoRig functionality test: schoolboy v2 image (2026-11-25)

| | |
|---|---|
| Input | the user's chat image, stored byte for byte as `references/schoolboy_v2/tpose_front_v2.jpg` |
| Image | 1024 × 1536 RGB, sha256 `63076f7c…`. A new drawing of the schoolboy: not the committed `tpose_front.jpg` (1086 × 1448, mean pixel difference 50). Arms about 15° above horizontal, big-head proportions, slimmer limbs |
| Project | `projects/schoolboy_v2_test/`: `character_project`, `character_package`, `moho_asset_package`, `native_checks` |
| Workflow | the normal one (the Phase 3.8 rule for new characters), from the character's own data only |
| Cost | SAM 3 $0.0096 (re-runs hit the cache: $0). Total spent so far $1.5847 of the $5.00 ceiling. Reconstruction was deterministic (no FLUX) |
| Result | **every stage ran end to end.** Two generalisation defects were found and fixed (§3). Nothing is approved, and Moho did not run: the native stages are MOCK_ONLY / NOT_EXECUTED, as designed |

Images are previews and simulations, never Moho output.

## 1. Stages

| stage | command | result |
|---|---|---|
| Phase 1 analysis | `autorig analyze … --character-id schoolboy_v2` | 17 body parts and all face features (eyes, pupils, brows, nose, mouth, both ears). Coverage 99.4 %, 0 overlaps, 0 error inside layers, linework retained 95.7 %, 0 unassigned px. Warnings: SAM 3 found no `upper_clothing`; extra arm and leg instances dropped; sleeves ignored |
| live readiness | (part of analyze) | 0 READY, 3 REQUIRES_RECONSTRUCTION (brows, eye direction, head tilt), 5 REQUIRES_NEW_ARTWORK (blink, mouth, lip sync, head turn, expressions). Same as the other characters: one neutral front view |
| Phase 2 reconstruction | `autorig prepare` | package valid; originals exact in the neutral pose; 23 359 generated px, all pending review; 52 review flags |
| natural posing | `autorig pose-refine` | 12 presets; neutral bit-exact. 6 OK_PENDING_REVIEW, 2 NEEDS_REVIEW (one-hand shrug, both hands raised), 4 NEEDS_ARTWORK (pointing, arms crossed, running, sitting). Squat and weight-shift foot drift 0.0 px |
| Phase 3 export | `autorig export-assets` | 9 gates PASS (source preserved, full-canvas layers, alignment, assets, 19-bone hierarchy, joints, bindings, no neutral gaps, originals hash-verified). 3 NEEDS_REVIEW: open defects, facial artwork, 19 approvals. `production_ready: false` |
| garment rig | `autorig garment-evidence` | 12 tests in 88 s. Verdicts in line with the other characters: 22 NEEDS_ARTWORK, 27 FAIL, 13 WARNING, 22 PASS (schoolboy: 16 FAIL; tough student: 29 FAIL). The FAILs are joint tears in lowered-arm poses awaiting reviewed corrective art |
| correctives | `autorig corrective-evidence --quick` | 9 assets generated (shoe tops, elbow and knee caps, armpit gussets, edge decontamination), all pending; quick poses WARNING; 72 s |
| motion | `autorig motion` | every fluid cycle passes all its checks (running 9/9, walk 9/9, moving variants 8/8). The plain comparison cycles 8/9 and 7/9 (as on the other characters); 323 s |
| Phase 4.0 proof | `tools/phase4_cloud_check.py` (on this package) | proof package built; split fidelity 0 diff; mock items MOCK_ONLY, save NOT_EXECUTED |
| Phase 4.1 full body | `tools/phase4_1_cloud_check.py` | 25 bones from this character's skeleton; every gate MOCK_ONLY / NOT_EXECUTED |
| Phase 4.2 native fills | `autorig native-fills` | 6 assets × 4 keys (shoulders lowered and raised, hips), pending |
| Phase 4.2 joint audit | `autorig deform-audit` | causes and counterfactual below (§2) |
| Phase 4.2 hybrid rig | `tools/phase4_2_cloud_check.py` | build and verify scripts run in the strict mock; A, B, C, D, G, H MOCK_ONLY; E MEASURED; F, I, J, K NOT_EXECUTED; verdict BLOCKED — NATIVE VALIDATION REQUIRED |

## 2. Joint audit (SIMULATION, `native_checks/audit/`)

| variant | holes | sweep | cracks |
|---|---|---|---|
| rigid 4.1 | 3 937 | 8 382 | 537 |
| hybrid (split + order) | 3 475 | 51 | 476 |
| + pending correctives | 2 058 (waist 1 818) | 51 | 286 |

- **Shoulders.** The cut is 60.4 / 54.1 px behind the pivot. The counterfactual rejects bone placement again: 18 149 vs 1 780 and 22 495 vs 3 392 defect px. Cause: LAYER_CUT.
- **Right elbow.** New on this drawing: the forearm layer starts 30.7 px behind the elbow pivot (half thickness 26.5), and the counterfactual says LAYER_CUT (10 070 vs 4 141). With the fix in §3.2: holes 444 → 126, and 91 with the pending cap.
- **Hips and knees.** Fixed by the pending fills and caps (in-plane tests: 0 / 9 / 0 / 3 px left). Real knee flexion: REQUIRES_NEW_VIEW.
- **Waist.** MISSING_ARTWORK, as for the other characters.

Artwork requests: 8 PENDING_REVIEW, 2 REQUIRES_NEW_VIEW (knees), 1 MISSING (waist).

## 3. Defects found and fixed

### 3.1 Floating outline pieces (Phase 1 partition)

On this drawing SAM 3 returned no `upper_clothing` mask, so outline pixels outside every mask went to the nearest bone:
- the outer edges of both ears and the blazer's side edges went to the upper arms;
- hair outline bits went to the forearms;
- a thigh sliver went down each shin.

When the arms moved, those pieces floated in the air
([before/after](schoolboy_v2_test/fragment_fix_before_after.jpg)).

- **Fix:** `phase1/partition.reassign_fragments`. A detached piece of a bone-assigned part, smaller than 10 % of the part's main component, moves whole to the part it touches. Pieces touching nothing stay. Pixels only change layer, so the partition stays exact.
- **Recorded:** `anatomy.json` `reassigned_fragments`, one record per part pair. Here 338 pieces, 1 708 px.
- **Effect:**
  - every limb layer is one connected piece;
  - natural-pose defects drop from 18 to 4 over the 12 presets;
  - OK_PENDING_REVIEW goes from 0 to 6.
- **Tests:** `tests/test_p1_fragments.py`. The Phase 1 and new-character tests pass.
- **Other characters.** The committed schoolboy and tough-student projects were **not** regenerated. Re-analysing them into a scratch folder (cached, $0) shows the rule would move 181 pieces (597 px) and 210 pieces (490 px). Their packages change only if you re-run analyze → prepare → evidence.

### 3.2 Elbow cut behind the joint (Phase 4.2 hybrid split)

The 4.2 split handled the shoulders and hips only. A blanket split of forearms and shins helped this drawing (right elbow 444 → 126 px) but hurt the committed schoolboy (left elbow 150 → 546 px), whose cut is at the joint.

- **Fix:** `hybrid.apply_split` splits a forearm or shin only when its measured cut lies more than 0.6 half-thicknesses behind the pivot. It is the audit's own LAYER_CUT criterion ("diagnose before changing the rig").
- **Effect:** only this drawing's right forearm is split. The schoolboy and tough-student hybrid rigs are unchanged, so the committed Phase 4.2 audits and cloud checks stay valid.
- **Tests:** in `tests/test_p42_hybrid.py`.

## 4. Remaining issues (not fixed here)

| issue | status |
|---|---|
| hands | no hand library for this drawing. Resting hands show the original palm-forward drawing (the Phase 3.8.1 rule reports NEEDS_ARTWORK). The committed sleeve-free schoolboy pack could be imported and checked (`hand-import`, `hand-audit`) |
| `upper_clothing` not segmented | the torso/arm boundary comes from the bones, not from the garment. The result is fine here, but less semantic |
| raised-arm armpit sliver | a thin outline strip under a raised arm, connected to the arm layer. A mask edit fixes it |
| elbow test poses | the arms rest about 15° raised, so a 90° elbow bend brings the hand beside the head, partly behind the hair layer (reported as occlusion; a draw-order choice for a person) |
| waist lean, knee flexion, facial animation | need drawings or another view, as for the other characters |
| Moho | not run: every native gate is MOCK_ONLY or NOT_EXECUTED. On the Mac: `tools/macos/phase4_native_test.sh --phase 4.2 --package projects/schoolboy_v2_test/character_package --custom-content "<Moho Custom Content Folder>"` |

## 5. Regression

Full suite after both fixes: **715 passed, 2 skipped**, 0 failed. It was run with one fresh process per test file;
one process for everything exceeds the container's memory (see the Phase 4.2 report). The 2 skips are the native
Phase 0 hooks.
- 712 before this test run, plus 2 fragment tests and 3 mid-limb split tests, gives 717 = 715 + 2.

## 6. Where to look

| image | path |
|---|---|
| segmentation and face | `projects/schoolboy_v2_test/character_project/previews/segmentation.png`, `facial_segmentation.png` |
| natural poses | `projects/schoolboy_v2_test/character_package/natural_pose/contact_sheet.png` |
| garment rig | `projects/schoolboy_v2_test/character_package/natural_pose/garments/contact_sheet_1.jpg` |
| run and walk cycles | `projects/schoolboy_v2_test/character_package/natural_pose/motion/` |
| joint close-ups (4.1 → 4.2) | `projects/schoolboy_v2_test/native_checks/audit/SIMULATION_joint_closeups.jpg` |
| hybrid rig with planned fills | `projects/schoolboy_v2_test/native_checks/phase4_2/schoolboy_v2_test/SIMULATION_review_planned_sheet.jpg` |
| editor | `autorig editor projects/schoolboy_v2_test/character_package` |
