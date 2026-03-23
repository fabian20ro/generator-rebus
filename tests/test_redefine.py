import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from generator.redefine import (
    REDEFINE_ROUNDS,
    build_parser,
    build_working_puzzle,
    fetch_clues,
    fetch_puzzles,
    redefine_puzzle,
)


def _make_clue_row(
    word: str,
    definition: str,
    *,
    direction: str = "horizontal",
    clue_id: str = "clue-1",
) -> dict:
    return {
        "id": clue_id,
        "puzzle_id": "puzzle-1",
        "word_normalized": word,
        "word_original": word.lower(),
        "definition": definition,
        "direction": direction,
        "start_row": 0,
        "start_col": 0,
        "length": len(word),
    }


class FetchPuzzlesTests(unittest.TestCase):
    def test_fetch_puzzles_by_date(self):
        mock_supabase = MagicMock()
        mock_query = MagicMock()
        mock_supabase.table.return_value.select.return_value = mock_query
        mock_query.gte.return_value = mock_query
        mock_query.lte.return_value = mock_query
        mock_query.execute.return_value = SimpleNamespace(
            data=[{"id": "abc", "title": "Test Puzzle"}]
        )

        result = fetch_puzzles(mock_supabase, date="2026-03-15")

        self.assertEqual(1, len(result))
        self.assertEqual("abc", result[0]["id"])
        mock_query.gte.assert_called_once_with("created_at", "2026-03-15T00:00:00")
        mock_query.lte.assert_called_once_with("created_at", "2026-03-15T23:59:59")

    def test_fetch_puzzles_by_id(self):
        mock_supabase = MagicMock()
        mock_query = MagicMock()
        mock_supabase.table.return_value.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute.return_value = SimpleNamespace(
            data=[{"id": "specific-id", "title": "Puzzle"}]
        )

        result = fetch_puzzles(mock_supabase, puzzle_id="specific-id")

        self.assertEqual(1, len(result))
        mock_query.eq.assert_called_once_with("id", "specific-id")

    def test_fetch_puzzles_empty(self):
        mock_supabase = MagicMock()
        mock_query = MagicMock()
        mock_supabase.table.return_value.select.return_value = mock_query
        mock_query.execute.return_value = SimpleNamespace(data=None)

        result = fetch_puzzles(mock_supabase)

        self.assertEqual([], result)


class FetchCluesTests(unittest.TestCase):
    def test_fetch_clues_returns_list(self):
        mock_supabase = MagicMock()
        mock_query = MagicMock()
        mock_supabase.table.return_value.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute.return_value = SimpleNamespace(
            data=[
                _make_clue_row("MUNTE", "Formă de relief"),
                _make_clue_row("APA", "Lichid vital"),
            ]
        )

        result = fetch_clues(mock_supabase, "puzzle-1")

        self.assertEqual(2, len(result))
        mock_query.eq.assert_called_once_with("puzzle_id", "puzzle-1")

    def test_fetch_clues_selects_required_fields(self):
        mock_supabase = MagicMock()
        mock_query = MagicMock()
        mock_supabase.table.return_value.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute.return_value = SimpleNamespace(data=[])

        fetch_clues(mock_supabase, "puzzle-1")

        select_arg = mock_supabase.table.return_value.select.call_args[0][0]
        for field in ["id", "word_normalized", "word_original", "definition", "direction"]:
            self.assertIn(field, select_arg)


class BuildWorkingPuzzleTests(unittest.TestCase):
    def test_creates_puzzle_with_title_and_size(self):
        puzzle_row = {"id": "p1", "title": "Munți și Văi", "grid_size": 9}
        clue_rows = [_make_clue_row("MUNTE", "Formă de relief")]

        puzzle = build_working_puzzle(puzzle_row, clue_rows)

        self.assertEqual("Munți și Văi", puzzle.title)
        self.assertEqual(9, puzzle.size)
        self.assertEqual([], puzzle.grid)

    def test_routes_horizontal_and_vertical_clues(self):
        puzzle_row = {"id": "p1", "title": "", "grid_size": 7}
        clue_rows = [
            _make_clue_row("MUNTE", "Formă de relief", direction="horizontal"),
            _make_clue_row("APA", "Lichid vital", direction="vertical"),
            _make_clue_row("SOARE", "Astrul zilei", direction="horizontal"),
        ]

        puzzle = build_working_puzzle(puzzle_row, clue_rows)

        self.assertEqual(2, len(puzzle.horizontal_clues))
        self.assertEqual(1, len(puzzle.vertical_clues))
        self.assertEqual("APA", puzzle.vertical_clues[0].word_normalized)

    def test_routes_short_db_direction_codes(self):
        puzzle_row = {"id": "p1", "title": "", "grid_size": 7}
        clue_rows = [
            _make_clue_row("MUNTE", "Formă de relief", direction="H"),
            _make_clue_row("APA", "Lichid vital", direction="V"),
        ]

        puzzle = build_working_puzzle(puzzle_row, clue_rows)

        self.assertEqual(1, len(puzzle.horizontal_clues))
        self.assertEqual(1, len(puzzle.vertical_clues))
        self.assertEqual("APA", puzzle.vertical_clues[0].word_normalized)

    def test_sets_definition_on_current_version(self):
        puzzle_row = {"id": "p1", "title": "", "grid_size": 7}
        clue_rows = [_make_clue_row("MUNTE", "Formă de relief")]

        puzzle = build_working_puzzle(puzzle_row, clue_rows)

        clue = puzzle.horizontal_clues[0]
        self.assertEqual("Formă de relief", clue.current.definition)
        self.assertEqual("db_import", clue.current.source)
        self.assertEqual(0, clue.current.round_index)

    def test_handles_missing_optional_fields(self):
        puzzle_row = {"id": "p1"}
        clue_rows = [{"id": "c1", "word_normalized": "TEST"}]

        puzzle = build_working_puzzle(puzzle_row, clue_rows)

        self.assertEqual("", puzzle.title)
        self.assertEqual(0, puzzle.size)
        clue = puzzle.horizontal_clues[0]
        self.assertEqual("TEST", clue.word_normalized)
        self.assertEqual("", clue.word_original)
        self.assertEqual("", clue.current.definition)


class RedefinePuzzleTests(unittest.TestCase):
    def _make_supabase_mock(self, clue_rows: list[dict]) -> MagicMock:
        mock_supabase = MagicMock()
        clue_query = MagicMock()
        mock_supabase.table.return_value.select.return_value = clue_query
        clue_query.eq.return_value = clue_query
        clue_query.execute.return_value = SimpleNamespace(data=clue_rows)

        update_chain = MagicMock()
        mock_supabase.table.return_value.update.return_value = update_chain
        update_chain.eq.return_value = update_chain

        return mock_supabase

    @patch("generator.redefine.rewrite_puzzle_definitions")
    def test_dry_run_skips_db_update(self, mock_rewrite):
        clue_rows = [_make_clue_row("MUNTE", "Formă de relief", clue_id="c1")]
        mock_supabase = self._make_supabase_mock(clue_rows)

        from generator.core.pipeline_state import ClueCandidateVersion, ClueAssessment

        mock_rewrite.return_value = {
            "MUNTE": ClueCandidateVersion(
                definition="Înălțime naturală",
                round_index=1,
                source="rewrite",
                assessment=ClueAssessment(),
            )
        }

        puzzle_row = {"id": "p1", "title": "Test", "grid_size": 7}
        client = MagicMock()

        count = redefine_puzzle(
            mock_supabase, puzzle_row, client, dry_run=True, multi_model=False, rounds=1,
        )

        self.assertEqual(1, count)
        # update should NOT have been called for dry run
        mock_supabase.table.return_value.update.assert_not_called()

    @patch("generator.redefine.rewrite_puzzle_definitions")
    def test_updates_supabase_when_not_dry_run(self, mock_rewrite):
        clue_rows = [_make_clue_row("MUNTE", "Formă de relief", clue_id="c1")]
        mock_supabase = self._make_supabase_mock(clue_rows)

        from generator.core.pipeline_state import ClueCandidateVersion, ClueAssessment

        mock_rewrite.return_value = {
            "MUNTE": ClueCandidateVersion(
                definition="Înălțime naturală",
                round_index=1,
                source="rewrite",
                assessment=ClueAssessment(),
            )
        }

        puzzle_row = {"id": "p1", "title": "Test", "grid_size": 7}
        client = MagicMock()

        count = redefine_puzzle(
            mock_supabase, puzzle_row, client, dry_run=False, multi_model=False, rounds=1,
        )

        self.assertEqual(1, count)
        mock_supabase.table.return_value.update.assert_called_once()

    @patch("generator.redefine.rewrite_puzzle_definitions")
    def test_returns_zero_when_no_improvement(self, mock_rewrite):
        clue_rows = [_make_clue_row("MUNTE", "Formă de relief", clue_id="c1")]
        mock_supabase = self._make_supabase_mock(clue_rows)
        mock_rewrite.return_value = {}

        puzzle_row = {"id": "p1", "title": "Test", "grid_size": 7}
        client = MagicMock()

        count = redefine_puzzle(
            mock_supabase, puzzle_row, client, dry_run=False, multi_model=False, rounds=1,
        )

        self.assertEqual(0, count)

    def test_returns_zero_when_no_clues(self):
        mock_supabase = self._make_supabase_mock([])
        puzzle_row = {"id": "p1", "title": "Test", "grid_size": 7}
        client = MagicMock()

        count = redefine_puzzle(
            mock_supabase, puzzle_row, client, dry_run=False, multi_model=False, rounds=1,
        )

        self.assertEqual(0, count)


class ParserTests(unittest.TestCase):
    def test_parser_accepts_date(self):
        parser = build_parser()
        args = parser.parse_args(["--date", "2026-03-15"])
        self.assertEqual("2026-03-15", args.date)

    def test_parser_accepts_puzzle_id(self):
        parser = build_parser()
        args = parser.parse_args(["--puzzle-id", "abc-123"])
        self.assertEqual("abc-123", args.puzzle_id)

    def test_parser_accepts_all(self):
        parser = build_parser()
        args = parser.parse_args(["--all"])
        self.assertTrue(args.all)

    def test_parser_accepts_dry_run(self):
        parser = build_parser()
        args = parser.parse_args(["--all", "--dry-run"])
        self.assertTrue(args.dry_run)

    def test_parser_rounds_default(self):
        parser = build_parser()
        args = parser.parse_args(["--all"])
        self.assertEqual(REDEFINE_ROUNDS, args.rounds)

    def test_parser_custom_rounds(self):
        parser = build_parser()
        args = parser.parse_args(["--all", "--rounds", "3"])
        self.assertEqual(3, args.rounds)

    def test_parser_no_multi_model(self):
        parser = build_parser()
        args = parser.parse_args(["--all", "--no-multi-model"])
        self.assertFalse(args.multi_model)

    def test_parser_multi_model_default_true(self):
        parser = build_parser()
        args = parser.parse_args(["--all"])
        self.assertTrue(args.multi_model)


if __name__ == "__main__":
    unittest.main()
