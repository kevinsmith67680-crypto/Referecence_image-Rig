"""Phase 3.4 evidence: corrective artwork on the real character (``autorig corrective-evidence <package>``).

For every test pose three renders are made with the same solve:
  * ``off``: correctives off (the structured garment rig alone);
  * ``on``: correctives previewed (pending review, ``corrective_mode="all"``) without the edge decontamination, for
    the geometric measurements, because decontaminated silhouette pixels are semi-transparent by design and the
    gap rules count alpha < 0.5 as open;
  * ``dc``: ``on`` plus the decontaminated derived layers (what the user would see).

``CorrectiveQualityEvaluator`` tracks the Phase 3.4 defect classes separately:
  A genuine gaps (exposed tear points, stretch-clipped open pixels);
  B reconstructed-pixel leakage (original pixels changed by a corrective; measured by diffing ``off`` and ``on``);
  C visible seams; D anti-aliasing contamination (light specks in dark cloth, over a dark background);
  E invalid mesh deformation (garment evaluator); F popping (sweeps);
  G review status (every corrective is pending until a person approves it).
It grades PASS / WARNING / NEEDS_ARTWORK / FAIL with ``review_required`` always true. An automatic PASS is never a
production approval.
"""

from __future__ import annotations

import json
import os
import platform
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from ..motion.quality import THRESHOLDS as QT
from ..phase2.pose_eval import on_bg
from .correctives import (CorrectiveArtworkPlanner, CorrectiveArtworkStore, CorrectiveRuntime, PoseSpaceCorrectionEngine,
                          approval_state, generate_all)
from .evidence import fk_state

# Step 20 test poses: (key, title, preset, kwargs, close-up joint)
POSES: list[tuple[str, str, str, dict[str, Any], str]] = [
    ("neutral", "neutral standing", "relaxed_standing", {}, "upper_arm_r"),
    ("arm45", "right arm 45 deg", "relaxed_standing", {"fk": {"r": (45.0, 5.0)}}, "upper_arm_r"),
    ("arm90", "right arm 90 deg", "relaxed_standing", {"fk": {"r": (90.0, 5.0)}}, "upper_arm_r"),
    ("arm120", "right arm 120 deg", "relaxed_standing", {"fk": {"r": (120.0, 5.0)}}, "upper_arm_r"),
    ("both_raised", "both arms raised", "both_hands_raised", {}, "upper_arm_l"),
    ("elbow45", "elbow 45 deg", "relaxed_standing", {"fk": {"r": (15.0, 45.0)}}, "forearm_r"),
    ("elbow95", "elbow 95 deg", "relaxed_standing", {"fk": {"r": (15.0, 95.0)}}, "forearm_r"),
    ("elbow135", "elbow 135 deg (deep)", "relaxed_standing", {"fk": {"r": (15.0, 135.0)}}, "forearm_r"),
    ("pointing", "pointing", "pointing_speaking", {}, "upper_arm_r"),
    ("walk", "walking (phase 0.25)", "casual_walk", {"phase": 0.25}, "shin_l"),
    ("run", "running (phase 0.25)", "running", {"phase": 0.25}, "shin_l"),
    ("sit", "sitting", "sitting", {}, "shin_l"),
    ("squat", "squatting", "natural_squat", {}, "shin_r"),
    ("lean", "torso side lean", "side_lean", {}, "neck"),
    ("head_tilt", "head tilt with collar", "head_tilt_compensated", {}, "neck"),
]

# Step 15 combined-pose sequences (existing presets and the FK/IK path; no second solver)
SEQUENCES: list[tuple[str, str, list[tuple[str, dict[str, Any]]], str]] = [
    ("arm_raise_elbow", "arm raise + elbow flexion",
     [("relaxed_standing", {"fk": {"r": (a, f)}}) for a, f in zip(np.linspace(20, 150, 13), np.linspace(5, 100, 13))],
     "upper_arm_r"),
    ("arm_raise_lean", "arm raise + torso lean",
     [("side_lean", {"fk": {"r": (a, 5.0)}, "exaggeration": e}) for a, e in zip(np.linspace(20, 150, 9),
                                                                              np.linspace(0.2, 1.0, 9))],
     "upper_arm_r"),
    ("walk_blazer", "walking cycle + blazer", [("casual_walk", {"phase": p}) for p in np.linspace(0, 1, 9)[:-1]],
     "pelvis"),
    ("run_knee", "running + knee drive", [("running", {"phase": p}) for p in np.linspace(0, 1, 9)[:-1]], "shin_l"),
    # the static presets (squat, sitting, head tilt) have no intermediate parameter: knee flexion is swept through
    # the existing leg IK (ankle target raised, foot unlocked), the head through the live head-tilt input
    ("knee_flex_ik", "knee flexion via leg IK (squat/sit knee range)",
     [("relaxed_standing", {"leg_lift": float(d)}) for d in np.linspace(0, 240, 17)], "shin_r"),
    ("head_collar", "head tilt + collar", [("relaxed_standing", {"live_tilt": float(t)}) for t in np.linspace(0, 24, 7)],
     "neck"),
]
# Step 5 / 7 joint sweeps
ARMPIT_SWEEP = [0.0, 30.0, 45.0, 60.0, 90.0, 120.0, 135.0, 150.0]
ELBOW_SWEEP = [0.0, 45.0, 90.0, 95.0, 120.0, 135.0]

# Phase 3.4 quality thresholds (documented in the report; the Phase 3.1 tear thresholds are reused unchanged)
THRESHOLDS = {
    "protected_violation_px": (0, "original (non-patch) pixels a corrective changed: must be 0"),
    "exposed_gap_points": (QT["exposed_gap_pairs"][0], "Phase 3.1 exposed_gap_pairs threshold, unchanged"),
    "visible_seam_points": (QT["visible_seam_pairs"][0], "Phase 3.1 visible_seam_pairs threshold, unchanged"),
    "clipped_open_px": (150, "stretch-clipped joint-patch pixels still open (a visible conflict)"),
    "pop_weight_jump": (0.5, "a drawn asset's activation weight changing by more than 0.5 between adjacent sweep frames "
                             "(about 10 deg apart or less) = pop; gap fills follow the gap and are excluded"),
    "light_speck_increase": (0, "decontamination must not add light specks"),
}


def _crop(img: np.ndarray, c, r: int) -> Image.Image:
    return on_bg(img).crop((int(c[0] - r), int(c[1] - r), int(c[0] + r), int(c[1] + r)))


def light_specks(img: np.ndarray, bg=(40, 40, 48)) -> int:
    """Light pixels inside dark cloth when composited over a dark background: lum > 125 with a 5x5 mean < 75 (the
    anti-aliasing fringe of the white paper; the white shirt and trims have light neighbourhoods)."""
    a = img[..., 3:4].astype(np.float32) / 255.0
    rgb = img[..., :3] * a + np.array(bg, np.float32) * (1 - a)
    lum = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    return int(((lum > 125) & (ndimage.uniform_filter(lum, 5) < 75) & (img[..., 3] > 0)).sum())


class CorrectiveQualityEvaluator:
    """Per-pose corrective quality record (classes A-G), graded separately; never a visual-quality score."""

    def evaluate(self, off: dict[str, Any], on: dict[str, Any], dc: dict[str, Any] | None) -> dict[str, Any]:
        Ro, Rn = off["render"], on["render"]
        io, inn = off["image"].astype(np.int16), on["image"].astype(np.int16)
        orig = (io[..., 3] > 127) & ~Ro["generated_visible"]
        # an original pixel is intact when the new pixel is the old one composited over something (a corrective
        # beneath a semi-transparent edge pixel only changes what shows through it): premultiplied difference
        # between 0 and (1 - alpha_off) * 255 per channel, alpha not lower (tolerance 3 levels)
        ao, an = io[..., 3] / 255.0, inn[..., 3] / 255.0
        d = inn[..., :3] * an[..., None] - io[..., :3] * ao[..., None]
        beneath = (an >= ao - 0.012) & (d >= -3).all(-1) & (d <= (1 - ao)[..., None] * 255 + 3).all(-1)
        changed = ~beneath
        patch = Ro["piece_top"] == Ro["piece_codes"]["patch"]
        viol = int((orig & changed & ~patch).sum())
        patch_cov = int((orig & changed & patch).sum())
        T = THRESHOLDS
        g_off, g_on = len(off["tears"]["exposed_gap"]), len(on["tears"]["exposed_gap"])
        s_off, s_on = len(off["tears"]["visible_seam"]), len(on["tears"]["visible_seam"])
        c_off, c_on = sum(Ro["clipped_uncovered"].values()), sum(Rn["clipped_uncovered"].values())
        f_on = light_specks(on["image"])
        f_dc = light_specks(dc["image"]) if dc is not None else None
        ge = on.get("garment_eval") or {}
        mesh = {k: v["status"] for k, v in (ge.get("parts") or {}).items() if v["status"] in ("FAIL", "NEEDS_ARTWORK")}
        shown = [c for c in on.get("correctives", []) if c.get("shown")]
        pending = [c["approval_key"] for c in shown if c["status"] != "approved"]
        feas = on["feasibility"]["status"]
        checks = {
            "A_gaps": {"exposed_gap_points": [g_off, g_on], "clipped_open_px": [c_off, c_on],
                       "pass": g_on <= T["exposed_gap_points"][0] and c_on <= T["clipped_open_px"][0]},
            "B_leakage": {"protected_violation_px": viol, "joint_patch_covered_px": patch_cov,
                          "prevented_overlap_px": int(Rn.get("protected_overlap_prevented_px", 0)), "pass": viol == 0},
            "C_seams": {"visible_seam_points": [s_off, s_on], "pass": s_on <= T["visible_seam_points"][0]},
            "D_fringe": {"light_specks": [f_on, f_dc], "pass": f_dc is None or f_dc <= f_on},
            "E_mesh": {"parts_fail_or_needs_artwork": mesh,
                       "pass": not any(v == "FAIL" for v in mesh.values())},
            "G_review": {"shown_correctives": [c["asset_id"] for c in shown], "pending_review": pending,
                         "pass": not pending},
        }
        if viol > T["protected_violation_px"][0]:
            status = "FAIL"
        elif feas == "NEEDS_ARTWORK":
            status = "NEEDS_ARTWORK"
        elif not all(c["pass"] for k, c in checks.items() if k != "G_review"):
            status = "WARNING" if viol == 0 else "FAIL"
        else:
            status = "PASS"
        return {"status": status, "review_required": True, "feasibility": feas,
                "missing": [m["asset"] for m in on["feasibility"].get("missing", [])], "checks": checks,
                "corrective_px": int(Rn.get("corrective_px", 0)),
                "activation": [{k: c.get(k) for k in ("asset_id", "weight", "keys", "flags", "status", "driver_value")}
                               for c in on.get("correctives", []) if c.get("weight", 0) > 0],
                "note": "automatic grade only; visual quality needs a person (PASS is not a production approval)"}


def _render(eng, vis, preset, kw, mode, evaluate=False, decont=False):
    from .evidence import run_test

    if "leg_lift" in kw or "live_tilt" in kw:
        from ..motion.rigctl import default_ik_state  # noqa: PLC0415

        ik, live = None, None
        if "leg_lift" in kw:
            ik = default_ik_state()
            A = eng.sk.bone("foot_r").head
            ik["leg_r"].update(enabled=True, mode="ik", lock=False, target=[float(A[0]), float(A[1] - kw["leg_lift"])])
        if "live_tilt" in kw:
            live = {"tilt": float(kw["live_tilt"])}
        t0 = time.perf_counter()
        r = eng.pose(preset, visible=vis, ik=ik, live_head=live, evaluate_garments=evaluate, corrective_mode=mode,
                     corrective_overrides=None if decont else {"edge_decontamination": False})
        r["wall_s"] = round(time.perf_counter() - t0, 3)
        return r

    k = {key: v for key, v in kw.items() if key in ("phase", "fk")}
    args = {**k, "corrective_mode": mode}
    if not decont:
        args["corrective_overrides"] = {"edge_decontamination": False}
    r = run_test(eng, vis, preset, args, evaluate)
    if "exaggeration" in kw:  # run_test has no exaggeration argument: re-pose directly
        t0 = time.perf_counter()
        r = eng.pose(preset, visible=vis, ik=fk_state(kw["fk"]) if "fk" in kw else None, phase=kw.get("phase"),
                     exaggeration=float(kw["exaggeration"]), evaluate_garments=evaluate, corrective_mode=mode,
                     corrective_overrides=None if decont else {"edge_decontamination": False})
        r["wall_s"] = round(time.perf_counter() - t0, 3)
    return r


def _sheet(cols: list[tuple[str, list[Image.Image]]], tile: int, labels: list[str]) -> Image.Image:
    n = max(len(t) for _, t in cols)
    W = len(cols) * (tile + 4) + 90
    out = Image.new("RGB", (W, n * (tile + 4) + 24), "white")
    d = ImageDraw.Draw(out)
    for j, lab in enumerate(labels[:n]):
        d.text((4, 24 + j * (tile + 4) + tile // 2), lab, fill=(20, 20, 20))
    for i, (title, tiles) in enumerate(cols):
        x = 90 + i * (tile + 4)
        d.text((x + 2, 6), title, fill=(20, 20, 20))
        for j, t in enumerate(tiles):
            out.paste(t.resize((tile, tile)), (x, 24 + j * (tile + 4)))
    return out


def _centre(r, joint):
    J = r["solve"]["joints_posed"][joint]
    return (J[0] + r["pad"], J[1] + r["pad"])


def corrective_evidence(pkg, out: Path | None = None, regenerate: bool = False, quick: bool = False,
                        progress: Callable[[str], Any] = print) -> dict[str, Any]:
    from ..motion.pipeline import engine_for_package

    pkg = Path(pkg)
    out = Path(out or pkg / "natural_pose" / "correctives")
    out.mkdir(parents=True, exist_ok=True)
    t_all = time.perf_counter()
    eng, vis = engine_for_package(pkg)
    store = CorrectiveArtworkStore(pkg)
    if regenerate or not store.index["assets"]:
        generate_all(eng, store, vis, progress)
        store = CorrectiveArtworkStore(pkg)
    eng.correctives = CorrectiveRuntime(eng, store)
    assets = store.latest()
    progress(f"{len(assets)} corrective assets (latest versions); store hashes ok: {not store.verify()}")
    results: dict[str, Any] = {"poses": {}, "sweeps": {}, "sequences": {}}
    planner_in, renders = [], {}
    poses = POSES if not quick else POSES[:4]
    for key, title, preset, kw, joint in poses:
        t0 = time.perf_counter()
        off = _render(eng, vis, preset, kw, "off", evaluate=False)
        on = _render(eng, vis, preset, kw, "all", evaluate=True)
        dc = _render(eng, vis, preset, kw, "all", decont=True)
        q = CorrectiveQualityEvaluator().evaluate(off, on, dc)
        q["title"], q["wall_s"] = title, round(time.perf_counter() - t0, 2)
        q["timing_ms"] = {"off": off["timing_ms"].get("total"), "on_with_eval": on["timing_ms"].get("total"),
                          "on_display": dc["timing_ms"].get("total")}
        results["poses"][key] = q
        planner_in.append({"pose": key, "result": {**off, "readings": _readings(eng, off)}})
        renders[key] = (off, dc, joint)
        c = q["checks"]
        progress(f"{key}: {q['status']} gaps {c['A_gaps']['exposed_gap_points']} clipped {c['A_gaps']['clipped_open_px']}"
                 f" seams {c['C_seams']['visible_seam_points']} leak {c['B_leakage']['protected_violation_px']}"
                 f" specks {c['D_fringe']['light_specks']} ({q['wall_s']} s)")
    # contact sheets and close-ups
    _contact(renders, out)
    _closeups(eng, vis, renders, out, results, quick, progress)
    if not quick:
        for name, title, seq, joint in SEQUENCES:
            results["sequences"][name] = _sequence(eng, vis, name, title, seq, joint, out, progress)
    # requirements, pose-space definitions, quality report
    reqs = CorrectiveArtworkPlanner().plan(planner_in)
    from .correctives import ANSWERS  # noqa: PLC0415

    for r in reqs:  # link requirements to the assets that answer them
        names = {r["id"].replace("REQ-", "").replace("_", ":", 1) if r["id"].startswith("REQ-corrective_") else ""}
        alt = {aid for n in names for k, v in ANSWERS.items() if k.split(":")[1] == n.split(":")[-1] for aid in v}
        r["assets"] = [{"asset_id": a.asset_id, "version": a.version, "approval_key": a.approval_key,
                        "state": approval_state(eng.approvals, a)} for a in assets
                       if a.requirement_id == r["id"] or a.asset_id in alt]
        if not r["assets"] and r["correction_type"] == "replacement_drawing":
            r["state"] = "NEEDS_ARTWORK (no drawing; none is invented)"
    store.write_json("analysis/corrective_requirements.json",
                     {"schema": "autorig.corrective_requirements", "version": "1.0.0", "requirements": reqs})
    store.write_json("analysis/pose_space_correctives.json", pose_space_doc(assets))
    _asset_previews(store, assets, out)
    _needs_artwork_sheet(renders, results, out)
    bench = benchmark(eng, vis, quick)
    results["benchmark"] = {**bench, "A_offline_evidence_s": round(time.perf_counter() - t_all, 1)}
    from .moho import corrective_matrix

    report = {"schema": "autorig.corrective_quality", "version": "1.0.0", "thresholds": {k: list(v) for k, v in
                                                                                          THRESHOLDS.items()},
              "assets": [{"asset_id": a.asset_id, "version": a.version, "category": a.category, "method": a.method,
                          "provenance": a.provenance, "state": approval_state(eng.approvals, a),
                          "approval_key": a.approval_key, "activation": a.activation, "params": a.params}
                         for a in assets],
              "results": results, "moho_matrix": corrective_matrix(assets), "live": live_notes(eng, vis),
              "visual_review_required": True,
              "production_approved": False}
    store.write_json("reports/corrective_quality.json", report)
    (out / "results.json").write_text(json.dumps(report, indent=1, default=_js))
    progress(f"done in {results['benchmark']['A_offline_evidence_s']} s -> {out}")
    return report


def _readings(eng, r):
    from ..motion.switches import correction_readings_from_pose

    return correction_readings_from_pose(eng.solver, r["pose"])


def pose_space_doc(assets) -> dict[str, Any]:
    eng = PoseSpaceCorrectionEngine()
    samples = []
    for a in assets:
        act = a.activation
        if act.get("kind") not in ("ramp", "keyed"):
            continue
        lo, hi = float(act["lo"]), float(act["hi"])
        for v in np.linspace(lo - 20, hi + 20, 9):
            w = eng.activate([a], {act["driver"]: float(v)})[0]
            samples.append({"asset_id": a.asset_id, "driver": act["driver"], "value": round(float(v), 1),
                            "weight": round(w["weight"], 4)})
    return {"schema": "autorig.pose_space_correctives", "version": "1.0.0",
            "engine": "PoseSpaceCorrectionEngine: eased ramps per driver; normalised Gaussian RBF over sparse keys for "
                      "keyed sets (two strongest drawn); exclusive groups by priority; artist overrides win; weight 0 in "
                      "the neutral pose; readings beyond the keys flagged unsupported_combination",
            "assets": [{"asset_id": a.asset_id, "drivers": a.drivers, "activation": a.activation, "priority": a.priority,
                        "exclusive_group": a.exclusive_group, "keys": [{k: v for k, v in kk.items() if k != "bind_matrix"}
                                                                       for kk in a.keys]} for a in assets],
            "samples": samples}


def _contact(renders, out: Path) -> None:
    from .evidence import figure

    cols = []
    for key, (off, dc, _) in renders.items():
        fa, fb = figure(dc, 420), figure(off, 420)
        w = max(fa.width, fb.width) + 10
        col = Image.new("RGB", (w, 2 * 420 + 50), "white")
        d = ImageDraw.Draw(col)
        d.text((4, 4), key, fill=(0, 0, 0))
        col.paste(fb, (5, 20))
        col.paste(fa, (5, 40 + 420))
        d.text((4, 24 + 420), "correctives (pending preview)", fill=(30, 30, 150))
        cols.append(col)
    half = (len(cols) + 1) // 2
    for i, part in enumerate((cols[:half], cols[half:])):
        if not part:
            continue
        W = sum(c.width for c in part)
        sheet = Image.new("RGB", (W, part[0].height), "white")
        x = 0
        for c in part:
            sheet.paste(c, (x, 0))
            x += c.width
        sheet.save(out / f"contact_sheet_{i + 1}.png")


def _closeups(eng, vis, renders, out: Path, results, quick, progress) -> None:
    # armpit sweep (0..150 deg), elbow sweep, knees, shoe tops, fringes
    def sweep(name, rows, joint, r):
        cols = []
        recs = []
        for lab, preset, kw in rows:
            off = _render(eng, vis, preset, kw, "off")
            dc = _render(eng, vis, preset, kw, "all", decont=True)
            on = _render(eng, vis, preset, kw, "all")
            c = _centre(dc, joint)
            cols.append((lab, [_crop(off["image"], c, r), _crop(dc["image"], c, r)]))
            recs.append({"label": lab, "gaps": [len(off["tears"]["exposed_gap"]), len(on["tears"]["exposed_gap"])],
                         "clipped_open_px": [sum(off["render"]["clipped_uncovered"].values()),
                                             sum(on["render"]["clipped_uncovered"].values())],
                         "corrective_px": on["render"]["corrective_px"],
                         "active": [x["asset_id"] for x in on["correctives"] if x.get("shown")]})
        _sheet(cols, 240, ["off", "on"]).save(out / f"{name}.png")
        results["sweeps"][name] = recs
        progress(f"{name}: {[(x['label'], x['gaps'], x['clipped_open_px']) for x in recs]}")

    sweep("armpit_sweep", [(f"{a:.0f}", "relaxed_standing", {"fk": {"r": (a, 5.0)}})
                           for a in (ARMPIT_SWEEP if not quick else [0.0, 120.0])], "upper_arm_r", 120)
    sweep("elbow_sweep", [(f"{f:.0f}", "relaxed_standing", {"fk": {"r": (15.0, f)}})
                          for f in (ELBOW_SWEEP if not quick else [0.0, 95.0])], "forearm_r", 95)
    if quick:
        return
    knees = [("walk", "casual_walk", {"phase": 0.25}, "shin_l"), ("run", "running", {"phase": 0.25}, "shin_l"),
             ("run .6", "running", {"phase": 0.6}, "shin_r"), ("squat", "natural_squat", {}, "shin_r"),
             ("sit", "sitting", {}, "shin_l")]
    cols = []
    for lab, preset, kw, joint in knees:
        off = _render(eng, vis, preset, kw, "off")
        dc = _render(eng, vis, preset, kw, "all", decont=True)
        c = _centre(dc, joint)
        cols.append((lab, [_crop(off["image"], c, 110), _crop(dc["image"], c, 110)]))
    _sheet(cols, 240, ["off", "on"]).save(out / "knee_closeups.png")
    cols = []
    for lab, preset, kw in [("stand", "relaxed_standing", {}), ("walk", "casual_walk", {"phase": 0.25}),
                            ("run", "running", {"phase": 0.25}), ("sit", "sitting", {}),
                            ("squat", "natural_squat", {})]:
        off = _render(eng, vis, preset, kw, "off")
        dc = _render(eng, vis, preset, kw, "all", decont=True)
        for s in ("r", "l"):
            c = _centre(dc, f"foot_{s}")
            cols.append((f"{lab} {s}", [_crop(off["image"], (c[0], c[1] - 20), 75),
                                        _crop(dc["image"], (c[0], c[1] - 20), 75)]))
    _sheet(cols, 180, ["off", "on"]).save(out / "shoe_top_closeups.png")
    # fringe: decontamination off/on over light and dark backgrounds
    tiles = []
    for key, joint in (("elbow95", "forearm_r"), ("arm45", "upper_arm_r"), ("squat", "shin_r")):
        if key not in renders:
            continue
        preset, kw = next((p[2], p[3]) for p in POSES if p[0] == key)
        a = _render(eng, vis, preset, kw, "all")
        b = _render(eng, vis, preset, kw, "all", decont=True)
        c = _centre(b, joint)
        row = []
        for r in (a, b):
            for bg in ((255, 255, 255), (40, 40, 48)):
                row.append(on_bg(r["image"], bg).crop((int(c[0] - 80), int(c[1] - 70), int(c[0] + 50), int(c[1] + 60))))
        tiles.append((key, row))
    _sheet(tiles, 260, ["before / light", "before / dark", "after / light", "after / dark"]).save(
        out / "fringe_comparison.png")


def _sequence(eng, vis, name, title, seq, joint, out: Path, progress) -> dict[str, Any]:
    frames, recs = [], []
    prev = None
    for i, (preset, kw) in enumerate(seq):
        on = _render(eng, vis, preset, kw, "all")
        dc = _render(eng, vis, preset, kw, "all", decont=True)
        c = _centre(on, joint)
        frames.append(_crop(dc["image"], c, 150))
        rec = {"frame": i, "gaps": len(on["tears"]["exposed_gap"]), "seams": len(on["tears"]["visible_seam"]),
               "clipped_open_px": sum(on["render"]["clipped_uncovered"].values()),
               "corrective_px": on["render"]["corrective_px"], "feasibility": on["feasibility"]["status"],
               "active": {x["asset_id"]: round(x["weight"], 3) for x in on["correctives"] if x.get("shown")},
               "fills": [x["asset_id"] for x in on["correctives"] if x.get("shown") and x.get("fill")],
               "flags": sorted({f for x in on["correctives"] for f in x.get("flags", [])})}
        if prev is not None:
            rec["d_corrective_px"] = rec["corrective_px"] - prev["corrective_px"]
        recs.append(rec)
        prev = rec
    pops, med = pops_from_frames(recs)
    strip = Image.new("RGB", (len(frames) * 184 + 4, 210), "white")
    dr = ImageDraw.Draw(strip)
    dr.text((4, 2), f"{title} (correctives pending preview)", fill=(0, 0, 0))
    for i, f in enumerate(frames):
        strip.paste(f.resize((180, 180)), (4 + i * 184, 24))
    strip.save(out / f"sequence_{name}.png")
    progress(f"sequence {name}: gaps {[r['gaps'] for r in recs]} corr {[r['corrective_px'] for r in recs]} pops {pops}")
    return {"title": title, "frames": recs, "pops": pops, "median_abs_change_px": med,
            "popping": "FAIL" if pops else "PASS"}


def run_sequences(pkg, names: list[str] | None = None, progress: Callable[[str], Any] = print) -> dict[str, Any]:
    """Re-run some combined-pose sequences and merge them into the written reports."""
    from ..motion.pipeline import engine_for_package

    pkg = Path(pkg)
    out = pkg / "natural_pose" / "correctives"
    eng, vis = engine_for_package(pkg)
    eng.correctives = CorrectiveRuntime(eng, CorrectiveArtworkStore(pkg))
    res = {name: _sequence(eng, vis, name, title, seq, joint, out, progress)
           for name, title, seq, joint in SEQUENCES if names is None or name in names}
    for f in (pkg / "reports" / "corrective_quality.json", out / "results.json"):
        if f.is_file():
            d = json.loads(f.read_text())
            d["thresholds"] = {k: list(v) for k, v in THRESHOLDS.items()}
            seqs = d["results"].setdefault("sequences", {})
            for k in list(seqs):
                if k not in {n for n, *_ in SEQUENCES}:
                    seqs.pop(k)
            seqs.update(res)
            for v in seqs.values():
                v["pops"], v["median_abs_change_px"] = pops_from_frames(v["frames"])
                v["popping"] = "FAIL" if v["pops"] else "PASS"
            f.write_text(json.dumps(d, indent=1, default=_js))
    return res


def pops_from_frames(recs: list[dict[str, Any]]) -> tuple[list[int], float]:
    """Popping = a drawn corrective switching on or off abruptly: its activation weight changes by more than
    ``pop_weight_jump`` between adjacent frames (the sweeps step the driving joint by about 10 deg or less). Gap fills are
    excluded: they are opaque and gap-shaped, so their visible area follows the gap, not their weight (their area per
    frame is still recorded)."""
    pops = []
    for a, b in zip(recs, recs[1:]):
        wa, wb = a.get("active", {}), b.get("active", {})
        fills = set(a.get("fills", [])) | set(b.get("fills", []))
        jump = max([abs(wb.get(k, 0.0) - wa.get(k, 0.0)) for k in (set(wa) | set(wb)) - fills] or [0.0])
        if jump > THRESHOLDS["pop_weight_jump"][0]:
            pops.append(b["frame"])
    d = [abs(b["corrective_px"] - a["corrective_px"]) for a, b in zip(recs, recs[1:])]
    return pops, float(np.median(d)) if d else 0.0


def _asset_previews(store, assets, out: Path) -> None:
    tiles = []
    for a in assets:
        files = [a.file] if a.file else [k.get("file") for k in a.keys if k.get("file")]
        for f in files[:5]:
            im = Image.open(store.pkg / f).convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            ck = np.indices(im.size[::-1]).sum(0) // 8 % 2
            bg = Image.fromarray(np.where(ck[..., None] == 1, [220, 220, 220, 255], [255, 255, 255, 255]).astype(np.uint8))
            bg.alpha_composite(im)
            t = bg.convert("RGB")
            t.thumbnail((200, 200))
            tiles.append((f"{a.asset_id}@v{a.version}" + ("" if len(files) == 1 else f" {Path(f).stem.split('__')[-1]}"),
                          t))
    if not tiles:
        return
    W = 6 * 210
    rows = (len(tiles) + 5) // 6
    sheet = Image.new("RGB", (W, rows * 230), "white")
    d = ImageDraw.Draw(sheet)
    for i, (lab, t) in enumerate(tiles):
        x, y = (i % 6) * 210, (i // 6) * 230
        sheet.paste(t, (x + 5, y + 22))
        d.text((x + 4, y + 4), lab[:34], fill=(0, 0, 0))
    sheet.save(out / "asset_previews.png")


def _needs_artwork_sheet(renders, results, out: Path) -> None:
    from .evidence import figure

    keys = [k for k, q in results["poses"].items() if q["status"] == "NEEDS_ARTWORK"]
    if not keys:
        return
    cols = []
    for k in keys:
        f = figure(renders[k][1], 380)
        col = Image.new("RGB", (max(f.width, 260) + 10, 380 + 110), "white")
        d = ImageDraw.Draw(col)
        d.text((4, 4), f"{k}: NEEDS_ARTWORK", fill=(160, 0, 0))
        col.paste(f, (5, 20))
        for j, m in enumerate(results["poses"][k]["missing"][:5]):
            d.text((4, 404 + 16 * j), m[:44], fill=(60, 0, 0))
        cols.append(col)
    W = sum(c.width for c in cols)
    sheet = Image.new("RGB", (W, cols[0].height), "white")
    x = 0
    for c in cols:
        sheet.paste(c, (x, 0))
        x += c.width
    sheet.save(out / "needs_artwork.png")


def hardware() -> dict[str, Any]:
    cpu = ""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    return {"machine": platform.machine(), "system": platform.system(), "python": platform.python_version(),
            "cpu": cpu or platform.processor(), "cpu_count": os.cpu_count(),
            "note": "cloud sandbox measurements; no Apple M1 figures are claimed"}


def benchmark(eng, vis, quick: bool = False) -> dict[str, Any]:
    """B: interactive preview (correctives auto, no garment evaluation); C: full evaluation. Median of n runs."""
    n = 2 if quick else 3
    out = {"hardware": hardware()}
    for key, ev in (("B_interactive_preview_s", False), ("C_full_evaluation_s", True)):
        ts = []
        for _ in range(n):
            t0 = time.perf_counter()
            eng.pose("relaxed_standing", visible=vis, ik=fk_state({"r": (120.0, 5.0)}), evaluate_garments=ev,
                     corrective_mode="all")
            ts.append(time.perf_counter() - t0)
        out[key] = round(float(np.median(ts)), 3)
    return out


def live_notes(eng, vis) -> dict[str, Any]:
    """Step 19: determinism (two renders bit-identical), drivers, no cloud inference."""
    a = eng.pose("relaxed_standing", visible=vis, ik=fk_state({"r": (130.0, 20.0)}), evaluate_garments=False,
                 corrective_mode="all")
    b = eng.pose("relaxed_standing", visible=vis, ik=fk_state({"r": (130.0, 20.0)}), evaluate_garments=False,
                 corrective_mode="all")
    return {"deterministic_bit_identical": bool(np.array_equal(a["image"], b["image"])),
            "drivers": "skeletal pose readings only (shoulder abduction, elbow, knee, ankle, clavicle, torso bend); "
                       "independent of face tracking, IK target source, hand switches and gamepad inputs",
            "cloud_inference": "none (generators are deterministic geometric constructions; no AI model is called)",
            "flicker": "see sequences[*].popping (frame-to-frame corrective area)",
            "showtime": {"status": "proposed_unverified", "live_inputs": "unassigned (no Moho input names invented)",
                         "webcam_tracking": "not implemented"}}


def _js(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, Path):
        return str(o)
    return str(o)


__all__ = ["corrective_evidence", "CorrectiveQualityEvaluator", "THRESHOLDS", "POSES", "SEQUENCES", "light_specks",
           "benchmark", "hardware", "pose_space_doc", "live_notes"]
