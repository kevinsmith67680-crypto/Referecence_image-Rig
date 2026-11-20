"""Natural pose refinement of a Phase 2 character package.

`refine_package(package_dir)` builds the anatomical skeleton, solves the preset library, renders each
preset with local skinned deformation and with the original rigid engine for comparison, evaluates
both, and writes everything under ``<package>/natural_pose/``. It never edits the package's original
or generated artwork.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from . import pose as legacy
from . import corrective as C
from .pose_eval import closeups, diagnostic, evaluate, gap_mask, on_bg
from .posing import PRESETS, Preset, PoseSolver, wrap
from .skeleton import LIMITS, SIDES, build_skeleton, layer_class
from .skinning import Deformer, PoseParams
from .store import ArtworkStore

OUT = "natural_pose"
SHEET = ["relaxed_standing", "both_hands_raised", "pointing_speaking", "casual_walk", "running", "sitting",
         "natural_squat", "head_tilt_compensated", "conversational_gesture"]
OLD_PARENT = {"head": None}
for _s in SIDES:
    OLD_PARENT.update({f"upper_arm_{_s}": None, f"forearm_{_s}": f"upper_arm_{_s}", f"hand_{_s}": f"forearm_{_s}",
                       f"thigh_{_s}": None, f"shin_{_s}": f"thigh_{_s}", f"foot_{_s}": f"shin_{_s}"})


def load(pkg: Path) -> dict[str, Any]:
    src = Image.open(pkg / "source" / "original.png")
    W, H = src.size
    store = ArtworkStore(pkg, W, H)
    layers = {c: store.compose_component(c) for c in store.originals}
    gen = {c: store.generated_mask(c) for c in store.originals}
    joints = json.loads((pkg / "analysis" / "anatomy.json").read_text())["joints"]
    order = json.loads((pkg / "analysis" / "occlusion.json").read_text())["graph"]["z_order_back_to_front"]
    return {"source": np.asarray(src.convert("RGBA")), "layers": layers, "generated": gen, "joints": joints,
            "order": [c for c in order if c in layers], "W": W, "H": H}


def natural_order(order: list[str], pr: Preset, available: set[str] | None = None) -> list[str]:
    """Adaptive layering: legs, then pelvis/neck/torso, arms in front of the body (behind the head unless
    the preset says otherwise), then the head group; preset 'front' layers last. Hand switch drawings
    named by the preset replace the reference hand when they are ``available``."""
    legs = [c for c in order if layer_class(c).startswith("leg")]
    body = [c for c in order if c in ("pelvis", "neck", "torso")]
    head = [c for c in order if layer_class(c) == "head"]
    arms = [c for s in SIDES for c in (f"upper_arm_{s}", f"forearm_{s}", f"hand_{s}") if c in order]
    rest = [c for c in order if c not in legs + body + head + arms]
    out = legs + body + rest + (arms + head if pr.arms_behind_head else head + arms)
    for s, name in pr.hands.items():
        v = f"hand_{s}__{name}"
        if available and v in available and f"hand_{s}" in out:
            out[out.index(f"hand_{s}")] = v
            if f"forearm_{s}__trim" in available and f"forearm_{s}" in out:  # drop original-hand bits
                out[out.index(f"forearm_{s}")] = f"forearm_{s}__trim"
    for f in pr.front:
        if f in out:
            out.remove(f)
            out.append(f)
    return out


def legacy_render(data: dict[str, Any], solver: PoseSolver, pose: PoseParams, pad: int) -> np.ndarray:
    """The original Phase 2 method on the same limb directions: rigid layers, Phase 1 pivots, one rigid
    body (no pelvis, spine, clavicle, IK or balance), original z-order."""
    th = pose.theta
    old_angles = {}
    for b, par in OLD_PARENT.items():
        w = solver.rot(th, b) - (solver.rot(th, par) if par else 0.0)
        old_angles[b] = wrap(w)
    pivots = {k: (tuple(np.array(v["position"]) + pad) if v.get("position") else None)
              for k, v in data["joints"].items()}
    T = legacy.world_transforms(pivots, old_angles)
    H, W = data["H"] + 2 * pad, data["W"] + 2 * pad
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for cid in data["order"]:
        lay = np.zeros((H, W, 4), np.uint8)
        lay[pad:pad + data["H"], pad:pad + data["W"]] = data["layers"][cid]
        b = legacy.bone_of(cid)
        canvas.alpha_composite(Image.fromarray(legacy.apply(lay, T.get(b, np.eye(3)) if b else np.eye(3)), "RGBA"))
    return np.asarray(canvas)


def _crop_box(images: list[np.ndarray], margin: int = 30) -> tuple[int, int, int, int]:
    acc = np.zeros(images[0].shape[:2], bool)
    for im in images:
        acc |= im[..., 3] > 0
    ys, xs = np.nonzero(acc)
    return (max(0, xs.min() - margin), max(0, ys.min() - margin), min(acc.shape[1], xs.max() + margin),
            min(acc.shape[0], ys.max() + margin))


def contact_sheet(cells: list[tuple[str, list[Image.Image]]], tile_h: int = 760) -> Image.Image:
    """``cells``: (title, [tile, ...]); each cell's tiles sit side by side under a title bar."""
    scaled = []
    for title, tiles in cells:
        ts = [t.resize((max(1, int(t.width * tile_h / t.height)), tile_h), Image.LANCZOS) for t in tiles]
        w = sum(t.width for t in ts) + 8 * (len(ts) - 1)
        cell = Image.new("RGB", (w, tile_h + 44), "white")
        d = ImageDraw.Draw(cell)
        d.rectangle((0, 0, w, 38), fill=(34, 40, 60))
        d.text((12, 12), title, fill=(255, 255, 255))
        x = 0
        for t in ts:
            cell.paste(t, (x, 44))
            x += t.width + 8
        scaled.append(cell)
    cols = 2
    cw = max(c.width for c in scaled)
    rows = (len(scaled) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cw + (cols + 1) * 24, rows * (tile_h + 44) + (rows + 1) * 24), (238, 240, 244))
    for i, c in enumerate(scaled):
        r, k = divmod(i, cols)
        sheet.paste(c, (24 + k * (cw + 24), 24 + r * (tile_h + 44 + 24)))
    return sheet


def correction_presets(sk, results: dict[str, Any]) -> dict[str, Any]:
    """Per-joint deformation presets with what the evaluated poses observed, ready to map to Moho bone
    influence / Smart Warp / Smart Bone corrective actions (mapping proposed, not verified in Moho)."""
    kinds = {"shoulder": "upper_arm", "elbow": "forearm", "wrist": "hand", "hip": "thigh", "knee": "shin",
             "neck": "neck"}
    out = {}
    for joint, kind in kinds.items():
        bones = [b for b in sk.bones if b.kind == kind]
        if not bones:
            continue
        i = sk.idx[bones[0].name]
        seams = [v["metrics"]["seams"]["joints"].get(f"{joint}_{s}", {}) for v in results.values() for s in SIDES]
        worst = max((s.get("exposed_gap", 0) + s.get("visible_seam", 0) for s in seams), default=0)
        needs = []
        if joint == "shoulder":
            needs.append("armpit gusset for abduction above ~120 deg (raised arms open the T-pose armpit corner)")
        if joint == "hip":
            needs.append("crotch gusset when the knees spread wider than the reference (squat/sit)")
        out[joint] = {"bones": [b.name for b in bones], "method": "angle-interpolated blend band (local skinning)",
                      "blend_band_px": {"into_parent": round(float(sk.rp[i]), 1), "into_child": round(float(sk.rc[i]), 1)},
                      "radial_falloff_px": [round(float(sk.R1[i]), 1), round(float(sk.R2[i]), 1)],
                      "limits_deg": LIMITS.get(kind, {}), "worst_torn_pairs_in_presets": int(worst),
                      "corrective_artwork_needed": needs,
                      "moho_mapping_proposed_unverified": {
                          "bitmap": "Smart Warp mesh over the joint, bound to the parent and child bones with the "
                                    "band widths above as influence falloff",
                          "vector": "points in the band bound to both bones (flexi-binding / bone strength)",
                          "corrective": "Smart Bone action on the child bone, keyed at the preset angles, for "
                                        "the corrective shapes listed"}}
    return out


def refine_package(pkg_dir: str | Path, presets: list[Preset] | None = None, pad: int = 160,
                   hand_sources: str | Path | None = None, gussets: bool = True) -> dict[str, Any]:
    t0 = time.time()
    pkg = Path(pkg_dir)
    out = pkg / OUT
    for sub in ("poses", "legacy", "closeups", "corrective/hands", "corrective/armpit"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    data = load(pkg)
    sk = build_skeleton(data["joints"], {c: a[..., 3] > 0 for c, a in data["layers"].items()})
    sources = json.loads(Path(hand_sources).read_text()) if hand_sources else None
    variants, cor_records, drawings = C.build_hand_variants(sk, data["layers"], sources,
                                                            Path(hand_sources).parent if hand_sources else pkg)
    for name, art in drawings.items():
        Image.fromarray(art, "RGBA").save(out / "corrective" / "hands" / f"{name}.png", optimize=True)
    for vid, lay in variants.items():
        Image.fromarray(lay, "RGBA").save(out / "corrective" / "hands" / f"{vid}.png", optimize=True)
    dfm = Deformer(sk, {**data["layers"], **variants}, data["generated"], reference=data["source"],
                   variants=set(variants))
    solver = PoseSolver(sk, dfm)
    presets = presets or PRESETS
    # neutral: identity pose must reproduce the original pixels exactly
    ident = dfm.render(PoseParams.identity(len(sk.bones)), data["order"], pad=0)
    union = np.zeros((data["H"], data["W"]), bool)
    for a in data["layers"].values():
        union |= a[..., 3] == 255
    neutral_exact = bool(np.array_equal(ident["image"][union], data["source"][union]))
    from scipy import ndimage as _nd
    lab, n = _nd.label(ident["image"][..., 3] > 127)
    rest_pieces = int((np.bincount(lab.ravel())[1:] >= 40).sum()) if n else 1
    lum = data["source"][..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    dark = (lum < 80) & union
    results, renders, gusset_records = {}, {}, []
    for pr in presets:
        pose, info = solver.solve(pr)
        order = natural_order(data["order"], pr, set(variants))
        r = dfm.render(pose, order, pad=pad)
        old = legacy_render(data, solver, pose, pad)
        tears = dfm.tears(pose, r, dark)
        used = [f"hand__{n}" for s, n in pr.hands.items() if f"hand_{s}__{n}" in order]
        resolved = {item: f"hand__{n}" for item, n in pr.resolved_by.items() if f"hand__{n}" in used}
        pending = [u for u in used if u not in resolved.values()]
        if gussets:
            for g in C.armpit_gussets(r, tears, sk, info, dfm, pose):
                gid = f"gusset__{pr.name}__{g['side']}"
                (out / "corrective" / "armpit" / f"{pr.name}__{g['side']}.svg").write_text(
                    C.gusset_svg(g, data["W"], data["H"]), encoding="utf-8")
                gusset_records.append({"id": gid, "type": "armpit_gusset", "category": "C_corrective_shape",
                                       "preset": pr.name, "status": "pending_review",
                                       "file": f"{OUT}/corrective/armpit/{pr.name}__{g['side']}.svg",
                                       "shoulder_abduction_deg": solver.anatomical(pose)[f"upper_arm_{g['side']}_abduction"],
                                       **g})
                pending.append(gid)
            if any(p.startswith("gusset__") for p in pending):
                tears = dfm.tears(pose, r, dark)  # re-measure after the corrective fill
        ev = evaluate(sk, solver, pose, info, r, pr, tears, rest_pieces, resolved, pending)
        renders[pr.name] = (r, old, info, tears)
        Image.fromarray(r["image"], "RGBA").save(out / "poses" / f"{pr.name}.png", optimize=True)
        on_bg(old).save(out / "legacy" / f"{pr.name}.jpg", quality=90)
        diagnostic(r["image"], sk, info, pad, gap_mask(r, tears)).save(out / "poses" / f"{pr.name}__diagnostic.jpg",
                                                                        quality=90)
        closeups(r["image"], old, sk, info, pad).save(out / "closeups" / f"{pr.name}.jpg", quality=90)
        results[pr.name] = {"title": pr.title, "intent": pr.intent, "status": ev["status"],
                            "confidence": ev["confidence"], "needs_artwork": ev["needs_artwork"],
                            "defects": ev["defects"], "notes": pr.notes,
                            "targets": {"joints_posed_px": info["joints_posed"],
                                        "anatomical_deg": solver.anatomical(pose),
                                        "local_rotation_deg": {b.name: round(float(pose.theta[i]), 2)
                                                               for i, b in enumerate(sk.bones)},
                                        "pelvis_shift_px": info["pelvis_shift_px"]},
                            "constraints": {"limits": "skeleton LIMITS", "feet_planted": info["support"]["planted"],
                                            "knee_min_bend_deg": pr.knee_min_bend},
                            "weight_distribution": {"left": info["support"]["weight_left"],
                                                    "right": round(1 - info["support"]["weight_left"], 3)},
                            "required_artwork_corrections": ev["needs_artwork"] + [
                                d for d in ev["defects"] if d.startswith(("visible seam", "joint fold"))],
                            "metrics": ev["metrics"]}
    # sequence checks: a squat-down and a walk weight shift with feet planted (foot sliding)
    seq = {}
    for name, field, values in (("squat_down", "pelvis_drop", [0.0, 0.06, 0.12, 0.18, 0.24]),
                                ("weight_shift", "weight_left", [0.5, 0.6, 0.7, 0.8])):
        base = next(p for p in PRESETS if p.name == ("natural_squat" if name == "squat_down" else "relaxed_standing"))
        feet, soles = [], []
        for v in values:
            p2 = Preset(**{**base.__dict__, field: v})
            pose, info = solver.solve(p2)
            feet.append([info["joints_posed"][f"foot_{s}"] for s in SIDES])
        feet = np.array(feet)
        seq[name] = {"values": values, "max_planted_foot_drift_px": round(float(np.abs(feet - feet[0]).max()), 3)}
    sheet_cells = [("Original reference (input artwork)", [on_bg(data["source"])])]
    diag_cells = list(sheet_cells)
    imgs = [r[0]["image"] for r in renders.values()] + [r[1] for r in renders.values()]
    box = _crop_box(imgs)
    for n in SHEET:
        if n not in renders:
            continue
        r, old, info, tears = renders[n]
        title = f"{results[n]['title']}  -  before (original rigid engine) | after (natural pose)  [{results[n]['status']}]"
        sheet_cells.append((title, [on_bg(old).crop(box), on_bg(r["image"]).crop(box)]))
        diag_cells.append((title, [on_bg(old).crop(box), diagnostic(r["image"], sk, info, pad,
                                                                     gap_mask(r, tears)).crop(box)]))
    contact_sheet(sheet_cells).save(out / "contact_sheet.png", optimize=True)
    contact_sheet(diag_cells).save(out / "contact_sheet_diagnostic.png", optimize=True)
    rig = sk.to_json()
    rig.update({"layer_bindings": {c: {"class": layer_class(c),
                                       "home_bones": sorted({sk.names[int(b)] for b in dfm.info[c]["homes"]})}
                                   for c in dfm.info},
                "corrections": correction_presets(sk, results),
                "facial_controls": "independent: facial layers ride the head bone rigidly; live facial controls "
                                   "(analysis/live_artwork_manifest.json) are not driven by the body solver",
                "moho": {"status": "proposed_unverified (Moho not run)",
                         "bones": "one Moho bone per entry in 'bones' (head/tail in source px -> convert with the "
                                  "Phase 0 pixel->Moho transform)",
                         "presets": "each preset = one Moho action keyed with 'local_rotation_deg' per bone; "
                                    "Smart Bones can drive the corrective actions in 'corrections'",
                         "live": "gamepad-triggered gestures map to preset actions; head tilt/rotation and facial "
                                 "controls stay separate actions"}})
    (out / "rig.json").write_text(json.dumps(rig, indent=2), encoding="utf-8")
    manifest = register_corrective(pkg, cor_records + gusset_records, sources)
    (out / "corrective" / "manifest.json").write_text(json.dumps(manifest, indent=2, default=float), encoding="utf-8")
    (out / "presets.json").write_text(json.dumps(results, indent=2, default=float), encoding="utf-8")
    report = {"package": pkg.name, "neutral_pose_exact": neutral_exact, "sequences": seq,
              "status_counts": {s: sum(1 for v in results.values() if v["status"] == s)
                                for s in ("OK_PENDING_REVIEW", "NEEDS_REVIEW", "NEEDS_ARTWORK")},
              "presets": {k: {"status": v["status"], "confidence": v["confidence"], "defects": v["defects"]}
                          for k, v in results.items()},
              "duration_s": round(time.time() - t0, 1),
              "evidence": "rendered by the standalone preview deformer; NOT Moho; nothing human-reviewed"}
    (out / "report.json").write_text(json.dumps(report, indent=2, default=float), encoding="utf-8")
    return {"report": report, "results": results, "skeleton": sk, "out": out}


def register_corrective(pkg: Path, records: list[dict[str, Any]], sources: dict[str, Any] | None) -> dict[str, Any]:
    """Add the corrective assets to the package's review items (reports/approvals.json) as pending review,
    so `autorig review --approve <id>` decides them and phase approval stays blocked until it does. An item
    already reviewed keeps its decision only while its artwork is unchanged (same fingerprint)."""
    import hashlib
    path = pkg / "reports" / "approvals.json"
    doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"items": [], "decisions": []}
    prior = {i["id"]: i for i in doc.get("items", [])}
    items = []
    for r in records:
        fp = hashlib.sha256(json.dumps({k: v for k, v in r.items() if k != "status"}, sort_keys=True,
                                       default=float).encode()).hexdigest()
        old = prior.get(f"corrective:{r['id']}")
        status = old["status"] if old and old.get("fingerprint") == fp else "pending_review"
        items.append({"id": f"corrective:{r['id']}", "type": "corrective_artwork", "category": r["category"],
                      "status": status, "fingerprint": fp, "preset": r.get("preset"), "detail": r.get("source")})
    doc["items"] = [i for i in doc.get("items", []) if not i["id"].startswith("corrective:")] + items
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return {"assets": records, "hand_sources": sources, "review": "autorig review <package> --approve corrective:<id>",
            "rule": "generated/constructed artwork is never approved in code"}
