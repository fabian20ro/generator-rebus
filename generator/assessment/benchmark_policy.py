"""Working prompt benchmark policy for the curated 2026-03-21 reset."""

from __future__ import annotations

from dataclasses import dataclass


UNCERTAINTY_DELTA = 0.5
WORKING_DATASET_SIZE = 70
WORKING_DATASET_TIER_COUNTS = {
    "low": 30,
    "medium": 25,
    "high": 15,
}
HISTORICAL_PROMPT_EVIDENCE = ("results4.tsv",)


@dataclass(frozen=True)
class BenchmarkIncumbent:
    description: str
    composite: float
    pass_rate: float
    avg_semantic: float
    avg_rebus: float
    tier_pass_rates: dict[str, float]


WORKING_INCUMBENT = BenchmarkIncumbent(
    description="baseline_results_20260321",
    composite=73.3,
    pass_rate=0.300,
    avg_semantic=9.1,
    avg_rebus=8.1,
    tier_pass_rates={
        "low": 0.067,
        "medium": 0.240,
        "high": 0.867,
    },
)
