"""Phase 4.1: the universal native skeleton specification.

``NativeSkeletonSpec`` describes a complete articulated character for Moho with semantic bone ids
(ROOT, PELVIS, SPINE_LOWER, ... RIGHT_FOOT, plus the four IK target bones). Every position comes from
the character's own package: the skeleton the editor and the AutoRig renderer use (the package rig with
the user's bone edits, then the garment-aligned shoulder pivots of Phase 3.3). Nothing here knows a
particular character: lengths, pivots and joint ranges are measured from the package or read from its
configuration.

Coordinates:
- *character space*: the package's source pixels (origin top-left, +y down), what every AutoRig phase uses;
- *canvas space*: the Moho document's pixels; ``Placement`` maps character -> canvas by an integer offset
  and never scales;
- *Moho space*: frame height = 2 units, origin at the frame centre, +y up (``geometry.px_to_world``).
  Parent-relative values (fPos, fAngle, fLength) come from ``geometry.compute_rest_pose``, the code whose
  output Moho 14.5.1 reproduced natively in Phase 4.0 (``world_matrix`` checks).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

SIDES = (("LEFT", "l"), ("RIGHT", "r"))
TARGETS = ("LEFT_HAND_TARGET", "RIGHT_HAND_TARGET", "LEFT_FOOT_TARGET", "RIGHT_FOOT_TARGET")
CONTROL_ROLES = {
    "ROOT_CONTROL": "ROOT", "PELVIS_CONTROL": "PELVIS", "HEAD_CONTROL": "HEAD",
    "LEFT_HAND_TARGET": "LEFT_HAND_TARGET", "RIGHT_HAND_TARGET": "RIGHT_HAND_TARGET",
    "LEFT_FOOT_TARGET": "LEFT_FOOT_TARGET", "RIGHT_FOOT_TARGET": "RIGHT_FOOT_TARGET",
}
CONFIG_FILE = "natural_pose/native_rig.json"
DEFAULT_CONFIG: dict[str, Any] = {
    # In-plane ranges are offsets from the rest pose in degrees. Front-view art: elbow and knee flexion are
    # partly depth motion; the in-plane ranges below are stylised and editable per character.
    "elbow_hyperextension_deg": 10.0,
    "knee_inward_bend_deg": 45.0,       # front-view squat: knees out, shins turn toward the midline
    "knee_outward_bend_deg": 5.0,
    "spine_split": 0.5,                 # SPINE_LOWER / SPINE_UPPER split of the package's spine bone
    "root_length_frac": 0.12,           # ROOT bone length as a fraction of the character height
    "target_length_frac": 0.6,          # IK target bone length as a fraction of the hand / foot bone
    "limits_deg": {},                   # per-bone overrides: {"LEFT_FOREARM": [-10, 150]}
}


@dataclass
class NativeBone:
    id: str
    parent: str | None
    base_px: tuple[float, float]
    tip_px: tuple[float, float]
    source: str
    role: str = "structural"                 # structural | control
    control: str | None = None              # control role(s) this bone serves
    ik: dict[str, Any] | None = None
    limits_deg: tuple[float, float] | None = None   # in-plane offsets from rest, CCW positive in Moho
    limit_source: str | None = None
    strength: float | None = None           # not used by rigid layer binding
    bindings: list[str] = field(default_factory=list)

    @property
    def length_px(self) -> float:
        return float(math.hypot(self.tip_px[0] - self.base_px[0], self.tip_px[1] - self.base_px[1]))

    @property
    def rest_angle_deg(self) -> float:
        """Direction in Moho's convention (degrees, counter-clockwise, +y up)."""
        return math.degrees(math.atan2(-(self.tip_px[1] - self.base_px[1]), self.tip_px[0] - self.base_px[0]))


@dataclass(frozen=True)
class Placement:
    """Character space -> canvas space: an integer translation, never a scale."""
    canvas: tuple[int, int]
    offset: tuple[int, int] = (0, 0)

    def to_canvas(self, p) -> tuple[float, float]:
        return (float(p[0]) + self.offset[0], float(p[1]) + self.offset[1])

    def from_canvas(self, p) -> tuple[float, float]:
        return (float(p[0]) - self.offset[0], float(p[1]) - self.offset[1])

    def to_moho(self, p) -> tuple[float, float]:
        from ..geometry import px_to_world
        return px_to_world(self.to_canvas(p), self.canvas[0], self.canvas[1])

    def from_moho(self, w) -> tuple[float, float]:
        from ..geometry import world_to_px
        return self.from_canvas(world_to_px(w, self.canvas[0], self.canvas[1]))


def _sign_up(b) -> float:
    """+1 when a counter-clockwise rotation (Moho) lifts the bone's tip, i.e. the bone points image-right."""
    return 1.0 if (b.tip_px[0] - b.base_px[0]) >= 0 else -1.0


def _sign_out(b, midline_x: float) -> float:
    """+1 when a counter-clockwise rotation moves a downward bone's tip away from the midline."""
    return 1.0 if b.base_px[0] >= midline_x else -1.0


def _span(sign: float, lo: float, hi: float) -> tuple[float, float]:
    a, b = sign * lo, sign * hi
    return (round(min(a, b), 3), round(max(a, b), 3))


def load_config(pkg: Path, override: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    path = Path(pkg) / CONFIG_FILE
    if path.is_file():
        user = json.loads(path.read_text(encoding="utf-8"))
        cfg.update({k: v for k, v in user.items() if k != "limits_deg"})
        cfg["limits_deg"].update(user.get("limits_deg") or {})
    if override:
        cfg.update({k: v for k, v in override.items() if k != "limits_deg"})
        cfg["limits_deg"].update(override.get("limits_deg") or {})
    return cfg


@dataclass
class NativeSkeletonSpec:
    character_id: str
    source_canvas: tuple[int, int]
    bones: list[NativeBone]
    midline_x: float
    ground_y: float
    provenance: dict[str, Any]
    config: dict[str, Any]

    # -- lookups ---------------------------------------------------------------------------------
    def bone(self, bid: str) -> NativeBone:
        for b in self.bones:
            if b.id == bid:
                return b
        raise KeyError(bid)

    def ids(self) -> list[str]:
        return [b.id for b in self.bones]

    def children(self, bid: str) -> list[str]:
        return [b.id for b in self.bones if b.parent == bid]

    def descendants(self, bid: str) -> list[str]:
        out, stack = [], self.children(bid)
        while stack:
            c = stack.pop(0)
            out.append(c)
            stack.extend(self.children(c))
        return out

    def controls(self) -> dict[str, str]:
        return dict(CONTROL_ROLES)

    # -- conversion --------------------------------------------------------------------------------
    def spec_bones(self, placement: Placement) -> list[dict[str, Any]]:
        """Bones for a Phase 0 rig spec (canvas pixels), parents first."""
        return [{"name": b.id, "parent": b.parent,
                 "base_px": [round(v, 3) for v in placement.to_canvas(b.base_px)],
                 "tip_px": [round(v, 3) for v in placement.to_canvas(b.tip_px)]} for b in self.bones]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": "autorig.native_skeleton", "version": "4.1.0",
            "character_id": self.character_id, "source_canvas": list(self.source_canvas),
            "midline_x": self.midline_x, "ground_y": self.ground_y,
            "coordinate_system": "character space = package source pixels, origin top-left, +y down; angles and "
                                 "limits in Moho's convention (degrees, counter-clockwise, +y up)",
            "bones": [{**asdict(b), "length_px": round(b.length_px, 3), "rest_angle_deg": round(b.rest_angle_deg, 4)}
                      for b in self.bones],
            "controls": self.controls(), "config": self.config, "provenance": self.provenance,
        }

    # -- construction --------------------------------------------------------------------------------
    @classmethod
    def from_package(cls, pkg: str | Path, character_id: str | None = None,
                     config: dict[str, Any] | None = None) -> "NativeSkeletonSpec":
        from ..editor.artwork import ArtworkModel
        from ..editor.posesvc import PoseService
        from ..editor.project import EditorProject
        from ..garmentrig.structure import align_pivots, load_config as garment_config

        pkg = Path(pkg)
        rig_path = pkg / "natural_pose" / "rig.json"
        if not rig_path.is_file():
            raise ValueError(f"not a character package: {rig_path} is missing (run pose-refine first)")
        rig = json.loads(rig_path.read_text(encoding="utf-8"))
        proj, art = EditorProject(pkg), ArtworkModel(pkg)
        st = proj.state()
        masks = art.masks(st["mask_ops"])
        sk = PoseService(pkg, art).skeleton(st, masks, None)
        alpha = {c: art.layer_rgba(c, masks, set(), with_extensions=False)[..., 3] > 0 for c in masks}
        sk, pivots = align_pivots(sk, alpha, garment_config(pkg))
        return cls.from_skeleton(sk, rig, (art.W, art.H), character_id or pkg.resolve().parent.name,
                                 load_config(pkg, config),
                                 {"rig": "natural_pose/rig.json", "skeleton": "editor project (bone edits) + "
                                  "garment-aligned shoulder pivots (Phase 3.3)", "pivots": pivots})

    @classmethod
    def from_skeleton(cls, sk, rig: dict[str, Any], canvas: tuple[int, int], character_id: str,
                      config: dict[str, Any], provenance: dict[str, Any]) -> "NativeSkeletonSpec":
        by_kind = {(b.kind, b.side): b for b in sk.bones}
        limits = {b["name"]: b.get("limits_deg") or {} for b in rig.get("bones", [])}

        def get(kind, side=None):
            b = by_kind.get((kind, side))
            if b is None:
                raise ValueError(f"the package skeleton has no {kind!r} bone for side {side!r}")
            return b

        def pt(v) -> tuple[float, float]:
            return (round(float(v[0]), 3), round(float(v[1]), 3))

        midline = float(rig.get("midline_x", get("pelvis").head[0]))
        ground = float(rig.get("ground_y", max(get("foot", s).tail[1] for _, s in SIDES)))
        pelvis, spine, chest, neck, head = (get(k) for k in ("pelvis", "spine", "chest", "neck", "head"))
        top = float(min(head.tail[1], head.head[1]))
        height = ground - top
        bones: list[NativeBone] = []
        root_len = config["root_length_frac"] * height
        bones.append(NativeBone("ROOT", None, (round(midline, 3), round(ground, 3)),
                                (round(midline, 3), round(ground - root_len, 3)),
                                "midline_x at ground_y (rig.json)", role="control", control="ROOT_CONTROL"))
        bones.append(NativeBone("PELVIS", "ROOT", pt(pelvis.head), pt(pelvis.tail), pelvis.name,
                                control="PELVIS_CONTROL"))
        f = float(config["spine_split"])
        mid = spine.head + f * (spine.tail - spine.head)
        bones.append(NativeBone("SPINE_LOWER", "PELVIS", pt(spine.head), pt(mid), f"{spine.name} (first {f:g})"))
        bones.append(NativeBone("SPINE_UPPER", "SPINE_LOWER", pt(mid), pt(spine.tail), f"{spine.name} (rest)"))
        bones.append(NativeBone("CHEST", "SPINE_UPPER", pt(chest.head), pt(chest.tail), chest.name))
        bones.append(NativeBone("NECK", "CHEST", pt(neck.head), pt(neck.tail), neck.name))
        bones.append(NativeBone("HEAD", "NECK", pt(head.head), pt(head.tail), head.name, control="HEAD_CONTROL"))
        for S, s in SIDES:
            cl, ua, fa, ha = get("clavicle", s), get("upper_arm", s), get("forearm", s), get("hand", s)
            bones.append(NativeBone(f"{S}_SHOULDER", "CHEST", pt(cl.head), pt(cl.tail), cl.name))
            bones.append(NativeBone(f"{S}_UPPER_ARM", f"{S}_SHOULDER", pt(ua.head), pt(ua.tail), ua.name,
                                    ik={"chain": f"{S}_ARM", "role": "root", "target": f"{S}_HAND_TARGET"}))
            bones.append(NativeBone(f"{S}_FOREARM", f"{S}_UPPER_ARM", pt(fa.head), pt(fa.tail), fa.name,
                                    ik={"chain": f"{S}_ARM", "role": "effector", "target": f"{S}_HAND_TARGET"}))
            bones.append(NativeBone(f"{S}_HAND", f"{S}_FOREARM", pt(ha.head), pt(ha.tail), ha.name))
        for S, s in SIDES:
            th, sh, ft = get("thigh", s), get("shin", s), get("foot", s)
            bones.append(NativeBone(f"{S}_THIGH", "PELVIS", pt(th.head), pt(th.tail), th.name,
                                    ik={"chain": f"{S}_LEG", "role": "root", "target": f"{S}_FOOT_TARGET"}))
            bones.append(NativeBone(f"{S}_SHIN", f"{S}_THIGH", pt(sh.head), pt(sh.tail), sh.name,
                                    ik={"chain": f"{S}_LEG", "role": "effector", "target": f"{S}_FOOT_TARGET"}))
            bones.append(NativeBone(f"{S}_FOOT", f"{S}_SHIN", pt(ft.head), pt(ft.tail), ft.name))
        tl = float(config["target_length_frac"])
        for S, s in SIDES:
            for part, end_kind, eff in (("HAND", "hand", "FOREARM"), ("FOOT", "foot", "SHIN")):
                e = get(end_kind, s)
                base = e.head
                tip = e.head + tl * (e.tail - e.head)
                bones.append(NativeBone(f"{S}_{part}_TARGET", "ROOT", pt(base), pt(tip),
                                        f"{e.name}.head (the {'wrist' if part == 'HAND' else 'ankle'})",
                                        role="control", control=f"{S}_{part}_TARGET",
                                        ik={"chain": f"{S}_{'ARM' if part == 'HAND' else 'LEG'}", "role": "target",
                                            "effector": f"{S}_{eff}"}))
        spec = cls(character_id, (int(canvas[0]), int(canvas[1])), bones, round(midline, 3), round(ground, 3),
                   provenance, config)
        spec._assign_limits(limits, {b.name: b for b in sk.bones})
        return spec

    def _assign_limits(self, pkg_limits: dict[str, dict], sk_bones: dict[str, Any]) -> None:
        cfg = self.config
        by = {b.id: b for b in self.bones}

        def src(bid):
            return pkg_limits.get(by[bid].source.split(" ")[0], {})

        def down_angle(b) -> float:
            """Angle between the bone and straight down (degrees, 0..180)."""
            dx, dy = b.tip_px[0] - b.base_px[0], b.tip_px[1] - b.base_px[1]
            return math.degrees(math.atan2(abs(dx), dy)) if dy >= 0 else 180 - math.degrees(math.atan2(abs(dx), -dy))

        for b in self.bones:
            lim = src(b.id) if b.id not in ("SPINE_LOWER", "SPINE_UPPER") else pkg_limits.get("spine", {})
            note = None
            if b.id in ("ROOT",) or b.id.endswith("_TARGET"):
                continue
            if b.id == "PELVIS" and "tilt" in lim:
                rng, note = tuple(lim["tilt"]), "pelvis tilt"
            elif b.id in ("SPINE_LOWER", "SPINE_UPPER") and "bend" in lim:
                f = cfg["spine_split"] if b.id == "SPINE_LOWER" else 1 - cfg["spine_split"]
                rng, note = (lim["bend"][0] * f, lim["bend"][1] * f), f"spine bend x {f:g}"
            elif "bend" in lim:
                rng, note = tuple(lim["bend"]), "bend"
            elif b.id.endswith("_SHOULDER") and "elevation" in lim:
                rng, note = _span(_sign_up(b), *lim["elevation"]), "clavicle elevation (up = tip up)"
            elif b.id.endswith("_UPPER_ARM") and "abduction" in lim:
                rest = down_angle(b)
                rng = _span(_sign_up(b), lim["abduction"][0] - rest, lim["abduction"][1] - rest)
                note = f"abduction from hanging, rest {rest:.1f} deg"
            elif b.id.endswith("_FOREARM") and "flexion" in lim:
                hi = abs(lim["flexion"][1])
                rng = _span(_sign_up(b), -cfg["elbow_hyperextension_deg"], hi)
                note = "elbow flexion toward the head (front view, palm-forward T-pose); hyperextension per config"
            elif b.id.endswith("_THIGH") and "abduction" in lim:
                rest = down_angle(b)
                rng = _span(_sign_out(b, self.midline_x), lim["abduction"][0] - rest, lim["abduction"][1] - rest)
                note = f"abduction from vertical, rest {rest:.1f} deg"
            elif b.id.endswith("_SHIN"):
                rng = _span(_sign_out(b, self.midline_x), -cfg["knee_inward_bend_deg"], cfg["knee_outward_bend_deg"])
                note = ("front view: knee flexion is depth motion; the in-plane range is a stylised squat bend "
                        "(knees out, shins in), per config")
            else:
                continue
            b.limits_deg, b.limit_source = (round(float(rng[0]), 3), round(float(rng[1]), 3)), note
        for bid, rng in (cfg.get("limits_deg") or {}).items():
            if bid in by:
                by[bid].limits_deg = (float(rng[0]), float(rng[1]))
                by[bid].limit_source = "character configuration (native_rig.json or --config)"


def validate(spec: NativeSkeletonSpec) -> list[str]:
    """Structural problems (empty when the skeleton is usable)."""
    problems = []
    ids = spec.ids()
    if len(set(ids)) != len(ids):
        problems.append("duplicate bone ids")
    seen = set()
    for b in spec.bones:
        if b.parent is not None and b.parent not in seen:
            problems.append(f"{b.id}: parent {b.parent} is not declared before it")
        if b.length_px < 1.0:
            problems.append(f"{b.id}: zero-length bone")
        if not all(np.isfinite(v) for v in (*b.base_px, *b.tip_px)):
            problems.append(f"{b.id}: non-finite coordinates")
        if b.limits_deg and not (b.limits_deg[0] <= 0.0 <= b.limits_deg[1]):
            problems.append(f"{b.id}: the rest pose lies outside its limits {b.limits_deg}")
        seen.add(b.id)
    for t in TARGETS:
        if t not in seen:
            problems.append(f"missing control {t}")
    return problems
