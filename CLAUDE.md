# CLAUDE.md — AutoRig Studio

AutoRig Studio converts a single cartoon character reference image into a
faithful, editable **Moho Pro 14** character rig.

## Current phase and gate

- **Phase 0 (Moho rigging proof of concept) is implemented but NOT locally
  verified.** Its cloud verdict is PARTIAL; see `docs/phase0/completion-report.md`.
- The user explicitly started **Phase 1** while Phase 0 was still unverified, asking that the
  limitation be carried forward. Phase 1 is implemented; verdict **PARTIAL** (`docs/phase1_report.md`).
  Replicate credit and the user's reference (`references/schoolboy/`) arrived 2026-11-20; the first real-artwork
  runs are in `docs/real_artwork_test.md` (run 2, T-pose, is the current one). Remaining: Phase 0 native
  verification, region-specific inpainting prompts, and human review of generated pixels.
- The user explicitly started **Phase 2** (reconstruction) on synthetic fixtures. It is implemented; verdict
  **PARTIAL** (`docs/phase2_report.md`; real-art run in `docs/real_artwork_test.md`): FLUX inpainting ran on real
  art but nothing is human-reviewed, eye-white inpainting failed, and Phase 1's body split is not rig-ready.
- **Phase 2 natural-pose refinement** (user-requested) is done: anatomical skeleton, local skinned joints,
  IK/balance solver, 12 presets (`docs/phase2_natural_pose_refinement.md`). Corrective artwork (sheet hands,
  pointing hand, palm-up approximation, per-pose armpit gussets) is registered as `corrective:*` review items,
  pending review. Statuses: 5 OK_PENDING_REVIEW, 4 NEEDS_REVIEW, 3 NEEDS_ARTWORK. Preview renders only; Moho
  not run. Corrective artwork is never approved in code.
- The user explicitly started **Phase 3** (interactive artwork editor, hybrid assembly, Moho-ready asset package).
  It is implemented; verdict **PARTIAL** (`docs/phase3_report.md`): editor tested in headless Chromium on the real
  T-pose package (Playwright, screenshots in `docs/phase3/screenshots/`), export validated with 0 errors but
  `production_ready: false` (25 approvals pending, facial/corrective artwork missing, Moho not run).
- The user explicitly started **Phase 3.1** (natural motion, anatomical rigging, clothing deformation). It is
  implemented; verdict **PARTIAL** (`docs/phase3_1_natural_motion_report.md`, audit in `docs/phase3_1_motion_audit.md`):
  planner/solver/garment engine/evaluators in `src/autorig/motion/`, editor Pose tab uses it, evidence in
  `projects/schoolboy_tpose/character_package/natural_pose/motion/`. Visual quality is not approved by anyone.
- The user explicitly started **Phase 3.2** (IK, switch layers, articulated controls). It is implemented; verdict
  **PARTIAL** (`docs/phase3_2_ik_switch_report.md`, plan in `docs/phase3_2_implementation_plan.md`): two-bone IK,
  arm/leg controllers, foot contacts, switch groups, hand library, correction triggers, rig controls spec, editor IK
  and Switches tabs; evidence in `projects/schoolboy_tpose/character_package/natural_pose/ik_switch/`. Hand variants
  are all pending review; relaxed/gripping/palm-down/partially-closed hands and every elbow/knee correction drawing
  are missing.
- The user asked to integrate their **schoolboy hand pack** (16 AI-generated concept drawings,
  `references/schoolboy/hand_pack/`) as a character-scoped **Hand Pose Store**. Done; verdict **PARTIAL**
  (`docs/hand_pose_store_integration.md`): imported into `projects/schoolboy_tpose/character_package/hand_store/`, all
  drawings pending review, evidence in `hand_store/evidence/`.
- The user asked to **replace the hands with a sleeve-free library** and add natural hand selection. Done; verdict
  **PARTIAL** (`docs/hand_library_replacement_report.md`): 16 hands extracted from `references/schoolboy/hand_pack_v2/`,
  imported as `schoolboy_hands_sleeveless` (the sleeve-attached v1 library and the Phase 2 sheet hands are archived,
  restorable), `WristAlignmentController`, `NaturalHandSelectionEngine`; evidence in `hand_store/evidence_v2/`. All
  drawings pending review; left pointing is a mirrored copy (the sheet has two right pointing hands).
- The user explicitly started **Phase 3.3** (clothing distortion prevention, garment rigging). It is implemented;
  verdict **PARTIAL** (`docs/phase3_3_clothing_deformation_report.md`, audit in `docs/phase3_3_clothing_audit.md`):
  garment parts, aligned shoulder pivots, weight meshes, rigid segments over joint patches, correction controller,
  distortion evaluator, editor Weights tab; evidence in `projects/schoolboy_tpose/character_package/natural_pose/garments/`.
  Not reviewed by a person; gussets, deep elbow/knee, seated, 3/4 torso, raised-shoulder collar and the shoe top under
  the hem are missing drawings.
- The user explicitly started **Phase 3.4** (corrective artwork, pose-space correction, visual quality recovery).
  It is implemented; verdict **PARTIAL** (`docs/phase3_4_corrective_artwork_report.md`, baseline in
  `docs/phase3_4_baseline_audit.md`). Generated assets:
  - armpit gussets;
  - shoe tops;
  - elbow and knee caps;
  - edge decontamination.

  They are geometric, built from the character's own pixels, stored in
  `projects/schoolboy_tpose/character_package/artwork/correctives/`, and all **pending review**. Evidence is in
  `natural_pose/correctives/`. Still NEEDS_ARTWORK: the deep elbow, the pointing hand (pending), sitting and
  leaning forward. Optional drawings: squat knee folds, running knee drive, raised-shoulder collar.
- The user explicitly started **Phase 3.5** (natural posing, artwork-aware feasibility, alternative drawings, dynamic
  layer order, artistic quality control). It is implemented; verdict **PARTIAL** (`docs/phase3_5_artistic_animation_report.md`,
  audit in `docs/phase3_5_artistic_quality_audit.md`). Code is in `src/autorig/artistic/`; evidence in
  `natural_pose/artistic/`; the acting test is in `natural_pose/artistic/acting_test.gif`. No pose or drawing has been approved
  by a person; the previews use pending hand drawings (labelled).
- **Do not start Phase 4** (native Moho import/rigging of the exported package) automatically. It needs the Phase 0
  native verification, the reviews, and the missing artwork.

## Phase 2 rules (reconstruction)

- Original pixels are category A and immutable (`phase2/store.py`). Generated pixels go only on separate extension
  layers *under* their original, inside a reconstruction mask, with a provenance mask. Never write generated pixels
  into an original or mark them approved in code; only `autorig review` approves.
- Deterministic flat fill first. Generative inpainting only for non-flat regions, through `ReconstructionProvider`.
  Inpainting adapters declare their mask convention (FLUX: white = inpaint; Ideogram: black = inpaint).
- Official Replicate models cannot be version-pinned: keep the pin-by-verification check (refuse on drift).
- Never present synthetic-fixture results as real-artwork evidence.
- Natural posing: pixels move by a position-based home bone (not by layer); joints blend by angle
  interpolation inside per-joint bands; the identity pose must stay bit-exact. Poses that need artwork the
  reference lacks are labelled NEEDS_ARTWORK, never passed. Presets are anatomical (rest-pose independent).
- Replicate inputs go inline as data URIs (the SAM 3 model rejected Files API URLs). Small crops are upscaled
  by an integer factor for the model and area-averaged back.

## Phase 3 rules (editor)

- Edits are operations in `<package>/editor/project.json` (event log + cursor, versions, schema migration). Never
  write the source, Phase 1 layers or Phase 2 originals/extensions; derived pixels are computed from the source.
- Masks keep the partition: a mask edit moves source pixels between layers or leaves them unassigned (reported as a
  neutral gap). The neutral composite must stay equal to the source; `fidelity()` and gate 9 check it.
- Pose preview reuses the Phase 2 solver (`posesvc.py`); never add a second solver. Joint corrections are metadata
  for Phase 4, not Moho Smart Bones. Live controls are never live-ready in Phase 3; no webcam tracking.
- Approvals only through user actions (`approval.set` from the UI or API). `production_ready` stays false while a
  mandatory approval or artwork is missing. No `.moho` file until the Phase 0 importer is verified in Moho.
- Export replaces only an earlier AutoRig export or an empty directory; the API writes only inside
  `<package>/editor/exports/`.
- After changing `editor/src`, rebuild and commit `editor/dist` (`cd editor && npm run build`).

## Phase 3.1 rules (motion)

- Keep the four stages separate: `planner.MotionPlanner` (intent: targets, contacts, gait phase) ->
  `solver.SkeletalPoseSolver` (FK/IK, limits, pelvis from contact, balance) -> `garments.ArtworkDeformationEngine`
  (garment regions, rotation-dependent bands, hem lag, layering) -> `quality`/`feasibility` evaluators.
- Never scale a bone (no axial foreshortening). Depth motion on front-view art (knee drive, sagittal lean, sitting)
  is NEEDS_ARTWORK with a named asset and a fallback, never a distorted approximation.
- Pending-review artwork is never approved: feasibility treats it as missing; it may only be *previewed*
  (`allow_pending`). Run arms use the approved open-hand style unless fists are approved.
- Gaits are phase-driven (0..1, right contact at 0). Swing knees follow a schedule (no IK snap); pelvis height comes
  from loaded feet. Cycle checks (loop closure, continuity spikes, foot sliding, penetration, coordination) must pass.
- Quality thresholds live in `motion/quality.py` `THRESHOLDS` and must be documented in the Phase 3.1 report. Visual
  quality is never scored automatically (`artistic_review_required` is always true).
- Fluid gaits (default): spring follow-through tables (`motion/secondary.py`, exact periodic steady state), eased
  timing and hand arcs; `fluid=False` must keep reproducing the plain cycle for comparison. Secondary motion never
  moves a planted foot; garment terms (hem lag, hair sway) must stay continuous fields (no seams).
- `autorig motion <package>` regenerates the committed evidence (contact sheets, cycles, close-ups, animations,
  hand paths, results.json).

## Phase 3.2 rules (IK and switches)

- One solver: `motion/ik.py` `TwoBoneIKSolver` is used by `SkeletalPoseSolver` for legs and arms and by the arm/leg
  controllers. It only rotates bones: unreachable targets are clamped and reported (`clamped_max_reach`/`min`).
- User IK lives in the editor project (`ik` state, ops `ik.set`/`ik.reset`); `MotionEngine.apply_ik` turns it into
  intent before the skeletal solve (stage 2). FK/IK switching goes through `MotionEngine.match_ik` (no jumps).
- Arm clavicle elevation is the fixed point of the shoulder rhythm (bracketed root, overhead abduction unwrapped),
  so an IK arm that reproduces an FK arm gets the same clavicle.
- Switch groups (`motion/switches.py`): user choice > keyframe > automatic preset choice > the original drawing.
  Pending drawings are previewed only with `allow_pending`; missing ones are NEEDS_ARTWORK with a named fallback.
  Offsets are rigid (translate/rotate about the wrist pivot), never scaled. Wrist alignment is measured on the
  *visible* pixels (the sleeve cuff covers the wrist).
- A deep-elbow bend above 125 deg without an elbow drawing makes the pose NEEDS_ARTWORK (band deformation alone
  breaks the fold there; it is clean at ~120 deg); other triggered corrections without drawings are optional assets.
- `autorig.rig_controls` v1 (`motion/rigctl.py`, schema in `data/schemas/rig_controls.schema.json`, kept in sync by
  a test) is validated before export (manifest 1.1.0). Moho mappings stay `proposed_unverified`; live inputs stay
  unassigned (no Moho input names are invented). No webcam tracking.
- `autorig ik-demo <package>` regenerates the committed Phase 3.2 evidence.

## Phase 3.3 rules (garment rig)

- `MotionEngine(garment_mode="structured")` is the default; `"legacy"` keeps the Phase 3.1 band deformer for
  before/after only. One skeleton for solving and rendering: `garmentrig.structure.align_pivots` moves the shoulder
  pivots (0.55 h toward the armhole top) unless the user edited the bone; `Bone.axis_offset` keeps the anatomical axis
  for abduction/flexion/rhythm/FK/readings, the IK uses the pivot line with per-side limits (`ik.solve_axis_aware`).
- Garment parts (`GarmentStructureAnalyzer`) decide which bones may move a pixel (RIGID / LIMITED_DEFORMATION /
  FLEXIBLE); semantic parts, not Phase 1 cuts. Weights (`garmentrig.weights`): per-layer grid meshes, >= 0, sum 1,
  restricted per part, rigid parts locked, never distance-only. User edits are `weights.*` project ops (schema v5),
  replayed deterministically; the source and layers are never written.
- `StructuredDeformer`: rigid segments (sleeves, trouser shin) over the weighted joint patch; rigid fits for the badge,
  buttons, tie, pocket flaps; generated pixels drawn beneath the layer that hid them at rest; joint-patch pixels
  beyond `max_patch_stretch` are not drawn (reported conflict), never smeared. The identity pose stays bit-exact.
- `GarmentCorrectionController` extends the Phase 3.2 controller (one switch engine): angle ranges, eased blend,
  attachment, layer order, approval, missing dependencies; drawings are never approved in code.
- `GarmentDistortionEvaluator`: per part and garment PASS/WARNING/NEEDS_ARTWORK/FAIL, thresholds in
  `evaluate.THRESHOLDS` (documented in the report); no global score; never claims visual quality.
- Moho: `garmentrig/moho.py` matrix is `proposed_unverified`; per-vertex weights are not assumed to import; no exporter.
- `autorig garment-evidence <package>` regenerates `natural_pose/garments/` (~3 min).

## Phase 3.4 rules (corrective artwork)

- Corrective assets live in the append-only `CorrectiveArtworkStore` (`garmentrig/correctives.py`):
  - versioned PNGs in `<package>/artwork/correctives/<category>/`;
  - the index with hashes and provenance in `analysis/corrective_assets.json`.

  Never overwrite or approve them in code. Approval is `approval.set` on `corrective:<id>@v<N>`, one per version.
  Provenance values: GEOMETRIC_CORRECTION, AI_GENERATED, ARTIST_EDITED, then APPROVED or REJECTED. Pending assets are
  only previewed (`allow_pending`, or `corrective_mode="all"`).
- A corrective never covers an original pixel:
  - Every corrective piece is composited *beneath* original pixels.
  - Joint caps may only go over empty pixels and their own limb's joint patch.
  - Gap fills go beneath everything.
  - `CorrectiveQualityEvaluator` checks it by diffing renders with correctives off and on (`protected_violation_px`
    must be 0).
  - The identity pose stays bit-exact with correctives off.
- Generators build artwork from the character's own pixels: geometry, sampled colours, the measured outline. They
  never call an AI model; AI inpainting would need explicit review. Missing view-specific drawings stay
  NEEDS_ARTWORK (the planner records them and never invents them).
- Edge decontamination: derived layers only. Only silhouette pixels lighter than the outline inside them change.
  Edge pixels with another layer beneath them at rest are skipped. Composited over the paper it reproduces the source.
- Geometry metrics (gaps, clipped pixels) are measured with the decontamination off: its edges are semi-transparent by
  design. Fringes are measured separately (`light_specks`).
- `PoseSpaceCorrectionEngine`: neutral pose weight 0, eased ramps, normalised RBF keys, exclusive groups, artist
  overrides, extrapolation flagged. The user's per-asset settings are `correctives.set|mode|reset` ops (schema v6).
- `autorig corrective-evidence <package>` regenerates `natural_pose/correctives/` and the analysis/report JSON
  (~10 min); `--regenerate` adds new asset versions.

## Phase 3.5 rules (artistic posing)

- Poses are planned as whole bodies first (`artistic/poses.py` `ArtisticPose` -> `ArtisticPosePlanner`):
  - weight leg, contrapposto, line of action, head counterbalance, per-arm gestures with their hand action, view.

  They reach the one solver only through `MotionEngine.pose(intent_fn=...)`, which reshapes the preset's intent in
  stage 1. Never add a second solver and never scale a bone.
- Silhouette warnings (`SilhouetteQualityEvaluator`) and animation checks (`ArtisticAnimationEvaluator`) are kept
  separate from the geometric garment evaluator. None of them claims artistic quality: `artistic_review_required` is
  always true.
- Candidates (`PoseCandidateGenerator`) are ranked by an explicit ordering:
  - feasibility class;
  - no high-severity silhouette warning;
  - silhouette penalty;
  - deformation risk.

  The ranking is a recommendation; the artist's choice (`artistic.select`) wins.
- Feasibility (`ArtworkFeasibilityEngine`) runs on the skeleton-only solve, before deformation. Classes: SUPPORTED,
  SUPPORTED_WITH_CORRECTIONS, REQUIRES_ALTERNATIVE_ARTWORK, REQUIRES_NEW_VIEW, UNSUPPORTED.
  - Pending drawings are listed as `pending_approval`, never counted as approved.
  - Depth motion (forward lean, stride, knee drive) is REQUIRES_NEW_VIEW, rendered only as a labelled fallback.
- Alternative drawings (`artistic/alternatives.py`):
  - append-only store in `<package>/artwork/alternatives/`;
  - approvals via `approval.set` on `alternative:<id>@v<N>`;
  - full-canvas, never scaled;
  - selected through the same variant-layer substitution as hand switches (`MotionEngine.pose(layer_variants=...)`),
    with no second switch engine.

  Missing drawings become artwork requests (`PoseAwareArtworkGenerator`; the default provider calls no model).
- Dynamic draw order (`DynamicLayerOrderController`, `MotionEngine.pose(layer_order=..., depth=...)`):
  - rules on the posed skeleton with hysteresis; the neutral order is unchanged;
  - sublayer splits by garment part partition a layer exactly.
- Art-directed joint corrections (`artistic/joint_art.py`):
  - procedural `joint_fold` corrective assets that supersede the Phase 3.4 caps;
  - fold strokes are an overlay allowed only over the same limb's own fabric, reported as `fold_overlay_px`;
  - caps and folds are applied after all pieces are drawn;
  - `corrective_mode="off"` also disables the Phase 2 gussets.
- Transitions (`artistic/transitions.py`) are deterministic: easing, leads and trails, anticipation, overshoot and
  settle, held hand switches. There is no cloth simulation and no head turn without turned-head drawings.
  - Easing is quintic; anticipation and overshoot are C2 bumps (a plain sine starts with a velocity jump).
  - The continuity verdict comes from `ArtisticAnimationEvaluator.continuity_check` (parameter velocity jumps, solver
    clamp and balance-saturation events), not from acceleration peaks, which are reported for information only.
  - Keep key weights below the static-balance saturation (about 0.73 on the schoolboy): beyond it the pelvis stops
    mid-move.
- Editor Artistic tab: project schema v7 `artistic.*` ops; feedback becomes constraints through a phrase table
  (`artistic/feedback.py`), not a language model.
- `autorig artistic-evidence <package>` regenerates `natural_pose/artistic/`, `reports/artistic_quality.json` and
  `analysis/{view_manifest,limb_configurations}.json` (~15 min).

## Hand Pose Store rules

- Imported hand drawings live in `<package>/hand_store/` (append-only: `index.json` with hashes and provenance, packs
  byte for byte, replacement PNGs as new versions). `autorig hand-import` / the Hands tab validate the whole pack
  (manifest, unique ids, alpha, canvas size, complete side/pose pairs, safe zip paths) and reject it with
  actionable errors otherwise. Never modify stored files; `HandStore.verify()` checks the hashes.
- User decisions are editor-project operations (schema v3 `hand_store` state): `handstore.use|placement|mask|rename|
  delete|restore|version`; approvals use `approval.set` with `handstore:<library>/<asset>@v<N>` (each version is
  reviewed separately). Imported drawings are never approved in code and are only previewed until approved.
- Stored drawings are candidates in the existing switch system (`HandPoseLibrary` candidates per side/pose: original,
  `phase2:<drawing>`, `store:<uid>`); there is no second switch engine. The original hand stays the open-palm default.
- Placement is derived, never taken from the manifest anchor: the drawing's own cuff edge (masked sleeve/cuff) goes
  onto the character's white shirt cuff edge (skeleton + original pixels), tucked under the sleeve, rotated onto the
  hand bone; one scale per side (open-palm visible area matched to the original hand). User corrections: dx, dy,
  rot, uniform scale. Derived layers are computed from the stored source (mask, matte-halo removal) and covered by
  `forearm_<side>__hscuff` (the trimmed forearm without the original hand's skin pixels past the cuff).
- `autorig hand-evidence <package>` regenerates the committed hand evidence. Export writes `hand_switches.json`
  (validated metadata vs `not_implemented_in_native_moho`; Moho mapping `proposed_unverified`).

## Sleeve-free hand library rules

- Sheets are extracted by `autorig hand-sheet` (`handsheet.py`): background = near-white low-saturation pixels
  connected to the border (dark outlines never qualify), unmatted edges, tight crops, no scaling. Order, handedness and
  wrist openings are *measured* and checked against the assistant's `inspection.json`; a pair that is not mirrored is
  reported and the side without a drawing gets a flagged `mirror_x` copy. Closed-outline wrists (no cut) get a chord
  at the side's wrist width and are flagged for manual pivot review. The committed pack zip must equal a rebuild.
- `base_orientation: wrist_edge` drawings are placed by `WristAlignmentController` (`wristalign.py`): opening centre on
  the character's cuff edge, tucked TUCK_PX under it, wrist axis on the hand bone, rotation about the opening, one
  scale per side and library; pixels behind the tucked opening are trimmed. Corrections: dx, dy, rot, scale, overlap,
  pivot_x/pivot_y. Store hands are rigid on the hand bone (`GarmentSkeleton.RIGID`: no blend band, no stretch).
- Libraries are active or archived (`library_status`): a pack's `supersedes` archives older libraries by default (while
  the superseding library is active, so archiving it restores them), the
  Phase 2 sheet hands (`phase2_sheet_hands`) are archived while a sleeve-free library is active; `handstore.library`
  (project schema v4) restores or archives. Archived drawings stay in the store; `gesture_id_map` maps old ids.
- `motion/handselect.py` `NaturalHandSelectionEngine` fills only the *automatic* slot of the switch groups (user >
  keyframe > auto > default). Rules (`ACTIONS`), weights (`WEIGHTS`, per character in
  `natural_pose/hand_rules.json` or `handselect.weights`), temporal rules (hysteresis, holds, beats, symmetry), wrist
  aim within the wrist limit and eased in sequences. Scores never claim visual quality. It runs in AutoRig, not Moho.
- `autorig hand-replace-evidence <package>` regenerates `hand_store/evidence_v2/` (10 test renders, sheets, gates).

## Live Models track (Moho 14.5)

- Requirement: generate **Moho 14.5-compatible Live Models**, with Smart Bones and actions
  mapped to Live Mode's webcam (face tracking) and XInput gamepad inputs. See `docs/live-models.md`.
- **Stage LM-0 is done** (research, read-only probe script, `.moho` inspect/diff tool).
  LM-1 waits for the Phase 0 native verification **and** the discovery data (steps D0–D6)
  in `research/moho-14.5/`.
- No Lua API for assigning live inputs is known. Input identifiers, `.mohoproj` keys and
  API names must come from discovered evidence (probe reports, saved sample files,
  screenshots). **Never invent them.**

## Phase 1 rules (segmentation and analysis)

- Replicate token: from `REPLICATE_API_TOKEN` only; never print, log or commit it. In the cloud sandbox,
  the network proxy injects the real credential, so set `REPLICATE_API_TOKEN=proxy-injected` for commands.
- Model `vufinder/sam3` pinned at `1bf97763…` (snapshot in `src/autorig/data/replicate/`); run
  `python -m autorig replicate-check` (free) before changing versions. Dense output mode only.
- Paid inference only through `Sam3ReplicateProvider` (cache + ledger + ceiling; default $1.00 cumulative,
  `AUTORIG_REPLICATE_BUDGET_USD`). Check the budget before any run; never raise it silently.
- Never substitute an unrelated illustration for the user's reference. The synthetic character
  (`autorig.synthetic`) is a GT test fixture only.
- Artwork layers are original pixels, full-canvas and unscaled. Never invent hidden artwork; list it as
  missing. Readiness is READY only when the artwork exists. SHOWTIME mappings stay `proposed_unverified`
  until verified in Moho.
- `.autorig_cache/` (predictions, ledger) is local and git-ignored.

## Environment facts

- Moho 14.5 ("SHOWTIME", October 2026, free for Moho 14 owners) adds Live Mode. Phase 0
  targets Moho 14 APIs that 14.5 also has.
- Development happens in a cloud sandbox (Claude Code on the web). **Moho is
  not available here.** The user runs Moho Pro 14 on an Apple M1 Mac, with the
  project stored on an external SSD. Paths can contain spaces (`/Volumes/...`).
- Python 3.12 backend (`src/autorig`). Moho 14 embeds **Lua 5.4**.
- `mohoscripting.com` and `lostmarble.com` are blocked from the sandbox; GitHub
  is reachable. Web search snippets were used as secondary evidence.

## Non-negotiable rules

1. **Never claim Moho behaviour was verified unless Moho actually ran.** Cloud
   results are "verified against the mock / in simulation". Native tests are
   reported as **NOT EXECUTED** until the user runs them.
2. **Never invent Moho API functions.** Generated Lua may only use members in
   `src/autorig/data/moho_api_allowlist.json`. Adding one requires an evidence
   entry (doc page, working third-party code, or forum answer) in the allowlist
   and in `docs/moho-lua-api-evidence.md`, and an implementation in
   `tests/lua/mock_moho.lua`. `tests/test_lua_moho_mock.py` enforces that the
   mock and the allowlist match exactly.
3. Geometry is computed in Python (`autorig.geometry`); the Lua template only
   applies precomputed numbers. Keep it that way so the maths stays unit-testable.
4. Do not modify the Moho application bundle (`/Applications/...`). Install
   scripts into Moho's Custom Content Folder.
5. Generated artefacts that are committed (`examples/*/art`, `expected/`,
   `moho/` reference script, `preview/`) must stay in sync with the generators.
   Regenerate them with `python -m autorig build-example <spec>`; tests fail otherwise.

## Commands

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest                       # full cloud suite (native tests skip)
.venv/bin/python -m autorig build-example examples/phase0_test_character/rig_spec.json
.venv/bin/python -m autorig build-lua examples/phase0_test_character/rig_spec.json --out build/moho/AutoRig_Phase0_Build.lua
.venv/bin/python -m autorig verify-report examples/phase0_test_character/rig_spec.json examples/phase0_test_character/phase0_moho_report.json
AUTORIG_MOHO_REPORT=examples/phase0_test_character/phase0_moho_report.json .venv/bin/python -m pytest -m moho_native -v
.venv/bin/python -m autorig build-probe --report-dir research/moho-14.5 --out build/moho/AutoRig_Probe.lua
.venv/bin/python -m autorig moho-file diff research/moho-14.5/L0_baseline.moho research/moho-14.5/L1_camera.moho
# Phase 1
.venv/bin/python -m autorig analyze <image> --out <project_dir> [--provider fixture] [--budget-usd 1.00]
.venv/bin/python -m autorig replicate-check            # free schema drift check
.venv/bin/python -m autorig replicate-smoke <image>    # one tiny paid prediction (budget-capped)
.venv/bin/python -m autorig eval-synthetic <project_dir>
# Phase 2
.venv/bin/python -m autorig prepare <phase1_project> --out <package_dir> [--provider flux-fill-dev] [--synthetic-gt]
.venv/bin/python -m autorig review <package_dir> --list | --approve ID | --reject ID | --set-pivot JOINT X Y | --approve-phase
.venv/bin/python -m autorig pose-refine <package_dir> [--presets ...] [--hand-sources references/schoolboy/hand_sources.json]
# Phase 3.1
.venv/bin/python -m autorig motion <package_dir> [--out <dir>]     # natural-motion evidence (about 2-3 min)
.venv/bin/python -m autorig motion-bench <package_dir>             # measure solve/render times on this machine
# Phase 3.2
.venv/bin/python -m autorig ik-demo <package_dir>                  # IK / contacts / hand-switch evidence (~2 min)
.venv/bin/python -m autorig rig-controls <package_dir> [--out f]   # write + validate the rig controls spec
# Hand Pose Store
.venv/bin/python -m autorig hand-import <package_dir> <pack.zip>   # validate + store a hand pack (pending review)
.venv/bin/python -m autorig hand-evidence <package_dir>            # attachment sheet, seams, IK sweep (~3 min)
# Sleeve-free hand library
.venv/bin/python -m autorig hand-sheet references/schoolboy/hand_pack_v2/hand_sheet_sleeveless.jpg --inspection references/schoolboy/hand_pack_v2/inspection.json --out references/schoolboy/hand_pack_v2 --library-id schoolboy_hands_sleeveless --supersedes schoolboy_hand_switches_v1 --zip-name AutoRig_Hand_Library_Sleeveless_v2.zip
.venv/bin/python -m autorig hand-library <package_dir> [--archive LIB | --restore LIB | --default LIB]
.venv/bin/python -m autorig hand-replace-evidence <package_dir>    # replacement evidence + 14 gates (~5 min)
# Phase 3.3 garment rig
.venv/bin/python -m autorig garment-evidence <package_dir>         # before/after, close-ups, sweeps, reports (~3 min)
.venv/bin/python -m autorig garment-weights <package_dir> [--explain LAYER X Y] [--export prefix]
# Phase 3.4 corrective artwork
.venv/bin/python -m autorig corrective-evidence <package_dir> [--regenerate] [--quick]  # assets (pending) + evidence (~10 min)
# Phase 3.5 artistic posing
.venv/bin/python -m autorig artistic-evidence <package_dir> [--quick]  # poses, candidates, acting test (~15 min)
# Phase 3
.venv/bin/python -m autorig editor <package_dir> [--port 8765]     # then open http://127.0.0.1:8765
.venv/bin/python -m autorig export-assets <package_dir> --out <dir>
cd editor && npm install && npm run build && npm test             # frontend build + vitest
cd editor && AUTORIG_PYTHON=../.venv/bin/python NODE_PATH=$(npm root -g) npm run e2e   # Playwright (global install in the cloud)
```

## Layout

- `src/autorig/` — spec (+ JSON Schema), geometry, artwork, Lua codegen, preview, report verifier, CLI
- `src/autorig/data/templates/phase0_build.lua` — the Moho menu-script template
- `src/autorig/data/templates/probe_moho.lua` — read-only Moho API discovery script (Live Models)
- `src/autorig/mohofile.py` — `.moho`/`.mohoproj` reader, JSON grep and structural diff
- `research/moho-14.5/` — discovery data from the user's Moho 14.5 (committed on purpose)
- `src/autorig/segmentation/` — Replicate client, SAM 3 provider, budget ledger, fixture provider
- `src/autorig/phase1/` — anatomy, face, partition, readiness, live controls, compositor, schemas, project writer
- `src/autorig/synthetic.py` — synthetic ground-truth character (test fixture)
- `examples/phase1_synthetic_character/` — synthetic fixture + its character_project + evaluation
- `src/autorig/phase2/` — store (pixel preservation), occlusion, providers, reconstruct, facial, joints, pose, vector, review, pipeline
- `src/autorig/phase2/` natural posing — skeleton, skinning (deformer), posing (solver + presets), pose_eval, natural (orchestration), corrective (hand variants, armpit gussets)
- `examples/phase2_synthetic_character/` — Phase 2 package built from the synthetic Phase 1 project
- `src/autorig/editor/` — Phase 3 backend: project (op log), artwork (masks, fidelity, comparison), posesvc, vectors,
  readiness, export (quality gates), server (FastAPI)
- `src/autorig/motion/` — Phase 3.1: profile, intent, planner (presets + front-view gaits), solver, garments
  (garment model + deformation engine), feasibility, quality, pipeline (MotionEngine, evidence CLI); Phase 3.2: ik
  (solver, limb controllers, foot contacts), switches (switch groups, hand library, correction triggers), rigctl
  (rig controls spec, live action map), demo (`ik-demo`)
- `src/autorig/handsheet.py` — hand sheet extraction; `src/autorig/wristalign.py` — WristAlignmentController;
  `src/autorig/motion/handselect.py` — NaturalHandSelectionEngine; `src/autorig/handreplace.py` — replacement evidence;
  `references/schoolboy/hand_pack_v2/` — the user's sleeve-free sheet, inspection record, extracted sprites, pack zip
- `src/autorig/garmentrig/` — Phase 3.3: structure (parts, classes, pivots), weights (meshes, ops, explain), deform
  (StructuredDeformer), corrections, evaluate, evidence, moho (compatibility matrix); `editor/src/components/WeightsPanel.tsx`
- `src/autorig/garmentrig/correctives.py` — Phase 3.4: CorrectiveArtworkStore, generators (armpit gusset, shoe top,
  joint caps, edge decontamination), PoseSpaceCorrectionEngine, CorrectiveRuntime, planner;
  `garmentrig/corrective_evidence.py` (CorrectiveQualityEvaluator, sequences, benchmark);
  `editor/src/components/CorrectivesPanel.tsx`
- `src/autorig/artistic/` — Phase 3.5: poses (ArtisticPose, planner, action library), silhouette, feasibility,
  candidates, joint_art (fold corrections), layer_order, alternatives (store, controller, artwork requests, views,
  limb configs), transitions (acting sequences), evaluate (ArtisticAnimationEvaluator), feedback, evidence;
  `editor/src/components/ArtisticReviewPanel.tsx`
- `src/autorig/handstore.py` — Hand Pose Store (import/validation, store index, cuff mask, wrist placement, derived
  layers); `src/autorig/handevidence.py` — hand evidence; `references/schoolboy/hand_pack/` — the user's hand pack
- `editor/` — Phase 3 browser editor (React + TypeScript + Vite + react-konva); `dist/` is committed; `e2e/` Playwright
- `projects/schoolboy_tpose/moho_asset_package/` — validated Phase 3 export of the unedited real package (not
  production-ready); `docs/phase3/screenshots/` — real-browser screenshots from the e2e test
- `references/schoolboy/` — the user's character sheet, front-view crop, (assistant-estimated) landmarks and
  `tpose_front.jpg` (the T-pose reference, preferred for rigging)
- `projects/schoolboy/`, `projects/schoolboy_tpose/` — snapshots of the real-art runs 1 and 2 (unreviewed)
- `tests/lua/mock_moho.lua` — strict mock of the allowlisted Moho API (Lua 5.4 via `lupa`)
- `examples/phase0_test_character/` — spec, art layers, reference script, previews, expected values
- `tools/macos/install_phase0.sh` — generates a machine-specific script and installs it
- `docs/` — architecture, Moho API evidence, MohoMCP assessment, Phase 0 guides and report

## Conventions

- Rig spec pixel coordinates: origin top-left, +y down. Moho world: origin at frame
  centre, +y up, frame height spans y ∈ [-1, 1].
- Bone `fPos` is in the parent's local frame (origin at parent base, +x along parent);
  `fAngle` is radians, CCW, parent-relative. Frame 0 = rest/setup pose.
- Commit messages: imperative mood; no model identifiers in commits or docs.
