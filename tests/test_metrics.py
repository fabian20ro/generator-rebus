import json
import tempfile
import unittest
from pathlib import Path

from generator.core.metrics import (
    BatchMetric,
    PuzzleMetric,
    WordMetric,
    load_word_difficulty,
    update_word_difficulty,
    write_metrics,
)


class MetricsTests(unittest.TestCase):
    def test_write_metrics_creates_json(self):
        batch = BatchMetric(
            timestamp="2026-03-14T01:00:00",
            seed=12345,
            models_used=["gpt-oss-20b"],
            puzzles=[PuzzleMetric(size=7, word_count=18, avg_semantic=8.5)],
            word_metrics=[WordMetric(word="CASA", length=4, final_verified=True, semantic_score=9)],
            total_elapsed_ms=60000,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "metrics.json"
            write_metrics(batch, path)
            self.assertTrue(path.exists())
            data = json.loads(path.read_text())
            self.assertEqual(data["seed"], 12345)
            self.assertEqual(len(data["puzzles"]), 1)
            self.assertEqual(data["puzzles"][0]["size"], 7)

    def test_word_difficulty_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "word_difficulty.json"

            metrics_round1 = [
                WordMetric(word="CASA", length=4, final_verified=True, semantic_score=9),
                WordMetric(word="SMACEALA", length=8, final_verified=False, semantic_score=3),
            ]
            update_word_difficulty(metrics_round1, path)

            data = load_word_difficulty(path)
            self.assertEqual(data["CASA"]["attempts"], 1)
            self.assertEqual(data["CASA"]["successes"], 1)
            self.assertEqual(data["SMACEALA"]["successes"], 0)

            metrics_round2 = [
                WordMetric(word="CASA", length=4, final_verified=True, semantic_score=10),
                WordMetric(word="SMACEALA", length=8, final_verified=False, semantic_score=2),
            ]
            update_word_difficulty(metrics_round2, path)

            data = load_word_difficulty(path)
            self.assertEqual(data["CASA"]["attempts"], 2)
            self.assertEqual(data["CASA"]["successes"], 2)
            self.assertEqual(data["CASA"]["avg_semantic"], 9.5)
            self.assertEqual(data["CASA"]["avg_rebus"], 0.0)
            self.assertEqual(data["CASA"]["semantic_spread"], 1)
            self.assertEqual(data["SMACEALA"]["attempts"], 2)
            self.assertEqual(data["SMACEALA"]["successes"], 0)

    def test_word_difficulty_tracks_failure_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "word_difficulty.json"

            metrics = [
                WordMetric(
                    word="TUN",
                    length=3,
                    initial_verified=False,
                    final_verified=False,
                    semantic_score=9,
                    guessability_score=6,
                    rebus_score=7,
                    semantic_delta=1,
                    rebus_delta=1,
                    rewrite_attempted=True,
                    rewrite_changed_definition=True,
                    rewrite_rescued_verify=False,
                    was_blocker=True,
                    wrong_guess="BARIL",
                    verify_candidates=["BARIL", "TUN"],
                    failure_kind="wrong_guess",
                    rarity_only_override=True,
                    form_mismatch=True,
                    model_generated="gpt-oss-20b",
                    model_rated="eurollm-22b",
                ),
                WordMetric(
                    word="TUN",
                    length=3,
                    initial_verified=False,
                    final_verified=False,
                    semantic_score=8,
                    guessability_score=5,
                    rebus_score=6,
                    semantic_delta=0,
                    rebus_delta=0,
                    rewrite_attempted=True,
                    rewrite_changed_definition=False,
                    rewrite_rescued_verify=False,
                    was_blocker=True,
                    wrong_guess="BARIL",
                    verify_candidates=["BARIL", "BUTOI"],
                    failure_kind="wrong_guess",
                    model_generated="gpt-oss-20b",
                    model_rated="eurollm-22b",
                ),
            ]
            update_word_difficulty(metrics, path)

            data = load_word_difficulty(path)
            self.assertEqual(data["TUN"]["blockers"], 2)
            self.assertEqual(data["TUN"]["rarity_override_count"], 1)
            self.assertEqual(data["TUN"]["form_mismatch_count"], 1)
            self.assertEqual(data["TUN"]["failure_kind_counts"]["wrong_guess"], 2)
            self.assertEqual(data["TUN"]["wrong_guess_counts"]["BARIL"], 2)
            self.assertEqual(data["TUN"]["verify_candidate_counts"]["BARIL"], 2)
            self.assertEqual(data["TUN"]["verify_candidate_counts"]["TUN"], 1)
            self.assertEqual(data["TUN"]["avg_guessability"], 5.5)
            self.assertEqual(data["TUN"]["avg_rebus"], 6.5)
            self.assertEqual(data["TUN"]["rewrite_attempts"], 2)
            self.assertEqual(data["TUN"]["rewrite_rescues"], 0)
            self.assertEqual(data["TUN"]["avg_semantic_delta"], 0.5)
            self.assertEqual(data["TUN"]["avg_rebus_delta"], 0.5)
            self.assertEqual(data["TUN"]["rebus_spread"], 1)
            self.assertEqual(data["TUN"]["generated_model_counts"]["gpt-oss-20b"], 2)
            self.assertEqual(data["TUN"]["rated_model_counts"]["eurollm-22b"], 2)

    def test_load_empty_difficulty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nonexistent.json"
            result = load_word_difficulty(path)
            self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
