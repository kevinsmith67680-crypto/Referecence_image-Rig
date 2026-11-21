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
