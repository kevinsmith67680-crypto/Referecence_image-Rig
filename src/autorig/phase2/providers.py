"""Pluggable reconstruction (inpainting) providers.

The engine only talks to :class:`ReconstructionProvider`. Providers never see
or write the artwork store: they return candidate RGBA crops, which the
engine filters (only pixels inside the reconstruction mask, never on
originals) before anything is stored.

Replicate adapters were written from the models' live OpenAPI schemas
(inspected 2026-11-20). They differ in ways that must not be assumed away:
FLUX Fill paints WHITE mask areas, Ideogram v2 paints BLACK ones; only some
accept negative prompts; candidate counts and resolution rules differ. All
are Replicate "official" models without a public version list, so they are
pinned by verification: the recorded latest_version id must still be current
before a paid call. NONE has been inference-tested (account has no credit).
"""

from __future__ import annotations

import hashlib
import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np
from PIL import Image

from ..segmentation.budget import CostPolicy, LedgerEntry, SpendLedger
from ..segmentation.replicate_client import ReplicateClient, ReplicateError, ReplicatePredictionError


class ModelDriftError(ReplicateError):
    pass


class CandidateRejected(ValueError):
    pass


@dataclass
class ReconstructionRequest:
    region_id: str
    image: np.ndarray  # RGBA crop (context included)
    mask: np.ndarray  # bool crop, True = reconstruct
    prompt: str
    negative_prompt: str | None = None
    n_candidates: int = 1
    seed: int | None = 0


@dataclass
class Candidate:
    rgba: np.ndarray  # same shape as the request crop
    seed: int | None
    provenance: dict[str, Any] = field(default_factory=dict)


class ReconstructionProvider(Protocol):
    name: str

    def reconstruct(self, req: ReconstructionRequest) -> list[Candidate]: ...


def check_candidate(req: ReconstructionRequest, rgba: np.ndarray) -> np.ndarray:
    """Strict alignment: a candidate must match the crop exactly (no resizing)."""
    if rgba.shape[:2] != req.mask.shape:
        raise CandidateRejected(f"candidate size {rgba.shape[:2]} != request crop {req.mask.shape}")
    if rgba.shape[2] == 3:
        rgba = np.dstack([rgba, np.full(rgba.shape[:2], 255, np.uint8)])
    return rgba


class FixtureReconstructionProvider:
    """Deterministic offline provider: returns programmed candidates (e.g. exact
    ground truth for the synthetic character) - no network, no cost."""

    name = "fixture"

    def __init__(self, fn: Callable[[ReconstructionRequest, int], np.ndarray]):
        self.fn = fn
        self.calls: list[str] = []

    def reconstruct(self, req: ReconstructionRequest) -> list[Candidate]:
        self.calls.append(req.region_id)
        out = []
        for k in range(req.n_candidates):
            seed = None if req.seed is None else req.seed + k
            out.append(Candidate(check_candidate(req, self.fn(req, k)), seed, {"provider": self.name}))
        return out


@dataclass(frozen=True)
class ModelAdapter:
    key: str
    owner: str
    name: str
    pinned_version: str
    mask_white_means_inpaint: bool
    supports_negative_prompt: bool
    max_outputs_per_call: int
    size_multiple: int
    max_side: int
    price_per_image_usd: float
    price_verified: bool
    extra_inputs: tuple[tuple[str, Any], ...] = ()
    outputs_field_is_list: bool = True
    count_field: str | None = None

    def build_inputs(self, image_url: str, mask_url: str, req: ReconstructionRequest, n: int, seed: int | None) -> dict:
        inp: dict[str, Any] = {"image": image_url, "mask": mask_url, "prompt": req.prompt, **dict(self.extra_inputs)}
        if req.negative_prompt:
            if not self.supports_negative_prompt:
                raise ValueError(f"{self.owner}/{self.name} does not accept a negative prompt")
            inp["negative_prompt"] = req.negative_prompt
        if seed is not None:
            inp["seed"] = int(seed)
        if self.count_field:
            inp[self.count_field] = n
        return inp


ADAPTERS = {
    # Initial candidate (see docs/phase2_implementation_plan.md §3). Untested: no account credit.
    "flux-fill-dev": ModelAdapter(
        "flux-fill-dev", "black-forest-labs", "flux-fill-dev",
        "a053f84125613d83e65328a289e14eb6639e10725c243e8fb0c24128e5573f4c",
        mask_white_means_inpaint=True, supports_negative_prompt=False, max_outputs_per_call=4, size_multiple=32,
        max_side=1440, price_per_image_usd=0.05, price_verified=False,
        extra_inputs=(("megapixels", "match_input"), ("output_format", "png"), ("num_inference_steps", 28)),
        count_field="num_outputs"),
    "flux-fill-pro": ModelAdapter(
        "flux-fill-pro", "black-forest-labs", "flux-fill-pro",
        "41c767bcbfffe54ef8f05eb4d0100f9314790f7fc43a7b88d73ec06839deddb9",
        mask_white_means_inpaint=True, supports_negative_prompt=False, max_outputs_per_call=1, size_multiple=1,
        max_side=4096, price_per_image_usd=0.05, price_verified=True,
        extra_inputs=(("output_format", "png"),), outputs_field_is_list=False),
    "ideogram-v2": ModelAdapter(
        "ideogram-v2", "ideogram-ai", "ideogram-v2",
        "3e6071946ab5319b3bcc37a4d00083e743dfdff5be386df6a2ff1f212fc7365b",
        mask_white_means_inpaint=False, supports_negative_prompt=True, max_outputs_per_call=1, size_multiple=1,
        max_side=4096, price_per_image_usd=0.08, price_verified=False,
        extra_inputs=(("style_type", "Design"), ("magic_prompt_option", "Off")), outputs_field_is_list=False),
}


def encode_mask(mask: np.ndarray, white_means_inpaint: bool) -> Image.Image:
    on, off = (255, 0) if white_means_inpaint else (0, 255)
    return Image.fromarray(np.where(mask, on, off).astype(np.uint8), "L")


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class ReplicateInpaintProvider:
    def __init__(self, client: ReplicateClient, cache_dir: str | Path, adapter: str = "flux-fill-dev",
                 policy: CostPolicy | None = None, poll_seconds: float = 2.0, timeout_seconds: float = 900.0,
                 flatten_rgb=(127, 127, 127)):
        self.client = client
        self.adapter = ADAPTERS[adapter]
        self.name = f"replicate:{self.adapter.owner}/{self.adapter.name}"
        self.cache_dir = Path(cache_dir)
        self.policy = policy or CostPolicy.from_env()
        self.ledger = SpendLedger(self.cache_dir / "ledger.json", self.policy)
        self.poll_seconds, self.timeout_seconds = poll_seconds, timeout_seconds
        self.flatten_rgb = flatten_rgb
        self._verified = False

    def verify_pin(self) -> None:
        a = self.adapter
        live = self.client.get_model(a.owner, a.name).get("latest_version", {}).get("id")
        if live != a.pinned_version:
            raise ModelDriftError(f"{a.owner}/{a.name} changed: pinned {a.pinned_version[:12]}, live {str(live)[:12]}. "
                                  "Re-test and re-pin before spending money on it.")
        self._verified = True

    def _prepare(self, req: ReconstructionRequest) -> tuple[Image.Image, Image.Image]:
        a = self.adapter
        h, w = req.mask.shape
        if max(h, w) > a.max_side or (h % a.size_multiple) or (w % a.size_multiple):
            raise ValueError(f"crop {w}x{h} violates {a.key} constraints (multiple of {a.size_multiple}, "
                             f"max {a.max_side}); the engine must size crops accordingly")
        rgba = Image.fromarray(req.image, "RGBA")
        flat = Image.new("RGB", rgba.size, self.flatten_rgb)  # no alpha: some models read alpha as a mask
        flat.paste(rgba, mask=rgba.split()[3])
        return flat, encode_mask(req.mask, a.mask_white_means_inpaint)

    def reconstruct(self, req: ReconstructionRequest) -> list[Candidate]:
        a = self.adapter
        image, mask = self._prepare(req)
        key = hashlib.sha256(json.dumps({
            "model": f"{a.owner}/{a.name}", "version": a.pinned_version,
            "image": hashlib.sha256(_png_bytes(image)).hexdigest(), "mask": hashlib.sha256(_png_bytes(mask)).hexdigest(),
            "prompt": req.prompt, "negative": req.negative_prompt, "n": req.n_candidates, "seed": req.seed,
            "extra": a.extra_inputs}, sort_keys=True, default=str).encode()).hexdigest()
        edir = self.cache_dir / "inpaint" / key[:2] / key
        done = edir / "candidates.json"
        if not done.is_file():
            if not self._verified:
                self.verify_pin()
            self.ledger.check_amount(req.n_candidates * a.price_per_image_usd, f"{req.n_candidates} inpainting image(s)")
            edir.mkdir(parents=True, exist_ok=True)
            (edir / "image.png").write_bytes(_png_bytes(image))
            (edir / "mask.png").write_bytes(_png_bytes(mask))
            img_url = self.client.upload_file(edir / "image.png")["urls"]["get"]
            mask_url = self.client.upload_file(edir / "mask.png")["urls"]["get"]
            files, k = [], 0
            while k < req.n_candidates:
                n = min(a.max_outputs_per_call, req.n_candidates - k)
                seed = None if req.seed is None else req.seed + k
                inputs = a.build_inputs(img_url, mask_url, req, n, seed)
                pred = self.client.create_model_prediction(a.owner, a.name, inputs)
                (edir / f"prediction_{k}.json").write_text(json.dumps(pred, indent=2), encoding="utf-8")
                pred = self.client.wait(pred, poll_seconds=self.poll_seconds, timeout_seconds=self.timeout_seconds)
                (edir / f"prediction_{k}.json").write_text(json.dumps(pred, indent=2), encoding="utf-8")
                produced = pred.get("output") or []
                produced = produced if isinstance(produced, list) else [produced]
                cost = len(produced) * a.price_per_image_usd if pred.get("status") == "succeeded" else 0.0
                self.ledger.record(LedgerEntry(str(pred.get("id")), f"{a.owner}/{a.name}", a.pinned_version,
                                               str(pred.get("status")), (pred.get("metrics") or {}).get("predict_time"),
                                               cost, key, note=f"{len(produced)} image(s) @ ${a.price_per_image_usd}"
                                               f"{'' if a.price_verified else ' (unverified price)'}"))
                if pred.get("status") != "succeeded":
                    raise ReplicatePredictionError(f"inpainting {pred.get('id')} {pred.get('status')}: "
                                                   f"{pred.get('error')}", pred)
                for j, url in enumerate(produced):  # download immediately
                    dest = edir / f"candidate_{k + j}{Path(url).suffix or '.png'}"
                    self.client.download(url, dest)
                    files.append({"file": dest.name, "seed": None if seed is None else seed + j,
                                  "prediction_id": pred.get("id")})
                k += n
            done.write_text(json.dumps({"model": f"{a.owner}/{a.name}", "version": a.pinned_version,
                                        "inputs_excluding_urls": {**dict(a.extra_inputs), "prompt": req.prompt,
                                                                  "negative_prompt": req.negative_prompt,
                                                                  "seed": req.seed},
                                        "candidates": files, "created_at": time.time()}, indent=2), encoding="utf-8")
        meta = json.loads(done.read_text(encoding="utf-8"))
        out = []
        for c in meta["candidates"]:
            arr = np.asarray(Image.open(edir / c["file"]).convert("RGBA"))
            prov = {"provider": self.name, "version": a.pinned_version, "seed": c["seed"],
                    "prediction_id": c["prediction_id"], "cache_key": key, "mask_convention":
                    "white=inpaint" if a.mask_white_means_inpaint else "black=inpaint"}
            try:
                out.append(Candidate(check_candidate(req, arr), c["seed"], prov))
            except CandidateRejected as exc:
                out.append(Candidate(np.zeros((*req.mask.shape, 4), np.uint8), c["seed"],
                                     prov | {"rejected": str(exc)}))
        return out
