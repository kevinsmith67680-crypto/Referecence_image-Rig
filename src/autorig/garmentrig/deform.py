"""Structured garment deformation: weight-mesh skinning, rigid garment segments over weighted joint patches,
rigid decorative parts and generated pixels drawn beneath the layer that hid them in the source.

Each layer is drawn in up to three kinds of pieces, in this order:

1. **Generated pixels moved down.** If a layer's hidden (generated) pixels were covered at rest by a layer that
   is drawn *before* it in this pose, they are drawn just before that layer. Example: the trouser extension
   under the shoe is drawn under the shoe, while the trouser cuff's original pixels still go over the shoe.
2. **The weighted joint patch.** The whole layer is deformed by its weight mesh (angle interpolation, as in
   Phase 2, but with semantic, restricted, narrow transitions).
3. **Rigid overlays.** FLEXIBLE garments are drawn as rigid segments with their bone's full transform: upper
   sleeve, forearm sleeve with its cuff, trouser shin with its cuff. On the inner side of a bend the child
   segment overlaps its parent cleanly. On the outer side the patch underneath shows only in the wedge that
   opens. RIGID decorative parts (badge, buttons, tie, pocket flaps) are drawn with the rigid fit of the local
   deformation, so they are never sheared.

The identity pose draws every layer's original pixels unchanged.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
from scipy import ndimage

from ..motion.garments import ArtworkDeformationEngine, GarmentModel, GarmentSkeleton
from ..phase2.skeleton import Skeleton, layer_class
from ..phase2.skinning import Deformer, PoseParams
from .structure import RIGID, GarmentStructure, shoulder_frame
from .weights import WeightSet

FEATHER_PX = 1.5
PIECE_CODES = {"patch": 1, "segment": 2, "fit": 3, "hidden_under": 4, "corrective": 5}
FIT_KINDS = ("badge", "button", "tie", "pocket_flap_{s}")


class StructuredSkeleton(GarmentSkeleton):
    """Garment skeleton whose influence comes from the current layer's weight mesh (set by the deformer)."""

    def __init__(self, base: Skeleton, model: GarmentModel):
        super().__init__(base, model)
        self.cur = None

    def influence(self, b: int, pts: np.ndarray, home: np.ndarray) -> np.ndarray:
        if self.cur is not None:
            return self.cur.influence(b, pts)
        return super().influence(b, pts, home)

    def hem_weight(self, pts: np.ndarray, home: np.ndarray, top: float, bottom: float) -> np.ndarray:
        if self.cur is not None:
            return self.cur.lag_at(pts)
        return super().hem_weight(pts, home, top, bottom)


class StructuredDeformer(ArtworkDeformationEngine):
    def __init__(self, gsk: StructuredSkeleton, layers: dict[str, np.ndarray], generated, reference, variants,
                 wset: WeightSet, structure: GarmentStructure, overlays: bool = True,
                 pivot_report: list[dict[str, Any]] | None = None):
        self.wset, self.structure, self.use_overlays = wset, structure, overlays
        self.pivot_report = pivot_report or []
        lim = wset.cfg.get("max_patch_stretch", 3.0)
        self.max_patch_stretch = lim if isinstance(lim, dict) else {"default": float(lim)}
        H, W = next(iter(layers.values())).shape[:2]
        gen = generated or {}
        # visible-source owner of each pixel (original, non-generated pixels of the base layers)
        self.owner_names: list[str] = []
        self.owner = np.full((H, W), -1, np.int16)
        for cid, a in layers.items():
            if variants and cid in variants:
                continue
            m = (a[..., 3] > 0) & ~gen.get(cid, np.zeros((H, W), bool))
            self.owner[m] = len(self.owner_names)
            self.owner_names.append(cid)
        self.keep_src = True
        super().__init__(gsk, layers, generated, reference=reference, variants=variants)

    # -- per-layer data ---------------------------------------------------------------------------------------
    def _add_info(self, cid: str, rgba: np.ndarray, orig_alpha: np.ndarray, generated) -> None:
        super()._add_info(cid, rgba, orig_alpha, generated)
        inf = self.info.get(cid)
        if inf is None:
            return
        mesh = self.wset.mesh_for(cid, rgba)
        inf["wm"] = mesh
        x0, y0 = inf["origin"]
        h, w = inf["alpha"].shape
        yy, xx = np.mgrid[y0:y0 + h, x0:x0 + w]
        pts = np.stack([xx.ravel(), yy.ravel()], 1).astype(np.float64)
        inf["home"] = mesh.dominant_at(pts).reshape(h, w).astype(np.int16)
        inf["homes"] = np.unique(inf["home"][inf["alpha"]])
        inf["overlays"] = self._overlay_specs(cid, inf, pts) if self.use_overlays else []
        cut = [o["mask"] for o in inf["overlays"] if o.get("cut_from_patch")]
        if cut:  # these pixels are drawn only by their segment: the patch samples a copy without them
            inf["patch_keep"] = np.clip(1.0 - np.maximum.reduce(cut), 0.0, 1.0).astype(np.float32)

    def add_variant_layer(self, cid: str, rgba: np.ndarray) -> None:
        # a replaced drawing gets a fresh part map and weight mesh
        self.wset.meshes.pop(cid, None)
        self.structure.layer_parts.pop(cid, None)
        super().add_variant_layer(cid, rgba)

    def refresh_weights(self) -> None:
        """Re-attach meshes after a weight edit (the WeightSet was rebuilt)."""
        for cid, inf in self.info.items():
            mesh = self.wset.mesh_for(cid, inf["rgba"])
            inf["wm"] = mesh
            x0, y0 = inf["origin"]
            h, w = inf["alpha"].shape
            yy, xx = np.mgrid[y0:y0 + h, x0:x0 + w]
            pts = np.stack([xx.ravel(), yy.ravel()], 1).astype(np.float64)
            inf["home"] = mesh.dominant_at(pts).reshape(h, w).astype(np.int16)

    def _overlay_specs(self, cid: str, inf: dict[str, Any], pts: np.ndarray) -> list[dict[str, Any]]:
        sk, st = self.sk, self.structure
        h, w = inf["alpha"].shape
        ids = st.ids_at(cid, pts).reshape(h, w)
        lp = st.layer_parts.get(cid)
        if lp is not None and (lp.ids >= 0).any():  # transparent margin: nearest labelled pixel
            miss = ids < 0
            if miss.any() and (ids >= 0).any():
                _, (iy, ix) = ndimage.distance_transform_edt(miss, return_indices=True)
                ids = ids[iy, ix]
        name = np.array([p.name for p in st.parts] + [""])[ids]
        out: list[dict[str, Any]] = []
        cls = layer_class(cid)
        base = cid.split("__")[0]
        ramp = lambda s: np.clip(s / FEATHER_PX + 0.5, 0.0, 1.0)
        if cls.startswith("arm") and not base.startswith("hand_"):
            s = cls[-1]
            P, au = shoulder_frame(sk, s, self.pivot_report)
            s_sh = ((pts[:, 0] - P[0]) * au[0] + (pts[:, 1] - P[1]) * au[1]).reshape(h, w)
            s_fo = sk.s(sk.idx[f"forearm_{s}"], pts).reshape(h, w)
            # the rigid upper sleeve starts where the shoulder transition ends: its straight proximal cut at the
            # shoulder plane drew a straight diagonal across the chest (Phase 3.4 baseline D5); the weighted patch
            # is continuous with the segment at the band end, so the join is invisible there
            # (for a lowered arm; a raised arm keeps the Phase 3.3 start at the shoulder plane, where the underarm
            # patch would otherwise have to stretch further: the two masks cross-fade with the arm's posed angle)
            b_sh = self.wset.cfg["bands"]["shoulder"][1] * sk.bone(f"upper_arm_{s}").h
            soft = lambda v: np.clip(v / 6.0 + 0.5, 0.0, 1.0)
            sl = np.isin(name, [f"sleeve_upper_{s}", f"elbow_{s}"]) * ramp(-s_fo)
            # the start follows a curved armhole line: at the shoulder plane on the underarm side (where the patch of a
            # hanging arm would fold over itself) and at the band end on the shoulder-top side (where it only
            # stretches and stays continuous with the segment), an S-curve in between
            n = np.array([au[1], -au[0]])
            if n[1] < 0:
                n = -n  # toward the underarm (down in the T-pose image)
            hw = sk.bone(f"upper_arm_{s}").h
            perp = ((pts[:, 0] - P[0]) * n[0] + (pts[:, 1] - P[1]) * n[1]).reshape(h, w)
            v = np.clip((hw - perp) / hw - float(self.wset.cfg.get("sleeve_start_underarm_frac", 1.0)), 0.0, 1.0)
            start = b_sh * v * v * (3 - 2 * v)
            up = sl * soft(s_sh - start)
            up_plane = sl * ramp(s_sh)
            fo = np.isin(name, [f"elbow_{s}", f"sleeve_fore_{s}", f"shirt_cuff_{s}"]) * ramp(s_fo)
            # hand fragments Phase 1 left in the arm layer are drawn rigidly with the hand (they share mesh cells
            # with the cuff, so the weighted mapping would stretch them at a bent wrist)
            hd = ndimage.gaussian_filter((name == f"hand_{s}").astype(np.float32), 0.7)
            out += [{"kind": "segment", "bone": sk.idx[f"upper_arm_{s}"], "mask": up.astype(np.float32),
                     "part": f"sleeve_upper_{s}", "mask_raised": up_plane.astype(np.float32),
                     "bone_axis": np.asarray(sk.bone(f"upper_arm_{s}").u, float),
                     "raised_range_deg": list(self.wset.cfg.get("sleeve_start_crossfade_deg", [60.0, 100.0]))},
                    {"kind": "segment", "bone": sk.idx[f"forearm_{s}"], "mask": fo.astype(np.float32),
                     "part": f"sleeve_fore_{s}"},
                    {"kind": "segment", "bone": sk.idx[f"hand_{s}"], "mask": np.clip(hd * 1.5, 0, 1),
                     "part": f"hand_{s}"}]
        if cls.startswith("leg") and not base.startswith("foot_"):
            s = cls[-1]
            s_sh = sk.s(sk.idx[f"shin_{s}"], pts).reshape(h, w)
            # the rigid shin segment stops where the hem starts to follow the foot (ankle transition)
            a0 = self.wset.cfg["bands"]["ankle"][0] * sk.bone(f"foot_{s}").h
            s_an = sk.s(sk.idx[f"foot_{s}"], pts).reshape(h, w)
            # Phase 3.4 (D4): the shin segment's straight proximal cut at the knee plane showed as a line across the
            # inner knee when the leg bends; it starts ``segment_start["shin"]`` of the knee band in (soft 6 px ramp),
            # where the weighted patch is continuous with it
            k1 = float(self.wset.cfg.get("segment_start", {}).get("shin", 0.0)) * self.wset.cfg["bands"]["knee"][1] \
                * sk.bone(f"shin_{s}").h
            start = (lambda v: np.clip(v / 6.0 + 0.5, 0.0, 1.0)) if k1 > 0 else ramp
            sh = np.isin(name, [f"knee_{s}", f"trouser_shin_{s}", f"trouser_cuff_{s}"]) * start(s_sh - k1) \
                * ramp(a0 - s_an)
            out.append({"kind": "segment", "bone": sk.idx[f"shin_{s}"], "mask": sh.astype(np.float32),
                        "part": f"trouser_shin_{s}"})
        if cls == "torso" and "head" in sk.idx:
            # Phase 3.4 (D8): jaw/ear fragments Phase 1 left in the body layer share mesh cells with the collar, so
            # the weighted mapping tore them off the jaw at a head tilt or lean; they are drawn rigidly with the head
            hd = ndimage.gaussian_filter(np.isin(name, ["head", "hair"]).astype(np.float32), 0.7)
            out.append({"kind": "segment", "bone": sk.idx["head"], "mask": np.clip(hd * 1.5, 0, 1), "part": "head",
                        "cut_from_patch": True})
        for p in st.parts:
            if p.cls != RIGID or p.kind not in FIT_KINDS:
                continue
            m = name == p.name
            if m.sum() < 12:
                continue
            soft = ndimage.gaussian_filter(ndimage.binary_dilation(m, iterations=1).astype(np.float32), 0.7)
            out.append({"kind": "fit", "bone": None, "mask": np.clip(soft * 1.4, 0, 1).astype(np.float32),
                        "part": p.name, "core": m})
        out = [o for o in out if (o["mask"] * inf["pre"][..., 3] > 0.5).sum() >= 8]
        for o in out:
            ys, xs = np.nonzero((o["mask"] > 0.01) | (o["mask_raised"] > 0.01 if "mask_raised" in o else False))
            o["bbox"] = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        return out

    # -- mapping ----------------------------------------------------------------------------------------------
    def render_layer(self, cid: str, pose: PoseParams, pad: int = 0):
        self.sk.cur = self.info[cid]["wm"]
        try:
            return super().render_layer(cid, pose, pad)
        finally:
            self.sk.cur = None

    def smooth_forward(self, cid: str, pts: np.ndarray, pose: PoseParams) -> np.ndarray:
        self.sk.cur = self.info[cid]["wm"]
        try:
            return Deformer.forward(self, cid, pts, pose)
        finally:
            self.sk.cur = None

    def forward(self, cid: str, pts: np.ndarray, pose: PoseParams) -> np.ndarray:
        """Drawn position of rest points: the rigid overlay transform where a segment overlay covers the point,
        the weighted (patch) mapping elsewhere."""
        q = self.smooth_forward(cid, pts, pose)
        inf = self.info[cid]
        if pose.is_identity():
            return q
        for o in inf.get("overlays", []):
            cov = self._mask_at(inf, self.overlay_mask(o, pose), pts) > 0.5
            if o["kind"] == "fit":
                cov &= self._mask_at(inf, o["core"].astype(np.float32), pts) > 0.5
            if cov.any():
                A = self._overlay_affine(cid, o, pose)
                q[cov] = pts[cov] @ A[:2, :2].T + A[:2, 2]
        return q

    @staticmethod
    def _mask_at(inf, mask, pts) -> np.ndarray:
        x0, y0 = inf["origin"]
        return ndimage.map_coordinates(mask, [pts[:, 1] - y0, pts[:, 0] - x0], order=1, mode="constant", cval=0.0)

    def _overlay_affine(self, cid: str, o: dict[str, Any], pose: PoseParams) -> np.ndarray:
        if o["kind"] == "segment":
            return self.world(o["bone"], pose)
        # rigid fit (rotation + translation) of the weighted mapping over the part's pixels
        inf = self.info[cid]
        x0, y0 = inf["origin"]
        ys, xs = np.nonzero(o["core"])
        step = max(1, len(ys) // 400)
        P = np.stack([xs[::step] + x0, ys[::step] + y0], 1).astype(np.float64)
        Q = self.smooth_forward(cid, P, pose)
        pc, qc = P.mean(0), Q.mean(0)
        M = (Q - qc).T @ (P - pc)
        U, _, Vt = np.linalg.svd(M)
        R = U @ Vt
        if np.linalg.det(R) < 0:
            U[:, -1] *= -1
            R = U @ Vt
        A = np.eye(3)
        A[:2, :2] = R
        A[:2, 2] = qc - R @ pc
        return A

    JOINT_RULES = ("shoulder", "elbow", "knee", "ankle", "hip")

    def _jacobian_stats(self, cid, rest, home, pose, bones) -> dict[str, Any]:
        """Phase 3.1 per-layer statistics on the pixels that are neither drawn by a rigid segment nor inside a
        joint transition band. The joint patch is graded separately by the garment evaluator (FLEXIBLE patch
        stretch); counting it here again, against the old deformer's area limits, would double-count it."""
        inf = self.info.get(cid)
        if inf is None:
            return super()._jacobian_stats(cid, rest, home, pose, bones)
        cover = np.zeros(len(rest), bool)
        for o in inf.get("overlays", []):
            if o["kind"] == "segment":
                cover |= self._mask_at(inf, self.overlay_mask(o, pose), rest) > 0.5
        mesh = inf["wm"]
        gy, gx = mesh.grid_coords(rest)
        gh, gw = mesh.shape
        vy, vx = np.clip(np.rint(gy).astype(int), 0, gh - 1), np.clip(np.rint(gx).astype(int), 0, gw - 1)
        band = np.zeros(len(rest), bool)
        for key, grid in mesh.rule.items():
            if key.rsplit("_", 1)[0] in self.JOINT_RULES:
                t = grid[vy, vx]
                band |= np.isfinite(t) & (t > 0.01) & (t < 0.99)
        keep = ~(cover | band)
        if not keep.any():
            return {"rigid": True, "note": "all pixels drawn by rigid segments or inside joint patches",
                    "joint_patch_fraction": round(float(band.mean()), 4)}
        out = super()._jacobian_stats(cid, rest[keep], home[keep], pose, bones)
        out["joint_patch_fraction"] = round(float(band.mean()), 4)
        out["rigid_segment_fraction"] = round(float(cover.mean()), 4)
        return out

    # -- drawing ----------------------------------------------------------------------------------------------
    def overlay_mask(self, o: dict[str, Any], pose: PoseParams) -> np.ndarray:
        """The overlay's rest-frame mask for this pose. The upper sleeve's start cross-fades with the arm's posed
        angle from straight down (Phase 3.4: band end for a hanging arm, shoulder plane for a raised one)."""
        if "mask_raised" not in o or pose.is_identity():
            return o["mask"]
        A = self.world(o["bone"], pose)
        u = A[:2, :2] @ o["bone_axis"]
        ang = math.degrees(math.acos(float(np.clip(u[1] / max(float(np.hypot(*u)), 1e-9), -1.0, 1.0))))
        lo, hi = o["raised_range_deg"]
        t = float(np.clip((ang - lo) / max(hi - lo, 1e-6), 0.0, 1.0))
        t = t * t * (3 - 2 * t)
        return o["mask"] if t <= 0 else (1 - t) * o["mask"] + t * o["mask_raised"]

    def _render_overlay(self, cid: str, o: dict[str, Any], pose: PoseParams, pad: int):
        inf = self.info[cid]
        x0, y0 = inf["origin"]
        A = self._overlay_affine(cid, o, pose)
        bx0, by0, bx1, by1 = o["bbox"]
        corners = np.array([[bx0, by0], [bx1, by0], [bx0, by1], [bx1, by1]], float) + [x0, y0]
        qc = corners @ A[:2, :2].T + A[:2, 2]
        qx0, qy0 = int(math.floor(qc[:, 0].min())) - 2, int(math.floor(qc[:, 1].min())) - 2
        qx1, qy1 = int(math.ceil(qc[:, 0].max())) + 3, int(math.ceil(qc[:, 1].max())) + 3
        gh, gw = qy1 - qy0, qx1 - qx0
        gy, gx = np.mgrid[qy0:qy1, qx0:qx1]
        Ai = np.linalg.inv(A)
        px = Ai[0, 0] * gx + Ai[0, 1] * gy + Ai[0, 2] - x0
        py = Ai[1, 0] * gx + Ai[1, 1] * gy + Ai[1, 2] - y0
        mk = ndimage.map_coordinates(self.overlay_mask(o, pose), [py, px], order=1, mode="constant", cval=0.0)
        out = np.zeros((gh, gw, 4), np.float32)
        sel = mk > 0.003
        if sel.any():
            for k in range(4):
                out[..., k][sel] = ndimage.map_coordinates(inf["pre"][..., k], [py[sel], px[sel]], order=1,
                                                           mode="constant", cval=0.0) * mk[sel]
        gen = np.zeros((gh, gw), bool)
        if inf["gen"] is not None and sel.any():
            hh, ww = inf["gen"].shape
            gen[sel] = inf["gen"][np.clip(np.rint(py[sel]).astype(int), 0, hh - 1),
                                  np.clip(np.rint(px[sel]).astype(int), 0, ww - 1)]
        src = np.full((gh, gw, 2), np.nan)
        src[sel] = np.stack([px[sel] + x0, py[sel] + y0], 1)
        return out, (qx0 + pad, qy0 + pad), gen, src

    def render(self, pose: PoseParams, order: list[str], pad: int = 0) -> dict[str, Any]:
        H, W = self.shape
        ident = pose.is_identity()
        items: dict[str, list[dict[str, Any]]] = {}
        before: dict[str, list[dict[str, Any]]] = {}
        clip_masks: list = []
        stats: dict[str, Any] = {}
        pos = {c: i for i, c in enumerate(order)}
        base_pos: dict[str, int] = {}
        for i, c in enumerate(order):
            base_pos.setdefault(c.split("__")[0], i)
        for cid in order:
            if cid not in self.info:
                continue
            inf = self.info[cid]
            if ident:
                x0, y0 = inf["origin"]
                lay = inf["pre"]
                org = (x0 + pad, y0 + pad)
                gen = inf["gen"] if inf["gen"] is not None else np.zeros(lay.shape[:2], bool)
                h, w = lay.shape[:2]
                yy, xx = np.mgrid[y0:y0 + h, x0:x0 + w]
                src = np.stack([xx, yy], -1).astype(np.float64)
                stats[cid] = {"rigid": True}
            else:
                self.last_src = None
                keep = inf.get("patch_keep")
                full_pre = inf["pre"]
                if keep is not None:
                    inf["pre"] = full_pre * keep[..., None]
                try:
                    lay, org, gen, st = self.render_layer(cid, pose, pad)
                finally:
                    inf["pre"] = full_pre
                src = self.last_src
                if src is not None and self.max_patch_stretch:
                    lim = self._stretch_limits(cid, src)
                    lay, cmask = clip_stretch(lay, src, lim)
                    st = {**st, "patch_clipped_px": int(cmask.sum())}
                    if cmask.any():
                        clip_masks.append((cid, org, cmask, src))
                stats[cid] = st
            main = {"cid": cid, "img": lay, "origin": org, "gen": gen, "kind": "patch"}
            lst = [self._split_hidden(main, src, order, pos, base_pos, before)]
            if not ident and self.use_overlays:
                for o in sorted(inf.get("overlays", []), key=lambda o: self.sk.depth[o["bone"]] if o["bone"] is not None else 99):
                    oimg, oorg, ogen, osrc = self._render_overlay(cid, o, pose, pad)
                    piece = {"cid": cid, "img": oimg, "origin": oorg, "gen": ogen, "kind": f"{o['kind']}:{o['part']}"}
                    lst.append(self._split_hidden(piece, osrc, order, pos, base_pos, before))
            items[cid] = lst
        canvas = np.zeros((H + 2 * pad, W + 2 * pad, 4), np.float32)
        gen_vis = np.zeros(canvas.shape[:2], bool)
        top = np.full(canvas.shape[:2], -1, np.int16)
        piece_top = np.zeros(canvas.shape[:2], np.int8)
        alphas: dict[str, np.ndarray] = {}
        drawn: list[dict[str, Any]] = []
        # corrective artwork pieces (Phase 3.4), placed relative to the layers
        corr_before: dict[str, list] = {}
        corr_after: dict[str, list] = {}
        corr_group: dict[str, list] = {}
        for c in (getattr(self, "correctives", None) or []):
            pl = c["placement"]
            it = self._render_corrective(c, pad)
            if it is None:
                continue
            key = pl.get("layer") or pl.get("group")
            if pl["mode"] == "after_patches":
                corr_group.setdefault(key, []).append(it)
            elif pl["mode"] == "after_layer":
                corr_after.setdefault(self._resolve(key, order), []).append(it)
            else:
                corr_before.setdefault(self._resolve(key, order), []).append(it)
        protected = np.zeros(canvas.shape[:2], bool)

        def draw(it, idx):
            if it["kind"].startswith("corrective"):
                # a corrective never covers an original pixel: where one is already drawn (not generated; for a
                # joint cap also not its joint patch), it is composited beneath; the prevented overlap is counted
                h, w = it["img"].shape[:2]
                ox, oy = it["origin"]
                cx0, cy0, cx1, cy1 = max(0, ox), max(0, oy), min(canvas.shape[1], ox + w), min(canvas.shape[0], oy + h)
                if cx1 <= cx0 or cy1 <= cy0:
                    return
                C = it["img"][cy0 - oy:cy1 - oy, cx0 - ox:cx1 - ox]
                dst = canvas[cy0:cy1, cx0:cx1]
                under = (dst[..., 3] > 0.02) & ~gen_vis[cy0:cy1, cx0:cx1]
                if it.get("mode") == "after_patches":
                    under &= piece_top[cy0:cy1, cx0:cx1] != PIECE_CODES["patch"]
                protected[cy0:cy1, cx0:cx1] |= under & (C[..., 3] > 0.5) & (dst[..., 3] > 0.5)
                dst[:] = np.where(under[..., None], dst + C * (1.0 - dst[..., 3:4]), C + dst * (1.0 - C[..., 3:4]))
                on = ~under & (C[..., 3] > 0.5)
                piece_top[cy0:cy1, cx0:cx1][on] = PIECE_CODES["corrective"]
                top[cy0:cy1, cx0:cx1][on] = -2
                gen_vis[cy0:cy1, cx0:cx1] |= on
                a_ = alphas.setdefault(it["cid"], np.zeros(canvas.shape[:2], bool))
                a_[cy0:cy1, cx0:cx1] |= on
                drawn.append({"layer": it["cid"], "piece": it["kind"]})
                return
            self._draw(canvas, gen_vis, top, alphas, it, idx, piece_top)
            drawn.append({"layer": it["cid"], "piece": it["kind"]})

        def group_of(cid):
            cls = layer_class(cid)
            return cls if cls.startswith(("arm_", "leg_")) else None

        def cap(it, gidx):
            """A joint cap goes over empty pixels and over this limb's own joint-patch pixels, and beneath every
            other piece (segments, fits, whole layers, other limbs): the draw order is left unchanged."""
            h, w = it["img"].shape[:2]
            ox, oy = it["origin"]
            cx0, cy0, cx1, cy1 = max(0, ox), max(0, oy), min(canvas.shape[1], ox + w), min(canvas.shape[0], oy + h)
            if cx1 <= cx0 or cy1 <= cy0:
                return
            C = it["img"][cy0 - oy:cy1 - oy, cx0 - ox:cx1 - ox]
            dst = canvas[cy0:cy1, cx0:cx1]
            pt, tp = piece_top[cy0:cy1, cx0:cx1], top[cy0:cy1, cx0:cx1]
            # over empty pixels (not semi-transparent original edges) and this limb's joint patch; beneath the rest
            over = ((pt == 0) & (dst[..., 3] < 0.02)) | ((pt == PIECE_CODES["patch"]) & np.isin(tp, gidx))
            if it["params"].get("render_style") == "diffused_fill_bg_outline":
                from .correctives import style_cap  # noqa: PLC0415

                C = style_cap(dst, C[..., 3], over, it["params"])
            dst[:] = np.where(over[..., None], C + dst * (1.0 - C[..., 3:4]), dst + C * (1.0 - dst[..., 3:4]))
            on = over & (C[..., 3] > 0.5)
            pt[on] = PIECE_CODES["corrective"]
            tp[on] = -2
            gen_vis[cy0:cy1, cx0:cx1] |= on
            a_ = alphas.setdefault(it["cid"], np.zeros(canvas.shape[:2], bool))
            a_[cy0:cy1, cx0:cx1] |= on
            drawn.append({"layer": it["cid"], "piece": it["kind"]})

        i = 0
        while i < len(order):
            cid = order[i]
            g = group_of(cid)
            j = i + 1
            if g and g in corr_group:  # the whole limb group, then its joint caps
                while j < len(order) and group_of(order[j]) == g:
                    j += 1
            for c in order[i:j]:
                for it in corr_before.get(c, []) + before.get(c, []) + items.get(c, []) + corr_after.get(c, []):
                    draw(it, pos[it["cid"]] if it["cid"] in pos else pos[c])
            if g and g in corr_group:
                for it in corr_group[g]:
                    cap(it, list(range(i, j)))
            i = j
        # clipped patch pixels that nothing else covers: the visible conflicts, per garment part
        clipped_map = np.full(canvas.shape[:2], -1, np.int16)  # part id of each uncovered clipped pixel
        for cid, (ox, oy), cm, src in clip_masks:
            ys, xs = np.nonzero(cm)
            cy, cx = ys + oy, xs + ox
            ok = (cy >= 0) & (cy < canvas.shape[0]) & (cx >= 0) & (cx < canvas.shape[1])
            ys, xs, cy, cx = ys[ok], xs[ok], cy[ok], cx[ok]
            open_ = canvas[cy, cx, 3] < 0.5
            if not open_.any():
                continue
            sp = src[ys[open_], xs[open_]]
            good = ~np.isnan(sp[:, 0])
            clipped_map[cy[open_][good], cx[open_][good]] = self.structure.ids_at(cid, sp[good])
        # gap fills (Phase 3.4 armpit gussets): composited beneath everything drawn, inside the open gap only
        corrective_masks: dict[str, np.ndarray] = {}
        fill_info: dict[str, Any] = {}
        for f in (getattr(self, "gap_fills", None) or []):
            from .correctives import armpit_fill  # noqa: PLC0415

            ids = [self.structure.index[n] for n in f["parts"] if n in self.structure.index]
            gap = np.isin(clipped_map, ids)
            ax, ay = f["anchor"][0] + pad, f["anchor"][1] + pad
            yy, xx = np.ogrid[:canvas.shape[0], :canvas.shape[1]]
            gap &= (xx - ax) ** 2 + (yy - ay) ** 2 <= f["radius"] ** 2
            res_ = armpit_fill(canvas, gap, (ax, ay), f["params"], f["weight"]) if gap.any() else None
            if res_ is None:
                continue
            F, fm, inf = res_
            canvas[:] = canvas + F * (1.0 - canvas[..., 3:4])
            newpx = fm & (piece_top == 0)
            piece_top[newpx] = PIECE_CODES["corrective"]
            top[newpx] = -2
            gen_vis |= newpx
            corrective_masks[f["asset_id"]] = fm
            fill_info[f["asset_id"]] = inf
            drawn.append({"layer": f"corrective:{f['asset_id']}", "piece": f"corrective:{f['asset_id']}"})
        clipped_uncovered: dict[str, int] = {}
        still = (clipped_map >= 0) & (canvas[..., 3] < 0.5)
        clipped_map[~still] = -1
        for pid, n in zip(*np.unique(clipped_map[still], return_counts=True)):
            nm = self.structure.parts[pid].name
            clipped_uncovered[nm] = clipped_uncovered.get(nm, 0) + int(n)
        out = canvas.copy()
        a = out[..., 3:4]
        out[..., :3] = np.where(a > 1e-6, out[..., :3] / np.maximum(a, 1e-6), 0.0)
        img = np.clip(np.rint(out * 255.0), 0, 255).astype(np.uint8)
        return {"image": img, "alphas": alphas, "stats": stats, "generated_visible": gen_vis, "pad": pad,
                "clipped_uncovered": clipped_uncovered, "clipped_map": clipped_map,
                "corrective_masks": corrective_masks, "gap_fill_info": fill_info,
                # original pixels a corrective would have covered (it was composited beneath them instead)
                "protected_overlap_prevented_px": int(protected.sum()),
                "corrective_px": int((piece_top == PIECE_CODES["corrective"]).sum()),
                "top": top, "order": list(order), "pieces": drawn, "piece_top": piece_top,
                "piece_codes": PIECE_CODES}

    def _stretch_limits(self, cid: str, src: np.ndarray) -> np.ndarray:
        """Per-pixel stretch limit: the joint whose transition band the pixel's rest position lies in decides
        (``max_patch_stretch``: {"default": ..., "shoulder": ..., ...})."""
        lims = self.max_patch_stretch
        out = np.full(src.shape[:2], float(lims.get("default", 3.0)))
        mesh = self.info[cid]["wm"]
        ok = ~np.isnan(src[..., 0])
        if not ok.any():
            return out
        gy, gx = mesh.grid_coords(src[ok])
        gh, gw = mesh.shape
        vy, vx = np.clip(np.rint(gy).astype(int), 0, gh - 1), np.clip(np.rint(gx).astype(int), 0, gw - 1)
        vals = out[ok]
        P = src[ok]
        for key, grid in mesh.rule.items():
            joint = key.rsplit("_", 1)[0] if key[-2:] in ("_r", "_l") else key
            if joint not in lims:
                continue
            t = grid[vy, vx]
            inband = np.isfinite(t) & (t > 0.01) & (t < 0.99)
            if joint == "shoulder":  # the strict limit is for the armpit (underarm) side only, not the shoulder top
                piv, au = shoulder_frame(self.sk, key[-1], self.pivot_report)
                n = np.array([au[1], -au[0]])
                if n[1] < 0:
                    n = -n  # toward the underarm (down in the T-pose image)
                inband &= ((P[:, 0] - piv[0]) * n[0] + (P[:, 1] - piv[1]) * n[1]) > 0.35 * self.sk.bone(
                    f"upper_arm_{key[-1]}").h
            vals = np.where(inband, np.minimum(vals, float(lims[joint])), vals)
        out[ok] = vals
        return out

    @staticmethod
    def _resolve(layer: str, order: list[str]) -> str:
        """The drawn layer id for a base layer id (switch variants replace their base layer)."""
        if layer in order:
            return layer
        return next((c for c in order if c.split("__")[0] == layer), layer)

    def _render_corrective(self, c: dict[str, Any], pad: int) -> dict[str, Any] | None:
        """Warp one corrective asset (premultiplied RGBA in its bind frame) by its pose transform, scaled by its
        activation weight."""
        rgba, (bx, by), A, wgt = c["pre"], c["origin"], c["A"], float(c["weight"])
        if wgt <= 1e-4:
            return None
        h, w = rgba.shape[:2]
        corners = np.array([[bx, by], [bx + w, by], [bx, by + h], [bx + w, by + h]], float)
        qc = corners @ A[:2, :2].T + A[:2, 2]
        qx0, qy0 = int(math.floor(qc[:, 0].min())) - 2, int(math.floor(qc[:, 1].min())) - 2
        qx1, qy1 = int(math.ceil(qc[:, 0].max())) + 3, int(math.ceil(qc[:, 1].max())) + 3
        gy, gx = np.mgrid[qy0:qy1, qx0:qx1]
        Ai = np.linalg.inv(A)
        px = Ai[0, 0] * gx + Ai[0, 1] * gy + Ai[0, 2] - bx
        py = Ai[1, 0] * gx + Ai[1, 1] * gy + Ai[1, 2] - by
        out = np.zeros((qy1 - qy0, qx1 - qx0, 4), np.float32)
        a = ndimage.map_coordinates(rgba[..., 3], [py, px], order=1, mode="constant", cval=0.0)
        sel = a > 0.002
        if not sel.any():
            return None
        for k in range(4):
            out[..., k][sel] = ndimage.map_coordinates(rgba[..., k], [py[sel], px[sel]], order=1, mode="constant",
                                                       cval=0.0) * wgt
        return {"cid": f"corrective:{c['asset_id']}", "img": out, "mode": c["placement"].get("mode"),
                "params": c.get("params") or {}, "origin": (qx0 + pad, qy0 + pad), "gen": out[..., 3] > 0.5,
                "kind": f"corrective:{c['asset_id']}"}

    def _split_hidden(self, piece, src, order, pos, base_pos, before) -> dict[str, Any]:
        """Generated pixels that a layer drawn earlier covered at rest are drawn just beneath that layer."""
        gen, cid = piece["gen"], piece["cid"]
        if src is None or not gen.any():
            return piece
        own = self._owner_at(src)
        for oi in np.unique(own[gen]):
            if oi < 0:
                continue
            occ = self.owner_names[oi].split("__")[0]
            j = base_pos.get(occ)
            if j is None or j >= pos[cid] or occ == cid.split("__")[0]:
                continue
            sel = gen & (own == oi)
            if not sel.any():
                continue
            before.setdefault(order[j], []).append({"cid": cid, "img": piece["img"] * sel[..., None],
                                                    "origin": piece["origin"], "gen": sel,
                                                    "kind": f"hidden_under:{order[j]}"})
            keep = ~sel
            piece = {**piece, "img": piece["img"] * keep[..., None], "gen": piece["gen"] & keep}
        return piece

    @staticmethod
    def _draw(canvas, gen_vis, top, alphas, it, idx, piece_top=None) -> None:
        lay, (ox, oy), gen = it["img"], it["origin"], it["gen"]
        h, w = lay.shape[:2]
        cx0, cy0 = max(0, ox), max(0, oy)
        cx1, cy1 = min(canvas.shape[1], ox + w), min(canvas.shape[0], oy + h)
        if cx1 <= cx0 or cy1 <= cy0:
            return
        sub = lay[cy0 - oy:cy1 - oy, cx0 - ox:cx1 - ox]
        dstv = canvas[cy0:cy1, cx0:cx1]
        dstv[:] = sub + dstv * (1.0 - sub[..., 3:4])
        on = sub[..., 3] > 0.5
        a = alphas.setdefault(it["cid"], np.zeros(canvas.shape[:2], bool))
        a[cy0:cy1, cx0:cx1] |= on
        top[cy0:cy1, cx0:cx1][on] = idx
        if piece_top is not None:
            piece_top[cy0:cy1, cx0:cx1][on] = PIECE_CODES.get(it["kind"].split(":")[0], 0)
        g = gen[cy0 - oy:cy1 - oy, cx0 - ox:cx1 - ox] & on
        region = gen_vis[cy0:cy1, cx0:cx1]
        region[:] = g | (region & ~on)

    def _owner_at(self, src: np.ndarray) -> np.ndarray:
        H, W = self.shape
        ok = ~np.isnan(src[..., 0])
        out = np.full(src.shape[:2], -1, np.int16)
        xi = np.clip(np.rint(np.where(ok, src[..., 0], 0)).astype(int), 0, W - 1)
        yi = np.clip(np.rint(np.where(ok, src[..., 1], 0)).astype(int), 0, H - 1)
        out[ok] = self.owner[yi[ok], xi[ok]]
        return out

    def tears(self, pose: PoseParams, render: dict[str, Any], dark: np.ndarray | None = None, step: int = 2
              ) -> dict[str, Any]:
        """Phase 2 tear detection on the drawn mapping. A torn pair whose midpoint is covered by a joint patch is
        bridged by continuous (stretched) fabric, not a visible cut: it moves to ``bridged``. The evaluator
        measures the patch's stretch separately."""
        out = super().tears(pose, render, dark, step)
        pt = render.get("piece_top")
        if pt is None:
            return out
        H, W = pt.shape
        keep, bridged = [], []
        for x, y in out["visible_seam"]:
            if 0 <= y < H and 0 <= x < W and pt[y, x] == PIECE_CODES["patch"]:
                bridged.append((x, y))
            else:
                keep.append((x, y))
        out["visible_seam"] = keep
        out["bridged"] = bridged
        if out.get("outline_break"):
            ob = set(map(tuple, bridged))
            out["outline_break"] = [p for p in out["outline_break"] if tuple(p) not in ob]
        return out

    # -- diagnostics ------------------------------------------------------------------------------------------
    def weight_overlay(self, pose: PoseParams, corr: dict[str, Any], pad: int, render: dict[str, Any]) -> np.ndarray:
        """Blend weights at their posed positions: pixels moved by a fractional joint influence (0 = rigid)."""
        H, W = render["image"].shape[:2]
        out = np.zeros((H, W, 4), np.uint8)
        bones = self.active_bones(pose)
        self.terms = corr.get("terms", [])
        try:
            for cid in render["order"]:
                if cid not in self.info:
                    continue
                inf = self.info[cid]
                x0, y0 = inf["origin"]
                ay, ax = np.nonzero(inf["alpha0"])
                if not len(ay):
                    continue
                ay, ax = ay[::2], ax[::2]
                pts = np.stack([ax + x0, ay + y0], 1).astype(np.float64)
                mesh = inf["wm"]
                frac = np.zeros(len(pts))
                for b in bones:
                    f = mesh.influence(b, pts)
                    frac = np.maximum(frac, np.minimum(f, 1 - f) * 2)
                for o in inf.get("overlays", []):
                    if o["kind"] == "segment":
                        frac[self._mask_at(inf, self.overlay_mask(o, pose), pts) > 0.5] = 0.0
                q = self.forward(cid, pts, pose)
                qx, qy = np.rint(q[:, 0] + pad).astype(int), np.rint(q[:, 1] + pad).astype(int)
                ok = (qx >= 0) & (qx < W) & (qy >= 0) & (qy < H) & (frac > 0.02)
                v = np.clip(frac[ok], 0, 1)
                out[qy[ok], qx[ok]] = np.stack([255 * v, 80 + 120 * (1 - v), 255 * (1 - v), np.full_like(v, 210)], 1)
        finally:
            self.terms = []
        return ndimage.grey_dilation(out, size=(3, 3, 1))


def local_stretch(src: np.ndarray) -> np.ndarray:
    """Largest forward stretch factor at each output pixel, from the rest positions ``src`` (h, w, 2) of the drawn
    pixels (NaN where nothing is drawn): 1 / smallest singular value of the inverse map's Jacobian."""
    sx, sy = src[..., 0], src[..., 1]
    def d(a, axis):
        f = np.full(a.shape, np.nan)
        if axis == 1:
            f[:, 1:-1] = (a[:, 2:] - a[:, :-2]) / 2
        else:
            f[1:-1] = (a[2:] - a[:-2]) / 2
        return f
    a, b, c, e = d(sx, 1), d(sx, 0), d(sy, 1), d(sy, 0)
    # singular values of [[a, b], [c, e]]
    s1 = a * a + b * b + c * c + e * e
    det = np.abs(a * e - b * c)
    disc = np.sqrt(np.maximum(s1 * s1 - 4 * det * det, 0.0))
    smin = np.sqrt(np.maximum((s1 - disc) / 2, 0.0))
    out = np.where(np.isfinite(smin), 1.0 / np.maximum(smin, 1e-6), np.nan)
    return out


def clip_stretch(lay: np.ndarray, src: np.ndarray, limit) -> tuple[np.ndarray, np.ndarray]:
    """Patch pixels stretched beyond ``limit`` (scalar or per-pixel array) are not drawn (short ramp, 5 %): the gap
    they leave is reported as a conflict (missing corrective artwork) instead of being filled with smeared fabric."""
    valid = ~np.isnan(src[..., 0])
    if not valid.any():
        return lay, np.zeros(lay.shape[:2], bool)
    # where the inverse map only converged on some pixels (extreme stretch), the drawn pixels form stripes:
    # measure the stretch on the nearest-valid fill and treat striped neighbourhoods as over the limit
    _, (iy, ix) = ndimage.distance_transform_edt(~valid, return_indices=True)
    filled = src[iy, ix]
    holes = ndimage.binary_closing(valid, iterations=2) & ~valid
    st = local_stretch(filled)
    st = ndimage.median_filter(np.nan_to_num(st, nan=1.0), size=5)
    # stripes only occur under strong stretch: concave outlines (between fingers) are not stripes
    striped = (ndimage.uniform_filter(holes.astype(np.float32), 5) > 0.15) & (st > 1.4)
    k = np.clip((st - limit) / (0.05 * limit), 0.0, 1.0)
    k = np.maximum(k, striped.astype(np.float32))
    k = ndimage.binary_closing(k > 0.5, iterations=2) * 1.0 if (k > 0.5).any() else k
    cmask = (k > 0.5) & (lay[..., 3] > 0.5)
    if cmask.any():
        lay = lay * (1.0 - k)[..., None]
    return lay.astype(np.float32), cmask
