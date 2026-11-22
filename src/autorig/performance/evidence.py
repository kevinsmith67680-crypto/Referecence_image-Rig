"""Phase 3.6 evidence on the real schoolboy (``autorig performance-evidence <package>``).

Writes ``natural_pose/performance/``:
- ``acting/``: the Phase 3.5 acting test re-rendered (baseline) and the upgraded performance, frame-aligned:
  - ``before_after.gif`` (side by side, every frame);
  - ``contact_sheet.png`` (every 6th frame);
  - ``diagnostics.png`` (feet, pelvis, weight, arm and secondary curves for both);
  - ``shrug_review_preview.png`` (Phase 3.7: SHRUG_SUBTLE next to SHRUG_EXPRESSIVE in review mode, labelled with the
    drawings actually rendered).
- ``classroom/``: desk contact performance (``classroom.gif``, ``contact_sheet.png``, ``diagnostics.png``);
- ``secondary/``: body-only vs with secondary motion (``side_by_side.gif``, ``closeups.png``, ``diagnostics.png``);
- ``results.json``: motion, artistic and geometric checks, timings, benchmark.

Also writes:
- ``reports/performance_quality.json``;
- ``analysis/performance_animation.json`` (Moho-oriented metadata, proposed_unverified);
- ``analysis/live_performance_controls.json``.

Every frame comes from AutoRig's own solver and renderer. Pending drawings are previewed and labelled. Nothing is
approved.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw

from ..artistic.poses import ArtisticPosePlanner
from ..artistic.transitions import ActingSequence, acting_test_keys
from ..phase2.pose_eval import on_bg
from . import scenarios
from .director import PerformanceDirector
from .evaluate import PerformanceQualityEvaluator
from .export import animation_metadata, compatibility, live_control_spec
from .seat import SeatAndInteractionController, draw_props

H_OUT = 540


class Frames:
    """Rendered frames kept PNG-compressed in memory (a full-resolution RGBA frame is ~10 MB raw), with the union of
    their alpha boxes."""

    def __init__(self):
        self.data: list[bytes] = []
        self.box: tuple[int, int, int, int] | None = None

    def add(self, img: np.ndarray) -> None:
        import io  # noqa: PLC0415

        ys, xs = np.nonzero(img[..., 3] > 0)
        if len(xs):
            b = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
            self.box = b if self.box is None else (min(self.box[0], b[0]), min(self.box[1], b[1]),
                                                   max(self.box[2], b[2]), max(self.box[3], b[3]))
        buf = io.BytesIO()
        Image.fromarray(img, "RGBA").save(buf, "PNG", compress_level=1)
        self.data.append(buf.getvalue())

    def __getitem__(self, i: int) -> np.ndarray:
        import io  # noqa: PLC0415

        return np.asarray(Image.open(io.BytesIO(self.data[i])).convert("RGBA"))

    def __len__(self) -> int:
        return len(self.data)


def _union(*fs: "Frames", margin: int = 14) -> tuple[int, int, int, int]:
    bs = [f.box for f in fs if f.box is not None]
    return (max(0, min(b[0] for b in bs) - margin), max(0, min(b[1] for b in bs) - margin),
            max(b[2] for b in bs) + margin, max(b[3] for b in bs) + margin)


def _js(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def _box(frames: list[np.ndarray], margin: int = 14) -> tuple[int, int, int, int]:
    u = np.zeros(frames[0].shape[:2], bool)
    for f in frames:
        u |= f[..., 3] > 0
    ys, xs = np.nonzero(u)
    return (max(0, xs.min() - margin), max(0, ys.min() - margin), xs.max() + margin, ys.max() + margin)


def _tile(img: np.ndarray, box, label: str, sub: str = "", colour=(60, 60, 60), h: int = H_OUT) -> Image.Image:
    im = on_bg(img).crop(box)
    im = im.resize((max(1, int(im.width * h / im.height)), h), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    d.text((6, 6), label, fill=colour)
    if sub:
        d.text((6, 20), sub, fill=(150, 60, 0))
    return im


def _pair(a: Image.Image, b: Image.Image, gap: int = 8) -> Image.Image:
    out = Image.new("RGB", (a.width + b.width + gap, max(a.height, b.height)), "white")
    out.paste(a, (0, 0))
    out.paste(b, (a.width + gap, 0))
    return out


def _save_gif(ims: list[Image.Image], path: Path, fps: float, scale: float = 0.6) -> None:
    sm = [i.resize((int(i.width * scale), int(i.height * scale)), Image.LANCZOS) for i in ims]
    sm[0].save(path, save_all=True, append_images=sm[1:], duration=int(1000 / fps), loop=0)


def _sheet(ims: list[Image.Image], step: int, cols: int = 7, scale: float = 0.5) -> Image.Image:
    sel = [i.resize((int(i.width * scale), int(i.height * scale)), Image.LANCZOS) for i in ims[::step]]
    w, h = sel[0].width, sel[0].height
    rows = (len(sel) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (w + 4), rows * (h + 4)), "white")
    for k, im in enumerate(sel):
        sheet.paste(im, ((k % cols) * (w + 4), (k // cols) * (h + 4)))
    return sheet


def plot(series: list[tuple[str, list[float], list[float], tuple[int, int, int]]], title: str, w: int = 760,
         h: int = 210, marks: list[tuple[float, str]] | None = None, ylabel: str = "") -> Image.Image:
    """A small line chart (PIL only)."""
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    L, R, T, B = 48, 12, 22, 24
    xs = [x for _, sx, _, _ in series for x in sx]
    ys = [y for _, _, sy, _ in series for y in sy]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    if y1 - y0 < 1e-6:
        y0, y1 = y0 - 1, y1 + 1
    pad = 0.08 * (y1 - y0)
    y0, y1 = y0 - pad, y1 + pad
    X = lambda x: L + (x - x0) / max(x1 - x0, 1e-9) * (w - L - R)  # noqa: E731
    Y = lambda y: T + (1 - (y - y0) / (y1 - y0)) * (h - T - B)  # noqa: E731
    d.rectangle([L, T, w - R, h - B], outline=(200, 200, 200))
    d.text((L, 4), title, fill=(0, 0, 0))
    d.text((2, T), f"{y1:.1f}", fill=(90, 90, 90))
    d.text((2, h - B - 10), f"{y0:.1f}", fill=(90, 90, 90))
    d.text((L, h - B + 6), f"{x0:.1f}s", fill=(90, 90, 90))
    d.text((w - R - 30, h - B + 6), f"{x1:.1f}s", fill=(90, 90, 90))
    if ylabel:
        d.text((L + 4, T + 2), ylabel, fill=(110, 110, 110))
    for x, lab in marks or []:
        d.line([(X(x), T), (X(x), h - B)], fill=(225, 225, 225))
        d.text((X(x) + 2, h - B - 12), lab, fill=(150, 150, 150))
    lx = w - R - 160
    for k, (name, sx, sy, col) in enumerate(series):
        d.line([(X(a), Y(b)) for a, b in zip(sx, sy)], fill=col, width=2)
        d.text((lx, T + 4 + 12 * k), name, fill=col)
    return im


def _stack(ims: list[Image.Image]) -> Image.Image:
    W = max(i.width for i in ims)
    out = Image.new("RGB", (W, sum(i.height for i in ims)), "white")
    y = 0
    for i in ims:
        out.paste(i, (0, y))
        y += i.height
    return out


# -- the acting test ------------------------------------------------------------------------------------------------
def acting(eng, vis, out: Path, progress: Callable[[str], Any], quick: bool = False) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    fps = 12.0
    planner = ArtisticPosePlanner(eng.profile)
    from ..artistic.candidates import render_pose  # noqa: PLC0415
    from ..artistic.layer_order import DynamicLayerOrderController  # noqa: PLC0415

    seq = ActingSequence(acting_test_keys(), fps)
    base_frames, base_track, t0 = Frames(), [], time.perf_counter()
    frames = seq.frames()
    if quick:
        frames = frames[::6]
    for t, ap, meta in frames:
        r = render_pose(eng, vis, ap, planner=planner, layer_order=DynamicLayerOrderController())
        base_frames.add(r["image"])
        s = r["solve"]
        base_track.append({"t": t, "J": s["joints_posed"], "T": s["bone_tails_posed"], "hands": r["hands"]})
    t_base = time.perf_counter() - t0
    progress(f"acting baseline: {len(base_frames)} frames in {t_base:.0f} s")
    d = PerformanceDirector(eng, vis, scenarios.acting_upgrade())
    up_frames, up_res, t0 = Frames(), [], time.perf_counter()
    for t, _, _ in frames:
        r = d.solve(t, render=True)
        up_frames.add(r["image"])
        r.pop("image", None)
        r["render"] = {"clipped_uncovered": (r.get("render") or {}).get("clipped_uncovered")}
        up_res.append(r)
    t_up = time.perf_counter() - t0
    progress(f"acting upgrade: {len(up_frames)} frames in {t_up:.0f} s")
    box = _union(base_frames, up_frames)
    pairs = []
    for k, (t, _, meta) in enumerate(frames):
        pf = up_res[k]["performance"]
        hands = "/".join(f"{h['shown']}{'*' if h['status'] == 'pending_review' else ''}" for h in pf["hands"].values())
        a = _tile(base_frames[k], box, f"Phase 3.5  {t:.2f}s", meta.get("key", ""))
        b = _tile(up_frames[k], box, f"Phase 3.6  {t:.2f}s", f"hands {hands} (* pending)")
        pairs.append(_pair(a, b))
    _save_gif(pairs, out / "before_after.gif", fps, 0.5)
    _sheet(pairs, 6 if not quick else 1, cols=3, scale=0.42).save(out / "contact_sheet.png")
    # diagnostics on dense skeleton-only tracks (cheap)
    times = np.arange(0, seq.duration + 1e-9, 1 / 24)
    bt = []
    for t in times:
        ap, _ = seq.sample(t)
        rq = planner.request(ap)
        nm = rq.pop("name")
        rq.pop("depth", None)
        s = eng.pose(nm, visible=vis, skeleton_only=True, **rq)["solve"]
        bt.append(s)
    ut = [d.solve(float(t))["solve"] for t in times]
    marks = [(k.t, k.label) for k in acting_test_keys()]

    def col(tr, f):
        return [f(s) for s in tr]
    charts = [
        plot([("3.5 right ankle x", list(times), col(bt, lambda s: s["joints_posed"]["foot_r"][0]), (200, 60, 60)),
              ("3.6 right ankle x", list(times), col(ut, lambda s: s["joints_posed"]["foot_r"][0]), (40, 120, 200)),
              ("3.5 left ankle x", list(times), col(bt, lambda s: s["joints_posed"]["foot_l"][0]), (230, 140, 60)),
              ("3.6 left ankle x", list(times), col(ut, lambda s: s["joints_posed"]["foot_l"][0]), (60, 170, 90))],
             "planted feet (px): Phase 3.5 jumps 13.4 px at the weight crossings, Phase 3.6 stays planted", marks=marks),
        plot([("3.5 pelvis x", list(times), col(bt, lambda s: s["joints_posed"]["pelvis"][0]), (200, 60, 60)),
              ("3.6 pelvis x", list(times), col(ut, lambda s: s["joints_posed"]["pelvis"][0]), (40, 120, 200))],
             "pelvis x (px): weight transfer", marks=marks),
        plot([("3.5 right hand y", list(times), col(bt, lambda s: s["joints_posed"]["hand_r"][1]), (200, 60, 60)),
              ("3.6 right hand y", list(times), col(ut, lambda s: s["joints_posed"]["hand_r"][1]), (40, 120, 200))],
             "right wrist height (px, down is +): anticipation dip before the raise, overshoot and settle", marks=marks),
        plot([("hair lag", list(times), [d.secondary.value("hair", t) for t in times], (120, 60, 160)),
              ("hem lag", list(times), [d.secondary.value("hem", t) for t in times], (40, 150, 150))],
             "Phase 3.6 secondary motion (deg)", marks=marks),
    ]
    _stack(charts).save(out / "diagnostics.png")
    # Phase 3.7: SHRUG_SUBTLE (supported) next to SHRUG_EXPRESSIVE in review mode. The hands are chosen by the
    # constrained selection engine (palm-up drawings only: the handedness-corrected cupped drawings, pending) and the
    # labels name the drawings actually rendered. The Phase 3.6 version of this image showed relaxed hands turned
    # ~80 deg under a "cupped" label (see natural_pose/hand_selection/diagnosis/).
    shrug_t = next(k.t for k in acting_test_keys() if k.label == "shrug") + 0.3
    perf = scenarios.acting_upgrade(shrug="expressive")
    rv = PerformanceDirector(eng, vis, perf).solve(shrug_t, render=True)
    ref = up_frames[min(len(up_frames) - 1, int(round(shrug_t * fps)) // (6 if quick else 1))]
    sb = _box([rv["image"], ref])
    shown = lambda r: "/".join(f"{s}:{r['hands'][s]['pose']}{' (pending)' if r['hands'][s]['pending_review'] else ''}"
                               for s in ("r", "l"))
    sub = d.solve(shrug_t)
    a = _tile(ref, sb, "SHRUG_SUBTLE (supported)", f"hands {shown(sub)}")
    b = _tile(rv["image"], sb, "SHRUG_EXPRESSIVE - REVIEW ONLY", f"hands {shown(rv)}; production: NEEDS_ARTWORK "
              "(palm_up), falls back to the subtle hands", (170, 0, 0))
    _pair(a, b).save(out / "shrug_review_preview.png")
    ev = PerformanceQualityEvaluator(d)
    return {"frames": len(frames), "fps": fps, "baseline_render_s_per_frame": round(t_base / len(frames), 2),
            "upgrade_render_s_per_frame": round(t_up / len(frames), 2),
            "baseline_motion": _baseline_motion(bt, times), "motion": ev.motion(), "artistic": ev.artistic(),
            "geometric": PerformanceQualityEvaluator.geometric(up_res),
            "shrug": {"status": d.library.status("shrug")["status"], "shown": "SHRUG_SUBTLE (shrug_low)",
                      "review_preview": "shrug_review_preview.png (SHRUG_EXPRESSIVE review: "
                                        f"{shown(rv)})"},
            "timing": d.timing, "calibration": d.calibration}


def _baseline_motion(track: list[dict[str, Any]], times) -> dict[str, Any]:
    out = {}
    for s in ("r", "l"):
        P = np.array([x["joints_posed"][f"foot_{s}"] for x in track], float)
        st = np.hypot(*np.diff(P, axis=0).T)
        out[s] = {"max_step_px": round(float(st.max()), 3), "jumps_over_1px": [round(float(times[i + 1]), 3)
                                                                                for i in np.nonzero(st > 1.0)[0]]}
    return {"feet": out, "note": "Phase 3.5 ActingSequence through ArtisticPosePlanner (skeleton-only, 24 Hz)"}


# -- the classroom test --------------------------------------------------------------------------------------------
def classroom(eng, vis, out: Path, progress: Callable[[str], Any], quick: bool = False) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    d = PerformanceDirector(eng, vis, scenarios.classroom(eng))
    times = d.frames()
    if quick:
        times = times[::6]
    frames, res, t0 = Frames(), [], time.perf_counter()
    for t in times:
        r = d.solve(t, render=True)
        frames.add(draw_props(r["image"], r["pad"], d.desks, eng.profile.ground_y))
        r.pop("image", None)
        r["render"] = {"clipped_uncovered": (r.get("render") or {}).get("clipped_uncovered")}
        res.append(r)
    dt = time.perf_counter() - t0
    progress(f"classroom: {len(frames)} frames in {dt:.0f} s")
    box = _union(frames)
    ims = []
    for k, (t, r) in enumerate(zip(times, res)):
        f = frames[k]
        pf = r["performance"]
        c = pf.get("contacts", {}).get("r", {})
        hands = "/".join(f"{h['shown']}{'*' if h['status'] == 'pending_review' else ''}" for h in pf["hands"].values())
        im = _tile(f, box, f"{t:.2f}s  desk contact w={c.get('weight', 0):.2f}", f"hands {hands} (* pending)")
        dd = ImageDraw.Draw(im)
        dd.text((6, im.height - 30), "classmate ->", fill=(120, 120, 120))
        dd.text((6, im.height - 16), "desk: test prop. Forward lean: REQUIRES_NEW_VIEW (not rendered)", fill=(150, 60, 0))
        ims.append(im)
    _save_gif(ims, out / "classroom.gif", d.perf.fps, 0.7)
    _sheet(ims, 6 if not quick else 1, cols=6, scale=0.5).save(out / "contact_sheet.png")
    ts = np.arange(0, d.duration + 1e-9, 1 / 24)
    sv = [d.solve(float(t)) for t in ts]
    c = d.contacts.specs[0]
    marks = [(k.t, k.label.split(" ")[0]) for k in d.base.keys] + [(c.start, "contact"), (c.end, "release")]
    charts = [
        plot([("desk contact weight", list(ts), [c.weight(t) for t in ts], (200, 60, 60))], "hand contact blend (C2)",
             marks=marks),
        plot([("palm-to-desk error px", list(ts), [r["performance"]["contact_sliding_px"].get(c.id, float("nan"))
                                                   if r["performance"]["contact_sliding_px"] else 0.0 for r in sv],
               (40, 120, 200))], "contact sliding while fully planted (px)", marks=marks),
        plot([("right wrist x", list(ts), [r["solve"]["joints_posed"]["hand_r"][0] for r in sv], (200, 60, 60)),
              ("left wrist x", list(ts), [r["solve"]["joints_posed"]["hand_l"][0] for r in sv], (40, 120, 200)),
              ("pelvis x", list(ts), [r["solve"]["joints_posed"]["pelvis"][0] for r in sv], (60, 170, 90))],
             "wrists and pelvis (px): acquisition, hold, point, release", marks=marks),
    ]
    _stack(charts).save(out / "diagnostics.png")
    ev = PerformanceQualityEvaluator(d)
    return {"frames": len(frames), "render_s_per_frame": round(dt / len(frames), 2), "motion": ev.motion(),
            "artistic": ev.artistic(), "geometric": PerformanceQualityEvaluator.geometric(res),
            "interactions": SeatAndInteractionController(eng).interactions(),
            "forward_lean": "REQUIRES_NEW_VIEW (art:torso_lean_forward): not rendered; the lean is lateral",
            "desk": d.desks[0].to_json(), "contact": c.to_json(),
            "gesture_status": {g["id"]: d.library.status(g["gesture"], g["side"]) for g in d.perf.gestures},
            "metadata": animation_metadata(d, 6.0)}


# -- the secondary-motion demo ----------------------------------------------------------------------------------------
def secondary(eng, vis, out: Path, progress: Callable[[str], Any], quick: bool = False) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    on = PerformanceDirector(eng, vis, scenarios.secondary_demo())
    off = PerformanceDirector(eng, vis, scenarios.secondary_demo(mute_secondary=True))
    times = on.frames()
    if quick:
        times = times[::6]
    A, B, t0 = Frames(), Frames(), time.perf_counter()
    for t in times:
        A.add(off.solve(t, render=True)["image"])
        B.add(on.solve(t, render=True)["image"])
    dt = time.perf_counter() - t0
    progress(f"secondary: {2 * len(times)} frames in {dt:.0f} s")
    box = _union(A, B)
    pairs, diffs = [], []
    for k, t in enumerate(times):
        a, b = A[k], B[k]
        dm = np.any(a != b, axis=2)
        diffs.append(int(dm.sum()))
        ov = b.copy()
        ov[dm] = [255, 0, 160, 255]
        pairs.append(_pair(_pair(_tile(a, box, f"body only  {t:.2f}s"), _tile(b, box, "with secondary",
                                                                                  f"hair {on.secondary.value('hair', t):+.2f}°"
                                                                                  f" hem {on.secondary.value('hem', t):+.2f}°")),
                           _tile(ov, box, "difference (magenta)")))
    _save_gif(pairs, out / "side_by_side.gif", on.perf.fps, 0.45)
    k = int(np.argmax(diffs))
    _sheet(pairs, max(1, len(pairs) // 8), cols=2, scale=0.45).save(out / "contact_sheet.png")
    ts = np.arange(0, on.duration + 1e-9, 1 / 48)
    lean_end = on.base.keys[1].t
    _stack([plot([("hair lag", list(ts), [on.secondary.value("hair", t) for t in ts], (120, 60, 160)),
                  ("hem lag", list(ts), [on.secondary.value("hem", t) for t in ts], (40, 150, 150))],
                 "secondary lag (deg): the body stops at the mark, hair and hem settle after",
                 marks=[(lean_end, "body stops")]),
            plot([("changed px vs body only", list(times), diffs, (200, 60, 60))], "pixels changed by secondary motion")
            ]).save(out / "diagnostics.png")
    return {"frames": len(times), "render_s_per_frame": round(dt / (2 * len(times)), 2),
            "max_changed_px": max(diffs), "max_changed_at_s": round(float(times[k]), 3),
            "settled_at_end_deg": {p: round(abs(on.secondary.value(p, on.duration)), 3) for p in ("hair", "hem")},
            "report": on.secondary.report(), "rigid_parts": "badge, buttons, tie, lapels, collar: unchanged (test)"}


# -- benchmark ------------------------------------------------------------------------------------------------------
def benchmark(eng, vis, n: int = 5) -> dict[str, Any]:
    from ..garmentrig.corrective_evidence import hardware  # noqa: PLC0415

    d = PerformanceDirector(eng, vis, scenarios.classroom(eng))
    ts = [1.0, 2.0, 3.0, 3.6, 5.0][:n]

    def med(fn):
        out = []
        for t in ts:
            a = time.perf_counter()
            fn(t)
            out.append(time.perf_counter() - a)
        return round(float(np.median(out)), 4)
    t0 = time.perf_counter()
    PerformanceDirector(eng, vis, scenarios.classroom(eng))
    init = time.perf_counter() - t0
    r = d.solve(3.6, render=True)
    return {"A_full_render_s": med(lambda t: d.solve(t, render=True)),
            "B_interactive_pose_eval_s": med(lambda t: d.solve(t)),
            "B_layers_eval_s": med(lambda t: d.pose_at(t)),
            "C_timeline_preview_track_s_per_frame": round(med(lambda t: d.track(4.0)) / max(len(d.frames(4.0)), 1), 4),
            "D_quality_motion_check_s": round(_time(lambda: PerformanceQualityEvaluator(d).motion(fps=24.0)), 2),
            "secondary_simulate_s": d.timing.get("secondary_simulate_s"), "director_init_s": round(init, 3),
            "secondary_lookup_s": med(lambda t: d.secondary.garment(t)),
            "render_breakdown_ms": r.get("timing_ms"), "hardware": hardware(),
            "note": "cloud sandbox; skeleton-only evaluation (contacts include one extra free solve) is the interactive "
                    "path; full artwork renders are offline. No Apple M1 or real-time figure is claimed: run "
                    "`autorig performance-bench <package>` on the Mac."}


def _time(fn) -> float:
    a = time.perf_counter()
    fn()
    return time.perf_counter() - a


def performance_evidence(pkg, out: Path | None = None, quick: bool = False, progress=print) -> dict[str, Any]:
    from ..garmentrig.correctives import CorrectiveArtworkStore, CorrectiveRuntime  # noqa: PLC0415
    from ..motion.pipeline import engine_for_package  # noqa: PLC0415

    pkg = Path(pkg)
    out = Path(out or pkg / "natural_pose" / "performance")
    out.mkdir(parents=True, exist_ok=True)
    t_all = time.perf_counter()
    eng, vis = engine_for_package(pkg)
    eng.correctives = CorrectiveRuntime(eng, CorrectiveArtworkStore(pkg))
    res: dict[str, Any] = {}
    res["acting"] = acting(eng, vis, out / "acting", progress, quick)
    res["classroom"] = classroom(eng, vis, out / "classroom", progress, quick)
    res["secondary"] = secondary(eng, vis, out / "secondary", progress, quick)
    res["benchmark"] = benchmark(eng, vis)
    res["benchmark"]["evidence_total_s"] = round(time.perf_counter() - t_all, 1)
    meta = res["classroom"].pop("metadata")
    report = {"schema": "autorig.performance_quality", "version": "1.0.0", "results": res,
              "compatibility": compatibility(), "artistic_review_required": True, "artistic_approval": "not recorded",
              "production_ready": False}
    (out / "results.json").write_text(json.dumps(report, indent=1, default=_js))
    (pkg / "reports").mkdir(exist_ok=True)
    (pkg / "reports" / "performance_quality.json").write_text(json.dumps(report, indent=1, default=_js))
    (pkg / "analysis").mkdir(exist_ok=True)
    (pkg / "analysis" / "performance_animation.json").write_text(json.dumps(meta, indent=1, default=_js))
    (pkg / "analysis" / "live_performance_controls.json").write_text(json.dumps(live_control_spec(), indent=1))
    progress(f"done in {res['benchmark']['evidence_total_s']} s -> {out}")
    return report


__all__ = ["performance_evidence", "benchmark", "plot"]
