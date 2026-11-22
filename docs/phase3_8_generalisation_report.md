# Phase 3.8 generalisation report: what AutoRig assumed about the schoolboy

Phase 3.8 ran the whole workflow on a second character, `tough_student_test`, built from the user's T-pose reference.
Every stage below was run on it with the normal commands. When a stage behaved differently from the schoolboy, the
cause was traced in the code.

This report lists the character-specific assumptions that were found, split into three groups:
- what was fixed in this phase (small, reproducible, with a test);
- what is documented as an architectural issue;
- what turned out to be general.

Each fix was checked against the schoolboy, so that its committed data and tests did not change.

## 1. Where the two characters differ

| | schoolboy (`schoolboy_tpose`) | new character (`tough_student_test`) |
|---|---|---|
| canvas | 1086×1448 JPEG, near-white paper | 1086×1448 JPEG, near-white paper (same size) |
| hair | dark curls | blond and red swept quiff, pointed tips with paper gaps between strands |
| skin | brown | orange-peach |
| jacket | navy blazer (lum ~ 45), **beige** trim, tie, V-neck sweater, pocket flaps | darker navy blazer (lum ~ 33), **light grey/white** trim, open collar, **gold chain**, crest with a gold **outline** |
| shoes | **black** shoes, white soles | **white** trainers, white laces |
| legs | stance ~19° per leg | stance ~15° per leg |
| arms | T-pose, abduction ~87° | T-pose, abduction ~86° |
| hand library | sleeve-free sheet on **white** | the same 16 outlines (IoU 0.98–0.99), recoloured peach with cel shading, on **black** |

## 2. Assumptions found and fixed in this phase

| # | Where | Assumption | What happened on the new character | Fix | Schoolboy |
|---|---|---|---|---|---|
| 1 | `phase1/compositor.estimate_foreground` + `absorb_fringe` | The foreground is a colour threshold followed by a 1 px opening. | The opening dropped the 1–2 px strip of outline that a SAM mask clips, so the 2 px fringe never reached it: **5,434 px** missing, mostly outline under the trainer soles and hand bottoms. Paper inside the hair gaps was kept as character (**452 px**). | The background is now the paper-coloured region *connected to the border* (`border_paper`). Enclosed white clothing stays character. Paper is removed from the layers (`remove_border_paper`). The 2 px fringe is measured against this spatial mask. An opt-in `analyze --complete-outline PX` returns deeper clipped silhouette outline to the nearest layer; it adds 11,233 px on this character and is recorded in the pipeline steps. It is off by default because a uniformly eroded segmentation must still be reported (existing test). | Phase 1 is not re-run for committed projects. Phase 1 tests pass unchanged. |
| 2 | `handsheet.background` | Hand sheets have a white background. | A black sheet was unusable. A global "delete dark pixels" rule would delete every outline. | Dark-sheet mode (`sheet_polarity`, `background_dark`, `unmatte_dark`). The drawing is its coloured pixels plus the dark outline band grown from them. The band stops at the sheet's grey rim and never grows wider than the outline width measured where the rim exists (p90, 8 px). Rim pixels become the outline colour; rim pixels outside the outline become a 1 px half-alpha edge. | The white path is byte-identical: the committed v2 pack still rebuilds bit for bit (test). |
| 3 | `handsheet.build_pack` manifest | `intended_use` named the schoolboy. | The new library said it was for the schoolboy. | `intended_use` and `artwork_source` come from the inspection record; the old text is the default. | Unchanged bytes. |
| 4 | `handstore.cuff_cover` | Generated pixels under the original hand look like skin. | Phase 2's **deterministic flat fill** extended the forearm under the original hand with the cuff's white and an outline along the expected shape: a **white rounded cuff end** about 25 px long. The schoolboy's region had been a skin-coloured FLUX fill. The white classifier counted it as cuff, so it was kept, and it covered the top of **every library hand** (a "duplicated cuff"). | Generated pixels (where the layer differs from the source) never set the cuff edge. The generated pixels beyond the measured cuff edge are removed from the store-hand cover. | `forearm_*__hscuff` is pixel-identical (checked). |
| 5 | `garmentrig.correctives.ShoeTopRecovery` | Shoes are dark: the colour was sampled only from pixels with luminance 25–160, with a dark-grey fallback. | The extension under the hem of the **white trainers** was drawn dark grey (median RGB 54/50/48). | The shoe's dominant tone is used, decided once per shoe: light pixels when they outnumber the mid-tone ones under the hem. The shoe-top assets were regenerated as new pending versions. v2 decided per pixel column and kept dark streaks where outline and lace columns won; v3 decides per shoe (median RGB about 208/203/200). | Dark shoes keep the old rule: the schoolboy's committed shoe tops regenerate pixel for pixel (test). A per-column rule had changed 2 px; no new schoolboy versions were created. |
| 6 | `garmentrig.structure._decor` | A badge is a solid gold area of 600 px or more. | The crest is a gold *outline* (453 px) around a red field, so it was classified as a **button**. That attached it to the spine bone, not the chest. | Outline-emblem rule: a non-central decoration of at least 200 px with an extent of at least 900 px² and an aspect ratio of 0.5–2 becomes the badge, filled to its convex hull. | Decorations identical (test pins all six). |
| 7 | `motion/demo.ik_demo` | The Phase 2 sheet fist `hand_r__fist` exists. | `KeyError: 'hand_r__fist'`. | The active fist drawing's own layer is used; store drawings report the suggested translation. | This also removes a latent mix-up: the old code measured the store fist and moved the archived sheet fist. |
| 8 | `refmotion/evidence.roundtrip` | The round trip is the schoolboy's. | The new character's round trip would have been stored as, and attributed to, `schoolboy_roundtrip`, and its sheet was titled "on the rendered schoolboy". | The name and the title come from the Phase 1 character id (`tough_student_roundtrip`). Projects without an id keep the old name. | Unchanged name and title. |
| 9 | `artistic/alternatives.ViewAwareRigController` | The 3/4, side and back drawings are in `references/schoolboy/character_sheet.jpg`. | The new character's view manifest cited the schoolboy's sheet. | Only the character's own `references/<id>/character_sheet.jpg` is cited. The new character has none, so no source is claimed. | Unchanged. |
| 10 | CLI help | — | Budget help still said `$1.00`. The hand-sheet help said "white background". | Text corrected. | — |

## 3. Architectural issues, documented and not fixed

1. **Fixed garment palette.** `motion/garments.colour_classes` hard-codes the schoolboy's colours:
   - navy: b − r ≥ 3;
   - beige trim: r − b in 15..80;
   - white;
   - gold;
   - warm dark trousers;
   - outline: luminance < 38.

   On the new character:
   - **the light trim is classified as "white"**, so no pocket flaps are found and parts of the lapel trim count as shirt collar;
   - the darker blazer (luminance ≈ 33) falls *also* under the outline threshold, which is harmless because the garment code reads "navy", not "outline".

   A general fix is a palette measured from the character's own layers: the sleeve's dominant fabric colour, light lines inside the jacket region, the trouser colour. That touches every Phase 3.1–3.4 consumer, and it would change the committed schoolboy evidence, so it is listed as Phase 3.9 work.
2. **Fixed uniform vocabulary.** *(Phase 3.8.1: the chain and the shirt now carry measured labels, necklace and
   shirt front; the slot names are unchanged.)* The garment parts are named for the schoolboy: `tie`, `badge`, `sweater`, `pocket_flap`, `button`. On the new character:
   - the gold chain becomes the rigid `tie` slot (rigid on the chest, which is the right behaviour, with the wrong name);
   - the open white shirt becomes `sweater`;
   - pocket flaps are not found.

   Nothing breaks, but the labels in reports and the editor are misleading. A general garment-part ontology (emblem, necklace, opening, flap) is needed.
3. **Fixed pixel sizes.** Many thresholds are absolute pixels tuned at the schoolboy's scale:
   - Phase 1 fringe: 2 px;
   - decoration areas: 20, 40, 200, 600 and 900 px;
   - hand sheet: `MIN_HAND_PX`, `HOLE_MIN_PX`;
   - shoulder-check windows: ±40, 35, 25 and 120 px;
   - `TUCK_PX`, `max_patch_stretch`.

   The new character has the same canvas size and almost the same figure height, so none of these was exercised. A reference at another resolution would need them expressed relative to the figure (for example head height or arm length).
4. **Phase 2 fills the hidden wrist with the forearm's visible colour.** Under the original hand, the flat fill continued the white cuff and its outline: `forearm_*__under__hand_*` is a deterministic flat fill of 1,040 / 1,255 px. On the schoolboy the same region was a skin-coloured FLUX fill. A wrist under a hand should be skin, which needs a semantic rule (the region a hand covers is skin) or a region-specific prompt; the generative prompt is also still generic (known open item). Fix #4 removes the extension from under library hands. It is pending review, and a reviewer can reject it for the original hand.
5. **Phase 1 arm/hand split.** The forearm layer holds **199 dark pixels of the hand's own outline** beyond the cuff. 53 more came from the opt-in outline completion's nearest-layer rule. With the original hand, they show as a dotted arc over the palm when the wrist bends (production pointing, `14_pointing.png`). Library hands do not show them, because `cuff_cover` removes them. The fix today is the editor's mask tool (move them to `hand_r`). A general fix would move forearm pixels beyond the cuff that connect to the hand outline into the hand layer.
6. **Trouser cuffs over white trainers.** The inner corners of the trouser hems (shin layer) separate as small outline marks beside the shoes when the stance narrows. The garment evaluator grades the shoes FAIL (schoolboy: WARNING). The cause is segmentation: hem-outline pixels at the shoe boundary, owned by the shin layer. The shoe-top extension does not cover them. The fix is a mask edit, or a hem-boundary rule.
7. **Pelvis height when the soft knee and the knee limit conflict.** *(Fixed in Phase 3.8.1: the knee limits win and
   the static balance stops where the legs can hold the pose; see `phase3_8_1_fix_report.md` §3.)* The Phase 3.6 acting test grades **REVIEW** on
   the new character (schoolboy: PASS). Every review flag is `knee_l_flexion` outside its standing limit:
   13.2° at the start, up to 17.9° during the weight shift, against a 10° stance limit. These are 65 distinct
   readings, and 248 of 308 frames are affected. Foot sliding (0.01 px), velocity jumps (0) and solver events (0)
   are clean.
   - The weight inputs are identical on both characters (same weight shares, same knee minimums). The rest knees
     are straight on both (−0.2° / 0.2°). The cause is geometric.
   - Static balance puts the centre of mass over the weighted support. At weight 0.73 that moves this character's
     pelvis 94 px sideways, 18.6 % of the leg length. On the schoolboy it is 77 px, 14.1 %.
   - The free leg then reaches more obliquely. The solver sets the pelvis at the highest height where every planted
     leg reaches its locked ankle with its soft-knee minimum (free leg: 14°). It does not lower that height for the
     loaded leg's 10° maximum: with `hi > lo`, `np.clip(want, hi, max(lo, hi))` returns `hi`.
   - So the loaded left leg bends past its limit. Counted with an instrumented solve, the two constraints conflict
     by up to 4.2 px of pelvis height in 1,421 of the acting test's solver calls (several per frame). On the
     schoolboy they conflict by at most 0.26 px, in 40 calls, which stays inside the check's 0.5° tolerance.

   A general fix gives the loaded leg's anatomical limit priority over the free leg's cosmetic softening when the two
   cannot both hold: take the pelvis height from `lo`, not lower than the straight-leg reach. It was **not applied in
   this phase**. It changes the one solver's output for the schoolboy too: 40 acting solver calls move by up to 0.26 px
   and the committed Phase 3.6 evidence would change. The visible effect on the new character is a slightly bent
   loaded knee in the front view. The report flags it; it is not hidden.
8. **Front-view check for reference clips.** `refmotion/importer.QUALITY["three_quarter_ratio"]` is a fixed 0.65: the
   detected shoulder width over the shoulder-to-hip distance, based on adult human proportions (0.75–0.9 facing the
   camera). The MediaPipe round trip on this character's front-view render measures 0.641, so it is classed
   three-quarter and `usable: false` (schoolboy: 0.675, usable). Human clips are judged by human proportions, so this
   affects cartoon round trips only. A threshold relative to the character's own rest proportions would remove it.

## 4. What generalised without change

- **The SAM 3 vocabulary and the anatomy estimator.** All 17 body parts and the face features (eyes, pupils, brows, nose, mouth, both ears) were found automatically, and every joint was placed without manual landmarks. The same four warnings appeared as on the schoolboy:
  - `upper_clothing` not found;
  - three "arm" instances;
  - both "sleeve" masks ignored.
- **Phase 2 reconstruction**: neutral exact, 0 generated pixels visible in neutral.
- **The skeleton.** The shoulder pivots are lifted 29–30 px toward the armhole top by the garment rig. The IK keeps bone lengths: the retargeted pointing frame changes every bone length by less than 0.01 px (see the reference section of the main report).
- **The garment rig.** It builds 41 parts with valid weight meshes; the crest becomes a RIGID badge once fix #6 is in.
- **Hand metadata, orientation, constraint-first selection, wrist alignment and switch groups.** All 16 drawings sit on their anatomical sides, and the cupped pair is reassigned exactly as on the schoolboy. All 16 pass the explicit-choice constraint check, and all 16 wrist checks PASS.
- **The performance director, reference retargeting, the editor project (schema v9), persistence and export.**

## 5. Measured comparison under identical tests

| test | schoolboy | new character |
|---|---|---|
| Phase 1 coverage / linework retained | 0.992 / 0.939 (run 2) | 0.98734 / 0.928 default; **0.99997 / 0.9998** with `--complete-outline 5` |
| background-removed: missing / paper kept / clipped outline (px) | 3,303 / 445 / 759 | **14 / 0 / 0** (after fix #1 and the opt-in) |
| light fringe after edge decontamination | 137 px (2.0 % of the silhouette) | 4 px (0.06 %) |
| relaxed standing with correctives on: exposed gap points | 2 | 24 (trouser cuff marks, §3.6) |
| arm-lowering test, legs at the drawn stance, resting arms: gaps / seams | see `arms` in results.json | see the rigging quality report |
| hand drawings on the correct anatomical side | 16/16 | 16/16 |

The garment-status comparison (12 garment tests) is in `phase3_8_rigging_quality_report.md`.
