import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from generator.batch_publish import (
    Candidate,
    PreparedPuzzle,
    _merge_best_clue_variants,
    _needs_rewrite,
    run_batch,
)
from generator.core.clue_rating import append_rating_to_note
from generator.core.markdown_io import ClueEntry
from generator.core.quality import QualityReport


class BatchPublishTests(unittest.TestCase):
    def test_high_scores_do_not_force_rewrite_even_if_verify_failed(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="TUN",
            word_original="",
            definition="Recipient mare pentru vin",
            verified=False,
            verify_note=append_rating_to_note(
                "AI a ghicit: BARIL",
                semantic_score=9,
                guessability_score=7,
                feedback="definiție bună, dar există sinonim concurent",
            ),
        )

        self.assertFalse(_needs_rewrite(clue))

    def test_low_guessability_triggers_rewrite(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="TUN",
            word_original="",
            definition="Recipient mare pentru vin",
            verified=False,
            verify_note=append_rating_to_note(
                "AI a ghicit: BARIL",
                semantic_score=9,
                guessability_score=4,
                feedback="trimite spre un sinonim mai uzual",
            ),
        )

        self.assertTrue(_needs_rewrite(clue))

    def test_missing_scores_trigger_rewrite(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="TUN",
            word_original="",
            definition="Recipient mare pentru vin",
            verified=None,
            verify_note="AI a ghicit: BARIL",
        )

        self.assertTrue(_needs_rewrite(clue))

    def test_merge_best_clue_variants_keeps_higher_scored_definition(self):
        best = ClueEntry(
            row_number=1,
            word_normalized="AER",
            word_original="",
            definition="Gaz din atmosferă",
            verified=False,
            verify_note=append_rating_to_note(
                "AI a ghicit: AIR",
                semantic_score=8,
                guessability_score=4,
                feedback="duce spre sinonim străin",
            ),
        )
        current = ClueEntry(
            row_number=1,
            word_normalized="AER",
            word_original="",
            definition="Substanță gazoasă pe care o respirăm",
            verified=True,
            verify_note=append_rating_to_note(
                "",
                semantic_score=9,
                guessability_score=8,
                feedback="clar și natural",
            ),
        )

        merged = _merge_best_clue_variants([best], [current])

        self.assertEqual("Substanță gazoasă pe care o respirăm", merged[0].definition)

    @patch("generator.batch_publish.upload_puzzle")
    @patch("generator.batch_publish._prepare_puzzle_for_publication")
    @patch("generator.batch_publish._load_words")
    def test_run_batch_rejects_blocked_puzzle_before_upload(
        self,
        mock_load_words,
        mock_prepare,
        mock_upload,
    ):
        mock_load_words.return_value = []
        mock_prepare.return_value = PreparedPuzzle(
            title="Titlu de Test",
            candidate=Candidate(
                score=100.0,
                report=QualityReport(
                    score=100.0,
                    word_count=0,
                    average_length=0.0,
                    average_rarity=0.0,
                    two_letter_words=0,
                    three_letter_words=0,
                    high_rarity_words=0,
                    uncommon_letter_words=0,
                    friendly_words=0,
                ),
                template=[[True, True], [True, True]],
                markdown="# Rebus\n",
            ),
            puzzle=object(),
            passed=0,
            total=0,
            definition_score=0.0,
            blocking_words=["TAC", "ATASARE"],
        )

        with TemporaryDirectory() as tmp_dir:
            with self.assertRaises(RuntimeError):
                run_batch(
                    sizes=[7],
                    output_root=Path(tmp_dir),
                    words_path=Path(tmp_dir) / "words.json",
                    rewrite_rounds=2,
                    preparation_attempts=1,
                    run_dir=Path(tmp_dir) / "run",
                )

        mock_upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
