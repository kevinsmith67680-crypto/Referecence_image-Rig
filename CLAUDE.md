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
