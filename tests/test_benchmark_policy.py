import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from generator.assessment.benchmark_policy import (
    DIRECTION_FOLLOWUP_PRESETS,
    EXPERIMENT_BLOCK_RANGES,
    FOLLOWUP_PRIORITY,
    HISTORICAL_PROMPT_EVIDENCE,
    WORKING_BASELINE_DESCRIPTION,
    WORKING_DATASET_SIZE,
    WORKING_DATASET_TIER_COUNTS,
    load_latest_kept_result,
)


class BenchmarkPolicyTests(unittest.TestCase):
    def test_working_baseline_is_named_but_not_hardcoded_as_metrics(self):
        self.assertEqual("baseline_results_20260321", WORKING_BASELINE_DESCRIPTION)

    def test_working_dataset_counts_match_curated_reset(self):
        self.assertEqual(70, WORKING_DATASET_SIZE)
        self.assertEqual(
            {
                "low": 30,
                "medium": 25,
                "high": 15,
            },
            WORKING_DATASET_TIER_COUNTS,
        )

    def test_results4_stays_historical_evidence_only(self):
        self.assertEqual(("results4.tsv",), HISTORICAL_PROMPT_EVIDENCE)

    def test_load_latest_kept_result_reads_results_tsv(self):
        with TemporaryDirectory() as tmpdir:
            results_path = Path(tmpdir) / "results.tsv"
            results_path.write_text(
                "\n".join(
                    [
                        "commit\tcomposite\tpass_rate\tavg_semantic\tavg_rebus\tstatus\tdescription",
                        "aaa111\t72.0\t0.250\t8.8\t7.9\tkeep\tolder",
                        "bbb222\t72.5\t0.260\t8.9\t8.0\tdiscard\tignored",
                        "ccc333\t73.3\t0.300\t9.1\t8.1\tkeep\tbaseline_results_20260321",
                    ]
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                {
                    "commit": "ccc333",
                    "composite": "73.3",
                    "pass_rate": "0.300",
                    "avg_semantic": "9.1",
                    "avg_rebus": "8.1",
                    "status": "keep",
                    "description": "baseline_results_20260321",
                },
                load_latest_kept_result(results_path),
            )

    def test_block_ranges_and_followup_priority_match_reset_plan(self):
        self.assertEqual((1, 12), EXPERIMENT_BLOCK_RANGES["cleanup"])
        self.assertEqual((13, 36), EXPERIMENT_BLOCK_RANGES["verify-examples"])
        self.assertEqual((37, 48), EXPERIMENT_BLOCK_RANGES["rewrite-anti-distractor"])
        self.assertEqual((61, 72), EXPERIMENT_BLOCK_RANGES["rate-exactness-calibration"])
        self.assertEqual(
            (
                "verify-examples",
                "rewrite-anti-distractor",
                "rate-exactness-calibration",
                "definition-examples",
                "verify-bundles",
                "definition-rewrite-bundles",
                "definition-rate-bundles",
                "confirm-bundles",
            ),
            FOLLOWUP_PRIORITY,
        )
        self.assertEqual(
            {
                "verify-led": ("verify-examples", "verify-bundles"),
                "rewrite-led": ("rewrite-anti-distractor", "definition-rewrite-bundles"),
                "rate-led": ("rate-exactness-calibration", "definition-rate-bundles"),
            },
            DIRECTION_FOLLOWUP_PRESETS,
        )


if __name__ == "__main__":
    unittest.main()
