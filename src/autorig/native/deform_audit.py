"""Phase 4.2: joint deformation root-cause audit (simulation of rigid native binding; never Moho output).

For each joint (shoulders, elbows, hips, knees, waist) and each Phase 4.2 test pose, the audit measures what the
rigid native rig does to the artwork. It then classifies the cause, so a fix can be chosen by evidence rather than by
rebuilding the skeleton:

| metric | measures | cause it points to |
|---|---|---|
| ``hole_px`` | pixels a small closing fills near the joint in the pose but not at rest (cracks, wedges, triangular gaps) | inadequate overlap / missing hidden artwork |
| ``hidden_fill_px`` | of those, pixels the pending Phase 2 reconstruction would cover | missing hidden artwork (a reconstruction exists, pending review) |
| ``sweep_px`` | pixels of the limb layer that lie behind the joint pivot at rest and end up outside the original silhouette | the layer cut is not at the joint (e.g. the sleeve cap swings: "oversized shoulder") |
| ``occluded_px`` | moved limb pixels drawn *under* a body layer they overlap | layer ordering |
| ``pivot_cut_px`` | distance from the bone pivot to the centre of the cut between the limb layer and its neighbours | bone placement |
| view flags | pose needs depth motion on front-view art | unsupported viewing angle |
| corrective store | approved / pending / missing corrective drawings for the joint | missing corrective drawings |

Thresholds are module constants, recorded in the report. Visual quality is never scored automatically.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

from .hybrid import VARIANTS, HLayer, Simulator, assemble, base_layers, poses_42
from .skeleton import NativeSkeletonSpec

JOINTS = {
    # joint: (moving bone whose base is the joint, static neighbour bones, poses that move it)
    "shoulder_l": ("LEFT_UPPER_ARM", ("CHEST", "LEFT_SHOULDER", "NECK"), (10, 20)),
    "shoulder_r": ("RIGHT_UPPER_ARM", ("CHEST", "RIGHT_SHOULDER", "NECK"), (10, 30)),
    "elbow_l": ("LEFT_FOREARM", ("LEFT_UPPER_ARM",), (40,)),
    "elbow_r": ("RIGHT_FOREARM", ("RIGHT_UPPER_ARM",), (50,)),
    "hip_l": ("LEFT_THIGH", ("PELVIS",), (60,)),
    "hip_r": ("RIGHT_THIGH", ("PELVIS",), (70,)),
    "knee_l": ("LEFT_SHIN", ("LEFT_THIGH",), (60,)),
    "knee_r": ("RIGHT_SHIN", ("RIGHT_THIGH",), (70,)),
    "waist": ("SPINE_LOWER", ("PELVIS",), (80,)),
}
THRESHOLDS = {
    "hole_px": 150,             # new gap pixels near a joint
    "hidden_fill_frac": 0.5,    # share of the holes the pending reconstruction covers
    "sweep_px": 300,            # behind-pivot limb pixels pushed outside the original silhouette
    "occluded_px": 400,         # moved limb pixels hidden under a body layer
    "pivot_cut_frac": 0.6,      # pivot-to-cut distance / limb half-thickness
    "window_frac": 1.6,         # joint window half-size / limb half-thickness (min 60 px)
    "closing_frac": 0.45,       # closing radius / limb half-thickness (3..22 px)
    "cut_band_frac": 0.25,      # axial bin width for locating the transverse cut / limb half-thickness
    "seam_px": 150,             # boundary pixels running along the limb (sliver / seam) worth reporting
    "crack_px": 60,             # new pixels a 3 px closing fills (thin cracks: inadequate overlap)
}
CAUSES = {
    "MISSING_HIDDEN_ARTWORK": "missing hidden artwork (a pending reconstruction exists)",
    "MISSING_ARTWORK": "missing hidden artwork (no reconstruction covers the gap)",
    "LAYER_CUT": "the layer cut is not at the joint: limb pixels behind the pivot swing out",
    "LAYER_ORDER": "incorrect layer ordering for the pose",
    "BONE_PLACEMENT": "the pivot is far from the layer cut",
    "RIGID_BINDING_SPAN": "a rigidly bound layer spans the joint (needs flexible binding or corrective artwork)",
    "UNSUPPORTED_VIEW": "the motion is depth motion on front-view art",
    "MISSING_CORRECTIVE_DRAWING": "no approved corrective drawing for this joint",
    "INADEQUATE_OVERLAP": "thin cracks at the seam: the two layers do not overlap enough",
    "MESH_INFLUENCE": "inappropriate mesh influence (not applicable to rigid binding: no mesh is bound natively)",
}


def _half_thickness(sk: NativeSkeletonSpec, rig: dict[str, Any], bone: str) -> float:
    src = sk.bone(bone).source.split(" ")[0]
    for b in rig.get("bones", []):
        if b["name"] == src and b.get("half_thickness_px"):
            return float(b["half_thickness_px"])
    return 50.0


def _disk(r: int) -> np.ndarray:
    y, x = np.mgrid[-r:r + 1, -r:r + 1]
    return x * x + y * y <= r * r


def _corrective_status(pkg: Path, joint: str) -> dict[str, Any]:
    idx = pkg / "analysis/corrective_assets.json"
    want = {"shoulder": "armpit", "elbow": "elbow", "hip": "hip", "knee": "knee", "waist": "torso"}[joint.split("_")[0]]
    side = joint.split("_")[1] if "_" in joint else None
    found = []
    if idx.is_file():
        for a in json.loads(idx.read_text(encoding="utf-8")).get("assets", []):
            part = str(a.get("body_part", ""))
            if part.startswith(want) and (side is None or part.endswith(f"_{side}")):
                found.append({"asset": a.get("asset_id"), "version": a.get("version"),
                              "provenance": a.get("provenance"), "approved": a.get("provenance") == "APPROVED"})
    return {"assets": found, "approved": sum(1 for f in found if f["approved"]),
            "pending": sum(1 for f in found if not f["approved"])}


class JointAudit:
    def __init__(self, pkg: str | Path, sk: NativeSkeletonSpec | None = None):
        self.pkg = Path(pkg)
        self.sk = sk or NativeSkeletonSpec.from_package(self.pkg)
        self.rig = json.loads((self.pkg / "natural_pose/rig.json").read_text(encoding="utf-8"))
        self.base = base_layers(self.pkg, self.sk)
        self.sim = Simulator(self.sk, rig=self.rig)
        self.poses = {p["frame"]: p for p in poses_42(self.sk)}
        self._rest_alpha: dict[str, np.ndarray] = {}

    def layers(self, variant: str) -> list[HLayer]:
        return assemble(self.pkg, self.sk, variant, self.base)

    def _joint_px(self, bone: str, world) -> tuple[float, float]:
        from ..geometry import world_to_px
        w, h = self.sim.placement.canvas
        return world_to_px(world[bone][0], w, h)

    def _window(self, centre, half: int, shape) -> tuple[slice, slice]:
        x, y = int(round(centre[0])), int(round(centre[1]))
        return (slice(max(0, y - half), min(shape[0], y + half)), slice(max(0, x - half), min(shape[1], x + half)))

    def measure(self, variant: str, frames_only: set[int] | None = None,
                joints_only: set[str] | None = None) -> dict[str, Any]:
        layers = self.layers(variant)
        visible = [l for l in layers if l.status in ("original", "derived_partition")]
        review = [l for l in layers if l.status not in ("original", "derived_partition")]
        rest_pose = self.poses[1]
        rest = self.sim.render(layers, rest_pose)
        rest_orig = self.sim.render(visible, rest_pose)
        rest_fig = rest_orig[..., 3] > 127
        sil = ndimage.binary_dilation(rest_fig, _disk(3))
        z_of = {l.name: l.z for l in layers}
        out: dict[str, Any] = {"variant": variant, "steps": list(VARIANTS[variant]), "joints": {},
                               "layers": len(layers), "review_layers": [l.name for l in review]}
        renders = {}
        for frame, pose in self.poses.items():
            if frame in (1, 90) or (frames_only is not None and frame not in frames_only):
                continue
            world = self.sim.world(pose)
            full = self.sim.render(layers, pose)
            orig = self.sim.render(visible, pose) if review else full
            renders[frame] = full
            for joint, (bone, neighbours, frames) in JOINTS.items():
                if frame not in frames or (joints_only is not None and joint not in joints_only):
                    continue
                ht = _half_thickness(self.sk, self.rig, bone)
                half = int(max(60, THRESHOLDS["window_frac"] * ht))
                r = int(min(22, max(3, THRESHOLDS["closing_frac"] * ht)))
                fig = full[..., 3] > 127
                fig_o = orig[..., 3] > 127
                wp, wr = (self._window(self._joint_px(bone, world), half, fig.shape),
                          self._window(self._joint_px(bone, self.sim.world(rest_pose)), half, fig.shape))
                closed = ndimage.binary_closing(fig[wp], _disk(r))
                holes = int((closed & ~fig[wp]).sum())
                rest_closed = ndimage.binary_closing(rest_fig[wr], _disk(r))
                rest_holes = int((rest_closed & ~rest_fig[wr]).sum())
                closed_o = ndimage.binary_closing(fig_o[wp], _disk(r))
                holes_o = int((closed_o & ~fig_o[wp]).sum())
                crack = int((ndimage.binary_closing(fig[wp], _disk(3)) & ~fig[wp]).sum())
                rest_crack = int((ndimage.binary_closing(rest_fig[wr], _disk(3)) & ~rest_fig[wr]).sum())
                hole_px = max(0, holes - rest_holes)
                hole_px_orig = max(0, holes_o - rest_holes)
                hidden_fill = max(0, hole_px_orig - hole_px) if review else None
                # sweep: limb pixels behind its pivot at rest, moved, outside the original silhouette
                limb = [l for l in layers if l.bone == bone and l.status in ("original", "derived_partition")]
                sweep = self._sweep(limb, bone, world, sil)
                occl = self._occluded(layers, bone, neighbours, world, z_of)
                over = self._over_body(layers, bone, world)
                entry = {"frame": frame, "pose": pose["id"], "hole_px": hole_px,
                         "hole_px_originals_only": hole_px_orig, "hidden_fill_px": hidden_fill,
                         "crack_px": max(0, crack - rest_crack),
                         "sweep_px": sweep, "occluded_px": occl, "limb_over_body_px": over,
                         "window_half_px": half, "closing_r_px": r}
                out["joints"].setdefault(joint, []).append(entry)
        for joint, (bone, neighbours, _) in JOINTS.items():
            if joints_only is not None and joint not in joints_only:
                continue
            g = self._cut_geometry(layers, bone, neighbours)
            out.setdefault("static", {})[joint] = {
                "pivot_cut_px": None if g is None else abs(g["cut_offset_px"]),
                "cut": g,
                "pivot_method": self._pivot_method(bone),
                "half_thickness_px": _half_thickness(self.sk, self.rig, bone),
                "behind_pivot_px": self._behind(layers, bone),
                "parent_past_joint_px": self._parent_past(layers, bone),
                "corrective_store": _corrective_status(self.pkg, joint)}
        out["_renders"] = renders
        out["_rest"] = rest
        return out

    def _sweep(self, limb: list[HLayer], bone: str, world, sil: np.ndarray) -> int:
        b = self.sk.bone(bone)
        total = 0
        m = self.sim.matrix(bone, world)
        ox, oy = self.sim.placement.offset
        for l in limb:
            ys, xs = np.nonzero(l.rgba[..., 3] > 127)
            if xs.size == 0:
                continue
            bx, by = b.base_px
            tx, ty = b.tip_px
            L = math.hypot(tx - bx, ty - by)
            t = ((xs - bx) * (tx - bx) + (ys - by) * (ty - by)) / L
            sel = t < 0
            if not sel.any():
                continue
            cx, cy = xs[sel] + ox, ys[sel] + oy
            px = np.rint(m[0, 0] * cx + m[0, 1] * cy + m[0, 2]).astype(int)
            py = np.rint(m[1, 0] * cx + m[1, 1] * cy + m[1, 2]).astype(int)
            ok = (px >= 0) & (py >= 0) & (px < sil.shape[1]) & (py < sil.shape[0])
            total += int((~sil[py[ok], px[ok]]).sum() + (~ok).sum())
        return total

    def _occluded(self, layers, bone: str, neighbours, world, z_of) -> int:
        """Moved limb pixels that a body layer drawn above them covers in the pose."""
        moving = {bone, *self.sk.descendants(bone)}
        mov = [l for l in layers if l.bone in moving and l.status in ("original", "derived_partition")]
        body = [l for l in layers if l.bone not in moving and l.status in ("original", "derived_partition")
                and l.bone != "ROOT"]
        if not mov:
            return 0
        masks = {l.name: self._posed_mask(l, world) for l in mov + body}
        total = 0
        for a in mov:
            for c in body:
                if z_of[c.name] > z_of[a.name]:
                    total += int((masks[a.name] & masks[c.name]).sum())
        return total

    def _over_body(self, layers, bone: str, world) -> int:
        """Moved limb pixels drawn over body pixels the limb did not cover at rest (inward collapse)."""
        moving = {bone, *self.sk.descendants(bone)}
        vis = [l for l in layers if l.status in ("original", "derived_partition")]
        mov = [l for l in vis if l.bone in moving]
        if not mov:
            return 0
        posed = np.zeros(self.sim.placement.canvas[::-1], bool)
        for l in mov:
            posed |= self._posed_mask(l, world)
        rest_limb = np.zeros_like(posed)
        body = np.zeros_like(posed)
        for l in vis:
            a = np.asarray(self.sim.canvas_layer(l.rgba))[..., 3] > 127
            if l.bone in moving:
                rest_limb |= a
            else:
                body |= a
        return int((posed & body & ~rest_limb).sum())

    def _pivot_method(self, bone: str) -> str | None:
        src = self.sk.bone(bone).source.split(" ")[0]
        for b in self.rig.get("bones", []):
            if b["name"] == src:
                return b.get("pivot_method")
        return None

    def _parent_past(self, layers, bone: str) -> int:
        """Pixels of the parent bone's layers that lie past this joint's pivot plane (they stay with the parent when
        the joint bends: a sliver left behind)."""
        b = self.sk.bone(bone)
        if not b.parent:
            return 0
        (bx, by), (tx, ty) = b.base_px, b.tip_px
        L = math.hypot(tx - bx, ty - by)
        n = 0
        for l in layers:
            if l.bone != b.parent or l.status not in ("original", "derived_partition"):
                continue
            ys, xs = np.nonzero(l.rgba[..., 3] > 0)
            n += int((((xs - bx) * (tx - bx) + (ys - by) * (ty - by)) / L > 0.25 * L).sum())
        return n

    def _posed_mask(self, l: HLayer, world) -> np.ndarray:
        from ..preview import _invert_affine_px
        w, h = self.sim.placement.canvas
        im = self.sim.canvas_layer(l.rgba)
        m = self.sim.matrix(l.bone, world)
        if not np.allclose(m, np.eye(3), atol=1e-9):
            im = im.transform((w, h), Image.Transform.AFFINE, _invert_affine_px(m), resample=Image.Resampling.NEAREST)
        return np.asarray(im)[..., 3] > 127

    def _cut_geometry(self, layers, bone: str, neighbours) -> dict[str, Any] | None:
        """Where the limb layer meets its neighbours, along the bone axis (0 = the pivot, negative = behind it).

        The *transverse* cut is the densest axial band of boundary pixels (a cut across the limb). Boundary pixels
        outside it run along the limb (a long seam or a segmentation sliver); they are counted separately, because a
        centroid over both would put a clean knee cut 120 px from its pivot."""
        limb = np.zeros_like(layers[0].rgba[..., 3], bool)
        nb = np.zeros_like(limb)
        moving = {bone, *self.sk.descendants(bone)}
        for l in layers:
            if l.status not in ("original", "derived_partition"):
                continue
            if l.bone in moving:
                limb |= l.rgba[..., 3] > 0
            elif l.bone in neighbours:
                nb |= l.rgba[..., 3] > 0
        cut = limb & ndimage.binary_dilation(nb, iterations=2)
        if not cut.any():
            return None
        ys, xs = np.nonzero(cut)
        b = self.sk.bone(bone)
        (bx, by), (tx, ty) = b.base_px, b.tip_px
        L = math.hypot(tx - bx, ty - by)
        t = ((xs - bx) * (tx - bx) + (ys - by) * (ty - by)) / L
        ht = _half_thickness(self.sk, self.rig, bone)
        bw = max(4.0, THRESHOLDS["cut_band_frac"] * ht)
        bins = np.floor(t / bw).astype(int)
        vals, counts = np.unique(bins, return_counts=True)
        best = int(vals[np.argmax(counts)])
        band = np.abs(bins - best) <= 1
        return {"cut_px": int(cut.sum()), "transverse_px": int(band.sum()),
                "cut_offset_px": round(float(np.median(t[band])), 1),
                "seam_along_limb_px": int((~band).sum()),
                "axial_range_px": [round(float(t.min()), 1), round(float(t.max()), 1)]}

    def _pivot_cut(self, layers, bone: str, neighbours) -> float | None:
        g = self._cut_geometry(layers, bone, neighbours)
        return None if g is None else abs(g["cut_offset_px"])

    def pivot_experiment(self, joint: str) -> dict[str, Any] | None:
        """Counterfactual for BONE_PLACEMENT vs LAYER_CUT: move the joint pivot onto the measured layer cut (the
        skeleton changes, the rigid 4.1 layers stay) and compare with moving the cut onto the pivot (``split``: the
        layers change, the skeleton stays). Only run when the transverse cut is off the pivot."""
        bone, neighbours, frames = JOINTS[joint]
        ht = _half_thickness(self.sk, self.rig, bone)
        rigid = self.layers("rigid_4_1")
        g = self._cut_geometry(rigid, bone, neighbours)
        if g is None or abs(g["cut_offset_px"]) <= THRESHOLDS["pivot_cut_frac"] * ht:
            return None
        sk2 = copy.deepcopy(self.sk)
        b2 = sk2.bone(bone)
        (bx, by), (tx, ty) = b2.base_px, b2.tip_px
        L = math.hypot(tx - bx, ty - by)
        off = g["cut_offset_px"]
        new = (bx + (tx - bx) / L * off, by + (ty - by) / L * off)
        old = b2.base_px
        b2.base_px = new
        if b2.parent:
            p = sk2.bone(b2.parent)
            if math.hypot(p.tip_px[0] - old[0], p.tip_px[1] - old[1]) < 1e-6:
                p.tip_px = new
        alt = JointAudit.__new__(JointAudit)
        alt.pkg, alt.sk, alt.rig, alt.base = self.pkg, sk2, self.rig, self.base
        alt.sim, alt.poses, alt._rest_alpha = Simulator(sk2, rig=self.rig), self.poses, {}
        res = {"joint": joint, "bone": bone, "moved_pivot_px": [round(new[0], 1), round(new[1], 1)],
               "original_pivot_px": [round(old[0], 1), round(old[1], 1)], "offset_px": off, "frames": {}}
        for name, aud, variant in (("pivot_at_cut", alt, "rigid_4_1"), ("cut_at_pivot", self, "split")):
            m = aud.measure(variant, frames_only=set(frames), joints_only={joint})
            for e in m["joints"][joint]:
                res["frames"].setdefault(str(e["frame"]), {})[name] = {
                    k: e[k] for k in ("hole_px", "sweep_px", "limb_over_body_px")}
        tot = {n: sum(v[n]["hole_px"] + v[n]["sweep_px"] + v[n]["limb_over_body_px"] for v in res["frames"].values())
               for n in ("pivot_at_cut", "cut_at_pivot")}
        res["defect_px"] = tot
        res["supports"] = "BONE_PLACEMENT" if tot["pivot_at_cut"] < tot["cut_at_pivot"] else "LAYER_CUT"
        res["note"] = ("defect = holes + sweep + limb pixels newly drawn over the body; the pivot that produces fewer "
                       "defect pixels is supported. The renders of both are in the close-up sheet.")
        res["_renders"] = {"pivot_at_cut": alt, "cut_at_pivot": self}
        return res

    def _behind(self, layers, bone: str) -> int:
        b = self.sk.bone(bone)
        n = 0
        for l in layers:
            if l.bone != bone or l.status not in ("original", "derived_partition"):
                continue
            ys, xs = np.nonzero(l.rgba[..., 3] > 0)
            bx, by = b.base_px
            tx, ty = b.tip_px
            L = math.hypot(tx - bx, ty - by)
            n += int((((xs - bx) * (tx - bx) + (ys - by) * (ty - by)) / L < 0).sum())
        return n


def classify(entry: dict[str, Any], static: dict[str, Any], pose: dict[str, Any]) -> list[str]:
    t = THRESHOLDS
    causes = []
    if entry["hole_px"] >= t["hole_px"] or entry.get("hole_px_originals_only", 0) >= t["hole_px"]:
        hf = entry.get("hidden_fill_px")
        if hf is not None and entry["hole_px_originals_only"] and hf / entry["hole_px_originals_only"] >= t["hidden_fill_frac"]:
            causes.append("MISSING_HIDDEN_ARTWORK")
        if entry["hole_px"] >= t["hole_px"]:
            causes.append("MISSING_ARTWORK" if hf is not None else "MISSING_HIDDEN_ARTWORK")
    if entry["sweep_px"] >= t["sweep_px"]:
        causes.append("LAYER_CUT")
    if entry["occluded_px"] >= t["occluded_px"]:
        causes.append("LAYER_ORDER")
    if static.get("pivot_cut_px") is not None and static["pivot_cut_px"] > t["pivot_cut_frac"] * static["half_thickness_px"]:
        causes.append("BONE_PLACEMENT")
    if static.get("behind_pivot_px", 0) >= 500 and (entry["hole_px"] >= t["hole_px"] or entry["sweep_px"] >= t["sweep_px"]):
        causes.append("RIGID_BINDING_SPAN")
    if pose.get("view_note"):
        causes.append("UNSUPPORTED_VIEW")
    if causes and static["corrective_store"]["approved"] == 0:
        causes.append("MISSING_CORRECTIVE_DRAWING")
    return causes


def diagnose(result: dict[str, Any]) -> dict[str, Any]:
    """Root cause per joint and pose, decided by which experiment removes which defect (needs the rigid_4_1, order,
    split, hybrid and hybrid_review variants). Each cause carries the numbers that support it."""
    t = THRESHOLDS
    V = result["variants"]
    need = ("rigid_4_1", "order", "split", "hybrid", "hybrid_review")
    if any(v not in V for v in need):
        return {}
    poses = {p["frame"]: p for p in result["poses"]}
    exps = result.get("pivot_experiments", {})
    row = lambda v, j, f: next(e for e in V[v]["joints"][j] if e["frame"] == f)  # noqa: E731
    out: dict[str, Any] = {}
    for joint, rows in V["rigid_4_1"]["joints"].items():
        st = V["rigid_4_1"]["static"][joint]
        for e in rows:
            f = e["frame"]
            r, o, sp, hy, rv = (row(v, joint, f) for v in need)
            causes: dict[str, str] = {}
            if r["occluded_px"] >= t["occluded_px"] and o["occluded_px"] < t["occluded_px"]:
                causes["LAYER_ORDER"] = (f"{r['occluded_px']} limb px hidden under a body layer; drawing the arm "
                                         f"chain above the torso leaves {o['occluded_px']}")
            exp = exps.get(joint)
            off = st.get("cut") or {}
            if r["sweep_px"] >= t["sweep_px"]:
                if exp and exp["supports"] == "BONE_PLACEMENT":
                    causes["BONE_PLACEMENT"] = (f"moving the pivot onto the layer cut ({exp['offset_px']} px) gives "
                                                f"{exp['defect_px']['pivot_at_cut']} defect px vs "
                                                f"{exp['defect_px']['cut_at_pivot']} for moving the cut")
                else:
                    why = (f"; moving the pivot onto the cut instead gives {exp['defect_px']['pivot_at_cut']} defect "
                           f"px vs {exp['defect_px']['cut_at_pivot']}" if exp else "")
                    causes["LAYER_CUT"] = (f"{st['behind_pivot_px']} limb px lie behind the pivot (cut "
                                           f"{off.get('cut_offset_px')} px from it); {r['sweep_px']} swing outside "
                                           f"the silhouette, {sp['sweep_px']} after the split{why}")
            if st.get("parent_past_joint_px", 0) >= t["seam_px"]:
                causes["LAYER_CUT"] = causes.get("LAYER_CUT", "") + (
                    f"{'; ' if 'LAYER_CUT' in causes else ''}{st['parent_past_joint_px']} px of the parent layer lie "
                    "past the joint (a sliver that stays behind)")
            if hy["crack_px"] >= t["crack_px"]:
                causes["INADEQUATE_OVERLAP"] = f"{hy['crack_px']} px of thin cracks remain at the seam after the split"
            if hy["hole_px"] >= t["hole_px"]:
                orig = rv["hole_px_originals_only"]
                fill = rv["hidden_fill_px"] or 0
                if orig and fill / orig >= t["hidden_fill_frac"]:
                    causes["MISSING_HIDDEN_ARTWORK"] = (f"{hy['hole_px']} gap px; the pending Phase 2 reconstruction "
                                                        f"covers {fill} of {orig} (review required)")
                else:
                    causes["MISSING_ARTWORK"] = (f"{hy['hole_px']} gap px; the pending reconstruction covers "
                                                 f"{fill} of {orig}: no hidden artwork exists for this gap")
            if poses[f].get("view_note"):
                causes["UNSUPPORTED_VIEW"] = poses[f]["view_note"]
            residual = any(k in causes for k in ("MISSING_ARTWORK", "MISSING_HIDDEN_ARTWORK", "INADEQUATE_OVERLAP",
                                                 "UNSUPPORTED_VIEW"))
            if residual and st["corrective_store"]["approved"] == 0:
                cs = st["corrective_store"]
                causes["MISSING_CORRECTIVE_DRAWING"] = (f"{cs['pending']} pending, 0 approved corrective assets for "
                                                        "this joint")
            out.setdefault(joint, []).append({"frame": f, "pose": e["pose"], "causes": causes,
                                              "rigid": {k: r[k] for k in ("hole_px", "crack_px", "sweep_px",
                                                                          "occluded_px")},
                                              "hybrid": {k: hy[k] for k in ("hole_px", "crack_px", "sweep_px",
                                                                            "occluded_px")}})
    return out


DEFAULT_VARIANTS = ("rigid_4_1", "order", "split", "hybrid", "hybrid_review", "hybrid_correctives")


def run_audit(pkg: str | Path, variants=DEFAULT_VARIANTS,
              out: str | Path | None = None, sheets: bool = True, experiments: bool = True) -> dict[str, Any]:
    audit = JointAudit(pkg)
    result: dict[str, Any] = {"schema": "autorig.native_deformation_audit", "version": "4.2.0",
                              "character_id": audit.sk.character_id, "label": "SIMULATION of rigid native binding "
                              "(not Moho output)", "thresholds": THRESHOLDS, "causes": CAUSES,
                              "poses": list(audit.poses.values()), "variants": {}}
    renders = {}
    for v in variants:
        m = audit.measure(v)
        renders[v] = (m.pop("_renders"), m.pop("_rest"))
        for joint, rows in m["joints"].items():
            for e in rows:
                e["causes"] = classify(e, m["static"][joint], audit.poses[e["frame"]])
        result["variants"][v] = m
    exp_renders = {}
    if experiments:
        result["pivot_experiments"] = {}
        for joint in JOINTS:
            x = audit.pivot_experiment(joint)
            if x is not None:
                exp_renders[joint] = x.pop("_renders")
                result["pivot_experiments"][joint] = x
    result["diagnosis"] = diagnose(result)
    result["summary"] = summarise(result)
    if out:
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "joint_audit.json").write_text(json.dumps(result, indent=2, default=float) + "\n", encoding="utf-8")
        if sheets:
            result["sheets"] = write_sheets(audit, renders, out)
            if exp_renders:
                result["sheets"].append(write_experiment_sheet(audit, result["pivot_experiments"], exp_renders, out))
    return result


def write_experiment_sheet(audit: JointAudit, exps: dict[str, Any], exp_renders: dict[str, Any], out: Path) -> str:
    """Close-ups of the pivot counterfactual: pivot moved onto the cut vs cut moved onto the pivot (SIMULATION)."""
    font = ImageFont.load_default(size=16)
    cell = 260
    rows = [(j, int(f)) for j, x in exps.items() for f in x["frames"]]
    sheet = Image.new("RGB", (170 + 3 * cell, 50 + cell * len(rows)), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    d.text((8, 6), f"SIMULATION (not Moho) - pivot experiment, {audit.sk.character_id}", fill=(200, 0, 0), font=font)
    for i, lab in enumerate(("rest", "pivot moved onto cut", "cut moved onto pivot (split)")):
        d.text((170 + i * cell + 6, 28), lab, fill=(0, 0, 0), font=font)
    bg = np.array([236, 236, 236], float)
    for r, (joint, frame) in enumerate(rows):
        bone = JOINTS[joint][0]
        x = exps[joint]
        d.text((8, 50 + r * cell + 90), f"{joint}\nframe {frame}\n{x['supports']}", fill=(0, 0, 0), font=font)
        for i, (aud, variant, fr) in enumerate(((audit, "rigid_4_1", 1),
                                                 (exp_renders[joint]["pivot_at_cut"], "rigid_4_1", frame),
                                                 (exp_renders[joint]["cut_at_pivot"], "split", frame))):
            img = aud.sim.render(aud.layers(variant), aud.poses[fr])
            a = img[..., 3:4] / 255.0
            flat = (img[..., :3] * a + bg * (1 - a)).clip(0, 255).astype(np.uint8)
            c = aud._joint_px(bone, aud.sim.world(aud.poses[fr]))
            h = 150
            crop = Image.fromarray(flat).crop((int(c[0]) - h, int(c[1]) - h, int(c[0]) + h, int(c[1]) + h))
            sheet.paste(crop.resize((cell - 6, cell - 6)), (170 + i * cell, 50 + r * cell))
    p = out / "SIMULATION_pivot_experiment.jpg"
    sheet.save(p, quality=88)
    return p.name


def summarise(result: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for v, m in result["variants"].items():
        tot = {"hole_px": 0, "crack_px": 0, "sweep_px": 0, "occluded_px": 0}
        causes: dict[str, int] = {}
        for rows in m["joints"].values():
            for e in rows:
                for k in tot:
                    tot[k] += e[k]
                for c in e["causes"]:
                    causes[c] = causes.get(c, 0) + 1
        out[v] = {"totals": tot, "cause_counts": causes}
    roots: dict[str, list[str]] = {}
    for joint, rows in (result.get("diagnosis") or {}).items():
        for e in rows:
            for c in e["causes"]:
                roots.setdefault(c, []).append(f"{joint}@{e['frame']}")
    out["root_causes"] = roots
    return out


def write_sheets(audit: JointAudit, renders: dict[str, Any], out: Path) -> list[str]:
    """Joint close-ups per variant (SIMULATION), full-body pose strip per variant."""
    font = ImageFont.load_default(size=18)
    small = ImageFont.load_default(size=14)
    written = []
    variants = list(renders)
    bg = np.array([236, 236, 236], float)

    def flat(rgba):
        a = rgba[..., 3:4] / 255.0
        return (rgba[..., :3] * a + bg * (1 - a)).clip(0, 255).astype(np.uint8)

    cell = 220
    rows = [(j, f) for j, (_, _, frames) in JOINTS.items() for f in frames]
    sheet = Image.new("RGB", (160 + cell * len(variants), 40 + cell * len(rows)), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    d.text((8, 8), f"SIMULATION (not Moho) - joint close-ups, {audit.sk.character_id}", fill=(200, 0, 0), font=font)
    for i, v in enumerate(variants):
        d.text((160 + i * cell + 6, 22), v, fill=(0, 0, 0), font=small)
    for r, (joint, frame) in enumerate(rows):
        bone = JOINTS[joint][0]
        world = audit.sim.world(audit.poses[frame])
        c = audit._joint_px(bone, world)
        half = 110
        d.text((8, 40 + r * cell + cell // 2 - 10), f"{joint}\nframe {frame}", fill=(0, 0, 0), font=small)
        for i, v in enumerate(variants):
            img = renders[v][0][frame]
            x0, y0 = int(c[0]) - half, int(c[1]) - half
            crop = Image.fromarray(flat(img)).crop((x0, y0, x0 + 2 * half, y0 + 2 * half)).resize((cell - 4, cell - 4))
            sheet.paste(crop, (160 + i * cell, 40 + r * cell))
    p = out / "SIMULATION_joint_closeups.jpg"
    sheet.save(p, quality=88)
    written.append(p.name)
    for v in variants:
        frames = sorted(renders[v][0])
        w, h = audit.sim.placement.canvas
        sw = w // 4
        strip = Image.new("RGB", (sw * (len(frames) + 1), h // 4 + 30), (255, 255, 255))
        ds = ImageDraw.Draw(strip)
        ds.text((6, 4), f"SIMULATION (not Moho) - {v} - {audit.sk.character_id}", fill=(200, 0, 0), font=small)
        imgs = [(1, renders[v][1])] + [(f, renders[v][0][f]) for f in frames]
        for i, (f, img) in enumerate(imgs):
            strip.paste(Image.fromarray(flat(img)).resize((sw, h // 4)), (i * sw, 30))
            ds.text((i * sw + 4, 30), str(f), fill=(0, 0, 160), font=small)
        p = out / f"SIMULATION_poses_{v}.jpg"
        strip.save(p, quality=85)
        written.append(p.name)
    return written
