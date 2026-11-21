"""PoseFeasibilityEvaluator: which poses the character's actual artwork can support.

Statuses:
* SUPPORTED: original (or approved) artwork and standard joint deformation only.
* SUPPORTED_WITH_CORRECTIONS: needs corrective deformation (rotation-dependent bands, garment rules, hem
  lag) and/or corrective artwork that the user has *approved*.
* NEEDS_ARTWORK: a required drawing is missing, or exists only unapproved (pending review or rejected). The
  missing asset is named, and a simpler supported alternative is offered when one exists.
* UNSUPPORTED: the request contradicts the rig's view (for example a side-view gait on a front-view rig).

Pending-review artwork is never treated as approved. It can be *previewed* (``allow_pending``), but the pose
keeps NEEDS_ARTWORK until the user approves the asset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..phase2.skeleton import SIDES
from .intent import PoseIntent
from .planner import PresetSpec

SUPPORTED = "SUPPORTED"
WITH_CORRECTIONS = "SUPPORTED_WITH_CORRECTIONS"
NEEDS_ARTWORK = "NEEDS_ARTWORK"
UNSUPPORTED = "UNSUPPORTED"
ORDER = [SUPPORTED, WITH_CORRECTIONS, NEEDS_ARTWORK, UNSUPPORTED]


@dataclass
class ArtInventory:
    view: str
    layers: set[str]
    hands: dict[str, dict[str, str]]  # side -> drawing name -> status ("original" for the reference hand)
    correctives: dict[str, str]  # corrective asset id -> status
    art: dict[str, str] = field(default_factory=dict)  # extra drawings (foreshortened limbs, seats ...) -> status

    @classmethod
    def build(cls, layers: set[str], variants: set[str], approvals: dict[str, Any], view: str = "front",
              corrective_ids: list[str] | None = None) -> "ArtInventory":
        st = lambda key: (approvals.get(key) or {}).get("status", "pending_review")
        hands = {s: {"open": "original"} for s in SIDES}
        for v in variants:
            if v.startswith("hand_") and "__" in v:
                base, name = v.split("__", 1)
                hands[base[-1]][name] = _norm(st(f"corrective:hand__{name}"))
        corr = {c: _norm(st(f"corrective:{c}")) for c in (corrective_ids or [])}
        return cls(view, set(layers), hands, corr)

    def state(self, asset: str, sides: list[str]) -> str:
        kind, name = asset.split(":", 1)
        if kind == "hand":
            states = [self.hands.get(s, {}).get(name, "missing") for s in sides] or ["missing"]
            for k in ("missing", "rejected", "pending_review"):
                if k in states:
                    return k
            return "approved"
        if kind == "corrective":
            hits = [v for k, v in self.correctives.items() if k.startswith(name)]
            if not hits:
                return "missing"
            return "approved" if all(h == "approved" for h in hits) else "pending_review"
        return self.art.get(name, "missing")


def _norm(s: str) -> str:
    return {"approved": "approved", "rejected": "rejected"}.get(s, "pending_review")


class PoseFeasibilityEvaluator:
    def __init__(self, inv: ArtInventory):
        self.inv = inv

    def choose_hands(self, it: PoseIntent, allow_pending: bool) -> dict[str, Any]:
        """Per side: the best available hand drawing. Approved first; pending only when previewing."""
        out = {}
        for s in SIDES:
            arm = it.arms.get(s)
            prefs = arm.hand if arm else ["open"]
            avail = self.inv.hands.get(s, {})
            chosen, note, pending = "open", None, False
            for name in prefs:
                stt = avail.get(name, "missing")
                if stt in ("original", "approved"):
                    chosen = name
                    break
                if stt == "pending_review" and allow_pending:
                    chosen, pending = name, True
                    break
            if chosen != prefs[0]:
                note = f"{prefs[0]} hand is {avail.get(prefs[0], 'missing')}: using '{chosen}'"
            out[s] = {"drawing": chosen, "preferred": prefs[0], "pending_review": pending, "note": note}
        return out

    def evaluate(self, spec: PresetSpec, it: PoseIntent, hands: dict[str, Any], active_corrections: list[dict],
                 solver_notes: list[str] | None = None, tears_need_gusset: bool = False,
                 exaggeration: float = 1.0) -> dict[str, Any]:
        missing, optional, used = [], [], []
        if spec.view != self.inv.view:
            return {"status": UNSUPPORTED, "missing": [], "alternative": spec.fallback,
                    "reason": f"preset is drawn for a {spec.view} view; this rig is {self.inv.view}-view"}
        for req in spec.requires:
            kind, name = req.asset.split(":", 1)
            sides = [s for s in SIDES if kind != "hand" or (s in it.arms and name in it.arms[s].hand)]
            stt = self.inv.state(req.asset, sides or list(SIDES))
            entry = {"asset": req.asset, "why": req.why, "state": stt, "sides": sides}
            required = not req.optional
            if kind == "corrective" and tears_need_gusset:
                required = True
                entry["why"] += " (the armpit tears without it in this pose)"
            if kind == "art" and req.optional and spec.kind == "gait" and exaggeration > 1.0:
                required = True
                entry["why"] += " (exaggeration > 1 asks for more knee drive than a front view can show)"
            if stt == "approved":
                used.append(entry)
            elif required:
                missing.append(entry)
            else:
                optional.append(entry)
        for s, h in hands.items():
            if h["drawing"] != "open":
                hs = self.inv.hands[s].get(h["drawing"], "missing")
                if hs == "approved":
                    used.append({"asset": f"hand:{h['drawing']}", "why": "approved hand drawing", "state": hs,
                                 "sides": [s]})
                elif not any(m["asset"] == f"hand:{h['drawing']}" for m in missing):
                    missing.append({"asset": f"hand:{h['drawing']}", "why": "previewed, not approved",
                                    "state": hs, "sides": [s]})
        limited = [n for n in (solver_notes or []) if "knee limit" in n]
        if missing:
            status = NEEDS_ARTWORK
        elif active_corrections or used:
            status = WITH_CORRECTIONS
        else:
            status = SUPPORTED
        out = {"status": status, "missing": missing, "optional_assets": optional, "approved_assets_used": used,
               "corrections": [c["rule"] for c in active_corrections],
               "alternative": spec.fallback if status == NEEDS_ARTWORK else None,
               "front_view_limits": limited,
               "rule": "pending-review artwork never counts as approved; approve it in the editor to unlock the pose"}
        if spec.depth_motion:
            out["reason"] = "the action happens toward or away from the viewer; a front view needs extra drawings"
        return out
