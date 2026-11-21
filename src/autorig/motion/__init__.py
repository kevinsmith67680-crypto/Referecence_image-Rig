"""Phase 3.1 natural motion: planning, skeletal solving, garment-aware deformation and evaluation.

The posing system is split into independent stages:

* ``planner.MotionPlanner``: what the character does. It turns a preset or a gait phase into a ``PoseIntent``
  (foot, hand and pelvis targets, contact flags, torso and head intent, garment hints, required artwork).
* ``solver.SkeletalPoseSolver``: anatomical joint positions for that intent. It uses FK, two-bone IK, fixed
  segment lengths (never axial scaling), joint limits, clavicle rhythm, pelvis placement from foot contact,
  balance and head stabilisation.
* ``garments.ArtworkDeformationEngine``: renders the solved pose from the original artwork. It adds a garment
  model (semantic regions, anchors, pose-dependent corrective bands, hem lag, layering rules) on top of the
  Phase 2 skinning deformer.
* ``feasibility.PoseFeasibilityEvaluator`` and ``quality.PoseQualityEvaluator``: what the available artwork can
  support, and what the rendered result actually measures.

Coordinates are source pixels (origin top-left, +y down). Angles are degrees in image space, positive =
clockwise on screen, as in ``autorig.phase2.skeleton``.
"""
