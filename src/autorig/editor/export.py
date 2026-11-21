"""Moho-ready asset package export and quality gates (Phase 3).

The package is an asset package for the future Phase 4 importer, not a ``.moho`` file. It holds:
* the original source (byte-identical) and the approved transparent layers (source pixels in the user's
  corrected masks, with generated extensions underneath);
* vector corrections (SVG), the layer hierarchy, and bones with pivots, constraints and Moho-convention
  angles;
* deformation corrections, facial and live metadata, provenance, quality reports and approvals.

Each quality gate is PASS, WARNING, NEEDS_REVIEW or FAIL. ``production_ready`` is true only when no
gate is NEEDS_REVIEW or FAIL.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .artwork import ArtworkModel
from .posesvc import bone_warnings
from .project import EditorProject
from .readiness import face_panel, live_panel
from .vectors import bounds, to_svg

SCHEMA = "autorig.moho_asset_package"
VERSION = "1.1.0"  # 1.1.0: rig_controls (Phase 3.2), additive
LEVELS = ("PASS", "WARNING", "NEEDS_REVIEW", "FAIL")
P1_JOINT = {"upper_arm": "shoulder", "forearm": "elbow", "hand": "wrist", "thigh": "hip", "shin": "knee",
            "foot": "ankle", "neck": "neck_base", "head": "neck", "pelvis": "pelvis"}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rejected_ids(st: dict[str, Any]) -> set[str]:
    return {k for k, v in st["approvals"].items() if v.get("status") == "rejected"}


def to_moho(x: float, y: float, W: int, H: int) -> list[float]:
    """Source px -> Moho world (origin at frame centre, +y up, frame height spans [-1, 1])."""
    return [round((x - W / 2) / (H / 2), 6), round((H / 2 - y) / (H / 2), 6)]


def bones_export(st: dict[str, Any]) -> list[dict[str, Any]]:
    W, H = st["canvas"]["width"], st["canvas"]["height"]
    B = st["bones"]
    absang = {}
    for n, b in B.items():
        dx, dy = b["tail"][0] - b["head"][0], b["tail"][1] - b["head"][1]
        absang[n] = math.atan2(-dy, dx)  # Moho: counter-clockwise from +x with +y up
    out = []
    for n, b in B.items():
        p = b.get("parent")
        rel = absang[n] - (absang[p] if p in absang else 0.0)
        rel = (rel + math.pi) % (2 * math.pi) - math.pi
        out.append({"name": n, "parent": p, "kind": b.get("kind"), "side": b.get("side"),
                    "head_px": b["head"], "tail_px": b["tail"],
                    "length_px": round(float(np.hypot(b["tail"][0] - b["head"][0], b["tail"][1] - b["head"][1])), 2),
                    "head_moho": to_moho(*b["head"], W, H), "tail_moho": to_moho(*b["tail"], W, H),
                    "moho_angle_rad_parent_relative": round(rel, 6),
                    "phase1_joint_name": (f"{P1_JOINT[b['kind']]}_{b['side']}" if b.get("side") and b.get("kind") in P1_JOINT
                                          else P1_JOINT.get(b.get("kind"))),
                    "rotation_constraints_deg": b.get("limits", {}), "blend_band_px": b.get("band"),
                    "user_edited": bool(b.get("edited"))})
    return out


def quality_gates(proj: EditorProject, art: ArtworkModel, exported: dict[str, Any] | None = None) -> dict[str, Any]:
    st = proj.state()
    masks = art.masks(st["mask_ops"])
    rej = rejected_ids(st)
    order = [L["id"] for L in st["layers"] if L["kind"] == "bitmap" and L["visible"] and L["id"] in masks]
    meta = json.loads((proj.pkg / "metadata.json").read_text())
    gates = []

    def g(gid, name, level, detail):
        gates.append({"id": gid, "check": name, "level": level, "detail": detail})

    src_rel = meta["source"].get("path", "source/original.png")
    ok = _sha(proj.pkg / src_rel) == meta["source"]["sha256"] == proj.data["source_sha256"]
    g(1, "original source image preserved", "PASS" if ok else "FAIL", f"{src_rel} sha256 matches the Phase 1 record" if ok
      else "source changed")
    files = (exported or {}).get("files", [])
    dims_bad = [f for f in files if f.endswith(".png") and Image.open(f).size != (art.W, art.H)]
    g(2, "artwork layer dimensions", "FAIL" if dims_bad else "PASS",
      f"{len(dims_bad)} layer(s) not {art.W}x{art.H}" if dims_bad else f"all layers {art.W}x{art.H} (full canvas)")
    out_of = [v["id"] for v in st["vectors"].values() if v.get("visible") and
              not (-20 <= bounds(v)[0] and -20 <= bounds(v)[1] and bounds(v)[2] <= art.W + 20 and bounds(v)[3] <= art.H + 20)]
    g(3, "coordinate alignment", "WARNING" if out_of else "PASS",
      f"vectors outside the canvas: {out_of}" if out_of else "layers full-canvas; vectors and bones in source px")
    missing = [f for f in files if not Path(f).is_file()]
    g(4, "no missing referenced assets", "FAIL" if missing else "PASS", f"missing: {missing}" if missing else
      f"{len(files)} files present" if files else "checked at export")
    bw = bone_warnings(st, proj.base["bones"], masks, art.footprint)
    errs = [w for w in bw if w["level"] == "error"]
    g(5, "valid bone hierarchy", "FAIL" if errs else "PASS", "; ".join(f"{w['bone']}: {w['issue']}" for w in errs) or
      f"{len(st['bones'])} bones, single root, no cycles, no zero-length bones")
    jw = [w for w in bw if w["level"] == "warning" and w["bone"] and "binding" not in w["issue"]]
    g(6, "valid joint positions", "WARNING" if jw else "PASS", "; ".join(f"{w['bone']}: {w['issue']}" for w in jw) or "ok")
    bind = [w for w in bw if "binding" in w["issue"]]
    g(7, "valid layer bindings", "WARNING" if bind else "PASS", "; ".join(w["issue"] for w in bind) or
      "every visible layer is bound to an existing bone")
    fid = art.fidelity(order, masks, rej)
    g(8, "no unexpected neutral-pose gaps", "PASS" if fid["gap_px"] == 0 else "NEEDS_REVIEW",
      f"{fid['gap_px']} character pixels uncovered" if fid["gap_px"] else "neutral composite covers every character pixel")
    verify = art.store.verify()
    preserved = not verify and fid["max_abs_error_in_covered"] == 0
    g(9, "original artwork preservation verified", "PASS" if preserved else "FAIL",
      "originals hash-verified; neutral composite equals the source where covered" if preserved else f"{verify[:3]} "
      f"max error {fid['max_abs_error_in_covered']}")
    presets = json.loads((proj.pkg / "natural_pose" / "presets.json").read_text()) if \
        (proj.pkg / "natural_pose" / "presets.json").is_file() else {}
    accepted = {d.get("preset") for d in st["defects"].values() if d.get("status") in ("resolved", "accepted")}
    motion = proj.pkg / "natural_pose" / "motion" / "results.json"
    if motion.is_file():  # Phase 3.1 evidence supersedes the Phase 2 preset statuses
        mres = json.loads(motion.read_text())["presets"]
        open_defects = sorted({k.split("@")[0] for k, v in mres.items()
                               if (v["feasibility"]["status"] in ("NEEDS_ARTWORK", "UNSUPPORTED")
                                   or not v["quality"]["numerical"]["all_pass"])
                               and k.split("@")[0] not in accepted and "preview" not in k})
    else:
        open_defects = [n for n, v in presets.items() if v["status"] != "OK_PENDING_REVIEW" and n not in accepted]
    open_defects += [k for k, d in st["defects"].items() if d.get("status") == "unresolved"]
    g(10, "unresolved deformation defects reported", "NEEDS_REVIEW" if open_defects else "PASS",
      f"open: {open_defects}" if open_defects else "none")
    face = face_panel(proj.pkg, st, masks)
    req_new = [c["capability"] for c in face["capabilities"] if c["readiness"] == "REQUIRES_NEW_ARTWORK"]
    g(11, "facial artwork completeness reported", "NEEDS_REVIEW" if req_new else "PASS",
      f"REQUIRES_NEW_ARTWORK: {req_new}" if req_new else "all facial capabilities have artwork")
    used_ext = {e for L in st["layers"] if L["visible"] and L["kind"] == "bitmap" for e in L.get("extensions", [])}
    mandatory = {k: v for k, v in st["approvals"].items()
                 if k in used_ext or k.startswith("corrective:") or k.startswith("vector:")}
    mandatory["sign_off:neutral_fidelity"] = st["approvals"].get("sign_off:neutral_fidelity", {"status": "pending_review"})
    pending = sorted(k for k, v in mandatory.items() if v.get("status") not in ("approved", "rejected"))
    lvl = "NEEDS_REVIEW" if pending else "PASS"  # rejected generated pixels are excluded from the export
    g(12, "mandatory user approvals recorded", lvl,
      f"{len(pending)} pending, e.g. {pending[:6]}" if pending else "all mandatory approvals recorded")
    worst = max((LEVELS.index(x["level"]) for x in gates), default=0)
    return {"gates": gates, "overall": LEVELS[worst], "production_ready": worst <= 1, "fidelity": fid,
            "rejected_generated_excluded": sorted(rej)}


def _motion_json(pkg: Path, name: str) -> dict[str, Any] | None:
    """Phase 3.1 garment model / motion profile (Smart Bone-ready correction metadata), if generated."""
    f = Path(pkg) / "natural_pose" / "motion" / name
    if not f.is_file():
        return None
    d = json.loads(f.read_text())
    d["status"] = "proposed_unverified (preview renderer only; Moho not run)"
    return d


def _engine(proj: EditorProject, art: ArtworkModel, st: dict[str, Any], masks, rej):
    from .posesvc import PoseService

    return PoseService(proj.pkg, art).motion_engine(st, masks, rej)


def _rig_controls(eng, st: dict[str, Any]) -> tuple[dict, list[str]]:
    """Rig controls (IK, switches, corrections, live map) validated before export. Built with the same engine
    the editor uses; the document stays engine-side (Moho mapping proposed_unverified)."""
    from ..motion.rigctl import build_rig_controls, validate

    doc = build_rig_controls(eng, st.get("ik"), st.get("switches"), st.get("hand_selection"))
    return doc, validate(doc)


def _hand_switches(eng, st: dict[str, Any], out: Path, W: int, H: int) -> tuple[dict[str, Any], list[str]]:
    """Future-compatible hand switch manifest for Moho Switch Layers. ``validated`` holds what this export checked
    (files written, full canvas, alpha, hashes, pivots, approvals, measured alignment); ``not_implemented_in_native_moho``
    lists what no verified Moho code does yet. Every hand candidate is exported as a full-canvas rest-frame layer."""
    import hashlib

    from ..motion.rigctl import LIVE_ACTIONS
    from ..motion.switches import FALLBACK_POSE

    lib = eng.hand_lib
    files, groups, pending = [], [], 0
    for s in ("r", "l"):
        hb = eng.sk.bone(f"hand_{s}")
        variants = []
        for pose, vs in lib.candidates[s].items():
            for v in vs:
                rgba = eng.layers[v.layer] if v.cid == "original" else eng.variants[v.layer]
                fname = f"layers/{v.layer}.png"
                if v.cid != "original":
                    f = out / fname
                    if not f.exists():
                        Image.fromarray(rgba, "RGBA").save(f, optimize=True)
                        files.append(str(f))
                data = (out / fname).read_bytes() if (out / fname).is_file() else None
                ok_alpha = bool((rgba[..., 3] == 0).any() and (rgba[..., 3] > 127).any())
                al = lib.alignment_v(v, eng.profile.midline_x)
                pending += int(v.status in ("pending_review", "rejected"))
                variants.append({
                    "pose": pose, "candidate": v.cid, "label": v.label or pose, "layer": v.layer, "file": fname,
                    "sha256": hashlib.sha256(data).hexdigest() if data else None, "status": v.status,
                    "approval_item": v.approval_item, "active_for_pose": v is lib.hands[s][pose],
                    "category": v.category, "provenance": v.provenance, "placement": (v.placement or {}).get("final"),
                    "placement_correction": (v.placement or {}).get("correction"),
                    "mask": (v.placement or {}).get("mask_settings"),
                    "alignment_params": (v.placement or {}).get("params"),
                    "library": (v.meta or {}).get("library_id"), "sleeve": (v.meta or {}).get("sleeve"),
                    "handedness": (v.meta or {}).get("handedness"), "flags": (v.meta or {}).get("flags") or [],
                    "checks": {"file_written": data is not None, "full_canvas": rgba.shape[:2] == (H, W),
                               "has_alpha": ok_alpha, "side_ok": al.get("side_ok"),
                               "rest_attachment_error_px": al.get("attachment_error_px"),
                               "width_ratio": al.get("width_ratio")}})
        sw = (st.get("switches") or {}).get(f"hand_{s}", {})
        groups.append({"id": f"hand_{s}", "alias": "hand_right" if s == "r" else "hand_left", "bone": f"hand_{s}",
                       "pivot_px": [round(float(v), 2) for v in hb.head],
                       "pivot_moho": [round((float(hb.head[0]) - W / 2) / (H / 2), 5), round((H / 2 - float(hb.head[1])) / (H / 2), 5)],
                       "default": FALLBACK_POSE, "user_choice": sw.get("user"), "auto": sw.get("auto", True),
                       "keyframes": sw.get("keys", []), "variants": variants})
    actions = {k: a for k, a in LIVE_ACTIONS.items() if a.get("kind") == "switch" and a.get("group", "").startswith("hand_")}
    n = sum(len(g["variants"]) for g in groups)
    from ..motion.handselect import rules_json

    hsi = eng.hand_store or {}
    return {
        "schema": "autorig.hand_switches", "schema_version": "1.1.0",
        "validated": {"groups": groups,
                      "rules": ["layers are full-canvas rest-frame RGBA, unscaled after placement (source files unchanged)",
                                "sleeve-attached drawings: their own sleeve/cuff masked and matte halo removed",
                                "sleeve-free drawings: placed by their own wrist opening (centre on the character's cuff edge, "
                                "tucked under it, axis on the hand bone, one scale per side); pixels behind the tucked "
                                "opening trimmed (alignment_params)",
                                "pending or rejected drawings are listed but not approved; production use requires approval"]},
        "libraries": hsi.get("libraries", {}),
        "gesture_id_map": hsi.get("gesture_id_map", []),
        "selection": {"rules": rules_json(eng.hand_select), "state": st.get("hand_selection"),
                      "note": "the selection algorithm runs in AutoRig; Moho would receive its results as switch keys"},
        "semantic_actions": actions,
        "moho_mapping": {"status": "proposed_unverified",
                         "proposal": "one Moho Switch Layer per group (child per variant image), bound to the hand bone; "
                                     "switch keys from 'keyframes'; live actions map to switch values later"},
        "not_implemented_in_native_moho": [
            "creating a Switch layer and adding these images as its children: no Moho API for it is verified in the allowlist",
            "binding switch values to Smart Bones, actions or Live Mode inputs (input identifiers not discovered)",
            "writing a .moho/.mohoproj file: blocked until the Phase 0 importer is verified natively"],
        "summary": {"groups": len(groups), "variants": n, "pending_or_rejected": pending,
                    "production_ready": pending == 0}}, files


def _clear_previous_export(out: Path) -> None:
    """Only an earlier AutoRig export (or an empty directory) may be replaced; never delete anything else."""
    if out.is_file() or out.is_symlink():
        raise ValueError(f"export target {out} is not a directory")
    if not any(out.iterdir()):
        out.rmdir()
        return
    man = out / "manifest.json"
    try:
        ok = json.loads(man.read_text()).get("schema") == SCHEMA
    except (OSError, ValueError):
        ok = False
    if not ok:
        raise ValueError(f"refusing to overwrite {out}: it is not an earlier AutoRig asset package")
    shutil.rmtree(out)


def export_package(proj: EditorProject, art: ArtworkModel, out: str | Path | None = None) -> dict[str, Any]:
    st = proj.state()
    masks = art.masks(st["mask_ops"])
    rej = rejected_ids(st)
    out = Path(out) if out else proj.dir / "exports" / time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    if out.exists():
        _clear_previous_export(out)
    for sub in ("source", "layers", "vectors", "reports"):
        (out / sub).mkdir(parents=True)
    meta = json.loads((proj.pkg / "metadata.json").read_text())
    src_rel = meta["source"].get("path", "source/original.png")
    shutil.copyfile(proj.pkg / src_rel, out / "source" / Path(src_rel).name)
    files = [str(out / "source" / Path(src_rel).name)]
    layers = []
    for z, L in enumerate(st["layers"]):
        if L["kind"] == "bitmap" and L["id"] in masks:
            arr = art.layer_rgba(L["id"], masks, rej, L.get("edge_feather", 0.0))
        elif L["kind"] == "bitmap" and L.get("duplicate_of") in masks:
            arr = art.layer_rgba(L["duplicate_of"], masks, rej, L.get("edge_feather", 0.0))
        elif L["kind"] == "switch":
            arr = np.asarray(Image.open(proj.pkg / L["file"]).convert("RGBA"))
        else:
            continue
        f = out / "layers" / f"{L['id']}.png"
        Image.fromarray(arr, "RGBA").save(f, optimize=True)
        files.append(str(f))
        ys, xs = np.nonzero(arr[..., 3] > 0)
        bone = st["bones"].get(L.get("bone") or "", {})
        layers.append({"id": L["id"], "name": L["name"], "group": L["group"], "z_index": z, "file": f"layers/{L['id']}.png",
                       "kind": L["kind"], "visible": L["visible"], "opacity": L["opacity"], "role": L["role"],
                       "bone": L.get("bone"), "pivot_px": bone.get("head"),
                       "bounds_px": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if len(xs) else None,
                       "provenance": L["provenance"], "user_category": L["user_category"],
                       "generated_extensions": [e for e in L.get("extensions", []) if e not in rej],
                       "approval": {e: st["approvals"].get(e, {}).get("status", "pending_review") for e in L.get("extensions", [])}})
    vectors = []
    for v in st["vectors"].values():
        if not v.get("visible"):
            continue
        f = out / "vectors" / f"{v['id']}.svg"
        f.write_text(to_svg(v, art.W, art.H), encoding="utf-8")
        files.append(str(f))
        vectors.append({"id": v["id"], "file": f"vectors/{v['id']}.svg", "purpose": v.get("purpose"),
                        "target": v.get("target"), "origin": v.get("origin"), "status": v.get("status"),
                        "approval": st["approvals"].get(f"vector:{v['id']}", {}).get("status", "pending_review"),
                        "bounds_px": [round(x, 1) for x in bounds(v)]})
    corr_path = proj.pkg / "natural_pose" / "corrective" / "manifest.json"
    corrective = json.loads(corr_path.read_text()) if corr_path.is_file() else {}
    validation = art.validate_masks(masks)
    face = face_panel(proj.pkg, st, masks)
    live = live_panel(proj.pkg, st, masks)
    eng = _engine(proj, art, st, masks, rej)
    rig_controls, rc_errors = _rig_controls(eng, st)
    if rc_errors:
        raise ValueError("rig controls do not validate: " + "; ".join(rc_errors[:5]))
    (out / "rig_controls.json").write_text(json.dumps(rig_controls, indent=2, default=float), encoding="utf-8")
    files.append(str(out / "rig_controls.json"))
    hand_sw, hand_files = _hand_switches(eng, st, out, art.W, art.H)
    (out / "hand_switches.json").write_text(json.dumps(hand_sw, indent=2, default=float), encoding="utf-8")
    files += hand_files + [str(out / "hand_switches.json")]
    garment_rig = None
    if getattr(eng, "structure", None) is not None:  # Phase 3.3 garment rig: data for Phase 4 (never a Moho file)
        import numpy as _np

        from ..garmentrig.moho import matrix_json

        _np.savez_compressed(out / "garment_weights.npz", **eng.weights.export_arrays())
        garment_rig = {"structure": eng.structure.to_json(), "weights": eng.weights.to_json(),
                       "validation": eng.weights.validate(), "pivots": eng.pivot_report,
                       "corrections": eng.corr_switch.to_json(), "moho_compatibility": matrix_json(),
                       "status": "proposed_unverified"}
        (out / "garment_rig.json").write_text(json.dumps(garment_rig, indent=1, default=str), encoding="utf-8")
        files += [str(out / "garment_rig.json"), str(out / "garment_weights.npz")]
    manifest = {
        "schema": SCHEMA, "schema_version": VERSION, "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "editor_project": {"schema_version": proj.data["schema_version"], "revision": proj.revision(), "ops": proj.cursor},
        "source": {"file": f"source/{Path(src_rel).name}", "sha256": meta["source"]["sha256"]},
        "canvas": {"width": art.W, "height": art.H, "units": "source px, origin top-left, +y down",
                   "moho_frame": {"convention": "origin frame centre, +y up, frame height spans [-1, 1]",
                                  "x": "(x_px - W/2) / (H/2)", "y": "(H/2 - y_px) / (H/2)",
                                  "angles": "Moho radians CCW parent-relative; editor/natural-pose degrees are "
                                            "clockwise-on-screen (converted in bones[].moho_angle_rad_parent_relative)"}},
        "layers": layers, "groups": st["groups"], "vectors": vectors, "bones": bones_export(st),
        "deformation": {"blend_bands": {n: b.get("band") for n, b in st["bones"].items()},
                        "joint_corrections": st["corrections"]["joints"], "armpit_gussets_enabled": st["corrections"]["gussets"],
                        "corrective_artwork": corrective.get("assets", []), "defects": st["defects"],
                        "moho_translation": "proposed_unverified: bands -> bone influence / Smart Warp falloff; "
                                            "corrective shapes -> Smart Bone actions (Phase 4)",
                        "garments": _motion_json(proj.pkg, "garments.json"),
                        "motion_profile": _motion_json(proj.pkg, "profile.json")},
        "garment_rig": None if garment_rig is None else {
            "file": "garment_rig.json", "weights": "garment_weights.npz", "status": "proposed_unverified",
            "parts": len(garment_rig["structure"]["parts"]), "weights_valid": garment_rig["validation"]["ok"]},
        "rig_controls": rig_controls, "hand_switches": {"file": "hand_switches.json",
                                                         "summary": hand_sw["summary"]}, "facial": face, "live": live,
        "provenance": {"mask_corrections": len(st["mask_ops"]), "reassigned_px": validation["reassigned_px"],
                       "unassigned_px": validation["unassigned_px"],
                       "rule": "layer pixels are source pixels; generated pixels only in listed extensions/corrective"},
        "approvals": st["approvals"],
    }
    manifest["files"] = [str(Path(f).relative_to(out)) for f in files]
    gates = quality_gates(proj, art, {"files": files})
    manifest["quality"] = {"gates": gates["gates"], "overall": gates["overall"], "fidelity": gates["fidelity"],
                           "mask_validation": {k: v for k, v in validation.items() if k != "layers"}}
    manifest["production_ready"] = gates["production_ready"]
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=float), encoding="utf-8")
    (out / "reports" / "quality_gates.json").write_text(json.dumps(gates, indent=2, default=float), encoding="utf-8")
    errors = validate_package(out)
    return {"out": str(out), "manifest": manifest, "gates": gates, "validation_errors": errors}


def validate_package(out: str | Path) -> list[str]:
    """Independent check of an exported package: files, dimensions, coordinates, hierarchy."""
    out = Path(out)
    m = json.loads((out / "manifest.json").read_text())
    errs = []
    if m.get("schema") != SCHEMA:
        errs.append("wrong schema")
    W, H = m["canvas"]["width"], m["canvas"]["height"]
    for f in m["files"] + ["manifest.json"]:
        if not (out / f).is_file():
            errs.append(f"missing file {f}")
    if _sha(out / m["source"]["file"]) != m["source"]["sha256"]:
        errs.append("source not byte-identical")
    for L in m["layers"]:
        if not (out / L["file"]).is_file():
            errs.append(f"missing layer {L['file']}")
        elif Image.open(out / L["file"]).size != (W, H):
            errs.append(f"layer {L['id']} not full-canvas")
    names = {b["name"] for b in m["bones"]}
    roots = [b for b in m["bones"] if not b["parent"]]
    if len(roots) != 1:
        errs.append("bone hierarchy needs exactly one root")
    for b in m["bones"]:
        if b["parent"] and b["parent"] not in names:
            errs.append(f"bone {b['name']} has missing parent")
        for k in ("head_px", "tail_px"):
            x, y = b[k]
            if not (-W <= x <= 2 * W and -H <= y <= 2 * H):
                errs.append(f"bone {b['name']} {k} far off canvas")
    for L in m["layers"]:
        if L["bone"] and L["bone"] not in names:
            errs.append(f"layer {L['id']} bound to unknown bone {L['bone']}")
    return errs
