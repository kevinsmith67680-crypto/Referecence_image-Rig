"""Source (reference) image handling. The original file is never modified:
it is copied byte-for-byte into the project and verified by SHA-256."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


@dataclass
class SourceImage:
    path: Path
    sha256: str
    width: int
    height: int
    mode: str
    format: str | None
    rgba: np.ndarray  # (H, W, 4) uint8, decoded pixels

    @classmethod
    def load(cls, path: str | Path) -> "SourceImage":
        path = Path(path)
        data = path.read_bytes()
        with Image.open(path) as im:
            im.load()
            fmt, mode = im.format, im.mode
            rgba = np.asarray(im.convert("RGBA")).copy()
        h, w = rgba.shape[:2]
        rgba.setflags(write=False)
        return cls(path=path, sha256=sha256_bytes(data), width=w, height=h, mode=mode, format=fmt, rgba=rgba)

    @property
    def has_transparency(self) -> bool:
        return bool((self.rgba[..., 3] < 255).any())

    @property
    def rgb(self) -> np.ndarray:
        return self.rgba[..., :3]

    def copy_into(self, dest_dir: str | Path) -> dict:
        """Copy the original bytes (unchanged) and, if the source is not PNG, also a
        lossless PNG decode for tools that need PNG. Returns provenance."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        record = {"original_name": self.path.name, "sha256": self.sha256, "width": self.width,
                  "height": self.height, "mode": self.mode, "format": self.format}
        if (self.format or "").upper() == "PNG":
            dst = dest_dir / "original.png"
            shutil.copyfile(self.path, dst)
            record["project_path"] = "source/original.png"
            record["byte_identical_copy"] = True
        else:
            ext = self.path.suffix.lower() or ".img"
            shutil.copyfile(self.path, dest_dir / f"original{ext}")
            Image.fromarray(self.rgba).save(dest_dir / "original.png")
            record["project_path"] = f"source/original{ext}"
            record["png_decode_path"] = "source/original.png"
            record["byte_identical_copy"] = True
        copied = dest_dir / Path(record["project_path"]).name
        if sha256_file(copied) != self.sha256:  # pragma: no cover - filesystem corruption
            raise IOError("copied source does not match the original's SHA-256")
        return record

    def verify_unchanged(self) -> bool:
        return sha256_file(self.path) == self.sha256
