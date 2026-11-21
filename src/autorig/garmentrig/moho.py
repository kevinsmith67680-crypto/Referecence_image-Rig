"""Moho 14.5 compatibility matrix for the Phase 3.3 garment rig (documentation data, not an exporter).

Each AutoRig garment feature is classified by how it could reach Moho: ``direct`` (a Moho feature with the same
meaning), ``conversion`` (approximated by a Moho feature, with stated loss), ``custom_lua`` (needs a script built
on the verified allowlist) or ``not_exportable``. Every mapping is ``proposed_unverified``: Moho never ran here,
the Phase 0 importer is not verified, and no Moho API name is introduced by this table. Per-vertex weights are NOT
assumed to import into Moho.
"""

from __future__ import annotations

from typing import Any

MOHO_FEATURES = {
    "bone_influence": "bone strength / region of influence (Moho bones)",
    "layer_binding": "a whole layer bound to one bone",
    "point_binding": "individual vector or mesh points bound to one bone",
    "flexi_binding": "smooth influence of several bones (falloff by bone strength)",
    "mesh_warping": "Smart Warp / mesh deformation of image layers",
    "smart_bones": "actions driven by a bone angle (corrective shapes over an angle range)",
    "switch_layers": "layers that swap drawings",
}

MATRIX: list[dict[str, Any]] = [
    {"feature": "RIGID garment parts (shoes, hands, head, shirt cuffs)", "moho": "layer_binding", "class": "direct",
     "note": "one layer per part bound to its bone; the cuff stays on the forearm layer"},
    {"feature": "rigid decorative overlays (badge, buttons, tie, pocket flaps)", "moho": "layer_binding",
     "class": "conversion", "note": "bound to the nearest torso bone; the rigid fit of a bending torso is lost"},
    {"feature": "FLEXIBLE rigid segments (upper/forearm sleeve, trouser shin)", "moho": "layer_binding",
     "class": "conversion", "note": "segments become separate layers (the cut follows the joint plane, not the "
                                    "Phase 1 layer cut); needs a layer split in Phase 4"},
    {"feature": "weighted joint patch (wedge fill at elbows, knees, shoulders)", "moho": "mesh_warping",
     "class": "conversion", "note": "a Smart Warp mesh from the weight grid with points bound to their dominant "
                                    "bone; angle interpolation becomes linear point binding (more pinching)"},
    {"feature": "per-vertex weight meshes (normalised, restricted, locked)", "moho": "point_binding",
     "class": "not_exportable", "note": "Moho binds a point to one bone (or uses flexi falloff); fractional "
                                        "per-vertex weights do not import. Export keeps them as data only"},
    {"feature": "LIMITED torso deformation (spine/chest bands, clavicle shrug)", "moho": "flexi_binding",
     "class": "conversion", "note": "bone strength radii approximate the band widths"},
    {"feature": "garment-aligned shoulder pivots", "moho": "bone_influence", "class": "direct",
     "note": "bone origins at the aligned pivots (already in the exported bones)"},
    {"feature": "angle-range correctives (GarmentCorrectionController, blend)", "moho": "smart_bones",
     "class": "conversion", "note": "a Smart Bone action per trigger over its angle range; drawings must exist "
                                    "and be approved first"},
    {"feature": "switch correctives and hand drawings", "moho": "switch_layers", "class": "direct",
     "note": "switch layer per group (existing hand_switches.json)"},
    {"feature": "generated pixels drawn beneath their occluder", "moho": "layer_binding", "class": "direct",
     "note": "extensions are separate layers placed under the occluding layer"},
    {"feature": "hem lag / hair sway secondary motion", "moho": "smart_bones", "class": "custom_lua",
     "note": "needs a per-frame script or baked keys (allowlisted API only)"},
    {"feature": "patch stretch-limit clipping and distortion evaluation", "moho": None, "class": "not_exportable",
     "note": "analysis in AutoRig only"},
]


def matrix_json() -> dict[str, Any]:
    return {"schema": "autorig.moho_garment_compatibility", "version": "1.0.0", "status": "proposed_unverified",
            "moho_features": MOHO_FEATURES, "rows": MATRIX,
            "notes": ["Moho did not run in this phase; nothing here is verified in Moho",
                      "no Moho Lua API names are introduced; a native exporter is Phase 4",
                      "per-vertex weights are not assumed to import into Moho"]}
