# Phase 3.8 hand library report: the black-background gesture grid on the new character

Input: the user's "Cartoon Hand Gesture Grid(1).png". It reached this session only as a chat attachment, re-encoded
to JPEG:
- stored as `references/tough_student/hand_grid/hand_grid_black.jpg` (1254×1254, RGB, no alpha band);
- sha256 `47c59800…6b88`.

The background is opaque black (border median RGB 0/0/0, p95 deviation 1). The original PNG bytes were not
available; with them, the expected location is `references/tough_student/hand_grid/`.

No drawing is approved. All 16 are `NEEDS_REVIEW` / `PENDING_REVIEW` in the pack, the store and the metadata.

## 1. Extraction (step 8)

Command (the existing `hand-sheet` tool, now with a dark-sheet mode):

```bash
python -m autorig hand-sheet references/tough_student/hand_grid/hand_grid_black.jpg \
  --inspection references/tough_student/hand_grid/inspection.json --out references/tough_student/hand_grid \
  --library-id tough_student_hands_v1 --zip-name AutoRig_Hand_Library_ToughStudent_v1.zip
```

**Why a new mode.** Every hand has a black outline about 7 px thick. Outside it the sheet has a faint 1 px grey
"sticker" rim (luminance p10–p90 30–89), and the rim has gaps.

- A global "delete black pixels" rule would delete every outline and finger line.
- A plain flood fill of the border-connected black leaks into the outlines through the rim gaps: 7,887 outline
  pixels touched skin at a threshold of 25.

**How the dark-sheet mode works** (`handsheet.background_dark`, `unmatte_dark`):
- The drawing is seeded from its coloured pixels: skin, shading and highlights.
- The outline is the dark band grown geodesically from them. The band never grows through the rim and never grows
  wider than the outline width measured where the rim exists. That width is the colour-to-rim distance: p10/p50/p90
  6.4/7.2/8.0 px, band 8 px.
- Enclosed dark regions the band does not reach would be background gaps. There were none on this sheet.
- Rim pixels inside the drawing take the outline colour (50–137 px per hand). Rim pixels just outside become a 1 px
  outline edge at alpha 128, so no light halo remains.
- The white-sheet path is unchanged: the committed schoolboy pack still rebuilds byte for byte.

Results per drawing (`extraction_report.json`):

| | value |
|---|---|
| drawings found | 16, exactly one per grid cell, none touching the sheet edge |
| outline coverage of the visible edge | 98.8–100 % (r4c3 98.8 %, r1c3 99.5 %, r2c3 99.7 %, the rest 100 %) |
| light (rim) pixels left on the edge | **0** for all 16 |
| skin pixels on the edge without outline (cut-off parts) | **0** for all 16 |
| outline width on the edge (p50 / p95) | 7.1–7.2 / 8.0–8.5 px, i.e. the drawn outline, no dark halo |
| stray pixels removed per sprite | 4–31 |
| scaling | none: tight crops at sheet scale plus a 4 px transparent margin |

**Cross-check.** The 16 outlines are the same drawings as the schoolboy's sleeve-free sheet, recoloured with peach
skin and two-tone cel shading on black. The silhouette IoU per cell against the white-sheet extraction is
0.979–0.989, and the areas agree within ±1.5 %. Two independent extraction methods, one on white and one on black,
agree on the same drawings.

Visual check (`10_hand_library_extraction.png`): each sprite rendered on white, dark grey and a checkerboard at
1–4× zoom. Fingertips are intact and finger lines preserved; there is no light or dark halo.

## 2. Handedness and orientation (step 9)

Two records were written by the assistant after inspecting the sprites; neither is a person's review:
- `inspection.json`: gesture, wrist side, view and thumb per cell;
- `semantic_inspection.json`: handedness, visible surface, palm normal, thumb and rotation tolerance.

Measured checks (`hand-sheet`, `hand-audit`):

| check | result |
|---|---|
| open-palm thumb side (measured) | rows 1–2 = **left** hand group, rows 3–4 = **right** |
| mirror pairs | 7 of 8 mirrored; **pointing is not**: r1c4 and r3c4 are both right hands |
| thumb rule per drawing | r1c4 (in the left group) is drawn as a right hand (flagged) |
| derived drawing | `left_pointing` = horizontal mirror of r1c4, flagged `mirror_x` |
| cupped pair | r2c4 is a right hand (palm up, thumb on the far side) stored in the left group; r4c4 the reverse. `hand-audit` reassigns both to their anatomical sides |
| wrist openings | 10 open cuts (confidence 0.87–1.0); 6 closed outlines (pointing ×2, gripping ×2, cupped ×2), placed by a chord at the side's wrist width, confidence 0.35, **manual pivot review** |

These are the same findings as on the schoolboy sheet. The drawings are the same outlines, so the sheet's labelling
problems carried over.

## 3. Compatibility with the new character (step 10)

`HandCompatibilityReport`: `docs/phase3_8/new_character_evidence/hand_compatibility_report.json` and
`10b_hand_colour_candidates.png`.

| | character's own hands | library on the character |
|---|---|---|
| skin (median RGB) | 253/166/97 (orange-peach) | 253/193/148 (lighter, pinker): approx. RGB distance **58** |
| tone clusters | 2 | 2 (two-tone cel shading on both) |
| outline thickness | 3.0 px | 4.0 px after the per-side scale |
| scale onto the character | — | 0.563 (right), 0.568 (left): one scale per side, fixed (no scaling during animation) |
| hand size | own hand bone 67 px | the open-palm drawing's visible area is matched to the original hand by the scale |

**Candidate adjustments.** These are non-destructive, never imported and never approved:
- `references/tough_student/hand_grid/candidates/*__skin_matched.png`: 16 drawings with only the skin tones
  multiplied by a per-channel gain toward the character's skin;
- outlines, finger lines and alpha are untouched.

A person must look at them. If accepted, they are imported as new versions through the editor, and each new version
is reviewed separately. No outline thinning was applied; the 1 px difference is listed for review.

## 4. Switch groups and selection (step 11)

The 16 drawings are registered in the existing switch groups `hand_r` and `hand_l`. They are store candidates in the
`HandPoseLibrary`, so no second switch engine was added.

Per side, the active slots are:
- relaxed, fist, pointing, thumbs up, gripping, palm down, cupped;
- open palm stays the character's **original hand**. The library's open-palm drawings are candidates the user can
  choose with `handstore.use`; the evidence uses that choice for its open-palm tiles;
- `palm_up` and `partially_closed` are **missing** (NEEDS_ARTWORK).

The Phase 3.7 `NaturalHandSelectionEngine` chooses automatically. In review mode it picks the pending pointing drawing
for a pointing action. In production it keeps the original hand and reports `NEEDS_APPROVAL`
(`14_pointing.png`). It never silently substitutes the relaxed hand: every fallback is reported.

## 5. All 16 drawings on the character (steps 12–13)

Each drawing was forced through the switch system as an explicit user choice (`{"user": pose, "auto": false}`).
Pending drawings are previewed (`allow_pending`), on an arm configuration that makes the gesture read
(`newchar_evidence.GESTURE_ARMS`). Images: `11_left_hand_variants.png`, `12_right_hand_variants.png` (full character
plus 2× wrist close-up) and `13_wrist_alignment.png` (2.2× wrist close-ups).

| side | gesture | drawing (cell) | drawn as | constraint check (explicit choice) | rotation from drawn | wrist check |
|---|---|---|---|---|---|---|
| right | relaxed | right_relaxed (r3c1) | right | OK, palm to camera | −9.8° | PASS |
| right | open palm | right_open_palm (r3c2) | right | OK | 53.2° (tolerance 180) | PASS |
| right | fist | right_fist (r3c3) | right | OK | −26.9° | PASS |
| right | pointing | right_pointing (r3c4) | right | OK | −10.0° | PASS |
| right | thumbs up | right_thumbs_up (r4c1) | right | OK | −5.0° | PASS |
| right | gripping | right_gripping (r4c2) | right | OK, palm up | 47.6° | PASS |
| right | palm down | right_palm_down (r4c3) | right | OK, back to camera | −32.4° | PASS |
| right | cupped | left_cupped (**r2c4**, reassigned) | right | OK, palm up | −10.0° | PASS |
| left | relaxed | left_relaxed (r1c1) | left | OK | 13.6° | PASS |
| left | open palm | left_open_palm (r1c2) | left | OK | −54.4° | PASS |
| left | fist | left_fist (r1c3) | left | OK | 27.0° | PASS |
| left | pointing | left_pointing (**mirror of r1c4**) | left | OK | 10.0° | PASS |
| left | thumbs up | left_thumbs_up (r2c1) | left | OK | 3.0° | PASS |
| left | gripping | left_gripping (r2c2) | left | OK | −47.6° | PASS |
| left | palm down | left_palm_down (r2c3) | left | OK | 33.6° | PASS |
| left | cupped | right_cupped (**r4c4**, reassigned) | left | OK | 10.0° | PASS |

The wrist check covers:
- attached under the cuff;
- centred in the opening;
- no duplicated cuff;
- rotation within tolerance;
- proportion.

**A second check disagrees with the wrist checks above: store alignment** (`HandPoseLibrary.alignment_v`, also shown
in the editor's Hands tab as "Wrist MISMATCH"). It measures each drawing's wrist width against the character's sleeve
opening at rest.
- 7 of the 14 active library drawings are just under the tolerance, at 0.71–0.74× the opening against a minimum of
  0.75×: right relaxed, pointing, thumbs up, gripping, palm down and cupped, and left thumbs up.
- The other 7 are OK.

The cause: the scale for each side matches the open palm's *visible area* to the original hand. This character's white
shirt cuffs are broad, so most drawn wrists come out slightly narrower than the cuff. The hands stay attached and
tucked under the cuff (the wrist checks pass), but a reviewer may read them as a little small. The fix is a person's
choice: a per-variant scale correction in the editor (`handstore.placement` `scale`, fixed per drawing, not animated).
It was not applied in code.

**The first run failed visibly.** A white blob covered the top of every hand. The cause was the Phase 2 extension under
the original hand: a deterministic flat fill that continued the white cuff and its outline. The stored-hand cuff
cover kept it. This is
fixed in `handstore.cuff_cover` (generalisation report, fix #4). The two palm-down failures in the first run came from
the test's arm setup: with the forearm horizontal, the drawing was turned 91° against its 60° tolerance. The engine
rejected it correctly. The arm now hangs toward a desk, as in the Phase 3.7 desk case.

**Visual notes (an assistant's reading, not an approval):**
- The library hands read as one style. They are paler than the character's face and own hands (§3).
- At the character's scale the library outline is about 1 px heavier.
- The mirrored left pointing hand is a mirror image, not a drawing of a left hand.
- The six closed-outline wrists need their pivots checked by a person.

## 6. Status

- Technical: extraction, classification, metadata, import, switch registration, selection, wrist alignment and all
  16 previews work on the new character.
- Not done by anyone:
  - artistic approval of the 16 drawings;
  - a decision on the skin-tone candidates;
  - a manual pivot check of the 6 closed-outline wrists;
  - an anatomically correct palm-up open hand (missing, so `SHRUG_EXPRESSIVE` stays NEEDS_ARTWORK in production);
  - a drawn left pointing hand (only a mirrored copy exists).
