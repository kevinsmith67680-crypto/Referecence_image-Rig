"""Phase 4.2: the hybrid native rig, i.e. the Phase 4.1 skeleton with joint-aware artwork layers.

Phase 4.1 bound every Phase 1 layer rigidly to one bone. The Phase 4.2 audit (``deform_audit.py``) traced the visible
defects to the layers, not to the skeleton. This module assembles the layer set per *variant*, so each fix can be
measured before it is adopted:

- ``split``: a limb root layer (upper arm, thigh) loses the pixels that lie behind its pivot plane (the sleeve cap,
  the trouser hip). They go to a derived layer bound to the parent bone (SHOULDER, PELVIS), so they no longer swing
  with the limb. Pixels past the far joint (the elbow, the knee) go to the next bone. A forearm or shin is split the
  same way only when its measured cut lies well behind its pivot. Detached specks of a limb layer
  go to the neighbouring layer they touch. It is an exact partition of the original pixels: nothing is duplicated,
  lost or scaled.
- ``order``: each arm chain is drawn above the torso, as in the original drawing's front view. The original layers
  partition the source, so the neutral composite does not change.
- ``extensions`` (review only): the pending Phase 2 reconstruction beneath each original layer, labelled pending.
- ``correctives`` (review only): the pending Phase 3.4 elbow / knee caps, armpit gusset keys and shoe tops as
  underlays clipped to the rest silhouette, so they never show at rest. Which one shows in a pose is a Smart Bone
  action in Moho (``smartbone_plan``); the simulation applies the same activation rule. Never approved here.

Every native operation the hybrid rig needs is one Phase 4.0 ran in Moho (image layers, rigid layer binding, angle
keys). Smart Bone actions, flexible (multi-bone) image binding and Switch Layers are not evidenced in Lua. They are
planned in ``smartbone_plan`` with a manual Moho procedure, and never automated by guessing.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..geometry import BonePose, compute_rest_pose, layer_delta_px
from ..spec import RigSpec, parse_spec
from .binding import NativeBindingStrategy
from .skeleton import NativeSkeletonSpec, Placement, _sign_out, _sign_up

VARIANTS = {
    "rigid_4_1": (),
    "order": ("order",),
    "split": ("split",),
    "hybrid": ("split", "order", "approved"),
    "hybrid_review": ("split", "order", "extensions"),
    "hybrid_correctives": ("split", "order", "correctives"),
    "hybrid_review_all": ("split", "order", "extensions", "correctives"),
}
NATIVE_STEPS = ("split", "order", "approved")  # automated natively (Phase 4.0 operations); approved art only
REVIEW_STEPS = ("extensions", "correctives")  # pending generated pixels: review builds only, labelled
ROOT_LIMBS = {"UPPER_ARM": ("SHOULDER", "FOREARM"), "THIGH": ("PELVIS", "SHIN")}
AUDIT_PAD = 320


@dataclass
class HLayer:
    name: str
    rgba: np.ndarray            # source canvas (character space), uint8 RGBA
    bone: str
    z: float
    binding_class: str
    status: str                 # original | derived_partition | pending_extension | pending_corrective
    source_layer: str
    reason: str
    extra: dict[str, Any] = field(default_factory=dict)


# -- the 4.2 test poses -----------------------------------------------------------------------------------------

def poses_42(sk: NativeSkeletonSpec) -> list[dict[str, Any]]:
    """The ten poses the Phase 4.2 request lists. Signs come from each bone's own direction (any character).
    Knee bends are in-plane mechanical tests: real knee flexion on front-view art is depth motion
    (REQUIRES_NEW_VIEW) and is never imitated by rotation."""
    b = sk.bone
    up = {S: _sign_up(b(f"{S}_UPPER_ARM")) for S in ("LEFT", "RIGHT")}
    out = {S: _sign_out(b(f"{S}_THIGH"), sk.midline_x) for S in ("LEFT", "RIGHT")}
    knee = lambda S: {f"{S}_THIGH": 15 * out[S], f"{S}_SHIN": -30 * out[S], f"{S}_FOOT": 15 * out[S]}  # noqa: E731
    rows = [
        (1, "neutral", "Neutral pose", {}),
        # no clavicle drop: the sleeve cap kept on the clavicle would open a crack along the lapel (audit)
        (10, "both_arms_lowered", "Both arms lowered",
         {"LEFT_UPPER_ARM": -65 * up["LEFT"], "RIGHT_UPPER_ARM": -65 * up["RIGHT"]}),
        (20, "left_arm_raised", "Left arm raised", {"LEFT_UPPER_ARM": 60 * up["LEFT"]}),
        (30, "right_arm_raised", "Right arm raised", {"RIGHT_UPPER_ARM": 60 * up["RIGHT"]}),
        (40, "left_elbow_90", "Left elbow at 90 deg", {"LEFT_FOREARM": 90 * up["LEFT"]}),
        (50, "right_elbow_90", "Right elbow at 90 deg", {"RIGHT_FOREARM": 90 * up["RIGHT"]}),
        (60, "left_knee_bent", "Left knee bent (in-plane joint test)", knee("LEFT")),
        (70, "right_knee_bent", "Right knee bent (in-plane joint test)", knee("RIGHT")),
        (80, "torso_leaning", "Torso leaning", {"SPINE_LOWER": 4.0, "SPINE_UPPER": 4.0, "CHEST": 6.0}),
        (90, "return_to_neutral", "Return to neutral", {}),
    ]
    out_list = []
    for frame, pid, label, offs in rows:
        e = {"frame": frame, "id": pid, "label": label, "offsets_deg": {k: float(v) for k, v in offs.items()},
             "pos_offset_px": {}, "after_reopen": pid == "torso_leaning"}
        if "knee" in pid:
            e["view_note"] = "front-view art: real knee flexion is depth motion (REQUIRES_NEW_VIEW); in-plane joint test"
        out_list.append(e)
    return out_list


# -- layer assembly ----------------------------------------------------------------------------------------------

def base_layers(pkg: str | Path, sk: NativeSkeletonSpec) -> tuple[list[HLayer], dict[str, Any]]:
    """The editor's layers (original pixels, mask edits applied, no extensions) with the Phase 4.1 binding."""
    from ..editor.artwork import ArtworkModel
    from ..editor.project import EditorProject

    pkg = Path(pkg)
    rig = json.loads((pkg / "natural_pose/rig.json").read_text(encoding="utf-8"))
    sp = pkg / "natural_pose/garments/structure.json"
    structure = json.loads(sp.read_text(encoding="utf-8")) if sp.is_file() else None
    manifest = json.loads((pkg / "artwork/manifest.json").read_text(encoding="utf-8"))
    man_layers = {l["id"]: l for l in manifest.get("layers", [])}
    proj, art = EditorProject(pkg), ArtworkModel(pkg)
    st = proj.state()
    masks = art.masks(st["mask_ops"])
    rejected = {k for k, v in st["approvals"].items() if v.get("status") == "rejected"}
    strategy = NativeBindingStrategy(sk, rig, structure)
    out, z = [], 0
    ctx = {"art": art, "masks": masks, "rejected": rejected, "rig": rig, "manifest": manifest, "pkg": pkg,
           "approvals": st.get("approvals") or {}}
    for L in st["layers"]:
        if L["kind"] != "bitmap" or not L["visible"] or L["id"] not in masks:
            continue
        cid = L["id"]
        rgba = art.layer_rgba(cid, masks, rejected, with_extensions=False)
        if not (rgba[..., 3] > 0).any():
            continue
        ys, xs = np.nonzero(rgba[..., 3] > 0)
        b = strategy.plan_layer(cid, (man_layers.get(cid) or {}).get("bone") or L.get("bone"), z,
                                (float(xs.mean()), float(ys.mean())))
        out.append(HLayer(cid, rgba, b.bone, float(z), b.binding_class, "original", cid, b.reason))
        z += 1
    return out, ctx


def _axis_t(sk: NativeSkeletonSpec, bone: str, shape) -> tuple[np.ndarray, float]:
    b = sk.bone(bone)
    h, w = shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    bx, by = b.base_px
    tx, ty = b.tip_px
    L = math.hypot(tx - bx, ty - by)
    return ((xx - bx) * (tx - bx) + (yy - by) * (ty - by)) / L, L


MID_LIMBS = {"FOREARM": "UPPER_ARM", "SHIN": "THIGH"}
MID_CUT_FRAC = 0.6   # split a mid limb only when its cut lies this many half-thicknesses behind its pivot


def mid_cut_offset(l: HLayer, layers: list[HLayer], sk: NativeSkeletonSpec) -> float | None:
    """Where a mid-limb layer (forearm, shin) meets its parent limb's layer, along its own bone axis (0 = the
    elbow / knee pivot, negative = behind it): the median of the boundary pixels' axial positions."""
    from scipy import ndimage

    parent = f"{l.bone.split('_', 1)[0]}_{MID_LIMBS[l.bone.split('_', 1)[1]]}"
    pm = np.zeros(l.rgba.shape[:2], bool)
    for o in layers:
        if o.bone == parent and o.status in ("original", "derived_partition"):
            pm |= o.rgba[..., 3] > 0
    cut = (l.rgba[..., 3] > 0) & ndimage.binary_dilation(pm, iterations=2)
    if not cut.any():
        return None
    t, _ = _axis_t(sk, l.bone, l.rgba.shape)
    return float(np.median(t[cut]))


def apply_split(layers: list[HLayer], sk: NativeSkeletonSpec, rig: dict[str, Any] | None = None) -> list[HLayer]:
    """Root limbs are always split at their pivot plane (the audit's LAYER_CUT at the shoulders and hips). A mid limb
    (forearm, shin) is split only when its measured cut lies more than ``MID_CUT_FRAC`` half-thicknesses behind its
    pivot: where the cut is at the joint, moving pixels across it opens a hole instead (measured on the schoolboy's
    left elbow: 150 -> 546 px)."""
    out = []
    half = {b.get("name"): float(b.get("half_thickness_px") or 0) for b in (rig or {}).get("bones", [])}
    for l in layers:
        suffix = l.bone.split("_", 1)[1] if "_" in l.bone else ""
        if l.status == "original" and suffix in MID_LIMBS:
            off = mid_cut_offset(l, layers, sk)
            src = sk.bone(l.bone).source.split(" ")[0]
            ht = half.get(src) or 0.0
            if off is not None and ht > 0 and off < -MID_CUT_FRAC * ht:
                parent = f"{l.bone.split('_', 1)[0]}_{MID_LIMBS[suffix]}"
                t, _ = _axis_t(sk, l.bone, l.rgba.shape)
                a = l.rgba[..., 3] > 0
                behind = a & (t < 0)
                for mask, name, bone, why in (
                        (behind, f"{l.name}__joint", parent, f"pixels of {l.name} behind the {l.bone} pivot plane "
                                                             f"stay with {parent} (cut {off:.1f} px from the pivot)"),
                        (a & ~behind, l.name, l.bone, l.reason)):
                    if mask.any():
                        rgba = np.zeros_like(l.rgba)
                        rgba[mask] = l.rgba[mask]
                        out.append(HLayer(name, rgba, bone, l.z, l.binding_class,
                                          "original" if name == l.name else "derived_partition", l.name, why,
                                          {"px": int(mask.sum()), "cut_offset_px": round(off, 1)}))
                continue
        if l.status != "original" or suffix not in ROOT_LIMBS:
            out.append(l)
            continue
        side = l.bone.split("_", 1)[0]
        parent_suffix, child_suffix = ROOT_LIMBS[suffix]
        parent = parent_suffix if parent_suffix == "PELVIS" else f"{side}_{parent_suffix}"
        child = f"{side}_{child_suffix}" if child_suffix else None
        t, L = _axis_t(sk, l.bone, l.rgba.shape)
        a = l.rgba[..., 3] > 0
        behind = a & (t < 0)
        past = a & (t > L) if child else np.zeros_like(a)  # no child given: pixels past the tip stay
        keep = a & ~behind & ~past
        for mask, name, bone, why in ((behind, f"{l.name}__joint", parent,
                                       f"pixels of {l.name} behind the {l.bone} pivot plane stay with {parent}"),
                                      (keep, l.name, l.bone, l.reason),
                                      (past, f"{l.name}__spill", child,
                                       f"pixels of {l.name} past the {l.bone} tip move with {child}")):
            if not mask.any():
                continue
            rgba = np.zeros_like(l.rgba)
            rgba[mask] = l.rgba[mask]
            out.append(HLayer(name, rgba, bone, l.z, l.binding_class,
                              "original" if name == l.name else "derived_partition", l.name, why,
                              {"px": int(mask.sum())}))
    return out


SPECK_MAX_PX = 400      # a detached component of a limb layer this small ...
SPECK_FRAC = 0.02       # ... and below this share of the layer's main component is a segmentation speck
SPECK_REACH_PX = 4      # it goes to the layer (on another bone) that has the most pixels within this distance
BODY_BONES = ("ROOT", "PELVIS", "SPINE_LOWER", "SPINE_UPPER", "CHEST", "NECK", "HEAD")


def reassign_specks(layers: list[HLayer]) -> list[HLayer]:
    """Detached specks of a limb layer (segmentation leftovers along a neighbour's outline) move to the neighbouring
    layer they touch, so they stay where they are drawn instead of flying with the limb. Pixels only move between
    layers: the partition of the original pixels stays exact."""
    from scipy import ndimage

    out = list(layers)
    extra: list[HLayer] = []
    for l in layers:
        if l.status not in ("original", "derived_partition") or l.bone in BODY_BONES:
            continue
        lab, n = ndimage.label(l.rgba[..., 3] > 0, structure=np.ones((3, 3)))
        if n < 2:
            continue
        sizes = ndimage.sum(np.ones(lab.shape), lab, range(1, n + 1))
        main = int(np.argmax(sizes)) + 1
        limit = min(SPECK_MAX_PX, SPECK_FRAC * float(sizes[main - 1]))
        boxes = ndimage.find_objects(lab)
        moved: dict[str, np.ndarray] = {}
        for i in range(1, n + 1):
            if i == main or sizes[i - 1] > limit:
                continue
            ys, xs = boxes[i - 1]
            r = SPECK_REACH_PX
            sl = (slice(max(0, ys.start - r), ys.stop + r), slice(max(0, xs.start - r), xs.stop + r))
            comp = lab[sl] == i
            near = ndimage.binary_dilation(comp, iterations=r)
            best, best_n = None, 0
            for o in layers:
                if o is l or o.bone == l.bone or o.status not in ("original", "derived_partition"):
                    continue
                k = int((near & (o.rgba[sl][..., 3] > 0)).sum())
                if k > best_n:
                    best, best_n = o, k
            if best is None:
                continue
            m = moved.setdefault(best.name, np.zeros(lab.shape, bool))
            m[sl] |= comp
        for target, m in moved.items():
            o = next(x for x in layers if x.name == target)
            rgba = np.zeros_like(l.rgba)
            rgba[m] = l.rgba[m]
            l.rgba = l.rgba.copy()
            l.rgba[m] = 0
            extra.append(HLayer(f"{l.name}__specks_to_{o.bone.lower()}", rgba, o.bone, l.z, l.binding_class,
                                "derived_partition", l.source_layer,
                                f"detached specks of {l.name} that touch {o.name}: they stay with {o.bone}",
                                {"px": int(m.sum()), "speck_target_layer": o.name}))
    return out + extra


def apply_order(layers: list[HLayer]) -> list[HLayer]:
    """Arm chains above the torso (CHEST-bound) layers; relative order kept."""
    torso_z = max((l.z for l in layers if l.bone == "CHEST"), default=None)
    if torso_z is None:
        return layers
    arm_bones = {f"{S}_{p}" for S in ("LEFT", "RIGHT") for p in ("SHOULDER", "UPPER_ARM", "FOREARM", "HAND")}
    moved = [l for l in layers if l.bone in arm_bones and l.z < torso_z and l.status != "pending_extension"]
    for i, l in enumerate(sorted(moved, key=lambda l: l.z)):
        l.extra["order_moved_from"] = l.z
        l.z = torso_z + 0.01 * (i + 1)
    return layers


def add_extensions(layers: list[HLayer], ctx: dict[str, Any]) -> list[HLayer]:
    art, masks, rej = ctx["art"], ctx["masks"], ctx["rejected"]
    out = []
    for l in layers:
        if l.status == "original" and l.name == l.source_layer:
            ext = art.layer_rgba(l.name, masks, rej, with_extensions=True)
            ext[(l.rgba[..., 3] > 0)] = 0
            orig_all = art.layer_rgba(l.name, masks, rej, with_extensions=False)[..., 3] > 0
            ext[orig_all] = 0
            if (ext[..., 3] > 0).any():
                out.append(HLayer(f"{l.name}__ext_pending", ext, l.bone, l.z - 0.001, l.binding_class,
                                  "pending_extension", l.name, "pending Phase 2 reconstruction beneath the original "
                                  "(review build only)", {"px": int((ext[..., 3] > 0).sum())}))
        out.append(l)
    return out


CORRECTIVE_CATEGORIES = ("elbows", "knees", "armpits", "shoes", "shoulders", "hips", "torso")
CORRECTIVE_STATUSES = ("pending_corrective", "approved_corrective")
KEY_TOLERANCE = 0.05   # fraction of the test pose: a key within it of the pose counts as reached


def _source_canvas_piece(crop: np.ndarray, origin, bind, shape) -> tuple[np.ndarray, list[float]]:
    """A corrective crop placed in rest source space: ``inv(bind)`` (a translation for every stored asset) applied
    to its origin, rounded to whole pixels (never scaled, never rotated here)."""
    B = np.asarray(bind, float)
    if not np.allclose(B[:2, :2], np.eye(2), atol=1e-9):
        raise ValueError("corrective bind matrix is not a pure translation")
    x0 = float(origin[0]) - B[0, 2]
    y0 = float(origin[1]) - B[1, 2]
    ix, iy = int(round(x0)), int(round(y0))
    out = np.zeros(shape, np.uint8)
    h, w = crop.shape[:2]
    ys0, xs0 = max(0, iy), max(0, ix)
    ys1, xs1 = min(shape[0], iy + h), min(shape[1], ix + w)
    if ys1 > ys0 and xs1 > xs0:
        out[ys0:ys1, xs0:xs1] = crop[ys0 - iy:ys1 - iy, xs0 - ix:xs1 - ix]
    return out, [round(x0 - ix, 3), round(y0 - iy, 3)]


def add_correctives(layers: list[HLayer], ctx: dict[str, Any], sk: NativeSkeletonSpec,
                    pending: bool = True) -> list[HLayer]:
    """The pending Phase 3.4 corrective drawings (elbow / knee caps, armpit gusset keys, shoe tops) as native layers,
    for review builds only. Each is placed in rest source space, clipped to the rest silhouette (so it can never
    show at rest) and drawn *beneath*: caps and shoe tops under their limb chain, gussets under everything. Which
    one is visible in a pose is the job of a Smart Bone action (planned in ``smartbone_plan``, keyed by hand in
    Moho); the simulation applies the same activation rule. Nothing is approved here."""
    from ..garmentrig.correctives import CorrectiveArtworkStore

    pkg = ctx["pkg"]
    store = CorrectiveArtworkStore(pkg)
    # Fully opaque rest pixels: an underlay clipped to them cannot change the rest composite, even under the
    # semi-transparent edge pixels of the drawing.
    acc = np.zeros(layers[0].rgba.shape[:2], np.float32)
    for l in layers:
        if l.status in ("original", "derived_partition"):
            a = l.rgba[..., 3].astype(np.float32) / 255.0
            acc = a + acc * (1 - a)
    sil = acc >= 1.0 - 1e-6
    by_src = {b.source.split(" ")[0]: b.id for b in sk.bones}
    shape = layers[0].rgba.shape
    out = list(layers)
    zmin_all = min(l.z for l in layers)
    from .correctives42 import SUPERSEDES  # noqa: PLC0415

    ctx.setdefault("excluded_correctives", [])
    for a in store.latest():
        if a.category not in CORRECTIVE_CATEGORIES:
            continue
        if a.placement.get("mode") == "gap_fill_beneath" and not a.placement.get("native"):
            ctx["excluded_correctives"].append({"asset": a.asset_id, "version": a.version, "method": a.method,
                                                "reason": SUPERSEDES})
            continue
        bone = a.attach_bone if a.attach_bone in sk.ids() else by_src.get(a.attach_bone)
        if bone is None:
            continue
        native_fill = bool(a.placement.get("native"))
        aid = f"corrective:{a.asset_id}@v{a.version}"
        decision = (ctx.get("approvals") or {}).get(aid, {}).get("status")
        if decision == "rejected" or (decision != "approved" and not pending):
            continue
        status = "approved_corrective" if decision == "approved" else "pending_corrective"
        pieces = ([(k["key_id"], k["file"], k["origin"], k["bind_matrix"], k.get("values") or {})
                   for k in a.keys if k.get("file")] if a.keys else
                  ([(None, a.file, a.origin, a.bind_matrix, {})] if a.file else []))
        for key_id, rel, origin, bind, values in pieces:
            crop = store.image(rel)
            im, frac = _source_canvas_piece(crop, origin, bind, shape)
            before = int((im[..., 3] > 0).sum())
            if not native_fill:
                im[~sil] = 0  # underlays never show at rest; native gap fills are switched off at rest instead
            px = int((im[..., 3] > 0).sum())
            if px == 0:
                continue
            # every corrective is composited beneath every original layer: it can only show where the
            # originals leave a gap, so no original pixel is ever covered
            z = zmin_all - (0.5 if a.placement.get("mode") == "gap_fill_beneath" else 0.25)
            name = f"{a.asset_id}{'__' + key_id if key_id else ''}__v{a.version}" + (
                "_pending" if status == "pending_corrective" else "")
            out.append(HLayer(name, im, bone, z, "CORRECTIVE", status, a.asset_id,
                              f"pending Phase 3.4 {a.category} corrective ({a.provenance}), beneath, clipped to the "
                              "rest silhouette (review build only; visibility is a Smart Bone action)",
                              {"asset": a.asset_id, "version": a.version, "key": key_id, "values": values,
                               "activation": dict(a.activation), "approval_id": aid,
                               "px": px, "clipped_px": before - px, "subpixel_rounding_px": frac,
                               "category": a.category,
                               "native_automation": "manual_switch" if native_fill else "build_script"}))
    return out


def rest_abduction(sk: NativeSkeletonSpec, rig: dict[str, Any] | None) -> dict[str, float]:
    """Anatomical arm abduction at rest per side (0 = hanging, 90 = horizontal). The package's rendering skeleton
    records it (``rest_angle_deg`` of the upper arm, measured on the anatomical axis); the native bone itself starts
    at the garment-aligned pivot, so its own direction is not the anatomical axis."""
    out = {}
    for S, s in (("LEFT", "l"), ("RIGHT", "r")):
        rb = next((b for b in (rig or {}).get("bones", []) if b.get("name") == f"upper_arm_{s}"), None)
        if rb is not None and rb.get("rest_angle_deg") is not None:
            out[s] = abs(float(rb["rest_angle_deg"]))
        else:
            b = sk.bone(f"{S}_UPPER_ARM")
            dx, dy = b.tip_px[0] - b.base_px[0], b.tip_px[1] - b.base_px[1]
            out[s] = math.degrees(math.atan2(abs(dx), dy))
    return out


def driver_readings(sk: NativeSkeletonSpec, world: dict[str, tuple], rest_world: dict[str, tuple],
                    abd0: dict[str, float] | None = None) -> dict[str, float]:
    """The Phase 3.4 activation drivers measured on a native pose: arm abduction (the anatomical rest abduction plus
    the upper arm's rotation toward raised), elbow and knee flexion (degrees bent from the rest angle)."""
    def wrap(d):
        return (d + 180.0) % 360.0 - 180.0

    def ang(w, b):
        return math.degrees(w[b][1])

    abd0 = abd0 or rest_abduction(sk, None)
    out = {}
    for S, s in (("LEFT", "l"), ("RIGHT", "r")):
        up = _sign_up(sk.bone(f"{S}_UPPER_ARM"))
        out[f"abduction_{s}"] = abd0[s] + up * wrap(ang(world, f"{S}_UPPER_ARM") - ang(rest_world, f"{S}_UPPER_ARM"))
        for drv, (a, b) in ((f"elbow_{s}", (f"{S}_UPPER_ARM", f"{S}_FOREARM")),
                            (f"knee_{s}", (f"{S}_THIGH", f"{S}_SHIN"))):
            rel = wrap(ang(world, b) - ang(world, a))
            rel0 = wrap(ang(rest_world, b) - ang(rest_world, a))
            out[drv] = abs(wrap(rel - rel0))
    for b in sk.bones:  # local rotation of every bone from rest (degrees, Moho's CCW sign)
        d = ang(world, b.id) - ang(rest_world, b.id)
        if b.parent:
            d -= ang(world, b.parent) - ang(rest_world, b.parent)
        out[f"bone_offset:{b.id}"] = wrap(d)
    return out


def driver_value(act: dict[str, Any], readings: dict[str, float]) -> float:
    """The activation input: a Phase 3.4 reading (degrees), or for a native fill the fraction of its test pose's
    rotation of the driving bone (``bone_offset:<BONE>``, signed by ``direction``)."""
    drv = act.get("driver", "")
    x = readings.get(drv, 0.0)
    if drv.startswith("bone_offset:"):
        return x * float(act.get("direction", 1.0)) / max(float(act.get("full_deg", 1.0)), 1e-6)
    return x


def activation_weight(act: dict[str, Any], readings: dict[str, float]) -> float:
    kind = act.get("kind")
    if kind == "always":
        return 1.0
    if kind not in ("ramp", "keyed"):
        return 0.0
    x = driver_value(act, readings)
    lo, hi = float(act.get("lo", 0.0)), float(act.get("hi", 1.0))
    u = min(1.0, max(0.0, (x - lo) / max(hi - lo, 1e-6)))
    return u * u * (3 - 2 * u)


def active_correctives(layers: list[HLayer], readings: dict[str, float]) -> set[str]:
    """Names of the corrective layers visible in a pose. Clipped underlays built by the script (caps, shoe tops) are
    always on: they lie under fully opaque rest pixels and beneath every layer, so they only show through a gap.
    A Smart-Bone-switched native gap fill shows its largest key not past the pose; a Phase 3.4 keyed asset shows the
    key nearest the reading while its activation weight is above zero."""
    on: set[str] = set()
    keyed: dict[str, list[HLayer]] = {}
    for l in layers:
        if l.status not in CORRECTIVE_STATUSES:
            continue
        if l.extra.get("native_automation") == "build_script":
            on.add(l.name)  # clipped underlays: always visible natively, they can only show through a gap
            continue
        if activation_weight(l.extra["activation"], readings) <= 0.0:
            continue
        if l.extra.get("key"):
            keyed.setdefault(l.extra["asset"], []).append(l)
        else:
            on.add(l.name)
    for asset, ls in keyed.items():
        act = ls[0].extra["activation"]
        x = driver_value(act, readings)
        val = lambda l: float(next(iter(l.extra["values"].values()), 0.0))  # noqa: E731
        if act.get("driver", "").startswith("bone_offset:"):
            # a native fill never shows a key baked for a wider gap than the pose has: the largest key <= x
            under = [l for l in ls if val(l) <= x + KEY_TOLERANCE]
            if under:
                on.add(max(under, key=val).name)
        else:
            on.add(min(ls, key=lambda l: abs(val(l) - x)).name)
    return on


def assemble(pkg: str | Path, sk: NativeSkeletonSpec, variant: str,
             base: tuple[list[HLayer], dict[str, Any]] | None = None) -> list[HLayer]:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; known: {sorted(VARIANTS)}")
    layers, ctx = base or base_layers(pkg, sk)
    layers = [HLayer(l.name, l.rgba, l.bone, l.z, l.binding_class, l.status, l.source_layer, l.reason, dict(l.extra))
              for l in layers]
    steps = VARIANTS[variant]
    if "split" in steps:
        layers = reassign_specks(apply_split(layers, sk, ctx.get("rig")))
    if "order" in steps:
        layers = apply_order(layers)
    if "extensions" in steps:
        layers = add_extensions(layers, ctx)
    if "correctives" in steps:
        layers = add_correctives(layers, ctx, sk, pending=True)
    elif "approved" in steps:
        layers = add_correctives(layers, ctx, sk, pending=False)
    layers.sort(key=lambda l: l.z)
    return layers


# -- simulation (labelled SIMULATION; never Moho output) ------------------------------------------------------------

class Simulator:
    """Rigid layer binding, exactly as Moho's SetLayerParentBone is assumed to behave (verified for the rest pose in
    Phase 4.0), on a padded canvas so no pose is cropped."""

    def __init__(self, sk: NativeSkeletonSpec, pad: int = AUDIT_PAD, rig: dict[str, Any] | None = None):
        self.sk = sk
        self.abd0 = rest_abduction(sk, rig)
        self._rest_world = None
        w, h = sk.source_canvas
        self.placement = Placement((w + 2 * pad, h + 2 * pad), (pad, pad))
        raw = {"spec_version": "0.1.0", "character": {"id": "audit", "display_name": "audit"},
               "canvas": {"width_px": w + 2 * pad, "height_px": h + 2 * pad},
               "moho": {"bone_layer_name": "AUDIT", "rest_frame": 0, "start_frame": 1, "end_frame": 2,
                        "report_filename": "a.json"},
               "bones": sk.spec_bones(self.placement),
               "parts": [{"name": "x", "layer_name": "x", "image": "x.png", "bone": "ROOT", "z_index": 0}],
               "animation": {"name": "audit", "tracks": []}}
        self.spec: RigSpec = parse_spec(raw)
        self.rest = {r.name: BonePose(r.name, r.world_base, r.world_angle, r.length)
                     for r in compute_rest_pose(self.spec)}

    def canvas_layer(self, rgba: np.ndarray) -> Image.Image:
        w, h = self.placement.canvas
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        im.paste(Image.fromarray(rgba, "RGBA"), self.placement.offset)
        return im

    def world(self, pose: dict[str, Any]) -> dict[str, tuple]:
        from .fullbody import pose_world
        return pose_world(self.spec, self.sk, pose["offsets_deg"], pose.get("pos_offset_px") or None)

    def readings(self, world: dict[str, tuple]) -> dict[str, float]:
        if self._rest_world is None:
            self._rest_world = self.world({"offsets_deg": {}})
        return driver_readings(self.sk, world, self._rest_world, self.abd0)

    def active(self, layers: list[HLayer], world: dict[str, tuple]) -> set[str]:
        if not any(l.status in CORRECTIVE_STATUSES for l in layers):
            return set()
        return active_correctives(layers, self.readings(world))

    def matrix(self, bone: str, world: dict[str, tuple]) -> np.ndarray:
        w, h = self.placement.canvas
        base, ang = world[bone]
        return np.asarray(layer_delta_px(w, h, self.rest[bone], BonePose(bone, base, ang, self.rest[bone].length)),
                          float)

    def render(self, layers: list[HLayer], pose: dict[str, Any], only: set[str] | None = None) -> np.ndarray:
        from ..preview import _invert_affine_px

        w, h = self.placement.canvas
        world = self.world(pose)
        on = self.active(layers, world)
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        for l in layers:
            if only is not None and l.name not in only:
                continue
            if l.status in CORRECTIVE_STATUSES and l.name not in on:
                continue
            im = self.canvas_layer(l.rgba)
            m = self.matrix(l.bone, world)
            if np.allclose(m, np.eye(3), atol=1e-9):
                canvas.alpha_composite(im)
                continue
            canvas.alpha_composite(im.transform((w, h), Image.Transform.AFFINE, _invert_affine_px(m),
                                                resample=Image.Resampling.BICUBIC))
        return np.asarray(canvas)
