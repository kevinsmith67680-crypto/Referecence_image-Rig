"""Pose preview for the editor, built on the Phase 2 natural-pose solver (no second solver).

The skeleton comes from the editor's bone state, which is the user's authority: edited pivots, parents,
limits and blend bands. Posing is a natural preset (IK, balance, torso compensation, limits) plus optional
direct per-bone rotations. Rendering uses the Phase 2 deformer with the user's corrected masks.
Diagnostics are the Phase 2 evaluation: seams, limits, ground, balance, unreviewed generated pixels.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage

from ..phase2 import corrective as C
from ..phase2.natural import natural_order
from ..phase2.pose_eval import evaluate
from ..phase2.posing import PRESET_BY_NAME, PRESETS, Preset, PoseSolver
from ..phase2.skeleton import SIDES, Bone, Skeleton, build_skeleton
from ..phase2.skinning import Deformer, PoseParams
from .artwork import ArtworkModel

# widest plausible ranges per kind (deg); limits beyond these are flagged as extreme
EXTREME = {"upper_arm": 185, "forearm": 165, "hand": 90, "thigh": 110, "shin": 160, "foot": 80, "neck": 45,
           "head": 50, "spine": 35, "chest": 30, "clavicle": 45, "pelvis": 25}


class PoseError(ValueError):
    pass


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def bone_warnings(st: dict[str, Any], base_bones: dict[str, Any], masks: dict[str, np.ndarray] | None,
                  footprint: np.ndarray | None) -> list[dict[str, Any]]:
    B = st["bones"]
    W, H = st["canvas"]["width"], st["canvas"]["height"]
    out = []
    roots = [n for n, b in B.items() if not b.get("parent")]
    if len(roots) != 1:
        out.append({"level": "error", "bone": None, "issue": f"hierarchy must have exactly one root (found {roots})"})
    for n, b in B.items():
        p = b.get("parent")
        if p and p not in B:
            out.append({"level": "error", "bone": n, "issue": f"missing parent bone '{p}'"})
        seen, cur = set(), n
        while cur and cur in B:
            if cur in seen:
                out.append({"level": "error", "bone": n, "issue": "cycle in the bone hierarchy"})
                break
            seen.add(cur)
            cur = B[cur].get("parent")
        L = float(np.hypot(b["tail"][0] - b["head"][0], b["tail"][1] - b["head"][1]))
        if L < 3:
            out.append({"level": "error", "bone": n, "issue": "zero-length bone"})
        x, y = b["head"]
        if not (0 <= x < W and 0 <= y < H):
            out.append({"level": "error", "bone": n, "issue": "joint outside the canvas"})
        elif footprint is not None:
            dt = _dist_to(footprint, x, y)
            if dt > 25:
                out.append({"level": "warning", "bone": n, "issue": f"joint {dt:.0f} px outside the artwork"})
        if n in base_bones:
            b0 = base_bones[n]
            L0 = float(np.hypot(b0["tail"][0] - b0["head"][0], b0["tail"][1] - b0["head"][1])) or 1.0
            if not 0.5 <= L / L0 <= 2.0:
                out.append({"level": "warning", "bone": n, "issue": f"length {L:.0f} px is {L / L0:.2f}x the analysed length"})
        for key, rng in (b.get("limits") or {}).items():
            if not isinstance(rng, (list, tuple)) or len(rng) != 2:
                continue
            lim = EXTREME.get(b.get("kind"), 180)
            if max(abs(rng[0]), abs(rng[1])) > lim:
                out.append({"level": "warning", "bone": n, "issue": f"extreme {key} limit {rng} (plausible within +/-{lim})"})
    for s, o in (("r", "l"), ("l", "r")):
        for k in ("upper_arm", "forearm", "thigh", "shin"):
            a, b = B.get(f"{k}_{s}"), B.get(f"{k}_{o}")
            if a and b and s == "r":
                la = np.hypot(*np.subtract(a["tail"], a["head"]))
                lb = np.hypot(*np.subtract(b["tail"], b["head"]))
                if min(la, lb) and max(la, lb) / min(la, lb) > 1.35:
                    out.append({"level": "warning", "bone": f"{k}_{s}", "issue": f"left/right {k} lengths differ {max(la, lb) / min(la, lb):.2f}x"})
    for L in st["layers"]:
        if L["visible"] and (not L.get("bone") or L["bone"] not in B):
            out.append({"level": "warning", "bone": None, "issue": f"layer '{L['id']}' has no valid bone binding (pivot metadata missing)"})
    return out


def _dist_to(mask: np.ndarray, x: float, y: float) -> float:
    xi, yi = int(round(x)), int(round(y))
    r = 60
    sub = mask[max(0, yi - r):yi + r + 1, max(0, xi - r):xi + r + 1]
    if sub.any() and 0 <= yi < mask.shape[0] and 0 <= xi < mask.shape[1] and mask[yi, xi]:
        return 0.0
    ys, xs = np.nonzero(sub)
    if not len(ys):
        return float(r)
    return float(np.hypot(xs + max(0, xi - r) - xi, ys + max(0, yi - r) - yi).min())


class PoseService:
    def __init__(self, pkg: Path, art: ArtworkModel):
        self.pkg, self.art = Path(pkg), art
        self.joints = json.loads((self.pkg / "analysis" / "anatomy.json").read_text())["joints"]
        self.variants = {}
        hd = self.pkg / "natural_pose" / "corrective" / "hands"
        if hd.is_dir():
            for f in hd.glob("*__*.png"):
                if f.stem.startswith(("hand_", "forearm_")):
                    self.variants[f.stem] = np.asarray(Image.open(f).convert("RGBA"))
        lum = art.source[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
        self.dark = (lum < 80) & art.footprint
        self._cache: dict[str, Any] = {}

    def skeleton(self, st: dict[str, Any], masks: dict[str, np.ndarray], preset: str | None = None) -> Skeleton:
        base = build_skeleton(self.joints, masks)
        B = st["bones"]
        if not B:
            return base
        bad = [w for w in bone_warnings(st, {}, None, None) if w["level"] == "error"]
        if bad:
            raise PoseError("; ".join(f"{w['bone']}: {w['issue']}" for w in bad))
        bones = []
        order = [b.name for b in base.bones] + [n for n in B if n not in base.idx]
        for n in order:
            if n not in B:
                continue
            b0 = base.bones[base.idx[n]] if n in base.idx else None
            s = B[n]
            bones.append(Bone(n, s.get("parent"), s.get("kind") or (b0.kind if b0 else "other"), s.get("side"),
                              np.array(s["head"], float), np.array(s["tail"], float), b0.h if b0 else 20.0,
                              "editor" if s.get("edited") else (b0.method if b0 else "editor")))
        # parents must precede children for the solver's hierarchy
        names = {b.name for b in bones}
        ordered, placed = [], set()
        while len(ordered) < len(bones):
            progress = False
            for b in bones:
                if b.name not in placed and (not b.parent or b.parent in placed or b.parent not in names):
                    ordered.append(b)
                    placed.add(b.name)
                    progress = True
            if not progress:
                raise PoseError("bone hierarchy cannot be ordered (cycle)")
        sk = Skeleton(ordered, base.ground_y, base.midline_x, base.notes)
        for s in SIDES:
            if f"upper_arm_{s}" in sk.idx and f"clavicle_{s}" in sk.idx:
                S, h = sk.bone(f"upper_arm_{s}").head, sk.bone(f"upper_arm_{s}").h
                sk.zones[f"clavicle_{s}"] = {"sc_x": float(sk.bone(f"clavicle_{s}").head[0]),
                                             "fade_y0": float(S[1] - 0.2 * h), "fade_y1": float(S[1] + 0.8 * h)}
        for n, s in B.items():  # blend bands: bone state, then joint corrections (global, then per pose)
            if n not in sk.idx:
                continue
            i = sk.idx[n]
            band = s.get("band")
            corr = st["corrections"]["joints"].get(n, {})
            band = corr.get("band", band)
            if preset and preset in corr.get("per_pose", {}):
                band = corr["per_pose"][preset].get("band", band)
            if band:
                sk.rp[i], sk.rc[i] = float(band[0]), float(band[1])
        return sk

    def engine(self, st: dict[str, Any], masks: dict[str, np.ndarray], rejected: set[str], preset: str | None):
        key = _digest([st["mask_ops"], st["bones"], st["corrections"], sorted(rejected), preset,
                       [(L["id"], L["visible"]) for L in st["layers"]]])
        if key in self._cache:
            return self._cache[key]
        sk = self.skeleton(st, masks, preset)
        layers = {c: self.art.layer_rgba(c, masks, rejected) for c in masks}
        gen = {c: self.art.generated_mask(c, masks, rejected) for c in masks}
        dfm = Deformer(sk, {**layers, **self.variants}, gen, reference=self.art.source, variants=set(self.variants))
        solver = PoseSolver(sk, dfm)
        for n, b in st["bones"].items():
            if b.get("limits") and n in sk.idx:
                solver.limit_overrides[(b.get("kind"), b.get("side") or "")] = {k: tuple(v) for k, v in b["limits"].items()}
        if len(self._cache) > 6:
            self._cache.pop(next(iter(self._cache)))
        self._cache[key] = (sk, dfm, solver)
        return self._cache[key]

    def render(self, st: dict[str, Any], masks: dict[str, np.ndarray], rejected: set[str], preset_name: str,
               overrides: dict[str, float] | None = None, pad: int = 160, boundaries: bool = False) -> dict[str, Any]:
        sk, dfm, solver = self.engine(st, masks, rejected, preset_name)
        overrides = overrides or {}
        if preset_name == "rest":
            pr = Preset("rest", "Rest pose (direct joint manipulation)", "reference pose + manual rotations",
                        arms={})
            pose = PoseParams.identity(len(sk.bones))
            info = {"violations": [], "ik_notes": [], "feet_targets": {s: list(sk.bone(f"foot_{s}").head) for s in SIDES}}
        else:
            if preset_name not in PRESET_BY_NAME:
                raise PoseError(f"unknown preset {preset_name}")
            pr = PRESET_BY_NAME[preset_name]
            pose, info = solver.solve(pr)
        unknown = [b for b in overrides if b not in sk.idx]
        if unknown:
            raise PoseError(f"unknown bones in overrides: {unknown}")
        for b, deg in overrides.items():
            pose.theta[sk.idx[b]] += float(deg)
        info.update(solver.joints_world(pose))
        info["violations"] = sorted(set(info.get("violations", []) + solver.check_limits(pose)))
        info["com"] = solver.com(pose)
        tx, support = solver._support(pose, pr, solver._feet_targets(pr)) if preset_name != "rest" else \
            (float(info["com"][0]), {"planted": list(SIDES), "x_range": [0, st["canvas"]["width"]],
                                     "weight_left": 0.5, "foot_centres": {}})
        info["target_x"], info["support"] = tx, support
        info["balance_error_px"] = tx - float(info["com"][0])
        visible = [L["id"] for L in st["layers"] if L["visible"] and L["kind"] == "bitmap" and L["id"] in masks]
        order = natural_order(visible, pr, set(self.variants))
        r = dfm.render(pose, order, pad=pad)
        tears = dfm.tears(pose, r, self.dark)
        used = [f"hand__{n}" for s, n in pr.hands.items() if f"hand_{s}__{n}" in order]
        resolved = {item: f"hand__{n}" for item, n in pr.resolved_by.items() if f"hand__{n}" in used}
        pending = [u for u in used if u not in resolved.values()]
        gussets = []
        if st["corrections"].get("gussets", True) and preset_name != "rest":
            gussets = C.armpit_gussets(r, tears, sk, info, dfm, pose)
            if gussets:
                tears = dfm.tears(pose, r, self.dark)
                pending += [f"gusset__{preset_name}__{g['side']}" for g in gussets]
        lab, n = ndimage.label(self.art.footprint)
        ev = evaluate(sk, solver, pose, info, r, pr, tears, 1, resolved, pending)
        out = {"image": r["image"], "pad": pad, "status": ev["status"], "confidence": ev["confidence"],
               "defects": ev["defects"], "needs_artwork": ev["needs_artwork"], "metrics": ev["metrics"],
               "joints_posed": info["joints_posed"], "bone_tails_posed": info["bone_tails_posed"],
               "violations": info["violations"], "gussets": [{k: g[k] for k in ("side", "pixels", "torn_pairs")} for g in gussets],
               "local_rotation_deg": {b.name: round(float(pose.theta[i]), 2) for i, b in enumerate(sk.bones)},
               "order": order, "tears": {k: len(v) for k, v in tears.items() if k != "segments"},
               "tear_points": tears.get("exposed_gap", []) + tears.get("visible_seam", [])}
        if boundaries:
            ov = np.zeros(r["image"].shape[:2] + (4,), np.uint8)
            rng = np.random.default_rng(7)
            for cid, a in r["alphas"].items():
                edge = a & ~ndimage.binary_erosion(a)
                ov[edge] = (*rng.integers(40, 255, 3).tolist(), 255)
            out["boundaries"] = ov
        return out

    # -- Phase 3.1 motion engine (planner -> solver -> garment deformation -> evaluation) -------------------
    def motion_engine(self, st: dict[str, Any], masks: dict[str, np.ndarray], rejected: set[str]):
        from ..motion.pipeline import MotionEngine, corrective_manifest
        from ..motion.profile import load_overrides

        appr = {k: v for k, v in st["approvals"].items() if k.startswith("corrective:")}
        key = "motion:" + _digest([st["mask_ops"], st["bones"], st["corrections"], sorted(rejected), appr,
                                   [(L["id"], L["visible"]) for L in st["layers"]]])
        if key in self._cache:
            return self._cache[key]
        sk = self.skeleton(st, masks, None)
        layers = {c: self.art.layer_rgba(c, masks, rejected) for c in masks}
        gen = {c: self.art.generated_mask(c, masks, rejected) for c in masks}
        cids = [k.split(":", 1)[1] for k in st["approvals"] if k.startswith("corrective:gusset")]
        eng = MotionEngine(sk, layers, gen, self.art.source, self.variants, st["approvals"], "front",
                           load_overrides(self.pkg), cids, corrective_manifest(self.pkg))
        for k in [k for k in self._cache if k.startswith("motion:")]:
            self._cache.pop(k)
        self._cache[key] = eng
        return eng

    def motion(self, st: dict[str, Any], masks: dict[str, np.ndarray], rejected: set[str], body: dict[str, Any]
               ) -> dict[str, Any]:
        from ..motion.planner import SPEC_BY_NAME

        name = body.get("preset", "relaxed_standing")
        if name not in SPEC_BY_NAME:
            raise PoseError(f"unknown preset {name}")
        eng = self.motion_engine(st, masks, rejected)
        visible = [L["id"] for L in st["layers"] if L["visible"] and L["kind"] == "bitmap" and L["id"] in masks]
        overrides = {k: float(v) for k, v in (body.get("overrides") or {}).items()}
        unknown = [b for b in overrides if b not in eng.sk.idx]
        if unknown:
            raise PoseError(f"unknown bones in overrides: {unknown}")
        ik = body["ik"] if body.get("ik") is not None else st.get("ik")
        switches = body["switches"] if body.get("switches") is not None else st.get("switches")
        r = eng.pose(name, body.get("phase"), float(body.get("speed", 1.0)), float(body.get("exaggeration", 1.0)),
                     0.0, bool(body.get("allow_pending")), overrides, body.get("live_head") or None, visible,
                     skeleton_only=bool(body.get("skeleton_only")), fluid=bool(body.get("fluid", True)),
                     ik=ik, switches=switches, switch_time=body.get("switch_time"))
        s = r["solve"]
        out = {"name": name, "title": r["title"], "kind": r["kind"], "phase": r["phase"], "pad": r["pad"],
               "joints_posed": s["joints_posed"], "bone_tails_posed": s["bone_tails_posed"], "contact": s["contact"],
               "com": [round(float(v), 2) for v in s["com"]], "support": s["support"],
               "feasibility": r["feasibility"], "hands": r["hands"], "corrections": r["corrections"],
               "timing_ms": r["timing_ms"], "phase_name": r["intent"].channels.get("phase_name"),
               "violations": s["violations"], "clamps": s["clamps"], "spec": r["spec"],
               "local_rotation_deg": {b.name: round(float(r["pose"].theta[i]), 2) for i, b in enumerate(eng.sk.bones)},
               "ik": self.ik_report(eng, r, ik), "switches": r["switches"],
               "correction_switches": [c for c in r["correction_switches"] if c["on"]]}
        if body.get("skeleton_only"):
            return out
        q = r["quality"]
        out.update({"image": r["image"], "quality": {k: q[k] for k in ("checks", "numerical", "warnings",
                                                                       "review_items", "visual_quality")},
                    "status": r["feasibility"]["status"],
                    "confidence": f"{q['numerical']['passed']}/{q['numerical']['total']} checks",
                    "defects": q["warnings"] + q["review_items"],
                    "needs_artwork": [f"{m['asset']} ({m['state']})" for m in r["feasibility"]["missing"]],
                    "tear_points": r["tears"]["exposed_gap"] + r["tears"]["visible_seam"],
                    "order": r["order"], "gussets": [{k: g[k] for k in ("side", "pixels")} for g in r["gussets"]]})
        kinds = [k for k in body.get("diagnostics") or [] if k]
        if kinds:
            out["diagnostics"] = eng.diagnostics(r, kinds)
        if body.get("before"):
            b = eng.legacy(name, r["pad"])
            if b is not None:
                out["before"] = b["image"]
        return out

    @staticmethod
    def ik_report(eng, r: dict[str, Any], ik: dict[str, Any] | None) -> dict[str, Any]:
        """Handles for the canvas: per limb the root, middle joint, effector, target and pole (rest-canvas px),
        plus solver status, foot contacts and notes."""
        s = r["solve"]
        J, T = s["joints_posed"], s["bone_tails_posed"]
        limbs = {}
        for limb in ("arm_r", "arm_l", "leg_r", "leg_l"):
            kind, side = limb.split("_")
            b0, b1 = ("upper_arm", "forearm") if kind == "arm" else ("thigh", "shin")
            cfg = (ik or {}).get(limb) or {}
            rep = (s["arms"] if kind == "arm" else s["legs"]).get(side, {})
            ctl = eng.limb(limb)
            pole = cfg.get("pole")
            if pole is None and rep.get("pole") is not None:
                pole = rep["pole"]
            if pole is None:
                pole = [round(float(v), 2) for v in ctl.default_pole(r["pose"])]
            limbs[limb] = {"root": J[f"{b0}_{side}"], "mid": J[f"{b1}_{side}"], "effector": T[f"{b1}_{side}"],
                           "target": cfg.get("target") if cfg.get("enabled") else None, "pole": pole,
                           "enabled": bool(cfg.get("enabled")), "mode": cfg.get("mode", "fk"),
                           "status": rep.get("status"), "error_px": rep.get("error_px"),
                           "flexion_deg": rep.get("flexion_deg"),
                           "reach_px": [round(ctl.solver.min_reach, 1), round(ctl.solver.max_reach, 1)]}
        return {"limbs": limbs, "contacts": s.get("contacts"), "notes": s.get("ik_notes", []) + [
            n for n in s.get("notes", []) if n.startswith(("arm_", "leg_"))],
            "pelvis_offset": (ik or {}).get("pelvis_offset", [0.0, 0.0])}

    def thumbnail(self, st, masks, rejected, group: str, variant: str, size: int = 96) -> np.ndarray | None:
        """Rest-frame crop of a hand variant around the wrist (Switches panel)."""
        from ..motion.switches import HAND_POSES

        eng = self.motion_engine(st, masks, rejected)
        if not group.startswith("hand_") or variant not in HAND_POSES:
            return None
        v = eng.hand_lib.hands[group[-1]].get(variant)
        if v is None or v.layer is None:
            return None
        rgba = {**eng.layers, **eng.variants}[v.layer]
        hb = eng.sk.bone(group)
        cx, cy = (hb.head + hb.tail) / 2
        half = max(40, int(1.3 * hb.length))
        x0, y0 = int(cx) - half, int(cy) - half
        crop = np.zeros((2 * half, 2 * half, 4), np.uint8)
        H, W = rgba.shape[:2]
        sx0, sy0, sx1, sy1 = max(0, x0), max(0, y0), min(W, x0 + 2 * half), min(H, y0 + 2 * half)
        crop[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = rgba[sy0:sy1, sx0:sx1]
        im = Image.fromarray(crop, "RGBA").resize((size, size), Image.LANCZOS)
        return np.asarray(im)

    @staticmethod
    def motion_presets() -> list[dict[str, Any]]:
        from ..motion.planner import LIBRARY

        return [{"name": "rest", "title": "Reference pose (manual rotations)", "kind": "manual"}] + \
               [{"name": s.name, "title": s.title, "intent": s.intent, "kind": s.kind,
                 "depth_motion": s.depth_motion, "fallback": s.fallback} for s in LIBRARY]

    @staticmethod
    def presets() -> list[dict[str, Any]]:
        return [{"name": "rest", "title": "Reference pose (manual rotations)"}] + \
               [{"name": p.name, "title": p.title, "intent": p.intent, "needs_artwork": p.needs_artwork,
                 "hands": p.hands} for p in PRESETS]
