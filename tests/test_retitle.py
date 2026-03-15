import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from generator.retitle import (
    build_parser,
    fetch_clues,
    fetch_puzzles,
    retitle_puzzle,
)


def _fake_ai_client(title: str):
    """Create a fake AI client that returns a fixed title."""

    def _create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=title))]
        )

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
    )


def _fake_rate_client(score: int):
    """Create a fake rate client that returns a fixed score."""
    content = json.dumps({"creativity_score": score, "feedback": "ok"})

    def _create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
    )


def _fake_rate_client_sequential(scores: list[int]):
    """Create a fake rate client that returns scores in order per call."""
    call_index = {"i": 0}

    def _create(**kwargs):
        score = scores[call_index["i"] % len(scores)]
        call_index["i"] += 1
        content = json.dumps({"creativity_score": score, "feedback": "ok"})
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
    )


class FetchPuzzlesTests(unittest.TestCase):
    def test_fetch_puzzles_by_date(self):
        mock_supabase = MagicMock()
        mock_query = MagicMock()
        mock_supabase.table.return_value.select.return_value = mock_query
        mock_query.gte.return_value = mock_query
        mock_query.lte.return_value = mock_query
        mock_query.execute.return_value = SimpleNamespace(
            data=[{"id": "abc", "title": "Sensuri Comune"}]
        )

        result = fetch_puzzles(mock_supabase, date="2026-03-15")

        self.assertEqual(1, len(result))
        self.assertEqual("abc", result[0]["id"])
        mock_query.gte.assert_called_once_with("created_at", "2026-03-15T00:00:00")
        mock_query.lte.assert_called_once_with("created_at", "2026-03-15T23:59:59")

    def test_fetch_puzzles_fallbacks_only(self):
        mock_supabase = MagicMock()
        mock_query = MagicMock()
        mock_supabase.table.return_value.select.return_value = mock_query
        mock_query.execute.return_value = SimpleNamespace(
            data=[
                {"id": "1", "title": "Sensuri Comune"},
                {"id": "2", "title": "Titlu Creativ Unic"},
            ]
        )

        result = fetch_puzzles(mock_supabase, fallbacks_only=True)

        self.assertEqual(1, len(result))
        self.assertEqual("1", result[0]["id"])


class FetchCluesTests(unittest.TestCase):
    def test_fetch_clues_returns_list(self):
        mock_supabase = MagicMock()
        mock_query = MagicMock()
        mock_supabase.table.return_value.select.return_value = mock_query
        mock_query.eq.return_value = mock_query
        mock_query.execute.return_value = SimpleNamespace(
            data=[
                {"word_normalized": "MUNTE", "definition": "Formă de relief"},
                {"word_normalized": "APA", "definition": "Lichid vital"},
            ]
        )

        result = fetch_clues(mock_supabase, "abc-123")

        self.assertEqual(2, len(result))
        mock_query.eq.assert_called_once_with("puzzle_id", "abc-123")


class RetitlePuzzleTests(unittest.TestCase):
    def test_retitle_dry_run_skips_update(self):
        mock_supabase = MagicMock()
        # fetch_clues mock
        clue_query = MagicMock()
        mock_supabase.table.return_value.select.return_value = clue_query
        clue_query.eq.return_value = clue_query
        clue_query.execute.return_value = SimpleNamespace(
            data=[
                {"word_normalized": "MUNTE", "definition": "Formă de relief"},
                {"word_normalized": "PADURE", "definition": "Arbori mulți"},
            ]
        )

        puzzle_row = {"id": "abc", "title": "Sensuri Comune"}
        ai_client = _fake_ai_client("Orizont Verde")
        rate_client = _fake_rate_client(8)

        changed = retitle_puzzle(
            mock_supabase, puzzle_row, ai_client, rate_client, dry_run=True
        )

        self.assertTrue(changed)
        # update should NOT have been called
        mock_supabase.table.return_value.update.assert_not_called()

    def test_retitle_updates_supabase(self):
        mock_supabase = MagicMock()
        clue_query = MagicMock()
        mock_supabase.table.return_value.select.return_value = clue_query
        clue_query.eq.return_value = clue_query
        clue_query.execute.return_value = SimpleNamespace(
            data=[
                {"word_normalized": "MUNTE", "definition": "Formă de relief"},
                {"word_normalized": "PADURE", "definition": "Arbori mulți"},
            ]
        )

        update_chain = MagicMock()
        mock_supabase.table.return_value.update.return_value = update_chain
        update_chain.eq.return_value = update_chain

        puzzle_row = {"id": "abc", "title": "Sensuri Comune"}
        ai_client = _fake_ai_client("Orizont Verde")
        rate_client = _fake_rate_client(8)

        changed = retitle_puzzle(
            mock_supabase, puzzle_row, ai_client, rate_client, dry_run=False
        )

        self.assertTrue(changed)
        mock_supabase.table.return_value.update.assert_called_once()


class RetitleScoreComparisonTests(unittest.TestCase):
    """Tests for the score-based quality gate in retitle_puzzle."""

    def _make_supabase_mock(self):
        mock_supabase = MagicMock()
        clue_query = MagicMock()
        mock_supabase.table.return_value.select.return_value = clue_query
        clue_query.eq.return_value = clue_query
        clue_query.execute.return_value = SimpleNamespace(
            data=[
                {"word_normalized": "MUNTE", "definition": "Formă de relief"},
                {"word_normalized": "PADURE", "definition": "Arbori mulți"},
            ]
        )
        update_chain = MagicMock()
        mock_supabase.table.return_value.update.return_value = update_chain
        update_chain.eq.return_value = update_chain
        return mock_supabase

    @patch("generator.retitle.generate_creative_title", return_value="Titlu Mediocru")
    def test_skips_when_old_scores_higher(self, _mock_gen):
        mock_supabase = self._make_supabase_mock()
        puzzle_row = {"id": "abc", "title": "Titlu Excelent Unic"}
        ai_client = _fake_ai_client("unused")
        # old_score=8, new_score=4
        rate_client = _fake_rate_client_sequential([8, 4])

        changed = retitle_puzzle(
            mock_supabase, puzzle_row, ai_client, rate_client, dry_run=False
        )

        self.assertFalse(changed)
        mock_supabase.table.return_value.update.assert_not_called()

    @patch("generator.retitle.generate_creative_title", return_value="Titlu Nou Superior")
    def test_replaces_when_new_scores_higher(self, _mock_gen):
        mock_supabase = self._make_supabase_mock()
        puzzle_row = {"id": "abc", "title": "Titlu Vechi Slab"}
        ai_client = _fake_ai_client("unused")
        # old_score=3, new_score=9
        rate_client = _fake_rate_client_sequential([3, 9])

        changed = retitle_puzzle(
            mock_supabase, puzzle_row, ai_client, rate_client, dry_run=False
        )

        self.assertTrue(changed)
        mock_supabase.table.return_value.update.assert_called_once()

    @patch("generator.retitle.generate_creative_title", return_value="Titlu Egal Nou")
    def test_skips_when_scores_equal(self, _mock_gen):
        mock_supabase = self._make_supabase_mock()
        puzzle_row = {"id": "abc", "title": "Titlu Egal Vechi"}
        ai_client = _fake_ai_client("unused")
        # old_score=6, new_score=6 — ties go to existing title
        rate_client = _fake_rate_client_sequential([6, 6])

        changed = retitle_puzzle(
            mock_supabase, puzzle_row, ai_client, rate_client, dry_run=False
        )

        self.assertFalse(changed)
        mock_supabase.table.return_value.update.assert_not_called()

    def test_always_replaces_fallback_title(self):
        mock_supabase = self._make_supabase_mock()
        # "Sensuri Comune" is in FALLBACK_TITLES — should bypass score check
        puzzle_row = {"id": "abc", "title": "Sensuri Comune"}
        ai_client = _fake_ai_client("Orice Titlu Nou")
        # rate_client returns low score, but it shouldn't matter for fallbacks
        rate_client = _fake_rate_client(1)

        changed = retitle_puzzle(
            mock_supabase, puzzle_row, ai_client, rate_client, dry_run=False
        )

        self.assertTrue(changed)
        mock_supabase.table.return_value.update.assert_called_once()


class ParserTests(unittest.TestCase):
    def test_retitle_parser_accepts_date_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--date", "2026-03-15"])
        self.assertEqual("2026-03-15", args.date)

    def test_retitle_parser_accepts_all_fallbacks_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--all-fallbacks"])
        self.assertTrue(args.all_fallbacks)

    def test_retitle_parser_accepts_dry_run(self):
        parser = build_parser()
        args = parser.parse_args(["--date", "2026-03-15", "--dry-run"])
        self.assertTrue(args.dry_run)

    def test_retitle_parser_accepts_puzzle_id(self):
        parser = build_parser()
        args = parser.parse_args(["--puzzle-id", "abc-123"])
        self.assertEqual("abc-123", args.puzzle_id)

    def test_retitle_parser_no_multi_model(self):
        parser = build_parser()
        args = parser.parse_args(["--date", "2026-03-15", "--no-multi-model"])
        self.assertFalse(args.multi_model)

    def test_retitle_parser_multi_model_default_true(self):
        parser = build_parser()
        args = parser.parse_args(["--date", "2026-03-15"])
        self.assertTrue(args.multi_model)


if __name__ == "__main__":
    unittest.main()
