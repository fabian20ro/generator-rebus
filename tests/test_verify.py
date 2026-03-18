import unittest
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from generator.core.ai_clues import DefinitionRating, contains_english_markers
from generator.core.clue_rating import append_rating_to_note
from generator.core.markdown_io import ClueEntry
from generator.core.pipeline_state import working_clue_from_entry
from generator.phases.verify import _rate_clues, _verify_clues, rate_puzzle


class VerifyPhaseTests(unittest.TestCase):
    def test_english_marker_detection(self):
        self.assertTrue(contains_english_markers("Precise and correct definition"))
        self.assertFalse(contains_english_markers("Definiție scurtă și exactă"))

    @patch("generator.phases.verify.verify_definition")
    def test_verify_passes_answer_length_to_model(self, mock_verify_definition):
        mock_verify_definition.return_value = "AUR"
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
        )

    @patch("generator.phases.verify.verify_definition")
    def test_verify_passes_word_type_to_model(self, mock_verify_definition):
        mock_verify_definition.return_value = "LOVI"
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
        )

    @patch("generator.phases.verify.DexProvider.for_puzzle", return_value=None)
    @patch("generator.phases.verify.rate_definition")
    def test_rate_puzzle_reports_two_averages(self, mock_rate_definition, _mock_dex):
        mock_rate_definition.side_effect = [
            DefinitionRating(semantic_score=8, guessability_score=6, feedback="bună", creativity_score=7),
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
        self.assertIn("Scor semantic: 8/10", puzzle.horizontal_clues[0].verify_note)
        self.assertIn("Scor rebus:", puzzle.vertical_clues[0].verify_note)

    @patch("generator.phases.verify.DexProvider.for_puzzle", return_value=None)
    @patch("generator.phases.verify.rate_definition")
    def test_rate_logging_includes_definition_text(self, mock_rate_definition, _mock_dex):
        mock_rate_definition.return_value = DefinitionRating(
            semantic_score=9,
            guessability_score=9,
            feedback="clară",
            creativity_score=8,
        )
        puzzle = SimpleNamespace(
            horizontal_clues=[
                ClueEntry(1, "AUR", "", "Metal prețios galben", verify_note="", verified=True)
            ],
            vertical_clues=[],
        )

        with patch("sys.stdout", new=StringIO()) as captured:
            rate_puzzle(puzzle, client=object())

        self.assertIn("Metal prețios galben", captured.getvalue())


    @patch("generator.phases.verify.verify_definition")
    def test_verify_skips_words_in_skip_set(self, mock_verify_definition):
        mock_verify_definition.return_value = "NOR"
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


if __name__ == "__main__":
    unittest.main()
