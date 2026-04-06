import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from generator.core.model_manager import PRIMARY_MODEL, SECONDARY_MODEL, ModelConfig
from generator.phases.theme import TitleGenerationResult
from generator.retitle import (
    build_parser,
    fetch_clues,
    fetch_puzzles,
    generate_title_results_batch,
    retitle_puzzle,
    _RetitleBatchState,
    select_duplicate_puzzles_for_retitle,
    select_puzzles_for_retitle,
)


def _fake_ai_client(title: str):
    """Create a fake AI client that returns a fixed title."""

    def _create(**kwargs):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=title))]
        )
        return [response] if kwargs.get("stream") else response

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
    )


def _title_result(title: str, score: int, *, used_fallback: bool = False):
    return TitleGenerationResult(title=title, score=score, feedback="ok", used_fallback=used_fallback)


def _fake_rate_client(score: int):
    """Create a fake rate client that returns a fixed score."""
    content = json.dumps({"creativity_score": score, "feedback": "ok"})

    def _create(**kwargs):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )
        return [response] if kwargs.get("stream") else response

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
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )
        return [response] if kwargs.get("stream") else response

    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
    )


class _FakeRuntime:
    def __init__(self):
        self.trace = []

    def activate_primary(self):
        self.trace.append("primary")
        return PRIMARY_MODEL

    def activate_secondary(self):
        self.trace.append("secondary")
        return SECONDARY_MODEL


class _ModelAwareClient:
    def __init__(self, responses_by_model: dict[str, list[str]]):
        self._responses_by_model = {
            model: list(responses) for model, responses in responses_by_model.items()
        }

        def _create(**kwargs):
            model = kwargs["model"]
            responses = self._responses_by_model[model]
            content = responses.pop(0) if len(responses) > 1 else responses[0]
            response = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )
            return [response] if kwargs.get("stream") else response

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))


class FetchPuzzlesTests(unittest.TestCase):
    def test_select_puzzles_prioritizes_missing_title_score_then_created_at(self):
        rows = [
            {"id": "a", "title": "Sensuri Comune", "created_at": "2026-03-14T03:00:00+00:00", "title_score": 8},
            {"id": "b", "title": "Titlu B", "created_at": "2026-03-15T03:00:00+00:00", "title_score": None},
            {"id": "c", "title": "Titlu C", "created_at": "2026-03-13T03:00:00+00:00"},
            {"id": "d", "title": "Titlu D", "created_at": "2026-03-14T01:00:00+00:00", "title_score": 3},
            {"id": "e", "title": "Titlu E", "created_at": "2026-03-14T02:00:00+00:00"},
        ]

        result = select_puzzles_for_retitle(rows)

        self.assertEqual(["c", "e", "b", "d", "a"], [row["id"] for row in result])

    def test_select_duplicate_puzzles_prioritizes_largest_duplicate_groups(self):
        rows = [
            {"id": "a", "title": "Sensuri Comune", "created_at": "2026-03-14T03:00:00+00:00"},
            {"id": "b", "title": "sensuri comune", "created_at": "2026-03-15T03:00:00+00:00"},
            {"id": "c", "title": "Sensuri românești", "created_at": "2026-03-13T03:00:00+00:00"},
            {"id": "d", "title": "sensuri romanesti", "created_at": "2026-03-14T01:00:00+00:00"},
            {"id": "e", "title": "SENSURI ROMÂNEȘTI", "created_at": "2026-03-14T02:00:00+00:00"},
            {"id": "f", "title": "Titlu Unic", "created_at": "2026-03-12T03:00:00+00:00"},
        ]

        result = select_duplicate_puzzles_for_retitle(rows, global_rows=rows)

        self.assertEqual(["c", "d", "e", "a", "b"], [row["id"] for row in result])

    def test_fetch_puzzles_sorts_oldest_first(self):
        mock_supabase = MagicMock()
        mock_query = MagicMock()
        mock_supabase.table.return_value.select.return_value = mock_query
        mock_query.execute.return_value = SimpleNamespace(
            data=[
                {"id": "c", "created_at": "2026-03-15T01:00:00+00:00", "title": "X"},
                {"id": "a", "created_at": "2026-03-14T03:00:00+00:00", "title": "Y"},
                {"id": "b", "created_at": "2026-03-14T03:00:00+00:00", "title": "Z"},
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
    @patch("generator.retitle.LmRuntime")
    @patch("generator.retitle.generate_creative_title_result", return_value=_title_result("Orizont Verde", 8))
    def test_retitle_dry_run_skips_update(self, _mock_gen, mock_runtime_cls):
        runtime = mock_runtime_cls.return_value
        runtime.activate_primary.return_value = ModelConfig(registry_key="primary", model_id="primary", display_name="primary", max_completion_tokens=100)
        runtime.activate_secondary.return_value = ModelConfig(registry_key="secondary", model_id="secondary", display_name="secondary", max_completion_tokens=100)
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

    @patch("generator.retitle.LmRuntime")
    @patch("generator.retitle.generate_creative_title_result", return_value=_title_result("Orizont Verde", 8))
    def test_retitle_updates_supabase(self, _mock_gen, mock_runtime_cls):
        runtime = mock_runtime_cls.return_value
        runtime.activate_primary.return_value = ModelConfig(registry_key="primary", model_id="primary", display_name="primary", max_completion_tokens=100)
        runtime.activate_secondary.return_value = ModelConfig(registry_key="secondary", model_id="secondary", display_name="secondary", max_completion_tokens=100)
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
        payload = mock_supabase.table.return_value.update.call_args[0][0]
        self.assertEqual("Orizont Verde", payload["title"])
        self.assertEqual(8, payload["title_score"])
        self.assertIn("updated_at", payload)
        self.assertEqual("Orizont Verde", puzzle_row["title"])
        self.assertEqual(8, puzzle_row["title_score"])

    @patch("generator.retitle.LmRuntime")
    @patch("generator.retitle.generate_creative_title_result", return_value=_title_result("sensuri romanesti", 8))
    def test_retitle_rejects_normalized_duplicate_title(self, _mock_gen, mock_runtime_cls):
        runtime = mock_runtime_cls.return_value
        runtime.activate_primary.return_value = ModelConfig(registry_key="primary", model_id="primary", display_name="primary", max_completion_tokens=100)
        runtime.activate_secondary.return_value = ModelConfig(registry_key="secondary", model_id="secondary", display_name="secondary", max_completion_tokens=100)
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

        puzzle_row = {"id": "abc", "title": "Titlu Vechi"}
        ai_client = _fake_ai_client("unused")
        rate_client = _fake_rate_client(8)

        changed = retitle_puzzle(
            mock_supabase,
            puzzle_row,
            ai_client,
            rate_client,
            dry_run=False,
            forbidden_title_keys={"SENSURI ROMANESTI"},
        )

        self.assertFalse(changed)
        mock_supabase.table.return_value.update.assert_not_called()

    @patch("generator.retitle.LmRuntime")
    @patch("generator.retitle.generate_creative_title_result", return_value=_title_result("Fir de Cuvinte", 0, used_fallback=True))
    def test_retitle_skips_when_only_fallback_candidate_exists(self, _mock_gen, mock_runtime_cls):
        runtime = mock_runtime_cls.return_value
        runtime.activate_primary.return_value = ModelConfig(registry_key="primary", model_id="primary", display_name="primary", max_completion_tokens=100)
        runtime.activate_secondary.return_value = ModelConfig(registry_key="secondary", model_id="secondary", display_name="secondary", max_completion_tokens=100)
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

        changed = retitle_puzzle(
            mock_supabase,
            {"id": "abc", "title": "Titlu Vechi"},
            _fake_ai_client("unused"),
            _fake_rate_client(8),
            dry_run=False,
        )

        self.assertFalse(changed)
        mock_supabase.table.return_value.update.assert_not_called()


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

    @patch("generator.retitle.LmRuntime")
    @patch("generator.retitle.generate_creative_title_result", return_value=_title_result("Titlu Mediocru", 4))
    def test_skips_when_old_scores_higher(self, _mock_gen, mock_runtime_cls):
        runtime = mock_runtime_cls.return_value
        runtime.activate_primary.return_value = ModelConfig(registry_key="primary", model_id="primary", display_name="primary", max_completion_tokens=100)
        runtime.activate_secondary.return_value = ModelConfig(registry_key="secondary", model_id="secondary", display_name="secondary", max_completion_tokens=100)
        mock_supabase = self._make_supabase_mock()
        puzzle_row = {"id": "abc", "title": "Titlu Excelent Unic"}
        ai_client = _fake_ai_client("unused")
        # old_score=8, new_score=4
        rate_client = _fake_rate_client_sequential([8, 4])

        changed = retitle_puzzle(
            mock_supabase, puzzle_row, ai_client, rate_client, dry_run=False
        )

        self.assertFalse(changed)
        mock_supabase.table.return_value.update.assert_called_once()
        payload = mock_supabase.table.return_value.update.call_args[0][0]
        self.assertEqual(8, payload["title_score"])
        self.assertNotIn("title", payload)

    @patch("generator.retitle.LmRuntime")
    @patch("generator.retitle.generate_creative_title_result", return_value=_title_result("Titlu Nou Superior", 9))
    def test_replaces_when_new_scores_higher(self, _mock_gen, mock_runtime_cls):
        runtime = mock_runtime_cls.return_value
        runtime.activate_primary.return_value = ModelConfig(registry_key="primary", model_id="primary", display_name="primary", max_completion_tokens=100)
        runtime.activate_secondary.return_value = ModelConfig(registry_key="secondary", model_id="secondary", display_name="secondary", max_completion_tokens=100)
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

    @patch("generator.retitle.LmRuntime")
    @patch("generator.retitle.generate_creative_title_result", return_value=_title_result("Titlu Egal Nou", 6))
    def test_skips_when_scores_equal(self, _mock_gen, mock_runtime_cls):
        runtime = mock_runtime_cls.return_value
        runtime.activate_primary.return_value = ModelConfig(registry_key="primary", model_id="primary", display_name="primary", max_completion_tokens=100)
        runtime.activate_secondary.return_value = ModelConfig(registry_key="secondary", model_id="secondary", display_name="secondary", max_completion_tokens=100)
        mock_supabase = self._make_supabase_mock()
        puzzle_row = {"id": "abc", "title": "Titlu Egal Vechi"}
        ai_client = _fake_ai_client("unused")
        # old_score=6, new_score=6 — ties go to existing title
        rate_client = _fake_rate_client_sequential([6, 6])

        changed = retitle_puzzle(
            mock_supabase, puzzle_row, ai_client, rate_client, dry_run=False
        )

        self.assertFalse(changed)
        mock_supabase.table.return_value.update.assert_called_once()
        payload = mock_supabase.table.return_value.update.call_args[0][0]
        self.assertEqual(6, payload["title_score"])
        self.assertNotIn("title", payload)

    @patch("generator.retitle.LmRuntime")
    @patch("generator.retitle.generate_creative_title_result", return_value=_title_result("Orice Titlu Nou", 1))
    def test_always_replaces_fallback_title(self, _mock_gen, mock_runtime_cls):
        runtime = mock_runtime_cls.return_value
        runtime.activate_primary.return_value = ModelConfig(registry_key="primary", model_id="primary", display_name="primary", max_completion_tokens=100)
        runtime.activate_secondary.return_value = ModelConfig(registry_key="secondary", model_id="secondary", display_name="secondary", max_completion_tokens=100)
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

    @patch("generator.retitle.rate_title_creativity")
    @patch("generator.retitle.LmRuntime")
    @patch("generator.retitle.generate_creative_title_result", return_value=_title_result("Titlu Nou Superior", 9))
    def test_uses_stored_old_title_score_when_available(self, _mock_gen, mock_runtime_cls, mock_rate):
        runtime = mock_runtime_cls.return_value
        runtime.activate_primary.return_value = ModelConfig(registry_key="primary", model_id="primary", display_name="primary", max_completion_tokens=100)
        runtime.activate_secondary.return_value = ModelConfig(registry_key="secondary", model_id="secondary", display_name="secondary", max_completion_tokens=100)
        mock_supabase = self._make_supabase_mock()
        puzzle_row = {"id": "abc", "title": "Titlu Vechi Slab", "title_score": 3}
        ai_client = _fake_ai_client("unused")
        rate_client = _fake_rate_client(99)

        changed = retitle_puzzle(
            mock_supabase, puzzle_row, ai_client, rate_client, dry_run=False
        )

        self.assertTrue(changed)
        mock_rate.assert_not_called()
        payload = mock_supabase.table.return_value.update.call_args[0][0]
        self.assertEqual("Titlu Nou Superior", payload["title"])
        self.assertEqual(9, payload["title_score"])

    @patch("generator.retitle.rate_title_creativity")
    @patch("generator.retitle.LmRuntime")
    @patch("generator.retitle.generate_creative_title_result", return_value=_title_result("Titlu Nou Superior", 8))
    def test_invalid_old_title_gets_zero_without_llm_rating(self, _mock_gen, mock_runtime_cls, mock_rate):
        runtime = mock_runtime_cls.return_value
        runtime.activate_primary.return_value = ModelConfig(registry_key="primary", model_id="primary", display_name="primary", max_completion_tokens=100)
        runtime.activate_secondary.return_value = ModelConfig(registry_key="secondary", model_id="secondary", display_name="secondary", max_completion_tokens=100)
        mock_supabase = self._make_supabase_mock()
        puzzle_row = {"id": "abc", "title": "<|channel|>"}

        changed = retitle_puzzle(
            mock_supabase,
            puzzle_row,
            _fake_ai_client("unused"),
            _fake_rate_client(99),
            dry_run=False,
        )

        self.assertTrue(changed)
        mock_rate.assert_not_called()
        self.assertEqual(1, mock_supabase.table.return_value.update.call_count)
        payload = mock_supabase.table.return_value.update.call_args[0][0]
        self.assertEqual("Titlu Nou Superior", payload["title"])
        self.assertEqual(8, payload["title_score"])

    @patch("generator.retitle.LmRuntime")
    @patch("generator.retitle.generate_creative_title_result", return_value=_title_result("Titlu Mai Slab", 4))
    def test_invalid_old_title_backfills_zero_when_new_title_loses(self, _mock_gen, mock_runtime_cls):
        runtime = mock_runtime_cls.return_value
        runtime.activate_primary.return_value = ModelConfig(registry_key="primary", model_id="primary", display_name="primary", max_completion_tokens=100)
        runtime.activate_secondary.return_value = ModelConfig(registry_key="secondary", model_id="secondary", display_name="secondary", max_completion_tokens=100)
        mock_supabase = self._make_supabase_mock()
        puzzle_row = {"id": "abc", "title": "<|channel|>"}

        changed = retitle_puzzle(
            mock_supabase,
            puzzle_row,
            _fake_ai_client("unused"),
            _fake_rate_client(99),
            dry_run=False,
        )

        self.assertTrue(changed)
        payload = mock_supabase.table.return_value.update.call_args[0][0]
        self.assertEqual("Titlu Mai Slab", payload["title"])
        self.assertEqual(4, payload["title_score"])


class RetitleBatchGenerationTests(unittest.TestCase):
    def test_batch_generation_reuses_model_phases_across_multiple_puzzles(self):
        runtime = _FakeRuntime()
        gen_client = _ModelAwareClient(
            {
                PRIMARY_MODEL.model_id: ["Orizont Aprins", "Umbre Verzi"],
                SECONDARY_MODEL.model_id: ["Ecou Cald", "Foc Bland"],
            }
        )
        rate_client = _ModelAwareClient(
            {
                PRIMARY_MODEL.model_id: [
                    json.dumps({"creativity_score": 7, "feedback": "bun"}),
                    json.dumps({"creativity_score": 7, "feedback": "bun"}),
                ],
                SECONDARY_MODEL.model_id: [
                    json.dumps({"creativity_score": 8, "feedback": "excelent"}),
                    json.dumps({"creativity_score": 8, "feedback": "excelent"}),
                ],
            }
        )
        states = [
            _RetitleBatchState(
                puzzle_row={"id": "p1", "title": "Sensuri Comune"},
                words=["AER", "MUNTE"],
                definitions=["Gaz din atmosferă", "Formă de relief"],
                forbidden_title_keys=set(),
            ),
            _RetitleBatchState(
                puzzle_row={"id": "p2", "title": "Punți Nevăzute"},
                words=["APA", "LAC"],
                definitions=["Lichid vital", "Întindere de apă"],
                forbidden_title_keys=set(),
            ),
        ]

        results = generate_title_results_batch(
            states,
            gen_client,
            rate_client,
            runtime=runtime,
            multi_model=True,
        )

        self.assertEqual("Orizont Aprins", results["p1"].title)
        self.assertEqual("Umbre Verzi", results["p2"].title)
        self.assertEqual(["primary", "secondary"], runtime.trace)


class ParserTests(unittest.TestCase):
    def test_retitle_parser_accepts_date_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--date", "2026-03-15"])
        self.assertEqual("2026-03-15", args.date)

    def test_retitle_parser_accepts_all_fallbacks_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--all-fallbacks"])
        self.assertTrue(args.all_fallbacks)

    def test_retitle_parser_accepts_duplicates_only_flag(self):
        parser = build_parser()
        args = parser.parse_args(["--duplicates-only"])
        self.assertTrue(args.duplicates_only)

    def test_retitle_parser_accepts_batch_size(self):
        parser = build_parser()
        args = parser.parse_args(["--all", "--batch-size", "12"])
        self.assertEqual(12, args.batch_size)

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
