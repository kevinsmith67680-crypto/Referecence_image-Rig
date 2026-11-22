"""Phase 3.5 silhouette analysis: does the pose read as a solid black shape?

``SilhouetteQualityEvaluator`` works on the rendered solid silhouette (alpha > 0.5) and the per-layer masks of the same
render. Its warnings are kept apart from the geometric deformation warnings: a pose without gaps can still have an
unreadable silhouette.

Checks (deterministic image measurements):
1. hands_on_contour: share of each hand's outline that faces the background. A hand inside the body silhouette does
   not read.
2. arm_separation_px: background between each forearm/hand and the body, measured horizontally (negative space).
3. tangencies: two parts' contours touching over a short length without overlapping (hand on thigh edge, elbow on
   torso edge).
4. symmetry_iou: left/right mirror IoU of the silhouette about the pelvis. High = mannequin symmetry.
5. weight_clarity: weight share plus pelvis roll (a weight-bearing leg must be identifiable).
6. head_shoulder: head tilt against the shoulder line (counterbalance vs both tilting the same way).
7. legs_crossing: thigh masks overlapping each other.
8. elbow_contour: posed elbow within a few px of the torso silhouette edge (confusing contour).
9. components: the silhouette must be one connected shape (disconnected outline).
10. gesture: pose-specific readability, e.g. a pointing hand that must sit on the contour, away from the head.

The result carries metrics, warnings (code, severity, message) and the silhouette image. It never claims artistic
quality: ``artistic_review_required`` is always true.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from scipy import ndimage

THRESHOLDS = {
    "hand_contour_min": (0.25, "a gesturing hand needs at least this share of its outline on the silhouette edge"),
    "arm_separation_min_px": (6, "negative space between a hanging/gesturing arm and the body"),
    "symmetry_max": (0.90, "silhouette left/right IoU above this reads as a symmetric mannequin (unless intended)"),
    "tangency_max_px": (40, "contour contact length between two parts without overlap"),
    "elbow_contour_px": (6, "posed elbow this close to the torso edge makes a confusing contour"),
    "weight_min_shift": (0.08, "weight share away from 0.5 (or a pelvis roll >= 2 deg) that makes a weight leg readable"),
}

SYMMETRIC_OK = {"shrug", "sit", "squat", "both_hands_raised", "walk", "run"}


def _layer(alphas: dict[str, np.ndarray], prefixes: tuple[str, ...]) -> np.ndarray | None:
    out = None
    for c, a in alphas.items():
        if c.split("__")[0] in prefixes or c.startswith(tuple(f"{p}__" for p in prefixes)):
            out = a.copy() if out is None else out | a
    return out


class SilhouetteQualityEvaluator:
    def evaluate(self, res: dict[str, Any], action: str = "", intended: dict[str, Any] | None = None) -> dict[str, Any]:
        R = res["render"]
        a = res["image"][..., 3] > 127
        al = R["alphas"]
        pad = res["pad"]
        J = {k: (v[0] + pad, v[1] + pad) for k, v in res["solve"]["joints_posed"].items()}
        bg = ~a
        body = _layer(al, ("torso", "pelvis", "neck", "thigh_r", "thigh_l", "shin_r", "shin_l"))
        if body is None:
            body = np.zeros_like(a)
        warn: list[dict[str, Any]] = []
        m: dict[str, Any] = {}
        intended = intended or {}
        gesturing = {s: intended.get(f"gesture_{s}", "hang") for s in ("r", "l")}
        # 1. hands on the contour
        for s in ("r", "l"):
            h = _layer(al, (f"hand_{s}",))
            if h is None or not h.any():
                m[f"hand_contour_{s}"] = None
                continue
            edge = h & ~ndimage.binary_erosion(h)
            on = edge & ndimage.binary_dilation(bg, iterations=1)
            frac = float(on.sum() / max(edge.sum(), 1))
            m[f"hand_contour_{s}"] = round(frac, 3)
            need = THRESHOLDS["hand_contour_min"][0] if gesturing[s] not in ("knee", "chin", "hip") else 0.0
            if frac < need:
                warn.append({"code": "hand_inside_silhouette", "side": s, "severity": "high" if frac < 0.08 else "medium",
                             "message": f"{s} hand reads inside the body silhouette ({frac:.0%} of its outline on the edge)"})
        # 2. arm separation (negative space), rows of the forearm + hand
        for s in ("r", "l"):
            arm = _layer(al, (f"forearm_{s}", f"hand_{s}"))
            if arm is None:
                continue
            best = 0
            for y in np.nonzero(arm.any(1))[0][::2]:
                xa, xb = np.nonzero(arm[y])[0], np.nonzero(body[y])[0]
                if not len(xa) or not len(xb):
                    continue
                gap = (xb.min() - xa.max() - 1) if s == "r" else (xa.min() - xb.max() - 1)
                if gap > 0:
                    best = max(best, int(gap))
            m[f"arm_separation_{s}_px"] = best
            if best < THRESHOLDS["arm_separation_min_px"][0] and gesturing[s] in ("hang", "reach", "raised"):
                warn.append({"code": "arm_merged_with_body", "side": s, "severity": "medium",
                             "message": f"no negative space between the {s} arm and the body"})
        # 3. tangencies: arm/hand contour touching the body contour without overlapping it
        tang = {}
        for s in ("r", "l"):
            arm = _layer(al, (f"forearm_{s}", f"hand_{s}", f"upper_arm_{s}"))
            if arm is None:
                continue
            ov = int((arm & body).sum())
            touch = ndimage.binary_dilation(arm, iterations=2) & body & ~arm
            n = int(touch.sum())
            tang[s] = n if ov < 30 else 0
            if ov < 30 and n > THRESHOLDS["tangency_max_px"][0] // 4:
                warn.append({"code": "tangency", "side": s, "severity": "low",
                             "message": f"{s} arm grazes the body outline ({n} px contact, no overlap)"})
        m["tangency_px"] = tang
        # 4. symmetry about the pelvis
        cx = int(round(J["pelvis"][0]))
        ys, xs = np.nonzero(a)
        w = int(min(cx - xs.min(), xs.max() - cx))
        L = a[:, cx - w:cx]
        Rr = a[:, cx + 1:cx + 1 + w][:, ::-1]
        sym = float((L & Rr).sum() / max((L | Rr).sum(), 1))
        m["symmetry_iou"] = round(sym, 3)
        if sym > THRESHOLDS["symmetry_max"][0] and action not in SYMMETRIC_OK:
            warn.append({"code": "mannequin_symmetry", "severity": "medium",
                         "message": f"silhouette is almost symmetric (IoU {sym:.2f}): stiff, mannequin-like"})
        # 5. weight clarity
        it = res.get("intent")
        wl = float(getattr(it, "weight_left", 0.5)) if it is not None else 0.5
        roll = float(getattr(it, "pelvis_roll", 0.0)) if it is not None else 0.0
        m["weight_left"], m["pelvis_roll"] = round(wl, 3), round(roll, 2)
        if abs(wl - 0.5) < THRESHOLDS["weight_min_shift"][0] and abs(roll) < 2.0 and action not in SYMMETRIC_OK \
                and getattr(it, "balance", "static") == "static":
            warn.append({"code": "unclear_weight_leg", "severity": "low",
                         "message": "even weight and a level pelvis: no weight-bearing leg reads"})
        # 6. head vs shoulders (counterbalance)
        an = res["solve"].get("anatomical") or {}
        sh = [J.get("upper_arm_r"), J.get("upper_arm_l")]
        if all(v is not None for v in sh):
            sh_tilt = float(np.degrees(np.arctan2(sh[1][1] - sh[0][1], sh[1][0] - sh[0][0])))
            head_tilt = float(getattr(it, "head", 0.0)) if it is not None else 0.0
            m["shoulder_tilt_deg"], m["head_tilt_deg"] = round(sh_tilt, 2), round(head_tilt, 2)
            if abs(sh_tilt) > 3.0 and abs(head_tilt) > 3.0 and np.sign(sh_tilt) == np.sign(-head_tilt) \
                    and abs(sh_tilt) + abs(head_tilt) > 14.0:
                warn.append({"code": "head_follows_shoulders", "severity": "low",
                             "message": "head tilts with the shoulder line instead of countering it"})
        del an
        # 7. legs crossing
        tr, tl = _layer(al, ("thigh_r",)), _layer(al, ("thigh_l",))
        if tr is not None and tl is not None:
            cross = int((tr & tl).sum())
            m["thigh_overlap_px"] = cross
            if cross > 400 and action not in ("walk", "run"):
                warn.append({"code": "legs_crossing", "severity": "medium", "message": f"thighs overlap ({cross} px)"})
        # 8. elbow contour
        torso = _layer(al, ("torso",))
        if torso is not None:
            dt = ndimage.distance_transform_edt(~(torso & ~ndimage.binary_erosion(torso)))
            for s in ("r", "l"):
                e = J.get(f"forearm_{s}")
                if e is None:
                    continue
                x, y = int(round(e[0])), int(round(e[1]))
                if 0 <= y < dt.shape[0] and 0 <= x < dt.shape[1]:
                    d = float(dt[y, x])
                    m[f"elbow_to_torso_edge_{s}_px"] = round(d, 1)
                    if d < THRESHOLDS["elbow_contour_px"][0] and gesturing[s] in ("hang", "reach"):
                        warn.append({"code": "elbow_on_torso_contour", "side": s, "severity": "low",
                                     "message": f"{s} elbow sits on the torso outline (confusing contour)"})
        # 9. connected silhouette
        lab, n = ndimage.label(a)
        sizes = ndimage.sum(a, lab, range(1, n + 1)) if n else []
        comps = int(sum(1 for z in sizes if z > 40))
        m["components"] = comps
        if comps > 1:
            warn.append({"code": "disconnected_outline", "severity": "high",
                         "message": f"silhouette has {comps} separate pieces"})
        # 10. gesture readability
        if action.startswith("point"):
            s = "r" if action.endswith("right") else "l"
            hc = m.get(f"hand_contour_{s}") or 0.0
            hd = res["hands"].get(s, {}).get("drawing")
            hw = J.get(f"hand_{s}")
            head = J.get("head")
            far = hw is not None and head is not None and float(np.hypot(hw[0] - head[0], hw[1] - head[1])) > 150
            m["pointing_hand"] = hd
            if hd not in ("point", "pointing"):
                warn.append({"code": "gesture_hand_wrong", "side": s, "severity": "high",
                             "message": f"pointing pose shows the '{hd}' hand: reads as a wave, not a point"})
            if hc < 0.4 or not far:
                warn.append({"code": "gesture_unclear", "side": s, "severity": "medium",
                             "message": "pointing hand is not clear of the head and body silhouette"})
        sev = {"high": 3, "medium": 2, "low": 1}
        score = sum(sev[w["severity"]] for w in warn)
        sil = np.where(a, 25, 245).astype(np.uint8)
        return {"metrics": m, "warnings": warn, "penalty": score, "silhouette": sil,
                "readable": not any(w["severity"] == "high" for w in warn), "artistic_review_required": True}


__all__ = ["SilhouetteQualityEvaluator", "THRESHOLDS"]
