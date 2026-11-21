"""Facial artwork and live-puppeteering readiness for the editor (read from the Phase 1/2 manifests).

Statuses are never upgraded here: REQUIRES_NEW_ARTWORK stays so until the artwork exists. A user approval
records review only. Moho SHOWTIME mappings stay ``proposed_unverified`` until verified in Moho, so no
control is live-ready in Phase 3.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

FACE_LAYERS = ["head_base", "hair", "brow_l", "brow_r", "eye_l_white", "eye_l_iris", "eye_r_white", "eye_r_iris",
               "nose", "mouth"]
CAPABILITIES = [("left_eye_blink", ["eye_blink_left"]), ("right_eye_blink", ["eye_blink_right"]),
                ("eye_direction", ["eye_look_x", "eye_look_y"]),
                ("eyebrow_movement", ["brow_raise_left", "brow_raise_right", "brow_frown"]),
                ("mouth_opening", ["jaw_open"]), ("mouth_narrowing", ["mouth_width", "mouth_pucker"]),
                ("smile_frown", ["smile_left", "smile_right"]), ("head_tilt", ["head_roll"]),
                ("head_turn", ["head_yaw", "head_pitch"])]
RANK = {"REQUIRES_NEW_ARTWORK": 0, "REQUIRES_RECONSTRUCTION": 1, "NEEDS_REVIEW": 2, "READY": 3}


def _load(p: Path) -> dict[str, Any]:
    return json.loads(p.read_text()) if p.is_file() else {}


def face_panel(pkg: Path, st: dict[str, Any], masks: dict[str, np.ndarray]) -> dict[str, Any]:
    face = _load(pkg / "analysis" / "face.json")
    live = _load(pkg / "analysis" / "live_artwork_manifest.json").get("controls", {})
    lm = face.get("landmarks", {})
    anchors = {"eye_r": lm.get("eye_center_r"), "eye_l": lm.get("eye_center_l"), "pupil_r": lm.get("pupil_center_r"),
               "pupil_l": lm.get("pupil_center_l"), "brow_r": lm.get("brow_center_r"), "brow_l": lm.get("brow_center_l"),
               "jaw_pivot": face.get("jaw_pivot"), "mouth": (face.get("mouth") or {}).get("center") if isinstance(face.get("mouth"), dict) else None,
               "head_pivot": st["bones"].get("head", {}).get("head")}
    feats = []
    for c in FACE_LAYERS + ["eyelids", "jaw_chin"]:
        if c in masks:
            m = masks[c]
            ys, xs = np.nonzero(m)
            feats.append({"id": c, "present": bool(m.any()), "pixels": int(m.sum()),
                          "bbox_xyxy": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if m.any() else None,
                          "centroid": [round(float(xs.mean()), 1), round(float(ys.mean()), 1)] if m.any() else None,
                          "approval": st["approvals"].get(f"face:{c}", {}).get("status", "not_reviewed")})
        else:
            note = "no separate layer: eyelids need closed-eye drawings" if c == "eyelids" else \
                "part of head_base (jaw pivot from face analysis)" if c == "jaw_chin" else "layer missing"
            feats.append({"id": c, "present": False, "pixels": 0, "note": note,
                          "approval": st["approvals"].get(f"face:{c}", {}).get("status", "not_reviewed")})
    caps = []
    for cap, ctrls in CAPABILITIES:
        rows = [live[c] for c in ctrls if c in live]
        worst = min((r["artwork_readiness"] for r in rows), key=lambda s: RANK.get(s, 0)) if rows else "REQUIRES_NEW_ARTWORK"
        missing = sorted({m for r in rows for m in r.get("missing_assets", []) + r.get("required_switch_drawings", [])
                          if r["artwork_readiness"] != "READY"})
        caps.append({"capability": cap, "controls": ctrls, "readiness": worst, "missing": missing,
                     "approval": st["approvals"].get(f"face:{cap}", {}).get("status", "not_reviewed")})
    return {"features": feats, "anchors": anchors, "capabilities": caps,
            "rule": "statuses come from the live artwork manifest; nothing is inferred from the neutral image"}


def live_panel(pkg: Path, st: dict[str, Any], masks: dict[str, np.ndarray]) -> dict[str, Any]:
    man = _load(pkg / "analysis" / "live_artwork_manifest.json")
    lc = {c["id"]: c for c in _load(pkg / "analysis" / "live_controls.json").get("controls", [])}
    slots = {s["id"]: s for s in man.get("switch_drawing_slots", [])}
    rows = []
    for cid, c in man.get("controls", {}).items():
        req = c.get("required_image_layers", []) + c.get("required_switch_drawings", [])
        avail = [r for r in c.get("required_image_layers", []) if r in masks and masks[r].any()]
        avail += [s for s in c.get("required_switch_drawings", []) if slots.get(s, {}).get("status") not in (None, "missing")]
        missing = [r for r in req if r not in avail] + c.get("missing_assets", [])
        approval = st["approvals"].get(f"live:{cid}", {}).get("status", c.get("user_approval_status", "not_reviewed"))
        rows.append({"control": cid, "readiness": c["artwork_readiness"], "required_artwork": req,
                     "available_artwork": avail, "missing": sorted(set(missing)),
                     "proposed_control_type": lc.get(cid, {}).get("animation_target_type", "smart_bone"),
                     "user_approval": approval, "moho_mapping": "proposed_unverified",
                     "live_ready": False,
                     "why_not_live_ready": ("artwork missing" if c["artwork_readiness"] != "READY" else
                                            "needs user approval" if approval != "approved" else
                                            "Moho SHOWTIME mapping not verified (Phase 4)")})
    presets = _load(pkg / "natural_pose" / "presets.json")
    gestures = [{"gesture": n, "title": v["title"], "pose_status": v["status"], "trigger": "gamepad button (proposed)",
                 "moho_mapping": "proposed_unverified", "live_ready": False} for n, v in presets.items()]
    return {"controls": rows, "gamepad_gestures": gestures, "webcam_tracking": "not implemented in Phase 3",
            "rule": "semantic controls are separate from verified Moho SHOWTIME input mappings (none verified yet)"}
