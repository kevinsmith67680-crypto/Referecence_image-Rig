"""Phase 4.2: native-geometry gap fills (pending GEOMETRIC_CORRECTION assets for the hybrid native rig).

The Phase 3.4 armpit gussets were baked on the Phase 3 garment deformer's geometry. On the rigid native rig they do
not fit: in simulation they *widen* the raised-arm gap (``docs/phase4_2_joint_deformation_audit.md``). This module
bakes gap fills on the native geometry itself, with the Phase 3.4 recipe:

- the gap is what a small closing fills near the joint in the hybrid pose but not at rest (the audit's hole metric);
- an open gap (touching the background) is closed by ``armpit_fill``: a quadratic curve tangent to the silhouette
  at both ends, the character's own fabric colour diffused from the neighbouring pixels, the measured outline;
- an enclosed crack (no background contact, e.g. along a lapel) gets the local fabric colour without an outline.

Keys are baked at fractions of each test pose (0.25 .. 1.0). In Moho they become a Switch layer (or one image layer
per key) bound to the static neighbour bone, *beneath* everything, selected by a Smart Bone action on the moving
bone. That selection is a manual Moho step (no Lua evidence for Smart Bone or Switch layer authoring); the
simulation applies the same rule. Assets are appended to the package's ``CorrectiveArtworkStore`` as pending; nothing
here approves anything, and no AI model is called.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from scipy import ndimage

from ..garmentrig.correctives import (CorrectiveArtworkStore, CorrectiveAsset, _crop, _fabric_colour, _outline_style,
                                      armpit_fill, local_fabric_fill)
from .hybrid import HLayer, Simulator, assemble, base_layers, poses_42
from .skeleton import NativeSkeletonSpec

FRACTIONS = (0.25, 0.5, 0.75, 1.0)
METHOD = "NativeJointGapFill"
# joints whose gaps are baked here; elbows and knees use the Phase 3.4 caps (they fit the native rig in simulation)
FILL_JOINTS = ("shoulder_l", "shoulder_r", "hip_l", "hip_r")
# The waist is not filled: the gap between the blazer's front panels shows the shirt / sweater under the blazer,
# whose colour cannot be measured (diffusion picks up the light lapel trim). It stays MISSING_ARTWORK, and the lean
# itself is a flexible-binding question (a manual Moho test), not a gap to paint over.
NOT_FILLED = {"waist": "hidden shirt / sweater under the blazer front: no measurable colour; needs a drawing"}
RAISED_RADIUS_FRAC = 1.3   # a raised-arm gusset is the armpit crotch within this many half-thicknesses of the pivot
RAISED_SAG = 0.18          # concave armpit curve (fraction of the chord) instead of a convex fill
ARMPIT_DEPTH_FRAC = 0.8    # the armpit curve meets the torso side and the sleeve this many half-thicknesses from the apex
CRACK_CONTACT_FRAC = 0.15  # a gap component touching the background on less than this share of its pixels is a crack
CATEGORY = {"shoulder": "shoulders", "hip": "hips", "waist": "torso"}
SUPERSEDES = ("Phase 3.4 / 3.5 gap fills (armpit gussets) were baked on the Phase 3 garment deformer's geometry and "
              "lie where the T-pose arm is: on the rigid native rig they show as stray fabric when the arm moves "
              "away (simulation: wings at the lowered shoulders, a wider raised-arm gap); the native fills replace "
              "them")


def asset_id(joint: str, pose_id: str) -> str:
    return f"native_gap_{joint}__{pose_id}"


def driver_name(bone: str) -> str:
    return f"bone_offset:{bone}"


def _premult(rgba: np.ndarray) -> np.ndarray:
    img = rgba.astype(np.float32) / 255.0
    img[..., :3] *= img[..., 3:4]
    return img


def _to_rgba8(out: np.ndarray) -> np.ndarray:
    a = out[..., 3:4]
    rgb = np.where(a > 1e-6, out[..., :3] / np.maximum(a, 1e-6), 0.0)
    return np.clip(np.rint(np.concatenate([rgb, a], -1) * 255.0), 0, 255).astype(np.uint8)


def _disk(r: int) -> np.ndarray:
    y, x = np.mgrid[-r:r + 1, -r:r + 1]
    return x * x + y * y <= r * r


class NativeGapFillGenerator:
    def __init__(self, pkg: str | Path, sk: NativeSkeletonSpec | None = None):
        from .deform_audit import JOINTS, THRESHOLDS, _half_thickness  # noqa: PLC0415

        self.pkg = Path(pkg)
        self.sk = sk or NativeSkeletonSpec.from_package(self.pkg)
        import json  # noqa: PLC0415

        self.rig = json.loads((self.pkg / "natural_pose/rig.json").read_text(encoding="utf-8"))
        self.base = base_layers(self.pkg, self.sk)
        self.sim = Simulator(self.sk, rig=self.rig)
        self.layers = assemble(self.pkg, self.sk, "hybrid", self.base)
        self.joints, self.th, self._ht = JOINTS, THRESHOLDS, _half_thickness
        src = np.zeros_like(self.base[0][0].rgba)
        for l in self.base[0]:
            a = l.rgba[..., 3:4].astype(np.float32) / 255.0
            src = (l.rgba * a + src * (1 - a)).astype(np.uint8)
        self.source = src

    def _mask(self, bone_ids: set[str]) -> np.ndarray:
        m = np.zeros(self.source.shape[:2], bool)
        for l in self.base[0]:
            if l.bone in bone_ids:
                m |= l.rgba[..., 3] > 0
        return m

    def recipe(self, joint: str) -> dict[str, Any]:
        bone, neighbours, _ = self.joints[joint]
        fabric = self._mask({bone}) if joint != "waist" else self._mask({"CHEST"})
        around = fabric | self._mask(set(neighbours))
        outline, width = _outline_style(self.source, around)
        fill = _fabric_colour(self.source, fabric)
        return {"fill_rgb": [round(float(v)) for v in fill], "outline_rgb": [round(float(v)) for v in outline],
                "outline_width_px": width, "shade_min": 0.85, "max_bulge": 0.45}

    def gap(self, joint: str, pose: dict[str, Any], radius: float | None = None
            ) -> tuple[np.ndarray, np.ndarray, tuple[float, float]]:
        """(posed image as premultiplied float, gap mask, joint pivot px) on the simulator canvas."""
        bone = self.joints[joint][0]
        ht = self._ht(self.sk, self.rig, bone)
        half = int(max(60, self.th["window_frac"] * ht))
        r = int(min(22, max(3, self.th["closing_frac"] * ht)))
        from ..geometry import world_to_px  # noqa: PLC0415

        w, h = self.sim.placement.canvas
        world = self.sim.world(pose)
        rest_world = self.sim.world({"offsets_deg": {}})
        img = self.sim.render(self.layers, pose)
        rest = self.sim.render(self.layers, {"offsets_deg": {}})
        J = world_to_px(world[bone][0], w, h)
        J0 = world_to_px(rest_world[bone][0], w, h)
        gap = np.zeros(img.shape[:2], bool)
        for frame_img, centre, sign in ((img, J, 1), (rest, J0, -1)):
            fig = frame_img[..., 3] > 127
            x, y = int(round(centre[0])), int(round(centre[1]))
            sl = (slice(max(0, y - half), y + half), slice(max(0, x - half), x + half))
            holes = ndimage.binary_closing(fig[sl], _disk(r)) & ~fig[sl]
            if sign > 0:
                gap[sl] |= holes
            else:
                gap[sl] &= ~holes  # gaps that already exist at rest (e.g. between the arm and the torso) stay open
        if radius is not None:
            yy, xx = np.mgrid[:gap.shape[0], :gap.shape[1]]
            gap &= (xx - J[0]) ** 2 + (yy - J[1]) ** 2 <= radius * radius
        return _premult(img), gap, J

    def fill(self, image: np.ndarray, gap: np.ndarray, anchor, params: dict[str, Any]) -> np.ndarray | None:
        """Fill every gap component: open ones with the curve-bounded gusset, enclosed cracks with local fabric."""
        opaque = image[..., 3] > 0.5
        lab, n = ndimage.label(gap)
        out = np.zeros(image.shape, np.float32)
        outside = ~ndimage.binary_fill_holes(opaque | gap)
        for i in range(1, n + 1):
            comp = lab == i
            if comp.sum() < 12:
                continue
            contact = int((comp & ndimage.binary_dilation(outside, iterations=2)).sum())
            if contact >= max(6, CRACK_CONTACT_FRAC * comp.sum()):
                res = armpit_fill(image, comp, anchor, params)
                if res is None:
                    continue
                piece = res[0]
            else:
                region = ndimage.binary_dilation(comp, iterations=1) & ~opaque
                rgb = local_fabric_fill(image, opaque, region, np.asarray(params["fill_rgb"], np.float32))
                piece = np.zeros(image.shape, np.float32)
                piece[..., 3] = region
                piece[..., :3] = rgb / 255.0 * region[..., None]
            a = piece[..., 3:4]
            out = piece + out * (1 - a)  # components do not overlap; keep any earlier one
        return out if (out[..., 3] > 0).any() else None

    def armpit(self, image: np.ndarray, gap: np.ndarray, J, params: dict[str, Any], depth: float
               ) -> np.ndarray | None:
        """A raised-arm armpit: the apex of the opened wedge, closed by a concave curve between the torso side and
        the sleeve underside at ``depth`` px from the apex (sagging toward it), fabric-filled, outlined along the
        curve with the character's outline. The rest of the wedge stays open: under a raised arm there is space."""
        opaque = image[..., 3] > 0.5
        lab, n = ndimage.label(gap)
        if n == 0:
            return None
        sizes = ndimage.sum(gap, lab, range(1, n + 1))
        g = lab == (1 + int(np.argmax(sizes)))
        ys, xs = np.nonzero(g)
        d2 = (xs - J[0]) ** 2 + (ys - J[1]) ** 2
        A = np.array([xs[np.argmin(d2)], ys[np.argmin(d2)]], float)          # the apex: gap pixel nearest the pivot
        edge = opaque & ndimage.binary_dilation(g, iterations=2)
        ey, ex = np.nonzero(edge)
        dist = np.hypot(ex - A[0], ey - A[1])
        ring = np.abs(dist - depth) < 3.0
        if ring.sum() < 2:
            return None
        P = np.stack([ex[ring], ey[ring]], 1).astype(float)
        i = int(np.argmax(np.hypot(*(P - P.mean(0)).T)))
        j = int(np.argmax(np.hypot(*(P - P[i]).T)))
        T, S = P[i], P[j]
        chord = float(np.hypot(*(S - T)))
        if chord < 8:
            return None
        mid = (T + S) / 2
        v = A - mid
        C = mid + v / max(float(np.hypot(*v)), 1e-6) * float(params.get("sag", RAISED_SAG)) * chord
        t = np.linspace(0, 1, max(24, int(chord)))[:, None]
        B = (1 - t) ** 2 * T + 2 * (1 - t) * t * C + t ** 2 * S
        from PIL import Image as _I, ImageDraw as _D  # noqa: PLC0415

        H, W = gap.shape
        poly = _I.new("L", (W, H), 0)
        _D.Draw(poly).polygon([tuple(A)] + [tuple(p) for p in B], fill=1)
        region = np.asarray(poly, bool) & ~opaque
        region = ndimage.binary_closing(region, iterations=1) & ~opaque
        if region.sum() < 12:
            return None
        curve = np.zeros((H, W), bool)
        bi = np.clip(np.rint(B).astype(int), 0, [W - 1, H - 1])
        curve[bi[:, 1], bi[:, 0]] = True
        dc = ndimage.distance_transform_edt(~curve)
        wd = float(params.get("outline_width_px", 3.0))
        k_out = np.clip(wd / 2 + 0.5 - dc, 0, 1) * ndimage.binary_dilation(region, iterations=int(wd) + 1)
        fill = local_fabric_fill(image, opaque, region, np.asarray(params["fill_rgb"], np.float32))
        yy, xx = np.mgrid[:H, :W]
        dA = np.hypot(xx - A[0], yy - A[1])
        shade = float(params.get("shade_min", 0.85)) + (1 - float(params.get("shade_min", 0.85))) * np.clip(dA / depth, 0, 1)
        rgb = fill * shade[..., None]
        outline = np.asarray(params["outline_rgb"], np.float32)
        rgb = rgb * (1 - k_out[..., None]) + outline[None, None] * k_out[..., None]
        a = np.maximum(region.astype(np.float32), k_out)
        out = np.zeros((H, W, 4), np.float32)
        out[..., :3] = rgb / 255.0 * a[..., None]
        out[..., 3] = a
        return out

    def generate(self, joint: str, pose_id: str) -> tuple[CorrectiveAsset, dict[str, np.ndarray]] | None:
        bone, neighbours, _ = self.joints[joint]
        pose = next(p for p in poses_42(self.sk) if p["id"] == pose_id)
        full = pose["offsets_deg"].get(bone)
        if not full:
            return None
        params = self.recipe(joint)
        raised = joint.startswith("shoulder") and "raised" in pose_id
        radius = None
        if raised:
            radius = RAISED_RADIUS_FRAC * self._ht(self.sk, self.rig, bone)
            params = {**params, "sag": RAISED_SAG}
        ox, oy = self.sim.placement.offset
        attach = neighbours[0] if joint.startswith("shoulder") else "PELVIS"
        side = joint.split("_")[1] if "_" in joint else None
        drv = driver_name(bone)
        asset = CorrectiveAsset(
            asset_id=asset_id(joint, pose_id), category=CATEGORY[joint.split("_")[0]], body_part=joint, side=side,
            method=METHOD, provenance="GEOMETRIC_CORRECTION", attach_bone=attach,
            placement={"mode": "gap_fill_beneath", "native": True},
            drivers=[drv],
            activation={"kind": "ramp", "driver": drv, "lo": 0.05, "hi": 0.15, "direction": 1.0 if full > 0 else -1.0,
                        "full_deg": abs(float(full)), "unit": "fraction of the test pose's rotation of the bone"},
            target_pose={"pose": pose_id, "bone": bone, "offset_deg": float(full)},
            compatible_layers=[l.name for l in self.layers if l.bone in (bone, attach)],
            params={**params, "native": True, "fractions": list(FRACTIONS), "attach_native_bone": attach},
            source_refs=["native rigid geometry (hybrid rig: split + order), simulated",
                         "the character's own pixels near the gap (fabric colour, outline colour and width)"],
            moho={"class": "switch_by_smart_bone", "mapping": f"one image per key under a Switch layer bound to "
                  f"{attach}, beneath every layer; a Smart Bone action on {bone} selects the key "
                  f"({', '.join(f'{f:g}' for f in FRACTIONS)} of {full:+.0f} deg)", "status": "proposed_unverified"},
            requirement_id=f"REQ-native-{joint}-{pose_id}", character=self.sk.character_id)
        images: dict[str, np.ndarray] = {}
        for f in FRACTIONS:
            kp = {"offsets_deg": {k: v * f for k, v in pose["offsets_deg"].items()}, "pos_offset_px": {}}
            image, gap, J = self.gap(joint, kp, radius)
            if not gap.any():
                continue
            if raised:
                out = None
                for k in (1.0, 0.75, 0.5):  # the first depth at which both sides of the wedge are found
                    out = self.armpit(image, gap, J, params, k * ARMPIT_DEPTH_FRAC * self._ht(self.sk, self.rig, bone))
                    if out is not None:
                        break
            else:
                out = self.fill(image, gap, J, params)
            if out is None:
                continue
            rgba = _to_rgba8(out)
            crop, origin = _crop(rgba)
            key_id = f"k{int(round(f * 100)):03d}"
            images[key_id] = crop
            asset.keys.append({"key_id": key_id, "values": {drv: float(f)}, "offset_deg": round(full * f, 3),
                               "origin": [origin[0] - ox, origin[1] - oy], "bind_matrix": np.eye(3).tolist(),
                               "gap_fill_px": int((rgba[..., 3] > 127).sum()), "gap_px": int(gap.sum()),
                               "role": "native bake (review + Moho switch candidate)"})
        return (asset, images) if images else None

    def plan(self) -> list[tuple[str, str]]:
        out = []
        for joint in FILL_JOINTS:
            bone = self.joints[joint][0]
            for p in poses_42(self.sk):
                if joint in self.joints and p["frame"] in self.joints[joint][2] and p["offsets_deg"].get(bone):
                    out.append((joint, p["id"]))
        return out


def generate_native_fills(pkg: str | Path, regenerate: bool = False, progress=print) -> list[dict[str, Any]]:
    """Append the native gap fills to the package's corrective store (pending). Existing assets are kept unless
    ``regenerate`` (which adds a new version; the store is append-only)."""
    store = CorrectiveArtworkStore(pkg)
    have = {a.asset_id for a in store.latest()}
    gen = NativeGapFillGenerator(pkg)
    out = []
    for joint, pose_id in gen.plan():
        aid = asset_id(joint, pose_id)
        if aid in have and not regenerate:
            out.append({"asset": aid, "status": "kept"})
            continue
        res = gen.generate(joint, pose_id)
        if res is None:
            out.append({"asset": aid, "status": "no gap"})
            continue
        a = store.add(*res)
        progress(f"  {a.asset_id}@v{a.version}: {len(a.keys)} keys (pending review)")
        out.append({"asset": aid, "status": "added", "version": a.version, "keys": [k["key_id"] for k in a.keys]})
    return out


def native_corrective_layers(layers: list[HLayer]) -> list[HLayer]:
    return [l for l in layers if l.extra.get("category") and l.status.endswith("corrective")]
