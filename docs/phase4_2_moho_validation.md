# Phase 4.2: Moho validation (Stage 8)

**Status: NOT EXECUTED in Moho.** No Phase 4.2 native run exists (`docs/phase4/evidence/native_runs_4_2/` does not
exist yet). Phase 4.0 is still PARTIAL: gates 2, 4 and 5 are not executed. No Phase 4.1 native run exists either.
This phase built the hybrid rig and cloud-tested it with the strict mock and the simulation. The Moho run below has
not happened, and nothing in this document claims native behaviour.

## 1. Phase 4.0 / 4.1 status (checked first)

The branch has no new native evidence (fetched on 2026-11-23). Phase 4.0 is unchanged:

| gate | status |
|---|---|
| 1 native document | PASS |
| 2 bone moves artwork in Moho renders | NOT_EXECUTED |
| 3 save | PASS |
| 4 reopen | NOT_EXECUTED |
| 5 move after reopening | NOT_EXECUTED |

Phase 4.1: 0 of 12 gates executed. The harness records whether Phase 4.0 had passed when a 4.2 run starts.

## 2. What the Mac run does

```bash
tools/macos/phase4_native_test.sh --phase 4.2 --custom-content "<Moho Custom Content Folder>"
```

Add `--review` to include the pending clipped underlays (elbow and knee caps, shoe tops; labelled pending). The
harness:

1. checks macOS and Moho 14.5, and builds the package with this checkout's absolute paths (`autorig native-hybrid`);
2. installs `AutoRig_P42_HybridBuild.lua` and `AutoRig_P42_HybridVerify.lua`;
3. guides one run:
   1. File > New, with the dimensions it prints;
   2. Scripts > AutoRig > *AutoRig Phase 4.2 hybrid rig*;
   3. Save As into the run folder, then Close;
   4. it reopens the saved file;
   5. Scripts > AutoRig > *AutoRig Phase 4.2: Verify Reopened Hybrid Rig*. This reads everything back, lists the
      actions and keys the torso lean at frame 80;
   6. Save, then Quit;
4. renders frames 1–90 (Moho's command-line renderer, or File > Export Animation if that fails);
5. optionally runs **MT-1**, the Smart Bone switch for one gap fill, on a copy. It then runs the verify script on the
   reopened copy and renders frames 10–30;
6. asks for your **visual review** (accepted / rejected / later) and a note;
7. runs `autorig native-hybrid-verify` and writes `gate_4_2.json` and `comparisons/`. These are side-by-side
   images: Phase 4.1 Moho render | Phase 4.2 Moho render | Phase 4.2 simulation, plus joint close-ups. The 4.1 column
   appears only once a 4.1 run exists.

Evidence goes to `docs/phase4/evidence/native_runs_4_2/<UTC time>_<character>/`. Commit and push the folder.

Run it on both characters to cover Stage 9:

```bash
tools/macos/phase4_native_test.sh --phase 4.2 --package projects/tough_student_test/character_package --custom-content "<Moho Custom Content Folder>"
```

## 3. Gates (`native/gate42.py`)

A gate passes only when its evidence came from Moho: Moho-written reports, the saved `.moho`, Moho renders. Mock
reports and simulated renders give MOCK_ONLY.

| gate | requirement (Stage 8 criterion) | native evidence | status |
|---|---|---|---|
| 4.2-A | rig built natively: bones, layers, bindings, keys | build report | NOT_EXECUTED |
| 4.2-B | renders of the ten test frames | `renders/frame_0001..0090.png` | NOT_EXECUTED |
| 4.2-C | neutral pose reproduces the character | frame 1 vs the source layers, mean diff ≤ 18 | NOT_EXECUTED |
| 4.2-D | stable attachment and proportions: each pose matches the planned binding | per-pose mean diff ≤ 24 | NOT_EXECUTED |
| 4.2-E | continuous outlines, overlaps, distortion: joint holes and cracks on the renders | MEASURED only, for the person | NOT_EXECUTED |
| 4.2-F | save/reopen persistence | verify report from the reopened file + the saved document | NOT_EXECUTED |
| 4.2-G | the reopened rig still works: lean keyed after reopening | verify report + frame 80 | NOT_EXECUTED |
| 4.2-H | return to neutral | frame 90 vs 1, mean diff ≤ 6 | NOT_EXECUTED |
| 4.2-I | Smart Bone activation | Smart Bone actions in the reopened copy + your MT-1 answer | NOT_EXECUTED |
| 4.2-J | garment silhouettes and natural articulation | your visual review (MT-4) | NOT_EXECUTED |
| 4.2-K | comparison with Phase 4.1 | a Phase 4.1 native run | NOT_EXECUTED |

The verdict is PASS only if A–J pass on native evidence. E and K are information: a number never approves a joint.
**Gate J (your verdict) is required for PASS**, because the request puts convincing articulation above numerical
scores.

### Cloud check (mock + simulation)

`tools/phase4_2_cloud_check.py` → `docs/phase4_2/cloud_checks/<character>/`:

| | schoolboy | tough student |
|---|---|---|
| A, B, C, D, G, H | MOCK_ONLY | MOCK_ONLY |
| E | MEASURED (simulation) | MEASURED (simulation) |
| F | NOT_EXECUTED (no saved file) | NOT_EXECUTED |
| I, J, K | NOT_EXECUTED | NOT_EXECUTED |
| verdict | BLOCKED — NATIVE VALIDATION REQUIRED | BLOCKED — NATIVE VALIDATION REQUIRED |

In the strict mock, both scripts run without an error on both characters:
- the build script reads back 25 bones, the layers, their bindings and the keys of the nine build frames;
- the verify script reads the reopened rig, lists its actions (none in the mock) and keys and reads back the lean.

## 4. Manual Moho tests (`manual_tests.json` in the package)

There is no Lua evidence for Smart Bone actions, Switch layers or flexible image binding, so they are never
generated. Each becomes a reproducible manual test:

| test | what it establishes | records |
|---|---|---|
| MT-1 | one Smart Bone action (named after its bone) selecting the keys of one native gap fill in a Switch layer: appears when the bone turns, no popping, gone at rest; survives save/reopen | `smart_bone_mt1_works`, the copy, its verify report, renders 10–30 |
| MT-2 | the same for every manual-switch asset in `smartbone_plan.json` | notes |
| MT-3 | flexible binding of the torso image layer to SPINE_LOWER, SPINE_UPPER and CHEST, for the lean; Phase 4.0's saved files carry a per-layer `flexi_bone_subset` key, but Lua cannot set it on evidence | a saved copy for `autorig moho-file diff` |
| MT-4 | your visual review of the native renders | `visual_review_verdict`, notes |

The step lists are in the package. They name the Moho UI only as far as it is known. Write down anything that
differs on your Mac; that becomes evidence for the next phase.

## 5. Missing capabilities (recorded, not guessed)

| capability | status | effect |
|---|---|---|
| creating Smart Bone actions from Lua | not evidenced | MT-1/MT-2 by hand; the verify script only *reads* actions (CountActions, ActionName, IsSmartBoneAction: allowlisted from documentation, read inside pcall, never run natively yet) |
| creating Switch layers / switch keys from Lua | not evidenced | gap fills are exported as key PNGs for the manual test |
| layer visibility or opacity keys from Lua | not evidenced | correctives that must be hidden at rest cannot be automated; only underlays that are invisible at rest are built by the script |
| flexible (multi-bone) image binding from Lua | not evidenced | MT-3; the torso lean tears the blazer without it |
| image mesh warping | not evidenced | not used; no stretching of artwork |

## 6. Performance

Cloud figures only (this container; the M1 figures need the Mac run):

| step | time |
|---|---|
| hybrid package (default build, with simulated frames) | about 30 s per character |
| native gap fills (`autorig native-fills`) | about 2 min per character |
| joint audit, six variants + counterfactual | about 4 min per character |
| build + verify scripts in the strict mock | under 1 s |

The harness records the package build time, Moho memory after build and reopen, the reopen time and the render
times.
