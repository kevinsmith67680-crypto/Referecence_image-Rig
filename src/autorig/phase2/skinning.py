"""Local skinned deformation of the original layers (Phase 2 refinement).

Every rest pixel p is mapped by phi(p) = Root o R_b1(f_b1(p) th_b1) o R_b2(...) o ...,
with bones in hierarchy order:
* R_b(a) is a rotation by angle a about bone b's rest pivot. An optional axial scale along the
  bone gives approximate foreshortening; poses that use it are flagged.
* f_b is the bone's influence (`Skeleton.influence`). It is 0 or 1 away from joints, so most pixels
  move rigidly and keep their exact original look. Only within a joint's blend band is the rotation
  interpolated. Interpolating the angle, rather than blending matrices, keeps a joint's volume: no
  pinching.

This is local mesh-free deformation around prepared joints, not whole-image warping. Each layer is
rendered by inverse mapping: for each output pixel q, solve phi(p) = q by fixed-point iteration,
seeded from each home bone's rigid inverse, then sample the original pixels at p (bilinear,
premultiplied). Layers are composited in a per-pose z-order. The identity pose returns the original
layers bit for bit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import ndimage

from .skeleton import Skeleton


@dataclass
class PoseParams:
    theta: np.ndarray  # degrees per bone; entry for the root ("pelvis") = pelvis tilt about its pivot
    scale: np.ndarray  # axial scale per bone (1 = none)
    shift: np.ndarray = field(default_factory=lambda: np.zeros(2))  # root translation (px)

    @classmethod
    def identity(cls, n: int) -> "PoseParams":
        return cls(np.zeros(n), np.ones(n), np.zeros(2))

    def is_identity(self) -> bool:
        return not self.theta.any() and np.all(self.scale == 1) and not self.shift.any()


def _rot(theta_deg: float, pivot) -> np.ndarray:
    t = math.radians(theta_deg)
    c, s = math.cos(t), math.sin(t)
    x, y = pivot
    return np.array([[c, -s, x - c * x + s * y], [s, c, y - s * x - c * y], [0, 0, 1.0]])


class Deformer:
    def __init__(self, sk: Skeleton, layers: dict[str, np.ndarray], generated: dict[str, np.ndarray] | None = None,
                 margin: int = 6, reference: np.ndarray | None = None, variants: set[str] | None = None):
        """``layers``: full-canvas RGBA rest layers (original pixels, generated extensions underneath).
        ``generated``: per-layer boolean masks of generated (unreviewed) pixels, for provenance tracking."""
        self.sk = sk
        self.shape = next(iter(layers.values())).shape[:2]
        self.info: dict[str, dict[str, Any]] = {}
        H, W = self.shape
        # Seamless joins: every layer is extended by a 1 px "bleed" ring wherever it touches another
        # layer, coloured with that neighbour's original pixels. Resampling otherwise leaves both layers
        # half-transparent along their shared cut and the background shows through as a hairline. The
        # ring duplicates pixels that are already there, so the neutral pose is unchanged.
        self.variants = set(variants or ())
        opaque = {c: a[..., 3] == 255 for c, a in layers.items() if c not in self.variants}
        orig_alpha = {c: a[..., 3] > 0 for c, a in layers.items()}
        if reference is not None:  # the visible source pixels (never a hidden generated extension)
            rest = reference.astype(np.float32) / 255.0
        else:
            rest = np.zeros((H, W, 4), np.float32)
            for c, a in layers.items():
                m = opaque[c]
                rest[m] = a[m].astype(np.float32) / 255.0
        anyop = np.zeros((H, W), bool)
        for m in opaque.values():
            anyop |= m
        for cid, rgba in list(layers.items()):
            if cid in self.variants:  # switch drawings replace a layer; no bleed from the rest pose
                continue
            alpha0 = rgba[..., 3] > 0
            ring = ndimage.binary_dilation(alpha0) & ~alpha0 & anyop
            if ring.any():
                rgba = rgba.copy()
                rgba[ring] = np.rint(rest[ring] * 255).astype(np.uint8)
                layers = {**layers, cid: rgba}
        self.bleed = True
        for cid, rgba in layers.items():
            alpha = rgba[..., 3] > 0
            if not alpha.any():
                continue
            ys, xs = np.nonzero(alpha)
            x0, y0 = max(0, xs.min() - margin), max(0, ys.min() - margin)
            x1, y1 = min(W, xs.max() + margin + 1), min(H, ys.max() + margin + 1)
            crop = rgba[y0:y1, x0:x1].astype(np.float32) / 255.0
            pre = crop.copy()
            pre[..., :3] *= pre[..., 3:4]
            a_crop = alpha[y0:y1, x0:x1]
            ay, ax = np.nonzero(a_crop)
            home_pts = sk.home(cid, np.stack([ax + x0, ay + y0], 1).astype(np.float64))
            home = np.zeros(a_crop.shape, np.int16)
            home[ay, ax] = home_pts
            # extend home bones to the transparent margin (nearest opaque pixel) for sub-pixel lookups
            _, (iy, ix) = ndimage.distance_transform_edt(~a_crop, return_indices=True)
            home = home[iy, ix]
            gen = None
            if generated and cid in generated and generated[cid].any():
                gen = generated[cid][y0:y1, x0:x1]
            self.info[cid] = {"rgba": rgba, "pre": pre, "alpha": a_crop, "origin": (x0, y0), "home": home,
                              "alpha0": orig_alpha[cid][y0:y1, x0:x1],
                              "dil": ndimage.binary_dilation(a_crop, iterations=3), "gen": gen,
                              "homes": np.unique(home_pts), "area": int(alpha.sum())}

    # -- transforms ------------------------------------------------------------------------------
    def root_matrix(self, pose: PoseParams) -> np.ndarray:
        r = _rot(pose.theta[0], self.sk.J[0])
        r[:2, 2] += pose.shift
        return r

    def world(self, b: int, pose: PoseParams) -> np.ndarray:
        """Rigid world transform of bone b (full rotation of b and its ancestors)."""
        chain, j = [], b
        while j > 0:
            chain.append(j)
            j = self.sk.parent[j]
        T = self.root_matrix(pose)
        for j in reversed(chain):
            T = T @ self._bone_matrix(j, pose, 1.0, own=(j == b))
        return T

    def _bone_matrix(self, b: int, pose: PoseParams, f: float, own: bool = True) -> np.ndarray:
        """Rotation about the bone's pivot, after its axial scale. The scale (foreshortening) shrinks the
        bone's own pixels; for descendants it is a translation that keeps them on the moved child joint."""
        ux, uy = self.sk.U[b]
        J = self.sk.J[b]
        S, tS = np.eye(2), np.zeros(2)
        if pose.scale[b] != 1:
            if own:
                k = 1.0 + f * (pose.scale[b] - 1.0)
                S = np.array([[1 + (k - 1) * ux * ux, (k - 1) * ux * uy], [(k - 1) * ux * uy, 1 + (k - 1) * uy * uy]])
                tS = J - S @ J
            else:
                tS = (pose.scale[b] - 1.0) * self.sk.bones[b].length * np.array([ux, uy])
        t = math.radians(pose.theta[b] * f)
        R = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
        out = np.eye(3)
        out[:2, :2] = R @ S
        out[:2, 2] = R @ tS + J - R @ J
        return out

    def active_bones(self, pose: PoseParams) -> list[int]:
        return [b for b in self.sk.topo if b != 0 and (pose.theta[b] != 0 or pose.scale[b] != 1)]

    def compose(self, pts: np.ndarray, home: np.ndarray, pose: PoseParams, bones: list[int]):
        """Per-point affine (a, b, c, d, tx, ty) of phi at rest points ``pts``."""
        n = len(pts)
        a, b_, c, d = np.ones(n), np.zeros(n), np.zeros(n), np.ones(n)
        tx, ty = np.zeros(n), np.zeros(n)
        for bi in bones:
            f = self.sk.influence(bi, pts, home)
            if not f.any():
                continue
            ang = np.radians(pose.theta[bi]) * f
            co, si = np.cos(ang), np.sin(ang)
            jx, jy = self.sk.J[bi]
            if pose.scale[bi] != 1:  # foreshortening: own pixels scale along the bone, descendants translate
                own = home == bi
                kk = np.where(own, 1.0 + f * (pose.scale[bi] - 1.0), 1.0)
                ux, uy = self.sk.U[bi]
                s11, s12, s22 = 1 + (kk - 1) * ux * ux, (kk - 1) * ux * uy, 1 + (kk - 1) * uy * uy
                dl = np.where(self.sk.desc[bi][home], (pose.scale[bi] - 1.0) * self.sk.bones[bi].length, 0.0)
                tsx = np.where(own, jx - (s11 * jx + s12 * jy), 0.0) + dl * ux
                tsy = np.where(own, jy - (s12 * jx + s22 * jy), 0.0) + dl * uy
                r11, r12 = co * s11 - si * s12, co * s12 - si * s22
                r21, r22 = si * s11 + co * s12, si * s12 + co * s22
                trx = co * tsx - si * tsy + jx - (co * jx - si * jy)
                try_ = si * tsx + co * tsy + jy - (si * jx + co * jy)
            else:
                r11, r12, r21, r22 = co, -si, si, co
                trx, try_ = jx - (r11 * jx + r12 * jy), jy - (r21 * jx + r22 * jy)
            a, b_, c, d, tx, ty = (a * r11 + b_ * r21, a * r12 + b_ * r22, c * r11 + d * r21, c * r12 + d * r22,
                                   a * trx + b_ * try_ + tx, c * trx + d * try_ + ty)
        R0 = self.root_matrix(pose)
        return (R0[0, 0] * a + R0[0, 1] * c, R0[0, 0] * b_ + R0[0, 1] * d, R0[1, 0] * a + R0[1, 1] * c,
                R0[1, 0] * b_ + R0[1, 1] * d, R0[0, 0] * tx + R0[0, 1] * ty + R0[0, 2],
                R0[1, 0] * tx + R0[1, 1] * ty + R0[1, 2])

    def forward(self, cid: str, pts: np.ndarray, pose: PoseParams) -> np.ndarray:
        inf = self.info[cid]
        home = self._home_at(inf, pts)
        a, b, c, d, tx, ty = self.compose(pts, home, pose, self.active_bones(pose))
        return np.stack([a * pts[:, 0] + b * pts[:, 1] + tx, c * pts[:, 0] + d * pts[:, 1] + ty], 1)

    @staticmethod
    def _home_at(inf, pts) -> np.ndarray:
        x0, y0 = inf["origin"]
        h, w = inf["home"].shape
        xi = np.clip(np.rint(pts[:, 0] - x0).astype(int), 0, w - 1)
        yi = np.clip(np.rint(pts[:, 1] - y0).astype(int), 0, h - 1)
        return inf["home"][yi, xi]

    # -- rendering --------------------------------------------------------------------------------
    def render_layer(self, cid: str, pose: PoseParams, pad: int = 0):
        """Returns (premultiplied float RGBA crop, (x, y) origin in padded canvas, generated-pixel mask,
        deformation stats)."""
        inf = self.info[cid]
        x0, y0 = inf["origin"]
        bones = [b for b in self.active_bones(pose)]
        ay, ax = np.nonzero(inf["alpha"])
        step = max(1, int(math.sqrt(len(ay) / 4000)))
        rest = np.stack([ax[::step] + x0, ay[::step] + y0], 1).astype(np.float64)
        home_r = inf["home"][ay[::step], ax[::step]]
        # skip bones with no influence anywhere on this layer
        bones = [b for b in bones if self.sk.influence(b, rest, home_r).any()]
        stats = self._jacobian_stats(cid, rest, home_r, pose, bones)
        dst = self._apply(rest, home_r, pose, bones)
        qx0, qy0 = int(math.floor(dst[:, 0].min())) - 3, int(math.floor(dst[:, 1].min())) - 3
        qx1, qy1 = int(math.ceil(dst[:, 0].max())) + 4, int(math.ceil(dst[:, 1].max())) + 4
        gh, gw = qy1 - qy0, qx1 - qx0
        hcrop, wcrop = inf["alpha"].shape
        # Seeds from the forward map: splat every rest pixel (deeper bones last, so in a fold the child
        # is drawn over its parent), then fill small holes from the nearest seed. Newton refinement
        # from such seeds converges in a few steps even where a joint stretches or folds.
        ey, ex = np.nonzero(inf["dil"])
        rp_all = np.stack([ex + x0, ey + y0], 1).astype(np.float64)
        hp_all = inf["home"][ey, ex]
        order_ = np.argsort(self.sk.depth[hp_all], kind="stable")
        rp_all, hp_all = rp_all[order_], hp_all[order_]
        fw = self._apply(rp_all, hp_all, pose, bones)
        seed = np.full((gh, gw, 2), np.nan)
        for off in ((0.0, 0.0), (0.5, 0.0), (0.0, 0.5), (0.5, 0.5)):  # sub-pixel splats cover stretch
            pts = rp_all + off
            fq = fw if off == (0.0, 0.0) else self._apply(pts, hp_all, pose, bones)
            qi = np.rint(fq[:, 0]).astype(int) - qx0, np.rint(fq[:, 1]).astype(int) - qy0
            okq = (qi[0] >= 0) & (qi[0] < gw) & (qi[1] >= 0) & (qi[1] < gh)
            seed[qi[1][okq], qi[0][okq]] = pts[okq]
        have = ~np.isnan(seed[..., 0])
        if not have.any():
            out = np.zeros((gh, gw, 4), np.float32)
            return out, (qx0 + pad, qy0 + pad), np.zeros((gh, gw), bool), stats
        dist, (iy, ix) = ndimage.distance_transform_edt(~have, return_indices=True)
        use = dist <= 3.0
        gy, gx = np.nonzero(use)
        q = np.stack([gx + qx0, gy + qy0], 1).astype(np.float64)
        p = seed[iy[gy, gx], ix[gy, gx]].copy()
        p += (q - np.stack([ix[gy, gx] + qx0, iy[gy, gx] + qy0], 1))  # shift the borrowed seed
        for _ in range(3):  # cheap fixed-point steps
            h = self._home_at(inf, p)
            a, b, c, d, tx, ty = self.compose(p, h, pose, bones)
            det = a * d - b * c
            rx, ry = q[:, 0] - tx, q[:, 1] - ty
            p = np.stack([(d * rx - b * ry) / det, (-c * rx + a * ry) / det], 1)
        fwd = self._apply(p, self._home_at(inf, p), pose, bones)
        bad = np.hypot(*(fwd - q).T) >= 0.35
        for _ in range(8):  # Newton with the local Jacobian where the fixed point stalls
            if not bad.any():
                break
            pb = p[bad]
            hb_ = self._home_at(inf, pb)
            f0 = self._apply(pb, hb_, pose, bones)
            fx = self._apply(pb + [0.5, 0], hb_, pose, bones)
            fy = self._apply(pb + [0, 0.5], hb_, pose, bones)
            j11, j21 = (fx[:, 0] - f0[:, 0]) / 0.5, (fx[:, 1] - f0[:, 1]) / 0.5
            j12, j22 = (fy[:, 0] - f0[:, 0]) / 0.5, (fy[:, 1] - f0[:, 1]) / 0.5
            det = j11 * j22 - j12 * j21
            det = np.where(np.abs(det) < 1e-6, 1e-6, det)
            rx, ry = q[bad, 0] - f0[:, 0], q[bad, 1] - f0[:, 1]
            step = np.stack([(j22 * rx - j12 * ry) / det, (-j21 * rx + j11 * ry) / det], 1)
            n = np.hypot(*step.T)[:, None]
            p[bad] = pb + step * np.minimum(1.0, 6.0 / np.maximum(n, 1e-9))
            fb = self._apply(p[bad], self._home_at(inf, p[bad]), pose, bones)
            idx_bad = np.nonzero(bad)[0]
            bad[idx_bad[np.hypot(*(fb - q[bad]).T) < 0.35]] = False
        fwd = self._apply(p, self._home_at(inf, p), pose, bones)
        ok = np.hypot(*(fwd - q).T) < 0.35
        # inside folds (an inner crease where the child overlaps its parent) the solve can stall; a pixel
        # that a rest pixel splats onto directly then takes that rest pixel (<= 1 px approximation)
        direct = have[gy, gx] & ~ok
        p[direct] = seed[gy[direct], gx[direct]]
        ok |= direct
        ok &= self._sample(inf["pre"][..., 3], p[:, 0] - x0, p[:, 1] - y0) > 0.003
        src = np.full((gh * gw, 2), np.nan)
        src[(gy * gw + gx)[ok]] = p[ok]
        valid = ~np.isnan(src[:, 0])
        out = np.zeros((gh, gw, 4), np.float32)
        gen = np.zeros(out.shape[:2], bool)
        if valid.any():
            sx, sy = src[valid, 0] - x0, src[valid, 1] - y0
            vals = np.stack([self._sample(inf["pre"][..., k], sx, sy) for k in range(4)], 1)
            flat = out.reshape(-1, 4)
            flat[valid] = vals
            if inf["gen"] is not None:
                gi = inf["gen"][np.clip(np.rint(sy).astype(int), 0, hcrop - 1),
                                np.clip(np.rint(sx).astype(int), 0, wcrop - 1)]
                gen.reshape(-1)[np.nonzero(valid)[0]] = gi
        return out, (qx0 + pad, qy0 + pad), gen, stats

    def _apply(self, pts, home, pose, bones):
        a, b, c, d, tx, ty = self.compose(pts, home, pose, bones)
        return np.stack([a * pts[:, 0] + b * pts[:, 1] + tx, c * pts[:, 0] + d * pts[:, 1] + ty], 1)

    @staticmethod
    def _sample(img: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        return ndimage.map_coordinates(img, [y, x], order=1, mode="constant", cval=0.0)

    def _jacobian_stats(self, cid, rest, home, pose, bones) -> dict[str, Any]:
        """Local area change of phi on the layer: det(J) < 0 is a fold, small is compression,
        large is stretch. Measured with 1 px finite differences at sampled rest pixels."""
        if not bones:
            return {"rigid": True}
        e = 1.0
        p0 = self._apply(rest, home, pose, bones)
        px = self._apply(rest + [e, 0], home, pose, bones)
        py = self._apply(rest + [0, e], home, pose, bones)
        det = ((px[:, 0] - p0[:, 0]) * (py[:, 1] - p0[:, 1]) - (px[:, 1] - p0[:, 1]) * (py[:, 0] - p0[:, 0])) / e ** 2
        blended = np.zeros(len(rest), bool)
        for b in bones:
            f = self.sk.influence(b, rest, home)
            blended |= (f > 0.001) & (f < 0.999)
        return {"rigid": False, "blended_fraction": round(float(blended.mean()), 4),
                "det_min": round(float(det.min()), 3), "det_max": round(float(det.max()), 3),
                "folded_fraction": round(float((det <= 0.05).mean()), 5),
                "compressed_fraction": round(float((det < 0.45).mean()), 5),
                "stretched_fraction": round(float((det > 2.2).mean()), 5)}

    def render(self, pose: PoseParams, order: list[str], pad: int = 0) -> dict[str, Any]:
        """Composite the posed layers in ``order`` (back to front) onto a canvas padded by ``pad``."""
        H, W = self.shape
        canvas = np.zeros((H + 2 * pad, W + 2 * pad, 4), np.float32)
        gen_vis = np.zeros(canvas.shape[:2], bool)
        top = np.full(canvas.shape[:2], -1, np.int16)
        alphas, stats = {}, {}
        ident = pose.is_identity()
        for cid in order:
            if cid not in self.info:
                continue
            inf = self.info[cid]
            if ident:
                x0, y0 = inf["origin"]
                lay = inf["pre"]
                ox, oy = x0 + pad, y0 + pad
                gen = inf["gen"] if inf["gen"] is not None else np.zeros(lay.shape[:2], bool)
                st = {"rigid": True}
            else:
                lay, (ox, oy), gen, st = self.render_layer(cid, pose, pad)
            stats[cid] = st
            h, w = lay.shape[:2]
            cx0, cy0 = max(0, ox), max(0, oy)
            cx1, cy1 = min(canvas.shape[1], ox + w), min(canvas.shape[0], oy + h)
            if cx1 <= cx0 or cy1 <= cy0:
                continue
            sub = lay[cy0 - oy:cy1 - oy, cx0 - ox:cx1 - ox]
            dstv = canvas[cy0:cy1, cx0:cx1]
            dstv[:] = sub + dstv * (1.0 - sub[..., 3:4])
            a = np.zeros(canvas.shape[:2], bool)
            a[cy0:cy1, cx0:cx1] = sub[..., 3] > 0.5
            top[a] = order.index(cid)
            alphas[cid] = a
            g = gen[cy0 - oy:cy1 - oy, cx0 - ox:cx1 - ox] & (sub[..., 3] > 0.5)
            region = gen_vis[cy0:cy1, cx0:cx1]
            region[:] = g | (region & ~(sub[..., 3] > 0.5))
        out = canvas.copy()
        a = out[..., 3:4]
        out[..., :3] = np.where(a > 1e-6, out[..., :3] / np.maximum(a, 1e-6), 0.0)
        img = np.clip(np.rint(out * 255.0), 0, 255).astype(np.uint8)
        return {"image": img, "alphas": alphas, "stats": stats, "generated_visible": gen_vis, "pad": pad,
                "top": top, "order": list(order)}

    def tears(self, pose: PoseParams, render: dict[str, Any], dark: np.ndarray | None = None, step: int = 2
              ) -> dict[str, Any]:
        """Seams: pairs of rest-adjacent visible pixels (``step`` apart) that the pose pulls apart by more
        than 2.5x their spacing. A tear only counts when it can be seen at its posed position: the
        background shows through (exposed transparency), or one of the two torn layers is on top there
        (a visible cut or broken outline). Hidden tears under another layer do not count."""
        H, W = self.shape
        order = render["order"]  # indices must match render["top"]
        owner = np.full((H, W), -1, np.int16)
        for i, cid in enumerate(order):
            if cid not in self.info:
                continue
            inf = self.info[cid]
            x0, y0 = inf["origin"]
            a0 = inf["alpha0"]
            owner[y0:y0 + a0.shape[0], x0:x0 + a0.shape[1]][a0] = i
        gy, gx = np.mgrid[0:H:step, 0:W:step]
        own = owner[gy, gx]
        PX, PY = np.full(gy.shape, np.nan), np.full(gy.shape, np.nan)
        for i, cid in enumerate(order):
            sel = own == i
            if sel.any() and cid in self.info:
                q = self.forward(cid, np.stack([gx[sel], gy[sel]], 1).astype(np.float64), pose)
                PX[sel], PY[sel] = q[:, 0], q[:, 1]
        pad, top, alpha = render["pad"], render["top"], render["image"][..., 3]
        out = {"exposed_gap": [], "visible_seam": [], "outline_break": [], "segments": []}
        for sl_a, sl_b in (((slice(None), slice(None, -1)), (slice(None), slice(1, None))),
                           ((slice(None, -1), slice(None)), (slice(1, None), slice(None)))):
            ax_, ay_, bx_, by_ = PX[sl_a], PY[sl_a], PX[sl_b], PY[sl_b]
            ok = ~np.isnan(ax_) & ~np.isnan(bx_)
            d = np.hypot(bx_ - ax_, by_ - ay_)
            t = ok & (d > 2.5 * step)
            if not t.any():
                continue
            mx = np.rint((ax_[t] + bx_[t]) / 2 + pad).astype(int)
            my = np.rint((ay_[t] + by_[t]) / 2 + pad).astype(int)
            inb = (mx >= 0) & (mx < alpha.shape[1]) & (my >= 0) & (my < alpha.shape[0])
            mx, my = np.clip(mx, 0, alpha.shape[1] - 1), np.clip(my, 0, alpha.shape[0] - 1)
            la, lb = own[sl_a][t], own[sl_b][t]
            gap = inb & (alpha[my, mx] < 128)
            seam = inb & ~gap & ((top[my, mx] == la) | (top[my, mx] == lb)) & (la != lb)
            for k, m_ in (("exposed_gap", gap), ("visible_seam", seam)):
                out[k] += list(zip(mx[m_].tolist(), my[m_].tolist()))
            g_ = gap | seam  # posed end points of each visible torn pair (what a corrective fill must bridge)
            out["segments"] += np.stack([ax_[t][g_] + pad, ay_[t][g_] + pad, bx_[t][g_] + pad,
                                         by_[t][g_] + pad], 1).tolist()
            if dark is not None:
                rx, ry = gx[sl_a][t], gy[sl_a][t]
                brk = (gap | seam) & dark[ry, rx]
                out["outline_break"] += list(zip(mx[brk].tolist(), my[brk].tolist()))
        return out

    def masses(self) -> dict[int, tuple[float, np.ndarray]]:
        """Mass proxy per home bone: opaque rest pixel count and its centroid (all layers)."""
        acc: dict[int, list] = {}
        for cid, inf in self.info.items():
            if cid in self.variants:
                continue
            x0, y0 = inf["origin"]
            ay, ax = np.nonzero(inf["alpha"])
            hb = inf["home"][ay, ax]
            for b in np.unique(hb):
                sel = hb == b
                m = float(sel.sum())
                c = np.array([ax[sel].mean() + x0, ay[sel].mean() + y0])
                if b in acc:
                    m0, c0 = acc[b]
                    acc[b] = [m0 + m, (c0 * m0 + c * m) / (m0 + m)]
                else:
                    acc[b] = [m, c]
        return {int(b): (v[0], v[1]) for b, v in acc.items()}
