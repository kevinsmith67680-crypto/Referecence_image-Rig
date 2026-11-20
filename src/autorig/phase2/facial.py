"""FacialReconstructionEngine support + live artwork manifest.

* Feature separation without changing the neutral image: each eye is split into
  an eye-white layer and an iris/pupil layer (both ORIGINAL pixels); the
  sclera hidden under the iris is reconstructed on the eye-white's extension.
* Skin under brows / eyes / mouth / nose and the skull under the hair are
  reconstructed by the shared engine (deterministic flat fill when flat).
* Assets a single front view cannot reveal (closed eyes, mouth interior,
  teeth/tongue, mouth shapes, turned/tilted head views) are NOT generated;
  they are recorded as required switch drawings with approval slots.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..phase1 import masks as M

SWITCH_SLOTS = {
    "eye_r_closed": ("eye_blink_right", "closed right eye drawing (eyelid down)"),
    "eye_l_closed": ("eye_blink_left", "closed left eye drawing (eyelid down)"),
    "mouth_open": ("jaw_open", "open mouth with interior, teeth and tongue"),
    "mouth_wide": ("mouth_width", "widened mouth shape"),
    "mouth_narrow": ("mouth_width", "narrowed mouth shape"),
    "mouth_pucker": ("mouth_pucker", "puckered/pursed lips"),
    "mouth_smile_left": ("smile_left", "left corner raised"),
    "mouth_smile_right": ("smile_right", "right corner raised"),
    "head_turn_left": ("head_yaw", "three-quarter view, turned left (incl. hair, ears, features)"),
    "head_turn_right": ("head_yaw", "three-quarter view, turned right"),
    "head_up": ("head_pitch", "head pitched up"),
    "head_down": ("head_pitch", "head pitched down"),
}


def split_eyes(layers: dict[str, np.ndarray], face_masks: dict[str, np.ndarray]) -> tuple[dict[str, np.ndarray], list[str]]:
    """eye_X -> eye_X_white + eye_X_iris (pixel-exact split of the original layer)."""
    notes = []
    for s in ("r", "l"):
        eye = layers.get(f"eye_{s}")
        iris = face_masks.get(f"iris_{s}")
        if eye is None:
            continue
        if iris is None:
            iris = face_masks.get(f"pupil_{s}")
        if iris is None or not (iris & (eye[..., 3] > 0)).any():
            notes.append(f"eye_{s}: no iris/pupil mask inside the eye; eye kept as one layer")
            continue
        inside = iris & (eye[..., 3] > 0)
        white, ir = eye.copy(), np.zeros_like(eye)
        ir[inside] = eye[inside]
        white[inside] = 0
        layers[f"eye_{s}_white"], layers[f"eye_{s}_iris"] = white, ir
        del layers[f"eye_{s}"]
    return layers, notes


def travel_px(inner: np.ndarray, container: np.ndarray) -> dict[str, int]:
    """How far a feature can translate while staying inside its container."""
    bb = M.bbox_xyxy(inner)
    if bb is None:
        return {"left": 0, "right": 0, "up": 0, "down": 0}
    pad = 205
    y0, x0 = max(0, bb[1] - pad), max(0, bb[0] - pad)
    inner, container = inner[y0:bb[3] + pad, x0:bb[2] + pad], container[y0:bb[3] + pad, x0:bb[2] + pad]
    res = {}
    for name, (dx, dy) in {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}.items():
        k = 0
        while k < 200:
            sh = np.roll(np.roll(inner, (k + 1) * dy, axis=0), (k + 1) * dx, axis=1)
            if (sh & ~container).any():
                break
            k += 1
        res[name] = k
    return res


def build_live_manifest(components: dict[str, np.ndarray], recon: list[dict[str, Any]], joints: dict[str, Any],
                        face: dict[str, Any], vector_assets: list[dict[str, Any]], hair: np.ndarray | None) -> dict:
    by_owner: dict[str, list[dict]] = {}
    for r in recon:
        by_owner.setdefault(r["owner"], []).append(r)

    def recon_state(owner, needle):
        rs = [r for r in by_owner.get(owner, []) if needle in r["region"]]
        if not rs:
            return "missing"
        return "pending_review" if all(r["extensions"] for r in rs) else "incomplete"

    def status_from(parts: list[str]) -> str:
        if "missing_switch" in parts:
            return "REQUIRES_NEW_ARTWORK"
        if "missing" in parts or "incomplete" in parts:
            return "REQUIRES_RECONSTRUCTION"
        if "pending_review" in parts:
            return "NEEDS_REVIEW"
        return "READY"

    vec_by_target = {}
    for v in vector_assets:
        vec_by_target.setdefault(v["target"], []).append(v["id"])
    controls: dict[str, dict[str, Any]] = {}

    def add(cid, layers, switches, region, rng, states, missing, vectors=()):
        controls[cid] = {
            "required_image_layers": [l for l in layers if l in components] or layers,
            "missing_layers": [l for l in layers if l not in components],
            "optional_vector_layers": list(vectors), "required_switch_drawings": switches,
            "pivot_or_deformation_region": region, "safe_range": rng,
            "artwork_readiness": status_from(states + (["missing_switch"] if switches else [])),
            "missing_assets": missing, "user_approval_status": "not_reviewed",
        }

    nj = joints.get("neck", {})
    head = ["head_base", "hair", "nose", "mouth", "brow_r", "brow_l", "eye_r_white", "eye_r_iris", "eye_l_white", "eye_l_iris"]
    for cid, sw in (("head_yaw", ["head_turn_left", "head_turn_right"]), ("head_pitch", ["head_up", "head_down"])):
        add(cid, head, sw, {"pivot": nj.get("pivot"), "joint": "neck"}, {"degrees": 0, "note": "needs turn views"},
            [], [SWITCH_SLOTS[s][1] for s in sw])
    add("head_roll", head + ["neck"], [], {"pivot": nj.get("pivot"), "joint": "neck"},
        {"degrees": nj.get("safe_range_deg", {}).get("with_extensions"), "basis": "pose sweep gap test"},
        [recon_state("neck", "under__head_base")], [] if recon_state("neck", "under__head_base") != "missing"
        else ["neck extension under the chin"], vec_by_target.get("neck", []))
    for side, lr in (("l", "left"), ("r", "right")):
        add(f"eye_blink_{lr}", [f"eye_{side}_white", f"eye_{side}_iris"], [f"eye_{side}_closed"],
            {"region_bbox": M.bbox_xyxy(components[f"eye_{side}_white"]) if f"eye_{side}_white" in components else None},
            {"value": [0, 1]}, [], [SWITCH_SLOTS[f"eye_{side}_closed"][1]], vec_by_target.get(f"eye_{side}", []))
    for axis, keys in (("x", ("left", "right")), ("y", ("up", "down"))):
        states, travel = [], {}
        for s in ("r", "l"):
            if f"eye_{s}_iris" in components and f"eye_{s}_white" in components:
                container = (components[f"eye_{s}_white"] | components[f"eye_{s}_iris"])
                travel[s] = {k: v for k, v in travel_px(M.erode(components[f"eye_{s}_iris"], 0),
                                                        M.erode(container, 4)).items() if k in keys}
                states.append(recon_state(f"eye_{s}_white", "under__eye"))
            else:
                states.append("missing")
        add(f"eye_look_{axis}", [f"eye_{s}_{p}" for s in ("r", "l") for p in ("white", "iris")], [],
            {"pupil_centers": {s: face.get("landmarks", {}).get(f"pupil_center_{s}") for s in ("r", "l")}},
            {"pixels": travel, "basis": "iris translation that stays inside the eye (4 px margin)"}, states,
            ["sclera under the iris (reconstructed, pending review)"] if "pending_review" in states else [])
    for side, lr in (("l", "left"), ("r", "right")):
        b = components.get(f"brow_{side}")
        up = None
        if b is not None and hair is not None:
            up = travel_px(b, ~hair & ~M.dilate(b, 0) | b).get("up")
        add(f"brow_raise_{lr}", [f"brow_{side}", "head_base"], [],
            {"region_bbox": M.bbox_xyxy(b) if b is not None else None},
            {"pixels_up_before_hair": up}, [recon_state("head_base", f"under__brow_{side}")],
            ["skin under the brow (reconstructed, pending review)"])
    add("brow_frown", ["brow_r", "brow_l", "head_base"], [], {"note": "inner brow ends move down/in"},
        {"note": "translation/rotation of brow layers"},
        [recon_state("head_base", "under__brow_r"), recon_state("head_base", "under__brow_l")],
        ["skin under both brows (reconstructed, pending review)"])
    mouth_bbox = M.bbox_xyxy(components["mouth"]) if "mouth" in components else None
    for cid, sw in (("jaw_open", ["mouth_open"]), ("mouth_width", ["mouth_wide", "mouth_narrow"]),
                    ("mouth_pucker", ["mouth_pucker"]), ("smile_left", ["mouth_smile_left"]),
                    ("smile_right", ["mouth_smile_right"])):
        add(cid, ["mouth", "head_base"], sw, {"region_bbox": mouth_bbox, "jaw_pivot": face.get("jaw_pivot")},
            {"value": [0, 1]}, [recon_state("head_base", "under__mouth")], [SWITCH_SLOTS[s][1] for s in sw])
    switch_slots = [{"id": k, "control": v[0], "description": v[1], "status": "missing",
                     "source": None, "approval": "not_reviewed",
                     "rule": "generated drawings must be labelled generated and approved; never 'original'"}
                    for k, v in SWITCH_SLOTS.items()]
    return {"controls": controls, "switch_drawing_slots": switch_slots,
            "software_mappings": "kept separate: see analysis/live_controls.json -> moho_showtime (proposed only)"}
