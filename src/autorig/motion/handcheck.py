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


def switch_consistency(res: dict[str, Any], eng, allow_pending: bool) -> dict[str, Any]:
    """Phase 3.8.1: the requested hand switch, the resolved switch and the rendered drawing agree, per side.

    For each hand it compares:
    - the switch group's resolution (``requested`` -> ``active``, with the reason in ``note``);
    - the pose, cid and layer the render reports for the hand;
    - the drawing the hand library holds for that pose on that side.

    A substitution (requested != active) must carry a note, so it is never silent. A pending drawing may be shown
    only when previewing (``allow_pending``). The shown drawing's handedness must be the side's (store drawings use
    their metadata ``use_side``). ``ok`` is False on any mismatch; nothing is corrected here."""
    out: dict[str, Any] = {"ok": True, "sides": {}}
    for s in ("r", "l"):
        g = (res.get("switches") or {}).get(f"hand_{s}") or {}
        h = (res.get("hands") or {}).get(s) or {}
        v = eng.hand_lib.hands[s].get(h.get("pose"))
        hm = ((v.meta or {}).get("hand_meta") if v is not None else None) or {}
        issues: list[str] = []
        if g and g.get("active") != h.get("pose"):
            issues.append(f"switch resolved to {g.get('active')!r} but the render shows {h.get('pose')!r}")
        if v is None:
            issues.append(f"no drawing for {h.get('pose')!r} on this side")
        else:
            if h.get("layer") != v.layer:
                issues.append(f"rendered layer {h.get('layer')!r} is not the {h.get('pose')!r} drawing ({v.layer!r})")
            if h.get("cid") != v.cid:
                issues.append(f"rendered cid {h.get('cid')!r} != library cid {v.cid!r}")
        substituted = bool(g) and g.get("requested") != g.get("active")
        if substituted and not g.get("note"):
            issues.append(f"silent substitution: {g.get('requested')!r} requested, {g.get('active')!r} shown")
        if h.get("pending_review") and not allow_pending:
            issues.append("a pending drawing is shown outside a review preview")
        use = hm.get("use_side")
        if use is not None and use != s:
            issues.append(f"the shown drawing belongs to the {'right' if use == 'r' else 'left'} hand")
        rec = {"requested": g.get("requested"), "active": g.get("active"), "source": g.get("source"),
               "rendered_pose": h.get("pose"), "cid": h.get("cid"), "layer": h.get("layer"),
               "pending_review": bool(h.get("pending_review")), "substituted": substituted, "note": g.get("note"),
               "handedness": hm.get("handedness"), "issues": issues}
        out["sides"][s] = rec
        out["ok"] = out["ok"] and not issues
    return out
