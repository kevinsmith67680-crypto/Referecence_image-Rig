"""Phase 3.7 hand asset metadata and palm orientation (``HandAssetMetadata`` v1, ``HandOrientationAnalyzer``).

Why: the Phase 3.6 shrug preview asked for "cupped" hands because of a file name. Nothing checked which hand a
drawing is, which way its palm faces, or whether turning it to fit the arm changes what it shows. This module records,
per drawing, what the artwork actually shows, and analyses what a placed drawing shows in a pose.

Every field records its source:
- ``measured``: computed from the pixels or from the placement, e.g. the wrist opening, the wrist axis, feature
  directions, sprite size, the rest placement angle and scale;
- ``inspected``: the semantic inspection record (``semantic_inspection.json``, written after looking at the sprites).
  This covers handedness, the visible surface, the palm normal, the thumb and the rotation tolerance. The inspection
  was done by the assistant, not by a person;
- ``user``: an editor correction (project op ``handmeta.set``). Corrections win over both.

File names are never used as evidence: the store's pose label is kept as ``label_pose`` and compared with the
observed gesture.

The palm normal is a 3D unit vector in sprite axes: x is image right, y is image down, z points toward the viewer.
Placing a drawing on the rig is a flat, in-plane rotation R(theta). It rotates the x and y components of the normal
and leaves z unchanged. A palm facing the camera therefore stays facing the camera at any angle; only a different
drawing makes it palm-up. ``HandOrientationAnalyzer`` reports that as unreachable (NEEDS_ARTWORK), never as a fit.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

SCHEMA = "autorig.hand_asset_metadata"
SCHEMA_VERSION = 1
METADATA_FILE = "analysis/hand_asset_metadata.json"
PALM_CLASSES = ("PALM_TO_CAMERA", "BACK_TO_CAMERA", "PALM_UP", "PALM_DOWN", "EDGE_ON", "AMBIGUOUS")
APPROVAL_STATES = ("APPROVED", "PENDING_REVIEW", "REJECTED", "MISSING")
CONFIDENCE = ("high", "medium", "low")
SURFACES = ("palm", "back", "palm_side_curled", "edge")
CORRECTABLE = {
    # editor corrections (handmeta.set): field -> validator
    "handedness": lambda v: v in ("left", "right"),
    "gesture": lambda v: isinstance(v, str) and 0 < len(v) <= 40,
    "visible_surface": lambda v: v in SURFACES,
    "palm_normal_sprite": lambda v: isinstance(v, (list, tuple)) and len(v) == 3 and
    all(isinstance(x, (int, float)) for x in v) and 0.5 < math.hypot(*v) < 1.5,
    "rotation_tolerance_deg": lambda v: isinstance(v, (int, float)) and 0 <= v <= 180,
    "view_elevation": lambda v: v in ("level", "from_above", "from_below"),
    "thumb": lambda v: isinstance(v, str) and len(v) <= 20,
    "finger_dir_deg": lambda v: isinstance(v, (int, float)) and -180 <= v <= 180,
    "excluded": lambda v: isinstance(v, bool),
    "note": lambda v: isinstance(v, str) and len(v) <= 500,
}
SIDE_NAME = {"r": "right", "l": "left"}
NAME_SIDE = {"right": "r", "left": "l"}
CLASS_THRESHOLD = 0.6  # a normal component must dominate by this much for a class; below it the view is EDGE_ON
HIDDEN_Z = 0.3  # a palm turned away from the camera beyond this cannot read as palm-up
FOREARM_MIN_OFF_VERTICAL_DEG = 30.0  # palm up/down needs the forearm this far from hanging (front view, in-plane)


def _wrap(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def valid_normal(n: Any) -> bool:
    return CORRECTABLE["palm_normal_sprite"](n)


def approval_state(status: str | None) -> str:
    """Engine status -> approval state. The original reference hand is APPROVED by definition (category A original
    pixels); a drawing becomes APPROVED only through ``approval.set``."""
    return {"approved": "APPROVED", "original": "APPROVED", "pending_review": "PENDING_REVIEW",
            "rejected": "REJECTED"}.get(status or "missing", "MISSING")


# -- orientation ------------------------------------------------------------------------------------------------
def rotate(n: list[float] | tuple[float, ...], theta_deg: float) -> np.ndarray:
    """In-plane rotation of a sprite-axes vector by ``theta`` in the rig's 'ang' convention (sprite-down -> the
    world direction ``theta`` deg from straight down, + toward image left). z is unchanged."""
    t = math.radians(theta_deg)
    c, s = math.cos(t), math.sin(t)
    x, y = float(n[0]), float(n[1])
    z = float(n[2]) if len(n) > 2 else 0.0
    return np.array([c * x - s * y, s * x + c * y, z])


def classify(n: np.ndarray | None) -> tuple[str, dict[str, float]]:
    """Palm class of a world palm normal (front camera). ``up`` = -y (image up), ``cam`` = +z."""
    if n is None:
        return "AMBIGUOUS", {}
    n = np.asarray(n, float)
    L = float(np.linalg.norm(n))
    if L < 1e-6:
        return "AMBIGUOUS", {}
    n = n / L
    comp = {"camera": float(n[2]), "up": float(-n[1]), "lateral": float(n[0])}
    best = max(("PALM_TO_CAMERA", n[2]), ("BACK_TO_CAMERA", -n[2]), ("PALM_UP", -n[1]), ("PALM_DOWN", n[1]),
               key=lambda kv: kv[1])
    if best[1] < CLASS_THRESHOLD:
        return "EDGE_ON", comp
    # palm up only reads when the palm is not turned away from the viewer (otherwise the drawing shows the back of
    # the hand from below); palm down only when the back is not hidden
    if best[0] == "PALM_UP" and comp["camera"] < -HIDDEN_Z:
        return "AMBIGUOUS", comp
    if best[0] == "PALM_DOWN" and comp["camera"] > HIDDEN_Z and comp["up"] > -0.85:
        return "PALM_DOWN", comp
    return best[0], comp


class HandOrientationAnalyzer:
    """What a placed drawing shows: palm class in character space for a front camera, rotation from the drawn
    orientation, and whether the arm pose can carry the required palm at all.

    Image-space finger direction is not the anatomical palm direction: a hand with its fingers pointing sideways can
    still have its palm facing the camera. The class comes from the palm normal, which a flat rotation cannot tip out
    of the image plane."""

    def __init__(self, camera: str = "front"):
        self.camera = camera

    def analyze(self, meta: dict[str, Any] | None, rotation_deg: float, forearm_deg: float | None = None
                ) -> dict[str, Any]:
        out: dict[str, Any] = {"camera": self.camera, "rotation_from_drawn_deg": round(float(_wrap(rotation_deg)), 2)}
        if self.camera != "front":
            out.update(palm_class="AMBIGUOUS", notes=[f"no palm model for camera '{self.camera}'"], within_tolerance=False)
            return out
        n0 = (meta or {}).get("palm_normal_sprite")
        if not meta or n0 is None or not valid_normal(n0):
            out.update(palm_class="AMBIGUOUS", normal=None, tolerance_deg=None, within_tolerance=None,
                       notes=["no valid palm metadata for this drawing (it cannot satisfy a palm requirement)"])
            return out
        n = rotate(n0, rotation_deg)
        cls, comp = classify(n)
        tol = float(meta.get("rotation_tolerance_deg", 180.0))
        rot = abs(_wrap(rotation_deg))
        out.update(palm_class=cls, normal=[round(float(x), 3) for x in n], components={k: round(v, 3) for k, v in comp.items()},
                   drawn_class=classify(np.asarray(n0, float))[0], tolerance_deg=tol, within_tolerance=rot <= tol + 1e-9,
                   notes=[])
        if out["within_tolerance"] is False:
            out["notes"].append(f"turned {rot:.0f} deg from how it was drawn (tolerance {tol:.0f} deg)")
        if forearm_deg is not None:
            out["forearm_off_vertical_deg"] = round(abs(_wrap(forearm_deg)), 1)
        return out

    @staticmethod
    def reachable_by_rotation(meta: dict[str, Any] | None, required: str) -> bool:
        """Can any flat rotation of this drawing show ``required``? Camera-facing normals never become palm up/down,
        and an edge-on view never faces the camera."""
        n0 = (meta or {}).get("palm_normal_sprite")
        if n0 is None or not valid_normal(n0):
            return False
        z = float(n0[2]) / max(float(np.linalg.norm(n0)), 1e-9)
        inplane = math.sqrt(max(0.0, 1.0 - z * z))
        if required == "PALM_UP":
            return inplane >= CLASS_THRESHOLD and z >= -HIDDEN_Z
        if required == "PALM_DOWN":
            return inplane >= CLASS_THRESHOLD
        if required == "PALM_TO_CAMERA":
            return z >= CLASS_THRESHOLD
        if required == "BACK_TO_CAMERA":
            return -z >= CLASS_THRESHOLD
        if required == "EDGE_ON":
            return abs(z) < CLASS_THRESHOLD
        return False

    @staticmethod
    def arm_allows(required: str, forearm_deg: float | None) -> tuple[bool, str | None]:
        """Front view, forearm in the image plane: the palm normal is perpendicular to the forearm. Palm up/down
        needs the forearm off vertical (a hanging forearm cannot hold its palm up without a 90 deg wrist bend)."""
        if forearm_deg is None or required not in ("PALM_UP", "PALM_DOWN"):
            return True, None
        off = abs(_wrap(forearm_deg))
        off = min(off, 180.0 - off)
        if off < FOREARM_MIN_OFF_VERTICAL_DEG:
            return False, (f"the forearm is {off:.0f} deg from vertical (< {FOREARM_MIN_OFF_VERTICAL_DEG:.0f}): "
                           f"{required.lower()} is not anatomically available in this arm pose")
        return True, None


# -- inventory --------------------------------------------------------------------------------------------------
def _ang(v) -> float:
    return math.degrees(math.atan2(-float(v[0]), float(v[1])))


def _wrist_side(axis) -> str:
    """Where the opening is in the sprite: the axis points from the opening into the hand."""
    ax, ay = float(axis[0]), float(axis[1])
    if abs(ay) >= abs(ax):
        return "top" if ay > 0 else "bottom"
    return "right" if ax < 0 else "left"


def _mirror_inspection(c: dict[str, Any]) -> dict[str, Any]:
    m = copy.deepcopy(c)
    m["handedness"] = {"left": "right", "right": "left"}.get(c.get("handedness"), c.get("handedness"))
    n = list(c.get("palm_normal_sprite") or [0, 0, 1])
    m["palm_normal_sprite"] = [-n[0], n[1], n[2]]
    if c.get("finger_dir_deg") is not None:
        m["finger_dir_deg"] = -float(c["finger_dir_deg"])
    m["thumb"] = {"image_left": "image_right", "image_right": "image_left"}.get(c.get("thumb"), c.get("thumb"))
    m["cues"] = "horizontal mirror of the inspected cell: " + str(c.get("cues", ""))
    return m


def load_inspection(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def _measured_store(v, geom: dict[str, Any]) -> dict[str, Any]:
    pl = v.placement or {}
    prm = pl.get("params") or {}
    fin = pl.get("final") or {}
    ax = geom.get("wrist_axis")
    edge = geom.get("wrist_edge_px")
    feats = {k: [round(float(x), 4) for x in val] for k, val in (geom.get("features") or {}).items()
             if k.endswith("_dir")}
    m = {"wrist": {"opening": geom.get("wrist_opening"), "method": geom.get("wrist_method"),
                   "confidence": geom.get("wrist_confidence"), "manual_review": bool(geom.get("manual_review")),
                   "edge_px": edge, "axis_sprite": ax, "side": _wrist_side(ax) if ax else None,
                   "centre_px": ([round((edge[0][0] + edge[1][0]) / 2, 2), round((edge[0][1] + edge[1][1]) / 2, 2)]
                                 if edge else None),
                   "width_px": round(math.dist(edge[0], edge[1]), 2) if edge else None},
         "drawn_forearm_deg": round(_ang(ax), 2) if ax else None,
         "features": feats, "sprite_px": None, "rest_placement": {
             "side": v.side, "angle_deg": round(float(fin["angle_deg"]), 2) if fin.get("angle_deg") is not None else None,
             "scale": fin.get("scale"), "wrist_fit": prm.get("wrist_fit")}}
    return m


def build_inventory(eng, store=None, inspection: dict[str, Any] | None = None) -> dict[str, Any]:
    """Base metadata (measured + inspected) for every hand candidate of the engine's library: the original hands,
    Phase 2 sheet hands when active, and every active store drawing. ``store``: a HandStore (sprite sizes)."""
    insp = inspection or {}
    cells = insp.get("cells", {})
    assets: dict[str, dict[str, Any]] = {}
    lib = eng.hand_lib
    for s in ("r", "l"):
        for pose, cands in lib.candidates[s].items():
            for v in cands:
                if v.layer is None:
                    continue
                if v.cid == "original":
                    key = f"original:{s}"
                    c = (insp.get("original") or {}).get(s)
                    hb = eng.sk.bone(f"hand_{s}")
                    meas = {"wrist": {"opening": "reference_cuff", "side": None, "axis_sprite": None,
                                      "manual_review": False},
                            "drawn_forearm_deg": round(_wrap(hb.rest_angle), 2), "features": {},
                            "rest_placement": {"side": s, "angle_deg": 0.0, "scale": 1.0, "wrist_fit": None},
                            "sprite_px": None}
                    prov = {"origin": "ORIGINAL", "source": "reference image (original pixels)", "derived": None}
                    rec = {"key": key, "cid": "original", "uid": None, "version": None, "library_id": None,
                           "store_side": s, "label_pose": pose, "approval_item": None}
                elif v.cid.startswith("store:"):
                    uid = v.cid[len("store:"):]
                    key = uid
                    if key in assets:
                        continue
                    geom = {}
                    vv = None
                    if store is not None:
                        try:
                            vv = store.version(uid, (v.provenance or {}).get("version"))
                            geom = dict(vv.get("geometry") or {})
                        except (KeyError, ValueError):
                            geom = {}
                    meas = _measured_store(v, geom)
                    if vv is not None:
                        meas["sprite_px"] = list(vv.get("size") or [])
                    cell = geom.get("sheet_cell") or (v.provenance or {}).get("sheet_cell")
                    c = cells.get(cell) if cell else None
                    if c is not None and geom.get("derived") == "mirror_x":
                        c = _mirror_inspection(c)
                    prov = {"origin": "AI_GENERATED", "source": (v.provenance or {}).get("artwork_source"),
                            "sheet_cell": cell, "derived": geom.get("derived"), "sha256": (v.provenance or {}).get("sha256"),
                            "extractor_handedness": geom.get("handedness")}
                    rec = {"key": key, "cid": v.cid, "uid": uid, "version": (v.provenance or {}).get("version"),
                           "library_id": (v.meta or {}).get("library_id"),
                           "store_side": (v.meta or {}).get("stored_side") or s, "label_pose": pose,
                           "approval_item": v.approval_item}
                else:  # phase2 sheet hands (archived while a sleeve-free library is active)
                    key = f"{v.cid}:{s}"
                    c = None
                    meas = {"wrist": {"opening": "sleeve_attached"}, "features": {}, "rest_placement": {"side": s}}
                    prov = {"origin": "PHASE2_SHEET", "source": (v.provenance or {}).get("source")}
                    rec = {"key": key, "cid": v.cid, "uid": None, "version": None, "library_id": "phase2_sheet_hands",
                           "store_side": s, "label_pose": pose, "approval_item": v.approval_item}
                inspected = c or {}
                hand = inspected.get("handedness")
                src = {k: "inspected" for k in ("handedness", "gesture", "visible_surface", "palm_normal_sprite",
                                                 "rotation_tolerance_deg", "view_elevation", "thumb", "finger_dir_deg")
                       if inspected.get(k) is not None}
                rec.update({
                    "schema": SCHEMA, "schema_version": SCHEMA_VERSION,
                    "gesture": inspected.get("gesture"),
                    "handedness": hand, "handedness_confidence": inspected.get("confidence"),
                    "handedness_cues": inspected.get("cues"),
                    "visible_surface": inspected.get("visible_surface"),
                    "palm_normal_sprite": inspected.get("palm_normal_sprite"),
                    "view_elevation": inspected.get("view_elevation"),
                    "finger_dir_deg": inspected.get("finger_dir_deg"),
                    "thumb": inspected.get("thumb"),
                    "rotation_tolerance_deg": inspected.get("rotation_tolerance_deg"),
                    "gesture_semantics": inspected.get("gesture_semantics"),
                    "viewing_direction": "front",
                    "measured": meas, "provenance": prov,
                    "approval": {"state": approval_state(v.status), "item": v.approval_item},
                    "field_sources": src, "excluded": False, "note": None,
                })
                assets[key] = rec
    out = {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "inspection": {
        "inspected_by": insp.get("inspected_by"), "inspected_at": insp.get("inspected_at"),
        "sheet_sha256": insp.get("sheet_sha256")}, "assets": assets}
    for rec in assets.values():
        finish(rec)
    return out


def finish(rec: dict[str, Any]) -> dict[str, Any]:
    """Derived fields: use side, label agreement, consistency checks and flags (recomputed after corrections)."""
    flags: list[str] = []
    hand = rec.get("handedness")
    store_side = rec.get("store_side")
    use = NAME_SIDE.get(hand, store_side) if hand else store_side
    rec["use_side"] = use
    rec["reassigned"] = use != store_side
    if rec["reassigned"]:
        flags.append(f"drawn as a {hand} hand but stored for the {SIDE_NAME.get(store_side)} side: offered on the "
                     f"{SIDE_NAME.get(use)} side only")
    g, lp = rec.get("gesture"), rec.get("label_pose")
    rec["label_agrees"] = (g is None) or (g == lp)
    if g is not None and g != lp:
        flags.append(f"label says {lp!r}, the drawing shows {g!r}")
    rp = ((rec.get("measured") or {}).get("rest_placement") or {}).get("angle_deg")
    if rp is not None and rec.get("cid", "").startswith("store:"):
        rec["rest_rotation_from_drawn_deg"] = rp
        tol = rec.get("rotation_tolerance_deg")
        if tol is not None and abs(_wrap(rp)) > 135 and float(tol) < 180:
            flags.append(f"at rest the drawing is placed {abs(_wrap(rp)):.0f} deg from how it was drawn on its stored "
                         "side (it extends toward the body): handedness or side assignment is wrong")
    if (rec.get("measured") or {}).get("wrist", {}).get("manual_review"):
        flags.append("wrist opening is a closed outline: pivot needs manual review")
    if rec.get("palm_normal_sprite") is None:
        flags.append("no palm metadata: the drawing cannot satisfy a palm requirement")
    if (rec.get("provenance") or {}).get("derived"):
        flags.append(f"derived copy ({rec['provenance']['derived']}) of another drawing")
    rec["flags"] = flags
    rec["errors"] = validate(rec)
    return rec


def validate(rec: dict[str, Any]) -> list[str]:
    """Schema checks (types, ranges). Invalid metadata never passes a hard constraint it is needed for."""
    errs: list[str] = []
    if rec.get("schema") != SCHEMA or rec.get("schema_version") != SCHEMA_VERSION:
        errs.append("schema/schema_version mismatch")
    if rec.get("handedness") is not None and rec["handedness"] not in ("left", "right"):
        errs.append(f"handedness {rec['handedness']!r} must be 'left' or 'right'")
    if rec.get("handedness_confidence") is not None and rec["handedness_confidence"] not in CONFIDENCE:
        errs.append(f"handedness_confidence {rec['handedness_confidence']!r} unknown")
    if rec.get("visible_surface") is not None and rec["visible_surface"] not in SURFACES:
        errs.append(f"visible_surface {rec['visible_surface']!r} unknown")
    n = rec.get("palm_normal_sprite")
    if n is not None and not CORRECTABLE["palm_normal_sprite"](n):
        errs.append("palm_normal_sprite must be three numbers with length ~1")
    t = rec.get("rotation_tolerance_deg")
    if t is not None and not CORRECTABLE["rotation_tolerance_deg"](t):
        errs.append("rotation_tolerance_deg must be within [0, 180]")
    if (rec.get("approval") or {}).get("state") not in APPROVAL_STATES:
        errs.append("approval.state unknown")
    return errs


def apply_corrections(base: dict[str, Any], corrections: dict[str, Any] | None) -> dict[str, Any]:
    """Resolved inventory: base metadata + user corrections (project state ``hand_metadata.corrections``:
    key -> {field: value})."""
    out = copy.deepcopy(base)
    for key, fields in (corrections or {}).items():
        rec = out["assets"].get(key)
        if rec is None:
            continue
        for f, val in fields.items():
            rec[f] = val
            rec.setdefault("field_sources", {})[f] = "user"
        if "handedness" in fields:
            rec["handedness_confidence"] = "high"
        finish(rec)
    return out


def check_correction(field: str, value: Any) -> str | None:
    fn = CORRECTABLE.get(field)
    if fn is None:
        return f"field {field!r} cannot be corrected (allowed: {', '.join(sorted(CORRECTABLE))})"
    if value is not None and not fn(value):
        return f"invalid value for {field}: {value!r}"
    return None


def load(pkg: str | Path) -> dict[str, Any] | None:
    f = Path(pkg) / METADATA_FILE
    return json.loads(f.read_text(encoding="utf-8")) if f.is_file() else None


def save(pkg: str | Path, inv: dict[str, Any]) -> Path:
    f = Path(pkg) / METADATA_FILE
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(inv, indent=1, default=float) + "\n", encoding="utf-8")
    return f


def side_map(inv: dict[str, Any] | None) -> dict[str, str]:
    """Store uid -> side it is offered on (only reassigned drawings)."""
    out = {}
    for rec in (inv or {}).get("assets", {}).values():
        if rec.get("uid") and rec.get("reassigned") and not rec.get("excluded"):
            out[rec["uid"]] = rec["use_side"]
    return out


def attach(eng, inv: dict[str, Any] | None) -> int:
    """Put each candidate's resolved metadata on its HandVariant (``meta['hand_meta']``). Returns the count."""
    if not inv:
        return 0
    n = 0
    A = inv.get("assets", {})
    for s in ("r", "l"):
        for cands in eng.hand_lib.candidates[s].values():
            for v in cands:
                if v.cid == "original":
                    rec = A.get(f"original:{s}")
                elif v.cid.startswith("store:"):
                    rec = A.get(v.cid[len("store:"):])
                else:
                    rec = A.get(f"{v.cid}:{s}")
                if rec is not None:
                    rec = {**rec, "approval": {"state": approval_state(v.status), "item": v.approval_item},
                           "placed_side": s}
                    v.meta = {**(v.meta or {}), "hand_meta": rec}
                    n += 1
    return n


def summary(inv: dict[str, Any]) -> dict[str, Any]:
    A = list(inv.get("assets", {}).values())
    return {"assets": len(A), "reassigned": [a["key"] for a in A if a.get("reassigned")],
            "label_disagrees": [a["key"] for a in A if not a.get("label_agrees", True)],
            "with_errors": [a["key"] for a in A if a.get("errors")],
            "approval": {s: sum(1 for a in A if (a.get("approval") or {}).get("state") == s) for s in APPROVAL_STATES},
            "palm_up_capable": [a["key"] for a in A if HandOrientationAnalyzer.reachable_by_rotation(a, "PALM_UP")]}


__all__ = ["HandOrientationAnalyzer", "PALM_CLASSES", "APPROVAL_STATES", "approval_state", "build_inventory",
           "apply_corrections", "validate", "classify", "rotate", "side_map", "attach", "load", "save", "summary",
           "check_correction", "SCHEMA", "SCHEMA_VERSION", "METADATA_FILE", "load_inspection"]
