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

        from ..handstore import STORE_DIR

        from ..handstore import engine_input

        appr = {k: v for k, v in st["approvals"].items() if k.startswith(("corrective:", "handstore:"))}
        idx = self.pkg / STORE_DIR / "index.json"
        store_rev = hashlib.sha256(idx.read_bytes()).hexdigest()[:12] if idx.is_file() else None
        cids = [k.split(":", 1)[1] for k in st["approvals"] if k.startswith("corrective:gusset")]
        # the engine is rebuilt only when the character changes; Hand Library edits and approvals update it in place
        base_key = "motion:" + _digest([st["mask_ops"], st["bones"], st["corrections"], sorted(rejected),
                                        [(L["id"], L["visible"]) for L in st["layers"]], sorted(cids)])
        mf = self.pkg / "analysis" / "hand_asset_metadata.json"
        meta_rev = hashlib.sha256(mf.read_bytes()).hexdigest()[:12] if mf.is_file() else None
        hand_key = _digest([appr, st.get("hand_store"), store_rev, st.get("hand_metadata"), meta_rev])
        eng = self._cache.get(base_key)
        w_ops = (st.get("garment_weights") or {}).get("ops", [])
        w_key = _digest([w_ops])
        if eng is not None and getattr(eng, "_hand_key", None) == hand_key:
            self._sync_weights(eng, w_ops, w_key)
            self.sync_correctives(eng)
            return eng
        sk = eng.sk if eng is not None else self.skeleton(st, masks, None)
        layers = eng.layers if eng is not None else {c: self.art.layer_rgba(c, masks, rejected) for c in masks}
        hs = engine_input(self.pkg, st.get("hand_store"), sk, layers, self.variants, st["approvals"], self.art.source,
                          st.get("hand_metadata"))
        if eng is not None:
            eng.set_hand_store(hs, st["approvals"])
        else:
            gen = {c: self.art.generated_mask(c, masks, rejected) for c in masks}
            from ..garmentrig.structure import load_config  # noqa: PLC0415

            from ..garmentrig.correctives import CorrectiveArtworkStore  # noqa: PLC0415

            eng = MotionEngine(sk, layers, gen, self.art.source, self.variants, st["approvals"], "front",
                               load_overrides(self.pkg), cids, corrective_manifest(self.pkg), hs,
                               "structured", load_config(self.pkg), w_ops, CorrectiveArtworkStore(self.pkg))
            eng._weights_key = w_key
            for k in [k for k in self._cache if k.startswith("motion:")]:
                self._cache.pop(k)
            self._cache[base_key] = eng
        eng._hand_key = hand_key
        self._sync_weights(eng, w_ops, w_key)
        self.sync_correctives(eng)
        from ..motion.handselect import NaturalHandSelectionEngine, load_rules  # noqa: PLC0415

        eng.hand_select = NaturalHandSelectionEngine.for_character(
            load_rules(self.pkg), (st.get("hand_selection") or {}).get("weights"))
        return eng

    # -- Phase 3.5 Artistic Review -----------------------------------------------------------------------------
    def artistic_info(self, st: dict[str, Any]) -> dict[str, Any]:
        from ..artistic.poses import action_library  # noqa: PLC0415

        lib = action_library()
        A = st.get("artistic") or {}
        acts = {a: {"title": e["base"].title, "intention": e["base"].intention, "variants": list(e["variants"]),
                    "requires": e["base"].requires} for a, e in lib.items()}
        appr = {k: v for k, v in st["approvals"].items() if k.startswith(("artistic_pose:", "alternative:"))}
        return {"actions": acts, "selections": A.get("selections", {}), "templates": A.get("templates", []),
                "constraints": A.get("constraints", []), "notes": A.get("notes", []),
                "cycle_revisions": A.get("cycle_revisions", []), "layer_rules": A.get("layer_rules", []),
                "approvals": appr,
                "note": "Candidates are previews (pending hand drawings and correctives are shown, labelled). A pose is "
                        "approved only by you; automatic rankings are recommendations, never verdicts."}

    def artistic_candidates(self, st, masks, rejected, action: str) -> dict[str, Any]:
        from ..artistic.candidates import PoseCandidateGenerator, assess, render_pose  # noqa: PLC0415
        from ..artistic.layer_order import DynamicLayerOrderController  # noqa: PLC0415
        from ..artistic.poses import ArtisticPose, action_library  # noqa: PLC0415

        if action not in action_library():
            raise PoseError(f"unknown action {action!r}")
        A = st.get("artistic") or {}
        eng = self.motion_engine(st, masks, rejected)
        vis = [L["id"] for L in st["layers"] if L["visible"] and L["kind"] == "bitmap" and L["id"] in masks]
        cst = st.get("correctives") or {}
        gen = PoseCandidateGenerator(eng, vis, constraints=A.get("constraints", []))
        rules = A.get("layer_rules", [])
        out = []
        variants = list(gen.lib[action]["variants"])
        from ..artistic.poses import apply_constraints, variant  # noqa: PLC0415

        items = [(v, apply_constraints(variant(action, v, gen.lib), gen.constraints)) for v in variants]
        items += [(f"template:{t['name']}", ArtisticPose.from_json(t["pose"])) for t in A.get("templates", [])
                  if t.get("action") == action]
        for name, ap in items:
            r = render_pose(eng, vis, ap, planner=gen.planner, corrective_mode=cst.get("mode", "auto"),
                            extra={"corrective_settings": cst.get("settings") or {}},
                            layer_order=DynamicLayerOrderController(rules))
            a = assess(eng, ap, r)
            key = f"artistic_pose:{action}/{name}"
            out.append({"variant": name, "image": r["image"], "silhouette": a["silhouette_image"],
                        "feasibility": a["feasibility"], "warnings": a["silhouette"]["warnings"],
                        "metrics": a["silhouette"]["metrics"], "hands": a["hands"], "pending_hands": a["pending_hands"],
                        "layer_order": r.get("layer_order"), "correctives": [c["asset_id"] for c in r.get("correctives", [])
                                                                             if c.get("shown")],
                        "pose": ap.to_json(), "approval_key": key,
                        "state": (st["approvals"].get(key) or {}).get("status", "pending_review")})
        from ..artistic.candidates import rank_key  # noqa: PLC0415

        ranked = sorted(out, key=lambda c: rank_key({"feasibility": c["feasibility"], "silhouette": {
            "warnings": c["warnings"]}, "deformation_risk": 0}))
        return {"action": action, "candidates": out, "recommended": ranked[0]["variant"] if ranked else None,
                "selected": (A.get("selections") or {}).get(action)}

    # -- Phase 3.4 Correctives tab ------------------------------------------------------------------------
    def correctives(self, st: dict[str, Any]) -> dict[str, Any]:
        """Assets (latest versions, all versions listed), approval state, requirements, settings, quality summary."""
        from ..garmentrig.correctives import CorrectiveArtworkStore, approval_state  # noqa: PLC0415

        store = CorrectiveArtworkStore(self.pkg)
        appr = st["approvals"]
        latest = {a.asset_id: a for a in store.latest()}
        versions: dict[str, list[dict[str, Any]]] = {}
        for rec in store.index["assets"]:
            versions.setdefault(rec["asset_id"], []).append(
                {"version": rec["version"], "provenance": rec["provenance"], "created": rec.get("created"),
                 "state": (appr.get(f"corrective:{rec['asset_id']}@v{rec['version']}") or {}).get("status",
                                                                                                 "pending_review")})
        assets = []
        for a in latest.values():
            imgs = [{"key": "main", "file": a.file}] if a.file else []
            imgs += [{"key": k["key_id"], "file": k.get("file"), "values": k.get("values")} for k in a.keys
                     if k.get("file")]
            assets.append({"asset_id": a.asset_id, "version": a.version, "category": a.category,
                           "body_part": a.body_part, "side": a.side, "method": a.method, "provenance": a.provenance,
                           "approval_key": a.approval_key, "state": approval_state(appr, a),
                           "activation": a.activation, "drivers": a.drivers, "placement": a.placement,
                           "procedural": bool(a.params.get("procedural")), "images": imgs,
                           "replaceable": bool(a.file) and not a.params.get("procedural")
                                          and a.placement.get("mode") != "derived_layers",
                           "moho": a.moho, "requirement_id": a.requirement_id, "versions": versions.get(a.asset_id, [])})
        def _load(rel):
            f = self.pkg / rel
            return json.loads(f.read_text()) if f.is_file() else None
        q = _load("reports/corrective_quality.json") or {}
        return {"assets": sorted(assets, key=lambda x: (x["category"], x["asset_id"])),
                "requirements": (_load("analysis/corrective_requirements.json") or {}).get("requirements", []),
                "settings": (st.get("correctives") or {}).get("settings", {}),
                "mode": (st.get("correctives") or {}).get("mode", "auto"),
                "quality": {k: {"status": v["status"], "title": v.get("title")}
                            for k, v in ((q.get("results") or {}).get("poses") or {}).items()},
                "store_ok": not store.verify(),
                "note": "Generated corrective artwork is pending review until you approve it. Nothing is approved "
                        "automatically; you can keep editing without accepting any of it."}

    def corrective_image(self, rel: str) -> np.ndarray:
        from ..garmentrig.correctives import CorrectiveArtworkStore  # noqa: PLC0415

        store = CorrectiveArtworkStore(self.pkg)
        known = {r for a in store.index["assets"] for r in a.get("sha256", {})}
        if rel not in known:
            raise PoseError(f"unknown corrective image {rel!r}")
        return store.image(rel)

    def corrective_generate(self, st, masks, rejected) -> dict[str, Any]:
        from ..garmentrig.correctives import CorrectiveArtworkStore, generate_all  # noqa: PLC0415

        eng = self.motion_engine(st, masks, rejected)
        vis = [L["id"] for L in st["layers"] if L["visible"] and L["kind"] == "bitmap" and L["id"] in masks]
        log: list[str] = []
        made = generate_all(eng, CorrectiveArtworkStore(self.pkg), vis, log.append)
        self.sync_correctives(eng)
        return {"created": [{"asset_id": a.asset_id, "version": a.version, "approval_key": a.approval_key}
                            for a in made], "log": log}

    def corrective_replace(self, st, masks, rejected, asset_id: str, rgba: np.ndarray, note: str = "") -> dict[str, Any]:
        """Manual artwork: the user's drawing replaces an image asset as a new version (ARTIST_EDITED, pending
        review). Same placement, attachment and activation; the drawing must have the asset's canvas size."""
        import copy as _c  # noqa: PLC0415

        from ..garmentrig.correctives import CorrectiveArtworkStore  # noqa: PLC0415

        store = CorrectiveArtworkStore(self.pkg)
        cur = next((a for a in store.latest() if a.asset_id == asset_id), None)
        if cur is None:
            raise PoseError(f"unknown corrective asset {asset_id!r}")
        if not cur.file or cur.params.get("procedural") or cur.placement.get("mode") == "derived_layers":
            raise PoseError(f"{asset_id} is procedural (its shape follows the pose): edit its settings instead")
        old = store.image(cur.file)
        if rgba.ndim != 3 or rgba.shape[2] != 4 or rgba.shape[:2] != old.shape[:2]:
            raise PoseError(f"the drawing must be RGBA {old.shape[1]}x{old.shape[0]} (the asset's canvas)")
        if not (rgba[..., 3] > 0).any():
            raise PoseError("the drawing is empty")
        new = _c.deepcopy(cur)
        new.provenance = "ARTIST_EDITED"
        new.method = f"{cur.method} + artist drawing"
        new.params = {**cur.params, "replaces_version": cur.version, "note": note[:200]}
        new.source_refs = list(cur.source_refs) + [f"artist drawing (editor upload) replacing v{cur.version}"]
        new.sha256 = {}
        a = store.add(new, {"main": rgba.astype(np.uint8)})
        eng = self.motion_engine(st, masks, rejected)
        self.sync_correctives(eng)
        return {"asset_id": a.asset_id, "version": a.version, "approval_key": a.approval_key,
                "provenance": a.provenance}

    def sync_correctives(self, eng) -> None:
        """Re-read the corrective store when its index changed (generation, replacement drawing)."""
        f = self.pkg / "analysis" / "corrective_assets.json"
        rev = hashlib.sha256(f.read_bytes()).hexdigest()[:12] if f.is_file() else None
        if eng.correctives is not None and getattr(eng, "_corrective_rev", None) != rev:
            eng.correctives.reload()
            eng._corrective_rev = rev

    @staticmethod
    def _sync_weights(eng, ops, key) -> None:
        """Weight edits (undo/redo included) replay on the automatic weights without rebuilding the engine."""
        if eng.weights is None or getattr(eng, "_weights_key", None) == key:
            return
        eng.weights.set_ops(ops, {c: inf["rgba"] for c, inf in eng.dfm.info.items()})
        eng.dfm.refresh_weights()
        eng._weights_key = key

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
        hand_layers, preview_note = None, None
        if body.get("hand_preview"):  # {side, cid}: show one Hand Library candidate in this pose
            hp = body["hand_preview"]
            if hp.get("side") not in ("r", "l"):
                raise PoseError(f"hand preview needs side r|l: {hp}")
            v = eng.hand_lib.candidate(hp["side"], hp.get("cid", ""))
            if v is None or v.layer is None:  # e.g. its library was archived since: no preview, no error
                preview_note = f"hand preview {hp.get('cid')} is not a candidate any more (archived or deleted)"
            else:
                hand_layers = {hp["side"]: v.layer}
        hsel = st.get("hand_selection") or {}
        cst = st.get("correctives") or {}
        cmode = body.get("corrective_mode") or cst.get("mode", "auto")
        if cmode not in ("auto", "all", "off") and not str(cmode).startswith("only:"):
            raise PoseError(f"unknown corrective_mode {cmode!r}")
        csettings = cst.get("settings") or {}
        actions = {**(hsel.get("actions") or {}), **(body.get("hand_actions") or {})}
        targets = {**(hsel.get("targets") or {}), **(body.get("hand_targets") or {})}
        r = eng.pose(name, body.get("phase"), float(body.get("speed", 1.0)), float(body.get("exaggeration", 1.0)),
                     0.0, bool(body.get("allow_pending")), overrides, body.get("live_head") or None, visible,
                     skeleton_only=bool(body.get("skeleton_only")), fluid=bool(body.get("fluid", True)),
                     ik=ik, switches=switches, switch_time=body.get("switch_time"), hand_layers=hand_layers,
                     hand_actions={k: v for k, v in actions.items() if v}, hand_targets={k: v for k, v in targets.items() if v},
                     corrective_mode=cmode, corrective_settings=csettings)
        s = r["solve"]
        out = {"name": name, "title": r["title"], "kind": r["kind"], "phase": r["phase"], "pad": r["pad"],
               "joints_posed": s["joints_posed"], "bone_tails_posed": s["bone_tails_posed"], "contact": s["contact"],
               "com": [round(float(v), 2) for v in s["com"]], "support": s["support"],
               "feasibility": r["feasibility"], "hands": r["hands"], "corrections": r["corrections"],
               "timing_ms": r["timing_ms"], "phase_name": r["intent"].channels.get("phase_name"),
               "violations": s["violations"], "clamps": s["clamps"], "spec": r["spec"],
               "local_rotation_deg": {b.name: round(float(r["pose"].theta[i]), 2) for i, b in enumerate(eng.sk.bones)},
               "ik": self.ik_report(eng, r, ik), "switches": r["switches"],
               "correction_switches": [c for c in r["correction_switches"] if c["on"]], "hand_preview_note": preview_note,
               "correctives": r.get("correctives", []), "corrective_mode": cmode,
               "hand_selection": {"decisions": {s: {k: d.get(k) for k in ("action", "rule", "chosen", "cid", "score",
                                                                          "reason", "held", "ranking", "aim_applied",
                                                                          "missing_preferred", "overridden_by")}
                                                for s, d in r["hand_selection"]["decisions"].items()},
                                  "grip": r["hand_selection"].get("grip")}}
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
        if body.get("corrective_compare"):  # the same pose without corrective artwork (before/after)
            b = eng.pose(name, body.get("phase"), float(body.get("speed", 1.0)), float(body.get("exaggeration", 1.0)),
                         0.0, bool(body.get("allow_pending")), overrides, body.get("live_head") or None, visible,
                         fluid=bool(body.get("fluid", True)), ik=ik, switches=switches,
                         switch_time=body.get("switch_time"), hand_layers=hand_layers,
                         hand_actions={k: v for k, v in actions.items() if v},
                         hand_targets={k: v for k, v in targets.items() if v}, evaluate_garments=False,
                         corrective_mode="off")
            out["corrective_before"] = b["image"]
        out["corrective_px"] = int(r["render"].get("corrective_px", 0))
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

    # -- garment rig (Phase 3.3 Weights tab) --------------------------------------------------------------------
    def garments(self, st, masks, rejected) -> dict[str, Any]:
        eng = self.motion_engine(st, masks, rejected)
        if eng.structure is None:
            return {"available": False}
        layers = {}
        for cid, m in eng.weights.meshes.items():
            if cid in eng.layers:
                parts = sorted({eng.structure.parts[i].name for i in np.unique(m.part) if i >= 0})
                layers[cid] = {"bones": list(m.bones), "parts": parts, "grid": list(m.shape),
                               "locked": int(m.locked.sum()), "edited": int((m.touched > 0).sum())}
        return {"available": True, "structure": eng.structure.to_json(), "pivots": eng.pivot_report,
                "layers": layers, "validation": eng.weights.validate(),
                "ops": (st.get("garment_weights") or {}).get("ops", []), "classes": ["RIGID", "LIMITED_DEFORMATION",
                                                                                      "FLEXIBLE"]}

    def garment_overlay(self, st, masks, rejected, kind: str, layer: str | None, bone: str | None) -> np.ndarray:
        """Rest-frame RGBA overlay: ``parts`` (garment part map), ``influence`` (a bone's subtree influence as a
        heatmap, red = 1), ``mesh`` (the layer's grid: active, locked, edited vertices) or ``pivots``."""
        eng = self.motion_engine(st, masks, rejected)
        H, W = eng.source.shape[:2]
        out = np.zeros((H, W, 4), np.uint8)
        if eng.structure is None:
            return out
        vis = [L["id"] for L in st["layers"] if L["visible"] and L["kind"] == "bitmap" and L["id"] in masks]
        cids = [layer] if layer in eng.weights.meshes else [c for c in vis if c in eng.weights.meshes]
        if kind == "parts":
            return eng.structure.overlay(cids)
        if kind == "influence" and bone in eng.sk.idx:
            b = eng.sk.idx[bone]
            for cid in cids:
                inf = eng.dfm.info.get(cid)
                if inf is None:
                    continue
                x0, y0 = inf["origin"]
                ay, ax = np.nonzero(inf["alpha0"])
                pts = np.stack([ax + x0, ay + y0], 1).astype(np.float64)
                f = inf["wm"].influence(b, pts)
                v = np.clip(f, 0, 1)
                col = np.stack([255 * v, 60 + 120 * (1 - v) * v * 4, 255 * (1 - v), np.where(v > 0.002, 200, 40)], 1)
                out[ay + y0, ax + x0] = np.clip(col, 0, 255).astype(np.uint8)
            return out
        if kind == "mesh":
            for cid in cids[:1] if layer else []:
                m = eng.weights.meshes[cid]
                xy = m.vertex_xy()
                for (vy, vx) in zip(*np.nonzero(m.active)):
                    x, y = int(round(xy[vy, vx, 0])), int(round(xy[vy, vx, 1]))
                    if 1 <= x < W - 1 and 1 <= y < H - 1:
                        c = (255, 60, 60, 255) if m.locked[vy, vx] else (255, 200, 0, 255) if m.touched[vy, vx] else (40, 120, 255, 220)
                        out[y - 1:y + 2, x - 1:x + 2] = c
            return out
        return out

    def garment_explain(self, st, masks, rejected, layer: str, x: float, y: float) -> dict[str, Any]:
        eng = self.motion_engine(st, masks, rejected)
        if eng.weights is None or layer not in eng.weights.meshes:
            raise PoseError(f"no weight mesh for layer {layer}")
        return eng.weights.explain(layer, (x, y))

    def garment_compare(self, st, masks, rejected, preset: str) -> np.ndarray:
        """Side by side: the pose with the automatic weights (left) and with the user's weight edits (right)."""
        from ..phase2.pose_eval import on_bg  # noqa: PLC0415

        eng = self.motion_engine(st, masks, rejected)
        vis = [L["id"] for L in st["layers"] if L["visible"] and L["kind"] == "bitmap" and L["id"] in masks]
        ops = (st.get("garment_weights") or {}).get("ops", [])
        lay = {c: inf["rgba"] for c, inf in eng.dfm.info.items()}
        imgs = []
        for o in ([], ops):
            eng.weights.set_ops(o, lay)
            eng.dfm.refresh_weights()
            r = eng.pose(preset, visible=vis, evaluate_garments=False)
            imgs.append(np.asarray(on_bg(r["image"]).convert("RGB")))
        eng._weights_key = None
        self._sync_weights(eng, ops, _digest([ops]))
        h = min(i.shape[0] for i in imgs)
        return np.concatenate([imgs[0][:h], np.full((h, 8, 3), 255, np.uint8), imgs[1][:h]], 1)

    def hand_library(self, st, masks, rejected) -> dict[str, Any]:
        """Hand Library panel data: every candidate per side and pose (original, Phase 2 drawing, stored drawings),
        which one is active, its approval, provenance, placement and measured alignment."""
        from ..handstore import STORE_DIR, HandStore
        from ..motion.switches import FALLBACK_POSE, HAND_POSES, REQUIRED_HAND_POSES

        eng = self.motion_engine(st, masks, rejected)
        lib = eng.hand_lib
        cands = []
        for s in ("r", "l"):
            for pose, vs in lib.candidates[s].items():
                for v in vs:
                    al = lib.alignment_v(v, eng.profile.midline_x)
                    cands.append({**v.to_json(), "active": v is lib.hands[s][pose],
                                  "alignment": {k: al.get(k) for k in ("status", "method", "attachment_error_px",
                                                                       "width_ratio", "wrist_fit", "side_ok", "issues",
                                                                       "auto_align", "visible_band_vs_reference_px")}})
        store = None
        if (self.pkg / STORE_DIR / "index.json").is_file():
            hs = HandStore(self.pkg)
            store = {"packs": hs.index["packs"], "assets": hs.index["assets"], "verify": hs.verify()}
        deleted = set(st.get("hand_store", {}).get("deleted", []))
        from ..motion.handselect import ACTIONS, rules_json  # noqa: PLC0415

        hsi = eng.hand_store or {}
        return {"poses": list(HAND_POSES), "required": list(REQUIRED_HAND_POSES), "fallback": FALLBACK_POSE,
                "candidates": cands, "missing": lib.missing(), "store": store, "state": st.get("hand_store"),
                "deleted": sorted(deleted), "libraries": hsi.get("libraries", {}),
                "gesture_id_map": hsi.get("gesture_id_map", []), "use_remapped": hsi.get("use_remapped", []),
                "selection": {"state": st.get("hand_selection"), "actions": sorted(ACTIONS),
                              "rules": rules_json(eng.hand_select)}}

    def hand_preview(self, st, masks, rejected, side: str, cid: str, size: int = 220) -> np.ndarray | None:
        """Rest-frame preview of one candidate on the character: the candidate replaces the hand, the sleeve is
        drawn over it (as in every pose), cropped around the wrist, with the wrist joint marked."""
        from ..phase2.skinning import PoseParams

        eng = self.motion_engine(st, masks, rejected)
        v = eng.hand_lib.candidate(side, cid)
        if v is None or v.layer is None:
            return None
        vis = [L["id"] for L in st["layers"] if L["visible"] and L["kind"] == "bitmap" and L["id"] in masks]
        order = list(vis)
        if f"hand_{side}" in order and v.layer != f"hand_{side}":
            order[order.index(f"hand_{side}")] = v.layer
            cover = f"forearm_{side}__hscuff" if v.cid.startswith("store:") and f"forearm_{side}__hscuff" in eng.variants \
                else f"forearm_{side}__trim"
            if cover in eng.variants and f"forearm_{side}" in order:
                order[order.index(f"forearm_{side}")] = cover
        # sleeve over the hand, as motion_order composes it
        for c in (f"forearm_{side}__hscuff", f"forearm_{side}__trim", f"forearm_{side}"):
            if c in order:
                order.remove(c)
                order.append(c)
        r = eng.dfm.render(PoseParams.identity(len(eng.sk.bones)), order, 0)
        img = Image.fromarray(r["image"], "RGBA")
        bg = Image.new("RGBA", img.size, (236, 238, 242, 255))
        bg.alpha_composite(img)
        hb = eng.sk.bone(f"hand_{side}")
        cx, cy = (hb.head + hb.tail) / 2
        half = int(max(70, 1.6 * hb.length))
        crop = bg.crop((int(cx - half), int(cy - half), int(cx + half), int(cy + half))).convert("RGB")
        from PIL import ImageDraw

        d = ImageDraw.Draw(crop)
        wx, wy = hb.head[0] - (cx - half), hb.head[1] - (cy - half)
        d.ellipse((wx - 4, wy - 4, wx + 4, wy + 4), outline=(28, 126, 214), width=2)
        tx, ty = hb.tail[0] - (cx - half), hb.tail[1] - (cy - half)
        d.line((wx, wy, tx, ty), fill=(28, 126, 214), width=1)
        return np.asarray(crop.resize((size, size), Image.LANCZOS))

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
