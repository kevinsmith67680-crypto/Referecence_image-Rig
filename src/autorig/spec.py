"""Rig specification loading and validation.

A rig spec is JSON validated in two passes:

1. Structural validation against ``data/rig_spec.schema.json`` (JSON Schema 2020-12).
2. Semantic validation (references, hierarchy order, frame ranges, ...) in
   :func:`validate_semantics`, which JSON Schema cannot express.

All coordinates in the spec are canvas pixels (origin top-left, +y down).
Conversion to Moho world units lives in :mod:`autorig.geometry`.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_RESOURCE = "data/rig_spec.schema.json"


class SpecError(ValueError):
    """Raised when a rig spec is structurally or semantically invalid."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("Invalid rig spec:\n  - " + "\n  - ".join(self.problems))


@dataclass(frozen=True)
class Bone:
    name: str
    parent: str | None
    base_px: tuple[float, float]
    tip_px: tuple[float, float]


@dataclass(frozen=True)
class Part:
    name: str
    layer_name: str
    image: str
    bone: str
    z_index: int
    vector_source: str | None = None


@dataclass(frozen=True)
class Key:
    frame: int
    value: float


@dataclass(frozen=True)
class Track:
    bone: str
    channel: str
    keys: tuple[Key, ...]


@dataclass(frozen=True)
class RigSpec:
    raw: dict[str, Any]
    character_id: str
    canvas_width: int
    canvas_height: int
    bone_layer_name: str
    rest_frame: int
    start_frame: int
    end_frame: int
    report_filename: str
    bones: tuple[Bone, ...]
    parts: tuple[Part, ...]
    animation_name: str
    tracks: tuple[Track, ...]
    source_path: Path | None = field(default=None, compare=False)

    @property
    def frame_count(self) -> int:
        return self.end_frame - self.start_frame + 1

    def bone(self, name: str) -> Bone:
        for b in self.bones:
            if b.name == name:
                return b
        raise KeyError(name)

    def bone_index(self, name: str) -> int:
        """0-based index == the Moho bone ID the generated script will create."""
        for i, b in enumerate(self.bones):
            if b.name == name:
                return i
        raise KeyError(name)

    def parts_back_to_front(self) -> list[Part]:
        return sorted(self.parts, key=lambda p: p.z_index)

    def track_for(self, bone_name: str) -> Track | None:
        for t in self.tracks:
            if t.bone == bone_name:
                return t
        return None

    def canonical_sha256(self) -> str:
        """Hash of the spec content (key-order independent) for provenance."""
        payload = {k: v for k, v in self.raw.items() if k != "$schema"}
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()


def load_schema() -> dict[str, Any]:
    text = resources.files("autorig").joinpath(SCHEMA_RESOURCE).read_text(encoding="utf-8")
    return json.loads(text)


def validate_structure(raw: dict[str, Any]) -> list[str]:
    validator = jsonschema.Draft202012Validator(load_schema())
    problems = []
    for err in sorted(validator.iter_errors(raw), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in err.absolute_path) or "<root>"
        problems.append(f"schema: {where}: {err.message}")
    return problems


def validate_semantics(raw: dict[str, Any]) -> list[str]:
    """Checks that JSON Schema cannot express. Assumes structure is valid."""
    problems: list[str] = []
    moho = raw["moho"]
    start, end = moho["start_frame"], moho["end_frame"]
    if end < start:
        problems.append(f"moho: end_frame ({end}) < start_frame ({start})")

    seen: list[str] = []
    for i, b in enumerate(raw["bones"]):
        name = b["name"]
        if name in seen:
            problems.append(f"bones[{i}]: duplicate bone name '{name}'")
        parent = b["parent"]
        if parent is not None and parent not in seen:
            # Parents must be declared before children: the declaration order
            # is the Moho bone creation order and therefore the bone ID order.
            problems.append(
                f"bones[{i}] '{name}': parent '{parent}' must be declared earlier in the list"
            )
        dx = b["tip_px"][0] - b["base_px"][0]
        dy = b["tip_px"][1] - b["base_px"][1]
        if math.hypot(dx, dy) < 1.0:
            problems.append(f"bones[{i}] '{name}': base and tip are < 1px apart (zero-length bone)")
        for key in ("base_px", "tip_px"):
            x, y = b[key]
            if not all(math.isfinite(v) for v in (x, y)):
                problems.append(f"bones[{i}] '{name}': {key} is not finite")
        seen.append(name)
    if not any(b["parent"] is None for b in raw["bones"]):
        problems.append("bones: at least one root bone (parent null) is required")

    bone_names = set(seen)
    part_names: set[str] = set()
    layer_names: set[str] = set()
    z_values: set[int] = set()
    for i, p in enumerate(raw["parts"]):
        if p["name"] in part_names:
            problems.append(f"parts[{i}]: duplicate part name '{p['name']}'")
        if p["layer_name"] in layer_names:
            problems.append(f"parts[{i}]: duplicate layer_name '{p['layer_name']}'")
        if p["layer_name"] == moho["bone_layer_name"]:
            problems.append(f"parts[{i}]: layer_name collides with the bone layer name")
        if p["bone"] not in bone_names:
            problems.append(f"parts[{i}] '{p['name']}': unknown bone '{p['bone']}'")
        if p["z_index"] in z_values:
            problems.append(f"parts[{i}] '{p['name']}': duplicate z_index {p['z_index']}")
        part_names.add(p["name"])
        layer_names.add(p["layer_name"])
        z_values.add(p["z_index"])

    tracked: set[tuple[str, str]] = set()
    for i, t in enumerate(raw["animation"]["tracks"]):
        if t["bone"] not in bone_names:
            problems.append(f"animation.tracks[{i}]: unknown bone '{t['bone']}'")
        if (t["bone"], t["channel"]) in tracked:
            problems.append(f"animation.tracks[{i}]: duplicate track for {t['bone']}/{t['channel']}")
        tracked.add((t["bone"], t["channel"]))
        frames = [k["frame"] for k in t["keys"]]
        if frames != sorted(set(frames)):
            problems.append(f"animation.tracks[{i}] '{t['bone']}': key frames must be strictly increasing")
        for k in t["keys"]:
            if not (start <= k["frame"] <= end):
                problems.append(
                    f"animation.tracks[{i}] '{t['bone']}': key frame {k['frame']} outside [{start}, {end}]"
                )
    return problems


def parse_spec(raw: dict[str, Any], source_path: Path | None = None) -> RigSpec:
    problems = validate_structure(raw)
    if problems:
        raise SpecError(problems)
    problems = validate_semantics(raw)
    if problems:
        raise SpecError(problems)

    moho = raw["moho"]
    bones = tuple(
        Bone(
            name=b["name"],
            parent=b["parent"],
            base_px=(float(b["base_px"][0]), float(b["base_px"][1])),
            tip_px=(float(b["tip_px"][0]), float(b["tip_px"][1])),
        )
        for b in raw["bones"]
    )
    parts = tuple(
        Part(
            name=p["name"],
            layer_name=p["layer_name"],
            image=p["image"],
            bone=p["bone"],
            z_index=int(p["z_index"]),
            vector_source=p.get("vector_source"),
        )
        for p in raw["parts"]
    )
    tracks = tuple(
        Track(
            bone=t["bone"],
            channel=t["channel"],
            keys=tuple(Key(frame=int(k["frame"]), value=float(k["value"])) for k in t["keys"]),
        )
        for t in raw["animation"]["tracks"]
    )
    return RigSpec(
        raw=raw,
        character_id=raw["character"]["id"],
        canvas_width=int(raw["canvas"]["width_px"]),
        canvas_height=int(raw["canvas"]["height_px"]),
        bone_layer_name=moho["bone_layer_name"],
        rest_frame=int(moho["rest_frame"]),
        start_frame=int(moho["start_frame"]),
        end_frame=int(moho["end_frame"]),
        report_filename=moho["report_filename"],
        bones=bones,
        parts=parts,
        animation_name=raw["animation"]["name"],
        tracks=tracks,
        source_path=source_path,
    )


def load_spec(path: str | Path) -> RigSpec:
    path = Path(path)
    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    return parse_spec(raw, source_path=path.resolve())


def missing_assets(spec: RigSpec, asset_root: str | Path) -> list[str]:
    """Return spec-relative asset paths that do not exist under asset_root."""
    root = Path(asset_root)
    missing = []
    for p in spec.parts:
        for rel in (p.image, p.vector_source):
            if rel and not (root / rel).is_file():
                missing.append(rel)
    return missing
