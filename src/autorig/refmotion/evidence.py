"""Phase 3.7 reference-motion evidence (``autorig reference-evidence <package>``).

1. The three test references (pointing, shrug, walking) are procedurally authored synthetic skeletons, labelled
   synthetic. They go into the character-independent library (``motion_references/``) and are retargeted onto the
   schoolboy. Per reference, a comparison sheet shows:
   - the reference skeleton;
   - the reference-guided render;
   - the hand-authored Phase 3.7 version;
   - an overlay of the reference and rig silhouettes;

   plus per-channel errors, feasibility (depth -> REQUIRES_* classes, never stretched), critic defects and the hand
   decisions.
2. The video path is checked with a real model, on the user's own character: an AutoRig-rendered MP4 of the schoolboy
   (pointing + subtle shrug from the acting test) goes through ffmpeg decoding and MediaPipe Pose (local CPU), then
   analysis, then retargeting. The recovered angles are compared with the known rig parameters (round trip). This
   validates the pipeline; it is not a human reference.
3. ``RevisionLoop`` runs on the retargeted shrug (shoulder rise).
4. Timings: decode, pose estimation, analysis, retarget, solve and render, on this machine.

Writes ``<package>/natural_pose/reference_motion/evidence/``: ``comparison_<ref>.png``, ``roundtrip.png``,
``roundtrip.mp4`` and ``results.json``.
"""

from __future__ import annotations

import json
import platform
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageDraw

from ..phase2.pose_eval import on_bg
from .analyze import ReferenceMotionAnalyzer
from .critic import RevisionLoop, VisualAnimationCritic
from .importer import ReferenceMotionImporter, encode_video
from .retarget import ReferenceMotionRetargeter
from .store import CharacterReferenceSettings, MotionReferenceStore, default_root
from .track import J, LandmarkTrack, synthetic_reference

BONES = [("shoulder_r", "shoulder_l"), ("shoulder_r", "elbow_r"), ("elbow_r", "wrist_r"), ("wrist_r", "index_r"),
         ("shoulder_l", "elbow_l"), ("elbow_l", "wrist_l"), ("wrist_l", "index_l"), ("hip_r", "hip_l"),
         ("shoulder_r", "hip_r"), ("shoulder_l", "hip_l"), ("hip_r", "knee_r"), ("knee_r", "ankle_r"),
         ("hip_l", "knee_l"), ("knee_l", "ankle_l"), ("ear_r", "ear_l")]
REFS = ("pointing", "shrug", "walking")


def draw_skeleton(tr: LandmarkTrack, i: int, size: tuple[int, int] = (360, 520), label: str = "",
                  highlight: list[str] | None = None) -> Image.Image:
    P = tr.data[i, :, :2]
    ys, xs = P[:, 1], P[:, 0]
    s = 0.86 * min(size[0] / max(xs.max() - xs.min(), 1), size[1] / max(ys.max() - ys.min(), 1))
    s = min(s, 0.86 * size[1] / max(ys.max() - ys.min(), 1))
    ox, oy = size[0] / 2 - s * (xs.max() + xs.min()) / 2, size[1] / 2 - s * (ys.max() + ys.min()) / 2
    im = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(im)
    T = lambda n: (ox + s * P[J[n], 0], oy + s * P[J[n], 1])
    hl = set(highlight or [])
    for a, b in BONES:
        seg = {"elbow_r": "upper_arm_r", "elbow_l": "upper_arm_l", "wrist_r": "forearm_r", "wrist_l": "forearm_l",
               "knee_r": "thigh_r", "knee_l": "thigh_l", "ankle_r": "shin_r", "ankle_l": "shin_l"}.get(b)
        col = (200, 0, 0) if seg in hl else (30, 30, 30)
        d.line([T(a), T(b)], fill=col, width=4)
    n = T("nose")
    d.ellipse([n[0] - 14, n[1] - 14, n[0] + 14, n[1] + 14], outline=(30, 30, 30), width=3)
    if label:
        d.text((6, 6), label, fill=(0, 0, 160))
    return im


def _fit(im: Image.Image, h: int) -> Image.Image:
    return im.resize((max(1, int(im.width * h / im.height)), h), Image.LANCZOS)


def _row(ims: list[Image.Image], h: int = 420, title: str = "") -> Image.Image:
    ims = [_fit(i, h) for i in ims]
    out = Image.new("RGB", (sum(i.width for i in ims) + 8 * len(ims), h + 22), "white")
    d = ImageDraw.Draw(out)
    d.text((4, 4), title, fill=(0, 0, 0))
    x = 0
    for i in ims:
        out.paste(i, (x, 22))
        x += i.width + 8
    return out


def _stack(ims: list[Image.Image]) -> Image.Image:
    W = max(i.width for i in ims)
    out = Image.new("RGB", (W, sum(i.height for i in ims)), "white")
    y = 0
    for i in ims:
        out.paste(i, (0, y))
        y += i.height
    return out


def _body_box(r: dict[str, Any]) -> tuple[int, int, int, int]:
    a = r["image"][..., 3] > 0
    ys, xs = np.nonzero(a)
    return (max(0, xs.min() - 20), max(0, ys.min() - 20), xs.max() + 20, ys.max() + 20)


def _silhouette_overlay(r: dict[str, Any], tr: LandmarkTrack, i: int, eng) -> Image.Image:
    """The rig silhouette (grey) with the reference skeleton scaled to the rig's shoulder-hip length (red)."""
    box = _body_box(r)
    a = (r["image"][..., 3] > 0)[box[1]:box[3], box[0]:box[2]]
    im = Image.fromarray(np.where(a[..., None], np.array([200, 200, 200], np.uint8), np.uint8(255)).astype(np.uint8))
    J_ = r["solve"]["joints_posed"]
    pad = r["pad"]
    rs = (np.array(J_["upper_arm_r"]) + np.array(J_["upper_arm_l"])) / 2
    rh = (np.array(J_["thigh_r"]) + np.array(J_["thigh_l"])) / 2
    P = tr.data[i, :, :2]
    ps = (P[J["shoulder_r"]] + P[J["shoulder_l"]]) / 2
    ph = (P[J["hip_r"]] + P[J["hip_l"]]) / 2
    s = np.linalg.norm(rs - rh) / max(np.linalg.norm(ps - ph), 1e-6)
    off = rs + pad - np.array(box[:2]) - s * ps
    d = ImageDraw.Draw(im)
    T = lambda n: tuple(off + s * P[J[n]])
    for a_, b_ in BONES:
        d.line([T(a_), T(b_)], fill=(200, 0, 0), width=3)
    return im


def _key_frames(analysis: dict[str, Any], tr: LandmarkTrack, n: int = 4) -> list[float]:
    holds = analysis["timing"]["holds"]
    ts = [0.0]
    mid = [0.5 * (h[0] + h[1]) for h in holds if h[0] > 0.05 and h[1] < tr.duration - 0.05]
    ts += mid[:2] if mid else [tr.duration * 0.4]
    ts.append(tr.duration * 0.25)
    ts = sorted(set(round(t, 3) for t in ts))[:n]
    return ts


def reference_comparisons(eng, vis, pkg: Path, store: MotionReferenceStore, out: Path, progress) -> dict[str, Any]:
    from ..artistic.transitions import acting_test_keys  # noqa: PLC0415
    from ..performance import scenarios, shoulders  # noqa: PLC0415
    from ..performance.director import Performance, PerformanceDirector  # noqa: PLC0415

    R = ReferenceMotionRetargeter()
    A = ReferenceMotionAnalyzer()
    settings = CharacterReferenceSettings(pkg)
    res: dict[str, Any] = {}
    neutral = PerformanceDirector(eng, vis, scenarios.acting_upgrade()).solve(0.0, render=True)
    nref = shoulders.measure(neutral["image"], neutral["solve"]["joints_posed"], neutral["pad"])
    t_shrug = next(k.t for k in acting_test_keys() if k.label == "shrug") + 0.3
    t_point = next(k.t for k in acting_test_keys() if k.label == "point") + 0.3
    originals = {"pointing": (scenarios.acting_upgrade(), t_point, "3.7 hand-authored pointing (acting test)"),
                 "shrug": (scenarios.acting_upgrade(shrug="expressive"), t_shrug, "3.7 hand-authored SHRUG_EXPRESSIVE"),
                 "walking": (None, None, "no front-view walk is authored: the stride needs a side view")}
    for name in REFS:
        t0 = time.perf_counter()
        ref_id = f"synthetic_{name}"
        if store.versions(ref_id):
            e = store.get(ref_id)
            tr, an, meta = e["track"], e["analysis"], {**e["meta"], **e["meta"].get("tags", {})}
            meta = {**tr.meta, **meta}
        else:
            tr = synthetic_reference(name)
            an = A.analyze(tr)
            store.add(ref_id, tr, tr.meta, an)
            meta = tr.meta
        t_an = time.perf_counter() - t0
        st = settings.get(ref_id)
        t1 = time.perf_counter()
        rt = R.retarget(an, meta, st["intensity"], st["timing_scale"])
        d = PerformanceDirector(eng, vis, Performance.from_json(rt["performance"]))
        err = R.compare(d, rt["target_curves"], tr.fps, st["timing_scale"])
        t_rt = time.perf_counter() - t1
        rows, crit = [], []
        hl = an.get("depth_segments", [])
        for tk in _key_frames(an, tr):
            i = min(tr.frames - 1, int(round(tk * tr.fps)))
            r = d.solve(tk * st["timing_scale"], render=True)
            c = VisualAnimationCritic().critique({"eng": eng, "result": r, "params": d.pose_at(tk)[2]["params"],
                                                  "shoulder_reference": nref, "shrug": name == "shrug",
                                                  "mode": "review"})
            crit.append({"t": tk, "defects": [(x["region"], x["issue"], x["severity"]) for x in c["defects"]]})
            hands = "/".join(f"{s}:{r['hands'][s]['pose']}{'*' if r['hands'][s]['pending_review'] else ''}" for s in "rl")
            tiles = [draw_skeleton(tr, i, label=f"reference t={tk:.2f}s (synthetic)", highlight=hl),
                     on_bg(r["image"]).crop(_body_box(r)),
                     _silhouette_overlay(r, tr, i, eng)]
            ttl = f"{name} t={tk:.2f}s | reference-guided render (hands {hands}; * pending) | rig silhouette + reference"
            orig, ot, olabel = originals[name]
            if orig is not None and tk == _key_frames(an, tr)[-1]:
                ro = PerformanceDirector(eng, vis, orig).solve(ot, render=True)
                tiles.append(on_bg(ro["image"]).crop(_body_box(ro)))
                ttl += f" | {olabel}"
            rows.append(_row(tiles, title=ttl))
        _stack(rows).save(out / f"comparison_{name}.png")
        settings.save_retarget(ref_id, {"settings": st, "feasibility": rt["feasibility"], "errors": err,
                                        "performance": rt["performance"]})
        res[name] = {"ref_id": ref_id, "keys": rt["keys"], "feasibility": rt["feasibility"], "errors": err,
                     "critic": crit, "depth_segments": hl,
                     "timing_s": {"analysis": round(t_an, 3), "retarget_and_compare": round(t_rt, 3)},
                     "accents": {k: an["accents"].get(k) for k in ("arm_r_abduction", "clavicle_r", "head")},
                     "holds": an["timing"]["holds"], "head_lead_s": an.get("head_lead_s"),
                     "coordination": an.get("coordination")}
        progress(f"reference {name}: {rt['feasibility']['class']}, {rt['keys']} keys")
    return res


def _character_id(pkg: Path) -> str:
    """The character's id from its Phase 1 project (Phase 3.8: the round trip of a new character is stored under its
    own name). Projects made before character ids existed (id "character") keep the original name."""
    import json  # noqa: PLC0415

    try:
        meta = json.loads((Path(pkg) / "metadata.json").read_text(encoding="utf-8"))
        p1 = Path(pkg).parent / ((meta.get("phase1_project") or {}).get("path_name") or "character_project")
        cid = json.loads((p1 / "metadata.json").read_text(encoding="utf-8")).get("character_id")
    except (OSError, ValueError):
        cid = None
    return "schoolboy" if cid in (None, "", "character") else str(cid)


def roundtrip(eng, vis, pkg: Path, store: MotionReferenceStore, out: Path, progress, quick: bool = False) -> dict[str, Any]:
    """AutoRig render of the package's character -> MP4 -> ffmpeg -> MediaPipe -> analysis -> retarget; compared with
    the known parameters."""
    from ..performance import scenarios  # noqa: PLC0415
    from ..performance.director import PerformanceDirector  # noqa: PLC0415
    from .pose import MediaPipePoseProvider, mediapipe_available  # noqa: PLC0415

    why = mediapipe_available()
    if why:
        return {"status": "NOT_RUN", "reason": why}
    perf = scenarios.acting_upgrade()
    d = PerformanceDirector(eng, vis, perf)
    fps = 12.0
    seg = (2.4, 6.0) if not quick else (2.6, 3.8)
    ts = [seg[0] + k / fps for k in range(int((seg[1] - seg[0]) * fps) + 1)]
    frames, truth = [], []
    t0 = time.perf_counter()
    for t in ts:
        r = d.solve(t, render=True)
        img = np.asarray(on_bg(r["image"]))
        frames.append(img)
        truth.append(d.pose_at(t)[2]["params"])
    t_render = time.perf_counter() - t0
    H, W = frames[0].shape[:2]
    sc = 720 / H
    small = [np.asarray(Image.fromarray(f).resize((int(W * sc) // 2 * 2, 720), Image.LANCZOS)) for f in frames]
    mp4 = encode_video(small, out / "roundtrip.mp4", fps)
    cid = _character_id(pkg)
    rname = f"{cid}_roundtrip"
    meta = {"name": rname, "category": "gesture", "gesture": "pointing+shrug",
            "hand_actions": {"r": "pointing"},
            "provenance": {"kind": "autorig_render", "source": f"AutoRig render of the user's {cid} character "
                           "(Phase 3.7 acting test, t 2.4-6.0 s)", "usage_permission": "the user's own character, "
                           "rendered by this project"}}
    t1 = time.perf_counter()
    media = ReferenceMotionImporter().load(mp4, meta)
    t_dec = time.perf_counter() - t1
    prov = MediaPipePoseProvider()
    tr = prov.estimate(media.frames, media.fps)
    tr.meta = {**tr.meta, **meta}
    q = ReferenceMotionImporter.quality(tr)
    tr.meta["quality"] = q
    t2 = time.perf_counter()
    an = ReferenceMotionAnalyzer().analyze(tr)
    t_an = time.perf_counter() - t2
    if not store.versions(rname):
        store.add(rname, tr, {**meta, "quality": q}, an)
    # compare recovered changes with the known parameter changes (both relative to the first frame)
    ch = {k: np.asarray(v) for k, v in an["channels"].items()}
    n = min(len(truth), len(next(iter(ch.values()))))
    comp = {}
    for c in ("arm_r_abduction", "arm_r_flexion", "arm_l_abduction", "clavicle_r", "clavicle_l", "head",
              "line_of_action"):
        gt = np.array([p.get(c, 0.0) for p in truth[:n]])
        est = ch[c][:n]
        e = (est - est[0]) - (gt - gt[0])
        comp[c] = {"rms_deg": round(float(np.sqrt(np.mean(e ** 2))), 2), "max_deg": round(float(np.abs(e).max()), 2),
                   "truth_range": round(float(np.ptp(gt)), 2), "est_range": round(float(np.ptp(est)), 2)}
    # sheet: frames with detected landmarks
    tiles = []
    for k in range(0, len(small), max(1, len(small) // 6)):
        im = Image.fromarray(small[k]).copy()
        dr = ImageDraw.Draw(im)
        P = tr.data[k]
        for a_, b_ in BONES:
            if P[J[a_], 3] > 0.3 and P[J[b_], 3] > 0.3:
                dr.line([tuple(P[J[a_], :2]), tuple(P[J[b_], :2])], fill=(220, 0, 0), width=3)
        dr.text((6, 6), f"t={ts[k]:.2f}s MediaPipe on an AutoRig render", fill=(0, 0, 160))
        tiles.append(im)
    _row(tiles[:6], h=420, title=f"Round trip: MediaPipe Pose (local CPU) on the rendered {cid}; red = detected "
                                 "skeleton").save(out / "roundtrip.png")
    return {"status": "RUN", "frames": len(frames), "fps": fps, "segment": list(seg), "quality": q,
            "errors_vs_known_params": comp, "provider": prov.info(),
            "timing_s": {"render_per_frame": round(t_render / len(frames), 3), "decode_total": round(t_dec, 3),
                         "pose_per_frame": prov.timing.get("per_frame_s"), "analysis": round(t_an, 3)},
            "note": "a cartoon render, not a human: errors show the provider/analysis chain on this character's "
                    "proportions; they are not human-reference accuracy"}


def reference_evidence(pkg: str | Path, quick: bool = False, progress: Callable[[str], Any] = print) -> dict[str, Any]:
    from ..garmentrig.correctives import CorrectiveArtworkStore, CorrectiveRuntime  # noqa: PLC0415
    from ..motion.pipeline import engine_for_package  # noqa: PLC0415
    from ..performance import shoulders  # noqa: PLC0415
    from ..performance.director import Performance, PerformanceDirector  # noqa: PLC0415

    pkg = Path(pkg)
    out = pkg / "natural_pose" / "reference_motion" / "evidence"
    out.mkdir(parents=True, exist_ok=True)
    store = MotionReferenceStore(default_root(pkg))
    eng, vis = engine_for_package(pkg)
    eng.correctives = CorrectiveRuntime(eng, CorrectiveArtworkStore(pkg))
    res: dict[str, Any] = {"machine": {"platform": platform.platform(), "processor": platform.processor() or
                                       platform.machine(), "python": platform.python_version()},
                           "library": str(store.root)}
    res["references"] = reference_comparisons(eng, vis, pkg, store, out, progress)
    res["roundtrip"] = roundtrip(eng, vis, pkg, store, out, progress, quick)
    progress(f"round trip: {res['roundtrip'].get('status')}")
    # revision loop on the retargeted shrug
    sh = CharacterReferenceSettings(pkg)
    rt = json.loads((sh.dir / "synthetic_shrug" / "retarget.json").read_text())
    an = store.get("synthetic_shrug")["analysis"]
    hold = max(an["timing"]["holds"], key=lambda h: h[1] - h[0])
    t = 0.5 * (hold[0] + hold[1])
    from ..performance import scenarios  # noqa: PLC0415

    neutral = PerformanceDirector(eng, vis, scenarios.acting_upgrade()).solve(0.0, render=True)
    nref = shoulders.measure(neutral["image"], neutral["solve"]["joints_posed"], neutral["pad"])
    loop = RevisionLoop(eng, vis).run(rt["performance"], t, {"shoulder_reference": nref, "shrug": True})
    res["revision_loop"] = {k: loop[k] for k in ("status", "attempts", "changed", "approved", "original_untouched")}
    res["revision_loop"]["initial_defects"] = [(d["region"], d["issue"], d["severity"]) for d in loop["initial"]["defects"]]
    res["revision_loop"]["final_defects"] = [(d["region"], d["issue"], d["severity"]) for d in loop["final"]["defects"]]
    if loop["candidate"]:
        d0 = PerformanceDirector(eng, vis, Performance.from_json(rt["performance"])).solve(t, render=True)
        d1 = PerformanceDirector(eng, vis, Performance.from_json(loop["candidate"])).solve(t, render=True)
        _row([on_bg(d0["image"]).crop(_body_box(d0)), on_bg(d1["image"]).crop(_body_box(d1))],
             title="Revision loop on the retargeted shrug: before | proposed candidate (needs approval; nothing "
                   "applied)").save(out / "revision_loop.png")
    (out / "results.json").write_text(json.dumps(res, indent=1, default=float) + "\n", encoding="utf-8")
    return res


__all__ = ["reference_evidence", "draw_skeleton", "roundtrip", "reference_comparisons"]
