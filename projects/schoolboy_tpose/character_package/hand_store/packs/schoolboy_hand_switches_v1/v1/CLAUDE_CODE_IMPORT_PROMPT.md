# Claude Code prompt — integrate schoolboy hand variants into AutoRig Studio

You are the senior engineer for AutoRig Studio, a 2D cartoon auto-rigging pipeline intended to produce Moho-ready rigs and future live-puppeteering controls.

## Input files

Use the attached `AutoRig_Hand_Pose_Library.zip`. It contains a `manifest.json`, 16 separately exported transparent PNGs, and a contact sheet. The assets are AI-generated concept drawings and **not yet approved production artwork**. They have rough wrist-anchor estimates. The style is designed for the schoolboy with orange-brown hands, navy school blazer and white shirt cuffs.

## Objective

Add the hand variants to a persistent character-scoped **Hand Pose Store** inside AutoRig, and make them selectable in the existing artwork switch system for left and right wrists. Preserve original character artwork and all previous project files. Do not silently activate unapproved drawings.

## Work

1. Inspect the repository, existing hand layers, switch-group architecture, rig schemas, Phase 3.2 work and project persistence. Reuse what exists; do not create an incompatible second switch engine.
2. Import and validate `manifest.json` and all 16 PNGs. Verify that each file opens, has alpha, has a unique ID, and matches declared canvas size; reject incomplete packs with actionable errors. Keep source/provenance and hashes.
3. Create a character-scoped asset store and database/index that persists imports on disk, survives browser refresh and project reopening, and supports future alternate packs and versions. Never rely exclusively on browser local storage.
4. Register two groups: `hand_left` and `hand_right`, with eight artwork variants each. Map original existing hand art as the default if available; store the imported `relaxed` as a candidate until user approval.
5. Add a Hand Library panel: searchable pose thumbnails, side/pose filters, preview on model, variant approval and rejection, replace/import PNG, rename, delete candidate, and revert to original.
6. Implement interactive wrist anchor matching. Derive the character wrist from the approved skeleton, preview each variant over the original hand, allow x/y pivot, orientation and scale corrections, and store offsets **per variant**. Do not trust the manifest's approximate anchor or assume left/right PNGs are perfect mirrors.
7. Fix redundant cuff/sleeve overlap: the generated sprites include a navy sleeve and white cuff. Prefer masking these regions, or prepare reviewed hand-only variants, rather than overlaying a second cuff over the existing forearm sleeve. Avoid destructive edits to the source sprite.
8. When approved, switching variants must preserve the wrist world position, the arm IK target and sleeve seam alignment at any valid pose. No pop in position or size. Keep original forearm art and proper layer ordering.
9. Integrate switches with existing gesture presets: relaxed standing → relaxed, pointing → point, running → fist or relaxed, shrug → open_palm, grasping → grip. Auto-selection must never override manual user selection without permission; unavailable variants remain NEEDS_ARTWORK.
10. Persist hand switches and optional timeline keyframes in the project schema using a versioned migration. Keep semantic actions (`HAND_LEFT_POINT`, `HAND_RIGHT_FIST`, etc.) separate from unverified Moho-specific API names so they can later drive live puppeteering.
11. Export future-compatible switch manifests for Moho Switch Layers, clearly separating validated metadata from functions not yet implemented in native Moho. No unsupported claim of working .moho export.
12. Write tests for file importing, alpha transparency, side mapping, missing assets, wrist alignment, IK interaction, cuff masking, persisted selections, undo/redo, approvals and neutral pose fidelity. Run real-schoolboy visual previews as well as synthetic tests.
13. Produce a comparison contact sheet showing each approved or candidate hand attached at a wrist, with zoomed seams. Record measured anchor error, any visual popping and unresolved issues.
14. Commit verified changes and write `docs/hand_pose_store_integration.md` with test evidence, screenshots and PASS/PARTIAL/FAIL. Do not proceed to the next phase automatically.

**Quality gate:** The character's original hand must remain available. Unreviewed AI artwork must never be automatically treated as approved. Every hand must remain attached during IK movement, without duplicate cuffs or resizing the forearm.
