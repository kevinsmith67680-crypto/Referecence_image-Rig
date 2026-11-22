# AutoRig Studio: Architecture

## 1. Product pipeline (target)

```
reference image ──► segmentation (SAM 3 on Replicate) ──► part masks ──► art layers (bitmap/vector)
                                                                              │
                         joint / pivot estimation ──► rig spec (JSON) ◄───────┘
                                                          │
                                   autorig codegen ──► Moho Lua script ──► Moho Pro 14 rig (.moho)
                                                          │                         │
                                                          └── verification ◄────────┘
                                                              (read-back report, MohoMCP)
```

The **rig spec** is the contract between the AI side (segmentation and pose
estimation, Phases 1+) and the Moho side (Phase 0). Phase 0 proves the right
half of the diagram with a hand-authored spec and synthetic art.

## 2. Phase 0 components

| Component | File | Responsibility |
|---|---|---|
| Rig spec schema | `src/autorig/data/rig_spec.schema.json` | Structure of a rig: canvas, bones (pixel joints), parts (art layer, bound bone, z-order), animation tracks |
| Spec loader | `src/autorig/spec.py` | JSON Schema validation plus semantic checks (parents declared first, references, z-order, frame ranges) |
| Geometry | `src/autorig/geometry.py` | Pixel → Moho units, world joints → parent-local `fPos`/`fAngle`, forward kinematics, expected keys |
| Artwork | `src/autorig/artwork.py` | Test character: full-canvas RGBA PNG and SVG per part, geometry derived from the bones |
| Lua codegen | `src/autorig/lua_codegen.py` + `data/templates/phase0_build.lua` | Self-contained Moho menu script with precomputed data and baked absolute paths |
| Report verifier | `src/autorig/report.py` | Compares Moho's read-back JSON with the spec (oracle of the native test) |
| Preview | `src/autorig/preview.py` | Python simulation of layer binding and FK (contact sheet and GIF) |
| CLI | `src/autorig/cli.py` | `validate`, `generate-art`, `build-lua`, `expected`, `preview`, `verify-report`, `build-example` |
| API allowlist | `src/autorig/data/moho_api_allowlist.json` | The only Moho members generated Lua may use, each with evidence |
| Strict mock | `tests/lua/mock_moho.lua` | Lua 5.4 mock implementing exactly the allowlist; anything else raises |
| Installer | `tools/macos/install_phase0.sh` | Generates the machine-specific script and copies it to the Custom Content Folder |

## 3. Coordinate systems and conventions

**Spec / canvas pixels:** origin top-left, +x right, +y down, canvas
`W × H` (Phase 0: 1920 × 1080).

**Moho world (bone-layer space):** origin at the frame centre, +y up. The
frame spans `y ∈ [-1, 1]` and `x ∈ [-W/H, W/H]`. Conversion:

```
x_moho = (x_px - W/2) / (H/2)
y_moho = (H/2 - y_px) / (H/2)
```

**Bones** (evidence: SimplSam `ss_make_bones.lua`; see the API evidence doc):

- `fAngle`: radians, counter-clockwise positive, **relative to the parent**.
- `fPos`: **in the parent's local frame**, with the origin at the parent's base
  and +x along the parent. A child on the parent's tip has `fPos = (parent.fLength, 0)`.
- Root bones (`fParent = -1`) use bone-layer coordinates directly.
- Bone ID = creation index. The spec lists parents before children, so spec
  order is creation order.

**Art layers:** each part is a **full-canvas** transparent PNG with the
project's pixel size, painted at its final position. An image with the
project's pixel size fills the Moho frame (Lost Marble forum guidance), so
every layer lines up with the others and with the bones without per-layer
offsets. The generated script refuses to run if the document is not exactly
1920 × 1080. This avoids depending on how Moho scales images of other sizes.

**Binding:** each image layer is placed inside the bone layer and bound with
`SetLayerParentBone(boneID)`. This is rigid layer binding, the classic cut-out
setup, and the layer follows its bone's deviation from the rest pose.

**Animation:** frame 0 is the setup (rest) pose. The test animation keys
`fAnimAngle` at frames 1, 10, 19, 28 and 36 with
`value = rest_local_angle + radians(offset)`. In-between frames use Moho's
default interpolation. The script deliberately does not set interpolation
(see the evidence doc).

## 4. Generated script flow (`AutoRig_Phase0_Build.lua`)

1. **Preflight** (changes nothing on failure): a document is open; the asset
   path is baked in; no layer named `AutoRig_Phase0` exists; the project is
   1920 × 1080; every image file exists (`io.open`).
2. **Build** at frame 0: `CreateNewLayer(MOHO.LT_BONE)`, then `AddBone` × 4 in
   parent order (sets `fParent`/`fAnimParent`, `fPos`/`fAnimPos`, `fLength`,
   `fAngle`/`fAnimAngle`, then `UpdateBoneMatrix`). Next, for each part from
   back to front: `CreateNewLayer(MOHO.LT_IMAGE)`,
   `LayerAsImage():SetSourceImage(path)`, `PlaceLayerInGroup(layer, boneLayer, true, false)`
   and `SetLayerParentBone(id)`. Finally, the angle keys and
   `SetStartFrame(1)`/`SetEndFrame(36)`.
3. **Read-back** from Moho's own objects: bone fields, world base/tip through
   `fMovedMatrix:Transform`, every angle key, each layer's type and
   `LayerParentBone()`, and the bone layer's children.
4. **Report**: `phase0_moho_report.json` is written next to the assets, and an
   alert shows the outcome. API errors are caught, written to the report and
   shown, never silently swallowed.

## 5. Verification layers

| Layer | Where | What it proves | What it does not prove |
|---|---|---|---|
| Unit tests (geometry, spec, art, codegen) | Cloud | The maths, validation, art/pivot alignment and serialization are correct | Anything about Moho |
| Strict-mock execution (Lua 5.4) | Cloud | The script runs end to end, uses only evidenced API members and produces a passing read-back in a Moho-like model | That real Moho behaves like the mock |
| Mutation checks | Cloud (manual run) | The suite catches realistic defects (wrong binding, missing cast, wrong rotation sign, ...) | — |
| Python simulation preview | Cloud | Joints stay connected and the motion reads correctly | Moho rendering |
| `verify-report` on Moho's read-back | **Mac (NOT EXECUTED)** | Moho holds exactly the rig the spec describes | Visual correctness |
| Manual checklist | **Mac (NOT EXECUTED)** | Art and bones line up, animation plays, the file saves and reopens | — |

## 6. Hybrid bitmap/vector

Phase 0 uses **bitmap image layers** (PNG), which match segmentation output.
Each part also ships an SVG vector source generated from the same primitives.
Importing those as Moho vector layers is **not** part of Phase 0 and is untested.

## 7. MohoMCP

[MohoMCP](https://github.com/Kveto/MohoMCP) can read documents, layers, bones
and keyframes, set transforms and keys, and capture screenshots. It **cannot
create layers or bones**, so rig construction uses our own menu script. MohoMCP
is a candidate for automated read-back and visual checks in later phases. See
[mohomcp-assessment.md](mohomcp-assessment.md).

## 8. Live Models (Moho 14.5)

Moho 14.5 Live Mode drives Smart Bones and actions from webcam face tracking
and XInput gamepads. AutoRig will generate the Smart Bones and actions in Lua.
No Lua API for *assigning* live inputs is known, so mappings will be either
generated as UI assignment steps or injected into the saved `.moho`
(zip + JSON) once its format is learned from sample files. Discovery tooling
already exists: `src/autorig/data/templates/probe_moho.lua` (a read-only API
probe) and `src/autorig/mohofile.py` (inspect and diff). See
[live-models.md](live-models.md).

## 9. Phase 1: character analysis and segmentation

```
reference.png ──► Sam3ReplicateProvider (pinned vufinder/sam3, cache, ledger, ceiling)
                  │  body concepts (person, hair, head, face, neck, shirt, sleeve, arm, hand, shorts/pants, leg, shoe)
                  ▼
        collect_instances (score/size/duplicate gates) ──► CharacterAnatomyAnalyzer (pluggable PoseEstimators)
                  │                                              │ 20 joints + confidence/reliable/flags/occlusion
                  ▼                                              ▼
        head crop ×upscale ──► facial concepts ──► analyze_face (quality gates, intensity pupils, landmarks)
                  │                                              │
                  ▼                                              ▼
        anatomical partition (thick bone segments within visible regions) ──► original-pixel layers
                  │
                  ▼
        compositor (fidelity vs independent foreground) · readiness · LiveControlSpec · hybrid contract
                  ▼
        character_project/  (all JSON schema-validated; metadata with SHA-256 of every output)
```

* Coordinates: everything stays in the original image's pixel space; layers are full-canvas and unscaled,
  the same convention as Phase 0's art layers. Phase 2 can map joints to Moho units with `autorig.geometry`.
* Bridge to Phase 0: `anatomy.json` joints and parent relations, together with `artwork/layers.json` (bone
  per layer, pivots and z-order), carry everything needed to emit a `rig_spec.json` in Phase 2. Not done yet.
* Details and measured results: [phase1_report.md](phase1_report.md).

## 10. Phase 2: reconstruction and animation-ready assets

Phase 1 project (read-only) → `OcclusionAnalyzer` (linework cue + priors + shape models → graph, regions, z-order)
→ `ArtworkStore` (A immutable originals / B generated extensions under them / C corrections / D working)
→ reconstruction (deterministic flat fill → pluggable `ReconstructionProvider` for complex regions)
→ joint preparation and pose previews (gap, contour and overlap diagnostics) → vector corrections → live artwork manifest
→ QA reports → human review (`approvals.json`, `visual_review.html`) → Phase 2 character package.
See [phase2_implementation_plan.md](phase2_implementation_plan.md) and [phase2_report.md](phase2_report.md).

## 11. Phase 3: interactive editor and Moho-ready asset package

Phase 2 package (read-only) → `EditorProject` (`<package>/editor/project.json`: operation log + cursor, versions,
schema migration) → replayed state → `ArtworkModel` (base partition + mask operations → layer RGBA, composite,
fidelity, diff heatmap, provenance), `PoseService` (the Phase 2 skeleton, skinning, `PoseSolver` and `evaluate`
with edited bones, limits and blend bands), readiness (face, live) → `export_package` (layers, vectors, bones in
pixels and Moho coordinates, deformation metadata, readiness, approvals, 12 quality gates, `validate_package`).

FastAPI (`autorig editor`) serves the API and the React + TypeScript + react-konva editor (`editor/dist`). The
browser holds view state only; every edit is an operation persisted by the backend. Masks move source pixels
between layers, so the neutral pose stays identical to the reference; gaps and z-order regressions are reported.
See [phase3_implementation_plan.md](phase3_implementation_plan.md) and [phase3_report.md](phase3_report.md).

## 12. Phase 3.1: natural motion

`src/autorig/motion/` replaces the single preset solver with four separate stages:
* `MotionPlanner` produces a `PoseIntent`: foot and hand targets, contacts, the gait phase, and torso, head and
  garment intent.
* `SkeletalPoseSolver` handles FK, two-bone IK, character limits, pelvis height from loaded feet, balance and
  clavicle rhythm, and never scales a bone.
* `ArtworkDeformationEngine` is the Phase 2 skinning deformer on a garment-aware skeleton: the blazer hem hangs from
  the pelvis, trousers attach softly, bands widen with joint rotation, the hem lags, and layering rules apply.
* `PoseFeasibilityEvaluator` and `PoseQualityEvaluator` report supported / needs-artwork status and measured checks
  with documented thresholds.

Gaits are phase-driven front-view cycles. The editor's Pose tab uses the engine: skeleton-only solves for scrubbing
and playback, and full renders after release. `autorig motion` writes the evidence set to
`<package>/natural_pose/motion/`. See [phase3_1_natural_motion_report.md](phase3_1_natural_motion_report.md).

## 13. Phase 3.2: IK, switch layers and articulated controls

The Phase 3.1 stages stay; Phase 3.2 adds controls between them. The processing order in `MotionEngine.pose` is:
intent -> contact and IK target planning (`apply_ik`) -> skeletal solve -> correction selection
(`CorrectionSwitchController`, plus the continuous band rules) -> garment deformation -> switch resolution
(`ArtworkSwitchController`) -> composition -> evaluation.

* `motion/ik.py`: `TwoBoneIKSolver` (analytic, pole target, flexion limits that define the reach interval, clamping
  without scaling), `ArmIKController` / `LegIKController` on the existing bones (FK<->IK matching),
  `FootContactController` (PLANTED, LIFTING, SWINGING, LANDING, FREE; world locks with slide/lift errors).
* `motion/switches.py`: switch groups (hands, elbow/knee/shoulder corrections, the mouth), the `HandPoseLibrary`
  (8 poses per side; wrist attachment measured on the visible pixels; auto-align search), correction triggers with
  hysteresis.
* `motion/rigctl.py`: the `autorig.rig_controls` v1 document (IK chains, state, contacts, switch groups, triggers,
  missing artwork, semantic live actions), validated with its JSON Schema before export.
* Editor: project schema v2 (`ik`, `switches`; ops `ik.set`, `ik.reset`, `switch.set`, `switch.key`,
  `switch.offset`), IK tab with draggable targets and poles on the canvas (skeleton-only solves while dragging,
  the operation on release), Switches tab with thumbnails, approval, alignment and keys.

See [phase3_2_ik_switch_report.md](phase3_2_ik_switch_report.md).

## 14. Hand Pose Store

`src/autorig/handstore.py` keeps imported hand drawings with the character (`<package>/hand_store/`, append-only,
hashed). The editor project (schema v3) records the user's decisions (`handstore.*` operations, approvals per
version). `engine_input` derives a rest-frame layer per drawing:
* sleeve/cuff mask and matte-halo removal;
* wrist placement from the character's own shirt cuff and the hand bone;
* one scale per side.

Those layers enter the existing `HandPoseLibrary` as candidates next to the original and Phase 2 drawings, so switch
resolution, presets, live actions, IK and export are unchanged in structure. Hand Library edits update the cached
engine in place (`MotionEngine.set_hand_store`). See [hand_pose_store_integration.md](hand_pose_store_integration.md).

## 15. Sleeve-free hand library and natural hand selection

* `src/autorig/handsheet.py` turns a hand sheet into a pack (`autorig hand-sheet`). It extracts each drawing (border
  flood fill, unmatted edges, tight crop, no scaling) and measures the wrist opening (a cut with corners, or a chord on a
  closed outline). Handedness comes from mirror-pair IoU, the open palm's thumb and per-drawing thumb evidence.
* `src/autorig/wristalign.py` `WristAlignmentController` places a sleeve-free drawing by its own opening:
  * the opening goes onto the character's cuff edge, tucked under it;
  * the axis goes onto the hand bone, rotating about the opening;
  * one scale per side and library;
  * pixels behind the opening are trimmed.
  `handstore.derive_layers` dispatches on `base_orientation`.
* `GarmentSkeleton.RIGID` binds stored hand drawings rigidly to the hand bone (no wrist blend band).
* `handstore.library_status` marks libraries active or archived (`supersedes`, the `phase2_sheet_hands` pseudo-library,
  the user's `handstore.library` choice). Archived drawings stay stored and leave the switch candidates.
  `gesture_id_map` maps old ids.
* `src/autorig/motion/handselect.py` `NaturalHandSelectionEngine` runs after the skeletal solve:
  * `MotionEngine.select_hands` ranks the active drawing of every pose by eight weighted criteria under per-action
    rules, then applies the temporal rules and symmetry;
  * the result fills the switch groups' automatic slot (user > keyframe > auto > default);
  * the gesture's wrist aim is applied to the hand bone within the wrist limit, eased in sequences;
  * gripping targets move the arm IK target so the grip centre lands on the object.
* `rig_controls` 1.1.0 adds `hand_selection` (groups, variants, pivots, offsets, rules, overrides, keys, provenance, Moho
  translation notes); `hand_switches.json` 1.1.0 adds libraries, the gesture map and alignment parameters.
* Editor project schema v4: `hand_store.libraries`, `hand_selection` (`handselect.action|weights`). See
  [hand_library_replacement_report.md](hand_library_replacement_report.md).

## 16. Phase 3.3: garment rig

* `src/autorig/garmentrig/structure.py` `GarmentStructureAnalyzer` labels every layer pixel (originals, hidden
  extensions, switch drawings) with a semantic garment part (41 on the schoolboy) of class RIGID,
  LIMITED_DEFORMATION or FLEXIBLE, with allowed bones, attachments and fold regions. `align_pivots` moves the shoulder
  pivots toward the armhole top (`Bone.axis_offset` keeps the anatomical axis; `ik.solve_axis_aware` shifts the IK
  limits per bend side) and `validate_pivots` checks every limb pivot against the artwork.
* `garmentrig/weights.py` `WeightSet`: per-layer grid meshes of normalised, restricted, lockable bone weights from the
  parts and joint planes (narrow per-class transitions, smoothed LIMITED torso family); user edits are `weights.*`
  editor ops (schema v5) replayed deterministically; `explain` reports why a point follows its bones.
* `garmentrig/deform.py` `StructuredDeformer` (a `MotionEngine(garment_mode="structured")` default): weight-mesh
  angle interpolation for the joint patch, rigid segments (sleeves, trouser shin) and rigid fits (badge, buttons,
  tie, pocket flaps) drawn over it, generated pixels drawn beneath the layer that hid them at rest, and a stretch limit
  that leaves an over-stretched patch undrawn (a reported conflict). `garment_mode="legacy"` keeps the Phase 3.1
  deformer for comparisons.
* `garmentrig/corrections.py` `GarmentCorrectionController` (extends the Phase 3.2 controller) and
  `garmentrig/evaluate.py` `GarmentDistortionEvaluator` (per part/garment PASS/WARNING/NEEDS_ARTWORK/FAIL);
  `garmentrig/evidence.py` (`autorig garment-evidence`); `garmentrig/moho.py` (compatibility matrix,
  `proposed_unverified`). Editor: Weights tab (`WeightsPanel.tsx`), `/api/garments*`. Export: `garment_rig.json`,
  `garment_weights.npz`. See [phase3_3_clothing_deformation_report.md](phase3_3_clothing_deformation_report.md).

## 17. Phase 3.4: corrective artwork

`src/autorig/garmentrig/correctives.py` holds the main pieces.

**Store.** `CorrectiveArtworkStore` is append-only:
- versioned PNGs in `<package>/artwork/correctives/<category>/`;
- an index with hashes and provenance in `analysis/corrective_assets.json`;
- requirements in `analysis/corrective_requirements.json`;
- pose-space keys in `analysis/pose_space_correctives.json`;
- the quality report in `reports/corrective_quality.json`.

**Generators.** They build everything from the character's own pixels and call no AI model:
- `ShoeTopRecovery`: the shoe top hidden under the hem, drawn beneath the foot layer;
- `JointCapGenerator`: elbow and knee caps, composited over the empty pixels and the limb's own joint patch, beneath everything else, and styled at render time;
- `ArmpitCorrectionGenerator`: a procedural recipe that runs `armpit_fill` on the renderer's stretch-clipped gap, composited beneath everything drawn, with an outer curve tangent to the torso side and the sleeve underside; baked key previews are kept for review and Moho;
- `EdgeDecontamination`: derived layers only. Silhouette pixels lighter than the outline inside them become outline colour with alpha, solved so that over the paper they reproduce the source.

**Activation and planning.**
- `PoseSpaceCorrectionEngine` handles eased ramps, normalised RBF keys, exclusive groups, artist overrides, extrapolation flags and a zero weight in the neutral pose.
- `CorrectiveRuntime` turns its output into renderer pieces, gap fills and the derived-layer swap.
- `CorrectiveArtworkPlanner` writes requirement records; it never invents artwork.

**Rendering.** `StructuredDeformer` draws each corrective beneath every original pixel. The prevented overlap is counted; the leakage itself is measured by diffing renders with correctives off and on.

**Structural fixes** in `deform.py` and `structure.py`:
- The upper-sleeve segment starts on a curved armhole line, cross-faded with the arm angle.
- The shin segment starts at the knee band end.
- Jaw/ear fragments left in the torso layer are relabelled `head` and drawn rigidly with the head, cut from the patch.
- The collar and lapels no longer take the clavicle shrug.

**Evaluation.** `evaluate.py` adds a local-deviation significance check, defect categories and `review_required`. `corrective_evidence.py` (`autorig corrective-evidence`) adds `CorrectiveQualityEvaluator`, which tracks classes A–G, plus the combined-pose sequences and popping checks.

**Editor.** The Correctives tab (`CorrectivesPanel.tsx`) uses `/api/correctives*` and the project ops `correctives.mode|set|reset` (schema v6). Approvals are `approval.set` on `corrective:<id>@v<N>`.

See [phase3_4_corrective_artwork_report.md](phase3_4_corrective_artwork_report.md).

## 18. Phase 3.5: artistic posing

`src/autorig/artistic/` sits on top of the motion pipeline; it adds no second solver.

- **Posing and assessment:**
  - `poses.py`: `ArtisticPose` and `ArtisticPosePlanner` reshape a preset's `PoseIntent` through
    `MotionEngine.pose(intent_fn=...)`: weight leg, contrapposto, line of action, head counterbalance, gestures with
    hand actions. It also holds the action library with candidate variants.
  - `silhouette.py`: `SilhouetteQualityEvaluator`, solid-silhouette readability.
  - `feasibility.py`: `ArtworkFeasibilityEngine`, five classes, decided before deformation.
  - `candidates.py`: `PoseCandidateGenerator`, explicit ranking.
- **Artwork and draw order:**
  - `joint_art.py`: procedural elbow and knee fold corrections.
  - `layer_order.py`: `DynamicLayerOrderController` with sublayer splits; `MotionEngine.pose(layer_order=..., depth=...)`.
  - `alternatives.py`: alternative drawing store and controller (`MotionEngine.pose(layer_variants=...)`), artwork
    requests, view manifest, limb configurations.
  - The `ShoulderSilhouetteController` gusset recipe lives in `garmentrig/correctives.py`.
- **Motion and review:**
  - `transitions.py`: acting sequences with eased, overlapping, anticipated and settled transitions.
  - `evaluate.py`: `ArtisticAnimationEvaluator`.
  - `feedback.py`: the phrase table that turns notes into constraints.
  - `evidence.py`: `autorig artistic-evidence`.
- **Editor:** the Artistic tab (`ArtisticReviewPanel.tsx`), `/api/artistic*`, and `artistic.*` ops (schema v7).

See [phase3_5_artistic_animation_report.md](phase3_5_artistic_animation_report.md).

