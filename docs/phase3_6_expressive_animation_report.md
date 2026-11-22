# Phase 3.6 report: expressive animation, contact constraints, motion curves and secondary motion

**Verdict: PARTIAL.**
- **Done:** the performance engine is implemented and tested on the real schoolboy:
  - timing curves, anticipation and follow-through, overlap;
  - weight transfer, hand contacts;
  - motion layers, acting styles, gesture library and dialogue events;
  - controlled secondary motion, the timeline editor;
  - Moho metadata and the live control spec.

  The deterministic motion checks pass on both benchmark performances.
- **Not done:** no person has reviewed any performance, gesture or drawing. The previews use hand drawings that
  are still pending review, and several gestures need artwork that does not exist. Native Moho was not run. See §27.

All renders come from AutoRig's own solver and renderer, on the real T-pose package
(`projects/schoolboy_tpose/character_package`), in the cloud sandbox. `autorig performance-evidence <package>`
regenerates:
- `natural_pose/performance/`;
- `reports/performance_quality.json`;
- `analysis/performance_animation.json` and `analysis/live_performance_controls.json`.

Three kinds of statement are kept apart:
- **Measured:** deterministic checks on the solved skeleton or the renders.
- **My reading:** what the renders look like to me, offered as a review aid.
- **Needs a person:** artistic approval. No such approval was recorded.

---

## 1. Previous animation audit

Full audit in `docs/phase3_6_implementation_plan.md` (written before the code). The main finding was a new defect:

**Planted feet slid in the Phase 3.5 acting test.**
- `ArtisticPosePlanner.apply` moves the free foot inward, turns it out by 6° and raises its knee minimum as soon as
  that leg's weight share drops below 0.42.
- That is a threshold, so whenever the weight passed from one leg to the other, a planted foot jumped **13.4 px in
  one frame**. It happened at frames 38, 40, 51 and 53 (right ankle 461.3 → 474.6 px and back; left 623.1 →
  609.7 px and back).
- The Phase 3.5 continuity check did not see it, because the jump came from a rule inside the pose builder, not from
  a parameter curve or a solver clamp.

The diagnostics chart `natural_pose/performance/acting/diagnostics.png` (top panel) shows the jumps.

Other limitations found:
- **Balance saturation:** a weight share above about 0.73 saturated the static balance. Phase 3.5 avoided it with
  hand-tuned key weights, not a guard.
- **Timing:** one curve for every parameter.
- **Anticipation:** a single abduction bump, with no recipes.
- **Contacts:** none for the hands beyond fixed hip and knee targets.
- **Secondary motion:** only inside gait cycles.
- **Motion layers:** none.
- **Styles, gesture clips and dialogue vocabulary:** none.
- **Editor:** no timeline.

## 2. Architecture changes

The new package is `src/autorig/performance/`. It reuses the one solver, the structured garment deformer, the switch
system and the corrective runtime. Every effect enters as stage-1 intent through `MotionEngine.pose(intent_fn=...)`.

| Component | Module | Reuses |
|---|---|---|
| PerformanceDirector | `director.py` (`Performance`, `PerformanceDirector`) | `MotionEngine.pose` |
| ActingIntentPlanner | `gestures.py`, `dialogue.py`, `styles.py` (intent → clips on layers) | `ArtisticPosePlanner` |
| KeyPoseSequencer | `sequencer.py` | Phase 3.5 key poses (`params_of`) |
| TimingCurveEngine | `curves.py` | `ease`, `bump` from `artistic/transitions.py` |
| AnticipationAndFollowThroughController | `anticipation.py` | the same C2 bump and overshoot envelope |
| MotionOverlapController | `overlap.py` | the Phase 3.5 lead/trail idea, now bounded and editable |
| WeightTransferController | `weight.py` | the solver's static balance (window only re-centred) |
| ContactConstraintController | `contacts.py` | `TwoBoneIKSolver` via `ArmIntent.target`/`pole`; planted `LegIntent`s |
| SeatAndInteractionController | `seat.py` | `ArtworkFeasibilityEngine` requirements |
| MotionLayerController | `layers.py` | — |
| SecondaryMotionController | `secondary.py` | the garment terms `hem_lag_deg` / `hair_lag_deg` of the structured deformer |
| ArtworkAndCorrectionResolver | `resolver.py` | switch groups (user choice), `HandPoseLibrary` statuses, `CorrectiveRuntime` |
| PerformanceQualityEvaluator | `evaluate.py` | `ArtisticAnimationEvaluator.continuity_check` |

Every frame is a pure function of time:
- the feet are locked from the t = 0 intent;
- the springs are a precomputed fixed-step table;
- the layer order is evaluated fresh for each frame;
- the hand poses are explicit.

## 3. Animation curve improvements

`curves.py` provides:
- `linear`, `ease_in`, `ease_out`;
- `ease_in_out` (quintic: zero velocity and acceleration at both ends; the default);
- `ease_in_out_cubic`;
- `bezier` (CSS-style; time stays monotone; authored overshoot allowed);
- `hold` (an authored step, reported as intended, never as a defect);
- `custom`: an editable monotone Hermite through artist points.

Curves can be set per key, per channel or per channel group. Keys marked `path` pass through with continuous
velocity (a monotone Hermite over the run, with no overshoot between keys). Evaluation is a function of continuous
time, so it does not depend on the frame rate or on the evaluation order (tested).

After the layers are composed, every parameter is soft-clamped (C1) inside its anatomical range, so an authored
overshoot never leaves the range and never kinks at the limit.

**Continuity detector.** The Phase 3.5 detector is kept and refined. A candidate velocity jump (a velocity change
above 10 % of the channel's range per second, at a 1 ms step) is now re-measured at a step 8 times finer:
- a smooth but fast curve changes its velocity by acceleration × step, so the change shrinks: it is counted as
  `fast_smooth_samples`, not as a defect;
- a real jump does not shrink.

Without this, the 0.17 s torso anticipation before the point produced 48 false "jumps". The Phase 3.5 test that a
sine-started anticipation is a velocity jump still passes, so detection power is unchanged and fast cartoon motion
is no longer classified as defective.

## 4. Anticipation and follow-through

`anticipation.py` holds per-gesture recipes (`point`, `raise`, `shrug`, `weight_shift`, `wave`, `surprise`,
`look`, `none`). Each recipe lists the channels that prepare against the move and the channels that follow through.
- **Pointing:** the torso and shoulder prepare, the arm pulls back slightly, then it extends and settles.
- **Shrug:** the shoulders dip, then rise.
- **Look:** no counter-move; the head leads through the overlap instead.

Limits and controls:
- The anticipation is a C2 bump over the first 32 % of the move: it starts and ends at rest, so there is no sine
  jump.
- It is capped at 15 % of the channel's move and at a per-group absolute limit (arms 12°, forearms 10°, shoulders
  4°, torso 3°).
- Intensity is set per key or clip, scaled by the acting style; 0 disables it.
- Follow-through overshoots past the key and settles inside the hold. Its residual at the end of the hold is 0 for
  every key in the acting test (measured).

Subtle moves get no recipe by default. The evaluator warns if one does (none in the benchmarks).

## 5. Contact constraint implementation

`ContactSpec` holds:
- the part and kind: hand to desk, hip, knee, object or two-hand object; elbow to desk; foot to ground; body to
  seat;
- the target and its space: a static point, a moving path (monotone Hermite keys), an object path plus an offset,
  or a solved joint plus an offset;
- start, end, acquire and release times;
- strength, hand orientation, the attachment point (palm or fingertips), the IK chain, the release behaviour
  (`return_to_pose` or `hold`) and a priority.

How a contact is solved:
1. A skeleton-only free solve of the same frame (without contacts) gives the free wrist, elbow and hand angle.
2. The wrist target is `lerp(free wrist, contact wrist, w(t))`, with `w(t)` a C2 ramp times the strength.
3. The elbow pole is the free elbow, and the hand direction blends from the free angle.

At `w = 0` the IK reproduces the free pose, so acquisition and release do not jump and the elbow keeps its side.
Priorities are resolved per chain. Elbow contacts project the elbow point onto the upper arm's reach circle, so the
two-bone IK lands the elbow exactly there. Unreachable targets are clamped by the IK solver and reported, never
stretched (tested).

## 6. Hand-to-object interactions

Measured, on the classroom desk and on synthetic object paths:

| Check | Result |
|---|---|
| Hand on desk, sliding while fully planted | 0.066 px (tolerance 1 px) |
| Contact acquisition / release jumps (48 Hz) | none |
| Moving object (contact follows a path) | < 1 px at t = 1.0, 1.6, 2.2 s (test) |
| Unreachable object | reported: IK clamped, contact error > 1 px, `reachable = false` (test) |
| A gesture clip on the contacted arm | cannot pull the hand off the desk (< 1 px, test) |
| Elbow contact | the elbow lands on the projected target within 2 px (test) |

## 7. Weight transfer

`WeightTransferController`:
1. **Feet.** It locks the planted feet at the performance's starting stance; they move only during an explicit
   `StepEvent`, which shifts the weight fully onto the support leg, lifts the foot on a C2 bump and lands it
   planted.
2. **Free leg.** Softening (knee minimum, turn-out) is a smooth function of each leg's weight share, ramping as it
   falls from 0.5 to 0.36. It is not a threshold.
3. **Balance window.** The window is centred on a calibrated estimate of the balanced pelvis offset (two
   skeleton-only solves: about 284 px per unit of weight). The solver's balance then converges for every weight
   share. Requested weights are soft-clamped to 0.16–0.84.

Measured:
- **Planted-foot travel:** 0.000 px in both benchmark performances, against 13.4 px jumps in the Phase 3.5 baseline.
- **Balance saturation:** none. Weight 0.83 keeps the pelvis travelling (test).

**Joint-limit finding.** With the feet locked at a wide stance, the classroom's lean, together with the balance
shift toward the planted hand, bent the support knee to 11.9°, past its 10° front-view limit. The evaluator reported
it as a joint-limit violation. The classroom performance now starts from a narrower stance (0.8 of natural); this is
a scenario choice, not a change to any limit.

## 8. Overlapping action

`MotionOverlapController` sets bounded per-group offsets (maximum ±0.25 of a move), stored as editable metadata in
each performance and per key:
- the pelvis leads (0.12), then the torso (0.06) and shoulders (0.03);
- the arms move on the beat (0);
- the forearms (−0.07), hands (−0.12) and head (−0.09) trail.

On attention keys (`head_lead`: look, point, and the confident and energetic styles), the head leads instead (+0.10).
Every group still arrives on the key at the end of the move, so key poses and holds are kept. Hair and the blazer hem
get their lag from the springs (§9).

## 9. Secondary motion

`SecondaryMotionController` drives damped springs:
- **Hair:** 1.9 Hz, damping 0.45, gain 0.85, limit 5°. Driver: head tilt plus body sway.
- **Blazer hem:** 2.4 Hz, damping 0.4, gain 1.3, limit 4°. Driver: pelvis roll plus torso lean.

They write the existing garment terms of the structured deformer, so the hair sways from its top (never at the
hairline) and the hem swings about its pivot. There is no cloth simulation.
- **Deterministic:** integrated once from t = 0 at a fixed 1/240 s step, cached, and looked up. Random access,
  scrubbing, replay and any frame rate give identical values (tested).
- **Controls:** reset times; keyframe overrides with C2 blends; per-part enable, stiffness, damping, gain and limit.

What is not driven:
- **Tie:** NEEDS_ARTWORK. It is drawn on the torso layer and the vest behind it does not exist, so a swing would
  open a hole.
- **Loose sleeves:** UNSUPPORTED; they are rigid segments in the garment rig.
- **Badge, buttons, tie, lapels, collar:** pixel-identical with and without secondary motion (render test).

## 10. Motion layering

`MotionLayerController` composes the layers in a fixed order:

| Order | Layer | Mode |
|---|---|---|
| 1 | BODY_BASE | override |
| 2 | ROOT_MOVEMENT | additive (idle, style posture) |
| 3 | UPPER_BODY_GESTURES | override; masked, weighted, anchored clips |
| 4 | HEAD_MOVEMENT | additive |
| 5 | CONTACT_CONSTRAINTS | IK, after every pose layer |
| 6 | HAND_SWITCHES | gesture clips, then artist markers |
| 7 | GARMENT_CORRECTIONS | render settings |
| 8 | SECONDARY_MOTION | garment terms |
| 9 | FUTURE_FACIAL_CONTROLS | isolated |

How the layers combine:
- **Joint space.** Composition works on angles and weight shares; positions are never averaged.
- **Anchors.** Anchored clips start and end at the current pose, and their C2 blend weight keeps velocity
  continuous at clip boundaries (tested).
- **Coexistence and override.** A head clip coexists with an upper-body gesture (tested). Contacts outrank gestures
  on their chain.
- **Facial isolation.** The facial layer accepts only facial channel names and never changes a body parameter
  (tested).
- **Editing.** Mute and weight per layer are project operations.

## 11. Acting personality profiles

`ActingStyleProfile` provides five profiles: `neutral`, `confident`, `arrogant`, `shy` and `energetic`.

They scale:
- gesture amplitude and duration;
- anticipation, overshoot and holds;
- torso involvement;
- head-leading;
- secondary-motion intensity.

They can also add a small posture offset (shoulders, weight, head tilt) on ROOT_MOVEMENT and HEAD_MOVEMENT.

They never touch the skeleton, the artwork or the garment rig. The bone heads, tails and lengths are identical in
every style (tested). Descriptions are about motion only. Every value is editable, and profiles are optional.

## 12. Gesture performance library

Each gesture records its tone, key poses, timing, curves, anticipation recipe, views, required artwork, contacts,
hand choices and secondary settings. Status is computed from the character's real artwork, and
`production_ready = false` for every gesture:

| Gesture | Status (right side) | Why |
|---|---|---|
| point | NEEDS_APPROVAL | pointing hand drawing pending review |
| explain | NEEDS_APPROVAL | cupped hands pending; palm-up missing |
| shrug (palms up) | **NEEDS_ARTWORK** | palm-up hands do not exist; alternative `shrug_low` |
| shrug_low (supported alternative) | SUPPORTED | shoulders carry the shrug, hands stay low |
| wave | SUPPORTED | the reference open palm (original artwork) |
| disagree | NEEDS_APPROVAL | palm-down pending; a real head shake (yaw) needs turned-head drawings |
| nod | REQUIRES_NEW_VIEW | a nod is a head pitch; the front-view head only tilts |
| surprise | SUPPORTED | open palms (original) |
| lean_towards | SUPPORTED | lateral lean; a forward lean is REQUIRES_NEW_VIEW |
| raise_hand | SUPPORTED | open palm (original) |
| return_neutral | SUPPORTED | relaxed hand pending, falls back to the original open palm |

"SUPPORTED" means the artwork exists and is original. It does not mean that a person has approved the performance.

## 13. Dialogue acting preparation

`DialogueActingController` turns semantic events into clips. Events closer together than their gestures are queued,
not stacked.

| Event | Body acting |
|---|---|
| SPEECH_EMPHASIS | a small forearm beat |
| QUESTION | explain |
| DISAGREEMENT | disagree |
| SURPRISE | surprise |
| POINT_AT_TARGET | point, with the head leading |
| LOOK_AT_TARGET | head tilt and weight toward the target |
| RETURN_TO_IDLE | return_neutral, then idle |

It also provides:
- **Idle:** a controlled breath in the shoulders and a slow, small weight sway, deterministic.
- **Listening:** turns with the head toward the speaker.

It writes body channels only. Mouth, brows and blinks stay on the isolated facial layer. There is no speech
recognition and no facial tracking.

## 14. Timeline editor

The new **Performance** tab (`editor/src/components/PerformancePanel.tsx`) reuses the editor architecture. Its API is
`/api/performance`, `/scenario`, `/track`, `/frame.png` and `/render` (the existing background job). It offers:
- a keyframe timeline with hold bars and lanes for gestures, head, root, contacts and hand switches;
- an animation curve view per channel;
- a per-key curve editor (kind and Bézier control points), anticipation recipe and overshoot;
- gesture clip placement at the playhead, with duration, anticipation, overshoot and amplitude;
- contact markers and hand switch markers (add and remove);
- layer mute toggles;
- style selection and secondary-motion controls (the tie is shown as blocked);
- loop, frame-by-frame stepping, a scrub slider and playback speed (0.25–2×);
- full-artwork frame render and animation render;
- save as and load.

Every edit is a project op in schema v8, so undo, redo and reload keep it.

The preview is the skeleton-only track: wireframe and curves, measured at about 16 (cloud sandbox; 20 ms per interactive frame evaluation including a contact free solve) ms per frame. Full-artwork
frames are rendered on demand.

e2e step 6i (headless Chromium, real package) covers:
- loading the acting test and checking "feet max step 0/0 px";
- selecting a key and switching its curve to Bézier (a project op);
- changing the style, adding a gesture clip and a hand marker, and muting a layer;
- stepping, playing and rendering a full frame;
- undo, and persistence after reload.

Screenshots: `docs/phase3_6/screenshots/`.

## 15. Animation quality evaluation

`PerformanceQualityEvaluator` keeps three categories separate. There is no single score.
- **Motion:**
  - parameter velocity jumps (with the resolution test) and solver kinks (clamps, balance saturation);
  - authored steps, listed separately;
  - foot sliding at 48 Hz, hand-contact sliding, contact-transition jumps, joint-limit violations.

  Fast smooth motion is not flagged.
- **Artistic hints:**
  - key-pose holds, timing, anticipation (presence and appropriateness), follow-through settling;
  - centre of mass and weight-leg clarity;
  - secondary saturation (time near the limit);
  - pending or missing hand frames, gesture statuses.

  `artistic_review_required` is always true.
- **Geometric:** exposed gaps and clipped pixels, plus the corrective-leakage test (protected original pixels: 0 in a
  classroom frame, tested).

## 16. Upgraded acting-test results

Files in `natural_pose/performance/acting/`: `before_after.gif`, `contact_sheet.png`, `diagnostics.png`,
`shrug_review_preview.png`.

The upgrade keeps the same 7 beats, key times and holds: 78 frames, 6.4 s, 12 fps, frame-aligned with a re-render of
the Phase 3.5 baseline.

Measured. The baseline is the Phase 3.5 `ActingSequence`, re-rendered and checked the same way.

| Check | Phase 3.5 baseline | Phase 3.6 |
|---|---|---|
| Planted-foot travel | **13.38 px jumps**: right foot at 3.17 and 4.42 s, left at 3.33 and 4.21 s | **0.000 px** |
| Parameter velocity jumps (with the resolution test) | 0 (Phase 3.5 fix) | 0 (48 fast-but-smooth samples recognised as smooth) |
| Solver kinks (clamps, balance saturation) | 0 | 0 |
| Joint-limit violations | — | 0 |
| Centre of mass outside the feet | — | 0 px |
| Follow-through residual at the end of each hold | — | 0 (raise, point, shrug) |
| Hair lag (max) / time near its 5° limit | none | 5.0° / 7 % of the time |
| Blazer hem lag (max) | none | 0.74° |
| Hand drawings | — | right: relaxed (pending), open palm (original), pointing (pending); every frame has at least one pending drawing |
| Motion verdict (deterministic checks only) | REVIEW (foot slide) | **PASS** |
| Render | 2.82 s per frame | 2.95 s per frame |

Changes between the two:
- The shrug beat changes from the "ta-da" (open palms out) to `shrug_low` (shoulders up, hands low), because the
  palm-up hands it needs do not exist.
- `shrug_review_preview.png` shows the palm-up shrug with the **pending** cupped hands, labelled REVIEW ONLY.

**My reading (needs a person):**
- **Feet.** The biggest visible fix is in motion: the feet no longer pop sideways when the weight passes over
  (`diagnostics.png`, top panel; frames 37–40 of `before_after.gif`).
- **Timing.** The raise and the point start with a small preparation. The arm settles into each key instead of
  stopping dead, and the head leads the turn and the point.
- **Secondary motion.** The hair trails the head changes and the hem swings slightly after the weight shifts.
- **Still frames.** Most key poses look almost the same as Phase 3.5, because the key poses were kept on purpose so
  the comparison is frame-aligned. The upgrade is about timing and mechanics, not new poses.
- **Shrug.** It no longer reads as a "ta-da". But with the clavicles raised, the blazer shoulders bunch up and look
  puffy (the missing raised-shoulder collar and blazer drawing, Phase 3.3), and the low-hands shrug is understated.
  Whether it reads as a shrug is for a person to judge.
- **Hair.** It reaches its 5° limit in the fastest head changes (7 % of the time). It may be livelier than wanted;
  the gain and damping are editable.

## 17. Classroom interaction demonstration

Files in `natural_pose/performance/classroom/`: `classroom.gif`, `contact_sheet.png`, `diagnostics.png`.

The sequence:
1. stand;
2. shift the weight toward the desk;
3. reach, and put the right hand's fingertips on the desk (a contact, C2 acquire over 0.6 s);
4. lean laterally toward the desk;
5. point at a classmate with the left hand (pending mirrored pointing drawing) while the right hand stays planted;
6. release the desk and return to standing.

The desk is a test prop (plain geometry). The forward lean is **REQUIRES_NEW_VIEW** (`art:torso_lean_forward`) and
is not rendered.

Measured:

| Check | Result |
|---|---|
| Frames | 75 at 12 fps (6.2 s) |
| Contact sliding while fully planted | 0.066 px max (tolerance 1 px) |
| Contact acquisition / release jumps (48 Hz) | none |
| Planted-foot travel | 0.000 px |
| Velocity jumps / solver kinks / joint-limit violations | 0 / 0 / 0 (after the stance change in §7) |
| Centre of mass outside the feet | 0 px |
| Hand drawings | right: relaxed, then palm-down on the desk (pending); left: relaxed, then pointing (pending, a mirrored copy of the right hand) |
| Left point gesture status | NEEDS_APPROVAL (pointing hand pending) |
| Render | 3.11 s per frame |

**My reading (needs a person):**
- **Contact.** The reach and the planted hand read clearly. While the left arm points, the hand stays on the desk,
  and the release blends back without a snap (`diagnostics.png`: a C2 weight ramp, under 0.1 px error, smooth
  wrists).
- **Lean.** It is subtle, because a front view can only lean sideways. A real lean toward the desk would need a
  forward-leaning torso drawing.
- **Hands.** The palm-down hand on the desk and the left pointing hand are pending drawings; the left pointing hand
  is a mirrored copy.
- **Pacing.** The weight shift before the reach is short (a 0.2 s hold, flagged as info).

## 18. Secondary-motion comparison

Files in `natural_pose/performance/secondary/`: `side_by_side.gif` (body only / with secondary / difference in
magenta), `contact_sheet.png`, `diagnostics.png`. The motion: a quick sideways lean, a stop, then a 2.95 s hold.

Measured:

| Check | Result |
|---|---|
| Frames | 49 × 2 at 12 fps |
| Largest difference vs body only | 41,809 px at 0.92 s (the hair region and the blazer skirt edges) |
| Hair lag max / hem lag max | 5.0° / 0.74° (demo gains: hair 1.2, hem 1.6) |
| Lag at the end (after the hold) | 0.0° (settled) |
| Badge, buttons, tie, lapels, collar | pixel-identical with and without secondary motion (render test) |
| Tie / loose sleeves | not driven: NEEDS_ARTWORK / UNSUPPORTED |

**My reading (needs a person):**
- **What moves.** After the stop, the hair sways and the blazer hem swings slightly, then they settle. The magenta
  difference column shows it is confined to the hair and the skirt edges.
- **Size.** The effect is small at full size and barely visible at gif scale.
- **Identity.** It does not change the character's identity: the hairstyle keeps its shape, and rigid details do not
  move.
- **Not driven.** The tie does not swing; it would need the vest behind it. Whether this improves the performance is
  for a person to judge.

## 19. Visual defects and missing artwork

**Visual defects, still present** (from earlier phases, not solved by animation controls):
- the elbow and knee folds are imperfect, and the raised-arm armpit is too full;
- walking and running read as standing (REQUIRES_NEW_VIEW);
- sitting and squatting are a wide crouch (REQUIRES_ALTERNATIVE_ARTWORK; the seat constraint only places the pelvis
  and does not fix the drawing);
- the head tilts but cannot turn or nod;
- the face never changes expression.

**Missing drawings, by priority:**
1. palm-up hands (shrug, question);
2. a vest behind the tie (tie secondary motion);
3. forward-leaning torso / 3/4 torso (leaning toward the desk);
4. turned and pitched heads (head turn, nod, a real head shake);
5. seated thigh, seat and seated trousers;
6. deep-elbow fold;
7. a lean raised-arm armpit.

## 20. Automated tests

| Suite | Result |
|---|---|
| Backend (`pytest`, full) | **546 passed, 2 skipped**: the Phase 3.5 baseline of 517 plus 29 new Phase 3.6 tests |
| Frontend unit (`vitest`) | 11 passed (the editor builds with the new Performance tab) |
| Native Moho tests | **NOT EXECUTED** (Moho is not available in the sandbox) |

**New tests.** `tests/test_p36_performance.py`, 29 tests. They cover:
- **Curves:** curve kinds, Hermite and soft clamp, sequencer boundary continuity, anticipation, overshoot and holds,
  frame-rate and order independence, overlap bounds, anticipation recipes and disable.
- **Contacts:** contact weights, priorities and validation; the desk contact (sliding, transitions, start
  continuity); moving and unreachable targets; the elbow contact.
- **Weight:** planted feet vs the Phase 3.5 baseline slide; no saturation, and steps.
- **Secondary:** determinism and random access; damping, limits, reset, overrides and blocked parts; rigid parts
  unchanged in renders.
- **Layers:** composition, masks, mute and weight; soft limits and facial isolation; contacts surviving gestures.
- **Acting:** styles not changing anatomy; honest gesture statuses; dialogue planning; hand holds; seat diagnostics;
  the frame-aligned upgrade; original-pixel protection.
- **Output and editor:** export metadata (no Moho API names); project ops, undo and migration; the HTTP API
  round-trip.

**Changed tests** (compatibility updates only):
- the project schema pins went from `7` to `8` in `test_hand_replacement.py`, `test_hand_store.py` (2 places),
  `test_p33_garments.py`, `test_p34_correctives.py` and `test_p35_artistic.py`. Phase 3.6 adds schema v8;
- no assertion was relaxed.

**Changed detector.** The continuity check adds the resolution re-measure (§3). Its Phase 3.5 tests are unchanged and
still pass.

## 21. Browser test results

**Passed (1/1)**: the full editor e2e on a scratch copy of the real package, in headless Chromium, including the new
step 6i.

Step 6i loads the acting test, checks that the planted feet do not move (`feet max step 0/0 px`), and then edits the
performance through project ops:
- key 4's curve becomes Bézier;
- the style becomes confident;
- a wave clip and a hand marker are added;
- the SECONDARY_MOTION layer is muted.

It then steps frames, plays and renders a full-artwork frame, undoes the mute, and reloads to check persistence.

The first run's screenshot showed the timeline and curve views clipped in the side panel (they were drawn 590 px
wide). They now scale to the panel width; the e2e was re-run and passed.

Screenshots in `docs/phase3_6/screenshots/`:
- `01_performance_timeline.png`;
- `02_performance_edits_and_frame.png`;
- `03_performance_after_reload.png`.

## 22. Performance measurements

Measured in the cloud sandbox (Intel Xeon 2.1 GHz, 4 cores). **No Apple M1 or real-time figure is claimed.** On the
Mac, run `autorig performance-bench <package>`.

| Path | Time |
|---|---|
| A: full-artwork render (classroom frame, correctives on) | 3.16 s per frame (solve 22 ms; render and gap analysis 3,216 ms) |
| B: interactive pose evaluation (skeleton-only, with a contact free solve) | 20 ms per frame |
| B: layer composition only | 0.1 ms |
| C: timeline preview (skeleton-only track) | 16 ms per frame |
| D: quality analysis (motion checks at 24 Hz) | 9.3 s per performance |
| Secondary simulation (whole timeline, once) / lookup | 0.33 s / < 0.1 ms |
| Director construction (calibration included) | 0.39 s |
| Full evidence run | 1,060 s |

Notes:
- The skeleton path is interactive in the cloud sandbox (a 16 ms preview step). Full artwork is offline, at about
  3 s per frame: real-time artwork playback is **not** claimed.
- Artwork, meshes, corrective assets and layer compositions are cached by the existing engine. Nothing reruns
  segmentation or AI inference during animation.
- **Apple M1:** run `autorig performance-bench <package>` on the Mac. No M1 numbers exist yet.

## 23. Moho export compatibility

`analysis/performance_animation.json` holds:
- per-frame bone rotations relative to rest, and the root shift;
- IK targets, contact events and hand switch keys;
- pose actions, base keys and clips, layer settings;
- secondary settings and values;
- style, semantic events and corrective references.

Every entry is `proposed_unverified`. No Moho API name is used and no exporter was written.

| Item | Class | Native target (product feature) |
|---|---|---|
| skeletal keyframes | REQUIRES_CONVERSION | bone angle keys (rest angle − rotation, radians, CCW) |
| root motion | REQUIRES_CONVERSION | root bone position keys |
| timing curves, overlap | REQUIRES_BAKING | keys on every frame (native interpolation not assumed to match) |
| anticipation / overshoot | REQUIRES_BAKING | keys on every frame |
| gesture key poses | REQUIRES_CONVERSION | Actions (pending-hand gestures stay review-only) |
| hand switch keys | REQUIRES_CONVERSION | switch layer keys |
| IK targets / contacts | REQUIRES_BAKING | baked arm angles (native target bones possible, unverified) |
| contact events | REQUIRES_MANUAL_SETUP | markers / notes |
| weight transfer | REQUIRES_BAKING | keys |
| motion layers | REQUIRES_BAKING | flattened keys (optionally Actions per clip) |
| secondary motion | REQUIRES_BAKING | baked deformation keys |
| garment corrections | REQUIRES_CONVERSION | Smart Bone Actions / switch layers (approved assets only; none yet) |
| dynamic layer order | REQUIRES_MANUAL_SETUP | layer order per pose |
| gesture ids, semantic events | DIRECTLY_EXPORTABLE | metadata |
| acting styles | REQUIRES_BAKING | baked keys |
| facial controls | UNSUPPORTED | no facial artwork yet |

## 24. Live puppeteering preparation

`analysis/live_performance_controls.json` (`LivePerformanceControlSpec`) maps these events to controls with
priorities:
- GESTURE_POINT, GESTURE_SHRUG (as `shrug_low`), GESTURE_WAVE;
- LOOK_AT_TARGET;
- HAND_PLANT, HAND_RELEASE;
- RETURN_TO_IDLE.

Rules:
- **Channels:** body and face channels are separate.
- **Priority:** live input > artist keys > procedural clips > idle, and contacts outrank gestures, so the IK is
  preserved.
- **Secondary motion:** off, or with a short reset, for live use.
- **Neutral restoration:** RETURN_TO_IDLE blends every layer back with C2 ramps.
- **Smoothing:** optional critically damped smoothing.
- **Hand switches:** triggered switches keep the minimum hold.
- **Inference:** no cloud or AI inference during live animation.

There is no webcam tracking, and the Moho mappings are `proposed_unverified`.

## 25. Outstanding artistic approvals

**Nothing is approved:**
- 0 of 59 project approval items;
- every hand-store drawing (relaxed, pointing, palm-down, cupped, fist, gripping, thumbs-up, the mirrored left
  pointing);
- every corrective;
- no performance, gesture or style has a recorded approval.

Previews show pending drawings with a `*` label. The production status of every gesture is false.

## 26. Phase 4 recommendations

Phase 4 was **not** started. Before it:
1. Review in the editor:
   - the upgraded acting test, the classroom test and the secondary demo (the Performance tab);
   - the pending hand drawings.
2. Draw the palm-up hands and the vest behind the tie.
3. Run the Phase 0 native verification in Moho (still NOT EXECUTED).
4. For Phase 4 transfer:
   - bake the performance to per-frame bone keys first (exact at every frame);
   - only then try native target bones and Actions, comparing renders frame by frame.

## 27. Verdict: PARTIAL

**Separate verdicts:**

| Area | Verdict |
|---|---|
| Technical implementation (engine, contacts, curves, layers, secondary, editor, metadata) | complete; deterministic checks pass |
| Artistic quality | **not approved**: no person has reviewed the performances; my reading is in §16–§18 |
| Production readiness | **not ready**: pending hand drawings, missing palm-up hands, tie vest, forward-lean, turned-head and seated artwork |
| Native Moho | **NOT EXECUTED** (export metadata only, `proposed_unverified`) |

**Acceptance criteria:**

| # | Criterion | Status |
|---|---|---|
| 1 | Existing animation systems audited | **Met**: plan doc; a new foot-slide defect found |
| 2 | Advanced animation curves work | **Met**: 8 curve kinds, per channel, Hermite path keys, tests |
| 3 | Anticipation and follow-through implemented | **Met**: per-gesture recipes, C2 bumps, caps, settling inside holds |
| 4 | Body-part offsets stable | **Met**: bounded ±0.25, every group arrives on the key, continuity PASS |
| 5 | Foot contact reliable | **Met**: 0.000 px planted travel (Phase 3.5: 13.4 px jumps) |
| 6 | Hand contact works | **Met**: 0.066 px max sliding on the desk |
| 7 | Contact activation and release smooth | **Met**: C2 blend from the free pose; no transition jumps |
| 8 | Weight transfer without discontinuities | **Met**: no balance saturation (weight 0.83 keeps moving), continuous free-leg softening |
| 9 | Secondary motion deterministic | **Met**: random access = playback = any frame rate (tests) |
| 10 | Hair and clothing secondary controlled | **Met technically**: limits, settling, rigid parts unchanged. The hair touches its limit 7 % of the acting test; a reviewer may want less |
| 11 | Motion layers combine without conflicts | **Met**: joint-space override/additive, masks, contacts after layers, facial isolation (tests) |
| 12 | Acting profiles change timing, not anatomy | **Met**: bones identical in all five styles (test) |
| 13 | Gesture performances reusable | **Met**: 10 gestures + `shrug_low`, clips at any time/side/style, honest statuses |
| 14 | Dialogue acting controls prepared | **Met**: 7 semantic events, idle, listening; no speech or face work |
| 15 | Timeline editing works | **Met**: Performance tab, e2e step 6i (§21) |
| 16 | Saving and reopening preserve animation | **Met**: schema v8 ops, undo, reload (e2e and HTTP test) |
| 17 | Original artwork protected | **Met**: 0 protected-pixel violations; rigid details unchanged |
| 18 | Existing IK, switches and correctives keep working | **Met**: all 517 earlier tests pass; contacts use the same IK; hands use the same switch system |
| 19 | Upgraded acting test shows visible improvement | **Partly**: the measurable fixes (no foot pops, anticipation, settling, head lead, secondary) show in motion; still frames are close to the baseline by design; the shrug is no longer a ta-da but looks bulky at the shoulders. **Needs a person** |
| 20 | Classroom contact test stable | **Met technically**; the forward lean is REQUIRES_NEW_VIEW and not shown |
| 21 | Performance measured | **Met** in the cloud sandbox; no Apple M1 figure |
| 22 | Native Moho export requirements documented | **Met**: metadata and compatibility classes, `proposed_unverified` |
| 23 | Live puppeteering metadata prepared | **Met**: `LivePerformanceControlSpec`; no webcam |
| 24 | Automated and browser tests pass | **Met**: 546 backend, 11 frontend, e2e (§21); native NOT EXECUTED |
| 25 | Artistic approval status reported accurately | **Met**: nothing is approved, and §25 says so |

**Why PARTIAL:**
- No person has approved any performance, gesture or drawing.
- The previews depend on pending hand drawings.
- Several gestures and poses need artwork that does not exist: palm-up hands, a vest behind the tie, a forward-leaning
  torso, turned and pitched heads, seated legs.
- Native Moho was not run.

Smooth, contact-correct motion is not professional acting until a person says so. Phase 4 was **not** started.

## Erratum (Phase 3.7)

Phase 3.7 found a defect in this phase's performance resolver. It wrote hand choices as `{"user": pose, "auto": True}`,
and the switch controller honours `user` only when `auto` is False. Every performance hand request was therefore
replaced by the automatic choice for the base preset (relaxed hands). The pointing hand (acting test), the palm-down
desk hand (classroom) and the "cupped" shrug review preview were **not** shown as this report described. The review
preview actually showed relaxed hands turned about 80° under a "cupped" label.

The fix, the corrected renders and the regenerated evidence in `natural_pose/performance/` are in
`phase3_7_hand_selection_audit.md` and `phase3_7_completion_report.md`. Motion results (feet, contacts, continuity,
secondary motion) are unaffected.
