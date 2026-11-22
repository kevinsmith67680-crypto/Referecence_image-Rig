# Phase 3.8 rigging quality report: the new character

Every image here was rendered by AutoRig's own solver, garment rig and compositor, on the `tough_student_test`
package. The images are in `docs/phase3_8/new_character_evidence/` and the numbers in its `results.json`.

Technical checks and artistic judgement are kept apart. The measurements are geometric hints. The visual notes are
the assistant's reading of the renders, **not an artistic approval**; no person has reviewed any of this.

## 1. Skeleton and pivots (step 6) — `05_skeleton_overlay.png`

- **Automatic:** every Phase 1 joint was placed without manual landmarks. The rig has 19 bones, a single root, no
  zero-length bones (export gate).
- **Shoulders:** Phase 1 placed the shoulder joints at (409, 455) and (679, 457), at the arm's proximal end. The
  garment rig lifts them 30.3 and 29.2 px toward the top of the armhole, to (407, 425) and (681, 428). These are not
  at the sleeve centre; they are at the top of the shoulder where a lowered sleeve hangs from. The anatomical axis is
  kept: `axis_offset` 13.0° and −12.7°, the same as the schoolboy (13.1° and −12.8°).
- **Arm segments:** upper arm 134.7 / 132.9 px, forearm 122.3 / 120.3 px, hand 66.6 / 67.2 px. Elbows sit
  mid-sleeve, which Phase 1 flags "verify": a straight arm gives no bend to locate. Wrists sit at the cuffs.
- **Legs:** hips at the thigh tops (481, 771) / (607, 777), under the blazer hem. Knees mid-leg, ankles at the
  trainer tops. Leg stance 15.1° and 14.6° from vertical.
- **Plausibility:** plausible for a front view. The elbow and knee pivots are estimates and should be checked in the
  Joints tool. The e2e test moved a pivot there and it persisted.

## 2. Lowering the T-pose arms (step 7) — `06`–`09`, `09b`

The legs are kept at the drawn stance so that only the arms and shoulders change. Hands are the original open palms.
Each case was rendered twice: originals only (production) and with the pending candidate corrections (review).

| case | abduction r/l (°) | arm length r/l (px) | gaps / seams / outline breaks | shoulder slope r/l (°) | shoulder hump r/l (px) | failing garment parts |
|---|---|---|---|---|---|---|
| A T-pose (rest) | 86.1 / 86.3 | 257.0 / 253.2 | 1 / 0 / 1 | 8.6 / 7.9 | 0.5 / 0.2 | none |
| B lowered 30° | 56.1 / 56.3 | 257.0 / 253.2 | 2 / 3 / 3 | 34.0 / 34.4 | 22.3 / 20.2 (arm forms the shoulder; check not applicable above 40°) | none |
| C lowered 60° | 26.1 / 26.3 | 257.0 / 253.2 | 3 / 3 / 4 | 16.1 / 18.5 | **15.7 / 20.8** | blazer_shoulder_l, trouser_shin_r |
| D resting | 8 / 8 | 257.0 / 253.2 | 3 / 3 / 4 | 15.6 / 16.7 | 8.3 / 10.4 | blazer_shoulder_r/l (local deviation 3.5 px), trouser_shin_r |
| E right down, left out | 8 / 86.3 | 257.0 / 253.2 | 16 / 0 / 16 | 15.3 / 7.9 | 8.5 / 0.2 | trouser_cuff_r NEEDS_ARTWORK, shoe_r |

With the pending corrections the numbers change by less than 1 px. None of these poses needs a gusset; the armpit
correctives act above 90°.

**Requirements:**

| requirement | result |
|---|---|
| arm length maintained | **yes**, constant to 0.1 px. No bone is ever scaled. |
| torso proportions, crest and trim | unchanged (rigid badge; lapels LIMITED, WARNING ≤ 1.4 px) |
| cuffs attached | yes |
| layer order | correct (forearm over hand, arm over blazer side) |
| no sleeve cutting across the chest | yes |
| major shoulder gaps | 0–3 gap points |
| blazer shoulder shape | **not convincing** at rest or in transit; see the visual reading below |

**Visual reading (assistant, not an approval):**
- The arms lower smoothly, and the sleeves hang from the shoulder point, not from the sleeve centre.
- At rest, the shoulders read **square and slightly padded**. The measured line slopes only 16–17°, while a relaxed
  arms-down shoulder in this style slopes about 30° (the schoolboy sheet's front view: 31°/47°). Between 30° and 60°
  of lowering, the rotated T-pose sleeve cap bulges 16–21 px above the shoulder chord, which reads as an inflated
  pad.
- This is the same limit Phase 3.7 found on the schoolboy. It comes from rotating a drawing whose shoulder seam is
  horizontal. Geometric stretching is not used to hide it.

**Missing artwork (not invented):**
- an **arms-down shoulder and sleeve-cap drawing** per side, with the shoulder seam sloping down;
- optionally a mid-abduction (≈ 45°) cap.

The existing candidate corrections (armpit gussets v2, fold strokes) do not address the shoulder top.

## 3. Natural poses (step 14) — `17_pose_contact_sheet.png`

Each pose is classified by the Phase 3.5 `ArtworkFeasibilityEngine` (or the motion feasibility for hands on hips) on
both a review render and a production render, then mapped to this phase's vocabulary.

| | pose | status | why |
|---|---|---|---|
| A | T-pose | SUPPORTED | original pixels, bit-exact |
| B | relaxed standing | NEEDS_APPROVAL | renders SUPPORTED_WITH_CORRECTIONS; its relaxed hands are pending (production shows the original palms) |
| C | weight-shifted standing | NEEDS_APPROVAL | as B |
| D | right pointing | NEEDS_APPROVAL | pointing drawing pending (production: original hand) |
| E | left pointing | NEEDS_APPROVAL | the left pointing drawing is a pending mirrored copy |
| F | subtle shrug | SUPPORTED | original open palms; shoulders rise 3.5/1.5 px, the line does not rise outward |
| G | expressive shrug | REQUIRES_ALTERNATIVE_ARTWORK | no palm-up open hand; review shows the pending cupped drawings; production shows the subtle shrug and reports NEEDS_ARTWORK |
| H | hands on hips | NEEDS_APPROVAL | production SUPPORTED with the original hands; its preferred relaxed drawing is pending |
| I | arm raised | NEEDS_APPROVAL | renders with corrections; left relaxed hand pending |
| J | elbow bent (deep) | REQUIRES_ALTERNATIVE_ARTWORK | `corrective:elbow_deep_r` (sleeve fold above 125°) |
| K | walking | REQUIRES_ALTERNATIVE_ARTWORK | stride needs a side or 3/4 view (REQUIRES_NEW_VIEW); only in-place motion is rendered |
| L | running | REQUIRES_ALTERNATIVE_ARTWORK | knee drive toward the viewer and a side view |
| M | sitting | REQUIRES_ALTERNATIVE_ARTWORK | foreshortened thighs, seat, trousers seated |
| N | squatting | NEEDS_APPROVAL | renders with corrections (knee folds pending); palm-down hands pending |
| O | forward leaning | REQUIRES_ALTERNATIVE_ARTWORK | torso lean forward is depth motion (REQUIRES_NEW_VIEW) |

No pose was forced: walking, running, sitting and leaning are shown as labelled in-plane fallbacks.

**Visual reading:**
- Standing, pointing, shrug, arm raised and squat read as the intended actions.
- Hands on hips reads correctly.
- The front-view "sitting" looks like a wide squat. That is why it is REQUIRES_ALTERNATIVE_ARTWORK.
- Small dark marks beside the inner trainers appear in most poses where the stance narrows (§6).

## 4. Garments (step 15) — `16_garment_corrections.png`, `natural_pose/garments/`

The structured garment rig ran unchanged on the new uniform: 41 parts and valid meshes. Per-garment status in the 12
Phase 3.3 garment tests, compared with the schoolboy:

| garment | schoolboy (12 tests) | new character (12 tests) |
|---|---|---|
| trousers | NEEDS_ARTWORK in all 12 | NEEDS_ARTWORK in all 12 |
| blazer | FAIL / NEEDS_ARTWORK | FAIL / WARNING / NEEDS_ARTWORK |
| shirt | FAIL / WARNING / NEEDS_ARTWORK | FAIL / NEEDS_ARTWORK |
| shoes | WARNING | **FAIL** in 8 (trouser-cuff marks counted as shoe gaps), WARNING in 4 |
| body (hands) | WARNING / FAIL | FAIL / WARNING |
| tie slot (schoolboy: tie; new: **gold chain**) | PASS | PASS (residual 0 px: rigid with the chest) |

**Chest details while the arms move** (lapels, chain, crest, collar), from the garment evaluator:

| pose | lapels | chain (tie slot) | crest (badge) | collar |
|---|---|---|---|---|
| relaxed standing | WARNING (deviation 1.3 px) | PASS (0 px) | PASS (0 px) | WARNING (1.4 px) |
| arm raised | NEEDS_ARTWORK (3.4 px) | PASS | PASS | NEEDS_ARTWORK (4.1 px) |
| deep elbow | WARNING (1.3 px) | PASS | PASS | WARNING (1.0 px) |
| squat | WARNING | PASS | WARNING | WARNING |

The arms do not drag the lapels or the chain in relaxed poses: deviations stay at or below 1.4 px, and the chain and
crest are rigid on the chest. Raising an arm deforms the lapel and collar by 3–4 px, which the evaluator marks
NEEDS_ARTWORK (the raised-shoulder collar drawing).

**Limitations:**
- Garment vocabulary and palette: chain → "tie", shirt → "sweater", no pocket flaps, light trim read as white
  (generalisation report §3).
- Trouser cuffs over the white trainers (§6).

## 5. Corrective artwork (step 16) — `16_garment_corrections.png`, `natural_pose/correctives/`

The corrective planner generated assets from the new character's own pixels. All are GEOMETRIC_CORRECTION and
**PENDING REVIEW**:
- shoe tops r/l (**v3** with the light-shoe fix decided per shoe; v1 was dark grey, v2 kept dark streaks);
- elbow caps r/l, knee caps r/l;
- armpit gussets r/l (v1, plus v2 from the Phase 3.5 shoulder controller);
- elbow folds r/l, knee folds r/l;
- edge decontamination.

Effect, review preview, exposed gap points off → on:

| pose | off | on |
|---|---|---|
| arm raised | 59 | 21 |
| squat | 82 | 40 |
| relaxed standing | 30 | 27 |
| deep elbow | 29 | 28 |

`protected_violation_px` is 0 in every Phase 3.4 pose: no corrective covers an original pixel.

**Artefacts a reviewer will see:**
- a thin light diagonal line in the armpit with the pending gusset in the raised-arm preview;
- fold strokes at the deep elbow that do not hide the missing fold drawing; the filled notch also keeps a stray
  dark dot;
- a ragged, speckled outer outline along the squat knee fold (left close-up of the squat row), where the fold
  meets the trouser silhouette.

Still missing: deep elbow, raised-shoulder collar, seated trousers, torso lean, knee-drive drawings.

## 6. Defects found and their causes

| defect | where | cause | status |
|---|---|---|---|
| white rounded cuff end over every library hand | first hand renders | Phase 2 flat fill under the original hand, kept by the stored-hand cuff cover | **fixed** (`cuff_cover`) |
| dark shoe-top extension under white trainers | shoe_top v1 | shoe colour sampled only from dark pixels | **fixed** (v3 pending) |
| crest attached to the spine as a "button" | garment structure | badge rule needed a solid gold area | **fixed** (outline-emblem rule) |
| square, padded shoulders when the arms are down; cap bulge 16–21 px in transit | §2 | rotating a horizontal T-pose shoulder seam | NEEDS_ARTWORK (arms-down shoulder drawing) |
| small dark marks beside the inner trainers | relaxed stance, most poses | trouser-hem outline pixels owned by the shin layer separate when the ankle angle changes | segmentation; fix with a mask edit; reported as shoe FAIL |
| dotted arc over the original palm when the wrist bends | production pointing | hand-outline pixels in the forearm layer (Phase 1 split) drawn over the hand | segmentation; fix with a mask edit; library hands unaffected |
| eye whites under the irises are dark | Phase 2 FLUX | generic inpainting prompt | pending: reject or flat-fill |
| library hands paler than the character's skin; wrists 0.71–0.74× the sleeve opening | hands | different palette; per-side area match | candidates (skin-matched, not imported); per-variant scale correction available |

## 7. Animation (step 17) — `18_animation_preview.gif`

See the main report §16 for the acting sequence: T-pose → lower both arms → shift weight → raise the right arm →
point → hold → shrug → relax. It includes the per-segment production / review status and the motion-quality checks.

The motion is continuous: 0 velocity jumps, 0 solver events, and planted feet move 0.01 px. The verdict is **REVIEW**
because the loaded left knee bends 10.8–18.5° against the 10° standing limit. This is visible as an inward knee in
the weight-shift frames. The standard Phase 3.6 acting test gives the same result on this character (schoolboy:
PASS). The cause is the solver's pelvis-height priority when the free leg's soft knee and the loaded leg's limit
cannot both hold (generalisation report §3.7).

## 8. Summary

Technically, the rig generalises:
- the skeleton is automatic and IK keeps the bone lengths;
- arm lowering is continuous;
- chain and crest are rigid on the chest;
- correctives are generated from the new character's pixels;
- every pose is classified honestly.

Artistically, four things stop it from being convincing yet:
- the shoulders with the arms down;
- the trouser-cuff marks;
- the unreviewed hand palette;
- the bent loaded knee during weight shifts (a solver priority, not artwork).

None of these is hidden by stretching. Each is named as missing artwork, a mask edit or an open solver issue.
