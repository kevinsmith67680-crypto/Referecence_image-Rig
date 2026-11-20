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
