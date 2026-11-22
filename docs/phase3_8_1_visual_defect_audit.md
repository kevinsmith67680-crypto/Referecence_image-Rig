# Phase 3.8.1: visual defect audit

Scope: the visual and rigging defects in the Phase 3.8 renders of the new character (`tough_student_test`), traced to
their causes. Each cause was checked on the original schoolboy as well. The fixes and their before/after evidence
are in [`phase3_8_1_fix_report.md`](phase3_8_1_fix_report.md).

**Regression reference.** The request refers to an attached screenshot comparing the character before and after
reopening. No attachment reached this environment (`/mnt/user-data/uploads` is empty), so the committed
`docs/phase3_8/new_character_evidence/19_project_reload.png` was used: the same before/after-reopening comparison,
right arm pointing, left arm lowered.

**Inputs.** The original PNGs are still unavailable. Every render uses the chat's JPEG re-encodes stored in
`references/tough_student/` (Phase 3.8 §3); none was substituted silently.

## 1. The lowered arm's "extended-finger" hand

### The chain, traced for the lowered left arm of `pointing_speaking` (review mode)

| step | finding |
|---|---|
| requested gesture | `relaxed_standing` for the left hand (preset action map) |
| selected switch | `hand_l` auto → `relaxed`, source `auto`, `requested == active` |
| sprite | `store:tough_student_hands_v1/left_relaxed` = sheet cell r1c1 |
| handedness (Phase 3.7 inspection) | left, confidence *medium*. The record already said: "no palm creases are drawn, so the drawing could also read as the back of a right hand" |
| gesture metadata | relaxed, fingers down, tolerance 50° |
| palm orientation | recorded PALM_TO_CAMERA, normal (0, 0, +1) |
| wrist rotation | 9–14° from the drawn orientation, within the tolerance |
| wrist pivot | open cut, confidence 1.0; wrist check PASS |
| approval | PENDING_REVIEW (previewed, labelled) |
| rendered | the r1c1 drawing on the left arm: flat hand, straight fingers, **thumb on the outside** (image right) |

### What was wrong

Every step did what its data said. The defect is in the data and in a missing rule.

1. **Classification.** r1c1 and r3c1 carry no palm-side cue, while every palm view on the same sheet draws palm
   creases (the open palms, the cupped hands). Their thumb is a short digit at the hand's edge, partly behind the
   outline, which is how a back view reads. Read as palms, they sit on a hanging arm with the palm forward and the
   thumb pointing away from the body: a fully supinated forearm.
   - The artist's own arms-down front view (`references/schoolboy/character_sheet.jpg`, same style) shows hanging
     hands from the **back**, with the thumbs toward the body.
   - Read as backs, r1c1 is a right hand and r3c1 a left hand.
2. **No anatomical rule.** `relaxed_standing` accepted PALM_TO_CAMERA, BACK_TO_CAMERA and EDGE_ON alike. A
   palm-forward hand on a hanging arm is anatomically possible, but it is a held pose and never reads as a resting
   hand.
3. **Production showed the same fault.** Nothing is approved, so production falls back to the original T-pose
   hand: an open palm, thumb up in the T-pose, which ends up palm forward with the thumb outward on a hanging arm.
   - That is the "extended-finger" look in production.
   - It was not reported as a problem, because the new character's inspection record had **no `original` entry**:
     with no metadata, no palm rule could check the original hand.

It is not a selection-engine bug (it chose by its data), not a rotation bug, and not a pivot bug.

### Requested vs rendered switches

The engine resolves switches correctly: a note is recorded whenever `requested != active`. The Phase 3.8 evidence
harness, however, rendered the reload test **without the project's saved switch state**. The saved `hand_r = fist`
choice was persisted, but the image showed the preset's pointing hand. That is a requested/rendered mismatch in
the evidence, not in the editor: the editor passes the saved switches. Nothing compared the two before this phase.

The same image also showed a stray dark mark beside the pointing fingertip. The reload test's scratch copy had
skipped `natural_pose/corrective/` (the Phase 2 hand-variant manifest), so the forearm's hand-outline pixels were
drawn. This was fixed in Phase 3.8's final commit and is listed here for completeness.

## 2. Shoulders and armpits

Sweeps of both arms at 0, 30, 60, 90 and 120° from hanging (`docs/phase3_8_1/shoulder_sweep_before_after.png`,
"before" rows) show four distinct defects.

| defect | where | cause (traced) |
|---|---|---|
| a dark bar sticking up above the shoulder | 0–60° on both arms | **Phase 2 generated pixels.** The torso under each upper arm was reconstructed with a deterministic flat fill that continued the visible shoulder line, outline included, straight out under the sleeve: a run of 2,865 / 2,992 generated px whose top rows are outline-dark. When the arm comes down the sleeve rotates away and the run is exposed as a horizontal bar with a square end. Isolated by rendering the torso layer alone: the bar is the torso's own pixels (y 396–408, x 631–661 on the left) |
| a seam that opens between torso and sleeve on the shoulder | 0–60°, small | **Layer-dependent weights.** The same garment part, `blazer_shoulder_l`, had clavicle weight 0.51 in the torso layer next to 0.99 in the upper-arm layer, across the Phase 1 cut. The smoothing that blends neighbouring torso parts ran on each layer's own grid, so the result depended on which layer held a pixel. When the clavicle drops as the arms come down, the two layers move apart |
| square, padded shoulder silhouette | arms down | **the T-pose drawing**: its shoulder line is horizontal and meets the sleeve's top edge, which becomes the outer side of a hanging arm. Rounding the corner would hide original pixels, so it is missing artwork (an arms-down shoulder), not a rig bug |
| dashed light line along the armpit | 120°, both arms | **seam of the pending gusset.** The sleeve's anti-aliased underside (alpha 0.53–0.91) counted as figure (alpha > 0.5), so the gusset stopped beside it and the paper showed through the partly transparent edge: 55 such pixels |

Checked and not at fault:
- shoulder pivots: garment-aligned, at the armhole top, IK lengths constant;
- draw order: unchanged and correct;
- occlusion masks;
- mesh stretching: the joint patch's stretch limit refuses the gap, and the gusset fills it beneath.

The schoolboy shows the same mechanisms: a light crack at the shoulder seam and ragged bumps on the left shoulder
at 0°, and the dashed armpit line at 120°.

## 3. Knee limit violations (acting test)

Phase 3.8 diagnosed the cause in its generalisation report §3.7. Re-measured here:
- 248 of 308 frames at 48 Hz had `knee_l_flexion` outside the 10° stance limit; the maximum was 17.9°.
- In most of them the free leg's soft-knee minimum and the loaded leg's limit could not both hold, and the solver
  favoured the soft knee.
- In the rest no pelvis height was possible at all. The balance shifted the pelvis about 94 px over the loaded
  foot, and from there even a straight free leg could not reach its planted foot unless the loaded knee bent past
  10°.

## 4. Artwork compatibility and artefacts

| item | measured | cause |
|---|---|---|
| hand skin tone | library 253/193/148 vs the character's 253/166/97 (RGB distance about 58) | a different palette (Phase 3.8); skin-matched candidates exist |
| narrow wrists | 7 drawings at 0.709–0.745× the sleeve opening (minimum 0.75) | one scale per side, matched on the open palm's area |
| marks beside the trainers | 2 detached components (97 + 35 px) in relaxed standing, 1 in pointing | trouser-hem outline pixels resting on the shoe belong to the shin layer and separate from it |
| dotted arc over the original palm | production renders whenever the original hand shows | the outline of Phase 2's generated forearm extension under the hand (a flat fill of 1,040 / 1,255 px that continued the cuff); removing those generated pixels removes the arc (`docs/phase3_8_1/artwork_candidates.png`) |
| eye whites under the irises | 94 % / 99.7 % of the generated pixels dark (median about RGB 16/10/8) | FLUX inpainted the sclera and redrew iris and pupil |
| chain labelled "tie", shirt labelled "sweater" | — | the garment slots are named after the schoolboy's uniform; no measured semantics |
