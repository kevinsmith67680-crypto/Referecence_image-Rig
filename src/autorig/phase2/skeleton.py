"""Anatomical 2D skeleton for natural posing (Phase 2 refinement).

Built from the Phase 1 joints and the original layer masks. It adds what the
first pose engine lacked: a spine (pelvis -> spine -> chest), clavicles for
shoulder movement, a neck/head split, and pivots refined against the artwork
(shoulder at the armpit line, joints re-centred on the limb's medial line).

Each pixel gets a *home bone*. This is decided by position (joint planes and
body zones), not by the layer it was cut into, so the jacket pixels that
Phase 1 put in the arm layer stay with the chest. Near a joint, the influence
`f_b(p)` blends smoothly between parent and child. Adjacent layers evaluate the
same function at their shared border, so they deform together and the straight
cut lines between layers never open.

Coordinates: source pixels, origin top-left, +y down. Angles: degrees in image
space, positive = clockwise on screen. Bone direction angles are measured from
straight down (0 = hanging), positive toward image left.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import ndimage

from ..phase1 import masks as M

HEAD_LAYERS = {"head_base", "hair", "nose", "mouth", "brow_r", "brow_l", "eye_r_white", "eye_r_iris",
               "eye_l_white", "eye_l_iris", "eye_r", "eye_l", "ear_r", "ear_l"}
SIDES = ("r", "l")
# image-rotation sign that moves a limb of this side laterally (character's right = image left)
SIDE_SIGN = {"r": 1.0, "l": -1.0}

# blend band per bone kind, in units of the local half-thickness h: (into parent, into child)
BAND = {"spine": (0.9, 0.9), "chest": (0.8, 0.8), "neck": (0.5, 0.6), "head": (0.25, 0.3),
        "clavicle": (0.4, 1.0), "upper_arm": (0.85, 1.0), "forearm": (0.6, 0.6), "hand": (0.3, 0.4),
        "thigh": (0.3, 0.7), "shin": (0.55, 0.55), "foot": (0.25, 0.3)}
RADIAL = {"clavicle": (1.6, 2.6), "spine": (1.6, 2.4), "chest": (1.6, 2.4)}  # default (1.25, 2.1)

# anatomical joint limits (degrees). Bone direction "abduction" is measured from straight down, positive
# lateral; "bend" is relative to the parent bone. Cartoon-friendly, but no hyperextension.
LIMITS: dict[str, dict[str, Any]] = {
    "pelvis": {"tilt": (-14, 14)},
    "spine": {"bend": (-18, 18)}, "chest": {"bend": (-14, 14)},
    "neck": {"bend": (-20, 20)}, "head": {"bend": (-28, 28)},
    "clavicle": {"elevation": (-10, 32)},
    "upper_arm": {"abduction": (-40, 178)},
    "forearm": {"flexion": (-150, 150)},  # 2D projection: both senses occur; |flex| <= 150 (no hyperextension)
    "hand": {"bend": (-60, 60)},
    "thigh": {"abduction": (-25, 75)},
    "shin": {"flexion": (0, 140), "valgus_max": 8},  # knee bends laterally in front view; knock-knee <= 8 deg
    "foot": {"bend": (-60, 60)},  # 2D front-view projection: a flat foot under a tilted shin is normal
}


def ang(v) -> float:
    """Direction angle in degrees from straight down; positive toward image left (clockwise)."""
    return math.degrees(math.atan2(-float(v[0]), float(v[1])))


def unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    n = float(np.hypot(*v))
    return v / n if n > 1e-9 else np.array([0.0, 1.0])


def smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / max(e1 - e0, 1e-6), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def layer_class(cid: str) -> str:
    cid = cid.split("__")[0]  # switch variants (e.g. hand_r__fist) behave like their base layer
    if cid in HEAD_LAYERS or cid.startswith(("eye_", "brow_", "ear_")):
        return "head"
    if cid in ("neck", "torso", "pelvis"):
        return cid
    for s in SIDES:
        if cid in (f"upper_arm_{s}", f"forearm_{s}", f"hand_{s}"):
            return f"arm_{s}"
        if cid in (f"thigh_{s}", f"shin_{s}", f"foot_{s}"):
            return f"leg_{s}"
    return "other"


@dataclass
class Bone:
    name: str
    parent: str | None
    kind: str
    side: str | None
    head: np.ndarray
    tail: np.ndarray
    h: float  # local half-thickness (px)
    method: str
    # anatomical axis minus the head->tail line (deg). Non-zero when a garment-aligned pivot moved the head off the
    # limb's axis (Phase 3.3): anatomical angles (abduction, flexion, rhythm) use the axis, IK geometry the line.
    axis_offset: float = 0.0

    @property
    def anat_angle(self) -> float:
        """Rest direction of the limb's anatomical axis (deg, as ``rest_angle``)."""
        return self.rest_angle + self.axis_offset

    @property
    def u(self) -> np.ndarray:
        return unit(self.tail - self.head)

    @property
    def length(self) -> float:
        return float(np.hypot(*(self.tail - self.head)))

    @property
    def rest_angle(self) -> float:
        return ang(self.tail - self.head)


def _extent(mask: np.ndarray, p, d, max_len: int = 600) -> int:
    """Consecutive mask pixels from p along direction d (0 if p itself is outside)."""
    h, w = mask.shape
    n = 0
    for k in range(max_len):
        x, y = int(round(p[0] + d[0] * k)), int(round(p[1] + d[1] * k))
        if not (0 <= y < h and 0 <= x < w) or not mask[y, x]:
            break
        n += 1
    return n


def _recentre(mask: np.ndarray, p, u) -> tuple[np.ndarray, float]:
    """Move p onto the middle of the limb cross-section perpendicular to u; returns (point, half-width)."""
    n = np.array([-u[1], u[0]])
    a, b = _extent(mask, p, n), _extent(mask, p, -n)
    if a + b < 3:
        return np.asarray(p, float), 0.0
    return np.asarray(p, float) + n * (a - b) / 2.0, (a + b) / 2.0


class Skeleton:
    def __init__(self, bones: list[Bone], ground_y: float, midline_x: float, notes: list[str]):
        self.bones = bones
        self.names = [b.name for b in bones]
        self.idx = {n: i for i, n in enumerate(self.names)}
        self.ground_y, self.midline_x, self.notes = ground_y, midline_x, notes
        N = len(bones)
        self.parent = np.array([self.idx[b.parent] if b.parent else -1 for b in bones])
        self.depth = np.zeros(N, int)
        for i in range(N):
            j, d = self.parent[i], 0
            while j >= 0:
                d, j = d + 1, self.parent[j]
            self.depth[i] = d
        self.anc = np.zeros((N, N), bool)  # anc[b, a]: a is a strict ancestor of b
        for i in range(N):
            j = self.parent[i]
            while j >= 0:
                self.anc[i, j] = True
                j = self.parent[j]
        self.desc = self.anc.T.copy()  # desc[b, d]: d is a strict descendant of b
        self.topo = list(np.argsort(self.depth, kind="stable"))
        self.J = np.array([b.head for b in bones], float)
        self.U = np.array([b.u for b in bones], float)
        self.rp = np.array([BAND.get(b.kind, (0.5, 0.5))[0] * b.h for b in bones])
        self.rc = np.array([BAND.get(b.kind, (0.5, 0.5))[1] * b.h for b in bones])
        self.R1 = np.array([RADIAL.get(b.kind, (1.25, 2.1))[0] * b.h for b in bones])
        self.R2 = np.array([RADIAL.get(b.kind, (1.25, 2.1))[1] * b.h for b in bones])
        self.zones: dict[str, Any] = {}

    def bone(self, name: str) -> Bone:
        return self.bones[self.idx[name]]

    def s(self, b: int, pts: np.ndarray) -> np.ndarray:
        return (pts[:, 0] - self.J[b, 0]) * self.U[b, 0] + (pts[:, 1] - self.J[b, 1]) * self.U[b, 1]

    # -- home bones ----------------------------------------------------------------------------
    def _body_zone(self, pts: np.ndarray) -> np.ndarray:
        I = self.idx
        out = np.full(len(pts), I["chest"], np.int16)
        out[self.s(I["chest"], pts) < 0] = I["spine"]
        out[self.s(I["spine"], pts) < 0] = I["pelvis"]
        # body-layer pixels above the neck base belong to the neck, above the head pivot to the head (Phase 1
        # can leave ear/jaw outline fragments in the torso layer; they must not stay behind on a head tilt)
        out[self.s(I["neck"], pts) > 0] = I["neck"]
        out[self.s(I["head"], pts) > 0] = I["head"]
        return out

    def home(self, cid: str, pts: np.ndarray) -> np.ndarray:
        """Home bone index for rest-pose points of layer ``cid``."""
        I, cls = self.idx, layer_class(cid)
        if cls == "head":
            return np.full(len(pts), I["head"], np.int16)
        if cls == "neck":
            out = np.full(len(pts), I["neck"], np.int16)
            out[self.s(I["neck"], pts) < 0] = I["chest"]
            return out
        if cls in ("torso", "other"):
            return self._body_zone(pts)
        if cls == "pelvis":
            return np.full(len(pts), I["pelvis"], np.int16)
        kind, side = cls.split("_")
        chain = ["upper_arm", "forearm", "hand"] if kind == "arm" else ["thigh", "shin", "foot"]
        chain = [f"{c}_{side}" for c in chain if f"{c}_{side}" in I]  # tolerate partial rigs
        if not chain:
            return self._body_zone(pts) if "chest" in I else np.zeros(len(pts), np.int16)
        out = np.full(len(pts), I[chain[0]], np.int16)
        for c in chain[1:]:
            out[self.s(I[c], pts) > 0] = I[c]
        before = self.s(I[chain[0]], pts) < 0
        if before.any():
            out[before] = self._body_zone(pts[before]) if kind == "arm" else I["pelvis"]
        return out

    # -- influence ------------------------------------------------------------------------------
    def influence(self, b: int, pts: np.ndarray, home: np.ndarray) -> np.ndarray:
        """Fraction of bone b's local rotation applied at rest points ``pts`` with home bones ``home``."""
        f = self.desc[b][home].astype(np.float32)
        own, anc = home == b, self.anc[b][home]
        if self.bones[b].kind == "clavicle" and anc.any():
            # shrug: body pixels follow by position - fully at the shoulder top, fading out below the
            # shoulder line - so the chest (badge, lapels) is never sheared by a hard zone boundary
            z = self.zones[self.bones[b].name]
            st = smoothstep(-self.rp[b], self.rc[b], self.s(b, pts))
            vf = 1.0 - smoothstep(z["fade_y0"], z["fade_y1"], pts[:, 1])
            f[anc] = (st * vf)[anc]
            anc = np.zeros_like(anc)
        if own.any() or anc.any():
            sb = self.s(b, pts)
            st = smoothstep(-self.rp[b], self.rc[b], sb)
            f[own] = st[own]
            if self.bones[b].kind == "thigh" and own.any():
                # the tops of both thighs meet at the crotch: near the midline they stay with the pelvis and
                # diverge gradually further down, so the fly/crotch outline does not tear
                side_x = -1.0 if self.bones[b].side == "r" else 1.0
                sep = abs(self.J[b, 0] - self.midline_x) or 1.0
                lat = smoothstep(0.0, 0.8 * sep, (pts[:, 0] - self.midline_x) * side_x)
                deep = smoothstep(self.rc[b], 3.0 * self.rc[b], sb)
                f[own] = (st * (lat + (1 - lat) * deep))[own]
            if anc.any():
                d = np.hypot(pts[:, 0] - self.J[b, 0], pts[:, 1] - self.J[b, 1])
                rad = 1.0 - smoothstep(self.R1[b], self.R2[b], d)
                if self.bones[b].kind == "thigh":
                    # the two hips pull the crotch in opposite directions: fade their pull on the pelvis
                    # toward the midline so the crotch stays put (the thighs close in underneath it)
                    side_x = -1.0 if self.bones[b].side == "r" else 1.0
                    sep = abs(self.J[b, 0] - self.midline_x) or 1.0
                    rad = rad * smoothstep(0.35 * sep, 1.0 * sep, (pts[:, 0] - self.midline_x) * side_x)
                f[anc] = (st * rad)[anc]
        return f

    def to_json(self) -> dict[str, Any]:
        return {"coordinate_system": "source pixels, origin top-left, +y down; angles deg, + = clockwise on screen",
                "ground_y": round(self.ground_y, 1), "midline_x": round(self.midline_x, 1),
                "bones": [{"name": b.name, "parent": b.parent, "kind": b.kind, "side": b.side,
                           "head": [round(float(v), 1) for v in b.head], "tail": [round(float(v), 1) for v in b.tail],
                           "length_px": round(b.length, 1), "rest_angle_deg": round(b.rest_angle, 1),
                           "axis_offset_deg": round(b.axis_offset, 2),
                           "half_thickness_px": round(b.h, 1), "pivot_method": b.method,
                           "blend_band_px": {"into_parent": round(float(self.rp[i]), 1),
                                             "into_child": round(float(self.rc[i]), 1)},
                           "radial_falloff_px": [round(float(self.R1[i]), 1), round(float(self.R2[i]), 1)],
                           "limits_deg": LIMITS.get(b.kind, {})} for i, b in enumerate(self.bones)],
                "zones": self.zones, "notes": self.notes}


def _thickness(mask: np.ndarray | None, a, b, lo=0.25, hi=0.65, default=30.0) -> float:
    if mask is None or not mask.any():
        return default
    dt = ndimage.distance_transform_edt(mask)
    vals = []
    for t in np.linspace(lo, hi, 9):
        p = np.asarray(a) + t * (np.asarray(b) - np.asarray(a))
        x, y = int(round(p[0])), int(round(p[1]))
        if 0 <= y < mask.shape[0] and 0 <= x < mask.shape[1]:
            vals.append(dt[y, x])
    return float(max(vals)) if vals and max(vals) > 2 else default


def build_skeleton(joints: dict[str, Any], masks: dict[str, np.ndarray]) -> Skeleton:
    """``joints``: Phase 1 anatomy joints ({name: {"position": [x, y] | None, "method": str}}).
    ``masks``: rest-pose alpha masks per layer (originals plus extensions)."""
    P = {n: (np.array(j["position"], float) if j.get("position") else None) for n, j in joints.items()}
    meth = {n: j.get("method", "") for n, j in joints.items()}
    shape = next(iter(masks.values())).shape
    sil = np.zeros(shape, bool)
    for m in masks.values():
        sil |= m
    get = lambda *names: _union(masks, names)
    notes: list[str] = []
    for req in ("hip_r", "hip_l", "neck"):
        if P.get(req) is None:
            raise ValueError(f"cannot build a skeleton without joint '{req}'")

    P0 = (P["hip_r"] + P["hip_l"]) / 2
    cx = float(P0[0])
    head_m, neck_m = get(*HEAD_LAYERS), get("neck")
    hb = M.bbox_xyxy(head_m) or [0, 0, 1, 1]
    head_h = hb[3] - hb[1]
    H = P["neck"] + np.array([0.0, -0.12 * head_h])  # head pivot: inside the lower head, above the chin
    if M.area(neck_m):
        nb = M.bbox_xyxy(neck_m)
        N = np.array([M.centroid(neck_m)[0], nb[3] - 0.25 * (nb[3] - nb[1])])
    else:
        N = P["neck"] + np.array([0.0, 0.2 * head_h])
    W = P0 + 0.33 * (N - P0)
    C = P0 + 0.66 * (N - P0)
    torso_m = get("torso", "pelvis")
    h_torso = _thickness(torso_m, W, C, 0.0, 1.0, 80.0)
    bones = [Bone("pelvis", None, "pelvis", None, P0, W, h_torso, "midpoint of hips"),
             Bone("spine", "pelvis", "spine", None, W, C, h_torso, "0.33 of pelvis->neck base"),
             Bone("chest", "spine", "chest", None, C, N, h_torso, "0.66 of pelvis->neck base"),
             Bone("neck", "chest", "neck", None, N, H, max(_thickness(neck_m, N, H, 0, 1, 25.0), 12.0),
                  "lower quarter of the neck layer"),
             Bone("head", "neck", "head", None, H, np.array([H[0], hb[1]], float), head_h / 2,
                  "head/neck contact raised by 12% of head height")]
    zones: dict[str, Any] = {}
    for s in SIDES:
        sign_x = -1.0 if s == "r" else 1.0  # image direction of the character's side
        arm_m = get(f"upper_arm_{s}", f"forearm_{s}", f"hand_{s}")
        S1, E1, Wr, Hd = (P.get(f"{n}_{s}") for n in ("shoulder", "elbow", "wrist", "hand"))
        if S1 is None or Wr is None:
            raise ValueError(f"arm {s}: shoulder and wrist are required")
        S, h_arm, smeth = _refine_shoulder(S1, Wr, sil, get(f"upper_arm_{s}"))
        # elbow: keep a detected bend, else the cartoon midpoint, then re-centre on the limb
        if E1 is not None and "bend" in meth.get(f"elbow_{s}", "") and "weak" not in meth.get(f"elbow_{s}", ""):
            E, emeth = E1, "phase1 bend"
        else:
            E, emeth = S + 0.52 * (Wr - S), "0.52 of shoulder->wrist (straight arm)"
        E, h_el = _recentre(get(f"upper_arm_{s}", f"forearm_{s}"), E, unit(Wr - S))
        Wr2, h_wr = _recentre(get(f"forearm_{s}", f"hand_{s}"), Wr, unit(Wr - E))
        Hd = Hd if Hd is not None else Wr2 + (Wr2 - E) * 0.3
        SC = np.array([cx + 0.3 * (S[0] - cx), S[1] - 0.25 * h_arm])
        zones[f"clavicle_{s}"] = {"sc_x": float(SC[0]), "fade_y0": float(S[1] - 0.2 * h_arm),
                                  "fade_y1": float(S[1] + 0.8 * h_arm)}
        bones += [Bone(f"clavicle_{s}", "chest", "clavicle", s, SC, S, h_arm, "30% from midline toward shoulder"),
                  Bone(f"upper_arm_{s}", f"clavicle_{s}", "upper_arm", s, S, E, h_arm, smeth),
                  Bone(f"forearm_{s}", f"upper_arm_{s}", "forearm", s, E, Wr2, max(h_el, 8.0), emeth),
                  Bone(f"hand_{s}", f"forearm_{s}", "hand", s, Wr2, np.asarray(Hd, float), max(h_wr, 8.0),
                       "phase1 wrist (arm/hand boundary), re-centred")]
        Hp, K1, A1, Ft = (P.get(f"{n}_{s}") for n in ("hip", "knee", "ankle", "foot"))
        if Hp is None or A1 is None:
            raise ValueError(f"leg {s}: hip and ankle are required")
        if K1 is not None and "bend" in meth.get(f"knee_{s}", "") and "weak" not in meth.get(f"knee_{s}", ""):
            K, kmeth = K1, "phase1 bend"
        else:
            K, kmeth = Hp + 0.5 * (A1 - Hp), "midpoint of hip->ankle (straight leg)"
        leg_m = get(f"thigh_{s}", f"shin_{s}")
        K, h_kn = _recentre(leg_m, K, unit(A1 - Hp))
        h_th = _thickness(get(f"thigh_{s}"), Hp, K, 0.2, 0.6, 40.0)
        Ft = Ft if Ft is not None else A1 + np.array([sign_x * 20.0, 40.0])
        bones += [Bone(f"thigh_{s}", "pelvis", "thigh", s, Hp, K, h_th, "phase1 hip"),
                  Bone(f"shin_{s}", f"thigh_{s}", "shin", s, K, A1, max(h_kn, 10.0), kmeth),
                  Bone(f"foot_{s}", f"shin_{s}", "foot", s, A1, np.asarray(Ft, float),
                       _thickness(get(f"foot_{s}"), A1, Ft, 0.2, 0.8, 25.0), "phase1 ankle (leg/foot contact)")]
    feet = get("foot_r", "foot_l")
    ground = float(np.nonzero(feet.any(axis=1))[0].max()) if feet.any() else float(shape[0] - 1)
    sk = Skeleton(bones, ground, cx, notes)
    sk.zones = zones
    return sk


def _union(masks, names) -> np.ndarray:
    shape = next(iter(masks.values())).shape
    out = np.zeros(shape, bool)
    for n in names:
        if n in masks:
            out |= masks[n]
    return out


def _refine_shoulder(S1, Wr, sil, upper_m) -> tuple[np.ndarray, float, str]:
    """Shoulder pivot for an abducted arm: march from the Phase 1 point toward the body until the
    silhouette below the arm's axis stops ending at the arm's underside (the armpit), then sit the
    pivot just lateral of it on the arm's medial line."""
    u = unit(np.asarray(Wr) - np.asarray(S1))  # toward the hand
    S, h = _recentre(upper_m if upper_m.any() else sil, S1 + u * 25, u)
    S = S - u * 25
    h = h or 30.0
    if abs(u[1]) >= 0.5:
        return np.asarray(S1, float), h, "phase1 (arm not abducted)"
    n = np.array([-u[1], u[0]])
    if n[1] < 0:
        n = -n  # armpit side = toward the feet
    t_arm = None
    for t in range(0, int(3 * h)):
        p = S - u * t
        if _extent(sil, p, n) > 1.7 * h:
            t_arm = t
            break
    if t_arm is None:
        return S, h, "phase1 shoulder re-centred (no armpit found)"
    shift = float(np.clip(t_arm - 0.45 * h, -1.5 * h, 1.5 * h))
    P = S - u * shift
    P, h2 = _recentre(sil & ~(_below_axis(sil.shape, P, n, h)), P + u * 0.3 * h, u)
    P = P - u * 0.3 * h
    return P, max(h, h2), f"armpit at {t_arm} px medial of phase1 point; pivot 0.45h lateral of it, on the arm axis"


def _below_axis(shape, p, n, h) -> np.ndarray:
    """Pixels farther than ~1.05h from the arm axis on the armpit side (excluded when re-centring)."""
    yy, xx = np.mgrid[:shape[0], :shape[1]]
    return ((xx - p[0]) * n[0] + (yy - p[1]) * n[1]) > 1.05 * h
