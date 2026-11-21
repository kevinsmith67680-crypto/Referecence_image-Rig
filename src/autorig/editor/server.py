"""FastAPI service for the AutoRig editor. One process serves one character package; local-first.

The backend owns the project state. The browser sends operations; undo, redo, versions and every file
live on disk in ``<package>/editor/``. Images are generated on demand from the operation log, so nothing
in the package is overwritten.
"""

from __future__ import annotations

import base64
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
            return jsonable({"groups": [g.to_json() for g in ctl.groups.values()], "state": ctl.state,
                             "hand_library": eng.hand_lib.to_json(eng.profile.midline_x),
                             "corrections": eng.corr_switch.to_json()})

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

    if static_dir and Path(static_dir).is_dir():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="editor")
    return app
