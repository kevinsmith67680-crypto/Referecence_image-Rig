"""Garment model and the artwork deformation engine (Phase 3.1).

Clothing is no longer a rigid attachment to the bone under it. A ``GarmentModel`` labels the rest artwork with
semantic garment regions (blazer body, lapels, collar, sweater, blazer hem, sleeves, shoulder seams, shirt
cuffs, trouser thighs, knees and cuffs, shoes). The regions are estimated from the skeleton, the layer masks
and colour (navy blazer, warm charcoal trousers, beige trim, white shirt). They are shown in the diagnostics
and can be wrong. Each region has anchors and deformation rules that are independent of the skeleton:

* blazer hem: hangs from the pelvis. It ignores thigh rotation, including the hem trim that the segmentation
  cut into the thigh layers, and lags behind pelvis roll (secondary motion about the waist);
* shoulder seams, elbows, hips, knees and the neck: blend bands that widen with the joint's rotation
  (rotation-dependent correctives: the data a Moho Smart Bone action would hold);
* trouser cuffs drawn over the shoes, sleeve ends over the hands (layering rules);
* fold metadata for knees and elbows (for later corrective drawings).

Everything acts through the Phase 2 skinning deformer, so the identity pose stays bit-exact and original pixels
are only moved, never repainted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import ndimage

from ..phase2.skeleton import SIDES, Skeleton, layer_class, smoothstep
from ..phase2.skinning import Deformer, PoseParams
from .profile import CharacterProfile

REGIONS = ["none", "blazer_body", "lapel_trim", "shirt_collar", "tie_badge", "sweater", "blazer_hem",
           "shoulder_seam_r", "shoulder_seam_l", "sleeve_upper_r", "sleeve_upper_l", "sleeve_fore_r", "sleeve_fore_l",
           "shirt_cuff_r", "shirt_cuff_l", "hand_r", "hand_l", "trouser_thigh_r", "trouser_thigh_l",
           "trouser_knee_r", "trouser_knee_l", "trouser_shin_r", "trouser_shin_l", "trouser_cuff_r", "trouser_cuff_l",
           "shoe_r", "shoe_l", "neck", "head", "hair"]
RID = {n: i for i, n in enumerate(REGIONS)}
REGION_COLOR = {"blazer_body": (40, 70, 200), "lapel_trim": (240, 200, 120), "shirt_collar": (250, 250, 250),
                "tie_badge": (250, 180, 0), "sweater": (110, 40, 160), "blazer_hem": (230, 40, 90),
                "shoulder_seam": (0, 220, 200), "sleeve_upper": (60, 160, 255), "sleeve_fore": (120, 200, 255),
                "shirt_cuff": (255, 255, 160), "hand": (255, 150, 120), "trouser_thigh": (150, 110, 60),
                "trouser_knee": (255, 120, 0), "trouser_shin": (190, 150, 90), "trouser_cuff": (255, 220, 0),
                "shoe": (100, 60, 30), "neck": (255, 190, 150), "head": (255, 210, 180), "hair": (160, 255, 120)}

HEM_SOFT = 30.0  # px: trouser attachment zone under the blazer hem

# rotation-dependent band widening: kind -> (gain at full range, full range in deg)
BAND_RULES = {"upper_arm": (0.5, 90.0, "shoulder seam (sleeve side)"), "forearm": (0.6, 120.0, "elbow / sleeve crease"),
              "thigh": (0.4, 45.0, "hip / trouser top"), "shin": (0.7, 60.0, "knee / trouser fold"),
              "neck": (0.5, 25.0, "neck attachment"), "head": (0.5, 25.0, "neck attachment")}


def colour_classes(src: np.ndarray) -> dict[str, np.ndarray]:
    rgb = src[..., :3].astype(np.int16)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = (0.299 * r + 0.587 * g + 0.114 * b)
    return {"navy": (b - r >= 3) & (lum < 110), "warm_dark": (b - r <= -3) & (lum < 120),
            "trim": (lum > 125) & (r - b >= 15) & (r - b <= 80) & (g - b < 35),
            "white": (lum > 195) & ((rgb.max(-1) - rgb.min(-1)) < 40), "outline": lum < 38,
            "gold": (r > 170) & (g > 110) & (b < 90) & (r - b > 110)}


@dataclass
class GarmentModel:
    labels: np.ndarray  # (H, W) uint8 region id of each rest pixel's garment region (original pixels)
    hem_override: np.ndarray  # (H, W) bool: thigh-layer pixels that belong to the blazer hem
    hem: dict[str, float]  # waist pivot and vertical extent of the blazer skirt
    anchors: dict[str, Any]
    stats: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, source: np.ndarray, masks: dict[str, np.ndarray], sk: Skeleton, p: CharacterProfile
              ) -> "GarmentModel":
        H, W = source.shape[:2]
        C = colour_classes(source)
        lab = np.zeros((H, W), np.uint8)
        yy, xx = np.mgrid[:H, :W]
        mid = p.midline_x
        b = sk.bone
        notes = ["regions are estimated from skeleton geometry, layer masks and colour; check them in the "
                 "garment diagnostics overlay"]
        get = lambda n: masks.get(n, np.zeros((H, W), bool))
        torso, pelvis = get("torso"), get("pelvis")
        # trim lines closest to the midline per row bound the central opening (sweater / shirt / tie)
        opening = np.zeros((H, W), bool)
        body = torso | pelvis
        for y in np.nonzero(body.any(1))[0]:
            xs_trim = np.nonzero(C["trim"][y] & body[y])[0]
            left, right = xs_trim[xs_trim < mid - 4], xs_trim[xs_trim > mid + 4]
            if len(left) and len(right) and (right.min() - left.max()) < 1.6 * p.waist_half:
                opening[y, left.max():right.min() + 1] = True
        clav_y = min(b("clavicle_r").head[1], b("clavicle_l").head[1])
        lab[torso] = RID["blazer_body"]
        lab[torso & opening] = RID["sweater"]
        lab[torso & C["trim"]] = RID["lapel_trim"]
        lab[torso & C["white"] & (yy < clav_y + 0.5 * b("upper_arm_r").h)] = RID["shirt_collar"]
        lab[torso & C["gold"]] = RID["tie_badge"]
        # blazer skirt in the pelvis layer: everything outside the central opening
        lab[pelvis] = RID["sweater"]
        lab[pelvis & ~opening] = RID["blazer_hem"]
        # hem trim cut into the thigh layers: per column, navy/trim pixels from the layer top down to the hem edge
        hem_over = np.zeros((H, W), bool)
        hip_y = p.hip_y
        band = (yy >= hip_y - 40) & (yy <= hip_y + 0.45 * b("thigh_r").length)
        for s in SIDES:
            th = get(f"thigh_{s}") & band
            for x in np.nonzero(th.any(0))[0]:
                col = np.nonzero(th[:, x])[0]
                garment = C["navy"][col, x] | C["trim"][col, x]
                if garment.sum() < 3:
                    continue
                top, last = col[0], col[np.nonzero(garment)[0].max()]
                if last - top > 0.45 * b("thigh_r").length:
                    continue
                hem_over[top:last + 4, x] = True  # include the outline under the trim
            hem_over &= get(f"thigh_{s}") | hem_over
        thighs = get("thigh_r") | get("thigh_l")
        hem_over &= thighs
        hem_over = ndimage.binary_opening(hem_over, iterations=1) & thighs
        for s in SIDES:
            sg = -1.0 if s == "r" else 1.0
            ua, fa, hd = get(f"upper_arm_{s}"), get(f"forearm_{s}"), get(f"hand_{s}")
            S, E, Wr = b(f"upper_arm_{s}").head, b(f"forearm_{s}").head, b(f"hand_{s}").head
            dS = np.hypot(xx - S[0], yy - S[1])
            lab[ua] = RID[f"sleeve_upper_{s}"]
            lab[(ua | torso) & (dS < 0.9 * b(f"upper_arm_{s}").h)] = RID[f"shoulder_seam_{s}"]
            lab[fa] = RID[f"sleeve_fore_{s}"]
            dW = np.hypot(xx - Wr[0], yy - Wr[1])
            lab[fa & C["white"] & (dW < 0.45 * b(f"forearm_{s}").length)] = RID[f"shirt_cuff_{s}"]
            lab[hd] = RID[f"hand_{s}"]
            tg, sh, ft = get(f"thigh_{s}"), get(f"shin_{s}"), get(f"foot_{s}")
            K, A = b(f"shin_{s}").head, b(f"foot_{s}").head
            dK = np.hypot(xx - K[0], yy - K[1])
            dA = np.hypot(xx - A[0], yy - A[1])
            lab[tg] = RID[f"trouser_thigh_{s}"]
            lab[sh] = RID[f"trouser_shin_{s}"]
            lab[(tg | sh) & (dK < 1.0 * b(f"shin_{s}").h)] = RID[f"trouser_knee_{s}"]
            lab[sh & (dA < 0.3 * b(f"shin_{s}").length)] = RID[f"trouser_cuff_{s}"]
            lab[ft] = RID[f"shoe_{s}"]
            del sg
        lab[hem_over] = RID["blazer_hem"]
        lab[get("neck")] = RID["neck"]
        for c, m in masks.items():
            if layer_class(c) == "head":
                lab[m] = RID["hair"] if c == "hair" else RID["head"]
        hair_m = get("hair")
        face_m = get("head_base")
        if hair_m.any():
            hy = np.nonzero(hair_m.any(1))[0]
            fy = np.nonzero(face_m.any(1))[0] if face_m.any() else hy
            hair = {"top_y": float(hy.min()), "hairline_y": float(max(fy.min(), hy.min() + 20)),
                    "pivot_x": float(mid), "pivot_y": float((b("head").head[1] + hy.min()) / 2)}
        else:
            hair = None
        neck_m = get("neck")
        rows = [np.nonzero(r)[0] for r in neck_m if r.any()]
        neck_half = float(np.median([(r.max() - r.min()) / 2 for r in rows])) if rows else 0.25 * p.hip_sep
        hem_px = lab == RID["blazer_hem"]
        ys = np.nonzero(hem_px.any(1))[0]
        top = float(max(b("spine").head[1], ys.min())) if len(ys) else hip_y - 60
        bottom = float(ys.max()) if len(ys) else hip_y + 40
        hem = {"pivot_x": float(mid), "pivot_y": top, "top_y": top, "bottom_y": bottom}
        anchors = {"neck_half_width_px": neck_half, "blazer_body": ["chest"], "lapel_trim": ["chest"], "shirt_collar": ["chest", "neck"],
                   "sweater": ["chest", "spine", "pelvis"], "blazer_hem": ["pelvis (thigh influence removed)"],
                   "shoulder_seam": ["clavicle", "upper_arm"], "sleeve_upper": ["upper_arm"],
                   "sleeve_fore": ["forearm"], "shirt_cuff": ["forearm"], "trouser_thigh": ["thigh"],
                   "trouser_knee": ["thigh", "shin"], "trouser_shin": ["shin"], "trouser_cuff": ["shin"],
                   "shoe": ["foot"]}
        stats = {n: int((lab == i).sum()) for n, i in RID.items() if i}
        stats["hem_pixels_in_thigh_layers"] = int(hem_over.sum())
        notes.append(f"{int(hem_over.sum())} blazer-hem pixels were cut into the thigh layers; they now hang from "
                     "the pelvis instead of following the thighs")
        anchors["hair"] = hair
        return cls(lab, hem_over, hem, anchors, stats, notes)

    def to_json(self) -> dict[str, Any]:
        return {"regions": {n: {"pixels": self.stats.get(n, 0), "anchors": self.anchors.get(n.rsplit("_", 1)[0]
                                                                                          if n[-2:] in ("_r", "_l")
                                                                                          else n, [])}
                            for n in REGIONS[1:]},
                "hem": self.hem, "hem_pixels_in_thigh_layers": int(self.hem_override.sum()),
                "band_rules": {k: {"gain": g, "full_range_deg": r, "region": n} for k, (g, r, n) in BAND_RULES.items()},
                "layering": ["foot before shin (trouser cuff over the shoe)", "hand before forearm (sleeve over wrist)",
                             "legs before pelvis/torso (blazer hem over the trousers)"],
                "notes": self.notes}

    def overlay(self) -> np.ndarray:
        H, W = self.labels.shape
        out = np.zeros((H, W, 4), np.uint8)
        for n, i in RID.items():
            if not i:
                continue
            key = n[:-2] if n[-2:] in ("_r", "_l") else n
            col = REGION_COLOR.get(key)
            if col:
                out[self.labels == i] = (*col, 150)
        return out


class GarmentSkeleton(Skeleton):
    """The anatomical skeleton plus garment-aware homes and influence. One virtual home (``HEM``) marks
    blazer-hem pixels: they belong to the pelvis but take no thigh rotation. Virtual homes ``RIGID[side]`` mark
    stored hand switch drawings (layer ids with ``__hs.``): every pixel follows the hand bone's full transform
    (no joint blend band), so a switch hand is rigid artwork driven by the wrist only. The sleeve stays with the
    forearm; the cuff covers the seam. Original hand pixels keep the blended wrist band (skin continuity)."""

    def __init__(self, base: Skeleton, model: GarmentModel):
        super().__init__(base.bones, base.ground_y, base.midline_x, base.notes)
        self.rp, self.rc, self.R1, self.R2 = base.rp.copy(), base.rc.copy(), base.R1.copy(), base.R2.copy()
        self.base_rp, self.base_rc = self.rp.copy(), self.rc.copy()
        self.zones = base.zones
        self.model = model
        N = len(self.bones)
        self.HEM = N
        extra = [self.idx["pelvis"]]
        self.RIGID: dict[str, int] = {}
        for s in SIDES:
            if f"hand_{s}" in self.idx:
                self.RIGID[s] = N + len(extra)
                extra.append(self.idx[f"hand_{s}"])
        self.vreal = np.r_[np.arange(N), extra].astype(np.int64)
        self._rigid = np.zeros(len(self.vreal), bool)
        self._rigid[list(self.RIGID.values())] = True
        self.depth = np.append(self.depth, self.depth[extra])
        self.desc = np.concatenate([self.desc, self.desc[:, extra]], 1)
        self.anc_v = np.concatenate([self.anc, self.anc[:, extra]], 1)
        self.leg_bones = [self.idx[f"{k}_{s}"] for s in SIDES for k in ("thigh", "shin", "foot")]
        self.thigh_bones = {self.idx[f"thigh_{s}"] for s in SIDES}
        # soft attachment under the blazer hem: trouser pixels close to the hem take the thigh rotation
        # gradually (over HEM_SOFT px), so the trousers stretch slightly under the hem instead of tearing away
        hem_any = (model.labels == RID["blazer_hem"]) | model.hem_override
        self.hem_dist = ndimage.distance_transform_edt(~hem_any).astype(np.float32) if hem_any.any() else None
        # trousers also attach softly to the pelvis-layer waistband / fly (the crotch seam)
        waist = hem_any | (model.labels == RID["sweater"]) & (np.arange(model.labels.shape[0])[:, None] > model.hem["top_y"])
        self.attach_dist = ndimage.distance_transform_edt(~waist).astype(np.float32) if waist.any() else None
        # hair sway zone: dilated so the silhouette edge and the resampling margin move with the hair they belong to
        self.hair_zone = ndimage.binary_dilation(model.labels == RID["hair"], iterations=8)

    def home(self, cid: str, pts: np.ndarray) -> np.ndarray:
        out = super().home(cid, pts)
        H, W = self.model.labels.shape
        xi = np.clip(np.rint(pts[:, 0]).astype(int), 0, W - 1)
        yi = np.clip(np.rint(pts[:, 1]).astype(int), 0, H - 1)
        cls = layer_class(cid)
        if cls in ("torso", "other") or cls.startswith("arm"):
            # body zones put everything above the neck-base plane on the neck; the shoulder tops lateral of the
            # neck belong to the shoulder girdle (chest + clavicle), or they stay behind when the shoulders drop
            lateral = np.abs(pts[:, 0] - self.midline_x) > 1.15 * self.model.anchors["neck_half_width_px"]
            hp, nb = self.bone("head").head[1], self.bone("neck").head[1]
            low = pts[:, 1] > hp + 0.4 * (nb - hp)  # shoulder height, below the jaw/ear fragments
            out[(out == self.idx["neck"]) & lateral & low] = self.idx["chest"]
        if cls == "pelvis":
            out[self.model.labels[yi, xi] == RID["blazer_hem"]] = self.HEM
        elif cls.startswith("leg"):
            out[self.model.hem_override[yi, xi]] = self.HEM
        elif cls.startswith("arm") and "__hs." in cid and cls[-1] in self.RIGID:
            out[:] = self.RIGID[cls[-1]]  # stored hand switch drawing: rigid on the hand bone
        return out

    def influence(self, b: int, pts: np.ndarray, home: np.ndarray) -> np.ndarray:
        hem = home == self.HEM
        f = super().influence(b, pts, self.vreal[home])
        rig = self._rigid[home]
        if rig.any():  # rigid hand drawings: the full transform of the hand bone and its ancestors, no blend band
            real = self.vreal[home[rig]]
            f[rig] = ((real == b) | self.desc[b][real]).astype(np.float32)
        if hem.any() and b in self.leg_bones:
            f[hem] = 0.0
        if b in self.thigh_bones and self.attach_dist is not None:
            own = home == b
            if own.any():
                H, W = self.attach_dist.shape
                xi = np.clip(np.rint(pts[own, 0]).astype(int), 0, W - 1)
                yi = np.clip(np.rint(pts[own, 1]).astype(int), 0, H - 1)
                f[own] = f[own] * smoothstep(0.0, HEM_SOFT, self.attach_dist[yi, xi])
        return f

    def hair_weight(self, pts: np.ndarray, top: float, bottom: float) -> np.ndarray:
        """Hair sways most at the top and not at all at the hairline (so it never separates from the face)."""
        H, W = self.model.labels.shape
        xi = np.clip(np.rint(pts[:, 0]).astype(int), 0, W - 1)
        yi = np.clip(np.rint(pts[:, 1]).astype(int), 0, H - 1)
        is_hair = self.hair_zone[yi, xi]
        return np.where(is_hair, 1.0 - smoothstep(top, bottom, pts[:, 1]), 0.0)

    def hem_weight(self, pts: np.ndarray, home: np.ndarray, top: float, bottom: float) -> np.ndarray:
        """Share of the hem's secondary motion at rest points: the hem itself by height, and the trousers just
        under it fading out over HEM_SOFT px, so the displacement field is continuous (no seam opens)."""
        w = smoothstep(top, bottom, pts[:, 1])
        if self.hem_dist is None:
            return np.where(home == self.HEM, w, 0.0)
        H, W = self.hem_dist.shape
        xi = np.clip(np.rint(pts[:, 0]).astype(int), 0, W - 1)
        yi = np.clip(np.rint(pts[:, 1]).astype(int), 0, H - 1)
        near = 1.0 - smoothstep(0.0, HEM_SOFT, self.hem_dist[yi, xi])
        return np.where(home == self.HEM, w, w * near)

    def set_bands(self, rp: np.ndarray, rc: np.ndarray) -> None:
        self.rp, self.rc = rp, rc


@dataclass
class GarmentTerm:
    """A garment-local rotation about a rest-pose pivot, applied before the skeleton (rest frame)."""
    name: str
    pivot: tuple[float, float]
    angle: float  # deg
    top: float  # weight 0 at/above this rest y
    bottom: float  # weight 1 at/below this rest y


class GarmentDeformationEngine:
    """Pose-dependent garment corrections: band widening per joint rotation and secondary hem motion."""

    def __init__(self, gsk: GarmentSkeleton, model: GarmentModel):
        self.sk, self.model = gsk, model

    def corrections(self, pose: PoseParams, garment_hints: dict[str, float] | None = None) -> dict[str, Any]:
        sk, th = self.sk, pose.theta
        rp, rc = sk.base_rp.copy(), sk.base_rc.copy()
        active = []
        for i, b in enumerate(sk.bones):
            rule = BAND_RULES.get(b.kind)
            if not rule:
                continue
            gain, full, region = rule
            rot = abs(th[i] + (th[sk.parent[i]] if b.kind == "upper_arm" else 0.0))
            k = 1.0 + gain * min(1.0, rot / full)
            if k > 1.02:
                cap = 0.48 * b.length
                if b.kind != "upper_arm":  # shoulder: widen on the sleeve side only (the torso side opens hairlines)
                    rp[i] = min(rp[i] * k, max(cap, sk.base_rp[i]))
                rc[i] = min(rc[i] * k, max(cap, sk.base_rc[i]))
                active.append({"rule": f"band:{b.name}", "region": region, "driver_deg": round(float(rot), 1),
                               "band_scale": round(float(k), 3)})
        terms = []
        lag = (garment_hints or {}).get("hem_lag_deg", 0.0)
        if abs(lag) > 0.05:
            h = self.model.hem
            terms.append(GarmentTerm("blazer_hem_lag", (h["pivot_x"], h["pivot_y"]), float(lag), h["top_y"],
                                     h["bottom_y"]))
            active.append({"rule": "secondary:blazer_hem_lag", "region": "blazer_hem", "angle_deg": round(float(lag), 2)})
        hair = (garment_hints or {}).get("hair_lag_deg", 0.0)
        hg = self.model.anchors.get("hair")
        if hg and abs(hair) > 0.05:
            top = hg["top_y"] + 0.15 * (hg["hairline_y"] - hg["top_y"])
            terms.append(GarmentTerm("hair_sway", (hg["pivot_x"], hg["pivot_y"]), float(hair), top, hg["hairline_y"]))
            active.append({"rule": "secondary:hair_sway", "region": "hair", "angle_deg": round(float(hair), 2)})
        return {"rp": rp, "rc": rc, "terms": terms, "active": active}


class ArtworkDeformationEngine(Deformer):
    """Phase 2 skinning deformer driven by the garment skeleton, with garment terms applied in the rest frame."""

    def __init__(self, gsk: GarmentSkeleton, layers, generated=None, reference=None, variants=None):
        self.terms: list[GarmentTerm] = []
        super().__init__(gsk, layers, generated, reference=reference, variants=variants)

    def compose(self, pts, home, pose, bones):
        if not self.terms:
            return Deformer.compose(self, pts, home, pose, bones)
        return self._compose_terms(pts, home, pose, bones)

    def _compose_terms(self, pts, home, pose, bones):
        # skeleton part without the root, then garment terms (rest frame), then the root
        n = len(pts)
        ident = PoseParams(pose.theta.copy(), pose.scale, np.zeros(2))
        ident.theta[0] = 0.0
        a, b_, c, d, tx, ty = Deformer.compose(self, pts, home, ident, bones)
        for t in self.terms:
            w = (self.sk.hair_weight(pts, t.top, t.bottom) if t.name == "hair_sway"
                 else self.sk.hem_weight(pts, home, t.top, t.bottom))
            if not w.any():
                continue
            ang = np.radians(t.angle) * w
            co, si = np.cos(ang), np.sin(ang)
            jx, jy = t.pivot
            r11, r12, r21, r22 = co, -si, si, co
            trx, try_ = jx - (r11 * jx + r12 * jy), jy - (r21 * jx + r22 * jy)
            a, b_, c, d, tx, ty = (a * r11 + b_ * r21, a * r12 + b_ * r22, c * r11 + d * r21, c * r12 + d * r22,
                                   a * trx + b_ * try_ + tx, c * trx + d * try_ + ty)
        R0 = self.root_matrix(pose)
        return (R0[0, 0] * a + R0[0, 1] * c, R0[0, 0] * b_ + R0[0, 1] * d, R0[1, 0] * a + R0[1, 1] * c,
                R0[1, 0] * b_ + R0[1, 1] * d, R0[0, 0] * tx + R0[0, 1] * ty + R0[0, 2],
                R0[1, 0] * tx + R0[1, 1] * ty + R0[1, 2])

    def render_with(self, pose: PoseParams, order: list[str], corr: dict[str, Any], pad: int = 0) -> dict[str, Any]:
        self.sk.set_bands(corr["rp"], corr["rc"])
        self.terms = corr["terms"]
        try:
            return self.render(pose, order, pad)
        finally:
            self.terms = []

    def tears_with(self, pose: PoseParams, render: dict[str, Any], corr: dict[str, Any], dark=None):
        self.sk.set_bands(corr["rp"], corr["rc"])
        self.terms = corr["terms"]
        try:
            return self.tears(pose, render, dark)
        finally:
            self.terms = []

    def masses(self) -> dict[int, tuple[float, np.ndarray]]:
        acc: dict[int, list] = {}
        for cid, inf in self.info.items():
            if cid in self.variants:
                continue
            x0, y0 = inf["origin"]
            ay, ax = np.nonzero(inf["alpha"])
            hb = self.sk.vreal[inf["home"][ay, ax]]
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

    def weight_overlay(self, pose: PoseParams, corr: dict[str, Any], pad: int, render: dict[str, Any]) -> np.ndarray:
        """Blend (deformation) weights: pixels moved by a fractional joint influence, at their posed positions."""
        self.sk.set_bands(corr["rp"], corr["rc"])
        H, W = render["image"].shape[:2]
        out = np.zeros((H, W, 4), np.uint8)
        bones = self.active_bones(pose)
        for cid in render["order"]:
            if cid not in self.info:
                continue
            inf = self.info[cid]
            x0, y0 = inf["origin"]
            ay, ax = np.nonzero(inf["alpha0"])
            if not len(ay):
                continue
            step = 2
            ay, ax = ay[::step], ax[::step]
            pts = np.stack([ax + x0, ay + y0], 1).astype(np.float64)
            home = inf["home"][ay, ax]
            frac = np.zeros(len(pts))
            for b in bones:
                f = self.sk.influence(b, pts, home)
                frac = np.maximum(frac, np.minimum(f, 1 - f) * 2)
            q = self.forward(cid, pts, pose)
            qx, qy = np.rint(q[:, 0] + pad).astype(int), np.rint(q[:, 1] + pad).astype(int)
            ok = (qx >= 0) & (qx < W) & (qy >= 0) & (qy < H) & (frac > 0.02)
            v = np.clip(frac[ok], 0, 1)
            out[qy[ok], qx[ok]] = np.stack([255 * v, 80 + 120 * (1 - v), 255 * (1 - v), np.full_like(v, 210)], 1)
        return ndimage.grey_dilation(out, size=(3, 3, 1))
