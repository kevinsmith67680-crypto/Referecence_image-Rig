# Phase 3.8.1: critical visual rigging defect remediation — fix report

**Verdict: PARTIAL** (§9). The defects with a code cause are fixed generally, on both characters:
- hand classification and the resting-hand rule;
- requested vs rendered hand switches;
- layer-independent garment weights;
- the armpit seam;
- the knee-limit constraint handling;
- garment semantics.

The rest are artwork or review decisions. Each now has a reviewable candidate and stays pending:
- the arms-down shoulder;
- the generated shoulder run;
- the trouser-hem marks;
- the forearm extension under the original hand;
- the eye whites;
- the hand skin tone and wrist scale.

Nothing has been approved, by code or by a person. Moho was not run.

Audit of causes: [`phase3_8_1_visual_defect_audit.md`](phase3_8_1_visual_defect_audit.md). Moho:
[`phase3_8_1_moho_readiness.md`](phase3_8_1_moho_readiness.md).

Evidence is in `docs/phase3_8_1/`:
- `hands_before_after.png`, `shoulder_sweep_before_after.png`, `armpit_before_after.png`, `knee_before_after.png`,
  `full_character_before_after.png`, `acting_after_contact_sheet.png`, `acting_after.gif`, `artwork_candidates.png`;
- `renders/{before,after}_records.json`, `artwork_candidates.json`.

"Before" images were rendered by the same function from commit 8d31693's code and data (a git worktree). "After"
images come from this commit. Both are AutoRig renders, review previews unless marked production.

Inputs: the original PNGs are still unavailable. The JPEG re-encodes from Phase 3.8 were used, and this is stated
here rather than substituted silently.

## 1. Resting and pointing hands (Task 1)

| fix | where | general rule |
|---|---|---|
| relaxed pair re-inspected as the **back** of the opposite hand (r1c1 → right, r3c1 → left), medium confidence, with the evidence in each record and a `revisions` log keeping the previous reading | `references/{schoolboy/hand_pack_v2,tough_student/hand_grid}/semantic_inspection.json` (minimal diffs), then `autorig hand-audit` on both packages | inspected data, not code; a person can flip it back in the Hand Inspector (`handmeta.*`) |
| a hanging arm (forearm within 40° of vertical) rejects a palm-forward hand for `relaxed_standing` | `motion/handselect.py` (`HANGING_REST_PALMS`, `HANG_DEG`) | anatomical: a palm toward the camera there means a fully supinated forearm with the thumb pointing away |
| the new character's original hand gets inspection metadata (palm to camera, thumb up, as on the schoolboy) | `tough_student/hand_grid/semantic_inspection.json` `original` | without it no palm rule could check the original hand |
| `switch_consistency(result, eng, allow_pending)` compares requested → resolved → rendered pose, cid and layer, flags silent substitutions, pending drawings outside previews and wrong-handed drawings | `motion/handcheck.py` | used by the evidence and the tests |
| the reload evidence renders with the project's saved switches and records the consistency check | `newchar_evidence.section_reload` | the editor always did; the harness did not |

Result (both characters, `hands_before_after.png`):
- **review:** relaxed standing shows the back of each hand with the thumb toward the body, which matches the
  artist's own arms-down drawing. Before, the thumbs pointed outward.
- **pointing:** the pending pointing drawing, unchanged.
- **requested = rendered** on both hands in every case: 9 cases × 2 modes, plus a user `fist` choice on the right.
- **production:** nothing is approved, so the group default (the original hand) is shown with an explicit report
  instead of passing as a resting hand:
  - `relaxed: NEEDS_APPROVAL`;
  - `open_palm: NEEDS_ARTWORK` ("a palm toward the camera … is not a resting hand");
  - `partially_closed: NEEDS_ARTWORK`.
- **substitutions:** a requested pending drawing in production is substituted with a note, never silently.
- **left pointing:** a mirrored copy of the right drawing; this is still flagged.

Unchanged and still open:
- Production keeps showing the palm-forward original hand on hanging arms until a resting drawing is approved.
- The library has no partially-closed or curled resting hand.

## 2. Shoulders and armpits (Task 2)

| fix | where | before → after |
|---|---|---|
| the shoulder and sleeve-cap parts, which the Phase 1 torso/upper-arm cut runs through, take one smoothed field computed on a canvas grid from every layer's part labels. Their weights no longer depend on which layer holds a pixel. The other torso parts (blazer body, lapels, collar, opening) keep their Phase 3.3 weights exactly; a first version that smoothed every torso part on the canvas pushed the schoolboy's standing lapel stretch to 0.2504, past its FAIL threshold (`test_p34`), and was narrowed. `family_smoothing: "layer"` keeps the old behaviour for comparison | `garmentrig/weights.py` (`family_field`, `_smooth_family_canvas`, `SEAM_KINDS`) | clavicle weight across the left shoulder cut: torso 0.51 vs arm 0.99 → 0.62 / 0.75 / 0.84 / 0.86, monotone across the cut |
| the armpit gusset also backs the partly transparent edge pixels it borders | `garmentrig/correctives.armpit_fill` | unbacked seam pixels next to the gusset at 120°: 55 → 12 (the 12 are on the outer silhouette); the dashed light line is gone (`armpit_before_after.png`) |
| **candidate (pending):** `ShoulderExtensionRounding`, a torso variant in which only the Phase 2 generated shoulder run under each arm changes, ending in a round shoulder outlined with the measured outline colour and width | `garmentrig/shoulder.py`; stored as `alternative:shoulder_rounding@v1` (`artwork/alternatives/torso/`) | the dark bar above the lowered shoulders is gone in review previews at 0, 30 and 60° on both arms (`shoulder_sweep_before_after.png`); 0 original pixels changed, 949 generated px; the T-pose composite is identical (test) |
| **artwork request:** `art:arms_down_shoulder` (sloped shoulder line into a round sleeve cap, per side) | `artistic/alternatives.REQUEST_REGIONS` | the square shoulder itself is the T-pose drawing and stays NEEDS_ARTWORK |

No mesh stretching was added. The joint patch keeps its stretch limit, and every change is either a weight field,
a gap fill beneath the originals, or a pending drawing.

## 3. Knee limits (Task 3)

| fix | where |
|---|---|
| when the soft-knee minimum and the knee limit cannot both hold, the limit wins: the pelvis goes to the lowest height at which no planted knee passes its limit, never above the height at which every planted leg still reaches its ankle (reported as `knee_conflict`) | `motion/solver._pelvis_height` |
| the static balance step stops at the boundary where the planted legs can still hold the pose. Bisection keeps it continuous, the remaining offset stays in `balance_error_px`, and the solve is flagged `balance_limited_by_legs` | `motion/solver.solve`, `_leg_infeasible` |

The knee limit was **not** raised (still 10° standing). A per-character feasible weight range was tried and
reverted. It was calibrated on one base pose, so it reported 0.84 as feasible while 0.73 failed in real frames, and
it would have clamped the schoolboy's classroom weights.

| | before | after |
|---|---|---|
| tough student, acting test at 48 Hz: frames with a knee violation | 248 / 308 (max 17.9°) | **0** (max 10.48°, inside the check's 0.5° tolerance) |
| foot sliding / velocity jumps | 0.01 px / 0 | 0.01 px / 0 |
| acting verdict | REVIEW (knee) | REVIEW: 66 `balance_saturated` events. The scenario asks for weight 0.73, more than this stance can hold with both knees inside their limits, so the pelvis stops up to 7.7 px short of static balance. This is reported, not hidden |
| classroom / secondary motion | PASS / PASS | PASS / PASS |
| **schoolboy** acting / classroom / secondary | PASS / PASS / PASS | PASS / PASS / PASS, 0 events |

Schoolboy change, measured against commit 8d31693 with every joint compared frame by frame:

| | max joint displacement |
|---|---|
| acting (308 frames) | 0.022 px |
| classroom (299) | 0.014 px |
| secondary (193) | 0.014 px |
| 8 of 12 presets | ≤ 0.02 px |

Phase 3.8 estimated 0.26 px; that figure was pelvis height inside intermediate solver iterations, not the final
joints. Every tolerance is ≥ 25 times larger: 0.5 px foot slide, 1.0 px contact slide, 4 px continuity spike,
0.5° joint-limit tolerance. The change is below one rendered pixel and is not visible.

## 4. Artwork compatibility and semantics (Task 4)

Only the semantics are applied in code. Each artwork item is a candidate previewed on a scratch copy
(`artwork_candidates.png`); the package is never changed.

| item | candidate | measured effect | applying it |
|---|---|---|---|
| hand skin tone | the 16 skin-matched drawings from Phase 3.8 (on disk) | library 253/193/148 vs character 253/166/97 | import as new hand-store versions in the Hands tab; each version reviewed separately |
| narrow wrists | `handstore.placement` scale ops for the 7 drawings at 0.709–0.745×: scale 1.02–1.07 (to 0.76×), fixed per drawing | previewed | Hand Inspector |
| trouser-hem marks | `mask.edit` strokes moving the hem outline resting on each shoe (494 / 481 px) from the shin layer to the foot layer | detached marks 2 → 0 (relaxed), 1 → 0 (pointing), 2 → 0 (walk). **New, small:** a thin light gap opens at the hem of one shoe | Mask tool; a reviewer weighs the trade |
| dotted arc over the original palm | reject the generated forearm extension under the original hand (1,040 / 1,255 px beyond the cuff edge) | the arc disappears in production renders | Phase 2 review (`autorig review --reject`) |
| eye whites | the generated eye white refilled flat with the character's own visible sclera colour (about 252/247/242) | 94 % / 99.7 % dark → 0 % | Phase 2 review: reject the FLUX extension, import the fill |
| garment labels | measured semantics: the centre piece is a necklace when it is about as wide as tall or made of links (aspect < 1.5 or ≥ 3 enclosed gaps), else a tie; the opening is a shirt front when ≥ 40 % near-white, else a sweater | tough student: **necklace** (aspect 1.05, 8 gaps) and **shirt front** (0.60 white). Schoolboy: tie (2.2, 0) and sweater (0.0), unchanged | applied (labels, descriptions and garment groups; the rig slot is kept, so weights and rules are unchanged). The editor's Weights tab shows the label (rebuilt `editor/dist`) |

## 5. The new-character test, repeated (Task 5)

Ten renders, each in review and production mode, are recorded in `renders/after_records.json` (and the Phase 3.8
equivalents in `before_records.json`). Per render the records hold:
- the hand assets shown (pose, cid, source) and their approval state;
- requested vs active switches;
- the correctives drawn and the alternatives used;
- the solver violations, feasibility and missing artwork.

| # | case | hands (review) | approval | correctives (review) | violations | unresolved artwork |
|---|---|---|---|---|---|---|
| 1 | T-pose | original | original | none | none | — |
| 2 | relaxed standing | relaxed: r1c1 on the right, r3c1 on the left (backs, thumbs toward the body) | pending | shoe tops, edge decontamination; shoulder candidate (pending) | none | resting hand needs approval; production NEEDS_ARTWORK for a palm-forward resting hand |
| 3 | right pointing | right pointing; left relaxed | pending | + right armpit gusset | none | pointing needs approval |
| 4 | left pointing | left pointing (mirrored copy); right relaxed | pending | + left armpit gusset | none (Phase 3.8: 1 knee) | a drawn left pointing hand |
| 5 | both arms lowered | relaxed both | pending | shoe tops, decontamination; shoulder candidate | none | as case 2 |
| 6 | both arms raised | original | original | both gussets (pending) | none | gusset approval (production: NEEDS_ARTWORK `corrective:gusset`) |
| 7 | subtle shrug | relaxed both | pending | shoe tops, decontamination | none (Phase 3.8: 1) | expressive shrug still needs palm-up drawings |
| 8 | weight shift | right original, left relaxed | pending | + right gusset | none (Phase 3.8: knee 13–18°) | balance limited by the legs (reported) |
| 9 | elbow bend (110°) | right original | original | elbow fold (pending) | none | deep elbow above 125° still NEEDS_ARTWORK |
| 10 | short acting test | — | — | — | knee: 0 frames (was 248) | `acting_after.gif`, `acting_after_contact_sheet.png` (review preview) |

Every case: requested = rendered on both hands, no silent substitution, no pending drawing outside a review preview.

## 6. Tests and regression (Task 6)

New tests: `tests/test_p381_visualfix.py` (10), covering:
- the hanging resting-hand rule;
- the re-inspected relaxed hands and the production fallback report;
- requested = rendered in full renders, with wrist checks;
- weight continuity across the layer cut;
- the shoulder candidate (originals untouched, T-pose identical, fewer exposed dark generated pixels);
- the armpit seam;
- the knee limits with planted feet;
- the schoolboy acting unchanged (no violations, no balance events);
- the garment semantics on both characters;
- the candidates as proposals only.

Two existing tests changed. `tests/test_hand_replacement.py::test_rules_choose_natural_hands_and_forbid_the_wrong_gestures`:
- Its synthetic relaxed drawing was a palm-forward hand on a hanging arm, and it asserted that production chooses
  the palm-forward original hand as the resting hand.
- Both encoded the defect this phase fixes.
- The fixture now uses the back-of-hand reading. The production assertion became stricter: no drawing is chosen,
  the relaxed hand is reported NEEDS_APPROVAL, and the palm-forward hand is rejected with the hanging-arm reason.
- `tests/test_p37_hands.py::test_http_inspector_and_metadata` asserted exactly 2 reassigned drawings (the cupped
  pair). It now asserts the exact set of 4 (cupped and relaxed pairs), because the re-inspection reassigns the
  relaxed pair. The assertion is as strict as before.
- No other assertion was changed or removed.

| suite | Phase 3.8 | Phase 3.8.1 |
|---|---|---|
| backend `pytest` | 606 passed, 2 skipped (native) | **616 passed**, 2 skipped (native Moho: NOT EXECUTED), 0 failed, 19 min 17 s (606 + 10 new) |
| frontend `vitest` | 11 | 11 (editor rebuilt for the label) |
| schoolboy editor e2e | passed | SCHOOLBOY_E2E |
| new-character editor e2e | passed | **passed** (136 s, screenshots to scratch) |

The first full run after the changes had **3 failures**, each handled without weakening an assertion:
- `test_p34_correctives::test_standing_collar_is_not_a_failure_and_categories_are_recorded`: the schoolboy's standing
  lapel stretch reached 0.2504, past its FAIL threshold. This was a real regression from the first version of the
  weight fix, which smoothed every torso part on the canvas. It was narrowed to the shoulder parts, and the test
  passes unchanged.
- `test_p37_hands::test_http_inspector_and_metadata`: the reassigned set grew by the relaxed pair (see above).
- `test_p381_visualfix`: a bug in the new test itself (a renderer attribute that does not exist).

Schoolboy areas covered:
- IK and hand switching: `test_p32_ik_switch`, `test_hand_replacement`, `test_p37_hands`;
- garment deformation: `test_p33_garments`;
- correctives: `test_p34_correctives`;
- saving, reopening and editor approvals: `test_p3_editor` plus the editor e2e;
- animation playback: `test_p36_performance` plus the e2e playback step.

Save/reload on the new character (`docs/phase3_8/new_character_evidence/19_project_reload.png`, regenerated):
- identical state and render after reopening, with the saved `hand_r = fist` now shown;
- `switch_consistency` ok.

## 7. Moho (Task 7)

NOT EXECUTED: Moho cannot run here. The smallest native test is in
[`phase3_8_1_moho_readiness.md`](phase3_8_1_moho_readiness.md): bone layer, imported art, one bound arm, save,
reopen, move the arm, about 10 minutes on the Mac with the existing Phase 0 installer. Phase 4 was not started.

## 8. Acceptance gates

| # | gate | result |
|---|---|---|
| 1 | resting hands display the correct artwork | **PARTIAL.** Review: the correct reading (backs, thumbs toward the body). Production: no approved resting drawing exists, so the original palm-forward hand is shown as a **reported** fallback (NEEDS_APPROVAL / NEEDS_ARTWORK) |
| 2 | requested switch = rendered switch | PASS (all cases, both modes, plus a user choice) |
| 3 | wrist attachments stable | PASS (wrist checks on every store hand shown); 7 narrow wrists have pending scale candidates |
| 4 | pointing uses the correct hands | PASS in review (pending, labelled); production shows the original with NEEDS_APPROVAL until a person approves the drawing; left pointing is a mirrored copy |
| 5 | shoulder and armpit defects corrected or blocked by missing artwork | PASS. Fixed: weight seam, armpit line. Candidate (pending): generated shoulder run. Blocked by missing artwork: the square shoulder, `art:arms_down_shoulder` |
| 6 | knee constraints respected | PASS (0 violations, was 248 frames). The acting test reports the balance limited by the legs |
| 7 | character identity unchanged | PASS (no original pixel changed; T-pose composite identical) |
| 8 | schoolboy functionality preserved | PASS (tests below; ≤ 0.022 px joint change) |
| 9 | save/reload identical | PASS |
| 10 | rendered comparisons show improvement | PASS (`docs/phase3_8_1/*_before_after.png`) |
| 11 | pending artwork unapproved | PASS (nothing approved; every candidate pending) |
| 12 | all regression tests pass | FINAL_GATE |

Visual quality has **not** been judged by a person. The statements above are measurements and an assistant's
reading of the renders, not artistic approval.

## 9. Verdict

**PARTIAL.** Every defect with a code cause is fixed generally and verified on both characters. Gate 1 cannot be a
full PASS without a person approving a resting-hand drawing, or drawing a curled one. The square arms-down shoulder,
the hem gap trade-off, the eye whites and the skin tone wait for review or artwork. Specific blockers:

1. Approve or replace the relaxed hand drawings (now read as backs). No curled or partially-closed resting hand
   exists.
2. Draw `art:arms_down_shoulder`, or review the pending `alternative:shoulder_rounding@v1`.
3. Review the Phase 2 extensions: reject the eye-white FLUX fill and the forearm-under-hand fill (candidates ready).
4. Decide on the hem-mask candidate (it removes the marks but opens a small hem gap) and the wrist-scale and
   skin-tone candidates.
5. The acting scenario's weight shift (0.73) exceeds what this character's stance can hold within its knee limits.
   Lower it per character, or accept the reported balance offset.
6. The original PNG inputs are still missing; the JPEG re-encodes were used.
7. Phase 0 native verification on the Mac (`phase3_8_1_moho_readiness.md`) before any Phase 4 work.
