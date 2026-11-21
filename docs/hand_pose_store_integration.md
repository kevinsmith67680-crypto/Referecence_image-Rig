# Hand Pose Store integration: schoolboy hand pack

**Verdict: PARTIAL.** The pack is imported, validated and stored. Every drawing is selectable for both wrists in the
existing switch system, attached at the character's cuff, sized like the original hand, masked so no second cuff
appears, and previewed under IK without moving the skeleton. Choices, approvals and corrections persist with
undo/redo.

It is not PASS because:
* nobody has reviewed the drawings: all 16 are pending, so none is used in final output yet;
* six drawings (thumbs-up, grip and cupped, both sides) have a thumb or fingers next to the cuff that is wider than
  the sleeve opening, and their visible wrist band shifts in posed renders;
* there is a small cut-edge artefact at strong wrist bends;
* Moho has not run, so no Switch Layer exists natively.

All numbers were measured in the cloud sandbox on `projects/schoolboy_tpose/character_package`.

## 1. What was imported

* **Pack:** `references/schoolboy/hand_pack/AutoRig_Hand_Pose_Library.zip`.
  * Zip SHA-256 `65b9ed59…cec7cb1`; library `schoolboy_hand_switches_v1`, manifest schema 1.0.0.
  * 16 RGBA 400×320 sprites: eight poses (relaxed, open_palm, fist, point, thumbs_up, grip, palm_down, cupped) ×
    left/right.
  * Provenance: "Generated visual concepts; not extracted from original reference artwork". Every asset is
    `NEEDS_REVIEW`.
* **Validation:** 0 errors, 0 warnings.
* **What the validator checks** (all problems are collected, each naming its asset):
  * a manifest exists, its keys are present, `asset_count` matches;
  * IDs are unique and safe; sides and poses are known;
  * every listed pose exists for every listed side;
  * every file is in the zip, opens as PNG, has an alpha channel, a transparent background and visible pixels;
  * every file matches its declared `canvas_px`; the anchor is inside the canvas;
  * zip paths are safe (no `..`, absolute paths or symlinks).
* **Pose mapping:** `point` → `pointing`, `grip` → `gripping`. `cupped` is kept as its own pose. It was **not**
  mapped to the requested "partially closed" pose, which therefore stays NEEDS_ARTWORK.

## 2. Store and persistence

The store is `<package>/hand_store/`. It is character-scoped and append-only:
* `index.json`: packs (zip hash, file hashes, provenance, import time) and assets (side, pose, versions with hashes).
* `packs/<library>/v<N>/`: the pack, byte for byte.
* `uploads/…/v<N>.png`: replacement PNGs.

Behaviour:
* Re-importing the same zip is a no-op. A changed pack becomes a new pack version; a replaced PNG becomes a new
  asset version, and the old ones stay.
* `HandStore.verify()` rechecks every hash. Tampering is detected (tested).
* Nothing lives only in the browser.

The user's decisions live in the editor project, so they get undo/redo and survive a reload or a server restart:
* **Schema v3** adds the `hand_store` section. v1 and v2 projects migrate with their operations unchanged (tested).
* **Operations:**
  * `handstore.use`: which candidate a side/pose uses;
  * `handstore.placement`: dx, dy, rotation and a uniform scale multiplier;
  * `handstore.mask`: on/off, cut-line offset, halo removal;
  * `handstore.rename`;
  * `handstore.delete` / `handstore.restore`;
  * `handstore.version`.
* **Approvals** use the existing `approval.set` with the item `handstore:<library>/<asset>@v<N>`, so each version is
  reviewed separately.

## 3. One switch system, more candidates

There is no second switch engine. The Phase 3.2 `HandPoseLibrary` now keeps several candidates per side and pose:
* `original`: the reference hand, open palm only;
* `phase2:<drawing>`: the earlier sheet-derived drawings;
* `store:<library>/<asset>`: the imported drawings.

One candidate is active per side/pose: the user's choice, else an approved one, else the original, else the Phase 2
drawing, else the stored one. The groups `hand_r` and `hand_l` keep their IDs (the project, IK and live-action state
already use them). They carry the aliases `hand_right` and `hand_left` that the request names, and the switch
manifest uses those aliases.

**Resolution is unchanged:** user choice > keyframe > automatic preset choice > the original drawing. Pending
drawings show only with "preview pending"; rejected or missing ones fall back with a note. Tested:
* the original hand stays the open-palm default;
* the pending relaxed drawing is not shown in normal mode and is shown in preview mode (NEEDS_ARTWORK);
* once the relaxed drawing is approved, relaxed standing uses it automatically;
* an explicit user choice (fist) still wins over that automatic choice.

**Gestures** (presets keep their Phase 3.2 hand preferences):
* relaxed standing → relaxed;
* pointing → point;
* running → fist, then relaxed;
* shrug → open palm;
* grasping → grip, through a new semantic action `GESTURE_GRASP` (no grasping pose preset exists).

New semantic actions `HAND_*_PALM_DOWN` and `HAND_*_CUPPED` were added. Actions stay semantic: no Moho names.

## 4. Wrist anchor matching

The manifest's wrist anchor `(200, 92)` is not used. Compared with each drawing's measured cuff edge, it is off by
**9.5–43.5 px in x and up to 10 px in y**.

How a drawing is placed:
* **The drawing's wrist:** the centre of its own cuff's bottom edge, measured from its pixels.
* **The character's wrist:** the hand bone of the approved skeleton gives the wrist joint and axis. The character's
  white shirt cuff (original pixels inside the forearm layer) gives the opening centre, width (63.7 px right,
  62.9 px left) and edge.
* **Placement:** the drawing's cuff edge goes onto the character's cuff edge, tucked 6 px under the sleeve, and its
  "down" axis is rotated onto the hand bone.
* **Scale:** one per side (0.720 right, 0.729 left), so that the stored open palm shows the same visible area as the
  original hand (1.02× measured). Scaling by cuff width alone would have shrunk the hands to about 0.55× (their
  sleeves are drawn much wider than the character's); that was tried and rejected as a size pop.
* **No mirroring:** left and right drawings are used as drawn, never assumed to be mirrors. Every drawing lands on
  the correct side (tested).

The user can then correct each drawing with dx, dy, rotation and a uniform scale (0.5–2×) about the wrist point. The
optional Auto-align search is a button, not automatic. For drawings unlike the reference shape it moves the hand off
the wrist, so it is never applied automatically.

An Auto-align attempt that matched the visible hand band to the reference hand was tried first and dropped: on grip,
cupped and thumbs-up it moved the drawing up to 8 px off the wrist and still measured 63–75 px "error" (it compares
hand shapes, not attachment).

## 5. Sleeve and cuff: no second cuff

Every sprite carries its own navy sleeve and white cuff. The derived layer masks them non-destructively; the source
file is never changed.
* **The mask:** for each column of the sprite, it removes everything down to the cuff's bottom edge and through its
  outline. The cut line is interpolated across the sleeve's side outlines.
* **A raised thumb is kept** (thumbs-up): skin connected to the hand below the cut stays.
* **Results:** 0 skin pixels removed in all 16 drawings; 7,671–10,573 px masked per drawing.
* **Halo:** the sprites also carry a grey matte halo from background removal (90 % of every outer 2 px ring is grey).
  It is removed too (1,120–1,752 px per drawing) and reported at import.

The character's trimmed forearm layer still contained about 925 px of the *original* hand's skin past the cuff. Over
a replacement hand that showed as a brown patch. Stored drawings are therefore covered by `forearm_<side>__hscuff`:
the same layer with only those pixels removed. Phase 2 drawings keep their earlier cover, so their rendering is
unchanged.

**Measured.** With the tucked placement, the drawings' own cuff stays under the character's sleeve whether masked
or not; the mask matters as soon as a drawing is moved. With the drawing pulled 20 px out of the sleeve:
* **unmasked:** 534 white cuff pixels from the hand layer (a second cuff);
* **masked:** 15 white pixels (hand highlights).

See `hand_store/evidence/cuff_mask_demo.jpg`. In normal placement, stored hands show 0–2 cuff-like pixels near the
wrist; the Phase 2 drawings show 16–18.

## 6. Attachment, IK and popping

`autorig hand-evidence` checks all 26 candidates (16 stored, 8 Phase 2, 2 original) on the hanging arm and on an
IK-raised arm (`hand_store/evidence/attachment_sheet.jpg`, `results.json`).

| Check | Result |
|---|---|
| Gap between hand and sleeve | **0 px** for every candidate, hanging and under IK |
| Skeleton change when switching | **0 px** (switching never moves the wrist, the IK target or any joint) |
| IK sweep, 16 frames, hand switched every frame (`ik_switch_sweep.gif`) | max gap **0 px** |
| Size, stored drawings | one scale per side (no size pop between them); visible area 0.74–1.02× the original hand (relaxed 0.74–0.75, fist 0.79, pointing 0.78–0.81, palm down 0.92–0.94, open palm 1.02) |
| Sleeve opening filled by the hand | 0.65–1.0 |
| Duplicate cuff pixels from the hand layer | 0–2 (stored), 16–18 (Phase 2 drawings) |
| Posed drift of the visible wrist band | 0.9–4.0 px for relaxed, open palm, fist, pointing and palm down; **8.1–8.6 thumbs-up, 11.3–18.7 grip, 22–25 cupped** |

**About the flagged drift.** On these three shapes the thumb or fingers sit next to the cuff, before the wrist plane.
Those pixels follow the forearm, as all arm artwork does, so the visible band's centroid shifts between rest and
pose. In the renders the hands stay attached (gap 0 px), but the band shift is listed as a flag for review. The same
three shapes are 1.40–1.56× wider than the sleeve opening right at the cuff, which is also flagged.

Edit speed in the editor (cloud CPU): about 7–8 s for the first engine build with the store. After that, an approval,
placement, mask or choice change reaches a skeleton pose in 0.5–0.7 s, because only the changed layer is
re-registered (it was 12 s before this was made incremental).

## 7. Hand Library panel (editor "Hands" tab)

**Import and browse:**
* import a pack (.zip), with the validation report or actionable errors shown;
* search, side, pose and status filters;
* thumbnails rendered *on the model* (the candidate at the character's wrist with the sleeve over it).

**Per candidate:**
* status and "active" badges; provenance, version and hash;
* wrist check: anchor residual and wrist fit for stored drawings;
* use for this pose, approve, reject;
* "Preview in pose": the current pose with this candidate, labelled as a preview;
* revert the side to the original hand;
* rename, delete and restore (soft delete, undoable), replace PNG (new version), version choice;
* wrist correction nudges (dx, dy, rotation, ±3 % scale, reset);
* mask controls (on/off, halo removal, cut line up/down) with the mask statistics.

**Browser test (Playwright on a fresh package copy):**
* imports the pack through the file input, and checks that the store is on disk and that a bad pack is rejected;
* confirms the drawing starts unapproved, approves the left relaxed hand, nudges it 1 px and previews it in the pose;
* checks that the manual left-hand choice is kept, and that auto mode then uses the approved relaxed drawing;
* deletes a candidate and undoes the delete;
* restarts the server and checks that the approval and placement persist;
* runs the export.

Screenshots: `docs/phase3_2/screenshots/06_hand_library_candidate.png` and `07_hand_preview_in_pose.png`.

## 8. Export: switch manifest for Moho Switch Layers

`export-assets` and the editor export now also write `hand_switches.json` and every hand candidate as a full-canvas
rest-frame PNG.

* **`validated`:** per group (`hand_right` / `hand_left`):
  * bone and pivot, in pixels and in the Moho frame;
  * user choice, auto flag and keyframes;
  * per variant: file and SHA-256, status, approval item, active flag, provenance, placement and correction, mask
    settings;
  * checks: file written, full canvas, alpha, side, rest metrics.
* **`not_implemented_in_native_moho`:**
  * creating a Switch layer and its children (no verified Moho API in the allowlist);
  * binding switch values to Smart Bones, actions or Live Mode inputs;
  * writing a `.moho` file (blocked until the Phase 0 importer is verified).

`moho_mapping` is `proposed_unverified`. The semantic hand actions are included. `production_ready` is false while
any drawing is pending. The export validates with 0 errors.

## 9. Tests

| Suite | Result |
|---|---|
| `tests/test_hand_store.py` | 27 passed |
| Full Python suite | 430 passed, 2 skipped (native Moho tests: NOT EXECUTED) |
| Editor (vitest) | 11 passed |
| Browser end-to-end | passed (with the Hand Library steps) |

`tests/test_hand_store.py` covers:
* synthetic packs: valid import with hashes and pending status; ten broken-pack cases with their error messages;
  unsafe and non-zip archives; PNG replacement versions; cuff mask with a raised thumb; halo removal; placement
  maths; field validation;
* the user's pack: complete validation; original default and pending never active; side mapping and one scale per
  side; every stored hand attached under IK without a second cuff; the mask demonstrably preventing a second cuff;
  auto choice versus a manual choice; neutral render unchanged; project v3 operations, approvals, undo/redo,
  reopening and v2 migration; export manifest; committed evidence.

These are measurements of the preview renderer. They do not make the AI concept drawings production artwork.

## 10. Unresolved issues

* **No drawing is approved.** All 16 imported drawings are AI-generated concepts pending your review; until you
  approve them, only previews use them.
* **Thumbs-up, grip, cupped:** visible-band drift of 8–25 px, and wider than the sleeve opening at the cuff
  (1.40–1.56×). Check them in the attachment sheet and correct or reject them.
* **Partially closed** still has no drawing. "cupped" was kept separate rather than assumed to be it.
* **Strong wrist bends** (about 35° or more): a small dark sliver of a drawing's cut edge can show beside the cuff,
  and a tiny dark tick of sprite outline remains at one cuff corner on some drawings.
* **Phase 2 drawings** (sheet fist, point, thumbs-up, palm-up) still use the old forearm cover, so they show the
  original-hand patch below the cuff. The new cover could be applied to them too; it was not, so as not to change
  earlier evidence silently.
* **The Phase 3.2 rest-alignment metric** (visible band versus the reference hand) is only meaningful for drawings
  shaped like the reference. Stored drawings use the cuff-edge check instead.
* **Moho was not run.** No Switch Layer was created natively; the manifest mapping is unverified.

## 11. Verdict: PARTIAL

| Requirement | Result |
|---|---|
| Import and validate the manifest and 16 PNGs, with hashes and provenance | PASS |
| Persistent character-scoped store (refresh and reopen, future packs and versions) | PASS |
| Selectable for both wrists in the existing switch system; original as default; relaxed as a candidate | PASS |
| Hand Library panel (search, filters, preview, approve/reject, replace, rename, delete, revert) | PASS |
| Interactive wrist anchor matching (pivot, orientation, scale corrections) | PASS (automatic match plus per-variant corrections; visual review still needed) |
| No duplicate cuff, original forearm kept | PASS (mask plus cuff-only cover; measured) |
| Switching keeps the wrist position, IK target and seam; no pop in position or size | PARTIAL (no skeleton or IK change and gap 0 for all; consistent scale; three shapes flagged for band drift and wrist width) |
| Preset integration without overriding manual choices | PASS |
| Versioned persistence with migration; semantic actions separate from Moho names | PASS |
| Export manifest separating validated from unimplemented | PASS |
| Tests and real-character previews | PASS |
| Quality gate: unreviewed AI artwork never treated as approved | PASS |
| Production use | **not yet**: review pending; Moho not run |

The next step is yours: review the drawings in the Hands tab (approve, correct or reject), especially thumbs-up,
grip and cupped. Nothing further was started.
