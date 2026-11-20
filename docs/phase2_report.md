# Phase 2 report: artwork reconstruction, occlusion recovery and animation-ready assets

> **Update 2026-11-20:** Replicate credit and the user's reference image are now available. The first real-artwork
> run of Phases 1 and 2 (real SAM 3 and FLUX Fill inpainting, $0.51) is documented in
> [real_artwork_test.md](real_artwork_test.md). The verdict stays PARTIAL; the reasons are listed there.

| | |
|---|---|
| **Verdict** | **PARTIAL** |
| Why not PASS | **Cloud inpainting could not be tested**: Replicate still answers *HTTP 402 Insufficient credit*, re-checked at the start of Phase 2. Under the acceptance rules that alone caps the verdict at PARTIAL. There is also **no real reference artwork** in the repository, and Phase 1's real SAM 3 segmentation has never run. All evidence comes from the **labelled synthetic character**. |
| Why not FAIL | Every Phase 2 component is implemented and tested (282 tests pass). On the synthetic fixture, original pixels are provably untouched: the neutral pose is exact, with **0 generated pixels visible**. Hidden regions are reconstructed with **recall 1.0 and precision 0.92** against exact ground truth, pose tests expose and report defects, and every schema validates. |
| Real-image reconstruction | **NOT DEMONSTRATED.** Nothing in this report shows that reconstruction works on real cartoon artwork or with a generative model. |
| Earlier phases | Phase 0: not verified in Moho. Phase 1: PARTIAL (no real SAM 3 inference, no reference image). Both carry forward unchanged. |
| Next stage | **Not started**, as instructed. |

## 1. Implementation summary

| Step | Delivered | Module |
|---|---|---|
| 1 Inspection and plan | Repo, reports and schemas reviewed; blockers re-verified (402, no reference image, Phase 0 unverified) | `docs/phase2_implementation_plan.md` |
| 2 Occlusion | `OcclusionAnalyzer`: contact zones, a **linework-ownership cue** (the occluder owns the outline at a shared boundary), category priors, conflict → ambiguous, expected part shapes (tapered limb capsules, hulls, skull ellipse fit, neck capsule), exposure per joint or facial control, manual overrides (edges, excluded regions, replacement masks, pivots), and z-order resolution with cycle breaking | `phase2/occlusion.py` |
| 3 Providers | `ReconstructionProvider` interface; deterministic fixture provider; `ReplicateInpaintProvider` with schema-derived adapters (FLUX Fill dev/pro, Ideogram v2), pin-by-verification, budget pre-check, multi-candidate seeds, cache, bounded retries, immediate download, ledger | `phase2/providers.py` |
| 4 Preservation | `ArtworkStore`: A original (immutable, pixel-hashed), B generated (separate layers and provenance masks), C corrections, D working; extensions can only add pixels inside their region and never on their own original; the original is composited on top; change maps; rollback; verification | `phase2/store.py` |
| 5 Body reconstruction | Deterministic flat fill, plus outline continuation *only where a visible outline runs into the region*; non-flat regions route to a provider or are flagged; nothing is merged into originals | `phase2/reconstruct.py` |
| 6 Joints | 13 joints: pivot (with reliability), attached components, padding, front/back, rotation-safe extensions, range sweep (originals only vs with extensions), contour and overlap checks, binding recommendations | `phase2/joints.py` |
| 7 Face | Pixel-exact eye split (eye white / iris), sclera and skin fills, skull under hair, brow/ear-under-hair flags, 12 switch-drawing slots (missing, approval required) | `phase2/facial.py` |
| 8 Live manifest | 15 controls: layers, optional vectors, switch drawings, pivot/region, safe range, readiness, missing assets, approval status; Moho mappings kept separate | `phase2/facial.py` |
| 9 Hybrid vectors | SVG joint caps (only on flat parts where gaps remain) and eyelid guides; full-canvas viewBox; labelled generated, `exact_reconstruction: false` | `phase2/vector.py` |
| 10 Pose engine | 9 poses (neutral, arm 45/90, left arm 90, elbows bent, walking, sitting approximation, head tilt left/right); gap, contour, overlap, clipping and distortion diagnostics | `phase2/pose.py` |
| 11 QA | `reconstruction_quality.json`, comparison, alpha/provenance overlay, difference, joint close-ups, pose contact sheet, change maps, flags | `phase2/pipeline.py` |
| 12 Review | `approvals.json`, self-contained `reports/visual_review.html` (exports decisions JSON), `autorig review` (approve, reject, request candidate, set pivot, restore original, import, approve phase) | `phase2/review.py` |
| 13 Package | Step 13 layout (plus `artwork/manifest.json`, `artwork/store.json`, `previews/diagnostics/`) | `phase2/pipeline.py` |
| CLI | `autorig prepare`, `autorig review` | `cli.py` |

## 2. Architecture changes

* A new `src/autorig/phase2/` package. Phase 1 projects are **read-only inputs**: the tests hash every Phase 1 file before
  and after. Phase 2 writes a new package directory.
* `ReplicateClient.create_model_prediction` (the official-model endpoint) and `SpendLedger.check_amount` were added.
  The SAM 3 paths are unchanged.
* The **canonical layer manifest v2** (`artwork/manifest.json`) separates A originals, B extensions, C corrections and vectors.
  Phase 1's `layers.json` v1 is untouched.
* The occlusion graph **corrects Phase 1's proposed z-order** where linework disagrees: on the fixture, shoe over shin and
  shin over thigh. These changes are recorded in `occlusion.json`.

## 3. Reconstruction model selection

Schemas were inspected through the Replicate API, at no cost:

| Model | Mask | Negative prompt | Outputs/call | Size handling | Price |
|---|---|---|---|---|---|
| **`black-forest-labs/flux-fill-dev`** (initial candidate) | white = inpaint | no | 1–4 | `megapixels=match_input`, ×32 dims, ≤1440 | ~$0.04–0.05/image (not shown on its page; ledger uses $0.05) |
| `black-forest-labs/flux-fill-pro` | white = inpaint | no | 1 | input size | $0.05/image |
| `ideogram-ai/ideogram-v2` | **black = inpaint (inverted)** | yes | 1 | mask resized | not verified (ledger uses $0.08) |
| `bria/genfill` | undocumented | yes | — | `preserve_alpha` | not verified; not adapted |

Rationale for FLUX Fill dev as the starting point:
* open weights, so it is reproducible;
* up to 4 seeded candidates per call;
* pixel-aligned lossless PNG output (`match_input`);
* LoRA support for a later illustration-style adapter;
* a documented mask convention.

Ideogram's adapter exists to exercise the inverted-mask path; a test checks that both conventions are encoded correctly.
**Suitability for 2D cartoon art is unknown until inference runs.**

## 4. Exact provider and model versions

All candidates are Replicate **official models**, which hide their version lists, so they cannot be pinned by version. Instead
the recorded `latest_version` is re-read before every paid call, and the call is **refused if it changed** (tested).

| Model | Pinned `latest_version` |
|---|---|
| flux-fill-dev | `a053f84125613d83e65328a289e14eb6639e10725c243e8fb0c24128e5573f4c` (2025-03-31) |
| flux-fill-pro | `41c767bcbfffe54ef8f05eb4d0100f9314790f7fc43a7b88d73ec06839deddb9` (2026-04-14) |
| ideogram-v2 | `3e6071946ab5319b3bcc37a4d00083e743dfdff5be386df6a2ff1f212fc7365b` (2025-11-07) |

Segmentation is unchanged: `vufinder/sam3@1bf97763…`, still not inference-tested.

## 5. Tests executed

`python -m pytest`: **282 passed, 2 skipped** (the native Moho tests, NOT EXECUTED). Phase 2 adds 29 tests and every earlier test still passes.

| File | Tests | Covers (Step 14 items) |
|---|---|---|
| `test_p2_units.py` | 15 | Store immutability and tamper detection (1, 5); extension cannot touch originals or leave its region; refusal to create already-approved items; change map (10); rollback (16); linework cue and manual override (2); z-order cycle breaking; engine filtering of a greedy generator that paints everything (5); candidate size mismatch (3, 4); Replicate inpainting: candidates, seeds, cost and **cache hit with zero requests** (9); mask-convention inversion; negative prompt only where supported; **model drift refused before spending**; **budget refused before any upload** (8); 503/429 retry and failed prediction recorded (6, 7); wrong-size output rejected, not resized; crop constraints |
| `test_p2_pipeline.py` | 14 | Phase 1 inputs unmodified; package structure and all six schemas (from disk); source byte-identical, originals exact, 0 generated pixels in neutral (1, 5); full-canvas layers and SVGs (4); masks and provenance aligned (3, 10); known occlusion relations and ambiguity flags (2); non-flat regions flagged, not faked; hidden-region GT scores; joint pivots and bindings (11); poses and gap detection (12, 13); facial completeness and LiveControlSpec compatibility (14, 15); approval workflow and rollback (16); refusal to overwrite user folders; **reproducibility** (17) |

## 6. Real artwork evaluation

**None possible.** No reference image exists in the repository, and Phase 1 has no real segmentation. No unrelated
illustration was substituted. All numbers below are from the synthetic fixture.

## 7. Successful reconstructions (synthetic fixture, deterministic flat fill)

21 of 26 regions were filled; all are `pending_review`. Against exact ground truth they score:
* overall precision 0.9165, recall 1.0, mean colour error 8.9/255;
* **exact (colour error 0)**: skin under both brows, eyes, mouth and nose; sclera under both irises; forearm under both hands;
  thighs under the pelvis and under the shins;
* skull under the hair: error 5.9;
* torso under the upper arms: error 34.9, from outline continuation drawn where the GT has fill.

The neck regions show precision 0.43–0.53: the neck model extends higher under the head than the fixture's drawn neck.
Those pixels are hidden in the neutral pose, but they count against precision and are reported as such.

## 8. Failed or deferred reconstructions

| Region | Outcome | Why |
|---|---|---|
| `upper_arm_*__under__forearm_*` | `generative_required_no_provider` | surroundings mix sleeve and skin (not flat) |
| `shin_*__under__foot_*` | `generative_required_no_provider` | surroundings mix sock and skin |
| `pelvis__under__torso` | `not_needed_for_planned_motion` | no planned joint exposes it |
| `torso__under__forearm_*` | filled, but colour error ~90–105 | tiny slivers made almost entirely of continued outline |

Three bugs were found and fixed during development using the GT:
* outlines were continued *inside* continuous fabric (colour error 153);
* dark **fills** (navy shorts, black shoes) were treated as linework, which first gave wrong fill colours and then wrong
  front/back decisions;
* anti-aliasing noise made flat parts look textured.

## 9. Original-pixel preservation

Source byte-identical; all 25 originals pixel-hash verified; neutral composite of the originals **exactly** equals the
source; **generated pixels visible in the neutral pose: 0**; SSIM 1.0. A generator that tries to paint the whole crop
keeps only its region pixels, and the change outside is counted and discarded (tested). Each generated pixel has a
provenance mask and a change-map entry. Rollback restores the originals.

## 10. Joint and pose preview results

Pose gaps in pixels, originals only → with extensions:

| Pose | Originals only | With extensions |
|---|---|---|
| neutral | 0 | 0 |
| arm_45 | 2353 | 1496 |
| arm_90 | 3140 | 1497 |
| left_arm_90 | 2924 | 1209 |
| elbows_bent | 1245 | 1245 |
| walking | 3497 | 3061 |
| sitting | 1934 | 460 |
| head_tilt_left | 165 | 4 |
| head_tilt_right | 173 | 12 |

Safe ranges with extensions (gaps ≤ 25 px) are recorded per joint:
* neck ±20°, wrists ±30°, knees ±60°;
* shoulders, hips, elbows and ankles 0°: gaps remain, so they are flagged `needs_review`.

Recommended remedies:
* joint caps (generated SVGs, pending review) for shoulders and hips;
* a deformable mesh for elbows and knees;
* generative or manual art for the elbow and ankle regions.

Gap counts come from shape models (expected part outlines), so they are approximate; the close-up images show the real
defects. The "sitting" pose is a front-view approximation.

## 11. Facial reconstruction readiness

Eyes split into white and iris layers (neutral unchanged). Sclera and skin fills are done and pending review. Brows touching the
hair are flagged ("may continue under the hair, cannot be inferred"). **Missing, never generated:**
* closed eyes and eyelids;
* an open mouth with interior, teeth and tongue;
* mouth shapes (wide, narrow, pucker, smiles);
* three-quarter, up and down head views.

These sit in 12 switch-drawing slots that require labelled, approved artwork.

## 12. Live puppeteering readiness (`live_artwork_manifest.json`)

| Status | Controls |
|---|---|
| NEEDS_REVIEW (artwork reconstructed, unapproved) | head_roll, eye_look_x, eye_look_y, brow_raise_left, brow_raise_right, brow_frown |
| REQUIRES_NEW_ARTWORK | head_yaw, head_pitch, eye_blink_left, eye_blink_right, jaw_open, mouth_width, mouth_pucker, smile_left, smile_right |

**None is READY.** Moho SHOWTIME mappings stay separate and *proposed only*. No tracking or live puppeteering is implemented.

## 13. Cloud costs

**$0.00.** Paid calls made: one SAM 3 smoke attempt, rejected with HTTP 402 (nothing created). Schema reads are free. Inpainting
is ceiling-protected (default $1.00 cumulative, shared ledger) and has never been called.

## 14. Remaining technical risks

1. **Generative inpainting on real cartoon art is entirely unproven**: its style match, mask fidelity, and colour
   drift at seams.
2. Real-image occlusion: the linework cue assumes outlined art. Line-less, painterly or textured styles fall back to
   priors, which are flagged ambiguous.
3. The shape models (capsules, hulls, ellipse) are approximations. Wrong models produce generated pixels that are hidden
   but useless, and inaccurate gap measurements.
4. Shoulder and hip seams need joint caps or meshes. Rigid layers alone leave gaps at large angles.
5. Edge pixels still carry background colour (Phase 1 limitation).
6. Run time is about 100 s per character, dominated by joint sweeps; this is acceptable but unoptimised.

## 15. Recommendations for Phase 3

1. Clear the blockers first:
   * add Replicate credit;
   * supply the schoolboy reference;
   * run Phase 1 on it, then Phase 2 with `--provider flux-fill-dev` and a small ceiling;
   * review the candidates in `visual_review.html`.
2. Compare flux-fill-dev against flux-fill-pro on the non-flat elbow and ankle regions, then pin the winner.
3. Approve or correct pivots (`autorig review --set-pivot`) before any Moho binding work.
4. Commission or author the 12 missing switch drawings; never mark generated ones as original-accurate.
5. Only then: Moho import of bitmap originals, extensions under them and the SVG corrections, with Phase 0 verified first.

## 16. Verdict

**PARTIAL.** The implementation is complete and test-backed on synthetic fixtures. Generative inpainting is untested
(no credit), and no real artwork has been processed. **Phase 3 not started.**

Sources:
* [Replicate: flux-fill-dev](https://replicate.com/black-forest-labs/flux-fill-dev)
* [Replicate: flux-fill-pro](https://replicate.com/black-forest-labs/flux-fill-pro)
* [Replicate pricing](https://replicate.com/pricing)
* [Price per token: Flux Fill Pro](https://pricepertoken.com/image/model/black-forest-labs-flux-fill-pro)
* [Puter: flux-fill-dev](https://developer.puter.com/ai/black-forest-labs/flux-fill-dev/)
