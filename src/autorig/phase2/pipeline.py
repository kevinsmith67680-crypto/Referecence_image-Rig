"""Phase 2 pipeline: Phase 1 character_project (read-only) -> Phase 2 character package."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from .. import __version__
from ..phase1 import compositor as C
from ..phase1 import masks as M
from ..phase1.compositor import _ssim_gray
from ..phase1.source import sha256_file
from . import facial, joints as J, review, schemas
from .occlusion import trim_regions_to_cover, OcclusionAnalyzer, resolve_z_order
from .pose import POSES, RIG, PoseEngine, bone_of, diagnostic_image
from .reconstruct import ReconstructionConfig, ReconstructionEngine, outline_props
from .store import ArtworkStore
from .vector import make_vector_assets

MARKER = "autorig.character_package"


class PackageError(RuntimeError):
    pass


@dataclass
class Phase2Config:
    recon: ReconstructionConfig = field(default_factory=ReconstructionConfig)
    sweep_step: int = 30
    overrides: dict[str, Any] = field(default_factory=dict)
    overwrite: bool = False
    synthetic_ground_truth: bool = False


def _w(path: Path, d: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, indent=2, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)) + "\n",
                    encoding="utf-8")


def _prepare(root: Path, overwrite: bool) -> None:
    if root.exists() and any(root.iterdir()):
        meta = root / "metadata.json"
        ours = meta.is_file() and json.loads(meta.read_text()).get("schema") == MARKER
        if not ours:
            raise PackageError(f"{root} exists and is not an AutoRig character package; refusing to touch it")
        if not overwrite:
            raise PackageError(f"{root} already holds a package; pass overwrite=True")
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)


def prepare_character(phase1_dir: str | Path, out_dir: str | Path, provider=None, config: Phase2Config | None = None):
    cfg = config or Phase2Config()
    p1, root = Path(phase1_dir), Path(out_dir)
    t0 = time.time()
    meta1 = json.loads((p1 / "metadata.json").read_text())
    src_sha = meta1["source"]["sha256"]
    if sha256_file(p1 / "source" / "original.png") != src_sha:
        raise PackageError("Phase 1 source/original.png does not match its recorded SHA-256")
    _prepare(root, cfg.overwrite)
    warnings: list[str] = []
    (root / "source").mkdir()
    shutil.copyfile(p1 / "source" / "original.png", root / "source" / "original.png")
    for name in ("anatomy.json", "face.json", "live_controls.json"):
        (root / "analysis").mkdir(exist_ok=True)
        shutil.copyfile(p1 / "analysis" / name, root / "analysis" / name)
    shutil.copytree(p1 / "masks", root / "masks" / "source")
    src = np.asarray(Image.open(root / "source" / "original.png").convert("RGBA"))
    H, W = src.shape[:2]
    anatomy = json.loads((p1 / "analysis" / "anatomy.json").read_text())
    face = json.loads((p1 / "analysis" / "face.json").read_text())
    joints = {k: dict(v) for k, v in anatomy["joints"].items()}
    for jn, xy in cfg.overrides.get("pivots", {}).items():  # reviewer pivot corrections
        joints[jn].update(position=list(xy), reliable=True, method="manual_review_override")
    layers1 = json.loads((p1 / "artwork" / "layers.json").read_text())["layers"]

    # A: immutable originals (eyes split pixel-exactly into white + iris)
    rgba = {l["id"]: np.asarray(Image.open(p1 / l["image"]).convert("RGBA")) for l in layers1}
    empty = sorted(c for c, a in rgba.items() if not (a[..., 3] > 0).any())
    for c in empty:  # a part Phase 1 could not find has nothing to preserve, occlude or pose
        del rgba[c]
        warnings.append(f"Phase 1 layer '{c}' is empty (part not found); it is excluded from Phase 2 and needs "
                        "manual landmarks or artwork")
    face_masks = {p.stem: M.load_mask(p) for p in (p1 / "masks" / "face").glob("*.png")}
    rgba, notes = facial.split_eyes(rgba, face_masks)
    warnings += notes
    z1 = {l["id"]: l["z_index"] for l in layers1}
    for s in ("r", "l"):
        if f"eye_{s}_white" in rgba:
            z1[f"eye_{s}_white"], z1[f"eye_{s}_iris"] = z1[f"eye_{s}"], z1[f"eye_{s}"] + 0.5
    store = ArtworkStore(root, W, H)
    for cid, arr in rgba.items():
        src_layer = cid.rsplit("_", 1)[0] if cid.endswith(("_white", "_iris")) else cid
        store.add_original(cid, arr, {"from_phase1_layer": f"artwork/bitmap/{src_layer}.png",
                                      "phase1_file_sha256": sha256_file(p1 / "artwork" / "bitmap" / f"{src_layer}.png"),
                                      "split": "eye white/iris split of original pixels" if src_layer != cid else None})
    comps = {c: a[..., 3] > 0 for c, a in rgba.items()}

    # occlusion
    oa = OcclusionAnalyzer(src, comps, z1, joints, face_masks, cfg.overrides)
    edges = oa.edges()
    regions = oa.regions(edges)
    order, dropped = resolve_z_order(list(comps), edges, z1)
    expected = {c: oa.expected_shape(c)[0] for c in comps}
    regions, notes = trim_regions_to_cover(regions, order, {c: a[..., 3] == 255 for c, a in rgba.items()})
    warnings += notes

    # reconstruction
    engine = ReconstructionEngine(store, src, comps, expected, oa.dark, provider, cfg.recon)
    recon = engine.run(regions)
    store.save()
    for r in regions:
        M.save_mask(r.mask, root / "masks" / "reconstruction" / f"{r.id}.png")
    new_art = [{"asset": m["artwork"], "reason": m["reason"], "needed_for": m["needed_for"]}
               for m in face.get("missing_artwork", []) if not m["artwork"].startswith(("skin_under", "sclera_fill",
                                                                                       "neck_extension"))]
    new_art += [{"asset": a, "reason": "a single front view does not reveal it", "needed_for": n}
                for a, n in (("teeth", ["jaw_open", "lip_sync"]), ("tongue", ["jaw_open", "lip_sync"]),
                             ("upper_eyelid_r", ["eye_blink_right"]), ("upper_eyelid_l", ["eye_blink_left"]),
                             ("lower_eyelid_r", ["eye_blink_right"]), ("lower_eyelid_l", ["eye_blink_left"]))]
    occ_doc = {"schema": "autorig.occlusion", "schema_version": schemas.V,
               "image": {"width": W, "height": H, "coordinate_system": "source pixels, origin top-left, +y down"},
               "graph": {"nodes": {c: {"pixels": M.area(m), "bone": bone_of(c)} for c, m in comps.items()},
                         "edges": edges, "z_order_back_to_front": order,
                         "edges_dropped_to_break_cycles": [f"{e['front']}>{e['back']}" for e in dropped],
                         "z_order_changes_vs_phase1": [f"{e['front']} now in front of {e['back']}" for e in edges
                                                       if e["confidence"] >= 0.5 and not e["consistent_with_phase1_z_order"]]},
               "regions": [{"id": r.id, "owner": r.owner, "occluded_by": r.occluded_by,
                            "mask": f"masks/reconstruction/{r.id}.png", "pixels": M.area(r.mask),
                            "bbox_xyxy": M.bbox_xyxy(r.mask), "joint": r.joint, "exposed_by": r.exposed_by,
                            "method": r.method, "confidence": r.confidence, "ambiguous": r.ambiguous,
                            "strategy": r.strategy, "notes": r.notes} for r in regions],
               "new_artwork_required": new_art, "flags": oa.flags,
               "overrides_applied": {k: (list(v) if not isinstance(v, dict) else sorted(v)) for k, v in cfg.overrides.items()}}

    # layers for poses
    composed = {c: store.compose_component(c) for c in comps}
    lum = lambda a: a[..., :3].astype(np.float32) @ np.array([.299, .587, .114], np.float32)
    dark_of = {c: (composed[c][..., 3] > 127) & (lum(composed[c]) < 100) for c in comps}
    radius = {}
    for b, (pivot, _, cs) in RIG.items():
        c = next((x for x in cs if x in comps), None)
        if c is None or not comps[c].any():
            continue
        dt = ndimage.distance_transform_edt(comps[c])
        radius[b] = float(np.clip(np.percentile(dt[comps[c]], 90), 8, 90))
    radius["head"] = radius.get("body", 30.0) * 0.4
    pivots = {k: (tuple(v["position"]) if v.get("position") else None) for k, v in joints.items()}
    eng_o = PoseEngine(rgba, order, pivots, expected, dark_of, radius)
    eng_e = PoseEngine(composed, order, pivots, expected, dark_of, radius)
    (root / "previews" / "diagnostics").mkdir(parents=True, exist_ok=True)
    pose_results = {}
    for name, angles in POSES.items():
        ro, re_ = eng_o.render(name, angles), eng_e.render(name, angles)
        Image.fromarray(re_.image, "RGBA").save(root / "previews" / f"{name}.png", optimize=True)
        diagnostic_image(re_).save(root / "previews" / "diagnostics" / f"{name}.png", optimize=True)
        diagnostic_image(ro).save(root / "previews" / "diagnostics" / f"{name}__originals_only.png", optimize=True)
        pose_results[name] = {"with_extensions": re_.diagnostics, "originals_only": ro.diagnostics}
    Image.fromarray(eng_e.render("neutral", {}).image, "RGBA").save(root / "previews" / "neutral.png")

    # joints
    ext_records = [{**asdict(e), "joint": next((r.joint for r in regions if r.id == e.region_id), None)}
                   for e in store.extensions]
    jprep = J.prepare_joints(eng_o, eng_e, joints, order, edges, ext_records, step=cfg.sweep_step)
    _w(root / "analysis" / "joint_preparation.json",
       {"schema": "autorig.joint_preparation", "schema_version": schemas.V, "joints": jprep,
        "notes": ["Rigid 2D rotation tests only; native Moho bindings are not created in Phase 2.",
                  "Pivots come from Phase 1; unreliable ones are marked needs_review."]})

    # vectors + live manifest
    flat_info = {}
    for r in recon:
        if r["strategy"] == "deterministic_flat_fill" and r["owner"] not in flat_info:
            e = next(x for x in store.extensions if x.id == r["extensions"][0])
            col, width = outline_props(src, comps[r["owner"]], oa.dark)
            flat_info[r["owner"]] = {"fill_rgb": e.provenance["fill_rgb"], "outline_rgb": list(col), "outline_width": width}
    vectors = make_vector_assets(root, W, H, jprep, flat_info, face)
    live = facial.build_live_manifest(comps, recon, jprep, face, vectors, comps.get("hair"))
    live_doc = {"schema": "autorig.live_artwork_manifest", "schema_version": schemas.V, **live}
    _w(root / "analysis" / "live_artwork_manifest.json", live_doc)

    # quality
    quality, images = _quality(root, src, store, rgba, composed, order, comps, recon, regions, pose_results, jprep,
                               edges, meta1, eng_o, eng_e)
    if cfg.synthetic_ground_truth:
        from .evaluate import evaluate_against_synthetic
        quality["synthetic_ground_truth"] = evaluate_against_synthetic(store, regions)
    q_doc = {"schema": "autorig.reconstruction_quality", "schema_version": schemas.V, **quality, "images": images}
    _w(root / "reports" / "reconstruction_quality.json", q_doc)

    # review
    items = review.build_items(store, vectors, jprep, edges, live["switch_drawing_slots"])
    review.write_approvals(root, items)
    review.write_review_html(root, items, {"reference vs reconstruction": images["comparison"],
                                           "pose contact sheet": images["pose_contact_sheet"],
                                           "joint close-ups": images["joint_closeups"],
                                           "alpha / provenance overlay": images["alpha_overlay"]},
                             {"generated_pixels_total": quality["reconstruction_size"]["generated_pixels_total"],
                              "flags": len(quality["flags_for_human_review"]),
                              "evidence": "synthetic fixture only" if cfg.synthetic_ground_truth else "see metadata"})

    # canonical artwork manifest
    zidx = {c: i for i, c in enumerate(order)}
    art = {"schema": "autorig.artwork_manifest", "schema_version": schemas.V, "contract_version": "2.0.0",
           "coordinate_system": {"width": W, "height": H, "origin": "top-left, +y down, source pixels",
                                 "rule": "all bitmap, mask and vector assets share this canvas; no scaling/offsets"},
           "original_bitmap": {"path": "source/original.png", "sha256": src_sha},
           "layers": [{"id": c, "category": "A_original", "image": store.originals[c]["path"], "z_index": zidx[c],
                       "bone": bone_of(c), "pivot": (lambda pv: list(pv) if pv else None)(
                           pivots.get(RIG[bone_of(c)][0]) if bone_of(c) and RIG[bone_of(c)][0] else None),
                       "pixel_sha256": store.originals[c]["pixel_sha256"], "provenance": store.originals[c]["provenance"]}
                      for c in order],
           "extensions": [{"id": e.id, "component": e.component, "category": "B_generated", "image": e.image,
                           "provenance_mask": e.provenance_mask, "method": e.method, "status": e.status,
                           "pixels": e.pixels, "region": e.region_id, "drawn": "under its original component",
                           "provenance": e.provenance} for e in store.extensions],
           "corrections": [], "vectors": vectors,
           "masks": {"source": "masks/source/", "reconstruction": "masks/reconstruction/", "provenance": "masks/provenance/"}}
    _w(root / "artwork" / "manifest.json", art)
    _w(root / "analysis" / "occlusion.json", occ_doc)
    store.clear_working()

    docs = {"occlusion": occ_doc, "joint_preparation": json.loads((root / "analysis" / "joint_preparation.json").read_text()),
            "live_artwork_manifest": live_doc, "artwork_manifest": art, "reconstruction_quality": q_doc}
    errors = {k: schemas.validate(k, v) for k, v in docs.items()}
    problems = store.verify()
    src_ok = sha256_file(root / "source" / "original.png") == src_sha
    outputs = [{"path": p.relative_to(root).as_posix(), "sha256": sha256_file(p), "bytes": p.stat().st_size}
               for p in sorted(root.rglob("*")) if p.is_file() and p.name != "metadata.json"]
    led = getattr(provider, "ledger", None)
    meta = {"schema": MARKER, "schema_version": schemas.V, "phase": 2, "autorig_version": __version__,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": {"sha256": src_sha, "byte_identical_copy": src_ok, "path": "source/original.png"},
            "phase1_project": {"path_name": p1.name, "metadata_sha256": sha256_file(p1 / "metadata.json"),
                               "segmentation_provider": meta1["segmentation_runs"][0]["provider"]},
            "providers": {"reconstruction": getattr(provider, "name", None) or "deterministic only (no generative provider)",
                          "adapter": asdict(provider.adapter) if hasattr(provider, "adapter") else None},
            "config": {"recon": {k: (sorted(v) if isinstance(v, set) else v) for k, v in asdict(cfg.recon).items()},
                       "sweep_step": cfg.sweep_step, "overrides": occ_doc["overrides_applied"]},
            "cost": {"estimated_usd_total_ledger": led.total_usd() if led else 0.0,
                     "ceiling_usd": led.policy.ceiling_usd if led else None},
            "verification": {"store_problems": problems, "source_unchanged": src_ok},
            "evidence_level": {"real_reference_evaluated": False,
                               "generative_inpainting_tested": bool(getattr(provider, "adapter", None)) and bool(led and led.entries),
                               "segmentation_provider": meta1["segmentation_runs"][0]["provider"],
                               "note": "fixture/synthetic evidence does not demonstrate real cartoon artwork quality"},
            "schema_validation": {k: v or "ok" for k, v in errors.items()}, "outputs": outputs,
            "duration_s": round(time.time() - t0, 1), "warnings": warnings}
    errors["character_package"] = schemas.validate("character_package", meta)
    meta["schema_validation"]["character_package"] = errors["character_package"] or "ok"
    _w(root / "metadata.json", meta)
    return {"root": root, "metadata": meta, "errors": errors, "docs": docs, "store": store, "regions": regions,
            "recon": recon, "poses": pose_results, "valid": not any(errors.values()) and not problems and src_ok}


def _quality(root, src, store, originals, composed, order, comps, recon, regions, poses, jprep, edges, meta1,
             eng_o, eng_e):
    H, W = src.shape[:2]
    rep = root / "reports"
    (rep / "change_maps").mkdir(parents=True, exist_ok=True)
    neutral_o = C.compose([(c, originals[c]) for c in order], (H, W))
    neutral_e = C.compose([(c, composed[c]) for c in order], (H, W))
    union = neutral_o[..., 3] > 0
    exact = bool(np.array_equal(neutral_o[union], src[union]))
    vis_gen = int(((neutral_e != neutral_o).any(axis=2)).sum())
    gen_all = store.generated_mask()
    g = lambda a: a[..., :3].astype(np.float64) @ np.array([.299, .587, .114])
    fg = union
    ssim = round(_ssim_gray(np.where(fg, g(neutral_e), 0), np.where(fg, g(src), 0)), 5)
    for c in comps:
        if any(e.component == c for e in store.extensions):
            store.change_map(c).save(rep / "change_maps" / f"{c}.png", optimize=True)
    gen_by = {}
    for e in store.extensions:
        gen_by[e.component] = gen_by.get(e.component, 0) + e.pixels
    pose_gaps = {n: {"originals_only": v["originals_only"]["gap_pixels_total"],
                     "with_extensions": v["with_extensions"]["gap_pixels_total"]} for n, v in poses.items()}
    seams = {j: {"max_gap_with_extensions": max([s["gaps_with_extensions"] or 0 for s in v["sweep"]] or [0]),
                 "max_gap_originals_only": max([s["gaps_original_only"] or 0 for s in v["sweep"]] or [0]),
                 "safe_range_deg": v["safe_range_deg"], "status": v["status"]} for j, v in jprep.items()}
    contour = {n: {j: d.get("dark_contour_pieces") for j, d in v["with_extensions"]["joints"].items()}
               for n, v in poses.items()}
    flags = []
    for e in edges:
        if e["ambiguous"]:
            flags.append({"item": f"edge {e['front']}>{e['back']}", "why": f"ambiguous ({e['method']}, conf {e['confidence']})"})
    for r in regions:
        if r.strategy in ("deterministic_flat_fill", "generative") and (r.confidence < 0.6 or r.ambiguous):
            flags.append({"item": f"region {r.id}", "why": f"low-confidence shape model ({r.method}, {r.confidence})"})
        if r.strategy.startswith("generative_") and r.strategy != "generative":
            flags.append({"item": f"region {r.id}", "why": r.strategy})
    for c, n in gen_by.items():
        if n > 0.25 * store.originals[c]["pixels"]:
            flags.append({"item": f"component {c}", "why": f"generated pixels = {n / store.originals[c]['pixels']:.0%} of original"})
    for j, v in jprep.items():
        for i in v["issues"]:
            flags.append({"item": f"joint {j}", "why": i})
    # images
    def heat(a, b):
        err = np.abs(a[..., :3].astype(int) - b[..., :3].astype(int)).max(axis=2)
        out = np.stack([g(b) * 0.3] * 3, -1).astype(np.uint8)
        out[err > 0] = (255, 40, 0)
        return Image.fromarray(out)
    cmap = np.full((H, W, 3), 255, np.uint8)
    cmap[union] = (160, 160, 160)
    cmap[gen_all & ~union] = (230, 0, 160)
    cmap[gen_all & union] = (120, 0, 90)  # generated but hidden under other originals in neutral
    bgc = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    over = bgc.copy()
    over.alpha_composite(Image.fromarray(neutral_e))
    tiles = [Image.fromarray(src).convert("RGB"), over.convert("RGB"), Image.fromarray(cmap), heat(neutral_e, src)]
    s = 0.4
    tw, th = int(W * s), int(H * s)
    sheet = Image.new("RGB", (tw * 4 + 30, th + 24), "white")
    d = ImageDraw.Draw(sheet)
    for i, (t, label) in enumerate(zip(tiles, ["reference", "neutral reconstruction", "provenance (magenta=generated)",
                                                "difference (red=changed)"])):
        sheet.paste(t.resize((tw, th)), (i * (tw + 10), 24))
        d.text((i * (tw + 10) + 4, 4), label, fill=(0, 0, 0))
    sheet.save(rep / "comparison.png", optimize=True)
    Image.fromarray(cmap).save(rep / "alpha_overlay.png", optimize=True)
    heat(neutral_e, src).save(rep / "difference.png", optimize=True)
    # joint close-ups: worst angle of each joint, originals only vs with extensions
    tiles = []
    for j, v in jprep.items():
        if not v["pivot"] or not v["sweep"]:
            continue
        worst = max(v["sweep"], key=lambda x: x["gaps_original_only"] or 0)
        b = v["bone"]
        ro = eng_o.render("c", {b: worst["angle_deg"]})
        re_ = eng_e.render("c", {b: worst["angle_deg"]})
        x, y = v["pivot"]
        R = int(v["overlap_padding_px"] * 1.6) + 10
        box = (int(x - R), int(y - R), int(x + R), int(y + R))
        pair = Image.new("RGB", (2 * 160 + 6, 160 + 16), "white")
        pair.paste(diagnostic_image(ro, box).resize((160, 160)), (0, 16))
        pair.paste(diagnostic_image(re_, box).resize((160, 160)), (166, 16))
        ImageDraw.Draw(pair).text((2, 2), f"{j} {worst['angle_deg']:+d}deg: orig | +ext", fill=(0, 0, 0))
        tiles.append(pair)
    cols = 4
    js = Image.new("RGB", (cols * 330, ((len(tiles) + cols - 1) // cols) * 180 or 10), "white")
    for i, t in enumerate(tiles):
        js.paste(t, ((i % cols) * 330, (i // cols) * 180))
    js.save(rep / "joint_closeups.png", optimize=True)
    names = list(poses)
    pw, ph = int(W * 0.3), int(H * 0.3)
    ps = Image.new("RGB", (pw * 5, ph * ((len(names) + 4) // 5)), "white")
    for i, n in enumerate(names):
        im = Image.open(root / "previews" / "diagnostics" / f"{n}.png").resize((pw, ph))
        ps.paste(im, ((i % 5) * pw, (i // 5) * ph))
    ps.save(rep / "pose_contact_sheet.png", optimize=True)
    quality = {
        "original_pixel_preservation": {"source_unchanged": sha256_file(root / "source" / "original.png") ==
                                        meta1["source"]["sha256"], "originals_verified": not store.verify(),
                                        "neutral_originals_exact": exact,
                                        "generated_pixels_visible_in_neutral": vis_gen,
                                        "rule": "generated pixels are drawn under their own original and behind occluders"},
        "neutral_similarity": {"ssim_gray": ssim, "note": "similarity is not proof of animation quality"},
        "alpha_coverage": {"original_union_pixels": M.area(union),
                           "phase1_coverage_ratio": meta1["fidelity"]["coverage_ratio"]},
        "gaps": {"per_pose": pose_gaps},
        "joint_seams": seams,
        "layer_alignment": {"canvas": [W, H], "all_layers_full_canvas": all(a.shape[:2] == (H, W) for a in composed.values()),
                            "offsets": "none (identity transform for every layer)"},
        "reconstruction_size": {"generated_pixels_total": int(sum(gen_by.values())), "by_component": gen_by,
                                "regions": {r.id: {"pixels": M.area(r.mask), "strategy": r.strategy} for r in regions}},
        "provenance": {"extensions": len(store.extensions),
                       "every_generated_pixel_has_provenance_mask": not any("provenance" in p for p in store.verify()),
                       "methods": sorted({e.method for e in store.extensions})},
        "outline_discontinuities": {"dark_contour_pieces_per_pose_joint": contour},
        "pose_stability": {"poses": len(poses), "worst_pose_gap_with_extensions": max(v["with_extensions"] for v in pose_gaps.values())},
        "flags_for_human_review": flags,
    }
    images = {"comparison": "reports/comparison.png", "alpha_overlay": "reports/alpha_overlay.png",
              "difference": "reports/difference.png", "joint_closeups": "reports/joint_closeups.png",
              "pose_contact_sheet": "reports/pose_contact_sheet.png", "change_maps": "reports/change_maps/"}
    return quality, images
