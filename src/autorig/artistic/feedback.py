"""Phase 3.5 artistic feedback: short review notes turned into structured, reusable pose constraints.

A deterministic phrase table, not a language model. Examples:
- "Shoulder looks too large" -> clavicle -3 deg;
- "Elbow looks too round" -> elbow fold correction strength -0.25;
- "Move the hand farther from the torso" -> arm abduction +5 deg;
- "Make the running stance less stiff" -> contrapposto +0.2, exaggeration +0.2.

Sides come from "left"/"right" in the text (the character's sides), otherwise from the action's gesturing arm or
both. A phrase that matches nothing is stored as a note and changes nothing. Every constraint is shown to the user
before it is applied, and is stored per character in the editor project (``artistic.feedback`` op).
"""

from __future__ import annotations

import re
from typing import Any

GESTURE_ARM = {"point_right": "r", "point_left": "l", "arm_raised": "r", "deep_elbow": "r", "hand_behind_back": "r"}

RULES: list[tuple[str, list[dict[str, Any]]]] = [
    (r"shoulders? .*(too (large|big|high|wide)|bulky)", [{"param": "clavicle_{s}", "delta": -3.0}]),
    (r"shoulders? .*(too (low|small)|raise)", [{"param": "clavicle_{s}", "delta": 3.0}]),
    (r"elbows? .*(too round|balloon|puffy)", [{"kind": "corrective", "asset": "elbow_fold_{s}", "param": "strength",
                                                "delta": -0.25}]),
    (r"knees? .*(too round|balloon|puffy)", [{"kind": "corrective", "asset": "knee_fold_{s}", "param": "strength",
                                               "delta": -0.25}]),
    (r"armpits? .*(too full|inflated|big)", [{"kind": "corrective", "asset": "armpit_gusset_{s}", "param": "strength",
                                               "delta": -0.3}]),
    (r"hands? .*(farther|further|away) from the (torso|body)", [{"param": "arm_{s}_abduction", "delta": 5.0}]),
    (r"hands? .*closer to the (torso|body)", [{"param": "arm_{s}_abduction", "delta": -5.0}]),
    (r"(less stiff|more relaxed|too stiff|stiff)", [{"param": "contrapposto", "delta": 0.2},
                                                     {"param": "exaggeration", "delta": 0.2}]),
    (r"(more|stronger) (weight shift|contrapposto)", [{"param": "contrapposto", "delta": 0.25}]),
    (r"more weight on the left", [{"param": "weight_left", "delta": 0.1}]),
    (r"more weight on the right", [{"param": "weight_left", "delta": -0.1}]),
    (r"(lean|line of action) more", [{"param": "line_of_action", "delta": 3.0, "signed": True}]),
    (r"(lean|line of action) less", [{"param": "line_of_action", "delta": -3.0, "signed": True}]),
    (r"head .*tilt.* more|tilt the head more", [{"param": "head", "delta": 3.0, "signed": True}]),
    (r"head .*tilt.* less|tilt the head less", [{"param": "head", "delta": -3.0, "signed": True}]),
    (r"arm .*(higher|up)", [{"param": "arm_{s}_abduction", "delta": 8.0}]),
    (r"arm .*(lower|down)", [{"param": "arm_{s}_abduction", "delta": -8.0}]),
    (r"(bend|bent) .*elbow .*more|more bend", [{"param": "arm_{s}_flexion", "delta": 10.0}]),
    (r"(bend|bent) .*elbow .*less|straighter arm|straighten", [{"param": "arm_{s}_flexion", "delta": -10.0}]),
    (r"(more|bigger) exaggerat|push the pose", [{"param": "exaggeration", "delta": 0.25}]),
    (r"(less|smaller) exaggerat|tone (it )?down", [{"param": "exaggeration", "delta": -0.25}]),
]


def parse_feedback(text: str, action: str = "") -> dict[str, Any]:
    t = text.lower().strip()
    sides = [s for s, w in (("r", "right"), ("l", "left")) if re.search(rf"\b{w}\b", t)
             and not re.search(rf"weight on the {w}", t)]
    if not sides:
        sides = [GESTURE_ARM[action]] if action in GESTURE_ARM else ["r", "l"]
    out: list[dict[str, Any]] = []
    matched = []
    for pat, cons in RULES:
        if re.search(pat, t):
            matched.append(pat)
            for c in cons:
                sides_c = sides if ("{s}" in c.get("param", "") or "{s}" in c.get("asset", "")) else [None]
                for s in sides_c:
                    d = {k: (v.format(s=s) if isinstance(v, str) and s else v) for k, v in c.items() if k != "signed"}
                    d["action"] = action or "*"
                    d["source_text"] = text
                    out.append(d)
    return {"constraints": out, "matched": matched, "understood": bool(out),
            "note": None if out else "no rule matched: stored as a note for the artist (nothing changes)"}


__all__ = ["parse_feedback", "RULES"]
