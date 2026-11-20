# First real-artwork test (Phases 1 and 2), 2026-11-20

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
