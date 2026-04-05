"""Tests for generator.core.pipeline_state."""

import unittest
from generator.core.markdown_io import ClueEntry, PuzzleData
from generator.core.pipeline_state import (
    ClueAssessment,
    ClueCandidateVersion,
    ClueScores,
    WorkingClue,
    WorkingPuzzle,
    all_working_clues,
    puzzle_from_working_state,
    render_verify_note,
    set_current_definition,
    update_current_assessment,
    working_clue_from_entry,
    working_puzzle_from_puzzle,
    _split_compound_entry,
)


def _make_entry(**overrides) -> ClueEntry:
    defaults = dict(
        row_number=1,
        word_normalized="TEST",
        word_original="test",
        word_type="",
        definition="a test word",
        verified=True,
        verify_note="",
        start_row=0,
        start_col=0,
    )
    defaults.update(overrides)
    return ClueEntry(**defaults)


class TestWorkingClueFromEntry(unittest.TestCase):
    def test_basic_conversion(self):
        entry = _make_entry(definition="o probă")
        clue = working_clue_from_entry(entry)
        self.assertEqual(clue.word_normalized, "TEST")
        self.assertEqual(clue.word_original, "test")
        self.assertEqual(clue.current.definition, "o probă")
        self.assertEqual(clue.current.source, "import")
        self.assertEqual(clue.current.round_index, 0)
        self.assertFalse(clue.locked)
        self.assertIsNone(clue.best)

    def test_empty_definition_has_no_history(self):
        entry = _make_entry(definition="")
        clue = working_clue_from_entry(entry)
        self.assertEqual(clue.history, [])

    def test_non_empty_definition_has_history(self):
        entry = _make_entry(definition="something")
        clue = working_clue_from_entry(entry)
        self.assertEqual(len(clue.history), 1)
        self.assertIs(clue.history[0], clue.current)

    def test_preserves_position(self):
        entry = _make_entry(start_row=3, start_col=5)
        clue = working_clue_from_entry(entry)
        self.assertEqual(clue.start_row, 3)
        self.assertEqual(clue.start_col, 5)

    def test_preserves_word_type(self):
        entry = _make_entry(word_type="V")
        clue = working_clue_from_entry(entry)
        self.assertEqual("V", clue.word_type)


class TestActiveVersion(unittest.TestCase):
    def test_returns_current_when_no_best(self):
        clue = WorkingClue(row_number=1, word_normalized="A", word_original="a")
        self.assertIs(clue.active_version(), clue.current)

    def test_returns_best_when_set(self):
        clue = WorkingClue(row_number=1, word_normalized="A", word_original="a")
        best = ClueCandidateVersion("best def", 2, "rewrite")
        clue.best = best
        self.assertIs(clue.active_version(), best)


class TestSetCurrentDefinition(unittest.TestCase):
    def test_sets_new_current(self):
        clue = WorkingClue(row_number=1, word_normalized="CASA", word_original="casă")
        set_current_definition(clue, "o locuință", round_index=3, source="rewrite")
        self.assertEqual(clue.current.definition, "o locuință")
        self.assertEqual(clue.current.round_index, 3)
        self.assertEqual(clue.current.source, "rewrite")

    def test_appends_to_history(self):
        clue = WorkingClue(row_number=1, word_normalized="X", word_original="x")
        self.assertEqual(len(clue.history), 0)
        set_current_definition(clue, "def1", round_index=1, source="gen")
        set_current_definition(clue, "def2", round_index=2, source="rewrite")
        self.assertEqual(len(clue.history), 2)
        self.assertEqual(clue.history[0].definition, "def1")
        self.assertEqual(clue.history[1].definition, "def2")

    def test_new_current_has_empty_assessment(self):
        clue = WorkingClue(row_number=1, word_normalized="X", word_original="x")
        set_current_definition(clue, "test", round_index=1, source="gen")
        self.assertIsNone(clue.current.assessment.verified)


class TestUpdateCurrentAssessment(unittest.TestCase):
    def test_sets_verified(self):
        clue = WorkingClue(row_number=1, word_normalized="X", word_original="x")
        update_current_assessment(clue, verified=True)
        self.assertTrue(clue.current.assessment.verified)

    def test_sets_scores(self):
        clue = WorkingClue(row_number=1, word_normalized="X", word_original="x")
        scores = ClueScores(semantic_exactness=9, rebus_score=8)
        update_current_assessment(clue, scores=scores)
        self.assertEqual(clue.current.assessment.scores.semantic_exactness, 9)
        self.assertEqual(clue.current.assessment.scores.rebus_score, 8)

    def test_partial_update(self):
        clue = WorkingClue(row_number=1, word_normalized="X", word_original="x")
        update_current_assessment(clue, wrong_guess="CAPRA")
        self.assertEqual(clue.current.assessment.wrong_guess, "CAPRA")
        self.assertIsNone(clue.current.assessment.verified)  # untouched


class TestAllWorkingClues(unittest.TestCase):
    def test_combines_horizontal_and_vertical(self):
        h = [WorkingClue(1, "A", "a"), WorkingClue(2, "B", "b")]
        v = [WorkingClue(1, "C", "c")]
        puzzle = WorkingPuzzle("T", 5, [], h, v)
        result = all_working_clues(puzzle)
        self.assertEqual(len(result), 3)
        self.assertEqual([c.word_normalized for c in result], ["A", "B", "C"])


class TestWorkingPuzzleFromPuzzle(unittest.TestCase):
    def test_basic_conversion(self):
        puzzle = PuzzleData(
            title="Test",
            size=3,
            grid=[["A", "#", "B"], ["C", "D", "E"], ["#", "F", "G"]],
            horizontal_clues=[_make_entry(row_number=1, word_normalized="AB")],
            vertical_clues=[_make_entry(row_number=1, word_normalized="CD")],
        )
        wp = working_puzzle_from_puzzle(puzzle)
        self.assertEqual(wp.title, "Test")
        self.assertEqual(wp.size, 3)
        self.assertEqual(len(wp.horizontal_clues), 1)
        self.assertEqual(len(wp.vertical_clues), 1)
        self.assertEqual(wp.horizontal_clues[0].word_normalized, "AB")

    def test_grid_is_deep_copied(self):
        grid = [["A", "B"], ["C", "D"]]
        puzzle = PuzzleData(size=2, grid=grid)
        wp = working_puzzle_from_puzzle(puzzle)
        wp.grid[0][0] = "X"
        self.assertEqual(grid[0][0], "A")  # original unchanged


class TestPuzzleFromWorkingState(unittest.TestCase):
    def test_roundtrip(self):
        entry = _make_entry(definition="o definiție")
        puzzle = PuzzleData(
            title="Round",
            size=2,
            grid=[["A", "B"], ["C", "D"]],
            horizontal_clues=[entry],
            vertical_clues=[],
        )
        wp = working_puzzle_from_puzzle(puzzle)
        result = puzzle_from_working_state(wp)
        self.assertEqual(result.title, "Round")
        self.assertEqual(result.size, 2)
        self.assertEqual(len(result.horizontal_clues), 1)
        self.assertEqual(result.horizontal_clues[0].word_normalized, "TEST")

    def test_roundtrip_preserves_word_type(self):
        entry = _make_entry(definition="o definiție", word_type="N")
        puzzle = PuzzleData(
            title="Round",
            size=2,
            grid=[["A", "B"], ["C", "D"]],
            horizontal_clues=[entry],
            vertical_clues=[],
        )
        wp = working_puzzle_from_puzzle(puzzle)
        result = puzzle_from_working_state(wp)
        self.assertEqual("N", result.horizontal_clues[0].word_type)


class TestSplitCompoundEntry(unittest.TestCase):
    def test_single_word_returns_unchanged(self):
        entry = _make_entry(word_normalized="CASA")
        result = _split_compound_entry(entry)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].word_normalized, "CASA")

    def test_compound_splits(self):
        entry = _make_entry(word_normalized="CASA - MARE", word_original="casă - mare", word_type="A")
        result = _split_compound_entry(entry)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].word_normalized, "CASA")
        self.assertEqual(result[1].word_normalized, "MARE")
        self.assertEqual(result[0].word_original, "casă")
        self.assertEqual(result[1].word_original, "mare")
        self.assertEqual("A", result[0].word_type)
        self.assertEqual("A", result[1].word_type)

    def test_compound_with_split_flag(self):
        entry = _make_entry(word_normalized="A - B - C")
        puzzle = PuzzleData(horizontal_clues=[entry])
        wp = working_puzzle_from_puzzle(puzzle, split_compound=True)
        self.assertEqual(len(wp.horizontal_clues), 3)


class TestRenderVerifyNote(unittest.TestCase):
    def test_empty_assessment(self):
        note = render_verify_note(ClueAssessment())
        self.assertEqual(note, "")

    def test_with_wrong_guess_only(self):
        assessment = ClueAssessment(wrong_guess="CAPRA")
        note = render_verify_note(assessment)
        self.assertIn("CAPRA", note)

    def test_with_scores(self):
        assessment = ClueAssessment(
            scores=ClueScores(semantic_exactness=9, answer_targeting=8, rebus_score=7)
        )
        note = render_verify_note(assessment)
        # Should contain score info via append_rating_to_note
        self.assertTrue(len(note) > 0)

    def test_with_multiple_verify_candidates(self):
        assessment = ClueAssessment(verify_candidates=["PARI", "ARACI", "NULE"])
        note = render_verify_note(assessment)
        self.assertIn("AI a propus", note)
        self.assertIn("ARACI", note)

    def test_entry_roundtrip_preserves_verify_candidates(self):
        entry = _make_entry(
            verified=False,
            verify_note="AI a propus: PARI, ARACI, NULE | Scor semantic: 8/10 | Scor rebus: 6/10",
        )
        clue = working_clue_from_entry(entry)
        self.assertEqual(["PARI", "ARACI", "NULE"], clue.current.assessment.verify_candidates)
        self.assertEqual("PARI", clue.current.assessment.wrong_guess)


if __name__ == "__main__":
    unittest.main()
