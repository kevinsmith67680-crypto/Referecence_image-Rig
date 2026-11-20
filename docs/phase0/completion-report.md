# Phase 0 completion report: Moho rigging proof of concept

| | |
|---|---|
| **Verdict** | **PARTIAL** |
| Why not PASS | The native Moho run, the core claim of Phase 0, has **not been executed**. The cloud sandbox has no Moho and no macOS. |
| Why not FAIL | Every deliverable is implemented. All 173 cloud tests pass, the generated script runs end to end against a strict mock of the evidenced Moho API, and mutation checks show the suite catches realistic defects. |
| Branch | `claude/eloquent-cori-iv4ab1` |
| Gate | **Phase 1 (SAM 3) stays blocked** until the checks in [manual-verification.md](manual-verification.md) pass on the Mac. |

## 1. Objectives and deliverables

| # | Deliverable | Status | Where |
|---|---|---|---|
| — | Repository structure, CLAUDE.md, README, architecture docs | Done | `CLAUDE.md`, `README.md`, `docs/architecture.md` |
| — | Investigate Moho 14 Lua API and MohoMCP | Done (within network limits, see §3) | `docs/moho-lua-api-evidence.md`, `docs/mohomcp-assessment.md`, `src/autorig/data/moho_api_allowlist.json` |
| 1 | Four separate transparent artwork layers | Done | `examples/phase0_test_character/art/{torso,head,upper_arm,forearm}.png` (1920×1080 RGBA) and `.svg` vector sources |
| 2 | JSON rig specification | Done | `examples/phase0_test_character/rig_spec.json`, schema `src/autorig/data/rig_spec.schema.json` |
| 3 | Lua script that creates the Moho bone hierarchy | Done; cloud-verified against the mock only | Template `src/autorig/data/templates/phase0_build.lua`; reference build `examples/phase0_test_character/moho/AutoRig_Phase0_Build.lua`; machine build via `tools/macos/install_phase0.sh` |
| 4 | Correct layer-to-bone bindings | Done; cloud-verified against the mock only | `SetLayerParentBone` per layer; checked by `verify-report` |
| 5 | 36-frame test animation | Done; simulated only | Spec `animation`; `preview/phase0_preview.gif` (simulation) |
| 6 | Installation instructions for Moho Pro 14 on macOS | Done; **not tried on a Mac** | `docs/phase0/install-macos-moho14.md` |
| 7 | Automated validation tests | Done (cloud); native tests **NOT EXECUTED** | `tests/` (173 pass); `tests/native/` (2 skipped) |
| 8 | Detailed completion report | This document | — |

## 2. Implemented functionality

**Rig spec (JSON):** canvas size; bones as pixel joints (`base_px`, `tip_px`,
`parent`); parts (layer name, PNG, SVG, bound bone, `z_index`); Moho settings
(bone-layer name, frame range, report name); and animation tracks
(`rotation_offset_deg` keys). It is validated by JSON Schema 2020-12 plus
semantic checks: parents declared first, unique names and z-order, known bone
references, non-zero bone length, strictly increasing keys within 1–36, and
relative paths without `..`.

**Test character:** torso (root, hips → neck), head (neck → crown),
upper_arm (shoulder → elbow) and forearm (elbow → wrist, with hand). Each part
is a full-canvas transparent PNG drawn at 3× supersampling, with round joint
caps centred on the bone pivots, and the geometry is derived from the bones.
Tests assert that every pivot is opaque in its own layer **and** in its parent
layer, so no gap opens at a joint.

**Geometry (Python):** pixel → Moho units (frame height = 2 units), world
joints → parent-local `fPos`/`fAngle` (Moho convention, see the evidence doc),
forward kinematics, and the expected `fAnimAngle` keys. A property test inverts
FK for 25 random 12-bone hierarchies to 1e-9.

**Generated Moho menu script** (`AutoRig_Phase0_Build.lua`, Lua 5.4):

- **Preflight**, which changes nothing on failure: document open; asset path
  baked in; no existing `AutoRig_Phase0` layer; project 1920×1080; all four
  images exist.
- **Build:** a bone layer and four bones in parent order (setting `fParent` and
  `fAnimParent`, `fPos` and `fAnimPos`, `fLength`, `fAngle` and `fAnimAngle`,
  then `UpdateBoneMatrix`). Four image layers are created back to front via
  `LayerAsImage():SetSourceImage`, moved into the bone layer with
  `PlaceLayerInGroup` and bound with `SetLayerParentBone`. The script then sets
  20 animation keys (frames 1, 10, 19, 28, 36 × 4 bones) and the timeline 1–36.
- **Read-back from Moho's own objects:** bone fields, world base/tip through
  `fMovedMatrix`, all angle keys, layer types, bindings and child order. These
  are written to `phase0_moho_report.json` with a built-in JSON encoder.
- **Error handling:** API errors are caught, recorded in the report, logged to
  the console and shown in an alert. They are never swallowed.
- **API surface:** only allowlisted members, each with cited evidence (58 at Phase 0 completion; the list later grew to 64 with read-only calls used only by the Live Models probe).

**Verification tooling:** `autorig verify-report` (54 checks for this rig,
covering status, document, bone layer, per-bone id, parent, `fPos`, `fAngle`,
`fLength`, world matrix and keys, and per-layer type, binding and image),
`expected_rig.json`, and a Python FK preview renderer (contact sheet and GIF,
labelled as a simulation).

**macOS installer:** `tools/macos/install_phase0.sh` (bash 3.2 compatible)
validates, generates the script with the absolute SSD path, and installs it into
the Custom Content Folder. It refuses to write into `/Applications` or any
`.app` bundle.

**CI:** a GitHub Actions workflow on `ubuntu-latest` and `macos-14` (Apple
Silicon) runs the suite and an installer dry run.

## 3. Research findings (summary)

- Moho 14 embeds **Lua 5.4**, according to mohoscripting.com and MohoMCP.
- `mohoscripting.com` and `lostmarble.com` were **unreachable** from the sandbox.
  API facts come from web-search snippets of those sites plus **three working
  community codebases** read in full (SimplSam, Strider22, MohoMCP). All 58
  members used have at least one source, recorded per member.
- **Critical convention** (from SimplSam's code): a child bone's `fPos` is in
  the parent's local frame, so a child on the parent's tip has
  `fPos = (parentLength, 0)`. This is implemented in Python and is checked
  natively by `verify-report`'s `world_matrix` check.
- **Not used for lack of evidence:** `FileSave*`/`FileNew`, `SetKeyInterp`
  (conflicting usages) and `SourceImage()`.
- **MohoMCP** cannot create layers, bones or bindings. It is useful for read-back,
  keyframe inspection and screenshots, and is optional for Phase 0. Its installer
  writes into the app bundle; ours deliberately does not.

## 4. Test results (cloud)

Environment: Linux sandbox, Python 3.12.3, Lua 5.4 (embedded via `lupa` 2.8),
Pillow 13.0.0, pytest 9.2.0.

```
173 passed, 2 skipped in 5.95s
SKIPPED [2] tests/native/test_moho_native.py: NOT EXECUTED: requires Moho Pro 14 on macOS.
```

| Test file | Tests | Covers |
|---|---|---|
| `test_spec.py` | 26 | Example loads; 7 schema rejections; 12 semantic rejections; hash stability |
| `test_geometry.py` | 44 | px↔world mapping; rest-pose values; FK round trip for 25 random hierarchies; joint continuity on all 37 frames; key values; pivot-preserving layer transform |
| `test_artwork.py` | 24 | 1920×1080 RGBA; transparent background; pivots inside own and parent art; committed PNGs reproducible from code; SVG structure |
| `test_lua_codegen.py` | 20 | Lua string escaping for SSD paths (spaces, accents, quotes, NUL), bit-exact floats, Lua 5.4 compilation, ScriptName = file name, reference build in sync, static allowlist scan |
| `test_lua_moho_mock.py` | 23 | **Mock surface == allowlist**, evidence for every member, 9 unsupported-API rejections, full build (layer tree, bones, keys, document state, report), 4 refusal paths, API-error path, JSON encoder |
| `test_report.py` | 26 | Correct report passes; 19 injected defect classes each caught; warnings for extra keys, spec drift and Lua version; garbage input |
| `test_preview_and_cli.py` | 10 | Rest frame equals plain composite; pixel-level elbow integrity at frames 10, 19, 28; committed previews and expected JSON in sync; CLI exit codes |
| `native/test_moho_native.py` | 2 | **NOT EXECUTED**: verify Moho's real report; Lua 5.4 inside Moho |

**End-to-end in the mock:** the generated script, run in Lua 5.4 against the
strict mock, writes a report, and `verify-report` returns **VERDICT: PASS (50
pass, 0 fail, 0 warn, 4 info)**.

**Mutation checks** (manual run; each defect injected separately, then reverted):

| Injected defect | Result |
|---|---|
| `SetSourceImage` without the `LayerAsImage` cast | caught (8 failures) |
| Every layer bound to bone 0 | caught (3) |
| Call to the undocumented `moho:FileSave()` | caught (5) |
| Key values doubled | caught (3) |
| `PlaceLayerInGroup` omitted | caught (7) |
| Wrong rotation sign in parent-local `fPos` | caught (35) |

**Other checks performed:**

- A clean non-editable `pip install` of the package works, and the template,
  schema and allowlist ship as package data.
- The installer was run in the sandbox: generate-only, install into a temporary
  folder, refusal of an `.app` path before any work, and a bad argument (exit 2).

**What these results do not show:** that Moho behaves like the mock. The mock
encodes the evidenced conventions; real behaviour is established only by the
native run.

## 5. NOT EXECUTED (requires the Mac)

| Item | Instructions |
|---|---|
| Running the generated script in Moho Pro 14 | install guide §4–6 |
| `verify-report` on Moho's real read-back | install guide §7, checklist A3 |
| Native pytest (`-m moho_native`, 2 tests) | install guide §7, checklist A4 |
| Visual alignment, z-order, animation, binding pokes, save and reopen | checklist B–F |
| Moho's **Scripts → Install Script…** path and the macOS reload shortcut | install guide §4–5 |
| Optional MohoMCP cross-check | `docs/mohomcp-assessment.md` |
| GitHub Actions run (Linux and macOS-14) | Workflow added; the run result is not recorded in this report |

## 6. Risks and assumptions, most likely first

1. **Image scaling:** the design assumes a project-sized image fills the frame
   exactly. The script enforces a 1920×1080 project. If Moho scales images
   differently, the art will not line up with the bones (checklist C1/C2).
2. **`fMovedMatrix` freshness:** if `UpdateBoneMatrix(id)` does not refresh the
   matrix immediately, `world_matrix` checks may fail while the rig is fine.
   Decide visually (C2).
3. **`PlaceLayerInGroup` stacking direction:** the read-back records the child
   order. If the order is reversed, z-order is wrong (C3). The fix is a
   one-line template change.
4. **Generic vs. cast layer objects:** the script always casts (`LayerAsImage`,
   `LayerAsBone`, `LayerAsGroup`), as community code does.
5. **Removable-volume permission:** macOS may block Moho from reading or writing
   on the SSD until allowed (install guide §6, troubleshooting).
6. **Interpolation:** default interpolation is used. Only key frames are
   asserted exactly.

## 7. Remaining manual verification steps

1. Follow `docs/phase0/install-macos-moho14.md` §1–6. That takes about 15
   minutes on a machine that already has Moho Pro 14.
2. Run `verify-report` and the native pytest tests (§7).
3. Complete `docs/phase0/manual-verification.md` (A–F).
4. Send back the outputs, the report JSON, screenshots and the checklist
   (install guide §9).

**Exit criteria for Phase 0 = PASS:** A1–A5, B1–B5, C1–C3, D1–D3, E1–E3 and F1
are all PASS (D4 and F2 are informational). The phase can then be closed and
Phase 1 (SAM 3) unblocked.
