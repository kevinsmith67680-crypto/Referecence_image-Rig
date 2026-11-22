# Phase 3.6 implementation plan: expressive animation, contacts, motion curves and secondary motion

Starting point: branch `claude/eloquent-cori-iv4ab1`, commit `cd2476e` (Phase 3.5, verdict PARTIAL). Baseline:
- 517 backend tests;
- 11 frontend unit tests;
- browser e2e passing;
- 2 native Moho tests NOT EXECUTED.

This plan was written after reading the code and re-running parts of the Phase 3.5 acting test. It was written
before any Phase 3.6 code.

## 1. What exists (audit)

| Component | Where | What it does | Reuse in 3.6 |
|---|---|---|---|
| MotionPlanner | `motion/planner.py` | presets (relaxed, pointing, shrug, arm raise, head tilt, squat, sitting, side lean, lean forward/back) and phase-driven front-view gaits | base intents for every frame |
| ArtisticPosePlanner | `artistic/poses.py` | whole-body pose → stage-1 intent (`intent_fn`): weight leg, contrapposto, line of action, head, clavicles, arm gestures | the per-frame pose layer |
| SkeletalPoseSolver | `motion/solver.py` | the one solver: FK/IK, limits, pelvis from contact, static balance | unchanged; reached only through `MotionEngine.pose(intent_fn=...)` |
| IK | `motion/ik.py` | `TwoBoneIKSolver`, `solve_axis_aware`, arm/leg controllers, `FootContactController` (state + lock error per foot) | hand contacts become `ArmIntent.target`; foot contacts are planted `LegIntent`s |
| Curves | `artistic/transitions.py` | quintic `ease`, C2 `bump`, overshoot envelope, `LEAD` table (leads/trails), `ActingSequence` | generalised into a curve library; `ActingSequence` stays as the Phase 3.5 baseline |
| Gesture presets | `artistic/poses.py` `action_library()` | 12 actions + 2 with variants | key poses of the gesture library |
| Pose-space corrections | `garmentrig/correctives.py` `PoseSpaceCorrectionEngine` | eased ramps, RBF keys, exclusive groups | unchanged (already continuous in pose) |
| Secondary motion | `motion/secondary.py` | damped springs, **periodic steady state for gait cycles only** (FFT) | spring parameters reused; a new deterministic integrator is needed for non-periodic sequences |
| Garment secondary terms | `motion/garments.py` | `PoseIntent.garment = {hem_lag_deg, hair_lag_deg}` → blazer-hem lag and hair sway terms in the structured deformer | the output channel of the new secondary controller |
| Keyframe format | `ActingSequence` | `KeyPose(t, ArtisticPose, hold, anticipation, overshoot)`; hand switch at half the move | extended into an editable timeline (keys, clips, contacts, offsets, curves, layers) |
| Motion smoothing tests | `artistic/evaluate.py` | `continuity_check` (parameter velocity jumps, solver clamps, balance saturation); gait cycle checks in `motion/quality.py` | kept; extended with foot sliding and hand-contact checks |
| Acting sequence | `artistic/evidence.py` `acting_test()` | 78 frames, 12 fps, 6.4 s | baseline for the frame-aligned before/after |
| Artwork-switch timing | `motion/handselect.py` | hysteresis, holds (`MIN_HOLD_S` 0.4), aim easing | used by the artwork resolver |
| Garment pipeline | `garmentrig/deform.py` `StructuredDeformer` | rigid segments, weight meshes, rigid fits (badge, buttons, tie, pocket flaps), deferred folds | unchanged |
| Approvals | editor project | **0 approved items**: hand-store drawings, correctives, extensions, pivots and edges are all pending | reported per gesture; never approved in code |
| Editor | `editor/` | op log (undo/redo, versions, schema v7), Pose tab with background render jobs and a flipbook | new Performance tab and schema v8 `performance.*` ops |

## 2. Limitations found

1. **Planted feet slide during the Phase 3.5 acting test.** This is new, measured on skeleton-only solves of the 78
   frames. `ArtisticPosePlanner.apply` moves the free foot inward (`free_foot_in`), turns it out by 6° and raises
   its knee minimum when that leg's weight share drops below 0.42. It is a threshold, so when the weight passes
   from one leg to the other, a planted foot jumps 13.4 px in one frame.
   - The jumps happen at frames 38, 40, 51 and 53: right ankle 461.3 → 474.6 px and back; left 623.1 → 609.7 px
     and back.
   - The Phase 3.5 continuity check did not catch it. The jump comes from a rule inside the pose builder, not from
     a parameter curve or a solver clamp. A foot-sliding check on the solved ankles is needed.
2. Weight beyond about 0.73 saturates the static balance, which stops the pelvis mid-move. Phase 3.5 avoided it by
   hand-tuning two key weights. There is no general guard.
3. Timing is one curve (quintic) for every parameter, plus a fixed lead table. There are:
   - no hold keys, Bézier or Hermite curves, per-channel curves or editable curves;
   - no multi-key paths that keep their velocity through a key (every key is a stop).
4. Anticipation is a single abduction bump. There are no per-gesture recipes (torso preparation before a point,
   shoulder dip before a shrug), no intensity control and no per-clip disable.
5. There are no hand contacts other than the planner's fixed hip/knee targets. There are no contact spans, blending,
   release, moving targets or priorities, and no desk, seat or object.
6. Secondary motion exists only inside gait cycles (periodic FFT). Static acting sequences have no hair or hem
   follow-through.
7. There are no motion layers, masks or blend weights: a pose is one `ArtisticPose`.
8. There are no acting styles, no reusable gesture clips with metadata, and no dialogue event vocabulary.
9. The editor has no timeline: the Artistic tab ranks single poses, and the Pose tab plays gait cycles.
10. Rendering takes about 3 s per frame. Only the skeleton solve is interactive: 7.6 ms per frame, measured.

## 3. Architecture (new package `src/autorig/performance/`, no second solver)

```
PerformanceDirector                       director.py: evaluates a Performance (timeline) per frame; renders
 ├─ ActingIntentPlanner                   intent.py: style + gestures/events -> clips on layers
 ├─ KeyPoseSequencer                      sequencer.py: keys, holds, per-channel curves, multi-key Hermite paths
 ├─ TimingCurveEngine                     curves.py: linear, ease in/out, ease-in-out, cubic Bezier, Hermite
 │                                        (monotone), hold, custom points, C2 anticipation/overshoot bumps
 ├─ AnticipationAndFollowThroughController  anticipation.py: per-gesture recipes, intensity, disable
 ├─ MotionOverlapController               overlap.py: bounded per-group offsets (editable metadata)
 ├─ WeightTransferController              weight.py: achievable weight range, soft remap, continuous free-leg
 │                                        softening, planted feet, step events (lift/land)
 ├─ ContactConstraintController           contacts.py: contact specs -> IK targets with C2 acquire/release
 ├─ SeatAndInteractionController          seat.py: desk/seat props, pelvis-to-seat, diagnostics
 ├─ MotionLayerController                 layers.py: BODY_BASE ... FUTURE_FACIAL_CONTROLS, masks, weights
 ├─ SecondaryMotionController             secondary.py: fixed-step deterministic springs -> hem/hair terms
 ├─ ArtworkAndCorrectionResolver          resolver.py: hand actions with holds, approvals, correctives
 └─ PerformanceQualityEvaluator           evaluate.py: geometric / motion / artistic, kept separate
 + ActingStyleProfile (styles.py), GesturePerformanceLibrary (gestures.py),
   DialogueActingController (dialogue.py), export metadata + LivePerformanceControlSpec (export.py)
```

Data flow per frame, in a fixed order:
1. Evaluate the layer tracks (each a key sequence) at `t`.
2. Compose them in joint-parameter space (angles in degrees, weight share; no positional averaging).
3. Apply the anatomical soft limits.
4. Weight remap and foot locks.
5. Contacts: a skeleton-only free solve, then IK targets blended by each contact's weight.
6. Write the secondary-motion garment terms.
7. Make one `MotionEngine.pose(intent_fn=...)` call.
8. Hand switches through the existing switch system (pending drawings only previewed).
9. Correctives through the existing runtime.

## 4. Rules

- The single solver and the existing deformer stay as they are. Every Phase 3.6 effect enters as stage-1 intent.
- Planted feet do not move unless a step event lifts them. Free-leg softening is a continuous function of the leg's
  weight share.
- Overshoot never leaves the anatomical range: parameters are soft-clamped before the solve.
- Secondary motion is a pure function of time. It is integrated from t = 0 at a fixed internal step, cached, and
  looked up, so scrubbing, random access, replay and any frame rate give the same values.
- The tie needs artwork: it cannot swing because the vest behind it is not drawn (NEEDS_ARTWORK). Rigid parts (badge,
  buttons, collar, lapels) keep their rigid fits.
- Missing artwork stays missing:
  - the shrug's palm-up hands (NEEDS_ARTWORK; a supported alternative shrug and a labelled review preview are
    shown);
  - seated thighs (REQUIRES_ALTERNATIVE_ARTWORK);
  - forward lean, nod and head turn (REQUIRES_NEW_VIEW).
- Pending drawings are previewed with labels only and never approved.

## 5. Implementation order

1. `curves.py`, `sequencer.py`, `overlap.py`, `anticipation.py` + tests (continuity at boundaries, holds, frame-rate
   independence).
2. `weight.py`: the foot-slide fix, a guard on the achievable weight range, step events.
3. `contacts.py`, `seat.py`: desk/hip/knee/object/elbow/seat contacts through `ArmIntent.target` / planted legs.
4. `secondary.py` (deterministic springs), `layers.py`, `styles.py`, `gestures.py`, `dialogue.py`.
5. `director.py`, `resolver.py`, `evaluate.py`, `export.py` (Moho metadata + compatibility, live control spec).
6. Editor:
   - schema v8 `performance` state and ops;
   - API (track, frame render, background animation job);
   - Performance tab (timeline, curves, clips, markers, playback, layers);
   - e2e.
7. Evidence on the real schoolboy:
   - upgraded acting test, frame-aligned with a re-render of the Phase 3.5 baseline;
   - classroom desk test;
   - secondary-motion demo;
   - benchmark.
8. Report, full suites, commit. Stop before Phase 4.
