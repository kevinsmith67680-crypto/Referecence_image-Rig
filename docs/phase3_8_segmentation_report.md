# Phase 3.8 segmentation report: background, anatomy and garments of the new character

The input is the user's "Tough Student in School Uniform(1).png". It reached this session only as a chat attachment,
re-encoded to JPEG:
- stored as `references/tough_student/tpose_front.jpg` (1086×1448, RGB, no alpha band);
- sha256 `4ccb022f…1e1000`;
- immutable copy, chmod 444, plus the byte-identical copy in `projects/tough_student_test/character_project/source/`.

The original PNG bytes were not available. The expected location is `references/tough_student/`; re-running the
commands below on the PNG would replace the JPEG artefacts (compression ringing at the outlines).

Commands, using the normal workflow:

```bash
python -m autorig analyze references/tough_student/tpose_front.jpg --out projects/tough_student_test/character_project \
  --character-id tough_student --complete-outline 5
python -m autorig prepare projects/tough_student_test/character_project --out projects/tough_student_test/character_package \
  --provider flux-fill-dev --candidates 1
```

Cost:
- SAM 3: $0.009 (the re-runs hit the cache);
- FLUX Fill dev: $0.50 + $0.15 (the second run re-inpainted the regions whose masks changed).

The ledger stands at **$1.58 of the $5.00 ceiling**.

## 1. Import (step 2)

Recorded in `docs/phase3_8/new_character_evidence/import_reports.json` (`autorig.newchar.image_import_report`).

| | character | hand sheet |
|---|---|---|
| size / format / mode | 1086×1448 JPEG RGB | 1254×1254 JPEG RGB |
| alpha band / genuine transparency | no / no | no / no |
| background | light, border median RGB 253/251/252, uniform (p95 deviation 2) | dark, border median RGB 0/0/0, uniform (p95 deviation 1) |
| border-connected background | 72.7 % of the image | 73.8 % |
| drawn content bbox (x0,y0,x1,y1) | 9, 19, 1077, 1391 | 60, 52, 1207, 1204 |
| scaling | none | none |
| coordinates | origin top-left, +x right, +y down, source px | same |

The genuine-transparency check reads the alpha values; an "RGBA" mode alone is never taken as transparency (unit
test).

## 2. Background extraction (step 3)

The background-removed character is the neutral composite of the original layers: Phase 1 SAM 3 masks, Phase 2 store.
Pixels are copied, never resampled. It is checked against a **spatial** character mask: everything not connected to
the border through paper-coloured pixels. White clothing enclosed by outlines therefore counts as character, whatever
its colour (`autorig.newchar.background_extraction_qa`).

| | default Phase 1 (first run) | with this phase's fixes |
|---|---|---|
| character pixels missing from every layer | 5,434 (mostly 1–4 px of outline under the soles and the hand bottoms) | **14** |
| paper kept as character (white wedges between hair strands) | 452 | **0** |
| dark outline cut off within 3 px of the silhouette | 1,468 | **0** |
| light fringe on the silhouette, original layers | 697 (11 %) | 3,018 (46 %): the silhouette now includes the antialiased outer pixels |
| light fringe after edge decontamination (derived copy, Phase 3.4) | — | **4 px (0.06 %)** |
| enclosed white regions (shirt, trainer bodies, laces, cuffs) covered by layers | 12/12 | 12/12 |

How it was fixed (details in the generalisation report, #1):
- the paper is border-connected paper;
- the 2 px fringe is measured against that spatial mask instead of an opened colour mask;
- the opt-in `--complete-outline 5` returns 11,233 px of silhouette outline that the masks had clipped.

The schoolboy's committed package, measured the same way: 3,303 missing, 445 paper kept, 759 clipped, 137 px fringe
after decontamination. Its Phase 1 project is a committed snapshot and was not re-run.

Visual check (`02_background_removed.png`): the composite on white, black, mid-grey and a checkerboard, plus 3×
close-ups of the hair tips, a hand and a trainer.
- The original layers show a 1 px light ring on black.
- The decontaminated layers show none.
- Hair tips, the outline, the crest, the chain, the laces and the trainer outlines are intact.

## 3. Anatomy and parts (step 4)

Phase 1, automatic; no manual landmarks.

| group | result |
|---|---|
| body parts | all 17 populated. Pixels: hair 35,658; head 35,711; neck 4,060; torso 56,945; pelvis 26,733; upper arms 20,755 / 20,746; forearms 11,116 / 11,078; hands 11,485 / 11,428; thighs 36,588 / 36,010; shins 36,441 / 35,773; feet 19,304 / 19,396. Unassigned 0, overlaps 0 |
| face (SAM 3 face pass) | eyes, pupils, brows, nose, mouth, **both ears**: all found |
| fidelity | coverage 0.99997, linework retained 0.9998, max error inside layers 0 |
| warnings (same as on the schoolboy) | no `upper_clothing`; three "arm" instances (two largest kept); both "sleeve" masks ignored (they cover more than 40 % of the shirt) |
| joints flagged "verify" | pelvis, torso, shoulders, elbows, right wrist, hips, knees: straight T-pose limbs give no bend to locate the elbow or knee, and the hips are under the trousers. All were placed plausibly (`05_skeleton_overlay.png`) |

Garment structure (Phase 3.3 `GarmentStructureAnalyzer`, derived from this artwork): 41 parts.

| this character's feature | garment part | class | note |
|---|---|---|---|
| blazer body, hem, shoulders | blazer_body, blazer_hem, blazer_shoulder_r/l | LIMITED | |
| sleeves | sleeve_cap / upper / fore, elbow_r/l | FLEXIBLE | |
| lapels and trim | lapels (partly), shirt_collar | LIMITED | the **light trim** is classified "white", so some of it reads as shirt collar |
| open white shirt | sweater (schoolboy vocabulary) | LIMITED | |
| **gold chain** | tie (schoolboy vocabulary) | RIGID on the chest | right behaviour, wrong label |
| **crest** | **badge** | RIGID on the chest | after the outline-emblem rule; before it was "button_1", attached to the spine |
| gold buttons | button_1..4 | RIGID | |
| pocket flaps | — | — | not found (trim colour); they deform with the blazer hem |
| shirt cuffs | shirt_cuff_r/l | RIGID | |
| trousers | trouser_hip / thigh / knee / shin / cuff, waistband | LIMITED / FLEXIBLE | |
| trainers | shoe_r/l | RIGID | |

## 4. Detail preservation (step 5)

Only hidden regions were reconstructed. The visible artwork is untouched: the hair, brows, face, trim, crest, chain,
trousers and trainers are all original pixels.
- Phase 2: neutral exact, **0 generated pixels visible in neutral**.
- 25 extension layers sit beneath their originals, each with a provenance mask.
- Every generated pixel is pending review.

Regions:
- flat fill: brows, eye whites, nose, mouth, forearm under hand, upper arm under forearm;
- generative (FLUX): eye white and skin under the irises, head under the hair, neck under the head, feet under the
  trouser hems;
- not needed for the planned motion: torso under the hair and under the head.

Uncertainties a reviewer should look at:
1. `forearm_*__under__hand_*` (flat fill): the white cuff continued, with an outline, under the original hand. Library
   hands no longer show it (`cuff_cover`). The original hand shows its outline when the wrist bends.
2. **The eye white under the iris is wrong.** FLUX inpainted it (the schoolboy's was a flat fill) and redrew iris and
   pupil: the median colour is RGB 16/10/8 and 19/13/11, with 99.7 % and 93.9 % of the pixels dark. This is the
   known run-1 defect; the generic inpainting prompt is still open. The pixels are pending: reject them, or replace
   them with a deterministic white sclera fill, before any eye-direction control.
3. Forearm layers hold 199 + 53 px of the hand's outline beyond the cuff (Phase 1 arm/hand split; generalisation
   report §3.5).
4. Trouser-hem outline pixels at the trainer tops belong to the shin layers. They separate as small marks when the
   stance narrows (§3.6).
5. Light trim vs white shirt: colour-based garment labelling cannot separate them on this character.

## 5. Verdict for segmentation

Usable. All parts, face features and joints were found automatically. The background extraction is clean once the
spatial paper rule and the opt-in outline completion are used. The garment structure is derived from this artwork
with schoolboy-specific labels; this is documented, not hidden. Nothing is reviewed by a person.
