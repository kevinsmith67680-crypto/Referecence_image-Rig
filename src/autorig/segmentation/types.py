"""Provider-independent segmentation types.

All coordinates are in the ORIGINAL source image pixel space (origin top-left,
+x right, +y down). Masks are full-canvas boolean arrays of shape (H, W).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np


class SegmentationFormatError(ValueError):
    """Model output did not have the verified structure (never guessed around)."""


@dataclass(frozen=True)
class Prompt:
    """A concept prompt. Boxes are pixel xyxy, points are pixel xy."""

    text: str | None = None
    positive_boxes: tuple[tuple[float, float, float, float], ...] = ()
    negative_boxes: tuple[tuple[float, float, float, float], ...] = ()
    positive_points: tuple[tuple[float, float], ...] = ()
    negative_points: tuple[tuple[float, float], ...] = ()
    label: str | None = None  # our semantic name for the request (e.g. "arm")

    def __post_init__(self) -> None:
        if not (self.text or self.positive_boxes or self.positive_points):
            raise ValueError("a prompt needs text, positive_boxes or positive_points")

    @property
    def name(self) -> str:
        return self.label or self.text or "visual_prompt"

    def to_sam3_json(self, width: int, height: int) -> str:
        """vufinder/sam3 format: JSON string; coordinates normalized to [0, 1];
        boxes as (center_x, center_y, width, height) - per the model's schema."""

        def box(b):
            x0, y0, x1, y1 = b
            if not (x1 > x0 and y1 > y0):
                raise ValueError(f"degenerate box {b}")
            return [round((x0 + x1) / 2 / width, 6), round((y0 + y1) / 2 / height, 6),
                    round((x1 - x0) / width, 6), round((y1 - y0) / height, 6)]

        def pt(p):
            return [round(p[0] / width, 6), round(p[1] / height, 6)]

        obj: dict[str, Any] = {}
        if self.text:
            obj["text"] = self.text
        if self.positive_boxes:
            obj["positive_boxes"] = [box(b) for b in self.positive_boxes]
        if self.negative_boxes:
            obj["negative_boxes"] = [box(b) for b in self.negative_boxes]
        if self.positive_points:
            obj["positive_points"] = [pt(p) for p in self.positive_points]
        if self.negative_points:
            obj["negative_points"] = [pt(p) for p in self.negative_points]
        return json.dumps(obj, sort_keys=True)

    def to_record(self) -> dict[str, Any]:
        return {k: v for k, v in {
            "label": self.label, "text": self.text,
            "positive_boxes": [list(b) for b in self.positive_boxes] or None,
            "negative_boxes": [list(b) for b in self.negative_boxes] or None,
            "positive_points": [list(p) for p in self.positive_points] or None,
            "negative_points": [list(p) for p in self.negative_points] or None,
        }.items() if v is not None}


@dataclass
class Instance:
    mask: np.ndarray  # bool (H, W)
    score: float
    box_xyxy: tuple[float, float, float, float]
    prompt_index: int
    instance_index: int

    @property
    def area(self) -> int:
        return int(self.mask.sum())


@dataclass
class ConceptResult:
    prompt: Prompt
    instances: list[Instance]


@dataclass
class SegmentationRun:
    provider: str
    image_sha256: str
    image_size: tuple[int, int]  # (W, H)
    results: list[ConceptResult]
    provenance: dict[str, Any] = field(default_factory=dict)

    def by_label(self, label: str) -> list[Instance]:
        for r in self.results:
            if r.prompt.name == label:
                return r.instances
        return []

    def provenance_record(self) -> dict[str, Any]:
        rec = dict(self.provenance)
        rec.update({
            "provider": self.provider,
            "image_sha256": self.image_sha256,
            "image_size": list(self.image_size),
            "prompts": [r.prompt.to_record() for r in self.results],
            "instances_per_prompt": {r.prompt.name: len(r.instances) for r in self.results},
        })
        return rec


class SegmentationProvider(Protocol):
    name: str

    def segment(self, image_path: str, prompts: list[Prompt], *,
                confidence_threshold: float = 0.5) -> SegmentationRun: ...


def parse_sam3_prompt_json(data: Any, width: int, height: int, prompt_index: int) -> list[Instance]:
    """Parse one per-prompt result file of vufinder/sam3 (dense mode).

    Verified structure (real output of the model's default example):
    ``{"masks": [N][1][H][W] of 0/1, "boxes": [N][4] pixel xyxy, "scores": [N]}``.
    Any deviation raises SegmentationFormatError instead of being guessed around.
    """
    if not isinstance(data, dict) or not {"masks", "boxes", "scores"} <= set(data):
        keys = sorted(data) if isinstance(data, dict) else type(data).__name__
        raise SegmentationFormatError(f"prompt {prompt_index}: expected masks/boxes/scores, got {keys}")
    masks, boxes, scores = data["masks"], data["boxes"], data["scores"]
    if not (len(masks) == len(boxes) == len(scores)):
        raise SegmentationFormatError(
            f"prompt {prompt_index}: {len(masks)} masks, {len(boxes)} boxes, {len(scores)} scores")
    out = []
    for i, (m, b, s) in enumerate(zip(masks, boxes, scores)):
        arr = np.asarray(m)
        if arr.ndim == 3 and arr.shape[0] == 1:
            arr = arr[0]
        if arr.shape != (height, width):
            raise SegmentationFormatError(
                f"prompt {prompt_index} instance {i}: mask shape {arr.shape} != image (H, W) {(height, width)}")
        vals = np.unique(arr)
        if not set(vals.tolist()) <= {0, 1, False, True}:
            raise SegmentationFormatError(f"prompt {prompt_index} instance {i}: non-binary mask values {vals[:5]}")
        if len(b) != 4:
            raise SegmentationFormatError(f"prompt {prompt_index} instance {i}: box {b}")
        out.append(Instance(mask=arr.astype(bool), score=float(s), box_xyxy=tuple(float(v) for v in b),
                            prompt_index=prompt_index, instance_index=i))
    return out
