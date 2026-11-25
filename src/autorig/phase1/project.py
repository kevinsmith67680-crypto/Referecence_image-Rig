"""Phase 1 pipeline: reference image -> character_project/ (analysis, masks,
original-pixel artwork layers, previews, live-control contract, metadata).

character_project/
  source/original.png              byte-identical copy of the reference
  analysis/anatomy.json face.json live_readiness.json live_readiness.md live_controls.json
  masks/body/*.png masks/face/*.png
  artwork/bitmap/*.png  artwork/vector/  artwork/layers.json (hybrid contract)
  previews/original.png segmentation.png facial_segmentation.png layers.png
           reconstructed.png difference.png
  metadata.json
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .. import __version__
from ..segmentation.sam3 import FixtureSegmentationProvider
from ..segmentation.types import ConceptResult, Instance, Prompt, SegmentationRun
from . import compositor as C
from . import live_controls, readiness, schemas
from . import masks as M
from .anatomy import (JOINT_PARENTS, PART_PIVOT_JOINT, PART_SEGMENTS, CharacterAnatomyAnalyzer,
                      ManualLandmarkEstimator, MaskGeometryPoseEstimator, collect_instances, validate_joints)
from .face import analyze_face
from .partition import partition
from .prompts import BODY_PROMPTS, FACE_PROMPTS, to_prompts
from .source import SourceImage, sha256_file

MARKER = "autorig.character_project"
FACE_LAYER_FEATURES = ("nose", "mouth", "eye_r", "eye_l", "brow_r", "brow_l")  # carved out of the head
Z_ORDER = ["foot_r", "shin_r", "thigh_r", "foot_l", "shin_l", "thigh_l", "pelvis", "neck", "torso",
           "upper_arm_r", "forearm_r", "hand_r", "upper_arm_l", "forearm_l", "hand_l", "head_base",
           "nose", "mouth", "eye_r", "eye_l", "brow_r", "brow_l", "hair"]
BONE_OF = {"head_base": "head", "nose": "head", "mouth": "head", "eye_r": "head", "eye_l": "head",
           "brow_r": "head", "brow_l": "head", "hair": "head"}
BONE_PARENT = {"pelvis": None, "torso": "pelvis", "neck": "torso", "head": "neck",
               "upper_arm_r": "torso", "forearm_r": "upper_arm_r", "hand_r": "forearm_r",
               "upper_arm_l": "torso", "forearm_l": "upper_arm_l", "hand_l": "forearm_l",
               "thigh_r": "pelvis", "shin_r": "thigh_r", "foot_r": "shin_r",
               "thigh_l": "pelvis", "shin_l": "thigh_l", "foot_l": "shin_l"}
CONTROL_DEPS = {"eye_r": ["eye_blink_right", "eye_look_x", "eye_look_y"],
                "eye_l": ["eye_blink_left", "eye_look_x", "eye_look_y"],
                "brow_r": ["brow_raise_right", "brow_frown"], "brow_l": ["brow_raise_left", "brow_frown"],
                "mouth": ["jaw_open", "mouth_width", "mouth_pucker", "smile_left", "smile_right"],
                "head_base": ["head_yaw", "head_pitch", "head_roll", "jaw_open"],
                "hair": ["head_yaw", "head_pitch", "head_roll"], "neck": ["head_roll"]}


class ProjectExistsError(RuntimeError):
    pass


@dataclass
class AnalysisConfig:
    min_score: float = 0.5
    confidence_threshold: float = 0.5
    face_upscale_target: int = 1024
    body_prompts: list[tuple[str, str]] = field(default_factory=lambda: list(BODY_PROMPTS))
    face_prompts: list[tuple[str, str]] = field(default_factory=lambda: list(FACE_PROMPTS))
    manual_landmarks: str | None = None
    character_id: str = "character"
    overwrite: bool = False
    complete_outline_px: int = 0  # opt-in silhouette outline completion (Phase 3.8); 0 = off


@dataclass
class ProjectResult:
    root: Path
    metadata: dict[str, Any]
    schema_errors: dict[str, list[str]]
    documents: dict[str, Any]

    @property
    def valid(self) -> bool:
        return not any(self.schema_errors.values())


def _prepare_dir(root: Path, overwrite: bool) -> None:
    if root.exists() and any(root.iterdir()):
        meta = root / "metadata.json"
        ours = meta.is_file() and json.loads(meta.read_text(encoding="utf-8")).get("schema") == MARKER
        if not ours:
            raise ProjectExistsError(f"{root} exists and is not an AutoRig character project; refusing to touch it")
        if not overwrite:
            raise ProjectExistsError(f"{root} already holds a character project; pass overwrite=True to replace it")
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)


def segment_region(provider, image_path: Path, source: SourceImage, box, prompts: list[Prompt],
                   work_dir: Path, upscale_target: int, confidence_threshold: float) -> SegmentationRun:
    """Segment a sub-region (e.g. the head) on an upscaled crop and map every mask
    back into ORIGINAL image coordinates. The crop is only a segmentation input;
    artwork always comes from the untouched source pixels."""
    if isinstance(provider, FixtureSegmentationProvider):
        run = provider.segment(str(image_path), prompts, confidence_threshold=confidence_threshold)
        run.provenance["region"] = {"box_xyxy": list(box), "method": "fixture_full_image"}
        return run
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(source.width, x1), min(source.height, y1)
    cw, ch = x1 - x0, y1 - y0
    scale = max(1.0, min(4.0, upscale_target / max(cw, ch)))
    crop = Image.fromarray(np.ascontiguousarray(source.rgba[y0:y1, x0:x1]), "RGBA")
    up = crop.resize((round(cw * scale), round(ch * scale)), Image.Resampling.LANCZOS)
    crop_path = work_dir / "face_crop.png"
    up.save(crop_path)
    sub = provider.segment(str(crop_path), prompts, confidence_threshold=confidence_threshold)
    results = []
    for res in sub.results:
        insts = []
        for inst in res.instances:
            small = M.resize_mask_area(inst.mask, (cw, ch))
            full = np.zeros((source.height, source.width), dtype=bool)
            full[y0:y1, x0:x1] = small
            bx = inst.box_xyxy
            insts.append(Instance(full, inst.score, (x0 + bx[0] / scale, y0 + bx[1] / scale, x0 + bx[2] / scale,
                                                     y0 + bx[3] / scale), inst.prompt_index, inst.instance_index))
        results.append(ConceptResult(res.prompt, insts))
    prov = dict(sub.provenance)
    prov["region"] = {"box_xyxy": [x0, y0, x1, y1], "upscale": round(scale, 4), "crop_image": "analysis/face_crop.png",
                      "method": "crop_upscale_segment_downscale (masks area-resampled back to source pixels)"}
    return SegmentationRun(sub.provider, source.sha256, (source.width, source.height), results, prov)


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5,
                              cwd=Path(__file__).resolve().parent).stdout.strip() or None
    except Exception:
        return None


def _write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _r(p):
    return None if p is None else [round(float(p[0]), 2), round(float(p[1]), 2)]


def analyze_character(image_path: str | Path, out_dir: str | Path, provider, config: AnalysisConfig | None = None
                      ) -> ProjectResult:
    cfg = config or AnalysisConfig()
    image_path, root = Path(image_path), Path(out_dir)
    t0 = time.time()
    source = SourceImage.load(image_path)
    _prepare_dir(root, cfg.overwrite)
    for sub in ("source", "analysis", "masks/body", "masks/face", "artwork/bitmap", "artwork/vector", "previews"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    src_record = source.copy_into(root / "source")
    Image.fromarray(source.rgba).save(root / "previews" / "original.png")
    shape = (source.height, source.width)
    steps, warnings = [], []
    img_meta = {"width": source.width, "height": source.height,
                "coordinate_system": "pixels; origin top-left; +x right; +y down", "source_sha256": source.sha256}

    # 1. body segmentation
    body_run = provider.segment(str(image_path), to_prompts(cfg.body_prompts),
                                confidence_threshold=cfg.confidence_threshold)
    labels = sorted({lab for lab, _ in cfg.body_prompts})
    labeled, rejected = collect_instances(body_run, labels, shape, min_score=cfg.min_score)
    steps.append({"step": "body_segmentation", "provider": body_run.provider,
                  "accepted": {k: len(v) for k, v in labeled.items()}, "rejected": len(rejected)})
    for lab in labels:
        if not labeled.get(lab):
            warnings.append(f"no accepted segmentation for body concept '{lab}'")
    silhouette = np.zeros(shape, dtype=bool)
    for items in labeled.values():
        for lm in items:
            silhouette |= lm.mask

    # 2. anatomy
    estimators = []
    if cfg.manual_landmarks:
        estimators.append(ManualLandmarkEstimator.from_file(cfg.manual_landmarks))
    estimators.append(MaskGeometryPoseEstimator())
    analyzer = CharacterAnatomyAnalyzer(estimators)
    ctx, side_flags = analyzer.build_context(shape, labeled, silhouette)
    joints, _ = analyzer.analyze(ctx)
    issues = validate_joints(joints, shape, silhouette)
    warnings += side_flags
    parts, pinfo = partition(ctx, joints)
    unassigned = pinfo.pop("unassigned_mask")
    warnings += pinfo.get("flags", [])
    steps.append({"step": "anatomy", "estimators": [e.name for e in estimators],
                  "unreliable_joints": [n for n, j in joints.items() if not j.reliable],
                  "reassigned_fragments": sum(g["pieces"] for g in pinfo.get("reassigned_fragments", []))})
    if pinfo.get("reassigned_fragments"):
        frag = pinfo["reassigned_fragments"]
        warnings.append(f"{sum(g['pieces'] for g in frag)} detached fragments ({sum(g['pixels'] for g in frag)} px) "
                        "of bone-assigned parts given to the part they touch (anatomy.json reassigned_fragments)")

    # 3. face
    head_full = ctx.head_region | parts["hair"]
    hb = M.bbox_xyxy(head_full)
    if hb:
        pad = 0.12 * max(hb[2] - hb[0], hb[3] - hb[1])
        face_box = (hb[0] - pad, hb[1] - pad, hb[2] + pad, hb[3] + pad)
        face_run = segment_region(provider, image_path, source, face_box, to_prompts(cfg.face_prompts),
                                  root / "analysis", cfg.face_upscale_target, cfg.confidence_threshold)
        face_labels = sorted({lab for lab, _ in cfg.face_prompts})
        face_labeled, face_rej = collect_instances(face_run, face_labels, shape, min_score=0.0,
                                                   min_area_frac=0.000002)
    else:
        face_run, face_labeled, face_rej = None, {}, []
        warnings.append("no head region found: facial analysis skipped")
    face = analyze_face(source.rgba, ctx.head_region, parts["hair"], face_labeled, min_score=cfg.min_score)
    # instance-level rejections use 'prompt_label'; the face schema requires 'label' on every entry
    face.rejected = [{"label": r.get("prompt_label"), **r} for r in face_rej] + face.rejected
    steps.append({"step": "face", "accepted_features": sorted(face.features), "rejected": len(face.rejected)})

    # 4. layer partition: head -> head_base + facial feature layers
    layer_masks: dict[str, np.ndarray] = {}
    carved = np.zeros(shape, dtype=bool)
    for fid in FACE_LAYER_FEATURES:
        if fid in face.features:
            m = face.features[fid].mask & parts["head"] & ~carved
            if M.area(m):
                layer_masks[fid] = m
                carved |= m
    layer_masks["head_base"] = parts["head"] & ~carved
    for p, m in parts.items():
        if p != "head":
            layer_masks[p] = m
    ordered = [n for n in Z_ORDER if n in layer_masks]
    # keep the anti-aliased linework fringe with the character (original colours)
    fg, fg_info = C.estimate_foreground(source.rgba)
    paper, paper_info = C.border_paper(source.rgba)
    if paper is not None:
        # Phase 3.8: on a uniform paper the character is everything not connected to the border through
        # paper-coloured pixels. The colour estimate's opening dropped the 1-2 px outline strip a mask clips, so the
        # fringe below never reached it; enclosed white clothing is character whatever its colour.
        layer_masks, removed = C.remove_border_paper(layer_masks, paper)
        fg = ~paper
        fg_info = {**fg_info, "method": "border_connected_paper", "colour_heuristic": fg_info.get("method"),
                   "tolerance": paper_info["tolerance"]}
        steps.append({"step": "silhouette_refinement", **paper_info, "background_pixels_removed": removed,
                      "note": "background = paper-coloured pixels connected to the border; the 2 px fringe is "
                              "measured against it. Colours stay original."})
        if cfg.complete_outline_px > 0:
            layer_masks, oc = C.complete_outline(layer_masks, source.rgba, paper, int(cfg.complete_outline_px))
            steps.append({"step": "outline_completion", "opt_in": True, **oc,
                          "note": "dark silhouette outline clipped by a mask, given to the nearest layer"})
    layer_masks, fringe_px = C.absorb_fringe(layer_masks, fg, max_px=2.0)
    for p in parts:
        if p != "head":
            parts[p] = layer_masks[p]
    parts["head"] = layer_masks["head_base"].copy()
    for fid in FACE_LAYER_FEATURES:
        if fid in layer_masks:
            parts["head"] |= layer_masks[fid]
    for p, m in parts.items():
        pinfo["parts"][p].update({"pixels": M.area(m), "bbox_xyxy": M.bbox_xyxy(m), "empty": M.area(m) == 0})
    steps.append({"step": "fringe_absorption", "pixels": fringe_px, "max_distance_px": 2.0,
                  "note": "anti-aliased edge pixels kept opaque with original colours; no matting yet"})

    # 5. write masks + extracted original-pixel layers
    for p, m in parts.items():
        M.save_mask(m, root / "masks" / "body" / f"{p}.png")
    face_mask_ids = sorted(set(face.features) | {"head_base"})
    for fid in face_mask_ids:
        m = layer_masks["head_base"] if fid == "head_base" else face.features[fid].mask
        M.save_mask(m, root / "masks" / "face" / f"{fid}.png")
    layer_rgba = []
    for name in ordered:
        rgba = M.extract_layer(source.rgba, layer_masks[name])
        Image.fromarray(rgba, "RGBA").save(root / "artwork" / "bitmap" / f"{name}.png", optimize=True)
        layer_rgba.append((name, rgba))
    (root / "artwork" / "vector" / "README.md").write_text(
        "# Vector correction layers\n\nEmpty in Phase 1. Future editable vector layers (e.g. reconstructed eyelids, "
        "mouth interiors) go here and are registered in `../layers.json` under `vector_corrections`. They never "
        "replace or approximate the original bitmap layers automatically.\n", encoding="utf-8")

    # 6. compositing + fidelity
    recon = C.compose(layer_rgba, shape)
    bg = tuple(fg_info.get("background_rgb", (0, 0, 0)))
    metrics = C.fidelity(source.rgba, recon, fg, layer_masks, bg)
    metrics["foreground_estimate"] = fg_info
    metrics["unassigned_character_pixels"] = M.area(unassigned)
    Image.fromarray(recon, "RGBA").save(root / "previews" / "reconstructed.png")
    union = np.zeros(shape, dtype=bool)
    for m in layer_masks.values():
        union |= m
    C.difference_image(source.rgba, recon, fg, union, bg).save(root / "previews" / "difference.png")
    pts = {n: j.xy for n, j in joints.items() if j.x is not None}
    lines = [(joints[c].xy, joints[p].xy) for c, p in JOINT_PARENTS.items()
             if p and joints[c].x is not None and joints[p].x is not None]
    C.overlay(source.rgba, {p: m for p, m in parts.items() if M.area(m)}, points=pts, lines=lines).save(
        root / "previews" / "segmentation.png")
    if hb:
        crop = (max(0, hb[0] - 10), max(0, hb[1] - 10), min(source.width, hb[2] + 10), min(source.height, hb[3] + 10))
        fpts = {k: v for k, v in face.landmarks.items() if isinstance(v, list) and len(v) == 2
                and all(isinstance(t, (int, float)) for t in v)}
        scale = max(1.0, min(3.0, 900 / max(crop[2] - crop[0], crop[3] - crop[1])))
        C.overlay(source.rgba, {k: f.mask for k, f in face.features.items()}, points=fpts, crop=crop,
                  scale=scale).save(root / "previews" / "facial_segmentation.png")
    else:
        Image.new("RGB", (200, 60), (255, 255, 255)).save(root / "previews" / "facial_segmentation.png")
    C.layer_sheet(layer_rgba).save(root / "previews" / "layers.png")

    # 7. readiness + live controls
    ready = readiness.evaluate(face, pinfo["parts"], joints)
    ready_doc = {"schema": "autorig.live_readiness", "schema_version": schemas.V, **ready}
    controls_doc = {"schema": "autorig.live_controls", "schema_version": schemas.V,
                    **live_controls.build(ready, set(face.features))}

    # 8. documents
    run_records = [body_run.provenance_record()] + ([face_run.provenance_record()] if face_run else [])
    overlaps = []
    flat = [(lab, i, lm) for lab, items in labeled.items() for i, lm in enumerate(items) if lab != "person"]
    for a in range(len(flat)):
        for b in range(a + 1, len(flat)):
            n = M.area(flat[a][2].mask & flat[b][2].mask)
            if n:
                overlaps.append({"a": f"{flat[a][0]}[{flat[a][1]}]", "b": f"{flat[b][0]}[{flat[b][1]}]", "pixels": n})
    bones = []
    for part, segs in PART_SEGMENTS.items():
        for a, b in segs:
            ja, jb = joints[a], joints[b]
            bones.append({"name": part if len(segs) == 1 else f"{part}:{b}", "from": a, "to": b,
                          "length_px": None if ja.x is None or jb.x is None else round(math.dist(ja.xy, jb.xy), 2)})
    anatomy_doc = {
        "schema": "autorig.anatomy", "schema_version": schemas.V, "image": img_meta,
        "side_convention": "front-facing: the character's LEFT (suffix _l) appears on the IMAGE RIGHT",
        "facing_assumption": "front", "midline_x": round(ctx.midline_x, 2),
        "joints": {n: j.record() for n, j in joints.items()},
        "bones": bones,
        "parts": {p: {**info, "mask": f"masks/body/{p}.png",
                      "pivot": {"position": _r(joints[PART_PIVOT_JOINT[p]].xy) if PART_PIVOT_JOINT.get(p) else None,
                                "joint": PART_PIVOT_JOINT.get(p), "status": "proposed"}}
                  for p, info in pinfo["parts"].items()},
        "clothing_regions": pinfo["clothing_regions"],
        "unassigned_pixels": pinfo["unassigned_pixels"],
        "reassigned_fragments": pinfo.get("reassigned_fragments", []),
        "occlusion": {"joints_occluded": [{"joint": n, "occluded_by": j.occluded_by}
                                          for n, j in joints.items() if j.occluded_by],
                      "instance_overlaps": overlaps},
        "validation": {"issues": issues, "unreliable_joints": [n for n, j in joints.items() if not j.reliable]},
        "segmentation": {"accepted_instances": {k: len(v) for k, v in labeled.items()},
                         "rejected_instances": rejected, "run": body_run.provenance_record()},
        "estimators": [e.name for e in estimators],
    }
    feats = {fid: {"label": f.label, "method": f.method, "score": round(min(1.0, f.score), 4),
                   "pixels": M.area(f.mask), "bbox_xyxy": M.bbox_xyxy(f.mask), "centroid": _r(M.centroid(f.mask)),
                   "mask": f"masks/face/{fid}.png", "provenance": f.provenance}
             for fid, f in face.features.items()}
    L = face.landmarks
    face_doc = {
        "schema": "autorig.face", "schema_version": schemas.V, "image": img_meta,
        "head_bbox_xyxy": face.head_bbox, "midline_x": None if face.midline_x is None else round(face.midline_x, 2),
        "features": feats, "rejected_candidates": face.rejected, "landmarks": L,
        "eye_centers": {s: L.get(f"eye_center_{s}") for s in ("r", "l")},
        "eyebrow_positions": {s: {k: L.get(f"brow_{k}_{s}") for k in ("center", "inner", "outer")} for s in ("r", "l")},
        "mouth": {k: L.get(k) for k in ("mouth_center", "mouth_corner_r", "mouth_corner_l", "mouth_state")},
        "jaw_pivot": L.get("jaw_pivot"),
        "visibility": face.visibility,
        "segmentation_confidence": {fid: round(min(1.0, f.score), 4) for fid, f in face.features.items()},
        "missing_artwork": face.missing_artwork, "notes": face.notes,
        "segmentation": face_run.provenance_record() if face_run else {},
    }
    layers = []
    for z, name in enumerate(ordered):
        m = layer_masks[name]
        bone = BONE_OF.get(name, name)
        pj = PART_PIVOT_JOINT.get(name if name in PART_PIVOT_JOINT else bone)
        if name in face.features:
            ppos, psrc = M.centroid(face.features[name].mask), f"feature_centroid:{name}"
        else:
            ppos, psrc = (joints[pj].xy if pj else None), f"anatomy_joint:{pj}"
        role = "head_base" if name == "head_base" else ("facial_feature" if name in face.features else "body_part")
        mask_path = f"masks/face/{name}.png" if role != "body_part" else f"masks/body/{name}.png"
        if role == "body_part":
            M.save_mask(m, root / mask_path)  # partitioned layer mask (equals masks/body/<part>.png)
        layers.append({
            "id": name, "kind": "extracted_original_pixels", "role": role,
            "image": f"artwork/bitmap/{name}.png", "mask": mask_path, "bbox_xyxy": M.bbox_xyxy(m), "z_index": z,
            "pixels": M.area(m),
            "pivot": {"position": _r(ppos), "status": "proposed" if ppos else "missing", "source": psrc},
            "bone": {"name": bone, "parent_bone": BONE_PARENT.get(bone)},
            "provenance": {"source_sha256": source.sha256,
                           "method": "facial feature mask within head" if role == "facial_feature" else (
                               "head minus carved facial features" if role == "head_base" else
                               pinfo["parts"].get(name, {}).get("method")),
                           "segmentation_runs": [r.get("prediction_id") or r.get("provider") for r in run_records]},
            "transform": {"scale": 1.0, "offset": [0, 0], "note": "pixels copied 1:1 from the source"},
            "facial_control_dependencies": CONTROL_DEPS.get(name, []),
            "vector_replacement": None, "status": "extracted" if M.area(m) else "empty",
        })
    mask_entries = [{"id": f"body/{p}", "path": f"masks/body/{p}.png", "kind": "anatomical_part", "pixels": M.area(m)}
                    for p, m in parts.items()]
    mask_entries += [{"id": f"face/{fid}", "path": f"masks/face/{fid}.png", "kind": "facial_feature",
                      "pixels": M.area(layer_masks["head_base"] if fid == "head_base" else face.features[fid].mask)}
                     for fid in face_mask_ids]
    layers_doc = {
        "schema": "autorig.hybrid_artwork", "schema_version": schemas.V, "contract_version": schemas.V,
        "coordinate_system": {"units": "source pixels", "origin": "top-left, +y down", "width": source.width,
                              "height": source.height, "note": "every layer is full-canvas; no per-layer scaling"},
        "original_bitmap": {"path": src_record["project_path"], "sha256": source.sha256,
                            "role": "reference_only_never_modified"},
        "layers": layers, "masks": mask_entries, "generated_artwork": [], "vector_corrections": [],
        "categories": {
            "original_bitmap": "source/ - the untouched reference",
            "extracted_original_pixel_layers": "layers[] with kind extracted_original_pixels (artwork/bitmap/)",
            "generated_or_reconstructed_artwork": "generated_artwork[] - empty in Phase 1 (no reconstruction yet)",
            "editable_vector_correction_layers": "vector_corrections[] - empty; files go to artwork/vector/",
            "masks": "masks[] (masks/body, masks/face)",
            "bone_attachment_metadata": "layers[].bone and layers[].pivot",
            "facial_control_dependencies": "layers[].facial_control_dependencies + analysis/live_controls.json",
        },
    }
    docs = {"anatomy": (root / "analysis" / "anatomy.json", anatomy_doc),
            "face": (root / "analysis" / "face.json", face_doc),
            "live_readiness": (root / "analysis" / "live_readiness.json", ready_doc),
            "live_controls": (root / "analysis" / "live_controls.json", controls_doc),
            "hybrid_artwork": (root / "artwork" / "layers.json", layers_doc)}
    errors = {name: schemas.validate(name, doc) for name, (_, doc) in docs.items()}
    for name, (path, doc) in docs.items():
        _write_json(path, doc)
    (root / "analysis" / "live_readiness.md").write_text(readiness.to_markdown(ready, cfg.character_id),
                                                         encoding="utf-8")

    unchanged = source.verify_unchanged()
    if not unchanged:  # pragma: no cover - would indicate a bug or external modification
        warnings.append("SOURCE FILE CHANGED DURING PROCESSING")
    est = sum(float(r.get("estimated_usd") or 0.0) for r in run_records if not r.get("cache_hit"))
    ledger_total = None
    if hasattr(provider, "ledger"):
        ledger_total = provider.ledger.total_usd()
    outputs = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name != "metadata.json":
            outputs.append({"path": p.relative_to(root).as_posix(), "sha256": sha256_file(p), "bytes": p.stat().st_size})
    metadata = {
        "schema": MARKER, "schema_version": schemas.V, "autorig_version": __version__,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "git_commit": _git_commit(),
        "character_id": cfg.character_id, "source": src_record, "source_unchanged_after_processing": unchanged,
        "pipeline": {"steps": steps, "config": {k: v for k, v in cfg.__dict__.items()},
                     "duration_s": round(time.time() - t0, 2)},
        "segmentation_runs": [{"provider": r.get("provider"), "model": r.get("model"), "version": r.get("version"),
                               **{k: v for k, v in r.items() if k not in ("provider", "model", "version")}}
                              for r in run_records],
        "cost": {"this_run_estimated_usd": round(est, 6), "ledger_total_estimated_usd": ledger_total,
                 "basis": getattr(getattr(provider, "policy", None), "rate_source", "no paid inference (fixture)")},
        "fidelity": metrics, "outputs": outputs, "schema_validation": {k: v or "ok" for k, v in errors.items()},
        "warnings": warnings,
        "limitations": [
            "Joint positions are approximate; unreliable ones are flagged and need manual review.",
            "No hidden artwork was reconstructed or invented (Phase 1 scope).",
            "Moho SHOWTIME input mappings are proposals only; none is verified.",
            "Edge pixels are kept opaque with their original (background-blended) colours; placing a layer on a "
            "different background can show a faint halo until alpha matting is added.",
            "Phase 0 Moho integration has not been verified inside Moho.",
        ],
    }
    errors["character_project"] = schemas.validate("character_project", metadata)
    metadata["schema_validation"]["character_project"] = errors["character_project"] or "ok"
    _write_json(root / "metadata.json", metadata)
    return ProjectResult(root, metadata, errors, {k: d for k, (_, d) in docs.items()} | {"metadata": metadata})
