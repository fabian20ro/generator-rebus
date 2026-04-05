import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from generator.core.model_manager import SECONDARY_MODEL
from generator.core.pipeline_state import ClueAssessment, ClueScores, PuzzleAssessment
from generator.core.puzzle_metrics import PuzzleEvaluationResult
from generator.phases.theme import TitleGenerationResult
from generator.repair_puzzles import (
    build_parser,
    repair_puzzle,
    select_puzzles_for_repair,
)


def _make_clue_row(
    word: str,
    definition: str,
    *,
    direction: str = "H",
    clue_id: str = "clue-1",
    row: int = 0,
    col: int = 0,
    word_type: str = "",
    canonical_definition_id: str | None = None,
) -> dict:
    return {
        "id": clue_id,
        "puzzle_id": "puzzle-1",
        "word_normalized": word,
        "word_original": word.lower(),
        "word_type": word_type,
        "definition": definition,
        "direction": direction,
        "start_row": row,
        "start_col": col,
        "length": len(word),
        "clue_number": 1,
        "canonical_definition_id": canonical_definition_id,
        "verify_note": "",
        "verified": False,
        "definition_source": "canonical" if canonical_definition_id else "legacy",
    }


def _assessment(min_rebus: int, *, avg_rebus: float | None = None, verified_count: int = 1, total: int = 1) -> PuzzleAssessment:
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
        self._canonical_rows: list[dict] = []
        self._next_canonical_id = 1

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
        self.clue_select.limit.return_value = self.clue_select
        self.clue_select.execute.return_value = SimpleNamespace(data=clue_rows)
        self.clue_update = MagicMock()
        self.clue_update.eq.return_value = self.clue_update
        self.clue_update.execute.return_value = SimpleNamespace(data=[])
        self.clue_table = MagicMock()
        self.clue_table.select.return_value = self.clue_select
        self.clue_table.update.return_value = self.clue_update

        self.clue_effective_select = MagicMock()
        self.clue_effective_select.eq.return_value = self.clue_effective_select
        self.clue_effective_select.limit.return_value = self.clue_effective_select
        self.clue_effective_select.execute.return_value = SimpleNamespace(data=clue_rows)
        self.clue_effective_table = MagicMock()
        self.clue_effective_table.select.return_value = self.clue_effective_select

        self.canonical_select = MagicMock()
        self.canonical_select.eq.return_value = self.canonical_select
        self.canonical_select.limit.return_value = self.canonical_select
        self.canonical_select.in_.return_value = self.canonical_select
        self.canonical_select.execute.side_effect = lambda: SimpleNamespace(data=list(self._canonical_rows))
        self.canonical_insert = MagicMock()

        def _canonical_insert_execute():
            payload = dict(self.canonical_insert_payload)
            payload.setdefault("id", f"canon-{self._next_canonical_id}")
            self._next_canonical_id += 1
            self._canonical_rows.append(payload)
            return SimpleNamespace(data=[payload])

        self.canonical_insert.execute.side_effect = _canonical_insert_execute
        self.canonical_table = MagicMock()
        self.canonical_table.select.return_value = self.canonical_select

        def _canonical_insert(payload):
            self.canonical_insert_payload = payload
            return self.canonical_insert

        self.canonical_table.insert.side_effect = _canonical_insert

        self.alias_select = MagicMock()
        self.alias_select.eq.return_value = self.alias_select
        self.alias_select.limit.return_value = self.alias_select
        self.alias_select.execute.return_value = SimpleNamespace(data=[])
        self.alias_insert = MagicMock()
        self.alias_insert.execute.return_value = SimpleNamespace(data=[])
        self.alias_table = MagicMock()
        self.alias_table.select.return_value = self.alias_select
        self.alias_table.insert.return_value = self.alias_insert

        def _table(name: str):
            if name == "crossword_puzzles":
                return self.puzzle_table
            if name == "crossword_clues":
                return self.clue_table
            if name == "crossword_clue_effective":
                return self.clue_effective_table
            if name == "canonical_clue_definitions":
                return self.canonical_table
            if name == "canonical_clue_aliases":
                return self.alias_table
            raise AssertionError(name)

        self.supabase.table.side_effect = _table


class QueueTests(unittest.TestCase):
    def test_select_puzzles_prioritizes_missing_score_then_lowest_then_oldest(self):
        rows = [
            {"id": "scored-new", "rebus_score_min": 3, "created_at": "2026-03-20T10:00:00+00:00", "repaired_at": None},
            {"id": "missing-new", "rebus_score_min": None, "created_at": "2026-03-21T10:00:00+00:00", "repaired_at": None},
            {"id": "missing-old", "rebus_score_min": None, "created_at": "2026-03-10T10:00:00+00:00", "repaired_at": None},
            {"id": "scored-old", "rebus_score_min": 3, "created_at": "2026-03-11T10:00:00+00:00", "repaired_at": "2026-03-12T10:00:00+00:00"},
            {"id": "scored-higher", "rebus_score_min": 7, "created_at": "2026-03-01T10:00:00+00:00", "repaired_at": None},
        ]

        selected = select_puzzles_for_repair(rows, limit=5)

        self.assertEqual(
            ["missing-old", "missing-new", "scored-old", "scored-new", "scored-higher"],
            [row["id"] for row in selected],
        )


class RepairPuzzleTests(unittest.TestCase):
    @patch("generator.repair_puzzles.score_puzzle_state")
    @patch("generator.repair_puzzles.run_rewrite_loop")
    @patch("generator.repair_puzzles.evaluate_puzzle_state")
    def test_backfills_metadata_even_when_candidate_not_better(
        self,
        mock_evaluate,
        mock_run_rewrite,
        mock_score_state,
    ):
        clue_rows = [_make_clue_row("MUNTE", "Formă de relief")]
        fixture = _SupabaseFixture(clue_rows)
        puzzle_row = {
            "id": "p1",
            "title": "Titlu vechi",
            "grid_size": 7,
            "description": None,
            "rebus_score_min": None,
            "rebus_score_avg": None,
            "definition_score": None,
            "verified_count": None,
            "total_clues": None,
            "pass_rate": None,
        }
        mock_evaluate.return_value = PuzzleEvaluationResult(
            assessment=_assessment(5),
            passed=1,
            total=1,
            evaluator_model=SECONDARY_MODEL.display_name,
        )
        mock_run_rewrite.return_value = SimpleNamespace(initial_passed=1, final_passed=1, total=1)
        mock_score_state.return_value = _assessment(5)

        status = repair_puzzle(
            fixture.supabase,
            puzzle_row,
            MagicMock(),
            MagicMock(),
            dry_run=False,
            multi_model=False,
            rounds=1,
            verify_candidates=1,
        )

        self.assertEqual("rejected", status)
        self.assertEqual(1, fixture.puzzle_table.update.call_count)
        self.assertEqual(0, fixture.clue_table.update.call_count)
        payload = fixture.puzzle_table.update.call_args[0][0]
        self.assertEqual(5, payload["rebus_score_min"])
        self.assertIn("Scor rebus: 5/10", payload["description"])

    @patch(
        "generator.repair_puzzles.generate_creative_title_result",
        return_value=TitleGenerationResult("Titlu Nou", 8, "ok"),
    )
    @patch("generator.repair_puzzles.score_puzzle_state")
    @patch("generator.repair_puzzles.run_rewrite_loop")
    @patch("generator.repair_puzzles.evaluate_puzzle_state")
    def test_accepts_better_candidate_and_updates_puzzle_and_clues(
        self,
        mock_evaluate,
        mock_run_rewrite,
        mock_score_state,
        _mock_title,
    ):
        clue_rows = [_make_clue_row("MUNTE", "Formă de relief", clue_id="c1")]
        fixture = _SupabaseFixture(clue_rows)
        puzzle_row = {
            "id": "p1",
            "title": "Titlu vechi",
            "grid_size": 7,
            "description": "vechi",
            "rebus_score_min": 5,
            "rebus_score_avg": 5.0,
            "definition_score": 10.0,
            "verified_count": 1,
            "total_clues": 1,
            "pass_rate": 1.0,
        }
        mock_evaluate.return_value = PuzzleEvaluationResult(
            assessment=_assessment(5),
            passed=1,
            total=1,
            evaluator_model=SECONDARY_MODEL.display_name,
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
            return SimpleNamespace(initial_passed=0, final_passed=1, total=1)

        mock_run_rewrite.side_effect = _rewrite
        mock_score_state.return_value = _assessment(7)

        status = repair_puzzle(
            fixture.supabase,
            puzzle_row,
            MagicMock(),
            MagicMock(),
            dry_run=False,
            multi_model=False,
            rounds=1,
            verify_candidates=1,
        )

        self.assertEqual("accepted", status)
        self.assertEqual(1, fixture.puzzle_table.update.call_count)
        self.assertEqual(1, fixture.clue_table.update.call_count)

        puzzle_payload = fixture.puzzle_table.update.call_args[0][0]
        self.assertEqual("Titlu Nou", puzzle_payload["title"])
        self.assertEqual(8, puzzle_payload["title_score"])
        self.assertEqual(7, puzzle_payload["rebus_score_min"])
        self.assertIn("repaired_at", puzzle_payload)
        self.assertIn("updated_at", puzzle_payload)

        clue_payload = fixture.clue_table.update.call_args[0][0]
        self.assertTrue(clue_payload["canonical_definition_id"].startswith("canon-"))
        self.assertTrue(clue_payload["verified"])
        self.assertIn("Scor rebus: 7/10", clue_payload["verify_note"])


class ParserTests(unittest.TestCase):
    def test_parser_accepts_limit(self):
        parser = build_parser()
        args = parser.parse_args(["--limit", "3"])
        self.assertEqual(3, args.limit)

    def test_parser_multi_model_defaults_true(self):
        parser = build_parser()
        args = parser.parse_args([])
        self.assertTrue(args.multi_model)
