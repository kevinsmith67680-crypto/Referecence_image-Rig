"""Phase 4.1 hard gates (4.1-A .. 4.1-L): check the evidence of a native full-body run.

A gate passes only on evidence from the real application: Moho-written reports, the saved document and
Moho renders. Reports from the strict mock and simulated renders give MOCK_ONLY. A gate whose evidence is
absent is NOT_EXECUTED; a gate that depends on an operation this phase does not perform yet is BLOCKED.
Nothing here is a substitute for the Mac run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..report import verify_report
from .fullbody import (BUILD_REPORT, IK_REPORT, MANIFEST, SAVED_IK_NAME, SAVED_NAME, VERIFY_REPORT, load_fullbody)
from .gate import (_background, _flatten_on, _iou, _load_render, check_reopened, check_saved, origin)

VERDICT_PASS = "GATES 4.1 PASSED"
VERDICT_BLOCKED = "BLOCKED — NATIVE VALIDATION REQUIRED"
MOVE_DIFF = 48
MOVE_IOU_MIN = 0.60
MOVE_RATIO_MIN = 0.5
ALIGN_IOU_MIN = 0.85
NEUTRAL_MEAN_DIFF_MAX = 18.0      # mean per-channel difference on the expected figure pixels (0-255)
RETURN_NEUTRAL_MAX = 6.0          # mean difference between the frame-110 and frame-1 renders
IK_IOU_MIN = 0.45                 # Moho's IK solution may bend differently from the analytic simulation

GATES = {
    "A": "The complete native skeleton is created",
    "B": "All required artwork layers import at the correct scale",
    "C": "Bone bindings move the correct artwork",
    "D": "Left and right arm controls function",
    "E": "Left and right leg controls function",
    "F": "Head, torso and pelvis controls function",
    "G": "Native joint constraints behave correctly",
    "H": "The document saves and reopens",
    "I": "The rig remains editable after reopening",
    "J": "Actual Moho renders demonstrate the movement",
    "K": "Original artwork remains visually faithful in the neutral pose",
    "L": "The implementation uses character-specific data",
}
ARM_POSES, LEG_POSES, BODY_POSES = (10, 20, 30, 40, 50), (60, 70), (80, 90, 100)


def _json(path: Path | None) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else None


def _gate(key: str, status: str, detail: str, evidence: list[str] | None = None) -> dict[str, Any]:
    return {"id": f"4.1-{key}", "requirement": GATES[key], "status": status, "detail": detail,
            "evidence": evidence or []}


class Renders:
    """Moho renders of the pose frames, compared with the simulated expected frames."""

    def __init__(self, pkg: Path, man: dict[str, Any], spec, folder: Path | None, simulated: bool = False):
        self.pkg, self.man, self.spec, self.folder, self.simulated = pkg, man, spec, folder, simulated
        self.size = (spec.canvas_width, spec.canvas_height)
        self._cache: dict[int, np.ndarray | None] = {}
        self.bg = None

    def path(self, frame: int) -> Path | None:
        if self.folder is None:
            return None
        p = self.folder / f"frame_{frame:04d}.png"
        return p if p.is_file() else None

    def render(self, frame: int) -> np.ndarray | None:
        if frame not in self._cache:
            p = self.path(frame)
            try:
                self._cache[frame] = _load_render(p, self.size) if p else None
            except (OSError, ValueError):
                self._cache[frame] = None
        return self._cache[frame]

    def expected(self, key: str) -> np.ndarray:
        return np.asarray(Image.open(self.pkg / self.man["expected"][key]).convert("RGBA")).astype(float)

    def background(self) -> np.ndarray:
        if self.bg is None:
            r = self.render(1)
            self.bg = _background(r) if r is not None else np.array([255.0, 255.0, 255.0])
        return self.bg

    def change(self, frame: int, ref: int = 1, key: str | None = None, iou_min: float = MOVE_IOU_MIN) -> dict[str, Any]:
        r0, r1 = self.render(ref), self.render(frame)
        if r0 is None or r1 is None:
            return {"frame": frame, "status": "NOT_EXECUTED", "detail": "render missing"}
        bg = self.background()
        e0, e1 = self.expected(str(ref)), self.expected(key or str(frame))
        d_r = np.abs(_flatten_on(r1, bg) - _flatten_on(r0, bg)).max(axis=2) > MOVE_DIFF
        d_e = np.abs(_flatten_on(e1, bg) - _flatten_on(e0, bg)).max(axis=2) > MOVE_DIFF
        iou = _iou(d_r, d_e)
        ratio = float(d_r.sum()) / max(1.0, float(d_e.sum()))
        ok = iou >= iou_min and ratio >= MOVE_RATIO_MIN
        return {"frame": frame, "status": "PASS" if ok else "FAIL", "changed_iou": round(iou, 4),
                "changed_ratio": round(ratio, 4), "changed_px": int(d_r.sum()), "expected_changed_px": int(d_e.sum())}

    def neutral(self) -> dict[str, Any]:
        r = self.render(1)
        if r is None:
            return {"status": "NOT_EXECUTED", "detail": "no render of frame 1"}
        bg = self.background()
        e = self.expected("1")
        fr = np.abs(_flatten_on(r, bg) - bg).max(axis=2) > 40
        fe = e[..., 3] > 127
        iou = _iou(fr, fe)
        diff = float(np.abs(_flatten_on(r, bg) - _flatten_on(e, bg))[fe].mean()) if fe.any() else 255.0
        back = self.render(110)
        ret = (float(np.abs(_flatten_on(back, bg) - _flatten_on(r, bg)).mean()) if back is not None else None)
        # The per-pixel difference on the expected figure is the verdict: it catches a shifted or scaled image as
        # well as changed colours. The silhouette IoU is reported only: near-white artwork (shirts, shoes) on a
        # light background drops out of a threshold mask even when the image is identical.
        ok = diff <= NEUTRAL_MEAN_DIFF_MAX and (ret is None or ret <= RETURN_NEUTRAL_MAX)
        return {"status": "PASS" if ok else "FAIL", "silhouette_iou": round(iou, 4),
                "mean_abs_diff_on_figure": round(diff, 2), "return_to_neutral_mean_diff": ret,
                "thresholds": {"mean_abs_diff_on_figure_max": NEUTRAL_MEAN_DIFF_MAX,
                               "return_to_neutral_max": RETURN_NEUTRAL_MAX}}


def _status_of(results: list[dict[str, Any]]) -> str:
    sts = [r["status"] for r in results]
    if not sts or "NOT_EXECUTED" in sts:
        return "NOT_EXECUTED"
    return "PASS" if all(s == "PASS" for s in sts) else "FAIL"


def _native(status: str, origins: list[str]) -> str:
    """A PASS stands only when every piece of evidence came from Moho ("native" reports, "moho" renders)."""
    if status == "PASS" and any(o not in ("native", "moho") for o in origins):
        return "MOCK_ONLY"
    return status


def evaluate_gate41(package_out: str | Path, evidence_dir: str | Path | None = None, *,
                    character_package: str | Path | None = None, renders_simulated: bool = False) -> dict[str, Any]:
    pkg = Path(package_out)
    ev = Path(evidence_dir) if evidence_dir else None
    spec, raw, man, _ = load_fullbody(pkg)

    def f(name):
        return (ev / name) if ev else None

    build, verify, ikrep = _json(f(BUILD_REPORT)), _json(f(VERIFY_REPORT)), _json(f(IK_REPORT))
    obs = _json(f("observations.json")) or {}
    env = _json(f("environment.json"))
    b_o, v_o, i_o = origin(build), origin(verify), origin(ikrep)
    r_o = "simulation" if renders_simulated else "moho"
    rnd = Renders(pkg, man, spec, f("renders"), renders_simulated)
    rik = Renders(pkg, man, spec, f("renders_ik"), renders_simulated)
    rik._cache[1] = rnd.render(1)
    rik.bg = None
    gates = []

    # A: skeleton
    p0 = verify_report(spec, build) if build else None
    bone_checks = [c for c in (p0.checks if p0 else []) if c.name.startswith(("bone[", "bones.", "bone_layer"))]
    if build is None:
        gates.append(_gate("A", "NOT_EXECUTED", "no build report"))
    else:
        ok = build.get("status") == "completed" and bone_checks and all(c.status != "FAIL" for c in bone_checks)
        fails = [f"{c.name}: {c.detail}" for c in bone_checks if c.status == "FAIL"]
        gates.append(_gate("A", _native("PASS" if ok else "FAIL", [b_o]),
                           f"origin {b_o}; {len(spec.bones)} bones; " + ("rest values, parents, world matrices and keys "
                                                                         "match" if ok else f"failures: {fails[:6]}"),
                           [BUILD_REPORT]))

    # saved file (used by B, H, I)
    saved = check_saved(spec, f(SAVED_NAME), raw)
    saved_struct = [c for c in saved if c["status"] in ("PASS", "FAIL")]
    saved_ok = len(saved_struct) > 3 and all(c["status"] == "PASS" for c in saved_struct)
    fits = [c for c in saved if c["name"].endswith("frame_fit")]
    fits_ok = bool(fits) and len(fits) == len(spec.parts) and all("fills" in c["detail"] for c in fits)

    # B: artwork scale
    layer_checks = [c for c in (p0.checks if p0 else []) if c.name.startswith(("layer[", "layers."))]
    align = rnd.neutral()
    if build is None or not fits:
        gates.append(_gate("B", "NOT_EXECUTED", "no build report or saved document"))
    else:
        ok_layers = all(c.status != "FAIL" for c in layer_checks)
        if not ok_layers or not fits_ok:
            gates.append(_gate("B", "FAIL", f"layers {'ok' if ok_layers else 'FAIL'}, frame fit "
                                            f"{[c['detail'] for c in fits if 'fills' not in c['detail']][:3]}"))
        elif align["status"] == "NOT_EXECUTED":
            gates.append(_gate("B", "NOT_EXECUTED", f"{len(fits)} layers fill the frame in the saved file; the neutral "
                                                    "render is missing"))
        else:
            st = "PASS" if (align.get("silhouette_iou", 0) >= ALIGN_IOU_MIN or
                            align.get("mean_abs_diff_on_figure", 255) <= NEUTRAL_MEAN_DIFF_MAX) else "FAIL"
            gates.append(_gate("B", _native(st, [b_o, r_o]), f"{len(fits)} layers fill the frame; neutral silhouette "
                                                             f"IoU {align.get('silhouette_iou')}", [SAVED_NAME, "renders/frame_0001.png"]))

    # C, J: bindings move the right artwork; renders show the movement
    pose_frames = [p["frame"] for p in json.loads((pkg / "validation_plan.json").read_text())["poses"]]
    moves = {fr: rnd.change(fr) for fr in pose_frames if fr not in (1, 110)}
    bind_ok = all(c.status != "FAIL" for c in layer_checks if c.name.endswith(".binding"))
    c_status = _status_of(list(moves.values()))
    if build is None:
        gates.append(_gate("C", "NOT_EXECUTED", "no build report"))
    elif not bind_ok:
        gates.append(_gate("C", "FAIL", "a layer is bound to the wrong bone in Moho's read-back"))
    else:
        gates.append(_gate("C", _native(c_status, [b_o, r_o]), f"bindings read back correctly; per-pose change "
                                                                f"checks: { {k: v['status'] for k, v in moves.items()} }"))
    neutral_back = align.get("return_to_neutral_mean_diff")
    j_status = c_status if c_status != "PASS" or (neutral_back is not None and neutral_back <= RETURN_NEUTRAL_MAX) else "FAIL"
    gates.append(_gate("J", _native(j_status, [r_o]), f"{sum(1 for fr in pose_frames if rnd.path(fr))} of "
                                                      f"{len(pose_frames)} pose renders; frame 110 vs 1 mean diff "
                                                      f"{neutral_back}"))

    # D, E: arms and legs (FK renders + IK targets)
    def limb(key, frames, ik_frames, label):
        fk = _status_of([moves[fr] for fr in frames])
        ik = [rik.change(fr, 1, str(fr), IK_IOU_MIN) for fr in ik_frames]
        ik_ops = [o for o in (ikrep or {}).get("operations", []) if o["name"].startswith("target:")]
        drag = obs.get(f"ik_drag_{label}")
        if ikrep is None:
            st, d = ("NOT_EXECUTED" if fk != "FAIL" else "FAIL"), f"FK renders {fk}; IK targets not executed"
        else:
            ik_st = _status_of(ik)
            ops_ok = bool(ik_ops) and all(o["ok"] for o in ik_ops)
            st = "PASS" if fk == "PASS" and ops_ok and ik_st == "PASS" and drag == "yes" else (
                "NOT_EXECUTED" if "NOT_EXECUTED" in (fk, ik_st) or drag is None else "FAIL")
            d = f"FK {fk}; target links {'accepted' if ops_ok else 'REJECTED'}; IK renders {ik_st}; drag test {drag}"
        return _gate(key, _native(st, [b_o, r_o] + ([i_o] if ikrep else [])), d)
    gates.append(limb("D", ARM_POSES, (120, 130), "arms"))
    gates.append(limb("E", LEG_POSES, (140, 150), "legs"))

    # F: head, torso, pelvis
    gates.append(_gate("F", _native(_status_of([moves[fr] for fr in BODY_POSES]), [b_o, v_o, r_o]),
                       f"pelvis (80, keyed after reopening), torso (90), head (100): "
                       f"{ {fr: moves[fr]['status'] for fr in BODY_POSES} }"))

    # G: constraints
    if ikrep is None:
        gates.append(_gate("G", "NOT_EXECUTED", "the opt-in IK/constraints script did not run"))
    else:
        cons = [o for o in ikrep.get("operations", []) if o["name"].startswith("constraint:")]
        ops_ok = bool(cons) and all(o["ok"] for o in cons)
        clamped = rik.render(160)
        verdict = None
        if clamped is not None:
            bg = rik.background()
            r = _flatten_on(clamped, bg)
            dc = float(np.abs(r - _flatten_on(rik.expected("160_clamped"), bg)).mean())
            du = float(np.abs(r - _flatten_on(rik.expected("160_unclamped"), bg)).mean())
            verdict = "clamped" if dc < du else "not_clamped"
        drag = obs.get("constraint_drag_stops_at_limit")
        st = ("PASS" if ops_ok and (verdict == "clamped" or drag == "yes") else
              "NOT_EXECUTED" if verdict is None and drag is None else "FAIL")
        gates.append(_gate("G", _native(st, [i_o, r_o]), f"constraint writes {'accepted' if ops_ok else 'REJECTED'}; "
                                                         f"keyed rotation past the limit renders {verdict}; drag test {drag}"))

    # H: save and reopen
    reopen = check_reopened(spec, raw, verify) if verify else []
    reopen_ok = bool(reopen) and all(c["status"] == "PASS" for c in reopen)
    if verify is None or saved[0]["status"] != "PASS":
        gates.append(_gate("H", "NOT_EXECUTED", "no verify report from the reopened document, or no saved document"))
    else:
        st = "PASS" if (saved_ok and reopen_ok) else "FAIL"
        gates.append(_gate("H", _native(st, [b_o, v_o]), f"saved file structure {'ok' if saved_ok else 'FAIL'}; "
                                                         f"reopened read-back {'ok' if reopen_ok else 'FAIL'}"))

    # I: editable after reopening
    if verify is None:
        gates.append(_gate("I", "NOT_EXECUTED", "no verify report"))
    else:
        mv = verify.get("moves") or []
        keys_ok = bool(mv) and all(abs(k["set"][0] - k["read"][0]) < 1e-4 and abs(k["set"][1] - k["read"][1]) < 1e-4
                                   for m in mv for k in m["keys"])
        st = "PASS" if keys_ok and moves[80]["status"] == "PASS" else (
            "NOT_EXECUTED" if moves[80]["status"] == "NOT_EXECUTED" and keys_ok else "FAIL")
        gates.append(_gate("I", _native(st, [v_o, r_o]), f"pelvis keys read back {'ok' if keys_ok else 'FAIL'}; "
                                                         f"render of frame 80 {moves[80]['status']}"))

    # K: neutral fidelity
    gates.append(_gate("K", _native(align["status"], [b_o, r_o]), json.dumps(align)))

    # L: character-specific data
    if character_package is None:
        gates.append(_gate("L", "NOT_EXECUTED", "pass --character-package to re-derive the skeleton"))
    else:
        from .skeleton import NativeSkeletonSpec
        sk = NativeSkeletonSpec.from_package(character_package, man["character_id"])
        stored = json.loads((pkg / "native_skeleton.json").read_text())
        same = [b["id"] for b in stored["bones"]] == sk.ids() and all(
            tuple(b["base_px"]) == sk.bone(b["id"]).base_px and tuple(b["tip_px"]) == sk.bone(b["id"]).tip_px
            for b in stored["bones"])
        built_here = build is not None and (build.get("generator") or {}).get("spec_sha256") == spec.canonical_sha256()
        st = "PASS" if same and built_here else ("NOT_EXECUTED" if build is None else "FAIL")
        gates.append(_gate("L", _native(st, [b_o]), f"skeleton re-derived from the package {'matches' if same else 'DIFFERS'}; "
                                                    f"Moho built this character's spec: {built_here}"))

    order = {g: i for i, g in enumerate(GATES)}
    gates.sort(key=lambda g: order[g["id"][-1]])
    passed = all(g["status"] == "PASS" for g in gates)
    return {"gate": "4.1", "verdict": VERDICT_PASS if passed else VERDICT_BLOCKED, "passed": passed,
            "gates": gates, "evidence_origin": {"build": b_o, "verify": v_o, "ik": i_o, "renders": r_o},
            "checks": {"pose_changes": moves, "neutral": align, "saved": saved, "reopened": reopen},
            "environment": env, "observations": obs,
            "api_surface": (verify or {}).get("api_surface"), "timings": (verify or {}).get("timings_s"),
            "package": {"dir": str(pkg), "character_id": man["character_id"], "spec_sha256": man["spec_sha256"]}}


def format_gate41(res: dict[str, Any]) -> str:
    lines = [f"PHASE 4.1 GATES ({res['package']['character_id']})"]
    for g in res["gates"]:
        lines.append(f"  [{g['status']:<12}] {g['id']} {g['requirement']}")
        lines.append(f"                 {g['detail'][:240]}")
    lines.append(f"Evidence origin: {res['evidence_origin']}")
    lines.append(f"VERDICT: {res['verdict']}")
    return "\n".join(lines)


def comparison_sheets(package_out: str | Path, evidence_dir: str | Path, out: str | Path) -> list[str]:
    """Side by side: Moho render | AutoRig rigid simulation, per pose and per joint close-up. Only from renders
    that exist; the Moho pixels are copied, never edited (crops and labels only)."""
    pkg, ev, out = Path(package_out), Path(evidence_dir), Path(out)
    spec, raw, man, _ = load_fullbody(pkg)
    plan = json.loads((pkg / "validation_plan.json").read_text())
    rnd = Renders(pkg, man, spec, ev / "renders")
    written = []
    out.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default(size=max(16, spec.canvas_height // 50))
    bg = rnd.background()
    for p in plan["poses"]:
        r = rnd.render(p["frame"])
        if r is None:
            continue
        e = _flatten_on(rnd.expected(str(p["frame"])), bg)
        rr = _flatten_on(r, bg)
        pair = Image.new("RGB", (spec.canvas_width * 2, spec.canvas_height), (255, 255, 255))
        pair.paste(Image.fromarray(rr.clip(0, 255).astype(np.uint8)), (0, 0))
        pair.paste(Image.fromarray(e.clip(0, 255).astype(np.uint8)), (spec.canvas_width, 0))
        d = ImageDraw.Draw(pair)
        d.text((10, 10), f"MOHO RENDER frame {p['frame']}", fill=(0, 90, 0), font=font)
        d.text((spec.canvas_width + 10, 10), f"AutoRig rigid SIMULATION: {p['label']}", fill=(200, 0, 0), font=font)
        path = out / f"compare_f{p['frame']:03d}_{p['id']}.png"
        pair.save(path, optimize=True)
        written.append(path.name)
    for joint, c in plan["closeups"].items():
        r = rnd.render(c["frame"])
        if r is None:
            continue
        x0, y0, x1, y1 = c["box"]
        e = _flatten_on(rnd.expected(str(c["frame"])), bg)
        rr = _flatten_on(r, bg)
        w = x1 - x0
        pair = Image.new("RGB", (w * 2, y1 - y0), (255, 255, 255))
        pair.paste(Image.fromarray(rr[y0:y1, x0:x1].clip(0, 255).astype(np.uint8)), (0, 0))
        pair.paste(Image.fromarray(e[y0:y1, x0:x1].clip(0, 255).astype(np.uint8)), (w, 0))
        path = out / f"closeup_{joint}_f{c['frame']:03d}.png"
        pair.save(path, optimize=True)
        written.append(path.name)
    return written
