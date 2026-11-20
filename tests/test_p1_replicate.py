"""Replicate client + SAM 3 provider against an in-process fake of the Replicate
API and replicate.delivery (httpx.MockTransport). No network, no paid inference.

The fake returns results in the format VERIFIED from a real vufinder/sam3
output: per-prompt JSON {masks: [N][1][H][W], boxes: [N][4] xyxy, scores: [N]}.
"""

import json
import logging

import httpx
import numpy as np
import pytest
from PIL import Image

from autorig.segmentation.budget import BudgetExceeded, CostPolicy, LedgerEntry, SpendLedger
from autorig.segmentation.replicate_client import (ReplicateAuthError, ReplicateClient, ReplicateError,
                                                    ReplicateHTTPError, ReplicatePredictionError)
from autorig.segmentation.sam3 import PINNED_VERSION, Sam3ReplicateProvider, version_inputs
from autorig.segmentation.types import Prompt, SegmentationFormatError, parse_sam3_prompt_json

TOKEN = "fake-test-token-not-a-secret-123456"
W, H = 64, 48  # non-square on purpose


def make_image(tmp_path):
    arr = np.full((H, W, 3), 240, np.uint8)
    arr[10:30, 5:20] = (200, 30, 30)    # "red" blob (left)
    arr[12:40, 40:60] = (30, 30, 200)   # "blue" blob (right)
    p = tmp_path / "img.png"
    Image.fromarray(arr).save(p)
    return p


def concept(text):
    m = np.zeros((H, W), np.uint8)
    if text == "red":
        m[10:30, 5:20] = 1
        return [m]
    if text == "blob":
        b = np.zeros((H, W), np.uint8)
        m[10:30, 5:20] = 1
        b[12:40, 40:60] = 1
        return [m, b]
    return []


class FakeReplicate:
    def __init__(self, *, fail=None, mask_shape=None, final_status="succeeded", polls=1):
        self.fail = dict(fail or {})  # (method, path) -> list of status codes to return first
        self.mask_shape = mask_shape
        self.final_status = final_status
        self.polls = polls
        self.requests: list[httpx.Request] = []
        self.created = 0
        self.prompts = []

    def _fail(self, key):
        seq = self.fail.get(key)
        if seq:
            return seq.pop(0)
        return None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path, method = request.url.host, request.url.path, request.method
        code = self._fail((method, path))
        if code == "timeout":
            raise httpx.ReadTimeout("simulated", request=request)
        if code == "connect":
            raise httpx.ConnectError("simulated", request=request)
        if code:
            return httpx.Response(code, json={"detail": f"simulated {code} token={TOKEN}"},
                                  headers={"retry-after": "0"})
        if host == "api.replicate.com":
            if method == "POST" and path == "/v1/files":
                return httpx.Response(201, json={"id": "f1", "urls": {"get": "https://api.replicate.com/v1/files/f1"}})
            if method == "POST" and path == "/v1/predictions":
                body = json.loads(request.content)
                self.created += 1
                self.prompts = [json.loads(p) for p in body["input"]["prompts"]]
                self.last_input = body["input"]
                return httpx.Response(201, json={"id": "p1", "status": "starting", "version": body["version"]})
            if method == "GET" and path == "/v1/predictions/p1":
                self.polls -= 1
                if self.polls >= 0:
                    return httpx.Response(200, json={"id": "p1", "status": "processing"})
                out = {"results": [f"https://replicate.delivery/test/prompt_{i + 1:04d}.json"
                                   for i in range(len(self.prompts))], "visualizations": None}
                return httpx.Response(200, json={
                    "id": "p1", "status": self.final_status, "output": out if self.final_status == "succeeded" else None,
                    "error": None if self.final_status == "succeeded" else "CUDA out of memory",
                    "metrics": {"predict_time": 2.5}, "created_at": "2026-11-20T00:00:00Z"})
            if method == "POST" and path == "/v1/predictions/p1/cancel":
                return httpx.Response(200, json={"id": "p1", "status": "canceled"})
        if host == "replicate.delivery":
            k = int(path.rsplit("_", 1)[1].split(".")[0]) - 1
            masks = concept(self.prompts[k].get("text"))
            if self.mask_shape:
                masks = [np.zeros(self.mask_shape, np.uint8) for _ in masks] or [np.zeros(self.mask_shape, np.uint8)]
            boxes = []
            for m in masks:
                ys, xs = np.nonzero(m) if m.any() else (np.array([0]), np.array([0]))
                boxes.append([float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1)])
            return httpx.Response(200, json={"masks": [[m.tolist()] for m in masks], "boxes": boxes,
                                             "scores": [0.91] * len(masks)})
        return httpx.Response(404, json={"detail": "not found"})


def client(fake, **kw):
    return ReplicateClient(TOKEN, transport=httpx.MockTransport(fake), sleep=lambda s: None, backoff_seconds=0, **kw)


def provider(tmp_path, fake, ceiling=1.0, **kw):
    return Sam3ReplicateProvider(client(fake), tmp_path / "cache", policy=CostPolicy(ceiling_usd=ceiling),
                                 poll_seconds=0, **kw)


# --- token hygiene --------------------------------------------------------------------

def test_token_from_env_and_missing(monkeypatch):
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    with pytest.raises(ReplicateAuthError, match="REPLICATE_API_TOKEN is not set"):
        ReplicateClient()
    monkeypatch.setenv("REPLICATE_API_TOKEN", TOKEN)
    c = ReplicateClient()
    assert TOKEN not in repr(c) and "<redacted>" in repr(c)


def test_token_never_in_errors_or_logs(caplog):
    fake = FakeReplicate(fail={("GET", "/v1/models/a/b"): [500, 500, 500, 500, 500, 422]})
    c = client(fake, max_retries=1)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ReplicateHTTPError) as exc:
        c.get_model("a", "b")
    assert TOKEN not in str(exc.value) and "<redacted>" in str(exc.value)
    assert TOKEN not in caplog.text


def test_token_only_sent_to_api_host(tmp_path):
    fake = FakeReplicate()
    provider(tmp_path, fake).segment(make_image(tmp_path), [Prompt(text="red")])
    for r in fake.requests:
        if r.url.host == "api.replicate.com":
            assert r.headers["authorization"] == f"Bearer {TOKEN}"
        else:
            assert "authorization" not in r.headers


def test_refuses_foreign_hosts():
    c = client(FakeReplicate())
    with pytest.raises(ReplicateError, match="refusing to send the API token"):
        c._json("GET", "https://evil.example.com/v1/models")
    with pytest.raises(ReplicateError, match="unexpected host"):
        c.download("https://evil.example.com/x.json", "/tmp/never")


# --- error handling & retries ---------------------------------------------------------

@pytest.mark.parametrize("code, exc", [(401, ReplicateAuthError), (403, ReplicateAuthError),
                                       (402, ReplicateHTTPError), (422, ReplicateHTTPError)])
def test_client_errors_not_retried(code, exc):
    fake = FakeReplicate(fail={("GET", "/v1/models/a/b"): [code]})
    with pytest.raises(exc):
        client(fake).get_model("a", "b")
    assert len(fake.requests) == 1


def test_get_retries_429_and_5xx_then_succeeds():
    fake = FakeReplicate(fail={("GET", "/v1/predictions/p1"): [429, 503, 502]})
    fake.polls = -1
    pred = client(fake, max_retries=4).get_prediction("p1")
    assert pred["status"] == "succeeded" and len(fake.requests) == 4


def test_retry_limit_is_configurable():
    fake = FakeReplicate(fail={("GET", "/v1/predictions/p1"): [503] * 10})
    with pytest.raises(ReplicateHTTPError):
        client(fake, max_retries=2).get_prediction("p1")
    assert len(fake.requests) == 3


def test_create_prediction_not_retried_after_send():
    fake = FakeReplicate(fail={("POST", "/v1/predictions"): ["timeout"]})
    with pytest.raises(ReplicateError, match="not retried to avoid a duplicate"):
        client(fake).create_prediction(PINNED_VERSION, {})
    assert len(fake.requests) == 1


def test_create_prediction_retried_when_never_sent_or_429():
    fake = FakeReplicate(fail={("POST", "/v1/predictions"): ["connect", 429]})
    fake.prompts = []
    pred = client(fake).create_prediction(PINNED_VERSION, {"prompts": []})
    assert pred["id"] == "p1" and fake.created == 1 and len(fake.requests) == 3


def test_failed_prediction_raises_and_is_recorded(tmp_path):
    fake = FakeReplicate(final_status="failed")
    prov = provider(tmp_path, fake)
    with pytest.raises(ReplicatePredictionError, match="CUDA out of memory"):
        prov.segment(make_image(tmp_path), [Prompt(text="red")])
    assert prov.ledger.entries[-1].status == "failed"
    saved = list((tmp_path / "cache").rglob("prediction.json"))
    assert saved and json.loads(saved[0].read_text())["status"] == "failed"


# --- provider: version, encoding, parsing, cache, budget ---------------------------------------

def test_inputs_respect_pinned_version_schema(tmp_path):
    fake = FakeReplicate()
    provider(tmp_path, fake).segment(make_image(tmp_path), [Prompt(text="red")])
    assert set(fake.last_input) <= set(version_inputs(PINNED_VERSION))
    assert fake.last_input["offset_masks"] is False and fake.last_input["split_output"] is True


def test_older_version_never_receives_unknown_fields(tmp_path):
    old = "e6b684bc76855b99c4be8c930ce48ab0fa89330140ae82f41f62c883e340ab76"
    fake = FakeReplicate()
    provider(tmp_path, fake, version=old).segment(make_image(tmp_path), [Prompt(text="red")])
    assert "offset_masks" not in fake.last_input and "split_output" not in fake.last_input


def test_unknown_version_rejected(tmp_path):
    with pytest.raises(ValueError, match="not in the schema snapshot"):
        provider(tmp_path, FakeReplicate(), version="0" * 64)


def test_prompt_encoding_normalised_boxes_and_points():
    p = Prompt(text="hand", positive_boxes=((10, 20, 30, 60),), negative_points=((32, 24),))
    obj = json.loads(p.to_sam3_json(64, 48))
    assert obj["positive_boxes"][0] == pytest.approx([20 / 64, 40 / 48, 20 / 64, 40 / 48], abs=1e-6)  # cx,cy,w,h
    assert obj["negative_points"] == [[0.5, 0.5]]
    with pytest.raises(ValueError):
        Prompt()


def test_end_to_end_masks_in_original_coordinates(tmp_path):
    fake = FakeReplicate()
    run = provider(tmp_path, fake).segment(make_image(tmp_path), [Prompt(text="red"), Prompt(text="blob"),
                                                                  Prompt(text="nothing")])
    red, blob, nothing = (r.instances for r in run.results)
    assert red[0].mask.shape == (H, W) and red[0].mask[10:30, 5:20].all() and red[0].area == 20 * 15
    assert len(blob) == 2 and nothing == []
    assert run.provenance["version"] == PINNED_VERSION and run.provenance["predict_time_s"] == 2.5
    assert run.provenance["estimated_usd"] == pytest.approx(2.5 * 0.000975)


def test_outputs_saved_before_parsing_and_cache_hit_is_free(tmp_path):
    fake = FakeReplicate()
    prov = provider(tmp_path, fake)
    img = make_image(tmp_path)
    prov.segment(img, [Prompt(text="red")])
    raw = list((tmp_path / "cache").rglob("outputs/prompt_0001.json"))
    assert raw, "raw outputs must be saved to disk"
    n_requests, n_ledger = len(fake.requests), len(prov.ledger.entries)
    again = prov.segment(img, [Prompt(text="red")])
    assert again.provenance["cache_hit"] is True
    assert len(fake.requests) == n_requests and len(prov.ledger.entries) == n_ledger
    assert np.array_equal(again.results[0].instances[0].mask, concept("red")[0].astype(bool))


def test_cache_key_changes_with_inputs(tmp_path):
    prov = provider(tmp_path, FakeReplicate())
    a = prov.cache_key("x" * 64, prov.build_params([Prompt(text="a")], 64, 48, 0.5))
    b = prov.cache_key("x" * 64, prov.build_params([Prompt(text="a")], 64, 48, 0.6))
    c = prov.cache_key("y" * 64, prov.build_params([Prompt(text="a")], 64, 48, 0.5))
    assert len({a, b, c}) == 3


def test_budget_refuses_before_any_request(tmp_path):
    fake = FakeReplicate()
    prov = provider(tmp_path, fake, ceiling=0.05)  # worst case 120 s * $0.000975 = $0.117 > ceiling
    with pytest.raises(BudgetExceeded, match="ceiling"):
        prov.segment(make_image(tmp_path), [Prompt(text="red")])
    assert fake.requests == []


def test_ledger_persists_and_accumulates(tmp_path):
    pol = CostPolicy(ceiling_usd=0.5)
    led = SpendLedger(tmp_path / "ledger.json", pol)
    led.record(LedgerEntry("p1", "m", "v", "succeeded", 10.0, pol.estimate_usd(10.0), "k"))
    again = SpendLedger(tmp_path / "ledger.json", pol)
    assert again.total_usd() == pytest.approx(0.00975)
    again.record(LedgerEntry("p2", "m", "v", "succeeded", None, 0.45, "k2"))
    with pytest.raises(BudgetExceeded):
        again.check(1)


def test_mask_shape_mismatch_is_an_error_not_a_guess(tmp_path):
    fake = FakeReplicate(mask_shape=(W, H))  # transposed dimensions
    with pytest.raises(SegmentationFormatError, match="mask shape"):
        provider(tmp_path, fake).segment(make_image(tmp_path), [Prompt(text="red")])


@pytest.mark.parametrize("bad, needle", [
    ({"masks": []}, "expected masks/boxes/scores"),
    ({"masks": [[[[0]]]], "boxes": [], "scores": []}, "masks, 0 boxes"),
    ({"masks": [[np.full((2, 3), 2).tolist()]], "boxes": [[0, 0, 1, 1]], "scores": [0.9]}, "non-binary"),
])
def test_parse_rejects_malformed_outputs(bad, needle):
    with pytest.raises(SegmentationFormatError, match=needle):
        parse_sam3_prompt_json(bad, 3, 2, 0)
