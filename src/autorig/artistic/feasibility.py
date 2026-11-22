"""Phase 3.5 artwork-aware pose feasibility: can the character's artwork support this pose before it is deformed?

Classes:
- ``SUPPORTED``: the approved artwork and the garment rig render it.
- ``SUPPORTED_WITH_CORRECTIONS``: it renders with corrective artwork or correction switches (gussets, caps, shoe
  tops, garment corrections).
- ``REQUIRES_ALTERNATIVE_ARTWORK``: the front-view parts can be posed, but a drawing the pose depends on is missing or
  only pending review (a pointing hand, a deep-elbow fold, seated thighs). The pose is shown as a labelled fallback,
  never forced through heavy deformation.
- ``REQUIRES_NEW_VIEW``: the intended motion goes toward or away from the viewer (forward lean, stride, knee drive);
  front-view art cannot show it.
- ``UNSUPPORTED``: neither a fallback nor a known drawing would make it work (for example a back view without back
  artwork).

The decision uses the skeleton-only solve (no pixel deformation), the existing feasibility evaluator (missing
assets), the artistic pose's declared requirements, the view manifest and the alternative-artwork library.
"""

from __future__ import annotations

from typing import Any

CLASSES = ("SUPPORTED", "SUPPORTED_WITH_CORRECTIONS", "REQUIRES_ALTERNATIVE_ARTWORK", "REQUIRES_NEW_VIEW",
           "UNSUPPORTED")

# depth motion that needs a view other than FRONT (front-view parts only move in the picture plane)
NEW_VIEW_ACTIONS = {"lean_forward": "FRONT_THREE_QUARTER_LEFT", "walk": "SIDE_LEFT", "run": "SIDE_LEFT"}


class ArtworkFeasibilityEngine:
    def __init__(self, views=None, alternatives=None):
        self.views = views
        self.alternatives = alternatives

    def classify(self, ap, res: dict[str, Any], approvals: dict[str, Any] | None = None) -> dict[str, Any]:
        """``ap``: ArtisticPose; ``res``: a MotionEngine.pose result (skeleton_only is enough)."""
        feas = res.get("feasibility") or {}
        hands = res.get("hands") or {}
        reasons: list[str] = []
        needs: list[dict[str, Any]] = []
        view = getattr(ap, "view", "FRONT")
        if self.views is not None and not self.views.available(view):
            return {"class": "UNSUPPORTED" if not self.views.requestable(view) else "REQUIRES_NEW_VIEW",
                    "reasons": [f"view {view} has no artwork"], "needs": [{"asset": f"view:{view}", "state": "missing"}],
                    "render": "skip"}
        # hands the gesture needs: pending drawings are previewed only
        for s, h in hands.items():
            if h.get("pending_review"):
                needs.append({"asset": f"hand:{h.get('drawing')} ({s})", "state": "pending_review",
                              "why": "the gesture's hand drawing is not approved; shown as a preview"})
            if h.get("missing_preferred"):
                needs.append({"asset": f"hand:{h['missing_preferred']} ({s})", "state": "missing",
                              "why": f"preferred hand missing; showing '{h.get('drawing')}'"})
        seen_hands = {n["asset"].split(" (")[0] for n in needs}
        for m in feas.get("missing", []):
            st = m.get("state", "missing")
            if m["asset"].startswith("hand:") and st == "pending_review" and m["asset"] in seen_hands:
                continue
            if m.get("answered_by"):
                st = "answered_by:" + ",".join(f"{a['asset_id']}({a['state']})" for a in m["answered_by"])
            needs.append({"asset": m["asset"], "state": st, "why": m.get("why", "")})
        for r in getattr(ap, "requires", []):
            if not any(r.split(" ")[0] in n["asset"] for n in needs):
                needs.append({"asset": r, "state": "missing", "why": "declared by the pose"})
        action = getattr(ap, "action", "")
        new_view = NEW_VIEW_ACTIONS.get(action)
        if new_view:
            reasons.append(f"the intended motion goes toward the viewer: needs {new_view} artwork (front view shows "
                           "an in-plane approximation)")
        corrections = [c for c in res.get("correction_switches", []) if c.get("on")] + \
                      [c for c in res.get("correctives", []) if c.get("shown") and c.get("category") != "edges"]
        missing = [n for n in needs if n["state"] == "missing"]
        pending = [n for n in needs if n["state"] == "pending_review"]
        view_only = ("side or", "art:thigh_knee_up", "art:shin_tucked", "art:torso_lean", "corrective:torso_lean")
        missing_art = [n for n in missing if not n["asset"].startswith(view_only)]
        if new_view and (action == "lean_forward" or not missing_art):
            cls = "REQUIRES_NEW_VIEW"
        elif missing_art:
            cls = "REQUIRES_ALTERNATIVE_ARTWORK"
        elif corrections or feas.get("status") == "SUPPORTED_WITH_CORRECTIONS":
            cls = "SUPPORTED_WITH_CORRECTIONS"
        else:
            cls = "SUPPORTED"
        if missing:
            reasons.append("missing: " + "; ".join(n["asset"] for n in missing))
        if pending:
            reasons.append("pending review (previewed): " + "; ".join(n["asset"] for n in pending))
        if corrections:
            reasons.append("corrections: " + ", ".join(sorted({str(c.get('asset_id') or c.get('id')) for c in corrections})))
        label = cls + (f" (pending approval: {len(pending)} drawing{'s' if len(pending) != 1 else ''})" if pending else "")
        render = "preview_with_labels" if cls in ("REQUIRES_ALTERNATIVE_ARTWORK", "REQUIRES_NEW_VIEW") else "render"
        return {"class": cls, "label": label, "pending_approval": [n["asset"] for n in pending],
                "missing": [n["asset"] for n in missing], "reasons": reasons, "needs": needs, "render": render,
                "base_feasibility": feas.get("status"),
                "note": "pending drawings are previewed only; a REQUIRES_* pose is never counted as a success"}


__all__ = ["ArtworkFeasibilityEngine", "CLASSES", "NEW_VIEW_ACTIONS"]
