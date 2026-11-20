# Phase 2 implementation plan: artwork reconstruction and animation-ready assets

## 1. Inspection findings (2026-11-20)

| Item | State | Consequence for Phase 2 |
|---|---|---|
| Phase 0 (Moho rig PoC) | Implemented, **not verified in Moho** (no native report) | Nothing Moho-native is built in Phase 2 |
| Phase 1 (segmentation) | Implemented, **PARTIAL**: real SAM 3 inference blocked (HTTP 402), no real reference image | Phase 2 is validated on the **synthetic labelled character** only; real-image reconstruction is **not claimed** |
| Replicate credit | Re-checked: still **HTTP 402 Insufficient credit** | Generative inpainting cannot be tested → Phase 2 verdict is capped at **PARTIAL** |
| Reference image | Not in the repository | Real-artwork evaluation **BLOCKED** |
| Reusable components | `phase1.masks`, `compositor`, `schemas` pattern, `segmentation.replicate_client` / `budget`, `synthetic` (has the full drawn shapes *behind* every occlusion = exact hidden-region ground truth) | Reused, not replaced. Phase 1 outputs are read-only inputs |

## 2. Architecture

```
Phase 1 character_project (read-only)
   │ anatomy.json, face.json, masks/body, masks/face, artwork/bitmap, source/original.png
   ▼
OcclusionAnalyzer ── geometry (contact zones, completed part shapes) + linework ownership cue + priors
   │                 → occlusion graph (front/back, confidence, ambiguous) + reconstruction regions
   ▼
ArtworkStore (OriginalPixelPreservation)
   │  A original (immutable, SHA-256) · B generated (separate layers + provenance masks)
   │  C approved corrections · D working (scratch, never shipped)
   ▼
BodyReconstructionEngine / FacialReconstructionEngine
   │  1) deterministic flat fill + outline continuation (no AI) for flat regions
   │  2) ReconstructionProvider (pluggable; Replicate inpainting adapters) for complex regions
   │  every result filtered so it can only add pixels inside the reconstruction mask, never on originals
   ▼
JointPreparationEngine ── pivots, attachments, padding, z-order, range-of-motion test, binding mode
PosePreviewEngine ────── rigid 2D poses around pivots, gap/overlap/clipping/contour diagnostics
VectorCorrections ────── editable SVG joint caps / guides (generated, never "exact")
LiveArtworkManifest ──── per-control layers, switch drawings, ranges, readiness, approval
ReconstructionQualityEvaluator ── metrics, comparisons, heatmaps, joint close-ups, pose sheets
Review workflow ──────── approvals.json + reports/visual_review.html + `autorig review`
   ▼
Phase 2 character_project (new directory; Phase 1 project untouched)
```

## 3. Reconstruction provider selection (schemas inspected via the Replicate API)

| Model | Mask convention | Negative prompt | Candidates/call | Resolution | Seed | Price |
|---|---|---|---|---|---|---|
| `black-forest-labs/flux-fill-dev` | **white = inpaint** | no | 1–4 | `megapixels=match_input`; dims rounded to ×32, max 1440² | yes | not shown on page (third-party ≈ $0.04/image) |
| `black-forest-labs/flux-fill-pro` | white = inpaint, same size as image | no | 1 | input size | yes | $0.05/image |
| `ideogram-ai/ideogram-v2` | **black = inpaint (inverted)**, mask resized | yes | 1 | input size | yes | not verified |
| `bria/genfill` | undocumented ("Mask file") | yes | 1 | input size, `preserve_alpha` | yes | not verified |

All four are Replicate **official models**, so their version list is hidden and they cannot be pinned by version.
Mitigation ("pin by verification"): the selected model's `latest_version.id` and an input-schema hash are recorded.
Before every paid call they are re-read, and the call is refused if either changed.

**Initial candidate: `flux-fill-dev`** (configurable, untested):
* open weights, so it can be reproduced off-platform;
* up to 4 candidates per call, with a seed;
* `match_input` resolution and PNG output (lossless), giving pixel-aligned results;
* LoRA support for a future illustration-style adapter;
* a documented mask convention.

`flux-fill-pro` is the quality alternative, with a single output per call. Ideogram v2 is kept as an adapter mainly
to exercise the inverted-mask path. Selection quality on 2D cartoon art is **unknown until inference runs**.

## 4. Key rules

1. The source file and every Phase 1 extracted layer are immutable (SHA-256 verified).
2. Generated pixels live on separate extension layers *under* the original layer. Any generated pixel that falls
   on an original opaque pixel, or outside the reconstruction mask, is discarded and counted.
3. Deterministic geometry and colour fill comes first; AI only for regions flagged complex. Nothing generated is
   "approved" until a human approves it.
4. No claim about real-image quality, Moho rigging or live tracking.

## 5. Risks

| Risk | Mitigation |
|---|---|
| Inferred z-order wrong, so generated pixels show in the neutral pose | The neutral-composite check fails loudly; ambiguous edges are flagged; manual overrides |
| Flat fill on textured or shaded parts looks wrong | Variance test routes such regions to `requires_generative` (pending, not faked) |
| Unreliable Phase 1 pivots | Propagated to joints; `joint_preparation.json` marks them `needs_review` |
| Official-model drift on Replicate | Verification pinning; refusal on change |
| Synthetic success mistaken for real-art success | Every report separates synthetic fixture results from real-image results (blocked) |

## 6. Acceptance tests (automated)

* Source, and every original layer, byte- and pixel-identical after processing and after rollback.
* Occlusion graph: valid schema, known front/back pairs on the fixture, ambiguity flags, overrides applied.
* Reconstruction masks aligned (same canvas, inside the expected shape, disjoint from originals).
* Provider: errors, retries, budget refusal before any request, cache, provenance, mask-convention inversion, discard
  of changes to protected pixels.
* Hidden-region reconstruction on the fixture against exact GT (coverage, false additions, colour error).
* Joint pivots validated; pose previews rendered; gaps detected (and shown to be reduced by extensions).
* Facial completeness, LiveControlSpec compatibility, approval workflow (unreviewed ≠ approved), undo/rollback.
* Reproducible fixture processing (identical outputs on re-run).
