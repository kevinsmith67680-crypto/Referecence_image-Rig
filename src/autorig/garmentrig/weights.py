"""Per-layer garment weight meshes: normalised, restricted, lockable bone weights that users can edit.

Each layer has a regular grid mesh (``mesh_spacing_px``; 6 px by default) over its bounding box. Every vertex has
one weight per bone in the layer's allowed set. The weights satisfy three rules: each weight is >= 0, the weights
sum to 1, and a bone outside the vertex's garment-part restriction has weight 0. Pixels between vertices
interpolate the weights bilinearly. A convex combination of normalised vectors is normalised, so every pixel's
weights sum to 1 too.

A bone's influence on a pixel is the weight of its whole subtree: ``f_b = sum of w_d over d in {b} + desc(b)``.
The deformer rotates the pixel by ``f_b * theta_b`` about each bone's pivot (angle interpolation, as in Phase 2).
For weights that are 0/1 within a segment this is exactly that segment's rigid bone transform.

The automatic weights are not distance-based. Each vertex takes its garment part (``structure``); the part picks
the bone chain. Weights then blend along that chain across the joint planes, with narrow per-class transitions
(``config["bands"]``). The blazer shoulders take a clavicle share (shrug) that fades below the shoulder line; lapels
take a third of it. Rigid decorative parts get one constant weight vector (the body weights at the part's centre)
and are locked. Hands, shoes and the head are one-hot and locked.

User edits are operations (paint, erase, smooth, normalise, lock, reset). They are replayed in order on the
automatic weights, so the result is deterministic and stored in the editor project; the source image is never
touched. ``explain`` reports why a point follows the bones it does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import ndimage

from ..motion.garments import GarmentSkeleton
from ..phase2.skeleton import SIDES, Skeleton, layer_class, smoothstep
from .structure import LIMITED, RIGID, GarmentStructure, shoulder_frame

EPS = 1e-6


@dataclass
class WeightMesh:
    cid: str
    origin: tuple[float, float]  # source position of vertex (0, 0)
    spacing: float
    bones: list[str]  # K bone names (columns of w)
    w: np.ndarray  # (K, gh, gw) float32, normalised
    locked: np.ndarray  # (gh, gw) bool
    part: np.ndarray  # (gh, gw) int16 garment part of each vertex
    active: np.ndarray  # (gh, gw) bool: vertex near the layer's pixels
    lag: np.ndarray  # (gh, gw) float32: share of the blazer-hem secondary motion
    rule: dict[str, np.ndarray] = field(default_factory=dict)  # transition values per joint (for explain)
    touched: np.ndarray | None = None  # (gh, gw) int: number of user ops that changed the vertex
    cum: dict[int, np.ndarray] = field(default_factory=dict)  # bone index -> (gh, gw) subtree influence
    dom: np.ndarray | None = None  # (gh, gw) skeleton index of the dominant bone
    _stencil_cache: Any = field(default=None, repr=False, compare=False)  # (points, bilinear stencil)

    @property
    def shape(self) -> tuple[int, int]:
        return self.w.shape[1:]

    def grid_coords(self, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return (pts[:, 1] - self.origin[1]) / self.spacing, (pts[:, 0] - self.origin[0]) / self.spacing

    def vertex_xy(self) -> np.ndarray:
        gh, gw = self.shape
        gy, gx = np.mgrid[:gh, :gw]
        return np.stack([self.origin[0] + gx * self.spacing, self.origin[1] + gy * self.spacing], -1)

    def finalize(self, sk: Skeleton) -> None:
        """Cumulative (subtree) influence grids and the dominant bone per vertex."""
        K = len(self.bones)
        bi = np.array([sk.idx[b] for b in self.bones], int)
        self.cum = {}
        for b in range(len(sk.bones)):
            sel = [k for k in range(K) if bi[k] == b or sk.desc[b, bi[k]]]
            if sel:
                self.cum[b] = self.w[sel].sum(0).astype(np.float32)
        self.dom = bi[np.argmax(self.w, 0)].astype(np.int16)

    def influence(self, b: int, pts: np.ndarray) -> np.ndarray:
        g = self.cum.get(int(b))
        if g is None:
            return np.zeros(len(pts), np.float32)
        # Phase 3.4 performance: one render asks for every bone's influence on the same points; the bilinear
        # stencil is computed once per points array (same arithmetic as ``_bilinear``: results are identical)
        st = self._stencil_cache
        if st is None or st[0] is not pts:
            gy, gx = self.grid_coords(pts)
            gh, gw = g.shape
            gy = np.clip(gy, 0, gh - 1.000001)
            gx = np.clip(gx, 0, gw - 1.000001)
            y0, x0 = gy.astype(np.int64), gx.astype(np.int64)
            fy, fx = (gy - y0).astype(np.float32), (gx - x0).astype(np.float32)
            y1, x1 = np.minimum(y0 + 1, gh - 1), np.minimum(x0 + 1, gw - 1)
            st = (pts, (y0, x0, y1, x1, fy, fx, 1 - fy, 1 - fx))
            self._stencil_cache = st
        y0, x0, y1, x1, fy, fx, ify, ifx = st[1]
        return (g[y0, x0] * ifx + g[y0, x1] * fx) * ify + (g[y1, x0] * ifx + g[y1, x1] * fx) * fy

    def lag_at(self, pts: np.ndarray) -> np.ndarray:
        gy, gx = self.grid_coords(pts)
        return _bilinear(self.lag, gy, gx)

    def weights_at(self, pts: np.ndarray) -> np.ndarray:
        gy, gx = self.grid_coords(pts)
        return np.stack([_bilinear(self.w[k], gy, gx) for k in range(len(self.bones))], 0)

    def dominant_at(self, pts: np.ndarray) -> np.ndarray:
        gy, gx = self.grid_coords(pts)
        gh, gw = self.shape
        return self.dom[np.clip(np.rint(gy).astype(int), 0, gh - 1), np.clip(np.rint(gx).astype(int), 0, gw - 1)]


def _bilinear(g: np.ndarray, gy: np.ndarray, gx: np.ndarray) -> np.ndarray:
    gh, gw = g.shape
    gy = np.clip(gy, 0, gh - 1.000001)
    gx = np.clip(gx, 0, gw - 1.000001)
    y0, x0 = gy.astype(np.int64), gx.astype(np.int64)
    fy, fx = (gy - y0).astype(np.float32), (gx - x0).astype(np.float32)
    y1, x1 = np.minimum(y0 + 1, gh - 1), np.minimum(x0 + 1, gw - 1)
    return ((g[y0, x0] * (1 - fx) + g[y0, x1] * fx) * (1 - fy) + (g[y1, x0] * (1 - fx) + g[y1, x1] * fx) * fy)


class WeightSet:
    """All layers' weight meshes for one character, built from the garment structure plus the user's ops."""

    def __init__(self, sk: GarmentSkeleton, structure: GarmentStructure, analyzer, config: dict[str, Any],
                 pivot_report: list[dict[str, Any]] | None = None):
        self.sk, self.structure, self.analyzer, self.cfg = sk, structure, analyzer, config
        self.pivot_report = pivot_report or []
        self.meshes: dict[str, WeightMesh] = {}
        self.ops: list[dict[str, Any]] = []
        self.op_errors: list[dict[str, Any]] = []
        self._family_field: dict[str, Any] | None = None

    # -- construction -------------------------------------------------------------------------------------
    def mesh_for(self, cid: str, rgba: np.ndarray) -> WeightMesh:
        if cid not in self.meshes:
            if cid not in self.structure.layer_parts:
                self.analyzer.label_variant(self.structure, cid, rgba)
            self.meshes[cid] = self._auto_mesh(cid, rgba[..., 3] > 0)
            for op in self.ops:
                if op.get("layer") in (cid, None, "*"):
                    self._apply_op(op, only=cid)
            self.meshes[cid].finalize(self.sk)
        return self.meshes[cid]

    def _auto_mesh(self, cid: str, alpha: np.ndarray) -> WeightMesh:
        st, sk = self.structure, self.sk
        g = float(self.cfg.get("mesh_spacing_px", 6))
        ys, xs = np.nonzero(alpha)
        if not len(ys):
            ys, xs = np.array([0]), np.array([0])
        pad = 3 * g
        ox, oy = math.floor(xs.min() - pad), math.floor(ys.min() - pad)
        gw = int(math.ceil((xs.max() + pad - ox) / g)) + 1
        gh = int(math.ceil((ys.max() + pad - oy) / g)) + 1
        mesh_xy = np.stack(np.meshgrid(ox + np.arange(gw) * g, oy + np.arange(gh) * g), -1).reshape(-1, 2)
        pts = mesh_xy.astype(np.float64)
        # part of each vertex: the nearest labelled pixel of the layer
        lp = st.layer_parts[cid]
        lab = lp.ids
        if (lab >= 0).any():
            _, (iy, ix) = ndimage.distance_transform_edt(lab < 0, return_indices=True)
            filled = lab[iy, ix]
        else:
            filled = np.zeros_like(lab)
        x0, y0 = lp.origin
        hh, ww = filled.shape
        xi = np.clip(np.rint(pts[:, 0] - x0).astype(int), 0, ww - 1)
        yi = np.clip(np.rint(pts[:, 1] - y0).astype(int), 0, hh - 1)
        part = filled[yi, xi].astype(np.int16)
        dil = ndimage.binary_dilation(alpha, iterations=int(2 * g))
        H, W = alpha.shape
        cx, cy = np.clip(np.rint(pts[:, 0]).astype(int), 0, W - 1), np.clip(np.rint(pts[:, 1]).astype(int), 0, H - 1)
        inside = (pts[:, 0] >= 0) & (pts[:, 0] < W) & (pts[:, 1] >= 0) & (pts[:, 1] < H)
        active = inside & dil[cy, cx]
        full, rule = self.auto_weights(cid, pts, part)
        full = self._smooth_family(full, part, gh, gw)
        if self.cfg.get("family_smoothing", "canvas") == "canvas":
            # Phase 3.8.1: the shoulder parts that a Phase 1 layer cut runs through take the canvas field, so the
            # torso and upper-arm layers agree on them; every other family part keeps the Phase 3.3 per-layer result
            full = self._smooth_family_canvas(cid, full, part, pts, rule)
        # restrict to the layer's bones (union of its parts' allowed bones)
        used = sorted({int(b) for b in np.nonzero(full.sum(1) > EPS)[0]}, key=lambda b: sk.topo.index(b))
        names = [sk.bones[b].name for b in used]
        w = full[used].reshape(len(used), gh, gw).astype(np.float32)
        locked = np.array([st.parts[p].cls == RIGID if p >= 0 else False for p in part]).reshape(gh, gw)
        lag = self._lag(cid, pts, part).reshape(gh, gw).astype(np.float32)
        m = WeightMesh(cid, (float(ox), float(oy)), g, names, w, locked, part.reshape(gh, gw), active.reshape(gh, gw),
                       lag, {k: v.reshape(gh, gw) for k, v in rule.items()}, np.zeros((gh, gw), np.int32))
        normalise(m)
        return m

    SMOOTH_KINDS = ("blazer_body", "blazer_shoulder_{s}", "sleeve_cap_{s}", "lapels", "shirt_collar", "sweater")

    def _smooth_family(self, full: np.ndarray, part: np.ndarray, gh: int, gw: int) -> np.ndarray:
        """Gradual transitions between neighbouring LIMITED torso parts (blazer body, shoulders, sleeve cap,
        lapels, collar, sweater). Their weight rules differ (e.g. the lapels take a third of the shrug), and a
        step between them would shear the fabric along the part boundary. A normalised Gaussian over the family's
        vertices (``limited_smoothing_vertices``) removes the step; arm, leg and rigid parts are untouched."""
        sig = float(self.cfg.get("limited_smoothing_vertices", 2.5))
        if sig <= 0:
            return full
        st = self.structure
        fam_ids = [i for i, p in enumerate(st.parts) if p.kind in self.SMOOTH_KINDS]
        F = np.isin(part, fam_ids).reshape(gh, gw)
        if F.sum() < 4:
            return full
        den = ndimage.gaussian_filter(F.astype(np.float64), sig)
        out = full.copy()
        for b in range(full.shape[0]):
            row = full[b].reshape(gh, gw)
            if not (row[F] > 0).any():
                continue
            num = ndimage.gaussian_filter(np.where(F, row, 0.0), sig)
            sm = np.where(F, num / np.maximum(den, 1e-9), row)
            out[b] = sm.ravel()
        # back inside each part's restriction, normalised
        nb = full.shape[0]
        for pi in fam_ids:
            m = part == pi
            if not m.any():
                continue
            allowed = np.zeros(nb, bool)
            allowed[[self.sk.idx[b] for b in st.parts[pi].bones if b in self.sk.idx]] = True
            out[~allowed[:, None] & m[None, :]] = 0.0
        out = np.clip(out, 0.0, None)
        return out / np.maximum(out.sum(0), EPS)

    # -- Phase 3.8.1: one smoothed family field for the whole canvas ------------------------------------------
    def family_field(self) -> dict[str, Any]:
        """The LIMITED torso family's body weights (blazer body, shoulders, sleeve caps, lapels, collar, sweater),
        smoothed once on a canvas grid built from every layer's part labels.

        The Phase 3.3 smoothing ran on each layer's own grid, so the same garment part got different weights on
        either side of a Phase 1 layer cut: at the tough student's left shoulder the torso layer had 0.51 clavicle
        next to 0.99 in the upper-arm layer, and the cut opened when the clavicle moved (a dark bar of the torso's
        shoulder outline above the lowered sleeve). Garment parts are semantic, so their weights must not depend
        on which layer holds a pixel. Body weights use the torso-layer rule (no arm share); arm layers add their
        shoulder-plane blend on top in ``_smooth_family_canvas``."""
        if self._family_field is not None:
            return self._family_field
        st = self.structure
        H, W = st.shape
        g = float(self.cfg.get("mesh_spacing_px", 6))
        lab = np.full((H, W), -1, np.int16)
        for cid in sorted(st.layer_parts, key=lambda c: ("__" in c, c)):  # originals first, then extensions/variants
            m = st.full_map(cid)
            put = (lab < 0) & (m >= 0)
            lab[put] = m[put]
        fam_ids = [i for i, p in enumerate(st.parts) if p.kind in self.SMOOTH_KINDS]
        gw, gh = int(math.ceil(W / g)) + 1, int(math.ceil(H / g)) + 1
        pts = np.stack(np.meshgrid(np.arange(gw) * g, np.arange(gh) * g), -1).reshape(-1, 2).astype(np.float64)
        if (lab >= 0).any():
            _, (iy, ix) = ndimage.distance_transform_edt(lab < 0, return_indices=True)
            filled = lab[iy, ix]
        else:
            filled = np.zeros_like(lab)
        xi = np.clip(np.rint(pts[:, 0]).astype(int), 0, W - 1)
        yi = np.clip(np.rint(pts[:, 1]).astype(int), 0, H - 1)
        part = filled[yi, xi].astype(np.int16)
        near = ndimage.binary_dilation(lab >= 0, iterations=int(3 * g))[yi, xi]
        body, _ = self.auto_weights("torso", pts, part)
        F = (np.isin(part, fam_ids) & near).reshape(gh, gw)
        sig = float(self.cfg.get("limited_smoothing_vertices", 2.5))
        den = ndimage.gaussian_filter(F.astype(np.float64), sig) if sig > 0 else F.astype(np.float64)
        field = np.zeros((body.shape[0], gh, gw))
        for b in range(body.shape[0]):
            row = body[b].reshape(gh, gw)
            if not (row[F] > 0).any():
                continue
            num = ndimage.gaussian_filter(np.where(F, row, 0.0), sig) if sig > 0 else np.where(F, row, 0.0)
            field[b] = np.where(den > 1e-9, num / np.maximum(den, 1e-9), row)
        self._family_field = {"spacing": g, "field": field, "fam_ids": fam_ids}
        return self._family_field

    SEAM_KINDS = ("blazer_shoulder_{s}", "sleeve_cap_{s}")

    def _smooth_family_canvas(self, cid: str, full: np.ndarray, part: np.ndarray, pts: np.ndarray,
                              rule: dict[str, np.ndarray]) -> np.ndarray:
        """Shoulder and sleeve-cap vertices of this layer take the canvas field (bilinear at the vertex); in arm
        layers they then blend into the arm across the shoulder plane exactly as ``auto_weights`` does. Those are
        the parts the Phase 1 torso / upper-arm cut runs through; the other family parts (blazer body, lapels,
        collar, opening) keep the per-layer result in ``full``, unchanged from Phase 3.3."""
        ff = self.family_field()
        seam_ids = [i for i, p in enumerate(self.structure.parts) if p.kind in self.SEAM_KINDS]
        fam = np.isin(part, seam_ids)
        if not fam.any():
            return full
        g, field = ff["spacing"], ff["field"]
        gy, gx = pts[fam, 1] / g, pts[fam, 0] / g
        body = np.stack([_bilinear(field[b], gy, gx) for b in range(field.shape[0])])
        out = full.copy()
        out[:, fam] = body
        I, sk = self.sk.idx, self.sk
        nb = full.shape[0]
        if layer_class(cid) != "torso":
            names = np.array([self.structure.parts[p].name if p >= 0 else "" for p in part])
            for s in SIDES:
                ts = rule.get(f"shoulder_{s}")
                if ts is None:
                    continue
                m = fam & np.isin(names, [f"sleeve_cap_{s}", f"blazer_shoulder_{s}"]) & ~np.isnan(ts)
                if not m.any():
                    continue
                te = np.nan_to_num(rule.get(f"elbow_{s}", np.zeros(len(pts)))[m])
                arm = np.zeros((nb, int(m.sum())))
                arm[I[f"upper_arm_{s}"]] = 1.0 - te
                arm[I[f"forearm_{s}"]] = te
                out[:, m] = (1.0 - ts[m]) * out[:, m] + ts[m] * arm
        # back inside each part's restriction, normalised (as the per-layer smoothing)
        st = self.structure
        for pi in seam_ids:
            m = part == pi
            if not m.any():
                continue
            allowed = np.zeros(nb, bool)
            allowed[[I[b] for b in st.parts[pi].bones if b in I]] = True
            out[~allowed[:, None] & m[None, :]] = 0.0
        out = np.clip(out, 0.0, None)
        return out / np.maximum(out.sum(0), EPS)

    def auto_weights(self, cid: str, pts: np.ndarray, part: np.ndarray) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """(n_bones, N) weights over the whole skeleton for points with garment parts ``part``."""
        sk, st, cfg = self.sk, self.structure, self.cfg
        I = sk.idx
        N = len(pts)
        nb = len(sk.bones)
        out = np.zeros((nb, N), np.float64)
        rule: dict[str, np.ndarray] = {}
        bands = cfg["bands"]

        def tr(joint: str, s: np.ndarray, h: float) -> np.ndarray:
            a, b = bands[joint]
            return smoothstep(a * h, b * h, s)

        def onehot(name: str) -> np.ndarray:
            v = np.zeros((nb, N))
            if name in I:
                v[I[name]] = 1.0
            return v

        names = np.array([st.parts[p].name if p >= 0 else "" for p in part])
        kinds = np.array([st.parts[p].kind if p >= 0 else "" for p in part])
        # body weights: pelvis -> spine -> chest -> (neck), clavicle shrug zone on the shoulders
        hs = sk.bone("spine").h
        t_sp = tr("spine", sk.s(I["spine"], pts), hs)
        t_ch = tr("chest", sk.s(I["chest"], pts), sk.bone("chest").h)
        body = np.zeros((nb, N))
        body[I["pelvis"]] = 1 - t_sp
        body[I["spine"]] = t_sp * (1 - t_ch)
        body[I["chest"]] = t_sp * t_ch
        rule["spine"], rule["chest"] = t_sp, t_ch
        neck_parts = np.isin(kinds, ["neck", "shirt_collar"])
        t_nk = tr("neck", sk.s(I["neck"], pts), max(sk.bone("neck").h, 12.0))
        rule["neck"] = t_nk
        clav = {}
        for s in SIDES:
            b = I[f"clavicle_{s}"]
            z = sk.zones.get(f"clavicle_{s}")
            if z is None:
                clav[s] = np.zeros(N)
                continue
            stp = smoothstep(-sk.rp[b], sk.rc[b], sk.s(b, pts))
            vf = 1.0 - smoothstep(z["fade_y0"], z["fade_y1"], pts[:, 1])
            clav[s] = stp * vf
            rule[f"clavicle_{s}"] = clav[s]

        # the collar follows the neck only partly, over a wide band: a +-6 px neck band across a ~160 px collar
        # sheared the collar points at every small neck rotation (Phase 3.4 baseline D7)
        t_cn = tr("collar_neck", sk.s(I["neck"], pts), max(sk.bone("neck").h, 12.0)) * float(
            cfg.get("collar_neck_share", 0.5))
        rule["collar_neck"] = t_cn

        def body_w(sel: np.ndarray, clav_share: float = 1.0, allow_neck: bool = False) -> np.ndarray:
            v = body[:, sel].copy()
            if allow_neck:
                nk = t_cn[sel] * v[I["chest"]]
                v[I["neck"]] += nk
                v[I["chest"]] -= nk
            for s in SIDES:
                sh = clav_share * clav[s][sel] * v[I["chest"]]
                v[I[f"clavicle_{s}"]] += sh
                v[I["chest"]] -= sh
            return v

        torso_kinds = ["blazer_body", "blazer_shoulder_{s}", "sleeve_cap_{s}", "lapels", "shirt_collar", "sweater"]
        sel = np.isin(kinds, torso_kinds)
        if sel.any():
            out[:, sel] = body_w(sel, 1.0, False)
            lap = sel & (kinds == "lapels")
            if lap.any():
                out[:, lap] = body_w(lap, float(cfg.get("lapel_clavicle_share", 0.35)), False)
            col = sel & (kinds == "shirt_collar")
            if col.any():
                out[:, col] = body_w(col, float(cfg.get("collar_clavicle_share", 1.0)), True)
            sw = sel & (kinds == "sweater")
            if sw.any():
                out[:, sw] = body_w(sw, 0.0, False)
        nsel = kinds == "neck"
        if nsel.any():
            v = np.zeros((nb, int(nsel.sum())))
            # the neck's hidden top and the jaw/ear fragments in the body layer follow the head across its band
            t_hd = tr("head", sk.s(I["head"], pts[nsel]), sk.bone("head").h)
            v[I["chest"]] = 1 - t_nk[nsel]
            v[I["neck"]] = t_nk[nsel] * (1 - t_hd)
            v[I["head"]] = t_nk[nsel] * t_hd
            out[:, nsel] = v
            r = rule.setdefault("head", np.full(N, np.nan))
            r[nsel] = t_hd
        for k in ("blazer_hem", "waistband"):
            m = kinds == k
            out[:, m] = onehot("pelvis")[:, m]
        # sleeves: body -> upper arm across the shoulder plane, upper -> forearm across the elbow plane
        for s in SIDES:
            m = np.isin(names, [f"sleeve_cap_{s}", f"blazer_shoulder_{s}", f"sleeve_upper_{s}", f"elbow_{s}",
                                f"sleeve_fore_{s}", f"shirt_cuff_{s}"])
            if layer_class(cid) == "torso":  # torso-layer shoulder/cap pixels keep body weights
                m &= np.isin(names, [f"sleeve_upper_{s}"])
            if not m.any():
                continue
            P, au = shoulder_frame(sk, s, self.pivot_report)
            s_sh = (pts[m, 0] - P[0]) * au[0] + (pts[m, 1] - P[1]) * au[1]
            t_sh = tr("shoulder", s_sh, sk.bone(f"upper_arm_{s}").h)
            t_el = tr("elbow", sk.s(I[f"forearm_{s}"], pts[m]), sk.bone(f"forearm_{s}").h)
            wb = body_w(m, 1.0, False)
            arm = (1 - t_el) * onehot(f"upper_arm_{s}")[:, m] + t_el * onehot(f"forearm_{s}")[:, m]
            out[:, m] = (1 - t_sh) * wb + t_sh * arm
            for key, val in ((f"shoulder_{s}", t_sh), (f"elbow_{s}", t_el)):
                r = rule.setdefault(key, np.full(N, np.nan))
                r[m] = val
        # trousers: pelvis -> thigh (soft attachment under the hem, crotch), thigh -> shin across the knee
        for s in SIDES:
            m = np.isin(names, [f"trouser_hip_{s}", f"trouser_thigh_{s}", f"knee_{s}", f"trouser_shin_{s}",
                                f"trouser_cuff_{s}"])
            if not m.any():
                continue
            tb = I[f"thigh_{s}"]
            t_hip = GarmentSkeleton.influence(sk, tb, pts[m], np.full(int(m.sum()), tb, np.int64))
            t_kn = tr("knee", sk.s(I[f"shin_{s}"], pts[m]), sk.bone(f"shin_{s}").h)
            # the trouser hem rests on the shoe: below the ankle plane it follows the foot
            t_an = tr("ankle", sk.s(I[f"foot_{s}"], pts[m]), sk.bone(f"foot_{s}").h)
            t_an = np.where(names[m] == f"trouser_cuff_{s}", t_an, 0.0) * float(cfg.get("hem_follows_foot", 0.6))
            leg = ((1 - t_kn) * onehot(f"thigh_{s}")[:, m]
                   + t_kn * ((1 - t_an) * onehot(f"shin_{s}")[:, m] + t_an * onehot(f"foot_{s}")[:, m]))
            out[:, m] = (1 - t_hip) * onehot("pelvis")[:, m] + t_hip * leg
            for key, val in ((f"hip_{s}", t_hip), (f"knee_{s}", t_kn), (f"ankle_{s}", t_an)):
                r = rule.setdefault(key, np.full(N, np.nan))
                r[m] = val
            hem = names == "blazer_hem"
            out[:, hem] = onehot("pelvis")[:, hem]
        for s in SIDES:
            for k, b in ((f"shoe_{s}", f"foot_{s}"), (f"hand_{s}", f"hand_{s}")):
                m = names == k
                out[:, m] = onehot(b)[:, m]
        for k in ("head", "hair"):
            m = names == k
            out[:, m] = onehot("head")[:, m]
        # rigid decorative parts: one weight vector (the body weights at the part centre)
        for p in st.parts:
            if p.cls != RIGID or p.kind not in ("button", "badge", "tie", "pocket_flap_{s}") or not p.centroid:
                continue
            m = names == p.name
            if m.any():
                c = np.array([p.centroid], float)
                cw = np.zeros((nb, 1))
                cw[I["pelvis"]] = 1 - tr("spine", sk.s(I["spine"], c), hs)
                tc = tr("chest", sk.s(I["chest"], c), sk.bone("chest").h)
                cw[I["spine"]] = (1 - cw[I["pelvis"]]) * (1 - tc)
                cw[I["chest"]] = (1 - cw[I["pelvis"]]) * tc
                out[:, m] = cw
        # restriction: only the part's allowed bones
        for pi in np.unique(part):
            if pi < 0:
                continue
            allowed = [I[b] for b in st.parts[pi].bones if b in I]
            m = part == pi
            mask = np.zeros(nb, bool)
            mask[allowed] = True
            out[~mask[:, None] & m[None, :]] = 0.0
        out = np.clip(out, 0.0, None)
        sums = out.sum(0)
        empty = sums < EPS
        if empty.any():
            for i in np.nonzero(empty)[0]:
                p = part[i]
                prim = st.parts[p].primary if p >= 0 else "pelvis"
                out[I.get(prim, 0), i] = 1.0
            sums = out.sum(0)
        return out / sums, rule

    def _lag(self, cid: str, pts: np.ndarray, part: np.ndarray) -> np.ndarray:
        gm = self.sk.model
        top, bottom = gm.hem["top_y"], gm.hem["bottom_y"]
        hem_pid = self.structure.index.get("blazer_hem", -2)
        home = np.where(part == hem_pid, self.sk.HEM, 0)
        cls = layer_class(cid)
        if cls == "head" or cls.startswith("arm") or cid.startswith("foot_"):
            return np.zeros(len(pts))
        return self.sk.hem_weight(pts, home, top, bottom)

    # -- user operations -------------------------------------------------------------------------------------
    def set_ops(self, ops: list[dict[str, Any]], layers: dict[str, np.ndarray]) -> None:
        """Rebuild every mesh from the automatic weights plus ``ops`` (deterministic replay)."""
        self.ops = [dict(o) for o in ops]
        self.op_errors = []
        cids = list(self.meshes)
        self.meshes = {}
        for cid in cids:
            if cid in layers:
                self.mesh_for(cid, layers[cid])

    def _apply_op(self, op: dict[str, Any], only: str | None = None) -> None:
        t = op["type"]
        cid = op.get("layer")
        if only is not None and cid not in (only, None, "*"):
            return
        targets = [only] if only else ([cid] if cid in self.meshes else list(self.meshes))
        for c in targets:
            m = self.meshes.get(c)
            if m is None:
                continue
            try:
                apply_op(m, op, self.sk, self.structure)
            except ValueError as e:
                self.op_errors.append({"op": op, "layer": c, "error": str(e)})

    def explain(self, cid: str, xy) -> dict[str, Any]:
        """Why the point follows the bones it does: its part and class, the allowed bones, the transition rules,
        the interpolated weights and subtree influences, locks and user edits."""
        m = self.meshes[cid]
        p = np.array([xy], float)
        gy, gx = m.grid_coords(p)
        vy, vx = int(np.clip(round(gy[0]), 0, m.shape[0] - 1)), int(np.clip(round(gx[0]), 0, m.shape[1] - 1))
        pid = int(m.part[vy, vx])
        part = self.structure.parts[pid] if pid >= 0 else None
        w = m.weights_at(p)[:, 0]
        infl = {self.sk.bones[b].name: round(float(m.influence(b, p)[0]), 4) for b in m.cum}
        rules = {k: (None if np.isnan(v[vy, vx]) else round(float(v[vy, vx]), 3)) for k, v in m.rule.items()}
        return {"layer": cid, "point": [float(xy[0]), float(xy[1])], "vertex": [vx, vy],
                "part": part.name if part else None, "label": (part.label or part.name) if part else None,
                "semantic": (part.semantic or part.kind) if part else None, "class": part.cls if part else None,
                "allowed_bones": part.bones if part else [], "primary_bone": part.primary if part else None,
                "weights": {b: round(float(v), 4) for b, v in zip(m.bones, w) if v > 1e-4},
                "subtree_influence": {k: v for k, v in infl.items() if v > 1e-4},
                "transitions": {k: v for k, v in rules.items() if v is not None},
                "locked": bool(m.locked[vy, vx]), "user_edits": int(m.touched[vy, vx]) if m.touched is not None else 0,
                "why": _why(part, w, m.bones, rules)}

    def validate(self) -> dict[str, Any]:
        issues: list[dict[str, Any]] = []
        for cid, m in self.meshes.items():
            issues += validate_mesh(m, self.structure, self.sk)
        return {"ok": not issues, "issues": issues[:50], "n_issues": len(issues),
                "meshes": len(self.meshes), "vertices": int(sum(m.active.sum() for m in self.meshes.values())),
                "op_errors": self.op_errors}

    def to_json(self) -> dict[str, Any]:
        return {"schema": "autorig.garment_weights", "version": "1.0.0",
                "convention": "per-layer grid mesh; per vertex one weight per bone (>= 0, sum 1, restricted to "
                              "the vertex's garment part); pixel weights are bilinear in the grid; a bone's "
                              "influence is the summed weight of its subtree (angle interpolation)",
                "spacing_px": self.cfg.get("mesh_spacing_px", 6), "ops": self.ops,
                "layers": {cid: {"origin": list(m.origin), "grid": list(m.shape), "bones": m.bones,
                                 "active_vertices": int(m.active.sum()), "locked_vertices": int(m.locked.sum()),
                                 "edited_vertices": int((m.touched > 0).sum()) if m.touched is not None else 0}
                           for cid, m in self.meshes.items()}}

    def export_arrays(self) -> dict[str, np.ndarray]:
        out = {}
        for cid, m in self.meshes.items():
            out[f"{cid}__w"] = m.w
            out[f"{cid}__locked"] = m.locked
            out[f"{cid}__part"] = m.part
            out[f"{cid}__meta"] = np.array([m.origin[0], m.origin[1], m.spacing], np.float64)
        return out


def _why(part, w, bones, rules) -> str:
    if part is None:
        return "outside every garment part"
    top = sorted(zip(bones, w), key=lambda t: -t[1])
    lead = ", ".join(f"{b} {v:.2f}" for b, v in top if v > 0.01)
    trans = ", ".join(f"{k} {v:.2f}" for k, v in rules.items() if v is not None and 0.01 < v < 0.99)
    msg = f"part '{part.name}' ({part.cls}) may follow {', '.join(part.bones)}; weights: {lead}"
    if trans:
        msg += f"; inside the transition band of {trans}"
    if part.cls == RIGID:
        msg += "; rigid part: one weight vector, locked"
    elif part.cls == LIMITED:
        msg += "; limited deformation: stretch limits apply"
    return msg


def normalise(m: WeightMesh) -> None:
    w = np.clip(m.w, 0.0, None)
    s = w.sum(0)
    bad = s < EPS
    if bad.any():
        w[0][bad] = 1.0
        s = w.sum(0)
    m.w = (w / s).astype(np.float32)


def validate_mesh(m: WeightMesh, structure: GarmentStructure, sk: Skeleton) -> list[dict[str, Any]]:
    out = []
    a = m.active
    if not np.isfinite(m.w).all():
        out.append({"layer": m.cid, "issue": "non-finite weights"})
    if (m.w[:, a] < -1e-6).any():
        out.append({"layer": m.cid, "issue": "negative weights", "count": int((m.w[:, a] < -1e-6).sum())})
    s = m.w[:, a].sum(0)
    if len(s) and np.abs(s - 1).max() > 1e-4:
        out.append({"layer": m.cid, "issue": "weights do not sum to 1", "max_error": float(np.abs(s - 1).max())})
    for pid in np.unique(m.part[a]):
        if pid < 0:
            continue
        allowed = set(structure.parts[pid].bones)
        sel = a & (m.part == pid)
        for k, b in enumerate(m.bones):
            if b not in allowed and (m.w[k][sel] > 1e-5).any():
                out.append({"layer": m.cid, "issue": f"bone {b} outside the restriction of part "
                                                     f"{structure.parts[pid].name}",
                            "count": int((m.w[k][sel] > 1e-5).sum())})
    return out


# -- edit operations (pure functions on one mesh) ---------------------------------------------------------------
def _brush(m: WeightMesh, op: dict[str, Any]) -> np.ndarray:
    """Falloff weight per vertex (0..1) for a circular brush; zero on locked vertices."""
    cx, cy = op["center"]
    r = max(float(op.get("radius", 20.0)), 1.0)
    xy = m.vertex_xy()
    d = np.hypot(xy[..., 0] - cx, xy[..., 1] - cy)
    hard = float(op.get("hardness", 0.5))
    fall = np.where(d <= r * hard, 1.0, np.clip((r - d) / max(r * (1 - hard), 1e-6), 0, 1))
    fall = fall * float(op.get("strength", 0.5))
    fall[m.locked] = 0.0
    if op.get("part"):  # restriction zone: only vertices of this part
        fall[m.part != op["part_index"]] = 0.0
    return fall


def apply_op(m: WeightMesh, op: dict[str, Any], sk: Skeleton, structure: GarmentStructure) -> None:
    t = op["type"]
    if op.get("part"):
        if op["part"] not in structure.index:
            raise ValueError(f"unknown garment part {op['part']}")
        op = {**op, "part_index": structure.index[op["part"]]}
    if t in ("weights.paint", "weights.erase"):
        b = op["bone"]
        if b not in sk.idx:
            raise ValueError(f"unknown bone {b}")
        if b not in m.bones:  # a new column (only usable where a part allows it)
            m.bones.append(b)
            m.w = np.concatenate([m.w, np.zeros((1, *m.shape), np.float32)], 0)
        k = m.bones.index(b)
        fall = _brush(m, op)
        allowed = np.zeros(m.shape, bool)
        for pid in np.unique(m.part):
            if pid >= 0 and b in structure.parts[pid].bones:
                allowed |= m.part == pid
        fall = np.where(allowed, fall, 0.0)
        if not (fall > 0).any():
            raise ValueError(f"no editable vertex under the brush allows bone {b} (locked or restricted)")
        target = 1.0 if t == "weights.paint" else 0.0
        new = m.w[k] + fall * (target - m.w[k])
        _set_column(m, k, new, fall > 0)
        m.touched[fall > 0] += 1
    elif t == "weights.smooth":
        fall = _brush(m, op)
        blur = np.stack([ndimage.uniform_filter(m.w[k], size=3, mode="nearest") for k in range(len(m.bones))])
        sel = fall > 0
        m.w[:, sel] = (m.w[:, sel] * (1 - fall[sel]) + blur[:, sel] * fall[sel])
        _restrict(m, structure)
        normalise(m)
        m.touched[sel] += 1
    elif t == "weights.normalise":
        normalise(m)
    elif t == "weights.lock":
        sel = _brush(m, {**op, "strength": 1.0, "hardness": 1.0}) > 0 if "center" in op else np.ones(m.shape, bool)
        if op.get("part"):
            sel &= m.part == op["part_index"]
        if op.get("locked", True):
            m.locked |= sel
        else:
            rigid = np.array([[structure.parts[p].cls == RIGID if p >= 0 else False for p in row] for row in m.part])
            m.locked &= ~(sel & ~rigid)  # rigid parts stay locked
    else:
        raise ValueError(f"unknown weight op {t}")


def _set_column(m: WeightMesh, k: int, new: np.ndarray, sel: np.ndarray) -> None:
    """Set bone k's weight on ``sel`` and rescale the other bones so the vertex still sums to 1."""
    new = np.clip(new, 0.0, 1.0)
    others = m.w.sum(0) - m.w[k]
    scale = np.where(others > EPS, (1.0 - new) / np.maximum(others, EPS), 0.0)
    for j in range(len(m.bones)):
        if j == k:
            continue
        m.w[j][sel] = m.w[j][sel] * scale[sel]
    m.w[k][sel] = new[sel]
    lone = sel & (others <= EPS) & (new < 1.0)  # nothing to rescale: keep the vertex on bone k
    m.w[k][lone] = 1.0
    normalise(m)


def _restrict(m: WeightMesh, structure: GarmentStructure) -> None:
    for pid in np.unique(m.part):
        if pid < 0:
            continue
        allowed = set(structure.parts[pid].bones)
        sel = m.part == pid
        for k, b in enumerate(m.bones):
            if b not in allowed:
                m.w[k][sel] = 0.0
