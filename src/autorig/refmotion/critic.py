"""VisualAnimationCritic and the controlled revision loop (Phase 3.7, Part C).

The critic reads a rendered frame, plus the reference and the original drawing when given, and returns structured
defects. Each defect has:
- ``region`` and ``issue``;
- ``severity``: info / minor / major / blocking;
- ``suggestedCorrection`` and ``requiresArtwork``;
- ``control``: the responsible parameter, when a control can address it;
- ``evidence``, ``source`` and ``confidence``.

Every defect is a *hypothesis* (``hypothesis: true``). The critic never writes artwork, never changes controls and
never approves anything (``approves`` is always False).

Providers:
- ``RuleBasedCriticProvider`` reads the measurable checks the pipeline already makes:
  - hand selection (NEEDS_ARTWORK, fallbacks, override violations);
  - wrist alignment (``motion/handcheck.py``);
  - shoulder silhouette (``performance/shoulders.py``);
  - reference angle error (retarget comparison).

  It is deterministic, local and fast.
- ``VLMCriticProvider`` is an optional vision-language model behind a provider interface. It is disabled unless
  ``AUTORIG_VLM_CRITIC`` names an endpoint and a key is configured, and it is never used during interactive
  animation. It was not run in this environment: it is untested integration code, not a working critic.

``RevisionLoop``:
1. render the frame and collect the defects;
2. pick the worst actionable defect that has a control;
3. apply one limited correction to a temporary copy of the performance;
4. re-render and re-critique;
5. keep the candidate only if the target defect improved and no new blocking defect appeared, otherwise roll back.

It makes at most ``max_attempts`` attempts, detects oscillation (a control reversing direction) and never edits the
original performance or artwork. The result is a proposal that a person must accept (``reference.revision.accept``).
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any

SEVERITY = {"info": 0, "minor": 1, "major": 2, "blocking": 3}


@dataclass
class Defect:
    region: str
    issue: str
    severity: str
    suggestedCorrection: str
    requiresArtwork: bool = False
    control: dict[str, Any] | None = None  # {"param", "delta", "cap", "metric"}
    evidence: dict[str, Any] = field(default_factory=dict)
    source: str = "rules"
    confidence: float = 1.0
    hypothesis: bool = True

    def key(self) -> str:
        return f"{self.region}:{self.issue.split(':')[0]}"


class CriticProvider:
    name = "base"
    local = True

    def evaluate(self, ctx: dict[str, Any]) -> list[Defect]:
        raise NotImplementedError


class RuleBasedCriticProvider(CriticProvider):
    name = "rules"
    REF_ERR_DEG = 6.0

    def evaluate(self, ctx: dict[str, Any]) -> list[Defect]:
        from ..motion.handcheck import wrist_check  # noqa: PLC0415
        from ..performance import shoulders  # noqa: PLC0415

        r, eng = ctx["result"], ctx["eng"]
        mode = ctx.get("mode", "review")
        out: list[Defect] = []
        perf_h = (r.get("performance") or {}).get("hands") or {}
        for s in ("r", "l"):
            dec = (r.get("hand_selection") or {}).get("decisions", {}).get(s, {})
            pdec = (perf_h.get(s) or {}).get("decision") or dec
            for n in pdec.get("needs_artwork") or []:
                if n["status"] == "NEEDS_ARTWORK":
                    out.append(Defect(f"hand_{s}", f"needs artwork: {n['pose']}", "blocking" if mode == "production" else "major",
                                      f"draw the {n['pose']} hand ({', '.join(n.get('palm') or [])})", True,
                                      evidence={"reason": n["reason"]}))
                elif n["status"] == "NEEDS_APPROVAL":
                    out.append(Defect(f"hand_{s}", f"needs approval: {n['pose']}", "minor",
                                      "review the pending drawing in the hand inspector", False,
                                      evidence={"reason": n["reason"], "cid": n.get("cid")}))
            oc = dec.get("override_check")
            if oc and not oc["ok"]:
                rot = next((v for v in oc["violations"] if v["constraint"] == "rotation"), None)
                ctl = None
                if rot and oc.get("rotation_from_drawn_deg") is not None:
                    sgn = -1.0 if oc["rotation_from_drawn_deg"] > 0 else 1.0
                    ctl = {"param": f"arm_{s}_wrist", "delta": 5.0 * sgn, "cap": 15.0, "metric": f"rotation_{s}"}
                out.append(Defect(f"hand_{s}", "explicit hand choice violates a hard constraint", "major",
                                  "; ".join(v["reason"] for v in oc["violations"]), False, ctl,
                                  evidence={"violations": oc["violations"]}))
            if "render" in r and (r.get("hands") or {}).get(s):
                wc = wrist_check(r, eng, s)
                for k in wc.get("failed", []):
                    out.append(Defect(f"wrist_{s}", f"wrist check failed: {k}", "major",
                                      "adjust the drawing's placement (hand library offsets) or use another drawing",
                                      False, evidence=wc["checks"].get(k, {})))
        if "render" in r and "image" in r:
            m = shoulders.measure(r["image"], r["solve"]["joints_posed"], r["pad"])
            ref = ctx.get("shoulder_reference")
            chk = shoulders.check(m, ref, shrug=bool(ctx.get("shrug")), abduction=shoulders.abductions(r))
            for s in ("r", "l"):
                x = chk["sides"].get(s) or {}
                if x.get("status") != "OK" or not x.get("applies", True):
                    continue
                if ref and x.get("rise_px", 0) > shoulders.THRESHOLDS["max_rise_px"] and ctx.get("shrug"):
                    out.append(Defect(f"shoulder_{s}", f"shoulder rises {x['rise_px']:.0f} px (inflated pad)", "major",
                                      "lower the clavicle elevation", False,
                                      {"param": f"clavicle_{s}", "delta": -2.0, "cap": 8.0, "metric": f"rise_{s}"},
                                      evidence=x))
                if x["slope_deg"] < shoulders.THRESHOLDS["min_slope_deg"]:
                    out.append(Defect(f"shoulder_{s}", f"shoulder line rises outward ({x['slope_deg']:.1f} deg)",
                                      "minor", "lower the clavicle elevation", False,
                                      {"param": f"clavicle_{s}", "delta": -2.0, "cap": 8.0, "metric": f"slope_{s}"},
                                      evidence=x))
        tgt = ctx.get("reference_params")
        if tgt:
            got = ctx.get("params") or {}
            for c, v in tgt.items():
                if c in got and abs(got[c] - v) > self.REF_ERR_DEG and c.startswith(("arm_", "clavicle_", "head", "line_")):
                    e = v - got[c]
                    out.append(Defect(c, f"differs from the reference by {e:+.1f}", "minor",
                                      f"move {c} toward the reference", False,
                                      {"param": c, "delta": 0.5 * e, "cap": 10.0, "metric": f"ref_{c}"},
                                      evidence={"reference": v, "current": got[c]}))
        return out


class VLMCriticProvider(CriticProvider):
    """Optional vision-language critic. Untested integration code: not executed in this environment.

    Contract: POST the rendered frame, the reference frame and the original drawing (PNG, base64) with ``PROMPT`` to
    an OpenAI-compatible chat endpoint (``AUTORIG_VLM_CRITIC`` = base URL, key in ``AUTORIG_VLM_KEY``). The reply must
    be JSON: a list of objects with the Defect fields. Every returned item is forced to ``hypothesis: true``,
    ``source: vlm`` and a confidence <= 0.6, and ``control`` is dropped, so a model can never drive a control
    directly."""

    name = "vlm"
    local = False
    PROMPT = ("You review one frame of a 2D cartoon character animation against its reference motion and the "
              "character's original drawing. List concrete visual defects as JSON objects with fields region, "
              "issue, severity (info|minor|major|blocking), suggestedCorrection, requiresArtwork (bool). Judge "
              "gesture readability, silhouette, hands (handedness, palm direction), wrist attachment, shoulders, "
              "body mechanics, proportions, clothing and fidelity to the original drawing. Do not approve anything.")

    def __init__(self, endpoint: str | None = None, model: str | None = None):
        self.endpoint = endpoint or os.environ.get("AUTORIG_VLM_CRITIC")
        self.model = model or os.environ.get("AUTORIG_VLM_MODEL")

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.model and os.environ.get("AUTORIG_VLM_KEY"))

    def status(self) -> dict[str, Any]:
        return {"name": self.name, "configured": self.configured, "local": False,
                "tested": False, "note": "integration code; not executed in the Phase 3.7 environment"}

    def evaluate(self, ctx: dict[str, Any]) -> list[Defect]:
        if not self.configured or ctx.get("interactive"):
            return []
        raise NotImplementedError("VLM critic transport is deliberately not wired in Phase 3.7 (no configured "
                                  "endpoint was available to test it)")

    @staticmethod
    def parse(reply: str) -> list[Defect]:
        """Strict parsing of a model reply (tested with fixtures)."""
        items = json.loads(reply)
        out = []
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict) or it.get("severity") not in SEVERITY:
                continue
            out.append(Defect(str(it.get("region", "frame"))[:40], str(it.get("issue", ""))[:200], it["severity"],
                              str(it.get("suggestedCorrection", ""))[:300], bool(it.get("requiresArtwork", False)),
                              None, {}, "vlm", min(float(it.get("confidence", 0.5) or 0.5), 0.6), True))
        return out


class VisualAnimationCritic:
    def __init__(self, providers: list[CriticProvider] | None = None):
        self.providers = providers or [RuleBasedCriticProvider()]

    approves = False

    def critique(self, ctx: dict[str, Any]) -> dict[str, Any]:
        defects: list[Defect] = []
        for p in self.providers:
            if ctx.get("interactive") and not p.local:
                continue
            defects += p.evaluate(ctx)
        seen, uniq = set(), []
        for d in sorted(defects, key=lambda d: -SEVERITY[d.severity]):
            if d.key() not in seen:
                seen.add(d.key())
                uniq.append(d)
        return {"defects": [asdict(d) for d in uniq], "blocking": sum(d.severity == "blocking" for d in uniq),
                "requires_artwork": sorted({d.issue for d in uniq if d.requiresArtwork}),
                "providers": [p.name for p in self.providers], "approves": False,
                "note": "defects are hypotheses; nothing is approved by the critic"}


class RevisionLoop:
    def __init__(self, eng, vis, critic: VisualAnimationCritic | None = None, max_attempts: int = 3):
        self.eng, self.vis = eng, vis
        self.critic = critic or VisualAnimationCritic()
        self.max_attempts = max_attempts

    def _render(self, perf_json: dict[str, Any], t: float, review: bool):
        from ..performance.director import Performance, PerformanceDirector  # noqa: PLC0415

        perf = Performance.from_json(perf_json)
        perf.allow_pending = review
        d = PerformanceDirector(self.eng, self.vis, perf)
        r = d.solve(t, render=True, corrective_mode="all" if review else "auto")
        return r, d.pose_at(t)[2]["params"]

    @staticmethod
    def _metric(c: dict[str, Any], metric: str) -> float | None:
        for d in c["defects"]:
            ctl = d.get("control") or {}
            if ctl.get("metric") == metric:
                ev = d["evidence"]
                if metric.startswith("rise_"):
                    return float(ev.get("rise_px", 0.0))
                if metric.startswith("slope_"):
                    return -float(ev.get("slope_deg", 0.0))
                if metric.startswith("ref_"):
                    return abs(float(ev["reference"]) - float(ev["current"]))
                return SEVERITY[d["severity"]]
        return 0.0  # the defect is gone

    def run(self, perf_json: dict[str, Any], t: float, ctx_extra: dict[str, Any] | None = None,
            review: bool = True) -> dict[str, Any]:
        base = copy.deepcopy(perf_json)
        r, params = self._render(base, t, review)
        ctx = {"eng": self.eng, "result": r, "params": params, "mode": "review" if review else "production",
               **(ctx_extra or {})}
        c0 = self.critic.critique(ctx)
        cur, cur_c, attempts, moved = base, c0, [], {}
        for _ in range(self.max_attempts):
            act = [d for d in cur_c["defects"] if d.get("control") and not d["requiresArtwork"]]
            if not act:
                break
            d = act[0]
            ctl = d["control"]
            p = ctl["param"]
            prev = moved.get(p, 0.0)
            if prev and (prev > 0) != (ctl["delta"] > 0):
                attempts.append({"defect": d["issue"], "param": p, "result": "stopped: oscillation"})
                break
            step = max(-ctl["cap"] - prev, min(ctl["cap"] - prev, ctl["delta"])) if ctl["delta"] < 0 else \
                min(ctl["cap"] - prev, ctl["delta"])
            if abs(step) < 1e-6:
                attempts.append({"defect": d["issue"], "param": p, "result": "stopped: correction cap reached"})
                break
            cand = copy.deepcopy(cur)
            k = min(cand["base_keys"], key=lambda k: abs(k["t"] - t))  # the key that holds this frame's pose
            near = [kk for kk in cand["base_keys"] if abs(kk["t"] - k["t"]) < 1e-9 or
                    (kk["t"] <= t + 0.6 and kk["t"] >= t - 0.6 and p in kk["params"])]
            for kk in near:
                kk["params"][p] = round(float(kk["params"].get(p, 0.0)) + step, 3)
            r2, params2 = self._render(cand, t, review)
            c2 = self.critic.critique({**ctx, "result": r2, "params": params2})
            before, after = self._metric(cur_c, ctl["metric"]), self._metric(c2, ctl["metric"])
            new_block = c2["blocking"] > cur_c["blocking"]
            ok = after < before - 1e-6 and not new_block
            attempts.append({"defect": d["issue"], "param": p, "step": round(step, 3), "metric": ctl["metric"],
                             "before": round(before, 3), "after": round(after, 3), "new_blocking": new_block,
                             "result": "kept" if ok else "rolled back"})
            if ok:
                cur, cur_c = cand, c2
                moved[p] = prev + step
            else:
                break
        changed = cur is not base
        return {"initial": c0, "final": cur_c, "attempts": attempts, "changed": changed,
                "candidate": cur if changed else None,
                "status": "PROPOSED_NEEDS_APPROVAL" if changed else "NO_CHANGE",
                "approved": False, "original_untouched": base == perf_json}


__all__ = ["VisualAnimationCritic", "RuleBasedCriticProvider", "VLMCriticProvider", "RevisionLoop", "Defect", "SEVERITY"]
