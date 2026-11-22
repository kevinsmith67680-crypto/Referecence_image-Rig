"""FastAPI service for the AutoRig editor. One process serves one character package; local-first.

The backend owns the project state. The browser sends operations; undo, redo, versions and every file
live on disk in ``<package>/editor/``. Images are generated on demand from the operation log, so nothing
in the package is overwritten.
"""

from __future__ import annotations

import base64
import copy
import re
import threading
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .artwork import ArtworkModel, png_bytes
from .export import export_package, quality_gates, rejected_ids
from .posesvc import PoseError, PoseService, bone_warnings
from .project import EditorProject, OpError
from .readiness import face_panel, live_panel

NO_STORE = {"Cache-Control": "no-store"}


def jsonable(obj: Any) -> Any:
    """numpy scalars/arrays and tuples -> plain JSON types."""
    import numpy as np
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.generic):
        return obj.item()
    return obj


class Session:
    def __init__(self, pkg: str | Path):
        self.pkg = Path(pkg)
        self.project = EditorProject(self.pkg)
        self.art = ArtworkModel(self.pkg)
        self.pose = PoseService(self.pkg, self.art)
        self.lock = threading.RLock()

    def masks(self):
        return self.art.masks(self.project.state()["mask_ops"])

    def payload(self) -> dict[str, Any]:
        st = self.project.state()
        masks = self.masks()
        val = self.art.validate_masks(masks)
        return {"project": self.project.summary(), "state": st, "mask_validation": val,
                "bone_warnings": bone_warnings(st, self.project.base["bones"], masks, self.art.footprint),
                "base_bones": self.project.base["bones"]}


def create_app(pkg: str | Path, static_dir: str | Path | None = None) -> FastAPI:
    S = Session(pkg)
    app = FastAPI(title="AutoRig Studio editor", version="1.0.0")
    app.state.session = S

    def png(arr) -> Response:
        return Response(png_bytes(arr), media_type="image/png", headers=NO_STORE)

    @app.get("/api/project")
    def project():
        with S.lock:
            return jsonable(S.payload())

    @app.post("/api/op")
    def op(body: dict = Body(...)):
        with S.lock:
            try:
                S.project.submit(body)
            except (OpError, KeyError, TypeError, ValueError) as exc:
                raise HTTPException(400, str(exc))
            return jsonable(S.payload())

    @app.post("/api/undo")
    def undo():
        with S.lock:
            S.project.undo()
            return jsonable(S.payload())

    @app.post("/api/redo")
    def redo():
        with S.lock:
            S.project.redo()
            return jsonable(S.payload())

    @app.get("/api/versions")
    def versions():
        return S.project.data["versions"]

    @app.post("/api/versions")
    def save_version(body: dict = Body(default={})):
        with S.lock:
            return S.project.save_version(body.get("label", ""))

    @app.post("/api/versions/{n}/restore")
    def restore(n: int):
        with S.lock:
            S.project.restore_version(n)
            return jsonable(S.payload())

    @app.get("/api/source.png")
    def source():
        return FileResponse(S.pkg / "source" / "original.png", headers=NO_STORE)

    @app.get("/api/layer/{lid}.png")
    def layer(lid: str):
        with S.lock:
            st = S.project.state()
            L = next((l for l in st["layers"] if l["id"] == lid), None)
            if L is None:
                raise HTTPException(404, lid)
            if L["kind"] == "switch":
                return FileResponse(S.pkg / L["file"], headers=NO_STORE)
            masks = S.masks()
            src = L.get("duplicate_of") or lid
            return png(S.art.layer_rgba(src, masks, rejected_ids(st), L.get("edge_feather", 0.0)))

    @app.get("/api/mask/{lid}.png")
    def mask(lid: str, base: bool = False):
        with S.lock:
            masks = S.art.base_masks if base else S.masks()
            if lid not in masks:
                raise HTTPException(404, lid)
            import numpy as np
            m = masks[lid]
            out = np.zeros(m.shape + (4,), np.uint8)
            out[m] = (255, 255, 255, 255)
            return png(out)

    @app.get("/api/compare/{kind}.png")
    def compare(kind: str):
        with S.lock:
            st = S.project.state()
            masks = S.masks()
            order = [L["id"] for L in st["layers"] if L["kind"] == "bitmap" and L["visible"] and L["id"] in masks]
            rej = rejected_ids(st)
            if kind == "diff":
                return png(S.art.diff_heatmap(order, masks, rej))
            if kind == "provenance":
                return png(S.art.provenance_overlay(masks, rej))
            if kind == "neutral":
                return png(S.art.composite(order, masks, rej))
            raise HTTPException(404, kind)

    @app.get("/api/compare/metrics")
    def metrics():
        with S.lock:
            st = S.project.state()
            masks = S.masks()
            order = [L["id"] for L in st["layers"] if L["kind"] == "bitmap" and L["visible"] and L["id"] in masks]
            return jsonable({"fidelity": S.art.fidelity(order, masks, rejected_ids(st)),
                             "mask_validation": S.art.validate_masks(masks)})

    @app.get("/api/presets")
    def presets():
        return PoseService.motion_presets()

    def b64(arr) -> str:
        return "data:image/png;base64," + base64.b64encode(png_bytes(arr)).decode()

    @app.post("/api/pose")
    def pose(body: dict = Body(...)):
        with S.lock:
            st = S.project.state()
            if body.get("preset", "relaxed_standing") != "rest":
                try:
                    r = S.pose.motion(st, S.masks(), rejected_ids(st), body)
                except PoseError as exc:
                    raise HTTPException(400, str(exc))
                if "image" in r:
                    r["image"] = b64(r["image"])
                if "before" in r:
                    r["before"] = b64(r["before"])
                if "corrective_before" in r:
                    r["corrective_before"] = b64(r["corrective_before"])
                if "diagnostics" in r:
                    r["diagnostics"] = {k: b64(v) for k, v in r["diagnostics"].items()}
                return JSONResponse(jsonable(r))
            try:
                r = S.pose.render(st, S.masks(), rejected_ids(st), body.get("preset", "relaxed_standing"),
                                  body.get("overrides", {}), boundaries=bool(body.get("boundaries")))
            except PoseError as exc:
                raise HTTPException(400, str(exc))
            out = {k: v for k, v in r.items() if k not in ("image", "boundaries")}
            out["image"] = "data:image/png;base64," + base64.b64encode(png_bytes(r["image"])).decode()
            if "boundaries" in r:
                out["boundaries"] = "data:image/png;base64," + base64.b64encode(png_bytes(r["boundaries"])).decode()
            return JSONResponse(jsonable(out))

    # -- animated gait cycles: full-artwork frames rendered in a background job ------------------------------
    jobs: dict[str, dict[str, Any]] = {}

    @app.post("/api/pose/cycle")
    def cycle_start(body: dict = Body(...)):
        import uuid

        from ..motion.pipeline import render_cycle, save_animation
        from ..motion.planner import SPEC_BY_NAME

        name = body.get("preset", "running")
        if name not in SPEC_BY_NAME or SPEC_BY_NAME[name].kind != "gait":
            raise HTTPException(400, f"{name} is not a gait cycle")
        n = int(body.get("frames", 24))
        if not 4 <= n <= 48:
            raise HTTPException(400, "frames must be 4..48")
        jid = uuid.uuid4().hex[:12]
        job = {"done": False, "progress": 0, "total": n, "frames": [], "error": None}
        jobs[jid] = job
        while len(jobs) > 6:
            jobs.pop(next(iter(jobs)))

        def work():
            try:
                with S.lock:
                    st = S.project.state()
                    masks = S.masks()
                    eng = S.pose.motion_engine(st, masks, rejected_ids(st))
                    vis = [L["id"] for L in st["layers"] if L["visible"] and L["kind"] == "bitmap" and L["id"] in masks]
                    out_dir = S.project.dir / "cycles"
                res = render_cycle(eng, name, n, vis, float(body.get("speed", 1.0)), float(body.get("exaggeration", 1.0)),
                                   bool(body.get("allow_pending")), bool(body.get("fluid", True)),
                                   progress=lambda i, t: job.update(progress=i), lock=S.lock,
                                   ik=st.get("ik"), switches=st.get("switches"))
                job["frames"] = [png_bytes(f) for f in res["frames"]]
                out_dir.mkdir(parents=True, exist_ok=True)
                save_animation(res["frames"], res["duration_ms"], out_dir / f"{jid}.webp", scale=0.5)
                save_animation(res["frames"], res["duration_ms"], out_dir / f"{jid}.gif", scale=0.4)
                job.update(box=res["box"], pad=res["pad"], duration_ms=res["duration_ms"],
                           files={"webp": str(out_dir / f"{jid}.webp"), "gif": str(out_dir / f"{jid}.gif")})
            except Exception as exc:  # reported to the UI
                job["error"] = str(exc)
            finally:
                job["done"] = True

        threading.Thread(target=work, daemon=True).start()
        return {"id": jid, "frames": n}

    @app.get("/api/pose/cycle/{jid}")
    def cycle_status(jid: str):
        job = jobs.get(jid)
        if job is None:
            raise HTTPException(404, jid)
        return jsonable({k: v for k, v in job.items() if k not in ("frames", "files")} | {"ready": len(job["frames"])})

    @app.get("/api/pose/cycle/{jid}/frame/{i}.png")
    def cycle_frame(jid: str, i: int):
        job = jobs.get(jid)
        if job is None or not 0 <= i < len(job["frames"]):
            raise HTTPException(404, f"{jid}/{i}")
        return Response(job["frames"][i], media_type="image/png", headers=NO_STORE)

    @app.get("/api/pose/cycle/{jid}/animation.{ext}")
    def cycle_animation(jid: str, ext: str):
        job = jobs.get(jid)
        if job is None or ext not in ("webp", "gif") or not job.get("files"):
            raise HTTPException(404, jid)
        return FileResponse(job["files"][ext], media_type=f"image/{ext}",
                            filename=f"{jid}.{ext}", headers=NO_STORE)

    # -- Phase 3.2: IK, switches, rig controls, live actions --------------------------------------------------
    def _engine():
        st = S.project.state()
        return st, S.pose.motion_engine(st, S.masks(), rejected_ids(st))

    @app.post("/api/ik/match")
    def ik_match(body: dict = Body(...)):
        """FK<->IK matching for one limb in the current pose; returns the new limb state (submit it as ik.set)."""
        with S.lock:
            st, eng = _engine()
            limb, to = body.get("limb"), body.get("to")
            if limb not in ("arm_r", "arm_l", "leg_r", "leg_l") or to not in ("ik", "fk"):
                raise HTTPException(400, "limb must be arm_r/arm_l/leg_r/leg_l and to must be ik or fk")
            preset = body.get("preset", "relaxed_standing")
            if preset == "rest":
                preset = "relaxed_standing"
            ik = body["ik"] if body.get("ik") is not None else st["ik"]
            r = eng.match_ik(limb, to, preset, body.get("phase"), ik=ik, speed=float(body.get("speed", 1.0)),
                             exaggeration=float(body.get("exaggeration", 1.0)),
                             allow_pending=bool(body.get("allow_pending")), fluid=bool(body.get("fluid", True)))
            return jsonable(r)

    @app.get("/api/switches")
    def switches_info():
        with S.lock:
            st, eng = _engine()
            ctl = eng.switch_controller(st["switches"])
            from ..motion.handselect import ACTIONS
            return jsonable({"groups": [g.to_json() for g in ctl.groups.values()], "state": ctl.state,
                             "hand_library": eng.hand_lib.to_json(eng.profile.midline_x),
                             "corrections": eng.corr_switch.to_json(), "selection_actions": sorted(ACTIONS)})

    @app.get("/api/switch/thumb/{group}/{variant}.png")
    def switch_thumb(group: str, variant: str):
        with S.lock:
            st = S.project.state()
            arr = S.pose.thumbnail(st, S.masks(), rejected_ids(st), group, variant)
        if arr is None:
            raise HTTPException(404, "no drawing for this variant")
        return png(arr)

    @app.get("/api/rig/controls")
    def rig_controls():
        from ..motion.rigctl import build_rig_controls, validate

        with S.lock:
            st, eng = _engine()
            doc = build_rig_controls(eng, st["ik"], st["switches"])
            return jsonable({"controls": doc, "validation_errors": validate(doc)})

    @app.post("/api/live/action")
    def live_action(body: dict = Body(...)):
        """Semantic live action -> editor operations (returned, not submitted; the UI submits them so undo works).
        No webcam or device input is read here."""
        from ..motion.rigctl import LIVE_ACTIONS, action_ops

        with S.lock:
            st, eng = _engine()
            act = body.get("action")
            if act not in LIVE_ACTIONS:
                raise HTTPException(400, f"unknown action {act}")
            ops = action_ops(act, eng.sk, float(body.get("value", 1.0)), float(body.get("t", 0.0)), st["ik"])
            return jsonable({"action": act, "spec": LIVE_ACTIONS[act], "ops": ops})

    @app.get("/api/live/actions")
    def live_actions():
        from ..motion.rigctl import LIVE_ACTIONS

        return LIVE_ACTIONS

    # -- Hand Pose Store / Hand Library ---------------------------------------------------------------------
    @app.get("/api/garments")
    def garments_info():
        with S.lock:
            st = S.project.state()
            return jsonable(S.pose.garments(st, S.masks(), rejected_ids(st)))

    @app.get("/api/garments/overlay.png")
    def garments_overlay(kind: str = "parts", layer: str | None = None, bone: str | None = None):
        if kind not in ("parts", "influence", "mesh"):
            raise HTTPException(400, "kind must be parts|influence|mesh")
        with S.lock:
            st = S.project.state()
            return png(S.pose.garment_overlay(st, S.masks(), rejected_ids(st), kind, layer, bone))

    @app.post("/api/garments/explain")
    def garments_explain(body: dict = Body(...)):
        with S.lock:
            st = S.project.state()
            try:
                return jsonable(S.pose.garment_explain(st, S.masks(), rejected_ids(st), str(body.get("layer")),
                                                       float(body.get("x", 0)), float(body.get("y", 0))))
            except PoseError as exc:
                raise HTTPException(400, str(exc))

    @app.get("/api/garments/compare.png")
    def garments_compare(preset: str = "relaxed_standing"):
        with S.lock:
            st = S.project.state()
            return png(S.pose.garment_compare(st, S.masks(), rejected_ids(st), preset))

    # -- Phase 3.5 Artistic Review -----------------------------------------------------------------------------
    @app.get("/api/artistic")
    def artistic_info():
        with S.lock:
            return jsonable(S.pose.artistic_info(S.project.state()))

    @app.post("/api/artistic/candidates")
    def artistic_candidates(body: dict = Body(...)):
        import numpy as np  # noqa: PLC0415

        with S.lock:
            st = S.project.state()
            try:
                r = S.pose.artistic_candidates(st, S.masks(), rejected_ids(st), str(body.get("action")))
            except PoseError as exc:
                raise HTTPException(400, str(exc))
        for c in r["candidates"]:
            img = c["image"]
            ys, xs = np.nonzero(img[..., 3] > 0)
            box = (max(0, ys.min() - 8), ys.max() + 9, max(0, xs.min() - 8), xs.max() + 9)
            c["image"] = b64(img[box[0]:box[1], box[2]:box[3]])
            sil = c["silhouette"][box[0]:box[1], box[2]:box[3]]
            c["silhouette"] = b64(np.dstack([sil, sil, sil, np.full(sil.shape, 255, np.uint8)]))
        return JSONResponse(jsonable(r))

    @app.post("/api/artistic/feedback/parse")
    def artistic_feedback_parse(body: dict = Body(...)):
        from ..artistic.feedback import parse_feedback  # noqa: PLC0415

        return jsonable(parse_feedback(str(body.get("text", "")), str(body.get("action", ""))))

    # -- Phase 3.6 Performance timeline ---------------------------------------------------------------------
    perf_cache: dict[str, Any] = {}

    def _director(st, doc=None):
        import json as _json  # noqa: PLC0415

        from ..performance.director import Performance, PerformanceDirector  # noqa: PLC0415

        doc = doc if doc is not None else (st.get("performance") or {}).get("doc")
        if doc is None:
            raise HTTPException(400, "no performance yet: load a scenario first")
        masks = S.masks()
        eng = S.pose.motion_engine(st, masks, rejected_ids(st))
        key = f"{id(eng)}:" + _json.dumps(doc, sort_keys=True, default=str)
        d = perf_cache.get(key)
        if d is None:
            vis = [L["id"] for L in st["layers"] if L["visible"] and L["kind"] == "bitmap" and L["id"] in masks]
            try:
                d = PerformanceDirector(eng, vis, Performance.from_json(doc))
            except (ValueError, KeyError, TypeError) as exc:
                raise HTTPException(400, f"invalid performance: {exc}")
            perf_cache.clear()
            perf_cache[key] = d
        return d

    @app.get("/api/performance")
    def performance_info():
        from ..performance.anticipation import RECIPES  # noqa: PLC0415
        from ..performance.curves import KINDS  # noqa: PLC0415
        from ..performance.export import compatibility, live_control_spec  # noqa: PLC0415
        from ..performance.gestures import GesturePerformanceLibrary  # noqa: PLC0415
        from ..performance.layers import LAYERS  # noqa: PLC0415
        from ..performance.overlap import DEFAULT_OFFSETS, GROUPS  # noqa: PLC0415
        from ..performance.resolver import ArtworkAndCorrectionResolver  # noqa: PLC0415
        from ..performance.secondary import DEFAULTS  # noqa: PLC0415
        from ..performance.styles import PROFILES  # noqa: PLC0415

        with S.lock:
            st = S.project.state()
            eng = S.pose.motion_engine(st, S.masks(), rejected_ids(st))
            lib = GesturePerformanceLibrary(ArtworkAndCorrectionResolver(eng))
            P = st.get("performance") or {}
            return jsonable({"doc": P.get("doc"), "library": sorted((P.get("library") or {}).keys()),
                             "gestures": {n: {"title": g.title, "tone": g.tone, "duration": g.duration,
                                              "sided": g.sided, "status": lib.status(n)}
                                          for n, g in lib.gestures.items()},
                             "styles": {k: v.to_json() for k, v in PROFILES.items()}, "curve_kinds": list(KINDS),
                             "recipes": sorted(RECIPES), "layers": list(LAYERS), "groups": list(GROUPS),
                             "default_offsets": DEFAULT_OFFSETS, "secondary_defaults": DEFAULTS,
                             "scenarios": ["acting_upgrade", "classroom", "secondary_demo"],
                             "live": live_control_spec(), "compatibility": compatibility(),
                             "note": "Pending hand drawings are previewed and labelled. Performances are approved only "
                                     "by you (approval.set on performance:<name>); nothing here is automatic."})

    @app.post("/api/performance/scenario")
    def performance_scenario(body: dict = Body(...)):
        from ..performance import scenarios  # noqa: PLC0415

        name = str(body.get("name"))
        with S.lock:
            st = S.project.state()
            eng = S.pose.motion_engine(st, S.masks(), rejected_ids(st))
            if name == "acting_upgrade":
                doc = scenarios.acting_upgrade()
            elif name == "classroom":
                doc = scenarios.classroom(eng)
            elif name == "secondary_demo":
                doc = scenarios.secondary_demo()
            else:
                raise HTTPException(400, f"unknown scenario {name!r}")
            return jsonable({"doc": doc.to_json()})

    @app.post("/api/performance/track")
    def performance_track(body: dict = Body(default={})):
        with S.lock:
            st = S.project.state()
            d = _director(st, body.get("doc"))
            fps = float(body.get("fps") or d.perf.fps)
            import time as _time  # noqa: PLC0415

            t0 = _time.perf_counter()
            tr = d.track(fps)
            dt = _time.perf_counter() - t0
            bones = [[b.name, b.parent] for b in d.eng.sk.bones]
            frames = [{"t": f["t"], "joints": f["joints"], "tails": f["tails"], "params": f["params"],
                       "hands": {s: {"pose": h["shown"], "status": h["status"]}
                                 for s, h in f["performance"]["hands"].items()},
                       "contacts": {s: c["weight"] for s, c in (f["performance"].get("contacts") or {}).items()},
                       "secondary": f["performance"]["secondary"],
                       "clips": [a["clip"] for a in f["performance"]["layers"]]} for f in tr]
            feet = {s: round(max((abs(frames[i]["joints"][f"foot_{s}"][0] - frames[i - 1]["joints"][f"foot_{s}"][0])
                                  for i in range(1, len(frames))), default=0.0), 3) for s in ("r", "l")}
            gest = [{"id": c.id, "gesture": c.gesture, "layer": c.layer, "start": c.start, "end": round(c.end, 4),
                     "side": c.side} for c in d.layers.clips]
            return jsonable({"fps": fps, "duration": d.duration, "frames": frames, "bones": bones,
                             "keys": [{"id": k.id, "t": k.t, "hold": k.hold, "label": k.label,
                                       "curve": k.curve.to_json(), "anticipation": k.anticipation,
                                       "overshoot": k.overshoot} for k in d.base.keys],
                             "clips": gest, "contacts": [c.to_json() for c in d.contacts.specs],
                             "markers": d.perf.hand_markers, "layers": d.layers.settings,
                             "secondary": d.secondary.report(), "gesture_status": {
                                 g["id"]: d.library.status(g["gesture"], g.get("side", "r")) for g in d.perf.gestures},
                             "max_foot_step_px": feet, "props": d.perf.props,
                             "timing": {"track_s_per_frame": round(dt / max(len(tr), 1), 4), **d.timing}})

    @app.get("/api/performance/frame.png")
    def performance_frame(t: float = 0.0):
        import numpy as np  # noqa: PLC0415

        from ..performance.seat import draw_props  # noqa: PLC0415

        with S.lock:
            st = S.project.state()
            d = _director(st)
            r = d.solve(float(t), render=True)
        img = r["image"]
        if d.desks:
            img = draw_props(img, r["pad"], d.desks, d.eng.profile.ground_y)
        ys, xs = np.nonzero(img[..., 3] > 0)
        img = img[max(0, ys.min() - 8):ys.max() + 9, max(0, xs.min() - 8):xs.max() + 9]
        return png(img)

    @app.post("/api/performance/render")
    def performance_render(body: dict = Body(default={})):
        import uuid  # noqa: PLC0415

        from ..motion.pipeline import save_animation  # noqa: PLC0415
        from ..performance.seat import draw_props  # noqa: PLC0415

        with S.lock:
            st = S.project.state()
            d = _director(st)
        fps = float(body.get("fps") or d.perf.fps)
        times = d.frames(fps)
        if len(times) > 160:
            raise HTTPException(400, "at most 160 frames per render job")
        jid = uuid.uuid4().hex[:12]
        job = {"done": False, "progress": 0, "total": len(times), "frames": [], "error": None}
        jobs[jid] = job
        while len(jobs) > 6:
            jobs.pop(next(iter(jobs)))

        def work():
            try:
                imgs = []
                for i, t in enumerate(times):
                    with S.lock:
                        r = d.solve(t, render=True)
                    img = r["image"]
                    if d.desks:
                        img = draw_props(img, r["pad"], d.desks, d.eng.profile.ground_y)
                    imgs.append(img)
                    job["frames"].append(png_bytes(img))
                    job["progress"] = i + 1
                out_dir = S.project.dir / "performances"
                out_dir.mkdir(parents=True, exist_ok=True)
                save_animation(imgs, int(1000 / fps), out_dir / f"{jid}.gif", scale=0.4)
                job["files"] = {"gif": str(out_dir / f"{jid}.gif")}
            except Exception as exc:  # reported to the UI
                job["error"] = str(exc)
            finally:
                job["done"] = True

        threading.Thread(target=work, daemon=True).start()
        return {"id": jid, "frames": len(times)}

    # -- Phase 3.4 Correctives tab ------------------------------------------------------------------------
    @app.get("/api/correctives")
    def correctives_info():
        with S.lock:
            return jsonable(S.pose.correctives(S.project.state()))

    @app.get("/api/correctives/image")
    def corrective_image(path: str):
        with S.lock:
            try:
                return png(S.pose.corrective_image(path))
            except PoseError as exc:
                raise HTTPException(404, str(exc))

    @app.post("/api/correctives/generate")
    def correctives_generate():
        with S.lock:
            st = S.project.state()
            return jsonable(S.pose.corrective_generate(st, S.masks(), rejected_ids(st)))

    @app.post("/api/correctives/replace")
    def correctives_replace(body: dict = Body(...)):
        import io  # noqa: PLC0415

        import numpy as np  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415

        data = str(body.get("png", ""))
        if data.startswith("data:"):
            data = data.split(",", 1)[1]
        try:
            rgba = np.asarray(Image.open(io.BytesIO(base64.b64decode(data))).convert("RGBA"))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"not a PNG image: {exc}")
        with S.lock:
            st = S.project.state()
            try:
                return jsonable(S.pose.corrective_replace(st, S.masks(), rejected_ids(st), str(body.get("asset")),
                                                          rgba, str(body.get("note", ""))))
            except PoseError as exc:
                raise HTTPException(400, str(exc))

    @app.get("/api/handstore")
    def handstore_info():
        with S.lock:
            st = S.project.state()
            return jsonable(S.pose.hand_library(st, S.masks(), rejected_ids(st)))

    def _b64(body: dict, key: str = "data_b64", limit: int = 64 * 1024 * 1024) -> bytes:
        raw = str(body.get(key) or "")
        if "," in raw[:80]:
            raw = raw.split(",", 1)[1]
        if len(raw) > limit * 4 // 3 + 16:
            raise HTTPException(413, "upload too large")
        try:
            return base64.b64decode(raw, validate=True)
        except ValueError as exc:
            raise HTTPException(400, "data_b64 is not base64") from exc

    @app.post("/api/handstore/import")
    def handstore_import(body: dict = Body(...)):
        """Import a hand pack (zip, base64). Validated in full; nothing is approved; files are stored with hashes."""
        from ..handstore import HandStore, PackError

        data = _b64(body)
        name = re.sub(r"[^A-Za-z0-9._ -]", "_", str(body.get("name") or "pack.zip"))[:96]
        with S.lock:
            try:
                rep = HandStore(S.pkg).import_pack(data, name)
            except PackError as exc:
                return JSONResponse({"status": "rejected", "errors": exc.errors}, status_code=422)
            return jsonable(rep)

    @app.post("/api/handstore/replace")
    def handstore_replace(body: dict = Body(...)):
        """A new version of a stored drawing (PNG with alpha, base64). Earlier versions stay; the new one needs review."""
        from ..handstore import HandStore, PackError

        data = _b64(body, limit=16 * 1024 * 1024)
        with S.lock:
            try:
                rep = HandStore(S.pkg).replace_png(str(body.get("uid")), data,
                                                   re.sub(r"[^A-Za-z0-9._ -]", "_", str(body.get("name") or "replacement.png"))[:96])
            except KeyError as exc:
                raise HTTPException(404, str(exc)) from exc
            except PackError as exc:
                return JSONResponse({"status": "rejected", "errors": exc.errors}, status_code=422)
            return jsonable(rep)

    @app.get("/api/handstore/source/{lib}/{asset}.png")
    def handstore_source(lib: str, asset: str, v: int | None = None):
        from ..handstore import HandStore

        with S.lock:
            try:
                arr = HandStore(S.pkg).image(f"{lib}/{asset}", v)
            except KeyError as exc:
                raise HTTPException(404, str(exc)) from exc
        return png(arr)

    @app.get("/api/handstore/preview/{side}/{cid:path}.png")
    def handstore_preview(side: str, cid: str):
        with S.lock:
            st = S.project.state()
            arr = S.pose.hand_preview(st, S.masks(), rejected_ids(st), side, cid)
        if arr is None:
            raise HTTPException(404, "no such hand candidate")
        return png(arr)

    @app.get("/api/face")
    def face():
        with S.lock:
            return jsonable(face_panel(S.pkg, S.project.state(), S.masks()))

    @app.get("/api/live")
    def live():
        with S.lock:
            return jsonable(live_panel(S.pkg, S.project.state(), S.masks()))

    @app.get("/api/gates")
    def gates():
        with S.lock:
            return jsonable(quality_gates(S.project, S.art))

    @app.post("/api/export")
    def export(body: dict = Body(default={})):
        with S.lock:
            name = str(body.get("name") or "").strip()
            if name and not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", name) or name in (".", ".."):
                raise HTTPException(400, "export name: letters, digits, '.', '_' or '-' only")
            out = S.project.dir / "exports" / name if name else None  # exports stay inside the package
            try:
                r = export_package(S.project, S.art, out)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            return jsonable({"out": r["out"], "production_ready": r["manifest"]["production_ready"],
                    "overall": r["gates"]["overall"], "gates": r["gates"]["gates"],
                    "validation_errors": r["validation_errors"], "layers": len(r["manifest"]["layers"]),
                    "vectors": len(r["manifest"]["vectors"]), "bones": len(r["manifest"]["bones"])})

    # -- Phase 3.7: hand inspector, hand metadata, reference motion ------------------------------------------------
    def _vis(st) -> list[str]:
        masks = S.masks()
        return [L["id"] for L in st["layers"] if L["visible"] and L["kind"] == "bitmap" and L["id"] in masks]

    def _hand_ctx(st, body: dict):
        """A solved frame for the inspector: the current pose preset or a performance time."""
        review = str(body.get("mode", "review")) == "review"
        src = str(body.get("source", "pose"))
        render = bool(body.get("render", False))
        if src == "performance" and (st.get("performance") or {}).get("doc"):
            from ..performance.director import Performance, PerformanceDirector  # noqa: PLC0415

            doc = dict(st["performance"]["doc"])
            doc["allow_pending"] = review
            masks = S.masks()
            eng = S.pose.motion_engine(st, masks, rejected_ids(st))
            d = PerformanceDirector(eng, _vis(st), Performance.from_json(doc))
            r = d.solve(float(body.get("t", 0.0)), render=render, corrective_mode="all" if review else "auto")
            return eng, r
        eng = S.pose.motion_engine(st, S.masks(), rejected_ids(st))
        preset = (st.get("pose") or {}).get("preset") or "relaxed_standing"
        acts = {s: a for s, a in (body.get("actions") or {}).items() if a} or \
            {s: a for s, a in ((st.get("hand_selection") or {}).get("actions") or {}).items() if a} or None
        r = eng.pose(preset, visible=_vis(st), skeleton_only=not render, allow_pending=review, hand_actions=acts,
                     switches=st.get("switches") or None, corrective_mode="all" if review else "auto")
        return eng, r

    @app.post("/api/hands/inspect")
    def hands_inspect(body: dict = Body(default={})):
        """Hand Selection Inspector: per side the active drawing, its metadata and palm analysis, the rejected
        candidates with their reasons, the explanation, wrist checks (when rendered) and missing artwork."""
        from ..motion.handcheck import wrist_check  # noqa: PLC0415
        from ..motion.handselect import OVERRIDE_RULE, HandContext  # noqa: PLC0415

        with S.lock:
            st = S.project.state()
            eng, r = _hand_ctx(st, body)
            out = {"mode": "review" if str(body.get("mode", "review")) == "review" else "production", "sides": {}}
            perf_h = (r.get("performance") or {}).get("hands") or {}
            for s in ("r", "l"):
                dec = r["hand_selection"]["decisions"][s]
                h = r["hands"][s]
                v = eng.hand_lib.hands[s].get(h["pose"])
                hm = ((v.meta or {}).get("hand_meta") if v is not None else None) or {}
                ev = None
                if v is not None:
                    c = dec["context"]
                    ctx = HandContext(s, c["action"], c["forearm_deg"], c["hand_deg"],
                                      float(eng.sk.bone(f"hand_{s}").rest_angle), c["elbow_flex_deg"],
                                      c["wrist_bend_deg"], tuple(c["wrist"]), c["view"],
                                      tuple(c["target"]) if c["target"] else None, None, c["t"], c["beat"])
                    ev = eng.hand_select.evaluate(v, ctx, OVERRIDE_RULE, None, True)
                pdec = (perf_h.get(s) or {}).get("decision")
                out["sides"][s] = {
                    "active": {"pose": h["pose"], "cid": h["cid"], "layer": h["layer"], "source": h["source"],
                               "pending_review": h["pending_review"], "offset": h.get("offset"),
                               "approval": (hm.get("approval") or {}).get("state")},
                    "metadata": {k: hm.get(k) for k in ("key", "gesture", "label_pose", "handedness",
                                                        "handedness_confidence", "visible_surface", "palm_normal_sprite",
                                                        "rotation_tolerance_deg", "flags", "reassigned", "use_side",
                                                        "store_side", "field_sources", "excluded")},
                    "palm": (ev or {}).get("palm"), "wrist_pivot": list(r["solve"]["joints_posed"][f"hand_{s}"]),
                    "decision": pdec or {k: dec.get(k) for k in ("action", "chosen", "cid", "mode", "reason",
                                                                  "needs_artwork", "fallback", "rejected",
                                                                  "explanation")},
                    "requested": perf_h.get(s), "override_check": dec.get("override_check"),
                    "ranking": [{k: x.get(k) for k in ("pose", "cid", "status", "total", "notes")}
                                for x in dec.get("ranking", [])],
                    "wrist": wrist_check(r, eng, s) if "render" in r else None}
            return jsonable(out)

    @app.get("/api/hands/closeup.png")
    def hands_closeup(side: str = "r", pose: str | None = None, mode: str = "review", source: str = "pose",
                      t: float = 0.0):
        """Wrist close-up of the current frame; ``pose`` forces a drawing (before/after comparison)."""
        from ..phase2.pose_eval import on_bg  # noqa: PLC0415

        if side not in ("r", "l"):
            raise HTTPException(400, "side r|l")
        with S.lock:
            st = copy.deepcopy(S.project.state())
            if pose:
                st["switches"] = {**(st.get("switches") or {}),
                                  f"hand_{side}": {"user": pose, "auto": False, "keys": [], "offsets": {}}}
            if source == "performance" and pose:
                source = "pose"
            _, r = _hand_ctx(st, {"mode": mode, "source": source, "t": t, "render": True})
        x, y = r["solve"]["joints_posed"][f"hand_{side}"]
        pad = r["pad"]
        im = on_bg(r["image"]).crop((int(x + pad - 130), int(y + pad - 110), int(x + pad + 130), int(y + pad + 170)))
        import numpy as np  # noqa: PLC0415

        return png(np.asarray(im.convert("RGBA")))

    @app.get("/api/hands/metadata")
    def hands_metadata():
        from .. import handmeta  # noqa: PLC0415

        with S.lock:
            st = S.project.state()
            base = handmeta.load(S.pkg)
            if base is None:
                return jsonable({"available": False, "note": "run autorig hand-audit <package> --inspection ..."})
            inv = handmeta.apply_corrections(base, (st.get("hand_metadata") or {}).get("corrections"))
            return jsonable({"available": True, "summary": handmeta.summary(inv), "assets": inv["assets"],
                             "corrections": (st.get("hand_metadata") or {}).get("corrections", {}),
                             "correctable": sorted(handmeta.CORRECTABLE)})

    def _ref_store():
        from ..refmotion.store import MotionReferenceStore, default_root  # noqa: PLC0415

        return MotionReferenceStore(default_root(S.pkg))

    @app.get("/api/reference")
    def reference_info():
        from ..motion.handselect import ACTIONS  # noqa: PLC0415
        from ..refmotion.pose import EVALUATED, ensure_models, mediapipe_available  # noqa: PLC0415

        with S.lock:
            st = S.project.state()
            store = _ref_store()
            return jsonable({"library": store.list(), "root": str(store.root),
                             "state": st.get("reference_motion"), "actions": sorted(ACTIONS),
                             "providers": {"mediapipe": mediapipe_available() or "ok",
                                           "models": {k: v["ok"] for k, v in ensure_models().items()},
                                           "evaluated": EVALUATED},
                             "note": "pose models run locally at import only (no cloud inference); retargeting "
                                     "and critique never approve anything"})

    @app.get("/api/reference/{ref_id}")
    def reference_get(ref_id: str):
        with S.lock:
            try:
                e = _ref_store().get(ref_id)
            except (KeyError, ValueError) as exc:
                raise HTTPException(404, f"no reference {ref_id!r}") from exc
            an = e["analysis"]
            return jsonable({"meta": e["meta"], "annotations": e["annotations"], "frames": e["track"].frames,
                             "fps": e["track"].fps, "duration": e["track"].duration,
                             "analysis": {k: an.get(k) for k in ("depth_segments", "accents", "coordination",
                                                                  "head_lead_s", "low_confidence_frac")},
                             "timing": {k: an["timing"].get(k) for k in ("holds", "phases", "peak_speed")},
                             "channels": an["channels"]})

    @app.get("/api/reference/{ref_id}/frame.png")
    def reference_frame(ref_id: str, i: int = 0):
        import numpy as np  # noqa: PLC0415

        from ..refmotion.evidence import draw_skeleton  # noqa: PLC0415

        with S.lock:
            try:
                e = _ref_store().get(ref_id)
            except (KeyError, ValueError) as exc:
                raise HTTPException(404, f"no reference {ref_id!r}") from exc
        tr = e["track"]
        i = max(0, min(int(i), tr.frames - 1))
        im = draw_skeleton(tr, i, (300, 420), f"{ref_id} t={i / tr.fps:.2f}s",
                           e["analysis"].get("depth_segments"))
        return png(np.asarray(im.convert("RGBA")))

    @app.post("/api/reference/import")
    def reference_import(body: dict = Body(...)):
        """Import reference media (base64) into the library: validated, pose-estimated locally, analysed."""
        import tempfile  # noqa: PLC0415

        from ..refmotion.analyze import ReferenceMotionAnalyzer  # noqa: PLC0415
        from ..refmotion.importer import ImportError_, ReferenceMotionImporter  # noqa: PLC0415
        from ..refmotion.pose import MediaPipePoseProvider  # noqa: PLC0415

        rid = str(body.get("ref_id") or "")
        name = re.sub(r"[^A-Za-z0-9._-]", "_", str(body.get("name") or "reference.mp4"))[:96]
        meta = {"name": rid, "gesture": body.get("gesture"), "category": body.get("category") or "gesture",
                "hand_actions": body.get("hand_actions") or None,
                "provenance": {"kind": "user_supplied", "source": body.get("source"),
                               "usage_permission": body.get("usage_permission")}}
        data = _b64(body, limit=128 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / name
            f.write_bytes(data)
            try:
                media = ReferenceMotionImporter().load(f, meta)
                tr = media.track if media.track is not None else MediaPipePoseProvider().estimate(media.frames, media.fps)
            except (ImportError_, RuntimeError, ValueError) as exc:
                return JSONResponse({"status": "rejected", "errors": [str(exc)]}, status_code=422)
        tr.meta = {**tr.meta, **meta}
        q = ReferenceMotionImporter.quality(tr)
        an = ReferenceMotionAnalyzer().analyze(tr)
        with S.lock:
            try:
                rec = _ref_store().add(rid, tr, {**meta, "quality": q}, an)
            except ValueError as exc:
                return JSONResponse({"status": "rejected", "errors": [str(exc)]}, status_code=422)
        return jsonable({"status": "stored", "reference": rec, "quality": q})

    def _retarget(st, ref_id: str, body: dict):
        from ..refmotion.retarget import ReferenceMotionRetargeter  # noqa: PLC0415

        e = _ref_store().get(ref_id)
        s = {**((st.get("reference_motion") or {}).get("settings") or {}).get(ref_id, {}), **body}
        meta = {**e["track"].meta, **e["meta"]}
        seg = s.get("segment")
        return e, ReferenceMotionRetargeter().retarget(
            e["analysis"], meta, float(s.get("intensity", 1.0)), float(s.get("timing_scale", 1.0)),
            segment=tuple(seg) if seg else None, hand_actions=s.get("hand_actions") or None,
            allow_pending=str(s.get("mode", "review")) == "review")

    @app.post("/api/reference/{ref_id}/retarget")
    def reference_retarget(ref_id: str, body: dict = Body(default={})):
        """Retarget onto this character (nothing is written: apply it with performance.set)."""
        from ..performance.director import Performance, PerformanceDirector  # noqa: PLC0415
        from ..refmotion.retarget import ReferenceMotionRetargeter  # noqa: PLC0415

        with S.lock:
            st = S.project.state()
            try:
                e, rt = _retarget(st, ref_id, body)
            except (KeyError, ValueError) as exc:
                raise HTTPException(404, str(exc)) from exc
            eng = S.pose.motion_engine(st, S.masks(), rejected_ids(st))
            d = PerformanceDirector(eng, _vis(st), Performance.from_json(rt["performance"]))
            err = ReferenceMotionRetargeter.compare(d, rt["target_curves"], e["track"].fps,
                                                    rt["settings"]["timing_scale"], rt["settings"]["segment"][0])
            gest = {s: d.hand_plan()[s] for s in ("r", "l")}
            return jsonable({"performance": rt["performance"], "feasibility": rt["feasibility"], "errors": err,
                             "keys": rt["keys"], "settings": rt["settings"], "duration": d.duration,
                             "hand_plan": {s: [{k: iv.get(k) for k in ("value", "t0", "t1", "kind")} |
                                               ({"chosen": iv["decision"]["chosen"],
                                                 "needs_artwork": iv["decision"].get("needs_artwork")}
                                                if "decision" in iv else {}) for iv in ivs] for s, ivs in gest.items()}})

    @app.post("/api/reference/{ref_id}/critique")
    def reference_critique(ref_id: str, body: dict = Body(default={})):
        from ..performance.director import Performance, PerformanceDirector  # noqa: PLC0415
        from ..refmotion.critic import VisualAnimationCritic  # noqa: PLC0415

        with S.lock:
            st = S.project.state()
            e, rt = _retarget(st, ref_id, body)
            eng = S.pose.motion_engine(st, S.masks(), rejected_ids(st))
            d = PerformanceDirector(eng, _vis(st), Performance.from_json(rt["performance"]))
            t = float(body.get("t", d.duration / 2))
            r = d.solve(t, render=True)
            c = VisualAnimationCritic().critique({"eng": eng, "result": r, "params": d.pose_at(t)[2]["params"],
                                                  "mode": str(body.get("mode", "review")), "interactive": True,
                                                  "shrug": "shrug" in str(e["meta"].get("tags", {}).get("gesture"))})
            return jsonable({"t": t, **c})

    @app.post("/api/reference/{ref_id}/revise")
    def reference_revise(ref_id: str, body: dict = Body(default={})):
        """Controlled revision loop on a temporary candidate. The result is a proposal; apply it with
        performance.set and record the decision with reference.revision."""
        from ..refmotion.critic import RevisionLoop  # noqa: PLC0415

        with S.lock:
            st = S.project.state()
            e, rt = _retarget(st, ref_id, body)
            eng = S.pose.motion_engine(st, S.masks(), rejected_ids(st))
            from ..performance.director import Performance, PerformanceDirector  # noqa: PLC0415

            dur = PerformanceDirector(eng, _vis(st), Performance.from_json(rt["performance"])).duration
            t = float(body.get("t", dur / 2))
            out = RevisionLoop(eng, _vis(st), max_attempts=int(body.get("max_attempts", 3))).run(
                rt["performance"], t, {"interactive": True})
            return jsonable({k: out[k] for k in ("status", "attempts", "changed", "approved", "candidate")} |
                            {"initial": out["initial"]["defects"], "final": out["final"]["defects"], "t": t})

    if static_dir and Path(static_dir).is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="editor")
    return app
