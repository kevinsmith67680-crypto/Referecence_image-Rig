"""ContactConstraintController: hands, elbows, feet and the pelvis in contact with the world, through the one IK solver.

A contact holds:
- body part and contact target, with the target's coordinate space;
- start and end times, and acquire and release durations;
- strength and attachment orientation;
- the IK chain it drives;
- release behaviour and priority.

Targets can be:
- static (``point``);
- moving (``keys`` [t, x, y], monotone Hermite, or an object's path plus an offset);
- a solved joint (``joint`` + ``offset``, e.g. the knee).

How a contact drives the solve (stage-1 intent only):
- A hand contact computes a skeleton-only "free" solve of the same frame without contacts. The wrist target is
  ``lerp(free wrist, contact wrist, w(t))``, with the elbow pole at the free elbow and the hand direction blended
  from the free hand angle. At ``w = 0`` the IK therefore reproduces the free pose exactly: activation and release
  do not jump, and the elbow keeps its side.
- ``w(t)`` is a quintic (C2) ramp over the acquire and release windows, times the strength.
- The contacted point of the hand (palm or fingertips) is placed on the target; the wrist is offset from it along
  the hand's direction.
- Elbow contacts project the elbow point onto the upper arm's reach circle, then aim the wrist along the requested
  forearm direction with the pole at the elbow, so the two-bone IK puts the elbow exactly on that point.
- Feet are planted ``LegIntent``s (``WeightTransferController`` locks them).
- The pelvis-to-seat contact sets the pelvis height (``SeatAndInteractionController``).
- Several contacts on one chain: the highest priority wins its weight, the next fills what is left.
- Unreachable targets are clamped by the IK solver and reported; the director may add bounded torso and pelvis
  compensation.

``release`` behaviours:
- ``return_to_pose``: blend back to the animated pose;
- ``hold``: keep the last contact point while the release ramp runs.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from ..motion.intent import ArmIntent
from .curves import MonotoneHermite, ramp

KINDS = ("foot_ground", "hand_desk", "hand_hip", "hand_knee", "hand_object", "elbow_desk", "body_seat",
         "two_hand_object")
PARTS = ("hand_r", "hand_l", "elbow_r", "elbow_l", "foot_r", "foot_l", "pelvis")
ATTACH = {"palm": 0.55, "fingertips": 0.95, "wrist": 0.0, "heel": 0.0}


@dataclass
class ContactSpec:
    id: str
    kind: str
    part: str
    target: dict[str, Any]  # {"point": [x, y]} | {"keys": [[t, x, y], ...]} | {"joint": name, "offset": [dx, dy]}
    #                         | {"object": id, "offset": [dx, dy]}
    start: float
    end: float
    acquire: float = 0.3
    release: float = 0.3
    strength: float = 1.0
    orientation: float | None = None  # world hand direction (deg, ``ang`` convention: from straight down)
    attach: str = "palm"
    chain: str | None = None
    release_behaviour: str = "return_to_pose"
    priority: int = 10
    space: str = "world"
    forearm_dir: float | None = None  # elbow contacts: world forearm direction (deg, ``ang`` convention)

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown contact kind {self.kind!r}")
        if self.part not in PARTS:
            raise ValueError(f"unknown contact part {self.part!r}")
        if self.end < self.start:
            raise ValueError(f"contact {self.id}: end before start")
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"contact {self.id}: strength must be in [0, 1]")
        if self.release_behaviour not in ("return_to_pose", "hold"):
            raise ValueError(f"contact {self.id}: unknown release behaviour {self.release_behaviour!r}")
        if self.chain is None:
            side = self.part.rsplit("_", 1)[-1]
            self.chain = {"hand": f"arm_{side}", "elbow": f"arm_{side}", "foot": f"leg_{side}"}.get(
                self.part.split("_")[0], "pelvis")
        self.space = "object" if "object" in self.target else "joint" if "joint" in self.target else "world"

    def weight(self, t: float) -> float:
        return self.strength * ramp(t, self.start - self.acquire, self.start) * (
            1.0 - ramp(t, self.end, self.end + self.release))

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_json(d: dict[str, Any]) -> "ContactSpec":
        return ContactSpec(**{k: v for k, v in d.items() if k in ContactSpec.__dataclass_fields__})


def _path(keys: list[list[float]]):
    ks = sorted(keys)
    if len(ks) == 1:
        return lambda t: (float(ks[0][1]), float(ks[0][2]))
    ts = [k[0] for k in ks]
    hx = MonotoneHermite(ts, [k[1] for k in ks], (0.0, 0.0))
    hy = MonotoneHermite(ts, [k[2] for k in ks], (0.0, 0.0))
    return lambda t: (hx(t), hy(t))


def _dir(deg: float) -> np.ndarray:
    """Unit vector of a world direction in the ``ang`` convention (degrees from straight down, + toward image
    left)."""
    a = math.radians(deg)
    return np.array([-math.sin(a), math.cos(a)])


def _ang(v) -> float:
    return math.degrees(math.atan2(-float(v[0]), float(v[1])))


def _lerp_angle(a: float, b: float, w: float) -> float:
    d = (b - a + 180.0) % 360.0 - 180.0
    return a + w * d


class ContactConstraintController:
    def __init__(self, specs: list[ContactSpec] | None = None, objects: dict[str, Any] | None = None):
        self.specs = list(specs or [])
        self.objects = {k: _path(v["keys"]) for k, v in (objects or {}).items()}
        self.objects_raw = dict(objects or {})
        self._paths = {c.id: _path(c.target["keys"]) for c in self.specs if "keys" in c.target}
        self._held: dict[str, np.ndarray] = {}

    # -- targets ---------------------------------------------------------------------------------------------
    def point(self, c: ContactSpec, t: float, joints: dict[str, Any] | None = None) -> np.ndarray:
        tg = c.target
        if "point" in tg:
            return np.asarray(tg["point"], float)
        if "keys" in tg:
            return np.asarray(self._paths[c.id](t), float)
        if "object" in tg:
            return np.asarray(self.objects[tg["object"]](t), float) + np.asarray(tg.get("offset", (0, 0)), float)
        if "joint" in tg:
            if joints is None or tg["joint"] not in joints:
                raise ValueError(f"contact {c.id}: joint {tg['joint']!r} is not available")
            return np.asarray(joints[tg["joint"]], float) + np.asarray(tg.get("offset", (0, 0)), float)
        raise ValueError(f"contact {c.id}: no target")

    def active(self, t: float, chain: str) -> list[tuple[ContactSpec, float]]:
        """Contacts acting on ``chain`` at ``t`` with their effective weights (priority order)."""
        out, left = [], 1.0
        for c in sorted([c for c in self.specs if c.chain == chain], key=lambda c: -c.priority):
            w = c.weight(t)
            if w <= 0:
                continue
            w = min(w, left)
            out.append((c, w))
            left -= w
            if left <= 1e-9:
                break
        return out

    def chains(self) -> list[str]:
        return sorted({c.chain for c in self.specs})

    def needs_free_solve(self, t: float) -> bool:
        return any(self.active(t, ch) for ch in self.chains() if ch.startswith("arm_"))

    # -- intent edits --------------------------------------------------------------------------------------------
    def apply_arms(self, it, t: float, free: dict[str, Any], profile) -> dict[str, Any]:
        """Turn active hand/elbow contacts into IK wrist targets. ``free``: skeleton-only solve without contacts."""
        J, T = free["joints_posed"], free["bone_tails_posed"]
        rep = {}
        for s in ("r", "l"):
            act = self.active(t, f"arm_{s}")
            if not act:
                continue
            wrist_free = np.asarray(J[f"hand_{s}"], float)
            elbow_free = np.asarray(J[f"forearm_{s}"], float)
            hand_free = _ang(np.subtract(T[f"hand_{s}"], J[f"hand_{s}"]))
            hand_len = float(np.hypot(*np.subtract(T[f"hand_{s}"], J[f"hand_{s}"])))  # the hand bone
            target, w_tot, hand_dir, pole = np.zeros(2), 0.0, hand_free, elbow_free
            info = []
            for c, w in act:
                if c.part.startswith("elbow"):
                    shoulder = np.asarray(J[f"upper_arm_{s}"], float)
                    L1 = float(np.hypot(*(elbow_free - shoulder)))
                    L2 = float(np.hypot(*(wrist_free - elbow_free)))
                    e = self.point(c, t, J)
                    v = e - shoulder
                    d = float(np.hypot(*v))
                    e2 = shoulder + v * (L1 / d) if d > 1e-6 else e
                    fdir = c.forearm_dir if c.forearm_dir is not None else _ang(wrist_free - elbow_free)
                    wt = e2 + L2 * _dir(fdir)
                    pole = pole * (1 - w) + e2 * w
                    info.append({"contact": c.id, "w": round(w, 4), "elbow_target": [round(float(x), 2) for x in e2],
                                 "projected_px": round(float(np.hypot(*(e2 - e))), 2)})
                else:
                    pt = self.point(c, t, J)
                    if c.release_behaviour == "hold" and t > c.end:
                        pt = self._held.get(c.id, pt)
                    elif t <= c.end:
                        self._held[c.id] = pt
                    hd = c.orientation if c.orientation is not None else hand_free
                    hand_dir = _lerp_angle(hand_dir, hd, w)
                    wt = pt - ATTACH.get(c.attach, 0.55) * hand_len * _dir(hd)
                    info.append({"contact": c.id, "w": round(w, 4), "point": [round(float(x), 2) for x in pt]})
                target = target + w * wt
                w_tot += w
            target = target + (1.0 - w_tot) * wrist_free
            prev = it.arms.get(s) or ArmIntent()
            it.arms[s] = ArmIntent(target=(float(target[0]), float(target[1])), pole=(float(pole[0]), float(pole[1])),
                                   hand_dir=float(hand_dir), hand=prev.hand, elbow=prev.elbow)
            rep[s] = {"weight": round(w_tot, 4), "wrist_target": [round(float(x), 2) for x in target],
                      "hand_dir": round(float(hand_dir), 2), "contacts": info}
        return rep

    def sliding(self, t: float, solved: dict[str, Any]) -> dict[str, Any]:
        """Distance between the solved contacted point and its target for fully active hand contacts."""
        out = {}
        J, T = solved["joints_posed"], solved["bone_tails_posed"]
        for c in self.specs:
            if not c.part.startswith("hand") or c.weight(t) < 0.999:
                continue
            s = c.part[-1]
            wr, tip = np.asarray(J[f"hand_{s}"], float), np.asarray(T[f"hand_{s}"], float)
            pt = wr + ATTACH.get(c.attach, 0.55) * (tip - wr)
            out[c.id] = round(float(np.hypot(*(pt - self.point(c, t, J)))), 3)
        return out

    def to_json(self) -> dict[str, Any]:
        return {"contacts": [c.to_json() for c in self.specs], "objects": self.objects_raw, "kinds": list(KINDS),
                "attach": ATTACH}


__all__ = ["ContactConstraintController", "ContactSpec", "KINDS", "PARTS", "ATTACH"]
