"""Moho-oriented animation metadata, the export compatibility assessment, and the LivePerformanceControlSpec.

This prepares data for Phase 4; it is not an exporter.
- No Moho Lua API member is named.
- No ``.moho`` file is written.
- Every mapping is ``proposed_unverified``, because Moho was not run.

Native constructs are named by product feature only (keyframes, Actions, Smart Bones, switch layers, bone
targets).

Classes: DIRECTLY_EXPORTABLE, REQUIRES_CONVERSION, REQUIRES_BAKING, REQUIRES_MANUAL_SETUP, UNSUPPORTED.

AutoRig timing curves, contact blending, the weight-transfer balance and the secondary springs are AutoRig
computations. The assessment assumes none of them exists natively. Without a verified conversion, they are baked
into per-frame keys.
"""

from __future__ import annotations

from typing import Any

PROPOSED = "proposed_unverified"
COMPAT_CLASSES = ("DIRECTLY_EXPORTABLE", "REQUIRES_CONVERSION", "REQUIRES_BAKING", "REQUIRES_MANUAL_SETUP",
                  "UNSUPPORTED")

COMPATIBILITY: list[dict[str, Any]] = [
    {"item": "skeletal keyframes (per-bone rotation relative to rest, per frame)", "class": "REQUIRES_CONVERSION",
     "native": "bone angle keyframes",
     "note": "AutoRig: degrees, clockwise on screen, relative to rest; a native angle is the rest angle minus the "
             "rotation in radians (counter-clockwise, parent-relative). Exact at every exported frame"},
    {"item": "root motion (pelvis shift)", "class": "REQUIRES_CONVERSION", "native": "root bone position keys",
     "note": "pixels -> frame units (y flipped)"},
    {"item": "timing curves (quintic, Bezier, monotone Hermite, overlap offsets)", "class": "REQUIRES_BAKING",
     "native": "keys on every frame", "note": "native interpolation is not assumed to match: bake per frame, or key only "
                                              "the key poses and accept the native curve (stated as a difference)"},
    {"item": "anticipation / overshoot bumps", "class": "REQUIRES_BAKING", "native": "keys on every frame",
     "note": "C2 bumps are not a native curve type"},
    {"item": "pose Actions (gesture key poses)", "class": "REQUIRES_CONVERSION", "native": "Actions",
     "note": "one Action per gesture key pose; gestures whose hands are pending stay review-only"},
    {"item": "hand switch keys", "class": "REQUIRES_CONVERSION", "native": "switch layer keys",
     "note": "per side; pending drawings only in a review export"},
    {"item": "IK targets / contacts (desk, hip, object)", "class": "REQUIRES_BAKING",
     "native": "bone targets, or baked arm angles",
     "note": "AutoRig blends the wrist target with C2 weights and an elbow pole; a native target-bone set-up is "
             "possible but unverified: bake the solved arm angles by default"},
    {"item": "contact events (start, end, acquire, release)", "class": "REQUIRES_MANUAL_SETUP",
     "native": "markers / notes", "note": "kept as metadata for an animator"},
    {"item": "weight transfer (balance, planted feet)", "class": "REQUIRES_BAKING", "native": "keys on every frame",
     "note": "the static balance is an AutoRig solve"},
    {"item": "motion layers (masks, weights, override/additive)", "class": "REQUIRES_BAKING",
     "native": "flattened keys (optionally Actions per clip)", "note": "layer composition is AutoRig-only"},
    {"item": "secondary motion (hair, hem springs)", "class": "REQUIRES_BAKING", "native": "baked deformation keys",
     "note": "the garment terms are AutoRig deformations; a native equivalent would be Smart Bones or a mesh warp "
             "(unverified)"},
    {"item": "garment corrections (approved correctives)", "class": "REQUIRES_CONVERSION",
     "native": "Smart Bone Actions / switch layers", "note": "only approved assets; currently none is approved"},
    {"item": "dynamic layer order", "class": "REQUIRES_MANUAL_SETUP", "native": "layer order per pose",
     "note": "reordering is not assumed scriptable"},
    {"item": "gesture identifiers and semantic events", "class": "DIRECTLY_EXPORTABLE", "native": "metadata",
     "note": "plain data"},
    {"item": "acting style profiles", "class": "REQUIRES_BAKING", "native": "baked into keys", "note": "AutoRig-only"},
    {"item": "facial controls (future)", "class": "UNSUPPORTED", "native": "-",
     "note": "no facial artwork or rig yet; the channel is reserved and isolated"},
]


def animation_metadata(director, fps: float | None = None, frames: list[dict[str, Any]] | None = None
                       ) -> dict[str, Any]:
    """Everything Phase 4 needs to transfer a performance; per-frame data comes from a skeleton-only track."""
    d = director
    f = fps or d.perf.fps
    tr = frames if frames is not None else d.track(f)
    sk = d.eng.sk
    bones = [{"name": b.name, "parent": b.parent, "head_px": [round(float(v), 3) for v in b.head],
              "tail_px": [round(float(v), 3) for v in b.tail]} for b in sk.bones]
    per_frame = []
    hand_keys: dict[str, list[dict[str, Any]]] = {"r": [], "l": []}
    for fr in tr:
        res = d.solve(fr["t"])
        th = [round(float(v), 4) for v in res["pose"].theta]
        sh = [round(float(v), 3) for v in res["pose"].shift]
        pf = fr["performance"]
        row = {"t": fr["t"], "theta_deg": th, "root_shift_px": sh, "secondary": pf["secondary"],
               "ik": {s: c["wrist_target"] for s, c in (pf.get("contacts") or {}).items()},
               "layers": [a["clip"] for a in pf["layers"]]}
        per_frame.append(row)
        for s, h in pf["hands"].items():
            if not hand_keys[s] or hand_keys[s][-1]["pose"] != h["shown"]:
                hand_keys[s].append({"t": fr["t"], "pose": h["shown"], "status": h["status"]})
    actions = {}
    for g in d.perf.gestures:
        actions[g.get("id") or g["gesture"]] = {"gesture": g["gesture"], "side": g.get("side"),
                                                "status": d.library.status(g["gesture"], g.get("side", "r"))}
    return {"schema": "autorig.performance_animation", "version": "1.0.0", "status": PROPOSED, "fps": f,
            "duration_s": round(d.duration, 4),
            "conventions": {"theta": "degrees, rotation of each bone relative to rest, clockwise on screen (image "
                                     "+y down), about the bone head, inherited by children",
                            "root_shift": "pelvis translation in source pixels"},
            "skeleton": bones, "frames": per_frame, "hand_switch_keys": hand_keys,
            "contacts": [c.to_json() for c in d.contacts.specs],
            "base_keys": [k.to_json() for k in d.base.keys], "clips": [c.to_json() for c in d.layers.clips],
            "layers": d.layers.settings, "secondary": d.secondary.report(), "pose_actions": actions,
            "style": d.style.to_json(), "semantic_events": d.perf.events,
            "corrective_references": {"mode": (d.perf.corrections or {}).get("mode"),
                                      "note": "only approved correctives may be exported; all are pending today"},
            "compatibility": compatibility()}


def compatibility() -> dict[str, Any]:
    return {"classes": list(COMPAT_CLASSES), "rows": COMPATIBILITY, "status": PROPOSED, "verified": [],
            "notes": ["Moho was not run: nothing here is verified natively",
                      "no Moho API names are used; no exporter is implemented in Phase 3.6"]}


# -- live performance controls ------------------------------------------------------------------------------------
LIVE_EVENTS = {
    "GESTURE_POINT": {"control": "gesture clip 'point' (UPPER_BODY_GESTURES)", "hand_switch": "pointing",
                      "priority": 30},
    "GESTURE_SHRUG": {"control": "gesture clip 'shrug_low' (palm-up shrug NEEDS_ARTWORK)", "hand_switch": None,
                      "priority": 30},
    "GESTURE_WAVE": {"control": "gesture clip 'wave'", "hand_switch": "open_palm", "priority": 30},
    "LOOK_AT_TARGET": {"control": "HEAD_MOVEMENT additive tilt + small weight shift", "hand_switch": None,
                       "priority": 20},
    "HAND_PLANT": {"control": "hand contact (CONTACT_CONSTRAINTS), C2 acquire", "hand_switch": "palm_down",
                   "priority": 40},
    "HAND_RELEASE": {"control": "end the hand contact (C2 release)", "hand_switch": "relaxed", "priority": 40},
    "RETURN_TO_IDLE": {"control": "gesture clip 'return_neutral' + idle", "hand_switch": "relaxed", "priority": 10},
}


def live_control_spec(director=None) -> dict[str, Any]:
    status = {}
    if director is not None:
        lib = director.library
        status = {"GESTURE_POINT": lib.status("point")["status"], "GESTURE_SHRUG": lib.status("shrug_low")["status"],
                  "GESTURE_WAVE": lib.status("wave")["status"]}
    return {"schema": "autorig.live_performance_controls", "version": "1.0.0", "status": PROPOSED,
            "events": {k: {**v, "autorig_status": status.get(k, "IMPLEMENTED_IN_AUTORIG")} for k, v in
                       LIVE_EVENTS.items()},
            "rules": {
                "channels": "body (pose layers, contacts, hand switches) and face (FUTURE_FACIAL_CONTROLS) are "
                            "separate; a face tracker can never move a body channel",
                "priority": "live input > artist keys > procedural gesture clips > idle; contacts outrank gestures "
                            "on their chain",
                "ik": "contacts are applied after every pose layer, so a live gesture cannot pull a planted hand off",
                "secondary": "live: secondary motion is off or uses the fixed-step spring with a short reset; it "
                             "never depends on playback history",
                "neutral": "RETURN_TO_IDLE blends every layer back to the base pose with C2 ramps",
                "smoothing": "optional critically damped smoothing of live inputs (time constant set per control)",
                "hand_switches": "triggered switches respect the 0.25 s minimum hold",
                "inference": "no cloud or AI inference during live animation: every control is a lookup or a "
                             "closed-form solve"},
            "webcam_tracking": "not implemented (later phase)", "moho_mapping": PROPOSED}


__all__ = ["animation_metadata", "compatibility", "live_control_spec", "COMPATIBILITY", "COMPAT_CLASSES"]
