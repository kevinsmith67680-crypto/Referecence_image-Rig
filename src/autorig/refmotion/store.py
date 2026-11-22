"""MotionReferenceStore: reusable reference motion, kept apart from per-character settings.

Character-independent library (default ``motion_references/`` at the repository root, or ``AUTORIG_MOTION_LIBRARY``).
Each entry is append-only, in ``<root>/<ref_id>/v<N>/``:
- ``reference.json``: metadata, tags, provenance, permission and hashes;
- ``track.json``: the landmark track (raw provider output, visibility kept);
- ``analysis.json``: derived channels, timing and foreshortening;
- ``annotations.json``: user annotations (phase labels, notes), appended.

Tags:
- ``gesture``, ``movement``, ``emotion``, ``view`` and ``intensity``;
- ``duration_s``;
- ``required_artwork``: from the analysis (depth segments) and the hand palm needs.

Nothing in the library refers to a character.

Per character, in ``<package>/natural_pose/reference_motion/<ref_id>/``:
- ``settings.json``: intensity, timing scale, segment, hand action overrides, channel offsets;
- ``retarget.json``: the latest retargeted performance, its feasibility and errors.

The editor project records which reference and settings are in use (``reference.*`` ops).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .analyze import ReferenceMotionAnalyzer
from .track import LandmarkTrack

SCHEMA = "autorig.motion_reference"
VERSION = 1
TAGS = ("gesture", "movement", "emotion", "view", "intensity")
SAFE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")


def default_root(pkg: str | Path | None = None) -> Path:
    env = os.environ.get("AUTORIG_MOTION_LIBRARY")
    if env:
        return Path(env)
    if pkg is not None:
        cand = Path(pkg).resolve().parents[1] / "motion_references"
        if cand.is_dir():
            return cand
        cand = Path(pkg).resolve().parents[2] / "motion_references"
        if cand.is_dir():
            return cand
    return Path("motion_references")


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def required_artwork(analysis: dict[str, Any], meta: dict[str, Any]) -> list[str]:
    from .retarget import ReferenceMotionRetargeter  # noqa: PLC0415

    f = ReferenceMotionRetargeter().feasibility(analysis, meta)
    out = sorted({i["needs"] for i in f["items"]})
    if meta.get("palm") == "PALM_UP":
        out.append("hand:palm_up (approved)")
    return out


class MotionReferenceStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _dir(self, ref_id: str) -> Path:
        if not SAFE.match(ref_id):
            raise ValueError(f"invalid reference id {ref_id!r} (lowercase letters, digits, _ and -)")
        return self.root / ref_id

    def versions(self, ref_id: str) -> list[int]:
        d = self._dir(ref_id)
        return sorted(int(p.name[1:]) for p in d.glob("v*") if p.name[1:].isdigit()) if d.is_dir() else []

    def add(self, ref_id: str, track: LandmarkTrack, meta: dict[str, Any], analysis: dict[str, Any] | None = None
            ) -> dict[str, Any]:
        """Store a new version (never overwrites). ``meta`` needs provenance with a usage permission."""
        from .importer import ReferenceMotionImporter  # noqa: PLC0415

        errs = ReferenceMotionImporter.check_meta(meta)
        if errs:
            raise ValueError("; ".join(errs))
        analysis = analysis or ReferenceMotionAnalyzer().analyze(track)
        vs = self.versions(ref_id)
        v = (vs[-1] + 1) if vs else 1
        d = self._dir(ref_id) / f"v{v}"
        d.mkdir(parents=True, exist_ok=False)
        tb = json.dumps(track.to_json()).encode()
        ab = json.dumps(analysis, default=float).encode()
        (d / "track.json").write_bytes(tb)
        (d / "analysis.json").write_bytes(ab)
        tags = {k: meta.get(k) for k in TAGS}
        tags["movement"] = tags["movement"] or meta.get("category")
        rec = {"schema": SCHEMA, "version": VERSION, "id": ref_id, "v": v, "name": meta.get("name", ref_id),
               "tags": {**tags, "duration_s": round(track.duration, 3),
                        "required_artwork": required_artwork(analysis, meta)},
               "provenance": meta.get("provenance"), "usage_permission": (meta.get("provenance") or {}).get(
                   "usage_permission") or meta.get("usage_permission"),
               "fps": track.fps, "frames": track.frames, "size": [track.width, track.height],
               "hand_actions": meta.get("hand_actions"), "palm": meta.get("palm"), "category": meta.get("category"),
               "quality": meta.get("quality"), "provider": (track.meta or {}).get("provider"),
               "sha256": {"track": _sha(tb), "analysis": _sha(ab)},
               "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "character_independent": True}
        (d / "reference.json").write_text(json.dumps(rec, indent=1, default=float) + "\n", encoding="utf-8")
        (d / "annotations.json").write_text("[]\n", encoding="utf-8")
        return rec

    def get(self, ref_id: str, v: int | None = None) -> dict[str, Any]:
        vs = self.versions(ref_id)
        if not vs:
            raise KeyError(ref_id)
        v = v or vs[-1]
        d = self._dir(ref_id) / f"v{v}"
        rec = json.loads((d / "reference.json").read_text(encoding="utf-8"))
        return {"meta": rec, "track": LandmarkTrack.load(d / "track.json"),
                "analysis": json.loads((d / "analysis.json").read_text(encoding="utf-8")),
                "annotations": json.loads((d / "annotations.json").read_text(encoding="utf-8")), "dir": str(d)}

    def verify(self, ref_id: str, v: int | None = None) -> list[str]:
        e = self.get(ref_id, v)
        d = Path(e["dir"])
        errs = []
        for k, f in (("track", "track.json"), ("analysis", "analysis.json")):
            if _sha((d / f).read_bytes()) != e["meta"]["sha256"][k]:
                errs.append(f"{ref_id}: {f} hash mismatch")
        return errs

    def annotate(self, ref_id: str, annotation: dict[str, Any], v: int | None = None) -> list[dict[str, Any]]:
        e = self.get(ref_id, v)
        a = {k: annotation[k] for k in ("t0", "t1", "label", "note") if k in annotation}
        if not a.get("label") and not a.get("note"):
            raise ValueError("an annotation needs a label or a note")
        anns = e["annotations"] + [{**a, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}]
        (Path(e["dir"]) / "annotations.json").write_text(json.dumps(anns, indent=1) + "\n", encoding="utf-8")
        return anns

    def list(self) -> list[dict[str, Any]]:
        out = []
        if not self.root.is_dir():
            return out
        for d in sorted(p for p in self.root.iterdir() if p.is_dir()):
            vs = self.versions(d.name) if SAFE.match(d.name) else []
            if vs:
                rec = json.loads((d / f"v{vs[-1]}" / "reference.json").read_text(encoding="utf-8"))
                out.append({k: rec.get(k) for k in ("id", "v", "name", "tags", "provenance", "fps", "frames",
                                                    "hand_actions", "category", "quality")})
        return out

    def search(self, **f) -> list[dict[str, Any]]:
        """Filter by tag equality (gesture, movement, emotion, view, intensity), ``max_duration`` and
        ``supported_only`` (no required artwork)."""
        out = []
        for r in self.list():
            t = r["tags"]
            ok = all(t.get(k) == v for k, v in f.items() if k in TAGS and v is not None)
            if f.get("max_duration") is not None and t.get("duration_s", 0) > float(f["max_duration"]):
                ok = False
            if f.get("supported_only") and t.get("required_artwork"):
                ok = False
            if ok:
                out.append(r)
        return out


class CharacterReferenceSettings:
    """Per-character settings and retarget results (never written into the library)."""

    DEFAULT = {"intensity": 1.0, "timing_scale": 1.0, "segment": None, "hand_actions": {}, "offsets": {}}

    def __init__(self, pkg: str | Path):
        self.dir = Path(pkg) / "natural_pose" / "reference_motion"

    def get(self, ref_id: str) -> dict[str, Any]:
        f = self.dir / ref_id / "settings.json"
        return {**self.DEFAULT, **(json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {})}

    def set(self, ref_id: str, settings: dict[str, Any]) -> dict[str, Any]:
        bad = set(settings) - set(self.DEFAULT)
        if bad:
            raise ValueError(f"unknown settings {sorted(bad)}")
        s = {**self.get(ref_id), **settings}
        if not 0.0 <= float(s["intensity"]) <= 2.0 or not 0.25 <= float(s["timing_scale"]) <= 4.0:
            raise ValueError("intensity must be in [0, 2] and timing_scale in [0.25, 4]")
        d = self.dir / ref_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "settings.json").write_text(json.dumps(s, indent=1) + "\n", encoding="utf-8")
        return s

    def save_retarget(self, ref_id: str, result: dict[str, Any]) -> Path:
        d = self.dir / ref_id
        d.mkdir(parents=True, exist_ok=True)
        f = d / "retarget.json"
        f.write_text(json.dumps(result, indent=1, default=float) + "\n", encoding="utf-8")
        return f


__all__ = ["MotionReferenceStore", "CharacterReferenceSettings", "default_root", "required_artwork"]
