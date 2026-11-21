# Phase 3 implementation plan: interactive artwork editor and Moho-ready asset package

The user started Phase 3 explicitly. In this phase "Phase 3" means the interactive editor and asset
assembly. The native Moho rig belongs to Phase 4 and is **not** part of it.

## 1. Audit of previous phases (not assumed to have succeeded)

| Area | State found in the repository | Consequence for Phase 3 |
|---|---|---|
| Phase 0 (Moho PoC) | Implemented; **NOT locally verified** (native tests NOT EXECUTED) | No `.moho` generation in Phase 3; the export is an asset package only |
| Phase 1 (SAM 3 analysis) | Real runs on the user's T-pose (`projects/schoolboy_tpose/character_project`); layers partition the source pixels; "upper_clothing" (blazer) not in the vocabulary; no torso joint | The editor must let the user correct masks and joints |
| Phase 2 (reconstruction) | Package with A originals, B generated extensions (FLUX, **unreviewed**; two known bad fills, hidden in the neutral pose), vectors (joint caps, eyelid guides), `approvals.json` | Generated pixels need visible provenance and approval controls |
| Natural pose refinement | `skeleton`, `skinning`, `posing`, `pose_eval`, `natural`; `natural_pose/rig.json`; corrective hand variants and gussets pending review | Reuse the solver; no second solver |
| Live readiness | `live_controls.json` (Phase 1), `live_artwork_manifest.json` (Phase 2): 15 controls, none READY | Show it as-is; never upgrade a status without the artwork |
| Backend / frontend | **No FastAPI service or React frontend existed**; only a CLI | Both are new in Phase 3 |
| Tests | 322 pytest (2 native skipped); no frontend tests | Add pytest, vitest and Playwright |

Coordinate systems and layer representations:

* **Source pixels** (origin top-left, +y down) are shared by Phase 1 layers (full canvas, unscaled),
  Phase 2 artwork and masks, and `natural_pose/rig.json` bones. The editor works in source pixels; the
  canvas only applies a view transform (zoom and pan).
* **Natural-pose angles** are in image degrees, positive = clockwise on screen. **Moho `fAngle`** is in
  radians, counter-clockwise, parent-relative. The export records the convention and the conversion; it
  is not applied silently.
* **Phase 0 rig spec:** pixel coordinates are converted to the Moho frame (origin at the frame centre,
  +y up, height 2) in `autorig.geometry`. The export records the frame size so Phase 4 can reuse that
  code.
* **Three layer formats coexist:** Phase 1 `layers.json` v1, Phase 2 `artwork/manifest.json` v2
  (A/B/C/vectors) and `store.json`, and the natural-pose layer bindings and corrective switch layers. The
  Phase 3 project unifies them in one explicit, versioned schema without rewriting any of them.
* **Joint names differ:** Phase 1 joints use names like `shoulder_r`, while skeleton bones use names like
  `upper_arm_r`, whose head is the shoulder. The editor edits **bones**, and the export lists both names.

## 2. Components

| Component | Where | Reuses |
|---|---|---|
| Editor project model: schema v1, operation log, undo/redo, checkpoints and versions, migration | `src/autorig/editor/project.py` | n/a |
| Derived artwork: user mask corrections applied to the source pixels, with the layer partition kept | `src/autorig/editor/artwork.py` | Phase 2 `ArtworkStore` (read-only) |
| Pose preview using the edited skeleton | `src/autorig/editor/posesvc.py` | `phase2.skeleton`, `skinning`, `posing`, `pose_eval`, `corrective` |
| Vector corrections (Bezier paths, fill and stroke) | `src/autorig/editor/vectors.py` | Phase 2 SVGs (imported as read-only seeds) |
| Comparison, provenance and neutral fidelity | `src/autorig/editor/artwork.py` (`fidelity`, `diff_heatmap`, `provenance_overlay`) | n/a |
| Face and live readiness | `src/autorig/editor/readiness.py` | Phase 1 `face.json`, Phase 2 `live_artwork_manifest.json` |
| Export package and quality gates | `src/autorig/editor/export.py` | Phase 2 schemas and store verification |
| HTTP API (FastAPI) and `autorig editor` | `src/autorig/editor/server.py`, `cli.py` | n/a |
| Browser editor (React, TypeScript, Vite, **react-konva**) | `editor/` | n/a |

### Canvas library choice

| Need | Konva (react-konva) | Fabric.js | PixiJS | Paper.js |
|---|---|---|---|---|
| Transparent layered images | yes (`Image` nodes) | yes | yes (WebGL) | basic |
| Interactive transforms | `Transformer` | strong | manual | manual |
| Masks and clipping | clip functions, composite ops | clipPath | masks | clipping |
| Vector overlays and Bezier | `Path`, `Line` (bezier) | strong SVG | Graphics | strongest |
| Pointer events and hit testing | per-shape events | yes | yes | yes |
| Zoom and pan, coordinate mapping | stage transform + `getRelativePointerPosition` | viewport transform | manual | view |
| React integration | official `react-konva` | community | community | none |
| Maturity and maintenance | active, mature | active | active | slower |
| Apple Silicon browsers | 2D canvas, smooth at 1-2 kpx | same | fastest (WebGL) | slower with many items |

**Konva** is the choice. It has idiomatic React bindings, layered bitmaps, per-shape pointer events,
transforms and enough vector support for correction shapes. Undo and redo stay on the backend (event
sourcing), so they are independent of the canvas library. Mask painting uses an off-screen 2D canvas
shown as a Konva image.

## 3. Architectural rules

1. **Non-destructive editing:** sources and Phase 2 originals are never written. Edits are operations;
   derived files live under `<package>/editor/`.
2. **Partition invariant:** each source pixel belongs to at most one layer. Moving pixels between layers
   keeps the neutral composite identical to the source. Removing pixels without a target is reported as a
   neutral-pose gap.
3. **One pose solver** (Phase 2 natural). Edited bones override the built skeleton. Poses are never
   stretched to pass, and solver warnings are shown.
4. **Approvals only from the user** (API calls from the UI); never in code. The export is
   `production_ready: false` until the mandatory approvals and assets exist.
5. **No Moho claims:** Smart Bone, Smart Warp and SHOWTIME mappings stay `proposed_unverified`.

## 4. Implementation order

1. Project model, operations, persistence and migration (pytest).
2. Derived artwork and mask corrections, validation and fidelity metrics (pytest on the real package).
3. Skeleton overrides and pose service; joint warnings (pytest).
4. Vectors, comparison, face and live readiness, export and quality gates (pytest).
5. FastAPI routes and the `autorig editor` CLI (pytest with TestClient).
6. React editor: layout, canvas (zoom, pan, fit, checkerboard, overlays), layer panel, mask tools,
   joint editor, vector editor, pose panel and joint correction, face, live, compare, export (vitest for
   the utilities).
7. Playwright end-to-end on the real T-pose package, with screenshots and persisted-output checks.
8. `docs/phase3_report.md` and commit.

## 5. Acceptance criteria (from the request)

These are tested by real operations whose persisted output is checked:
1. the real project loads and the reference displays;
2. layers can be selected and reordered;
3. masks can be corrected;
4. pivots can be edited;
5. vectors can be edited;
6. natural poses can be previewed;
7. joint defects can be reviewed;
8. face and live readiness are visible;
9. edits survive save and reopen;
10. the neutral pose stays faithful;
11. a valid package is exported;
12. tests pass;
13. real screenshots exist.

If the browser cannot run in the cloud, UI tests are marked NOT_EXECUTED, with local instructions.
