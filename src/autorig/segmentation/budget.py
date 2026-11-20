"""Replicate spend tracking and ceiling.

Replicate bills public models per second of GPU time; the API does not expose
the hardware a public model runs on nor a per-prediction price. Costs are
therefore ESTIMATES: ``metrics.predict_time`` x a configurable USD/second
rate. The default rate is the Nvidia L40S price ($0.000975/s, replicate.com
pricing as of 2026), the most expensive GPU SKU this account lists, so the
estimate errs on the high side.

The ledger is cumulative and persisted (JSON), so the ceiling holds across
runs. Before each prediction the worst case (``max_seconds_per_prediction`` x
rate) must fit under the ceiling, otherwise :class:`BudgetExceeded` is raised
and nothing is sent.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_USD_PER_SECOND = 0.000975
DEFAULT_CEILING_USD = 1.00
DEFAULT_MAX_SECONDS = 120.0


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class CostPolicy:
    ceiling_usd: float = DEFAULT_CEILING_USD
    usd_per_second: float = DEFAULT_USD_PER_SECOND
    max_seconds_per_prediction: float = DEFAULT_MAX_SECONDS
    rate_source: str = "assumed Nvidia L40S $0.000975/s (replicate.com/pricing); hardware not exposed by API"

    @classmethod
    def from_env(cls, **overrides: Any) -> "CostPolicy":
        env = {}
        if "AUTORIG_REPLICATE_BUDGET_USD" in os.environ:
            env["ceiling_usd"] = float(os.environ["AUTORIG_REPLICATE_BUDGET_USD"])
        if "AUTORIG_REPLICATE_USD_PER_SECOND" in os.environ:
            env["usd_per_second"] = float(os.environ["AUTORIG_REPLICATE_USD_PER_SECOND"])
            env["rate_source"] = "AUTORIG_REPLICATE_USD_PER_SECOND"
        env.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**env)

    def worst_case_usd(self) -> float:
        return self.max_seconds_per_prediction * self.usd_per_second

    def estimate_usd(self, predict_seconds: float | None) -> float:
        return max(0.0, float(predict_seconds or 0.0)) * self.usd_per_second


@dataclass
class LedgerEntry:
    prediction_id: str
    model: str
    version: str
    status: str
    predict_time_s: float | None
    estimated_usd: float
    cache_key: str
    created_at: float = field(default_factory=time.time)
    note: str = ""


class SpendLedger:
    def __init__(self, path: str | Path, policy: CostPolicy):
        self.path = Path(path)
        self.policy = policy
        self.entries: list[LedgerEntry] = []
        if self.path.is_file():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = [LedgerEntry(**e) for e in data.get("entries", [])]

    def total_usd(self) -> float:
        return round(sum(e.estimated_usd for e in self.entries), 6)

    def remaining_usd(self) -> float:
        return round(self.policy.ceiling_usd - self.total_usd(), 6)

    def check(self, n_predictions: int = 1) -> None:
        need = n_predictions * self.policy.worst_case_usd()
        if self.total_usd() + need > self.policy.ceiling_usd + 1e-12:
            raise BudgetExceeded(
                f"Replicate budget ceiling ${self.policy.ceiling_usd:.2f} would be exceeded: spent "
                f"${self.total_usd():.4f} (estimated), worst case for {n_predictions} more prediction(s) "
                f"${need:.4f}. Raise AUTORIG_REPLICATE_BUDGET_USD deliberately if this is intended.")

    def check_amount(self, usd: float, what: str = "this request") -> None:
        """Refuse BEFORE a call whose worst-case cost would cross the ceiling."""
        if self.total_usd() + usd > self.policy.ceiling_usd + 1e-12:
            raise BudgetExceeded(
                f"Replicate budget ceiling ${self.policy.ceiling_usd:.2f} would be exceeded by {what}: spent "
                f"${self.total_usd():.4f} (estimated), worst case ${usd:.4f}.")

    def record(self, entry: LedgerEntry) -> None:
        self.entries.append(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"policy": asdict(self.policy), "total_estimated_usd": self.total_usd(),
                                   "entries": [asdict(e) for e in self.entries]}, indent=2), encoding="utf-8")
        tmp.replace(self.path)
