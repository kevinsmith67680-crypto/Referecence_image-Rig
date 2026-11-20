# Real-artwork tests (Phases 1 and 2), 2026-11-20

Two runs on the user's character. Run 2, the T-pose reference, supersedes run 1 for rigging.

# Run 1: front view cropped from the character sheet

This is the first run of the pipeline on the user's own character art, with real SAM 3 segmentation and real
FLUX Fill inpainting. Every number below comes from this run. Nothing was reviewed or approved by a human
yet, and Moho was not involved.

## Input

* `references/schoolboy/character_sheet.jpg` is the user's character sheet (1448×1086 JPEG). It contains:
  * front, 3/4, side and back views;
  * 10 expressions, 11 mouth shapes and eye/brow sets;
  * puppet parts and action poses.
* `references/schoolboy/front_view.png` is the **Front** view: a pixel-exact crop at x 0–213, y 0–615, not
  rescaled (`crop.json`). The pipeline takes one character, so only this view was used.
* `references/schoolboy/landmarks_front.json` holds manual joint positions, **estimated by the assistant** from a
  gridded zoom, not by the user. They are used for the right leg, the elbows and the wrists (see below) and must
  be verified.

The front view is small: the figure is about 195×600 px. A higher-resolution export of the sheet would help every
step.

## Commands

```bash
python -m autorig analyze references/schoolboy/front_view.png --out projects/schoolboy/character_project \
  --manual-landmarks references/schoolboy/landmarks_front.json
python -m autorig prepare projects/schoolboy/character_project --out projects/schoolboy/character_package \
  --provider flux-fill-dev
```

## Costs (ledger `.autorig_cache/replicate/ledger.json`)

| Call | Count | Estimated cost |
|---|---|---|
| SAM 3, failed (upload transport, see below) | 1 | $0.0004 |
| SAM 3, succeeded (body; face crop) | 2 | $0.0053 |
| FLUX Fill dev, succeeded (5 regions × 2 candidates) | 5 predictions, 10 images | $0.50 (price unverified) |
| **Total** | | **$0.506 of the $1.00 ceiling** |

## Bugs found by real data, and fixed

1. **SAM 3 rejected uploaded files.** The model failed with "No valid image files found in the input!" when given a
   Files API URL. The same request sent as a data URI succeeded. Data URI is now the default for SAM 3 and for
   inpainting; upload remains an option.
2. **Face schema.** A rejected face candidate had no `label`, so `face.json` failed its schema.
3. **Empty Phase 1 parts crashed Phase 2.** Phase 2 now excludes them with a warning.
4. **Generated pixels visible in the neutral pose (33 px).** A hidden region came from an ambiguous edge that
   the final z-order did not enforce. Regions are now trimmed to what layers above the owner actually cover
   (`trim_regions_to_cover`), so this cannot happen.
5. **One failed or over-budget inpainting call aborted the whole package.** It is now recorded per region
   (`generative_failed` / `generative_skipped_budget`). A model version change still stops the run.
6. **Tiny crops went to the model at source size** (128–192 px). They are now upscaled by an integer factor to
   at least 512 px and area-averaged back to source pixels; the scale is recorded in provenance.

## Phase 1 results (real SAM 3)

| | |
|---|---|
| Faithfulness | Layers are exact copies of the source pixels (max error 0, no overlaps). Linework retained: 97.5%. |
| Coverage | 96.7% of the estimated foreground. **The backpack straps are in no layer**: "backpack" is not in the prompt vocabulary. |
| Face | All found: eyes, pupils, brows, nose, mouth, both ears. |
| Body, automatic | SAM 3 returned **one** "leg" (the character's left). The right leg exists only inside the "pants" mask, so right hip, knee and ankle were missing and `pelvis`, `thigh_r`, `shin_r` came out **empty**. Elbows were placed at the cuffs and wrists mid-hand (straight arms give the estimator no bend to find). |
| Body, with manual landmarks | All parts are populated, but the split is still wrong. The inner strip of the right trouser leg went to `thigh_l` (SAM's single "leg" mask spills over it), and the arm layers include blazer side panels. "upper_clothing" was not found: the vocabulary says "shirt", but this is a blazer. |

## Phase 2 results

| | |
|---|---|
| Preservation | Source unchanged, originals verified, neutral pose exact, **0 generated pixels visible in neutral**. |
| Occlusion | 48 front/back relations, of which 19 are ambiguous (flagged). |
| Regions | 17 hidden regions: 6 flat fill, 5 generative, 6 not needed for the planned motions. |
| Inpainting | Pipeline works end to end: version pin checked, budget pre-checked, 2 seeded candidates per region, one 429 retry, outputs downloaded and cached. FLUX re-encodes the whole crop, so about 25,000 px outside each mask changed and **all were discarded**. |

What the inpainted pixels look like (by eye; nothing approved):

| Region | Result |
|---|---|
| forearm under left hand | skin-coloured wrist: plausible |
| upper arm under left forearm | navy sleeve fabric: plausible |
| torso under left upper arm | dark jacket fabric with a grey piping stripe: plausible, a little noisy |
| eye whites under both irises | **wrong**: FLUX redrew the dark iris/pupil instead of white sclera. One generic prompt is used for every region, and a masked eye gets "continued" as an eye. |

Pose previews:
* neutral is exact;
* elbows bent looks reasonable;
* head tilt shows small gaps at the collar;
* arm_90, walking and sitting break up. The arm layer carries a blazer panel, and the trousers split along the
  wrong line.

**The main limit on real art is the Phase 1 body partition, not Phase 2.** The live artwork manifest has no
control READY: 9 need new artwork, 4 need reconstruction and 2 need review.

## What this changes

* Replicate credit and the reference image are no longer blockers.
* Phase 1 and Phase 2 now have real-artwork evidence. Both remain **PARTIAL**: the body partition is not good
  enough for rigging, the eye-white inpainting failed, and nothing has been human-reviewed.
* Phase 0 native Moho verification is still **NOT EXECUTED**.

## Recommended next steps (not started)

1. Clothing-aware Phase 1 vocabulary: blazer/jacket, trousers, backpack and straps. Split legs from the trousers
   mask using the feet when only one "leg" is found. Keep arm layers to the sleeves rather than jacket panels.
2. Region-specific inpainting prompts, for example "plain white eye sclera, no iris, no pupil", or a deterministic
   sclera fill.
3. Make pixel thresholds such as gap tolerance scale with image size. They were tuned on a 1000×1500 fixture.
4. Use the rest of the character sheet. It already contains **real** artwork for many items listed as "requires
   new artwork": blink and eye sets, the mouth shapes A/E/I/O/U and others, 3/4, side and back views, and
   separated hands. Extracting them beats generating them.


# Run 2: T-pose reference

`references/schoolboy/tpose_front.jpg` was supplied by the user: 1086×1448 JPEG, arms out, legs apart, no
backpack. It has about 12× the pixels of run 1's crop, and no limb overlaps the body.

```bash
python -m autorig analyze references/schoolboy/tpose_front.jpg --out projects/schoolboy_tpose/character_project
python -m autorig prepare projects/schoolboy_tpose/character_project --out projects/schoolboy_tpose/character_package \
  --provider flux-fill-dev --candidates 1
```

No manual landmarks: every joint is automatic.

## Bugs found and fixed

1. **JPEG data URI.** The MIME type was built from the extension (`image/jpg`), and the model rejected it. The
   real type (`image/jpeg`) is now used.
2. **Phase 2 rejected every non-PNG source.** It checked the PNG decode against the JPEG's hash. Now it verifies
   the byte-identical original, checks that the PNG decode is pixel-exact, and copies both.
3. **Wrists at the hand centre.** SAM's "arm" mask includes the hand, so the arm/hand contact was the whole
   hand. The estimator now uses the boundary of the arm minus the hand, ignores the 1–2 px mask fringe, and
   bridges the shirt cuff between them.
4. **Knees high on the thigh.** The leg's centre-line curls at the crotch and read as a "weak bend". A weak bend
   now counts only in the middle 30–70% of the limb.
5. **Hips converging at the crotch.** With spread legs, the trace up through the trousers ran inward. The height
   is kept, and x now comes from the straight middle of the leg's centre-line. On the synthetic ground truth,
   hip error improved from 13.9/12.9 px to 4.3/4.6 px. Mean body-part IoU moved 0.929 → 0.922 (test
   threshold 0.88).
6. **Pose contact sheet tiles were 30% of the source size** (64 px wide for run 1). They now scale to about
   480 px tall.

## Results

| | |
|---|---|
| Phase 1 | Coverage 99.2%, all 17 parts populated, face complete (eyes, pupils, brows, nose, mouth, ears), layers pixel-exact. Joints: wrists at the cuffs, elbows mid-arm, hips at the thigh tops, knees mid-leg. Remaining: no `torso` joint ("upper_clothing" not found: the vocabulary says "shirt"); linework retained 93.9%. |
| Phase 2 | Neutral exact, **0 generated pixels visible in neutral**. 18 hidden regions: 7 flat fill (now including the eye whites), 8 generative, 3 not needed. |
| Cost | SAM 3 $0.0107 (including one failed call, $0.0004) + FLUX Fill dev 8 images $0.40 (price unverified). Ledger total **$0.916 of $1.00**. One candidate per region was used to stay under the ceiling. |

Pose gaps (originals only → with extensions, px):

| Pose | Originals only | With extensions |
|---|---|---|
| neutral | 0 | 0 |
| arm_45 | 274 | 274 |
| arm_90 | 390 | 390 |
| left_arm_90 | 1290 | 2 |
| elbows_bent | 1223 | 4 |
| walking | 1306 | 872 |
| sitting | 2514 | 915 |
| head_tilt_left | 331 | 153 |
| head_tilt_right | 327 | 129 |

The poses rotate joints relative to the rest pose, which is now a T-pose.

Inpainting, judged by eye (nothing approved):

| Region | Result |
|---|---|
| forearm under each hand | skin-coloured wrist: plausible |
| shin_l under foot_l | trouser fabric: plausible |
| torso under upper_arm_l | jacket fabric: plausible |
| neck under torso | shirt collar and tie redrawn: hidden in neutral, odd content for a neck layer |
| **neck under head_base** | **wrong**: drew nostrils, a mouth line and a goatee into the neck (the region reaches up under the face) |
| **thigh_l / thigh_r under pelvis** | **wrong details**: copied a faint crest and a jacket button onto the trousers |

Visual pose check:
* neutral, elbows bent, sitting and left arm raised look clean;
* raising the right arm shows a dark wedge of jacket shoulder at the armpit;
* walking shows small tears at the crotch;
* head tilts leave specks at the chin.

## Remaining issues and next steps (not started)

1. Region-specific inpainting prompts: "plain skin neck, no facial features" and "plain dark trouser fabric, no
   buttons". Also shorten the neck shape so it does not reach up under the face.
2. No `torso__under__upper_arm_r` region (the left exists), so the right shoulder gap stays at 274/390 px.
   Check that occlusion edge.
3. Add "blazer/jacket" to the clothing vocabulary (torso joint) and trim the jacket shoulder from the upper-arm
   layers.
4. Human review in `visual_review.html`, then Phase 0 native verification. Both are needed before any Moho
   import (Phase 3, not started).
