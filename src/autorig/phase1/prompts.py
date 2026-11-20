"""Default SAM 3 concept-prompt profiles (label -> text). Several prompts may
share a label; their instances are pooled. Which texts work best on cartoon
art is an empirical question - profiles are data, so they can be tuned."""

from __future__ import annotations

from ..segmentation.types import Prompt

BODY_PROMPTS: list[tuple[str, str]] = [
    ("person", "person"),
    ("hair", "hair"),
    ("head", "head"),
    ("face", "face"),
    ("neck", "neck"),
    ("upper_clothing", "shirt"),
    ("sleeve", "sleeve"),
    ("arm", "arm"),
    ("hand", "hand"),
    ("lower_clothing", "shorts"),
    ("lower_clothing", "pants"),
    ("leg", "leg"),
    ("foot", "shoe"),
]

FACE_PROMPTS: list[tuple[str, str]] = [
    ("face", "face"),
    ("hair", "hair"),
    ("eye", "eye"),
    ("eyebrow", "eyebrow"),
    ("pupil", "pupil"),
    ("iris", "iris"),
    ("nose", "nose"),
    ("mouth", "mouth"),
    ("ear", "ear"),
]


def to_prompts(profile: list[tuple[str, str]]) -> list[Prompt]:
    return [Prompt(text=text, label=label) for label, text in profile]
