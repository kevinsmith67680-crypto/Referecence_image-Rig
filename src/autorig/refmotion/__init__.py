"""Phase 3.7 reference-guided animation.

The modules form one pipeline:
- ``track``: the LandmarkTrack data model and the procedurally authored synthetic references;
- ``importer``: ReferenceMotionImporter. Video (MP4/MOV through ffmpeg), GIF, image sequences and skeletal files
  (AutoRig JSON, BVH), with metadata and quality flags;
- ``pose``: PoseEstimationProvider. MediaPipe Pose Landmarker and Hand Landmarker run locally on the CPU; there is
  also a skeletal-file provider and a fixture provider;
- ``analyze``: ReferenceMotionAnalyzer. Joint angles, timing, holds, anticipation, overshoot, contacts, coordination,
  head lead, asymmetry, phases and foreshortening;
- ``retarget``: ReferenceMotionRetargeter. Normalised angles become a Performance for the one solver; depth motion is
  reported, never stretched;
- ``store``: MotionReferenceStore. Character-independent reference data is kept apart from per-character settings;
- ``critic``: VisualAnimationCritic (rule-based, with an optional VLM provider interface) and the controlled revision
  loop;
- ``evidence``: ``autorig reference-evidence``.

Nothing in this package runs cloud inference. A pose model runs only when a reference is imported, never during
interactive animation.
"""
