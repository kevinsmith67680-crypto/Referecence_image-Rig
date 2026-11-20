"""Replicate-hosted SAM 3 (``vufinder/sam3``) segmentation provider.

* Version-pinned. Inputs are built only from fields the pinned version's
  snapshotted OpenAPI schema declares (data/replicate/vufinder_sam3_versions.json);
  versions differ (only the newest has offset_masks/concat_input/split_output).
* Dense output mode only (``offset_masks=false``): that format is verified from
  a real model output; the offset format is undocumented and unverified.
* Cache: content-addressed by (model, version, image SHA-256, encoded prompts,
  parameters, parser version). A hit costs nothing and needs no network.
* Every prediction's JSON and output files are written to the cache directory
  immediately (before parsing), and every call is recorded in the spend ledger.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from importlib import resources
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .budget import CostPolicy, LedgerEntry, SpendLedger
from .replicate_client import ReplicateClient, ReplicatePredictionError
from .types import ConceptResult, Instance, Prompt, SegmentationFormatError, SegmentationRun, parse_sam3_prompt_json

MODEL = "vufinder/sam3"
PINNED_VERSION = "1bf97763d5dfd3a1584adca913a8ef4b43c684fca97e04e39e4c50a3a5e09650"
PARSER_VERSION = "sam3-dense-v1"
VERSIONS_RESOURCE = "data/replicate/vufinder_sam3_versions.json"


def load_version_snapshot() -> dict[str, Any]:
    return json.loads(resources.files("autorig").joinpath(VERSIONS_RESOURCE).read_text(encoding="utf-8"))


def version_inputs(version: str) -> dict[str, Any]:
    for v in load_version_snapshot()["versions"]:
        if v["id"] == version:
            return v["input"]
    raise ValueError(f"version {version} of {MODEL} is not in the schema snapshot; refresh it before use")


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _pack(instances: list[Instance]) -> tuple[np.ndarray, dict[str, Any]]:
    if not instances:
        return np.zeros((0,), dtype=np.uint8), {"shape": None, "instances": []}
    shape = instances[0].mask.shape
    bits = np.packbits(np.stack([i.mask for i in instances]).reshape(len(instances), -1), axis=1)
    meta = {"shape": list(shape), "instances": [
        {"score": i.score, "box_xyxy": list(i.box_xyxy), "prompt_index": i.prompt_index,
         "instance_index": i.instance_index} for i in instances]}
    return bits, meta


def _unpack(bits: np.ndarray, meta: dict[str, Any]) -> list[Instance]:
    if not meta["instances"]:
        return []
    h, w = meta["shape"]
    flat = np.unpackbits(bits, axis=1)[:, : h * w].astype(bool)
    return [Instance(mask=flat[k].reshape(h, w), score=m["score"], box_xyxy=tuple(m["box_xyxy"]),
                     prompt_index=m["prompt_index"], instance_index=m["instance_index"])
            for k, m in enumerate(meta["instances"])]


class Sam3ReplicateProvider:
    name = "replicate:vufinder/sam3"

    def __init__(self, client: ReplicateClient | None, cache_dir: str | Path, *,
                 version: str = PINNED_VERSION, policy: CostPolicy | None = None,
                 image_transport: str = "upload", poll_seconds: float = 1.5, timeout_seconds: float = 900.0):
        self.client = client
        self.version = version
        self.inputs_schema = version_inputs(version)
        self.cache_dir = Path(cache_dir)
        self.policy = policy or CostPolicy.from_env()
        self.ledger = SpendLedger(self.cache_dir / "ledger.json", self.policy)
        if image_transport not in ("upload", "data_uri"):
            raise ValueError("image_transport must be 'upload' or 'data_uri'")
        self.image_transport = image_transport
        self.poll_seconds = poll_seconds
        self.timeout_seconds = timeout_seconds

    # -- request construction --------------------------------------------------------
    def build_params(self, prompts: list[Prompt], width: int, height: int,
                     confidence_threshold: float) -> dict[str, Any]:
        spec = self.inputs_schema["confidence_threshold"]
        if not (spec.get("minimum", 0) <= confidence_threshold <= spec.get("maximum", 1)):
            raise ValueError(f"confidence_threshold {confidence_threshold} outside "
                             f"[{spec.get('minimum')}, {spec.get('maximum')}] for this version")
        params: dict[str, Any] = {
            "prompts": [p.to_sam3_json(width, height) for p in prompts],
            "confidence_threshold": confidence_threshold,
            "visualize": False,
            "backbone_output": False,
        }
        # Only send fields this version declares; force the verified dense format.
        if "offset_masks" in self.inputs_schema:
            params["offset_masks"] = False
        if "split_output" in self.inputs_schema:
            params["split_output"] = True
        if "concat_input" in self.inputs_schema:
            params["concat_input"] = False
        unknown = set(params) - set(self.inputs_schema)
        if unknown:
            raise ValueError(f"fields not supported by version {self.version[:12]}: {sorted(unknown)}")
        return params

    def cache_key(self, image_sha: str, params: dict[str, Any]) -> str:
        blob = json.dumps({"model": MODEL, "version": self.version, "image_sha256": image_sha,
                           "params": params, "parser": PARSER_VERSION}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def entry_dir(self, key: str) -> Path:
        return self.cache_dir / "sam3" / key[:2] / key

    # -- main entry ----------------------------------------------------------------------
    def segment(self, image_path: str | Path, prompts: list[Prompt], *,
                confidence_threshold: float = 0.5) -> SegmentationRun:
        image_path = Path(image_path)
        with Image.open(image_path) as im:
            width, height = im.size
        sha = file_sha256(image_path)
        params = self.build_params(prompts, width, height, confidence_threshold)
        key = self.cache_key(sha, params)
        edir = self.entry_dir(key)
        parsed_npz, parsed_json = edir / "parsed.npz", edir / "parsed.json"

        if parsed_npz.is_file() and parsed_json.is_file():
            meta = json.loads(parsed_json.read_text(encoding="utf-8"))
            instances = _unpack(np.load(parsed_npz)["bits"], meta["masks"])
            return self._run(prompts, instances, sha, width, height, meta["provenance"] | {"cache_hit": True})

        raw_dir = edir / "outputs"
        pred_path = edir / "prediction.json"
        if pred_path.is_file() and raw_dir.is_dir():
            pred = json.loads(pred_path.read_text(encoding="utf-8"))  # outputs saved earlier, parse again
        else:
            if self.client is None:
                raise RuntimeError("no Replicate client configured and no cached prediction for this request")
            self.ledger.check(1)  # refuse BEFORE any paid call
            edir.mkdir(parents=True, exist_ok=True)
            (edir / "request.json").write_text(json.dumps(
                {"model": MODEL, "version": self.version, "image_sha256": sha, "image_name": image_path.name,
                 "image_size": [width, height], "params": params, "prompt_records": [p.to_record() for p in prompts]},
                indent=2), encoding="utf-8")
            image_ref = self._image_reference(image_path)
            pred = self.client.create_prediction(self.version, {"image": image_ref, **params})
            pred_path.write_text(json.dumps(pred, indent=2), encoding="utf-8")
            try:
                pred = self.client.wait(pred, poll_seconds=self.poll_seconds, timeout_seconds=self.timeout_seconds,
                                        on_update=lambda p: pred_path.write_text(json.dumps(p, indent=2),
                                                                                 encoding="utf-8"))
            finally:
                self._record(pred, key)
            pred_path.write_text(json.dumps(pred, indent=2), encoding="utf-8")
            if pred.get("status") != "succeeded":
                raise ReplicatePredictionError(
                    f"prediction {pred.get('id')} {pred.get('status')}: {pred.get('error')}", pred)
            urls = (pred.get("output") or {}).get("results") or []
            if len(urls) != len(prompts):
                raise SegmentationFormatError(f"expected {len(prompts)} result files, got {len(urls)}")
            for u in urls:  # save outputs immediately
                self.client.download(u, raw_dir / Path(u).name)

        instances = self._parse_outputs(raw_dir, len(prompts), width, height)
        provenance = {
            "model": MODEL, "version": self.version, "prediction_id": pred.get("id"),
            "status": pred.get("status"), "predict_time_s": (pred.get("metrics") or {}).get("predict_time"),
            "estimated_usd": self.policy.estimate_usd((pred.get("metrics") or {}).get("predict_time")),
            "cost_basis": self.policy.rate_source, "params": params, "cache_key": key,
            "parser": PARSER_VERSION, "created_at": pred.get("created_at"), "completed_at": pred.get("completed_at"),
        }
        bits, mmeta = _pack(instances)
        np.savez_compressed(parsed_npz, bits=bits)
        parsed_json.write_text(json.dumps({"masks": mmeta, "provenance": provenance}, indent=2), encoding="utf-8")
        return self._run(prompts, instances, sha, width, height, provenance | {"cache_hit": False})

    # -- helpers --------------------------------------------------------------------------
    def _image_reference(self, image_path: Path) -> str:
        if self.image_transport == "data_uri":
            data = base64.b64encode(image_path.read_bytes()).decode()
            return f"data:image/{image_path.suffix.lstrip('.').lower() or 'png'};base64,{data}"
        return self.client.upload_file(image_path)["urls"]["get"]

    def _record(self, pred: dict[str, Any], key: str) -> None:
        t = (pred.get("metrics") or {}).get("predict_time")
        self.ledger.record(LedgerEntry(prediction_id=str(pred.get("id")), model=MODEL, version=self.version,
                                       status=str(pred.get("status")), predict_time_s=t,
                                       estimated_usd=self.policy.estimate_usd(t), cache_key=key))

    @staticmethod
    def _parse_outputs(raw_dir: Path, n_prompts: int, width: int, height: int) -> list[Instance]:
        files = sorted(raw_dir.glob("*.json"))
        indexed = []
        for f in files:
            m = re.search(r"(\d+)\.json$", f.name)
            indexed.append((int(m.group(1)) if m else len(indexed) + 1, f))
        indexed.sort()
        if len(indexed) != n_prompts:
            raise SegmentationFormatError(f"{len(indexed)} result files for {n_prompts} prompts in {raw_dir}")
        out: list[Instance] = []
        for prompt_index, (_, f) in enumerate(indexed):
            out.extend(parse_sam3_prompt_json(json.loads(f.read_text(encoding="utf-8")), width, height, prompt_index))
        return out

    def _run(self, prompts, instances, sha, width, height, provenance) -> SegmentationRun:
        results = [ConceptResult(p, [i for i in instances if i.prompt_index == k]) for k, p in enumerate(prompts)]
        return SegmentationRun(self.name, sha, (width, height), results, provenance)


class FixtureSegmentationProvider:
    """Offline provider answering text prompts from known masks (no network, no cost).

    ``concepts`` maps prompt text -> list of full-canvas masks (instances).
    ``degrade`` optionally perturbs masks (e.g. erode/dilate) to test robustness.
    """

    name = "fixture"

    def __init__(self, concepts: dict[str, list[np.ndarray]], *, score: float = 0.95, degrade=None,
                 drop: set[str] | None = None):
        self.concepts = concepts
        self.score = score
        self.degrade = degrade
        self.drop = drop or set()

    def segment(self, image_path: str | Path, prompts: list[Prompt], *,
                confidence_threshold: float = 0.5) -> SegmentationRun:
        with Image.open(image_path) as im:
            width, height = im.size
        results = []
        for k, p in enumerate(prompts):
            masks = [] if (p.text in self.drop) else self.concepts.get(p.text or "", [])
            inst = []
            for j, m in enumerate(masks):
                m = self.degrade(m) if self.degrade else m
                if m.shape != (height, width):
                    raise SegmentationFormatError(f"fixture mask {m.shape} != image {(height, width)}")
                if not m.any() or self.score < confidence_threshold:
                    continue
                ys, xs = np.nonzero(m)
                inst.append(Instance(m.astype(bool), self.score,
                                     (float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)), k, j))
            results.append(ConceptResult(p, inst))
        return SegmentationRun(self.name, file_sha256(image_path), (width, height), results,
                               {"model": "fixture", "version": "n/a", "estimated_usd": 0.0, "cache_hit": False,
                                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
