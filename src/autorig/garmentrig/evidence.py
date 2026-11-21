"""Phase 3.3 evidence: before/after renders of the twelve garment test poses on the real character, close-ups,
shoulder and elbow sweeps, per-test deformation reports and performance (``autorig garment-evidence``).

"Before" is the Phase 3.1 band deformer (``garment_mode="legacy"``). "After" is the structured garment rig. Both
use the same package, approvals and hand state. The renders are evidence for a human reviewer; the evaluator's
grades never claim visual quality.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw

from ..phase2.pose_eval import on_bg

TESTS: list[tuple[str, str, str, dict[str, Any]]] = [
    ("01_neutral", "neutral standing", "relaxed_standing", {}),
    ("02_left_arm_45", "left arm raised 45 deg", "relaxed_standing", {"fk": {"l": (45.0, 8.0)}}),
    ("03_right_arm_90", "right arm raised 90 deg", "relaxed_standing", {"fk": {"r": (90.0, 5.0)}}),
    ("04_both_raised", "both arms raised", "both_hands_raised", {}),
    ("05_elbows_bent", "elbows bent 95 deg", "relaxed_standing", {"fk": {"r": (15.0, 95.0), "l": (15.0, 95.0)}}),
    ("06_pointing", "pointing", "pointing_speaking", {}),
    ("07_walking", "walking (phase 0.25)", "casual_walk", {"phase": 0.25}),
    ("08_running", "running (phase 0.25)", "running", {"phase": 0.25}),
    ("09_sitting", "sitting", "sitting", {}),
    ("10_squatting", "squatting", "natural_squat", {}),
    ("11_lean_forward", "leaning forward", "leaning_forward", {}),
    ("12_lean_backward", "leaning backward", "leaning_backward", {}),
]
REGIONS = [("shoulder_r", "upper_arm_r", 70), ("shoulder_l", "upper_arm_l", 70), ("elbow_r", "forearm_r", 60),
           ("elbow_l", "forearm_l", 60), ("collar", "neck", 55), ("lapels", "lapels", 85), ("hem", "hem", 95),
           ("hip_r", "thigh_r", 60), ("knee_r", "shin_r", 60), ("knee_l", "shin_l", 60), ("cuff_r", "foot_r", 55),
           ("cuff_l", "foot_l", 55)]
SHOULDER_SWEEP = [0.0, 30.0, 60.0, 90.0, 120.0]
ELBOW_SWEEP = [0.0, 45.0, 90.0, 135.0]


def fk_state(sides: dict[str, tuple[float, float]]) -> dict[str, Any]:
    from ..motion.rigctl import default_ik_state

    out = {}
    for s, (ab, fl) in sides.items():
        a = default_ik_state()[f"arm_{s}"]
        a.update(enabled=True, mode="fk", fk={"abduction": float(ab), "flexion": float(fl)})
        out[f"arm_{s}"] = a
    return out


def run_test(eng, vis, preset: str, kw: dict[str, Any], evaluate: bool = True) -> dict[str, Any]:
    args: dict[str, Any] = {"visible": vis}
    if "phase" in kw:
        args["phase"] = kw["phase"]
    if "fk" in kw:
        args["ik"] = fk_state(kw["fk"])
    if eng.garment_mode == "structured":
        args["evaluate_garments"] = evaluate
    t0 = time.perf_counter()
    r = eng.pose(preset, **args)
    r["wall_s"] = round(time.perf_counter() - t0, 3)
    return r


def _centre(r: dict[str, Any], key: str) -> tuple[float, float]:
    J, pad = r["solve"]["joints_posed"], r["pad"]
    if key == "lapels":
        return ((J["neck"][0] + J["spine"][0]) / 2 + pad, J["neck"][1] * 0.4 + J["spine"][1] * 0.6 + pad)
    if key == "hem":
        return (J["pelvis"][0] + pad, J["thigh_r"][1] + pad)
    return (J[key][0] + pad, J[key][1] + pad)


def closeup_sheet(before: dict[str, Any] | None, after: dict[str, Any], title: str, tile: int = 220) -> Image.Image:
    rows = [("before (Phase 3.1 bands)", before), ("after (Phase 3.3 garment rig)", after)]
    rows = [(t, r) for t, r in rows if r is not None]
    sheet = Image.new("RGB", (len(REGIONS) * (tile + 4) + 4, len(rows) * (tile + 22) + 30), "white")
    d = ImageDraw.Draw(sheet)
    d.text((6, 6), title, fill=(20, 20, 20))
    for ri, (lab, r) in enumerate(rows):
        im = on_bg(r["image"])
        y = 30 + ri * (tile + 22)
        d.text((6, y), lab, fill=(60, 60, 140))
        for ci, (nm, key, rad) in enumerate(REGIONS):
            cx, cy = _centre(r, key)
            c = im.crop((int(cx - rad), int(cy - rad), int(cx + rad), int(cy + rad))).resize((tile, tile), Image.LANCZOS)
            ImageDraw.Draw(c).text((3, 3), nm, fill=(200, 0, 0))
            sheet.paste(c, (4 + ci * (tile + 4), y + 16))
    return sheet


def figure(r: dict[str, Any], height: int) -> Image.Image:
    im = on_bg(r["image"])
    a = r["image"][..., 3] > 0
    ys, xs = np.nonzero(a)
    box = (max(0, xs.min() - 12), max(0, ys.min() - 12), xs.max() + 12, ys.max() + 12)
    c = im.crop(box)
    return c.resize((max(1, int(c.width * height / c.height)), height), Image.LANCZOS)


def contact_sheet(pairs: list[tuple[str, dict[str, Any] | None, dict[str, Any]]], height: int = 560) -> Image.Image:
    """One column per test: before on top, after below (high resolution)."""
    cols = []
    for title, b, a in pairs:
        fa = figure(a, height)
        fb = figure(b, height) if b is not None else Image.new("RGB", fa.size, "white")
        w = max(fa.width, fb.width) + 16
        col = Image.new("RGB", (w, 2 * height + 70), "white")
        d = ImageDraw.Draw(col)
        d.text((6, 4), title, fill=(10, 10, 10))
        col.paste(fb, (8, 22))
        col.paste(fa, (8, 46 + height))
        d.text((6, 26 + height), "after", fill=(40, 40, 140))
        cols.append(col)
    W = sum(c.width for c in cols)
    out = Image.new("RGB", (W, cols[0].height), "white")
    x = 0
    for c in cols:
        out.paste(c, (x, 0))
        x += c.width
    return out


def _report(r: dict[str, Any]) -> dict[str, Any]:
    ge = r.get("garment_eval") or {}
    tr = r.get("tears") or {}
    return {"preset": r["name"], "feasibility": r["feasibility"]["status"],
            "missing": [m["asset"] for m in r["feasibility"].get("missing", [])],
            "optional_assets": [m["asset"] for m in r["feasibility"].get("optional_assets", [])],
            "tears": {k: len(tr.get(k, [])) for k in ("exposed_gap", "visible_seam", "outline_break", "bridged")},
            "generated_visible_px": int(r["render"]["generated_visible"].sum()),
            "garments": {k: v["status"] for k, v in (ge.get("garments") or {}).items()},
            "parts": {k: {"status": v["status"], "class": v["class"],
                          "failed_checks": {c: x["value"] for c, x in v["checks"].items() if x["grade"] != "PASS"},
                          "missing_artwork": v["missing_artwork"]} for k, v in (ge.get("parts") or {}).items()},
            "attachments": ge.get("attachments", []), "cuffs": ge.get("cuffs", {}),
            "corrections_on": [{k: c.get(k) for k in ("id", "value_deg", "asset", "asset_state", "handling",
                                                         "blend_weight", "needs_artwork", "required")}
                               for c in r.get("correction_switches", []) if c.get("on")],
            "timing_ms": r.get("timing_ms"), "wall_s": r.get("wall_s"),
            "visual_review_required": True}


def garment_evidence(pkg, out: Path | None = None, progress: Callable[[str], Any] = print) -> dict[str, Any]:
    from ..motion.pipeline import engine_for_package

    pkg = Path(pkg)
    out = Path(out) if out else pkg / "natural_pose" / "garments"
    for sub in ("before", "after", "closeups", "sweeps", "reports"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    after_eng, vis = engine_for_package(pkg, garment_mode="structured")
    build_s = round(time.time() - t0, 2)
    t0 = time.time()
    before_eng, _ = engine_for_package(pkg, garment_mode="legacy")
    build_legacy_s = round(time.time() - t0, 2)
    progress(f"engines built ({build_s} s structured, {build_legacy_s} s legacy)")
    results: dict[str, Any] = {"tests": {}, "performance": {"build_structured_s": build_s,
                                                            "build_legacy_s": build_legacy_s}}
    pairs = []
    for tid, title, preset, kw in TESTS:
        b = run_test(before_eng, vis, preset, kw)
        a = run_test(after_eng, vis, preset, kw)
        on_bg(b["image"]).save(out / "before" / f"{tid}.png")
        on_bg(a["image"]).save(out / "after" / f"{tid}.png")
        closeup_sheet(b, a, f"{tid}: {title}").save(out / "closeups" / f"{tid}.jpg", quality=90)
        rep = _report(a)
        rep["before"] = {"feasibility": b["feasibility"]["status"],
                         "tears": {k: len(b["tears"].get(k, [])) for k in ("exposed_gap", "visible_seam",
                                                                            "outline_break")},
                         "generated_visible_px": int(b["render"]["generated_visible"].sum()), "wall_s": b["wall_s"]}
        rep["title"] = title
        (out / "reports" / f"{tid}.json").write_text(json.dumps(rep, indent=1, default=_json))
        results["tests"][tid] = {"title": title, "feasibility": rep["feasibility"], "garments": rep["garments"],
                                 "missing": sorted(set(rep["missing"] + [m for p in rep["parts"].values()
                                                                        for m in p["missing_artwork"]])),
                                 "tears_after": rep["tears"], "tears_before": rep["before"]["tears"],
                                 "generated_visible_after": rep["generated_visible_px"],
                                 "generated_visible_before": rep["before"]["generated_visible_px"],
                                 "render_ms": a["timing_ms"].get("render_and_tears"),
                                 "eval_ms": a["timing_ms"].get("garment_eval"), "wall_s": a["wall_s"],
                                 "wall_s_before": b["wall_s"]}
        pairs.append((f"{tid[:2]} {title}", b, a))
        progress(f"{tid}: {rep['feasibility']} {rep['garments']}")
    contact_sheet(pairs[:6]).save(out / "contact_sheet_1.jpg", quality=88)
    contact_sheet(pairs[6:]).save(out / "contact_sheet_2.jpg", quality=88)
    # sweeps (right arm; the left arm hangs)
    sweeps = {}
    for name, values, mk in (("shoulder", SHOULDER_SWEEP, lambda v: {"fk": {"r": (v, 5.0)}}),
                             ("elbow", ELBOW_SWEEP, lambda v: {"fk": {"r": (20.0, v)}})):
        tiles, rows = [], []
        for v in values:
            b = run_test(before_eng, vis, "relaxed_standing", mk(v), evaluate=False)
            a = run_test(after_eng, vis, "relaxed_standing", mk(v))
            key = "upper_arm_r" if name == "shoulder" else "forearm_r"
            tiles.append((v, b, a, key))
            ge = a.get("garment_eval") or {}
            rows.append({"angle_deg": v, "feasibility": a["feasibility"]["status"],
                         "sleeve_parts": {p: ge["parts"][p]["status"] for p in ge.get("parts", {})
                                          if p.endswith("_r") and p.split("_")[0] in ("sleeve", "elbow", "blazer",
                                                                                     "shirt")},
                         "gaps_after": len(a["tears"]["exposed_gap"]), "gaps_before": len(b["tears"]["exposed_gap"]),
                         "missing": ge.get("missing_artwork", [])})
        sheet = Image.new("RGB", (len(tiles) * 304 + 8, 2 * 300 + 60), "white")
        d = ImageDraw.Draw(sheet)
        d.text((6, 4), f"{name} sweep (right arm): before (top) / after (bottom)", fill=(10, 10, 10))
        for i, (v, b, a, key) in enumerate(tiles):
            for j, r in enumerate((b, a)):
                im = on_bg(r["image"])
                cx, cy = _centre(r, key)
                rad = 150 if name == "shoulder" else 120
                c = im.crop((int(cx - rad), int(cy - rad), int(cx + rad), int(cy + rad))).resize((300, 300), Image.LANCZOS)
                sheet.paste(c, (6 + i * 304, 24 + j * 318))
            d.text((10 + i * 304, 24 + 2 * 318 - 14), f"{v:.0f} deg", fill=(200, 0, 0))
        sheet.save(out / "sweeps" / f"{name}_sweep.jpg", quality=90)
        sweeps[name] = rows
        progress(f"{name} sweep done")
    results["sweeps"] = sweeps
    # diagnostics: garment parts, pivots, weights
    st = after_eng.structure
    ov = Image.fromarray(st.overlay([c for c in vis if c in st.layer_parts]))
    src = Image.fromarray(after_eng.source).convert("RGBA")
    comp = Image.alpha_composite(src, ov)
    d = ImageDraw.Draw(comp)
    for p in after_eng.pivot_report:
        if "pivot" in p:
            x, y = p["pivot"]
            col = (255, 0, 0, 255) if p.get("aligned") else (0, 200, 0, 255)
            d.ellipse([x - 5, y - 5, x + 5, y + 5], outline=col, width=2)
            if "rest_pivot" in p and p.get("aligned"):
                rx, ry = p["rest_pivot"]
                d.line([rx, ry, x, y], fill=(255, 0, 0, 255), width=2)
    for a in st.attachments:
        x, y = a["rest_point"]
        d.rectangle([x - 3, y - 3, x + 3, y + 3], outline=(0, 0, 255, 255))
    comp.convert("RGB").save(out / "structure_and_pivots.png")
    results["structure"] = {"parts": len(st.parts), "classes": {c: sum(1 for p in st.parts if p.cls == c)
                                                                for c in ("RIGID", "LIMITED_DEFORMATION", "FLEXIBLE")},
                            "weights": after_eng.weights.validate()}
    (out / "structure.json").write_text(json.dumps(st.to_json(), indent=1, default=_json))
    (out / "weights.json").write_text(json.dumps(after_eng.weights.to_json(), indent=1, default=_json))
    walls = [v["wall_s"] for v in results["tests"].values()]
    walls_b = [v["wall_s_before"] for v in results["tests"].values()]
    results["performance"].update({"pose_wall_s_mean": round(float(np.mean(walls)), 3),
                                   "pose_wall_s_max": round(float(np.max(walls)), 3),
                                   "legacy_pose_wall_s_mean": round(float(np.mean(walls_b)), 3),
                                   "eval_ms_mean": round(float(np.mean([v["eval_ms"] or 0 for v in
                                                                         results["tests"].values()])), 1),
                                   "machine": "cloud sandbox CPU (4 cores); not measured on the user's M1"})
    results["visual_review_required"] = True
    results["note"] = ("grades are measurements of the drawn mapping; visual quality is judged by a person on the "
                       "renders (before/, after/, closeups/, sweeps/, contact sheets)")
    (out / "results.json").write_text(json.dumps(results, indent=1, default=_json))
    return results


def _json(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, tuple):
        return list(o)
    return str(o)
