"""Phase 3.7 shoulder silhouette check (``ShoulderSilhouetteCheck``): blazer fullness on a rendered frame.

The T-pose drawing has a nearly level shoulder line. When the arms come down, bone rotation alone keeps that line
square, and a shrug's clavicle elevation lifts it further, into "inflated pads". The Phase 3.6 shrug_low frame
measured -6 deg (the line rising outward) and a shoulder top 10 to 16 px above neutral.

Per side, on the figure's alpha silhouette between the collar junction and the outer shoulder edge, the check
measures:
- ``slope_deg``: the fitted shoulder line, + going down outward. The character sheet's arms-down front view measures
  31 deg (right) and 47 deg (left, where the backpack strap distorts it);
- ``hump_px``: the most the profile bulges above the chord between the collar junction and the outer shoulder;
- ``top_y``: the highest shoulder point. Compared with a reference frame (the neutral standing pose) this gives
  ``rise_px``.

Thresholds (``THRESHOLDS``) flag shrug frames whose shoulder line rises outward or whose top rises too far. This is a
geometric hint about the silhouette; it does not judge visual quality.
"""

from __future__ import annotations

from typing import Any

import numpy as np

THRESHOLDS = {"min_slope_deg": -1.0, "max_rise_px": 8.0, "max_hump_px": 16.0}
REFERENCE_FRONT_VIEW_SLOPE_DEG = {"r": 30.7, "l": 47.5}  # measured on references/schoolboy/front_view.png


def _profile(sil: np.ndarray, x0: float, x1: float, ytop: float, ybot: float):
    xs = np.arange(int(min(x0, x1)), int(max(x0, x1)))
    ys = []
    for x in xs:
        col = sil[int(max(ytop, 0)):int(ybot), x] if 0 <= x < sil.shape[1] else np.zeros(0, bool)
        nz = np.nonzero(col)[0]
        ys.append(ytop + nz[0] if len(nz) else np.nan)
    return xs, np.array(ys, float)


def measure(image: np.ndarray, joints: dict[str, Any], pad: int) -> dict[str, Any]:
    sil = image[..., 3] > 127
    out: dict[str, Any] = {}
    for s, sg in (("r", -1), ("l", 1)):
        cx, cy = joints[f"clavicle_{s}"][0] + pad, joints[f"clavicle_{s}"][1] + pad
        px, py = joints[f"upper_arm_{s}"][0] + pad, joints[f"upper_arm_{s}"][1] + pad
        band = sil[int(py - 40):int(py + 40)]
        cols = np.nonzero(band.any(0))[0]
        if not len(cols):
            out[s] = {"status": "UNCHECKED"}
            continue
        edge = cols.min() if sg < 0 else cols.max()
        outer, neck = edge - sg * 4, cx + sg * 35
        yt = joints["neck"][1] + pad - 25
        xs, ys = _profile(sil, neck, outer, yt, py + 120)
        ok = ~np.isnan(ys)
        xs, ys = xs[ok], ys[ok]
        if len(xs) < 8:
            out[s] = {"status": "UNCHECKED"}
            continue
        u = (xs - neck) * sg / max(abs(outer - neck), 1.0)
        m = (u > 0.15) & (u < 0.85)
        k = float(np.polyfit(xs[m] * sg, ys[m], 1)[0])
        i0, i1 = int(np.argmin(abs(u - 0.1))), int(np.argmin(abs(u - 0.95)))
        dx = float(xs[i1] - xs[i0])  # negative on the character's right side (profile runs right to left)
        dx = dx if abs(dx) > 1e-6 else 1e-6
        chord = ys[i0] + (ys[i1] - ys[i0]) * (xs - xs[i0]) / dx
        sel = (u >= 0.1) & (u <= 0.95)
        out[s] = {"status": "OK", "slope_deg": round(float(np.degrees(np.arctan(k))), 2),
                  "hump_px": round(float(np.max(chord[sel] - ys[sel])), 2),
                  "top_y": round(float(np.min(ys)) - pad, 2), "outer_x": round(float(edge - pad), 2)}
    return out


MAX_ABDUCTION_DEG = 40.0  # above this the arm itself forms the outer shoulder: the shoulder-line check does not apply


def check(m: dict[str, Any], reference: dict[str, Any] | None = None, shrug: bool = False,
          abduction: dict[str, float] | None = None) -> dict[str, Any]:
    """Flags per side. ``reference``: the neutral frame's measurement (rise = how much higher the shoulder top is).
    ``abduction``: upper-arm abduction per side (deg); a raised arm is reported as not applicable."""
    flags = []
    for s in ("r", "l"):
        x = m.get(s) or {}
        if x.get("status") != "OK":
            continue
        if abduction is not None and abs(float(abduction.get(s, 0.0))) > MAX_ABDUCTION_DEG:
            x["applies"] = False
            x["note"] = f"arm raised ({float(abduction[s]):.0f} deg): the arm forms the outer shoulder"
            continue
        x["applies"] = True
        if reference and (reference.get(s) or {}).get("status") == "OK":
            x["rise_px"] = round(reference[s]["top_y"] - x["top_y"], 2)
            if shrug and x["rise_px"] > THRESHOLDS["max_rise_px"]:
                flags.append(f"{s}: shoulder top {x['rise_px']:.0f} px above neutral (> {THRESHOLDS['max_rise_px']:.0f}): "
                             "inflated shrug")
        if x["slope_deg"] < THRESHOLDS["min_slope_deg"]:
            flags.append(f"{s}: shoulder line rises outward ({x['slope_deg']:.1f} deg): padded look")
        if x["hump_px"] > THRESHOLDS["max_hump_px"]:
            flags.append(f"{s}: shoulder cap bulges {x['hump_px']:.0f} px above the shoulder line")
    return {"sides": m, "flags": flags, "status": "WARNING" if flags else "PASS", "thresholds": THRESHOLDS,
            "reference_front_view_slope_deg": REFERENCE_FRONT_VIEW_SLOPE_DEG,
            "artistic_review_required": True}


def abductions(result: dict[str, Any]) -> dict[str, float]:
    a = (result.get("solve") or {}).get("anatomical") or {}
    return {s: float(a.get(f"upper_arm_{s}_abduction", 0.0)) for s in ("r", "l")}


__all__ = ["measure", "check", "abductions", "THRESHOLDS", "REFERENCE_FRONT_VIEW_SLOPE_DEG", "MAX_ABDUCTION_DEG"]
