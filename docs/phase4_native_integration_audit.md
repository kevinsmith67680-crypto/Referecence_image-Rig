# Phase 4: native Moho integration audit

Date: 2026-11-22. Scope: Phase 4 steps 1–2 (audit the existing integration; verify the installed Moho 14.5 environment
and API). Status of native verification: **NOT EXECUTED**. Moho did not run for this audit.

## 1. Can Moho be reached from this session?

No.

| check | result |
|---|---|
| execution environment | a Linux cloud container (Claude Code on the web). Moho is a macOS application and is not installed here |
| the user's Mac | no folder of the Mac is shared with this session (`list_computer_folders` returned no folders and none in `cannot_be_used`), so no command can run on the Mac |
| documentation sites | `mohoscripting.com` and `lostmarble.com` are blocked from the sandbox (unchanged since Phase 0) |
| Moho 14.5 discovery data | `research/moho-14.5/` is still empty: no probe report, no saved sample files, no screenshots (Live Models steps D0–D6 not run) |

So step 2 ("verify the installed Moho 14.5 environment and API") could not be done. The version, the API surface and the
file format of the user's installation are **unknown**. The harness records them on the first native run:
`environment.json` holds the bundle version from `Info.plist`; the verify report holds `AppVersion()`, `_VERSION` and
the file-related ScriptInterface names.

The request says "use the actual PNGs if they have since been supplied". They have not: `/mnt/user-data/uploads` is
empty. The tough student still uses the chat's JPEG re-encodes (`references/tough_student/`, Phase 3.8 §3).

## 2. What exists, and what it is evidence of

| component | where | evidence level |
|---|---|---|
| rig spec + JSON schema | `src/autorig/spec.py`, `data/rig_spec.schema.json` | cloud-tested |
| pixel → Moho geometry (frame height = 2 units, parent-local `fPos`, CCW radians) | `src/autorig/geometry.py` | unit-tested; the conventions themselves are **assumptions from documentation**, never checked in Moho |
| Phase 0 builder menu script | `data/templates/phase0_build.lua`, `lua_codegen.py` | runs against the strict mock (`tests/lua/mock_moho.lua`); **not run in Moho** |
| API allowlist | `data/moho_api_allowlist.json` (64 members, each with documentary evidence) | documentary evidence; the mock implements exactly these members (`tests/test_lua_moho_mock.py`) |
| read-back report verifier | `src/autorig/report.py` (`autorig verify-report`) | cloud-tested on mock reports |
| native pytest hook | `tests/native/` (`-m moho_native`, needs `AUTORIG_MOHO_REPORT`) | skipped; no native report exists |
| API probe (Live Models LM-0) | `data/templates/probe_moho.lua` | mock-tested; **never run in Moho** |
| `.moho` / `.mohoproj` reader and diff | `src/autorig/mohofile.py` | tested on synthetic JSON; the real Moho 14.5 file layout is **not confirmed** (no saved sample) |
| macOS installer (Phase 0) | `tools/macos/install_phase0.sh` | shell-checked in the cloud; never run on the Mac |
| Phase 3 asset package export | `projects/*/moho_asset_package/`, `src/autorig/editor/export.py` | validated against its own schema; `production_ready: false`; every Moho mapping `proposed_unverified` |
| Moho-oriented metadata from Phases 3.2–3.8 (rig controls, hand switches, garment matrix, performance and reference metadata, live control manifests) | `analysis/*.json`, `motion/rigctl.py`, `garmentrig/moho.py`, `performance/export.py`, `refmotion/export.py` | all `proposed_unverified`; no exporter writes any of it into Moho |

Nothing in the repository has ever been checked against a running Moho. The Phase 0 verdict is still PARTIAL
(`docs/phase0/completion-report.md`), and its native checklist (`docs/phase0/manual-verification.md`) is still blank.

## 3. The calls a minimal native proof needs

| need | allowlisted Moho API? | Phase 4.0 approach |
|---|---|---|
| create a bone layer, bones, parents, rest pose | yes (`CreateNewLayer`, `LayerAsBone`, `Skeleton`, `AddBone`, `fParent`, `fPos`, `fAngle`, `fLength`, `UpdateBoneMatrix` …) | Phase 0 builder, reused unchanged |
| create image layers with real artwork | yes (`LayerAsImage`, `SetSourceImage`, `PlaceLayerInGroup`) | same |
| bind artwork to a bone | yes (`SetLayerParentBone`, `LayerParentBone`) | same |
| manipulate a bone | yes (`fAnimAngle:SetValue`, `GetValue`, `CountKeys`, `GetKeyWhen`) | build keys at frames 1/12/24; the verify script adds frame 18 |
| find the rig in a reopened document | yes (`LayerByName`, `LayerAsGroup`, `CountLayers`, `Layer`, `Bone`, `Name`) | new verify script |
| new document | **no** (`FileNew` in `deliberately_not_used`) | manual: File > New |
| save / save as | **no** (`FileSave`, `FileSaveAs` in `deliberately_not_used`) | manual: File > Save As, then Save |
| close / reopen | **no** (no member) | manual close; the harness reopens the file with `open -a <Moho> <file>` (a macOS command, not a Moho API) |
| render a frame | **no** (no member) | the harness *tries* Moho's command-line renderer, then falls back to File > Export Animation |

### Evidence found during this audit (secondary only, not allowlisted)

| item | source (web search snippets; the sites cannot be fetched from here) | decision |
|---|---|---|
| `ScriptInterface:FileSave()`, `FileSaveAs(path)`, listed as available before 9.5 | [mohoscripting.com ScriptInterface](https://mohoscripting.com/classes/scriptinterface) | not adopted. Phase 4.0 uses the UI. The verify script lists the file-related names Moho actually exposes, without calling them, so a later phase can decide from evidence |
| a forum script calling `moho:FileNew()`, `FileSaveAs(...)`, `FileOpen`, `FileSave` | [lostmarble.com forum](https://lostmarble.com/forum/viewtopic.php?p=215361) | same |
| `FileClose()` misbehaving while the Lua console window is open | [lostmarble.com forum](https://www.lostmarble.com/forum/viewtopic.php?p=215418) | a reason to keep close manual |
| command-line rendering `-r file -f PNG -o out -start N -end N` (Anime Studio 5.1+, `-outfolder` from 9.5) | [lostmarble.com forum](https://lostmarble.com/forum/viewtopic.php?p=6190) | the harness tries it once per frame, logs the exact command and exit code, and falls back to manual export. Whether it works in Moho 14.5 is **unknown** |

None of these was added to the allowlist. Rule 2 of `CLAUDE.md` requires evidence plus a mock implementation before a
member may be called, and Phase 4.0 does not need them.

## 4. Assumptions only a native run can confirm

> **Update after the first native runs (2026-11-23, Moho 14.5.1 on macOS 27.2, Apple silicon;
> `phase4_completion_report.md`):**
> - **Confirmed:** 1 (image placement), from Moho's saved file: each image layer is 3.555556 × 2.0 units;
>   2 (bone conventions), from Moho's `fMovedMatrix` read-back; 5 (script I/O): the build report was written.
> - **Supported but not yet measured on a render:** 3 (binding): the user's observation and Moho's thumbnail.
> - **Persistence is half done:** the save is confirmed; reopening is not tested.
> - **Not tested:** 6 (the reopened document's frame).

1. **Image placement.** An image layer whose pixel size equals the project size fills the frame exactly (Phase 0
   architecture §3). If Moho scales or offsets it differently, every bone is misplaced relative to the art. Measured by
   the rest-frame render (silhouette IoU against the expected frame).
2. **Bone conventions.** `fPos` is parent-local (origin at the parent's base, +x along the parent), angles are CCW
   radians relative to the parent, and bone ID = creation order. Checked by the Phase 0 verifier on the read-back
   (`world_matrix`, `fPos`, `fAngle`).
3. **Layer binding.** `SetLayerParentBone` makes the layer follow the bone's deviation from its rest pose (rigid
   binding). Checked by the frame-12 render.
4. **Persistence.** Save and reopen keep the bones, the binding, the keys and the image paths. Checked by the verify
   script's read-back after reopening, and by the frame-18 render, which only exists if the new key survived the save.
5. **Script I/O.** Menu scripts can write files with `io.open` (the reports). If Moho's Lua blocks it, the alerts and
   the Lua console still say so.
6. **The reopened document's frame.** `fMovedMatrix` and `fAngle` are read after `SetCurFrame(0)`. Whether bone fields
   follow `SetCurFrame` at other frames is recorded for information only (`move.fields_follow_frame`).

## 5. Consequence for Phase 4

HARD GATE 4.0 cannot be met from this environment. As the request instructs, only the portable package and the
local execution harness were built and tested. The work stops at **BLOCKED — NATIVE VALIDATION REQUIRED**:
[`phase4_completion_report.md`](phase4_completion_report.md).
