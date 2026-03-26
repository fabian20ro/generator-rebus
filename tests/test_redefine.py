import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from generator.core.pipeline_state import ClueAssessment, ClueScores, PuzzleAssessment
from generator.core.puzzle_metrics import PuzzleEvaluationResult
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
    clue_number: int = 1,
    verify_note: str = "",
    verified: bool = False,
    row: int = 0,
    col: int = 0,
) -> dict:
    return {
        "id": clue_id,
        "puzzle_id": "puzzle-1",
        "word_normalized": word,
        "word_original": word.lower(),
        "definition": definition,
        "direction": direction,
        "start_row": row,
        "start_col": col,
        "length": len(word),
        "clue_number": clue_number,
        "verify_note": verify_note,
        "verified": verified,
    }


def _assessment(
    min_rebus: int,
    *,
    avg_rebus: float | None = None,
    verified_count: int = 1,
    total: int = 1,
) -> PuzzleAssessment:
    return PuzzleAssessment(
        definition_score=15.0,
        avg_exactness=8.0,
        avg_targeting=8.0,
        avg_creativity=6.0,
        avg_rebus=float(min_rebus if avg_rebus is None else avg_rebus),
        min_rebus=min_rebus,
        verified_count=verified_count,
        total_clues=total,
        pass_rate=(verified_count / total) if total else 0.0,
        blocker_words=[],
    )


class _SupabaseFixture:
    def __init__(self, clue_rows: list[dict]):
        self.supabase = MagicMock()

        self.puzzle_select = MagicMock()
        self.puzzle_select.eq.return_value = self.puzzle_select
        self.puzzle_select.execute.return_value = SimpleNamespace(data=[])
        self.puzzle_update = MagicMock()
        self.puzzle_update.eq.return_value = self.puzzle_update
        self.puzzle_update.execute.return_value = SimpleNamespace(data=[])
        self.puzzle_table = MagicMock()
        self.puzzle_table.select.return_value = self.puzzle_select
        self.puzzle_table.update.return_value = self.puzzle_update

        self.clue_select = MagicMock()
        self.clue_select.eq.return_value = self.clue_select
        self.clue_select.execute.return_value = SimpleNamespace(data=clue_rows)
        self.clue_update = MagicMock()
        self.clue_update.eq.return_value = self.clue_update
        self.clue_update.execute.return_value = SimpleNamespace(data=[])
        self.clue_table = MagicMock()
        self.clue_table.select.return_value = self.clue_select
        self.clue_table.update.return_value = self.clue_update

        def _table(name: str):
            if name == "crossword_puzzles":
                return self.puzzle_table
            if name == "crossword_clues":
                return self.clue_table
            raise AssertionError(name)

        self.supabase.table.side_effect = _table


class FetchPuzzlesTests(unittest.TestCase):
    def test_fetch_puzzles_prioritizes_never_repaired_before_recently_repaired(self):
        mock_supabase = MagicMock()
        mock_query = MagicMock()
        mock_supabase.table.return_value.select.return_value = mock_query
        mock_query.execute.return_value = SimpleNamespace(
            data=[
                {
                    "id": "recently-repaired",
                    "created_at": "2026-03-01T03:00:00+00:00",
                    "repaired_at": "2026-03-26T10:00:00+00:00",
                    "description": "ok",
                    "rebus_score_min": 7,
                    "rebus_score_avg": 7.0,
                    "definition_score": 10.0,
                    "verified_count": 10,
                    "total_clues": 10,
                    "pass_rate": 1.0,
                },
                {
                    "id": "never-repaired",
                    "created_at": "2026-03-20T03:00:00+00:00",
                    "repaired_at": None,
                    "description": "",
                    "rebus_score_min": None,
                    "rebus_score_avg": None,
                    "definition_score": None,
                    "verified_count": None,
                    "total_clues": None,
                    "pass_rate": None,
                },
            ]
        )

        result = fetch_puzzles(mock_supabase)

        self.assertEqual(["never-repaired", "recently-repaired"], [row["id"] for row in result])

    def test_fetch_puzzles_sorts_oldest_first(self):
        mock_supabase = MagicMock()
        mock_query = MagicMock()
        mock_supabase.table.return_value.select.return_value = mock_query
        mock_query.execute.return_value = SimpleNamespace(
            data=[
                {"id": "c", "created_at": "2026-03-15T01:00:00+00:00"},
                {"id": "a", "created_at": "2026-03-14T03:00:00+00:00"},
                {"id": "b", "created_at": "2026-03-14T03:00:00+00:00"},
            ]
        )

        result = fetch_puzzles(mock_supabase)

        self.assertEqual(["a", "b", "c"], [row["id"] for row in result])

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
        for field in [
            "id",
            "word_normalized",
            "word_original",
            "definition",
            "direction",
            "clue_number",
            "verify_note",
            "verified",
        ]:
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

    def test_imports_existing_verify_state(self):
        puzzle_row = {"id": "p1", "title": "", "grid_size": 7}
        clue_rows = [
            _make_clue_row(
                "MUNTE",
                "Formă de relief",
                verify_note="AI a ghicit: MUNTE | Scor semantic: 9/10 | Scor rebus: 7/10 | Scor creativitate: 6/10",
                verified=True,
            )
        ]

        puzzle = build_working_puzzle(puzzle_row, clue_rows)

        clue = puzzle.horizontal_clues[0]
        self.assertTrue(clue.current.assessment.verified)
        self.assertEqual(["MUNTE"], clue.current.assessment.verify_candidates)
        self.assertEqual(9, clue.current.assessment.scores.semantic_exactness)
        self.assertEqual(7, clue.current.assessment.scores.rebus_score)

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
    def _baseline_eval(self, puzzle, clue_specs: list[dict]) -> PuzzleEvaluationResult:
        for clue, spec in zip(puzzle.horizontal_clues + puzzle.vertical_clues, clue_specs):
            clue.current.assessment = ClueAssessment(
                verified=spec["verified"],
                verify_candidates=spec.get("verify_candidates", []),
                feedback=spec.get("feedback", ""),
                scores=ClueScores(
                    semantic_exactness=spec.get("semantic", 0),
                    answer_targeting=spec.get("targeting", 0),
                    creativity=spec.get("creativity", 0),
                    rebus_score=spec.get("rebus", 0),
                ),
            )
        assessment = _assessment(
            min(spec.get("rebus", 0) for spec in clue_specs),
            avg_rebus=sum(spec.get("rebus", 0) for spec in clue_specs) / len(clue_specs),
            verified_count=sum(1 for spec in clue_specs if spec["verified"]),
            total=len(clue_specs),
        )
        return PuzzleEvaluationResult(
            assessment=assessment,
            passed=assessment.verified_count,
            total=assessment.total_clues,
            evaluator_model="gpt-oss-20b",
        )

    @patch("generator.redefine.rewrite_puzzle_definitions")
    @patch("generator.redefine.evaluate_puzzle_state")
    def test_dry_run_skips_db_update(self, mock_evaluate, mock_rewrite):
        clue_rows = [_make_clue_row("MUNTE", "Formă de relief", clue_id="c1")]
        fixture = _SupabaseFixture(clue_rows)
        mock_evaluate.side_effect = lambda puzzle, *_args, **_kwargs: self._baseline_eval(
            puzzle,
            [
                {"verified": False, "semantic": 5, "targeting": 5, "creativity": 5, "rebus": 5},
            ],
        )

        def _rewrite(puzzle, *_args, **_kwargs):
            clue = puzzle.horizontal_clues[0]
            clue.current.definition = "Înălțime naturală"
            clue.current.assessment = ClueAssessment(
                verified=True,
                verify_candidates=["MUNTE"],
                feedback="clar",
                scores=ClueScores(
                    semantic_exactness=9,
                    answer_targeting=8,
                    creativity=6,
                    rebus_score=7,
                ),
            )
            clue.best = clue.current
            return SimpleNamespace(initial_passed=0, final_passed=1, total=1)

        mock_rewrite.side_effect = _rewrite
        puzzle_row = {"id": "p1", "title": "Test", "grid_size": 7}

        count = redefine_puzzle(
            fixture.supabase,
            puzzle_row,
            MagicMock(),
            dry_run=True,
            multi_model=False,
            rounds=1,
        )

        self.assertEqual(1, count)
        fixture.clue_table.update.assert_not_called()
        fixture.puzzle_table.update.assert_not_called()

    @patch("generator.redefine.rewrite_puzzle_definitions")
    @patch("generator.redefine.evaluate_puzzle_state")
    def test_updates_clue_state_and_metadata_when_not_dry_run(self, mock_evaluate, mock_rewrite):
        clue_rows = [_make_clue_row("MUNTE", "Formă de relief", clue_id="c1")]
        fixture = _SupabaseFixture(clue_rows)
        mock_evaluate.side_effect = lambda puzzle, *_args, **_kwargs: self._baseline_eval(
            puzzle,
            [
                {"verified": False, "semantic": 5, "targeting": 5, "creativity": 5, "rebus": 5},
            ],
        )

        def _rewrite(puzzle, *_args, **_kwargs):
            clue = puzzle.horizontal_clues[0]
            clue.current.definition = "Înălțime naturală"
            clue.current.assessment = ClueAssessment(
                verified=True,
                verify_candidates=["MUNTE"],
                feedback="clar",
                scores=ClueScores(
                    semantic_exactness=9,
                    answer_targeting=8,
                    creativity=6,
                    rebus_score=7,
                ),
            )
            clue.best = clue.current
            return SimpleNamespace(initial_passed=0, final_passed=1, total=1)

        mock_rewrite.side_effect = _rewrite
        puzzle_row = {
            "id": "p1",
            "title": "Test",
            "grid_size": 7,
            "description": "vechi",
            "rebus_score_min": 5,
            "rebus_score_avg": 5.0,
            "definition_score": 10.0,
            "verified_count": 0,
            "total_clues": 1,
            "pass_rate": 0.0,
        }

        count = redefine_puzzle(
            fixture.supabase,
            puzzle_row,
            MagicMock(),
            dry_run=False,
            multi_model=False,
            rounds=1,
        )

        self.assertEqual(1, count)
        self.assertEqual(1, fixture.clue_table.update.call_count)
        self.assertEqual(1, fixture.puzzle_table.update.call_count)
        clue_payload = fixture.clue_table.update.call_args[0][0]
        self.assertEqual("Înălțime naturală", clue_payload["definition"])
        self.assertTrue(clue_payload["verified"])
        self.assertIn("Scor semantic: 9/10", clue_payload["verify_note"])
        puzzle_payload = fixture.puzzle_table.update.call_args[0][0]
        self.assertEqual(7, puzzle_payload["rebus_score_min"])
        self.assertEqual(1, puzzle_payload["verified_count"])
        self.assertIn("updated_at", puzzle_payload)
        self.assertIn("repaired_at", puzzle_payload)
        self.assertNotIn("title", puzzle_payload)

    @patch("generator.redefine.rewrite_puzzle_definitions")
    @patch("generator.redefine.evaluate_puzzle_state")
    def test_updates_metadata_after_each_changed_clue(self, mock_evaluate, mock_rewrite):
        clue_rows = [
            _make_clue_row("MUNTE", "Formă de relief", clue_id="c1", clue_number=1, row=0, col=0),
            _make_clue_row("APA", "Lichid vital", clue_id="c2", clue_number=2, row=0, col=3),
        ]
        fixture = _SupabaseFixture(clue_rows)
        mock_evaluate.side_effect = lambda puzzle, *_args, **_kwargs: self._baseline_eval(
            puzzle,
            [
                {"verified": False, "semantic": 5, "targeting": 5, "creativity": 5, "rebus": 5},
                {"verified": False, "semantic": 4, "targeting": 4, "creativity": 5, "rebus": 4},
            ],
        )

        def _rewrite(puzzle, *_args, **_kwargs):
            first, second = puzzle.horizontal_clues
            first.current.definition = "Înălțime naturală"
            first.current.assessment = ClueAssessment(
                verified=True,
                verify_candidates=["MUNTE"],
                feedback="clar",
                scores=ClueScores(
                    semantic_exactness=9,
                    answer_targeting=8,
                    creativity=6,
                    rebus_score=7,
                ),
            )
            second.current.definition = "Izvor de viață"
            second.current.assessment = ClueAssessment(
                verified=True,
                verify_candidates=["APA"],
                feedback="clar",
                scores=ClueScores(
                    semantic_exactness=8,
                    answer_targeting=8,
                    creativity=6,
                    rebus_score=8,
                ),
            )
            first.best = first.current
            second.best = second.current
            return SimpleNamespace(initial_passed=0, final_passed=2, total=2)

        mock_rewrite.side_effect = _rewrite
        puzzle_row = {
            "id": "p1",
            "title": "Test",
            "grid_size": 7,
            "description": "vechi",
            "rebus_score_min": 4,
            "rebus_score_avg": 4.5,
            "definition_score": 9.0,
            "verified_count": 0,
            "total_clues": 2,
            "pass_rate": 0.0,
        }

        count = redefine_puzzle(
            fixture.supabase,
            puzzle_row,
            MagicMock(),
            dry_run=False,
            multi_model=False,
            rounds=1,
        )

        self.assertEqual(2, count)
        self.assertEqual(2, fixture.clue_table.update.call_count)
        self.assertEqual(2, fixture.puzzle_table.update.call_count)
        final_payload = fixture.puzzle_table.update.call_args_list[-1][0][0]
        self.assertEqual(7, final_payload["rebus_score_min"])
        self.assertEqual(2, final_payload["verified_count"])

    @patch("generator.redefine.rewrite_puzzle_definitions")
    @patch("generator.redefine.evaluate_puzzle_state")
    def test_backfills_metadata_when_no_clue_changes(self, mock_evaluate, mock_rewrite):
        clue_rows = [_make_clue_row("MUNTE", "Formă de relief", clue_id="c1")]
        fixture = _SupabaseFixture(clue_rows)
        mock_evaluate.side_effect = lambda puzzle, *_args, **_kwargs: self._baseline_eval(
            puzzle,
            [
                {"verified": True, "verify_candidates": ["MUNTE"], "semantic": 8, "targeting": 8, "creativity": 6, "rebus": 6},
            ],
        )
        mock_rewrite.side_effect = lambda puzzle, *_args, **_kwargs: SimpleNamespace(
            initial_passed=1,
            final_passed=1,
            total=1,
        )
        puzzle_row = {
            "id": "p1",
            "title": "Test",
            "grid_size": 7,
            "description": None,
            "rebus_score_min": None,
            "rebus_score_avg": None,
            "definition_score": None,
            "verified_count": None,
            "total_clues": None,
            "pass_rate": None,
        }

        count = redefine_puzzle(
            fixture.supabase,
            puzzle_row,
            MagicMock(),
            dry_run=False,
            multi_model=False,
            rounds=1,
        )

        self.assertEqual(0, count)
        self.assertEqual(0, fixture.clue_table.update.call_count)
        self.assertEqual(1, fixture.puzzle_table.update.call_count)
        payload = fixture.puzzle_table.update.call_args[0][0]
        self.assertEqual(6, payload["rebus_score_min"])
        self.assertIn("updated_at", payload)
        self.assertIn("repaired_at", payload)

    @patch("generator.redefine.rewrite_puzzle_definitions")
    @patch("generator.redefine.evaluate_puzzle_state")
    def test_noop_when_no_clue_changes_and_metadata_present(self, mock_evaluate, mock_rewrite):
        clue_rows = [_make_clue_row("MUNTE", "Formă de relief", clue_id="c1")]
        fixture = _SupabaseFixture(clue_rows)
        mock_evaluate.side_effect = lambda puzzle, *_args, **_kwargs: self._baseline_eval(
            puzzle,
            [
                {"verified": False, "semantic": 5, "targeting": 5, "creativity": 5, "rebus": 5},
            ],
        )
        mock_rewrite.side_effect = lambda puzzle, *_args, **_kwargs: SimpleNamespace(
            initial_passed=0,
            final_passed=0,
            total=1,
        )
        puzzle_row = {
            "id": "p1",
            "title": "Test",
            "grid_size": 7,
            "description": "ok",
            "rebus_score_min": 5,
            "rebus_score_avg": 5.0,
            "definition_score": 10.0,
            "verified_count": 0,
            "total_clues": 1,
            "pass_rate": 0.0,
        }

        count = redefine_puzzle(
            fixture.supabase,
            puzzle_row,
            MagicMock(),
            dry_run=False,
            multi_model=False,
            rounds=1,
        )

        self.assertEqual(0, count)
        self.assertEqual(0, fixture.clue_table.update.call_count)
        self.assertEqual(0, fixture.puzzle_table.update.call_count)

    @patch("generator.redefine.rewrite_puzzle_definitions")
    @patch("generator.redefine.evaluate_puzzle_state")
    def test_persists_state_only_clue_delta(self, mock_evaluate, mock_rewrite):
        clue_rows = [
            _make_clue_row(
                "MUNTE",
                "Formă de relief",
                clue_id="c1",
                verified=False,
                verify_note="",
            )
        ]
        fixture = _SupabaseFixture(clue_rows)
        mock_evaluate.side_effect = lambda puzzle, *_args, **_kwargs: self._baseline_eval(
            puzzle,
            [
                {"verified": False, "semantic": 5, "targeting": 5, "creativity": 5, "rebus": 5},
            ],
        )

        def _rewrite(puzzle, *_args, **_kwargs):
            clue = puzzle.horizontal_clues[0]
            clue.current.assessment = ClueAssessment(
                verified=True,
                verify_candidates=["MUNTE"],
                feedback="clar",
                scores=ClueScores(
                    semantic_exactness=8,
                    answer_targeting=8,
                    creativity=6,
                    rebus_score=6,
                ),
            )
            clue.best = clue.current
            return SimpleNamespace(initial_passed=0, final_passed=1, total=1)

        mock_rewrite.side_effect = _rewrite
        puzzle_row = {
            "id": "p1",
            "title": "Test",
            "grid_size": 7,
            "description": "ok",
            "rebus_score_min": 5,
            "rebus_score_avg": 5.0,
            "definition_score": 10.0,
            "verified_count": 0,
            "total_clues": 1,
            "pass_rate": 0.0,
        }

        count = redefine_puzzle(
            fixture.supabase,
            puzzle_row,
            MagicMock(),
            dry_run=False,
            multi_model=False,
            rounds=1,
        )

        self.assertEqual(1, count)
        clue_payload = fixture.clue_table.update.call_args[0][0]
        self.assertEqual("Formă de relief", clue_payload["definition"])
        self.assertTrue(clue_payload["verified"])
        self.assertIn("Scor semantic: 8/10", clue_payload["verify_note"])

    def test_returns_zero_when_no_clues(self):
        fixture = _SupabaseFixture([])
        puzzle_row = {"id": "p1", "title": "Test", "grid_size": 7}
        client = MagicMock()

        count = redefine_puzzle(
            fixture.supabase, puzzle_row, client, dry_run=False, multi_model=False, rounds=1,
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
