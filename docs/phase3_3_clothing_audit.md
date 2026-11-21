# Phase 3.3 audit: why the schoolboy's clothing distorts

**Method.** I read the deformation stack:
- `phase2/skeleton.py`: bones, pivots, home bones, blend bands, `influence`;
- `phase2/skinning.py`: the per-pixel inverse-mapping deformer, tears, Jacobian statistics;
- `motion/garments.py`: `GarmentModel` regions, `GarmentSkeleton` homes, `BAND_RULES`, hem lag;
- `motion/pipeline.py`: layer order, switch resolution, render;
- `motion/quality.py`.

I then rendered the 12 test poses of the brief and close-ups of the shoulders, elbows, collar, lapels, hem, hips, knees
and trouser cuffs with the code as it was at commit 66f6730 (Phase 3.2 + hand replacement). The renders used the real
T-pose package `projects/schoolboy_tpose/character_package` and the original hands (nothing previewed). The before
renders are kept in the Phase 3.3 evidence (`natural_pose/garments/before/`).

## How the deformation worked

**Pixel model.** Each layer pixel gets a *home bone* by position: joint planes and body zones, not garment semantics.
It moves by φ(p) = Root ∘ R_b1(f_b1(p)·θ_b1) ∘ R_b2(…), where `f_b` interpolates a bone's rotation across a blend band
around each joint:
- shoulder: 0.85h into the chest and 1.0h into the sleeve;
- elbow: 0.6h on each side;
- knee: 0.55h on each side.

`BAND_RULES` widens the bands with the joint angle, by up to +70 % and up to 48 % of the bone length. Each layer is
rendered by inverse mapping (fixed point + Newton) and composited in a per-pose z-order. The `GarmentModel` labels
(blazer body, lapels, sleeves, cuffs, trouser parts, shoes) are only used for the hem override and diagnostics; they
do not decide what follows which bone.

**The source is a T-pose.** The arms are horizontal in the drawing, so "neutral standing" (arms down) is an ~80°
shoulder rotation away from the artwork. It is one of the most deformed poses, not the least.

## Reproduced defects (before)

| Test | Feasibility | Tear points | Visible defects |
|---|---|---|---|
| neutral standing | supported (corrections) | 5 | wide puffy shoulders (silhouette 386 px at shoulder height vs a 201 px blazer body), fan creases in the shoulder pads, white hairline dots along both shoulder seams and elbows, trouser fabric over the shoelaces |
| left arm 45° | supported | 1 | shoulder fan, cuff patch on the shoes |
| right arm 90° | supported | 1 | (close to the drawn T-pose: little distortion) |
| both arms raised | NEEDS_ARTWORK (gusset) | 52 | large transparent wedges at both armpits, squeezed shoulder pads |
| elbows bent 95° | supported | 1 | bulbous rubber elbows, transparent dots at the inner crease, folded sleeve (det < 0) |
| pointing | NEEDS_ARTWORK (hand) | 29 | shoulder seam gaps |
| walking | supported | 3 | white crack along the left shoulder seam |
| running | supported | 10 | seam cracks, rubber elbows |
| sitting | NEEDS_ARTWORK (thighs, seat) | 12 | balloon knees, inner-knee gaps |
| squatting | supported | 10 | balloon knees with a transparent inner crease, puffy shoulders |
| lean forward | NEEDS_ARTWORK (3/4 torso) | 24 | rendered as a sideways lean (fallback); long open crack along the right shoulder seam |
| lean backward | NEEDS_ARTWORK (3/4 torso) | 33 | sideways-lean fallback; shoulder seam cracks |

**Measured on the neutral pose** (deformer Jacobian statistics, `render.stats`):
- 82 % of the upper-sleeve pixels and 31 % of the torso pixels are *blended*, i.e. moved by a fraction of a rotation
  instead of rigidly.
- The sleeve folds over itself (det min −0.003); the torso is squeezed to 0.20 of its area near the shoulders.
- Thighs are 35 % blended, and shins reach det 0.07.
- Generated (hidden, unreviewed) pixels are visible over the shoes: 1450 px right, 1398 px left.

## Root causes

| # | Cause | Kind | Evidence | Code |
|---|---|---|---|---|
| R1 | **Wide rotation-interpolation bands**, widened further with the joint angle, bend whole sleeves and trouser legs like rubber. Elbows and knees balloon and the inner sides fold. | bone weighting | 82 % of the sleeve blended; det < 0 at elbows (−0.44) and knees (−1.31) | `skeleton.BAND`, `Skeleton.influence`, `garments.BAND_RULES`, `GarmentDeformationEngine.corrections` |
| R2 | **Shoulder pivot on the sleeve axis at mid-height** (armpit line + 0.45h). Lowering the T-pose arm sweeps the sleeve cap about one half-thickness (54 px) outward, so the shoulders puff. Lifting the pivot 30 px narrows the silhouette from 386 to 340 px, but only when the blend no longer tears the chest (82 tear points in that experiment). | pivot placement | `build_skeleton` shoulder method; the lifted-pivot experiment | `phase2/skeleton.py` (`_shoulder`), editor bones |
| R3 | **Garment semantics are ignored for influence.** Blazer-body, lapel and shoulder-pad pixels within the shoulder band take part of the arm rotation (position-based home + band), so the lapel is pulled by the arm and the chest silhouette widens. The garment labels exist but do not drive the weights. | artwork separation / weighting | the shoulder pads fan in every arm pose; torso det 0.20 | `GarmentSkeleton.home/influence`, `GarmentModel` |
| R4 | **Layers are warped independently along a straight Phase 1 cut** (torso / sleeve, sleeve / forearm, thigh / shin). Where their influence fields stretch differently the resampled edges separate, giving hairline dots and long seam cracks. The 1 px bleed ring cannot close them. | rendering of split artwork | 5–52 tear points per pose; cracks along the shoulder seams | `Deformer.render_layer`, bleed ring |
| R5 | **Generated pixels composited at their layer's z.** The "trouser cuff over the shoe" rule draws the shin layer, including its reconstructed hidden part, over the shoe, so unreviewed pixels cover original shoe pixels in every pose. | rendering order | 1450 / 1398 generated px over the shoes | `motion_order`, `Deformer.render` |
| R6 | **Missing artwork:** armpit gussets for raised arms, deep elbow and knee drawings, thighs pointing at the viewer (sitting), 3/4 torso for forward/backward leans. These are reported (NEEDS_ARTWORK), but a raised arm still shows the transparent wedge when no gusset is approved. | missing artwork | both-arms-raised wedges; lean fallbacks | `CorrectionSwitchController`, planner specs |

**Not root causes:**
- Skeletal movement: the solver respects the limits, and the IK and bone lengths are correct.
- Mesh topology: there is no mesh; the deformer maps pixels directly.
- Hand switches: stored hands are rigid on the hand bone and the cuff stays on the forearm.

## What Phase 3.3 changes (summary; details in the report)

1. **Semantic garment parts**, each classed RIGID, LIMITED or FLEXIBLE (`GarmentStructureAnalyzer`). These parts,
   not position planes, decide which bones may move a pixel. Fixes R3.
2. **A per-layer garment mesh** with normalised, restricted, lockable bone weights and narrow joint transitions
   (editable, saved in the project). Replaces the bands (R1).
3. **Rigid garment parts drawn over deformable joint patches.** Sleeve and trouser segments, shoes, buttons and the
   badge move rigidly with their bone. The weighted joint patch underneath only fills the gaps that open at the outer
   side of a bend, and inner sides overlap in z-order. Closes the seam cracks (R4) and keeps the artwork unstretched
   (R1).
4. **Shoulder pivots aligned** to the top of the armhole, validated against the artwork (R2). The user can override
   them in the pivot editor.
5. **Generated pixels drawn beneath the layers that hid them in the source** (R5).
6. **`GarmentCorrectionController`** for angle-range corrective artwork, and **`GarmentDistortionEvaluator`**
   (per-garment PASS/WARNING/NEEDS_ARTWORK/FAIL), so missing artwork is reported per pose (R6).
