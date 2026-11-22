"""Phase 3.5 alternative artwork, pose-aware artwork requests, view manifest and limb configurations.

**AlternativeArtworkStore / AlternativeArtworkController.** Pose-specific replacement drawings for a layer (raised
shoulder, deep elbow, forearm rotation, deep hip flexion, seated thigh, bent knee, running knee drive, leaning torso,
hand gestures, head/body views).
- **Storage:** append-only in `<package>/artwork/alternatives/<layer>/<id>@v<N>.png` (full-canvas RGBA in the
  character's rest frame, never scaled), indexed with hashes in `analysis/alternative_artwork.json`.
- **Record fields:** artwork id, replaced layer, supported poses and views, attachment bone and pivot, compatible bones,
  draw order, scale (always 1.0) and orientation, provenance, generation method, approval, transition behaviour.
- **Selection:** the same switch mechanism the hand drawings use. The drawing is registered as a variant layer of the
  deformer and replaces its base layer in the draw order, so there is no second switch engine. Approved drawings are
  used; pending ones are only previewed; rejected ones never.

**PoseAwareArtworkGenerator.** When a pose needs a drawing that does not exist, it builds an *artwork request*:
1. the target pose render and its skeleton;
2. the inadequate region;
3. the original artwork crop and its mask;
4. the replacement geometry (bones, pivot, posed angle);
5. the style constraints (outline colour and width, palette sampled from the original);
6. the alignment spec.

Candidates come from a pluggable provider (``ArtworkProvider``). The default ``NoProvider`` produces none and asks
for a drawing; a generative provider would only ever propose local drawings, which stay pending until approved.
``identity_check`` compares a candidate's outline and palette with the original.

**ViewAwareRigController.** A view-availability manifest (FRONT, three-quarter, side, back). It records artwork,
missing parts, rig compatibility, approved pose ranges, layer-order rules and alternative limbs. Only FRONT has rigged
artwork. The user's character sheet has unsegmented 3/4, side and back drawings, recorded as a *source*, not as an
available view.

**Limb configurations.** One semantic rig specification for interchangeable limb set-ups (straight, deeply bent,
forward-reaching arm; normal, seated, running leg). Every configuration keeps the bone names, attachment joints, IK
targets and the wrist/ankle positions, and lists its artwork needs and candidate Moho mappings; every mapping is
``proposed_unverified``.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage

VIEWS = ("FRONT", "FRONT_THREE_QUARTER_LEFT", "FRONT_THREE_QUARTER_RIGHT", "SIDE_LEFT", "SIDE_RIGHT",
         "BACK_THREE_QUARTER", "BACK")
INDEX = "analysis/alternative_artwork.json"


@dataclass
class AlternativeArtwork:
    art_id: str
    layer: str  # the base layer it replaces
    supported_poses: list[str]
    supported_views: list[str] = field(default_factory=lambda: ["FRONT"])
    attach_bone: str = ""
    pivot: list[float] = field(default_factory=list)
    compatible_bones: list[str] = field(default_factory=list)
    draw_order: str = "same_as_base"  # or "front_of:<layer>" / "behind:<layer>"
    scale: float = 1.0
    orientation_deg: float = 0.0
    provenance: str = "ARTIST_EDITED"  # ORIGINAL_DERIVED | ARTIST_EDITED | AI_GENERATED
    method: str = "import"
    transition: dict[str, Any] = field(default_factory=lambda: {"kind": "switch", "hold_frames": 3})
    requirement: str | None = None
    version: int = 1
    file: str | None = None
    sha256: str | None = None
    created: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def approval_key(self) -> str:
        return f"alternative:{self.art_id}@v{self.version}"

    @property
    def variant_id(self) -> str:
        return f"{self.layer}__alt_{self.art_id}"


class AlternativeArtworkStore:
    def __init__(self, pkg):
        self.pkg = Path(pkg)
        f = self.pkg / INDEX
        self.index = json.loads(f.read_text()) if f.is_file() else {"schema": "autorig.alternative_artwork",
                                                                    "version": "1.0.0", "items": []}

    def save(self) -> None:
        f = self.pkg / INDEX
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(self.index, indent=1))

    def add(self, art: AlternativeArtwork, rgba: np.ndarray, canvas: tuple[int, int], layers: set[str]
            ) -> AlternativeArtwork:
        """Validate and store a drawing (append-only). It must be RGBA with the source canvas size, not empty,
        for an existing layer, at scale 1.0."""
        errs = []
        if art.layer not in layers:
            errs.append(f"unknown layer {art.layer!r}")
        if rgba.ndim != 3 or rgba.shape[2] != 4:
            errs.append("the drawing must be RGBA")
        elif rgba.shape[:2] != tuple(canvas):
            errs.append(f"the drawing must be {canvas[1]}x{canvas[0]} px (the character's canvas, unscaled)")
        elif not (rgba[..., 3] > 0).any():
            errs.append("the drawing is empty")
        if abs(art.scale - 1.0) > 1e-9:
            errs.append("alternative drawings are never scaled (draw them at the character's scale)")
        if not art.supported_poses:
            errs.append("list at least one supported pose")
        bad_views = [v for v in art.supported_views if v not in VIEWS]
        if bad_views:
            errs.append(f"unknown views {bad_views}")
        if errs:
            raise ValueError("; ".join(errs))
        prev = [i for i in self.index["items"] if i["art_id"] == art.art_id]
        art.version = 1 + max((i["version"] for i in prev), default=0)
        d = self.pkg / "artwork" / "alternatives" / art.layer
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{art.art_id}@v{art.version}.png"
        if f.exists():
            raise FileExistsError(f)
        Image.fromarray(rgba.astype(np.uint8), "RGBA").save(f)
        art.file = str(f.relative_to(self.pkg))
        art.sha256 = hashlib.sha256(f.read_bytes()).hexdigest()
        art.created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.index["items"].append(asdict(art))
        self.save()
        return art

    def latest(self) -> list[AlternativeArtwork]:
        best: dict[str, dict] = {}
        for i in self.index["items"]:
            if i["art_id"] not in best or i["version"] > best[i["art_id"]]["version"]:
                best[i["art_id"]] = i
        return [AlternativeArtwork(**i) for i in best.values()]

    def image(self, art: AlternativeArtwork) -> np.ndarray:
        return np.asarray(Image.open(self.pkg / art.file).convert("RGBA"))

    def verify(self) -> list[str]:
        return [i["file"] for i in self.index["items"] if not (self.pkg / i["file"]).is_file()
                or hashlib.sha256((self.pkg / i["file"]).read_bytes()).hexdigest() != i["sha256"]]


def approval_state(approvals: dict[str, Any], art: AlternativeArtwork) -> str:
    st = (approvals or {}).get(art.approval_key, {})
    return st.get("status", "pending_review") if isinstance(st, dict) else "pending_review"


class AlternativeArtworkController:
    """Registers stored alternative drawings as variant layers of the engine (once) and selects them per pose."""

    def __init__(self, eng, store: AlternativeArtworkStore):
        self.eng, self.store = eng, store
        self.items = store.latest()
        self.registered: set[str] = set()
        self.held: dict[str, tuple[str | None, int]] = {}  # layer -> (variant, frames held) for the switch hold

    def register(self) -> None:
        for a in self.items:
            if a.variant_id in self.registered:
                continue
            rgba = self.store.image(a)
            self.eng.dfm.add_variant_layer(a.variant_id, rgba)
            self.eng.variants[a.variant_id] = rgba
            self.registered.add(a.variant_id)

    def select(self, action: str, view: str, approvals: dict[str, Any], allow_pending: bool,
               user: dict[str, str | None] | None = None) -> tuple[dict[str, str], list[dict[str, Any]]]:
        """{base layer: variant id} for this pose, and a report. ``user``: explicit choices (layer -> art id, or None for
        the original), which override the automatic choice."""
        self.register()
        chosen: dict[str, str] = {}
        report = []
        for a in self.items:
            st = approval_state(approvals, a)
            ok_pose = action in a.supported_poses and view in a.supported_views
            usable = st == "approved" or (allow_pending and st != "rejected")
            pick = ok_pose and usable
            if user is not None and a.layer in user:
                pick = user[a.layer] == a.art_id and st != "rejected"
            if pick:
                chosen[a.layer] = a.variant_id
            report.append({"art_id": a.art_id, "layer": a.layer, "version": a.version, "state": st,
                           "selected": pick, "approval_key": a.approval_key,
                           "why": "user choice" if user and a.layer in user else
                           ("supports this pose" if ok_pose else "not for this pose")})
        return chosen, report


def substitute(order: list[str], chosen: dict[str, str]) -> list[str]:
    """Replace each base layer by its chosen alternative drawing (the hand-switch mechanism)."""
    return [chosen.get(c.split("__")[0], c) if c.split("__")[0] in chosen and "__" not in c else c for c in order]


# -- pose-aware artwork requests ---------------------------------------------------------------------------------
REQUEST_REGIONS = {
    # missing asset -> (layers to redraw, bones, note)
    "corrective:elbow_deep_r": (["upper_arm_r", "forearm_r"], ["upper_arm_r", "forearm_r"],
                                "sleeve folding on itself at > 125 deg: inner-elbow compression folds, cuff unchanged"),
    "art:thigh_foreshortened": (["thigh_r", "thigh_l"], ["thigh_r", "thigh_l"],
                                "seated thighs pointing at the viewer: a foreshortened thigh and knee top, not a squash"),
    "art:thigh_knee_up": (["thigh_r", "shin_r"], ["thigh_r", "shin_r"],
                          "running knee drive toward the viewer: a lifted, foreshortened thigh"),
    "art:torso_lean_forward": (["torso", "pelvis"], ["spine", "chest"],
                               "torso leaning toward the viewer: a 3/4 or foreshortened blazer front"),
    "corrective:raised_arm_armpit": (["torso", "upper_arm_r"], ["chest", "upper_arm_r"],
                                     "a lean raised-arm armpit (the stretched T-pose sleeve underside reads full)"),
}


class ArtworkProvider:
    name = "none"

    def propose(self, request: dict[str, Any], reference: np.ndarray, mask: np.ndarray) -> list[np.ndarray]:
        raise NotImplementedError


class NoProvider(ArtworkProvider):
    """Default: no generative model is called; the request asks the user (or an artist) for the drawing."""

    name = "none (request a drawing)"

    def propose(self, request, reference, mask):
        return []


class PoseAwareArtworkGenerator:
    def __init__(self, eng, provider: ArtworkProvider | None = None):
        self.eng = eng
        self.provider = provider or NoProvider()

    def request(self, asset: str, res: dict[str, Any], out_dir: Path) -> dict[str, Any] | None:
        from ..garmentrig.correctives import _fabric_colour, _outline_style  # noqa: PLC0415

        spec = REQUEST_REGIONS.get(asset)
        if spec is None:
            return None
        layers, bones, note = spec
        eng = self.eng
        mask = np.zeros(eng.source.shape[:2], bool)
        for c in layers:
            if c in eng.masks:
                mask |= eng.masks[c]
        ys, xs = np.nonzero(mask)
        x0, y0, x1, y1 = max(0, xs.min() - 20), max(0, ys.min() - 20), xs.max() + 21, ys.max() + 21
        ref = eng.source[y0:y1, x0:x1].copy()
        outline, width = _outline_style(eng.source, mask)
        fill = _fabric_colour(eng.source, mask)
        out_dir.mkdir(parents=True, exist_ok=True)
        slug = asset.replace(":", "_")
        Image.fromarray(ref).save(out_dir / f"{slug}__reference.png")
        Image.fromarray((mask[y0:y1, x0:x1] * 255).astype(np.uint8)).save(out_dir / f"{slug}__mask.png")
        pad = res["pad"]
        im = res["image"]
        a = im[..., 3] > 0
        yy, xx = np.nonzero(a)
        Image.fromarray(im[yy.min():yy.max() + 1, xx.min():xx.max() + 1]).save(out_dir / f"{slug}__target_pose.png")
        J = res["solve"]["joints_posed"]
        geo = {b: {"posed_head": [round(float(v), 1) for v in J[b]],
                   "rest_head": [round(float(v), 1) for v in eng.sk.bone(b).head],
                   "posed_rotation_deg": round(float(res["pose"].theta[eng.sk.idx[b]]), 2)} for b in bones if b in J}
        cands = self.provider.propose({"asset": asset}, ref, mask[y0:y1, x0:x1])
        doc = {"asset": asset, "why": note, "layers_to_redraw": layers, "bones": geo,
               "canvas": {"width": int(eng.source.shape[1]), "height": int(eng.source.shape[0]),
                          "crop": [int(x0), int(y0), int(x1), int(y1)]},
               "style": {"outline_rgb": [round(float(v)) for v in outline], "outline_width_px": width,
                         "fabric_rgb": [round(float(v)) for v in fill],
                         "rules": ["same outline colour and width", "same flat-with-soft-shade colouring",
                                   "draw at the character's scale (never scaled later)",
                                   "only the named region: no new full-character illustration"]},
               "alignment": {"attach_bone": bones[0], "pivot": [round(float(v), 1) for v in eng.sk.bone(bones[0]).head],
                             "draw_order": "same as the replaced layer"},
               "provider": self.provider.name, "candidates": len(cands),
               "files": {k: f"{slug}__{k}.png" for k in ("reference", "mask", "target_pose")},
               "next": "import the drawing as an alternative (Artistic Review or AlternativeArtworkStore.add); it stays "
                       "pending until approved"}
        (out_dir / f"{slug}.json").write_text(json.dumps(doc, indent=1))
        return doc


def identity_check(candidate: np.ndarray, original: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
    """Does a candidate drawing keep the character's look? Compares the outline darkness and the fabric palette
    (medians) of the candidate with the original region. A check, not an approval."""
    def stats(img, m):
        lum = img[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
        on = m & (img[..., 3] > 127) if img.shape[2] == 4 else m
        if not on.any():
            return None
        ring = on & ~ndimage.binary_erosion(on, iterations=3)
        return {"outline_lum": float(np.median(lum[ring])), "fabric_rgb": np.median(img[..., :3][on], 0).tolist()}
    a, b = stats(candidate, candidate[..., 3] > 0), stats(original, mask)
    if a is None or b is None:
        return {"ok": False, "why": "empty"}
    d_out = abs(a["outline_lum"] - b["outline_lum"])
    d_col = float(np.abs(np.subtract(a["fabric_rgb"], b["fabric_rgb"])).max())
    return {"ok": d_out < 25 and d_col < 40, "outline_lum_diff": round(d_out, 1), "fabric_rgb_max_diff": round(d_col, 1)}


# -- views and limb configurations ---------------------------------------------------------------------------------
class ViewAwareRigController:
    def __init__(self, pkg, layers: list[str]):
        self.pkg = Path(pkg)
        self.layers = layers

    def manifest(self, alternatives: list[AlternativeArtwork] | None = None) -> dict[str, Any]:
        sheet = Path("references/schoolboy/character_sheet.jpg")
        views = {}
        for v in VIEWS:
            if v == "FRONT":
                views[v] = {"available": True, "artwork": sorted(self.layers), "missing": [
                    "turned head drawings (head turn)", "deep-elbow, seated-thigh, knee-drive drawings"],
                    "rig_compatible": True, "approved_pose_range": "in-plane posing; depth motion NEEDS_ARTWORK",
                    "layer_order_rules": ["DynamicLayerOrderController built-ins"],
                    "alternative_limbs": [a.art_id for a in (alternatives or []) if "FRONT" in a.supported_views]}
            else:
                src = None
                if v in ("FRONT_THREE_QUARTER_LEFT", "FRONT_THREE_QUARTER_RIGHT", "SIDE_LEFT", "SIDE_RIGHT", "BACK"):
                    src = f"{sheet} ({'3/4' if 'QUARTER' in v else 'side' if 'SIDE' in v else 'back'} full-body drawing, "\
                          "not segmented; one side only, the other would be a mirror)"
                views[v] = {"available": False, "artwork": [], "missing": ["every body part (not segmented or rigged)"],
                            "rig_compatible": False, "approved_pose_range": None, "layer_order_rules": [],
                            "alternative_limbs": [], "source_drawing": src,
                            "workflow": "segment the sheet drawing (Phase 1) -> rig -> review; or request new art"}
        return {"schema": "autorig.view_manifest", "version": "1.0.0", "views": views,
                "note": "only FRONT has rigged artwork; no view is claimed without artwork"}

    def available(self, view: str) -> bool:
        return view == "FRONT"

    def requestable(self, view: str) -> bool:
        return view in VIEWS


LIMB_CONFIGS = {
    "arm_straight": {"bones": ["upper_arm", "forearm", "hand"], "artwork": "original layers", "status": "available"},
    "arm_bent_deep": {"bones": ["upper_arm", "forearm", "hand"], "artwork": "alternative forearm+sleeve fold drawing "
                      "(> 125 deg)", "status": "missing", "keeps": ["elbow pivot", "wrist IK target", "bone names"]},
    "arm_forward_reach": {"bones": ["upper_arm", "forearm", "hand"], "artwork": "foreshortened sleeve + hand toward the "
                          "viewer", "status": "missing", "keeps": ["shoulder pivot", "wrist IK target"]},
    "leg_standing": {"bones": ["thigh", "shin", "foot"], "artwork": "original layers", "status": "available"},
    "leg_seated": {"bones": ["thigh", "shin", "foot"], "artwork": "foreshortened thigh + knee top", "status": "missing",
                   "keeps": ["hip pivot", "ankle IK target", "foot contact"]},
    "leg_running": {"bones": ["thigh", "shin", "foot"], "artwork": "knee-up thigh + tucked shin", "status": "missing",
                    "keeps": ["hip pivot", "ankle IK target", "gait phase"]},
}


def limb_config_doc() -> dict[str, Any]:
    return {"schema": "autorig.limb_configurations", "version": "1.0.0", "configurations": LIMB_CONFIGS,
            "rules": ["one semantic rig: bone names, hierarchy and IK targets never change between configurations",
                      "a configuration swaps drawings (AlternativeArtworkController), never bone lengths",
                      "attachment joints and wrist/ankle positions are preserved at the switch"],
            "moho": {"switch_layer": {"use": "swap a limb's drawings per configuration", "status": "proposed_unverified"},
                     "vitruvian_bones": {"use": "alternative bone sets for a limb (e.g. seated leg)",
                                         "status": "proposed_unverified",
                                         "note": "not exercised; no Lua API member is assumed"},
                     "smart_bone_action": {"use": "drive the switch from a joint angle", "status": "proposed_unverified"}},
            "verified": []}


__all__ = ["AlternativeArtwork", "AlternativeArtworkStore", "AlternativeArtworkController", "approval_state",
           "substitute", "PoseAwareArtworkGenerator", "ArtworkProvider", "NoProvider", "identity_check",
           "ViewAwareRigController", "VIEWS", "LIMB_CONFIGS", "limb_config_doc", "REQUEST_REGIONS"]
