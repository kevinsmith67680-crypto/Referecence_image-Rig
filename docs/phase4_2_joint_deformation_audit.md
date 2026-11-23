# Phase 4.2: joint deformation audit (Stage 1)

| | |
|---|---|
| Question | for each shoulder, elbow, hip, knee and torso defect of the Phase 4.1 rig, what is the cause? |
| Method | measured experiments on the rigid native binding (SIMULATION), one change at a time, plus a counterfactual for bone placement |
| Native renders | **NOT EXECUTED.** No Phase 4.1 or 4.2 native run exists (`docs/phase4/evidence/native_runs_4_1/` is empty) |
| Code | `src/autorig/native/deform_audit.py`, `src/autorig/native/hybrid.py`; `autorig deform-audit <package> --out <dir>` |
| Evidence | `docs/phase4_2/audit/<character>/` (`joint_audit.json`, `SIMULATION_*.jpg`) |

Every number and picture here comes from the Python simulation of rigid layer binding. Phase 4.0 verified that
simulation against Moho for the rest pose only; posed frames have never been rendered by Moho. The causes below
are what the simulation can show. The native renders the request asks for wait for the Mac run
([`phase4_2_moho_validation.md`](phase4_2_moho_validation.md)), and so does the native/simulation comparison.

## 1. What was measured

The ten Phase 4.2 test poses (`hybrid.poses_42`):

| frame | pose |
|---|---|
| 1 | neutral |
| 10 | both arms lowered |
| 20 | left arm raised |
| 30 | right arm raised |
| 40 | left elbow 90° |
| 50 | right elbow 90° |
| 60 | left knee bent |
| 70 | right knee bent |
| 80 | torso leaning |
| 90 | return to neutral |

Rotation signs come from each bone's own direction, so the same code runs on any character. The knee poses are
in-plane mechanical tests. The lowered-arm pose rotates only the upper arms; the clavicle drop used earlier opened a
crack along the lapel (§4).

Per joint and pose (`deform_audit.JointAudit.measure`; thresholds in `THRESHOLDS`, scaled by the limb's half
thickness):

| metric | what it measures | cause it points to |
|---|---|---|
| `hole_px` | new gap pixels near the joint that a closing fills in the pose but not at rest (wedges, triangles) | missing artwork, or inadequate overlap |
| `crack_px` | the same with a 3 px closing (thin cracks) | inadequate overlap |
| `hidden_fill_px` | the part of the holes the pending Phase 2 reconstruction covers | missing hidden artwork |
| `sweep_px` | limb pixels behind the pivot that swing outside the rest silhouette | the layer cut is not at the joint |
| `occluded_px` | moved limb pixels drawn under a body layer | layer ordering |
| `limb_over_body_px` | limb pixels newly drawn over the body (inward collapse) | bone placement (counterfactual) |
| cut geometry | where the limb layer meets its neighbours, along the bone axis; the transverse cut vs a seam along the limb | layer cut or bone placement |

The causes come from experiments, not from thresholds alone. `deform_audit.diagnose` compares variants of the same rig:

| variant | change |
|---|---|
| `rigid_4_1` | the Phase 4.1 rig |
| `order` | arm chains drawn above the torso |
| `split` | limb-root layers split at their pivot planes; detached specks follow their neighbour |
| `hybrid` | split + order (the automated native rig) |
| `hybrid_review` | + pending Phase 2 reconstruction (review only) |
| `hybrid_correctives` | + pending corrective artwork (review only, §5) |

A cause is assigned when its experiment removes the defect: LAYER_ORDER if `order` removes the occlusion, LAYER_CUT
if `split` removes the swing, MISSING_HIDDEN_ARTWORK if the pending reconstruction fills at least half the gap,
otherwise MISSING_ARTWORK.

## 2. Bone placement vs layer cut: the counterfactual

The request asks not to rebuild the skeleton before the cause is known. The shoulder is the only joint whose
transverse layer cut is far from its pivot:

| joint | cut offset (schoolboy) | half thickness | pivot evidence |
|---|---|---|---|
| shoulder l | −61.9 px (behind the pivot) | 51.5 | armpit measured on the silhouette, Phase 3.3 garment-aligned |
| shoulder r | −49.9 px | 54.5 | same |
| elbows, hips, knees | 0.7 – 3.2 px | 47 – 66 | the cut is at the pivot |

There are two ways to make the cut and the pivot meet. Both were run (`JointAudit.pivot_experiment`, sheet
`SIMULATION_pivot_experiment.jpg`):

| shoulder | pivot moved onto the cut (skeleton changes) | cut moved onto the pivot (`split`, layers change) | supports |
|---|---|---|---|
| left | 18 637 defect px | 5 769 | **LAYER_CUT** |
| right | 17 380 | 6 584 | **LAYER_CUT** |

Defect px = holes + sweep + limb pixels newly drawn over the body. Moving the pivot 50–62 px into the torso makes a
lowered arm hang from inside the chest and cover it. The bone placement is right; the Phase 1 layer cut (along the
blazer's armhole seam) is not at the joint. The knee "cut" looked 120 px off when measured by a centroid. In fact it
is a clean cut at the pivot plus a 2 px thigh sliver that runs down the shin (593 px on the left). That is a layer
cut defect, not a bone placement defect.

## 3. Root causes (schoolboy)

From `docs/phase4_2/audit/schoolboy_tpose/joint_audit.json` (`diagnosis`). "Rigid" is the Phase 4.1 binding;
"hybrid" is split + order, with original pixels only.

| joint, pose | rigid 4.1: holes / sweep / occluded | hybrid: holes / sweep / occluded | causes |
|---|---|---|---|
| shoulder l, arms lowered | 9 / **2190** / 0 | 214 / 0 / 0 | LAYER_CUT (oversized shoulder: the sleeve cap swings out), then MISSING_ARTWORK (the shoulder line of a lowered sleeve is not drawn) |
| shoulder r, arms lowered | 21 / **1357** / **3723** | 206 / 0 / 0 | LAYER_CUT, LAYER_ORDER (the right arm was drawn under the torso), MISSING_ARTWORK |
| shoulder l, raised | **1243** / 633 / 0 | 383 / 0 / 0 | LAYER_CUT, MISSING_ARTWORK (the triangular gap: no armpit is drawn and no reconstruction covers it) |
| shoulder r, raised | **1879** / 306 / 0 | 394 / 0 / 0 | same |
| elbow l, 90° | 167 / 0 / 0 | 150 / 0 / 0 | MISSING_ARTWORK (the outer elbow notch; the pending reconstruction covers 0 of it) |
| elbow r, 90° | 188 / 0 / 0 | 188 / 0 / 0 | same |
| hip l, knee pose | 1011 / 0 / 1082 | 1011 / 0 / 1071 | MISSING_ARTWORK (the trouser top under the blazer hem), INADEQUATE_OVERLAP, UNSUPPORTED_VIEW |
| knee l | 814 / 1 / 0 | 766 / 20 / 0 | LAYER_CUT (thigh sliver), MISSING_ARTWORK, UNSUPPORTED_VIEW |
| hip r | 1046 / 0 / 1082 | 1046 / 0 / 1082 | as hip l |
| knee r | 752 / 165 / 0 | 736 / 165 / 0 | MISSING_ARTWORK, UNSUPPORTED_VIEW |
| waist, lean | 1676 / 0 / 0 | 1674 / 0 / 0 | LAYER_CUT (pelvis pixels above the waist), INADEQUATE_OVERLAP, MISSING_ARTWORK (the shirt/sweater under the blazer front) |

Every joint also carries MISSING_CORRECTIVE_DRAWING: no corrective drawing is approved for any joint.

Totals over all joints and poses:

| variant | holes | cracks | sweep | occluded |
|---|---|---|---|---|
| rigid_4_1 | 8 806 | 672 | 4 652 | 5 887 |
| order | 8 806 | 672 | 4 652 | 2 164 |
| split | 6 768 | 404 | 185 | 3 582 |
| hybrid (split + order) | 6 768 | 404 | 185 | 2 153 |
| hybrid_review (+ pending reconstruction) | 6 595 | 465 | 185 | 2 153 |
| hybrid_correctives (+ pending correctives) | **2 023** | 206 | 185 | 2 153 |

The 2 153 occluded px left in the hybrid are the thighs passing under the pelvis layer, where the blazer hem and
trouser top are drawn in front. That is the correct draw order, so it is not counted as a defect.

The pending Phase 2 reconstruction helps little at the joints (8 806 → 6 595 holes with everything else equal). Its
pixels were generated for hidden regions *under* other layers, not for the regions a moving limb uncovers. At the
elbows it showed dark flaps. It is therefore left out of the review build by default
(`--include-pending-extensions` adds it).

## 4. Findings by cause

| cause | where | evidence | fix (Stage 2+) |
|---|---|---|---|
| incorrect bone placement | none | the counterfactual (§2) | none: the skeleton is kept |
| incorrect layer binding / layer cut | shoulders (sleeve cap behind the pivot), knee (thigh sliver), detached specks (stray line under a raised arm) | sweep 4 652 → 185 after the split | `split` + speck reassignment (automated natively) |
| inadequate overlap | thin cracks at the hips and waist | crack_px | the native gap fills (pending) |
| incorrect layer ordering | right arm under the torso | occluded 3 723 → 0 | `order` (automated natively) |
| missing hidden artwork | every joint that opens | holes left in the hybrid; the reconstruction covers ≤ 4 % at the shoulders, hips and waist | corrective artwork (pending) or drawings |
| inappropriate mesh influence | not applicable | native binding is rigid; no mesh is bound | — |
| unsupported viewing angle | knee and hip bends | front-view art; real flexion is depth motion | a side or 3/4 view drawing (REQUIRES_NEW_VIEW) |
| missing corrective drawings | all | 0 approved correctives | review of the pending assets, new drawings |

Clavicle rotation: with the sleeve cap bound to the clavicle, a 4° clavicle drop opened a white crack along the
lapel (115 px). Lowering the arms does not need the drop, so the test pose no longer has it.

## 5. Generalisation (tough student)

`docs/phase4_2/audit/tough_student_test/` is the same audit for the second character, run from its own package.
The causes are the same. The shoulder cut lies 50.2 / 47.3 px behind the pivot, and the counterfactual again rejects
bone placement: 17 682 vs 6 585 and 17 848 vs 7 045 defect px. There is one difference: the tough student's right arm
is already drawn above the torso, so LAYER_ORDER does not occur.

| variant | holes | cracks | sweep | occluded |
|---|---|---|---|---|
| rigid_4_1 | 9 769 | 721 | 3 022 | 1 667 |
| hybrid | 7 422 | 485 | 77 | 1 647 |
| hybrid_correctives | 2 150 (waist: 1 826) | 278 | 77 | 1 647 |

The full comparison is in [`phase4_2_native_correctives_report.md`](phase4_2_native_correctives_report.md) §9.

## 6. Limits of this audit

- **SIMULATION only.** Moho's bilinear filtering, its rendering of rotated images and its actual layer binding of
  posed frames are not measured.
- The metrics never score artistic quality. The close-up sheets are for a person to judge.
- Thresholds are module constants (`deform_audit.THRESHOLDS`) and scale with the limb's measured half thickness.
