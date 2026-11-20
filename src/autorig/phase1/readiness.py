"""LiveReadinessEvaluator: is the EXISTING artwork sufficient for each live
capability? Conservative by design - a capability is READY only when every
piece of artwork it needs exists. A single front-facing image normally lacks
closed eyes, mouth interiors and turned head views, so those come out as
REQUIRES_NEW_ARTWORK, never as READY."""

from __future__ import annotations

from typing import Any

from .face import FaceAnalysis

READY = "READY"
RECON = "REQUIRES_RECONSTRUCTION"
NEW = "REQUIRES_NEW_ARTWORK"
UNSUPPORTED = "UNSUPPORTED"
REVIEW = "NEEDS_REVIEW"
STATUSES = (READY, RECON, NEW, UNSUPPORTED, REVIEW)
CAPABILITIES = ("blinking", "eyebrow_movement", "mouth_opening", "lip_sync", "eye_direction",
                "head_tilting", "head_turning", "facial_expressions")


def evaluate(face: FaceAnalysis, parts_info: dict[str, Any], joints: dict[str, Any]) -> dict[str, Any]:
    F = face.features
    both = lambda prefix: all(f"{prefix}_{s}" in F for s in ("r", "l"))
    any_ = lambda prefix: any(f"{prefix}_{s}" in F for s in ("r", "l"))
    mouth_state = face.landmarks.get("mouth_state")
    missing = {m["artwork"]: m for m in face.missing_artwork}
    out: dict[str, Any] = {}

    def put(cap, status, reasons, needs=(), evidence=None):
        out[cap] = {"status": status, "reasons": reasons,
                    "required_artwork": [n for n in needs if n in missing] or list(needs),
                    "evidence": evidence or {}}

    # blinking
    if both("eye"):
        put("blinking", NEW, ["Both eyes are separable, but no closed-eye artwork exists in a single open-eyed "
                              "reference."], ["closed_eye_r", "closed_eye_l"], {"eyes": ["eye_r", "eye_l"]})
    elif any_("eye"):
        put("blinking", REVIEW, ["Only one eye was segmented with sufficient quality."], ["closed_eye_r", "closed_eye_l"])
    else:
        put("blinking", REVIEW, ["No eye passed the segmentation quality gates."])
    # eyebrows
    if both("brow"):
        put("eyebrow_movement", RECON, ["Brows are separable; moving them reveals skin that was never painted "
                                        "(must be reconstructed, not invented here)."],
            ["skin_under_brow_r", "skin_under_brow_l"])
    else:
        put("eyebrow_movement", REVIEW if any_("brow") else NEW,
            ["Eyebrows not (both) separable - possibly hidden by hair or not drawn."], ["brow_r", "brow_l"])
    # mouth
    if "mouth" not in F:
        put("mouth_opening", REVIEW, ["Mouth not detected."])
        put("lip_sync", REVIEW, ["Mouth not detected."])
    else:
        if mouth_state == "closed_line":
            put("mouth_opening", NEW, ["The reference mouth is a closed line: no lips or interior to open."],
                ["open_mouth_with_interior", "skin_under_mouth"])
        else:
            put("mouth_opening", REVIEW, ["Mouth is a filled/open shape; whether it can open or close needs review."],
                ["skin_under_mouth"])
        put("lip_sync", NEW, ["Lip-sync needs a set of phoneme mouth shapes; only one exists."], ["phoneme_mouth_set"])
    # eye direction
    if both("pupil") and both("eye"):
        derived = [s for s in ("r", "l") if F[f"pupil_{s}"].method != "segmentation"]
        put("eye_direction", RECON,
            ["Pupils are separable inside the eyes, but the sclera under them is hidden and must be "
             "reconstructed before pupils can move."] + (
                [f"pupil_{s} was derived from pixel intensity - review" for s in derived] if derived else []),
            ["sclera_fill_r", "sclera_fill_l"])
    elif both("eye"):
        put("eye_direction", REVIEW, ["Eyes found but pupils not separable."])
    else:
        put("eye_direction", REVIEW, ["Eyes not separable."])
    # head
    neck = joints.get("neck")
    head_px = parts_info.get("head", {}).get("pixels", 0)
    if head_px and neck is not None and neck.reliable:
        put("head_tilting", RECON, ["Head layer and neck pivot exist; tilting exposes hidden neck/collar pixels "
                                    "under the chin that must be reconstructed."],
            ["neck_extension_under_chin", "back_of_hair_and_hidden_head_outline"])
    else:
        put("head_tilting", REVIEW, ["Head layer or a reliable neck pivot is missing."])
    put("head_turning", NEW, ["Turning needs three-quarter/profile views or new turn artwork; a single front "
                              "view cannot provide them."], ["three_quarter_and_profile_head_views"])
    put("facial_expressions", NEW, ["Only the neutral expression exists; expressions need new brow/eye/mouth "
                                    "artwork."], ["expression_variants (brows, eyes, mouth)"])
    counts = {s: sum(1 for v in out.values() if v["status"] == s) for s in STATUSES}
    return {"capabilities": out, "summary": counts,
            "policy": "READY only when all required artwork already exists in the reference; nothing is invented."}


def to_markdown(readiness: dict[str, Any], character: str) -> str:
    lines = [f"# Live animation readiness: {character}", "",
             "Classification of whether the **existing artwork** supports each live capability.",
             "", "| Capability | Status | Why | Artwork required |", "|---|---|---|---|"]
    for cap, v in readiness["capabilities"].items():
        lines.append(f"| {cap} | **{v['status']}** | {' '.join(v['reasons'])} | "
                     f"{', '.join(v['required_artwork']) or '-'} |")
    lines += ["", f"Summary: {readiness['summary']}", "", f"Policy: {readiness['policy']}", ""]
    return "\n".join(lines)
