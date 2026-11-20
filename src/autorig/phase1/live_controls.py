"""LiveControlSpec v1.0.0: the future animation-control contract.

Semantic controls (head_yaw, eye_blink_left, jaw_open, ...) describe WHAT a
rig should be able to do, independent of any input device. They are NOT
assumed to equal Moho 14.5 SHOWTIME channels. The Moho side is recorded in two
strictly separate lists:

* ``verified_mappings``  - only mappings confirmed in Moho 14.5 itself
  (currently none: the native discovery protocol D0-D6 has not been run);
* ``proposed_mappings``  - candidates derived from Lost Marble's published
  wording ("head turning, head tilting, blinking, mouth closing, lip pursing";
  gamepad buttons/thumbsticks via "Select Input"), each marked unverified.

The adapter design (input -> normalise -> curve -> Smart Bone angle / Action)
is data only; no Moho API is called here.
"""

from __future__ import annotations

from typing import Any

SPEC_VERSION = "1.0.0"

# id: (range, neutral, target_type, artwork_dependencies, capability)
CONTROLS: dict[str, tuple[list[float], float, str, list[str], str, str]] = {
    "head_yaw": ([-1, 1], 0, "smart_bone", ["head", "hair", "ear_r", "ear_l"], "head_turning",
                 "Turn head left/right (-1 = character's right, +1 = character's left)."),
    "head_pitch": ([-1, 1], 0, "smart_bone", ["head", "hair"], "head_turning", "Nod head down/up."),
    "head_roll": ([-1, 1], 0, "bone_rotation", ["head", "hair", "neck"], "head_tilting", "Tilt head sideways."),
    "eye_blink_left": ([0, 1], 0, "smart_bone", ["eye_l"], "blinking", "0 = open, 1 = closed."),
    "eye_blink_right": ([0, 1], 0, "smart_bone", ["eye_r"], "blinking", "0 = open, 1 = closed."),
    "eye_look_x": ([-1, 1], 0, "smart_bone", ["pupil_r", "pupil_l", "eye_r", "eye_l"], "eye_direction",
                   "Gaze horizontally."),
    "eye_look_y": ([-1, 1], 0, "smart_bone", ["pupil_r", "pupil_l", "eye_r", "eye_l"], "eye_direction",
                   "Gaze vertically."),
    "brow_raise_left": ([0, 1], 0, "smart_bone", ["brow_l"], "eyebrow_movement", "Raise left brow."),
    "brow_raise_right": ([0, 1], 0, "smart_bone", ["brow_r"], "eyebrow_movement", "Raise right brow."),
    "brow_frown": ([0, 1], 0, "smart_bone", ["brow_r", "brow_l"], "eyebrow_movement", "Knit brows."),
    "jaw_open": ([0, 1], 0, "smart_bone", ["mouth", "head"], "mouth_opening", "0 = closed, 1 = fully open."),
    "mouth_width": ([-1, 1], 0, "smart_bone", ["mouth"], "lip_sync", "Narrow (-1) to wide (+1)."),
    "mouth_pucker": ([0, 1], 0, "smart_bone", ["mouth"], "lip_sync", "Pucker/purse lips."),
    "smile_left": ([0, 1], 0, "smart_bone", ["mouth"], "facial_expressions", "Raise left mouth corner."),
    "smile_right": ([0, 1], 0, "smart_bone", ["mouth"], "facial_expressions", "Raise right mouth corner."),
    "expression_neutral": ([0, 1], 1, "action", ["head", "brow_r", "brow_l", "eye_r", "eye_l", "mouth"],
                           "facial_expressions", "Weight of the neutral (reference) expression."),
    "expression_angry": ([0, 1], 0, "action", ["brow_r", "brow_l", "eye_r", "eye_l", "mouth"],
                         "facial_expressions", "Angry expression weight / trigger."),
    "expression_sad": ([0, 1], 0, "action", ["brow_r", "brow_l", "eye_r", "eye_l", "mouth"],
                       "facial_expressions", "Sad expression weight / trigger."),
    "expression_surprised": ([0, 1], 0, "action", ["brow_r", "brow_l", "eye_r", "eye_l", "mouth"],
                             "facial_expressions", "Surprised expression weight / trigger."),
    "expression_happy": ([0, 1], 0, "action", ["brow_r", "brow_l", "eye_r", "eye_l", "mouth"],
                         "facial_expressions", "Happy expression weight / trigger."),
}

SHOWTIME_EVIDENCE = [
    {"source": "Lost Marble, 'Moho 14.5 SHOWTIME' announcement (Oct 2026)",
     "url": "https://moho.lostmarble.com/blogs/news/moho-14-5-showtime-design-rig-and-animate-characters-live-all-the-power-of-moho-now-live",
     "claim": "create a Smart Bone and assign it to any of the camera inputs; inputs include head turning, head "
              "tilting, blinking, mouth closing, lip pursing and many more"},
    {"source": "Moho 14.5 press release", "url": "https://www.openpr.com/news/4664782/moho-14-5-showtime-adds-real-time-character-and-vtuber",
     "claim": "any XInput gamepad; buttons and thumbsticks; assigned with 'Select Input'"},
]

PROPOSED = [
    ("head_yaw", "camera", "head turning", "smart_bone"),
    ("head_roll", "camera", "head tilting", "smart_bone"),
    ("eye_blink_left", "camera", "blinking", "smart_bone"),
    ("eye_blink_right", "camera", "blinking", "smart_bone"),
    ("jaw_open", "camera", "mouth closing", "smart_bone (inverted: 'closing' -> open = 1 - value?)"),
    ("mouth_pucker", "camera", "lip pursing", "smart_bone"),
    ("expression_happy", "gamepad", "a button (Select Input)", "action"),
    ("expression_angry", "gamepad", "a button (Select Input)", "action"),
    ("expression_sad", "gamepad", "a button (Select Input)", "action"),
    ("expression_surprised", "gamepad", "a button (Select Input)", "action"),
    ("eye_look_x", "gamepad", "a thumbstick axis (if sticks can drive Smart Bones)", "smart_bone"),
    ("eye_look_y", "gamepad", "a thumbstick axis (if sticks can drive Smart Bones)", "smart_bone"),
]


def build(readiness: dict[str, Any], face_features: set[str]) -> dict[str, Any]:
    caps = readiness["capabilities"]
    controls = []
    for cid, (rng, neutral, target, deps, cap, desc) in CONTROLS.items():
        c = caps[cap]
        missing_deps = [d for d in deps if d not in face_features and d not in ("head", "hair", "neck")]
        controls.append({
            "id": cid, "description": desc, "value_range": rng, "neutral": neutral,
            "animation_target_type": target, "artwork_dependencies": deps, "capability": cap,
            "missing_artwork": c["required_artwork"] + [f"segmented:{d}" for d in missing_deps],
            "readiness": c["status"],
            "confidence": 0.0 if c["status"] in ("NEEDS_REVIEW", "UNSUPPORTED") else (
                1.0 if c["status"] == "READY" else 0.5),
        })
    return {
        "spec_version": SPEC_VERSION,
        "status": "contract only - no tracking or rig implemented in Phase 1",
        "controls": controls,
        "moho_showtime": {
            "target_version": "Moho Pro 14.5 (SHOWTIME)",
            "verified_mappings": [],
            "verification_status": "NOT VERIFIED - Moho 14.5 input identifiers, ranges and storage are unknown "
                                   "until the native discovery protocol (docs/live-models.md, D0-D6) is run",
            "proposed_mappings": [
                {"control": c, "device": d, "showtime_input_label": label, "moho_target": t,
                 "status": "proposed_unverified"} for c, d, label, t in PROPOSED],
            "evidence": SHOWTIME_EVIDENCE,
        },
        "adapter_design": {
            "summary": "Live input -> normalise to the control's value_range -> response curve -> rig target",
            "stages": [
                {"stage": "input", "detail": "Moho 14.5 Live Mode camera/gamepad input (identifier from discovery)"},
                {"stage": "normalise", "detail": "map the input's native range onto value_range; clamp"},
                {"stage": "curve", "detail": "deadzone, gain, smoothing, optional inversion (e.g. 'mouth closing')"},
                {"stage": "target", "detail": "smart_bone: control angle = lerp(min_deg, max_deg, value); "
                                              "action: trigger/weight by value; bone_rotation: degrees"},
            ],
            "implementation_status": "design only - requires verified Moho input identifiers (LM-1)",
        },
    }
