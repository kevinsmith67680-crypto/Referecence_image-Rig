# Phase 3.5 artistic quality audit of the Phase 3.4 character

**Scope.** Phase 3.4 (commit e4128ca, verdict PARTIAL) as it renders today, judged as character animation, not as
geometry.

**Sources I inspected:**
- the Phase 3.4 contact sheets, sweeps, close-ups and sequences (`natural_pose/correctives/`);
- the user's character sheet (`references/schoolboy/character_sheet.jpg`);
- the 16 sleeve-free hand drawings (`hand_store/evidence_v2/library_attached.jpg`);
- the 12 Phase 3.5 test poses, re-rendered with the Phase 3.4 pipeline (correctives previewed, approved hands only),
  with solid silhouettes.

**Evidence.**
- [`docs/phase3_5/audit/baseline_contact_sheet.png`](phase3_5/audit/baseline_contact_sheet.png): renders on top,
  silhouettes below.
- [`audit_annotated.png`](phase3_5/audit/audit_annotated.png): each pose with its defect codes.
- `baseline_measurements.json`.

**Measurements** (`baseline_measurements.json`). These are deterministic checks, used only to back what the images
show:

| Pose | Hands drawn | Hand pixels over the body silhouette (r / l) | Silhouette left/right symmetry (IoU) | Weight left share, pelvis roll |
|---|---|---|---|---|
| A relaxed | open / open | 0.89 / 0.80 | 0.933 | 0.50, 0° |
| B weight shift | — no preset; same as A | 0.89 / 0.80 | 0.933 | 0.50, 0° |
| C point right | open / open (the pointing hand is pending) | 0.00 / 0.91 | 0.57 | 0.60, −3° |
| D point left | — no preset | — | — | — |
| E shrug | open / open | 0.00 / 0.00 | 0.902 | 0.50, 0° |
| F arm raised | open / open | 0.00 / 0.80 | 0.768 | 0.50, 0° |
| G deep elbow | open / open | 1.00 / 0.80 | 0.897 | 0.50, 0° (NEEDS_ARTWORK) |
| H walk | open / open | 0.37 / 1.00 | 0.584 | — |
| I run | open / open | 0.32 / 1.00 | 0.638 | — |
| J sit | open / open | 1.00 / 1.00 | 0.940 | 0.50 (NEEDS_ARTWORK) |
| K squat | open / open | 1.00 / 1.00 | 0.940 | 0.50 |
| L lean forward | open / open | 0.08 / 1.00 | 0.672 | side-lean fallback (NEEDS_ARTWORK) |

## What a professional animator would see

1. **Every hand is the T-pose reference hand.** It is a splayed open palm facing the viewer, in every pose. Hanging
   arms read as "jazz hands", the pointing pose reads as a wave, and the shrug reads as "ta-da". The library already
   holds the drawings that fix this: relaxed, pointing, fist, cupped, palm-down and thumbs-up, for both sides. They are
   pending review, though, so the renders fall back to the reference hand. In the hand-library sheet the relaxed hand
   alone makes the standing figure look natural.
2. **A symmetric mannequin.** Relaxed standing, sitting and squatting have a silhouette symmetry of 0.93–0.94. Weight
   is exactly 50/50, the pelvis is level, the shoulders are level, and both arms carry identical angles. There is no
   line of action, no contrapposto, and no counterbalance between head and shoulders. The character sheet's own
   front pose is subtly asymmetric: one foot turned, arms hanging close.
3. **Arms merge into the body.** The hanging arms get 9° of abduction and 14° of elbow flexion, so the forearms swing
   inward and the hands land in front of the thighs: 80–100% of each hand lies over the body silhouette. In the
   silhouettes of A, B, G, H, I, J, K and L the arms are invisible. There is no negative space, so no gesture can
   read.
4. **Walk and run read as standing.** In the front view the legs only move in the picture plane: the stride and the
   knee drive happen toward the viewer, where the front-view art cannot go. The arm swing was designed for a side view;
   in front it brings both hands across the pelvis. The silhouettes of H and I are nearly those of A.
5. **Sitting and squatting are a sumo crouch with both hands over the crotch.** The legs splay sideways because the
   thighs cannot point at the viewer. The sitting arms take IK targets on the knees, which pulls both hands to the
   middle. This is awkward and inappropriate at once.
6. **Generic garment fills look artificial.** The Phase 3.4 knee and elbow caps are smooth rounded discs with no fold
   ("balloon knees" in J and K), and the raised-arm armpit is full. They close gaps but do not read as fabric.
7. **The head never acts.** It is always frontal with the same scowl; tilts are small and never counter the shoulders.
   The shrug's head does not sink between the shoulders. The facial expressions on the character sheet are not
   segmented.
8. **Shoulders.** On a hanging arm, the sleeve cap rotated down from the T-pose leaves a slightly puffy, rounded
   shoulder line (A, B). The blazer's structured, squared shoulder is softened.
9. **Fixed draw order.** The right arm is always under the left arm, the left leg always over the right leg, and the
   arms always over the torso. Nothing can pass behind the body, and crossing legs cannot swap.
10. **Proportions are consistent.** No bone is ever scaled, and that is a strength to keep. The hands look oversized
    because they are always shown flat to the viewer.

## The 14 audit categories

| # | Category | Where | Cause |
|---|---|---|---|
| 1 | unnatural proportions | hands look oversized (flat palm); puffy shoulder caps | missing alternative artwork (hand drawings pending); sleeve-cap rotation (garment deformation) |
| 2 | poor line of action | every static pose: a vertical spine, level shoulders | poor pose design (no line of action in the presets) |
| 3 | unconvincing weight | A, B, E, K: 50/50, level pelvis | poor pose design |
| 4 | weak gesture silhouettes | A, B, G, H–L: arms merged into the body | poor pose design (arm angles); missing hand art |
| 5 | awkward limb positioning | sit and squat hands on the crotch; walk and run hands meet at the pelvis | poor pose design (IK targets); front-view limitation |
| 6 | incorrect body perspective | walk, run, sit, lean forward | incorrect viewing angle (front-only art for depth motion) |
| 7 | artificially rounded joints | elbow and knee caps; full armpit | inadequate garment correction (Phase 3.4 generic fills) |
| 8 | missing clothing folds | bent elbows and knees, armpit | missing alternative artwork / garment correction |
| 9 | poor shoulder and elbow silhouettes | puffy hanging shoulders; deep elbow | garment deformation (sleeve cap); missing deep-elbow drawing |
| 10 | unnatural knee and hip movement | squat and sit splay sideways | viewing angle plus missing seated-leg art |
| 11 | incorrect depth ordering | latent: fixed arm and leg order, no behind-the-body poses | incorrect draw order (static order) |
| 12 | excessively rigid regions | torso and pelvis move as one block; head never counters | poor pose design (no counter-rotation targets) |
| 13 | poses unsupported by the artwork | G deep elbow, J sit, L lean forward, I knee drive, C pointing hand | missing alternative artwork / view |
| 14 | less recognisable character | flat palms and a sumo crouch are not in the character's design (sheet: relaxed hands, upright sit on a chair) | poor pose design plus missing art |

## Root causes, ranked by visible effect

1. **Hand artwork (R1).** The only approved hand is the reference open palm. The right drawings exist but are
   pending review, and nothing previews them in the default renders.
2. **Pose design (R2, R4, R7).**
   - The static presets are symmetric, and the arm angles put the hands in front of the thighs.
   - The sitting and squatting arms target the knees, which pulls both hands to the middle.
   - The gait arm swing is side-view motion shown in a front view.
   - Nothing models a line of action, a weight-bearing leg, counter-rotation or head counterbalance.
3. **Viewing angle (R3).** Depth motion (stride, knee drive, seated thighs, forward lean) cannot be drawn with
   front-view parts. These poses need alternative drawings or a new view, never a squashed thigh.
4. **Generic garment fills (R5).** Round caps and full gussets solve gaps, not silhouettes.
5. **Static draw order (R6).**
6. **Head acting (R8).** Face and expression artwork is not segmented; that is out of scope here.

**Not a cause:** the skeletal solver. Balance, foot contacts and IK reach behave as specified. The solver executes
stiff intents faithfully, so better intents are the fix, not a second solver.

## Consequences for Phase 3.5 (in order)

1. Plan whole-body poses before solving: weight leg, contrapposto, counter-rotation, head counterbalance, arm
   placement with negative space, and the right hand drawing per gesture. Pending drawings are previewed and labelled,
   never approved.
2. Judge every pose by its solid silhouette (hands on the contour, arm separation, symmetry, tangencies), separately
   from the geometric checks.
3. Generate several candidates per action and let a person choose.
4. Classify each pose before deforming it: SUPPORTED, SUPPORTED_WITH_CORRECTIONS, REQUIRES_ALTERNATIVE_ARTWORK,
   REQUIRES_NEW_VIEW, or UNSUPPORTED. Show the depth poses with what is missing instead of forcing them.
5. Replace the round joint caps with folds that keep the sleeve width, and make the armpit leaner.
6. Make the draw order pose-dependent.
