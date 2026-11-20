# Phase 1 report: AI character analysis, segmentation and live-puppeteering preparation

> **Update 2026-11-20:** Replicate credit and the user's reference image are now available. The first real-artwork
> run of Phases 1 and 2 (real SAM 3 and FLUX Fill inpainting, $0.51) is documented in
> [real_artwork_test.md](real_artwork_test.md). The verdict stays PARTIAL; the reasons are listed there.

| | |
|---|---|
| **Verdict** | **PARTIAL** |
| Why not PASS | Two hard acceptance criteria cannot be met yet: **(1) real SAM 3 inference has not run.** Replicate rejected every prediction with *HTTP 402 Insufficient credit*. **(2) The real schoolboy reference image is not in the repository**, so nothing has been evaluated on the actual character. |
| Why not FAIL | The whole pipeline is implemented and tested (253 tests pass). The Replicate integration is verified against the live API up to the billing check. The output format is verified from a real model output. On a synthetic character with exact ground truth, the pipeline recomposes the image pixel-exactly, and every joint it marks *reliable* is within 5.5 px of the truth. |
| Phase 0 | **Still not verified inside Moho** (no native report exists). This limitation carries over unchanged. |
| Next stage | **Not ready.** Three blockers must clear first (see §14). |
| Branch | `claude/eloquent-cori-iv4ab1` |

> Nothing here claims the character is rigged, that real-time face tracking works, or that any
> Moho SHOWTIME mapping is verified. No hidden artwork was reconstructed.

---

## 1. Implementation summary

| Step | Delivered | Code |
|---|---|---|
| 1. Inspect existing work | Phase 0 code, schemas, coordinate conventions and tests reviewed; Phase 0 confirmed **not natively verified**; Phase 0 behaviour unchanged (all 189 earlier tests still pass, 2 native tests skipped) | — |
| 2. Replicate SAM 3 | Token from `REPLICATE_API_TOKEN`, never logged, sent only to `api.replicate.com`; live schema inspected; version pinned; per-version capability table; file upload; create-then-poll predictions; configurable retries (duplicate-safe); content-addressed cache; outputs saved before parsing; provenance (model, version, parameters, prediction id, timing); cost ledger with spending ceiling; offline fixture provider | `src/autorig/segmentation/` |
| 3. Anatomy | `CharacterAnatomyAnalyzer` with pluggable `PoseEstimator`s (manual landmarks; mask geometry using contact zones, medial limb paths, path-intrinsic bend detection and clothing-aware hidden-joint search). 20 joints with parents, confidence, `reliable` flag, method, flags, occlusion and visibility; part bounding boxes. No human proportions assumed. VLM estimator: interface only (no verified VLM API in use) | `phase1/anatomy.py` |
| 4. Face | `analyze_face`: features on an upscaled head crop mapped back to source pixels; quality gates with recorded rejection reasons; pupils recovered by intensity when not segmented; landmarks; eyelid outlines (marked as not separable); jaw pivot (approximate); visibility per component; missing-artwork list | `phase1/face.py` |
| 5. Body masks | Anatomical partition: hair, head, neck, hands and feet from masks; everything else to the nearest **thick** bone segment, constrained by the visible region (limb, shirt, shorts); sleeves via the visible clothing boundary. Clothing coverage is recorded per part, and anatomical parts per clothing region | `phase1/partition.py` |
| 6. Facial masks | Head base, brows, eyes, mouth and nose layers carved from the head; pupils, irises and ears kept as masks; low-quality candidates rejected, never auto-accepted | `phase1/face.py`, `project.py` |
| 7. LiveControlSpec | Versioned contract (v1.0.0) with all 20 controls; **verified mappings kept separate (currently empty)** from proposed Moho SHOWTIME mappings; adapter design (input → normalise → curve → Smart Bone/Action) as data only | `phase1/live_controls.py` |
| 8. Readiness | `LiveReadinessEvaluator`: 8 capabilities × {READY, REQUIRES_RECONSTRUCTION, REQUIRES_NEW_ARTWORK, UNSUPPORTED, NEEDS_REVIEW}; conservative policy; JSON and Markdown output | `phase1/readiness.py` |
| 9. Layers and compositor | Full-canvas RGBA layers of **original pixels** (1:1, no scaling), pivots, provenance; neutral recomposition, overlays, layer sheet, difference heatmap, fidelity metrics | `phase1/compositor.py` |
| 10. Hybrid contract | `artwork/layers.json` (v1.0.0) separating the original bitmap, extracted layers, generated artwork (empty), vector corrections (empty, schema defined), masks, bone attachment and facial-control dependencies | `phase1/project.py`, `phase1/schemas.py` |
| 11. Tests | 64 new Phase 1 tests (see §11) | `tests/test_p1_*.py` |
| 12. Project format | `character_project/` exactly as specified, plus `analysis/live_readiness.md`, `previews/layers.png` and `metadata.json` with SHA-256 for every output | `phase1/project.py` |
| CLI | `analyze`, `synthetic-character`, `eval-synthetic`, `replicate-check` (free), `replicate-smoke` (budget-capped), `export-schemas` | `cli.py` |

## 2. Exact Replicate model and version

* **Model:** [`vufinder/sam3`](https://replicate.com/vufinder/sam3) (Meta SAM 3; license per facebookresearch/sam3).
* **Pinned version:** `1bf97763d5dfd3a1584adca913a8ef4b43c684fca97e04e39e4c50a3a5e09650`
  (latest; created 2026-06-12; Cog 0.16.12). `replicate-check` re-verified on 2026-11-20 that the live
  schemas match the snapshot in `src/autorig/data/replicate/vufinder_sam3_versions.json`.
* **The variants differ, as you warned:**

| Version | Inputs |
|---|---|
| `1bf97763…` (pinned) | image, prompts, confidence_threshold, visualize, backbone_output, **offset_masks, concat_input, split_output** |
| `861f5a2e…` | as above without concat_input/split_output |
| `e6b684bc…` | image, prompts, confidence_threshold, visualize, backbone_output |

* **Prompt types supported (all implemented):** per-concept JSON strings containing `text`,
  `positive_boxes`, `negative_boxes`, `positive_points` and `negative_points`. Coordinates are normalised to [0, 1];
  boxes use the format (cx, cy, w, h). AutoRig accepts pixel coordinates and converts them.
* **Output format: verified from a real model output** (the model's default-example prediction
  `r1wdvm6p3srga0cyps1vb0345r`, downloaded at no cost). It is one JSON file per prompt holding
  `masks[N][1][H][W]` (0/1, at input resolution), `boxes[N][4]` (pixel xyxy) and `scores[N]`.
  The provider requests this dense format explicitly (`offset_masks=false`, `split_output=true`). The
  `offset_masks` cut-out format is undocumented and could not be observed, so it is not used. Any
  structural deviation, such as a transposed mask shape, raises `SegmentationFormatError` instead of
  being guessed around.
* **Pinned but not inference-tested:** the version could not be exercised because of the 402.

## 3. Actual API usage and costs

| Call | Count | Cost |
|---|---|---|
| `GET /v1/models/vufinder/sam3`, `/versions`, `/hardware`, `/account` | a few | free |
| `POST /v1/files` (synthetic image, 115 KB) | 1 | free |
| `POST /v1/predictions` | 2 attempts, both **HTTP 402 Insufficient credit** (no prediction created) | **$0.00** |
| Download of the public default-example outputs (format discovery) | 3 files | free |
| **Total billed** | | **$0.00** (ledger has no billed entries) |

Cost model: Replicate's API exposes neither the hardware of a public model nor a price per prediction.
Costs are therefore **estimates**: `metrics.predict_time` × a configurable rate. The default rate is
$0.000975/s (Nvidia L40S, the most expensive GPU listed for this account).
The default-example run took 8.97 s, about $0.009 at that rate. The ceiling defaults to **$1.00 cumulative**
(`AUTORIG_REPLICATE_BUDGET_USD`). Before every paid call, the worst case (120 s → $0.117) must fit under it,
otherwise nothing is sent.

## 4. Successful and failed segmentations

* **Real SAM 3: none executed** (402). Nothing about SAM 3's quality on cartoon art is claimed.
* **The real reference character: BLOCKED.** No schoolboy image is in the repository, and no
  unrelated illustration was substituted.
* **Synthetic ground-truth character, fixture provider** (exact masks): all 13 body concepts and
  12 facial features accepted, nothing rejected. This exercises the pipeline only. It does not
  test SAM 3.
* **Failure handling, tested:** a garbage "eye" mask covering the whole head is **rejected** with the
  reason "area outside plausible range". Readiness then becomes NEEDS_REVIEW. Missing concepts (eyebrow,
  sleeve, neck, hand, shoe) are reported as warnings or `not_detected` and change readiness. Weak, tiny,
  duplicate and wrongly-shaped instances are rejected with reasons in `anatomy.json` and `face.json`.

## 5. Body-landmark accuracy (synthetic ground truth, 1000×1500 px)

With exact masks: **median joint error 3.9 px; worst joint marked *reliable* 4.9 px**. Nine of 20 joints are
flagged *unreliable*, each with a reason:
* shoulders and hips hidden under the sleeves and shorts (13–23 px; extrapolated, so always flagged for review);
* straight-limb elbows and knees, where no bend is visible (up to 25 px for the elbows);
* the pelvis, derived from the hips.

Robustness, with the same character and degraded masks:

| Condition | Joint median err (px) | Worst *reliable* joint (px) | Unreliable joints | Part IoU (mean) | Coverage | Max error inside layers |
|---|---|---|---|---|---|---|
| exact masks | 3.9 | 4.9 | 9/20 | 0.929 | 1.0000 | 0 |
| masks eroded 3 px | 4.4 | 4.6 | 9/20 | 0.860 | 0.9819 | 0 |
| masks eroded 6 px | 9.4 | 0.5 | 14/20 | 0.735 | 0.9452 | 0 |
| masks eroded 10 px | 12.1 | 0.5 | 14/20 | 0.609 | 0.8964 | 0 |
| masks dilated 3 px | 3.1 | 5.1 | 9/20 | 0.849 | 1.0000 | 0 |
| masks dilated 8 px | 3.8 | 5.5 | 9/20 | 0.738 | 1.0000 | 0 |
| no sleeve/neck/eyebrow masks | 3.9 | 4.9 | 10/20 | 0.804 | 1.0000 | 0 |
| no hand/shoe masks | 23.9 | 4.2 | 17/20 | 0.532 | 1.0000 | 0 |

**Key property (regression-tested):** degraded evidence makes joints *unreliable*; it never makes them
confidently wrong. Getting there took four real fixes found by these GT runs:
* gap-tolerant part contacts;
* never treating the coarse `person` silhouette as attachment evidence;
* path-intrinsic bend detection, where a chord-based version produced a "reliable" 103 px knee;
* treating joints hidden under clothing as extrapolations.

Part masks against GT: hands, feet, head, hair, neck and torso ≥ 0.98 IoU; upper arms 0.96;
forearms 0.93; pelvis 0.89; thighs and shins 0.78–0.82. The last two follow a boundary *inside*
continuous regions (the knee cap; the shorts across the hip line), where the GT itself uses an arbitrary
convention. The `sleeve` concept lifts mean IoU from 0.84 to 0.93 by using the visible clothing boundary.

## 6. Facial-feature extraction quality

On the synthetic character with exact masks (an upper bound, since it tests the analysis logic, not SAM 3):
* every feature mask has IoU 1.000;
* eye centres 0.4 px, pupils 1.6 px, brows ≤ 0.8 px, nose 0.4 px, mouth corners ≤ 1.4 px, ears ≤ 0.01 px;
* the chin is 18 px off and the mouth centre 9 px; both are definitional, since the GT uses the arc's lowest
  point and the head ellipse rather than the mask centroid or the visible outline.

When the pupil concept is missing, the intensity fallback finds the pupils within 4 px. A bug found here, where
the fallback locked onto the eye-outline ring, was fixed. The closed line mouth is classified `closed_line`.
Lips and interior are reported as `not_separable` / `not_visible_mouth_closed`, and eyelids as outline-only.
**Real-image facial extraction quality: unknown (blocked).** Tiny features are segmented on a head
crop upscaled toward 1024 px; this is a design choice still awaiting real-data validation.

## 7. Reconstruction fidelity

Synthetic, exact masks:
* coverage 1.0;
* overlaps 0;
* **pixels differing inside layers 0** (colours are copied, never resampled);
* linework retained 100%;
* SSIM 0.9999 (PSNR undefined: identical).

Binary thresholds initially lost the anti-aliased fringe of the outlines: 2,398 px, about 6% of the linework. A
fringe-absorption step now assigns such pixels to the nearest layer with their **original colours**.
Known limitation: those edge pixels keep the background colour blended in. On a different background a faint
halo can appear until alpha matting is added. Every degraded run still has 0 error inside layers, and
uncovered pixels show in magenta in `difference.png`. **No single metric is treated as proof of artistic
quality.** The overlays and layer sheets are there for human review.

## 8. Live-control readiness (synthetic character)

| Capability | Status | Why |
|---|---|---|
| Blinking | REQUIRES_NEW_ARTWORK | eyes are separable; no closed-eye art exists |
| Eyebrow movement | REQUIRES_RECONSTRUCTION | brows separable; the skin under them was never painted |
| Mouth opening | REQUIRES_NEW_ARTWORK | the mouth is a closed line, so there is no interior |
| Lip-sync | REQUIRES_NEW_ARTWORK | a single mouth shape only |
| Eye direction | REQUIRES_RECONSTRUCTION | pupils separable; the sclera under them is hidden |
| Head tilting | REQUIRES_RECONSTRUCTION | neck and collar under the chin are hidden |
| Head turning | REQUIRES_NEW_ARTWORK | front view only |
| Facial expressions | REQUIRES_NEW_ARTWORK | neutral expression only |

**Nothing is READY**, and the evaluator is regression-tested never to claim READY without the artwork.
The 20 LiveControlSpec controls are defined with ranges, neutral values, targets, dependencies and readiness.
**Moho SHOWTIME:** `verified_mappings` is empty. Twelve `proposed_unverified` mappings are derived from
Lost Marble's published wording (head turning / tilting, blinking, mouth closing, lip pursing; XInput
buttons and sticks via "Select Input"). The real input identifiers, ranges and storage are unknown until the
Live Models discovery protocol (`docs/live-models.md`, D0–D6) runs on the Mac.

## 9. Missing facial artwork (synthetic character)

* Closed eyes (left and right).
* Sclera fill under both pupils.
* Skin under both brows.
* An open mouth with interior.
* A phoneme mouth set.
* Skin under the mouth.
* Three-quarter and profile head views.
* Back of hair and the hidden head outline.
* A neck extension under the chin.
* Expression variants.

Each item lists the capabilities that need it, in `face.json` → `missing_artwork`.

## 10. Hybrid artwork contract

`artwork/layers.json` v1.0.0:
* `original_bitmap` (reference only, never modified, with SHA-256);
* 23 `layers` of kind `extracted_original_pixels`, each with mask, bbox, z-index, proposed pivot, bone and
  parent bone, provenance, an identity transform (scale 1, offset 0), `facial_control_dependencies`, and a
  `vector_replacement` slot;
* `masks`;
* `generated_artwork` (empty; requires provenance and an `approved` flag);
* `vector_corrections` (empty; editable, Moho-importable vector layers targeting a layer).

Future vector work attaches beside the bitmap, so the illustration is never forced into approximate SVG.

## 11. Automated test results

`python -m pytest`: **253 passed, 2 skipped** (Python 3.12.3, numpy 2.5.5, scipy 1.18.1, httpx 0.28.1,
Pillow 13.0.0, Lua 5.4 via lupa).

| File | Tests | Covers |
|---|---|---|
| `test_p1_replicate.py` | 26 | Token handling and redaction (repr, errors, logs); token never sent to other hosts; 401/402/403/422 not retried; 429/5xx retried with a configurable limit; no retry of prediction creation after send (duplicate billing); failed predictions raised and recorded; version-schema-driven inputs; older version never gets unknown fields; prompt normalisation; masks in original non-square coordinates; outputs saved before parsing; cache hit with zero requests; cache keys; budget refusal **before any request**; persistent ledger; transposed-mask and malformed-output detection |
| `test_p1_pipeline.py` | 26 | Project structure; schema validation of all six documents from disk; exported schemas in sync; output hashes; **byte-identical source**; layers keep coordinates and original pixels; partition and exact recomposition; joint accuracy and honest flags; side convention; occlusion; part IoU; clothing vs anatomy; facial landmarks and masks vs GT; missing artwork; readiness never READY; LiveControlSpec; refusal to overwrite user folders; eroded masks reported (magenta); missing brows; intensity pupils; rejection of a low-quality eye; **"reliable means reliable"** under 4 degradations |
| `test_p1_units.py` | 12 | Empty and overlapping masks; bbox and coordinate conventions; exact pixel extraction; PNG mask round-trip; instance rejection reasons; fringe absorption keeps the partition; side split; bend detection (L-shaped vs straight); joint validation; foreground estimation; fidelity tamper detection |
| Phase 0 + Live Models | 189 (2 skipped) | Unchanged and passing; native Moho tests still **NOT EXECUTED** |

Mutation checks (each defect injected separately, then reverted; all caught):

| Mutant | Result |
|---|---|
| Extracted layers alter colours by +1 | 5 failures |
| Budget check removed | 1 failure |
| Token sent to `replicate.delivery` | 1 failure |
| Blinking claimed READY | 1 failure |
| Partition overlaps (hands not excluded) | 2 failures |
| Facial quality gate disabled | 1 failure |

## 12. Remaining problems

1. **No real SAM 3 inference** (402). SAM 3's behaviour on cartoon art (concept recall for "arm",
   "sleeve", "eyebrow" and similar, mask edges, small-feature quality on the upscaled crop) is entirely unmeasured.
2. **The real reference is missing.** No accuracy or fidelity number applies to the schoolboy character.
3. Elbows and knees on straight limbs, and joints hidden under clothing, need human confirmation.
   Manual landmarks are supported (`--manual-landmarks`).
4. Edge pixels keep background colour (no alpha matting), so a halo is possible on new backgrounds.
5. The foreground estimate is a background-colour heuristic for opaque images. It is unreliable on busy
   backgrounds; a transparent PNG avoids it.
6. Front-facing assumption for left/right; the analysis is not designed for profile or back views.
7. Phase 0 and the Live Models discovery have not been run in Moho.

## 13. Recommended improvements

1. Add Replicate credit, then run `replicate-smoke` (≈$0.01) and `analyze` on the synthetic character.
   Score real SAM 3 with `eval-synthetic`, and tune the prompt profiles before touching the real reference.
2. Evaluate the schoolboy reference; then hand-check joints and features, and provide manual landmarks where flagged.
3. Alpha matting for edge pixels (known flat background, or a matting model) with an explicit provenance tag.
4. Use SAM 3 box and point prompts seeded from the anatomy for small features that text prompts miss.
5. A vision-language pose estimator plugin (behind the existing interface, budget-capped like SAM 3).
6. Complete the Moho 14.5 discovery protocol, so verified SHOWTIME mappings can replace the proposed ones.

## 14. Readiness for the next stage: not ready

The blockers, all of which need you:

1. **Replicate credit:** <https://replicate.com/account/billing>. The ceiling stays $1.00 by default.
2. **The schoolboy reference image:** commit it (e.g. `references/schoolboy.png`) or attach it, and say
   whether it may be committed to the repository.
3. **Phase 0 native verification in Moho:** `docs/phase0/manual-verification.md`.

Then run:

```bash
REPLICATE_API_TOKEN=<your token> python -m autorig replicate-smoke examples/phase1_synthetic_character/synthetic_fixture.png --budget-usd 0.25
```

```bash
python -m autorig analyze references/schoolboy.png --out character_projects/schoolboy --character-id schoolboy --budget-usd 1.00
```

The cloud sandbox reaches Replicate through a credential the network proxy injects. There,
`REPLICATE_API_TOKEN` is set to the placeholder `proxy-injected`, and the real token never enters
the code or the logs.

Sources:
* [Replicate model vufinder/sam3](https://replicate.com/vufinder/sam3)
* [Replicate pricing](https://replicate.com/pricing)
* [facebookresearch/sam3](https://github.com/facebookresearch/sam3)
* [Lost Marble – Moho 14.5 SHOWTIME](https://moho.lostmarble.com/blogs/news/moho-14-5-showtime-design-rig-and-animate-characters-live-all-the-power-of-moho-now-live)
* [Moho 14.5 press release](https://www.openpr.com/news/4664782/moho-14-5-showtime-adds-real-time-character-and-vtuber)
