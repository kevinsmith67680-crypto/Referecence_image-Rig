# Phase 4: completion report

| | |
|---|---|
| **Phase 4.0 verdict** | **PARTIAL**. Moho ran, and nothing that ran failed. 2 of the 5 hard-gate items pass on native evidence; 3 were not executed |
| Native runs | two on 2026-11-23, Moho 14.5.1 (build 14.5.1) on macOS 27.2, Apple silicon (`docs/phase4/evidence/native_runs/`) |
| HARD GATE 4.0 | **not passed** (`autorig native-verify`: BLOCKED — NATIVE VALIDATION REQUIRED) |
| Steps 5–23 (Phase 4.1+) | **not started**; they wait for all five gate items to pass |

## 1. The evidence, and what is native

| source | what it is | native? |
|---|---|---|
| `…/20261123T105654Z_schoolboy_tpose_l/phase4_native_build_report.json` | read back by the build script inside Moho: `moho_app_version` 14.5.1, `lua_version` Lua 5.4, status completed | **yes**: written by Moho's Lua |
| `…/20261123T105258Z_schoolboy_tpose_l/AutoRig_P4_Native.moho` | a zip holding `Project.mohoproj` (mime `application/x-vnd.lm_mohodoc`, version 1045, created "Mon Nov 23 11:29:27 2026") and Moho's own `preview.jpg` thumbnail | **yes**: written by Moho (File > Save As) |
| `environment.json` (both runs) | the bundle version read from Moho's `Info.plist` by the harness | yes (the harness on the Mac) |
| `observations.json` (both runs) | your answers to the harness prompts | yes (a person, not an automatic check) |
| `harness.log`, `gate_4_0.json` (both runs) | the harness's log, and the gate checker run on the Mac | yes (the harness on the Mac) |
| `rig_spec.json`, `native_manifest.json` | the proof package built on the Mac. The cloud rebuild gives the same spec sha256 (`d053c46d…`) and pixel-identical art | generated input, not evidence of Moho behaviour |
| `docs/phase4/cloud_checks/` | strict-mock reports and simulated frames | **no**: MOCK/SIMULATION, not used for any decision below |
| `docs/phase4/native_assessment/20261123/` | gate results computed in the cloud **from** the native files above | derived (cloud), no new evidence |

No Moho render exists (`renders/` is absent in both runs). There is no verify report, no screenshot and no Lua console
text.

## 2. The two runs

**Run 1, `20261123T105258Z` (10:52:58–10:55:39 UTC): incomplete, nothing failed.**
- Enter was pressed at the build step about 40 s after the harness started (10:52:58 + 41 s). The harness then waited
  its fixed 120 s, found no build report, and stopped.
- Moho reported no error. The script simply had not run within that window.
- The gate file this run wrote at 10:55 found no saved document in the folder.

**Run 2, `20261123T105654Z` (10:56:55–11:29:31 UTC): stopped after the build, at the Save As step.**
- The build script completed in Moho, and you answered "yes" to *the arm is lowered at frame 12*.
- At Save As, the document went into **run 1's folder** instead of run 2's.
- The harness looked once in run 2's folder, found nothing, and stopped. Close, reopen, the verify script, the final
  save and the renders never ran.

Why the `.moho` in run 1's folder belongs to run 2:
- Moho stamped it "created … 11:29:27", and the zip entries are 11:29:28. That is 3 s before run 2's harness gave up
  (11:29:31), and 34 min after run 1 had ended.
- Moho stored the image paths relative to the file as `../../../../../build/phase4_native/…`. That resolves from a
  `docs/phase4/evidence/native_runs/<run>/` folder, so the file was written there by Moho, not moved there later.
- Its rig matches run 2's build report value for value (§3, item 3).

Neither run is complete. Run 2 got furthest, and **no step that ran failed**.

## 3. HARD GATE 4.0, item by item

| # | requirement | status | evidence |
|---|---|---|---|
| 1 | A real native Moho document has been created | **PASS** | native build report; the Phase 0 verifier gives 40 PASS, 0 FAIL, 4 INFO (`autorig verify-report`) |
| 2 | At least one bone genuinely moves artwork | **NOT PASSED** (renders not executed) | supporting native evidence only, see below |
| 3 | The document saves successfully | **PASS** (file found in run 1's folder) | the saved project parses and holds exactly this rig: 15 structural checks PASS, 0 FAIL |
| 4 | The document reopens successfully | **NOT EXECUTED** | no verify report: the document was never reopened by the harness |
| 5 | The bone remains functional after reopening | **NOT EXECUTED** | no verify report and no frame-18 render |

**Item 1 in detail.** Moho itself read back, at frame 0:
- the bone layer `AutoRig_P4_Native` (`LayerType()` = `MOHO.LT_BONE` = 4);
- the bones `body` (id 0, root), `shoulder` (id 1, parent 0) and `arm` (id 2, parent 1), with `fPos`, `fAngle` and
  `fLength` equal to the spec within 1e-4;
- their `fMovedMatrix` world base and tip matching the art's joints within 2e-3 units (about 1 px);
- angle keys at frames 0/1/12/24 on `shoulder` and `arm`;
- three image layers, `body_back_art` → bone 0, `arm_l_art` → bone 2, `body_front_art` → bone 0, each with its PNG path;
- a 1920×1080 document, frames 1–24, 24 fps.

**Item 2: why it is not marked as passing.** The gate requires Moho renders of frames 1 and 12, compared with the
expected frames. None was made. Two pieces of native evidence support the movement, but neither meets that criterion:
- **Your observation.** At frame 12, the arm artwork was lowered and followed the bone (`arm_moved_in_viewport_f12: yes`).
- **Moho's own thumbnail** (`preview.jpg` inside the saved file). It shows the left arm's artwork rotated down about the
  shoulder, lying along the arm bone drawn in the viewport.
  - It is a capture of the editing viewport, with the grid, the bone overlay and Moho's own pan and zoom, not a camera
    render.
  - A pixel comparison with the expected frames was tried and is inconclusive without registering the viewport, so it
    was not used.

**Item 3 in detail.** The checks read keys observed in this file; research/moho-14.5/README.md lists them. The project
holds:
- a 1920×1080 document, frames 1–24;
- a `BoneLayer` named `AutoRig_P4_Native`;
- three bones with the same names, parents, lengths and rest positions (within 1e-5);
- the build keys on every bone;
- three `ImageLayer`s with `parent_bone` 0/2/0 and the same PNG paths;
- no frame-18 key. That is expected: it was saved before the verify script, which never ran.

The run-2 folder alone shows this item as NOT_EXECUTED, because the harness looked there. The file alone shows it as
UNLINKED: there is no native report beside it. The combined evaluation is
`docs/phase4/native_assessment/20261123/gate_run2_with_saved_file.json`.

## 4. What the native evidence confirms

| question | answer from native evidence |
|---|---|
| artwork placement | **confirmed in Moho's file**: each full-canvas PNG became an image layer of 3.555556 × 2.0 units, exactly the 1920×1080 frame. This was the Phase 0 placement assumption |
| bones and the parent-local `fPos` convention | **confirmed**: Moho's own `fMovedMatrix` puts the base and tip of every bone where the spec puts them. The `shoulder` bone's base is *not* on its parent's tip, so the convention is really exercised |
| `UpdateBoneMatrix` refreshes `fMovedMatrix` | **confirmed** (open question 2 in `docs/moho-lua-api-evidence.md`) |
| bindings | **confirmed** in the read-back and in the saved file (bones 0/2/0) |
| animation keys | **confirmed**: `fAnimAngle` keys at frames 0/1/12/24 with the computed values, in the read-back and in the file. Moho stored every key with interpolation code `im: 1`, which is not yet mapped to a name |
| layer stacking | child index 0 is the first layer created (`body_back_art`), and the file keeps the same order. The thumbnail draws the arm over the blazer, which fits `top = true` stacking. It is not visually confirmed for `body_front_art` |
| artwork moves with the bone | **supported, not gate-verified** (item 2) |
| save | **works** (item 3) |
| reopen, and moving after reopening | **not tested** (items 4 and 5) |
| allowlisted API in Moho 14.5.1 | every member the build script calls ran without error. The verify script's calls and the command-line renderer are untested |

## 5. Diagnosis and fixes

Neither run hit a Moho or script failure. Both stopped because the harness checked a step **once** and gave up.

| cause | fix (in this commit) |
|---|---|
| the build report was checked only for 120 s after Enter | `wait_until_present`: checks for 60 s, then asks *"press Enter to check again, or q to stop"*, as often as needed (build report and verify report) |
| the saved file was checked once, and only in the run's own folder | the Save As step repeats until the file is there. It also looks for an `AutoRig_P4_Native.moho` written during this run in another `native_runs/` folder (or under `build/phase4_native/`) and offers to move it into the run's folder, recording `saved_relocated_from` |
| easy to pick an older, similarly named run folder in the save dialog | the prompt names the run folder, warns about the older ones next to it, and spells out Cmd+Shift+G → paste → name the file |
| observations with spaces would have been split | observations go to a temporary key=value file |
| the final Save after the verify script was checked once | it repeats until the file is newer, or until you type `s` to skip |
| the gate checker crashed when given the evidence folder instead of the proof package | it now reports what is missing |
| the save item only matched names | it now checks the saved project's structure (§3) |

## 6. What to do on the Mac

Use a **fresh full run**: build, save, close, reopen, verify, save and render in one session gives one clean evidence
folder. The two partial runs stay in the repository as history, unchanged.

1. Get the fixes:

   ```bash
   git pull origin claude/eloquent-cori-iv4ab1
   ```

2. Run the harness with the same Custom Content Folder as before:

   ```bash
   tools/macos/phase4_native_test.sh --custom-content "<your Moho Custom Content Folder>"
   ```

3. When it says *"Moho is starting"*:
   - wait until Moho is fully open;
   - File > New; Project Settings 1920 × 1080;
   - run **Scripts → AutoRig → AutoRig Phase 4.0 native proof**;
   - only then press Enter. If you press it too early, the harness now asks again instead of stopping.
4. At **Save As**:
   - press Cmd+Shift+G in the dialog, paste (the harness put the new run's folder on the clipboard) and press Enter;
   - check that the folder name ends in the **new** run's time;
   - name the file `AutoRig_P4_Native.moho`.

   If it lands elsewhere, the harness finds it and offers to move it.
5. **File > Close**; the harness reopens the file. Run **Scripts → AutoRig → AutoRig Phase 4.0: Verify Reopened Rig**,
   check that the arm is raised at frame 18, then **Cmd+S** and **Cmd+Q**.
6. Let it render frames 1, 12 and 18. If Moho's command line does not produce a PNG, export each frame with
   File > Export Animation (PNG, start = end = the frame) into the folder the harness names.
7. Push the new run folder:

   ```bash
   git add docs/phase4/evidence/native_runs && git commit -m "Add Phase 4.0 native run evidence" && git push origin claude/eloquent-cori-iv4ab1
   ```

The gate passes only if `gate_4_0.json` in the new folder says `GATE 4.0 PASSED`. Phase 4.1 starts only then.

## 7. Built in the cloud (Phase 4.0)

| piece | file |
|---|---|
| integration audit | [`phase4_native_integration_audit.md`](phase4_native_integration_audit.md) |
| proof package generator (`autorig native-poc`) | `src/autorig/native/poc.py` |
| build menu script (the Phase 0 template, renamed) | `lua_codegen.render_named_build_lua` |
| verify-after-reopen menu script | `src/autorig/data/templates/phase4_native_verify.lua` |
| HARD GATE 4.0 checker (`autorig native-verify`) | `src/autorig/native/gate.py` |
| Mac harness | `tools/macos/phase4_native_test.sh` |
| cloud check (mock + simulation) | `tools/phase4_cloud_check.py` → `docs/phase4/cloud_checks/` |

Inputs: your PNGs for the tough student have still not been supplied; the proof runs used the schoolboy.

## 8. Regression

`tests/test_p4_native.py` has 26 tests (3 new). They cover:
- the structural check of saved projects, which catches a wrong binding and lost keys;
- the checker's handling of missing expected frames;
- the committed native evidence: the build report and the saved file hold exactly this spec's rig, the gate is still
  BLOCKED, and the file alone is UNLINKED.

Full cloud suite, run with the Phase 4.1 work (2026-11-23): **676 passed, 2 skipped**. The skips are the native
Phase 0 hooks.

## 9. What this report does not claim

- No render, no reopen and no post-reopen movement has been seen in Moho. Items 2, 4 and 5 stay open.
- The thumbnail and your observation support item 2, but they are not the gate's render check.
- Nothing is production-ready. No pending artwork was approved.
