# Phase 4.1: architecture plan (full-body native rig)

Written before the native builder, as Step 2 of the Phase 4.1 request requires. Date: 2026-11-23.

## 0. Starting point: Phase 4.0 is not verified

Latest native evidence: `docs/phase4/evidence/native_runs/`, two runs on 2026-11-23, Moho 14.5.1. Assessment:
`docs/phase4_completion_report.md`, `docs/phase4/native_assessment/20261123/`.

| gate | requirement | status | missing evidence |
|---|---|---|---|
| 1 | native document created | **PASS** | — |
| 2 | bone moves bound artwork in genuine Moho renders | **NOT_EXECUTED** | Moho renders of frames 1 and 12 |
| 3 | document saves correctly | **PASS** | — (the saved file sits in the other run's folder; the combined evaluation is documented) |
| 4 | document reopens successfully | **NOT_EXECUTED** | the verify report written by Moho on the reopened file |
| 5 | bone animation works after reopening | **NOT_EXECUTED** | that verify report and the frame-18 render |

No gate failed, and none has been redefined. Phase 4.0 is still incomplete, so this phase does **safe, preparatory work
only**:
- everything that runs in the cloud;
- native scripts that use only the operations Phase 4.0 already ran in Moho 14.5.1 (bone layer, bones, image layers,
  layer binding, angle keys, read-back);
- operations that are not verified natively (IK targets, joint constraints) isolated in a separate, opt-in
  compatibility script with its own native test.

Nothing in Phase 4.1 can be marked as passing natively until a Mac run produces the evidence.

## 1. What the character package holds (Phases 1–3.8)

| data | where | used by the native rig |
|---|---|---|
| source image and original layers (category A) | `source/`, `artwork/original/*.png`, `artwork/manifest.json` (z order, pixel hashes) | **directly**: one image layer per layer, full canvas, unscaled |
| the user's mask edits | `editor/project.json` (`mask_ops`) | **directly**: the layers come from the editor's `ArtworkModel` (identical to the originals when unedited, as checked on both packages) |
| anatomical landmarks | `analysis/anatomy.json` | indirectly, through the skeleton built from them in Phase 2 |
| skeleton and pivots | `natural_pose/rig.json` (19 bones, pivots, limits), the editor's bone edits, the garment-aligned shoulder pivots (Phase 3.3 `align_pivots`) | **converted**: semantic bones (§3) |
| joint limits | `rig.json` `limits_deg` (anatomical parameters: abduction, flexion, elevation, bend, tilt) | **converted** to in-plane Moho ranges (offsets from rest); per-character overrides in `natural_pose/native_rig.json` |
| layer to bone assignment | `rig.json` `layer_bindings` (class and home bones), the manifest's `bone` | **converted** to one Moho bone per layer (§4) |
| garment classification | `natural_pose/garments/structure.json` (41 parts with RIGID / LIMITED_DEFORMATION / FLEXIBLE and allowed bones) | **converted** to the binding class of each layer, kept as metadata |
| garment weight meshes | `natural_pose/garments/weights.json` | **unsupported**: no evidenced Lua API binds an image layer with per-vertex weights, and the weights belong to AutoRig's deformer, not to Moho |
| structured deformer (joint patches, rigid segments) | `garmentrig/deform.py` | **unsupported** natively; with rigid binding, a joint seam opens when the joint bends. That is corrective work (Phase 4.2) |
| reconstructed (generated) pixels | `artwork/reconstructed/` (extensions, all pending review) | not imported by default. `--include-pending-extensions` adds them beneath their originals for review builds, labelled pending |
| corrective artwork | `artwork/correctives/` (pending) | **metadata only** (class CORRECTIVE, approval state); the import is Phase 4.2 |
| alternative drawings | `artwork/alternatives/` (pending) | metadata only |
| hand store and hand metadata | `hand_store/`, `analysis/hand_asset_metadata.json` (16 drawings, all pending) | **metadata only** (class SWITCHABLE): wrist pivot, side, handedness, palm view, approval. Switch Layers are Phase 4.3 |
| pose presets, performance, reference motion | `natural_pose/presets.json`, `performance/`, `motion_references/` | not transferred. Phase 4.1 keys its own mechanical test poses |
| rig controls / Moho mappings | `rig_controls`, `hand_switches.json`, the garment Moho matrix | `proposed_unverified`; superseded for the skeleton by the native spec |
| project versioning | `editor/project.json` (op log, versions) | recorded: the package and project hashes go into the native manifest, so a build is tied to one project state |

AutoRig's deformation engine and Moho do **not** share data structures. AutoRig moves pixels by position-based home
bones with angle-interpolated bands and weight meshes. A Moho image layer bound with `SetLayerParentBone` follows one
bone rigidly. Everything below is designed for the second model.

## 2. Pipeline

```
character package
  -> NativeSkeletonSpec.from_package   (native/skeleton.py: semantic bones, ranges, controls, IK roles)
  -> NativeBindingStrategy.plan        (native/binding.py: one bone and one class per layer)
  -> Placement                         (integer offset, canvas = source size by default; never a scale)
  -> Phase 0 rig spec                  (bones in canvas pixels, parts, rotation keys for the test poses)
  -> Lua: build (the Phase 0 template, renamed)  + verify-after-reopen (4.1)  + IK/constraints (opt-in, experimental)
  -> native_fullbody_manifest.json, expected (simulated) frames, validation plan
  -> Mac: tools/macos/phase4_native_test.sh --phase 4.1  ->  evidence  ->  autorig native-fullbody-verify (12 gates)
```

The build script stays the Phase 0 template. That code ran unchanged in Moho 14.5.1 (Phase 4.0 gate 1), so the
full-body build uses only operations that are already native-verified, just more of them.

## 3. Native skeleton

There are 25 bones, parents first: `ROOT`, `PELVIS`, `SPINE_LOWER`, `SPINE_UPPER`, `CHEST`, `NECK`, `HEAD`, then per
side `_SHOULDER`, `_UPPER_ARM`, `_FOREARM`, `_HAND`, `_THIGH`, `_SHIN`, `_FOOT`, then the targets `_HAND_TARGET` and
`_FOOT_TARGET`.

| semantic bone | from the package | note |
|---|---|---|
| ROOT | `midline_x` at `ground_y` (rig.json), pointing up, 12 % of the character's height | ROOT_CONTROL |
| PELVIS | `pelvis` | PELVIS_CONTROL; the legs hang from it |
| SPINE_LOWER, SPINE_UPPER | `spine`, split at its midpoint (`spine_split`) | the request asks for two spine bones; the package has one |
| CHEST, NECK, HEAD | `chest`, `neck`, `head` | HEAD_CONTROL |
| `<S>_SHOULDER`, `<S>_UPPER_ARM` | `clavicle_<s>`, `upper_arm_<s>` with the garment-aligned pivot | the same skeleton AutoRig renders with |
| `<S>_FOREARM`, `<S>_HAND` | `forearm_<s>`, `hand_<s>` | IK effector: FOREARM |
| `<S>_THIGH`, `<S>_SHIN`, `<S>_FOOT` | `thigh_<s>`, `shin_<s>`, `foot_<s>` | IK effector: SHIN |
| `<S>_HAND_TARGET`, `<S>_FOOT_TARGET` | at the wrist / ankle, child of ROOT | IK targets, linked only by the opt-in script |

LEFT is the character's left (image right), as in every AutoRig phase. The Moho bone names are the semantic ids, so
the rig spec schema now allows upper-case bone names. Other identifiers are unchanged.

## 4. Binding strategy

Native binding is rigid layer binding (`SetLayerParentBone`). It is the only binding Phase 4.0 exercised in Moho, so
every imported layer uses it. The *class* records what the layer really needs, so that Phase 4.2 can upgrade it:

| class | layers (by package class) | native now | later |
|---|---|---|---|
| RIGID | head parts, feet (shoes) | layer binding to HEAD / FOOT | — |
| LIMITED_DEFORMATION | torso → CHEST, neck → NECK, pelvis → PELVIS | layer binding | smooth binding or correctives if evidenced |
| FLEXIBLE | upper arm, forearm, thigh, shin | layer binding to the limb bone; the seam opens at the joint | correctives / Smart Bones (4.2) |
| SWITCHABLE | hands | layer binding to HAND; switch metadata kept | Switch Layers (4.3) |
| CORRECTIVE | corrective assets (pending) | not imported | 4.2 |

The torso layer goes to CHEST: the arms and the neck hang from CHEST, so a spine bend moves the torso artwork with
them. A layer is never bound to two bones, and global flexible binding is never used.

## 5. Coordinates

`Placement(canvas, offset)` is an integer translation. By default the canvas is the source size, so the offset is
(0, 0) and the Moho document must use the package's size; the build script refuses another size. Pixel → Moho is
`geometry.px_to_world` (frame height 2, +y up). Parent-relative values come from `geometry.compute_rest_pose`, which
Moho reproduced in Phase 4.0. Phase 4.0 also showed that full-canvas images become frame-sized layers
(3.555556 × 2.0 units for 1920×1080), so no image scale is applied anywhere. Round-trip tests cover several canvas
sizes and offsets.

## 6. Controls, IK and constraints

| control | bone | how it works now |
|---|---|---|
| ROOT_CONTROL | ROOT | FK: moves the whole character |
| PELVIS_CONTROL | PELVIS | FK: moves the upper body and both legs |
| HEAD_CONTROL | HEAD | FK |
| torso | SPINE_LOWER, SPINE_UPPER, CHEST | FK |
| LEFT/RIGHT_HAND_TARGET | target bones at the wrists | **inert** until the opt-in script sets the forearm's `fTargetBone` |
| LEFT/RIGHT_FOOT_TARGET | target bones at the ankles | **inert** until the script sets the shin's `fTargetBone` |

Members needed for IK and constraints: `M_Bone.fTargetBone` (AnimVal), `fConstraints`, `fMinConstraint`,
`fMaxConstraint`.
- They are documented in the scripting reference (search snippets only; the site is blocked here).
- Moho's own saved file has the matching keys (`target_bone`, `constraints`, `min_constraint`, `max_constraint`).
- They join the allowlist with that evidence and a mock implementation, and only the opt-in script uses them.

The meaning of the constraint angles (absolute parent-relative angle, or offset from rest) is **not confirmed**. The
script writes absolute values (rest + range), records that assumption, and the native test checks it. AutoRig's own
analytic IK solver is **not** copied into Moho: Moho's solver behaviour has to be measured.

## 7. Test poses and validation

Twelve key frames, all rotations of semantic bones, with signs derived from each bone's own direction (no character
numbers):
1. neutral (1);
2. left arm raised (10);
3. right arm raised (20);
4. both arms lowered (30);
5. left elbow bent (40);
6. right elbow bent (50);
7. left leg lifted sideways (60);
8. right knee bent in plane (70);
9. pelvis shifted (80, added after reopening as a position key, which tests editing after reopen);
10. torso leaned (90);
11. head tilted (100);
12. neutral again (110).

The opt-in IK script adds target moves at frames 120–150. The Mac harness (Phase 4.1 mode):
- builds the rig, saves, closes, reopens and verifies;
- renders frames 1–110 as one PNG sequence;
- `autorig native-fullbody-verify` evaluates gates 4.1-A … 4.1-L from that evidence.

Mock reports and simulated frames can never pass a gate.

## 8. Known limits, stated before building

- Rigid binding opens seams at the shoulder, elbow, hip and knee when they bend. Correctives are Phase 4.2.
- Front-view art: real knee flexion, deep sitting and turning the head need other views. The knee test pose is an
  in-plane mechanical test.
- Generated (pending) pixels are not imported by default, so gaps appear where limbs uncover the body.
- Feet and hands rotate with their parents. Moho's "independent angle" (the file shows `fixed_angle`) has no
  evidenced Lua member yet.
- Performance figures need the M1. Nothing measured in the cloud counts as one.
