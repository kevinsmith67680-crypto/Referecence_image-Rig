"""Phase 2 unit tests: preservation store, occlusion overrides/z-order, providers
(fixture + Replicate inpainting against an in-process fake API), engine filtering."""

import io
import json

import httpx
import numpy as np
import pytest
from PIL import Image

from autorig.phase1 import masks as M
from autorig.phase2.occlusion import OcclusionAnalyzer, resolve_z_order
from autorig.phase2.providers import (ADAPTERS, CandidateRejected, FixtureReconstructionProvider, ModelDriftError,
                                      ReconstructionRequest, ReplicateInpaintProvider, encode_mask)
from autorig.phase2.reconstruct import ReconstructionConfig, ReconstructionEngine
from autorig.phase2.store import ArtworkStore, PreservationError
from autorig.phase2.occlusion import Region
from autorig.segmentation.budget import BudgetExceeded, CostPolicy
from autorig.segmentation.replicate_client import ReplicateClient, ReplicatePredictionError

W, H = 64, 48


def box(x0, y0, x1, y1, shape=(H, W)):
    m = np.zeros(shape, bool)
    m[y0:y1, x0:x1] = True
    return m


def layer(mask, rgb=(200, 50, 50)):
    a = np.zeros((*mask.shape, 4), np.uint8)
    a[mask] = (*rgb, 255)
    return a


# --- store -----------------------------------------------------------------------------------

def test_store_originals_immutable_and_verified(tmp_path):
    st = ArtworkStore(tmp_path, W, H)
    st.add_original("arm", layer(box(10, 10, 30, 30)), {"src": "t"})
    with pytest.raises(PreservationError, match="immutable"):
        st.add_original("arm", layer(box(0, 0, 5, 5)), {})
    with pytest.raises(PreservationError, match="no resizing"):
        st.add_original("big", np.zeros((H + 1, W, 4), np.uint8), {})
    Image.fromarray(layer(box(0, 0, 3, 3)), "RGBA").save(tmp_path / "artwork" / "original" / "arm.png")  # tamper
    assert any("modified" in p for p in st.verify())


def test_extension_cannot_touch_originals_or_leave_region(tmp_path):
    st = ArtworkStore(tmp_path, W, H)
    orig = box(10, 10, 30, 30)
    st.add_original("torso", layer(orig), {})
    region = box(25, 10, 40, 30)  # overlaps the original on x 25..30
    proposal = layer(box(5, 5, 45, 35), (0, 255, 0))  # spills everywhere
    e = st.add_extension("torso", proposal, region, method="test", region_id="r", provenance={})
    gen = M.load_mask(tmp_path / e.provenance_mask)
    assert np.array_equal(gen, region & ~orig)
    assert e.discarded_on_original == M.area(region & orig) and e.discarded_outside_region > 0
    comp = st.compose_component("torso")
    assert np.array_equal(comp[orig], layer(orig)[orig])  # original drawn on top, unchanged
    with pytest.raises(PreservationError, match="cannot be created as approved"):
        st.add_extension("torso", proposal, region, method="t", region_id="r", provenance={}, status="approved")
    cmap = np.asarray(st.change_map("torso"))
    assert (cmap[gen] == (230, 0, 160)).all()


def test_rollback_restores_original(tmp_path):
    st = ArtworkStore(tmp_path, W, H)
    st.add_original("a", layer(box(10, 10, 20, 20)), {})
    st.add_extension("a", layer(box(20, 10, 30, 20)), box(20, 10, 30, 20), method="t", region_id="r", provenance={})
    before = st.original("a").copy()
    assert st.rollback("a") == 1 and st.extensions == []
    assert np.array_equal(st.compose_component("a"), before)
    assert not list((tmp_path / "artwork" / "reconstructed").glob("a__*"))


# --- occlusion -------------------------------------------------------------------------------

def _scene():
    src = np.full((H, W, 4), 255, np.uint8)
    back = box(5, 10, 40, 40)
    front = box(30, 15, 60, 35)
    back &= ~front
    src[back] = (170, 200, 235, 255)
    src[front] = (240, 200, 170, 255)
    edge = front & ~M.erode(front, 2)
    src[edge] = (20, 20, 30, 255)  # the front part owns the outline along the contact
    return src, {"torso": back, "upper_arm_r": front}


def test_linework_cue_and_manual_override():
    src, comps = _scene()
    joints = {"shoulder_r": {"position": [35, 25]}, "elbow_r": {"position": [55, 25]}}
    e = OcclusionAnalyzer(src, comps, {"torso": 0, "upper_arm_r": 1}, joints, {}).edges()[0]
    assert (e["front"], e["back"]) == ("upper_arm_r", "torso") and e["method"].startswith("linework")
    ov = {"edges": [{"a": "torso", "b": "upper_arm_r", "front": "torso"}], "exclude_regions": []}
    e2 = OcclusionAnalyzer(src, comps, {}, joints, {}, ov).edges()[0]
    assert e2["front"] == "torso" and e2["confidence"] == 1.0 and e2["method"] == "manual_override"


def test_z_order_resolution_breaks_cycles():
    edges = [{"front": "b", "back": "a", "confidence": 0.9}, {"front": "c", "back": "b", "confidence": 0.8},
             {"front": "a", "back": "c", "confidence": 0.55}]
    order, dropped = resolve_z_order(["a", "b", "c"], edges, {"a": 0, "b": 1, "c": 2})
    assert order == ["a", "b", "c"] and len(dropped) == 1 and dropped[0]["confidence"] == 0.55


# --- engine + fixture provider ------------------------------------------------------------------

def test_engine_filters_generative_output(tmp_path):
    src, comps = _scene()
    st = ArtworkStore(tmp_path, W, H)
    for c, m in comps.items():
        st.add_original(c, np.where(m[..., None], src, 0).astype(np.uint8), {})
    region_mask = box(30, 15, 40, 35) & ~comps["torso"]
    region = Region("torso__under__upper_arm_r", "torso", ["upper_arm_r"], region_mask, "shoulder_r",
                    ["shoulder_r"], "test", 0.8, False)

    def greedy(req, k):  # paints EVERYTHING, including protected original pixels
        return np.full((*req.mask.shape, 4), (0, 255, 0, 255), np.uint8)

    prov = FixtureReconstructionProvider(greedy)
    cfg = ReconstructionConfig(force_generative={region.id}, n_candidates=2, context_px=4, max_crop=64)
    eng = ReconstructionEngine(st, src, comps, {"torso": None}, np.zeros((H, W), bool), prov, cfg)
    res = eng.run([region])[0]
    assert res["strategy"] == "generative" and len(res["extensions"]) == 2
    e0, e1 = st.extensions
    assert e0.status == "pending_review" and e1.status == "alternative"
    gen = M.load_mask(tmp_path / e0.provenance_mask)
    assert np.array_equal(gen, region_mask) and e0.provenance["pixels_changed_outside_mask_discarded"] > 0
    assert st.verify() == []


def test_candidate_size_mismatch_rejected():
    req = ReconstructionRequest("r", np.zeros((32, 32, 4), np.uint8), np.ones((32, 32), bool), "p")
    prov = FixtureReconstructionProvider(lambda r, k: np.zeros((30, 32, 4), np.uint8))
    with pytest.raises(CandidateRejected):
        prov.reconstruct(req)


# --- Replicate inpainting provider (fake API) ---------------------------------------------------

class FakeInpaint:
    def __init__(self, adapter, *, live_version=None, fail=None, status="succeeded", out_size=None):
        self.a = ADAPTERS[adapter]
        self.live_version = live_version or self.a.pinned_version
        self.fail = list(fail or [])
        self.status, self.out_size = status, out_size
        self.requests, self.uploads, self.created = [], [], []

    def __call__(self, r: httpx.Request):
        self.requests.append(r)
        path = r.url.path
        if r.url.host == "api.replicate.com":
            if path == f"/v1/models/{self.a.owner}/{self.a.name}" and r.method == "GET":
                return httpx.Response(200, json={"latest_version": {"id": self.live_version}})
            if path == "/v1/files":
                self.uploads.append(r.content)
                return httpx.Response(201, json={"urls": {"get": f"https://api.replicate.com/v1/files/f{len(self.uploads)}"}})
            if path == f"/v1/models/{self.a.owner}/{self.a.name}/predictions":
                self.created.append(json.loads(r.content)["input"])
                return httpx.Response(201, json={"id": f"p{len(self.created)}", "status": "starting"})
            if path.startswith("/v1/predictions/"):
                if self.fail:
                    return httpx.Response(self.fail.pop(0), json={"detail": "busy"})
                pid = path.rsplit("/", 1)[1]
                inp = self.created[int(pid[1:]) - 1]
                n = inp.get("num_outputs", 1)
                outs = [f"https://replicate.delivery/x/{pid}_{k}.png" for k in range(n)]
                return httpx.Response(200, json={"id": pid, "status": self.status, "error": None if self.status ==
                                                 "succeeded" else "nsfw", "output": outs if self.a.outputs_field_is_list
                                                 else outs[0], "metrics": {"predict_time": 3.0}})
        if r.url.host == "replicate.delivery":
            size = self.out_size or (64, 64)
            buf = io.BytesIO()
            Image.new("RGB", size, (10, 200, 10)).save(buf, "PNG")
            return httpx.Response(200, content=buf.getvalue())
        return httpx.Response(404)


def provider(tmp_path, fake, adapter="flux-fill-dev", ceiling=1.0):
    client = ReplicateClient("fake-token-not-a-secret", transport=httpx.MockTransport(fake), sleep=lambda s: None,
                             backoff_seconds=0)
    return ReplicateInpaintProvider(client, tmp_path, adapter=adapter, policy=CostPolicy(ceiling_usd=ceiling),
                                    poll_seconds=0)


def req(n=2, neg=None):
    m = np.zeros((64, 64), bool)
    m[20:40, 20:40] = True
    return ReconstructionRequest("r1", np.full((64, 64, 4), 255, np.uint8), m, "flat cartoon", neg, n, 7)


def test_inpaint_candidates_seeds_cache_and_cost(tmp_path):
    fake = FakeInpaint("flux-fill-dev")
    prov = provider(tmp_path, fake)
    cands = prov.reconstruct(req(n=2))
    assert len(cands) == 2 and [c.seed for c in cands] == [7, 8]
    assert fake.created[0]["num_outputs"] == 2 and fake.created[0]["megapixels"] == "match_input"
    assert prov.ledger.total_usd() == pytest.approx(2 * 0.05)
    n = len(fake.requests)
    again = prov.reconstruct(req(n=2))
    assert len(fake.requests) == n and np.array_equal(again[0].rgba, cands[0].rgba)  # cache hit, free


def test_mask_conventions_differ_per_model(tmp_path):
    m = req().mask
    flux = np.asarray(encode_mask(m, ADAPTERS["flux-fill-dev"].mask_white_means_inpaint))
    ideo = np.asarray(encode_mask(m, ADAPTERS["ideogram-v2"].mask_white_means_inpaint))
    assert flux[30, 30] == 255 and flux[0, 0] == 0
    assert ideo[30, 30] == 0 and ideo[0, 0] == 255
    fake = FakeInpaint("ideogram-v2")
    prov = provider(tmp_path, fake, adapter="ideogram-v2")
    prov.reconstruct(req(n=2, neg="texture"))
    assert len(fake.created) == 2 and fake.created[0]["negative_prompt"] == "texture"  # 1 output per call


def test_negative_prompt_rejected_when_unsupported(tmp_path):
    with pytest.raises(ValueError, match="negative prompt"):
        provider(tmp_path, FakeInpaint("flux-fill-dev")).reconstruct(req(neg="texture"))


def test_model_drift_refused_before_spending(tmp_path):
    fake = FakeInpaint("flux-fill-dev", live_version="0" * 64)
    with pytest.raises(ModelDriftError, match="changed"):
        provider(tmp_path, fake).reconstruct(req())
    assert not fake.created and not fake.uploads


def test_budget_refused_before_any_upload(tmp_path):
    fake = FakeInpaint("flux-fill-dev")
    with pytest.raises(BudgetExceeded):
        provider(tmp_path, fake, ceiling=0.05).reconstruct(req(n=2))
    assert not fake.uploads and not fake.created


def test_retry_then_success_and_failed_prediction_recorded(tmp_path):
    fake = FakeInpaint("flux-fill-dev", fail=[503, 429])
    assert len(provider(tmp_path / "a", fake).reconstruct(req(n=1))) == 1
    bad = FakeInpaint("flux-fill-dev", status="failed")
    prov = provider(tmp_path / "b", bad)
    with pytest.raises(ReplicatePredictionError, match="nsfw"):
        prov.reconstruct(req(n=1))
    assert prov.ledger.entries[-1].status == "failed" and prov.ledger.entries[-1].estimated_usd == 0.0


def test_wrong_size_output_rejected_not_resized(tmp_path):
    fake = FakeInpaint("flux-fill-dev", out_size=(60, 64))
    cands = provider(tmp_path, fake).reconstruct(req(n=1))
    assert "rejected" in cands[0].provenance and not (cands[0].rgba[..., 3] > 0).any()


def test_crop_constraints_enforced(tmp_path):
    bad = ReconstructionRequest("r", np.zeros((50, 64, 4), np.uint8), np.ones((50, 64), bool), "p")
    with pytest.raises(ValueError, match="multiple of 32"):
        provider(tmp_path, FakeInpaint("flux-fill-dev")).reconstruct(bad)
