"""Phase 3.7 per-variant wrist alignment check (``WristAlignmentCheck``) on a rendered pose.

Placement itself stays the WristAlignmentController's job: the opening centre goes on the cuff, the wrist axis goes
on the hand bone, the drawing is rotated about the wrist pivot and one scale is used per side and library. This check
verifies the result in the posed render, per side:
- ``attached``: the hand covers the sleeve opening, so it is not detached (``fill`` >= 0.4 of the cuff width is hand
  3 px past the cuff, measured by ``handstore.posed_seam``; a slimmer wrist than the cuff is normal);
- ``centred``: the hand's centre at the cuff is within 35 % of the cuff width of the cuff centre;
- ``no_duplicate_cuff``: the hand layer shows at most 40 white cuff-like pixels within 40 px past the cuff;
- ``rotation``: the drawing is turned from how it was drawn no more than its metadata tolerance;
- ``proportion``: the placed scale equals the library's side scale (no per-pose scaling);
- ``arm_unchanged``: the hand never moves the arm. The forearm and upper-arm joints equal the skeleton-only solve
  (checked by the caller when comparing variants).

It never edits placement or arm geometry: a failing drawing is reported, and the fix is a different drawing or an
artist offset (``handstore.placement``).
"""

from __future__ import annotations

from typing import Any

THRESHOLDS = {"min_fill": 0.4, "max_center_offset_frac": 0.35, "max_own_white_px": 40, "min_cuff_px": 20.0}


def wrist_check(res: dict[str, Any], eng, side: str) -> dict[str, Any]:
    from ..handstore import posed_seam  # noqa: PLC0415

    h = (res.get("hands") or {}).get(side) or {}
    layer = h.get("layer")
    if not layer or "render" not in res:
        return {"status": "UNCHECKED", "reason": "no render"}
    seam = posed_seam(res, eng, side, layer)
    dec = ((res.get("hand_selection") or {}).get("decisions") or {}).get(side) or {}
    oc = dec.get("override_check") or {}
    v = eng.hand_lib.hands[side].get(h.get("pose"))
    hm = ((v.meta or {}).get("hand_meta") if v is not None else None) or {}
    out: dict[str, Any] = {"side": side, "pose": h.get("pose"), "cid": h.get("cid"), "layer": layer,
                           "approval": (hm.get("approval") or {}).get("state"), "checks": {}, "seam": seam}
    c = out["checks"]
    if h.get("cid") == "original":
        # the reference hand is drawn together with its own cuff: there is no separate opening to measure
        out["note"] = "original reference pixels: hand and cuff are drawn together (attachment not re-measured)"
    elif seam.get("status") != "OK":
        c["attached"] = {"ok": False, "reason": "seam not measurable"}
    else:
        fill = float(seam.get("fill", 0.0))
        c["attached"] = {"ok": seam.get("covered", False) and fill >= THRESHOLDS["min_fill"], "fill": fill}
        if seam.get("covered") and (seam.get("cuff_width_px") or 0.0) >= THRESHOLDS["min_cuff_px"]:
            off = abs(seam["hand_center_q_px"] - seam["cuff_center_q_px"])
            w = max(seam.get("cuff_width_px") or 1.0, 1.0)
            c["centred"] = {"ok": off <= THRESHOLDS["max_center_offset_frac"] * w, "offset_px": round(off, 2),
                            "cuff_width_px": w}
        ow = seam.get("own_white_px_near_wrist")
        c["no_duplicate_cuff"] = {"ok": ow is None or ow <= THRESHOLDS["max_own_white_px"], "own_white_px": ow}
    rot = oc.get("rotation_from_drawn_deg")
    tol = hm.get("rotation_tolerance_deg")
    if rot is not None and tol is not None:
        c["rotation"] = {"ok": abs(rot) <= float(tol) + 1e-9, "rotation_from_drawn_deg": rot, "tolerance_deg": tol}
    pl = (v.placement or {}) if v is not None else {}
    sc, side_sc = (pl.get("final") or {}).get("scale"), pl.get("side_scale")
    if sc is not None and side_sc is not None:
        c["proportion"] = {"ok": abs(float(sc) - float(side_sc)) <= 1e-3 * max(1.0, float(side_sc)),
                           "scale": round(float(sc), 5), "side_scale": side_sc}
    bad = [k for k, x in c.items() if not x.get("ok", False)]
    out["status"] = "PASS" if not bad else "FAIL"
    out["failed"] = bad
    return out


__all__ = ["wrist_check", "THRESHOLDS"]
