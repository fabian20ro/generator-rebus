"""Working prompt benchmark policy for the curated 2026-03-21 reset."""

from __future__ import annotations

from pathlib import Path


UNCERTAINTY_DELTA = 0.5
WORKING_DATASET_SIZE = 70
NEAR_MISS_PASS_DELTA = -(1.0 / WORKING_DATASET_SIZE)
RESEARCH_SIGNAL_MIN_GAINED_WORDS = 3
FAMILY_STOP_CONSECUTIVE_NON_KEEPS = 4
FAMILY_STOP_TOTAL_NON_KEEPS = 6
FAMILY_STOP_REPEAT_COLLATERAL = 3
CAMPAIGN_STOP_STALE_FAMILIES = 3
WORKING_DATASET_TIER_COUNTS = {
    "low": 30,
    "medium": 25,
    "high": 15,
}
HISTORICAL_PROMPT_EVIDENCE = ("results4.tsv",)
WORKING_RESULTS_PATH = Path(__file__).with_name("results.tsv")
WORKING_BASELINE_DESCRIPTION = "baseline_results_20260321"
PILOT_EXPERIMENT_RANGE = (1, 12)
EXPERIMENT_BLOCK_RANGES = {
    "cleanup": (1, 12),
    "verify-examples": (13, 36),
    "rewrite-anti-distractor": (37, 48),
    "definition-examples": (49, 60),
    "rate-exactness-calibration": (61, 72),
    "verify-bundles": (73, 84),
    "definition-rewrite-bundles": (85, 92),
    "definition-rate-bundles": (93, 96),
    "confirm-bundles": (97, 100),
}
FOLLOWUP_PRIORITY = (
    "verify-examples",
    "rewrite-anti-distractor",
    "rate-exactness-calibration",
    "definition-examples",
    "verify-bundles",
    "definition-rewrite-bundles",
    "definition-rate-bundles",
    "confirm-bundles",
)
DIRECTION_FOLLOWUP_PRESETS = {
    "verify-led": ("verify-examples", "verify-bundles"),
    "rewrite-led": ("rewrite-anti-distractor", "definition-rewrite-bundles"),
    "rate-led": ("rate-exactness-calibration", "definition-rate-bundles"),
}
CONTROL_WORD_WATCH = ("ADAPOST", "ETAN")
CONTROL_WORD_REPEAT_FAIL_ACTION = "demote-or-replace"
EXPERIMENT_FAMILY_PRIORITY = (
    "definition_examples",
    "definition_rewrite_bundles",
    "rate_exactness",
    "rewrite_anti_distractor",
    "verify_examples_short",
    "verify_examples_rare",
    "verify_bundles",
    "definition_rate_bundles",
    "confirm_bundles",
    "cleanup",
)


def load_latest_kept_result(results_path: Path = WORKING_RESULTS_PATH) -> dict[str, str]:
    lines = results_path.read_text(encoding="utf-8").strip().splitlines()
    if len(lines) < 2:
        raise ValueError(f"No benchmark rows in {results_path}")
    for line in reversed(lines[1:]):
        fields = line.split("\t")
        if len(fields) < 7 or fields[5] != "keep":
            continue
        return {
            "commit": fields[0],
            "composite": fields[1],
            "pass_rate": fields[2],
            "avg_semantic": fields[3],
            "avg_rebus": fields[4],
            "status": fields[5],
            "description": fields[6],
        }
    raise ValueError(f"No kept benchmark row in {results_path}")
