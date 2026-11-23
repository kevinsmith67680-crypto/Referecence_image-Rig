# Phase 4.2: visual comparisons

**Every image in this document is a SIMULATION of rigid native binding, not a Moho render.** The native renders the
request asks for (full body, joint close-ups, Phase 4.1 vs 4.2) are **NOT EXECUTED**: no Phase 4.1 or 4.2 native run
exists. When the Mac run happens, `autorig native-hybrid-verify` writes the native comparisons into the run
folder's `comparisons/`:
- Phase 4.1 Moho render | Phase 4.2 Moho render | Phase 4.2 simulation, per pose;
- Moho | simulation close-ups per joint.

Those, not the images below, are the before/after evidence for the verdict.

No image here was edited. They are crops, resizes and labels of renders produced by `deform-audit`,
`native-hybrid` and `tools/phase4_2_cloud_check.py`.

## 1. Joint close-ups: rigid 4.1 → hybrid → hybrid with pending correctives

`docs/phase4_2/audit/<character>/SIMULATION_joint_closeups.jpg` has one row per joint and pose and one column per
variant: rigid_4_1, order, split, hybrid, hybrid_review, hybrid_correctives.

- [schoolboy](phase4_2/audit/schoolboy_tpose/SIMULATION_joint_closeups.jpg)
- [tough student](phase4_2/audit/tough_student_test/SIMULATION_joint_closeups.jpg)

What a person sees there (schoolboy; the tough student behaves alike, §4):

| joint | rigid 4.1 | hybrid (automated natively) | + pending correctives (review only) |
|---|---|---|---|
| shoulder, arms lowered | the sleeve cap swings out over the shoulder line: the "oversized shoulder" | the cap stays on the clavicle; a square step remains at the top of the shoulder | the native fill rounds the shoulder line; no step |
| shoulder, arm raised | a deep triangular gap from the armpit to the shoulder top | the cap stays; a wedge opens under the arm, its apex at the pivot | a concave armpit closes the apex; the space under the arm stays open, as it should |
| elbow 90° | a notch at the outer elbow | same | the Phase 3.4 elbow cap rounds the outer elbow |
| hip (knee pose) | a white wedge between the blazer hem and the trouser | same | the native fill continues the trouser under the hem |
| knee (in-plane test) | a step and a white notch; a thigh sliver hangs down the shin | the sliver goes with the shin; the notch stays | the Phase 3.4 knee cap closes the notch |
| waist (lean) | white wedges between the blazer front panels | same | same: **no artwork exists** (shirt/sweater under the blazer), and none was invented |

Whether these read as natural is not decided here. All corrective pixels are pending, and the verdict is your
visual review ([`phase4_2_moho_validation.md`](phase4_2_moho_validation.md), gate 4.2-J).

## 2. The counterfactual: why the skeleton was not rebuilt

`docs/phase4_2/audit/<character>/SIMULATION_pivot_experiment.jpg` has three columns for each shoulder pose:
- rest;
- the pivot moved onto the Phase 1 cut;
- the cut moved onto the pivot (the split).

With the moved pivot, the lowered arm hangs from inside the chest and covers the lapel and the crest; the split keeps
the shoulder width.
- schoolboy: 18 637 vs 5 769 defect px (left shoulder);
- tough student: 17 682 vs 6 585.

## 3. Full body

| sheet | content |
|---|---|
| `docs/phase4_2/audit/<character>/SIMULATION_poses_rigid_4_1.jpg` | the ten poses with the Phase 4.1 binding |
| `docs/phase4_2/audit/<character>/SIMULATION_poses_hybrid.jpg` | the same poses with the automated hybrid rig |
| `docs/phase4_2/audit/<character>/SIMULATION_poses_hybrid_correctives.jpg` | with the pending correctives (review) |
| `docs/phase4_2/cloud_checks/<character>/SIMULATION_pose_sheet.jpg` | what the default native build should render, in the package canvas |
| `docs/phase4_2/cloud_checks/<character>/SIMULATION_planned_sheet.jpg` | the same after the manual Smart Bone switches (MT-1/MT-2) |
| `docs/phase4_2/cloud_checks/<character>/SIMULATION_review_planned_sheet.jpg` | review build (+ caps, shoe tops) after the Smart Bone switches |

Things a person will notice in the full-body sheets, and why:

- **Both arms lowered.** The arms hang from the right place now, but a T-pose sleeve rotated 65° still shows its
  T-pose folds and lies partly over the blazer front. This is the limit of rigid rotation of front-view T-pose art.
  A convincing arms-down pose needs an arms-down sleeve drawing (ART-shoulder_lowered; a Switch layer selected by the
  shoulder angle) or mesh deformation verified in Moho (MT-3 tests the torso; the same test applies to the
  shoulder).
- **Knee bends** are in-plane joint tests on front-view art. They show that the joint holds together; they are not
  real knee flexion (REQUIRES_NEW_VIEW).
- **Torso lean.** The blazer opens at the waist into white wedges (MISSING_ARTWORK; flexible binding: MT-3).
- **Return to neutral** is bit-identical to frame 1 in the simulation.

## 4. Second character

Every sheet exists for `tough_student_test`; it was built from its own data only (no schoolboy coordinates, masks,
colours or corrective shapes). See [`phase4_2_native_correctives_report.md`](phase4_2_native_correctives_report.md)
§9 for the numbers.

## 5. What is still missing for the visual verdict

1. The Mac run (`--phase 4.2`, then `--review`) on both characters, which gives the native renders and comparisons.
2. MT-1: the Smart Bone switch in Moho, so the planned fills appear in native renders at all.
3. Your review of the pending assets (`autorig review` / the editor's Correctives tab) and of the renders.
4. The drawings no generator can make: an arms-down sleeve, a side-view knee, the shirt under the blazer at the waist
   (`artwork_requests.json`).
