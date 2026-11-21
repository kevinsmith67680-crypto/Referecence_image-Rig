# Phase 3.4 baseline audit: Phase 3.3 defects, reproduced

**Method.** I started from commit 21a8146 (Phase 3.3), with the code unchanged, and re-rendered 15 poses of the real
package `projects/schoolboy_tpose/character_package`. Each render marks tear points:
- red = exposed gaps;
- blue = visible seams.

For every pose I listed the tear points by nearest joint and the clipped (over-stretched, undrawn) joint-patch pixels
by garment part. I then inspected the renders and close-ups myself; the scores were not taken on trust.

**Baseline copies:**
- The Phase 3.3 evidence in `natural_pose/garments/` (after renders, close-ups, sweeps, contact sheets, results
  and reports) is left unchanged as the baseline: it was produced by the Phase 3.3 code (commit 21a8146), and Phase
  3.4 does not regenerate it.
- The reproduction data is in `docs/phase3_4/baseline/`:
  - `repro.json`: counts per pose;
  - `defect_closeups.png`: the marked defects;
  - `sleeve_diagonal_pieces.png`: which renderer piece and layer draws each pixel at the shoulder.

| Pose | Gap points (near) | Visible seams (near) | Clipped patch px (part) | Generated px visible | s |
|---|---|---|---|---|---|
| neutral standing | 3 (right ankle) | 2 (right hand) | — | 241 | 2.43 |
| left arm 45° | 14 (right ankle) | 2 | — | 283 | 2.41 |
| right arm 90° | 11 (left ankle 10, shoulder 1) | 2 | — | 230 | 2.60 |
| right arm 120° | 30 (right shoulder 20, ankle 10) | 2 | sleeve_upper_r 1489 | 231 | 2.55 |
| both arms raised | 60 (shoulders 57) | 1 | sleeve_upper_r 4512, sleeve_upper_l 3954 | 82 | 2.66 |
| elbow 45° | 5 (ankle) | 2 | — | 237 | 2.61 |
| elbow 95° | 10 (ankle) | 2 | elbow_r 550 | 235 | 2.64 |
| elbow 135° | 9 (ankle) | 3 | elbow_r 1183 | 323 | 2.82 |
| pointing | 29 (shoulder 17, ankle 12) | 2 | sleeve_upper_r 840 | 295 | 3.40 |
| walking | 5 (ankle) | 2 (left shin) | — | 170 | 3.51 |
| running | 10 (ankle) | 9 (left shin 7) | — | 228 | 3.59 |
| sitting | 52 (ankles 39, shins 13) | 32 (shins 27) | knee_r 425, knee_l 556 | 617 | 2.94 |
| squatting | 38 (ankles) | 30 (shins 26) | — | 561 | 2.70 |
| side lean | 34 (neck 23, ankle 10) | 0 | — | 163 | 3.50 |
| head tilt | 26 (neck 18, ankle 8) | 2 | — | 182 | 3.00 |

## Defect inventory

| ID | Pose(s) | Part | Image location | Severity | Root cause | Artwork available | Recommended correction | Verification |
|---|---|---|---|---|---|---|---|---|
| D1 armpit gap | arm ≥ 110°, both raised, pointing | sleeve_upper, blazer side | wedge under the raised sleeve, lateral of the torso side (`defect_closeups.png`, tile 3) | high | confirmed: no artwork exists under the T-pose sleeve; the joint patch would need > 1.8× stretch, so Phase 3.3 leaves it undrawn | none (procedural Phase 2 gussets are per preset and pending review) | armpit gusset drawn beneath the torso and the sleeve, keyed by abduction | clipped/uncovered px at the shoulder, gap points, close-ups at 0/30/45/60/90/120° |
| D2 outer elbow | elbow 95°, 135° | elbow fold | outer corner of the bend (tile 4) | high at 95° (ragged), artwork at 135° | confirmed: the outer wedge needs > 4× patch stretch; the clipped edge is ragged | none | round joint cap beneath both rigid sleeve segments (95°); a deep-elbow drawing is still required > 125° | clipped px, close-ups at 45/90/95/135° |
| D3 hem/shoe gap | every standing pose, squat, sit | trouser_cuff, shoe | sliver at the hem corner on the inner side of each shoe (tile 6) | medium standing, high squatting | confirmed: the shoe's top under the hem was never drawn; the hem follows the shoe across the ankle band, but a turned shoe leaves a corner open | none (the foot layer has no hidden extension) | shoe-top extension of the foot layer, under the trouser, attached to the foot | ankle gap points, shoe close-ups standing/walk/run/sit/squat |
| D4 knee crease seam | squat, sit, running | knee fold | straight line across the inner knee (tiles 5, 8) | medium | confirmed: the rigid shin segment's straight proximal cut is drawn over the thigh | none | round knee cap beneath the segments; deep-knee drawing still required | seam points at the shins, knee close-ups |
| D5 straight sleeve diagonal | every pose with a lowered arm | sleeve_upper, blazer chest | straight top edge of the hanging sleeve across the chest (`sleeve_diagonal_pieces.png`: orange = rigid segment, blue = patch) | medium | confirmed: the rigid upper-sleeve segment starts exactly at the shoulder plane, so its straight cut meets the rotation-interpolated patch along a line | the patch itself (continuous) | start the rigid segment where the shoulder transition ends (the patch is continuous there) | shoulder close-ups; seam/edge check along the cut |
| D6 light fringe | limbs over dark cloth (sleeve inner edge, knees, elbows) | outlines | thin light line along the hanging sleeve's inner edge (`sleeve_diagonal_pieces.png`, left) | medium | confirmed: the source's anti-aliasing blends the outline with the white paper; Phase 1 layers are binary-alpha, so the light edge pixels are opaque | n/a | non-destructive edge decontamination of derived layers (alpha from the matte, colour from the outline), approval-gated | before/after edge crops over light and dark backgrounds; composite over white must equal the source |
| D7 collar/lapel FAIL (standing) | all standing poses | lapels, shirt_collar | collar points next to the neck (rest y 373–442; posed 607–781 × 508–578) | low (visual) | confirmed geometric, not a measurement bug: the neck transition is ±6 px wide but the collar is ~160 px wide, so a 2–3° neck rotation shears the collar tips ~4 px (99th-pct stretch 1.51 / 1.56) | n/a | widen and smooth the neck transition on the collar and lapels (LIMITED parts) | evaluator p99 on the same pixels; collar close-up |
| D8 jaw/ear slivers | head tilt, side lean | neck, head | thin gaps under the ear lobes (tiles 1, 2) | medium | suspected then confirmed: the neck layer's top follows the head across a ±50 px band, but the narrow neck-base band and the torso's ear/jaw fragments do not | none needed | head-band consistency for the neck fragments (rule fix) | neck gap points at head tilt / lean |
| D9 missing drawings | sitting, squat > 45°, elbow > 125°, leans, running knee drive, raised-shoulder collar | various | — | high | confirmed missing artwork | none | requirement records (Phase 3.4 planner); report NEEDS_ARTWORK | planner output and the NEEDS_ARTWORK sheet |
| D10 performance | all | — | — | medium | 2.2 s (legacy) → 3.0 s (structured) per pose; not profiled in Phase 3.3 | — | profile and optimise | benchmark A/B/C |

The squatting and sitting gaps include the knee-patch clipping (sit: knee_r 425 px, knee_l 556 px) and the hem/shoe
corner (D3). Sitting stays NEEDS_ARTWORK: the seated thighs point at the viewer.
