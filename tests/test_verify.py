import unittest
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from generator.core.ai_clues import DefinitionRating, contains_english_markers
from generator.core.clue_rating import append_rating_to_note
from generator.core.markdown_io import ClueEntry
from generator.core.model_manager import PRIMARY_MODEL, SECONDARY_MODEL
from generator.core.pipeline_state import ClueScores, working_clue_from_entry
from generator.core.puzzle_metrics import score_puzzle_state, puzzle_metadata_payload
from generator.phases.verify import (
    _finalize_pair_rating,
    _finalize_pair_verification,
    _rate_clues,
    _verify_clues,
    rate_puzzle,
    verify_working_puzzle,
)


class VerifyPhaseTests(unittest.TestCase):
    def test_english_marker_detection(self):
        self.assertTrue(contains_english_markers("Precise and correct definition"))
        self.assertFalse(contains_english_markers("Definiție scurtă și exactă"))

    @patch("generator.phases.verify.verify_definition_candidates")
    def test_verify_passes_answer_length_to_model(self, mock_verify_definition):
        mock_verify_definition.return_value = SimpleNamespace(candidates=["AUR"])
        client = object()
        clue = ClueEntry(
            row_number=1,
            word_normalized="AUR",
            word_original="",
            definition="Metal prețios galben",
        )

        _verify_clues([clue], client=client)

        mock_verify_definition.assert_called_once_with(
            client,
            "Metal prețios galben",
            3,
            word_type="",
            max_guesses=3,
        )

    @patch("generator.phases.verify.verify_definition_candidates")
    def test_verify_passes_word_type_to_model(self, mock_verify_definition):
        mock_verify_definition.return_value = SimpleNamespace(candidates=["LOVI"])
        client = object()
        clue = working_clue_from_entry(ClueEntry(
            row_number=1,
            word_normalized="LOVI",
            word_original="lovi",
            definition="A atinge cu forță",
        ))
        clue.word_type = "V"

        _verify_clues([clue], client=client)

        mock_verify_definition.assert_called_once_with(
            client,
            "A atinge cu forță",
            4,
            word_type="V",
            max_guesses=3,
        )

    @patch("generator.phases.verify.verify_definition_candidates")
    def test_verify_preserves_usage_suffix_in_definition_text(self, mock_verify_definition):
        mock_verify_definition.return_value = SimpleNamespace(candidates=["AZ"])
        client = object()
        clue = ClueEntry(
            row_number=1,
            word_normalized="AZ",
            word_original="az",
            definition="Pronume personal de persoana I singular (arh.)",
        )

        _verify_clues([clue], client=client)

        mock_verify_definition.assert_called_once_with(
            client,
            "Pronume personal de persoana I singular (arh.)",
            2,
            word_type="",
            max_guesses=3,
        )

    @patch("generator.phases.verify.verify_definition_candidates")
    def test_verify_marks_related_form_guess_explicitly(self, mock_verify_definition):
        mock_verify_definition.return_value = SimpleNamespace(candidates=["INCEPUT", "START"])
        clue = working_clue_from_entry(ClueEntry(
            row_number=1,
            word_normalized="NEINCEPUT",
            word_original="neînceput",
            definition="Care nu a început încă",
        ))

        result = _verify_clues([clue], client=object(), model_label=SECONDARY_MODEL.display_name)
        assessed = result[0].current.assessment

        self.assertFalse(assessed.verified)
        self.assertTrue(assessed.form_mismatch)
        self.assertEqual("related_form", assessed.failure_reason.kind)
        self.assertEqual(SECONDARY_MODEL.display_name, assessed.verified_by)

    @patch("generator.phases.verify.verify_definition_candidates")
    def test_verify_accepts_correct_word_among_multiple_candidates(self, mock_verify_definition):
        mock_verify_definition.return_value = SimpleNamespace(candidates=["PARI", "ARACI", "NULE"])
        clue = working_clue_from_entry(ClueEntry(
            row_number=1,
            word_normalized="ARACI",
            word_original="araci",
            definition="Bețe de sprijin pentru vie",
        ))

        result = _verify_clues([clue], client=object(), model_label=PRIMARY_MODEL.display_name)
        assessed = result[0].current.assessment

        self.assertTrue(assessed.verified)
        self.assertEqual(["PARI", "ARACI", "NULE"], assessed.verify_candidates)
        self.assertEqual("", assessed.wrong_guess)

    @patch(
        "generator.phases.verify.LmRuntime.activate_secondary",
        return_value=SimpleNamespace(display_name=SECONDARY_MODEL.display_name, model_id=SECONDARY_MODEL.model_id),
    )
    @patch(
        "generator.phases.verify.LmRuntime.activate_primary",
        return_value=SimpleNamespace(display_name=PRIMARY_MODEL.display_name, model_id=PRIMARY_MODEL.model_id),
    )
    @patch("generator.phases.verify.DexProvider.for_puzzle", return_value=None)
    @patch("generator.phases.verify.rate_definition")
    def test_rate_puzzle_reports_two_averages(self, mock_rate_definition, _mock_dex, _mock_primary, _mock_secondary):
        mock_rate_definition.side_effect = [
            DefinitionRating(semantic_score=8, guessability_score=6, feedback="bună", creativity_score=7),
            DefinitionRating(semantic_score=8, guessability_score=6, feedback="bună", creativity_score=7),
            DefinitionRating(semantic_score=6, guessability_score=4, feedback="prea vagă", creativity_score=5),
            DefinitionRating(semantic_score=6, guessability_score=4, feedback="prea vagă", creativity_score=5),
        ]
        puzzle = SimpleNamespace(
            horizontal_clues=[
                ClueEntry(1, "AUR", "", "Metal prețios galben", verify_note="", verified=True)
            ],
            vertical_clues=[
                ClueEntry(1, "NOR", "", "Masă albă pe cer", verify_note="", verified=False)
            ],
        )

        avg_semantic, avg_guessability, rated_count = rate_puzzle(puzzle, client=object())

        self.assertEqual(7.0, avg_semantic)
        self.assertEqual(5.0, avg_guessability)
        self.assertEqual(2, rated_count)
        self.assertIn("Scor semantic: 7/10", puzzle.horizontal_clues[0].verify_note)
        self.assertIn("Scor rebus:", puzzle.vertical_clues[0].verify_note)

    @patch(
        "generator.phases.verify.LmRuntime.activate_secondary",
        return_value=SimpleNamespace(display_name=SECONDARY_MODEL.display_name, model_id=SECONDARY_MODEL.model_id),
    )
    @patch(
        "generator.phases.verify.LmRuntime.activate_primary",
        return_value=SimpleNamespace(display_name=PRIMARY_MODEL.display_name, model_id=PRIMARY_MODEL.model_id),
    )
    @patch("generator.phases.verify.DexProvider.for_puzzle", return_value=None)
    @patch("generator.phases.verify.rate_definition")
    def test_rate_logging_includes_definition_text(self, mock_rate_definition, _mock_dex, _mock_primary, _mock_secondary):
        mock_rate_definition.side_effect = [
            DefinitionRating(
                semantic_score=9,
                guessability_score=9,
                feedback="clară",
                creativity_score=8,
            ),
            DefinitionRating(
                semantic_score=9,
                guessability_score=9,
                feedback="clară",
                creativity_score=8,
            ),
        ]
        puzzle = SimpleNamespace(
            horizontal_clues=[
                ClueEntry(1, "AUR", "", "Metal prețios galben", verify_note="", verified=True)
            ],
            vertical_clues=[],
        )

        with patch("sys.stdout", new=StringIO()) as captured:
            rate_puzzle(puzzle, client=object())

        self.assertIn("Metal prețios galben", captured.getvalue())


    @patch("generator.phases.verify.verify_definition_candidates")
    def test_verify_skips_words_in_skip_set(self, mock_verify_definition):
        mock_verify_definition.return_value = SimpleNamespace(candidates=["NOR"])
        clue_skipped = working_clue_from_entry(ClueEntry(
            row_number=1,
            word_normalized="AUR",
            word_original="",
            definition="Metal prețios galben",
            verified=True,
            verify_note=append_rating_to_note(
                "",
                semantic_score=9,
                guessability_score=8,
                feedback="bună",
            ),
        ))
        clue_evaluated = ClueEntry(
            row_number=2,
            word_normalized="NOR",
            word_original="",
            definition="Masă albă pe cer",
        )

        result = _verify_clues(
            [clue_skipped, clue_evaluated],
            client=object(),
            skip_words={"AUR"},
        )

        mock_verify_definition.assert_called_once()
        self.assertTrue(result[0].current.assessment.verified)
        self.assertTrue(result[1].current.assessment.verified)

    @patch("generator.phases.verify.rate_definition")
    def test_rate_skips_words_in_skip_set(self, mock_rate_definition):
        mock_rate_definition.return_value = DefinitionRating(
            semantic_score=7,
            guessability_score=6,
            feedback="medie",
            creativity_score=5,
        )
        clue_skipped = working_clue_from_entry(ClueEntry(
            row_number=1,
            word_normalized="AUR",
            word_original="",
            definition="Metal prețios galben",
            verified=True,
            verify_note=append_rating_to_note(
                "",
                semantic_score=9,
                guessability_score=8,
                feedback="bună",
            ),
        ))
        clue_evaluated = working_clue_from_entry(ClueEntry(
            row_number=2,
            word_normalized="NOR",
            word_original="",
            definition="Masă albă pe cer",
        ))

        _rate_clues(
            [clue_skipped, clue_evaluated],
            client=object(),
            skip_words={"AUR"},
        )

        mock_rate_definition.assert_called_once()
        self.assertEqual(9, clue_skipped.current.assessment.scores.semantic_exactness)
        self.assertEqual(8, clue_skipped.current.assessment.scores.answer_targeting)
        self.assertEqual(7, clue_evaluated.current.assessment.scores.semantic_exactness)
        self.assertEqual(6, clue_evaluated.current.assessment.scores.answer_targeting)

    @patch(
        "generator.phases.verify.LmRuntime.activate_secondary",
        return_value=SimpleNamespace(display_name=SECONDARY_MODEL.display_name, model_id=SECONDARY_MODEL.model_id),
    )
    @patch(
        "generator.phases.verify.LmRuntime.activate_primary",
        return_value=SimpleNamespace(display_name=PRIMARY_MODEL.display_name, model_id=PRIMARY_MODEL.model_id),
    )
    @patch("generator.phases.verify.verify_definition_candidates")
    def test_verify_working_puzzle_requires_both_models_to_match(self, mock_verify_definition, _mock_primary, _mock_secondary):
        mock_verify_definition.side_effect = [
            SimpleNamespace(candidates=["ARACI"], response_source="reasoning"),
            SimpleNamespace(candidates=["PARI"], response_source="reasoning"),
        ]
        puzzle = SimpleNamespace(
            horizontal_clues=[working_clue_from_entry(ClueEntry(1, "ARACI", "araci", "Bețe de sprijin pentru vie"))],
            vertical_clues=[],
        )

        passed, total = verify_working_puzzle(puzzle, client=object())

        self.assertEqual((0, 1), (passed, total))
        assessed = puzzle.horizontal_clues[0].current.assessment
        self.assertFalse(assessed.verified)
        self.assertTrue(assessed.verify_complete)
        self.assertEqual("wrong_guess", assessed.failure_reason.kind)  # type: ignore[union-attr]

    @patch(
        "generator.phases.verify.LmRuntime.activate_secondary",
        return_value=SimpleNamespace(display_name=SECONDARY_MODEL.display_name, model_id=SECONDARY_MODEL.model_id),
    )
    @patch(
        "generator.phases.verify.LmRuntime.activate_primary",
        return_value=SimpleNamespace(display_name=PRIMARY_MODEL.display_name, model_id=PRIMARY_MODEL.model_id),
    )
    @patch("generator.phases.verify.rate_definition")
    def test_rate_working_puzzle_marks_incomplete_pair_unrated(self, mock_rate_definition, _mock_primary, _mock_secondary):
        mock_rate_definition.side_effect = [
            DefinitionRating(semantic_score=8, guessability_score=6, feedback="ok", creativity_score=6),
            None,
        ]
        puzzle = SimpleNamespace(
            horizontal_clues=[ClueEntry(1, "ARACI", "araci", "Bețe de sprijin pentru vie")],
            vertical_clues=[],
        )

        avg_semantic, avg_guessability, rated = rate_puzzle(puzzle, client=object())

        self.assertEqual((0.0, 0.0, 0), (avg_semantic, avg_guessability, rated))
        self.assertEqual("", puzzle.horizontal_clues[0].verify_note)

    def test_puzzle_metrics_emit_null_payload_when_pair_incomplete(self):
        clue = working_clue_from_entry(ClueEntry(1, "ARACI", "araci", "Bețe de sprijin pentru vie"))
        clue.current.assessment.verify_complete = False
        clue.current.assessment.rating_complete = True
        clue.current.assessment.scores = ClueScores(
            semantic_exactness=8,
            answer_targeting=6,
            creativity=6,
            rebus_score=6,
        )
        puzzle = SimpleNamespace(horizontal_clues=[clue], vertical_clues=[])

        assessment = score_puzzle_state(puzzle)
        payload = puzzle_metadata_payload(assessment, description="x")

        self.assertFalse(assessment.scores_complete)
        self.assertIsNone(payload["rebus_score_min"])
        self.assertIsNone(payload["definition_score"])

    def test_finalize_pair_verification_handles_single_negative_vote_without_keyerror(self):
        clue = working_clue_from_entry(ClueEntry(1, "ARACI", "araci", "Bețe de sprijin pentru vie"))
        clue.current.assessment.verify_votes = {
            PRIMARY_MODEL.model_id: ["PARI"],
        }

        assessed = _finalize_pair_verification(
            [clue],
            model_order=[PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id],
            model_label="gemma + eurollm",
        )[0].current.assessment

        self.assertTrue(assessed.verify_complete)
        self.assertFalse(assessed.verified)
        self.assertEqual("PARI", assessed.wrong_guess)
        self.assertEqual("wrong_guess", assessed.failure_reason.kind)  # type: ignore[union-attr]

    def test_finalize_pair_rating_handles_missing_second_vote_without_keyerror(self):
        clue = working_clue_from_entry(ClueEntry(1, "ARACI", "araci", "Bețe de sprijin pentru vie"))
        clue.current.assessment.rating_votes = {
            PRIMARY_MODEL.model_id: DefinitionRating(
                semantic_score=8,
                guessability_score=6,
                feedback="ok",
                creativity_score=6,
            ),
        }

        _finalize_pair_rating(
            [clue],
            model_order=[PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id],
            model_label="gemma + eurollm",
        )

        assessed = clue.current.assessment
        self.assertFalse(assessed.rating_complete)
        self.assertIsNone(assessed.scores.semantic_exactness)
        self.assertEqual("unrated", assessed.failure_reason.kind)  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
