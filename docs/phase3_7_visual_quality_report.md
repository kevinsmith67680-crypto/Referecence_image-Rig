# Phase 3.7 visual quality report

Every image here was rendered by AutoRig's own solver and renderer on the real schoolboy package. Pending drawings
appear only in review mode, and they are labelled (`*` or "PENDING"). Nothing in this report is an artistic
approval; the measurements are geometric hints. Evidence folders, under `projects/schoolboy_tpose/character_package/`:
- `natural_pose/hand_selection/`: `diagnosis/`, `regression/`, `inventory/`, `continuity.json`,
  `invalid_metadata.json`, `results.json`, `benchmark.json`;
- `natural_pose/reference_motion/evidence/`;
- `natural_pose/performance/` (acting test re-rendered with the fixed hand selection).

## 1. The supplied screenshot and its fix

`hand_selection/diagnosis/shrug_comparison.png` shows one frame (acting test, shrug key + 0.3 s) six ways. The
supplied screenshot itself is copied next to it as `phase3_6_shrug_review_preview.png`.

| panel | hands rendered | shoulder rise vs neutral r/l | shoulder slope r/l |
|---|---|---|---|
| Phase 3.6 shrug_low (clavicles 13/15) | relaxed* / relaxed* | 11 / 16 px | −6° / −4° (rising outward) |
| Phase 3.6 review frame, reproduced | relaxed* turned ~80°, under a "cupped" label | 11 / 16 px | −5° / −3° |
| cupped drawings honoured as stored | cupped* upside down (palm down): wrong hand on each side | 11 / 16 px | −5° / −3° |
| **3.7 SHRUG_SUBTLE** | open palm (original), palms toward the camera | **2 / 4 px** | 0° / 1° |
| **3.7 SHRUG_EXPRESSIVE, review only** | cupped*, handedness-corrected, palm up | **2 / 6 px** | −1° / 1° |
| **3.7 SHRUG_EXPRESSIVE, production** | shown as SHRUG_SUBTLE (NEEDS_ARTWORK: no approved palm-up hand) | as subtle | as subtle |

Close-ups:
- `wrists_closeup.png`:
  - in the 3.6 panels the hands are turned sideways (relaxed) or upside down (cupped);
  - the expressive review panel has palm-up cupped hands attached at the cuff on the correct sides;
  - the subtle panel has the original open palms.
- `shoulders_closeup.png`: the 3.6 shoulder caps peak above the collar line. The 3.7 shoulders sit 7-12 px lower
  at the same frame, though the rounded T-pose cap remains (section 4).

Visual judgement: not claimed. The subtle shrug reads as "palms forward, shoulders slightly up". Whether that reads
as natural is for the user to judge.

## 2. Ten hand regression cases (step 18)

`hand_selection/regression/contact_sheet.png` shows each case in review mode (pending previewed, `*`) and in
production (approved and original only). Wrist close-ups are in `regression/wrists.png`, and the per-side
explanation, rejected candidates, wrist checks and shoulder metrics are in `cases.json`.

| case | review r / l | production r / l | notes |
|---|---|---|---|
| neutral | relaxed* / relaxed* | open palm / open palm | relaxed is NEEDS_APPROVAL in production |
| left point | relaxed* / pointing* | open palm / open palm | left pointing is a mirrored copy (flagged); production shows the original palm, and the pointing drawing is NEEDS_APPROVAL |
| right point | pointing* / relaxed* | open palm / open palm | |
| subtle shrug | open palm / open palm | open palm / open palm | original pixels both modes |
| expressive shrug | cupped* / cupped* | (substituted by subtle) | review: palm-up cupped on the corrected sides; production: NEEDS_ARTWORK palm_up |
| thumbs up | thumbs_up* / relaxed* | open palm / open palm | wrist check **centred FAIL** (thumbs-up drawing sits 20 px off the cuff centre on a 52 px cuff): needs an artist offset or review |
| gripping | gripping* / relaxed* | open palm / open palm | closed-outline wrist pivot (manual review flag) |
| open-hand conversation | open palm / relaxed* | open palm / open palm | |
| hand on desk | palm_down* / relaxed* | open palm / open palm | wrist check **centred FAIL** (34 px on a 52 px cuff): the back-of-hand drawing's mass is to one side; review |
| running | fist* / fist* | open palm / open palm | partially_closed NEEDS_ARTWORK |

Checks across all cases:
- **Side and palm:** every rendered drawing is on its own side, and no camera-facing palm is presented as palm-up.
- **Arm geometry:** never changed. A test confirms the upper arm, forearm and hand joints are identical across hand
  variants.
- **Approval:** in production every hand is the original (APPROVED); in review every pending drawing is labelled.
- **Cuffs:** no duplicated cuffs (own white pixels near the wrist ≤ 9).
- **Fingers:** no missing fingers in the rendered drawings (visual check of `wrists.png`; drawings are rigid on the
  hand bone).
- **Switching:** no sudden switches. Over the 78-frame acting test, both hands switch only at hand-interval
  boundaries (2.33, 3.25, 4.33, 5.5 s), with the shortest hold 0.92 s. Random access equals playback (test).

Invalid metadata and missing art (`invalid_metadata.json`), each a deliberate failure on the real library:
- wrong handedness: the drawing is rejected for handedness and the fallback is used;
- an invalid palm normal: rejected as metadata plus palm unknown; no crash, after a bug found and fixed in this run;
- a drawing excluded by the user: it becomes NEEDS_ARTWORK with a fallback;
- a camera-facing normal claimed for the cupped drawing: rejected, "no flat rotation shows palm up";
- production: palm_up NEEDS_ARTWORK, cupped NEEDS_APPROVAL, fallback reported.

## 3. Pointing, hand-to-desk and the acting test (step 20)

- Pointing: the right point (acting test and regression) uses the pending pointing drawing in review, with the index
  along the forearm. The Phase 3.6 render showed a relaxed hand here because of the resolver bug.
- Hand to desk (classroom): the `hand_on_desk` action chooses palm_down* in review (BACK_TO_CAMERA from above, within
  its tolerance). Contact sliding is unchanged from Phase 3.6. The desk prop is drawn in the performance evidence.
- The 78-frame acting test is re-rendered by `autorig performance-evidence`, which writes
  `natural_pose/performance/acting/` with the fixed hand selection. `shrug_review_preview.png` there now shows
  SHRUG_SUBTLE next to SHRUG_EXPRESSIVE (review), labelled with the drawings actually rendered.

## 4. Shoulders (step 8)

| frame | rise vs neutral r/l | slope r/l | hump r/l |
|---|---|---|---|
| neutral standing | 0 / 0 | 9° / 11° | 6 / 7 px |
| 3.6 shrug_low | 11 / 16 px | −6° / −4° | 10 / 12 px |
| 3.7 SHRUG_SUBTLE | 2 / 4 px | 0° / 1° | 9 / 10 px |
| 3.7 SHRUG_EXPRESSIVE | 2 / 6 px | −1° / 1° | 12 / 14 px |
| character sheet (arms down) | – | 31° / 47° | 2 / 5 px |

The inflated-pad defect of the shrug is fixed: rise is now 2-6 px against 11-16 px before, and the line no longer
rises outward.

Two shoulder limits remain:
- the arms-down shoulder line stays flatter than the character sheet's;
- arms abducted 20-30° show a rounded cap bulging 19-30 px above the shoulder chord (running, hand on desk).

Both come from rotating the T-pose sleeve cap. Deeper clavicle depression opens a seam at the lapel transition, so a
sloped arms-down shoulder is listed as optional artwork. The shoulder-line check no longer applies to arms raised
above 40°, where the arm forms the outer shoulder: an earlier version wrongly flagged pointing poses.

## 5. Reference-guided renders (step 19)

`reference_motion/evidence/comparison_<ref>.png` shows each reference four ways: the reference skeleton (red =
segments moving in depth), the reference-guided schoolboy, the rig silhouette with the reference scaled onto it, and
the hand-authored 3.7 version.

| reference (synthetic) | class | keys | angle error vs target (RMS) | hands |
|---|---|---|---|---|
| pointing | SUPPORTED | 12 | abduction 2.1°, head 0.1°, lean 0.2° | pointing* (r) |
| shrug | REQUIRES_ALTERNATIVE_ARTWORK (forearms toward the camera) | 8 | flexion 2.7°, clavicle 0.6° | cupped* palm up (review) |
| walking | REQUIRES_NEW_VIEW (stride, knee drive; forearms) | 19 | in-place weight/pelvis only (contrapposto 0.06) | walking → relaxed* |

- The reference-guided shrug opens the forearms and chooses palm-up drawings, and it does so without being told
  about cupped hands: the reference's palm-up requirement went through the constrained engine.
- The walk is **not** claimed: only the in-place weight shift and pelvis roll transfer, the stride is never
  rendered, and the arm swing (toward the camera) is not faked in the image plane.
- MediaPipe round trip on a rendered schoolboy video: see `phase3_7_reference_motion_architecture.md`. The video path
  works end to end; accuracy on this cartoon's proportions is limited (arm abduction 13.7° RMS over a 145° range;
  shoulder elevation unreliable).

The revision loop on the retargeted shrug (`revision_loop.png`) found "shoulder line rises outward (−2.2°)". It
proposed clavicle −2°, which was kept because the metric improved with no new blocking defect. The original was
untouched and the result is `PROPOSED_NEEDS_APPROVAL`. The NEEDS_ARTWORK palm_up defects remain, because no control
can fix missing art.

## 6. What is not shown or not claimed

- No drawing is approved. All 16 sleeve-free drawings are pending.
- No open palm-up hand drawing exists, so SHRUG_EXPRESSIVE is NEEDS_ARTWORK in production.
- The VLM critic was not run (no endpoint); only the rule-based critic produced defects.
- No human reference footage was available; the three references are synthetic.
- Moho did not run.
