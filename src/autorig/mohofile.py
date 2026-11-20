"""Read, search and diff Moho project files (.moho / .mohoproj).

A ``.moho`` file is a zip archive holding a JSON project (``*.mohoproj``),
a thumbnail and optional embedded resources; Moho 13+ also opens a bare
``.mohoproj`` (Lost Marble forum, "Editing .moho files?";
github.com/definitelyokay/moho-template).

Purpose in AutoRig: discover how Moho 14.5 stores Live Mode input
assignments by diffing a file saved *before* and *after* assigning an input
by hand. The JSON layout is undocumented, so nothing here assumes any key
names - it only walks, searches and compares whatever JSON it finds.
Files are read into memory (never extracted to disk); size is capped.
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

MAX_PROJECT_BYTES = 256 * 1024 * 1024


class MohoFileError(ValueError):
    pass


@dataclass(frozen=True)
class MohoProject:
    path: Path
    data: Any
    archive_members: tuple[tuple[str, int], ...]  # (name, uncompressed size); empty for bare .mohoproj
    project_member: str | None


def load_project(path: str | Path) -> MohoProject:
    path = Path(path)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            members = tuple((i.filename, i.file_size) for i in infos)
            projects = [i for i in infos if i.filename.lower().endswith(".mohoproj")]
            if len(projects) != 1:
                raise MohoFileError(
                    f"{path}: expected exactly one .mohoproj inside the archive, found "
                    f"{[i.filename for i in projects] or 'none'}; members: {[m[0] for m in members]}")
            info = projects[0]
            if info.file_size > MAX_PROJECT_BYTES:
                raise MohoFileError(f"{path}: {info.filename} is {info.file_size} bytes (limit {MAX_PROJECT_BYTES})")
            raw = zf.read(info)
            member = info.filename
    else:
        if path.stat().st_size > MAX_PROJECT_BYTES:
            raise MohoFileError(f"{path}: file too large")
        raw = path.read_bytes()
        members, member = (), None
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MohoFileError(f"{path}: project is not valid UTF-8 JSON ({exc})") from exc
    return MohoProject(path=path, data=data, archive_members=members, project_member=member)


def _child_path(parent: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{parent}[{key}]"
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(key)):
        return f"{parent}.{key}"
    return f"{parent}[{json.dumps(str(key))}]"


def iter_nodes(obj: Any, path: str = "$") -> Iterator[tuple[str, Any]]:
    """Yield (json_path, value) for every node, containers included."""
    yield path, obj
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from iter_nodes(v, _child_path(path, k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from iter_nodes(v, _child_path(path, i))


def short(value: Any, limit: int = 80) -> str:
    if isinstance(value, dict):
        return f"{{object, {len(value)} keys}}"
    if isinstance(value, list):
        return f"[array, {len(value)} items]"
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def grep(obj: Any, pattern: str) -> list[tuple[str, str]]:
    """Nodes whose *key* (last path segment) or *string value* matches the
    case-insensitive regex."""
    rx = re.compile(pattern, re.IGNORECASE)
    hits = []
    for path, value in iter_nodes(obj):
        last = re.split(r"[.\[]", path)[-1].strip('"]')
        if rx.search(last) or (isinstance(value, str) and rx.search(value)):
            hits.append((path, short(value)))
    return hits


def summarize(project: MohoProject) -> list[str]:
    lines = [f"file: {project.path}"]
    if project.archive_members:
        lines.append("archive members:")
        lines.extend(f"  {name}  ({size} bytes)" for name, size in project.archive_members)
        lines.append(f"project member: {project.project_member}")
    data = project.data
    if isinstance(data, dict):
        lines.append(f"top-level keys ({len(data)}):")
        lines.extend(f"  {k}: {short(v)}" for k, v in data.items())
    else:
        lines.append(f"top-level value: {short(data)}")
    lines.append(f"total JSON nodes: {sum(1 for _ in iter_nodes(data))}")
    return lines


@dataclass(frozen=True)
class Change:
    kind: str  # added | removed | changed
    path: str
    old: Any = None
    new: Any = None

    def format(self) -> str:
        if self.kind == "added":
            return f"+ {self.path} = {short(self.new)}"
        if self.kind == "removed":
            return f"- {self.path} (was {short(self.old)})"
        return f"~ {self.path}: {short(self.old)} -> {short(self.new)}"


def diff(a: Any, b: Any, path: str = "$", ignore: re.Pattern[str] | None = None) -> list[Change]:
    """Structural diff. Lists are compared index by index (good enough to
    locate where a single hand-made change landed)."""
    if ignore is not None and ignore.search(path):
        return []
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[Change] = []
        for k in a:
            p = _child_path(path, k)
            if k not in b:
                if not (ignore and ignore.search(p)):
                    out.append(Change("removed", p, old=a[k]))
            else:
                out.extend(diff(a[k], b[k], p, ignore))
        for k in b:
            if k not in a:
                p = _child_path(path, k)
                if not (ignore and ignore.search(p)):
                    out.append(Change("added", p, new=b[k]))
        return out
    if isinstance(a, list) and isinstance(b, list):
        out = []
        for i in range(max(len(a), len(b))):
            p = _child_path(path, i)
            if i >= len(a):
                out.append(Change("added", p, new=b[i]))
            elif i >= len(b):
                out.append(Change("removed", p, old=a[i]))
            else:
                out.extend(diff(a[i], b[i], p, ignore))
        return out
    if type(a) is not type(b) or a != b:
        return [Change("changed", path, old=a, new=b)]
    return []
