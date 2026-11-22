# Phase 3.7 implementation plan: intelligent hand selection, reference-guided animation, visual validation

Starting point: branch `claude/eloquent-cori-iv4ab1` at 043de36 (Phase 3.6, verdict PARTIAL).
Regression to fix first: `natural_pose/performance/acting/shrug_review_preview.png`, which the user supplied as a screenshot.

The order follows the user's priority: diagnose and fix the hand selection on real pixels first, then build the
reference pipeline on top of the corrected selection.

## Principles kept from earlier phases

- **One pipeline.** `MotionEngine.pose(intent_fn=...)` is the only solver entry. There is one switch engine (the
  ArtworkSwitchController, resolving user > keyframe > auto > default) and one hand-selection engine
  (`NaturalHandSelectionEngine`). The reference retargeter produces a `Performance`, which the existing
  `PerformanceDirector` plays.
- **Pending artwork is never approved in code.** Production mode means approved drawings plus the original hand.
  Review mode previews pending drawings and labels them.
- **Never scale a bone.** Never disguise missing art by rotating it. Depth motion is reported with a named missing view
  or drawing.
- **Visual quality is never scored.** All checks are geometric hints, and `artistic_review_required` stays true.

## Part A: hand selection (steps 1-8)

| Step | Work | Where |
|---|---|---|
| 1 | Diagnose the shrug: re-render the 3.6 frame, trace the switch decisions, crop the wrists and shoulders, explain every cause | `handselect_evidence.diagnosis`, `docs/phase3_7_hand_selection_audit.md` |
| 2 | Audit every hand drawing from its pixels, not its file name. `HandAssetMetadata` v1 (JSON Schema) records measured fields (wrist opening, axis, features, rest placement) and inspected fields (handedness, visible surface, palm normal, thumb, rotation tolerance) with per-field provenance. Editor corrections are `handmeta.*` ops | `handmeta.py`, `handaudit.py`, `data/schemas/hand_asset_metadata.schema.json`, `references/schoolboy/hand_pack_v2/semantic_inspection.json` |
| 3 | `HandOrientationAnalyzer`: palm classes PALM_TO_CAMERA, BACK_TO_CAMERA, PALM_UP, PALM_DOWN, EDGE_ON, AMBIGUOUS, computed from the palm normal under the flat placement rotation. Camera-facing palms can never become palm-up. The arm pose limits palm up/down | `handmeta.py` |
| 4 | Refactor `NaturalHandSelectionEngine` to check hard constraints first: missing, approval, excluded, gesture, handedness, metadata validity, palm view, rotation tolerance. Scoring follows (gesture, palm, wrist axis, rotation, proportion, silhouette, attachment, approval, continuity, fidelity), with a full explanation | `motion/handselect.py` |
| 5 | Approval states APPROVED / PENDING_REVIEW / REJECTED / MISSING; production vs review; fallbacks reported, never silent | `handselect.py`, `performance/resolver.py` |
| 6 | Per-variant wrist alignment check: attached, centred, no duplicate cuff, rotation, proportion, arm unchanged. Placement itself stays the WristAlignmentController's | `motion/handcheck.py` |
| 7 | SHRUG_SUBTLE (supported, approved art only) and SHRUG_EXPRESSIVE (palm-up art required; NEEDS_ARTWORK in production) | `performance/gestures.py`, `performance/scenarios.py` |
| 8 | Shoulder fullness: measure the slope, hump and rise; lower the shrug clavicle elevation; flag inflated shoulders | `performance/shoulders.py` |

Also part of Part A: fix the Phase 3.6 resolver bug (`auto: True` dropped every performance hand request). Hand
*actions* are now resolved once per hand interval by the engine, so frames stay pure functions of time.

## Part B: reference motion (steps 9-13)

These modules are in the `refmotion/` package:
- `importer.py`: MP4/MOV (ffmpeg), GIF, image sequences, AutoRig JSON, BVH. A permission statement is required.
  Quality flags cover visibility, occlusion, view and scale.
- `pose.py`: PoseEstimationProvider with MediaPipe Pose and Hand Landmarker (local CPU, SHA-256-pinned models in
  `.autorig_cache/models`), skeletal and fixture providers. RTMPose was evaluated but not integrated.
- `analyze.py`: channels in rig conventions, timing, holds, accents, contacts, coordination, head lead, asymmetry and
  foreshortening.
- `retarget.py`: normalised angle deltas on the character's own neutral; keys at the phase boundaries; hand actions
  through the constrained engine; depth reported as REQUIRES_ALTERNATIVE_ARTWORK or REQUIRES_NEW_VIEW.
- `store.py`: an append-only, character-independent library (`motion_references/`), with per-character settings in
  the package.
- `track.py`: the LandmarkTrack model, plus the synthetic references (pointing, shrug, walking), labelled synthetic.

## Part C: visual critique (steps 14-15)

`refmotion/critic.py`:
- a rule-based provider built on the measurable checks;
- an optional VLM provider interface (disabled, untested, can never drive a control);
- a `RevisionLoop` that is bounded, detects oscillation, rolls back, works on a temporary candidate and stops at a
  proposal that needs the user's approval.

## Part D: editor (steps 16-17)

- Project schema v9, with `handmeta.set`, `handmeta.reset` and the `reference.*` ops (select, settings, annotate,
  preset.save, revision).
- API: `/api/hands/inspect`, `/api/hands/closeup.png`, `/api/hands/metadata`, `/api/reference*` (library, frame,
  import, retarget, critique, revise).
- New tabs:
  - **Hand Inspector**: active variant, classification, handedness, palm, pivot, rotation, approval, rejected
    candidates, explanation, override, close-up comparison, metadata corrections;
  - **Ref Motion**: library, skeleton scrub, segment, intensity, timing, hand actions, retarget, "Use as performance"
    (`performance.set`, played back in the existing Performance tab), critique, revision proposal, annotations,
    presets.

## Part E/F: evidence, compatibility, performance and tests (steps 18-26)

Evidence commands:
- `autorig hand-audit`;
- `autorig hand-selection-evidence`: diagnosis, 10 regression cases in review and production, continuity,
  invalid-metadata probes;
- `autorig reference-evidence`: synthetic references retargeted, a MediaPipe round trip on an AutoRig-rendered video,
  the revision loop;
- `autorig phase37-bench`: timings, plus Moho-oriented metadata (`proposed_unverified`).

Tests:
- `tests/test_p37_hands.py` and `tests/test_p37_reference.py`;
- earlier schema pins move to v9;
- the synthetic selection test library gains explicit metadata, because the engine now requires it for
  palm-specific actions;
- the e2e gains steps 6j (Hand Inspector) and 6k (Ref Motion).

Docs:
- `docs/phase3_7_hand_selection_audit.md`;
- `docs/phase3_7_reference_motion_architecture.md`;
- `docs/phase3_7_visual_quality_report.md`;
- `docs/phase3_7_completion_report.md`.

Out of scope: Phase 4 (native Moho) is not started.
