import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from generator.batch_publish import (
    Candidate,
    PreparedPuzzle,
    SizeSettings,
    _better_prepared_puzzle,
    _generate_candidate,
    _merge_best_clue_variants,
    _needs_rewrite,
    _preparation_attempts_for_size,
    _prepare_puzzle_for_publication,
    _synthesize_failure_reason,
    _template_fingerprint,
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

    @patch("generator.batch_publish.choose_better_clue_variant")
    def test_merge_best_clue_variants_uses_llm_tiebreak_on_equal_scores(self, mock_tiebreak):
        mock_tiebreak.return_value = "B"
        best = ClueEntry(
            row_number=1,
            word_normalized="AER",
            word_original="",
            definition="Gaz din jurul nostru",
            verified=True,
            verify_note=append_rating_to_note(
                "",
                semantic_score=9,
                guessability_score=8,
                feedback="bună",
            ),
        )
        current = ClueEntry(
            row_number=1,
            word_normalized="AER",
            word_original="",
            definition="Amestec gazos din atmosferă",
            verified=True,
            verify_note=append_rating_to_note(
                "",
                semantic_score=9,
                guessability_score=8,
                feedback="bună",
            ),
        )

        with patch("sys.stdout", new=StringIO()) as captured:
            merged = _merge_best_clue_variants([best], [current], client=object())

        self.assertEqual("Amestec gazos din atmosferă", merged[0].definition)
        mock_tiebreak.assert_called_once()
        log_line = captured.getvalue()
        self.assertIn("A='Gaz din jurul nostru'", log_line)
        self.assertIn("B='Amestec gazos din atmosferă'", log_line)
        self.assertIn("aleasă='Amestec gazos din atmosferă'", log_line)

    def test_nine_nine_clue_is_locked(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="AER",
            word_original="",
            definition="Gaz din atmosferă",
            verified=False,
            verify_note=append_rating_to_note(
                "AI a ghicit: VANT",
                semantic_score=9,
                guessability_score=9,
                feedback="clară și exactă",
            ),
        )

        self.assertFalse(_needs_rewrite(clue))

    def test_large_sizes_get_more_preparation_attempts(self):
        self.assertEqual(5, _preparation_attempts_for_size(7, 5))
        self.assertEqual(50, _preparation_attempts_for_size(10, 5))
        self.assertEqual(50, _preparation_attempts_for_size(12, 5))

    @patch("generator.batch_publish.choose_better_puzzle_variant")
    def test_prepared_puzzle_tiebreak_uses_llm_for_near_equal_scores(self, mock_tiebreak):
        mock_tiebreak.return_value = "B"
        best = _prepared_puzzle(title="A", definition_score=8.0, blocking_words=[])
        candidate = _prepared_puzzle(title="B", definition_score=8.2, blocking_words=[])

        with patch("sys.stdout", new=StringIO()) as captured:
            winner = _better_prepared_puzzle(best, candidate, client=object())

        self.assertEqual("B", winner.title)
        mock_tiebreak.assert_called_once()
        self.assertIn("Puzzle tie-break:", captured.getvalue())
        self.assertIn("câștigă B", captured.getvalue())

    @patch("generator.batch_publish.score_words")
    @patch("generator.batch_publish.solve")
    @patch("generator.batch_publish._slot_capacity_ok")
    @patch("generator.batch_publish.extract_slots")
    @patch("generator.batch_publish.generate_procedural_template")
    @patch("generator.batch_publish.parse_template")
    def test_generate_candidate_for_seven_uses_procedural_templates_only(
        self,
        mock_parse_template,
        mock_generate_template,
        mock_extract_slots,
        mock_slot_ok,
        mock_solve,
        mock_score_words,
    ):
        template = [
            [True, True, True, False, True, True, True],
            [True, True, True, True, True, True, True],
            [True, True, True, False, True, True, True],
            [False, True, True, True, True, True, False],
            [True, True, True, False, True, True, True],
            [True, True, True, True, True, True, True],
            [True, True, True, False, True, True, True],
        ]
        settings = SizeSettings(3, 50000, 6, 1, 1, 4, 16)
        slot = type("Slot", (), {
            "id": 1,
            "direction": "H",
            "length": 3,
            "start_row": 0,
            "start_col": 0,
            "cells": [(0, 0), (0, 1), (0, 2)],
        })()
        word = type("WordEntry", (), {"normalized": "AER", "original": "aer"})()

        mock_generate_template.return_value = template
        mock_extract_slots.return_value = [slot]
        mock_slot_ok.return_value = True
        mock_solve.return_value = {1: word}
        mock_score_words.return_value = QualityReport(
            score=10.0,
            word_count=1,
            average_length=3.0,
            average_rarity=1.0,
            two_letter_words=0,
            three_letter_words=1,
            high_rarity_words=0,
            uncommon_letter_words=0,
            friendly_words=1,
        )

        candidate = _generate_candidate(
            7,
            settings,
            word_index=object(),
            metadata={"AER": {"rarity_level": 1}},
            title="Test",
            seen_template_fingerprints=set(),
        )

        self.assertIsNotNone(candidate)
        mock_parse_template.assert_not_called()

    @patch("generator.batch_publish.score_words")
    @patch("generator.batch_publish.solve")
    @patch("generator.batch_publish._slot_capacity_ok")
    @patch("generator.batch_publish.extract_slots")
    @patch("generator.batch_publish.generate_procedural_template")
    def test_duplicate_seven_template_fingerprint_is_rejected(
        self,
        mock_generate_template,
        mock_extract_slots,
        mock_slot_ok,
        mock_solve,
        mock_score_words,
    ):
        template = [
            [True, True, True],
            [True, False, True],
            [True, True, True],
        ]
        settings = SizeSettings(3, 50000, 1, 1, 1, 4, 16)
        slot = type("Slot", (), {
            "id": 1,
            "direction": "H",
            "length": 3,
            "start_row": 0,
            "start_col": 0,
            "cells": [(0, 0), (0, 1), (0, 2)],
        })()
        word = type("WordEntry", (), {"normalized": "AER", "original": "aer"})()

        mock_generate_template.return_value = template
        mock_extract_slots.return_value = [slot]
        mock_slot_ok.return_value = True
        mock_solve.return_value = {1: word}
        mock_score_words.return_value = QualityReport(
            score=10.0,
            word_count=1,
            average_length=3.0,
            average_rarity=1.0,
            two_letter_words=0,
            three_letter_words=1,
            high_rarity_words=0,
            uncommon_letter_words=0,
            friendly_words=1,
        )

        seen = {_template_fingerprint(template)}
        candidate = _generate_candidate(
            7,
            settings,
            word_index=object(),
            metadata={"AER": {"rarity_level": 1}},
            title="Test",
            seen_template_fingerprints=seen,
        )

        self.assertIsNone(candidate)

    @patch("generator.batch_publish.generate_title_for_final_puzzle")
    @patch("generator.batch_publish._rewrite_failed_clues")
    @patch("generator.batch_publish.generate_definitions_for_puzzle")
    @patch("generator.batch_publish.parse_markdown")
    @patch("generator.batch_publish._best_candidate")
    def test_final_title_is_generated_after_definitions_stabilize(
        self,
        mock_best_candidate,
        mock_parse_markdown,
        mock_generate_definitions,
        mock_rewrite_failed,
        mock_final_title,
    ):
        puzzle = type("Puzzle", (), {
            "title": "",
            "horizontal_clues": [ClueEntry(1, "AER", "", "", verified=None, verify_note="")],
            "vertical_clues": [],
        })()
        mock_best_candidate.return_value = Candidate(
            score=12.0,
            report=QualityReport(
                score=12.0,
                word_count=1,
                average_length=3.0,
                average_rarity=1.0,
                two_letter_words=0,
                three_letter_words=1,
                high_rarity_words=0,
                uncommon_letter_words=0,
                friendly_words=1,
            ),
            template=[[True, True, True]],
            markdown="# Rebus\n",
        )
        mock_parse_markdown.return_value = puzzle

        def _fill_defs(puzzle_obj, client):
            puzzle_obj.horizontal_clues[0].definition = "Gaz din atmosferă"

        def _rewrite(puzzle_obj, client, rounds):
            puzzle_obj.horizontal_clues[0].current.definition = "Substanță gazoasă din atmosferă"
            return (1, 1)

        def _title_from_final(puzzle_obj, client=None):
            return puzzle_obj.horizontal_clues[0].definition

        mock_generate_definitions.side_effect = _fill_defs
        mock_rewrite_failed.side_effect = _rewrite
        mock_final_title.side_effect = _title_from_final

        prepared = _prepare_puzzle_for_publication(
            index=1,
            total_puzzles=1,
            size=7,
            raw_words=[],
            client=object(),
            rewrite_rounds=1,
            preparation_attempts=1,
            seen_template_fingerprints=set(),
        )

        self.assertEqual("Substanță gazoasă din atmosferă", prepared.title)
        self.assertEqual("Substanță gazoasă din atmosferă", prepared.puzzle.title)

    def test_failure_reason_prefers_wrong_guess(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="ARACI",
            word_original="",
            definition="Prezintă un fapt în mod clar și convingător.",
            verified=False,
            verify_note=append_rating_to_note(
                "AI a ghicit: EXPLICA",
                semantic_score=8,
                guessability_score=4,
                feedback="Duce la alt răspuns mai comun.",
            ),
        )

        reason = _synthesize_failure_reason(clue)

        self.assertEqual("Duce la alt răspuns: EXPLICA.", reason)

    def test_failure_reason_ignores_rarity_as_primary_defect(self):
        clue = ClueEntry(
            row_number=1,
            word_normalized="ARACI",
            word_original="",
            definition="Bețe de sprijin pentru viță",
            verified=False,
            verify_note=append_rating_to_note(
                "",
                semantic_score=9,
                guessability_score=5,
                feedback="Răspunsul este rar și mai puțin comun.",
            ),
        )

        reason = _synthesize_failure_reason(clue)

        self.assertIn("exactă", reason)

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


def _prepared_puzzle(title: str, definition_score: float, blocking_words: list[str]) -> PreparedPuzzle:
    clue = ClueEntry(
        row_number=1,
        word_normalized="AER",
        word_original="",
        definition="Gaz din atmosferă",
        verified=True,
        verify_note=append_rating_to_note(
            "",
            semantic_score=9,
            guessability_score=8,
            feedback="clară",
        ),
    )
    puzzle = type("Puzzle", (), {
        "title": title,
        "horizontal_clues": [clue],
        "vertical_clues": [],
    })()
    return PreparedPuzzle(
        title=title,
        candidate=Candidate(
            score=definition_score,
            report=QualityReport(
                score=definition_score,
                word_count=1,
                average_length=3.0,
                average_rarity=1.0,
                two_letter_words=0,
                three_letter_words=1,
                high_rarity_words=0,
                uncommon_letter_words=0,
                friendly_words=1,
            ),
            template=[[True, True, True]],
            markdown="# Rebus\n",
        ),
        puzzle=puzzle,
        passed=1,
        total=1,
        definition_score=definition_score,
        blocking_words=blocking_words,
    )


if __name__ == "__main__":
    unittest.main()
