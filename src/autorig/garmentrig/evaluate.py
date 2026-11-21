"""GarmentDistortionEvaluator: per-part and per-garment PASS / WARNING / NEEDS_ARTWORK / FAIL for a rendered pose.

Everything is measured on the *drawn* mapping of the original pixels: rigid segment and rigid-fit overlays where
they are drawn, and the weighted joint patch elsewhere. Pixels hidden under other layers are excluded from the
stretch checks. There is no single global score. Each part gets the checks that matter for its class:

* RIGID: rigid-fit residual (shape change, px) and scale.
* LIMITED_DEFORMATION: local stretch and compression (singular values of the drawn Jacobian), inversions, the
  rigid-fit residual (shape change) and scale.
* FLEXIBLE: stretch and inversions of the *visible* joint-patch pixels (the rigid segments are exact by
  construction), and the per-segment residual.

All classes also get exposed gaps, visible seams and outline breaks near the part, visible generated
(unreviewed) pixels, and clipped patch pixels (stretch-limit conflicts). Attachments are checked between parts:
shoulder seam, armpit, elbow, cuff, wrist, hip, knee, ankle, collar and waist. Cuff displacement is measured
against the forearm.

A check that fails because corrective artwork is missing makes the part NEEDS_ARTWORK, naming the asset; any
other failure is FAIL. Thresholds are in ``THRESHOLDS`` and are documented in the Phase 3.3 report. The numbers
never claim visual quality: ``visual_review_required`` is always true.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy import ndimage

from ..phase2.skinning import PoseParams
from .structure import FLEXIBLE, LIMITED, RIGID

ORDER = {"PASS": 0, "WARNING": 1, "FAIL": 2, "NEEDS_ARTWORK": 3}

# (pass_max, warning_max): a value above warning_max fails
THRESHOLDS: dict[str, Any] = {
    RIGID: {"residual_max_px": (0.75, 1.5), "scale": (0.02, 0.05)},  # residual: 98th percentile
    LIMITED: {"stretch_p99": (0.10, 0.25), "compression_p01": (0.12, 0.30), "inverted_fraction": (0.0005, 0.003),
              "residual_rms_px": (2.5, 5.0), "scale": (0.05, 0.12)},
    FLEXIBLE: {"patch_stretch_p99": (1.0, 2.0), "patch_inverted_fraction": (0.002, 0.01),
               "segment_residual_rms_px": (1.0, 2.5)},
    "common": {"exposed_gaps": (0, 3), "visible_seams": (0, 3), "outline_breaks": (0, 2),
               "generated_visible_px": (0, 150), "clipped_patch_px": (0, 60)},
    "attachment": {"detached": "the segment between the two posed attachment points crosses transparency"},
    "cuff_displacement_px": (1.0, 3.0),
}


def _grade(v: float, lim: tuple[float, float]) -> str:
    return "PASS" if v <= lim[0] else "WARNING" if v <= lim[1] else "FAIL"


def _worst(a: str, b: str) -> str:
    return a if ORDER[a] >= ORDER[b] else b


def _procrustes(P: np.ndarray, Q: np.ndarray, robust: bool = False) -> tuple[float, float]:
    """RMS and max residual of the best rigid (rotation + translation) fit of P onto Q (``robust``: the 98th
    percentile instead of the max, so the few pixels at a part boundary that share a mesh cell with the
    neighbouring part do not dominate)."""
    if len(P) < 3:
        return 0.0, 0.0
    pc, qc = P.mean(0), Q.mean(0)
    M = (Q - qc).T @ (P - pc)
    U, _, Vt = np.linalg.svd(M)
    R = U @ Vt
    if np.linalg.det(R) < 0:
        U[:, -1] *= -1
        R = U @ Vt
    res = np.hypot(*((P - pc) @ R.T + qc - Q).T)
    return float(np.sqrt((res ** 2).mean())), float(np.percentile(res, 98) if robust else res.max())


class GarmentDistortionEvaluator:
    def __init__(self, eng, max_samples: int = 1500):
        self.eng = eng
        self.max_samples = max_samples

    def evaluate(self, res: dict[str, Any]) -> dict[str, Any]:
        eng = self.eng
        dfm, st = eng.dfm, eng.structure
        if st is None:
            return {"available": False, "reason": "legacy garment mode (no garment structure)"}
        r, pose = res["render"], res["pose"]
        pad = r["pad"]
        order = r["order"]
        img_a = r["image"][..., 3]
        H, W = img_a.shape
        top = r["top"]
        corr = res.get("correction_switches") or []
        samples: dict[int, dict[str, list]] = {}
        # sample each drawn layer's original pixels, grouped by part
        for li, cid in enumerate(order):
            if cid not in dfm.info or cid not in st.layer_parts:
                continue
            inf = dfm.info[cid]
            x0, y0 = inf["origin"]
            a0 = inf["alpha0"].copy()
            if inf["gen"] is not None:
                a0 &= ~inf["gen"]
            ay, ax = np.nonzero(a0)
            if not len(ay):
                continue
            pts = np.stack([ax + x0, ay + y0], 1).astype(np.float64)
            pid = st.ids_at(cid, pts)
            step = max(1, int(np.sqrt(len(pts) / 6000)))
            sel = np.zeros(len(pts), bool)
            sel[::step] = True
            pts, pid = pts[sel], pid[sel]
            # piece-consistent finite differences: a point drawn by a rigid overlay is differentiated with that
            # overlay's transform, a patch point with the patch mapping (never across the overlay boundary)
            q = dfm.smooth_forward(cid, pts, pose)
            qx = dfm.smooth_forward(cid, pts + [1.0, 0.0], pose)
            qy = dfm.smooth_forward(cid, pts + [0.0, 1.0], pose)
            for o in inf.get("overlays", []):
                m = dfm._mask_at(inf, o["mask"], pts) > 0.5
                if o["kind"] == "fit" and "core" in o:
                    m &= dfm._mask_at(inf, o["core"].astype(np.float32), pts) > 0.5
                if m.any():
                    A = dfm._overlay_affine(cid, o, pose)
                    for arr, off in ((q, 0.0), (qx, [1.0, 0.0]), (qy, [0.0, 1.0])):
                        arr[m] = (pts[m] + off) @ A[:2, :2].T + A[:2, 2]
            j11, j21 = qx[:, 0] - q[:, 0], qx[:, 1] - q[:, 1]
            j12, j22 = qy[:, 0] - q[:, 0], qy[:, 1] - q[:, 1]
            det = j11 * j22 - j12 * j21
            s1 = j11 ** 2 + j12 ** 2 + j21 ** 2 + j22 ** 2
            disc = np.sqrt(np.maximum(s1 ** 2 - 4 * det ** 2, 0))
            smax = np.sqrt((s1 + disc) / 2)
            smin = np.sqrt(np.maximum((s1 - disc) / 2, 0))
            cover = np.zeros(len(pts), bool)
            cov_id = np.full(len(pts), -1, np.int32)
            for oi, o in enumerate(inf.get("overlays", [])):
                m = dfm._mask_at(inf, o["mask"], pts) > 0.5
                if o["kind"] == "fit" and "core" in o:
                    m &= dfm._mask_at(inf, o["core"].astype(np.float32), pts) > 0.5
                cover |= m
                cov_id[m] = li * 100 + oi  # the last (topmost) overlay drawing the point
            qi = np.clip(np.rint(q + pad).astype(int), 0, [W - 1, H - 1])
            vis = top[qi[:, 1], qi[:, 0]] == li
            dom = cov_id
            for p in np.unique(pid):
                if p < 0:
                    continue
                m = pid == p
                d = samples.setdefault(int(p), {k: [] for k in ("P", "Q", "det", "smax", "smin", "cover", "vis",
                                                                 "dom", "layer")})
                for k, v in (("P", pts[m]), ("Q", q[m]), ("det", det[m]), ("smax", smax[m]), ("smin", smin[m]),
                             ("cover", cover[m]), ("vis", vis[m]), ("dom", dom[m])):
                    d[k].append(v)
                d["layer"].append(np.full(int(m.sum()), li))
        # posed part label image (for gaps, seams and generated pixels)
        lab = np.full((H, W), -1, np.int32)
        for p, d in samples.items():
            Q = np.concatenate(d["Q"])
            vis = np.concatenate(d["vis"])
            qi = np.clip(np.rint(Q[vis] + pad).astype(int), 0, [W - 1, H - 1])
            lab[qi[:, 1], qi[:, 0]] = p
        grown = ndimage.grey_dilation(lab + 1, size=(9, 9)) - 1
        lab = np.where(lab >= 0, lab, grown)
        tears = res.get("tears") or {}

        def count_at(pts_list) -> dict[int, int]:
            out: dict[int, int] = {}
            for x, y in pts_list:
                if 0 <= y < H and 0 <= x < W and lab[y, x] >= 0:
                    out[int(lab[y, x])] = out.get(int(lab[y, x]), 0) + 1
            return out

        gaps, seams = count_at(tears.get("exposed_gap", [])), count_at(tears.get("visible_seam", []))
        breaks = count_at(tears.get("outline_break", []))
        genv = r.get("generated_visible")
        clipped_parts = r.get("clipped_uncovered") or {}
        parts_out: dict[str, Any] = {}
        for p, d in samples.items():
            part = st.parts[p]
            D = {k: np.concatenate(v) for k, v in d.items()}
            n = len(D["P"])
            if n > self.max_samples * 4:
                keep = np.linspace(0, n - 1, self.max_samples * 4).astype(int)
                D = {k: v[keep] for k, v in D.items()}
            checks: dict[str, Any] = {}
            status = "PASS"
            th = THRESHOLDS.get(part.cls, {})
            vis = D["vis"]
            if part.cls == RIGID:
                rms, mx = _procrustes(D["P"], D["Q"], robust=True)
                sc = abs(float(np.sqrt(max(np.mean(np.abs(D["det"])), 1e-9))) - 1.0)
                checks["residual_max_px"] = (round(mx, 3), _grade(mx, th["residual_max_px"]))
                checks["scale"] = (round(sc, 4), _grade(sc, th["scale"]))
            elif part.cls == LIMITED:
                v = vis & ~D["cover"]
                if v.sum() >= 10:
                    sp = float(np.percentile(D["smax"][v], 99) - 1.0)
                    cp = float(1.0 - np.percentile(D["smin"][v], 1))
                    inv = float((D["det"][v] <= 0.05).mean())
                else:
                    sp = cp = inv = 0.0
                rms, _ = _procrustes(D["P"], D["Q"])
                sc = abs(float(np.sqrt(max(np.mean(np.abs(D["det"])), 1e-9))) - 1.0)
                checks["stretch_p99"] = (round(sp, 4), _grade(sp, th["stretch_p99"]))
                checks["compression_p01"] = (round(cp, 4), _grade(cp, th["compression_p01"]))
                checks["inverted_fraction"] = (round(inv, 5), _grade(inv, th["inverted_fraction"]))
                checks["residual_rms_px"] = (round(rms, 3), _grade(rms, th["residual_rms_px"]))
                checks["scale"] = (round(sc, 4), _grade(sc, th["scale"]))
            else:
                v = vis & ~D["cover"]
                if v.sum() >= 10:
                    sp = float(np.percentile(D["smax"][v], 99) - 1.0)
                    inv = float((D["det"][v] <= 0.05).mean())
                else:
                    sp, inv = 0.0, 0.0
                seg = 0.0  # per rigid segment (grouped by the overlay that draws it)
                for b in np.unique(D["dom"]):
                    m = D["dom"] == b
                    if b >= 0 and m.sum() >= 20:
                        seg = max(seg, _procrustes(D["P"][m], D["Q"][m])[0])
                checks["patch_stretch_p99"] = (round(sp, 4), _grade(sp, th["patch_stretch_p99"]))
                checks["patch_inverted_fraction"] = (round(inv, 5), _grade(inv, th["patch_inverted_fraction"]))
                checks["segment_residual_rms_px"] = (round(seg, 3), _grade(seg, th["segment_residual_rms_px"]))
                checks["patch_visible_fraction"] = (round(float(v.mean()) if len(v) else 0.0, 3), "PASS")
            cm = THRESHOLDS["common"]
            checks["exposed_gaps"] = (gaps.get(p, 0), _grade(gaps.get(p, 0), cm["exposed_gaps"]))
            checks["visible_seams"] = (seams.get(p, 0), _grade(seams.get(p, 0), cm["visible_seams"]))
            checks["outline_breaks"] = (breaks.get(p, 0), _grade(breaks.get(p, 0), cm["outline_breaks"]))
            gv = int((genv & (lab == p)).sum()) if genv is not None else 0
            # generated (hidden, reconstructed) pixels are meant to show when a pose reveals them; they are pending
            # review, never a deformation failure: at most a WARNING
            checks["generated_visible_px"] = (gv, "PASS" if gv <= cm["generated_visible_px"][0] else "WARNING")
            clip = int(clipped_parts.get(part.name, 0))
            checks["clipped_patch_px"] = (clip, _grade(clip, cm["clipped_patch_px"]))
            for k, (_, g) in checks.items():
                status = _worst(status, g)
            # corrections that cover this part, are on, and have no approved drawing shown (missing, or pending
            # review and not previewed): the part's failures are missing artwork, not a deformation bug
            needs = [c for c in corr if c.get("on") and part.name in (c.get("garment_parts") or [])
                     and not c.get("applied") and c.get("asset_state") in ("missing", "pending_review")]
            req = [c for c in needs if c.get("required") or c.get("measure") in ("sitting", "torso_lean_sagittal")]
            missing = [f"corrective:{c['asset']}" + (" (pending review)" if c.get("asset_state") == "pending_review"
                                                      else "") for c in needs]
            if status in ("WARNING", "FAIL") and req:
                status = "NEEDS_ARTWORK"
            elif status == "FAIL" and needs:
                status = "NEEDS_ARTWORK"
            elif req:
                status = "NEEDS_ARTWORK"
            parts_out[part.name] = {"class": part.cls, "garment": part.garment, "status": status,
                                    "checks": {k: {"value": v, "grade": g} for k, (v, g) in checks.items()},
                                    "samples": int(len(D["P"])), "visible_fraction": round(float(vis.mean()), 3),
                                    "missing_artwork": missing}
        att = self._attachments(res, pad)
        for a in att:
            if a["status"] != "PASS":
                for pn in a["parts"]:
                    if pn in parts_out:
                        parts_out[pn]["status"] = _worst(parts_out[pn]["status"], a["status"])
                        parts_out[pn].setdefault("attachment_issues", []).append(a["name"])
        cuffs = self._cuffs(res, samples)
        for s, c in cuffs.items():
            pn = f"shirt_cuff_{s}"
            if pn in parts_out:
                parts_out[pn]["checks"]["cuff_displacement_px"] = {"value": c["displacement_px"], "grade": c["grade"]}
                parts_out[pn]["status"] = _worst(parts_out[pn]["status"], c["grade"])
        garments: dict[str, Any] = {}
        for name, p in parts_out.items():
            g = garments.setdefault(p["garment"], {"status": "PASS", "parts": {}, "missing_artwork": []})
            g["parts"][name] = p["status"]
            g["status"] = _worst(g["status"], p["status"])
            for m in p["missing_artwork"]:
                if m not in g["missing_artwork"]:
                    g["missing_artwork"].append(m)
        missing_all = sorted({m for p in parts_out.values() for m in p["missing_artwork"]})
        return {"available": True, "visual_review_required": True, "garments": garments, "parts": parts_out,
                "attachments": att, "cuffs": cuffs, "missing_artwork": missing_all,
                "counts": {k: sum(1 for p in parts_out.values() if p["status"] == k) for k in ORDER},
                "thresholds": "THRESHOLDS in autorig.garmentrig.evaluate (documented in the Phase 3.3 report)"}

    def _attachments(self, res: dict[str, Any], pad: int) -> list[dict[str, Any]]:
        eng = self.eng
        dfm, st = eng.dfm, eng.structure
        r, pose = res["render"], res["pose"]
        a_img = r["image"][..., 3]
        H, W = a_img.shape
        out = []
        drawn = set(r["order"])
        for at in st.attachments:
            pt = np.array([at["rest_point"]], float)
            posed = []
            for pn in at["parts"]:
                pid = st.index.get(pn)
                best = None
                for cid in r["order"]:
                    if cid not in dfm.info or cid not in st.layer_parts:
                        continue
                    lp = st.layer_parts[cid]
                    x0, y0 = lp.origin
                    h, w = lp.ids.shape
                    xi, yi = int(round(pt[0, 0] - x0)), int(round(pt[0, 1] - y0))
                    y_lo, y_hi, x_lo, x_hi = max(0, yi - 8), min(h, yi + 9), max(0, xi - 8), min(w, xi + 9)
                    if y_lo >= y_hi or x_lo >= x_hi:
                        continue
                    win = lp.ids[y_lo:y_hi, x_lo:x_hi] == pid
                    if win.any():
                        best = cid
                        break
                if best is not None:
                    posed.append(dfm.forward(best, pt, pose)[0])
            if len(posed) < 2:
                continue
            pa, pb = posed
            dist = float(np.hypot(*(pa - pb)))
            ts = np.linspace(0, 1, 9)
            seg = pa[None, :] + ts[:, None] * (pb - pa)[None, :] + pad
            xi = np.clip(np.rint(seg[:, 0]).astype(int), 0, W - 1)
            yi = np.clip(np.rint(seg[:, 1]).astype(int), 0, H - 1)
            connected = bool((a_img[yi, xi] >= 128).all())
            status = "PASS" if (dist <= 3.0 or connected) else "FAIL"
            note = "together" if dist <= 3.0 else ("bridged by the joint patch / overlap" if connected else
                                                   "detached: transparency between the two sides")
            out.append({"name": at["name"], "parts": at["parts"], "kind": at["kind"], "distance_px": round(dist, 2),
                        "connected": connected, "status": status, "note": note})
        del drawn
        return out

    def _cuffs(self, res: dict[str, Any], samples) -> dict[str, Any]:
        eng = self.eng
        st, dfm = eng.structure, eng.dfm
        out = {}
        for s in ("r", "l"):
            pid = st.index.get(f"shirt_cuff_{s}")
            if pid is None or pid not in samples:
                continue
            P = np.concatenate(samples[pid]["P"])
            Q = np.concatenate(samples[pid]["Q"])
            T = dfm.world(eng.sk.idx[f"forearm_{s}"], res["pose"])
            exp = P @ T[:2, :2].T + T[:2, 2]
            disp = float(np.percentile(np.hypot(*(Q - exp).T), 95)) if len(P) else 0.0
            out[s] = {"displacement_px": round(disp, 3), "grade": _grade(disp, THRESHOLDS["cuff_displacement_px"]),
                      "meaning": "95th percentile distance of drawn cuff pixels from the forearm's rigid transform "
                                 "(hand rotation and hand switches must not move the cuff)"}
        return out


def summary_line(ev: dict[str, Any]) -> str:
    if not ev.get("available"):
        return "garment evaluation unavailable"
    g = ev["garments"]
    return "; ".join(f"{k}: {v['status']}" for k, v in sorted(g.items()))


__all__ = ["GarmentDistortionEvaluator", "THRESHOLDS", "summary_line", "PoseParams"]
