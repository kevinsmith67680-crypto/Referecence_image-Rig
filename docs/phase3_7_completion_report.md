# Phase 3.7 completion report: intelligent hand selection, reference-guided animation, visual gesture validation

**Verdict: PARTIAL.**

What is complete:
- the hand-selection defect in the supplied shrug screenshot is reproduced, explained and fixed;
- the constraint-first selection is in place;
- the shrug variants and the shoulder check work on the real character;
- the reference pipeline runs end to end (MediaPipe locally) and is tested.

What keeps the verdict at PARTIAL:
- the acceptance criteria that need a person's artistic approval or artwork that does not exist (an open palm-up
  hand) cannot pass in code;
- no human reference footage was available (the references are synthetic);
- the optional VLM critic is untested integration code;
- Moho did not run.

Production readiness is not claimed.

Start: branch `claude/eloquent-cori-iv4ab1` at 043de36 (Phase 3.6). Phase 4 was not started.

## 1. Verified technical results

| | Result | Evidence |
|---|---|---|
| Shrug defect reproduced | The 3.6 frame re-rendered; switch state traced | `hand_selection/diagnosis/` |
| Cause | (1) Resolver `auto: True` dropped every performance hand request, so the automatic engine chose for the base preset; this also affected 3.6 pointing (20/156 side-frames) and the desk hand (55/86). (2) The relaxed drawing was turned ~80° from how it was drawn. (3) The cupped pair is stored on the wrong sides (rest rotation 182-183°; thumb on the far side). (4) The gesture never reached the selection engine. (5) Clavicles 13/15 raised the shoulder line (11-16 px, −4 to −6° slope). | `phase3_7_hand_selection_audit.md` |
| HandAssetMetadata v1 | 18 drawings: measured and inspected fields with provenance, JSON Schema, user corrections (`handmeta.*`, schema v9) | `analysis/hand_asset_metadata.json`, `inventory_sheet.png` |
| Handedness | Hard constraint; cupped pair reassigned from the drawings, not the names | tests, inventory |
| Palm view | HandOrientationAnalyzer: a flat rotation never turns a camera-facing palm up; palm-up only with a visible palm | tests, `invalid_metadata.json` |
| Rotation tolerance | A drawing turned beyond what it tolerates is rejected (the 80° relaxed hand) | tests |
| Approval modes | Production: approved + original; review: pending labelled; rejected never; missing gives NEEDS_ARTWORK; fallbacks reported | tests, regression sheet |
| Wrist alignment | Attached, centred, no duplicate cuff, rotation, proportion; arm unchanged across variants. 38 of 40 side-renders pass. 2 pending drawings fail "centred" (thumbs-up 20 px, palm-down 34 px off the cuff centre) and are reported | `regression/cases.json`, `wrists.png` |
| Shrug variants | SUBTLE supported; EXPRESSIVE uses palm-up drawings in review and becomes SHRUG_SUBTLE in production (substitution reported) | `shrug_comparison.png`, tests |
| Shoulders | Shrug rise 2-6 px (was 11-16), slope level (was rising outward) | `shoulders_closeup.png` |
| Reference import | MP4/MOV (ffmpeg), GIF, image sequences, JSON, BVH; permission required; quality flags | tests (video encode/decode, BVH, refusal) |
| Body landmarks | MediaPipe Pose, local CPU: 0.068 s/frame; T-pose reference detected (test); 44-frame round trip on a rendered video | `reference_motion/evidence/roundtrip.*` |
| Retargeting | Angle deltas on the character neutral; keys at phase boundaries; hand actions via the constrained engine. Pointing abduction RMS 2.1° | `comparison_*.png`, tests |
| Proportions | Bone lengths and pivots unchanged (test) | tests |
| Feasibility | Shrug forearms REQUIRES_ALTERNATIVE_ARTWORK; walk REQUIRES_NEW_VIEW; never stretched | tests, comparisons |
| Critique | Rule-based defects (region, issue, severity, suggestion, requiresArtwork, control); bounded revision loop proposals; never approves | `revision_loop.png`, tests |
| Editor | Hand Inspector and Ref Motion tabs, API, `reference.*` ops; browser e2e steps 6j/6k | `docs/phase3_7/screenshots/` |
| Moho metadata | Switch layers, wrist attachment (px and Moho frame), gesture Actions with keyframes, production switch sets, overrides, needed art: `proposed_unverified` | `analysis/phase37_moho_metadata.json` |
| Benchmarks | Measured on this machine (section 5) | `hand_selection/benchmark.json` |

## 2. Acceptance criteria (step 25)

| # | Criterion | Status | Note |
|---|---|---|---|
| 1 | Shrug hand-selection defect reproduced | PASS | Diagnosis renders and trace |
| 2 | Cause identified | PASS | Five causes, measured (above) |
| 3 | Hand orientation metadata implemented | PASS | HandAssetMetadata v1 + schema + editor corrections |
| 4 | Handedness constraints enforced | PASS | Hard constraint; tests; cupped reassignment |
| 5 | Incompatible palm views rejected | PASS | Analyzer + tests (no flat-rotation fakes) |
| 6 | Wrist alignment remains stable | PASS with findings | Arm unchanged across variants; 2 pending drawings off-centre at the cuff, reported for review |
| 7 | Approved artwork selected correctly | PASS (mechanism) | Only the original hands are approved today; approved drawings win in tests |
| 8 | Pending artwork remains review-only | PASS | Production never shows pending (tests, inspector, regression sheet) |
| 9 | Missing variants produce actionable warnings | PASS | NEEDS_ARTWORK / NEEDS_APPROVAL with the drawing to make |
| 10 | Shrug no longer uses visually inappropriate hand selection | PASS (technical) / visual judgement pending | No rotated camera-facing hands; palm-up only from palm-up drawings; production falls back to SUBTLE. Whether it *looks* right is the user's call |
| 11 | Shoulder silhouette not excessively inflated | PASS for the shrug | Rise 2-6 px (≤ 8 px check); rounded cap at 20-30° abduction and flatter arms-down line remain (artwork needed) |
| 12 | Reference video import works | PASS | ffmpeg decode/encode tested; real MP4 imported |
| 13 | Body landmarks can be extracted | PASS (on a cartoon render) | MediaPipe ran locally; no human footage was available to test |
| 14 | Motion retargeted to the existing skeleton | PASS | Through the one PerformanceDirector pipeline |
| 15 | Character proportions unchanged | PASS | Bone lengths test |
| 16 | Reference-guided poses respect artwork feasibility | PASS | Depth → REQUIRES_*; walk not claimed |
| 17 | Visual critique produces actionable reports | PASS (rule-based) | VLM provider untested (no endpoint) |
| 18 | Editor exposes hand-selection decisions | PASS | Hand Inspector (browser e2e 6j) |
| 19 | Existing IK and garment functionality intact | PASS | Full suite (section 4) |
| 20 | Full regression tests pass | PASS | Section 4 |
| 21 | Real-character visual evidence generated | PASS | Sections in the visual report |
| 22 | User artistic approval remains separate | PASS | Nothing approved in code; the critic never approves; revisions are proposals |

The criteria pass technically. The phase stays **PARTIAL** because the gesture the screenshot was about (the palms-up
shrug) has no approved, anatomically correct palm-up drawing. In production it is **NEEDS_ARTWORK**, and the review
preview uses pending cupped drawings, which are an approximation of an open palm-up hand. No visual result has been
approved by a person.

## 3. Visual improvements (to be judged by the user)

- Shrug: no sideways "presenting" hands; palm-up hands only from palm-up drawings, on the correct sides; shoulders
  2-6 px up instead of 11-16 px.
- Pointing and desk hands now actually show the requested drawings (the 3.6 renders showed relaxed hands).
- Production renders never show pending drawings.

## 4. Tests

- New: `tests/test_p37_hands.py` (27) and `tests/test_p37_reference.py` (18). They cover handedness, palm orientation,
  wrist alignment, gesture compatibility, approval gating, fallback, overrides, contacts (hand on desk), purity in
  time, motion extraction, retargeting, feasibility, reference comparison and persistence (ops, store, HTTP).
- Changed:
  - schema-version pins 8 → 9, as each phase does;
  - the synthetic selection library in `test_hand_replacement.py` gained explicit metadata (what each test drawing
    shows), because the engine now requires metadata for palm-specific actions. No assertion was weakened;
  - one assertion in the e2e hand-replacement step changed meaning. It used to expect the left thumbs-up drawing on the
    relaxed preset's hanging arm. Even after the maximum wrist aim that drawing stays turned 54° from how it was drawn
    (tolerance 45°), so its thumb would not point up. The step now asserts that the engine follows the intention,
    rejects the tilted drawing with the rotation reason and reports it. The positive case (forearm out, thumbs-up
    shown) is in the regression evidence and the unit tests.
- **Backend, full suite:** 589 passed, 2 skipped (the native Moho tests, NOT EXECUTED), 1 failed (19 min 38 s).
  The failure was a real validation gap (a metadata key `../etc` was accepted), fixed during the run and re-verified.
  The suite had loaded the code before several late fixes (production gesture substitution, raised-arm shoulder
  check, symmetric shoulder chord, metadata-key validation), so every affected file was re-run on the final code:
  `test_p37_hands`, `test_p37_reference`, `test_p36_performance`, `test_hand_replacement`, `test_p3_editor`,
  `test_p35_artistic`, with **139 passed**. Net result: 591 passed (546 before Phase 3.7, plus 45 new), 2 skipped,
  0 failing.
- **Frontend:** vitest 11 passed. `tsc` clean; `editor/dist` rebuilt.
- **Browser e2e** (Playwright, headless Chromium, real package copy, steps 6j Hand Inspector and 6k Ref Motion):
  **pass** (1 test, 9 min 3 s; screenshots in `docs/phase3_7/screenshots/`). Two e2e runs failed on the way; both were
  fixed in code and re-run: the pose API did not expose the new decision fields, and the Hand Inspector step
  reset a switch that a later Phase 3.2 persistence check needs (it now undoes its own ops). A layout bug (the new
  panels overflowed the side panel) was found in the screenshots and fixed.
- **Native Moho tests:** 2 NOT EXECUTED (Moho not available).

## 5. Benchmarks (cloud sandbox CPU, x86_64; not the user's M1)

| operation | time |
|---|---|
| hand selection, both sides (constraints + scoring + explanation) | 0.44 ms median |
| skeleton solve with hand selection (performance frame) | 5.8 ms median |
| full render (artwork, correctives) | 4.5 s |
| reference analysis (2.8 s clip, 85 frames) | 5.7 ms |
| retarget | 0.55 ms |
| rule-based critic per rendered frame (incl. wrist seams) | 337 ms |
| MediaPipe Pose (full) per frame, CPU | 68 ms (import only, never during interactive animation) |
| ffmpeg decode, 44 frames 720p | 0.27 s |

No cloud inference is used anywhere in Phase 3.7. Reference data is cached in the library (track + analysis), so
nothing is re-estimated.

## 6. Pending approvals (user)

- 16 sleeve-free hand drawings: `handstore:schoolboy_hands_sleeveless/*@v1`. In particular, review:
  - the cupped pair's side reassignment (assistant inspection, medium confidence; reversible in the Hand Inspector);
  - the closed-outline wrist pivots (cupped, pointing, gripping);
  - the thumbs-up and palm-down cuff centring.
- Every pose and performance in the evidence (none is approved).
- Revision-loop proposals (accept or reject in the Ref Motion tab).

## 7. Remaining production blockers

1. **Missing artwork:**
   - an open palm-up hand for each side (SHRUG_EXPRESSIVE, explaining with palms up);
   - a relaxed/partially closed hand;
   - foreshortened forearms (reference shrug);
   - a sagittal leg view (walking stride);
   - optional sloped arms-down shoulders.

   Earlier phases' missing drawings remain (deep elbow, seated, 3/4 torso, ...).
2. **Approvals:** all stored drawings are pending.
3. **Human reference footage:** the pipeline is tested on synthetic and rendered references only; a real
   permission-cleared clip should be imported on the user's machine (`autorig reference-import`).
4. **VLM critic:** integration code only, not tested.
5. **Moho:** the Phase 0 native verification is still outstanding, all Moho metadata is `proposed_unverified`, and
   Phase 4 has not started.

## 8. Deliverables

Docs:
- `docs/phase3_7_implementation_plan.md`;
- `docs/phase3_7_hand_selection_audit.md`;
- `docs/phase3_7_reference_motion_architecture.md`;
- `docs/phase3_7_visual_quality_report.md`;
- this report.

Generated, under `projects/schoolboy_tpose/character_package/`:
- hand metadata inventory: `analysis/hand_asset_metadata.json`, `natural_pose/hand_selection/inventory/`;
- diagnostics and corrected shrug comparisons: `natural_pose/hand_selection/diagnosis/`;
- wrist and shoulder close-ups: `diagnosis/wrists_closeup.png`, `shoulders_closeup.png`, `regression/wrists.png`;
- regression: `regression/` and `results.json`, `continuity.json`, `invalid_metadata.json`;
- reference comparisons: `natural_pose/reference_motion/evidence/`;
- benchmarks: `natural_pose/hand_selection/benchmark.json`;
- Moho metadata: `analysis/phase37_moho_metadata.json`;
- the reference library: `motion_references/` (three synthetic references plus the rendered round trip);
- remaining artwork requirements: section 7.

Code:
- `src/autorig/handmeta.py`, `handaudit.py`, `handselect_evidence.py`;
- `motion/handselect.py` (refactor), `motion/handcheck.py`;
- `performance/{resolver,director,gestures,scenarios,shoulders,evidence}.py`;
- `refmotion/`;
- editor (`HandInspectorPanel.tsx`, `ReferencePanel.tsx`, API, ops v9);
- CLI: `hand-audit`, `hand-selection-evidence`, `reference-models`, `reference-import`, `reference-evidence`,
  `phase37-bench`.
