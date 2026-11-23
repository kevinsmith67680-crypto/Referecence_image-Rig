# Phase 4.2: native joint deformation and corrective rigging report

| | |
|---|---|
| **Verdict** | **BLOCKED — NATIVE VALIDATION REQUIRED** (cloud work complete; equivalent to PARTIAL with every native item NOT EXECUTED) |
| Why | No Moho run of Phase 4.2 (or 4.1) exists, and Phase 4.0 gates 2, 4 and 5 are still open. The request says not to mark the phase PASS from the Python simulation, and the visual verdict belongs to a person |
| Root causes | layer cuts (shoulder cap behind the pivot, thigh sliver, specks), arm layer order, missing artwork. **Not** the skeleton: the pivot counterfactual gives about 3× more defects |
| Built | hybrid native rig (automated with Phase 4.0 operations), native gap fills (pending), Smart Bone plan, artwork requests, manual Moho tests, gates 4.2-A..K, harness `--phase 4.2` |
| Approved artwork | none. Every corrective is pending review |
| Phase 4.3 | not started |

Companion documents:
- [`phase4_2_joint_deformation_audit.md`](phase4_2_joint_deformation_audit.md) (Stage 1);
- [`phase4_2_visual_comparisons.md`](phase4_2_visual_comparisons.md);
- [`phase4_2_moho_validation.md`](phase4_2_moho_validation.md) (Stage 8).

## 1. Starting point

- The Phase 4.1 contact sheet the request refers to is the rigid SIMULATION in `docs/phase4_1/cloud_checks/`. It was
  never Moho output, and it is not treated as native evidence here.
- Native evidence on the branch, unchanged since Phase 4.0: two Phase 4.0 runs (build and save passed; reopen and
  renders not executed) and no Phase 4.1 run.
- Every "before/after" below is therefore SIMULATION against SIMULATION. The native before/after (4.1 Moho render vs
  4.2 Moho render) is written by `native-hybrid-verify` once the runs exist.

## 2. Stage 1: root causes (summary)

| cause (request's list) | found where | how it was shown |
|---|---|---|
| incorrect bone placement | **nowhere** | moving the shoulder pivot onto the layer cut: 18 637 / 17 380 defect px vs 5 769 / 6 584 for moving the cut (schoolboy) |
| incorrect layer binding (cut) | shoulders: the sleeve cap lies 50–62 px behind the pivot; knee: a thigh sliver down the shin; detached specks | sweep 4 652 → 185 px after the split |
| missing hidden artwork | every opening joint; the Phase 2 reconstruction covers ≤ 4 % of the shoulder, hip and waist gaps | holes left in the hybrid; `hybrid_review` |
| inadequate overlap | thin cracks at the hips and waist (and at the lapel with a clavicle drop) | `crack_px` |
| incorrect layer ordering | right arm drawn under the torso | occluded 3 723 → 0 px with `order` |
| inappropriate mesh influence | not applicable | the native binding is rigid; no mesh is bound |
| unsupported viewing angle | knee and hip bends | front-view art; depth motion |
| missing corrective drawings | every joint | 0 approved correctives |

Details, per joint and pose: the audit document.

## 3. Stage 2: native deformation methods, by region

| region | method | native status |
|---|---|---|
| head, torso, pelvis, hands, feet, blazer details (badge, buttons, lapels, collar) | rigid binding, unchanged | automated (Phase 4.0 operations) |
| upper arm, thigh roots | **split at the pivot plane** (`__joint` on the clavicle / pelvis, `__spill` on the next bone) | automated |
| limb specks | **rebound to the neighbour they touch** | automated |
| arm chains vs torso | **draw order**: arms above the torso | automated |
| elbows, knees, shoe tops | clipped underlays beneath everything (Phase 3.4 caps and shoe tops), always visible, showing only through a gap | automated in the review build (`--review`); pending |
| shoulders (lowered and raised), hips | **native gap fills**: keyed images in a Switch layer beneath everything, bound to the clavicle's parent / pelvis, selected by a Smart Bone action on the moving bone | **manual** (MT-1/MT-2): no Lua evidence for Switch layers or Smart Bones |
| waist (lean) | flexible binding of the torso layer (MT-3, discovery) or a drawing | manual test; artwork MISSING |
| controlled image warping, custom meshes | not used: no evidence that Lua can set them, and the request forbids stretching | — |

What "automated" means: only image layers, `SetLayerParentBone` and angle keys. These are the operations Phase 4.0
ran in Moho 14.5.1, and the build script is the Phase 0 template under another name. The Lua allowlist is unchanged
in Phase 4.2. The 4.2 verify script adds no new member: it reads actions with `CountActions`, `ActionName` and
`IsSmartBoneAction` (allowlisted from documentation in Phase 0, never run natively), inside pcall.

## 4. Stage 3: shoulders

- **Lowered arm, "oversized shoulder".** Cause: the sleeve cap behind the pivot swings out (LAYER_CUT). The split keeps
  it on the clavicle: sweep 2 190 → 0 px on the left, 1 357 → 0 on the right. That leaves a step at the shoulder top
  (holes about 210 px). The native fill rounds it: holes 214 → 6 and 206 → 4 px.
  - The blazer chest, lapels, trim and crest are untouched. Only pixels behind the pivot moved between layers, and the
    fill is drawn beneath everything.
  - Still not convincing: a 65° rigid rotation of a T-pose sleeve keeps its T-pose folds and lies partly over the
    blazer front. A convincing arms-down pose needs an arms-down sleeve drawing (ART-shoulder_lowered) in a Switch
    layer selected by the shoulder angle, or verified mesh deformation.
- **Raised arm, triangular gap.** Cause: LAYER_CUT, then MISSING_ARTWORK. The split removes the swinging cap; the
  wedge under the arm remains (383 / 394 px near the joint). The native armpit closes the apex with a concave fold
  (holes 75 / 92 px) and leaves the space under the arm open.
- **Remaining raised-arm defect.** A thin dark outline sliver hangs below the raised arm on both characters. It is
  the torso's side outline, which Phase 1 put in the arm layer, and it rotates with the arm. It is connected to the
  arm layer, so the speck rule does not move it. A mask edit in the editor (moving those pixels to the torso) fixes it
  without new artwork.
- **Phase 3.4/3.5 gussets are not used natively.** They were baked on the Phase 3 garment deformer and lie where the
  T-pose arm is. In the rigid rig they showed as "wings" when the arms lowered and widened the raised-arm gap. The
  native fills replace them (`binding_plan.json` → `excluded_correctives`).
- **No excessive stretching.** Nothing is scaled; every piece is rigid.

## 5. Stage 4: elbows

| test | result (simulation) |
|---|---|
| 0° | unchanged (rest composite bit-exact with correctives on) |
| 90° | outer notch 150 / 188 px (hybrid) → 48 / 84 px with the pending Phase 3.4 caps; no cut edge at the outer elbow; the cuff moves only with the forearm and hand |
| 45° | the caps are always on (underlays), so they fill any opening from the first degrees |
| deeper than 125° | NEEDS_ARTWORK (Phase 3.2 rule): a deep-elbow drawing (ART-elbow) |

The caps are round. They are pending, and whether they read as "unnatural round caps" is for your review. The
pending Phase 2 reconstruction is not used at the elbows: it showed dark flaps there. The inner crease has no
drawing.

## 6. Stage 5: hips and knees

- **Trouser discontinuity at the hip.** MISSING_ARTWORK: the trouser top under the blazer hem is not drawn. The native
  hip fill continues the trouser fabric under the hem (holes 1 011 / 1 046 → 0 px) with the measured colour and
  outline. Pending.
- **Knee.** The thigh sliver now moves with the shin (split), and the Phase 3.4 knee caps close the in-plane notch
  (766 / 736 → 22 / 18 px). The outline stays continuous and the shoe stays attached (rigid on the foot; shoe tops
  pending).
- **Not imitated:** forward knee motion and foreshortening. The knee poses are in-plane joint tests, labelled
  REQUIRES_NEW_VIEW. Real flexion needs a side or 3/4 knee drawing (ART-knee, REQUIRES_NEW_VIEW).

## 7. Stage 6: torso

The lean (SPINE_LOWER 4°, SPINE_UPPER 4°, CHEST 6°) rotates the CHEST-bound torso layer against the PELVIS-bound
lower blazer. Two white wedges open between the front panels (1 674 px). This is MISSING_ARTWORK: the shirt or sweater
under the blazer front is not drawn and its colour cannot be measured (diffusion picked up the light lapel trim), so no
fill was generated. The blazer hem, shirt and chain are not distorted, because nothing is scaled; they move rigidly.

Options, in order:
1. flexible binding of the torso image layer to the spine bones, if Moho allows it (MT-3; the saved file format has a
   per-layer `flexi_bone_subset`);
2. an approved drawing of the shirt under the blazer front (ART-waist);
3. leaner test poses.

The lean is keyed only after reopening (save/reopen test).

## 8. Stage 7: original artwork and approval

- **Originals are preserved.** Split and specks only move original pixels between layers:
  - `partition_exact` is true for every source layer, on both characters (tested);
  - the rest composite is unchanged up to draw order on shared antialiased edges (max 1/255, tested);
  - with correctives it is bit-exact (tested).
- **Generated pixels.**
  - The native fills are new `GEOMETRIC_CORRECTION` assets: 6 per character, 4 keys each. They are in the append-only
    `CorrectiveArtworkStore` with hashes, pending, with Moho mapping `proposed_unverified`.
  - Nothing is approved or rejected in code (tested).
  - Approval is `approval.set` on `corrective:<id>@v1` through `autorig review` or the editor's Correctives tab.
- **No AI model was called.** Every fill comes from the character's own pixels: geometry, diffused fabric colour,
  the measured outline. Closing an alpha gap never approves anything.

## 9. Stage 9: generalisation (both characters)

Both characters went through the same commands with their own data:
1. `deform-audit`;
2. `native-fills`;
3. `native-hybrid`, default and `--review`;
4. the cloud check.

The tough student is the blond-and-red-haired character in `projects/tough_student_test/`.

| | schoolboy | tough student |
|---|---|---|
| shoulder cut behind the pivot (l / r) | −61.9 / −49.9 px | −50.2 / −47.3 px |
| pivot counterfactual, defect px (pivot at cut vs cut at pivot), l | 18 637 vs 5 769 → LAYER_CUT | 17 682 vs 6 585 → LAYER_CUT |
| same, r | 17 380 vs 6 584 → LAYER_CUT | 17 848 vs 7 045 → LAYER_CUT |
| right arm under the torso (LAYER_ORDER) | yes (3 723 px) | no (59 px): `order` is a no-op there |
| holes, rigid 4.1 → hybrid → + pending correctives | 8 806 → 6 768 → 2 023 | 9 769 → 7 422 → 2 150 |
| of which the waist (MISSING_ARTWORK) | 1 674 | 1 826 |
| sweep, rigid → hybrid | 4 652 → 185 | 3 022 → 77 |
| lowered shoulders, holes hybrid → fill (l / r) | 214 → 6 / 206 → 4 | 219 → 6 / 215 → 8 |
| raised shoulders | 383 → 75 / 394 → 92 | 358 → 60 / 406 → 55 |
| elbows 90° with caps | 150 → 48 / 188 → 84 | 187 → 64 / 173 → 81 |
| hips with fills | 1 011 → 0 / 1 046 → 0 | 986 → 0 / 1 167 → 0 |
| knees with caps (in-plane) | 766 → 22 / 736 → 18 | 935 → 23 / 950 → 27 |
| native gap fills in the store (pending) | 6 assets × 4 keys | 6 assets × 4 keys |
| hybrid package | own canvas, 25 bones, exact partition | own canvas, 25 bones, exact partition |

The causes and the fixes transfer: the same cut defect, the same fixes and the same missing artwork. The numbers
differ because the artwork differs. Only the layer order is character-specific: the tough student's right arm was
already drawn above the torso.

Character-specific data stays in the packages: corrective assets in each store; per-character native settings in
`natural_pose/native_rig.json` (none needed). The code has no character name, coordinate, colour or mask.

## 10. Unapproved artwork requirements (`artwork_requests.json`)

| request | need | status (both characters) |
|---|---|---|
| ART-shoulder_lowered-l/r | arms-down shoulder: sleeve cap and shoulder line for a lowered arm | PENDING_REVIEW (native fill); an arms-down sleeve drawing for convincing lowered arms |
| ART-shoulder_raised-l/r | armpit fold for a raised arm | PENDING_REVIEW (native armpit) |
| ART-elbow-l/r | elbow fold: outer cap + inner crease; deep elbow > 125° | PENDING_REVIEW (caps); deep elbow and crease MISSING |
| ART-hip-l/r | trouser top under the blazer hem | PENDING_REVIEW (native fill) |
| ART-knee-l/r | side or 3/4 knee for real flexion | REQUIRES_NEW_VIEW |
| ART-waist | shirt / sweater under the blazer front | MISSING |

## 11. Performance

See [`phase4_2_moho_validation.md`](phase4_2_moho_validation.md) §6: cloud tooling only (package about 30 s, fills about
2 min, audit about 4 min per character, mock scripts under 1 s). The M1 measurements (build, reopen, memory, render)
are recorded by the harness and **NOT EXECUTED**.

## 12. Regression tests

- `tests/test_p42_hybrid.py`: 34 tests covering:
  - the poses;
  - exact partition and the unchanged rest composite;
  - draw order and specks;
  - corrective layering, rest bit-exactness and exclusions;
  - Smart Bone key selection;
  - driver readings;
  - the stored fills (pending, hash-verified);
  - the cut geometry and the diagnosis logic;
  - the committed audit;
  - packages for both characters, scripts in the strict mock, gates (blocked; person-recorded gates with clearly
    named test fixtures);
  - the review build, canvas refusal and harness arguments.
- One existing assertion changed. `test_p41_fullbody.py::test_harness_phase_arguments` used `--phase 4.2` as the
  unknown phase; 4.2 now exists, so it uses `4.3`. The expected message is unchanged.
- Full suite: **710 passed, 2 skipped** (712 tests).
  - 712 = 676 + 2 from Phase 4.1, plus 34 new Phase 4.2 tests.
  - The 2 skips are the native Phase 0 hooks (they need a Phase 0 Moho report).
  - A single pytest process was OOM-killed at about 74 % (14 GB RSS, in the Phase 3 editor tests, before any Phase 4.2
    test ran), with nothing else running. The suite was therefore run with one fresh process per test file (31 files
    plus `tests/native/`): every file passed, including the editor tests that had been running when the single
    process died.
- Phase 3 renderer: `CorrectiveRuntime` ignores native assets (`placement.native`), so the Phase 3.4–3.8 renders and
  reports are unchanged. The native fills stay listed in the editor's Correctives tab for review.

## 13. Verdict

**BLOCKED — NATIVE VALIDATION REQUIRED.**
- Stage 1 is complete in simulation: the causes come from experiments, and the skeleton is kept because the
  counterfactual rejects bone placement.
- Stages 2–7 and 9 are built and cloud-tested.
- Stage 8 (native renders, Smart Bone activation, save/reopen, your visual verdict) is NOT EXECUTED.

The simulation shows the hybrid rig with pending correctives cutting joint holes from 8 806 to 2 023 px (schoolboy).
The visual quality is not approved by anyone, and the lowered arm and torso lean still need artwork. Next, on the
Mac:
1. `--phase 4.0`, to complete gates 2, 4 and 5;
2. `--phase 4.2`, then `--phase 4.2 --review` with MT-1, on both characters;
3. review the pending correctives and the renders;
4. push the run folders.

Phase 4.3 is not started.
