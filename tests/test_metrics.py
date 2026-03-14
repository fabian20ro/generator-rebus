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
            self.assertEqual(data["SMACEALA"]["attempts"], 2)
            self.assertEqual(data["SMACEALA"]["successes"], 0)

    def test_load_empty_difficulty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nonexistent.json"
            result = load_word_difficulty(path)
            self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
