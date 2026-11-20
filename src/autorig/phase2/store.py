"""OriginalPixelPreservation: the artwork store.

Pixel categories (never mixed in one file):
  A  original   artwork/original/<component>.png       immutable copies of extracted source pixels
  B  generated  artwork/reconstructed/<component>__<ext>.png + masks/provenance/<component>__<ext>.png
  C  corrected  artwork/corrections/<component>__<id>.png  (user-approved corrections only)
  D  working    artwork/working/                         scratch; never referenced by any manifest

Invariants enforced here (and tested):
* every layer is full-canvas (source W x H) - no resizing, no offsets;
* an original is written once; its pixel hash is recorded and re-verified;
* a generated extension can only ADD pixels inside its reconstruction mask and
  never on its own component's original opaque pixels - anything else is
  discarded and counted;
* composition draws the original OVER its extensions, so the visible
  original can never change;
* rollback deletes every extension/correction of a component, restoring A.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..phase1 import masks as M

STATUSES = ("pending_review", "approved", "rejected", "superseded", "alternative")


class PreservationError(RuntimeError):
    pass


def pixel_hash(rgba: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(rgba).tobytes() + str(rgba.shape).encode()).hexdigest()


@dataclass
class Extension:
    id: str
    component: str
    image: str
    provenance_mask: str
    region_id: str | None
    method: str
    status: str
    pixels: int
    discarded_outside_region: int
    discarded_on_original: int
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class ArtworkStore:
    def __init__(self, root: str | Path, width: int, height: int):
        self.root = Path(root)
        self.w, self.h = width, height
        self.registry_path = self.root / "artwork" / "store.json"
        for d in ("artwork/original", "artwork/reconstructed", "artwork/corrections", "artwork/working",
                  "masks/provenance"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        self.originals: dict[str, dict[str, Any]] = {}
        self.extensions: list[Extension] = []
        self.history: list[dict[str, Any]] = []
        if self.registry_path.is_file():
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            self.originals = data["originals"]
            self.extensions = [Extension(**e) for e in data["extensions"]]
            self.history = data.get("history", [])

    # -- persistence -----------------------------------------------------------------
    def save(self) -> None:
        doc = {"store_version": "1.0.0", "canvas": [self.w, self.h], "originals": self.originals,
               "extensions": [asdict(e) for e in self.extensions], "history": self.history}
        self.registry_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    def _check_canvas(self, arr: np.ndarray, what: str) -> None:
        if arr.shape[:2] != (self.h, self.w):
            raise PreservationError(f"{what}: shape {arr.shape[:2]} != canvas {(self.h, self.w)} (no resizing allowed)")

    def _log(self, action: str, **kw) -> None:
        self.history.append({"action": action, "at": time.time(), **kw})

    # -- A: originals ------------------------------------------------------------------
    def add_original(self, cid: str, rgba: np.ndarray, provenance: dict[str, Any]) -> dict[str, Any]:
        self._check_canvas(rgba, cid)
        if cid in self.originals:
            raise PreservationError(f"original '{cid}' already exists and is immutable")
        path = self.root / "artwork" / "original" / f"{cid}.png"
        Image.fromarray(rgba.astype(np.uint8), "RGBA").save(path, optimize=True)
        rec = {"path": f"artwork/original/{cid}.png", "pixel_sha256": pixel_hash(rgba),
               "pixels": M.area(rgba[..., 3] > 0), "bbox_xyxy": M.bbox_xyxy(rgba[..., 3] > 0),
               "category": "A_original", "provenance": provenance}
        self.originals[cid] = rec
        self._log("add_original", component=cid)
        return rec

    def original(self, cid: str) -> np.ndarray:
        rec = self.originals[cid]
        arr = np.asarray(Image.open(self.root / rec["path"]).convert("RGBA"))
        if pixel_hash(arr) != rec["pixel_sha256"]:
            raise PreservationError(f"original '{cid}' was modified on disk")
        return arr

    def protected(self, cid: str) -> np.ndarray:
        return self.original(cid)[..., 3] > 0

    # -- B: generated extensions ----------------------------------------------------------
    def add_extension(self, cid: str, rgba: np.ndarray, region_mask: np.ndarray, *, method: str,
                      region_id: str | None, provenance: dict[str, Any], status: str = "pending_review") -> Extension:
        if status not in STATUSES:
            raise ValueError(status)
        if status == "approved":
            raise PreservationError("generated artwork cannot be created as approved; approve it in review")
        self._check_canvas(rgba, f"extension of {cid}")
        self._check_canvas(region_mask, f"region mask of {cid}")
        proposed = rgba[..., 3] > 0
        protected = self.protected(cid)
        allowed = proposed & region_mask & ~protected
        out = np.zeros_like(rgba, dtype=np.uint8)
        out[allowed] = rgba[allowed]
        out[allowed, 3] = 255
        eid = f"{cid}__{len([e for e in self.extensions if e.component == cid]) + 1:02d}"
        img_rel, prov_rel = f"artwork/reconstructed/{eid}.png", f"masks/provenance/{eid}.png"
        Image.fromarray(out, "RGBA").save(self.root / img_rel, optimize=True)
        M.save_mask(allowed, self.root / prov_rel)
        ext = Extension(id=eid, component=cid, image=img_rel, provenance_mask=prov_rel, region_id=region_id,
                        method=method, status=status, pixels=M.area(allowed),
                        discarded_outside_region=M.area(proposed & ~region_mask),
                        discarded_on_original=M.area(proposed & region_mask & protected),
                        provenance=provenance)
        self.extensions.append(ext)
        self._log("add_extension", component=cid, extension=eid, method=method)
        return ext

    def extension_rgba(self, ext: Extension) -> np.ndarray:
        return np.asarray(Image.open(self.root / ext.image).convert("RGBA"))

    def set_status(self, eid: str, status: str, note: str = "") -> Extension:
        if status not in STATUSES:
            raise ValueError(status)
        ext = next(e for e in self.extensions if e.id == eid)
        ext.status = status
        self._log("set_status", extension=eid, status=status, note=note)
        return ext

    def compose_component(self, cid: str, include: tuple[str, ...] = ("approved", "pending_review")) -> np.ndarray:
        """Extensions underneath, original on top: visible original pixels are unchangeable."""
        canvas = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        for ext in self.extensions:
            if ext.component == cid and ext.status in include:
                canvas.alpha_composite(Image.fromarray(self.extension_rgba(ext), "RGBA"))
        canvas.alpha_composite(Image.fromarray(self.original(cid), "RGBA"))
        return np.asarray(canvas)

    def generated_mask(self, cid: str | None = None, include=("approved", "pending_review")) -> np.ndarray:
        out = np.zeros((self.h, self.w), dtype=bool)
        for ext in self.extensions:
            if (cid is None or ext.component == cid) and ext.status in include:
                out |= M.load_mask(self.root / ext.provenance_mask)
        return out

    def change_map(self, cid: str) -> Image.Image:
        """Grey = original pixels, magenta = AI/algorithm-added pixels (B)."""
        orig = self.protected(cid)
        gen = self.generated_mask(cid)
        img = np.full((self.h, self.w, 3), 255, np.uint8)
        img[orig] = (150, 150, 150)
        img[gen] = (230, 0, 160)
        return Image.fromarray(img, "RGB")

    # -- rollback ---------------------------------------------------------------------------
    def rollback(self, cid: str) -> int:
        removed = [e for e in self.extensions if e.component == cid]
        for e in removed:
            for rel in (e.image, e.provenance_mask):
                (self.root / rel).unlink(missing_ok=True)
        self.extensions = [e for e in self.extensions if e.component != cid]
        for p in (self.root / "artwork" / "corrections").glob(f"{cid}__*.png"):
            p.unlink()
        self._log("rollback", component=cid, removed=[e.id for e in removed])
        return len(removed)

    def clear_working(self) -> None:
        shutil.rmtree(self.root / "artwork" / "working", ignore_errors=True)

    # -- verification -------------------------------------------------------------------------
    def verify(self) -> list[str]:
        problems = []
        for cid in self.originals:
            try:
                self.original(cid)
            except PreservationError as exc:
                problems.append(str(exc))
        for ext in self.extensions:
            gen = M.load_mask(self.root / ext.provenance_mask)
            rgba = self.extension_rgba(ext)
            if not np.array_equal(gen, rgba[..., 3] > 0):
                problems.append(f"{ext.id}: provenance mask does not match generated pixels")
            if ext.component in self.originals and (gen & self.protected(ext.component)).any():
                problems.append(f"{ext.id}: generated pixels on protected original pixels")
        return problems
