"""MotionEngine: planner -> feasibility -> solver -> garment-aware deformation -> evaluation.

One engine per (skeleton, layers) state. It is cached by the editor; poses then cost a solve (milliseconds)
plus a render (seconds on CPU, see the report). ``skeleton_only`` skips the render for interactive scrubbing.
"""

from __future__ import annotations

import math
import time
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

from ..phase2 import corrective as C
from ..phase2.natural import natural_order
from ..phase2.pose_eval import on_bg
from ..phase2.posing import PRESET_BY_NAME as OLD_PRESETS
from ..phase2.posing import PoseSolver
from ..phase2.skeleton import SIDES, Skeleton, layer_class
from ..phase2.skinning import Deformer, PoseParams
from .feasibility import ArtInventory, PoseFeasibilityEvaluator
from .garments import ArtworkDeformationEngine, GarmentDeformationEngine, GarmentModel, GarmentSkeleton
from .intent import PoseIntent
from .planner import LIBRARY, SPEC_BY_NAME, MotionPlanner, gait
from .profile import build_profile
from .quality import PoseQualityEvaluator
from .solver import SkeletalPoseSolver

DIAGNOSTICS = ("skeleton", "garments", "boundaries", "weights", "overlaps", "contacts", "com", "provenance",
               "missing", "correctives")


def motion_order(visible: list[str], it: PoseIntent, hands: dict[str, Any], available: set[str]) -> list[str]:
    """Garment-aware layering: per leg thigh, shoe, shin (trouser cuff over the shoe); body; per arm upper arm,
    hand, forearm (sleeve end over the wrist); head. Chosen hand drawings replace the reference hands."""
    vis = set(visible)
    legs = [c for s in SIDES for c in (f"thigh_{s}", f"foot_{s}", f"shin_{s}") if c in vis]
    body = [c for c in ("pelvis", "neck", "torso") if c in vis]
    arms = [c for s in SIDES for c in (f"upper_arm_{s}", f"hand_{s}", f"forearm_{s}") if c in vis]
    head = [c for c in visible if layer_class(c) == "head"]
    rest = [c for c in visible if c not in legs + body + arms + head]
    out = legs + body + rest + (arms + head if it.arms_behind_head else head + arms)
    for s, h in hands.items():
        if h["drawing"] == "open":
            continue
        v = f"hand_{s}__{h['drawing']}"
        if v in available and f"hand_{s}" in out:
            out[out.index(f"hand_{s}")] = v
            if f"forearm_{s}__trim" in available and f"forearm_{s}" in out:
                out[out.index(f"forearm_{s}")] = f"forearm_{s}__trim"
    for f in it.front:
        if f in out:
            out.remove(f)
            out.append(f)
    return out


class MotionEngine:
    def __init__(self, sk: Skeleton, layers: dict[str, np.ndarray], generated: dict[str, np.ndarray] | None,
                 source: np.ndarray, variants: dict[str, np.ndarray] | None = None,
                 approvals: dict[str, Any] | None = None, view: str = "front",
                 profile_overrides: dict[str, Any] | None = None, corrective_ids: list[str] | None = None):
        t0 = time.time()
        self.sk, self.layers, self.generated, self.source = sk, layers, generated or {}, source
        self.variants = variants or {}
        self.approvals = approvals or {}
        masks = {c: a[..., 3] > 0 for c, a in layers.items()}
        self.masks = masks
        self.profile = build_profile(sk, masks, view, profile_overrides)
        self.garments = GarmentModel.build(source, masks, sk, self.profile)
        self.gsk = GarmentSkeleton(sk, self.garments)
        self.dfm = ArtworkDeformationEngine(self.gsk, {**layers, **self.variants}, self.generated, reference=source,
                                            variants=set(self.variants))
        self.gdef = GarmentDeformationEngine(self.gsk, self.garments)
        self.solver = SkeletalPoseSolver(sk, self.profile, self.dfm.masses())
        self.planner = MotionPlanner(self.profile)
        self.inventory = ArtInventory.build(set(layers), set(self.variants), self.approvals, view, corrective_ids)
        self.feas = PoseFeasibilityEvaluator(self.inventory)
        self.rest_area = {c: int((a[..., 3] > 127).sum()) for c, a in {**layers, **self.variants}.items()}
        self.quality = PoseQualityEvaluator(self.profile, sk, self.rest_area)
        lum = source[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
        fp = np.zeros(source.shape[:2], bool)
        for m in masks.values():
            fp |= m
        self.dark = (lum < 80) & fp
        self._legacy = None
        self.build_s = round(time.time() - t0, 3)

    # -- poses ---------------------------------------------------------------------------------------
    def arm_style(self, name: str, allow_pending: bool) -> str:
        """Run arms: the fist pump only when a fist drawing can be used, else the approved open-hand style."""
        if name != "running":
            return "fist"
        ok = all(self.inventory.hands.get(s, {}).get("fist") == "approved" or
                 (allow_pending and self.inventory.hands.get(s, {}).get("fist") == "pending_review") for s in SIDES)
        return "fist" if ok else "open"

    def plan_and_solve(self, name: str, phase: float | None = None, speed: float = 1.0, exaggeration: float = 1.0,
                       travel: float = 0.0, overrides: dict[str, float] | None = None,
                       live_head: dict[str, float] | None = None, allow_pending: bool = False):
        it = self.planner.plan(name, phase, speed, exaggeration, travel, self.arm_style(name, allow_pending))
        pose, info = self.solver.solve(it)
        manual = {}
        for b, deg in (overrides or {}).items():
            if b not in self.sk.idx:
                raise KeyError(b)
            pose.theta[self.sk.idx[b]] += float(deg)
            manual[b] = float(deg)
        if live_head:
            info["live_head"] = self.compose_live_head(pose, live_head)
        if manual or live_head:
            info.update(self.solver.K.joints(pose))
            info["anatomical"] = self.solver.anatomical(pose)
            info["violations"] = self.solver.check_limits(pose, it, info["anatomical"])
            info["com"] = self.solver.com(pose)
        info["manual_rotations"] = manual
        return it, pose, info

    def compose_live_head(self, pose: PoseParams, live: dict[str, float]) -> dict[str, Any]:
        """Live puppeteering channel: head tilt is added to the neck/head bones only, on top of the body pose.
        Head turn needs turned-head drawings (reported, not faked)."""
        I = self.sk.idx
        tilt = float(live.get("tilt", 0.0))
        lo, hi = self.solver.limit("neck", "bend")
        add_n = float(np.clip(pose.theta[I["neck"]] + 0.4 * tilt, lo, hi)) - pose.theta[I["neck"]]
        pose.theta[I["neck"]] += add_n
        lo, hi = self.solver.limit("head", "bend")
        add_h = float(np.clip(pose.theta[I["head"]] + tilt - add_n, lo, hi)) - pose.theta[I["head"]]
        pose.theta[I["head"]] += add_h
        out = {"tilt_applied": round(add_n + add_h, 2), "bones": ["neck", "head"]}
        if live.get("turn"):
            out["turn"] = "not applied: head turns need turned-head drawings (REQUIRES_NEW_ARTWORK)"
        return out

    def pose(self, name: str, phase: float | None = None, speed: float = 1.0, exaggeration: float = 1.0,
             travel: float = 0.0, allow_pending: bool = False, overrides: dict[str, float] | None = None,
             live_head: dict[str, float] | None = None, visible: list[str] | None = None, pad: int = 160,
             skeleton_only: bool = False) -> dict[str, Any]:
        spec = SPEC_BY_NAME[name]
        t0 = time.perf_counter()
        it, pose, info = self.plan_and_solve(name, phase, speed, exaggeration, travel, overrides, live_head,
                                             allow_pending)
        t_solve = time.perf_counter() - t0
        hands = self.feas.choose_hands(it, allow_pending)
        corr = self.gdef.corrections(pose, it.garment)
        res: dict[str, Any] = {"name": name, "title": spec.title, "kind": spec.kind, "phase": it.phase,
                               "params": {"speed": speed, "exaggeration": exaggeration, "travel": travel,
                                          "allow_pending": allow_pending},
                               "intent": it, "pose": pose, "solve": info, "hands": hands,
                               "corrections": corr["active"], "spec": spec.to_json(), "pad": pad,
                               "timing_ms": {"solve": round(t_solve * 1000, 2)}}
        solver_notes = [n for s in SIDES if s in info["legs"] for n in [info["legs"][s]["note"]] if n]
        if skeleton_only:
            res["feasibility"] = self.feas.evaluate(spec, it, hands, corr["active"], solver_notes,
                                                    exaggeration=exaggeration)
            return res
        vis = visible or [c for c in self.layers]
        order = motion_order(vis, it, hands, set(self.variants))
        t1 = time.perf_counter()
        r = self.dfm.render_with(pose, order, corr, pad)
        tears = self.dfm.tears_with(pose, r, corr, self.dark)
        t_render = time.perf_counter() - t1
        gussets, gusset_need = [], False
        armpit = sum(1 for s in SIDES for (x, y) in tears["exposed_gap"] + tears["visible_seam"]
                     if np.hypot(x - pad - info["joints_posed"][f"upper_arm_{s}"][0],
                                 y - pad - info["joints_posed"][f"upper_arm_{s}"][1])
                     <= 2.2 * self.sk.bone(f"upper_arm_{s}").h)
        if armpit >= 6:
            gusset_need = True
            states = [self.inventory.correctives.get(f"gusset__{name}__{s}", "missing") for s in SIDES]
            if all(st == "approved" for st in states if st != "missing") and any(st != "missing" for st in states) \
                    or allow_pending:
                gussets = C.armpit_gussets(r, tears, self.sk, info, self.dfm, pose)
                if gussets:
                    tears = self.dfm.tears_with(pose, r, corr, self.dark)
        feas = self.feas.evaluate(spec, it, hands, corr["active"], solver_notes, tears_need_gusset=gusset_need,
                                  exaggeration=exaggeration)
        pending = [f"hand {s}: {h['drawing']}" for s, h in hands.items() if h["pending_review"]]
        if gussets and any(self.inventory.correctives.get(f"gusset__{name}__{g['side']}") != "approved"
                           for g in gussets):
            pending.append("armpit gusset (generated)")
        q = self.quality.frame(it, pose, info, r, tears, pending, int(r["generated_visible"].sum()))
        res.update({"image": r["image"], "render": r, "tears": tears, "order": order, "gussets": gussets,
                    "feasibility": feas, "quality": q})
        res["timing_ms"].update({"render_and_tears": round(t_render * 1000, 1),
                                 "total": round((time.perf_counter() - t0) * 1000, 1)})
        return res

    def cycle(self, name: str, samples: int = 32, speed: float = 1.0, exaggeration: float = 1.0,
              travel: float = 0.0, allow_pending: bool = False) -> dict[str, Any]:
        """Gait metrics over ``samples`` evenly spaced phases plus phase 1 (= phase 0 one cycle later)."""
        kind = {"running": "run", "casual_walk": "walk"}[name]
        style = self.arm_style(name, allow_pending)
        frames = []
        for i in range(samples + 1):
            ph, cyc = (i / samples, 0.0) if i < samples else (0.0, 1.0)
            it = gait(self.profile, kind, ph, speed, exaggeration, travel, cyc, arm_style=style)
            pose, info = self.solver.solve(it)
            frames.append((it, info))
        return self.quality.cycle(frames, travel)

    # -- comparison: the Phase 3 engine (before) -------------------------------------------------------
    def legacy(self, name: str, pad: int = 160) -> dict[str, Any] | None:
        if name not in OLD_PRESETS:
            return None
        if self._legacy is None:
            dfm = Deformer(self.sk, {**self.layers, **self.variants}, self.generated, reference=self.source,
                           variants=set(self.variants))
            self._legacy = (dfm, PoseSolver(self.sk, dfm))
        dfm, solver = self._legacy
        pr = OLD_PRESETS[name]
        pose, info = solver.solve(pr)
        order = natural_order([c for c in self.layers], pr, set(self.variants))
        r = dfm.render(pose, order, pad=pad)
        info.update(solver.joints_world(pose))
        return {"image": r["image"], "solve": info, "pose": pose, "pad": pad, "render": r}

    # -- diagnostics ---------------------------------------------------------------------------------
    def diagnostics(self, res: dict[str, Any], kinds: list[str]) -> dict[str, np.ndarray]:
        r, info, pad = res["render"], res["solve"], res["pad"]
        H, W = r["image"].shape[:2]
        out: dict[str, np.ndarray] = {}
        J, T = info["joints_posed"], info["bone_tails_posed"]
        P = lambda q: (q[0] + pad, q[1] + pad)
        for k in kinds:
            im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            if k == "skeleton":
                for b in self.sk.bones:
                    col = (255, 140, 40, 255) if b.side == "r" else (60, 180, 255, 255) if b.side == "l" else (250, 220, 60, 255)
                    d.line((*P(J[b.name]), *P(T[b.name])), fill=col, width=4)
                for n, q in J.items():
                    x, y = P(q)
                    d.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(20, 110, 230, 255), outline=(255, 255, 255, 255))
            elif k == "garments":
                lab = self.garments.overlay()
                posed = self._warp_rest_overlay(lab, res)
                im = Image.fromarray(posed, "RGBA")
                d = ImageDraw.Draw(im)
                h = self.garments.hem
                q = self.solver.K.point(0, (h["pivot_x"], h["pivot_y"]), res["pose"])
                for nm in ("upper_arm_r", "upper_arm_l", "shin_r", "shin_l", "forearm_r", "forearm_l"):
                    x, y = P(J[nm])
                    d.rectangle((x - 7, y - 7, x + 7, y + 7), outline=(255, 255, 255, 255), width=3)
                x, y = P(q)
                d.polygon([(x, y - 10), (x + 10, y), (x, y + 10), (x - 10, y)], outline=(255, 40, 120, 255), width=3)
            elif k == "boundaries":
                ov = np.zeros((H, W, 4), np.uint8)
                rng = np.random.default_rng(7)
                for cid, a in r["alphas"].items():
                    edge = a & ~ndimage.binary_erosion(a)
                    ov[edge] = (*rng.integers(40, 255, 3).tolist(), 255)
                im = Image.fromarray(ov, "RGBA")
            elif k == "weights":
                corr = self.gdef.corrections(res["pose"], res["intent"].garment)
                im = Image.fromarray(self.dfm.weight_overlay(res["pose"], corr, pad, r), "RGBA")
            elif k == "overlaps":
                cnt = np.zeros((H, W), np.int16)
                for a in r["alphas"].values():
                    cnt += a
                ov = np.zeros((H, W, 4), np.uint8)
                ov[cnt >= 2] = (255, 0, 255, 140)
                im = Image.fromarray(ov, "RGBA")
            elif k == "contacts":
                gy = self.profile.ground_y + pad
                d.line((0, gy, W, gy), fill=(0, 170, 0, 255), width=3)
                for s in SIDES:
                    x, y = P(J[f"foot_{s}"])
                    planted = info["contact"][s]
                    col = (0, 200, 0, 255) if planted else (230, 160, 0, 255)
                    d.ellipse((x - 10, y - 10, x + 10, y + 10), outline=col, width=4)
                    if planted:
                        d.line((x, y, x, gy), fill=col, width=2)
            elif k == "com":
                cx, cy = P(info["com"])
                gy = self.profile.ground_y + pad
                d.line((cx, cy, cx, gy), fill=(230, 120, 0, 255), width=3)
                d.ellipse((cx - 11, cy - 11, cx + 11, cy + 11), outline=(230, 120, 0, 255), width=4)
                xr = info["support"].get("x_range")
                if xr:
                    d.line((xr[0] + pad, gy + 8, xr[1] + pad, gy + 8), fill=(0, 120, 0, 255), width=8)
            elif k == "provenance":
                ov = np.zeros((H, W, 4), np.uint8)
                ov[r["generated_visible"]] = (255, 0, 255, 200)
                im = Image.fromarray(ov, "RGBA")
            elif k == "missing":
                y = 10
                for m in res["feasibility"]["missing"]:
                    d.rectangle((8, y - 2, 8 + 9 * len(m["asset"]) + 140, y + 16), fill=(180, 0, 0, 220))
                    d.text((12, y), f"MISSING {m['asset']} ({m['state']})", fill=(255, 255, 255, 255))
                    y += 22
                for s, h in res["hands"].items():
                    if h["note"]:
                        x, yy = P(J[f"hand_{s}"])
                        d.ellipse((x - 26, yy - 26, x + 26, yy + 26), outline=(220, 0, 0, 255), width=4)
            elif k == "correctives":
                ov = np.zeros((H, W, 4), np.uint8)
                for g in res.get("gussets", []):
                    poly = [(x + pad, y + pad) for x, y in g["polygon_posed_px"]]
                    if len(poly) >= 3:
                        ImageDraw.Draw(im).polygon(poly, outline=(255, 0, 0, 255), width=3)
                for c in res["corrections"]:
                    if c["rule"].startswith("band:"):
                        bn = c["rule"][5:]
                        x, y = P(J[bn])
                        rad = (self.gsk.rp[self.sk.idx[bn]] + self.gsk.rc[self.sk.idx[bn]]) / 2
                        a = int(60 + 150 * min(1.0, (c["band_scale"] - 1) / 0.7))
                        ImageDraw.Draw(im).ellipse((x - rad, y - rad, x + rad, y + rad), outline=(255, 200, 0, a), width=3)
                del ov
            out[k] = np.asarray(im)
        return out

    def _warp_rest_overlay(self, rgba: np.ndarray, res: dict[str, Any]) -> np.ndarray:
        """Move a rest-pose overlay with the pose (forward map of the owning layer's pixels)."""
        r, pad = res["render"], res["pad"]
        H, W = r["image"].shape[:2]
        out = np.zeros((H, W, 4), np.uint8)
        corr = self.gdef.corrections(res["pose"], res["intent"].garment)
        self.gsk.set_bands(corr["rp"], corr["rc"])
        self.dfm.terms = corr["terms"]
        try:
            for cid in r["order"]:
                if cid not in self.dfm.info:
                    continue
                inf = self.dfm.info[cid]
                x0, y0 = inf["origin"]
                ay, ax = np.nonzero(inf["alpha0"])
                ay, ax = ay[::2], ax[::2]
                pts = np.stack([ax + x0, ay + y0], 1).astype(np.float64)
                cols = rgba[ay + y0, ax + x0]
                keep = cols[:, 3] > 0
                q = self.dfm.forward(cid, pts[keep], res["pose"])
                qx, qy = np.rint(q[:, 0] + pad).astype(int), np.rint(q[:, 1] + pad).astype(int)
                ok = (qx >= 0) & (qx < W) & (qy >= 0) & (qy < H)
                out[qy[ok], qx[ok]] = cols[keep][ok]
        finally:
            self.dfm.terms = []
        return ndimage.grey_dilation(out, size=(3, 3, 1))

    def closeups(self, res: dict[str, Any], before: dict[str, Any] | None = None, zoom: float = 1.6) -> Image.Image:
        """Blazer shoulders, sleeves/elbows, pelvis and hem, trouser knees, shoes and ankles."""
        J = res["solve"]["joints_posed"]
        pad = res["pad"]
        regions = [("blazer shoulder r", "upper_arm_r", 120), ("blazer shoulder l", "upper_arm_l", 120),
                   ("sleeve / elbow r", "forearm_r", 100), ("sleeve / elbow l", "forearm_l", 100),
                   ("pelvis + blazer hem", "pelvis", 150), ("trouser knee r", "shin_r", 100),
                   ("trouser knee l", "shin_l", 100), ("shoe / ankle r", "foot_r", 100), ("shoe / ankle l", "foot_l", 100)]
        a_img = on_bg(res["image"], (214, 230, 214))
        b_img = on_bg(before["image"], (214, 230, 214)) if before is not None else None
        bJ = before["solve"]["joints_posed"] if before is not None else None
        cells = []
        for title, bn, r in regions:
            size = (int(2 * r * zoom), int(2 * r * zoom))
            col = Image.new("RGB", (size[0], size[1] * (2 if b_img else 1) + 24), "white")
            ImageDraw.Draw(col).text((4, 5), title + ("  (top: Phase 3, bottom: Phase 3.1)" if b_img else ""),
                                     fill=(0, 0, 0))
            y0 = 24
            if b_img:
                x, y = bJ[bn]
                box = (int(x + pad - r), int(y + pad - r), int(x + pad + r), int(y + pad + r))
                col.paste(b_img.crop(box).resize(size, Image.LANCZOS), (0, y0))
                y0 += size[1]
            x, y = J[bn]
            box = (int(x + pad - r), int(y + pad - r), int(x + pad + r), int(y + pad + r))
            col.paste(a_img.crop(box).resize(size, Image.LANCZOS), (0, y0))
            cells.append(col)
        h = max(c.height for c in cells)
        w = sum(c.width for c in cells) + 6 * (len(cells) - 1)
        sheet = Image.new("RGB", (w, h), "white")
        x = 0
        for c in cells:
            sheet.paste(c, (x, 0))
            x += c.width + 6
        return sheet

    def library(self) -> list[dict[str, Any]]:
        return [s.to_json() for s in LIBRARY]


# -- package-level evidence (CLI: autorig motion <package>) ---------------------------------------------------
def engine_for_package(pkg, view: str = "front") -> tuple["MotionEngine", list[str]]:
    """MotionEngine for a Phase 2 package, built exactly as the editor builds it (edited bones, masks, approvals)."""
    import json
    from pathlib import Path

    from ..editor.artwork import ArtworkModel
    from ..editor.posesvc import PoseService
    from ..editor.project import EditorProject
    from .profile import load_overrides

    pkg = Path(pkg)
    proj, art = EditorProject(pkg), ArtworkModel(pkg)
    ps = PoseService(pkg, art)
    st = proj.state()
    m = art.masks(st["mask_ops"])
    sk = ps.skeleton(st, m, None)
    rej = {k for k, v in st["approvals"].items() if v.get("status") == "rejected"}
    layers = {c: art.layer_rgba(c, m, rej) for c in m}
    gen = {c: art.generated_mask(c, m, rej) for c in m}
    cids = [k.split(":", 1)[1] for k in st["approvals"] if k.startswith("corrective:gusset")]
    eng = MotionEngine(sk, layers, gen, art.source, ps.variants, st["approvals"], view, load_overrides(pkg), cids)
    vis = [L["id"] for L in st["layers"] if L["visible"] and L["kind"] == "bitmap" and L["id"] in m]
    del json
    return eng, vis


def _tile(img: np.ndarray, box, title: str, sub: str, colour=(34, 40, 60)) -> Image.Image:
    im = on_bg(img).crop(box)
    cell = Image.new("RGB", (im.width, im.height + 58), "white")
    d = ImageDraw.Draw(cell)
    d.rectangle((0, 0, im.width, 54), fill=colour)
    d.text((8, 8), title, fill=(255, 255, 255))
    d.text((8, 30), sub, fill=(210, 220, 255))
    cell.paste(im, (0, 58))
    return cell


def _rows(rows: list[list[Image.Image]], scale: float = 1.0, gap: int = 14) -> Image.Image:
    rows = [[t.resize((int(t.width * scale), int(t.height * scale)), Image.LANCZOS) for t in r] for r in rows if r]
    W = max(sum(t.width for t in r) + gap * (len(r) - 1) for r in rows) + 2 * gap
    H = sum(max(t.height for t in r) for r in rows) + gap * (len(rows) + 1)
    sheet = Image.new("RGB", (W, H), (236, 238, 242))
    y = gap
    for r in rows:
        x = gap
        for t in r:
            sheet.paste(t, (x, y))
            x += t.width + gap
        y += max(t.height for t in r) + gap
    return sheet


def _summary(res: dict[str, Any]) -> dict[str, Any]:
    q, f, s = res["quality"], res["feasibility"], res["solve"]
    return {"name": res["name"], "title": res["title"], "phase": res["phase"], "params": res["params"],
            "feasibility": f, "quality": {k: q[k] for k in ("checks", "numerical", "warnings", "review_items",
                                                           "artistic_review_required", "visual_quality")},
            "hands": res["hands"], "corrections": res["corrections"], "order": res["order"],
            "gussets": [{k: g[k] for k in ("side", "pixels", "torn_pairs")} for g in res["gussets"]],
            "solve": {k: s[k] for k in ("joints_posed", "contact", "contact_loaded", "pelvis_shift_px", "clamps",
                                        "violations", "anatomical", "legs", "arms", "clavicle_elevation")
                      if k in s} | {"com": [round(float(v), 2) for v in s["com"]]},
            "intent": {k: v for k, v in res["intent"].to_json().items() if k not in ("legs", "arms")},
            "timing_ms": res["timing_ms"]}


def motion_report(pkg, out=None, progress=print) -> dict[str, Any]:
    """Render the evidence set for Phase 3.1 into ``<package>/natural_pose/motion`` (or ``out``)."""
    import json
    from pathlib import Path

    pkg = Path(pkg)
    out = Path(out) if out else pkg / "natural_pose" / "motion"
    (out / "closeups").mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    eng, vis = engine_for_package(pkg)
    progress(f"engine built in {eng.build_s:.1f} s")
    (out / "profile.json").write_text(json.dumps(eng.profile.to_json(), indent=1, default=float))
    (out / "garments.json").write_text(json.dumps(eng.garments.to_json(), indent=1, default=float))
    Image.fromarray(eng.garments.overlay(), "RGBA").save(out / "garment_regions.png", optimize=True)
    results: dict[str, Any] = {"presets": {}, "cycles": {}, "generated_s": None}
    pad = 160
    H, W = eng.source.shape[:2]
    box = (pad + int(eng.profile.midline_x) - 360, pad + 40, pad + int(eng.profile.midline_x) + 360, pad + H + 60)
    renders: dict[str, Any] = {}

    def run(key, name, phase=None, allow_pending=False):
        r = eng.pose(name, phase, visible=vis, allow_pending=allow_pending, pad=pad)
        renders[key] = r
        results["presets"][key] = _summary(r)
        progress(f"{key}: {r['feasibility']['status']}, checks {r['quality']['numerical']['passed']}/"
                 f"{r['quality']['numerical']['total']}, {r['timing_ms']['total']:.0f} ms")
        return r

    def lab(r):
        q = r["quality"]["numerical"]
        return f"{r['feasibility']['status']} | checks {q['passed']}/{q['total']} | review required"

    neutral = np.zeros((H + 2 * pad, W + 2 * pad, 4), np.uint8)
    for c in motion_order(vis, eng.planner.plan("relaxed_standing"), {s: {"drawing": "open"} for s in SIDES}, set()):
        lay = eng.layers.get(c)
        if lay is None:
            continue
        sub = neutral[pad:pad + H, pad:pad + W]
        a = lay[..., 3:4].astype(np.float32) / 255
        sub[..., :3] = (lay[..., :3] * a + sub[..., :3] * (1 - a)).astype(np.uint8)
        sub[..., 3] = np.maximum(sub[..., 3], lay[..., 3])
    row1 = [_tile(neutral, box, "Original neutral (reference pixels)", "identity pose: bit-exact")]
    for key, name in (("relaxed_standing", "relaxed_standing"), ("pointing_speaking", "pointing_speaking"),
                      ("both_hands_raised", "both_hands_raised"), ("head_tilt_compensated", "head_tilt_compensated"),
                      ("conversational_gesture", "conversational_gesture"), ("one_hand_shrug", "one_hand_shrug"),
                      ("hands_on_hips", "hands_on_hips")):
        r = run(key, name)
        row1.append(_tile(r["image"], box, r["title"], lab(r)))
    row2 = []
    for i in range(4):
        r = run(f"casual_walk@{i / 4:.3f}", "casual_walk", i / 4)
        row2.append(_tile(r["image"], box, f"Walking {i / 4:.2f} ({r['intent'].channels['phase_name']})", lab(r)))
    row3, row4 = [], []
    for i in range(8):
        r = run(f"running@{i / 8:.3f}", "running", i / 8)
        row3.append(_tile(r["image"], box, f"Running {i / 8:.3f} ({r['intent'].channels['phase_name']}), open hands",
                          lab(r)))
    for i in range(8):
        r = run(f"running_fists_preview@{i / 8:.3f}", "running", i / 8, allow_pending=True)
        row4.append(_tile(r["image"], box, f"Running {i / 8:.3f}, PREVIEW of pending fists", lab(r), (120, 40, 40)))
    row5 = []
    for key in ("sitting", "natural_squat", "side_lean", "leaning_forward", "hands_at_sides", "leaning_backward"):
        r = run(key, key)
        note = " (fallback shown)" if key.startswith("leaning") else " (approximation)" if key == "sitting" else ""
        row5.append(_tile(r["image"], box, r["title"] + note, lab(r)))
    _rows([row1, row2 + row5[:4], row3, row4, row5[4:]], 0.6).save(out / "contact_sheet.jpg", quality=90)
    _rows([row3]).save(out / "run_cycle.jpg", quality=90)
    _rows([row4]).save(out / "run_cycle_fists_preview.jpg", quality=90)
    progress("contact sheets written")
    # cycles (skeleton level, 64 samples) in place and moving
    for name in ("running", "casual_walk"):
        for travel in (0.0, 40.0):
            for ap in ((False, True) if name == "running" else (False,)):
                key = f"{name}{'_moving' if travel else ''}{'_fists' if ap else ''}"
                results["cycles"][key] = eng.cycle(name, 64, travel=travel, allow_pending=ap)
    # before / after (the Phase 3 engine on the same package) and close-ups
    pairs = []
    for key, name in (("relaxed_standing", "relaxed_standing"), ("pointing_speaking", "pointing_speaking"),
                      ("both_hands_raised", "both_hands_raised"), ("natural_squat", "natural_squat"),
                      ("sitting", "sitting"), ("running@0.250", "running"), ("casual_walk@0.250", "casual_walk")):
        b = eng.legacy(name, pad)
        new = renders[key]
        pairs.append([_tile(b["image"], box, f"Phase 3: {name}", "previous engine", (90, 90, 90)),
                      _tile(new["image"], box, f"Phase 3.1: {new['title']}", lab(new))])
        eng.closeups(new, b).save(out / "closeups" / f"{key.replace('@', '_')}.jpg", quality=90)
    _rows([sum(pairs[:4], []), sum(pairs[4:], [])], 0.6).save(out / "before_after.jpg", quality=90)
    progress("before/after and close-ups written")
    # diagnostics for one run frame
    diag = eng.diagnostics(renders["running@0.250"], list(DIAGNOSTICS))
    tiles = []
    base = on_bg(renders["running@0.250"]["image"])
    for k, ov in diag.items():
        im = base.copy().convert("RGBA")
        im.alpha_composite(Image.fromarray(ov, "RGBA"))
        tiles.append(_tile(np.asarray(im), box, f"diagnostic: {k}", "running 0.25"))
    _rows([tiles[:5], tiles[5:]], 0.5).save(out / "diagnostics_running.jpg", quality=90)
    results["generated_s"] = round(time.time() - t0, 1)
    results["library"] = eng.library()
    results["profile"] = eng.profile.to_json()
    (out / "results.json").write_text(json.dumps(results, indent=1, default=lambda o: o.tolist()
                                                 if isinstance(o, np.ndarray) else float(o)))
    progress(f"done in {results['generated_s']} s")
    return results
