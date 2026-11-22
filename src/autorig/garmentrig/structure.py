"""GarmentStructureAnalyzer: semantic garment parts, deformation classes, attachments, fold regions and pivots.

Phase 1 cut the artwork into anatomical layers along straight lines; garment semantics do not follow those cuts.
This module labels every layer pixel (visible originals, hidden generated extensions and switch drawings) with a
*garment part*: blazer body, shoulders, sleeve cap, upper/forearm sleeve, elbow fold, shirt cuff, collar, lapels,
tie, badge, buttons, pocket flaps, sweater, blazer hem, waistband, trouser hip/thigh/knee/shin/cuff, shoes, hands,
neck, head and hair. Labels come from colour, the layer masks and the skeleton's joint planes. A part decides
which bones may move its pixels and how (its class):

* ``RIGID``: one rigid transform for the whole part (buttons, badge, tie, pocket flaps, shirt cuffs, shoes,
  hands, head). On a bending torso, the rigid fit of the local deformation is used.
* ``LIMITED_DEFORMATION``: smooth, small deformation with strict stretch limits (blazer body, shoulders, lapels,
  collar, sweater, hem, waistband, trouser hip and cuff, neck, hair).
* ``FLEXIBLE``: rigid segments that bend at joints; the weighted joint patch shows only where a bend opens
  (sleeves, elbow and knee folds, trouser thigh and shin).

Semantic boundaries are not bone boundaries. The sleeve fabric medial of the shoulder pivot (the sleeve cap)
stays with the body, and the blazer hem cut into the thigh layers hangs from the pelvis. ``align_pivots`` moves
the shoulder pivot from the middle of the sleeve to just below the top of the armhole, unless the user moved that
bone in the editor. The reason is in the audit (R2): rotating a T-pose sleeve about its mid-axis swings the
sleeve cap outward.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from scipy import ndimage

from ..phase2.skeleton import SIDES, Bone, Skeleton, _extent, layer_class, unit
from ..motion.garments import HEM_SOFT, RID, colour_classes

RIGID, LIMITED, FLEXIBLE = "RIGID", "LIMITED_DEFORMATION", "FLEXIBLE"
CLASSES = (RIGID, LIMITED, FLEXIBLE)

# part kind -> (garment, class, allowed bones (templates), primary bone, description). "{s}" is the side.
PART_KINDS: dict[str, tuple[str, str, list[str], str, str]] = {
    "blazer_body": ("blazer", LIMITED, ["pelvis", "spine", "chest", "clavicle_r", "clavicle_l"], "chest",
                    "blazer front and back panels on the torso"),
    "blazer_shoulder_{s}": ("blazer", LIMITED, ["spine", "chest", "clavicle_{s}"], "clavicle_{s}",
                            "shoulder of the blazer body next to the armhole"),
    "sleeve_cap_{s}": ("blazer", FLEXIBLE, ["spine", "chest", "clavicle_{s}"], "clavicle_{s}",
                       "shoulder fold region: sleeve fabric medial of the shoulder pivot (stays with the shoulder)"),
    "sleeve_upper_{s}": ("blazer", FLEXIBLE, ["chest", "clavicle_{s}", "upper_arm_{s}", "forearm_{s}"],
                         "upper_arm_{s}", "upper sleeve between the shoulder pivot and the elbow"),
    "elbow_{s}": ("blazer", FLEXIBLE, ["upper_arm_{s}", "forearm_{s}"], "forearm_{s}",
                  "elbow fold region of the sleeve"),
    "sleeve_fore_{s}": ("blazer", FLEXIBLE, ["upper_arm_{s}", "forearm_{s}"], "forearm_{s}",
                        "forearm sleeve between the elbow and the cuff"),
    "shirt_cuff_{s}": ("shirt", RIGID, ["forearm_{s}"], "forearm_{s}", "white shirt cuff at the sleeve end"),
    "lapels": ("blazer", LIMITED, ["spine", "chest", "clavicle_r", "clavicle_l"], "chest",
               "lapels and their trim down to the top button (stiff)"),
    "shirt_collar": ("shirt", LIMITED, ["spine", "chest", "neck"], "chest", "white shirt collar"),
    "tie": ("tie", RIGID, ["spine", "chest"], "chest", "striped tie in the V-neck"),
    "badge": ("blazer", RIGID, ["spine", "chest"], "chest", "embroidered crest on the breast pocket"),
    "button": ("blazer", RIGID, ["pelvis", "spine", "chest"], "spine", "blazer button"),
    "pocket_flap_{s}": ("blazer", RIGID, ["pelvis", "spine", "chest"], "spine", "trimmed pocket flap"),
    "sweater": ("sweater", LIMITED, ["pelvis", "spine", "chest"], "spine", "V-neck sweater in the opening"),
    "blazer_hem": ("blazer", LIMITED, ["pelvis"], "pelvis", "blazer skirt and hem (hangs from the pelvis)"),
    "waistband": ("trousers", LIMITED, ["pelvis"], "pelvis", "trouser waistband and fly"),
    "trouser_hip_{s}": ("trousers", FLEXIBLE, ["pelvis", "thigh_{s}"], "thigh_{s}",
                        "hip fold region: trouser top under the blazer hem (soft attachment)"),
    "trouser_thigh_{s}": ("trousers", FLEXIBLE, ["pelvis", "thigh_{s}", "shin_{s}"], "thigh_{s}", "trouser thigh"),
    "knee_{s}": ("trousers", FLEXIBLE, ["thigh_{s}", "shin_{s}"], "shin_{s}", "knee fold region of the trousers"),
    "trouser_shin_{s}": ("trousers", FLEXIBLE, ["thigh_{s}", "shin_{s}"], "shin_{s}", "trouser shin"),
    "trouser_cuff_{s}": ("trousers", LIMITED, ["shin_{s}", "foot_{s}"], "shin_{s}",
                         "trouser hem resting on the shoe (its bottom edge follows the foot)"),
    "shoe_{s}": ("shoes", RIGID, ["foot_{s}"], "foot_{s}", "shoe"),
    "hand_{s}": ("body", RIGID, ["hand_{s}"], "hand_{s}", "hand (original drawing or switch drawing)"),
    "neck": ("body", LIMITED, ["chest", "neck", "head"], "neck", "neck (its hidden top under the skull follows the head)"),
    "head": ("body", RIGID, ["head"], "head", "head and face (independent of the garments)"),
    "hair": ("body", LIMITED, ["head"], "head", "hair (secondary sway only)"),
}

DEFAULT_CONFIG: dict[str, Any] = {
    "shoulder_pivot": {"lift_h": 0.55, "inset_px": 4.0},
    # joint transitions, in units of the joint's half-thickness h, along the child bone from its joint plane
    "bands": {"shoulder": [0.0, 0.5], "elbow": [-0.15, 0.45], "knee": [-0.25, 0.35], "ankle": [-0.6, 0.45],
              "neck": [-0.5, 0.6], "head": [-0.25, 0.3], "spine": [-0.9, 0.9], "chest": [-0.8, 0.8],
              "collar_neck": [-3.0, 3.0]},
    "collar_neck_share": 0.5,  # the shirt collar takes half the neck rotation (it sits on the shoulders)
    # lapels and collar follow the shoulder girdle like the blazer around them (the arm never moves them); a lower
    # share sheared the lapel edge open when the shoulders rise (both-arms-raised test)
    "lapel_clavicle_share": 0.0,  # Phase 3.4: anchored to the chest (D7: the shrug rotated the collar points)
    "collar_clavicle_share": 0.0,
    # share of the shoe's rotation the trouser hem takes (it rests on the shoe). Less than 1 lifts a hem corner off
    # the shoe and shows background (the shoe's top under the hem was never drawn: NEEDS_ARTWORK asset); 1.0
    # shears the plain hem slightly over the ankle band instead, which reads better than a hole
    "hem_follows_foot": 1.0,
    # Phase 3.4: where a rigid segment starts, as a fraction of its joint band's distal half-width (0 = at the joint
    # plane, the Phase 3.3 behaviour; 1 = at the band end, where the weighted patch is continuous with the segment)
    "segment_start": {"shin": 1.0},
    "fold_half_width_h": 0.6,  # elbow/knee fold regions: |s| < 0.6 h around the joint plane
    "limited_smoothing_vertices": 2.5,  # gradual weight transitions between LIMITED torso parts (no shear)
    "class_overrides": {},  # part name -> class (per character)
    "overlays": True,  # rigid segments over weighted joint patches
    # joint-patch pixels stretched more than this are not drawn: the conflict is reported (NEEDS_ARTWORK), the fabric
    # is never smeared. The armpit fill of a raised arm is a gusset (artwork), so the shoulder band is stricter.
    "max_patch_stretch": {"default": 4.0, "shoulder": 1.8},
    "mesh_spacing_px": 6,
}


def load_config(pkg: Path | None) -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if pkg is not None:
        f = Path(pkg) / "natural_pose" / "garment_rig.json"
        if f.is_file():
            _merge(cfg, json.loads(f.read_text()))
    return cfg


def _merge(a: dict, b: dict) -> None:
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _merge(a[k], v)
        else:
            a[k] = v


@dataclass
class Part:
    name: str
    kind: str
    garment: str
    cls: str
    bones: list[str]
    primary: str
    side: str | None
    description: str
    pixels: int = 0
    layers: dict[str, int] = field(default_factory=dict)
    centroid: list[float] | None = None
    fold: bool = False

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "kind": self.kind, "garment": self.garment, "class": self.cls,
                "allowed_bones": self.bones, "primary_bone": self.primary, "side": self.side,
                "description": self.description, "pixels": self.pixels, "layers": self.layers,
                "centroid": self.centroid, "fold_region": self.fold}


@dataclass
class LayerParts:
    origin: tuple[int, int]
    ids: np.ndarray  # (h, w) int16 part index, -1 outside the layer


class GarmentStructure:
    """Parts registry plus per-layer part maps (crops), attachments, fold regions and the pivot report."""

    def __init__(self, parts: list[Part], layer_parts: dict[str, LayerParts], attachments: list[dict[str, Any]],
                 pivots: list[dict[str, Any]], config: dict[str, Any], decor: dict[str, np.ndarray],
                 shape: tuple[int, int], notes: list[str]):
        self.parts = parts
        self.index = {p.name: i for i, p in enumerate(parts)}
        self.layer_parts = layer_parts
        self.attachments = attachments
        self.pivots = pivots
        self.config = config
        self.decor = decor  # name -> full-canvas bool mask of rigid decorative parts (visible source pixels)
        self.shape = shape
        self.notes = notes

    def part(self, name: str) -> Part:
        return self.parts[self.index[name]]

    def ids_at(self, cid: str, pts: np.ndarray) -> np.ndarray:
        lp = self.layer_parts.get(cid)
        if lp is None:
            return np.full(len(pts), -1, np.int16)
        x0, y0 = lp.origin
        h, w = lp.ids.shape
        xi = np.clip(np.rint(pts[:, 0] - x0).astype(int), 0, w - 1)
        yi = np.clip(np.rint(pts[:, 1] - y0).astype(int), 0, h - 1)
        return lp.ids[yi, xi]

    def full_map(self, cid: str) -> np.ndarray:
        H, W = self.shape
        out = np.full((H, W), -1, np.int16)
        lp = self.layer_parts[cid]
        x0, y0 = lp.origin
        h, w = lp.ids.shape
        out[y0:y0 + h, x0:x0 + w] = lp.ids
        return out

    def overlay(self, layers: list[str] | None = None) -> np.ndarray:
        """RGBA label image (later layers on top) for the diagnostics view."""
        H, W = self.shape
        out = np.zeros((H, W, 4), np.uint8)
        pal = _palette(len(self.parts))
        for cid in layers or list(self.layer_parts):
            if cid not in self.layer_parts:
                continue
            m = self.full_map(cid)
            ok = m >= 0
            out[ok] = np.concatenate([pal[m[ok]], np.full((int(ok.sum()), 1), 170, np.uint8)], 1)
        return out

    def to_json(self) -> dict[str, Any]:
        return {"schema": "autorig.garment_structure", "version": "1.0.0",
                "classes": {RIGID: "one rigid transform per part (rigid fit of the local deformation on a "
                                   "bending torso)",
                            LIMITED: "smooth, small deformation within strict stretch limits",
                            FLEXIBLE: "rigid segments bending at joints over a weighted joint patch"},
                "parts": [p.to_json() for p in self.parts], "attachments": self.attachments,
                "fold_regions": [p.name for p in self.parts if p.fold], "pivots": self.pivots,
                "config": self.config, "notes": self.notes}


def _palette(n: int) -> np.ndarray:
    rng = np.random.default_rng(7)
    pal = rng.integers(40, 255, (max(n, 1), 3)).astype(np.uint8)
    return pal


# -- pivots ----------------------------------------------------------------------------------------------------
def align_pivots(sk: Skeleton, masks: dict[str, np.ndarray], config: dict[str, Any] | None = None
                 ) -> tuple[Skeleton, list[dict[str, Any]]]:
    """Shoulder pivots move up from the sleeve's mid-axis toward the top of the armhole (``lift_h`` x h, at least
    ``inset_px`` inside the top contour), so a lowered arm hangs from the shoulder point instead of swinging its
    cap outward. Bones the user edited in the editor (``method == "editor"``) are kept. The clavicle tail follows
    the moved upper-arm head, so the chain stays connected. Returns a new skeleton and the pivot report (all
    joints validated)."""
    cfg = (config or DEFAULT_CONFIG)["shoulder_pivot"]
    bones = [copy.copy(b) for b in sk.bones]
    by = {b.name: b for b in bones}
    sil = np.zeros(next(iter(masks.values())).shape, bool)
    for m in masks.values():
        sil |= m
    report: list[dict[str, Any]] = []
    for s in SIDES:
        ua, cl = by.get(f"upper_arm_{s}"), by.get(f"clavicle_{s}")
        if ua is None or cl is None:
            continue
        J, u = ua.head.copy(), unit(ua.tail - ua.head)
        n = np.array([u[1], -u[0]])
        if n[1] > 0:
            n = -n  # toward the top contour (up in the image)
        arm = _union(masks, [f"upper_arm_{s}", "torso"])
        d_top = float(_extent(arm if arm.any() else sil, J, n))
        entry = {"joint": f"shoulder_{s}", "bone": ua.name, "rest_pivot": [round(float(v), 1) for v in J],
                 "distance_to_top_contour_px": round(d_top, 1), "half_thickness_px": round(ua.h, 1)}
        if ua.method == "editor" or cl.method == "editor":
            entry.update(aligned=False, pivot=entry["rest_pivot"],
                         reason="the user placed this pivot in the editor; it is kept")
            report.append(entry)
            continue
        lift = float(np.clip(cfg["lift_h"] * ua.h, 0.0, max(d_top - cfg["inset_px"], 0.0)))
        P = J + n * lift
        axis = ua.rest_angle + ua.axis_offset  # the anatomical axis (old joint -> elbow) keeps its meaning
        ua.head = P
        ua.axis_offset = float(((axis - ua.rest_angle + 180) % 360) - 180)
        ua.method = f"garment-aligned: {lift:.0f} px toward the top of the armhole ({ua.method})"
        cl.tail = P.copy()
        entry.update(aligned=True, pivot=[round(float(v), 1) for v in P], lift_px=round(lift, 1),
                     axis_offset_deg=round(ua.axis_offset, 2),
                     reason="pivot near the shoulder point: a lowered sleeve hangs from the top of the armhole "
                            "instead of swinging its cap outward (audit R2)")
        report.append(entry)
    new = Skeleton(bones, sk.ground_y, sk.midline_x, list(sk.notes))
    new.zones = copy.deepcopy(sk.zones)
    for i, b in enumerate(new.bones):  # keep the caller's blend bands (legacy influence uses them)
        j = sk.idx.get(b.name)
        if j is not None:
            new.rp[i], new.rc[i], new.R1[i], new.R2[i] = sk.rp[j], sk.rc[j], sk.R1[j], sk.R2[j]
    report += validate_pivots(new, masks)
    return new, report


def validate_pivots(sk: Skeleton, masks: dict[str, np.ndarray]) -> list[dict[str, Any]]:
    """Checks every limb pivot against the artwork: inside the silhouette, on the limb's cross-section (fraction
    across the limb, 0 = medial line, +-1 = outline), and not at a bounding-box centre of its layer."""
    sil = np.zeros(next(iter(masks.values())).shape, bool)
    for m in masks.values():
        sil |= m
    dt = ndimage.distance_transform_edt(sil)
    out = []
    for b in sk.bones:
        if b.kind in ("pelvis", "spine", "chest", "neck", "head", "clavicle"):
            continue
        x, y = int(round(b.head[0])), int(round(b.head[1]))
        inside = bool(0 <= y < sil.shape[0] and 0 <= x < sil.shape[1] and sil[y, x])
        u = b.u
        nrm = np.array([-u[1], u[0]])
        lay = {"upper_arm": "upper_arm", "forearm": "forearm", "hand": "hand", "thigh": "thigh", "shin": "shin",
               "foot": "foot"}.get(b.kind)
        par = {"forearm": "upper_arm", "hand": "forearm", "shin": "thigh", "foot": "shin"}.get(b.kind)
        limb = _union(masks, [f"{n}_{b.side}" for n in (lay, par) if n]) if lay else sil
        a, c = _extent(limb, b.head, nrm), _extent(limb, b.head, -nrm)
        frac = abs(float((a - c) / max(a + c, 1)))
        bbox_c = None
        if lay and f"{lay}_{b.side}" in masks and masks[f"{lay}_{b.side}"].any():
            ys, xs = np.nonzero(masks[f"{lay}_{b.side}"])
            bbox_c = [float((xs.min() + xs.max()) / 2), float((ys.min() + ys.max()) / 2)]
        at_bbox = bool(bbox_c is not None and np.hypot(b.head[0] - bbox_c[0], b.head[1] - bbox_c[1]) < 4.0)
        checks = {"inside_silhouette": inside, "edge_distance_px": round(float(dt[y, x]) if inside else 0.0, 1),
                  "cross_section_fraction": round(frac, 2), "at_layer_bbox_centre": at_bbox}
        # the shoulder pivot sits near the top of the sleeve on purpose (align_pivots); other joints are centred
        ok = inside and not at_bbox and (frac <= 0.35 or b.kind == "upper_arm")
        out.append({"joint": f"{b.kind}{'_' + b.side if b.side else ''}", "bone": b.name,
                    "pivot": [round(float(v), 1) for v in b.head], "method": b.method, "checks": checks,
                    "status": "ok" if ok else "warning"})
    return out


def _hull_mask(m: np.ndarray, shape: tuple[int, int], pad: int = 0) -> np.ndarray:
    """Filled convex hull of a mask's pixels (dilated by ``pad``), full canvas."""
    from PIL import Image as _Image, ImageDraw as _Draw
    from scipy.spatial import ConvexHull

    ys, xs = np.nonzero(m)
    out = np.zeros(shape, bool)
    if len(xs) < 3:
        out[m] = True
        return out
    pts = np.c_[xs, ys].astype(float)
    try:
        hull = pts[ConvexHull(pts).vertices]
    except Exception:  # degenerate (collinear) pixels
        out[m] = True
        return out
    im = _Image.new("L", (shape[1], shape[0]), 0)
    _Draw.Draw(im).polygon([tuple(p) for p in hull], fill=255)
    out = np.asarray(im) > 0
    return ndimage.binary_dilation(out, iterations=pad) if pad else out


def _union(masks: dict[str, np.ndarray], names: list[str]) -> np.ndarray:
    shape = next(iter(masks.values())).shape
    out = np.zeros(shape, bool)
    for n in names:
        if n in masks:
            out |= masks[n]
    return out


# -- analyzer ---------------------------------------------------------------------------------------------------
class GarmentStructureAnalyzer:
    """Builds the ``GarmentStructure`` for one character.

    ``layers``: full-canvas RGBA rest layers (originals + generated extensions; switch drawings may be added
    later through ``label_variant``). ``generated``: per-layer generated-pixel masks. ``garments``: the Phase 3.1
    ``GarmentModel`` (hem pixels in the thigh layers, central opening)."""

    def __init__(self, sk: Skeleton, source: np.ndarray, layers: dict[str, np.ndarray],
                 generated: dict[str, np.ndarray] | None, garments, profile, config: dict[str, Any] | None = None,
                 pivot_report: list[dict[str, Any]] | None = None):
        self.sk, self.source, self.garments, self.profile = sk, source, garments, profile
        self.pivot_report = pivot_report or []
        self.cfg = config or copy.deepcopy(DEFAULT_CONFIG)
        self.layers = layers
        self.generated = generated or {}
        self.shape = source.shape[:2]
        self.C = colour_classes(source)
        self.parts: list[Part] = []
        self.index: dict[str, int] = {}

    def shoulder_frame(self, side: str) -> tuple[np.ndarray, np.ndarray]:
        return shoulder_frame(self.sk, side, self.pivot_report)

    # registry
    def _pid(self, name: str, kind: str | None = None) -> int:
        if name in self.index:
            return self.index[name]
        kind = kind or name
        side = name[-1] if name[-2:] in ("_r", "_l") else None
        tmpl = kind if kind in PART_KINDS else (kind[:-2] + "_{s}" if side else kind)
        garment, cls, bones, primary, desc = PART_KINDS[tmpl]
        f = (lambda t: t.format(s=side)) if side else (lambda t: t)
        cls = self.cfg.get("class_overrides", {}).get(name, cls)
        p = Part(name, tmpl, garment, cls, [f(b) for b in bones if f(b) in self.sk.idx], f(primary), side, desc,
                 fold=tmpl in ("elbow_{s}", "knee_{s}", "trouser_hip_{s}", "sleeve_cap_{s}"))
        self.index[name] = len(self.parts)
        self.parts.append(p)
        return self.index[name]

    def analyze(self) -> GarmentStructure:
        pivot_report = self.pivot_report
        sk, C = self.sk, self.C
        H, W = self.shape
        notes = ["parts are estimated from colour, layer masks and joint planes; check them in the garment "
                 "structure overlay and correct the weights in the Weights tab where they are wrong"]
        orig = {c: (a[..., 3] > 0) & ~self.generated.get(c, np.zeros((H, W), bool)) for c, a in self.layers.items()}
        decor = self._decor(orig)
        for k in sorted(decor):
            self._pid(k, "button" if k.startswith("button_") else None)
        layer_parts = {cid: self._label_layer(cid, rgba[..., 3] > 0, decor) for cid, rgba in self.layers.items()}
        for p in self.parts:  # stats on all layer pixels (visible + hidden)
            tot, lay, cx, cy = 0, {}, 0.0, 0.0
            pi = self.index[p.name]
            for cid, lp in layer_parts.items():
                ys, xs = np.nonzero(lp.ids == pi)
                if len(ys):
                    lay[cid] = int(len(ys))
                    tot += len(ys)
                    cx += float(xs.sum() + lp.origin[0] * len(xs))
                    cy += float(ys.sum() + lp.origin[1] * len(ys))
            p.pixels, p.layers = tot, lay
            p.centroid = [round(cx / tot, 1), round(cy / tot, 1)] if tot else None
        return GarmentStructure(self.parts, layer_parts, self._attachments(), pivot_report or validate_pivots(
            sk, {c: a[..., 3] > 0 for c, a in self.layers.items()}), self.cfg, decor, self.shape, notes)

    def label_variant(self, structure: GarmentStructure, cid: str, rgba: np.ndarray) -> None:
        """Part map for a switch drawing registered after the analysis (hand drawings, trimmed forearms)."""
        self.parts, self.index = structure.parts, structure.index
        structure.layer_parts[cid] = self._label_layer(cid, rgba[..., 3] > 0, structure.decor)

    # decorative rigid parts (on the visible source pixels)
    def _decor(self, orig: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        C, sk, p = self.C, self.sk, self.profile
        H, W = self.shape
        body = _union(orig, ["torso", "pelvis"])
        mid = p.midline_x
        out: dict[str, np.ndarray] = {}
        gold = C["gold"] & body
        lab, n = ndimage.label(ndimage.binary_dilation(gold, iterations=2))
        chest_y = float(sk.bone("chest").head[1])
        buttons = []
        tie = np.zeros((H, W), bool)
        for i in range(1, n + 1):
            m = (lab == i) & gold
            a = int(m.sum())
            if a < 20:
                continue
            ys, xs = np.nonzero(m)
            cx, cy = float(xs.mean()), float(ys.mean())
            w, h = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
            if abs(cx - mid) < 0.25 * p.waist_half and cy < chest_y:
                tie |= m
            elif a >= 600 and cy < p.waist_y:
                out["badge"] = ndimage.binary_fill_holes(ndimage.binary_dilation(m, iterations=3)) & body
            elif "badge" not in out and cy < p.waist_y and a >= 200 and w * h >= 900 and 0.5 <= w / h <= 2.0:
                # Phase 3.8: an emblem drawn as a gold *outline* around a coloured field (the tough_student crest)
                # has few gold pixels and an open ring; its extent, not its pixel count, makes it a badge. The
                # whole emblem (outline + field) is the convex hull of the outline, inside the body.
                out["badge"] = _hull_mask(m, (H, W), pad=3) & body
            elif 40 <= a <= 600 and 0.6 <= w / h <= 1.6:
                buttons.append((cy, ndimage.binary_dilation(ndimage.binary_fill_holes(m), iterations=3) & body))
        for k, (_, m) in enumerate(sorted(buttons, key=lambda t: t[0])):
            out[f"button_{k + 1}"] = m
        if tie.any():  # stripes + the dark stripes between them: the hull of the gold stripes, inside the opening
            ys, xs = np.nonzero(tie)
            hull = np.zeros((H, W), bool)
            for y in range(ys.min(), ys.max() + 1):
                row = xs[ys == y]
                if len(row):
                    hull[y, row.min():row.max() + 1] = True
            hull = ndimage.binary_closing(hull, iterations=4)
            out["tie"] = ndimage.binary_dilation(hull, iterations=2) & body & ~C["white"]
        # pocket flaps: horizontal trim components below the waist line, lateral of the opening
        trim = C["trim"] & body
        lab, n = ndimage.label(trim)
        for i in range(1, n + 1):
            m = lab == i
            a = int(m.sum())
            if a < 150:
                continue
            ys, xs = np.nonzero(m)
            w, h = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
            cx, cy = float(xs.mean()), float(ys.mean())
            if w >= 2.0 * h and cy > p.waist_y - 0.6 * p.waist_half and abs(cx - mid) > 0.45 * p.waist_half:
                box = np.zeros((H, W), bool)
                box[ys.min():ys.max() + 1, xs.min():xs.max() + 1] = True
                side = "r" if cx < mid else "l"  # character's right = image left
                out[f"pocket_flap_{side}"] = ndimage.binary_dilation(box, iterations=3) & body
        return out

    def _label_layer(self, cid: str, mask: np.ndarray, decor: dict[str, np.ndarray]) -> LayerParts:
        sk, C, g = self.sk, self.C, self.garments
        H, W = self.shape
        margin = 6
        if not mask.any():
            return LayerParts((0, 0), np.full((1, 1), -1, np.int16))
        ys, xs = np.nonzero(mask)
        x0, y0 = max(0, xs.min() - margin), max(0, ys.min() - margin)
        x1, y1 = min(W, xs.max() + margin + 1), min(H, ys.max() + margin + 1)
        m = mask[y0:y1, x0:x1]
        yy, xx = np.mgrid[y0:y1, x0:x1]
        pts = np.stack([xx.ravel(), yy.ravel()], 1).astype(np.float64)
        ids = np.full(m.shape, -1, np.int16)
        cls = layer_class(cid)
        crop = lambda a: a[y0:y1, x0:x1]
        I = sk.idx

        def put(sel: np.ndarray, name: str, kind: str | None = None):
            ids[sel & m] = self._pid(name, kind)

        def s(b: str) -> np.ndarray:
            return sk.s(I[b], pts).reshape(m.shape)

        if cls == "head":
            put(np.ones_like(m), "hair" if cid == "hair" else "head")
        elif cls == "neck":
            put(np.ones_like(m), "neck")
        elif cls in ("torso", "other", "pelvis"):
            opening = crop(self._opening())
            put(np.ones_like(m), "blazer_body" if cls != "pelvis" else "blazer_hem")
            if cls == "pelvis":
                put(opening & (yy < g.hem["top_y"] + 0.35 * (g.hem["bottom_y"] - g.hem["top_y"])), "sweater")
                put(crop(C["warm_dark"]) & (yy > g.hem["top_y"]) & opening, "waistband")
            else:
                put(opening, "sweater")
                for side in SIDES:
                    if f"upper_arm_{side}" in I:
                        P, au = self.shoulder_frame(side)
                        h = sk.bone(f"upper_arm_{side}").h
                        near = np.hypot(xx - P[0], yy - P[1]) < 1.6 * h
                        lat = (xx - self.profile.midline_x) * (-1 if side == "r" else 1) > 0
                        put(near & lat, f"blazer_shoulder_{side}")
                        # sleeve fabric cut into the torso layer: lateral of the shoulder plane and inside the
                        # sleeve's tube (the torso side below the armpit is not sleeve)
                        s_sh = ((xx - P[0]) * au[0] + (yy - P[1]) * au[1])
                        E = sk.bone(f"forearm_{side}").head
                        perp = np.abs((xx - E[0]) * au[1] - (yy - E[1]) * au[0])
                        put((s_sh > 0) & (perp < 1.05 * h), f"sleeve_upper_{side}")
                clav_y = min(sk.bone("clavicle_r").head[1], sk.bone("clavicle_l").head[1])
                collar = crop(C["white"]) & (yy < clav_y + 0.5 * sk.bone("upper_arm_r").h)
                collar = ndimage.binary_dilation(collar, iterations=2) & ~crop(C["trim"])
                put(collar, "shirt_collar")
                top_btn = min((float(np.nonzero(v)[0].mean()) for k, v in decor.items() if k.startswith("button_")),
                              default=self.profile.waist_y)
                trim_up = crop(C["trim"]) & (yy < top_btn)
                lap = ndimage.binary_dilation(trim_up, iterations=7) & ~opening & ~collar
                put(lap | (trim_up & ~collar), "lapels")
            for name, dm in decor.items():
                put(crop(dm), name, "button" if name.startswith("button_") else None)
            if cls != "pelvis":
                # ear/jaw outline fragments Phase 1 left in the body layer follow the head (above the head pivot)
                # or the neck (above the neck base, except the shoulder tops lateral of the neck)
                # (only skin and the outline next to it: the collar and lapel tips above the neck base stay garment)
                hp, nb = sk.bone("head").head[1], sk.bone("neck").head[1]
                neck_half = self.garments.anchors.get("neck_half_width_px", 20.0)
                lateral = np.abs(xx - self.profile.midline_x) > 1.15 * neck_half
                low = yy > hp + 0.4 * (nb - hp)
                keep = [self.index[n] for n in ("shirt_collar", "lapels", "tie", "sweater") if n in self.index]
                garment = np.isin(ids, keep)  # parts already found here: the collar and lapel tips stay garment
                put((s("neck") > 0) & ~(lateral & low) & ~garment, "neck")
                put(s("head") > 0, "head")  # no garment reaches above the head pivot
                # Phase 3.4 (D8): dark jaw/ear outline pixels just below the head pivot, touching a head layer, were
                # labelled collar/neck and stayed behind at a head tilt or lean (floating outline specks)
                head_m = np.zeros(self.source.shape[:2], bool)
                for c2, a2 in self.layers.items():
                    if layer_class(c2) == "head":
                        head_m |= a2[..., 3] > 0
                if head_m.any():
                    d_head = crop(ndimage.distance_transform_edt(~head_m))
                    lum = crop(self.source[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32))
                    jaw = (d_head <= float(self.cfg.get("jaw_fragment_px", 2.5))) & (lum < 70) & (s("head") > -30)
                    put(jaw, "head")
        elif cls.startswith("arm"):
            side = cls[-1]
            base = cid.split("__")[0]
            if base.startswith("hand_"):
                put(np.ones_like(m), f"hand_{side}")
            else:
                P, au = self.shoulder_frame(side)
                s_sh = ((xx - P[0]) * au[0] + (yy - P[1]) * au[1])
                s_fo = s(f"forearm_{side}")
                h_el, h_up = sk.bone(f"forearm_{side}").h, sk.bone(f"upper_arm_{side}").h
                fw = self.cfg["fold_half_width_h"] * h_el
                put(np.ones_like(m), f"sleeve_fore_{side}")
                put(s_fo < 0, f"sleeve_upper_{side}")
                put(np.abs(s_fo) < fw, f"elbow_{side}")
                put(s_sh < 0, f"sleeve_cap_{side}")
                put(s_sh < -0.5 * h_up, f"blazer_shoulder_{side}")  # blazer shoulder cut into the arm layer
                Wr = sk.bone(f"hand_{side}").head
                dW = np.hypot(xx - Wr[0], yy - Wr[1])
                white = ndimage.binary_dilation(crop(C["white"]), iterations=2)
                cuff = white & (dW < 0.45 * sk.bone(f"forearm_{side}").length) & (s_fo > 0)
                s_ha = s(f"hand_{side}")
                cuff |= s_ha > -2.0  # sleeve-end pixels just past the wrist plane (cuff edge)
                put(cuff, f"shirt_cuff_{side}")
                # past the cuff: skin and finger-outline fragments the Phase 1 cut left in the arm layer belong to
                # the hand (they must follow it, not the cuff)
                far = s_ha > 0.35 * sk.bone(f"hand_{side}").h
                skin = crop(C["gold"]) & (s_ha > 0)
                put(far | skin, f"hand_{side}")
        elif cls.startswith("leg"):
            side = cls[-1]
            s_th, s_sh = s(f"thigh_{side}"), s(f"shin_{side}")
            h_kn = sk.bone(f"shin_{side}").h
            fw = self.cfg["fold_half_width_h"] * h_kn
            if cid.startswith("foot_"):
                put(np.ones_like(m), f"shoe_{side}")
            else:
                put(np.ones_like(m), f"trouser_shin_{side}")
                put(s_sh < 0, f"trouser_thigh_{side}")
                put(np.abs(s_sh) < fw, f"knee_{side}")
                A = sk.bone(f"foot_{side}").head
                put((np.hypot(xx - A[0], yy - A[1]) < 0.3 * sk.bone(f"shin_{side}").length) & (s_sh > fw),
                    f"trouser_cuff_{side}")
                if g.hem_override.any():
                    near = (crop(self._attach_dist()) < HEM_SOFT) & (s_th < 0.6 * sk.bone(f"thigh_{side}").length)
                    put(near, f"trouser_hip_{side}")
                    put(crop(g.hem_override), "blazer_hem")
        else:
            put(np.ones_like(m), "blazer_body")
        # pixels of hidden/generated areas keep the part of their nearest labelled neighbour (already labelled
        # above by position); anything left unlabelled inside the mask takes its nearest labelled pixel
        miss = m & (ids < 0)
        if miss.any() and (ids >= 0).any():
            _, (iy, ix) = ndimage.distance_transform_edt(ids < 0, return_indices=True)
            ids[miss] = ids[iy[miss], ix[miss]]
        return LayerParts((int(x0), int(y0)), ids)

    _open_cache: np.ndarray | None = None
    _attach_cache: np.ndarray | None = None

    def _opening(self) -> np.ndarray:
        """Central opening of the blazer (sweater/shirt/tie), from the Phase 3.1 garment labels."""
        if self._open_cache is None:
            lab = self.garments.labels
            op = (lab == RID["sweater"]) | (lab == RID["shirt_collar"]) | (lab == RID["tie_badge"])
            op &= np.abs(np.arange(self.shape[1])[None, :] - self.profile.midline_x) < 1.2 * self.profile.waist_half
            self._open_cache = ndimage.binary_closing(op, iterations=2)
        return self._open_cache

    def _attach_dist(self) -> np.ndarray:
        if self._attach_cache is None:
            g = self.garments
            hem = (g.labels == RID["blazer_hem"]) | g.hem_override
            waist = hem | (g.labels == RID["sweater"]) & (np.arange(self.shape[0])[:, None] > g.hem["top_y"])
            self._attach_cache = ndimage.distance_transform_edt(~waist).astype(np.float32)
        return self._attach_cache

    # attachments between parts (rest positions); the evaluator checks them per pose
    def _attachments(self) -> list[dict[str, Any]]:
        sk = self.sk
        b = sk.bone
        H, W = self.shape
        sil = np.zeros((H, W), bool)
        for a in self.layers.values():
            sil |= a[..., 3] > 0
        out = []

        def add(name, a, bb, pt, bones, kind):
            if a in self.index and bb in self.index:
                out.append({"name": name, "parts": [a, bb], "rest_point": [round(float(v), 1) for v in pt],
                            "bones": bones, "kind": kind})

        for s in SIDES:
            ua, fo, ha = b(f"upper_arm_{s}"), b(f"forearm_{s}"), b(f"hand_{s}")
            u = ua.u
            n = np.array([u[1], -u[0]])
            if n[1] > 0:
                n = -n
            add(f"shoulder_seam_top_{s}", f"blazer_shoulder_{s}", f"sleeve_upper_{s}", ua.head,
                [f"clavicle_{s}", ua.name], "seam")
            d_down = _extent(sil, ua.head - n * 2, -n)
            add(f"armpit_{s}", "blazer_body", f"sleeve_upper_{s}", ua.head - n * max(d_down - 3, 0),
                ["chest", ua.name], "seam")
            un = np.array([-fo.u[1], fo.u[0]])
            for k, sg in (("a", 1.0), ("b", -1.0)):
                add(f"elbow_{k}_{s}", f"sleeve_upper_{s}", f"sleeve_fore_{s}", fo.head + sg * un * 0.9 * fo.h,
                    [ua.name, fo.name], "fold")
            add(f"cuff_{s}", f"sleeve_fore_{s}", f"shirt_cuff_{s}", ha.head - ha.u * 6, [fo.name], "seam")
            add(f"wrist_{s}", f"shirt_cuff_{s}", f"hand_{s}", ha.head, [fo.name, ha.name], "cover")
            th, sh, ft = b(f"thigh_{s}"), b(f"shin_{s}"), b(f"foot_{s}")
            add(f"hip_{s}", "blazer_hem", f"trouser_hip_{s}", th.head + th.u * 0.25 * th.length,
                ["pelvis", th.name], "cover")
            un = np.array([-sh.u[1], sh.u[0]])
            for k, sg in (("a", 1.0), ("b", -1.0)):
                add(f"knee_{k}_{s}", f"trouser_thigh_{s}", f"trouser_shin_{s}", sh.head + sg * un * 0.9 * sh.h,
                    [th.name, sh.name], "fold")
            add(f"ankle_{s}", f"trouser_cuff_{s}", f"shoe_{s}", ft.head, [sh.name, ft.name], "cover")
        nk = b("neck")
        add("collar_neck", "shirt_collar", "neck", nk.head, ["chest", "neck"], "cover")
        add("waist", "blazer_body", "blazer_hem", np.array([self.profile.midline_x - 0.6 * self.profile.waist_half,
                                                             self.garments.hem["top_y"]]), ["spine", "pelvis"], "seam")
        return out


def shoulder_frame(sk: Skeleton, side: str, pivot_report: list[dict[str, Any]] | None = None
                   ) -> tuple[np.ndarray, np.ndarray]:
    """(shoulder pivot, sleeve axis) of one side. The sleeve axis runs from the anatomical (pre-alignment) shoulder
    joint to the elbow; the shoulder plane is perpendicular to it through the pivot."""
    ua, fo = sk.bone(f"upper_arm_{side}"), sk.bone(f"forearm_{side}")
    J = ua.head
    for r in pivot_report or []:
        if r.get("joint") == f"shoulder_{side}" and "rest_pivot" in r:
            J = np.array(r["rest_pivot"], float)
    return ua.head.copy(), unit(fo.head - J)


def class_of(structure: GarmentStructure, pid: int) -> str:
    return structure.parts[pid].cls if pid >= 0 else LIMITED


def bones_for_layer(structure: GarmentStructure, cid: str) -> list[str]:
    lp = structure.layer_parts.get(cid)
    if lp is None:
        return []
    names: list[str] = []
    for pid in np.unique(lp.ids):
        if pid >= 0:
            for b in structure.parts[pid].bones:
                if b not in names:
                    names.append(b)
    return names


def skeleton_with_bones(sk: Skeleton, bones: list[Bone]) -> Skeleton:
    new = Skeleton(bones, sk.ground_y, sk.midline_x, list(sk.notes))
    new.zones = copy.deepcopy(sk.zones)
    return new
