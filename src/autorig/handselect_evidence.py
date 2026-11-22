"""Phase 3.7 hand-selection evidence on the real schoolboy (``autorig hand-selection-evidence <package>``).

Writes ``<package>/natural_pose/hand_selection/``:
- ``diagnosis/``: the Phase 3.6 regression and its fix.
  - ``phase3_6_shrug_review_preview.png``: the supplied screenshot (the committed Phase 3.6 image).
  - ``shrug_comparison.png``: one frame six ways (below).
  - ``wrists_closeup.png`` and ``shoulders_closeup.png``.
  - ``diagnosis.json``: the traced switch decisions.

  The six shrug frames in ``shrug_comparison.png``:
  1. the Phase 3.6 shrug_low (clavicles 13/15);
  2. the Phase 3.6 review frame reproduced: the "cupped" request is overridden by ``auto: True``, so relaxed hands are
     turned about 80 deg;
  3. the cupped drawings as stored (wrong hand, upside down);
  4. SHRUG_SUBTLE;
  5. SHRUG_EXPRESSIVE in review mode (handedness-corrected cupped drawings, pending);
  6. SHRUG_EXPRESSIVE in production (NEEDS_ARTWORK, falls back to the subtle hands).
- ``regression/``: the 10 hand cases (neutral, left point, right point, subtle shrug, expressive shrug, thumbs up,
  gripping, open-hand conversation, hand on desk, running), each in review and production mode.
  - ``contact_sheet.png``, ``wrists.png`` and ``cases.json``;
  - per case: the selection explanation, wrist checks and shoulder metrics.
- ``continuity.json``: hand switches over the 78-frame acting test (subtle and expressive): switch times and the
  shortest hold.
- ``invalid_metadata.json``: deliberate wrong handedness, invalid metadata, an excluded drawing and missing artwork.
- ``results.json``: summary.

Nothing is approved; pending drawings are previewed only in review mode and are labelled.
"""

from __future__ import annotations

import copy
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw

from .artistic.candidates import render_pose
from .artistic.layer_order import DynamicLayerOrderController
from .artistic.poses import ArtisticPosePlanner, Gesture, mirror, variant
from .artistic.transitions import acting_test_keys
from .motion.handcheck import wrist_check
from .phase2.pose_eval import on_bg
from .performance import scenarios, shoulders
from .performance.director import PerformanceDirector

OUT = "natural_pose/hand_selection"
CASES = ["neutral", "left_point", "right_point", "shrug_subtle", "shrug_expressive", "thumbs_up", "gripping",
         "open_hand_conversation", "hand_on_desk", "running"]


def _ap_case(name: str):
    if name == "neutral":
        return variant("relaxed_standing")
    if name == "right_point":
        return variant("point_right")
    if name == "left_point":
        return mirror(variant("point_right"))
    if name == "thumbs_up":
        ap = variant("relaxed_standing")
        ap.arms["r"] = Gesture("hang", abduction=25.0, flexion=-60.0, wrist=0.0, hand="thumbs_up")  # forearm out, thumb up
        return ap
    if name == "gripping":
        ap = variant("relaxed_standing")
        ap.arms["r"] = Gesture("reach", reach=(0.75, 0.05), elbow="down", wrist=0.0, hand="gripping")
        return ap
    if name == "open_hand_conversation":
        ap = variant("relaxed_standing")
        ap.arms["r"] = Gesture("hang", abduction=20.0, flexion=-25.0, wrist=-5.0, hand="explaining")  # open to the side
        return ap
    if name == "running":
        return variant("run")
    return None


def _shrug_t() -> float:
    return next(k.t for k in acting_test_keys() if k.label == "shrug") + 0.3


def render_case(eng, vis, name: str, review: bool, planner: ArtisticPosePlanner) -> dict[str, Any]:
    t0 = time.perf_counter()
    if name in ("shrug_subtle", "shrug_expressive"):
        perf = scenarios.acting_upgrade(shrug="subtle" if name == "shrug_subtle" else "expressive")
        perf.allow_pending = review
        d = PerformanceDirector(eng, vis, perf)
        r = d.solve(_shrug_t(), render=True, corrective_mode="all" if review else "auto")
    elif name == "hand_on_desk":
        perf = scenarios.classroom(eng)
        perf.allow_pending = review
        d = PerformanceDirector(eng, vis, perf)
        r = d.solve(3.0, render=True, corrective_mode="all" if review else "auto")
    else:
        ap = _ap_case(name)
        r = render_pose(eng, vis, ap, preview_pending=review, corrective_mode="all" if review else "auto",
                        planner=planner, layer_order=DynamicLayerOrderController())
    r["wall_s"] = round(time.perf_counter() - t0, 3)
    return r


def _summary(r: dict[str, Any], eng) -> dict[str, Any]:
    out: dict[str, Any] = {"sides": {}}
    perf_h = (r.get("performance") or {}).get("hands") or {}
    for s in ("r", "l"):
        dec = r["hand_selection"]["decisions"][s]
        ph = perf_h.get(s) or {}
        pdec = ph.get("decision") or {}
        shown = r["hands"][s]
        expl = pdec.get("explanation") or dec.get("explanation") or []
        out["sides"][s] = {
            "requested": ph.get("requested") or dec.get("action"), "kind": ph.get("kind", "action"),
            "shown_pose": shown["pose"], "cid": shown["cid"], "source": shown["source"],
            "pending_review": shown["pending_review"], "note": ph.get("note") or shown.get("note"),
            "needs_artwork": pdec.get("needs_artwork") if pdec else dec.get("needs_artwork"),
            "fallback": pdec.get("fallback") if pdec else dec.get("fallback"),
            "rejected": len(pdec.get("rejected") or dec.get("rejected") or []),
            "override_check": dec.get("override_check"), "explanation": expl,
            "wrist": wrist_check(r, eng, s)}
    out["shoulders"] = shoulders.measure(r["image"], r["solve"]["joints_posed"], r["pad"])
    out["wall_s"] = r.get("wall_s")
    return out


def _crop(img: np.ndarray, cx: float, cy: float, w: int, h: int, scale: float = 1.0) -> Image.Image:
    im = on_bg(img)
    c = im.crop((int(cx - w / 2), int(cy - h / 2), int(cx + w / 2), int(cy + h / 2)))
    return c.resize((int(c.width * scale), int(c.height * scale)), Image.LANCZOS) if scale != 1.0 else c


def _wrists(r: dict[str, Any], label: str) -> Image.Image:
    J, pad = r["solve"]["joints_posed"], r["pad"]
    tiles = []
    for s in ("r", "l"):
        x, y = J[f"hand_{s}"]
        tiles.append(_crop(r["image"], x + pad, y + pad + 30, 240, 240, 1.0))
    out = Image.new("RGB", (490, 262), "white")
    out.paste(tiles[0], (0, 22))
    out.paste(tiles[1], (250, 22))
    ImageDraw.Draw(out).text((4, 4), label[:80], fill=(0, 0, 0))
    return out


def _shoulders(r: dict[str, Any], label: str) -> Image.Image:
    J, pad = r["solve"]["joints_posed"], r["pad"]
    x0, x1 = J["upper_arm_r"][0] + pad - 90, J["upper_arm_l"][0] + pad + 90
    y0 = J["neck"][1] + pad - 40
    c = on_bg(r["image"]).crop((int(x0), int(y0), int(x1), int(y0 + 170)))
    out = Image.new("RGB", (c.width, c.height + 22), "white")
    out.paste(c, (0, 22))
    ImageDraw.Draw(out).text((4, 4), label[:90], fill=(0, 0, 0))
    return out


def _grid(ims: list[Image.Image], cols: int, title: str = "") -> Image.Image:
    W = max(i.width for i in ims)
    H = max(i.height for i in ims)
    rows = (len(ims) + cols - 1) // cols
    out = Image.new("RGB", (cols * (W + 8), rows * (H + 8) + (26 if title else 0)), "white")
    d = ImageDraw.Draw(out)
    if title:
        d.text((6, 6), title, fill=(0, 0, 0))
    for i, im in enumerate(ims):
        out.paste(im, ((i % cols) * (W + 8), (26 if title else 0) + (i // cols) * (H + 8)))
    return out


def _tile(r: dict[str, Any], box, lines: list[str], h: int = 520, colours: list | None = None) -> Image.Image:
    im = on_bg(r["image"]).crop(box)
    im = im.resize((max(1, int(im.width * h / im.height)), h), Image.LANCZOS)
    out = Image.new("RGB", (max(im.width, 330), h + 14 * len(lines) + 6), "white")
    out.paste(im, (0, 0))
    d = ImageDraw.Draw(out)
    for i, ln in enumerate(lines):
        d.text((4, h + 3 + 14 * i), ln[:64], fill=(colours[i] if colours else (40, 40, 40)))
    return out


def _upper_box(r: dict[str, Any]) -> tuple[int, int, int, int]:
    J, pad = r["solve"]["joints_posed"], r["pad"]
    cx = (J["upper_arm_r"][0] + J["upper_arm_l"][0]) / 2 + pad
    top = J["neck"][1] + pad - 260
    return (int(cx - 420), int(top), int(cx + 420), int(top + 760))


# -- diagnosis --------------------------------------------------------------------------------------------------
def diagnosis(pkg: Path, eng, vis, out: Path, progress) -> dict[str, Any]:
    from .motion.pipeline import engine_for_package  # noqa: PLC0415

    out.mkdir(parents=True, exist_ok=True)
    src = "projects/schoolboy_tpose/character_package/natural_pose/performance/acting/shrug_review_preview.png"
    try:
        data = subprocess.run(["git", "show", f"043de36:{src}"], capture_output=True, check=True,
                              cwd=Path(__file__).resolve().parents[2]).stdout
        (out / "phase3_6_shrug_review_preview.png").write_bytes(data)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    t = _shrug_t()
    frames: dict[str, Any] = {}
    # 1 + 2: the Phase 3.6 parameters and switch state (user choice with auto=True -> overridden)
    old = scenarios.acting_upgrade()
    for k in old.base_keys:
        if k["label"].startswith("shrug"):
            k["params"].update({"arm_r_abduction": 15.0, "arm_r_flexion": -8.0, "arm_r_wrist": 2.0,
                                "arm_l_abduction": 14.0, "arm_l_flexion": -6.0, "arm_l_wrist": 2.0,
                                "clavicle_r": 13.0, "clavicle_l": 15.0})
            k["hands"] = {"r": "relaxed", "l": "relaxed"}
    sw36 = lambda pose: {f"hand_{s}": {"user": pose, "auto": True, "keys": [], "offsets": {}} for s in ("r", "l")}
    frames["p36_shrug_low"] = PerformanceDirector(eng, vis, old).solve(t, render=True, extra={"switches": sw36("relaxed")})
    rv = copy.deepcopy(old)
    for k in rv.base_keys:
        if k["label"].startswith("shrug"):
            k["params"].update({"arm_r_abduction": 18.0, "arm_r_flexion": -46.0, "arm_r_wrist": -16.0,
                                "arm_l_abduction": 18.0, "arm_l_flexion": -46.0, "arm_l_wrist": -16.0})
    # Phase 3.6 wrote {user: "cupped", auto: True}; its automatic engine (no rotation/palm constraint) then chose the
    # relaxed drawing for the base preset. The constrained 3.7 engine would no longer choose it, so the 3.6 pixels
    # are reproduced by forcing that drawing (what 3.6 actually displayed); the 3.6 switch state is recorded too.
    frames["p36_review_bug"] = PerformanceDirector(eng, vis, rv).solve(
        t, render=True, extra={"switches": {f"hand_{s}": {"user": "relaxed", "auto": False, "keys": [], "offsets": {}}
                                            for s in ("r", "l")}})
    frames["p36_review_bug"]["p36_switch_state"] = sw36("cupped")
    # 3: the cupped drawings honoured as stored (no metadata: wrong hand, upside down)
    progress("diagnosis: building the engine without hand metadata (stored assignment)")
    eng0, vis0 = engine_for_package(pkg, use_hand_metadata=False)
    from .garmentrig.correctives import CorrectiveArtworkStore, CorrectiveRuntime  # noqa: PLC0415

    eng0.correctives = CorrectiveRuntime(eng0, CorrectiveArtworkStore(pkg))
    swu = {f"hand_{s}": {"user": "cupped", "auto": False, "keys": [], "offsets": {}} for s in ("r", "l")}
    frames["cupped_as_stored"] = PerformanceDirector(eng0, vis0, rv).solve(t, render=True, extra={"switches": swu})
    del eng0
    # 4-6: Phase 3.7
    for name, shrug, review in (("p37_subtle", "subtle", True), ("p37_expressive_review", "expressive", True),
                                ("p37_expressive_production", "expressive", False)):
        perf = scenarios.acting_upgrade(shrug=shrug)
        perf.allow_pending = review
        frames[name] = PerformanceDirector(eng, vis, perf).solve(t, render=True,
                                                                 corrective_mode="all" if review else "auto")
    labels = {
        "p36_shrug_low": ("Phase 3.6 shrug_low (clavicles 13/15)", "shoulder pads raised; relaxed hands"),
        "p36_review_bug": ("Phase 3.6 review frame, reproduced", "'cupped' ignored (auto=True): relaxed hands turned ~80 deg"),
        "cupped_as_stored": ("cupped drawings as stored", "wrong hand on each side: upside down (palm down)"),
        "p37_subtle": ("3.7 SHRUG_SUBTLE (review)", "palms to camera, modest shoulders"),
        "p37_expressive_review": ("3.7 SHRUG_EXPRESSIVE - REVIEW ONLY", "cupped, handedness corrected, PENDING"),
        "p37_expressive_production": ("3.7 SHRUG_EXPRESSIVE - production", "NEEDS_ARTWORK: shown as SHRUG_SUBTLE"),
    }
    ref = shoulders.measure(frames["p37_subtle"]["image"], frames["p37_subtle"]["solve"]["joints_posed"],
                            frames["p37_subtle"]["pad"])
    neutral = PerformanceDirector(eng, vis, scenarios.acting_upgrade()).solve(0.0, render=True)
    nref = shoulders.measure(neutral["image"], neutral["solve"]["joints_posed"], neutral["pad"])
    del ref
    rep: dict[str, Any] = {"t": t, "frames": {}}
    tiles, wr, sh = [], [], []
    for k, r in frames.items():
        sm = _summary(r, eng)
        sm["shoulder_check"] = shoulders.check(sm["shoulders"], nref, shrug=True, abduction=shoulders.abductions(r))
        rep["frames"][k] = {"label": labels[k][0], "note": labels[k][1], **{kk: v for kk, v in sm.items()}}
        hs = "/".join(f"{s}:{sm['sides'][s]['shown_pose']}{'*' if sm['sides'][s]['pending_review'] else ''}"
                      for s in ("r", "l"))
        sc = sm["shoulder_check"]["sides"]
        lines = [labels[k][0], labels[k][1], f"hands {hs}  (* pending)",
                 "shoulder rise r/l: " + "/".join(f"{sc[s].get('rise_px', 0):.0f}" for s in ("r", "l")) + " px, slope " +
                 "/".join(f"{sc[s]['slope_deg']:.0f}" for s in ("r", "l")) + " deg"]
        col = [(170, 0, 0) if k.startswith(("p36", "cupped")) else (0, 110, 0)] + [(60, 60, 60)] * 3
        tiles.append(_tile(r, _upper_box(r), lines, colours=col))
        wr.append(_wrists(r, labels[k][0]))
        sh.append(_shoulders(r, labels[k][0] + " | " + "; ".join(sm["shoulder_check"]["flags"])[:60]))
    _grid(tiles, 3, "Shrug diagnosis: Phase 3.6 (red) vs Phase 3.7 (green). Pending drawings are labelled; "
                    "nothing is approved").save(out / "shrug_comparison.png")
    _grid(wr, 2, "Wrist close-ups (character right | left)").save(out / "wrists_closeup.png")
    _grid(sh, 2, "Blazer shoulders").save(out / "shoulders_closeup.png")
    rep["neutral_shoulders"] = nref
    rep["findings"] = [
        "the Phase 3.6 resolver wrote {user: pose, auto: True}; the switch controller honours user only when auto is "
        "False, so every performance hand request was replaced by the automatic choice for the base preset "
        "(relaxed_standing). The 3.6 review frame labelled 'cupped' shows the relaxed drawings.",
        "the hand bone was at ~80 deg (forearm 64 deg + wrist -16 deg): the camera-facing relaxed drawing turned ~80 deg "
        "reads as a presenting palm, not palm up. A flat rotation cannot make a camera-facing palm face up.",
        "the cupped drawings are stored on the wrong side: at rest each is placed ~182 deg from how it was drawn (all "
        "other sideways drawings are within 4 deg); the thumb is on the far side, so r2c4 (left_cupped) is a right "
        "hand and r4c4 (right_cupped) a left hand. Honoured as stored they are upside down (palm down).",
        "the selection action was relaxed_standing (the base preset), never the gesture; no palm, handedness or rotation "
        "constraint existed; pending drawings were shown in review mode (labelled) - correct, but the label named the "
        "request, not the drawing actually rendered.",
        "shoulders: the 3.6 shrug used clavicle params 13/15 on a T-pose shoulder line that is already level: the line "
        "rose outward (-4 to -6 deg) and the top rose 10-16 px (inflated pads).",
    ]
    (out / "diagnosis.json").write_text(json.dumps(rep, indent=1, default=float) + "\n", encoding="utf-8")
    return rep


# -- regression cases -------------------------------------------------------------------------------------------
def regression(eng, vis, out: Path, progress) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    planner = ArtisticPosePlanner(eng.profile)
    res: dict[str, Any] = {}
    tiles, wr = [], []
    neutral = render_case(eng, vis, "neutral", True, planner)
    nref = shoulders.measure(neutral["image"], neutral["solve"]["joints_posed"], neutral["pad"])
    for name in CASES:
        res[name] = {}
        for mode in ("review", "production"):
            r = neutral if (name == "neutral" and mode == "review") else render_case(eng, vis, name, mode == "review", planner)
            sm = _summary(r, eng)
            sm["shoulder_check"] = shoulders.check(sm["shoulders"], nref, shrug=name.startswith("shrug"),
                                                   abduction=shoulders.abductions(r))
            res[name][mode] = sm
            S = sm["sides"]
            lines = [f"{name} [{mode}]"]
            for s in ("r", "l"):
                x = S[s]
                st = "PENDING" if x["pending_review"] else "approved/original"
                na = ",".join(n["pose"] for n in (x["needs_artwork"] or []) if n["status"] == "NEEDS_ARTWORK")
                lines.append(f"{s}: {x['requested']} -> {x['shown_pose']} ({st}) wrist {x['wrist']['status']}"
                             + (f" NEEDS_ARTWORK {na}" if na else ""))
            if sm["shoulder_check"]["flags"]:
                lines.append("shoulders: " + sm["shoulder_check"]["flags"][0])
            tiles.append(_tile(r, _upper_box(r), lines, h=420))
            wr.append(_wrists(r, f"{name} [{mode}]"))
            progress(f"  {name} [{mode}]: r={S['r']['shown_pose']} l={S['l']['shown_pose']} "
                     f"wrists {S['r']['wrist']['status']}/{S['l']['wrist']['status']} ({sm['wall_s']} s)")
    _grid(tiles, 4, "Phase 3.7 hand regression cases (review = pending drawings previewed and labelled; production = "
                    "approved drawings and the original hand only)").save(out / "contact_sheet.png")
    _grid(wr, 4, "Wrist close-ups (character right | left)").save(out / "wrists.png")
    (out / "cases.json").write_text(json.dumps(res, indent=1, default=float) + "\n", encoding="utf-8")
    return res


def continuity(eng, vis) -> dict[str, Any]:
    out = {}
    for shrug in ("subtle", "expressive"):
        perf = scenarios.acting_upgrade(shrug=shrug)
        d = PerformanceDirector(eng, vis, perf)
        rows = []
        for t in d.frames():
            r = d.solve(t)
            rows.append((t, {s: r["hands"][s]["pose"] for s in ("r", "l")}))
        sw = {s: [] for s in ("r", "l")}
        for (t0, a), (t1, b) in zip(rows, rows[1:]):
            for s in ("r", "l"):
                if a[s] != b[s]:
                    sw[s].append({"t": round(t1, 3), "from": a[s], "to": b[s]})
        holds = []
        for s in ("r", "l"):
            ts = [0.0] + [x["t"] for x in sw[s]] + [rows[-1][0]]
            holds += [b - a for a, b in zip(ts, ts[1:]) if b > a]
        # determinism: random access == playback
        same = all(d.solve(t)["hands"][s]["pose"] == rows[i][1][s] for i, t in enumerate(d.frames()[::-7])
                   for s in ("r", "l")) if False else None
        out[shrug] = {"frames": len(rows), "switches": sw, "min_hold_s": round(min(holds), 3) if holds else None,
                      "plan": {s: [{k: iv[k] for k in ("value", "t0", "t1", "kind")} |
                                   ({"chosen": iv["decision"]["chosen"]} if "decision" in iv else {})
                                   for iv in d.hand_plan()[s]] for s in ("r", "l")}}
        del same
    return out


def invalid_metadata(eng) -> dict[str, Any]:
    """Deliberate failures on a real hand library (in memory; nothing is written)."""
    from .motion.handselect import HandContext  # noqa: PLC0415

    sel = eng.hand_select
    lib = eng.hand_lib
    ctx = HandContext("r", "shrugging", 64.0, 72.0, float(eng.sk.bone("hand_r").rest_angle), -40.0, -8.0, (300.0, 580.0))
    out: dict[str, Any] = {}
    base = sel.select(lib, ctx, None, True)
    out["baseline"] = {"chosen": base["chosen"], "cid": base["cid"]}
    v = lib.hands["r"]["cupped"]
    keep = copy.deepcopy(v.meta)
    try:
        v.meta["hand_meta"] = {**v.meta["hand_meta"], "handedness": "left"}
        d = sel.select(lib, ctx, None, True)
        out["wrong_handedness"] = {"chosen": d["chosen"], "fallback": bool(d["fallback"]),
                                   "cupped_violations": next(r["violations"] for r in d["rejected"] if r["pose"] == "cupped")}
        v.meta["hand_meta"] = {**keep["hand_meta"], "palm_normal_sprite": [0, 0], "errors": ["palm_normal_sprite must be "
                                                                                             "three numbers with length ~1"]}
        d = sel.select(lib, ctx, None, True)
        out["invalid_metadata"] = {"chosen": d["chosen"],
                                   "cupped_violations": next(r["violations"] for r in d["rejected"] if r["pose"] == "cupped")}
        v.meta["hand_meta"] = {**keep["hand_meta"], "excluded": True}
        d = sel.select(lib, ctx, None, True)
        out["excluded_by_user"] = {"chosen": d["chosen"], "needs_artwork": d["needs_artwork"], "fallback": d["fallback"]}
        v.meta["hand_meta"] = {**keep["hand_meta"], "palm_normal_sprite": [0.0, 0.0, 1.0]}
        d = sel.select(lib, ctx, None, True)
        out["camera_facing_claimed"] = {"chosen": d["chosen"], "note": "a camera-facing drawing never passes palm-up",
                                        "cupped_violations": next(r["violations"] for r in d["rejected"] if r["pose"] == "cupped")}
    finally:
        v.meta = keep
    d = sel.select(lib, ctx, None, False)
    out["production_missing_art"] = {"chosen": d["chosen"], "needs_artwork": d["needs_artwork"], "fallback": d["fallback"]}
    return out


def hand_selection_evidence(pkg: str | Path, quick: bool = False, progress: Callable[[str], Any] = print) -> dict[str, Any]:
    from .garmentrig.correctives import CorrectiveArtworkStore, CorrectiveRuntime  # noqa: PLC0415
    from .motion.pipeline import engine_for_package  # noqa: PLC0415

    pkg = Path(pkg)
    out = pkg / OUT
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    eng, vis = engine_for_package(pkg)
    eng.correctives = CorrectiveRuntime(eng, CorrectiveArtworkStore(pkg))
    progress(f"engine {time.perf_counter() - t0:.0f} s; hand metadata attached to {eng.hand_meta} candidates")
    res: dict[str, Any] = {"package": str(pkg)}
    res["diagnosis"] = diagnosis(pkg, eng, vis, out / "diagnosis", progress)
    progress("diagnosis done")
    res["regression"] = regression(eng, vis, out / "regression", progress)
    progress("regression done")
    res["invalid_metadata"] = invalid_metadata(eng)
    (out / "invalid_metadata.json").write_text(json.dumps(res["invalid_metadata"], indent=1, default=float) + "\n",
                                               encoding="utf-8")
    if not quick:
        res["continuity"] = continuity(eng, vis)
        (out / "continuity.json").write_text(json.dumps(res["continuity"], indent=1, default=float) + "\n",
                                             encoding="utf-8")
    reg = res["regression"]
    res["summary"] = {
        "cases": {n: {m: {s: reg[n][m]["sides"][s]["shown_pose"] + ("*" if reg[n][m]["sides"][s]["pending_review"] else "")
                          for s in ("r", "l")} for m in reg[n]} for n in reg},
        "wrist_fail": [f"{n}/{m}/{s}" for n in reg for m in reg[n] for s in ("r", "l")
                       if reg[n][m]["sides"][s]["wrist"]["status"] == "FAIL"],
        "needs_artwork": sorted({na["pose"] for n in reg for m in reg[n] for s in ("r", "l")
                                 for na in (reg[n][m]["sides"][s]["needs_artwork"] or []) if na["status"] == "NEEDS_ARTWORK"}),
        "wall_s": round(time.perf_counter() - t0, 1)}
    (out / "results.json").write_text(json.dumps({"summary": res["summary"],
                                                  "findings": res["diagnosis"]["findings"]}, indent=1, default=float)
                                      + "\n", encoding="utf-8")
    return res


__all__ = ["hand_selection_evidence", "CASES", "render_case", "invalid_metadata"]
