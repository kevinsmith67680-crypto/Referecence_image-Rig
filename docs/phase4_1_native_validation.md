# Phase 4.1: native validation

**Status: NOT EXECUTED in Moho.** No Phase 4.1 native run exists: `docs/phase4/evidence/native_runs_4_1/` is empty.
Phase 4.0 itself is still incomplete (§1), so this phase built and cloud-tested the full-body rig without running it
natively. Nothing below claims native behaviour that Moho has not shown.

## 1. Phase 4.0 status (checked first)

Evidence: `docs/phase4/evidence/native_runs/` (two runs on 2026-11-23, Moho 14.5.1, macOS 27.2, Apple silicon).
Assessment: `docs/phase4_completion_report.md`. No newer native evidence arrived; the branch head was the assessment
commit when this phase began.

| gate | requirement | status | still required |
|---|---|---|---|
| 1 | native document created | **PASS** | — |
| 2 | bone moves bound artwork in genuine Moho renders | **NOT_EXECUTED** | Moho renders of frames 1 and 12 |
| 3 | document saves correctly | **PASS** (the file was saved in the other run's folder) | — |
| 4 | document reopens successfully | **NOT_EXECUTED** | the verify report written by Moho on the reopened file |
| 5 | bone animation works after reopening | **NOT_EXECUTED** | that verify report and the frame-18 render |

None of these gate definitions changed for Phase 4.1.

## 2. Phase 4.1 gates

`autorig native-fullbody-verify <package_out> --evidence <run> --character-package <package>` (`native/gate41.py`)
evaluates them. A gate passes only when every piece of its evidence came from Moho. Mock reports and simulated
renders give MOCK_ONLY.

| gate | requirement | native evidence it needs | status |
|---|---|---|---|
| 4.1-A | complete native skeleton | the build report: Phase 0 verifier on 25 bones (ids, parents, `fPos`, `fAngle`, `fLength`, world matrices, keys) | **NOT_EXECUTED** |
| 4.1-B | all layers at the correct scale | the saved file: every image layer `2W/H × 2` units; the neutral render matches the expected frame | **NOT_EXECUTED** |
| 4.1-C | bindings move the correct artwork | read-back bindings; for each pose, the region that changed in the render vs the expected region (IoU ≥ 0.60, ratio ≥ 0.5) | **NOT_EXECUTED** |
| 4.1-D | left and right arm controls | FK pose renders 10–50; target links accepted; IK renders 120/130; your drag test | **NOT_EXECUTED** (IK is experimental) |
| 4.1-E | left and right leg controls | FK renders 60/70; target links; IK renders 140/150; drag test | **NOT_EXECUTED** |
| 4.1-F | head, torso, pelvis controls | renders 80 (pelvis, keyed after reopening), 90, 100 | **NOT_EXECUTED** |
| 4.1-G | joint constraints behave | constraint writes accepted; render 160 shows HEAD clamped at its limit, or your drag test stops at the limit | **NOT_EXECUTED** |
| 4.1-H | saves and reopens | the saved file holds the full rig; the verify report on the reopened file matches | **NOT_EXECUTED** |
| 4.1-I | editable after reopening | pelvis position keys read back; render 80 shows the shift | **NOT_EXECUTED** |
| 4.1-J | renders show the movement | all twelve pose renders change where expected; frame 110 returns to frame 1 | **NOT_EXECUTED** |
| 4.1-K | neutral pose faithful | neutral render vs expected: mean difference ≤ 18 on the figure; frame 110 vs 1 ≤ 6 | **NOT_EXECUTED** |
| 4.1-L | character-specific data | the skeleton re-derived from the package equals the exported one, and Moho built exactly this spec | **NOT_EXECUTED** |

### Cloud check, for both characters (mock + simulation; not evidence)

`tools/phase4_1_cloud_check.py` → `docs/phase4_1/cloud_checks/<character>/`. The scripts run in the strict mock, and
the simulated frames stand in for renders:

| gate | schoolboy | tough student | reading |
|---|---|---|---|
| A, C, F, G, I, J, K, L | MOCK_ONLY | MOCK_ONLY | the logic would pass; the evidence is not native |
| B, H | NOT_EXECUTED | NOT_EXECUTED | no saved document exists in the cloud |
| D, E | NOT_EXECUTED | NOT_EXECUTED | the drag tests need a person in Moho |

The checker's acceptance path is tested with fixture inputs labelled native (`tests/test_p41_fullbody.py`). Its
failure paths are tested too: frozen artwork fails C and J, and a skeleton from another character fails L.

## 3. The native run (Mac procedure)

```bash
git pull origin claude/eloquent-cori-iv4ab1
```

```bash
tools/macos/phase4_native_test.sh --phase 4.0 --custom-content "<Moho Custom Content Folder>"
```

```bash
tools/macos/phase4_native_test.sh --phase 4.1 --with-ik --custom-content "<Moho Custom Content Folder>"
```

Run Phase 4.0 first. The 4.1 run asks for confirmation if no passing 4.0 gate file exists, and records the answer.
In 4.1 mode the harness:
1. builds the package with the Mac's paths, sizing the canvas automatically, and installs three menu scripts;
2. asks for a new document of the printed size, then the build script;
3. asks two viewport questions: frame 10, and frames 60/70;
4. Save As into the new run folder (it finds and offers to move a misplaced file), then Close; it reopens the file;
5. the verify script, which reads everything back and keys the pelvis shift at 80, then the frame-80 question;
   Save, Quit;
6. renders frames 1–110 as one PNG sequence (command line, else File > Export Animation into the named folder);
7. with `--with-ik`, on a copy:
   - the experimental IK/limits script;
   - three drag tests (arms, legs, a limit);
   - Save, then render frames 120–160;
8. records Moho's memory after the build and after reopening, and the render time;
9. runs `autorig native-fullbody-verify`. Push the `native_runs_4_1/<run>` folder afterwards.

What only a person can confirm, and is asked:
- the viewport questions;
- the drag tests: does each arm or leg follow its target without flipping, leaving the other alone, and does a joint
  stop at its limit?

The renders confirm the rest.

## 4. Save and reopen

The lifecycle under test:
1. create the rig;
2. verify its hierarchy (build read-back);
3. pose it (the build keys);
4. save, close and reopen;
5. verify the hierarchy again and check that every image layer still loads (verify read-back and your answer);
6. edit it (pelvis keys);
7. save, then render.

What is not scriptable with an evidenced API: new document, save, close, opening a file from Lua, and rendering.
Those steps are manual, or macOS commands (`open -a`, the command-line renderer).
