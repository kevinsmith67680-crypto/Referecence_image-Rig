"""Phase 4.1: NativeBindingStrategy, which decides one Moho bone and one binding class per artwork layer.

Native method: rigid layer binding (``SetLayerParentBone``) is the only binding Moho 14.5.1 has run for AutoRig
(Phase 4.0), so every imported layer uses it. The class says what the layer really needs, so later phases can
upgrade it without rebuilding the rig:

- RIGID: follows one bone exactly (head parts, shoes);
- LIMITED_DEFORMATION: mostly one bone, small bends at its edges (torso, neck, pelvis);
- FLEXIBLE: spans a joint (upper arm, forearm, thigh, shin); with rigid binding the seam opens when it bends;
- SWITCHABLE: one drawing of a switch group (hands); Switch Layers are Phase 4.3;
- CORRECTIVE: corrective artwork (pending review); metadata only until Phase 4.2.

AutoRig's weight meshes are not converted: no evidenced Lua API binds an image layer with per-vertex weights.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .skeleton import NativeSkeletonSpec

CLASSES = ("RIGID", "LIMITED_DEFORMATION", "FLEXIBLE", "SWITCHABLE", "CORRECTIVE")
NATIVE_METHOD = "layer_binding"  # SetLayerParentBone: natively exercised in Phase 4.0
KIND_TO_SEMANTIC = {
    "upper_arm": "UPPER_ARM", "forearm": "FOREARM", "hand": "HAND", "clavicle": "SHOULDER",
    "thigh": "THIGH", "shin": "SHIN", "foot": "FOOT",
}
CLASS_RULES = {
    # package layer class -> (semantic bone, binding class); limb classes are resolved per layer below
    "head": ("HEAD", "RIGID"),
    "neck": ("NECK", "LIMITED_DEFORMATION"),
    "torso": ("CHEST", "LIMITED_DEFORMATION"),
    "pelvis": ("PELVIS", "LIMITED_DEFORMATION"),
}
LIMB_CLASS = {"UPPER_ARM": "FLEXIBLE", "FOREARM": "FLEXIBLE", "THIGH": "FLEXIBLE", "SHIN": "FLEXIBLE",
              "HAND": "SWITCHABLE", "FOOT": "RIGID", "SHOULDER": "LIMITED_DEFORMATION"}
SEAMS = {"FLEXIBLE": "the joint seam opens when the joint bends (rigid fallback); needs correctives (Phase 4.2)",
         "LIMITED_DEFORMATION": "edges do not bend with the neighbouring bones (rigid fallback)"}


@dataclass
class LayerBinding:
    layer: str
    bone: str
    binding_class: str
    native_method: str
    package_bone: str | None
    package_class: str | None
    home_bones: list[str]
    reason: str
    fallback_note: str | None = None
    garment_parts: list[dict[str, Any]] = field(default_factory=list)
    z_index: int = 0
    imported: bool = True
    status: str = "original"           # original | pending_extension


def _package_to_semantic(name: str, sk: NativeSkeletonSpec) -> str | None:
    for b in sk.bones:
        if b.source.split(" ")[0] == name:
            return b.id
    return None


class NativeBindingStrategy:
    def __init__(self, sk: NativeSkeletonSpec, rig: dict[str, Any], structure: dict[str, Any] | None = None):
        self.sk = sk
        self.layer_bindings = rig.get("layer_bindings") or {}
        self.parts = (structure or {}).get("parts") or []

    def _garment_parts(self, package_bone: str | None) -> list[dict[str, Any]]:
        if not package_bone:
            return []
        return [{"part": p["name"], "class": p.get("class"), "allowed_bones": p.get("allowed_bones")}
                for p in self.parts if package_bone in (p.get("allowed_bones") or [])]

    def plan_layer(self, layer_id: str, manifest_bone: str | None, z: int,
                   centroid: tuple[float, float] | None = None) -> LayerBinding:
        lb = self.layer_bindings.get(layer_id, {})
        pclass = lb.get("class")
        home = list(lb.get("home_bones") or [])
        if pclass in CLASS_RULES:
            bone, cls = CLASS_RULES[pclass]
            reason = f"package class {pclass!r} -> {bone}"
            if pclass == "torso":
                reason += " (the arms and neck hang from CHEST, so a spine bend keeps the torso with them)"
            pkg_bone = {"HEAD": "head", "NECK": "neck", "CHEST": "chest", "PELVIS": "pelvis"}[bone]
        else:
            pkg_bone = manifest_bone if manifest_bone and manifest_bone not in ("body", "head") else None
            bone = _package_to_semantic(pkg_bone, self.sk) if pkg_bone else None
            if bone is None and home:
                bone, pkg_bone = self._nearest(home, centroid)
                reason = f"nearest of the home bones {home} to the layer's centroid"
            else:
                reason = f"the layer's own package bone {pkg_bone!r}"
            if bone is None:
                raise ValueError(f"layer {layer_id!r}: no bone can be derived (class {pclass!r}, bone "
                                 f"{manifest_bone!r}, home bones {home})")
            suffix = bone.split("_", 1)[1] if "_" in bone else bone
            cls = LIMB_CLASS.get(suffix, "RIGID")
        return LayerBinding(layer_id, bone, cls, NATIVE_METHOD, pkg_bone, pclass, home, reason,
                            SEAMS.get(cls), self._garment_parts(pkg_bone), z)

    def _nearest(self, home: list[str], centroid) -> tuple[str | None, str | None]:
        best, best_d = (None, None), math.inf
        for name in home:
            sid = _package_to_semantic(name, self.sk)
            if sid is None:
                continue
            b = self.sk.bone(sid)
            if centroid is None:
                return sid, name
            ax, ay = b.base_px
            bx, by = b.tip_px
            dx, dy = bx - ax, by - ay
            t = max(0.0, min(1.0, ((centroid[0] - ax) * dx + (centroid[1] - ay) * dy) / max(1e-9, dx * dx + dy * dy)))
            d = math.hypot(centroid[0] - (ax + t * dx), centroid[1] - (ay + t * dy))
            if d < best_d:
                best, best_d = (sid, name), d
        return best


def metadata_items(pkg: Path) -> dict[str, list[dict[str, Any]]]:
    """Switchable and corrective artwork kept as metadata (never imported or approved here)."""
    pkg = Path(pkg)
    out: dict[str, list[dict[str, Any]]] = {"SWITCHABLE": [], "CORRECTIVE": []}
    meta = pkg / "analysis" / "hand_asset_metadata.json"
    if meta.is_file():
        for key, a in (json.loads(meta.read_text(encoding="utf-8")).get("assets") or {}).items():
            out["SWITCHABLE"].append({
                "id": key, "side": a.get("store_side"), "use_side": a.get("use_side"), "gesture": a.get("gesture"),
                "handedness": a.get("handedness"), "palm_class": (a.get("palm") or {}).get("palm_class")
                if isinstance(a.get("palm"), dict) else a.get("palm_class"),
                "approval_item": a.get("approval_item"), "approval": a.get("approval_status", "pending_review"),
                "native": "metadata only; Switch Layers are Phase 4.3"})
    idx = pkg / "analysis" / "corrective_assets.json"
    if idx.is_file():
        data = json.loads(idx.read_text(encoding="utf-8"))
        assets = data.get("assets") if isinstance(data, dict) else data
        for a in assets or []:
            if isinstance(a, dict):
                out["CORRECTIVE"].append({
                    "id": a.get("id") or a.get("asset_id"), "category": a.get("category"),
                    "version": a.get("version"), "provenance": a.get("provenance"),
                    "approval": a.get("approval") or a.get("status") or "pending_review",
                    "native": "metadata only; corrective layers and Smart Bones are Phase 4.2"})
    return out


def binding_report(bindings: list[LayerBinding]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for b in bindings:
        counts[b.binding_class] = counts.get(b.binding_class, 0) + 1
    return {"layers": [asdict(b) for b in bindings], "class_counts": counts,
            "native_method": NATIVE_METHOD,
            "rules": {"one_bone_per_layer": True, "global_flexible_binding": False,
                      "weights_imported": False,
                      "weights_note": "AutoRig weight meshes are not converted: no evidenced Lua API binds an image "
                                      "layer with per-vertex weights; rigid layer binding is the documented fallback"}}
