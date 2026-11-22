# Phase 3.7 hand-selection audit: the shrug regression, every hand drawing, and the fixes

The subject is `natural_pose/performance/acting/shrug_review_preview.png`, generated in Phase 3.6 and supplied by
the user as a screenshot. A copy is kept as `natural_pose/hand_selection/diagnosis/phase3_6_shrug_review_preview.png`.
Its right pane was labelled "REVIEW ONLY: palm-up shrug — cupped hands are pending". Every statement below comes
from re-rendering that frame and tracing the switch state (`autorig hand-selection-evidence`, `diagnosis.json`),
not from file names.

## 1. Diagnosis (step 1)

The checks the user asked for, in order:

| Question | Finding (measured) |
|---|---|
| Why were cupped hands selected? | **They were not shown at all.** The 3.6 performance resolver wrote `{"user": "cupped", "auto": True}`, and the switch controller honours `user` only when `auto` is False. The automatic slot won, filled by the selection engine for the *base preset's* action `relaxed_standing`, which chose the pending **relaxed** drawing. The label named the request, not the rendered drawing. The same bug silently replaced the 3.6 pointing hand (acting test, 20 of 156 side-frames) and the desk palm-down hand (classroom, 55 of 86 side-frames) with relaxed hands. |
| Does the variant match the gesture? | The rendered relaxed drawing is a hanging, camera-facing hand. The engine's action was `relaxed_standing`, never the shrug: the gesture intention never reached it. |
| Handedness? | Relaxed drawings: correct side. The cupped pair (had it been shown) is stored on the **wrong** sides (section 2). |
| Palm direction? | The relaxed drawing shows its palm (or back) to the camera. Turning it in the image plane cannot make it face up. |
| Sprite orientation? | The relaxed drawing is drawn hanging. In the review frame the hand bone was at 80° (forearm 64° + wrist −16°), so the drawing was turned ~80° from how it was drawn. A sideways relaxed hand with its palm to the camera reads as a presenting or "stop" hand, not a shrug. |
| Is the wrist rotation excessive? | The wrist bend itself (−16°) is within limits. What is excessive is the **drawing's** rotation from its drawn orientation: 80° against what the relaxed drawing tolerates (about 50°: relaxed fingers stop reading as hanging). |
| Camera orientation? | Front view. No palm-up drawing for a front camera exists in the library except the cupped pair. |
| Approved vs pending? | Everything shown was pending, and labelled. In review mode that was correct; in production nothing pending would have been shown. |
| Animator override? | The override existed (the "cupped" request) and was silently dropped. That is the bug. |
| Shoulder pose? | The shrug used clavicle parameters 13/15 on a T-pose shoulder line that is already level. Measured on the silhouette, the shoulder line rose outward (−6.3° / −4.3°) and the shoulder top rose 11 / 16 px above neutral: the "inflated pads" (section 5). |

## 2. Hand asset audit (step 2)

Every drawing was inspected at 2x zoom (`references/schoolboy/hand_pack_v2/semantic_inspection.json`; an assistant's
inspection, not a person's review) and measured (`autorig hand-audit`). File names were recorded as `label_pose` and
compared with what the drawing shows.

Handedness rule: with fingers f and palm normal n (sprite axes: x right, y down, z toward the viewer), a right hand has
its thumb along f × n. The resulting inventory is `analysis/hand_asset_metadata.json` (HandAssetMetadata v1, JSON
Schema `src/autorig/data/schemas/hand_asset_metadata.schema.json`), with a sheet at
`natural_pose/hand_selection/inventory/inventory_sheet.png`.

| asset | cell | label | shows | hand (confidence) | surface | palm normal | thumb | wrist opening | tolerance ° | rest rot ° | offered on | approval |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `left_cupped` | r2c4 | cupped | cupped | **right** (medium) | palm | [0, −0.8, 0.6] | top_far | closed_outline / right | 35 | **−182** | **r** (reassigned) | PENDING_REVIEW |
| `left_fist` | r1c3 | fist | fist | left (medium) | palm_side_curled | [0, 0, 1] | image_right | open_cut / top | 180 | −88 | l | PENDING_REVIEW |
| `left_gripping` | r2c2 | gripping | gripping | left (low) | edge | [1, 0, 0] | top | closed_outline / left | 100 | −2 | l | PENDING_REVIEW |
| `original:l` | reference | open_palm | open_palm | left (high) | palm | [0, 0, 1] | top | reference cuff | 180 | – | l | APPROVED (original pixels) |
| `left_open_palm` | r1c2 | open_palm | open_palm | left (high) | palm | [0, 0, 1] | image_right | open_cut / top | 180 | −90 | l | PENDING_REVIEW |
| `left_palm_down` | r2c3 | palm_down | palm_down | left (high) | back | [0, 0.74, −0.67] | image_left | open_cut / top | 60 | −91 | l | PENDING_REVIEW |
| `left_pointing` | r1c4 (mirror) | pointing | pointing | left (high) | palm_side_curled | [0, 0, 1] | top | closed_outline / left | 180 | −2 | l | PENDING_REVIEW |
| `left_relaxed` | r1c1 | relaxed | relaxed | left (medium) | palm | [0, 0, 1] | image_right | open_cut / top | 50 | −84 | l | PENDING_REVIEW |
| `left_thumbs_up` | r2c1 | thumbs_up | thumbs_up | left (high) | palm_side_curled | [0, 0, 1] | top | open_cut / left | 45 | −4 | l | PENDING_REVIEW |
| `right_cupped` | r4c4 | cupped | cupped | **left** (medium) | palm | [0, −0.8, 0.6] | top_far | closed_outline / left | 35 | **183** | **l** (reassigned) | PENDING_REVIEW |
| `right_fist` | r3c3 | fist | fist | right (medium) | palm_side_curled | [0, 0, 1] | image_left | open_cut / top | 180 | 90 | r | PENDING_REVIEW |
| `right_gripping` | r4c2 | gripping | gripping | right (low) | edge | [−1, 0, 0] | top | closed_outline / right | 100 | 3 | r | PENDING_REVIEW |
| `original:r` | reference | open_palm | open_palm | right (high) | palm | [0, 0, 1] | top | reference cuff | 180 | – | r | APPROVED (original pixels) |
| `right_open_palm` | r3c2 | open_palm | open_palm | right (high) | palm | [0, 0, 1] | image_left | open_cut / top | 180 | 91 | r | PENDING_REVIEW |
| `right_palm_down` | r4c3 | palm_down | palm_down | right (high) | back | [0, 0.74, −0.67] | image_right | open_cut / top | 60 | 93 | r | PENDING_REVIEW |
| `right_pointing` | r3c4 | pointing | pointing | right (high) | palm_side_curled | [0, 0, 1] | top | closed_outline / right | 180 | 3 | r | PENDING_REVIEW |
| `right_relaxed` | r3c1 | relaxed | relaxed | right (medium) | palm | [0, 0, 1] | image_left | open_cut / top | 50 | 89 | r | PENDING_REVIEW |
| `right_thumbs_up` | r4c1 | thumbs_up | thumbs_up | right (high) | palm_side_curled | [0, 0, 1] | top | open_cut / right | 45 | 3 | r | PENDING_REVIEW |

"rest rot" is how far the drawing is turned from how it was drawn when placed at rest on its *stored* side.

Findings:
- **The cupped pair is stored on the wrong sides.** Two independent cues agree:
  - the thumb is on the far side, so r2c4 is a right hand and r4c4 a left hand;
  - every other sideways drawing of a row group sits within 4° of its arm at rest, but the cupped drawings sit
    182-183° from it: they extend toward the body and would be shown upside down (palm down).

  The audit reassigns them: each is offered only on the side its handedness gives. The approval key does not change.
  This is a correction of an assistant inspection with medium confidence; a person can reverse it in the Hand
  Inspector (`handmeta.set handedness`).
- Every gesture label matches what the drawing shows. The labels still say nothing about palm direction or handedness,
  and that missing information is what caused the defect.
- Gripping drawings are profile views, so handedness cannot be verified from the silhouette (low confidence).
- Relaxed drawings have no palm creases. They are read as camera-facing palms (consistent with the open palm of the
  same row group), but could be the back of the other hand (medium confidence).
- Only the cupped pair can show a palm-up hand for a front camera. No open palm-up drawing exists.
- Nothing is approved. The only APPROVED hands are the original reference pixels.

## 3. HandOrientationAnalyzer (step 3)

- The palm normal is rotated by the flat placement rotation (rest placement plus hand-bone rotation plus aim). The z
  component never changes.
- Classes, by dominance at a threshold of 0.6:
  - PALM_TO_CAMERA / BACK_TO_CAMERA: the camera (z) component dominates;
  - PALM_UP: the up component dominates and the palm is not turned more than 0.3 away from the viewer;
  - PALM_DOWN: the down component dominates;
  - EDGE_ON: nothing dominates;
  - AMBIGUOUS: no or invalid metadata, or "palm up" with the palm hidden.
- `reachable_by_rotation`: a camera-facing drawing can never show PALM_UP or PALM_DOWN. The selection reports this as
  "no flat rotation of this drawing shows that (needs a different drawing)".
- `arm_allows`: on a front view, palm up/down needs the forearm at least 30° off vertical.
- Image-space finger direction is never used as the palm direction. A camera-facing hand turned 90° is still
  PALM_TO_CAMERA (tested).

## 4. Selection engine and approval (steps 4-6)

Hard constraints run first. A candidate failing any of them is rejected with the reason recorded, whatever its score:
1. missing;
2. approval: production uses approved and original only; rejected is never used;
3. excluded by the user;
4. gesture forbidden, or a target is needed;
5. handedness (metadata, else the extractor);
6. invalid metadata;
7. palm view in this pose (strict for palm-specific actions, metadata required);
8. rotation from the drawn orientation within the drawing's tolerance.

Survivors are scored on gesture, palm, anatomy (wrist-axis fit of the aim), attachment, silhouette, view, proportion,
continuity, fidelity and approval. Weights are in `WEIGHTS`; gesture stays dominant.

Every decision carries an explanation: the chosen drawing with its reason and mode, each rejected candidate with its
violated constraints, NEEDS_ARTWORK / NEEDS_APPROVAL entries, and the fallback. When nothing passes, the action's
`fallback` is used and reported. For example, `shrugging` falls back to `shrug_subtle`, with
"NEEDS_ARTWORK: palm_up; NEEDS_APPROVAL: cupped".

Explicit choices (user or keyframe) win over the automatic one, but are checked against the action-independent
constraints (`override_check`), and violations are reported.

Performance hands (fixing the Phase 3.6 bug):
- an *action* (`shrugging`, `shrug_subtle`, `hand_on_desk` ...) is resolved by the engine once per hand interval, on
  the interval's middle frame, and held, so random access equals playback (tested);
- a *pose* is an explicit choice (`auto: False`).

Wrist alignment (`motion/handcheck.py`) checks attached, centred, no duplicate cuff, rotation, proportion, and that
the arm is unchanged across variants (tested). Placement is never altered to fit an unsuitable drawing.

## 5. Shoulders (step 8)

Measured on the alpha silhouette (`performance/shoulders.py`), between the collar junction and the outer shoulder:

| frame | rise vs neutral r/l (px) | slope r/l (° , + = down outward) |
|---|---|---|
| character sheet front view (arms down, reference) | – | 31 / 47 |
| neutral standing (rig) | 0 / 0 | 9 / 11 |
| Phase 3.6 shrug_low (clavicles 13/15) | **11 / 16** | **−6 / −4** |
| Phase 3.7 SHRUG_SUBTLE (6/7) | 2 / 4 | 0 / 1 |
| Phase 3.7 SHRUG_EXPRESSIVE (7/8) | 2 / 6 | −1 / 1 |

Investigation:
- **Clavicle elevation** is the cause of the inflated pads. The fix is modest shrug elevation, with a check that flags
  a shoulder top more than 8 px above neutral or a line rising outward. The revision loop proposes the same correction
  automatically.
- **Upper-arm pivot, sleeve cap and weight mesh.** The square T-pose shoulder line persists in arms-down poses because
  the upper-arm joint patch rotates the T-pose sleeve cap about the aligned pivot. The neutral slope is 9-11°, against
  31-47° on the character sheet.
- **Deeper clavicle depression** (down to the −10° joint limit) raises the slope to 17-21°. It opens a hairline seam at
  the shoulder/lapel weight transition (lapels are chest-anchored since Phase 3.4), so it was not adopted. The
  41-part architecture and the rhythm are unchanged.
- **Gussets** are not involved (they only fill under the arm). No padding or volume was added.
- A truly sloped arms-down shoulder is listed as optional artwork (`art:shoulder_arms_down_{r,l}`).

## 6. What a person must still do

- Review and approve or reject the 16 sleeve-free drawings, especially the cupped pair's reassignment and its
  closed-outline wrist pivots.
- Draw an open palm-up hand for each side (SHRUG_EXPRESSIVE stays NEEDS_ARTWORK in production without it).
- Judge whether the subtle shrug reads naturally. No metric here claims it does.
