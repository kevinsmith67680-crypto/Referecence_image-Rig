"""Character-specific proportions and constraints, measured from the rig and the artwork.

Nothing here assumes adult human proportions. Widths, track and knee limits come from this character's bones
and shoe drawings, and every value can be overridden per character (``natural_pose/motion_profile.json``).
"""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..phase2.skeleton import LIMITS, SIDES, Skeleton

# In-plane (front-view) knee flexion limits, deg. A front view cannot show sagittal knee flexion without
# foreshortened drawings; the visible lateral bend is kept small so legs do not read as bowed.
FRONT_KNEE = {"stance": 10.0, "swing": 18.0, "swing_max": 28.0, "walk_swing": 12.0, "squat": 75.0}


@dataclass
class CharacterProfile:
    view: str
    midline_x: float
    ground_y: float
    hip_sep: float
    thigh: dict[str, float]
    shin: dict[str, float]
    upper_arm: dict[str, float]
    forearm: dict[str, float]
    shoe: dict[str, dict[str, float]]  # side -> medial/lateral extent from the ankle, width, ankle->sole drop
    track_min: float  # smallest ankle separation at which the shoes do not touch
    stance_natural: float  # ankle separation for relaxed standing
    track_run: float
    track_walk: float
    ankle_y: dict[str, float]  # rest ankle height (shoe flat on the ground)
    hip_y: float
    waist_y: float  # between the hip line and the spine joint (hands-on-hips height)
    waist_half: float  # half body width at waist_y (torso/pelvis artwork)
    front_knee: dict[str, float] = field(default_factory=lambda: dict(FRONT_KNEE))
    limits: dict[str, dict[str, Any]] = field(default_factory=lambda: copy.deepcopy(LIMITS))
    notes: list[str] = field(default_factory=list)

    def leg(self, s: str) -> float:
        return self.thigh[s] + self.shin[s]

    def arm(self, s: str) -> float:
        return self.upper_arm[s] + self.forearm[s]

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["limits"] = {k: {kk: list(vv) if isinstance(vv, tuple) else vv for kk, vv in v.items()}
                       for k, v in self.limits.items()}
        d["units"] = "source px; angles deg (image space, + = clockwise on screen)"
        return d


def build_profile(sk: Skeleton, masks: dict[str, np.ndarray], view: str = "front",
                  overrides: dict[str, Any] | None = None) -> CharacterProfile:
    """``masks``: rest alpha masks per layer (used for the shoe extents)."""
    b = sk.bone
    hip_sep = float(abs(b("thigh_l").head[0] - b("thigh_r").head[0])) or 100.0
    shoe = {}
    for s in SIDES:
        ax, ay = b(f"foot_{s}").head
        m = masks.get(f"foot_{s}")
        if m is not None and m.any():
            ys, xs = np.nonzero(m)
            medial = (xs.max() - ax) if s == "r" else (ax - xs.min())  # character's right = image left
            lateral = (ax - xs.min()) if s == "r" else (xs.max() - ax)
            shoe[s] = {"medial": float(medial), "lateral": float(lateral), "width": float(xs.max() - xs.min()),
                       "sole_drop": float(ys.max() - ay)}
        else:
            shoe[s] = {"medial": 0.3 * hip_sep, "lateral": 0.6 * hip_sep, "width": 0.9 * hip_sep,
                       "sole_drop": 0.5 * hip_sep}
    gap = 6.0
    waist_y = float((b("pelvis").head[1] + b("spine").head[1]) / 2)
    body = np.zeros(next(iter(masks.values())).shape, bool)
    for c in ("torso", "pelvis"):
        if c in masks:
            body |= masks[c]
    row = np.nonzero(body[int(round(waist_y))])[0] if body.any() else np.array([])
    waist_half = float((row.max() - row.min()) / 2) if len(row) else 0.9 * hip_sep
    track_min = shoe["r"]["medial"] + shoe["l"]["medial"] + gap
    p = CharacterProfile(
        view=view, midline_x=float(sk.midline_x), ground_y=float(sk.ground_y), hip_sep=hip_sep,
        thigh={s: b(f"thigh_{s}").length for s in SIDES}, shin={s: b(f"shin_{s}").length for s in SIDES},
        upper_arm={s: b(f"upper_arm_{s}").length for s in SIDES},
        forearm={s: b(f"forearm_{s}").length for s in SIDES}, shoe=shoe, track_min=track_min,
        stance_natural=max(track_min + 0.4 * hip_sep, 1.3 * hip_sep),
        track_run=track_min + 0.25 * hip_sep, track_walk=track_min + 0.3 * hip_sep,
        ankle_y={s: float(b(f"foot_{s}").head[1]) for s in SIDES}, hip_y=float(b("pelvis").head[1]),
        waist_y=waist_y, waist_half=waist_half,
        notes=[f"track_min = medial shoe extents ({shoe['r']['medial']:.0f} + {shoe['l']['medial']:.0f}) + {gap:.0f} px "
               "gap: narrower ankle spacing makes the shoes overlap (legs read as crossing)"])
    for k, v in (overrides or {}).items():
        if k == "limits":
            for kind, lim in v.items():
                p.limits.setdefault(kind, {}).update({kk: tuple(vv) if isinstance(vv, list) else vv
                                                      for kk, vv in lim.items()})
        elif k == "front_knee":
            p.front_knee.update(v)
        elif hasattr(p, k):
            setattr(p, k, v)
    return p


def load_overrides(pkg: Path) -> dict[str, Any]:
    f = Path(pkg) / "natural_pose" / "motion_profile.json"
    return json.loads(f.read_text()) if f.is_file() else {}
