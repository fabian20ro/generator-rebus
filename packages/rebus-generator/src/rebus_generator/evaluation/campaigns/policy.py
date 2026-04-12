"""Working prompt benchmark policy for the current prompt campaign."""

from __future__ import annotations

from pathlib import Path


UNCERTAINTY_DELTA = 0.5
WORKING_DATASET_SIZE = 70
EXPERIMENT_COMPARISON_RUNS = 3
NEAR_MISS_PASS_DELTA = -(1.0 / WORKING_DATASET_SIZE)
RESEARCH_SIGNAL_MIN_GAINED_WORDS = 3
FAMILY_STOP_CONSECUTIVE_NON_KEEPS = 4
FAMILY_STOP_TOTAL_NON_KEEPS = 6
FAMILY_STOP_REPEAT_COLLATERAL = 3
CAMPAIGN_STOP_STALE_FAMILIES = 3
V2_CAMPAIGN_STOP_STALE_FAMILIES = 4
V2_FAMILY_STOP_CONSECUTIVE_NON_KEEPS = 10
V2_FAMILY_STOP_TOTAL_NON_KEEPS = 10
V2_FAMILY_STOP_REPEAT_PRIMARY = 4
V3_CAMPAIGN_STOP_STALE_FAMILIES = 4
V3_FAMILY_STOP_CONSECUTIVE_NON_KEEPS = 3
V3_FAMILY_STOP_TOTAL_NON_KEEPS = 3
V3_FAMILY_STOP_REPEAT_PRIMARY = 3
V4_CAMPAIGN_STOP_STALE_FAMILIES = 3
V4_FAMILY_STOP_CONSECUTIVE_NON_KEEPS = 3
V4_FAMILY_STOP_TOTAL_NON_KEEPS = 3
V4_FAMILY_STOP_REPEAT_PRIMARY = 3
V5_CAMPAIGN_STOP_STALE_FAMILIES = 3
V5_FAMILY_STOP_CONSECUTIVE_NON_KEEPS = 3
V5_FAMILY_STOP_TOTAL_NON_KEEPS = 3
V5_FAMILY_STOP_REPEAT_PRIMARY = 3
V6_CAMPAIGN_STOP_STALE_FAMILIES = 3
V6_FAMILY_STOP_CONSECUTIVE_NON_KEEPS = 3
V6_FAMILY_STOP_TOTAL_NON_KEEPS = 3
V6_FAMILY_STOP_REPEAT_PRIMARY = 3
V7_CAMPAIGN_STOP_STALE_FAMILIES = 3
V7_FAMILY_STOP_CONSECUTIVE_NON_KEEPS = 3
V7_FAMILY_STOP_TOTAL_NON_KEEPS = 3
V7_FAMILY_STOP_REPEAT_PRIMARY = 3
WORKING_DATASET_TIER_COUNTS = {
    "low": 30,
    "medium": 25,
    "high": 15,
}
HISTORICAL_PROMPT_EVIDENCE = (
    "results1.tsv",
    "results2.tsv",
    "results3.tsv",
    "results4.tsv",
    "results5.tsv",
    "results6.tsv",
    "results7.tsv",
    "results8.tsv",
)
WORKING_RESULTS_PATH = Path("build/evaluation/assessment/results.tsv")
WORKING_BASELINE_DESCRIPTION = "baseline_results_20260329_v4exp001"
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
PRIMARY_FRAGILE_WORD_WATCH = ("AZ", "FERMENT", "MIRE", "OSTRACA", "SAN", "ETAN")
SECONDARY_FRAGILE_WORD_WATCH = ("STIMULAT", "NUC", "ADAPOST", "ATOMA")
EXPERIMENT_FAMILY_PRIORITY = (
    "definition_positive_examples",
    "definition_guidance",
    "definition_rewrite_bundles",
    "rate_rules",
    "rewrite_structural_guidance",
    "definition_negative_examples",
    "rate_counterexamples",
    "rewrite_framing",
    "verify_examples_short",
    "verify_examples_rare",
    "verify_bundles",
    "definition_rate_bundles",
    "confirm_bundles",
    "cleanup",
)
V2_EXPERIMENT_FAMILY_PRIORITY = (
    "short_word_exactness",
    "near_neighbor_exclusion",
    "blank_output_concretization",
    "rare_technical_noun_rescue",
)
V3_EXPERIMENT_FAMILY_PRIORITY = (
    "system_factor_temperatures",
    "verify_minimal_procedural",
    "rewrite_generic_exclusion",
    "prompt_dedup_cleanup",
)
V4_EXPERIMENT_FAMILY_PRIORITY = (
    "rewrite_rule_readditions",
    "rewrite_header_variants",
    "rewrite_compactness_bias",
)
V5_EXPERIMENT_FAMILY_PRIORITY = (
    "header_signal_isolation",
    "header_signal_blends",
    "precision_support",
)
V6_EXPERIMENT_FAMILY_PRIORITY = (
    "verify_romanian_only",
    "verify_resolution_compaction",
    "verify_targeted_examples",
    "verify_user_exactness",
    "rate_exact_answer_calibration",
    "rate_rare_sense_calibration",
    "definition_positive_romanian_sense",
    "definition_vague_neighbor_counterexamples",
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
