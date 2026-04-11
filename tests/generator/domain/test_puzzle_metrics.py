"""Tests for rebus_generator.domain.puzzle_metrics."""

import unittest
from rebus_generator.domain.pipeline_state import (
    ClueAssessment,
    ClueCandidateVersion,
    ClueScores,
    WorkingClue,
    WorkingPuzzle,
    working_clue_from_entry,
)
from rebus_generator.domain.puzzle_metrics import (
    score_puzzle_state,
    build_puzzle_description,
)
from rebus_generator.platform.io.markdown_io import ClueEntry
from rebus_generator.platform.llm.ai_clues import DefinitionRating


class TestPuzzleMetrics(unittest.TestCase):
    def test_score_puzzle_state_partial_votes(self):
        # A clue with no finalized scores but one rating vote
        clue = WorkingClue(row_number=1, word_normalized="TEST", word_original="test")
        clue.current.definition = "o probă"
        clue.current.assessment.rating_votes = {
            "model1": DefinitionRating(semantic_score=8, guessability_score=7, feedback="ok", creativity_score=1)
        }
        
        puzzle = WorkingPuzzle("T", 5, [], [clue], [])
        assessment = score_puzzle_state(puzzle)
        
        # Should show partial scores even if incomplete
        self.assertFalse(assessment.scores_complete)
        self.assertEqual(assessment.min_rebus, 6) # 0.75*7 + 0.25*1 = 5.25 + 0.25 = 5.5 -> 6
        self.assertEqual(assessment.avg_rebus, 6.0)

    def test_score_puzzle_state_mixed_complete_incomplete(self):
        # One complete clue, one with only votes
        c1 = WorkingClue(row_number=1, word_normalized="A", word_original="a")
        c1.current.definition = "def a"
        c1.current.assessment.verify_complete = True
        c1.current.assessment.rating_complete = True
        c1.current.assessment.scores = ClueScores(
            semantic_exactness=9, answer_targeting=8, creativity=7, rebus_score=8
        )
        
        c2 = WorkingClue(row_number=2, word_normalized="B", word_original="b")
        c2.current.definition = "def b"
        c2.current.assessment.rating_votes = {
            "m1": DefinitionRating(semantic_score=6, guessability_score=5, feedback="no", creativity_score=1)
        }
        # 0.75*5 + 0.25*1 = 3.75 + 0.25 = 4.0 -> 4
        
        puzzle = WorkingPuzzle("T", 5, [], [c1, c2], [])
        assessment = score_puzzle_state(puzzle)
        
        self.assertFalse(assessment.scores_complete)
        self.assertEqual(assessment.min_rebus, 4)
        self.assertEqual(assessment.avg_rebus, 6.0) # (8 + 4) / 2

    def test_build_puzzle_description_shows_partial(self):
        c = WorkingClue(row_number=1, word_normalized="TEST", word_original="test")
        c.current.definition = "def"
        c.current.assessment.rating_votes = {"m1": DefinitionRating(8, 8, "ok", 8)}
        
        puzzle = WorkingPuzzle("T", 5, [], [c], [])
        assessment = score_puzzle_state(puzzle)
        desc = build_puzzle_description(assessment, ["m1"])
        
        self.assertIn("Scor rebus: 8/10", desc)
        self.assertIn("Medie rebus: 8.0/10", desc)

    def test_backward_compatibility_creativity(self):
        # Old note without creativity
        entry = ClueEntry(
            row_number=1,
            word_normalized="TEST",
            word_original="test",
            definition="def",
            verified=True,
            verify_note="Scor semantic: 8/10 | Scor rebus: 7/10",
        )
        clue = working_clue_from_entry(entry)
        
        # Creativity should default to 1
        self.assertEqual(clue.current.assessment.scores.creativity, 1)
        
        # Now puzzle should be complete if all clues are like this
        puzzle = WorkingPuzzle("T", 5, [], [clue], [])
        assessment = score_puzzle_state(puzzle)
        self.assertTrue(assessment.scores_complete)
        self.assertEqual(assessment.min_rebus, 6) # 0.75*7 + 0.25*1 = 5.5 -> 6

if __name__ == "__main__":
    unittest.main()
