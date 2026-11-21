"""PoseIntent: the contract between the MotionPlanner and the SkeletalPoseSolver.

An intent says *what* the character does: where the feet and hands go, which feet carry weight, how the pelvis
moves, what the torso and head do, and which artwork the pose needs. It contains no bone rotations. The solver
works those out under the character's anatomical constraints.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class LegIntent:
    planted: bool
    ankle: tuple[float, float]  # world target of the ankle (rest-canvas px)
    foot_angle: float = 0.0  # world rotation of the shoe relative to its rest orientation (deg)
    foot_follow: float = 0.0  # extra share of the shin's world rotation the shoe follows (swing only)
    knee_max: float = 10.0  # in-plane (front-view) knee flexion limit for this leg in this pose
    knee_min: float = 3.0  # soft-knee minimum of a planted leg (sets the standing height)
    load: float = 1.0  # share of body weight / pelvis-height authority (gaits blend it through transitions)
    ground: tuple[float, float] | None = None  # ground contact this leg refers to (planted target, or next landing)
    hang_knee: float | None = None  # swing legs: knee flexion when hanging free (the foot never over-reaches)
    clearance: float = 0.0  # swing legs: minimum height of the ankle above its ground line (px), ramps with the swing
    pole: tuple[float, float] | None = None  # knee pole target (world); default: knee lateral (front view)


@dataclass
class ArmIntent:
    abduction: float = 10.0  # upper-arm direction from hanging, + = lateral (FK mode)
    flexion: float = 12.0  # elbow, + = forearm toward the midline when the arm hangs (FK mode)
    wrist: float = 4.0  # + = hand toward the midline
    reach: tuple[float, float] | None = None  # (lateral, up) wrist target, fractions of arm length from shoulder
    target: tuple[float, float] | None = None  # world wrist target (overrides reach)
    elbow: str = "down"  # IK bend preference: "down" or "out"
    hand_dir: float | None = None  # world direction of the hand bone (deg from straight down), overrides wrist
    hand: list[str] = field(default_factory=lambda: ["open"])  # preferred hand drawings, best first
    pole: tuple[float, float] | None = None  # elbow pole target (world) for IK; default from ``elbow``
    target_joint: str | None = None  # IK wrist target taken from a solved joint (e.g. "knee_r": hands on knees)
    target_offset: tuple[float, float] = (0.0, 0.0)  # added to the joint target (px)
    max_flex: float | None = None  # IK: user elbow flexion limit (deg), tighter than the anatomical one


@dataclass
class PoseIntent:
    name: str
    title: str
    intent: str
    view: str = "front"
    legs: dict[str, LegIntent] = field(default_factory=dict)
    arms: dict[str, ArmIntent] = field(default_factory=dict)
    pelvis_dx: float = 0.0  # lateral pelvis offset from the rest position (px); static poses add balance
    pelvis_dy: float = 0.0  # vertical offset from the standing height (px, + = lower; - only when airborne)
    pelvis_roll: float = 0.0  # deg, + raises the character's right hip
    spine: float = 0.0
    chest: float | None = None  # None: counter-roll against pelvis + spine (shoulders stay near level)
    chest_counter: float = 0.8
    head: float = 0.0  # head world tilt (deg); the neck takes neck_share of it
    neck_share: float = 0.4
    clavicle: dict[str, float] = field(default_factory=dict)  # side -> extra elevation (deg)
    scapulohumeral: bool = True  # clavicle elevates with arm abduction above horizontal
    balance: str = "static"  # static: CoM over the weighted support; dynamic: planned pelvis path (gaits)
    weight_left: float = 0.5
    standing_feet: dict[str, tuple[float, float]] | None = None  # nominal planted ankles for airborne frames
    root: tuple[float, float] = (0.0, 0.0)  # extra root translation (moving cycles)
    garment: dict[str, float] = field(default_factory=dict)  # e.g. hem_lag_deg, hair_lag_deg
    smooth_support: bool = False  # gaits: smooth (soft) max over planted feet for the pelvis height
    front: list[str] = field(default_factory=list)  # layers composited last
    arms_behind_head: bool = True
    phase: float | None = None
    contact: dict[str, bool] = field(default_factory=dict)
    channels: dict[str, float] = field(default_factory=dict)  # planner signals (arm forwardness, foot lift ...)
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)
