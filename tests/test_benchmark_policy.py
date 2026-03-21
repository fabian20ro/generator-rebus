import unittest

from generator.assessment.benchmark_policy import (
    HISTORICAL_PROMPT_EVIDENCE,
    WORKING_DATASET_SIZE,
    WORKING_DATASET_TIER_COUNTS,
    WORKING_INCUMBENT,
)


class BenchmarkPolicyTests(unittest.TestCase):
    def test_working_incumbent_matches_curated_march_21_reset(self):
        self.assertEqual("baseline_results_20260321", WORKING_INCUMBENT.description)
        self.assertEqual(73.3, WORKING_INCUMBENT.composite)
        self.assertEqual(0.300, WORKING_INCUMBENT.pass_rate)
        self.assertEqual(9.1, WORKING_INCUMBENT.avg_semantic)
        self.assertEqual(8.1, WORKING_INCUMBENT.avg_rebus)
        self.assertEqual(
            {
                "low": 0.067,
                "medium": 0.240,
                "high": 0.867,
            },
            WORKING_INCUMBENT.tier_pass_rates,
        )

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


if __name__ == "__main__":
    unittest.main()
