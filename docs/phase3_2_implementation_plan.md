# Phase 3.2 implementation plan: IK, switch layers and articulated controls

The user started Phase 3.2 explicitly. Phase 0 is still not verified in Moho, and nothing generated has been
reviewed by a person. Both limits carry forward, as in every earlier phase.

## 1. Audit of what exists (from the code, not the file names)

| Area | State found | Where |
|---|---|---|
| Skeleton | 19 bones: pelvis → spine → chest → neck → head; chest → clavicle → upper arm → forearm → hand; pelvis → thigh → shin → foot. Edited bones come from the editor project | `phase2/skeleton.py`, `editor/posesvc.py` |
| IK | One analytic two-bone function, `phase2.posing.two_bone_ik(root, target, l1, l2, prefer)`, with a bend-side callback and no pole target, joint limits or reach report. The motion solver uses it for legs (lateral knee) and for arm `reach`/`target` | `phase2/posing.py:121`, `motion/solver.py` |
| Gait and contacts | Phase-driven walk/run; planted feet world-locked; loads; swing knee schedule; pelvis height from loaded feet. No named contact states | `motion/planner.py`, `motion/solver.py` |
| Garments | Garment regions, blazer hem, rotation-dependent bands, hem/hair spring terms, layering | `motion/garments.py` |
| Artwork switching | Hands only, by file name: `hand_{s}__{name}` replaces the hand layer (and `forearm_{s}__trim` the forearm). The choice is per preset preference and approval. No groups, offsets, keyframes, alignment checks or user selection | `motion/pipeline.py:motion_order`, `motion/feasibility.py` |
| Hand artwork | Original open hands (reference). Fist, pointing, thumbs-up and palm-up variants for both sides, **all pending review**. Relaxed, gripping, palm-down and partially-closed hands do not exist | `natural_pose/corrective/hands/` |
| Corrective artwork | Procedural armpit gussets (generated, pending review). No elbow, knee, hip or wrist correction drawings | `phase2/corrective.py` |
| Rig / project schemas | Phase 0 `rig_spec.json` (JSON Schema). Phase 2 `natural_pose/rig.json` (skeleton dump, no version). Editor project `autorig.editor_project` v1 (op log; `MIGRATIONS` empty). Export manifest `autorig.moho_asset_package` 1.0.0 | `spec.py`, `editor/project.py`, `editor/export.py` |
| Editor | Pose tab: presets, gait phase/speed/exaggeration, playback, diagnostics, before/after, animated cycles. No IK targets or switch controls | `editor/src` |
| Tests | 372 passing (2 native NOT EXECUTED), including 32 Phase 3.1 tests and a Playwright end-to-end test | `tests/`, `editor/e2e` |

What earlier phases leave unverified, and what Phase 3.2 inherits:
* Moho never ran (Phase 0).
* Every generated or reference-derived asset is pending review: hands, gussets, FLUX extensions.
* T-pose armpit compression and the +5.5 % neck-layer area are open Phase 3.1 issues.
* The art is front-view only.

None of these blocks the IK and switch machinery. They limit what the *demonstrations* can honestly show: hand
switches can only be **previewed** until approved, and elbow/knee correction drawings are missing.

## 2. Missing features and where they go

| Feature | Plan | Module |
|---|---|---|
| Two-bone IK solver | `TwoBoneIKSolver`: analytic; pole target; bend-sign default; flexion limits that give min/max reach; reach report; deterministic; stable near full extension | `motion/ik.py` |
| Arm / leg IK controllers | `ArmIKController`, `LegIKController` on the existing bones. They produce the same local rotations the solver uses (no duplicate bones). Include FK↔IK matching | `motion/ik.py` |
| Foot contacts | `FootContactController`: PLANTED / LIFTING / SWINGING / LANDING / FREE from loads and swing phase; world locks; markers | `motion/ik.py` |
| Switch groups | `ArtworkSwitchController`: groups with variants, one active, attachment bone, pivot, local offsets, provenance, approval, missing assets, keyframes. Resolution order: user selection > keyframe > automatic preference (only when auto is on) | `motion/switches.py` |
| Hand library | `HandPoseLibrary`: 8 poses × 2 sides from the package; wrist attachment measured from pixels; left/right integrity; missing poses = NEEDS_ARTWORK | `motion/switches.py` |
| Pose-aware corrections | `CorrectionSwitchController`: trigger joint, angle range, hysteresis, priority, asset, approval. Continuous corrections stay the Phase 3.1 band rules | `motion/switches.py` |
| Live control map | Semantic actions (HAND_LEFT_POINT, ARM_RIGHT_REACH, FOOT_LEFT_PLANT, GESTURE_SHRUG …) → switch / IK / contact / preset changes. Body and face channels separate; Moho mapping `proposed_unverified` | `motion/rigctl.py` |
| Rig control spec | `autorig.rig_controls` v1 (JSON Schema): IK chains, targets, poles, constraints, FK/IK state, foot contacts, switch groups, variants, correction triggers, offsets, missing artwork, live map. Validated before export; added to the export manifest | `motion/rigctl.py`, `data/schemas/rig_controls.schema.json` |
| Project migration | Editor project v1 → v2: `ik` and `switches` state with defaults; new ops `ik.set`, `ik.reset`, `switch.set`, `switch.key`, `switch.offset`. v1 projects load unchanged | `editor/project.py` |
| Processing order | intent → contact/IK target planning → skeletal solve → correction selection → garment deformation → switch resolution → composition → evaluation | `motion/pipeline.py` |
| Editor | IK tab (per-limb enable, FK/IK, targets and poles draggable on the canvas, foot lock, limits, reset); Switches tab (variant selectors with thumbnails, approval, missing warnings, alignment report and offsets, correction groups) | `editor/src` |
| Evidence | `autorig ik-demo`: the 10 requested images, close-ups, an IK reach animation, and a hand-switch sheet with attachment errors | `motion/demo.py` |

## 3. Data migration

* Editor project `schema_version` 1 → 2. Migration keeps the operation log as is. The base state gains `ik` (four
  limbs, disabled) and `switches` (groups built from the package, active = original or automatic).
* Old projects replay unchanged: the new state sections have defaults, and old ops never touch them.
* A project saved by v2 is refused by older code (existing guard).
* Export manifest: `rig_controls` section added; schema version 1.0.0 → 1.1.0 (additive).

## 4. Testing strategy

* **Unit:** IK maths on synthetic geometry (accuracy, lengths, poles, limits, unreachable targets, determinism,
  stability near extension); contact state machine; switch resolution, keyframes, hysteresis.
* **Real package:** arm/leg controllers on the schoolboy skeleton; FK↔IK matching; foot locking during pelvis
  moves; hand attachment errors measured in pixels; neutral pose bit-exact; garment checks with IK poses.
* **Persistence:** ops → reopen → same state; undo/redo; v1 → v2 migration; schema validation.
* **Browser:** drag a wrist target and an ankle target on the canvas; switch a hand; reopen the project.
* Fixture results are never presented as production-quality animation.

## 5. Performance risks

* **IK:** microseconds per solve, so dragging can run the skeleton solve on every move.
* **Full render:** about 2.5 s per frame, so the artwork follows after the drag ends (as in Phase 3.1).
* **Switching:** layers are cached by the engine, so a switch costs one render.
* Timings are measured and reported (IK, switch, deformation, full frame, input-to-preview), not assumed.

## 6. Moho compatibility considerations

* Switch groups map to Moho Switch Layers.
* IK chains, poles and constraints map to Moho bone targets and constraints.
* Correction triggers map to Smart Bone actions.
* None of this is exported to `.moho` in this phase. The mappings stay `proposed_unverified`, and no Moho API is
  added to the allowlist.
