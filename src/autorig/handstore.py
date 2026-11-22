"""Character-scoped Hand Pose Store: imported hand drawings for the artwork switch system.

Layout (inside the character package, append-only):

    <package>/hand_store/index.json                    database: packs, assets, versions, hashes, provenance
    <package>/hand_store/packs/<library_id>/v<N>/       the imported pack, byte for byte (manifest, sprites, sheet)
    <package>/hand_store/uploads/<library_id>/<asset>/v<N>.png   replacement PNGs (new asset versions)

The store only *holds* drawings. What the user decides about them (which drawing a pose uses, wrist placement
corrections, cuff-mask settings, labels, deleted candidates) lives in the editor project (schema v3,
``hand_store`` state and ``handstore.*`` operations), so it has undo/redo and survives reopening. Approval uses the
existing ``approval.set`` operation with the item ``handstore:<library_id>/<asset_id>@v<N>``: every new version
needs its own review. Imported drawings are never approved in code.

Derived layers (placed at the character's wrist, sleeve/cuff masked, matte halo removed) are computed from the
stored source on demand; the source files are never modified.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage

STORE_DIR = "hand_store"
SCHEMA = "autorig.hand_store"
VERSION = 1
SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
SIDE_MAP = {"left": "l", "right": "r"}
# pack pose name -> switch pose id (motion/switches.py HAND_POSES)
PACK_POSES = {"relaxed": "relaxed", "open_palm": "open_palm", "fist": "fist", "point": "pointing",
              "thumbs_up": "thumbs_up", "grip": "gripping", "palm_down": "palm_down", "cupped": "cupped"}
# sprite direction from wrist to fingers. "wrist_edge": sleeve-free drawings that carry their own wrist opening
# (``wrist_edge_px`` end points and the unit ``wrist_axis`` into the hand; see ``autorig.handsheet``)
ORIENTATIONS = {"sleeve_top_hand_down": (0.0, 1.0), "wrist_edge": None}
GEOMETRY_KEYS = ("wrist_edge_px", "wrist_axis", "wrist_opening", "wrist_method", "wrist_confidence", "features",
                 "handedness", "derived", "flags", "view", "sheet_cell", "sleeve", "manual_review")
MAX_ZIP_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 400
ALLOWED_EXT = {".png", ".json", ".md", ".txt"}
TUCK_PX = 6.0  # the sprite's cut edge sits this far under the character's sleeve cuff


class PackError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("hand pack rejected:\n- " + "\n- ".join(errors))
        self.errors = errors


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _atomic_write(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    os.replace(tmp, path)


# -- reading and validating a pack -------------------------------------------------------------------------
def read_zip(data: bytes) -> dict[str, bytes]:
    """Files of a pack zip (a single top-level folder is stripped). Rejects unsafe paths and oversize archives."""
    if len(data) > MAX_ZIP_BYTES:
        raise PackError([f"archive is {len(data) / 1e6:.1f} MB; the limit is {MAX_ZIP_BYTES / 1e6:.0f} MB"])
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PackError(["not a zip archive"])
    infos = [i for i in z.infolist() if not i.is_dir()]
    if len(infos) > MAX_ENTRIES:
        raise PackError([f"archive has {len(infos)} files; the limit is {MAX_ENTRIES}"])
    errs, files = [], {}
    for i in infos:
        name = i.filename.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/") or ":" in name:
            errs.append(f"unsafe path in archive: {name!r}")
            continue
        if (i.external_attr >> 16) & 0o170000 == 0o120000:
            errs.append(f"symbolic link in archive: {name!r}")
            continue
        if name.startswith("__MACOSX/") or name.split("/")[-1].startswith("."):
            continue
        files[name] = z.read(i)
    if errs:
        raise PackError(errs)
    tops = {n.split("/")[0] for n in files if "/" in n}
    if "manifest.json" not in files and len(tops) == 1 and all("/" in n for n in files):
        root = tops.pop() + "/"
        files = {n[len(root):]: b for n, b in files.items()}
    return files


def _check_wrist_edge(tag: str, a: dict[str, Any], w: int, h: int) -> list[str]:
    """A sleeve-free drawing must say where its wrist opening is (two points on the canvas) and which way the hand
    goes from it (a unit vector)."""
    errs = []
    e, ax = a.get("wrist_edge_px"), a.get("wrist_axis")
    try:
        (x0, y0), (x1, y1) = e
        pts_ok = all(-1 <= x <= w and -1 <= y <= h for x, y in ((x0, y0), (x1, y1))) and math.hypot(x1 - x0, y1 - y0) >= 4
    except (TypeError, ValueError):
        pts_ok = False
    if not pts_ok:
        errs.append(f"{tag}: base_orientation wrist_edge needs wrist_edge_px [[x0, y0], [x1, y1]] on the canvas, "
                    f">= 4 px apart (got {e!r})")
    try:
        n = math.hypot(float(ax[0]), float(ax[1]))
        ax_ok = len(ax) == 2 and 0.9 <= n <= 1.1
    except (TypeError, ValueError, IndexError):
        ax_ok = False
    if not ax_ok:
        errs.append(f"{tag}: base_orientation wrist_edge needs wrist_axis, a unit vector from the wrist into the hand "
                    f"(got {ax!r})")
    return errs


def validate_pack(files: dict[str, bytes]) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], list[str]]:
    """Returns (manifest, assets, errors, warnings). Every problem is collected, with the asset it concerns."""
    errs: list[str] = []
    warns: list[str] = []
    if "manifest.json" not in files:
        return {}, [], ["manifest.json is missing at the top of the archive"], warns
    try:
        m = json.loads(files["manifest.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {}, [], [f"manifest.json is not valid JSON: {exc}"], warns
    for k in ("library_id", "asset_count", "pose_names", "hand_sides", "assets"):
        if k not in m:
            errs.append(f"manifest.json: missing key '{k}'")
    if errs:
        return m, [], errs, warns
    lib = str(m["library_id"])
    if not SAFE_ID.match(lib):
        errs.append(f"library_id {lib!r}: use 1-64 letters, digits, '.', '_' or '-'")
    assets = m["assets"] if isinstance(m["assets"], list) else []
    if m["asset_count"] != len(assets):
        errs.append(f"asset_count is {m['asset_count']} but {len(assets)} assets are listed")
    unknown_sides = [s for s in m["hand_sides"] if s not in SIDE_MAP]
    if unknown_sides:
        errs.append(f"hand_sides {unknown_sides} unknown; use 'left' and/or 'right'")
    unknown_poses = [p for p in m["pose_names"] if p not in PACK_POSES]
    if unknown_poses:
        errs.append(f"pose_names {unknown_poses} have no switch pose; known: {sorted(PACK_POSES)}")
    ids, pairs, out = set(), {}, []
    for i, a in enumerate(assets):
        tag = f"assets[{i}] ({a.get('id', '?')})"
        miss = [k for k in ("id", "side", "pose", "file", "canvas_px", "wrist_anchor_px") if k not in a]
        if miss:
            errs.append(f"{tag}: missing {miss}")
            continue
        aid = str(a["id"])
        if not SAFE_ID.match(aid):
            errs.append(f"{tag}: id must be 1-64 letters, digits, '.', '_' or '-'")
        if aid in ids:
            errs.append(f"{tag}: duplicate id {aid!r}")
        ids.add(aid)
        if a["side"] not in m["hand_sides"] or a["side"] not in SIDE_MAP:
            errs.append(f"{tag}: side {a['side']!r} is not one of {m['hand_sides']}")
        if a["pose"] not in m["pose_names"]:
            errs.append(f"{tag}: pose {a['pose']!r} is not listed in pose_names")
        key = (a["side"], a["pose"])
        if key in pairs:
            errs.append(f"{tag}: {a['side']} {a['pose']} is also asset {pairs[key]!r}; one drawing per side and pose")
        pairs[key] = aid
        orient = a.get("base_orientation", "sleeve_top_hand_down")
        if orient not in ORIENTATIONS:
            errs.append(f"{tag}: base_orientation {orient!r} unsupported; supported: {sorted(ORIENTATIONS)}")
        f = str(a["file"]).replace("\\", "/")
        if f not in files:
            errs.append(f"{tag}: file {f!r} is not in the archive")
            continue
        try:
            im = Image.open(io.BytesIO(files[f]))
            im.load()
        except Exception as exc:  # noqa: BLE001 - any decoder error is a pack error
            errs.append(f"{tag}: {f} does not open as an image ({exc})")
            continue
        if im.format != "PNG":
            errs.append(f"{tag}: {f} is {im.format}, not PNG")
        has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
        if not has_alpha:
            errs.append(f"{tag}: {f} has no alpha channel (mode {im.mode}); export it as RGBA with a transparent background")
            continue
        rgba = np.asarray(im.convert("RGBA"))
        cw, ch = (list(a["canvas_px"]) + [None, None])[:2]
        if (im.width, im.height) != (cw, ch):
            errs.append(f"{tag}: {f} is {im.width}x{im.height} but canvas_px declares {cw}x{ch}")
        al = rgba[..., 3]
        if int((al == 0).sum()) < 0.2 * al.size:
            errs.append(f"{tag}: {f} is mostly opaque ({int((al == 0).sum())} transparent px); the background must be transparent")
        if int((al > 127).sum()) < 500:
            errs.append(f"{tag}: {f} has almost no visible pixels")
        ax, ay = a["wrist_anchor_px"]
        if not (0 <= ax < im.width and 0 <= ay < im.height):
            errs.append(f"{tag}: wrist_anchor_px {a['wrist_anchor_px']} is outside the {im.width}x{im.height} canvas")
        if orient == "wrist_edge":
            errs.extend(_check_wrist_edge(tag, a, im.width, im.height))
        out.append({**a, "file": f, "sha256": _sha(files[f]), "size": [im.width, im.height],
                    "side_code": SIDE_MAP.get(a["side"]), "switch_pose": PACK_POSES.get(a["pose"])})
    for s in m["hand_sides"]:
        for p in m["pose_names"]:
            if (s, p) not in pairs:
                errs.append(f"incomplete pack: no {s} {p} drawing (every listed pose is needed for every listed side)")
    if not m.get("all_variants_require_review", True):
        warns.append("the manifest says review is not required; AutoRig requires review of every imported drawing")
    for a in assets:
        st = str(a.get("approval_status", "")).upper()
        if st and st not in ("NEEDS_REVIEW", "PENDING_REVIEW"):
            warns.append(f"{a.get('id')}: manifest approval_status {st!r} is ignored; imported drawings start pending review")
    return m, out, errs, warns


# -- store -------------------------------------------------------------------------------------------------
class HandStore:
    def __init__(self, pkg: str | Path):
        self.pkg = Path(pkg)
        self.dir = self.pkg / STORE_DIR
        self.index_path = self.dir / "index.json"
        if self.index_path.is_file():
            self.index = json.loads(self.index_path.read_text(encoding="utf-8"))
            if self.index.get("schema") != SCHEMA or int(self.index.get("schema_version", 0)) > VERSION:
                raise ValueError(f"{self.index_path}: unsupported hand store schema")
        else:
            self.index = {"schema": SCHEMA, "schema_version": VERSION, "package": self.pkg.name,
                          "source_sha256": self._source_sha(), "packs": [], "assets": {}}
        self._img: dict[tuple[str, int], np.ndarray] = {}

    def _source_sha(self) -> str | None:
        f = self.pkg / "metadata.json"
        if f.is_file():
            return json.loads(f.read_text())["source"].get("sha256")
        return None

    def _persist(self) -> None:
        self.index["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _atomic_write(self.index_path, json.dumps(self.index, indent=1))

    @property
    def revision(self) -> str:
        return _sha(json.dumps(self.index.get("assets", {}), sort_keys=True).encode())[:12]

    def import_pack(self, data: bytes, source_name: str = "pack.zip") -> dict[str, Any]:
        files = read_zip(data)
        m, assets, errs, warns = validate_pack(files)
        if errs:
            raise PackError(errs)
        lib, zsha = m["library_id"], _sha(data)
        for p in self.index["packs"]:
            if p["library_id"] == lib and p["zip_sha256"] == zsha:
                return {"status": "already_imported", "library_id": lib, "version": p["version"], "warnings": warns,
                        "assets": len(assets)}
        version = 1 + max([p["version"] for p in self.index["packs"] if p["library_id"] == lib], default=0)
        rel = Path("packs") / lib / f"v{version}"
        for name, b in files.items():
            if Path(name).suffix.lower() in ALLOWED_EXT:
                _atomic_write(self.dir / rel / name, b)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        new_versions = 0
        for a in assets:
            uid = f"{lib}/{a['id']}"
            rec = self.index["assets"].setdefault(uid, {
                "uid": uid, "library_id": lib, "asset_id": a["id"], "side": a["side_code"], "pack_side": a["side"],
                "pose": a["switch_pose"], "pack_pose": a["pose"], "versions": []})
            rec.update(provenance={"artwork_source": a.get("artwork_source"), "pack_provenance": m.get("source_provenance"),
                                   "manifest_status": a.get("approval_status"), "notes": a.get("notes")},
                       manifest_anchor=list(a["wrist_anchor_px"]), anchor_status=a.get("wrist_anchor_status"),
                       base_orientation=a.get("base_orientation", "sleeve_top_hand_down"))
            geom = {k: a[k] for k in GEOMETRY_KEYS if k in a}
            if rec["versions"] and rec["versions"][-1]["sha256"] == a["sha256"]:
                continue
            rec["versions"].append({"v": len(rec["versions"]) + 1, "file": str(rel / a["file"]), "sha256": a["sha256"],
                                    "size": a["size"], "origin": "pack", "pack_version": version, "imported_at": now,
                                    **({"geometry": geom} if geom else {})})
            new_versions += 1
        self.index["packs"].append({"library_id": lib, "version": version, "zip_sha256": zsha, "source_name": source_name,
                                    "imported_at": now, "dir": str(rel), "asset_count": len(assets),
                                    "manifest_sha256": _sha(files["manifest.json"]),
                                    "provenance": m.get("source_provenance"), "intended_use": m.get("intended_use"),
                                    "files": {n: _sha(b) for n, b in files.items() if Path(n).suffix.lower() in ALLOWED_EXT},
                                    "warnings": warns, "supersedes": [str(x) for x in m.get("supersedes", [])],
                                    "sleeve": m.get("sleeve", "attached"), "pack_schema_version": m.get("schema_version", 1)})
        self._persist()
        return {"status": "imported", "library_id": lib, "version": version, "assets": len(assets),
                "new_versions": new_versions, "warnings": warns}

    def replace_png(self, uid: str, data: bytes, source_name: str = "replacement.png") -> dict[str, Any]:
        """A new version of an imported drawing (the earlier versions stay in the store)."""
        rec = self.index["assets"].get(uid)
        if rec is None:
            raise KeyError(f"unknown hand drawing {uid}")
        try:
            im = Image.open(io.BytesIO(data))
            im.load()
        except Exception as exc:  # noqa: BLE001
            raise PackError([f"{source_name} does not open as an image ({exc})"])
        if im.format != "PNG" or im.mode not in ("RGBA", "LA", "P") or (im.mode == "P" and "transparency" not in im.info):
            raise PackError([f"{source_name}: a PNG with an alpha channel is required (got {im.format} {im.mode})"])
        al = np.asarray(im.convert("RGBA"))[..., 3]
        if int((al == 0).sum()) < 0.2 * al.size or int((al > 127).sum()) < 500:
            raise PackError([f"{source_name}: needs a transparent background and a visible drawing"])
        sha = _sha(data)
        if rec["versions"] and rec["versions"][-1]["sha256"] == sha:
            return {"status": "unchanged", "uid": uid, "v": rec["versions"][-1]["v"]}
        extra = {}
        if rec.get("base_orientation") == "wrist_edge":
            extra["geometry"] = replacement_geometry(np.asarray(im.convert("RGBA")), rec["versions"][-1].get("geometry", {}))
        v = len(rec["versions"]) + 1
        rel = Path("uploads") / rec["library_id"] / rec["asset_id"] / f"v{v}.png"
        _atomic_write(self.dir / rel, data)
        rec["versions"].append({"v": v, "file": str(rel), "sha256": sha, "size": [im.width, im.height],
                                "origin": "replacement", "source_name": source_name,
                                "imported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **extra})
        self._persist()
        return {"status": "replaced", "uid": uid, "v": v}

    def assets(self) -> list[dict[str, Any]]:
        return list(self.index["assets"].values())

    def geometry(self, uid: str, v: int | None = None) -> dict[str, Any]:
        """Wrist opening and features of a sleeve-free drawing version (empty for sleeve-attached drawings)."""
        return dict(self.version(uid, v).get("geometry") or {})

    def libraries(self) -> dict[str, dict[str, Any]]:
        """Per library: its latest pack and the libraries a later pack supersedes it with."""
        out: dict[str, dict[str, Any]] = {}
        for i, p in enumerate(self.index["packs"]):
            out[p["library_id"]] = {"library_id": p["library_id"], "version": p["version"], "order": i,
                                    "sleeve": p.get("sleeve", "attached"), "supersedes": p.get("supersedes", []),
                                    "imported_at": p["imported_at"], "superseded_by": []}
        for lib, rec in out.items():
            for other in out.values():
                if lib in other["supersedes"] and other["order"] > rec["order"]:
                    rec["superseded_by"].append(other["library_id"])
        return out

    def version(self, uid: str, v: int | None = None) -> dict[str, Any]:
        vs = self.index["assets"][uid]["versions"]
        if v is None:
            return vs[-1]
        for x in vs:
            if x["v"] == v:
                return x
        raise KeyError(f"{uid} has no version {v}")

    def image(self, uid: str, v: int | None = None) -> np.ndarray:
        ver = self.version(uid, v)
        key = (uid, ver["v"])
        if key not in self._img:
            data = (self.dir / ver["file"]).read_bytes()
            if _sha(data) != ver["sha256"]:
                raise ValueError(f"{uid} v{ver['v']}: file hash does not match the store index (modified on disk?)")
            self._img[key] = np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"))
        return self._img[key]

    def verify(self) -> list[str]:
        """Every stored file still matches its recorded hash."""
        bad = []
        for rec in self.index["assets"].values():
            for ver in rec["versions"]:
                f = self.dir / ver["file"]
                if not f.is_file() or _sha(f.read_bytes()) != ver["sha256"]:
                    bad.append(f"{rec['uid']} v{ver['v']}: {ver['file']}")
        return bad


def approval_key(uid: str, v: int) -> str:
    return f"handstore:{uid}@v{v}"


def default_store_state() -> dict[str, Any]:
    """Editor project state for the user's decisions about stored drawings (schema v3; ``libraries`` from v4)."""
    return {"use": {}, "placement": {}, "mask": {}, "labels": {}, "deleted": [], "versions": {}, "libraries": {}}


LIBRARY_STATUS = ("active", "archived")
PHASE2_LIBRARY = "phase2_sheet_hands"  # the Phase 2 hands cut from the character sheet (with their cuffs)


def library_status(store: "HandStore", st: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Active or archived, per library. Default: archived when a later-imported pack supersedes it (its manifest
    lists it under ``supersedes``), else active. The user's choice (``handstore.library``) overrides the default.
    Archived drawings stay in the store (rollback) but are not candidates of the switch groups."""
    user = (st or {}).get("libraries") or {}
    out = {}
    libs = store.libraries()
    for lib, rec in sorted(libs.items(), key=lambda kv: -kv[1]["order"]):  # newest first: superseders decided first
        by = [o for o in rec["superseded_by"] if out.get(o, {}).get("status") == "active"]
        default = "archived" if by else "active"
        status = user.get(lib, default)
        why = (f"superseded by {', '.join(by)}" if by else
               ("current" if not rec["superseded_by"] else f"its replacement {', '.join(rec['superseded_by'])} is archived"))
        out[lib] = {**rec, "default": default, "status": status, "user": lib in user,
                    "reason": why + ("; set by the user" if lib in user else "")}
    out = {lib: out[lib] for lib in libs}
    # the Phase 2 sheet hands are sleeve-attached too: archived while a sleeve-free library is active
    free = [lib for lib, r in out.items() if r["sleeve"] == "none" and r["status"] == "active"]
    default = "archived" if free else "active"
    status = user.get(PHASE2_LIBRARY, default)
    out[PHASE2_LIBRARY] = {"library_id": PHASE2_LIBRARY, "version": None, "order": -1, "sleeve": "attached",
                           "supersedes": [], "superseded_by": free, "imported_at": None, "default": default,
                           "status": status, "user": PHASE2_LIBRARY in user, "pseudo": True,
                           "reason": (f"sleeve-free library active ({', '.join(free)})" if free else "no sleeve-free library")
                           + ("; set by the user" if PHASE2_LIBRARY in user else "")}
    return out


def replacement_geometry(rgba: np.ndarray, old: dict[str, Any]) -> dict[str, Any]:
    """Wrist opening of a replacement version of a sleeve-free drawing: detected on the new pixels near the side
    the earlier version's opening faced (handsheet.wrist_opening)."""
    from .handsheet import SIDE_DEG, wrist_opening  # noqa: PLC0415

    ax = old.get("wrist_axis") or [0.0, 1.0]
    facing = math.degrees(math.atan2(-ax[1], -ax[0])) % 360  # direction from the hand out through the opening
    side = min(SIDE_DEG, key=lambda k: abs((SIDE_DEG[k] - facing + 180) % 360 - 180))
    w = wrist_opening(rgba[..., 3] > 127, side, old.get("wrist_width_px"))
    return {"wrist_edge_px": [w["p0"], w["p1"]], "wrist_axis": w["axis"], "wrist_opening": w["opening"],
            "wrist_method": w["method"] + " (replacement, re-detected)", "wrist_confidence": w["confidence"],
            "features": {}, "manual_review": True, "flags": ["replacement version: wrist opening re-detected"]}


# -- image analysis: sleeve/cuff mask, matte halo, wrist edge ---------------------------------------------
def classify(rgba: np.ndarray) -> dict[str, np.ndarray]:
    rgb = rgba[..., :3].astype(np.int16)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    al = rgba[..., 3] > 32
    lum = rgb.mean(-1)
    sat = rgb.max(-1) - rgb.min(-1)
    return {"alpha": al, "skin": al & (r > 140) & (r - b > 70),
            "white": al & (lum > 165) & (sat < 45),
            "navy": al & (lum >= 18) & (lum < 95) & (b >= r - 6) & (sat < 45),
            "outline": al & (lum < 40), "grey": al & (sat < 30) & (lum >= 40)}


def matte_halo(rgba: np.ndarray) -> np.ndarray:
    """Grey matte pixels left outside the black outline by background removal (outer 2 px ring)."""
    c = classify(rgba)
    vis = rgba[..., 3] > 0
    ring = vis & ~ndimage.binary_erosion(vis, iterations=2)
    rgb = rgba[..., :3].astype(np.int16)
    sat = rgb.max(-1) - rgb.min(-1)
    return ring & (sat < 30) & (rgb.mean(-1) > 60) & ~c["skin"]


def cuff_mask(rgba: np.ndarray, orientation: str = "sleeve_top_hand_down", cut_offset: float = 0.0
              ) -> tuple[np.ndarray, dict[str, Any]]:
    """Mask of the sprite's own sleeve and shirt cuff (non-destructive: a boolean mask, the sprite is unchanged).

    The sleeve is the navy region at the top; the cuff is the white band directly under it. For every column the
    mask runs down to the cuff's bottom edge and through its black outline. Columns beside the cuff (a thumb raised
    next to it) keep their skin. ``cut_offset`` moves the cut line down (+) or up (-) in sprite pixels."""
    if orientation != "sleeve_top_hand_down":
        raise ValueError(orientation)
    c = classify(rgba)
    H, W = rgba.shape[:2]
    ys, xs = np.nonzero(c["alpha"])
    if len(ys) == 0:
        return np.zeros((H, W), bool), {"found": False}
    top = int(ys.min())
    lab, n = ndimage.label(c["navy"])
    touch = {int(l) for l in np.unique(lab[top:top + 6]) if l}
    sleeve = np.isin(lab, list(touch)) if touch else np.zeros((H, W), bool)
    if not sleeve.any():
        return np.zeros((H, W), bool), {"found": False, "reason": "no navy sleeve at the top"}
    sy, sx = np.nonzero(sleeve)
    sx0, sx1, sbottom = int(sx.min()), int(sx.max()), int(sy.max())
    wl, wn = ndimage.label(c["white"])
    cuff = np.zeros((H, W), bool)
    for l in range(1, wn + 1):
        cy, cx = np.nonzero(wl == l)
        if cy.min() <= sbottom + 14 and cx.max() - cx.min() >= 0.35 * (sx1 - sx0) and \
                sx0 - 20 <= cx.mean() <= sx1 + 20 and cy.max() <= sbottom + 50:
            cuff |= wl == l
    cloth = sleeve | cuff
    edge = cuff if cuff.any() else cloth  # the cut follows the cuff's bottom edge (the lowest band of the sleeve)
    cut = np.full(W, -1, int)
    cx_cols = np.nonzero(cloth.any(0))[0]
    for x in range(W):
        col = np.nonzero(edge[:, x])[0]
        if len(col):
            y = int(col.max())
            k = 0
            while y + 1 < H and k < 10 and c["alpha"][y + 1, x] and not c["skin"][y + 1, x] and \
                    (c["outline"][y + 1, x] or c["grey"][y + 1, x] or c["white"][y + 1, x]):
                y += 1
                k += 1
            cut[x] = y
    span = np.nonzero(cut >= 0)[0]
    if not len(span):
        return np.zeros((H, W), bool), {"found": False, "reason": "no cuff edge"}
    x0, x1 = min(int(span.min()), int(cx_cols.min())), max(int(span.max()), int(cx_cols.max()))
    # columns of the sleeve without cuff pixels (its side outlines): interpolate the cuff edge across them
    gaps = np.arange(x0, x1 + 1)
    gaps = gaps[cut[gaps] < 0]
    if len(gaps):
        cut[gaps] = np.rint(np.interp(gaps, span, cut[span])).astype(int)
    span = np.arange(x0, x1 + 1)
    # side outlines of the sleeve/cuff box: extend the cut a few columns out, removing only non-skin pixels
    mask = np.zeros((H, W), bool)
    for x in range(W):
        if cut[x] >= 0:
            yc = int(round(cut[x] + cut_offset))
            mask[:max(yc + 1, 0), x] = True
    for x in list(range(max(0, x0 - 12), x0)) + list(range(x1 + 1, min(W, x1 + 13))):
        ref = cut[x0] if x < x0 else cut[x1]
        yc = int(round(ref + cut_offset))
        colm = np.arange(H) <= yc
        mask[:, x] |= colm & c["alpha"][:, x] & ~c["skin"][:, x]
    mask &= c["alpha"] | (rgba[..., 3] > 0)
    central = span[(span >= x0 + 0.25 * (x1 - x0)) & (span <= x1 - 0.25 * (x1 - x0))]
    cy = float(np.median(cut[central])) + cut_offset
    # a thumb raised in front of the cuff belongs to the hand: keep skin connected to the hand below the cut, with
    # its own outline
    sl, _ = ndimage.label(c["skin"])
    below = np.zeros((H, W), bool)
    below[min(H, int(cy) + 3):] = True
    keep_l = np.unique(sl[below & c["skin"]])
    keep = np.isin(sl, keep_l[keep_l > 0])
    above = np.zeros((H, W), bool)
    for x in range(W):
        if cut[x] >= 0:
            above[:max(int(round(cut[x] + cut_offset)) - 1, 0), x] = True  # strictly above the cuff's bottom line
    keep_up = keep & mask & above  # skin above the cut that belongs to the hand (a raised thumb)
    ring = ndimage.binary_dilation(keep_up, iterations=4) & c["outline"] & ~cloth & above
    mask &= ~(keep | ring)
    anchor = (float((x0 + x1) / 2), cy)
    hand = (rgba[..., 3] > 32) & ~mask
    rows = hand[int(cy) + 2:int(cy) + 10]
    wx = np.nonzero(rows.any(0))[0]
    width = float(wx.max() - wx.min() + 1) if len(wx) else 0.0
    left = int((mask & c["skin"]).sum())
    return mask, {"found": True, "anchor": [round(anchor[0], 2), round(anchor[1], 2)], "cut_span": [x0, x1],
                  "sleeve_px": int(sleeve.sum()), "cuff_px": int(cuff.sum()), "masked_px": int(mask.sum()),
                  "skin_masked_px": left, "wrist_width_px": width,
                  "remaining_cloth_px": int(((c["navy"] | c["white"]) & hand & (np.arange(H)[:, None] < cy + 30)).sum())}


_MASKS: dict[tuple, tuple[np.ndarray, dict[str, Any]]] = {}


def cuff_mask_cached(store: "HandStore", uid: str, v: int | None, orientation: str, cut_offset: float = 0.0):
    """cuff_mask for a stored drawing version, cached by the file hash (masks are pure functions of the pixels)."""
    ver = store.version(uid, v)
    key = (ver["sha256"], orientation, float(cut_offset))
    if key not in _MASKS:
        _MASKS[key] = cuff_mask(store.image(uid, ver["v"]), orientation, cut_offset)
        while len(_MASKS) > 128:
            _MASKS.pop(next(iter(_MASKS)))
    return _MASKS[key]


def hand_only(rgba: np.ndarray, mask: np.ndarray, defringe: bool = True) -> np.ndarray:
    out = rgba.copy()
    out[mask, 3] = 0
    if defringe:
        out[matte_halo(rgba), 3] = 0
    return out


# -- placement at the character's wrist -----------------------------------------------------------------
@dataclass
class Placement:
    anchor: tuple[float, float]  # sprite px: the wrist edge (cut-line centre)
    point: tuple[float, float]  # world (rest canvas) px the anchor goes to
    angle: float  # deg (``ang`` convention): sprite 'down' -> this world direction
    scale: float

    def matrix(self) -> np.ndarray:
        t = math.radians(self.angle)
        c, s = math.cos(t), math.sin(t)
        R = np.array([[c, -s], [s, c]]) * self.scale
        A = np.asarray(self.anchor, float)
        P = np.asarray(self.point, float)
        M = np.eye(3)
        M[:2, :2] = R
        M[:2, 2] = P - R @ A
        return M

    def to_json(self) -> dict[str, Any]:
        return {"anchor": [round(v, 2) for v in self.anchor], "point": [round(v, 2) for v in self.point],
                "angle_deg": round(self.angle, 3), "scale": round(self.scale, 5)}


def place(sprite: np.ndarray, pl: Placement, shape) -> np.ndarray:
    """Full-canvas RGBA (rest frame) with the sprite mapped by the placement (bicubic, premultiplied)."""
    M = pl.matrix()
    Mi = np.linalg.inv(M)
    im = Image.fromarray(sprite, "RGBA").convert("RGBa")
    out = im.transform((shape[1], shape[0]), Image.Transform.AFFINE,
                       (Mi[0, 0], Mi[0, 1], Mi[0, 2], Mi[1, 0], Mi[1, 1], Mi[1, 2]), resample=Image.Resampling.BICUBIC)
    return np.asarray(out.convert("RGBA"))


def reference_wrist(sk, layers: dict[str, np.ndarray], side: str, cover: np.ndarray | None = None,
                    source: np.ndarray | None = None, band: float = 25.0) -> dict[str, Any]:
    """The character's wrist for stored drawings: its own shirt cuff, from the approved skeleton and the original
    pixels. The hand bone gives the wrist joint and axis; the white cuff pixels inside the forearm layer near the
    wrist give the sleeve opening's centre, width and distal edge. Falls back to the forearm layer's end when the
    source has no white cuff there."""
    hb = sk.bone(f"hand_{side}")
    W = np.asarray(hb.head, float)
    u = (np.asarray(hb.tail, float) - W) / max(hb.length, 1e-6)
    n = np.array([-u[1], u[0]])
    fa = layers[f"forearm_{side}"][..., 3] > 127
    method = "white_cuff"
    pts = None
    if source is not None:
        rgb = source[..., :3].astype(np.int16)
        white = (rgb.mean(-1) > 190) & ((rgb.max(-1) - rgb.min(-1)) < 40) & fa
        ys, xs = np.nonzero(white)
        P = np.stack([xs, ys], 1).astype(float)
        t, q = (P - W) @ u, (P - W) @ n
        sel = (np.abs(t) <= band) & (np.abs(q) < 0.9 * hb.length)
        if sel.sum() >= 100:
            pts = (t[sel], q[sel])
    if pts is None:
        method = "forearm_end"
        cov = cover if cover is not None else fa
        ys, xs = np.nonzero(cov)
        P = np.stack([xs, ys], 1).astype(float)
        t, q = (P - W) @ u, (P - W) @ n
        near = (np.abs(q) < 0.9 * hb.length) & (t > -1.5 * hb.length) & (t < hb.length)
        tm = float(np.percentile(t[near], 99.5))
        sel = near & (t >= tm - 6)
        pts = (t[sel], q[sel])
    t, q = pts
    q0, q1 = float(np.percentile(q, 1)), float(np.percentile(q, 99))
    bins = np.floor((q - q0) / 4).astype(int)
    edges = [float(t[bins == k].max()) for k in np.unique(bins) if (bins == k).sum() >= 3]
    t_edge = float(np.median(edges)) if edges else float(t.max())
    edge = W + u * t_edge + n * ((q0 + q1) / 2)
    return {"method": method, "wrist_joint": W.tolist(), "axis": u.tolist(), "edge": edge.tolist(),
            "cuff_width_px": q1 - q0, "cuff_edge_t_px": t_edge,
            "angle_deg": float(math.degrees(math.atan2(-u[0], u[1])))}


def auto_placement(sprite: np.ndarray, info: dict[str, Any], ref: dict[str, Any], scale: float,
                   tuck: float = TUCK_PX) -> Placement:
    """Rotation: sprite 'down' onto the hand bone axis. Scale: per side (character cuff width / median width of
    the drawings' own cuffs). Position: the drawing's cut-line centre onto the character's cuff edge centre, tucked
    ``tuck`` px under the sleeve."""
    u = np.asarray(ref["axis"], float)
    point = np.asarray(ref["edge"], float) - tuck * u
    return Placement(tuple(info["anchor"]), (float(point[0]), float(point[1])), float(ref["angle_deg"]), float(scale))


def corrected(pl: Placement, corr: dict[str, Any] | None) -> Placement:
    """User corrections about the placed wrist point: dx, dy (px), rot (deg), scale (multiplier)."""
    if not corr:
        return pl
    return Placement(pl.anchor, (pl.point[0] + float(corr.get("dx", 0.0)), pl.point[1] + float(corr.get("dy", 0.0))),
                     pl.angle + float(corr.get("rot", 0.0)), pl.scale * float(corr.get("scale", 1.0)))


PLACEMENT_LIMITS = {"dx": (-80.0, 80.0), "dy": (-80.0, 80.0), "rot": (-180.0, 180.0), "scale": (0.5, 2.0),
                    # sleeve-free drawings (WristAlignmentController): extra overlap under the cuff (px) and the
                    # manual wrist pivot on the drawing (sprite px)
                    "overlap": (-6.0, 24.0), "pivot_x": (-60.0, 60.0), "pivot_y": (-60.0, 60.0)}
MASK_LIMITS = {"cut_offset": (-30.0, 60.0)}


def check_placement(f: dict[str, Any]) -> list[str]:
    errs = []
    for k, v in f.items():
        if k not in PLACEMENT_LIMITS:
            errs.append(f"placement field {k!r} unknown; use {sorted(PLACEMENT_LIMITS)}")
            continue
        lo, hi = PLACEMENT_LIMITS[k]
        if not isinstance(v, (int, float)) or not lo <= float(v) <= hi:
            errs.append(f"placement {k} must be a number in [{lo}, {hi}] (scale is a uniform multiplier)")
    return errs


def check_mask(f: dict[str, Any]) -> list[str]:
    errs = []
    for k, v in f.items():
        if k == "enabled":
            if not isinstance(v, bool):
                errs.append("mask enabled must be true or false")
        elif k == "defringe":
            if not isinstance(v, bool):
                errs.append("mask defringe must be true or false")
        elif k == "cut_offset":
            lo, hi = MASK_LIMITS[k]
            if not isinstance(v, (int, float)) or not lo <= float(v) <= hi:
                errs.append(f"cut_offset must be in [{lo}, {hi}] sprite px")
        else:
            errs.append(f"mask field {k!r} unknown")
    return errs


# -- derived layers for the engine ------------------------------------------------------------------------
_DERIVED: dict[tuple, tuple[np.ndarray, dict[str, Any]]] = {}


def layer_id(side: str, uid: str, v: int) -> str:
    return f"hand_{side}__hs.{uid.replace('/', '.')}.v{v}"


def _sleeve_free(a: dict[str, Any]) -> bool:
    return a.get("base_orientation") == "wrist_edge"


def side_scales(store: HandStore, ref: dict[str, dict[str, Any]], assets: list[dict[str, Any]],
                st: dict[str, Any], layers: dict[str, np.ndarray] | None = None,
                covers: dict[str, np.ndarray] | None = None) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], Any]]:
    """One scale per library and side, so every pose of a side has the same size (no size pop between drawings of
    a library). Keyed (library_id, side).

    Primary rule (``open_palm_area``): the library's open palm, placed at the wrist, shows the same visible area as
    the character's original open hand (iterated at rest, with the sleeve composited over both). Switching from the
    original hand to a stored one then keeps the hand size. Fallbacks: sleeve-attached libraries ``cuff_width``
    (character cuff width / median width of the drawings' own cuffs), sleeve-free ones ``wrist_width`` (character
    cuff width / median wrist-opening width)."""
    from .wristalign import WristAlignmentController  # noqa: PLC0415

    out: dict[tuple[str, str], float] = {}
    info: dict[tuple[str, str], Any] = {}
    wac = WristAlignmentController(ref)
    for lib in sorted({a["library_id"] for a in assets}):
        for s in ("r", "l"):
            if s not in ref:
                continue
            side_assets = [a for a in assets if a["side"] == s and a["library_id"] == lib]
            if not side_assets:
                continue
            palm = next((a for a in side_assets if a["pose"] == "open_palm"), None)
            have_layers = layers is not None and f"hand_{s}" in layers
            if all(_sleeve_free(a) for a in side_assets):
                geoms = [store.geometry(a["uid"], st["versions"].get(a["uid"])) for a in side_assets]
                widths = [wac.opening(g)["width_px"] for g in geoms if g.get("wrist_edge_px")]
                skey = ("scale_we", lib, s, json.dumps(ref[s], sort_keys=True, default=float),
                        tuple(store.version(a["uid"], st["versions"].get(a["uid"]))["sha256"] for a in side_assets))
                if skey in _DERIVED:
                    out[(lib, s)], info[(lib, s)] = _DERIVED[skey]
                    continue
                pimg = pgeom = target = cov = None
                if palm is not None and have_layers:
                    pimg = store.image(palm["uid"], st["versions"].get(palm["uid"]))
                    pgeom = store.geometry(palm["uid"], st["versions"].get(palm["uid"]))
                    fa = layers[f"forearm_{s}"][..., 3] > 127
                    target = float(((layers[f"hand_{s}"][..., 3] > 127) & ~fa).sum())
                    cov = (covers or {}).get(s)
                    cov = fa if cov is None else cov
                shape = next(iter(layers.values())).shape[:2] if layers else (1, 1)
                sc, inf = wac.side_scale(s, pimg, pgeom, target, cov, shape, widths)
                out[(lib, s)], info[(lib, s)] = sc, inf
                _DERIVED[skey] = (sc, inf)
                continue
            widths = []
            for a in side_assets:
                _, mi = cuff_mask_cached(store, a["uid"], st["versions"].get(a["uid"]), a.get("base_orientation", "sleeve_top_hand_down"))
                if mi.get("found"):
                    widths.append(mi["cut_span"][1] - mi["cut_span"][0] + 1)
            w = float(np.median(widths)) if widths else 0.0
            scale = ref[s]["cuff_width_px"] / w if w > 0 else 1.0
            method = "cuff_width"
            skey = None
            if palm is not None and have_layers:
                img = store.image(palm["uid"], st["versions"].get(palm["uid"]))
                m = {"enabled": True, "defringe": True, "cut_offset": 0.0, **st["mask"].get(palm["uid"], {})}
                mask, mi = cuff_mask_cached(store, palm["uid"], st["versions"].get(palm["uid"]),
                                            palm.get("base_orientation", "sleeve_top_hand_down"), float(m["cut_offset"]))
                skey = ("scale", s, store.version(palm["uid"], st["versions"].get(palm["uid"]))["sha256"],
                        json.dumps(m, sort_keys=True), json.dumps(ref[s], sort_keys=True, default=float), round(scale, 8))
                if skey in _DERIVED:
                    out[(lib, s)], info[(lib, s)] = _DERIVED[skey]
                    continue
                if mi.get("found"):
                    src = hand_only(img, mask, bool(m["defringe"]))
                    fa = layers[f"forearm_{s}"][..., 3] > 127
                    target = float(((layers[f"hand_{s}"][..., 3] > 127) & ~fa).sum())
                    cov = covers.get(s) if covers else None
                    cov = fa if cov is None else cov
                    shape = layers[f"hand_{s}"].shape[:2]
                    for _ in range(3):
                        placed = place(src, auto_placement(src, mi, ref[s], scale), shape)
                        area = float(((placed[..., 3] > 127) & ~cov).sum())
                        if area <= 0:
                            break
                        scale *= math.sqrt(target / area)
                    method = "open_palm_area"
            out[(lib, s)] = scale
            info[(lib, s)] = {"scale": round(scale, 5), "method": method,
                              "cuff_scale": round(ref[s]["cuff_width_px"] / w, 5) if w else None}
            if skey is not None:
                _DERIVED[skey] = (scale, info[(lib, s)])
    return out, info


def active_assets(store: HandStore, st: dict[str, Any]) -> list[dict[str, Any]]:
    """Stored drawings that are switch candidates: not deleted, in an active library."""
    libs = library_status(store, st)
    return [a for a in store.assets() if a["uid"] not in set(st.get("deleted", []))
            and libs.get(a["library_id"], {}).get("status", "active") == "active"]


def derive_layers(store: HandStore, st: dict[str, Any], sk, layers: dict[str, np.ndarray],
                  approvals: dict[str, Any], refine=None, cover_masks: dict[str, np.ndarray] | None = None,
                  source: np.ndarray | None = None, side_map: dict[str, str] | None = None
                  ) -> tuple[dict[str, np.ndarray], list[dict[str, Any]]]:
    """Rest-frame layers for every candidate drawing (not deleted, library active), plus candidate records for the
    hand library. Sleeve-attached drawings are cut at their own cuff (``cuff_mask``) and matched cuff edge to cuff
    edge; sleeve-free ones are placed by their wrist opening (``WristAlignmentController``).
    ``refine(side, rgba) -> (dx, dy, error)`` is the visible-attachment search (HandPoseLibrary.best_translation).
    ``side_map`` (Phase 3.7, from HandAssetMetadata): uid -> the side a drawing is offered on when its inspected
    handedness differs from the side it was stored for (it is placed on that side's cuff instead)."""
    from .wristalign import WristAlignmentController  # noqa: PLC0415

    st = {**default_store_state(), **(st or {})}
    assets = active_assets(store, st)
    if not assets:
        return {}, []
    covers = {s: (cover_masks or {}).get(s) for s in ("r", "l")}
    ref = {s: reference_wrist(sk, layers, s, covers.get(s), source) for s in ("r", "l") if f"forearm_{s}" in layers}
    scales, scale_info = side_scales(store, ref, assets, st, layers, covers)
    wac = WristAlignmentController(ref)
    lib_order = {lib: rec["order"] for lib, rec in store.libraries().items()}
    shape = next(iter(layers.values())).shape[:2]
    skey = tuple(round(float(x), 3) for s in ("r", "l") for b in (f"hand_{s}", f"forearm_{s}")
                 for x in (*sk.bone(b).head, *sk.bone(b).tail))
    out, cands = {}, []
    from .motion.switches import _status  # noqa: PLC0415 - avoid an import cycle at module load

    for a in sorted(assets, key=lambda a: (-lib_order.get(a["library_id"], 0), a["uid"])):  # newest library first
        s = (side_map or {}).get(a["uid"], a["side"])
        if s not in ref:
            continue
        ver = store.version(a["uid"], st["versions"].get(a["uid"]))
        corr = st["placement"].get(a["uid"], {})
        sc = scales[(a["library_id"], s)]
        if _sleeve_free(a):
            geom = dict(ver.get("geometry") or {})
            key = ("we", a["uid"], ver["sha256"], json.dumps(corr, sort_keys=True), skey, round(sc, 6), shape)
            if key not in _DERIVED:
                img = store.image(a["uid"], ver["v"])
                rgba, pm = wac.layer(s, img, geom, sc, corr, shape)
                meta = {**pm, "correction": corr, "side_scale": round(sc, 5), "scale_rule": scale_info.get((a["library_id"], s)),
                        "reference_wrist": ref[s], "geometry": geom,
                        "mask": {"found": True, "anchor": pm["params"]["pivot_px"],
                                 "wrist_width_px": pm["params"]["wrist_width_px"], "opening": pm["params"]["opening"]}}
                _DERIVED[key] = (rgba, meta)
        else:
            m = {"enabled": True, "defringe": True, "cut_offset": 0.0, **st["mask"].get(a["uid"], {})}
            key = (a["uid"], ver["sha256"], json.dumps(m, sort_keys=True), json.dumps(corr, sort_keys=True), skey,
                   round(sc, 6), shape)
            if key not in _DERIVED:
                img = store.image(a["uid"], ver["v"])
                mask, info = cuff_mask_cached(store, a["uid"], ver["v"], a.get("base_orientation", "sleeve_top_hand_down"),
                                              float(m["cut_offset"]))
                if not info.get("found"):
                    info = {"found": False, "anchor": list(a.get("manifest_anchor", [img.shape[1] / 2, img.shape[0] / 3]))}
                    mask = np.zeros(img.shape[:2], bool)
                src = hand_only(img, mask if m["enabled"] else np.zeros_like(mask), bool(m["defringe"]))
                auto = auto_placement(src, info, ref[s], sc)
                placed = place(src, auto, shape)
                ref_shift = (0.0, 0.0, None)
                if refine is not None:
                    ref_shift = refine(s, placed)
                    if ref_shift is not None:
                        auto = Placement(auto.anchor, (auto.point[0] + ref_shift[0], auto.point[1] + ref_shift[1]),
                                         auto.angle, auto.scale)
                final = corrected(auto, corr)
                rgba = place(src, final, shape)
                meta = {"mask": info, "auto": auto.to_json(), "final": final.to_json(), "correction": corr,
                        "mask_settings": m, "refine_px": list(ref_shift[:2]) if ref_shift else [0, 0],
                        "manifest_anchor": a.get("manifest_anchor"),
                        "manifest_anchor_delta_px": (None if not info.get("found") else
                                                     [round(info["anchor"][0] - a["manifest_anchor"][0], 1),
                                                      round(info["anchor"][1] - a["manifest_anchor"][1], 1)]),
                        "halo_removed_px": int(matte_halo(img).sum()) if m["defringe"] else 0,
                        "side_scale": round(sc, 5), "scale_rule": scale_info.get((a["library_id"], s)),
                        "reference_wrist": ref[s]}
                _DERIVED[key] = (rgba, meta)
        while len(_DERIVED) > 160:
            _DERIVED.pop(next(iter(_DERIVED)))
        rgba, meta = _DERIVED[key]
        lid = layer_id(s, a["uid"], ver["v"])
        out[lid] = rgba
        geom = dict(ver.get("geometry") or {})
        cands.append({"cid": f"store:{a['uid']}", "uid": a["uid"], "v": ver["v"], "side": s, "pose": a["pose"],
                      "stored_side": a["side"],
                      "layer": lid, "status": _status(approvals, approval_key(a["uid"], ver["v"])),
                      "approval_item": approval_key(a["uid"], ver["v"]),
                      "category": "G_ai_generated_concept", "label": st["labels"].get(a["uid"], a["asset_id"]),
                      "library_id": a["library_id"], "sleeve": "none" if _sleeve_free(a) else "attached",
                      "library_rank": -lib_order.get(a["library_id"], 0),
                      "features": geom.get("features", {}), "view": geom.get("view"),
                      "handedness": geom.get("handedness"), "derived": geom.get("derived"),
                      "flags": geom.get("flags", []),
                      "provenance": {**a.get("provenance", {}), "library_id": a["library_id"], "asset_id": a["asset_id"],
                                     "version": ver["v"], "sha256": ver["sha256"], "file": ver["file"],
                                     "origin": ver["origin"], "sheet_cell": geom.get("sheet_cell"),
                                     "derived": geom.get("derived")},
                      "placement": meta})
    return out, cands


def engine_input(pkg: str | Path, st: dict[str, Any] | None, sk, layers: dict[str, np.ndarray],
                 variants: dict[str, np.ndarray], approvals: dict[str, Any], source: np.ndarray | None = None,
                 hand_meta_state: dict[str, Any] | None = None, use_metadata: bool = True) -> dict[str, Any] | None:
    """The ``hand_store`` argument of MotionEngine for a package: derived layers, candidate records and the user's
    active-candidate choices. None when the package has no store.

    Phase 3.7: the package's HandAssetMetadata (``analysis/hand_asset_metadata.json``, from ``autorig hand-audit``)
    plus the user's corrections (project state ``hand_metadata``) decide which side a drawing is offered on; the
    resolved metadata is returned as ``hand_meta`` and attached to the engine's hand variants."""
    store_dir = Path(pkg) / STORE_DIR / "index.json"
    if not store_dir.is_file():
        return None
    from .motion.switches import HandPoseLibrary, best_translation  # noqa: PLC0415

    store = HandStore(pkg)
    state = {**default_store_state(), **(st or {})}
    ref_lib = HandPoseLibrary(sk, layers, {})
    covers, extra = {}, {}
    for s in ("r", "l"):
        c = variants.get(f"forearm_{s}__trim")
        if c is None:
            c = layers.get(f"forearm_{s}")
        if c is not None and f"forearm_{s}" in layers:
            cuff = cuff_cover(sk, layers, c, s, source)
            extra[f"forearm_{s}__hscuff"] = cuff
            c = cuff
        covers[s] = c[..., 3] > 127 if c is not None else None

    def refine(side: str, placed: np.ndarray):
        ref = ref_lib.ref_measure.get(side)
        if ref is None or covers.get(side) is None:
            return None
        return best_translation(sk, side, placed, covers[side], ref["attachment"], radius=8)

    # placement = wrist-edge match only. The visible-band search (``refine``) is not applied automatically: for
    # hands shaped unlike the reference (grip, cupped, a raised thumb) it moves the drawing off the wrist. It stays
    # available as the editor's explicit Auto-align tool.
    del refine
    from . import handmeta  # noqa: PLC0415

    inv = handmeta.load(pkg) if use_metadata else None
    if inv is not None:
        inv = handmeta.apply_corrections(inv, (hand_meta_state or {}).get("corrections"))
    lay, cands = derive_layers(store, state, sk, layers, approvals, None, covers, source, handmeta.side_map(inv))
    idmap = gesture_id_map(store, state, cands)
    use, remapped = dict(state["use"]), []
    for k, cid in list(use.items()):  # a choice of an archived drawing follows the map to its replacement
        hit = next((m for m in idmap if m["old"] == cid and m["side"] == k.split("/")[0]), None)
        if hit is not None and hit["new"] and not any(c["cid"] == cid for c in cands):
            use[k] = hit["new"]
            remapped.append({"slot": k, "from": cid, "to": hit["new"], "reason": hit["reason"]})
    libs = library_status(store, state)
    return {"layers": {**extra, **lay}, "candidates": cands, "use": use, "use_remapped": remapped,
            "gesture_id_map": idmap, "libraries": libs, "revision": store.revision,
            "phase2_active": libs[PHASE2_LIBRARY]["status"] == "active", "hand_meta": inv}


def gesture_id_map(store: HandStore, st: dict[str, Any], cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Old gesture identifiers -> the drawing that replaces them: every drawing of an archived (or superseded)
    library and every Phase 2 sheet hand, mapped by side and pose to the active sleeve-free candidate (newest
    library first). Rollback: restoring a library (``handstore.library``) makes its drawings candidates again."""
    from .motion.switches import HAND_POSES  # noqa: PLC0415

    libs = library_status(store, st)
    active = {}
    for c in sorted(cands, key=lambda c: c.get("library_rank", 0)):
        if c.get("sleeve") == "none":
            active.setdefault((c["side"], c["pose"]), c["cid"])
    out = []
    for a in store.assets():
        lib = libs.get(a["library_id"], {})
        if lib.get("status") == "archived":
            out.append({"old": f"store:{a['uid']}", "side": a["side"], "pose": a["pose"],
                        "new": active.get((a["side"], a["pose"])),
                        "reason": f"library {a['library_id']} archived ({lib.get('reason')})"})
    p2 = libs.get(PHASE2_LIBRARY, {})
    if p2.get("status") == "archived":
        for s in ("r", "l"):
            for pose, drawing in HAND_POSES.items():
                if drawing != "open":
                    out.append({"old": f"phase2:{drawing}", "side": s, "pose": pose, "new": active.get((s, pose)),
                                "reason": f"Phase 2 sheet hand (cut with its cuff) archived ({p2.get('reason')})"})
    return out


_COVERS: dict[tuple, np.ndarray] = {}


CUFF_OUTLINE_PX = 4.0  # the white cuff's own outline beyond its distal edge stays in the cover layer
SPECK_PX = 40  # isolated pieces this small near the wrist are segmentation specks


def cuff_cover(sk, layers: dict[str, np.ndarray], forearm: np.ndarray, side: str, source: np.ndarray | None) -> np.ndarray:
    """Forearm layer for stored hands (``forearm_<side>__hscuff``): the trimmed forearm without anything beyond the
    shirt cuff. Segmentation left the original hand's skin, its outline (ticks at the cuff corners) and a few specks
    in the forearm layer; over a replacement hand they show as a patch or dashes of the old hand. Removed: pixels
    beyond the white cuff's distal edge, measured per 4 px across the sleeve (plus the cuff's own outline,
    CUFF_OUTLINE_PX), the old hand's skin and the dark pixels touching it, and pieces smaller than SPECK_PX that are
    cut off from the sleeve near the wrist. Pixels are only removed, never added."""
    key = (side, hashlib.blake2b(forearm.tobytes(), digest_size=12).hexdigest(),
           tuple(np.round(np.r_[sk.bone(f"hand_{side}").head, sk.bone(f"hand_{side}").tail], 3)))
    if key in _COVERS:
        return _COVERS[key]
    ref = reference_wrist(sk, layers, side, None, source)
    W, u = np.asarray(ref["wrist_joint"]), np.asarray(ref["axis"])
    n = np.array([-u[1], u[0]])
    hb = sk.bone(f"hand_{side}")
    c = classify(forearm)
    H, Wd = forearm.shape[:2]
    yy, xx = np.mgrid[0:H, 0:Wd]
    t = (xx - W[0]) * u[0] + (yy - W[1]) * u[1]
    q = (xx - W[0]) * n[0] + (yy - W[1]) * n[1]
    beyond = t > ref["cuff_edge_t_px"] + 1.0
    skin = c["skin"] & beyond
    dark_near = ndimage.binary_dilation(skin, iterations=3) & beyond & ~(c["navy"] | c["white"]) & (forearm[..., 3] > 0)
    remove = skin | dark_near
    # the cuff's distal edge per 4 px strip across the sleeve (interpolated; held at the span ends)
    wsel = c["white"] & (np.abs(t - ref["cuff_edge_t_px"]) <= 25) & (np.abs(q) < 0.9 * hb.length)
    if wsel.sum() >= 50:
        tq, qq = t[wsel], q[wsel]
        q0 = float(qq.min())
        bins = np.floor((qq - q0) / 4).astype(int)
        ks = np.unique(bins)
        centres = np.array([q0 + 4 * k + 2 for k in ks if (bins == k).sum() >= 3])
        edges = np.array([float(tq[bins == k].max()) for k in ks if (bins == k).sum() >= 3])
        if len(edges) >= 2:
            edge_q = np.interp(q, centres, edges)  # held constant outside the cuff span
            near = (np.abs(q) < 1.2 * hb.length) & (t < ref["cuff_edge_t_px"] + 1.5 * hb.length)
            remove |= near & (t > edge_q + CUFF_OUTLINE_PX) & (forearm[..., 3] > 0)
            # outline ticks at the cuff corners (thin strokes of the old hand's outline): thin protrusions in the
            # white cuff's band and beyond. A 5x5 opening removes strokes up to ~4 px wide; the solid sleeve and
            # cuff (and the cuff's outline, which borders the white) are unchanged by it
            al0 = (forearm[..., 3] > 0) & ~remove
            zone = near & (t > float(np.percentile(tq, 5)) - 2.0)
            opened = ndimage.binary_opening(al0, structure=np.ones((5, 5), bool))
            remove |= zone & al0 & ~opened & ~c["white"] & ~c["navy"]
    out = forearm.copy()
    out[remove, 3] = 0
    al = out[..., 3] > 0
    lab, nl = ndimage.label(al)
    if nl > 1:
        sizes = ndimage.sum(al, lab, range(1, nl + 1))
        dist = np.hypot(xx - W[0], yy - W[1])
        for i in np.nonzero(sizes < SPECK_PX)[0]:
            comp = lab == i + 1
            if dist[comp].min() < 1.5 * hb.length:
                out[comp, 3] = 0
    _COVERS[key] = out
    return out


def posed_seam(res: dict[str, Any], eng, side: str, layer: str, beyond: float = 3.0) -> dict[str, Any]:
    """Seam at the wrist in a rendered pose, sampled on a fixed line ``beyond`` px past the character's sleeve cuff
    (perpendicular to the hand bone): the centre and width of the hand where it meets the cuff, and the white
    cuff-like pixels the hand layer itself shows within 40 px (a second cuff drawn by the hand would show here).
    Comparing a variant with the original hand at the same pose gives the switching pop."""
    r = res["render"]
    pad = r["pad"]
    ha = r["alphas"].get(layer)
    fa = next((r["alphas"][c] for c in (f"forearm_{side}__hscuff", f"forearm_{side}__trim", f"forearm_{side}")
               if c in r["alphas"]), None)
    if ha is None or fa is None:
        return {"status": "UNCHECKED"}
    vis = ha & ~fa
    pose = res["pose"]
    hb = eng.sk.bone(f"hand_{side}")
    Wm = eng.solver.K.world(eng.sk.idx[f"hand_{side}"], pose)
    Wp = Wm[:2, :2] @ np.asarray(hb.head, float) + Wm[:2, 2] + pad
    u = Wm[:2, :2] @ ((np.asarray(hb.tail, float) - hb.head) / max(hb.length, 1e-6))
    n = np.array([-u[1], u[0]])
    fy, fx = np.nonzero(fa)
    F = np.stack([fx, fy], 1).astype(float)
    tf, qf = (F - Wp) @ u, (F - Wp) @ n
    near = (np.abs(qf) < 0.8 * hb.length) & (tf > -1.5 * hb.length) & (tf < hb.length)
    if not near.any():
        return {"status": "UNCHECKED"}
    tcuff = float(np.percentile(tf[near], 99.5))
    qs = np.arange(-1.2 * hb.length, 1.2 * hb.length, 0.5)

    def line(mask, t):
        pts = Wp[None, :] + u[None, :] * t + n[None, :] * qs[:, None]
        xi, yi = np.rint(pts[:, 0]).astype(int), np.rint(pts[:, 1]).astype(int)
        ok = (xi >= 0) & (yi >= 0) & (xi < mask.shape[1]) & (yi < mask.shape[0])
        hit = np.zeros(len(qs), bool)
        hit[ok] = mask[yi[ok], xi[ok]]
        return hit

    cuff = line(fa, tcuff - 15.0)  # the sleeve opening (the forearm mask's very end is rounded)
    lab, nl = ndimage.label(cuff)
    if not nl:
        return {"status": "UNCHECKED"}
    k = max(range(1, nl + 1), key=lambda i: (lab == i).sum())
    ci = np.nonzero(lab == k)[0]
    c0, c1 = float(qs[ci[0]]), float(qs[ci[-1]])
    img = r["image"][..., :3].astype(np.int16)
    lum, sat = img.mean(-1), img.max(-1) - img.min(-1)
    mine = r["top"] == r["order"].index(layer)
    own_white = int((((lum > 165) & (sat < 45)) & mine).sum())
    own_cloth = int(((((lum > 165) & (sat < 45)) | ((lum >= 18) & (lum < 95) & (img[..., 2] >= img[..., 0] - 6) &
                                                     (sat < 45))) & mine).sum())
    hand = line(vis, tcuff + beyond)
    hi_ = np.nonzero(hand)[0]
    if not len(hi_):
        return {"status": "OK", "covered": False, "fill": 0.0, "cuff_width_px": round(c1 - c0, 2),
                "own_white_px": own_white, "own_cloth_px": own_cloth, "own_white_px_near_wrist": None}
    h0, h1 = float(qs[hi_[0]]), float(qs[hi_[-1]])
    inside = (qs >= c0) & (qs <= c1)
    fill = float(hand[inside].mean()) if inside.any() else 0.0
    white = (lum > 165) & (sat < 45) & mine
    ys, xs = np.nonzero(vis)
    P = np.stack([xs, ys], 1).astype(float)
    t = (P - Wp) @ u
    band = (t >= tcuff) & (t <= tcuff + 40)
    return {"status": "OK", "covered": True, "cuff_width_px": round(c1 - c0, 2), "fill": round(fill, 3),
            "own_cloth_px": own_cloth, "own_white_px": own_white,
            "overhang_px": [round(max(0.0, c0 - h0), 2), round(max(0.0, h1 - c1), 2)],
            "hand_center_q_px": round((h0 + h1) / 2, 2), "cuff_center_q_px": round((c0 + c1) / 2, 2),
            "own_white_px_near_wrist": int(white[ys[band], xs[band]].sum())}
