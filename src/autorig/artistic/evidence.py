"""Phase 3.5 evidence (``autorig artistic-evidence <package>``): every image comes from AutoRig's own renderer.

Outputs in ``<package>/natural_pose/artistic/``:
- per pose (A-L): the Phase 3.4 render vs the Phase 3.5 render, both silhouettes, feasibility class, artwork used
  (hands, alternatives, correctives), deformation diagnostics, silhouette warnings, human-review status;
- candidate sheets per action;
- joint and shoulder close-ups (Phase 3.4 caps vs the art-directed folds; leaner gusset);
- the dynamic layer-order demo;
- the acting test (frames, GIF and WebP, per-frame and sequence evaluation);
- artwork requests for the missing drawings;
- the view and limb-configuration manifests;
- benchmarks;
- a report (``reports/artistic_quality.json``).

Generated Phase 3.5 corrective assets (elbow/knee folds, the leaner gusset) go into the append-only corrective
store, pending review. Pending drawings appear only as labelled previews.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw

from ..phase2.pose_eval import on_bg
from .alternatives import (AlternativeArtworkController, AlternativeArtworkStore, PoseAwareArtworkGenerator,
                           ViewAwareRigController, limb_config_doc)
from .candidates import PoseCandidateGenerator, assess, render_pose
from .evaluate import ArtisticAnimationEvaluator
from .layer_order import DynamicLayerOrderController
from .poses import ACTIONS_12, ArtisticPosePlanner, variant
from .transitions import ActingSequence, acting_test_keys, params_of

POSE_TABLE = [  # (letter, action, Phase 3.4 preset, Phase 3.4 kwargs)
    ("A", "relaxed_standing", "relaxed_standing", {}), ("B", "weight_shift", "relaxed_standing", {}),
    ("C", "point_right", "pointing_speaking", {}), ("D", "point_left", None, {}),
    ("E", "shrug", "one_hand_shrug", {}), ("F", "arm_raised", "relaxed_standing", {"fk": {"r": (140.0, 10.0)}}),
    ("G", "deep_elbow", "relaxed_standing", {"fk": {"r": (15.0, 135.0)}}),
    ("H", "walk", "casual_walk", {"phase": 0.25}), ("I", "run", "running", {"phase": 0.25}),
    ("J", "sit", "sitting", {}), ("K", "squat", "natural_squat", {}), ("L", "lean_forward", "leaning_forward", {})]
NEW_ASSETS = ("elbow_fold_r", "elbow_fold_l", "knee_fold_r", "knee_fold_l")


def _fig(img: np.ndarray, h: int, bg=(255, 255, 255)) -> Image.Image:
    im = on_bg(img, bg)
    a = img[..., 3] > 0
    ys, xs = np.nonzero(a)
    c = im.crop((max(0, xs.min() - 10), max(0, ys.min() - 10), xs.max() + 10, ys.max() + 10))
    return c.resize((max(1, int(c.width * h / c.height)), h), Image.LANCZOS)


def _sil(img: np.ndarray, h: int) -> Image.Image:
    a = img[..., 3] > 127
    rgba = np.zeros(img.shape, np.uint8)
    rgba[a] = (25, 25, 25, 255)
    return _fig(rgba, h)


def ensure_assets(eng, vis, pkg: Path, progress) -> list[str]:
    """Phase 3.5 corrective assets (pending review), added once to the append-only store."""
    from ..garmentrig.correctives import CorrectiveArtworkStore, ShoulderSilhouetteController  # noqa: PLC0415
    from .joint_art import ArtDirectedJointCorrection  # noqa: PLC0415

    st = CorrectiveArtworkStore(pkg)
    have = {(a.asset_id, a.method) for a in st.latest()}
    made = []
    for s in ("r", "l"):
        for j in ("elbow", "knee"):
            if (f"{j}_fold_{s}", "ArtDirectedJointCorrection") not in have:
                g = ArtDirectedJointCorrection().generate(eng, j, s)
                if g:
                    a = st.add(*g)
                    made.append(f"{a.asset_id}@v{a.version}")
        if (f"armpit_gusset_{s}", "ShoulderSilhouetteController") not in have:
            g = ShoulderSilhouetteController().generate(eng, s, vis=vis)
            if g:
                a = st.add(*g)
                made.append(f"{a.asset_id}@v{a.version}")
    progress(f"Phase 3.5 corrective assets added: {made or 'none (already present)'}")
    return made


def artistic_evidence(pkg, out: Path | None = None, quick: bool = False, progress: Callable[[str], Any] = print
                      ) -> dict[str, Any]:
    from ..garmentrig.corrective_evidence import CorrectiveQualityEvaluator, hardware  # noqa: PLC0415
    from ..garmentrig.correctives import CorrectiveArtworkStore, CorrectiveRuntime  # noqa: PLC0415
    from ..garmentrig.evidence import fk_state  # noqa: PLC0415
    from ..motion.pipeline import engine_for_package  # noqa: PLC0415

    pkg = Path(pkg)
    out = Path(out or pkg / "natural_pose" / "artistic")
    out.mkdir(parents=True, exist_ok=True)
    t_all = time.perf_counter()
    eng, vis = engine_for_package(pkg)
    made = ensure_assets(eng, vis, pkg, progress)
    eng.correctives = CorrectiveRuntime(eng, CorrectiveArtworkStore(pkg))
    planner = ArtisticPosePlanner(eng.profile)
    gen = PoseCandidateGenerator(eng, vis)
    alt = AlternativeArtworkController(eng, AlternativeArtworkStore(pkg))
    results: dict[str, Any] = {"poses": {}, "candidates": {}, "generated_assets": made}
    before_off = {k: False for k in NEW_ASSETS}
    cols = []
    table = POSE_TABLE if not quick else POSE_TABLE[:3]
    for letter, action, preset, kw in table:
        t0 = time.perf_counter()
        before = None
        if preset is not None:
            args = {"phase": kw.get("phase")} if "phase" in kw else {}
            if "fk" in kw:
                args["ik"] = fk_state(kw["fk"])
            before = eng.pose(preset, visible=vis, evaluate_garments=False, corrective_mode="all",
                              corrective_overrides=before_off, **args)
        cand = gen.candidates(action)
        best = next(c for c in cand["candidates"] if c["variant"] == cand["recommended"])
        ap = variant(action, cand["recommended"], gen.lib)
        after = best["render"]
        chosen, alt_rep = alt.select(action, "FRONT", eng.approvals, True)
        off = render_pose(eng, vis, ap, planner=planner, corrective_mode="off")
        on_nodec = render_pose(eng, vis, ap, planner=planner, extra={"corrective_overrides": {"edge_decontamination": False}})
        q = CorrectiveQualityEvaluator().evaluate(off, on_nodec, None)
        sil_b = assess(eng, ap, before)["silhouette"] if before is not None else None
        rec = {"letter": letter, "action": action, "title": ap.title, "intention": ap.intention,
               "recommended": cand["recommended"], "reason": cand["reason"], "ranking": cand["ranking"],
               "feasibility": best["feasibility"], "silhouette_after": best["silhouette"],
               "silhouette_before": sil_b, "hands": best["hands"], "pending_hands": best["pending_hands"],
               "correctives_shown": [c["asset_id"] for c in after.get("correctives", []) if c.get("shown")
                                     and c.get("category") != "edges"],
               "alternatives": alt_rep, "layer_order": after.get("layer_order"),
               "diagnostics": {"gaps": len(after["tears"]["exposed_gap"]), "seams": len(after["tears"]["visible_seam"]),
                               "clipped_open_px": int(sum(after["render"]["clipped_uncovered"].values())),
                               "protected_violation_px": q["checks"]["B_leakage"]["protected_violation_px"],
                               "fold_overlay_px": q["checks"]["B_leakage"]["fold_overlay_px"]},
               "human_review": "pending (no person has approved this pose)", "wall_s": round(time.perf_counter() - t0, 1)}
        results["poses"][letter] = rec
        results["candidates"][action] = [{k: v for k, v in c.items() if k not in ("render", "silhouette_image")}
                                         for c in cand["candidates"]]
        _candidate_sheet(cand, out / f"candidates_{letter}_{action}.png")
        cols.append((letter, rec, before, after))
        Image.fromarray(after["image"]).save(out / f"after_{letter}_{action}.png")
        progress(f"{letter} {action}: {cand['recommended']} {best['feasibility']['label']} "
                 f"warnings {[w['code'] for w in best['silhouette']['warnings']]} "
                 f"(before: {[w['code'] for w in sil_b['warnings']] if sil_b else 'n/a'}) {rec['wall_s']} s")
    _contact(cols, out)
    if not quick:
        results["closeups"] = _closeups(eng, vis, out, fk_state)
        results["layer_order_demo"] = _layer_demo(eng, vis, planner, out)
        results["acting_test"] = acting_test(eng, vis, planner, out, progress)
    # artwork requests for missing drawings (using the after renders as the target poses)
    req_dir = out / "artwork_requests"
    pag = PoseAwareArtworkGenerator(eng)
    reqs = []
    for asset, letter in (("corrective:elbow_deep_r", "G"), ("art:thigh_foreshortened", "J"), ("art:thigh_knee_up", "I"),
                          ("art:torso_lean_forward", "L"), ("corrective:raised_arm_armpit", "F")):
        c = next((x for x in cols if x[0] == letter), None)
        if c is not None:
            d = pag.request(asset, c[3], req_dir)
            if d:
                reqs.append({"asset": asset, "pose": letter, "files": d["files"], "provider": d["provider"]})
    results["artwork_requests"] = reqs
    views = ViewAwareRigController(pkg, list(eng.layers)).manifest(alt.items)
    (pkg / "analysis").mkdir(exist_ok=True)
    (pkg / "analysis" / "view_manifest.json").write_text(json.dumps(views, indent=1))
    (pkg / "analysis" / "limb_configurations.json").write_text(json.dumps(limb_config_doc(), indent=1))
    results["benchmark"] = benchmark(eng, vis, planner)
    results["benchmark"]["hardware"] = hardware()
    results["benchmark"]["A_offline_evidence_s"] = round(time.perf_counter() - t_all, 1)
    report = {"schema": "autorig.artistic_quality", "version": "1.0.0", "results": results, "moho": moho_matrix(),
              "live": live_notes(), "artistic_review_required": True, "artistic_approval": "not recorded",
              "production_ready": False}
    (pkg / "reports").mkdir(exist_ok=True)
    (pkg / "reports" / "artistic_quality.json").write_text(json.dumps(report, indent=1, default=_js))
    (out / "results.json").write_text(json.dumps(report, indent=1, default=_js))
    progress(f"done in {results['benchmark']['A_offline_evidence_s']} s -> {out}")
    return report


def _candidate_sheet(cand: dict[str, Any], path: Path) -> None:
    cs = cand["candidates"]
    tiles = []
    for c in cs:
        f, s = _fig(c["render"]["image"], 360), _sil(c["render"]["image"], 360)
        t = Image.new("RGB", (f.width + s.width + 12, 470), "white")
        t.paste(f, (0, 40))
        t.paste(s, (f.width + 8, 40))
        d = ImageDraw.Draw(t)
        mark = " (recommended)" if c["variant"] == cand["recommended"] else ""
        d.text((3, 3), f"{c['variant']}{mark}", fill=(0, 0, 160) if mark else (0, 0, 0))
        d.text((3, 18), c["feasibility"]["class"], fill=(90, 90, 90))
        for i, w in enumerate(c["silhouette"]["warnings"][:3]):
            d.text((3, 404 + 15 * i), f"! {w['code']} {w.get('side', '')}", fill=(170, 0, 0))
        tiles.append(t)
    W = sum(t.width + 10 for t in tiles)
    sheet = Image.new("RGB", (W, 470), "white")
    x = 0
    for t in tiles:
        sheet.paste(t, (x, 0))
        x += t.width + 10
    sheet.save(path)


def _contact(cols, out: Path) -> None:
    H = 400
    tiles = []
    for letter, rec, before, after in cols:
        fa, sa = _fig(after["image"], H), _sil(after["image"], H)
        fb = _fig(before["image"], H) if before is not None else None
        sb = _sil(before["image"], H) if before is not None else None
        w = max(fa.width, fb.width if fb else 120) + 8
        t = Image.new("RGB", (w, 4 * H + 150), "white")
        d = ImageDraw.Draw(t)
        d.text((3, 3), f"{letter} {rec['action']}", fill=(0, 0, 0))
        if fb:
            t.paste(fb, (4, 20))
            t.paste(sb, (4, 24 + 2 * H))
        else:
            d.text((6, H // 2), "no Phase 3.4\npose", fill=(150, 0, 0))
        t.paste(fa, (4, 22 + H))
        t.paste(sa, (4, 26 + 3 * H))
        y = 30 + 4 * H
        cls = rec["feasibility"]["class"].replace("REQUIRES_", "REQ_")
        d.text((3, y), cls, fill=(0, 90, 0) if cls.startswith("SUPPORTED") else (170, 90, 0))
        if rec["pending_hands"]:
            d.text((3, y + 15), f"pending hands: {','.join(rec['pending_hands'])}", fill=(120, 80, 0))
        d.text((3, y + 30), f"warnings {len(rec['silhouette_before']['warnings']) if rec['silhouette_before'] else '-'}"
                            f" -> {len(rec['silhouette_after']['warnings'])}", fill=(60, 60, 60))
        d.text((3, y + 45), "human review: pending", fill=(170, 0, 0))
        tiles.append(t)
    half = (len(tiles) + 1) // 2
    for i, part in enumerate((tiles[:half], tiles[half:])):
        if not part:
            continue
        W = sum(t.width for t in part) + 90
        sheet = Image.new("RGB", (W, part[0].height), "white")
        d = ImageDraw.Draw(sheet)
        for j, lab in enumerate(("Phase 3.4", "Phase 3.5", "3.4 silhouette", "3.5 silhouette")):
            d.text((4, 20 + j * (H + 2) + H // 2), lab, fill=(0, 0, 0))
        x = 90
        for t in part:
            sheet.paste(t, (x, 0))
            x += t.width
        sheet.save(out / f"before_after_{i + 1}.png")


def _closeups(eng, vis, out: Path, fk_state) -> dict[str, Any]:
    cases = [("elbow 95", "relaxed_standing", {"ik": fk_state({"r": (15.0, 95.0)})}, "forearm_r", 90),
             ("elbow 120", "relaxed_standing", {"ik": fk_state({"r": (15.0, 120.0)})}, "forearm_r", 90),
             ("squat knee", "natural_squat", {}, "shin_r", 110), ("sit knee", "sitting", {}, "shin_l", 110),
             ("armpit 135", "relaxed_standing", {"ik": fk_state({"r": (135.0, 5.0)})}, "upper_arm_r", 120)]
    cols, rec = [], {}
    for lab, preset, kw, j, rad in cases:
        row = []
        for mode, ov in (("off", None), ("all", {k: False for k in NEW_ASSETS}), ("all", None)):
            r = eng.pose(preset, visible=vis, evaluate_garments=False, corrective_mode=mode, corrective_overrides=ov, **kw)
            pad = r["pad"]
            J = r["solve"]["joints_posed"][j]
            c = (J[0] + pad, J[1] + pad)
            row.append(on_bg(r["image"]).crop((int(c[0] - rad), int(c[1] - rad), int(c[0] + rad), int(c[1] + rad)))
                       .resize((240, 240)))
            if mode == "all" and ov is None:
                rec[lab] = {k: v for k, v in (r["render"].get("joint_fold_info") or {}).items()}
        cols.append((lab, row))
    sheet = Image.new("RGB", (len(cols) * 244 + 120, 3 * 244 + 24), "white")
    d = ImageDraw.Draw(sheet)
    for i, lab in enumerate(("no correction", "Phase 3.4 caps", "Phase 3.5 folds")):
        d.text((4, 24 + i * 244 + 110), lab, fill=(0, 0, 0))
    for i, (lab, row) in enumerate(cols):
        d.text((120 + i * 244, 4), lab, fill=(0, 0, 0))
        for k, im in enumerate(row):
            sheet.paste(im, (120 + i * 244, 22 + k * 244))
    sheet.save(out / "joint_closeups.png")
    return rec


def _layer_demo(eng, vis, planner, out: Path) -> dict[str, Any]:
    tiles, rec = [], {}
    for action in ("hand_behind_back",):
        ap = variant(action)
        for dyn in (False, True):
            r = render_pose(eng, vis, ap, planner=planner,
                            layer_order=DynamicLayerOrderController() if dyn else _NoOrder())
            tiles.append((f"{'dynamic' if dyn else 'static'} order", _fig(r["image"], 480)))
            rec[f"{action}_{'dynamic' if dyn else 'static'}"] = r.get("layer_order")
    W = sum(f.width + 10 for _, f in tiles)
    sheet = Image.new("RGB", (W, 505), "white")
    d = ImageDraw.Draw(sheet)
    x = 0
    for lab, f in tiles:
        sheet.paste(f, (x, 22))
        d.text((x + 3, 3), f"hand behind the back: {lab}", fill=(0, 0, 0))
        x += f.width + 10
    sheet.save(out / "layer_order_demo.png")
    return rec


class _NoOrder:
    def apply(self, order, info, it, eng=None, depth=None, action=""):
        return order, {"changed": False, "fired": [], "ambiguous": [], "moved": []}


def acting_test(eng, vis, planner, out: Path, progress, fps: float = 12.0) -> dict[str, Any]:
    seq = ActingSequence(acting_test_keys(), fps)
    lo = DynamicLayerOrderController()
    ev = ArtisticAnimationEvaluator(eng)
    frames, recs, evs = [], [], []
    t0 = time.perf_counter()
    for k, (t, ap, meta) in enumerate(seq.frames()):
        r = render_pose(eng, vis, ap, planner=planner, layer_order=lo)
        acts = {s: g.hand for s, g in ap.arms.items()}
        fe = ev.frame(r, "acting_test", {f"gesture_{s}": "hang" for s in ("r", "l")}, acts)
        J = r["solve"]["joints_posed"]
        evs.append({"joints": {n: J[n] for n in J}, "hands": {s: r["hands"][s]["drawing"] for s in ("r", "l")},
                    "garment_area_ratio": fe["garment_area_ratio"], "garment_variants": fe["garment_variants"]})
        recs.append({"frame": k, "t": round(t, 3), **meta, "hands": evs[-1]["hands"],
                     "warnings": [w["code"] for w in fe["warnings"]], "feasibility": r["feasibility"]["status"],
                     "bone_length_change": fe["max_bone_length_change"], "com_outside_support_px": fe["com_outside_support_px"]})
        frames.append(r["image"])
    dt = time.perf_counter() - t0

    def solve_at(t):
        ap_, _ = seq.sample(t)
        rq = planner.request(ap_)
        nm = rq.pop("name")
        rq.pop("depth", None)
        return eng.pose(nm, visible=vis, skeleton_only=True, **rq)["solve"]
    cont = ev.continuity_check(lambda t: params_of(seq.sample(t)[0]), seq.duration, solve_at, fps)
    seq_eval = ev.sequence(evs, fps, continuity=cont)
    # frames on a common canvas (union bbox), downscaled
    union = np.zeros(frames[0].shape[:2], bool)
    for f in frames:
        union |= f[..., 3] > 0
    ys, xs = np.nonzero(union)
    box = (max(0, xs.min() - 12), max(0, ys.min() - 12), xs.max() + 12, ys.max() + 12)
    ims = []
    for k, f in enumerate(frames):
        im = on_bg(f).crop(box)
        im = im.resize((int(im.width * 540 / im.height), 540), Image.LANCZOS)
        d = ImageDraw.Draw(im)
        d.text((6, 6), f"{recs[k]['t']:.2f}s {recs[k]['key']}", fill=(90, 90, 90))
        ims.append(im)
    (out / "acting_test").mkdir(exist_ok=True)
    for k in range(0, len(ims), 6):
        ims[k].save(out / "acting_test" / f"frame_{k:03d}.png")
    ims[0].save(out / "acting_test.gif", save_all=True, append_images=ims[1:], duration=int(1000 / fps), loop=0)
    try:
        ims[0].save(out / "acting_test.webp", save_all=True, append_images=ims[1:], duration=int(1000 / fps), loop=0)
    except Exception:  # noqa: BLE001
        pass
    step = max(1, len(ims) // 14)
    strip = [ims[k] for k in range(0, len(ims), step)]
    W = sum(i.width // 2 + 4 for i in strip)
    sheet = Image.new("RGB", (W, 275), "white")
    x = 0
    for im in strip:
        sheet.paste(im.resize((im.width // 2, 270)), (x, 2))
        x += im.width // 2 + 4
    sheet.save(out / "acting_test_strip.png")
    progress(f"acting test: {len(frames)} frames in {dt:.0f} s; continuity {seq_eval['continuity']}; "
             f"hand switches {seq_eval['hand_switches']}; flicker {len(seq_eval['hand_switch_flicker'])}")
    return {"fps": fps, "duration_s": seq.duration, "frames": recs, "sequence": seq_eval,
            "render_s_per_frame": round(dt / len(frames), 2),
            "keys": [{"t": kp.t, "label": kp.label, "hold": kp.hold, "anticipation": kp.anticipation,
                      "overshoot": kp.overshoot} for kp in seq.keys],
            "head_turn": "approximated by a head tilt toward the target (turned-head drawings: REQUIRES_NEW_VIEW)"}


def benchmark(eng, vis, planner, n: int = 3) -> dict[str, Any]:
    """A: offline evidence (total); B1: interactive skeleton-only; B2: interactive preview (approved correctives,
    no evaluation); C: offline high quality (all correctives, decontamination); D: artistic + geometric evaluation."""
    from .silhouette import SilhouetteQualityEvaluator  # noqa: PLC0415

    ap = variant("point_right")
    rq = planner.request(ap)
    name = rq.pop("name")
    rq.pop("depth", None)

    def med(fn):
        ts = []
        for _ in range(n):
            t0 = time.perf_counter()
            fn()
            ts.append(time.perf_counter() - t0)
        return round(float(np.median(ts)), 3)
    out = {"B1_skeleton_only_s": med(lambda: eng.pose(name, visible=vis, skeleton_only=True, **rq)),
           "B2_interactive_preview_s": med(lambda: eng.pose(name, visible=vis, evaluate_garments=False,
                                                            corrective_mode="auto", **rq)),
           "C_offline_render_s": med(lambda: eng.pose(name, visible=vis, evaluate_garments=False,
                                                      corrective_mode="all", **rq))}

    def full():
        r = eng.pose(name, visible=vis, evaluate_garments=True, corrective_mode="all", **rq)
        SilhouetteQualityEvaluator().evaluate(r, "point_right", {})
    out["D_full_evaluation_s"] = med(full)
    out["note"] = ("cloud sandbox; the interactive editor uses B1 while dragging and B2 on release; evaluation runs on "
                   "request only. No Apple M1 figure is claimed.")
    return out


def moho_matrix() -> dict[str, Any]:
    rows = {
        "pose presets / artistic poses": ("baked", "keyframed bone angles per pose (actions)", "proposed_unverified"),
        "hand switches (gesture -> drawing)": ("switch_layer", "switch layer keys driven by a Smart Bone action or keys",
                                              "proposed_unverified"),
        "alternative limb drawings": ("switch_layer", "switch layer per limb, keyed per pose", "proposed_unverified"),
        "limb configurations": ("vitruvian_bones", "alternative bone sets per limb (not exercised)", "proposed_unverified"),
        "dynamic draw order": ("layer_order_keys", "Moho layer reordering is not assumed scriptable: bake order per pose "
                               "as separate layer stacks or switch layers", "proposed_unverified"),
        "elbow/knee fold corrections": ("baked", "bake key drawings into a switch layer driven by a Smart Bone action",
                                        "proposed_unverified"),
        "armpit gusset (procedural)": ("baked", "keyed gusset drawings (5 keys) in a switch layer", "proposed_unverified"),
        "transitions (anticipation, overshoot)": ("baked", "keyframes on bones; Moho interpolation is not the AutoRig "
                                                  "easing", "proposed_unverified"),
        "mesh deformation (weights)": ("not_assumed", "per-vertex weights are not assumed to import", "proposed_unverified"),
        "artwork selection engine": ("custom_script_or_baked", "AutoRig's selection does not run inside Moho; export "
                                     "decisions as keys", "proposed_unverified")}
    return {"rows": {k: {"class": a, "mapping": b, "status": c} for k, (a, b, c) in rows.items()}, "verified": [],
            "notes": ["Moho was not run; no Lua API member is used or invented; no exporter in this phase"]}


def live_notes() -> dict[str, Any]:
    return {"semantic_hand_triggers": "hand actions (pointing, shrugging, raised, relaxed) map to switch drawings",
            "pose_presets_reusable": "ArtisticPose JSON templates (project templates)",
            "ik_stability": "artistic poses only reshape the intent; the one IK solver clamps unreachable targets",
            "channels_independent": "body poses never touch facial layers (face rig unchanged)",
            "deterministic_selection": "hand, alternative and corrective choices are pure functions of the pose",
            "no_generative_inference_at_playback": True, "correctives_prepared_before_performance": True,
            "safe_fallbacks": "REQUIRES_* poses render a labelled fallback, never a forced deformation",
            "unsupported_reported": "feasibility classes per pose", "webcam_tracking": "not implemented",
            "showtime_mapping": "proposed_unverified"}


def _js(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return None
    return str(o)


__all__ = ["artistic_evidence", "acting_test", "benchmark", "moho_matrix", "live_notes", "POSE_TABLE", "ACTIONS_12"]
