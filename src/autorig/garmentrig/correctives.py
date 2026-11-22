"""Phase 3.4 corrective artwork: requirements, a versioned store, original-pixel-safe generators and pose-space
activation.

* ``CorrectiveArtworkStore``: corrective assets live in ``<package>/artwork/correctives/<category>/`` as versioned
  PNGs (``<asset_id>@v<N>.png``, never overwritten), with an index in
  ``<package>/analysis/corrective_assets.json``. Each record holds:
  - asset id, character, body part, trigger and pose-space keys;
  - attachment bone and bind transform, layer placement;
  - generation method and parameters, source references, provenance, version.
  Requirements go to ``analysis/corrective_requirements.json`` and keys to ``analysis/pose_space_correctives.json``.
* Provenance states: ORIGINAL, GEOMETRIC_CORRECTION, AI_GENERATED, ARTIST_EDITED, APPROVED, REJECTED. Approval is a
  user action in the editor project (``approval.set`` on ``corrective:<asset_id>@v<N>``); nothing here approves
  anything. Pending assets are only *previewed* (``allow_pending``), as for every other generated drawing.
* Generators build artwork from the character's own pixels: geometry, sampled colours, the measured outline. They
  draw only *beneath* the layers they complete, so original visible pixels are never covered:
  - ``ArmpitCorrectionGenerator``: keyed armpit gussets beneath the torso and the sleeve;
  - ``ShoeTopRecovery``: the shoe's top under the trouser hem, in the foot layer's place;
  - ``JointCapGenerator``: round elbow and knee caps beneath the rigid segments, above the joint patch;
  - ``EdgeDecontamination``: removes the white-paper anti-aliasing from layer edges on derived layers only. Over
    white, the result equals the source.
* ``PoseSpaceCorrectionEngine``: activation from several pose readings (shoulder, elbow, clavicle, torso, hip,
  knee). It uses eased ramps for single assets and normalised Gaussian RBF weights over sparse keys for keyed sets
  (the two strongest keys are drawn, to avoid ghosting). It also handles priorities and exclusive groups, flags
  extrapolation as an unsupported pose combination, and keeps every weight at 0 in the neutral pose.
* ``CorrectiveArtworkPlanner``: turns evaluator and render findings into requirement records. It decides between
  reuse, layer order, pivot, mesh, geometric extension, replacement drawing, AI inpainting and manual artwork,
  preferring the cheapest correction that can work. It never invents artwork and never calls a generative model.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage

from ..phase2.skeleton import SIDES, smoothstep

PROVENANCE = ("ORIGINAL", "GEOMETRIC_CORRECTION", "AI_GENERATED", "ARTIST_EDITED", "APPROVED", "REJECTED")
CATEGORIES = ("shoulders", "armpits", "elbows", "sleeves", "hips", "knees", "shoes", "torso", "edges")
CORRECTION_TYPES = ("reuse_existing", "layer_order", "pivot_adjustment", "local_mesh", "geometric_extension",
                    "replacement_drawing", "ai_inpainting", "manual_artwork")
INDEX = "analysis/corrective_assets.json"
# earlier requirement names (Phase 2/3.3 feasibility) that a Phase 3.4 asset answers (still subject to its review)
ANSWERS = {"corrective:foot_extension_under_hem_r": ["shoe_top_r"], "corrective:foot_extension_under_hem_l": ["shoe_top_l"],
           "corrective:gusset": ["armpit_gusset_r", "armpit_gusset_l"]}


def answered_by(asset_name: str, assets: list["CorrectiveAsset"], approvals: dict[str, Any]) -> list[dict[str, Any]]:
    """The Phase 3.4 assets that answer an earlier requirement name (``corrective:gusset__<preset>__<side>`` and
    ``corrective:<asset>``), with their approval state; empty if none."""
    key = asset_name.split("__")[0]
    ids = ANSWERS.get(key, [])
    if asset_name.startswith("corrective:gusset__") and asset_name[-2:] in ("_r", "_l"):
        ids = [f"armpit_gusset{asset_name[-2:]}"]
    return [{"asset_id": a.asset_id, "approval_key": a.approval_key, "state": approval_state(approvals, a)}
            for a in assets if a.asset_id in ids]


@dataclass
class CorrectiveAsset:
    asset_id: str
    category: str
    body_part: str
    side: str | None
    method: str  # generator name
    provenance: str  # GEOMETRIC_CORRECTION | AI_GENERATED | ARTIST_EDITED
    attach_bone: str
    placement: dict[str, Any]  # {"mode": "before"|"after_layer"|"after_patches", "layer"|"group": id}
    drivers: list[str]  # pose readings that activate it
    activation: dict[str, Any]  # {"kind": "ramp", "driver", "lo", "hi"} | {"kind": "keyed", "group": ...}
    keys: list[dict[str, Any]] = field(default_factory=list)  # keyed variants: {"key_id", "values", "file", ...}
    file: str | None = None  # single-image assets
    origin: list[float] = field(default_factory=lambda: [0.0, 0.0])  # crop origin in the bind frame (source px)
    bind_matrix: list[list[float]] = field(default_factory=lambda: np.eye(3).tolist())
    target_pose: dict[str, Any] = field(default_factory=dict)
    compatible_layers: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    source_refs: list[str] = field(default_factory=list)
    requirement_id: str | None = None
    priority: int = 2
    exclusive_group: str | None = None
    moho: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    character: str = ""
    created: str = ""
    sha256: dict[str, str] = field(default_factory=dict)

    @property
    def approval_key(self) -> str:
        return f"corrective:{self.asset_id}@v{self.version}"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class CorrectiveArtworkStore:
    def __init__(self, pkg: str | Path):
        self.pkg = Path(pkg)
        self.root = self.pkg / "artwork" / "correctives"
        self.index_path = self.pkg / INDEX
        self.index = json.loads(self.index_path.read_text()) if self.index_path.is_file() else {
            "schema": "autorig.corrective_assets", "version": "1.0.0", "assets": []}

    # assets are append-only: a new version never replaces an older file
    def add(self, asset: CorrectiveAsset, images: dict[str, np.ndarray]) -> CorrectiveAsset:
        prev = [a for a in self.index["assets"] if a["asset_id"] == asset.asset_id]
        asset.version = 1 + max((a["version"] for a in prev), default=0)
        asset.created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        d = self.root / asset.category
        d.mkdir(parents=True, exist_ok=True)
        for name, rgba in images.items():
            f = d / f"{asset.asset_id}{'' if name == 'main' else '__' + name}@v{asset.version}.png"
            if f.exists():
                raise FileExistsError(f"corrective file exists: {f}")
            Image.fromarray(rgba, "RGBA").save(f)
            rel = str(f.relative_to(self.pkg))
            asset.sha256[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
            if name == "main":
                asset.file = rel
            else:
                for k in asset.keys:
                    if k["key_id"] == name:
                        k["file"] = rel
        self.index["assets"].append(asset.to_json())
        self.save()
        return asset

    def save(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(self.index, indent=1))

    def latest(self) -> list[CorrectiveAsset]:
        best: dict[str, dict] = {}
        for a in self.index["assets"]:
            if a["asset_id"] not in best or a["version"] > best[a["asset_id"]]["version"]:
                best[a["asset_id"]] = a
        return [CorrectiveAsset(**a) for a in best.values()]

    def image(self, rel: str) -> np.ndarray:
        return np.asarray(Image.open(self.pkg / rel).convert("RGBA"))

    def verify(self) -> list[str]:
        bad = []
        for a in self.index["assets"]:
            for rel, h in a.get("sha256", {}).items():
                f = self.pkg / rel
                if not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != h:
                    bad.append(rel)
        return bad

    def write_json(self, rel: str, doc: Any) -> Path:
        f = self.pkg / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(doc, indent=1, default=_js))
        return f


def _js(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def approval_state(approvals: dict[str, Any], asset: CorrectiveAsset) -> str:
    st = (approvals or {}).get(asset.approval_key, {})
    return st.get("status", "pending_review") if isinstance(st, dict) else "pending_review"


# -- palette helpers (measured on the character's own pixels) ----------------------------------------------------
def _outline_style(source: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, float]:
    """Outline colour (median of the dark ring) and width (px) along a region's silhouette."""
    ring = mask & ~ndimage.binary_erosion(mask, iterations=6)
    px = source[..., :3][ring].astype(np.float32)
    lum = px @ np.array([0.299, 0.587, 0.114], np.float32)
    dark = px[lum < 60]
    col = np.median(dark, 0) if len(dark) else np.array([20, 20, 24], np.float32)
    edge = mask & ~ndimage.binary_erosion(mask)
    dt = ndimage.distance_transform_edt(mask)
    lum_img = source[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    widths = []
    ys, xs = np.nonzero(edge)
    for y, x in list(zip(ys, xs))[:: max(1, len(ys) // 200)]:
        n = 0
        for k in range(1, 9):  # walk inward while dark
            yy, xx = int(y), int(x)
            win = (slice(max(0, yy - k), yy + k + 1), slice(max(0, xx - k), xx + k + 1))
            if not (mask[win] & (dt[win] == k) & (lum_img[win] < 70)).any():
                break
            n = k
        widths.append(n)
    w = float(np.median(widths)) if widths else 3.0
    return col.astype(np.float32), float(np.clip(w, 2.0, 6.0))


def _fabric_colour(source: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Median fabric colour of a region (outline and highlights excluded)."""
    px = source[..., :3][ndimage.binary_erosion(mask, iterations=6)].astype(np.float32)
    if not len(px):
        px = source[..., :3][mask].astype(np.float32)
    lum = px @ np.array([0.299, 0.587, 0.114], np.float32)
    lo, hi = np.percentile(lum, [25, 75]) if len(lum) else (0, 255)
    sel = (lum >= lo) & (lum <= hi)
    return np.median(px[sel], 0).astype(np.float32) if sel.any() else np.array([40, 40, 60], np.float32)


def _paint_region(shape, region: np.ndarray, fill: np.ndarray, outline: np.ndarray, width: float,
                  outline_mask: np.ndarray | None = None, shade: np.ndarray | None = None) -> np.ndarray:
    """RGBA (uint8) of a region: fabric fill (optionally shaded), outline of ``width`` px along ``outline_mask``
    (the part of the region's border that faces the background), antialiased."""
    H, W = shape
    out = np.zeros((H, W, 4), np.float32)
    rgb = np.broadcast_to(fill, (H, W, 3)).astype(np.float32).copy()
    if shade is not None:
        rgb *= shade[..., None]
    if outline_mask is not None and outline_mask.any():
        d = ndimage.distance_transform_edt(~outline_mask)
        k = np.clip(width + 0.5 - d, 0.0, 1.0)
        rgb = rgb * (1 - k[..., None]) + outline[None, None, :] * k[..., None]
    soft = ndimage.gaussian_filter(region.astype(np.float32), 0.6)
    a = np.clip(np.maximum(soft, region * 1.0), 0, 1)
    out[..., :3] = rgb
    out[..., 3] = a
    out[~(a > 0)] = 0
    return np.clip(np.rint(out * [1, 1, 1, 255]), 0, 255).astype(np.uint8)


def _crop(rgba: np.ndarray, margin: int = 2) -> tuple[np.ndarray, list[float]]:
    a = rgba[..., 3] > 0
    if not a.any():
        return rgba[:1, :1], [0.0, 0.0]
    ys, xs = np.nonzero(a)
    y0, x0 = max(0, ys.min() - margin), max(0, xs.min() - margin)
    y1, x1 = min(rgba.shape[0], ys.max() + margin + 1), min(rgba.shape[1], xs.max() + margin + 1)
    return rgba[y0:y1, x0:x1].copy(), [float(x0), float(y0)]


# -- generators ------------------------------------------------------------------------------------------------
class ShoeTopRecovery:
    """The shoe's top hidden under the trouser hem, reconstructed conservatively from the shoe's own pixels: each
    column of the shoe's visible top boundary is continued upward under the trouser (up to ``depth``), with the
    colour of the shoe just below the boundary, darkening toward the opening, and the shoe's side outline
    continued. It is attached to the foot, and it
    only adds pixels where the foot layer is empty. It is drawn just beneath the foot layer (which the trouser
    covers in turn), so no original pixel is ever covered."""

    def __init__(self, depth_frac: float = 0.42):
        self.depth_frac = depth_frac

    def generate(self, eng, side: str) -> tuple[CorrectiveAsset, dict[str, np.ndarray]] | None:
        src, masks, gen = eng.source, eng.masks, eng.generated
        foot = masks[f"foot_{side}"] & ~gen.get(f"foot_{side}", np.zeros_like(masks[f"foot_{side}"]))
        trouser = np.zeros_like(foot)
        for c in (f"shin_{side}", f"thigh_{side}"):
            if c in masks:
                trouser |= masks[c] & ~gen.get(c, np.zeros_like(foot))
        if not foot.any() or not trouser.any():
            return None
        ys, xs = np.nonzero(foot)
        depth = int(round(self.depth_frac * (ys.max() - ys.min())))
        H, W = foot.shape
        region = np.zeros((H, W), bool)
        colour = np.zeros((H, W, 3), np.float32)
        lum = src[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
        cols = []
        for x in range(xs.min(), xs.max() + 1):
            fy = np.nonzero(foot[:, x])[0]
            if not len(fy):
                continue
            y_top = int(fy.min())
            if y_top < 1 or not trouser[y_top - 1, x]:
                continue  # the shoe's top is visible here (not under the trouser)
            band = src[y_top + 2:y_top + 10, x, :3].astype(np.float32)
            bl = lum[y_top + 2:y_top + 10, x]
            fabric = band[(bl > 25) & (bl < 160)]
            light = band[bl >= 160]
            # Phase 3.8: the shoe's own dominant tone. A dark shoe (the schoolboy) keeps the dark rule; a light shoe
            # (white trainers) was sampled from its laces and outline only and drawn dark grey under the hem
            if len(light) > len(fabric):
                fabric = light
            c = np.median(fabric, 0) if len(fabric) else np.array([35, 33, 36], np.float32)
            y0 = max(0, y_top - depth)
            hidden = trouser[y0:y_top, x]
            region[y0:y_top, x] = hidden
            for k, y in enumerate(range(y0, y_top)):
                colour[y, x] = c * (0.78 + 0.22 * (y - y0) / max(1, y_top - y0))  # darker toward the opening
            cols.append(x)
        if not region.any():
            return None
        region = ndimage.binary_opening(region, iterations=1)
        region = ndimage.binary_closing(region, iterations=2) & trouser
        outline, width = _outline_style(src, foot)
        side_edges = np.zeros_like(region)
        cs = np.nonzero(region.any(0))[0]
        if len(cs):
            for x in (cs.min(), cs.max()):  # continue the shoe's side outlines upward
                side_edges[:, x] |= region[:, x]
        side_edges = ndimage.binary_dilation(side_edges, iterations=int(width) - 1) & region
        H2, W2 = region.shape
        rgba = np.zeros((H2, W2, 4), np.uint8)
        rgb = colour.copy()
        rgb[side_edges] = outline
        rgba[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
        rgba[..., 3] = (region * 255).astype(np.uint8)
        crop, origin = _crop(rgba)
        asset = CorrectiveAsset(
            asset_id=f"shoe_top_{side}", category="shoes", body_part=f"shoe_{side}", side=side,
            method="ShoeTopRecovery", provenance="GEOMETRIC_CORRECTION", attach_bone=f"foot_{side}",
            placement={"mode": "before", "layer": f"foot_{side}"}, drivers=[f"ankle_{side}"],
            activation={"kind": "always"}, origin=origin, target_pose={"pose": "any (static extension)"},
            compatible_layers=[f"foot_{side}"], priority=1,
            params={"depth_px": depth, "columns": len(cols), "outline_width_px": width,
                    "outline_rgb": [round(float(v)) for v in outline]},
            source_refs=[f"layer:foot_{side} (visible top band)", f"layer:shin_{side} (hiding region)"],
            moho={"class": "direct", "mapping": "extra image layer bound to the foot bone, under the trouser",
                  "status": "proposed_unverified"},
            requirement_id=f"REQ-shoe_top_{side}", character=eng.profile.view)
        return asset, {"main": crop}


class JointCapGenerator:
    """A round joint cap (elbow or knee): a disc of the garment's fabric colour, centred on the joint pivot with
    the radius of the limb's cross-section at the joint plane, outlined with the character's outline. It is drawn
    beneath both rigid segments and above the joint patch, so it only shows in the wedge that opens at the outer
    side of a bend, where the stretched patch was ragged or clipped. Attached to the parent bone (the pivot is
    fixed there), activated by the joint's flexion."""

    def generate(self, eng, joint: str, side: str) -> tuple[CorrectiveAsset, dict[str, np.ndarray]] | None:
        sk, src, masks = eng.sk, eng.source, eng.masks
        # 40 deg ramps: a narrower ramp made the cap appear within one sweep step (popping, Phase 3.4 sequences)
        child, parent, layers, lo, hi, cat = {
            "elbow": (f"forearm_{side}", f"upper_arm_{side}", [f"upper_arm_{side}", f"forearm_{side}"], 50.0, 90.0,
                      "elbows"),
            "knee": (f"shin_{side}", f"thigh_{side}", [f"thigh_{side}", f"shin_{side}"], 20.0, 60.0, "knees")}[joint]
        b = sk.bone(child)
        P = b.head
        limb = np.zeros(next(iter(masks.values())).shape, bool)
        for c in layers:
            if c in masks:
                limb |= masks[c]
        n = np.array([-b.u[1], b.u[0]])
        from ..phase2.skeleton import _extent  # noqa: PLC0415

        r = 0.5 * (_extent(limb, P, n) + _extent(limb, P, -n))
        if r < 4:
            return None
        H, W = limb.shape
        yy, xx = np.mgrid[:H, :W]
        d = np.hypot(xx - P[0], yy - P[1])
        region = d <= r
        outline_ring = (d >= r - 0.5) & region
        local = limb & (d < 2.5 * r)
        outline, width = _outline_style(src, limb)
        fill = _fabric_colour(src, local)
        shade = 0.92 + 0.08 * np.clip(1 - d / max(r, 1), 0, 1)  # slightly lighter toward the centre
        rgba = _paint_region((H, W), region, fill, outline, width, outline_ring, shade)
        crop, origin = _crop(rgba)
        asset = CorrectiveAsset(
            asset_id=f"{joint}_cap_{side}", category=cat, body_part=f"{joint}_{side}", side=side,
            method="JointCapGenerator", provenance="GEOMETRIC_CORRECTION", attach_bone=parent,
            placement={"mode": "after_patches", "group": f"{'arm' if joint == 'elbow' else 'leg'}_{side}"},
            drivers=[f"{joint}_{side}"], activation={"kind": "ramp", "driver": f"{joint}_{side}", "lo": lo, "hi": hi},
            origin=origin, target_pose={f"{joint}_{side}": f">= {hi} deg (eased from {lo})"},
            compatible_layers=layers, priority=2,
            params={"radius_px": round(float(r), 2), "centre": [round(float(v), 2) for v in P],
                    "fill_rgb": [round(float(v)) for v in fill], "outline_width_px": width,
                    "outline_rgb": [round(float(v)) for v in outline], "render_style": "diffused_fill_bg_outline"},
            source_refs=[f"layer:{c} (fabric colour and outline near the joint)" for c in layers],
            moho={"class": "conversion", "mapping": "image layer bound to the parent bone, shown by a Smart Bone "
                                                    "action on the joint angle (or always on, beneath the segments)",
                  "status": "proposed_unverified"},
            requirement_id=f"REQ-{joint}_cap_{side}", character=eng.profile.view)
        return asset, {"main": crop}


def _silhouette_tangent(edge: np.ndarray, p: np.ndarray, exclude: np.ndarray, r0: float = 5.0, r1: float = 28.0
                        ) -> np.ndarray | None:
    """Direction of the figure's silhouette arriving at ``p``: PCA of the outline pixels in a ring around p
    (outside ``exclude``), oriented toward p."""
    ys, xs = np.nonzero(edge & ~exclude)
    d = np.hypot(xs - p[0], ys - p[1])
    sel = (d >= r0) & (d <= r1)
    if sel.sum() < 4:
        return None
    pts = np.stack([xs[sel], ys[sel]], 1).astype(float)
    m = pts.mean(0)
    _, _, vt = np.linalg.svd(pts - m, full_matrices=False)
    u = vt[0]
    return u if float(np.dot(p - m, u)) >= 0 else -u


def _outline_lum(params: dict[str, Any]) -> float:
    return float(np.dot(np.asarray(params.get("outline_rgb", [20, 20, 24]), np.float32), [0.299, 0.587, 0.114]))


def local_fabric_fill(image: np.ndarray, opaque: np.ndarray, region: np.ndarray, fallback: np.ndarray,
                      ring: int = 8, dark_lum: float = 70.0) -> np.ndarray:
    """Fabric colour for every pixel of ``region``, diffused from the neighbouring drawn fabric: the opaque
    pixels within ``ring`` px of the region, without the dark outline pixels, interpolated by normalised Gaussian
    convolution at growing scales (a smooth membrane between the torso side and the sleeve). ``image`` is
    premultiplied float RGBA (0..1); returns RGB 0..255 (h, w, 3)."""
    a = image[..., 3:4]
    rgb = np.where(a > 1e-6, image[..., :3] / np.maximum(a, 1e-6), 0.0) * 255.0
    lum = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    near = opaque & ndimage.binary_dilation(region, iterations=ring) & (lum > dark_lum) & (a[..., 0] > 0.9)
    out = np.broadcast_to(fallback, region.shape + (3,)).astype(np.float32).copy()
    if near.sum() < 8:
        return out
    m = near.astype(np.float32)
    done = np.zeros(region.shape, bool)
    for sg in (3.0, 6.0, 12.0, 24.0, 48.0):
        den = ndimage.gaussian_filter(m, sg)
        ok = (den > 0.02) & ~done
        if ok.any():
            for k in range(3):
                num = ndimage.gaussian_filter(rgb[..., k] * m, sg)
                out[..., k][ok] = num[ok] / den[ok]
            done |= ok
        if done[region].all():
            break
    for k in range(3):  # smooth seams between the scales
        out[..., k] = ndimage.gaussian_filter(out[..., k], 2.0)
    return out


def style_cap(window: np.ndarray, cap_alpha: np.ndarray, over: np.ndarray, params: dict[str, Any]) -> np.ndarray:
    """Render-time styling of a joint cap in its canvas window (premultiplied float RGBA): the fabric colour is
    diffused from the drawn fabric around the cap (no flat disc, no edge against the segments), and the outline
    is drawn only where the cap borders the background (no ring across the fabric). Returns premultiplied RGBA."""
    on = (cap_alpha > 0.5) & over
    opaque = (window[..., 3] > 0.5) & ~on
    fallback = np.asarray(params.get("fill_rgb", [40, 38, 48]), np.float32)
    rgb = local_fabric_fill(window, opaque, on, fallback, ring=10, dark_lum=_outline_lum(params) + 12.0)
    bg = (window[..., 3] < 0.5) & ~on
    wd = float(params.get("outline_width_px", 3.0))
    if bg.any():
        d = ndimage.distance_transform_edt(~bg)
        k = np.clip(wd + 0.5 - d, 0, 1) * (cap_alpha > 0.02)
        rgb = rgb * (1 - k[..., None]) + np.asarray(params.get("outline_rgb", [20, 20, 24]), np.float32) * k[..., None]
    out = np.zeros(window.shape, np.float32)
    out[..., :3] = rgb / 255.0 * cap_alpha[..., None]
    out[..., 3] = cap_alpha
    return out


def armpit_fill(image: np.ndarray, gap: np.ndarray, anchor, params: dict[str, Any], weight: float = 1.0
                ) -> tuple[np.ndarray, np.ndarray, dict[str, Any]] | None:
    """The armpit gusset for one pose: the region the joint patch refused to stretch into (``gap``), bounded on its
    background side by a quadratic curve that is tangent to the figure's silhouette at both ends (the torso side
    and the sleeve underside), so the armhole is one smooth curve with no straight diagonal and no corner.
    It is filled with the blazer's fabric colour (darker toward the armpit), outlined along the curve with the
    character's outline width, and returned premultiplied, to be composited *beneath* everything already drawn
    (so it can never cover an original pixel)."""
    H, W = gap.shape
    opaque = image[..., 3] > 0.5
    g = ndimage.binary_closing(gap, iterations=3) & ~opaque
    if g.sum() < 20:
        return None
    lab, n = ndimage.label(g)
    if n > 1:
        sizes = ndimage.sum(g, lab, range(1, n + 1))
        g = np.isin(lab, 1 + np.nonzero(sizes >= max(20, 0.1 * sizes.max()))[0])
    if not g.any():
        return None
    ys, xs = np.nonzero(g)
    y0, y1, x0, x1 = max(0, ys.min() - 40), min(H, ys.max() + 41), max(0, xs.min() - 40), min(W, xs.max() + 41)
    sl = (slice(y0, y1), slice(x0, x1))
    gw, ow = g[sl], opaque[sl]
    bg = ~gw & ~ow
    near_o = ndimage.binary_dilation(ow, iterations=2)
    facing = gw & ndimage.binary_dilation(bg) & near_o  # gap pixels touching both the background and the figure
    cy, cx = np.nonzero(facing)
    if len(cy) < 2:
        return None
    P = np.stack([cx, cy], 1).astype(float)
    # the two contact points farthest apart: T and S (the ends of the open edge)
    i = int(np.argmax(np.hypot(*(P - P.mean(0)).T)))
    j = int(np.argmax(np.hypot(*(P - P[i]).T)))
    T, S = P[i], P[j]
    chord = float(np.hypot(*(S - T)))
    if chord < 6:
        return None
    edge = ow & ~ndimage.binary_erosion(ow) & ndimage.binary_dilation(bg)
    excl = ndimage.binary_dilation(gw, iterations=3)
    dT, dS = _silhouette_tangent(edge, T, excl), _silhouette_tangent(edge, S, excl)
    mid = (T + S) / 2
    C = mid.copy()
    if dT is not None and dS is not None:
        M = np.array([dT, -dS]).T
        if abs(np.linalg.det(M)) > 1e-3:
            a, b = np.linalg.solve(M, S - T)
            if a > 0 and b > 0:
                C = T + a * dT
    off = C - mid
    lim = params.get("max_bulge", 0.45) * chord
    if np.hypot(*off) > lim:
        C = mid + off * lim / np.hypot(*off)
    if params.get("sag"):  # Phase 3.5 ShoulderSilhouetteController: a concave armhole hollow instead of a fuller fill
        A_ = np.asarray(anchor, float) - [x0, y0]
        v_ = A_ - mid
        if np.hypot(*v_) > 1e-6:
            C = mid + v_ / np.hypot(*v_) * float(params["sag"]) * chord
    elif "max_bulge_convex" in params:  # an outward bulge reads as a full armpit
        A_ = np.asarray(anchor, float) - [x0, y0]
        cr_ = lambda u, v: float(u[0] * v[1] - u[1] * v[0])  # noqa: E731
        off = C - mid
        if np.sign(cr_(S - T, C - T)) != np.sign(cr_(S - T, A_ - T)):
            lim_c = float(params["max_bulge_convex"]) * chord
            if np.hypot(*off) > lim_c:
                C = mid + off * lim_c / max(float(np.hypot(*off)), 1e-9)
    t = np.linspace(0, 1, max(24, int(chord)))[:, None]
    B = (1 - t) ** 2 * T + 2 * (1 - t) * t * C + t ** 2 * S
    from PIL import Image as _I, ImageDraw as _D  # noqa: PLC0415

    hh, ww = gw.shape
    lens_im = _I.new("L", (ww, hh), 0)
    _D.Draw(lens_im).polygon([tuple(p) for p in B], fill=1)
    lens = np.asarray(lens_im, bool)
    A = np.asarray(anchor, float) - [x0, y0]
    cr = lambda u, v: float(u[0] * v[1] - u[1] * v[0])  # noqa: E731
    convex = np.sign(cr(S - T, C - T)) != np.sign(cr(S - T, A - T))
    region = (gw | lens) if convex else (gw & ~lens)
    region = ndimage.binary_fill_holes(region) & ~ow
    curve = np.zeros_like(gw)
    bi = np.clip(np.rint(B).astype(int), 0, [ww - 1, hh - 1])
    curve[bi[:, 1], bi[:, 0]] = True
    dc = ndimage.distance_transform_edt(~curve)
    wd = float(params.get("outline_width_px", 3.0))
    k_out = np.clip(wd / 2 + 0.5 - dc, 0, 1)
    yy, xx = np.mgrid[:hh, :ww]
    dA = np.hypot(xx - A[0], yy - A[1])
    span = max(float(dA[region].max()) if region.any() else 1.0, 1.0)
    shade = params.get("shade_min", 0.86) + (1 - params.get("shade_min", 0.86)) * np.clip(dA / span, 0, 1)
    fill = local_fabric_fill(image[sl], ow, region, np.asarray(params["fill_rgb"], np.float32),
                             dark_lum=_outline_lum(params) + 12.0)
    outline = np.asarray(params["outline_rgb"], np.float32)
    a_fill = region.astype(np.float32)
    a_out = k_out * (region | ndimage.binary_dilation(region, iterations=int(wd))).astype(np.float32)
    a = np.clip(np.maximum(a_fill, a_out), 0, 1) * float(weight)
    rgb = fill * shade[..., None]
    rgb = rgb * (1 - k_out[..., None]) + outline[None, None] * k_out[..., None]
    if params.get("crease") and region.any():  # a short armpit crease from the open edge toward the armpit
        from ..artistic.joint_art import _stroke  # noqa: PLC0415

        p0 = B[int(0.4 * (len(B) - 1))]
        dvec = A - p0
        dn = float(np.hypot(*dvec))
        if dn > 1:
            p1 = p0 + dvec / dn * min(0.45 * chord, 0.8 * dn)
            ctrl = (p0 + p1) / 2 + np.array([-dvec[1], dvec[0]]) / dn * 0.08 * chord
            ss = np.linspace(0, 1, 10)[:, None]
            cpts = (1 - ss) ** 2 * p0 + 2 * (1 - ss) * ss * ctrl + ss ** 2 * p1
            kc = _stroke(region.shape, cpts, 2.0, 0.5) * 0.6 * region
            rgb = rgb * (1 - kc[..., None]) + outline[None, None] * kc[..., None]
    out = np.zeros((H, W, 4), np.float32)
    out[sl][..., :3] = rgb / 255.0 * a[..., None]
    out[sl][..., 3] = a
    full = np.zeros((H, W), bool)
    full[sl] = a > 0.5
    info = {"T": (T + [x0, y0]).round(1).tolist(), "S": (S + [x0, y0]).round(1).tolist(),
            "C": (C + [x0, y0]).round(1).tolist(), "convex": bool(convex), "chord_px": round(chord, 1),
            "px": int(full.sum())}
    return out, full, info


class ArmpitCorrectionGenerator:
    """Armpit gussets for raised arms (a procedural recipe plus baked key previews).

    At render time, for an active gusset the renderer takes the gap the joint patch refused to fill: stretch-limit
    clipped, uncovered pixels of the sleeve and sleeve cap. ``armpit_fill`` closes it with a curve tangent to the
    torso side and to the sleeve underside, fills it with the blazer's colour (darker toward the armpit) and the
    character's outline, and composites it *beneath* every drawn piece. It can only show in the gap: the lapels and
    every original pixel stay on top. The recipe's parameters (colours, outline width, shading, bulge limit) are
    measured on the character and stored; the user approves the recipe version.

    The baked key images (abduction 115..175 deg) are stored for review and as the Moho bake candidates (switch
    layer selected by the shoulder angle, ``proposed_unverified``)."""

    KEYS = (115.0, 130.0, 145.0, 160.0, 175.0)

    def recipe(self, eng, side: str) -> dict[str, Any]:
        torso = eng.masks.get("torso")
        arm = eng.masks.get(f"upper_arm_{side}", torso)
        outline, width = _outline_style(eng.source, torso | arm)
        badge = eng.structure.decor.get("badge", np.zeros_like(torso))
        fill = _fabric_colour(eng.source, arm & ~ndimage.binary_dilation(badge, iterations=4))
        return {"fill_rgb": [round(float(v)) for v in fill], "outline_rgb": [round(float(v)) for v in outline],
                "outline_width_px": width, "shade_min": 0.72, "max_bulge": 0.45,
                "parts": [f"sleeve_upper_{side}", f"sleeve_cap_{side}"], "anchor_bone": f"upper_arm_{side}",
                "radius_h": 4.0}

    def generate(self, eng, side: str, keys: tuple[float, ...] | None = None, vis: list[str] | None = None
                 ) -> tuple[CorrectiveAsset, dict[str, np.ndarray]] | None:
        from .evidence import fk_state  # noqa: PLC0415

        vis = vis or list(eng.layers)
        params = self.recipe(eng, side)
        asset = CorrectiveAsset(
            asset_id=f"armpit_gusset_{side}", category="armpits", body_part=f"armpit_{side}", side=side,
            method="ArmpitCorrectionGenerator", provenance="GEOMETRIC_CORRECTION", attach_bone="chest",
            placement={"mode": "gap_fill_beneath"}, drivers=[f"abduction_{side}", f"clavicle_{side}", "torso_bend"],
            # fully on before the patch starts to clip (~105 deg): a gap fill is never translucent; its size grows
            # continuously from zero with the gap, so it cannot pop
            activation={"kind": "ramp", "driver": f"abduction_{side}", "lo": 80.0, "hi": 95.0},
            target_pose={f"abduction_{side}": ">= 95 deg (eased from 80); shape follows the gap"},
            compatible_layers=["torso", f"upper_arm_{side}"], priority=1, exclusive_group=f"armpit_{side}",
            params={**params, "procedural": True, "keys_deg": list(keys or self.KEYS)},
            source_refs=["renderer: stretch-limit clipped sleeve pixels (the gap)", f"layer:upper_arm_{side} "
                         "(blazer fabric colour)", "layers torso + upper_arm (outline colour and width)"],
            moho={"class": "baked", "mapping": "bake the key previews into a switch layer bound to the chest, "
                                               "selected by a Smart Bone action on the shoulder angle",
                  "status": "proposed_unverified"},
            requirement_id=f"REQ-armpit_{side}", character=eng.profile.view)
        images = {}
        for k in keys or self.KEYS:
            r = eng.pose("relaxed_standing", visible=vis, ik=fk_state({side: (k, 5.0)}), evaluate_garments=False,
                         corrective_mode="only:" + asset.asset_id, _preview_assets=[asset])
            curve = r["render"]["gap_fill_info"].get(asset.asset_id)
            gm = r["render"]["corrective_masks"].get(asset.asset_id)
            if gm is None or not gm.any():
                continue
            rgba = r["image"].copy()
            rgba[~gm] = 0
            crop, origin = _crop(rgba)
            key_id = f"abd{int(k)}"
            images[key_id] = crop
            chest = eng.dfm.world(eng.sk.idx["chest"], r["pose"])
            bind = np.array(chest)
            bind[:2, 2] += r["pad"]
            asset.keys.append({"key_id": key_id, "values": {f"abduction_{side}": float(k)}, "origin": origin,
                               "bind_matrix": bind.tolist(), "gap_fill_px": int(gm.sum()), "curve": curve,
                               "role": "baked preview (review + Moho bake candidate)"})
        return asset, images


class ShoulderSilhouetteController:
    """Phase 3.5: a leaner armpit gusset recipe (a new version of ``armpit_gusset_<side>``). The outward bulge is
    capped at 6 % of the open edge (18 % with the arm near vertical), a short crease runs from the open edge toward the
    armpit, and the fill still goes beneath everything (shoulder, lapels and blazer body stay as drawn).

    Measured limit: most of the raised-arm fullness is the stretched T-pose sleeve's own underside outline, which lies
    outside the gap. A concave sag (``params["sag"]``, kept as an option) opens a white crescent inside that outline,
    so a lean raised-arm armpit needs a drawing (REQUIRES_ALTERNATIVE_ARTWORK), not more or less fill."""

    def generate(self, eng, side: str, vis: list[str] | None = None):
        g = ArmpitCorrectionGenerator().generate(eng, side, vis=vis)
        if g is None:
            return None
        asset, images = g
        asset.method = "ShoulderSilhouetteController"
        asset.params = {**asset.params, "shoulder_controller": True, "crease": True, "max_bulge": 0.35,
                        "max_bulge_convex": 0.06}
        asset.source_refs = list(asset.source_refs) + ["Phase 3.5 audit: the v1 gusset read as an inflated armpit"]
        return asset, images


class EdgeDecontamination:
    """Non-destructive removal of the white-paper anti-aliasing on layer edges.

    Only pixels on the figure's outer silhouette are processed: the layer's edge pixels within ``width`` px of the
    *background*, i.e. outside every layer. Interior highlights are never touched. Each such pixel is modelled as
    C = a*F + (1-a)*B, with F the dark outline colour just inside and B the paper colour: a comes from the
    luminances, and the new colour F' = (C - (1-a)B)/a is solved exactly. Composited over the paper it reproduces
    the source (rounding only); over dark cloth the light fringe disappears. Only pixels lighter than the outline
    inside them are touched, so outlines keep their width and interior highlights stay. The result is a derived copy
    of the layers; the originals stay untouched."""

    def __init__(self, width: int = 2, strength: float = 1.0, background: tuple[int, int, int] | None = None):
        self.width, self.strength, self.background = width, strength, background

    def apply(self, layers: dict[str, np.ndarray], source: np.ndarray, union: np.ndarray | None = None,
              own: dict[str, np.ndarray] | None = None) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
        """``union``: the figure (every base layer's pixels); by default the union of ``layers``. ``own``: each
        layer's own pixels (without the deformer's 1 px bleed ring, which duplicates a neighbour's pixel): ring
        pixels on the silhouette are cleared in the derived copy, so an edge pixel is decontaminated once."""
        if union is None:
            union = np.zeros(source.shape[:2], bool)
            for a in layers.values():
                union |= a[..., 3] > 0
        bg_ring = ndimage.binary_dilation(union, iterations=3) & ~union
        B = (np.median(source[..., :3][bg_ring].astype(np.float32), 0) if self.background is None
             else np.array(self.background, np.float32))
        near_bg = ndimage.binary_dilation(~union, iterations=self.width) & union
        # an edge pixel with another layer beneath it at rest (e.g. a generated extension hidden under the arm)
        # is left alone: thinning it would show that layer in the rest pose instead of the paper the source had
        cover = np.zeros(union.shape, np.int16)
        for cid, rgba in layers.items():
            cover += (own[cid] if own is not None and cid in own else rgba[..., 3] > 0).astype(np.int16)
        out, changed, skipped = {}, 0, 0
        lum_w = np.array([0.299, 0.587, 0.114], np.float32)
        for cid, rgba in layers.items():
            a0 = rgba[..., 3] > 0
            ring = np.zeros_like(a0)
            if own is not None and cid in own:
                ring = a0 & ~own[cid] & near_bg
                a0 = a0 & own[cid]
            skipped += int((near_bg & a0 & (cover > 1)).sum())
            edge = near_bg & a0 & (cover == 1)
            if ring.any():
                rgba = rgba.copy()
                rgba[ring] = 0
            if not edge.any():
                out[cid] = rgba
                continue
            interior = a0 & ~ndimage.binary_dilation(~a0 | ~union, iterations=self.width)
            if not interior.any():
                out[cid] = rgba
                continue
            _, (iy, ix) = ndimage.distance_transform_edt(~interior, return_indices=True)
            ys, xs = np.nonzero(edge)
            C = rgba[ys, xs, :3].astype(np.float32)
            F = rgba[iy[ys, xs], ix[ys, xs], :3].astype(np.float32)
            lb, lf, lc = float(B @ lum_w), F @ lum_w, C @ lum_w
            # only edge pixels lighter than the outline inside them and darker than the paper are contaminated
            dark_f = (lf < lb - 40) & (lc > lf + 2.0) & (lc < lb - 1.0)
            a = np.clip((lb - lc) / np.maximum(lb - lf, 1e-3), 0.0, 1.0)
            a = 1.0 - self.strength * (1.0 - a)
            fix = dark_f & (a < 0.98)
            if not fix.any():
                out[cid] = rgba
                continue
            a_f = np.maximum(a[fix], 1e-3)[:, None]
            # the colour that reproduces the source over the paper exactly: C = a F' + (1 - a) B
            Fx = (C[fix] - (1.0 - a_f) * B[None, :]) / a_f
            Fx = np.where(a_f < 0.08, F[fix], Fx)  # nearly paper: the inside colour (the alpha carries it)
            new = rgba.copy()
            new[ys[fix], xs[fix], :3] = np.clip(np.rint(Fx), 0, 255).astype(np.uint8)
            new[ys[fix], xs[fix], 3] = np.clip(np.rint(a[fix] * rgba[ys[fix], xs[fix], 3]), 0, 255).astype(np.uint8)
            out[cid] = new
            changed += int(fix.sum())
        return out, {"changed_px": changed, "skipped_over_other_layer_px": skipped,
                     "background_rgb": [round(float(v)) for v in B], "width": self.width, "strength": self.strength}

    def asset(self, eng, report: dict[str, Any]) -> CorrectiveAsset:
        return CorrectiveAsset(
            asset_id="edge_decontamination", category="edges", body_part="all layer edges", side=None,
            method="EdgeDecontamination", provenance="GEOMETRIC_CORRECTION", attach_bone="pelvis",
            placement={"mode": "derived_layers"}, drivers=[], activation={"kind": "always"},
            params={**report}, source_refs=["all layers: silhouette pixels next to the paper background"],
            moho={"class": "direct", "mapping": "export the decontaminated derived layers (alpha edges)",
                  "status": "proposed_unverified"},
            requirement_id="REQ-edge_fringe", character=eng.profile.view)


# -- pose-space activation -------------------------------------------------------------------------------------
class PoseSpaceCorrectionEngine:
    """Activation of corrective assets from pose readings.

    * ``always``: weight 1 (static extensions: shoe tops).
    * ``ramp``: smoothstep on one driver between ``lo`` and ``hi`` (eased, monotonic, no popping).
    * ``keyed``: an eased gate on the main driver (``lo``..``hi``). Then normalised Gaussian RBF weights over the
      keys in a scaled driver space (``scales`` per driver), so combined poses (a raised shoulder with a torso
      lean) pick the nearest sample. Only the two strongest keys are drawn: the strongest at full alpha, the
      second with alpha 2*w (union, no ghosting).
    * Readings beyond the keys' range by more than one key spacing are flagged ``unsupported_combination``. The
      nearest key is still shown, and the pose is reported.
    * Exclusive groups keep the highest-priority asset; artist overrides (``overrides``: asset -> weight or
      false) win over the computed weight.
    Neutral poses give weight 0 to every ramped or keyed asset.
    """

    def activate(self, assets: list[CorrectiveAsset], readings: dict[str, float],
                 overrides: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        out = []
        for a in assets:
            act = a.activation
            kind = act.get("kind")
            rec = {"asset_id": a.asset_id, "kind": kind, "weight": 0.0, "keys": [], "flags": []}
            if kind == "always":
                rec["weight"] = 1.0
            elif kind == "ramp":
                v = float(readings.get(act["driver"], 0.0))
                rec["weight"] = float(smoothstep(act["lo"], act["hi"], np.array(v)))
                rec["driver_value"] = round(v, 2)
            elif kind == "keyed" and a.keys:
                v = float(readings.get(act["driver"], 0.0))
                gate = float(smoothstep(act["lo"], act["hi"], np.array(v)))
                rec["driver_value"] = round(v, 2)
                if gate > 0:
                    sc = act.get("scales", {})
                    d2 = []
                    for k in a.keys:
                        s = 0.0
                        for drv, kv in k["values"].items():
                            s += ((float(readings.get(drv, 0.0)) - kv) / sc.get(drv, 10.0)) ** 2
                        d2.append(s)
                    d2 = np.array(d2)
                    w = np.exp(-0.5 * d2 / 0.35)
                    if w.sum() < 1e-9:
                        w = (d2 == d2.min()).astype(float)
                    w /= w.sum()
                    order = np.argsort(-w)[:2]
                    rec["keys"] = [{"key_id": a.keys[i]["key_id"], "w": round(float(w[i]), 4)} for i in order]
                    rec["weight"] = gate
                    kv = [k["values"].get(act["driver"]) for k in a.keys]
                    spacing = (max(kv) - min(kv)) / max(1, len(kv) - 1) if len(kv) > 1 else 15.0
                    if v > max(kv) + spacing or float(np.sqrt(d2.min())) > 2.5:
                        rec["flags"].append("unsupported_combination")
            if overrides and a.asset_id in overrides:
                ov = overrides[a.asset_id]
                rec["weight"] = 0.0 if ov is False else float(ov) if isinstance(ov, (int, float)) else rec["weight"]
                rec["flags"].append("artist_override")
            rec["priority"] = a.priority
            rec["exclusive_group"] = a.exclusive_group
            out.append(rec)
        groups: dict[str, dict] = {}
        for r in out:  # exclusive groups: keep the highest priority (lowest number), then the strongest
            g = r["exclusive_group"]
            if g and r["weight"] > 0:
                cur = groups.get(g)
                if cur is None or (r["priority"], -r["weight"]) < (cur["priority"], -cur["weight"]):
                    if cur is not None:
                        cur["weight"] = 0.0
                        cur["flags"].append("suppressed_by_exclusive_group")
                    groups[g] = r
                else:
                    r["weight"] = 0.0
                    r["flags"].append("suppressed_by_exclusive_group")
        return out


class CorrectiveRuntime:
    """Loads the store's latest assets for one engine and turns activation records into renderer pieces."""

    def __init__(self, eng, store: CorrectiveArtworkStore | None):
        self.eng, self.store = eng, store
        self.assets = store.latest() if store is not None else []
        self.engine = PoseSpaceCorrectionEngine()
        self._img: dict[str, np.ndarray] = {}
        self.decont_layers: dict[str, np.ndarray] | None = None

    def _pre(self, rel: str) -> np.ndarray:
        if rel not in self._img:
            a = self.store.image(rel).astype(np.float32) / 255.0
            a[..., :3] *= a[..., 3:4]
            self._img[rel] = a
        return self._img[rel]

    def status(self, a: CorrectiveAsset, approvals: dict[str, Any]) -> str:
        return approval_state(approvals, a)

    def plan(self, readings: dict[str, float], pose, approvals: dict[str, Any], allow_pending: bool,
             mode: str = "auto", overrides: dict[str, Any] | None = None,
             extra: list[CorrectiveAsset] | None = None, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        """Renderer inputs for one pose: ``pieces`` (warped asset images), ``fills`` (procedural gap fills) and the
        activation ``report``. ``mode``: "auto" (approved; pending only with ``allow_pending``), "off", "all"
        (preview every asset that is not rejected) or "only:<asset_id>" (preview that one asset). ``extra``: assets
        not in the store yet (generator previews). ``settings``: the user's per-asset settings from the editor project
        (enabled, blend ``strength`` multiplying the weight, activation ``lo``/``hi``)."""
        out: dict[str, Any] = {"pieces": [], "fills": [], "report": []}
        assets = [self._with_settings(a, settings) for a in list(self.assets) + list(extra or [])]
        disabled = {a for a, v in (settings or {}).items() if v.get("enabled") is False}
        overrides = {**(overrides or {}), **{a: False for a in disabled}}
        if mode == "off" or not assets:
            return out
        only = mode.split(":", 1)[1] if mode.startswith("only:") else None
        if only:
            assets = [a for a in assets if a.asset_id == only]
        dfm, sk = self.eng.dfm, self.eng.sk
        acts = self.engine.activate([a for a in assets if a.placement.get("mode") != "derived_layers"],
                                    readings, overrides)
        by = {a.asset_id: a for a in assets}

        def _shown(a, w):
            st_ = self.status(a, approvals)
            return w > 0 and st_ != "rejected" and (st_ == "approved" or allow_pending or mode == "all" or only is not None)
        superseded = {x for r0 in acts for x in by[r0["asset_id"]].params.get("supersedes", [])
                      if _shown(by[r0["asset_id"]], r0["weight"])}
        for r in acts:
            a = by[r["asset_id"]]
            if a.asset_id in superseded:
                r["weight"] = 0.0
                r["flags"].append("superseded")
            sv = (settings or {}).get(a.asset_id, {})
            if "strength" in sv and r["weight"] > 0:
                r["weight"] *= float(sv["strength"])
                r["flags"].append("user_strength")
            st = self.status(a, approvals)
            shown = st != "rejected" and (st == "approved" or allow_pending or mode == "all" or only is not None)
            rec = {**r, "status": st, "shown": bool(shown and r["weight"] > 0), "version": a.version,
                   "approval_key": a.approval_key, "category": a.category,
                   "provenance": "APPROVED" if st == "approved" else a.provenance}
            out["report"].append(rec)
            if not shown or r["weight"] <= 0 or a.attach_bone not in sk.idx:
                continue
            if a.placement.get("mode") == "joint_fold":
                pr = a.params
                Wp, Wc = dfm.world(sk.idx[pr["parent"]], pose), dfm.world(sk.idx[pr["child"]], pose)
                bp, bc = sk.bone(pr["parent"]), sk.bone(pr["child"])
                P = Wp[:2, :2] @ np.asarray(bc.head, float) + Wp[:2, 2]
                ph = Wp[:2, :2] @ np.asarray(bp.head, float) + Wp[:2, 2]
                ct = Wc[:2, :2] @ np.asarray(bc.tail, float) + Wc[:2, 2]
                out.setdefault("joint_folds", []).append(
                    {"asset_id": a.asset_id, "group": a.placement["group"], "P": P.tolist(),
                     "a": (ph - P).tolist(), "b": (ct - P).tolist(), "w": float(pr["half_width_px"]),
                     "params": pr, "weight": r["weight"], "fabric_layers": pr.get("fabric_layers", [])})
                rec["fold"] = True
                continue
            if a.placement.get("mode") == "gap_fill_beneath":
                bone = a.params.get("anchor_bone", a.attach_bone)
                Wb = dfm.world(sk.idx[bone], pose)
                h0 = sk.bone(bone).head
                anchor = Wb[:2, :2] @ np.asarray(h0, float) + Wb[:2, 2]
                prm = a.params
                if prm.get("shoulder_controller"):  # pose-dependent fullness: lean unless the arm is near vertical
                    ab = float(readings.get(a.activation.get("driver", ""), 0.0))
                    u = float(np.clip((ab - 135.0) / 35.0, 0.0, 1.0))
                    prm = {**prm, "max_bulge_convex": 0.06 + 0.12 * u * u * (3 - 2 * u)}
                out["fills"].append({"asset_id": a.asset_id, "parts": a.params["parts"], "params": prm,
                                     "anchor": [float(anchor[0]), float(anchor[1])], "weight": r["weight"],
                                     "radius": float(a.params.get("radius_h", 2.6)) * sk.bone(bone).h})
                rec["fill"] = True
                continue
            W = dfm.world(sk.idx[a.attach_bone], pose)
            if a.keys:
                for n, kw in enumerate(r["keys"]):
                    k = next(k for k in a.keys if k["key_id"] == kw["key_id"])
                    alpha = r["weight"] * (1.0 if n == 0 else min(1.0, 2.0 * kw["w"]))
                    if alpha <= 1e-3 or not k.get("file"):
                        continue
                    A = W @ np.linalg.inv(np.array(k["bind_matrix"]))
                    out["pieces"].append({"asset_id": f"{a.asset_id}:{k['key_id']}", "pre": self._pre(k["file"]),
                                          "origin": tuple(k["origin"]), "A": A, "weight": alpha,
                                          "placement": a.placement})
            elif a.file:
                A = W @ np.linalg.inv(np.array(a.bind_matrix))
                out["pieces"].append({"asset_id": a.asset_id, "pre": self._pre(a.file), "origin": tuple(a.origin),
                                      "A": A, "weight": r["weight"], "placement": a.placement, "params": a.params})
        # draw order within a placement: lower priority number first (beneath)
        out["pieces"].sort(key=lambda p: by[p["asset_id"].split(":")[0]].priority)
        return out

    @staticmethod
    def _with_settings(a: CorrectiveAsset, settings: dict[str, Any] | None) -> CorrectiveAsset:
        sv = (settings or {}).get(a.asset_id) or {}
        if not any(k in sv for k in ("lo", "hi")) or a.activation.get("kind") not in ("ramp", "keyed"):
            return a
        import copy as _c  # noqa: PLC0415

        b = _c.copy(a)
        b.activation = {**a.activation, **{k: float(sv[k]) for k in ("lo", "hi") if k in sv}, "user_range": True}
        return b

    def reload(self) -> None:
        """Re-read the store (after generation or a replacement drawing)."""
        if self.store is not None:
            self.store = CorrectiveArtworkStore(self.store.pkg)
            self.assets = self.store.latest()
            self._img.clear()
            self.decont_layers = None

    def decontamination(self) -> CorrectiveAsset | None:
        return next((a for a in self.assets if a.method == "EdgeDecontamination"), None)

    def decont_state(self, approvals: dict[str, Any], allow_pending: bool, mode: str,
                     overrides: dict[str, Any] | None = None) -> dict[str, Any] | None:
        a = self.decontamination()
        if a is None or mode == "off" or (mode.startswith("only:") and mode[5:] != a.asset_id):
            return None
        st = self.status(a, approvals)
        shown = st != "rejected" and (st == "approved" or allow_pending or mode == "all" or mode.startswith("only:"))
        if overrides and overrides.get(a.asset_id) is False:
            shown = False
        return {"asset_id": a.asset_id, "kind": "derived_layers", "weight": 1.0 if shown else 0.0, "keys": [],
                "flags": [], "status": st, "shown": shown, "version": a.version, "approval_key": a.approval_key,
                "category": a.category, "provenance": "APPROVED" if st == "approved" else a.provenance,
                "priority": a.priority, "exclusive_group": None}

    def decont_pre(self) -> dict[str, np.ndarray]:
        """Premultiplied crops of the decontaminated derived layers, aligned with the deformer's own crops (only
        layers that change). Cached; layers registered later (hand variants) are added on demand."""
        a = self.decontamination()
        dfm = self.eng.dfm
        if self.decont_layers is None:
            self.decont_layers = {}
            self._decont_done: set[str] = set()
            self._union = np.zeros(self.eng.source.shape[:2], bool)
            for c, m in self.eng.masks.items():
                self._union |= m
        todo = [c for c in dfm.info if c not in self._decont_done]
        if todo:
            p = a.params if a is not None else {}
            ed = EdgeDecontamination(int(p.get("width", 2)), float(p.get("strength", 1.0)))
            own = {}
            for c in todo:
                inf = dfm.info[c]
                x0, y0 = inf["origin"]
                m = np.zeros(self._union.shape, bool)
                h0, w0 = inf["alpha0"].shape
                m[y0:y0 + h0, x0:x0 + w0] = inf["alpha0"]
                own[c] = m
            new, _ = ed.apply({c: dfm.info[c]["rgba"] for c in todo}, self.eng.source, self._union, own)
            for c in todo:
                self._decont_done.add(c)
                if new[c] is dfm.info[c]["rgba"]:
                    continue
                inf = dfm.info[c]
                x0, y0 = inf["origin"]
                h, w = inf["pre"].shape[:2]
                crop = new[c][y0:y0 + h, x0:x0 + w].astype(np.float32) / 255.0
                crop[..., :3] *= crop[..., 3:4]
                self.decont_layers[c] = crop
        return self.decont_layers


# -- planner ---------------------------------------------------------------------------------------------------
class CorrectiveArtworkPlanner:
    """Requirement records from rendered poses (evaluator + renderer findings). The decision ladder prefers the
    cheapest correction that can work and never treats new artwork as original:
    reuse -> layer order -> pivot -> local mesh -> geometric extension -> replacement drawing -> AI inpainting ->
    manual artwork."""

    def plan(self, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        reqs: dict[str, dict] = {}
        for f in findings:
            pose, res = f["pose"], f["result"]
            R = res["render"]
            readings = res.get("readings", {})
            for part, n in (R.get("clipped_uncovered") or {}).items():
                if n < 60:
                    continue
                side = part[-1] if part[-2:] in ("_r", "_l") else None
                if part.startswith(("sleeve_upper", "sleeve_cap")):
                    self._add(reqs, f"REQ-armpit_{side}", "armpits", f"armpit_{side}", pose, readings,
                              {"gap_px": n}, "geometric_extension",
                              "the joint patch refuses the stretch; a gusset drawn beneath torso and sleeve fills it",
                              0.7, "user approval (GEOMETRIC_CORRECTION)", "conversion: keyed switch layer")
                elif part.startswith("elbow"):
                    deep = readings.get(f"elbow_{side}", 0) > 125
                    self._add(reqs, f"REQ-elbow_cap_{side}" if not deep else f"REQ-elbow_deep_{side}", "elbows",
                              f"elbow_{side}", pose, readings, {"gap_px": n},
                              "geometric_extension" if not deep else "replacement_drawing",
                              "round joint cap beneath the segments" if not deep else
                              "a deep bend folds the sleeve: an inner-elbow drawing is needed", 0.75 if not deep else 0.3,
                              "user approval" if not deep else "artist drawing + approval",
                              "conversion: image layer + Smart Bone action")
                elif part.startswith(("knee", "trouser")):
                    self._add(reqs, f"REQ-knee_cap_{side}", "knees", f"knee_{side}", pose, readings, {"gap_px": n},
                              "geometric_extension", "round knee cap beneath the segments", 0.6, "user approval",
                              "conversion: image layer + Smart Bone action")
            J, pad = res["solve"]["joints_posed"], res.get("pad", 0)
            ankle_gaps = [(x, y) for (x, y) in res["tears"]["exposed_gap"]
                          if min(np.hypot(x - pad - J[f"foot_{s}"][0], y - pad - J[f"foot_{s}"][1]) for s in SIDES) < 80]
            if ankle_gaps:
                for s in SIDES:
                    self._add(reqs, f"REQ-shoe_top_{s}", "shoes", f"shoe_{s}", pose, readings,
                              {"ankle_gap_points": len(ankle_gaps)}, "geometric_extension",
                              "the shoe's top under the hem was never drawn: extend the shoe upward under the trouser",
                              0.8, "user approval", "direct: extra layer bound to the foot")
            for m in (res.get("feasibility") or {}).get("missing", []):
                rid = f"REQ-{m['asset'].replace(':', '_')}"
                self._add(reqs, rid, "torso" if "torso" in m["asset"] else "hips" if "hip" in m["asset"] or
                          "seat" in m["asset"] or "trousers" in m["asset"] else "knees" if "knee" in m["asset"]
                          else "elbows" if "elbow" in m["asset"] else "shoulders", m["asset"], pose, readings, {},
                          "replacement_drawing", m.get("why", "view-specific drawing required"), 0.2,
                          "artist drawing (or reviewed AI candidate) + approval", "switch layer / Smart Bone action")
        return list(reqs.values())

    @staticmethod
    def _add(reqs, rid, cat, region, pose, readings, measure, ctype, why, conf, approval, moho):
        r = reqs.setdefault(rid, {"id": rid, "category": cat, "garment_region": region, "triggering_poses": [],
                                  "target_joint_angles": {}, "required_hidden_area": {}, "correction_type": ctype,
                                  "decision": why, "source_artwork": [], "confidence": conf,
                                  "required_approval": approval, "expected_moho": moho,
                                  "ai_generation": "not used: " + ("geometric construction from the character's own "
                                                                   "pixels is sufficient" if ctype ==
                                                                   "geometric_extension" else
                                                                   "needs a view-specific drawing; an AI candidate "
                                                                   "would require review"),
                                  "treated_as_original": False})
        if pose not in r["triggering_poses"]:
            r["triggering_poses"].append(pose)
        for k, v in readings.items():
            if region.split("_")[0] in k or k.endswith(region[-2:]):
                r["target_joint_angles"].setdefault(pose, {})[k] = round(float(v), 1)
        for k, v in measure.items():
            r["required_hidden_area"][f"{pose}:{k}"] = v


def generate_all(eng, store: CorrectiveArtworkStore, vis: list[str], progress=print) -> list[CorrectiveAsset]:
    """Run every generator on the character and store the results (pending review)."""
    made = []
    for s in SIDES:
        g = ShoeTopRecovery().generate(eng, s)
        if g:
            made.append(store.add(*g))
            progress(f"shoe_top_{s}: {g[0].params['columns']} columns, depth {g[0].params['depth_px']} px")
        for joint in ("elbow", "knee"):
            g = JointCapGenerator().generate(eng, joint, s)
            if g:
                made.append(store.add(*g))
                progress(f"{joint}_cap_{s}: radius {g[0].params['radius_px']} px")
        g = ArmpitCorrectionGenerator().generate(eng, s, vis=vis)
        if g:
            made.append(store.add(*g))
            progress(f"armpit_gusset_{s}: keys {g[0].params['keys_deg']}")
    _, rep = EdgeDecontamination().apply({c: a for c, a in eng.layers.items()}, eng.source)
    a = EdgeDecontamination().asset(eng, rep)
    made.append(store.add(a, {}))
    progress(f"edge_decontamination: {rep['changed_px']} edge px")
    return made


__all__ = ["answered_by", "ANSWERS", "CorrectiveArtworkStore", "CorrectiveAsset", "CorrectiveArtworkPlanner", "PoseSpaceCorrectionEngine",
           "CorrectiveRuntime", "ShoeTopRecovery", "JointCapGenerator", "ArmpitCorrectionGenerator",
           "EdgeDecontamination", "generate_all", "PROVENANCE", "CATEGORIES", "CORRECTION_TYPES", "armpit_fill", "style_cap"]
