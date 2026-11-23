# Phase 4.1: implementation report

| | |
|---|---|
| **Verdict** | **BLOCKED — NATIVE VALIDATION REQUIRED** (preparatory work complete) |
| Why | Phase 4.0's native gates are still incomplete (2 of 5 pass), and no Phase 4.1 native run exists. As instructed, only safe, preparatory work was done |
| Built | the full-body package, skeleton spec, binding strategy, three Moho scripts, the 12-gate checker, the Phase 4.1 harness mode |
| Native | **NOT EXECUTED**: every Phase 4.1 gate is NOT_EXECUTED on native evidence |
| Phase 4.2 | not started |

## 1. Phase 4.0 verification status

Checked first (`docs/phase4/evidence/native_runs/`, `docs/phase4_completion_report.md`). No new evidence arrived.

| gate | status |
|---|---|
| 1 native document | **PASS** |
| 2 bone moves artwork in Moho renders | **NOT_EXECUTED** |
| 3 document saves | **PASS** |
| 4 document reopens | **NOT_EXECUTED** |
| 5 bone works after reopening | **NOT_EXECUTED** |

Gate definitions are unchanged. Because the foundation is not verified, Phase 4.1:
- uses natively only the operations Phase 4.0 already ran (the build template, unchanged);
- isolates IK targets and joint limits in an opt-in experimental script.

## 2. Architecture changes

Plan: [`phase4_1_architecture_plan.md`](phase4_1_architecture_plan.md). Result:
[`phase4_1_rig_architecture.md`](phase4_1_rig_architecture.md).

New modules:
- `native/skeleton.py`, `native/binding.py`, `native/fullbody.py`, `native/gate41.py`;
- templates `phase4_1_fullbody_verify.lua` and `phase4_1_ik_constraints.lua`.

CLI: `autorig native-fullbody`, `autorig native-fullbody-verify`. Harness: `--phase 4.1 [--with-ik]`.

Schema: bone names may be upper case (`$defs/bone_name`); everything else is unchanged.

Allowlist: `fConstraints`, `fMinConstraint`, `fMaxConstraint`, `fTargetBone`, with evidence and a mock implementation.

## 3. Native skeleton specification

`NativeSkeletonSpec` has 25 semantic bones: ROOT … RIGHT_FOOT plus four IK targets. Each bone records:
- its id/name and parent;
- rest base and tip, length and rest angle;
- its in-plane range and where the range came from;
- its IK role, control role and bound layers.

The bones are derived from the character's own rendering skeleton: the editor's bone edits plus the Phase 3.3 aligned
shoulder pivots. No coordinate is hardcoded. The two characters differ in every measured length; for example,
LEFT_THIGH is 272.0 px for the schoolboy and 248.8 px for the tough student.

## 4. Coordinate conversion

An integer translation maps character space to the canvas, and `px_to_world` maps the canvas to Moho. Parent-relative
values come from `compute_rest_pose`, the code Moho reproduced in Phase 4.0. Scale and rotation are each applied once:
tested at several canvas sizes, plus FK round trips and placement round trips. The canvas is sized automatically to
hold every test pose (schoolboy 1270 × 1486, tough student 1230 × 1484). Checking bone positions against Moho's own
read-back waits for the native run (gate A).

## 5. Bone hierarchy

The tree is in [`phase4_1_rig_architecture.md`](phase4_1_rig_architecture.md) §2.
- Rotating an upper arm moves only its forearm and hand (tested).
- The targets hang from ROOT, so pelvis moves do not drag them.

In the strict mock, the Phase 0 verifier passes on the full-body build read-back for both characters: 25 bones, world
matrices, keys at 13 frames.

## 6. Artwork import

All 25 layers of each character:
- original pixels, byte-identical to the package originals;
- unscaled and never cropped;
- in the editor's order, with the reference image copied into `reference/`.

Pending generated pixels are excluded unless `--include-pending-extensions` is given. See
[`phase4_1_artwork_binding_report.md`](phase4_1_artwork_binding_report.md).

## 7. Binding strategies

Every layer has one bone and one class: RIGID 12, LIMITED_DEFORMATION 3, FLEXIBLE 8, SWITCHABLE 2, plus CORRECTIVE as
metadata only. The native method is rigid layer binding throughout, the only one Moho has run. Weight meshes are not
converted (the fallback is documented).

## 8. Arm IK and 10. leg IK

- The target bones exist at the wrists and ankles.
- Linking (`fTargetBone`), the IK test keys (frames 120–150) and the user's drag tests live in the opt-in script. In
  the strict mock, all 30 operations of that script are accepted.
- Moho's actual solver behaviour (elbow and knee direction, flipping, unreachable targets) is **not measured**. The
  checker compares the IK renders with an analytic simulation using a looser threshold (IoU ≥ 0.45), because Moho may
  bend differently.

## 9. Shoulders and arms (FK)

Seven arm test poses are keyed at frames 10–50; the elbow bend direction comes from the joint ranges. The simulation
shows the rigid shoulder seam opening beyond about 30°: the sleeve cap separates from the blazer. That is flagged as
corrective work (an armpit / shoulder drawing, Phase 4.2), not hidden by stretching.

## 11. Head, torso and pelvis

PELVIS_CONTROL moves the upper body and both legs. Torso lean uses SPINE_LOWER, SPINE_UPPER and CHEST; head tilt uses
NECK and HEAD, at most ±28°. Turning the head needs side-view head art, which is missing (front-view art only).

## 12. Joint constraints

Ranges are converted from the package limits and configurable per character (`native_rig.json` / `--config`). The
opt-in script writes them as absolute parent-relative angles, an **unverified interpretation**. Frame 160 keys HEAD
30° past its limit, so the render shows whether Moho clamps keyed rotation. The drag test shows whether
manipulation stops at the limit.

## 13. Garment integration

The garment parts and classes are kept per layer. Blazer details, collar and shoes stay rigid with their layers.
Sleeves and trousers open seams at the joints; those need correctives.

## 14. Hand-switch readiness

The HAND bones and wrist pivots are recorded, the original hands are bound, and 18 variants are listed (all pending,
none approved). Switch Layers are Phase 4.3.

## 15. Character generalisation

Both characters were built from their own data; Character B is `tough_student_test`, the swept blond-and-red hair:
- skeletons, proportions, pivots, canvases and joint ranges all differ;
- the binding classes agree.

Gate L re-derives the skeleton from the package and compares it with the export. The test with the other character's
package fails, as it should.

## 16. Native test evidence

None for Phase 4.1. Cloud checks (mock + simulation): `docs/phase4_1/cloud_checks/`. Every gate there is MOCK_ONLY or
NOT_EXECUTED.

## 17. Save and reopen

The verify script reads everything back from the reopened file and keys the pelvis shift (frame 80). The saved file is
checked structurally using the format observed in Phase 4.0. See
[`phase4_1_native_validation.md`](phase4_1_native_validation.md) §4.

## 18. Moho render comparisons

`gate41.comparison_sheets` writes "Moho render | AutoRig rigid simulation" pairs per pose and per joint close-up
(shoulder, elbow, wrist, hip, knee, ankle). It only works from renders that exist, so none has been made. Geometric
differences (joint positions, changed regions) are measured separately from rasterisation (antialiasing, filtering).
No visual parity is claimed.

## 19. Performance

Cloud tooling figures only:
- skeleton: 2 s;
- package: 5 s without simulated frames, 30 s with them;
- mock scripts: under 10 ms.

The M1 measurements (build, reopen, memory, render, drag latency) are wired into the harness and NOT EXECUTED. See
[`phase4_1_performance_report.md`](phase4_1_performance_report.md).

## 20. Automated regression

- `tests/test_p41_fullbody.py`: 34 tests covering skeleton, hierarchy, coordinates, proportions, ranges, configuration,
  package fidelity, binding, hand attachment, canvas, reproducibility, invalid packages, mock scripts, gates (mock,
  empty, fixture acceptance, frozen artwork, wrong character) and harness arguments. All pass.
- Phase 0 / 4.0 tests affected by the schema, allowlist and mock changes: 128 pass.
- Full suite: 676 passed, 2 skipped (§23).

## 21. Remaining limitations

- No native evidence for Phase 4.1; Phase 4.0 gates 2, 4 and 5 are still open.
- IK and constraint behaviour in Moho is unknown, including the semantics of the constraint angles.
- Rigid binding opens seams at the shoulders, elbows, hips and knees, and pending generated pixels are excluded, so
  gaps show.
- Front-view art: real knee flexion, sitting and head turns need other views. The knee pose is an in-plane mechanical
  test.
- Feet and hands rotate with their parents (independent angle is not evidenced).
- Stray segmentation specks in the original layers become visible when a layer rotates (for example, near the lifted
  leg).
- Nothing is approved.

## 22. Phase 4.2 readiness

Not ready to start. Phase 4.2 (correctives and Smart Bones) needs:
1. Phase 4.0 passed;
2. the Phase 4.1 native run with gates A–L;
3. the experimental IK/limits results;
4. reviewed corrective artwork.

The rig already carries what 4.2 attaches to: bones, the classes of the FLEXIBLE layers, the corrective metadata and
the garment parts.

## 23. Regression results

Full cloud suite (`.venv/bin/python -m pytest`), run alone: **676 passed, 2 skipped** in 23 min.
- 676 = the 639 tests of Phase 4.0, plus 3 new tests for the Phase 4.0 evidence and saved-file checks, plus 34 Phase
  4.1 tests.
- The 2 skips are the native Phase 0 hooks (they need a Phase 0 Moho report).
- No existing assertion was changed.
- Two earlier runs were killed by memory pressure (exit 137) while other heavy work ran at the same time. They are not
  counted.

## 24. Verdict

**BLOCKED — NATIVE VALIDATION REQUIRED.** All preparatory Phase 4.1 work is built and cloud-tested. Not one Phase 4.1
gate is passed, because no native evidence exists. Next:
1. `tools/macos/phase4_native_test.sh --phase 4.0` (complete gates 2, 4 and 5);
2. `--phase 4.1 --with-ik`;
3. push both run folders.

Phase 4.2 is not started.
