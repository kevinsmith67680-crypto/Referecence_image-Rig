"""Editor project: explicit schema, operation log (event sourcing), undo/redo, versions, migration.

The base state comes from a Phase 2 character package; that package and its source are only ever read.
Every edit is an *operation*. The current state is the base state with ``ops[:cursor]`` replayed. Undo
and redo move the cursor; a new operation drops the redo tail. Each operation is persisted at once
(atomic write of ``editor/project.json``), so a crash loses nothing. Named versions are snapshots of the
operation list.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..handstore import LIBRARY_STATUS, PHASE2_LIBRARY, STORE_DIR, check_mask, check_placement, default_store_state
from ..motion.handselect import default_selection_state
from ..motion.rigctl import check_ik_fields, default_ik_state, group_variants
from ..motion.switches import HAND_POSES

SCHEMA = "autorig.editor_project"
VERSION = 6  # 2: IK and switch state (Phase 3.2); 3: Hand Pose Store decisions; 4: hand library status and
# hand selection settings (sleeve-free hand replacement); 5: garment weight edits (Phase 3.3); 6: corrective
# artwork settings (Phase 3.4). Older projects migrate unchanged
EDITOR_DIR = "editor"

# CHARACTER > groups (character's left = _l); leaves are layer ids
HIERARCHY: list[tuple[str, str | None, list[str]]] = [
    ("CHARACTER", None, []),
    ("Head", "CHARACTER", ["head_base"]),
    ("Hair", "Head", ["hair"]),
    ("Face", "Head", ["nose", "mouth"]),
    ("Right_Eye", "Face", ["eye_r_white", "eye_r_iris", "eye_r"]),
    ("Left_Eye", "Face", ["eye_l_white", "eye_l_iris", "eye_l"]),
    ("Right_Eyebrow", "Face", ["brow_r"]),
    ("Left_Eyebrow", "Face", ["brow_l"]),
    ("Torso", "CHARACTER", ["torso", "neck"]),
    ("Right_Arm", "CHARACTER", []),
    ("Right_Arm/Upper_Arm", "Right_Arm", ["upper_arm_r"]),
    ("Right_Arm/Forearm", "Right_Arm", ["forearm_r"]),
    ("Right_Arm/Hand", "Right_Arm", ["hand_r"]),
    ("Left_Arm", "CHARACTER", []),
    ("Left_Arm/Upper_Arm", "Left_Arm", ["upper_arm_l"]),
    ("Left_Arm/Forearm", "Left_Arm", ["forearm_l"]),
    ("Left_Arm/Hand", "Left_Arm", ["hand_l"]),
    ("Pelvis", "CHARACTER", ["pelvis"]),
    ("Right_Leg", "CHARACTER", []),
    ("Right_Leg/Thigh", "Right_Leg", ["thigh_r"]),
    ("Right_Leg/Shin", "Right_Leg", ["shin_r"]),
    ("Right_Leg/Foot", "Right_Leg", ["foot_r"]),
    ("Left_Leg", "CHARACTER", []),
    ("Left_Leg/Thigh", "Left_Leg", ["thigh_l"]),
    ("Left_Leg/Shin", "Left_Leg", ["shin_l"]),
    ("Left_Leg/Foot", "Left_Leg", ["foot_l"]),
]
CATEGORIES = ("original", "extracted", "reconstructed", "vector_corrected", "reference_derived", "approximation")
LAYER_FIELDS = {"name", "visible", "locked", "opacity", "role", "bone", "user_category", "edge_feather"}


class OpError(ValueError):
    pass


def _atomic_write(path: Path, data: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


def _bone_for(cid: str, bones: dict[str, Any]) -> str | None:
    base = cid.split("__")[0]
    if base in bones:
        return base
    if base in ("torso",):
        return "chest"
    if base in ("head_base", "hair", "nose", "mouth") or base.startswith(("eye_", "brow_")):
        return "head" if "head" in bones else None
    return base if base in bones else None


def build_base(pkg: Path) -> dict[str, Any]:
    """Initial editor state from a Phase 2 package (read-only)."""
    art = json.loads((pkg / "artwork" / "manifest.json").read_text())
    W, H = art["coordinate_system"]["width"], art["coordinate_system"]["height"]
    rig_path = pkg / "natural_pose" / "rig.json"
    rig = json.loads(rig_path.read_text()) if rig_path.is_file() else None
    bones = {}
    if rig:
        for b in rig["bones"]:
            bones[b["name"]] = {"head": b["head"], "tail": b["tail"], "parent": b["parent"], "kind": b["kind"],
                                "side": b["side"], "limits": {k: list(v) for k, v in b["limits_deg"].items()
                                                              if isinstance(v, (list, tuple))},
                                "band": [b["blend_band_px"]["into_parent"], b["blend_band_px"]["into_child"]],
                                "method": b["pivot_method"]}
    order = [l["id"] for l in sorted(art["layers"], key=lambda l: l["z_index"])]
    group_of = {}
    for gid, _, leaves in HIERARCHY:
        for leaf in leaves:
            group_of[leaf] = gid
    ext_by = {}
    for e in art.get("extensions", []):
        ext_by.setdefault(e["component"], []).append(e["id"])
    layers = []
    for cid in order:
        layers.append({"id": cid, "name": cid, "group": group_of.get(cid, "CHARACTER"), "visible": True,
                       "locked": False, "opacity": 1.0, "role": cid.rsplit("_", 1)[0] if cid[-2:] in ("_r", "_l")
                       else cid, "bone": _bone_for(cid, bones), "kind": "bitmap",
                       "provenance": "original" + ("+reconstructed_extension" if cid in ext_by else ""),
                       "user_category": "original", "edge_feather": 0.0, "extensions": ext_by.get(cid, []),
                       "file": f"artwork/original/{cid}.png"})
    hands = pkg / "natural_pose" / "corrective" / "hands"
    if hands.is_dir():  # hand switch drawings (corrective artwork, pending review), hidden by default
        for f in sorted(hands.glob("hand_*__*.png")):
            vid = f.stem
            side = vid.split("__")[0][-1]
            name = vid.split("__")[1]
            layers.append({"id": vid, "name": vid, "group": f"{'Right' if side == 'r' else 'Left'}_Arm/Hand",
                           "visible": False, "locked": False, "opacity": 1.0, "role": f"hand switch: {name}",
                           "bone": f"hand_{side}" if f"hand_{side}" in bones else None, "kind": "switch",
                           "provenance": "approximation" if name == "palm_up" else
                           ("reference_derived_composed" if name == "point" else "reference_derived"),
                           "user_category": "approximation" if name == "palm_up" else "reference_derived",
                           "edge_feather": 0.0, "extensions": [], "file": f"natural_pose/corrective/hands/{vid}.png"})
    groups = {gid: {"name": gid.split("/")[-1], "parent": parent} for gid, parent, _ in HIERARCHY}
    from .vectors import seed_vectors
    approvals = {}
    ap = pkg / "reports" / "approvals.json"
    if ap.is_file():
        for it in json.loads(ap.read_text()).get("items", []):
            approvals[it["id"]] = {"status": it.get("status", "pending_review"), "type": it.get("type"),
                                   "source": "phase2_approvals"}
    return {"canvas": {"width": W, "height": H, "units": "source px, origin top-left, +y down"},
            "layers": layers, "groups": groups, "bones": bones, "vectors": seed_vectors(pkg, art, W, H),
            "mask_ops": [], "corrections": {"gussets": True, "joints": {}}, "defects": {}, "approvals": approvals,
            "pose": {"preset": "relaxed_standing", "overrides": {}},
            "ik": default_ik_state(), "switches": {}, "hand_store": default_store_state(),
            "hand_selection": default_selection_state(), "garment_weights": {"ops": []},
            "correctives": default_corrective_state()}


def default_corrective_state() -> dict[str, Any]:
    """Phase 3.4 Correctives tab: preview mode and per-asset settings (enabled, blend strength, activation range).
    Approvals are ``approval.set`` on ``corrective:<asset>@v<N>``; nothing here approves anything."""
    return {"mode": "auto", "settings": {}}


CORRECTIVE_MODES = ("auto", "all", "off")


def _apply_correctives(st: dict[str, Any], op: dict[str, Any]) -> None:
    C = st.setdefault("correctives", default_corrective_state())
    t = op["type"]
    if t == "correctives.mode":
        if op.get("mode") not in CORRECTIVE_MODES:
            raise OpError(f"mode must be one of {CORRECTIVE_MODES}")
        C["mode"] = op["mode"]
    elif t == "correctives.set":
        a = op.get("asset")
        if not isinstance(a, str) or not a:
            raise OpError("correctives.set needs an asset id")
        cur = dict(C["settings"].get(a, {}))
        if "enabled" in op:
            cur["enabled"] = bool(op["enabled"])
        if "strength" in op:
            v = float(op["strength"])
            if not 0.0 <= v <= 1.0:
                raise OpError("strength must be in [0, 1]")
            cur["strength"] = v
        for k in ("lo", "hi"):
            if k in op:
                v = float(op[k])
                if not -360.0 <= v <= 360.0:
                    raise OpError(f"{k} must be an angle in degrees")
                cur[k] = v
        if "lo" in cur and "hi" in cur and cur["lo"] >= cur["hi"]:
            raise OpError("activation range: lo must be below hi")
        C["settings"][a] = cur
    elif t == "correctives.reset":
        a = op.get("asset")
        if a in (None, "*"):
            C["settings"] = {}
        else:
            C["settings"].pop(a, None)
    else:
        raise OpError(f"unknown operation type {t!r}")


WEIGHT_OPS = ("weights.paint", "weights.erase", "weights.smooth", "weights.normalise", "weights.lock",
              "weights.reset")


def _apply_weights(st: dict[str, Any], op: dict[str, Any]) -> None:
    """Garment weight edits (Phase 3.3 Weights tab). They are stored in order and replayed on the automatic weight
    meshes (``garmentrig.weights``); they never touch the source image or any layer's pixels. ``weights.reset``
    drops the edits of one layer (or all)."""
    G = st.setdefault("garment_weights", {"ops": []})
    t = op["type"]
    if t == "weights.reset":
        lay = op.get("layer")
        G["ops"] = [o for o in G["ops"] if lay not in (None, "*") and o.get("layer") != lay]
        return
    lay = op.get("layer")
    if not isinstance(lay, str) or not lay:
        raise OpError(f"{t} needs a layer id")
    rec: dict[str, Any] = {"type": t, "layer": lay}
    if t in ("weights.paint", "weights.erase", "weights.smooth", "weights.lock"):
        c = op.get("center")
        if c is not None:
            if not (isinstance(c, (list, tuple)) and len(c) == 2 and all(isinstance(x, (int, float)) for x in c)):
                raise OpError("center must be [x, y] (rest-canvas px)")
            rec["center"] = [float(c[0]), float(c[1])]
        elif t != "weights.lock":
            raise OpError(f"{t} needs a brush center")
        r = float(op.get("radius", 20.0))
        if not 1.0 <= r <= 400.0:
            raise OpError("radius must be in [1, 400] px")
        rec["radius"] = r
        for k, dflt in (("strength", 0.5), ("hardness", 0.5)):
            v = float(op.get(k, dflt))
            if not 0.0 <= v <= 1.0:
                raise OpError(f"{k} must be in [0, 1]")
            rec[k] = v
        if op.get("part"):
            rec["part"] = str(op["part"])
    if t in ("weights.paint", "weights.erase"):
        if not isinstance(op.get("bone"), str):
            raise OpError(f"{t} needs a bone")
        rec["bone"] = op["bone"]
    if t == "weights.lock":
        rec["locked"] = bool(op.get("locked", True))
    G["ops"].append(rec)


def apply_op(st: dict[str, Any], op: dict[str, Any]) -> None:
    """Apply one operation in place; raises OpError if invalid."""
    t = op.get("type")
    layers = {l["id"]: l for l in st["layers"]}

    def layer(lid):
        if lid not in layers:
            raise OpError(f"unknown layer {lid}")
        return layers[lid]

    if t == "layer.set":
        L = layer(op["id"])
        fields = op.get("fields", {})
        bad = set(fields) - LAYER_FIELDS
        if bad:
            raise OpError(f"fields not editable: {sorted(bad)}")
        if L["locked"] and set(fields) - {"locked", "visible"}:
            raise OpError(f"layer {L['id']} is locked")
        if "opacity" in fields and not 0.0 <= float(fields["opacity"]) <= 1.0:
            raise OpError("opacity must be in [0, 1]")
        if "user_category" in fields and fields["user_category"] not in CATEGORIES:
            raise OpError(f"category must be one of {CATEGORIES}")
        if "bone" in fields and fields["bone"] is not None and fields["bone"] not in st["bones"]:
            raise OpError(f"unknown bone {fields['bone']}")
        L.update(fields)
    elif t == "layer.reorder":
        L = layer(op["id"])
        st["layers"].remove(L)
        st["layers"].insert(max(0, min(int(op["to_index"]), len(st["layers"]))), L)
    elif t == "layer.duplicate":
        L = layer(op["id"])
        nid = op.get("new_id") or f"{L['id']}__copy"
        if nid in layers:
            raise OpError(f"layer {nid} exists")
        D = copy.deepcopy(L)
        D.update(id=nid, name=op.get("name", nid), duplicate_of=L["id"], visible=False)
        st["layers"].insert(st["layers"].index(L) + 1, D)
    elif t == "group.create":
        gid = op["id"]
        if gid in st["groups"]:
            raise OpError(f"group {gid} exists")
        parent = op.get("parent", "CHARACTER")
        if parent not in st["groups"]:
            raise OpError(f"unknown parent group {parent}")
        st["groups"][gid] = {"name": op.get("name", gid), "parent": parent}
        for lid in op.get("layers", []):
            layer(lid)["group"] = gid
    elif t == "group.ungroup":
        gid = op["id"]
        if gid not in st["groups"] or gid == "CHARACTER":
            raise OpError(f"cannot ungroup {gid}")
        parent = st["groups"][gid]["parent"]
        for L in st["layers"]:
            if L["group"] == gid:
                L["group"] = parent
        for g in st["groups"].values():
            if g["parent"] == gid:
                g["parent"] = parent
        del st["groups"][gid]
    elif t == "group.set":
        if op["id"] not in st["groups"]:
            raise OpError(f"unknown group {op['id']}")
        st["groups"][op["id"]].update({k: v for k, v in op.get("fields", {}).items() if k in ("name",)})
    elif t == "layer.move_to_group":
        if op["group"] not in st["groups"]:
            raise OpError(f"unknown group {op['group']}")
        layer(op["id"])["group"] = op["group"]
    elif t in ("mask.edit", "mask.reset", "mask.refine"):
        L = layer(op["layer"])
        if L["locked"]:
            raise OpError(f"layer {L['id']} is locked")
        if L["kind"] != "bitmap":
            raise OpError("masks are edited on bitmap layers only")
        if t == "mask.edit":
            if op.get("mode") not in ("add", "subtract"):
                raise OpError("mode must be add or subtract")
            if op.get("transfer_to"):
                T = layer(op["transfer_to"])
                if T["locked"] or T["kind"] != "bitmap" or T["id"] == L["id"]:
                    raise OpError("invalid transfer target")
            sh = op.get("shape", {})
            if sh.get("type") not in ("stroke", "polygon") or len(sh.get("points", [])) < (1 if sh.get("type") == "stroke" else 3):
                raise OpError("shape must be a stroke (>=1 point) or polygon (>=3 points)")
        st["mask_ops"].append(copy.deepcopy(op))
    elif t == "bone.set":
        name = op["name"]
        if name not in st["bones"]:
            raise OpError(f"unknown bone {name}")
        B = st["bones"][name]
        f = op.get("fields", {})
        for k in ("head", "tail"):
            if k in f:
                x, y = map(float, f[k])
                W, H = st["canvas"]["width"], st["canvas"]["height"]
                if not (-0.5 * W <= x <= 1.5 * W and -0.5 * H <= y <= 1.5 * H):
                    raise OpError(f"{k} far outside the canvas")
                B[k] = [round(x, 2), round(y, 2)]
                B["edited"] = True
        if "parent" in f:
            if f["parent"] is not None and f["parent"] not in st["bones"]:
                raise OpError(f"unknown parent {f['parent']}")
            if f["parent"] == name:
                raise OpError("a bone cannot be its own parent")
            B["parent"] = f["parent"]
            B["edited"] = True
        if "length" in f:  # move the tail along the bone
            hx, hy = B["head"]
            tx, ty = B["tail"]
            d = float(np.hypot(tx - hx, ty - hy)) or 1.0
            L = float(f["length"])
            B["tail"] = [round(hx + (tx - hx) / d * L, 2), round(hy + (ty - hy) / d * L, 2)]
            B["edited"] = True
        if "limits" in f:
            for key, rng in f["limits"].items():
                lo, hi = map(float, rng)
                if lo > hi:
                    raise OpError("limit min > max")
                B.setdefault("limits", {})[key] = [lo, hi]
        if "band" in f:
            rp, rc = map(float, f["band"])
            if rp <= 0 or rc <= 0:
                raise OpError("band widths must be > 0")
            B["band"] = [rp, rc]
    elif t == "bone.reset":
        st["bones"][op["name"]] = copy.deepcopy(op["base"])
    elif t == "vector.create":
        v = op["vector"]
        if v["id"] in st["vectors"]:
            raise OpError(f"vector {v['id']} exists")
        _check_vector(v)
        st["vectors"][v["id"]] = {"visible": True, "origin": "user", "status": "user_authored", **v}
    elif t == "vector.update":
        if op["id"] not in st["vectors"]:
            raise OpError(f"unknown vector {op['id']}")
        V = st["vectors"][op["id"]]
        V.update({k: v for k, v in op.get("fields", {}).items() if k not in ("id", "origin")})
        _check_vector(V)
        if V.get("origin") == "phase2_generated":
            V["edited_by_user"] = True
    elif t == "vector.delete":
        st["vectors"].pop(op["id"], None)
    elif t == "correction.set":
        j = op["joint"]
        if j not in st["bones"]:
            raise OpError(f"unknown joint/bone {j}")
        C = st["corrections"]["joints"].setdefault(j, {"per_pose": {}})
        f = {k: v for k, v in op.get("fields", {}).items() if k in ("band", "note")}
        if op.get("preset"):
            C["per_pose"].setdefault(op["preset"], {}).update(f)
        else:
            C.update(f)
    elif t == "correction.gussets":
        st["corrections"]["gussets"] = bool(op["enabled"])
    elif t == "defect.set":
        st["defects"][op["id"]] = {k: op.get(k) for k in ("joint", "preset", "status", "note")}
        if st["defects"][op["id"]]["status"] not in ("unresolved", "resolved", "accepted"):
            raise OpError("defect status must be unresolved, resolved or accepted")
    elif t == "approval.set":
        if op.get("status") not in ("approved", "rejected", "needs_review"):
            raise OpError("status must be approved, rejected or needs_review")
        prev = st["approvals"].get(op["item"], {})
        st["approvals"][op["item"]] = {**prev, "status": op["status"], "note": op.get("note", ""),
                                       "source": "editor_user", "at": op.get("at")}
    elif t == "ik.set":
        limb = op.get("limb")
        f = op.get("fields", {})
        errs = check_ik_fields(limb, f)
        if errs:
            raise OpError("; ".join(errs))
        if limb == "pelvis":
            st["ik"]["pelvis_offset"] = [float(v) for v in f["pelvis_offset"]]
        else:
            L = st["ik"][limb]
            L.update({k: ([float(x) for x in v] if k in ("target", "pole") and v is not None else v)
                      for k, v in f.items()})
            if L["enabled"] and L["mode"] == "ik" and L["target"] is None:
                raise OpError(f"{limb}: IK needs a target")
    elif t == "ik.reset":
        limb = op.get("limb", "all")
        base = default_ik_state()
        if limb == "all":
            st["ik"] = base
        elif limb in base:
            st["ik"][limb] = base[limb]
        else:
            raise OpError(f"unknown limb {limb}")
    elif t in ("switch.set", "switch.key", "switch.offset"):
        g = op.get("group", "")
        allowed = group_variants(g)
        if allowed is None:
            raise OpError(f"unknown switch group {g}")
        G = st["switches"].setdefault(g, {"user": None, "auto": True, "keys": [], "offsets": {}})
        v = op.get("variant")
        if v is not None and v not in allowed:
            raise OpError(f"{g}: unknown variant {v}")
        if t == "switch.set":
            G["user"] = v
            G["auto"] = bool(op.get("auto", v is None))
        elif t == "switch.key":
            tt = float(op["t"])
            G["keys"] = sorted([k for k in G["keys"] if abs(k[0] - tt) > 1e-9] + ([[tt, v]] if v is not None else []))
        else:
            if v is None:
                raise OpError("switch.offset needs a variant")
            dx, dy, rot = (float(op.get(k, 0.0)) for k in ("dx", "dy", "rot"))
            if max(abs(dx), abs(dy)) > 200 or abs(rot) > 180:
                raise OpError("offset out of range (|dx|,|dy| <= 200 px, |rot| <= 180 deg)")
            if op.get("scale") not in (None, 1, 1.0):
                raise OpError("switch offsets never scale artwork")
            G["offsets"][v] = {"dx": dx, "dy": dy, "rot": rot}
    elif t.startswith("handstore."):
        _apply_handstore(st, op)
    elif t in WEIGHT_OPS:
        _apply_weights(st, op)
    elif t.startswith("handselect."):
        _apply_handselect(st, op)
    elif t.startswith("correctives."):
        _apply_correctives(st, op)
    elif t == "pose.set":
        st["pose"] = {"preset": op.get("preset"), "overrides": {k: float(v) for k, v in op.get("overrides", {}).items()}}
    else:
        raise OpError(f"unknown operation type {t!r}")


CID = re.compile(r"^(original|phase2:[a-z_]{1,32}|store:[A-Za-z0-9_.-]{1,64}/[A-Za-z0-9_.-]{1,64})$")
UID = re.compile(r"^[A-Za-z0-9_.-]{1,64}/[A-Za-z0-9_.-]{1,64}$")


def _apply_handstore(st: dict[str, Any], op: dict[str, Any]) -> None:
    """User decisions about Hand Pose Store drawings (the drawings themselves live in <package>/hand_store)."""
    t = op["type"]
    H = st.setdefault("hand_store", default_store_state())
    for k, v in default_store_state().items():
        H.setdefault(k, v)
    known = op.get("_known_uids")
    if t == "handstore.library":
        lib, status = op.get("library"), op.get("status")
        if not isinstance(lib, str) or not re.match(r"^[A-Za-z0-9_.-]{1,64}$", lib):
            raise OpError("handstore.library needs a library id")
        if op.get("_known_libraries") is not None and lib not in op["_known_libraries"]:
            raise OpError(f"{lib} is not a library of this character's hand store")
        if status is None:
            H["libraries"].pop(lib, None)  # back to the default (archived when superseded)
        elif status in LIBRARY_STATUS:
            H["libraries"][lib] = status
        else:
            raise OpError(f"library status must be one of {list(LIBRARY_STATUS)} or null")
        return
    if t == "handstore.use":
        side, pose, cid = op.get("side"), op.get("pose"), op.get("candidate")
        if side not in ("r", "l") or pose not in HAND_POSES:
            raise OpError("handstore.use needs side r|l and a known hand pose")
        if cid is None:
            H["use"].pop(f"{side}/{pose}", None)
            return
        if not isinstance(cid, str) or not CID.match(cid):
            raise OpError("candidate must be original, phase2:<drawing> or store:<library>/<asset>")
        if cid == "original" and pose != "open_palm":
            raise OpError("the original hand is the open-palm pose")
        H["use"][f"{side}/{pose}"] = cid
        return
    uid = op.get("uid")
    if not isinstance(uid, str) or not UID.match(uid):
        raise OpError("uid must be <library_id>/<asset_id>")
    if known is not None and uid not in known:
        raise OpError(f"{uid} is not in this character's hand store")
    if t == "handstore.placement":
        f = op.get("fields")
        if f is None:
            H["placement"].pop(uid, None)
            return
        errs = check_placement(f)
        if errs:
            raise OpError("; ".join(errs))
        H["placement"][uid] = {**H["placement"].get(uid, {}), **{k: float(v) for k, v in f.items()}}
    elif t == "handstore.mask":
        f = op.get("fields")
        if f is None:
            H["mask"].pop(uid, None)
            return
        errs = check_mask(f)
        if errs:
            raise OpError("; ".join(errs))
        H["mask"][uid] = {**H["mask"].get(uid, {}), **f}
    elif t == "handstore.rename":
        label = str(op.get("label", "")).strip()
        if not 1 <= len(label) <= 64:
            raise OpError("label must be 1-64 characters")
        H["labels"][uid] = label
    elif t == "handstore.delete":
        if uid not in H["deleted"]:
            H["deleted"].append(uid)
        H["use"] = {k: v for k, v in H["use"].items() if v != f"store:{uid}"}
    elif t == "handstore.restore":
        H["deleted"] = [u for u in H["deleted"] if u != uid]
    elif t == "handstore.version":
        v = op.get("v")
        if v is None:
            H["versions"].pop(uid, None)
        elif not isinstance(v, int) or v < 1:
            raise OpError("version must be a positive integer")
        else:
            H["versions"][uid] = v
    else:
        raise OpError(f"unknown operation type {t!r}")


def _check_vector(v: dict[str, Any]) -> None:
    nodes = v.get("nodes", [])
    if len(nodes) < 2:
        raise OpError("a vector needs at least 2 nodes")
    for n in nodes:
        if not all(k in n for k in ("x", "y")):
            raise OpError("vector nodes need x and y")


def _v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Phase 3.2: the base state gains ``ik`` and ``switches`` (defaults come from build_base); v1 operations
    never touch them, so the log replays unchanged."""
    return {**data, "migrated_from": sorted(set(data.get("migrated_from", [])) | {1})}


def _v2_to_v3(data: dict[str, Any]) -> dict[str, Any]:
    """Hand Pose Store: the base state gains ``hand_store`` (defaults from build_base); earlier ops replay unchanged."""
    return {**data, "migrated_from": sorted(set(data.get("migrated_from", [])) | {2})}


def _v4_to_v5(data: dict[str, Any]) -> dict[str, Any]:
    """Garment rig (Phase 3.3): the base state gains ``garment_weights`` (the user's weight edits, replayed on the
    automatic weights); earlier ops replay unchanged."""
    return {**data, "migrated_from": sorted(set(data.get("migrated_from", [])) | {4})}


def _v3_to_v4(data: dict[str, Any]) -> dict[str, Any]:
    """Hand replacement: ``hand_store`` gains ``libraries`` (active/archived) and the base state gains
    ``hand_selection`` (defaults from build_base); earlier ops replay unchanged."""
    return {**data, "migrated_from": sorted(set(data.get("migrated_from", [])) | {3})}


def _v5_to_v6(data: dict[str, Any]) -> dict[str, Any]:
    """Corrective artwork (Phase 3.4): the base state gains ``correctives`` (mode + per-asset settings); earlier ops
    replay unchanged."""
    return {**data, "migrated_from": sorted(set(data.get("migrated_from", [])) | {5})}


MIGRATIONS: dict[int, Any] = {1: _v1_to_v2, 2: _v2_to_v3, 3: _v3_to_v4, 4: _v4_to_v5, 5: _v5_to_v6}  # from -> fn


def _apply_handselect(st: dict[str, Any], op: dict[str, Any]) -> None:
    """Hand selection settings (NaturalHandSelectionEngine): per-side gesture intention (``action``, overrides the
    preset's action) and its target, and per-character weight overrides. Choosing a hand drawing itself stays the
    switch groups' job (``switch.set``: explicit choice, auto toggle, reset to automatic)."""
    from ..motion.handselect import ACTIONS, WEIGHTS  # noqa: PLC0415

    H = st.setdefault("hand_selection", default_selection_state())
    for k, v in default_selection_state().items():
        H.setdefault(k, v)
    t = op["type"]
    if t == "handselect.action":
        side = op.get("side")
        if side not in ("r", "l"):
            raise OpError("handselect.action needs side r|l")
        action = op.get("action")
        if action is not None and action not in ACTIONS:
            raise OpError(f"unknown hand action {action!r}; known: {sorted(ACTIONS)}")
        tgt = op.get("target")
        if tgt is not None and (not isinstance(tgt, (list, tuple)) or len(tgt) != 2
                                or not all(isinstance(x, (int, float)) for x in tgt)):
            raise OpError("target must be [x, y] (rest-canvas px) or null")
        H["actions"][side] = action
        H["targets"][side] = None if tgt is None else [float(tgt[0]), float(tgt[1])]
    elif t == "handselect.weights":
        f = op.get("weights")
        if f is None:
            H["weights"] = {}
            return
        bad = [k for k in f if k not in WEIGHTS]
        if bad:
            raise OpError(f"unknown selection criteria {bad}; known: {sorted(WEIGHTS)}")
        if not all(isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0 for v in f.values()):
            raise OpError("weights must be numbers in [0, 1]")
        H["weights"] = {**H["weights"], **{k: float(v) for k, v in f.items()}}
    else:
        raise OpError(f"unknown operation type {t!r}")


def migrate(data: dict[str, Any]) -> dict[str, Any]:
    v = int(data.get("schema_version", 1))
    if v > VERSION:
        raise ValueError(f"project schema {v} is newer than this editor ({VERSION})")
    while v < VERSION:
        data = MIGRATIONS[v](data)
        v += 1
        data["schema_version"] = v
    return data


class EditorProject:
    def __init__(self, pkg: str | Path):
        self.pkg = Path(pkg)
        self.dir = self.pkg / EDITOR_DIR
        (self.dir / "versions").mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "project.json"
        self.base = build_base(self.pkg)
        if self.path.is_file():
            self.data = migrate(json.loads(self.path.read_text(encoding="utf-8")))
        else:
            self.data = {"schema": SCHEMA, "schema_version": VERSION, "package": self.pkg.name,
                         "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "ops": [], "cursor": 0,
                         "versions": [], "source_sha256": self._source_sha()}
            self._persist()
        self._cache: tuple[int, str, dict[str, Any]] | None = None

    def _source_sha(self) -> str:
        meta = json.loads((self.pkg / "metadata.json").read_text())
        return meta["source"]["sha256"]

    def _persist(self) -> None:
        self.data["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _atomic_write(self.path, json.dumps(self.data, indent=1))

    @property
    def ops(self) -> list[dict[str, Any]]:
        return self.data["ops"]

    @property
    def cursor(self) -> int:
        return self.data["cursor"]

    def revision(self) -> str:
        return hashlib.sha256(json.dumps(self.ops[:self.cursor], sort_keys=True).encode()).hexdigest()[:16]

    def state(self) -> dict[str, Any]:
        rev = self.revision()
        if self._cache and self._cache[1] == rev:
            return self._cache[2]
        st = copy.deepcopy(self.base)
        for op in self.ops[:self.cursor]:
            apply_op(st, op)
        self._cache = (self.cursor, rev, st)
        return st

    def submit(self, op: dict[str, Any]) -> dict[str, Any]:
        op = {**op, "at": op.get("at") or time.time()}
        if op.get("type") == "bone.reset":
            op["base"] = copy.deepcopy(self.base["bones"][op["name"]])
        trial = copy.deepcopy(self.state())
        check = op
        if str(op.get("type", "")).startswith("handstore.") and ("uid" in op or "library" in op):
            idx = self.pkg / STORE_DIR / "index.json"
            index = json.loads(idx.read_text()) if idx.is_file() else {}
            known = set(index.get("assets", {}))
            libs = {p["library_id"] for p in index.get("packs", [])} | {PHASE2_LIBRARY}
            check = {**op, "_known_uids": known, "_known_libraries": libs}  # validation only; the log keeps the op
        apply_op(trial, check)  # validate first: nothing invalid enters the log
        del self.ops[self.cursor:]
        self.ops.append(op)
        self.data["cursor"] = len(self.ops)
        self._persist()
        self._cache = None
        return self.state()

    def undo(self) -> dict[str, Any]:
        if self.cursor > 0:
            self.data["cursor"] -= 1
            self._persist()
        return self.state()

    def redo(self) -> dict[str, Any]:
        if self.cursor < len(self.ops):
            self.data["cursor"] += 1
            self._persist()
        return self.state()

    def save_version(self, label: str = "") -> dict[str, Any]:
        n = len(self.data["versions"]) + 1
        rec = {"n": n, "label": label or f"version {n}", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "cursor": self.cursor, "revision": self.revision()}
        _atomic_write(self.dir / "versions" / f"v{n}.json",
                      json.dumps({**rec, "ops": self.ops[:self.cursor]}, indent=1))
        self.data["versions"].append(rec)
        self._persist()
        return rec

    def restore_version(self, n: int) -> dict[str, Any]:
        v = json.loads((self.dir / "versions" / f"v{n}.json").read_text())
        self.data["ops"] = v["ops"]
        self.data["cursor"] = len(v["ops"])
        self._persist()
        self._cache = None
        return self.state()

    def summary(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "schema_version": VERSION, "package": self.pkg.name, "cursor": self.cursor,
                "ops": len(self.ops), "can_undo": self.cursor > 0, "can_redo": self.cursor < len(self.ops),
                "revision": self.revision(), "versions": self.data["versions"]}
