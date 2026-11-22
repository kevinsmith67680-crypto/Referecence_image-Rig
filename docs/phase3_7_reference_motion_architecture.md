# Phase 3.7 reference-guided animation: architecture

Status: implemented and tested; verdict PARTIAL (see `phase3_7_completion_report.md`). No permission-cleared human
reference video was available in this environment. The three test references are therefore procedurally authored
synthetic skeletons, labelled `provenance.kind = "synthetic"`. The video path was checked with a real pose model
(MediaPipe) on an AutoRig render of the user's own character. Neither is evidence about real human footage.

## Pipeline

```
media (MP4/MOV/GIF/images/JSON/BVH) + metadata (source, usage permission)
  -> ReferenceMotionImporter      (ffmpeg decode, quality flags)
  -> PoseEstimationProvider       (MediaPipe Pose + Hand Landmarker, local CPU; or skeletal / fixture)
  -> LandmarkTrack                (pixels, relative depth, visibility per landmark; raw kept)
  -> ReferenceMotionAnalyzer      (channels in rig conventions, timing, accents, contacts, foreshortening)
  -> MotionReferenceStore         (character-independent library, append-only, hashed)
  -> ReferenceMotionRetargeter    (angle deltas on the character's own neutral -> Performance keys + hand actions)
  -> PerformanceDirector          (the one pipeline: limits, contacts, balance, garment rig, switches)
  -> VisualAnimationCritic / RevisionLoop (hypotheses; proposals need the user's approval)
```

## Importer (`refmotion/importer.py`)

- Video is decoded by the system `ffmpeg` binary through a subprocess. Output is raw RGB, at most 30 fps and 900
  frames, scaled to at most 960 px. ffprobe supplies fps, size and duration. There is no Python codec dependency.
- GIF goes through PIL. Image sequences are a directory or a glob, sorted by name.
- Skeletal files:
  - AutoRig `autorig.reference_track` JSON;
  - BVH, read by a minimal parser with forward kinematics and orthographic front projection. Mixamo/CMU joint names
    are mapped; if a required joint is missing, the import is refused with its name.
- Metadata: a provenance `source` and a `usage_permission` statement are mandatory, and the import is refused
  without them.
- Recorded per reference: fps, duration, size, motion category, gesture/emotion/intensity tags and the hand actions.
- Quality (`quality()`):
  - mean visibility of the core joints;
  - occlusion per joint (the fraction of frames with visibility below 0.5);
  - the view, from the shoulder-width / torso-length ratio: front, three-quarter, side, or back (shoulders mirrored);
  - the subject's height in pixels.

  A reference is `usable` only when it is a front view with a mean visibility of at least 0.5. Unsupported views are
  flagged, never silently used.

## Pose estimation (`refmotion/pose.py`)

| Provider | Status | Notes |
|---|---|---|
| MediaPipe Pose Landmarker (full/lite) + Hand Landmarker | **integrated, run here** | Apache-2.0; local CPU, with arm64 wheels for the M1; video mode with temporal tracking; 33 body + 21 per-hand landmarks. Models are downloaded once by `autorig reference-models --download` into `.autorig_cache/models/` (git-ignored) and checked by SHA-256. MediaPipe hand labels assume a mirrored image, so they are swapped for normal footage. |
| RTMPose / MMPose | evaluated, not integrated | Needs PyTorch + mmcv/mmpose (or ONNX + rtmlib) and a person detector; arm64 mmcv builds are uncertain. It fits the provider interface. |
| MoveNet | not integrated | 17 landmarks, no hands. |
| Skeletal / fixture | integrated | Pass-through of skeletal files; deterministic test landmarks. |

Uncertainty handling: visibility is stored for every landmark. The analyzer smooths with confidence weights
(Gaussian in time, landmarks below 0.5 visibility weighted 1e-3), so low-confidence frames are interpolated from
their neighbours and flagged in `low_confidence_frac`. A visibility value is never taken as accuracy: the round trip
below shows high visibility (0.985) together with biased shoulder estimates on a cartoon.

## Analysis (`refmotion/analyze.py`)

Raw data is kept (the track). Derived channels use the rig's conventions, so they map onto `PerformanceDirector`
parameters:
- arm abduction (relative to the torso axis);
- the elbow parameter (forearm angle = abduction - flexion);
- wrist;
- shoulder elevation along the torso axis (a lean is not counted as a shrug);
- head tilt;
- torso lean (line of action);
- pelvis roll;
- weight (the hip midpoint between the ankles).

Timing:
- speed of the main channels;
- holds (speed below 8 % of the peak for at least 0.15 s);
- phase boundaries (speed minima below 35 % of the peak, at least 0.12 s apart);
- acceleration peaks.

Accents per channel: anticipation (the counter-movement before the main move) and overshoot (relative to the first
settled stretch).

Also derived:
- foot contacts (ankle low and slow);
- left/right arm speed correlation and lag;
- head lead;
- asymmetry.

Foreshortening: a segment is flagged as moving in depth when its 2D length drops below 0.8 of its own 98th
percentile, or when its out-of-plane angle exceeds asin 0.5 (exact depth, synthetic) or asin 0.6 (estimated MediaPipe
depth). Results on the synthetic references:

| reference | depth segments | holds | accents |
|---|---|---|---|
| pointing | none | 3 (the pointing hold 1.1-1.9 s) | abduction anticipation 5.8°, overshoot 2.7° |
| shrug | forearms (both) | 3 | shoulder rise ~11°, small dip before it |
| walking | forearms, shins | whole clip (in-plane channels barely move) | pelvis roll ±3° |

User annotations (phase labels, notes) are stored with the reference (append-only in the library) or as
`reference.annotate` project ops. They are never mixed into the analysis.

## Retargeting (`refmotion/retarget.py`)

- `rig = character neutral + intensity × (reference − reference first frame)` for every channel. The character's
  proportions, pivots and neutral are kept: only angles move, and human proportions are never transferred.
- Keys are placed at the analyzer's phase boundaries and hold ends (at least 0.1 s apart), so the sequencer's easing
  produces the timing. A hold is the pair of keys at its two ends. `timing_scale` stretches the times, and `segment`
  selects a part of the clip.
- Hand intentions in the reference metadata become hand *actions* on the keys where that arm is more than 15° from
  neutral (`relaxed_standing` otherwise). The NaturalHandSelectionEngine then chooses drawings under its hard
  constraints. A palm-up reference therefore asks for `shrugging`; in production it gets NEEDS_ARTWORK and a fallback,
  never a rotated camera-facing hand.
- Limits, contacts, balance, the garment rig and switches all come from the existing pipeline. Parameters are
  soft-clamped there.
- Feasibility:
  - segments in depth become REQUIRES_ALTERNATIVE_ARTWORK (forearms or upper arms toward the camera:
    `art:forearm_foreshortened_<s>`) or REQUIRES_NEW_VIEW (`view:leg_sagittal`);
  - locomotion keeps only the in-place weight shift and pelvis roll. The stride is REQUIRES_NEW_VIEW, is not rendered,
    and is never claimed as transferred.
- `compare()` gives the per-channel RMS and maximum error between the retargeted parameters and the target curves.

## Store (`refmotion/store.py`)

The library is `motion_references/<id>/v<N>/`, at the repository root, or wherever `AUTORIG_MOTION_LIBRARY` points.
Each version holds:
- `reference.json`: tags (gesture, movement, emotion, view, intensity, duration, required_artwork), provenance,
  permission, provider and SHA-256 hashes;
- `track.json`;
- `analysis.json`;
- `annotations.json`.

Versions are append-only, `verify()` checks the hashes, and `search()` filters by tags. The library contains no
character data.

Per character, `<package>/natural_pose/reference_motion/<id>/` holds `settings.json` (intensity, timing, segment, hand
actions, offsets) and `retarget.json`. The editor project holds the selection, settings, annotations, presets and
revision decisions (`reference.*` ops, schema v9).

## Critic and revision loop (`refmotion/critic.py`)

A Defect has region, issue, severity, suggestedCorrection, requiresArtwork, control, evidence, source and confidence,
and `hypothesis: true`. The critic's `approves` is always False.

The rule-based provider reads:
- hand decisions (NEEDS_ARTWORK, NEEDS_APPROVAL, explicit-choice violations);
- wrist checks;
- the shoulder silhouette (rise, slope);
- reference angle error.

The VLM provider is an interface only: disabled unless `AUTORIG_VLM_CRITIC`, `AUTORIG_VLM_MODEL` and `AUTORIG_VLM_KEY`
are set; never used for interactive requests. The transport is deliberately not wired, because no endpoint was
available to test it. Its parser forces `hypothesis` and a confidence of at most 0.6, and drops any `control`.

RevisionLoop:
1. pick the worst defect that has a control;
2. apply one bounded step to a deep copy of the performance (keys within 0.6 s of the frame);
3. re-render and re-critique;
4. keep the step only if the target metric improved and no new blocking defect appeared, otherwise roll back.

There are at most `max_attempts` steps, a per-control cap, and oscillation detection. The output is a candidate with
status `PROPOSED_NEEDS_APPROVAL`. The original performance is untouched, and applying the candidate is the user's
`performance.set`.

## Editor

- **Ref Motion** tab: library list (tags, provenance, required artwork), skeleton scrub, segment, intensity, timing,
  hand actions, Retarget, Use as performance, Critique, Propose revision, Accept or Reject, annotations and presets.
  "Use as performance" writes `performance.set`, so playback, curves and rendering stay in the Performance tab.
- **Hand Inspector**: see the hand-selection audit.

## Evidence (`autorig reference-evidence <package>`)

The output is in `natural_pose/reference_motion/evidence/`:
- `comparison_<ref>.png` per reference: the reference skeleton, the reference-guided render (hands labelled, `*` =
  pending), the rig silhouette with the scaled reference skeleton, and the hand-authored 3.7 version;
- `roundtrip.png` and `roundtrip.mp4`;
- `revision_loop.png`;
- `results.json`.

Round trip: a schoolboy render, encoded as MP4, then ffmpeg, MediaPipe Pose (full, local CPU), the analyzer, and
comparison with the known rig parameters. Over the acting test 2.4-6.0 s (pointing, then the subtle shrug), 44
frames:

| channel | truth range | estimated range | RMS error |
|---|---|---|---|
| arm_r_abduction | 145.2° | 134.1° | 13.7° |
| arm_r_flexion | 32.5° | 17.3° | 18.8° |
| arm_l_abduction | 5.0° | 9.0° | 1.9° |
| clavicle_r | 8.2° | 19.5° | 16.7° |
| head | 11.9° | 14.9° | 8.9° |
| line_of_action | 9.3° | 2.7° | 5.7° |

Visibility was 0.985 with a front view, and the pose model took 0.068 s per frame on this sandbox CPU.

What this shows: the video path runs end to end with a real model, and the large arm motion is recovered, but this
character's proportions bias the elbow and shoulder estimates. Shoulder elevation from MediaPipe should not be trusted
on this character. This is a cartoon render, not a human, so these numbers are not human-reference accuracy.
