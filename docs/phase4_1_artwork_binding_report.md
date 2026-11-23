# Phase 4.1: artwork import and binding

Native status: **not executed in Moho**. The values below come from the packages built in the cloud
(`docs/phase4_1/cloud_checks/<character>/binding_plan.json`).

## 1. Artwork import

| rule | how it is met | checked by |
|---|---|---|
| the character's own segmentation | one Moho image layer per visible bitmap layer of the editor project (25 for both characters; another character can have more or fewer) | `test_package_artwork_is_original_unscaled_and_complete` |
| original pixels | layers come from the editor's `ArtworkModel` without extensions; with no mask edits they are byte-identical to `artwork/original/*.png` (pixel hashes match the manifest; `all_originals_unchanged: true`) | same test |
| no scaling, no cropping | each layer is pasted on the canvas at an integer offset. The canvas is sized to hold every test pose; every alpha pixel survives | same test, `test_every_test_pose_stays_on_the_canvas` |
| transparency | RGBA PNG per layer, alpha unchanged | pixel equality |
| rendering order | the editor's layer order. The build places layers back to front with `PlaceLayerInGroup(top = true)`; Phase 4.0 showed index 0 is the first layer created | binding plan `z_index`, mock read-back |
| no duplicate artwork | one file per layer id; generated pixels only with `--include-pending-extensions`, as separate `<id>__ext_pending` layers beneath their originals | binding plan |
| hidden regions | pending generated pixels are **not** imported by default, so gaps open where limbs uncover the body. That is visible in the simulation and expected | `SIMULATION_pose_sheet.jpg` |
| paths valid after reopening | absolute paths (Moho stores `relativeTo: Absolute` plus a relative `complex_path`); the art stays in `build/phase4_1_native/<character>/art/` | native: reopen check (not run) |
| original reference in the package | `reference/original.jpg` (a byte copy of the package's `source/`) | `test_package…` |

## 2. Binding strategy

| class | schoolboy / tough student | layers → bone | native method |
|---|---|---|---|
| RIGID | 12 / 12 | 10 head parts → HEAD; feet → `<S>_FOOT` | layer binding |
| LIMITED_DEFORMATION | 3 / 3 | torso → CHEST, neck → NECK, pelvis → PELVIS | layer binding (fallback) |
| FLEXIBLE | 8 / 8 | upper arms, forearms, thighs, shins → their bones | layer binding (fallback) |
| SWITCHABLE | 2 / 2 | hands → `<S>_HAND` | layer binding; switch metadata kept |
| CORRECTIVE | metadata only (15 / 19 pending assets) | — | not imported (Phase 4.2) |

Decisions:
- **One bone per layer**, chosen from the package (`layer_bindings` class, or the manifest's bone). The nearest home
  bone is used only as a fallback.
- **The torso goes to CHEST**, not to the spine. The arms and the neck hang from CHEST, so a spine bend keeps the torso
  artwork with them. A waist seam opens instead; it is visible at frame 90 of the simulation.
- **No double transformation.** Layers sit inside the bone layer with zero translation, unit scale and the frame size.
  Only the bound bone moves them.
- **No global flexible binding.** AutoRig's weight meshes are **not converted**: no evidenced Lua API binds an image
  layer with per-vertex weights, and Moho's image-layer bone deformation is unverified. The documented fallback is
  rigid layer binding. FLEXIBLE layers open a seam at their joint (shoulder, elbow, hip, knee): Phase 4.2 work with
  correctives and Smart Bones.

## 3. Garment integration (from Phases 3.3–3.5)

`binding_plan.json` lists, for every layer, the garment parts whose allowed bones include the layer's bone, with their
classes. The 41 parts of `natural_pose/garments/structure.json` are used as follows:

| garment concern | native rig now |
|---|---|
| blazer body stability | the torso layer is rigid on CHEST, so the badge, buttons, lapels and pocket flaps stay rigid as in the AutoRig rules |
| sleeve attachment | the sleeves are part of the arm layers, rigid on their bones. The shoulder seam opens beyond about 30° (see the simulation of frames 10, 20 and 30) |
| trousers | thigh and shin layers rigid on their bones; the knee seam opens in the knee test (frame 70) |
| shirt collar | part of the torso and neck layers, rigid |
| shoes | foot layers RIGID on FOOT |
| decorative details | rigid with their layer, never deformed |

Each garment part's class and allowed bones are kept, so Phase 4.2 can attach correctives and Smart Bone actions
without rebuilding the rig.

## 4. Hand-switch readiness

| | LEFT | RIGHT |
|---|---|---|
| bone | LEFT_HAND (wrist pivot = its base) | RIGHT_HAND |
| bound layer | `hand_l` (the original drawing) | `hand_r` |
| variants recorded (schoolboy) | 9: side, handedness, palm view, gesture, approval item | 9 |
| approved variants | **0** (all pending; none approved in code) | 0 |

The original hand stays bound to the HAND bone. A future Switch Layer (Phase 4.3) attaches at the same pivot.

## 5. What a native run must still show

- the bindings read back by Moho (gate 4.1-C) and the artwork moving with them in renders;
- the image scale in the saved file and in the neutral render (gate 4.1-B);
- that no media go missing after reopening (verify read-back and your answer).
