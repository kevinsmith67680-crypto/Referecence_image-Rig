"""Phase 4.2 native validation (42-A .. 42-K): check the evidence of a native hybrid-rig run.

As for Phases 4.0 and 4.1, a gate passes only on evidence from the real application: Moho-written reports, the saved
document and Moho renders. Mock reports and simulated renders give MOCK_ONLY; absent evidence gives NOT_EXECUTED.
Two gates are a person's: the Smart Bone test (MT-1) and the visual review (MT-4), recorded in observations.json.
Joint measurements on the renders (holes, cracks) are reported for the person; they never pass a pose on their own.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from ..geometry import world_to_px
from ..report import verify_report
from .fullbody import pose_world
from .gate import _background, _flatten_on, _iou, _load_render, check_reopened, check_saved, origin
from .hybrid_build import BUILD_REPORT, SAVED_NAME, VERIFY_REPORT, load_hybrid

VERDICT_PASS = "PHASE 4.2 NATIVE VALIDATION PASSED"
VERIFY_REPORT_SMARTBONE = "phase4_2_verify_report_smartbone.json"
VERDICT_BLOCKED = "BLOCKED — NATIVE VALIDATION REQUIRED"
NEUTRAL_MEAN_DIFF_MAX = 18.0   # mean per-channel difference on the expected figure (0-255), as in Phase 4.1
POSE_MEAN_DIFF_MAX = 24.0      # a pose render vs the simulated built layers (Moho filtering differs slightly)
RETURN_NEUTRAL_MAX = 6.0
FIGURE_DIFF = 40               # a render pixel differing this much from the background is figure
GATES = {
    "A": "the hybrid rig is built natively (bones, layers, bindings, keys read back by Moho)",
    "B": "Moho renders of the ten test frames exist",
    "C": "the neutral pose reproduces the character (frame 1 vs the source layers)",
    "D": "every pose matches the planned hybrid binding (attachment, proportions)",
    "E": "joint continuity measured on the native renders (holes, cracks): information for the person",
    "F": "save and reopen keep the rig (verify report from the reopened file + the saved document)",
    "G": "the reopened rig still works: the torso lean keyed after reopening renders",
    "H": "return to neutral (frame 90 equals frame 1)",
    "I": "Smart Bone corrective activation (manual test MT-1, the person's observation)",
    "J": "visual review of the articulation by a person (MT-4)",
    "K": "comparison with the Phase 4.1 native renders",
}


def _json(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _gate(key: str, status: str, detail: str, evidence: list[str] | None = None, **extra) -> dict[str, Any]:
    return {"id": f"4.2-{key}", "requirement": GATES[key], "status": status, "detail": detail,
            "evidence": evidence or [], **extra}


def _native(status: str, origins: list[str]) -> str:
    if status == "PASS" and any(o not in ("native", "moho") for o in origins):
        return "MOCK_ONLY"
    return status


class NativeRenders:
    def __init__(self, folder: Path | None, size: tuple[int, int]):
        self.folder, self.size, self._cache = folder, size, {}

    def path(self, frame: int) -> Path | None:
        if self.folder is None:
            return None
        p = self.folder / f"frame_{frame:04d}.png"
        return p if p.is_file() else None

    def get(self, frame: int) -> np.ndarray | None:
        if frame not in self._cache:
            p = self.path(frame)
            try:
                self._cache[frame] = _load_render(p, self.size) if p else None
            except (OSError, ValueError):
                self._cache[frame] = None
        return self._cache[frame]


def joint_metrics(img: np.ndarray, bg: np.ndarray, centre, half: int, r: int) -> dict[str, int]:
    """Holes a closing fills near a joint (radius ``r``) and thin cracks (radius 3), on a render."""
    fig = np.abs(_flatten_on(img, bg) - bg).max(axis=2) > FIGURE_DIFF
    x, y = int(round(centre[0])), int(round(centre[1]))
    sl = (slice(max(0, y - half), y + half), slice(max(0, x - half), x + half))
    f = fig[sl]
    disk = lambda k: (np.add.outer(np.arange(-k, k + 1) ** 2, np.arange(-k, k + 1) ** 2) <= k * k)  # noqa: E731
    return {"holes_px": int((ndimage.binary_closing(f, disk(r)) & ~f).sum()),
            "cracks_px": int((ndimage.binary_closing(f, disk(3)) & ~f).sum())}


def evaluate_gate42(package_out: str | Path, evidence_dir: str | Path | None = None, *,
                    character_package: str | Path | None = None, before_dir: str | Path | None = None,
                    renders_simulated: bool = False) -> dict[str, Any]:
    from .deform_audit import JOINTS, THRESHOLDS, _half_thickness
    from .skeleton import NativeSkeletonSpec

    pkg = Path(package_out)
    ev = Path(evidence_dir) if evidence_dir else None
    spec, raw, man = load_hybrid(pkg)
    plan = json.loads((pkg / "validation_plan.json").read_text(encoding="utf-8"))
    sk = NativeSkeletonSpec(**{**_skeleton_args(pkg)})
    rig = json.loads((Path(character_package) / "natural_pose/rig.json").read_text()) if character_package else {}

    def f(name):
        return (ev / name) if ev else None

    build, verify = _json(f(BUILD_REPORT)), _json(f(VERIFY_REPORT))
    obs = _json(f("observations.json")) or {}
    b_o, v_o = origin(build), origin(verify)
    r_o = "simulation" if renders_simulated else "moho"
    size = (spec.canvas_width, spec.canvas_height)
    rnd = NativeRenders(f("renders"), size)
    frames = [p["frame"] for p in plan["poses"]]
    gates = []

    # A: native build
    p0 = verify_report(spec, build) if build else None
    if build is None:
        gates.append(_gate("A", "NOT_EXECUTED", "no build report"))
    else:
        fails = [f"{c.name}: {c.detail}" for c in p0.checks if c.status == "FAIL"]
        ok = build.get("status") == "completed" and not fails
        gates.append(_gate("A", _native("PASS" if ok else "FAIL", [b_o]),
                           f"origin {b_o}; {len(spec.bones)} bones, {len(spec.parts)} layers; "
                           + ("bones, layers, bindings and keys match" if ok else f"failures: {fails[:6]}"),
                           [BUILD_REPORT]))

    # B: renders exist
    have = [fr for fr in frames if rnd.get(fr) is not None]
    b_st = "PASS" if len(have) == len(frames) else ("NOT_EXECUTED" if not have else "FAIL")
    gates.append(_gate("B", _native(b_st, [r_o]), f"{len(have)} of {len(frames)} frames rendered: {have}",
                       [f"renders/frame_{fr:04d}.png" for fr in have]))

    r1 = rnd.get(1)
    bg = _background(r1) if r1 is not None else np.array([255.0, 255.0, 255.0])

    def expected(frame: int) -> np.ndarray:
        return np.asarray(Image.open(pkg / man["expected"][str(frame)]).convert("RGBA")).astype(float)

    def diff_on_figure(frame: int) -> dict[str, Any]:
        r = rnd.get(frame)
        if r is None:
            return {"frame": frame, "status": "NOT_EXECUTED"}
        e = expected(frame)
        fe = e[..., 3] > 127
        fr = np.abs(_flatten_on(r, bg) - bg).max(axis=2) > FIGURE_DIFF
        d = float(np.abs(_flatten_on(r, bg) - _flatten_on(e, bg))[fe].mean()) if fe.any() else 255.0
        return {"frame": frame, "mean_abs_diff_on_figure": round(d, 2), "silhouette_iou": round(_iou(fr, fe), 4)}

    # C: neutral
    c = diff_on_figure(1)
    if "mean_abs_diff_on_figure" not in c:
        gates.append(_gate("C", "NOT_EXECUTED", "no render of frame 1"))
    else:
        gates.append(_gate("C", _native("PASS" if c["mean_abs_diff_on_figure"] <= NEUTRAL_MEAN_DIFF_MAX else "FAIL",
                                        [r_o]), f"{c}", ["renders/frame_0001.png"]))

    # D: poses vs the planned hybrid binding
    rows = [diff_on_figure(fr) for fr in frames if fr not in (1,)]
    if not any("mean_abs_diff_on_figure" in r for r in rows):
        gates.append(_gate("D", "NOT_EXECUTED", "no pose renders"))
    else:
        bad = [r for r in rows if r.get("mean_abs_diff_on_figure", 1e9) > POSE_MEAN_DIFF_MAX]
        st = "NOT_EXECUTED" if any("mean_abs_diff_on_figure" not in r for r in rows) else ("FAIL" if bad else "PASS")
        gates.append(_gate("D", _native(st, [b_o, r_o]), f"per pose: {rows}", poses=rows,
                           threshold={"mean_abs_diff_on_figure_max": POSE_MEAN_DIFF_MAX}))

    # E: joint continuity on the renders (information)
    meas = []
    if rnd.get(1) is not None:
        by = {p["frame"]: p for p in plan["poses"]}
        for joint, (bone, _, jframes) in JOINTS.items():
            ht = _half_thickness(sk, rig, bone) if rig else 50.0
            half = int(max(60, THRESHOLDS["window_frac"] * ht))
            r = int(min(22, max(3, THRESHOLDS["closing_frac"] * ht)))
            for fr in jframes:
                img = rnd.get(fr)
                if img is None:
                    continue
                w = pose_world(spec, sk, by[fr]["offsets_deg"], None)
                cpx = world_to_px(w[bone][0], *size)
                rest = joint_metrics(r1, bg, world_to_px(pose_world(spec, sk, {}, None)[bone][0], *size), half, r)
                m = joint_metrics(img, bg, cpx, half, r)
                meas.append({"joint": joint, "frame": fr, "holes_px": max(0, m["holes_px"] - rest["holes_px"]),
                             "cracks_px": max(0, m["cracks_px"] - rest["cracks_px"])})
    gates.append(_gate("E", "MEASURED" if meas else "NOT_EXECUTED",
                       f"joint holes and cracks on the {r_o} renders (minus rest); for the person's review, never a "
                       "pass by itself" if meas else "no renders", measurements=meas, render_origin=r_o))

    # F: save / reopen
    saved = check_saved(spec, f(SAVED_NAME), raw)
    struct = [x for x in saved if x["status"] in ("PASS", "FAIL")]
    saved_ok = len(struct) > 3 and all(x["status"] == "PASS" for x in struct)
    if verify is None:
        gates.append(_gate("F", "NOT_EXECUTED", "no verify report from the reopened document",
                           saved_checks=saved))
    else:
        re = check_reopened(spec, raw, verify)
        re_fail = [x for x in re if x["status"] == "FAIL"]
        st = "PASS" if not re_fail and saved_ok else ("NOT_EXECUTED" if not struct else "FAIL")
        gates.append(_gate("F", _native(st, [v_o]), f"reopened read-back: {len(re) - len(re_fail)}/{len(re)} ok; "
                                                    f"saved file: {'ok' if saved_ok else [x['name'] for x in saved if x['status'] != 'PASS'][:5]}",
                           [VERIFY_REPORT, SAVED_NAME], reopened_checks=re, saved_checks=saved))

    # G: lean keyed after reopening
    moves = (verify or {}).get("moves") or []
    keyed_ok = bool(moves) and all(abs(k["set"] - k["read"]) < 1e-6 for m in moves for k in m["keys"]
                                   if isinstance(k.get("set"), (int, float)))
    lean_frame = next((p["frame"] for p in plan["poses"] if p.get("after_reopen")), None)
    lean = diff_on_figure(lean_frame) if lean_frame else {}
    if verify is None or "mean_abs_diff_on_figure" not in lean:
        gates.append(_gate("G", "NOT_EXECUTED", "no verify report or no render of the lean frame"))
    else:
        st = "PASS" if keyed_ok and lean["mean_abs_diff_on_figure"] <= POSE_MEAN_DIFF_MAX else "FAIL"
        gates.append(_gate("G", _native(st, [v_o, r_o]), f"lean keys read back: {keyed_ok}; frame {lean_frame}: {lean}"))

    # H: return to neutral
    r90 = rnd.get(frames[-1])
    if r1 is None or r90 is None:
        gates.append(_gate("H", "NOT_EXECUTED", "frame 1 or the last frame is missing"))
    else:
        d = float(np.abs(_flatten_on(r90, bg) - _flatten_on(r1, bg)).mean())
        gates.append(_gate("H", _native("PASS" if d <= RETURN_NEUTRAL_MAX else "FAIL", [r_o]),
                           f"frame {frames[-1]} vs 1 mean diff {d:.2f} (max {RETURN_NEUTRAL_MAX})"))

    # I: Smart Bone activation (the person's test)
    sb_plan = _json(pkg / "smartbone_plan.json") or {}
    drivers = sorted({a["driver_bone"] for a in sb_plan.get("assets", []) if a.get("driver_bone")})
    sb_verify = _json(f(VERIFY_REPORT_SMARTBONE))
    acts = (sb_verify or verify or {}).get("actions")
    smart = sorted(a["name"] for a in acts if isinstance(acts, list) and a.get("smart_bone") is True) \
        if isinstance(acts, list) else []
    mt1 = ((obs.get("smart_bone") or {}).get("MT-1")) or {}
    if "works" not in mt1 and obs.get("smart_bone_mt1_works") in ("yes", "no"):  # the harness's flat record
        mt1 = {"works": obs["smart_bone_mt1_works"] == "yes", "source": "harness prompt"}
    if not smart:
        gates.append(_gate("I", "NOT_EXECUTED", f"no Smart Bone actions in the reopened document (planned drivers: "
                                                f"{drivers}); run manual test MT-1", planned_drivers=drivers))
    elif "works" not in mt1:
        gates.append(_gate("I", "NOT_EXECUTED", f"Smart Bone actions found {smart}; the person's MT-1 observation is "
                                                "missing", planned_drivers=drivers))
    else:
        gates.append(_gate("I", _native("PASS" if mt1["works"] is True else "FAIL", [origin(sb_verify or verify)]),
                           f"actions {smart}; MT-1: {mt1}", planned_drivers=drivers))

    # J: the person's visual review
    vr = obs.get("visual_review") or {}
    if not vr and obs.get("visual_review_verdict") in ("accepted", "rejected"):
        vr = {"verdict": obs["visual_review_verdict"], "notes": obs.get("visual_review_notes", "")}
    if vr.get("verdict") not in ("accepted", "rejected"):
        gates.append(_gate("J", "NOT_EXECUTED", "no visual review recorded (observations.json visual_review)"))
    else:
        gates.append(_gate("J", _native("PASS" if vr["verdict"] == "accepted" else "FAIL", [r_o]), f"{vr}"))

    # K: comparison with Phase 4.1
    bdir = Path(before_dir) / "renders" if before_dir else None
    if bdir is None or not bdir.is_dir():
        gates.append(_gate("K", "NOT_EXECUTED", "no Phase 4.1 native renders given (--before <4.1 run folder>)"))
    else:
        gates.append(_gate("K", "MEASURED" if rnd.get(1) is not None else "NOT_EXECUTED",
                           f"Phase 4.1 renders in {bdir.name}; side-by-side sheets via comparison_sheets"))

    verdict_gates = [g for g in gates if g["id"][-1] in "ABCDFGHIJ"]
    sts = [g["status"] for g in verdict_gates]
    if all(s == "PASS" for s in sts):
        verdict = VERDICT_PASS
    elif "FAIL" in sts:
        verdict = "FAIL"
    else:
        verdict = VERDICT_BLOCKED
    origins = {"build": b_o, "verify": v_o, "renders": r_o if rnd.get(1) is not None else "missing"}
    return {"schema": "autorig.gate_4_2", "package": {"character_id": man["character_id"], "variant": man["variant"]},
            "gates": gates, "verdict": verdict, "evidence_origin": origins,
            "note": "PASS needs native reports, the saved file, Moho renders and the person's MT-1 and MT-4 records"}


def _skeleton_args(pkg: Path) -> dict[str, Any]:
    from .skeleton import NativeBone

    d = json.loads((pkg / "native_skeleton.json").read_text(encoding="utf-8"))
    bones = [NativeBone(id=b["id"], parent=b.get("parent"), base_px=tuple(b["base_px"]), tip_px=tuple(b["tip_px"]),
                        source=b.get("source", ""), limits_deg=tuple(b["limits_deg"]) if b.get("limits_deg") else None)
             for b in d["bones"]]
    return {"character_id": d["character_id"], "source_canvas": tuple(d["source_canvas"]), "bones": bones,
            "midline_x": d["midline_x"], "ground_y": d["ground_y"], "provenance": d.get("provenance", {}),
            "config": d.get("config", {})}


def format_gate42(res: dict[str, Any]) -> str:
    lines = [f"PHASE 4.2 NATIVE VALIDATION ({res['package']['character_id']}, {res['package']['variant']})"]
    for g in res["gates"]:
        lines.append(f"  [{g['status']:<12}] {g['id']} {g['requirement']}")
        lines.append(f"                 {g['detail'][:240]}")
    lines.append(f"Evidence origin: {res['evidence_origin']}")
    lines.append(f"VERDICT: {res['verdict']}")
    return "\n".join(lines)


def comparison_sheets(package_out: str | Path, evidence_dir: str | Path, out: str | Path,
                      before_dir: str | Path | None = None) -> list[str]:
    """Per pose and joint: [Phase 4.1 Moho render |] Phase 4.2 Moho render | Phase 4.2 simulation. Only from renders
    that exist; Moho pixels are copied, never edited (crops and labels only)."""
    pkg, ev, out = Path(package_out), Path(evidence_dir), Path(out)
    spec, raw, man = load_hybrid(pkg)
    plan = json.loads((pkg / "validation_plan.json").read_text(encoding="utf-8"))
    size = (spec.canvas_width, spec.canvas_height)
    now = NativeRenders(ev / "renders", size)
    before = NativeRenders(Path(before_dir) / "renders", None) if before_dir else None
    out.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default(size=max(16, spec.canvas_height // 50))
    r1 = now.get(1)
    if r1 is None:
        return []
    bg = _background(r1)
    written = []

    def panel(arr):
        return Image.fromarray(_flatten_on(arr, bg).clip(0, 255).astype(np.uint8))

    for p in plan["poses"]:
        r = now.get(p["frame"])
        if r is None:
            continue
        exp = np.asarray(Image.open(pkg / man["expected"][str(p["frame"])]).convert("RGBA")).astype(float)
        cols = [("PHASE 4.2 MOHO RENDER", panel(r), (0, 90, 0)), ("Phase 4.2 SIMULATION", panel(exp), (200, 0, 0))]
        if before is not None:
            bp = before.path(p["frame"])
            if bp is not None:
                cols.insert(0, ("PHASE 4.1 MOHO RENDER", Image.open(bp).convert("RGB"), (0, 0, 160)))
        sheet = Image.new("RGB", (sum(c[1].width for c in cols), max(c[1].height for c in cols)), (255, 255, 255))
        x = 0
        d = ImageDraw.Draw(sheet)
        for lab, im, col in cols:
            sheet.paste(im, (x, 0))
            d.text((x + 10, 10), f"{lab}\nframe {p['frame']}: {p['label']}", fill=col, font=font)
            x += im.width
        path = out / f"compare_f{p['frame']:03d}_{p['id']}.png"
        sheet.save(path, optimize=True)
        written.append(path.name)
    for key, c in plan["closeups"].items():
        r = now.get(c["frame"])
        if r is None:
            continue
        x0, y0, x1, y1 = c["box"]
        exp = np.asarray(Image.open(pkg / man["expected"][str(c["frame"])]).convert("RGBA")).astype(float)
        crops = [panel(r).crop((x0, y0, x1, y1)), panel(exp).crop((x0, y0, x1, y1))]
        sheet = Image.new("RGB", (sum(i.width for i in crops), y1 - y0), (255, 255, 255))
        for i, im in enumerate(crops):
            sheet.paste(im, (i * (x1 - x0), 0))
        ImageDraw.Draw(sheet).text((6, 6), f"MOHO | SIMULATION  {key}", fill=(0, 90, 0), font=font)
        path = out / f"closeup_{key}.png"
        sheet.save(path, optimize=True)
        written.append(path.name)
    return written
