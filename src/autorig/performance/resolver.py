"""ArtworkAndCorrectionResolver: which hand drawings and corrections a performance may use, and how they switch.

A performance names a hand per side as either an *action* or an explicit *pose*:
- an *action* is a gesture intention known to the NaturalHandSelectionEngine (``shrugging``, ``shrug_subtle``,
  ``explaining``, ``hand_on_desk`` ...). The engine chooses the drawing under its hard constraints: handedness,
  approval, palm view in the solved pose, rotation from the drawn orientation. The PerformanceDirector evaluates the
  choice once per hand interval, on the interval's middle frame (the held gesture), and holds it, so frames stay pure
  functions of time and drawings do not flicker;
- an explicit *pose* (``relaxed``, ``cupped``, ``pointing`` ...) is the animator's override. It goes through the
  existing switch system as a user choice (``switches[hand_<s>] = {"user": pose, "auto": False}``), so there is one
  switch engine. The override is still checked against the hard constraints and its violations are reported.

Phase 3.7 fix: Phase 3.6 wrote ``{"user": pose, "auto": True}``. The switch controller honours ``user`` only when
``auto`` is False, so every performance hand request was silently replaced by the automatic choice for the base
preset (``relaxed_standing``). The 3.6 pointing, desk and "cupped" review hands were therefore never shown.

Approval: in production (``allow_pending=False``) only approved drawings and the original hand are shown. Pending
drawings are previewed only in review mode and are labelled. A missing or unusable drawing falls back to the
reference open palm, and the report says so.

Switches are held at least ``MIN_HOLD_S``. A switch requested sooner is delayed to the end of the hold.

Corrections use the existing corrective runtime: the performance chooses the mode (``auto`` = approved only, ``all`` =
preview pending) and per-asset overrides. Nothing is approved here.
"""

from __future__ import annotations

from typing import Any

MIN_HOLD_S = 0.25
STATUS_ORDER = {"approved": 0, "original": 0, "pending_review": 1, "missing": 2}
POSE_NAMES = {"relaxed", "open_palm", "fist", "pointing", "thumbs_up", "gripping", "palm_down", "cupped", "palm_up",
              "partially_closed"}


def is_action(value: str | None) -> bool:
    """An engine action that is not also a pose name (pose names are explicit overrides)."""
    from ..motion.handselect import ACTIONS  # noqa: PLC0415

    return bool(value) and value in ACTIONS and value not in POSE_NAMES


class ArtworkAndCorrectionResolver:
    def __init__(self, eng, allow_pending: bool = True, corrective_mode: str = "all"):
        self.eng = eng
        self.allow_pending = allow_pending
        self.corrective_mode = corrective_mode

    @property
    def mode(self) -> str:
        return "review" if self.allow_pending else "production"

    def status(self, side: str, pose: str) -> str:
        """Best status of a hand pose on one side: approved/original, pending_review or missing."""
        lib = self.eng.hand_lib
        best = "missing"
        for v in (lib.candidates.get(side, {}).get(pose) or []):
            st = v.status if v.layer is not None else "missing"
            if STATUS_ORDER.get(st, 2) < STATUS_ORDER.get(best, 2):
                best = st
        return best

    def usable(self, side: str, pose: str) -> bool:
        st = self.status(side, pose)
        return st in ("approved", "original") or (st == "pending_review" and self.allow_pending)

    def palm_capable(self, side: str, palm: str) -> list[dict[str, Any]]:
        """Drawings offered on ``side`` whose metadata can show ``palm`` (some flat rotation within their tolerance
        exists) - candidates for a review preview; the selection still checks the solved pose."""
        from ..handmeta import HandOrientationAnalyzer  # noqa: PLC0415

        out = []
        for pose, cands in self.eng.hand_lib.candidates.get(side, {}).items():
            for v in cands:
                hm = (v.meta or {}).get("hand_meta")
                if v.layer is not None and hm and HandOrientationAnalyzer.reachable_by_rotation(hm, palm):
                    out.append({"pose": pose, "cid": v.cid, "status": v.status})
        return out

    def resolve(self, hands: dict[str, str], decisions: dict[str, dict[str, Any]] | None = None
                ) -> tuple[dict[str, Any], dict[str, Any]]:
        """``hands``: side -> action or pose; ``decisions``: side -> the planned engine decision for an action.
        Returns (switch state for ``MotionEngine.pose``, report)."""
        sw, rep = {}, {}
        for s, a in hands.items():
            dec = (decisions or {}).get(s)
            if is_action(a):
                pose = (dec or {}).get("chosen")
                kind = "action"
            else:
                pose, kind = a, "pose"
            if pose is None:  # nothing eligible, no fallback: the group default (reference open palm)
                sw[f"hand_{s}"] = {"user": None, "auto": False, "keys": [], "offsets": {}}
                rep[s] = {"requested": a, "kind": kind, "pose": None, "status": "missing", "shown": "open_palm",
                          "mode": self.mode, "note": f"{a}: no drawing passes the hard constraints; showing the "
                                                     "reference open palm", "decision": _brief(dec)}
                continue
            st = self.status(s, pose)
            use = pose if self.usable(s, pose) else "open_palm"
            sw[f"hand_{s}"] = {"user": use, "auto": False, "keys": [], "offsets": {}}
            note = None if use == pose else f"{pose} is {st} ({self.mode} mode): showing the reference open palm"
            if dec is not None and dec.get("fallback"):
                note = dec["fallback"]["reason"] + (f"; {note}" if note else "")
            rep[s] = {"requested": a, "kind": kind, "pose": pose, "status": st, "shown": use, "mode": self.mode,
                      "note": note, "decision": _brief(dec)}
        return sw, rep

    @staticmethod
    def enforce_holds(times: list[float], hands_seq: list[dict[str, str]], min_hold: float = MIN_HOLD_S
                      ) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
        """Delay any switch that comes less than ``min_hold`` after the previous switch on that side."""
        out, delayed = [], []
        last_change: dict[str, float] = {}
        cur: dict[str, str] = {}
        for t, h in zip(times, hands_seq):
            row = {}
            for s, a in h.items():
                if s not in cur:
                    cur[s], last_change[s] = a, t
                elif a != cur[s]:
                    if t - last_change[s] >= min_hold - 1e-9:
                        cur[s], last_change[s] = a, t
                    else:
                        delayed.append({"t": round(t, 3), "side": s, "wanted": a, "kept": cur[s]})
                row[s] = cur[s]
            out.append(row)
        return out, delayed


def _brief(dec: dict[str, Any] | None) -> dict[str, Any] | None:
    if not dec:
        return None
    return {"action": dec.get("action"), "chosen": dec.get("chosen"), "cid": dec.get("cid"), "mode": dec.get("mode"),
            "decided_at": dec.get("decided_at"), "needs_artwork": dec.get("needs_artwork"),
            "fallback": dec.get("fallback"), "rejected": dec.get("rejected"), "explanation": dec.get("explanation")}


__all__ = ["ArtworkAndCorrectionResolver", "MIN_HOLD_S", "is_action", "POSE_NAMES"]
