# Phase 4.1: rig architecture

The full-body native rig is built by `autorig native-fullbody` (`src/autorig/native/`). Native status: **not executed in
Moho**; see [`phase4_1_native_validation.md`](phase4_1_native_validation.md). The plan written before the code is in
[`phase4_1_architecture_plan.md`](phase4_1_architecture_plan.md).

## 1. Modules

| module | role |
|---|---|
| `native/skeleton.py` | `NativeSkeletonSpec` (semantic bones, ranges, IK roles, controls), `Placement` (character → canvas → Moho), `validate` |
| `native/binding.py` | `NativeBindingStrategy` (one bone and one class per layer), switchable / corrective metadata |
| `native/fullbody.py` | the package: art, rig spec, test poses, scripts, simulation, manifest (`build_fullbody`) |
| `native/gate41.py` | gates 4.1-A … 4.1-L from native evidence, comparison sheets |
| `data/templates/phase0_build.lua` | build script, unchanged (renamed `AutoRig_P41_FullBodyBuild`) |
| `data/templates/phase4_1_fullbody_verify.lua` | verify after reopening, pelvis keys, read-back, member discovery |
| `data/templates/phase4_1_ik_constraints.lua` | opt-in, experimental: target links, angle limits, IK and limit test keys |

## 2. Semantic skeleton (25 bones)

Bones are listed parents first, which is also the Moho bone ID order (0 … 24). Every position comes from the
character's own package: the editor's skeleton (the package rig plus the user's bone edits) with the Phase 3.3
garment-aligned shoulder pivots. That is the same skeleton AutoRig renders with.

```
ROOT  (ROOT_CONTROL)
├── PELVIS  (PELVIS_CONTROL)
│   ├── SPINE_LOWER ── SPINE_UPPER ── CHEST
│   │                                 ├── NECK ── HEAD  (HEAD_CONTROL)
│   │                                 ├── LEFT_SHOULDER ── LEFT_UPPER_ARM ── LEFT_FOREARM ── LEFT_HAND
│   │                                 └── RIGHT_SHOULDER ── RIGHT_UPPER_ARM ── RIGHT_FOREARM ── RIGHT_HAND
│   ├── LEFT_THIGH ── LEFT_SHIN ── LEFT_FOOT
│   └── RIGHT_THIGH ── RIGHT_SHIN ── RIGHT_FOOT
├── LEFT_HAND_TARGET, RIGHT_HAND_TARGET     (at the wrists)
└── LEFT_FOOT_TARGET, RIGHT_FOOT_TARGET     (at the ankles)
```

Rotating LEFT_UPPER_ARM moves only LEFT_FOREARM and LEFT_HAND below it (tested: `descendants`). The right arm and the
torso layers are not its descendants. The targets are children of ROOT, so moving the pelvis does not drag them.

Each bone records (`native_skeleton.json`):
- its id and Moho name, parent, and source (the package bone it came from);
- base and tip in character space, length, and rest angle;
- the in-plane range, with where the range came from;
- its IK role (chain, root, effector, target);
- its control role;
- its bound layers.

Measured lengths (px):

| bone | schoolboy | tough student |
|---|---|---|
| PELVIS / CHEST / HEAD | 112.7 / 116.2 / 301.6 | 124.8 / 128.6 / 290.4 |
| LEFT_UPPER_ARM / LEFT_FOREARM | 127.9 / 115.2 | 132.9 / 120.3 |
| LEFT_THIGH / LEFT_SHIN | 272.0 / 271.9 | 248.8 / 248.7 |

## 3. Joint ranges (in-plane, degrees from rest, CCW positive in Moho)

The ranges are converted from the package's anatomical limits (`rig.json` `limits_deg`). Signs come from each bone's
direction, so they hold for any character:

| joint | source | schoolboy, left side |
|---|---|---|
| pelvis tilt, chest, neck, head bend | `tilt` / `bend` | ±14, ±14, ±20, ±28 |
| spine (split in two) | `bend` × the split fraction | ±9 each |
| shoulder (clavicle) | `elevation`, up = tip up | −10 … +32 |
| upper arm | `abduction` from hanging, minus the rest abduction | −114.3 … +103.7 |
| elbow | `flexion` toward the head; hyperextension 10° (config) | −10 … +150 |
| wrist, ankle | `bend` | ±60 |
| hip | `abduction` from vertical, minus the rest abduction | −42.9 … +57.1 |
| knee | front-view stylised squat bend (config): shin toward the midline 45°, outward 5° | −45 … +5 |

`natural_pose/native_rig.json` (or `--config`) overrides any range per character. A test checks that every rest pose
and every test pose stays inside its ranges.

## 4. Coordinates

```
character px --(+ integer offset)--> canvas px --(px_to_world: (x - W/2)/(H/2), (H/2 - y)/(H/2))--> Moho world
world --(compute_rest_pose)--> parent-local fPos, fAngle, fLength (the values Moho read back in Phase 4.0)
```

- **Scale is applied once.** Image layers keep the frame size: Moho stored 3.555556 × 2.0 units for a 1920×1080 image
  in Phase 4.0. Bone lengths are pixel lengths ÷ (H/2); a test checks `length × H/2 == length_px` for several canvases.
- **Rotation is applied once.** Angles are parent-relative; FK of the rest values reproduces every world base (tests at
  three canvas sizes).
- **Canvas size.** It is chosen automatically to hold every test pose and IK test. Each layer's alpha box is moved by
  its bone, and the union plus a 24 px margin is taken. The canvas is never smaller than the source. Schoolboy:
  1270 × 1486, offset (87, 38). Tough student: 1230 × 1484, offset (76, 35). `--canvas WxH` is accepted only if
  nothing would be cropped.
- **Round trips.** `Placement.to_moho` / `from_moho` and `to_canvas` / `from_canvas` are tested at four canvas sizes
  and offsets, including a negative offset.

## 5. Controls

| control | bone | use |
|---|---|---|
| ROOT_CONTROL | ROOT | move or rotate the whole character |
| PELVIS_CONTROL | PELVIS | move or tilt the hips; the upper body and both legs follow (FK) |
| torso | SPINE_LOWER, SPINE_UPPER, CHEST | bend and lean; arms, neck and head follow |
| neck, HEAD_CONTROL | NECK, HEAD | tilt within ±20° / ±28° (front-view art: no turns) |
| LEFT/RIGHT_HAND_TARGET | wrist targets | IK for the arm chains once linked (experimental script); inert otherwise |
| LEFT/RIGHT_FOOT_TARGET | ankle targets | IK for the leg chains once linked; inert otherwise |
| limbs (FK) | UPPER_ARM, FOREARM, HAND, THIGH, SHIN, FOOT | rotate with Manipulate Bones |

No other auxiliary bones exist. All control names are the semantic ids, in upper case.

## 6. What the rig can absorb later without a rebuild

- **Correctives and Smart Bones (4.2):** the binding plan lists 15–19 pending corrective assets per character and the
  joints they belong to. The bones they will key already exist.
- **Switch Layers (4.3):** each HAND bone has its wrist pivot and bound layer recorded, plus 18 hand variants
  (all pending) with side, handedness, palm view and approval item.
- **IK and limits:** the target bones exist. Linking them is one script run, and does not change the rest pose.
