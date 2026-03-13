import unittest
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from generator.core.ai_clues import DefinitionRating, _contains_english_markers
from generator.core.markdown_io import ClueEntry
from generator.phases.verify import _verify_clues, rate_puzzle


class VerifyPhaseTests(unittest.TestCase):
    def test_english_marker_detection(self):
        self.assertTrue(_contains_english_markers("Precise and correct definition"))
        self.assertFalse(_contains_english_markers("Definiție scurtă și exactă"))

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

        mock_verify_definition.assert_called_once_with(client, "Metal prețios galben", 3)

    @patch("generator.phases.verify.rate_definition")
    def test_rate_puzzle_reports_two_averages(self, mock_rate_definition):
        mock_rate_definition.side_effect = [
            DefinitionRating(semantic_score=8, guessability_score=6, feedback="bună"),
            DefinitionRating(semantic_score=6, guessability_score=4, feedback="prea vagă"),
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
        self.assertIn("Scor ghicibilitate: 4/10", puzzle.vertical_clues[0].verify_note)

    @patch("generator.phases.verify.rate_definition")
    def test_rate_logging_includes_definition_text(self, mock_rate_definition):
        mock_rate_definition.return_value = DefinitionRating(
            semantic_score=9,
            guessability_score=9,
            feedback="clară",
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


if __name__ == "__main__":
    unittest.main()
