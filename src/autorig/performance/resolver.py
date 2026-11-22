"""ArtworkAndCorrectionResolver: which hand drawings and corrections a performance may use, and how they switch.

Hands are chosen as explicit poses per side (``open_palm``, ``relaxed``, ``pointing``, ``cupped`` ...). They go
through the existing switch system as user choices (``switches[hand_<s>].user``): one switch engine. A pose whose
drawing is pending review is shown only in previews (``allow_pending``); a missing drawing falls back to the
reference open palm and is reported.

Switches are held at least ``MIN_HOLD_S``. A switch requested sooner is delayed to the end of the hold, so drawings
cannot flicker.

Corrections use the existing corrective runtime: the performance chooses the mode (``auto`` = approved only,
``all`` = preview pending) and per-asset overrides. Nothing is approved here.
"""

from __future__ import annotations

from typing import Any

MIN_HOLD_S = 0.25
ACTION_POSE = {"relaxed_standing": "relaxed", "pointing": "pointing", "raised": "open_palm", "open": "open_palm",
               "shrugging": "palm_up", "explaining": "cupped", "resting": "palm_down", "gripping": "gripping",
               "hand_on_hip": "relaxed", "fist": "fist", "thumbs_up": "thumbs_up"}
STATUS_ORDER = {"approved": 0, "original": 0, "pending_review": 1, "missing": 2}


def pose_of(action_or_pose: str) -> str:
    return ACTION_POSE.get(action_or_pose, action_or_pose)


class ArtworkAndCorrectionResolver:
    def __init__(self, eng, allow_pending: bool = True, corrective_mode: str = "all"):
        self.eng = eng
        self.allow_pending = allow_pending
        self.corrective_mode = corrective_mode

    def status(self, side: str, pose: str) -> str:
        """Best status of a hand pose on one side: approved/original, pending_review or missing."""
        lib = self.eng.hand_lib
        best = "missing"
        for v in (lib.candidates.get(side, {}).get(pose) or []):
            st = v.status if v.layer is not None else "missing"
            st = "original" if st == "original" else st
            if STATUS_ORDER.get(st, 2) < STATUS_ORDER.get(best, 2):
                best = st
        return best

    def usable(self, side: str, pose: str) -> bool:
        st = self.status(side, pose)
        return st in ("approved", "original") or (st == "pending_review" and self.allow_pending)

    def resolve(self, hands: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
        """``hands``: side -> action or pose. Returns (switch state for ``MotionEngine.pose``, report)."""
        sw, rep = {}, {}
        for s, a in hands.items():
            pose = pose_of(a)
            st = self.status(s, pose)
            use = pose if self.usable(s, pose) else "open_palm"
            sw[f"hand_{s}"] = {"user": use, "auto": True, "keys": [], "offsets": {}}
            rep[s] = {"requested": a, "pose": pose, "status": st, "shown": use,
                      "note": None if use == pose else f"{pose} is {st}: showing the reference open palm"}
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


__all__ = ["ArtworkAndCorrectionResolver", "MIN_HOLD_S", "ACTION_POSE", "pose_of"]
