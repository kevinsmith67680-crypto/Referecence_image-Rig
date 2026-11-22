# Phase 3.5 report: natural posing, artwork-aware feasibility, dynamic layer order and artistic quality control

**Verdict: PARTIAL.** The technical work is implemented and tested on the real schoolboy package, and several static
poses look visibly better: relaxed standing, weight shift, pointing (both sides) and the raised arm. **No person has
reviewed or approved any pose, transition or drawing**, so acceptance criterion 20 ("actual artistic approval is
recorded") is not met and no artistic-quality claim is made. Walk, run, sit, squat and forward lean still do not look
natural. They are classified REQUIRES_NEW_VIEW or REQUIRES_ALTERNATIVE_ARTWORK and rendered only as labelled
fallbacks. The shrug still reads as a "ta-da". Most previews use hand drawings that are still pending review. Moho
was not run. See §24.

All renders come from the real T-pose package (`projects/schoolboy_tpose/character_package`), produced by AutoRig's own
solver and renderer in the cloud sandbox. `autorig artistic-evidence <package>` regenerates them into
`natural_pose/artistic/`, `reports/artistic_quality.json` and `analysis/{view_manifest,limb_configurations}.json`.

Two kinds of statement are kept apart throughout:
- **Geometric correctness** is measured: gaps, leaked pixels, bone lengths, balance, continuity of the motion curves.
- **Artistic quality** (does it look drawn by an animator?) is my reading of the renders, offered as a review aid.
  It is never a measurement or an approval. Every evaluator returns `artistic_review_required: true`.

---

## 1. Phase 3.4 visual audit

The full audit is in `docs/phase3_5_artistic_quality_audit.md`, with renders and measurements in
`docs/phase3_5/audit/`. It was written before any Phase 3.5 code. Findings, as a professional animator would see them:

1. Every hand is the T-pose reference hand, a splayed palm facing the viewer, in every pose.
2. The poses are symmetric mannequins: silhouette symmetry 0.93–0.94, a 50/50 weight split, a level pelvis.
3. The hanging arms merge into the body: hand-over-body ratio 0.80–0.89 in A.
4. Walk and run read as standing, because a front view only moves the legs in the picture plane.
5. Sit and squat are a sumo crouch with both hands over the crotch.
6. The Phase 3.4 elbow and knee caps are smooth discs; the raised-arm armpit is full and round.
7. The head never acts.
8. The hanging sleeve cap looks puffy.
9. The draw order is fixed.
10. Proportions are consistent: no bone is ever scaled. This is a strength to keep.

## 2. Confirmed root causes

Ranked by visible effect (audit §"Root causes"):

| Rank | Cause | Category |
|---|---|---|
| 1 | Only the reference open palm is an approved hand drawing | missing artwork |
| 2 | Presets had no line of action, weight leg, contrapposto, counter-rotation or head counterbalance; arm angles kept hands on the body | poor pose design |
| 3 | Depth motion (stride, knee drive, seated thighs, forward lean) cannot be drawn with front-view art | viewing angle |
| 4 | Generic round caps and full gussets solve gaps, not silhouettes | garment correction |
| 5 | Static draw order | layer order |
| 6 | No head or face acting artwork | missing artwork (out of scope) |

The skeletal solver is **not** a cause. Its output is anatomically coherent; the problem was the intent it was given.
That is why Phase 3.5 changes stage 1 (intent) and leaves the single solver alone.

## 3. Artistic pose planning

`src/autorig/artistic/poses.py`:
- An `ArtisticPose` describes a whole body: action, intention, weight share, contrapposto, line of action, head
  tilt, clavicles, per-arm `Gesture`s with their hand action, stance, free knee, view, exaggeration, depth intent and
  declared artwork needs. It serialises to JSON, which is how the editor stores templates.
- `ArtisticPosePlanner.apply` reshapes the preset's `PoseIntent` in stage 1, setting:
  - the weight leg (pelvis over it, free leg softened and drawn in);
  - pelvis roll (contrapposto);
  - spine and chest curve along the line of action, with the chest countering the pelvis;
  - head counterbalance;
  - clavicles;
  - arm gestures (hang, reach, hip, knee, chin, gait).

  It is passed to the one solver through `MotionEngine.pose(intent_fn=...)`. No bone is scaled, and the solver still
  clamps anything anatomically impossible. A user IK target still applies on top (test
  `test_user_ik_still_applies_on_top_of_an_artistic_intent`).
- The action library covers the 12 actions plus `hand_behind_back` and `hand_across`, each with variants (grounded,
  exaggerated, mirrored, arms freer, bent elbow, phase offsets, a symmetric reference for comparison).
  `apply_constraints` applies user feedback constraints.

Grounded poses are the default. Exaggeration (0..1) scales roll, curve, head and gesture, and is only a candidate
variant.

## 4. Silhouette analysis

`SilhouetteQualityEvaluator` (`artistic/silhouette.py`) works on the solid silhouette of the actual render, separately
from the geometric garment evaluator. It runs 10 checks:
- hands on the contour;
- arm separation;
- arm merged with the body;
- tangencies;
- mannequin symmetry;
- weight-leg clarity;
- head follows the shoulders;
- elbow on the torso contour;
- gesture hand wrong;
- disconnected outline.

Thresholds are in `THRESHOLDS`:

| Threshold | Value |
|---|---|
| hand_contour_min | 0.25 |
| arm_separation_min_px | 6 |
| symmetry_max | 0.90 |
| tangency_max_px | 40 |
| elbow_contour_px | 6 |
| weight_min_shift | 0.08 |

It returns metrics, warnings with severities, a penalty and the silhouette image. It never returns a quality score.

Before (Phase 3.4 preset) → after (Phase 3.5 recommended candidate), number of silhouette warnings:

| Pose | Before | After | Remaining |
|---|---|---|---|
| A relaxed | 3 | 0 | |
| B weight shift | 3 | 0 | |
| C point right | 2 | 0 | |
| D point left | n/a (no Phase 3.4 pose) | 0 | |
| E shrug | 0 | 0 | the warnings miss what is wrong with the shrug (§12) |
| F arm raised | 1 | 0 | |
| G deep elbow | 1 | 0 | |
| H walk | 1 | 0 | |
| I run | 1 | 1 | hand inside silhouette |
| J sit | 0 | 0 | |
| K squat | 0 | 0 | |
| L lean forward | 3 | 3 | hand inside silhouette, unclear weight leg, elbow on torso contour |

Zero warnings means the silhouette rules are satisfied. It does not mean the pose looks good: E, J and K have none and
still look wrong.

## 5. Pose candidate generation

`PoseCandidateGenerator` (`artistic/candidates.py`) renders every variant of an action plus any saved templates. Each
candidate is assessed by feasibility, silhouette and deformation risk, then ranked by an explicit ordering:
1. feasibility class;
2. no high-severity silhouette warning;
3. silhouette penalty;
4. deformation risk.

The ranking is a recommendation with a stated reason; the artist's choice (`artistic.select`) wins. The editor ranks
with the project's corrective mode (approved correctives only, by default) and the evidence with all correctives
previewed, so the recommendation can differ. For pointing right, the editor recommends `default` and the evidence
`bent_elbow`. The evidence has a
candidate sheet per pose, with renders and silhouettes: `natural_pose/artistic/candidates_<letter>_<action>.png`.

## 6. Alternative artwork

`artistic/alternatives.py`:
- **Store.** `AlternativeArtworkStore` is append-only: PNGs in `<package>/artwork/alternatives/<layer>/`, index in
  `analysis/alternative_artwork.json` with hashes and provenance. Each version has its own approval key,
  `alternative:<id>@v<N>`. An import is validated: full canvas, not empty, scale exactly 1, known layer, declared
  views. Code never approves.
- **Metadata per drawing** (`AlternativeArtwork`):
  - the base layer it replaces;
  - supported poses and views;
  - attachment bone, pivot and compatible bones;
  - draw order;
  - scale (must be 1) and orientation;
  - provenance and method;
  - transition (a held switch);
  - the requirement it answers;
  - version and hash.
- **Controller.** `AlternativeArtworkController.select` picks a drawing per pose, with priority user > approved >
  pending (preview only, with `allow_pending`). The drawing is substituted through the existing variant-layer
  mechanism (`MotionEngine.pose(layer_variants=...)`), the same one the hand switches use, so there is no second
  switch engine.
- **Tests.** Import, validation, rejection, pending-only preview and substitution are tested with a synthetic drawing
  (`test_alternative_artwork_store_validates_and_switches`).

**What does not exist:**
- No real alternative drawing exists for the schoolboy. The store is empty in the real package.
- There is no editor import button. Import is through the store API; the `artistic.alternative` project op records
  the user's choice.

## 7. Artwork generation workflow

`PoseAwareArtworkGenerator` (`artistic/alternatives.py`) follows the request's workflow:
1. Detect the missing drawing (from feasibility).
2. Choose the region (`REQUEST_REGIONS`).
3. Crop the reference from the character's own pixels.
4. Make a mask.
5. Render the target pose.
6. Write a request (JSON + PNGs).
7. Call the provider.
8. Store the result as pending.
9. Run the identity check (`identity_check`).
10. Preview it.
11. Have a person review it.
12. Approve it in the editor.

The default provider is `NoProvider`, which calls no model and returns "request a drawing". A generative provider
can be plugged in, but its output would still be stored as pending and could never be approved in code. Only local
drawings (a region of one limb) are requested; there are no full-character illustrations.

Requests were written for the five drawings the evidence needs. Each is in `natural_pose/artistic/artwork_requests/`
with a reference crop, a mask and a target-pose render:

| Request | For pose | Status |
|---|---|---|
| `corrective:elbow_deep_r` | G deep elbow | requested, no drawing |
| `art:thigh_foreshortened` | J sit | requested, no drawing |
| `art:thigh_knee_up` | I run | requested, no drawing |
| `art:torso_lean_forward` | L lean forward | requested, no drawing |
| `corrective:raised_arm_armpit` | F arm raised | requested, no drawing |

No Replicate or other paid inference was run in Phase 3.5.

## 8. Shoulder and joint silhouettes

**Elbow and knee folds.** `ArtDirectedJointCorrection` (`artistic/joint_art.py`) generates procedural `joint_fold`
corrective assets: `elbow_fold_r/l` and `knee_fold_r/l`, each v1 and pending. They supersede the Phase 3.4 caps when
both are active. A fold:
- keeps the sleeve or trouser width;
- mitres the outer corner with a material-specific fillet (blazer wool: 2 fold strokes, fillet 0.85; trouser
  twill: 3 strokes, fillet 0.95);
- adds tapered fold strokes from the inner crease;
- uses angle ramps of 40–90° (elbow) and 15–60° (knee).

Fold strokes are an overlay. They are allowed only over the same limb's own fabric, and the overlay is reported
separately as `fold_overlay_px`. Caps and folds are drawn after all pieces, so a later layer cannot cut them.

**Armpit.** `ShoulderSilhouetteController` (`correctives.py`) makes the armpit gusset v2, pending: the convex bulge is
capped (`max_bulge_convex = 0.06 + 0.12·smooth(u)`) and a crease stroke is added. I tried a "sag" to lean the armpit.
It opened a white crescent, because the stretched patch outline lies outside the gap, so I reverted it. A lean raised
armpit needs a drawing (request `corrective:raised_arm_armpit`). I did not hide the defect with more fill.

**What the close-ups show** (`natural_pose/artistic/joint_closeups.png`: no correction / Phase 3.4 caps / Phase 3.5
folds):
- **Elbow 95° and 120°.** Honestly, the outer silhouette is only a little more angular than the 3.4 cap; what changes
  is the added compression folds. Zoomed in, the first fold version left white notches at the elbow point that the
  caps had covered: background showing between the two sleeve pieces where the clamped corner does not reach. The
  fold now closes notches narrower than about 8 px (`close_notches`, radius 4). That is a morphological closing of the
  limb's own silhouette, limited to empty pixels near the joint. It repairs a notch or crack between the limb's own
  pieces, never bridges a gap between the limb and another part (arm and torso), and never fills a silhouette out
  (test `test_close_notches_repairs_narrow_gaps_only`). In the squat it also closes part of a white crack along the
  sleeve (125 px). **Remaining:** small light specks (up to about 4 px) ringed by outline,
  and small dark outline rings, near the elbow point. They are present in all three rows, so they come from the deformed
  sleeve edges, not from the correction.
- **Squat and sit knees.** Slightly more angular than the caps, with fold lines. The silhouette is still a wide
  rounded knee. A light fringe line at the inner crease is unchanged, and the squat knee fold shows a faint light
  fringe along part of its outer outline.
- **Armpit at 135°.** v2 is only marginally leaner than the 3.4 gusset. It still bulges below the armpit. This is
  **not** fixed.

Protection (geometric, verified):
- `protected_violation_px` is **0** for every A–L pose: no corrective covers an original pixel.
- The identity pose is bit-exact with correctives off.
- `test_artistic_poses_never_change_original_pixels` and `test_joint_folds_supersede_caps_and_never_leak` check this.

A leak found during development is fixed. In the pointing pose, 58 px leaked because the Phase 2 gusset was drawn in
`corrective_mode="off"`. The Phase 2 gussets are now also disabled in "off" mode.

## 9. Dynamic layer ordering

`DynamicLayerOrderController` (`artistic/layer_order.py`) applies rules on the posed skeleton:
- arm crosses in front;
- hand behind the body;
- swing leg forward (gaits);
- artist rules from the project op `artistic.layer_rule`;
- ambiguity detection.

It has a hysteresis margin of 8 px. The neutral order is unchanged (tested). For the hand behind the back, the upper
arm is split by garment part (`upper_arm_r__front` / `__back`, at the sleeve cap and blazer shoulder). The split
partitions the layer exactly (tested), so the shoulder stays in front while the arm passes behind the torso.

`natural_pose/artistic/layer_order_demo.png` compares the static and dynamic order for "hand behind the back". With
the static order the hand lies across the stomach; with the dynamic order the arm disappears behind the torso. The run
pose fires `leg_forward_r`.

**Moho:** layer reordering is not assumed scriptable. The proposal is to bake the order per pose, status
`proposed_unverified` (§19).

## 10. Multi-view feasibility

`ArtworkFeasibilityEngine` (`artistic/feasibility.py`) classifies a pose on the skeleton-only solve, before any
deformation:
- SUPPORTED;
- SUPPORTED_WITH_CORRECTIONS;
- REQUIRES_ALTERNATIVE_ARTWORK;
- REQUIRES_NEW_VIEW;
- UNSUPPORTED.

Pending drawings are listed under `pending_approval`, never counted as approved and never lumped with missing ones.
Depth motion (forward lean, stride, knee drive) is REQUIRES_NEW_VIEW and is rendered only as a labelled fallback.

`ViewAwareRigController.manifest()` writes `analysis/view_manifest.json` for 7 views: FRONT, FRONT_THREE_QUARTER_LEFT,
FRONT_THREE_QUARTER_RIGHT, SIDE_LEFT, SIDE_RIGHT, BACK_THREE_QUARTER and BACK. **Only FRONT exists** (the T-pose
reference). The character sheet in
`references/schoolboy/` is recorded as a source for future drawings, not as an available view.

## 11. Alternative limb configurations

`LIMB_CONFIGS` and `limb_config_doc()` write `analysis/limb_configurations.json`. It describes 6 per-limb
configurations, with the bones they use, the drawing they need and what they must keep (pivot, IK target, contact):

| Configuration | Drawing |
|---|---|
| `arm_straight` | available (original layers) |
| `leg_standing` | available (original layers) |
| `arm_bent_deep` | missing |
| `arm_forward_reach` | missing |
| `leg_seated` | missing |
| `leg_running` | missing | Moho mappings are kept in two
separate lists, and `verified` is empty: every mapping is `proposed_unverified`.

## 12. Natural posing: what looks better, what does not

Contact sheets: `natural_pose/artistic/before_after_1.png` (A–F) and `before_after_2.png` (G–L). Rows: Phase 3.4
render, Phase 3.5 render, both silhouettes, labels (feasibility, pending hands, warnings, "human review: pending").

**Better (my reading; needs a person to confirm):**
- **A relaxed standing.** The arms hang free of the body with relaxed hands; the slight weight shift and head tilt
  break the mannequin symmetry. The relaxed hand drawings are **pending review**, and at this size they read a little
  claw-like.
- **B weight shift.** This is a clear contrapposto: the hip pushes out over the weight leg, the free knee bends, the
  shoulders counter, and the left hand rests on the hip. It is the most "drawn"-looking pose of the set.
- **C and D pointing.** The bent-elbow variant was chosen, and the pointing hand drawing (pending) makes the gesture
  read immediately. The weight goes toward the target. D is new; Phase 3.4 had no left point.
- **F arm raised.** The arm is open and the body counter-leans. The armpit is still too full (§8).
- **G deep elbow.** Hand to chest, readable. The fold above 125° is still missing, so the pose is
  REQUIRES_ALTERNATIVE_ARTWORK.

**Still unnatural:**
- **E shrug.** Almost unchanged: arms out with flat palms to the viewer reads as "ta-da", not a shrug. A palm-up or
  cupped hand drawing is missing, and the shoulder lift is too small to read. Its silhouette check passes; the
  checks do not catch this.
- **H walk.** Reads as standing. Front-view legs cannot show a stride: REQUIRES_NEW_VIEW.
- **I run.** Bent arms with fists in front of the hips read more like a boxer's stance than a sprint. No knee drive is
  possible without knee-up thigh drawings: REQUIRES_NEW_VIEW.
- **J sit and K squat.** Still a wide sumo crouch. The hands now rest on the inner thighs instead of the crotch,
  because the arms cannot reach the splayed knees. Sit needs foreshortened-thigh, seat and seated-trouser drawings
  (REQUIRES_ALTERNATIVE_ARTWORK). The squat is classed SUPPORTED_WITH_CORRECTIONS because nothing it needs is
  missing, but it does not look natural. That is a limit of the classifier, and I am not counting it as a success.
- **L lean forward.** A labelled fallback (REQUIRES_NEW_VIEW). It keeps 3 silhouette warnings.
- **The head and face never act.** The head only tilts and the scowl never changes. Turned-head and expression
  drawings do not exist.

Geometric diagnostics of the after renders (`results.json` → `poses.<letter>.diagnostics`):
- `protected_violation_px` is 0 everywhere.
- Exposed gaps range from 0 to 10, and visible seams from 0 to 25. The highest seam counts are in E (25) and D (9).
- `fold_overlay_px` is up to about 2,480 in sit and squat (fold strokes over the trousers' own fabric).

## 13. Animation transitions

`artistic/transitions.py` builds `ActingSequence` from `KeyPose`s. Each key has a time, a hold, anticipation and
overshoot. The sequence is deterministic; there is no simulation and no cloth solver.
- **Easing.** Quintic ease-in/ease-out (smootherstep): zero velocity and acceleration at both ends of every move.
- **Leads and trails.** Weight, contrapposto and the free knee lead. The line of action and clavicles follow, then
  the upper arms. Forearms, wrists and the head trail behind (`LEAD` table).
- **Anticipation and overshoot.** Smooth C2 bumps (`bump`) added to the eased curve. The overshoot carries past the
  key just before arrival and settles inside the key's hold.
- **Hand switches.** Each happens once per segment, at half the gesture, and is held for the segment, so there is no
  flicker.

**Two defects found and fixed during the acting test** (numbers from `continuity_check`, §16):

1. **The pelvis stopped mid-move.** The first version shifted the weight to 0.8. The static balance hits the pelvis
   shift limit (0.8 × hip separation) at about 0.73, so the pelvis stopped while the weight parameter was still
   easing: 66 balance-saturation events, visible as an acceleration spike at frame 7. The keys now stay below the
   limit (0.72 and 0.71). Nothing visible is lost, because a higher value only saturates.
2. **The arm jumped into motion.** Anticipation and overshoot were plain sines, whose non-zero starting slope made the
   arm start moving with a velocity jump: 25 parameter velocity jumps, and a hand acceleration of 15,000 px/s² at
   the start of the raise. They are now C2 bumps, with 0 jumps. Tests:
   `test_transitions_start_and_arrive_without_velocity_jumps` and
   `test_continuity_check_catches_velocity_jumps_and_solver_kinks` (the second also proves the check catches the old
   sine form).

The weight-side arm also hangs a little wider (15° instead of 9°) in the shift and head keys. In the first version the
left hand overlapped the hip the contrapposto pushes out, which raised `hand_inside_silhouette` on 16 frames; it now
raises none.

## 14. Artistic review interface

The editor has a new **Artistic** tab (`editor/src/components/ArtisticReviewPanel.tsx`, API `/api/artistic*`, project
schema v7 with `artistic.*` ops and migration from v6). It lets the user:
- choose an action and render its candidates (render or silhouette view, with the recommended one marked);
- show the original reference;
- see per candidate:
  - the feasibility class;
  - pending hands;
  - silhouette warnings;
  - fired layer-order rules;
  - approval state;
- **Use**, **Approve** or **Reject** a candidate (`approval.set` on `artistic_pose:<action>/<variant>`), or **Save
  template**;
- type feedback, **Interpret** it into structured constraints shown before applying, then **Apply** or remove them;
- manage templates;
- mark a motion cycle (walk, run, acting test) for revision with a note.

Feedback goes through a deterministic phrase table (`artistic/feedback.py`), not a language model. Examples:

| Feedback | Constraint |
|---|---|
| "Shoulder looks too large" | clavicle −3° |
| "Elbow looks too round" | `elbow_fold_<side>` strength −0.25 |
| "Move the hand farther from the torso" | abduction +5° |
| "Make the running stance less stiff" | contrapposto +0.2, exaggeration +0.2 |

A phrase that matches nothing is stored as a note and changes nothing.

**Not in the panel:**
- the Phase 3.4/3.5 before/after comparison (it is in the evidence sheets);
- alternative-drawing import (store API only);
- hand switches are still edited in the existing Switches and Hands tabs.

The e2e test (step 6h) drives the tab in headless Chromium, on a scratch copy of the package:
- render the pointing candidates (at least 3);
- toggle the silhouettes;
- approve and use one (a scripted click in the test copy, not a review);
- interpret and apply feedback;
- save a template through the API;
- reload and check that the constraint persisted.

Screenshots are in `docs/phase3_5/screenshots/`. Template deletion and cycle revision are covered by the backend tests
only.

## 15. Before-and-after contact sheets

| File | Content |
|---|---|
| `natural_pose/artistic/before_after_1.png` | A–F: Phase 3.4 vs 3.5 renders and silhouettes, labels |
| `natural_pose/artistic/before_after_2.png` | G–L, same layout |
| `natural_pose/artistic/after_<letter>_<action>.png` | full-resolution Phase 3.5 render per pose |
| `natural_pose/artistic/candidates_<letter>_<action>.png` | all candidates per pose (render + silhouette) |
| `natural_pose/artistic/joint_closeups.png` | elbow 95/120, squat/sit knee, armpit 135: none / 3.4 caps / 3.5 folds |
| `natural_pose/artistic/layer_order_demo.png` | static vs dynamic order, hand behind the back |
| `docs/phase3_5/audit/` | the Phase 3.4 baseline renders used by the audit |

Each Phase 3.5 column is labelled with its feasibility class, its pending hands and "human review: pending".

## 16. Acting test

Sequence: stand → shift weight → "turn" the head → raise an arm → point → shrug → relax.
- 7 keys, 6.4 s at 12 fps, 78 frames.
- Every frame is rendered by AutoRig's solver and renderer, with correctives on, pending hands previewed, and the
  dynamic layer order on.
- Files:
  - `natural_pose/artistic/acting_test.gif` and `.webp`, playable;
  - `acting_test_strip.png`;
  - `acting_test/frame_*.png` (every 6th frame at 540 px).

Measured (`results.json` → `acting_test`):

| Check | Result |
|---|---|
| Parameter velocity jumps (1 ms sampling) | **0** (sine version: 25) |
| Solver kinks at 48 Hz: clamp starts/stops, balance saturation | **0** (weight 0.8 version: 66) |
| Hand switches / flicker | r 4, l 2 / **0** (every drawing held ≥ 3 frames) |
| Garment area pops (one-frame change not shared by neighbours) | **0** |
| Garment area change during fast moves (steady, reported) | 3 (upper_arm_r, the raise and the shrug) |
| Area change from a switched drawing (cuff-trimmed forearm) | 6 (at the hand switches) |
| Max bone length change | 0.0001 (bones never scale) |
| Centre of mass outside the feet | 0 px in every frame |
| Continuity verdict (deterministic checks only) | **PASS** |
| Silhouette warnings | 4 frames `unclear_weight_leg`, at mid weight transfer (frames 38–39, 51–52), expected while the weight passes the centre |
| Frames that use pending hand drawings | 64 of 78 (Phase 3.1 feasibility: NEEDS_ARTWORK until the relaxed and pointing hands are approved) |
| Render time | 2.95 s per frame (offline, not real time) |

**Changed in this phase: the continuity check.** The first acting-test evaluator flagged accelerations above 4× the
sequence median. That flags every fast but smooth move, such as the arm raise, and it cannot see a short anticipation
at 12 fps. It is replaced by `continuity_check`, which inspects where a motion can actually kink: velocity jumps in
the parameter curves, and solver clamp or balance-saturation events. The old peaks are still reported, as information
(`accel_peaks`: 121, the fast moves to look at). The new check is not more lenient: it caught both real defects above,
and a test shows it catches the old sine form.

Likewise, the per-frame garment-volume rule (area within 0.85–1.2 of the T-pose drawing) flagged every hanging arm,
because a hanging sleeve legitimately draws about 0.85 of the T-pose area. It now flags only gross changes
(< 0.6 or > 1.4). The sequence check separates pops from steady motion and from switched drawings. All three are
listed in the results; none is hidden.

**How it looks (my reading; a person must review it):**
- The timing reads as deliberate, with holds between beats. The weight shift and arm raise are smooth.
- The point is the clearest beat.
- The shrug beat is weak (§12).
- The "head turn" is only a tilt. A real turn needs turned-head drawings (REQUIRES_NEW_VIEW).
- The face never changes expression, so the acting is body-only.
- At 12 fps the 0.2 s anticipation before the raise is barely visible.

## 17. Automated regression results

| Suite | Result |
|---|---|
| Backend (`pytest`, full) | **517 passed, 2 skipped** (Phase 3.4 baseline: 490 passed, 2 skipped; +27 new Phase 3.5 tests). An earlier full run on this branch was 516 passed before the notch-closing test was added. |
| Frontend unit (`vitest`) | 11 passed |
| e2e (Playwright, headless Chromium, real package) | passed (1/1), including the new step 6h. The first run's screenshot showed the candidate cards overflowing (buttons and long labels spilling into the next card); the cards now wrap, and the re-run passed. |
| Native Moho tests | **NOT EXECUTED** (Moho is not available in the sandbox) |

**New tests.** `tests/test_p35_artistic.py`, 27 tests:
- the planner builds a whole-body contrapposto;
- the pose is less symmetric and the hands read on the silhouette;
- mirror and constraints;
- candidates are assessed and ranked;
- user IK applies on top;
- feasibility classes per action;
- the alternative store validates and switches;
- the layer order keeps neutral unchanged and its sublayers partition;
- hysteresis;
- the view manifest and limb configurations;
- transitions: ease, lead and holds;
- transitions without velocity jumps;
- the continuity check catches jumps and solver kinks;
- the sequence checks (pop, motion, switch);
- frame proportions;
- folds supersede caps and never leak;
- notch closing repairs narrow gaps only;
- original pixels never change;
- the shoulder controller caps the bulge;
- determinism;
- the feedback phrase table;
- project ops and migration;
- the HTTP API.

**Changed existing tests.** Only the schema-version pins changed: `VERSION == 6` became `7`, in
- `test_hand_replacement.py`;
- `test_hand_store.py` (2 places);
- `test_p33_garments.py`;
- `test_p34_correctives.py` (migrate to 7).

Phase 3.5 adds project schema v7. The pin is deliberate, so that schema changes are noticed. No assertion was relaxed
and no threshold of an earlier phase was changed.

## 18. Performance benchmarks

Measured in the cloud sandbox (Intel Xeon 2.1 GHz, 4 cores, Python 3.12). **No Apple M1 figure is claimed.** Medians
of 3, on the pointing pose:

| Mode | Time | Use |
|---|---|---|
| B1 skeleton only | 0.013 s | interactive while dragging |
| B2 interactive preview (approved correctives, no evaluation) | 2.80 s | on release |
| C offline high quality (all correctives, decontamination) | 2.75 s | evidence and export |
| D artistic + geometric evaluation | 3.68 s | on request |
| A full evidence run (12 poses × candidates, close-ups, acting test, benchmark) | 517 s | `artistic-evidence` |
| Acting test | 2.95 s per frame | offline |

Only the skeleton solve (B1) is interactive. Full renders are seconds per frame, so the system is **not** real time.

## 19. Moho compatibility

`moho_matrix()` in `reports/artistic_quality.json` lists the following. Every row is **`proposed_unverified`**; no
row is verified, because Moho was not run.

| Feature | Proposed mapping |
|---|---|
| Artistic poses | keyframed bone angles per pose (actions) |
| Hand switches and alternative drawings | switch layers |
| Limb configurations | alternative bone sets |
| Dynamic draw order | baked per pose; reordering is not assumed scriptable |
| Elbow and knee folds, armpit gusset | baked key drawings in switch layers driven by Smart Bone actions |
| Transitions | keyframes; Moho interpolation is not the AutoRig easing |
| Mesh weights | not assumed to import |
| AutoRig's artwork-selection engine | runs in AutoRig, **not** inside Moho |

No exporter was written and no Moho API name was invented.

## 20. Live puppeteering compatibility

The `live_notes()` design notes cover:
- semantic hand triggers;
- reusable pose templates;
- IK stability (one solver, clamped targets);
- independent channels (body poses never touch the face);
- deterministic selection;
- no generative inference at playback;
- correctives prepared before a performance;
- safe fallbacks for REQUIRES_* poses.

There is **no webcam tracking**, and SHOWTIME mappings stay `proposed_unverified`.

## 21. Remaining unsupported poses

| Pose | Class | Why |
|---|---|---|
| G deep elbow (> 125°) | REQUIRES_ALTERNATIVE_ARTWORK | no deep-elbow sleeve drawing; no approved fist or thinking hand |
| H walk | REQUIRES_NEW_VIEW | the stride needs a side or 3/4 view |
| I run | REQUIRES_NEW_VIEW | knee drive needs knee-up thigh and tucked shin drawings, or a side view |
| J sit | REQUIRES_ALTERNATIVE_ARTWORK | foreshortened thigh, seat, seated trousers |
| L lean forward | REQUIRES_NEW_VIEW | forward-leaning torso, 3/4 torso corrective |
| Head turn | REQUIRES_NEW_VIEW | turned-head drawings |
| K squat | SUPPORTED_WITH_CORRECTIONS on paper, but it does **not** look natural (§12) | needs the same thigh drawings as sit |
| E shrug | SUPPORTED_WITH_CORRECTIONS, but it reads poorly | palm-up or cupped hands |

## 22. Required additional artwork

These are by priority, judged by visible effect.

1. **Approval of the existing pending hand drawings** (relaxed, point, fist, palm-down). This is the largest single
   improvement and needs only review. It unblocks 64 of 78 acting-test frames.
2. **Palm-up or cupped hands**, for the shrug and explaining.
3. **A lean raised-arm armpit** (`corrective:raised_arm_armpit`; request written).
4. **A deep-elbow sleeve fold** (`corrective:elbow_deep_r`; request written).
5. **Foreshortened and knee-up thighs, a seat and seated trousers**, for sit, squat and run (requests written for the
   thighs).
6. **A forward-leaning torso and 3/4 torso** (`art:torso_lean_forward`; request written).
7. **Side or 3/4 views**, for walk and run.
8. **Turned heads and expressions**, for acting.

Then review of every Phase 3.4 and 3.5 corrective (gussets v1 and v2, shoe tops, folds, decontamination).

## 23. Recommendations for Phase 4

Phase 4 was **not** started. Before it:
1. Run the Phase 0 native verification in Moho (still NOT EXECUTED).
2. Have a person review:
   - the 12 poses in the Artistic tab, recording approvals;
   - the acting test;
   - the pending hand and corrective drawings.
3. Commission or draw items 1–4 of §22. A Phase 4 import of poses that need missing drawings would only move today's
   fallbacks into Moho.
4. In Phase 4, verify one mapping at a time (switch layer for a hand, baked draw order, one Smart Bone fold) and move
   each from `proposed_unverified` to verified only with Moho evidence.

## 24. Verdict: PARTIAL

**Acceptance criteria:**

| # | Criterion | Status |
|---|---|---|
| 1 | Phase 3.4 poses artistically audited | **Met**: audit doc and renders |
| 2 | Causes of unnatural movement identified | **Met**: §2 |
| 3 | Planner produces coherent whole-body poses | **Met technically**: weight, contrapposto, counter-rotation, head; visual judgement pending |
| 4 | Silhouette diagnostics functional | **Met**: 10 checks, before/after counts; they miss some problems (E, K) |
| 5 | Multiple candidates can be generated | **Met** |
| 6 | Unsupported poses identified before deformation | **Met**: skeleton-only classification; fallbacks labelled |
| 7 | Alternative artwork can be imported and selected | **Met in code and tests** (synthetic drawing); no real alternative drawing exists; no editor import UI |
| 8 | Dynamic draw ordering works | **Met**: rules, sublayer split, hysteresis, demo |
| 9 | Original artwork protected | **Met**: 0 protected-pixel violations; identity bit-exact |
| 10 | Natural body mechanics improve visibly | **Partly**: A, B, C, D, F visibly better; E, H–L not |
| 11 | Blazer keeps a consistent silhouette | **Partly**: no swelling (area ratios steady, no pops); the raised-arm armpit still bulges |
| 12 | Elbows and knees less artificially rounded | **Partly**: elbows slightly more angular, with folds and closed notches; knees only slightly; 2–3 px specks remain |
| 13 | Hand gestures properly aligned | **Met geometrically** (wrist alignment unchanged from earlier phases); the drawings are pending review |
| 14 | Walking and running anatomically coherent | **Geometrically yes** (one solver, contacts, no bone scaling); they do not read as walking or running in the front view (REQUIRES_NEW_VIEW) |
| 15 | Acting test smooth and coordinated | **Met by the deterministic checks** (0 jumps, 0 kinks, 0 flicker, 0 pops); smoothness *as seen* needs a person |
| 16 | Approved pose variants remain editable | **Met**: templates and selections are project ops; but nothing is approved yet |
| 17 | Editor preserves project changes | **Met**: schema v7, migration, reload in e2e |
| 18 | Existing IK and garment tests pass | **Met**: all 490 earlier tests pass (only schema-version pins changed) |
| 19 | Before/after images show meaningful improvement | **Partly**: yes for A–D and F; no for E and H–L |
| 20 | Actual artistic approval recorded | **Not met**: no person has reviewed anything |

**Summary:**
- **Geometric correctness:**
  - no protected pixel is touched;
  - bones are never scaled;
  - balance holds;
  - the acting test's motion curves are continuous;
  - there is no draw-order or hand flicker.
- **Artistic quality:**
  - better standing and gesture poses (A–D, F);
  - unchanged or still wrong walk, run, sit, squat, lean and shrug;
  - none of it is approved.
- **Missing artwork** (§22) is the main thing holding back the remaining poses. More procedural geometry would not
  fix them.

Phase 3.5 stops here. Phase 4 was not started.
