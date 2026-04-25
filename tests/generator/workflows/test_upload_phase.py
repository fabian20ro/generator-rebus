import unittest
from types import SimpleNamespace
from unittest.mock import patch

from rebus_generator.workflows.generate.upload import upload_puzzle


class _InsertQuery:
    def __init__(self, payload_store, table_name, *, fail=False):
        self.payload_store = payload_store
        self.table_name = table_name
        self.fail = fail

    def execute(self):
        if self.fail:
            raise RuntimeError(f"insert failed for {self.table_name}")
        if self.table_name == "crossword_puzzles":
            return SimpleNamespace(data=[{"id": "puzzle-1"}])
        return SimpleNamespace(data=[])


class _Table:
    def __init__(self, payload_store, table_name, *, fail_inserts=None):
        self.payload_store = payload_store
        self.table_name = table_name
        self.fail_inserts = fail_inserts or set()
        self.delete_filters = None

    def insert(self, payload):
        self.payload_store[self.table_name] = payload
        return _InsertQuery(
            self.payload_store,
            self.table_name,
            fail=self.table_name in self.fail_inserts,
        )

    def delete(self):
        self.payload_store.setdefault("_deletes", []).append({"table": self.table_name, "filters": {}})
        self.delete_filters = self.payload_store["_deletes"][-1]["filters"]
        return self

    def eq(self, field, value):
        if self.delete_filters is not None:
            self.delete_filters[field] = value
        return self

    def execute(self):
        return SimpleNamespace(data=[])


class _Client:
    def __init__(self, *, fail_inserts=None):
        self.payload_store = {}
        self.fail_inserts = fail_inserts or set()

    def table(self, name):
        return _Table(self.payload_store, name, fail_inserts=self.fail_inserts)


class UploadPhaseTests(unittest.TestCase):
    @patch("rebus_generator.workflows.generate.upload._now_iso", return_value="2026-03-31T10:11:12+00:00")
    @patch("rebus_generator.workflows.generate.upload.create_client")
    @patch("rebus_generator.workflows.generate.upload.SUPABASE_SERVICE_ROLE_KEY", "test-key")
    @patch("rebus_generator.workflows.generate.upload.SUPABASE_URL", "https://example.supabase.co")
    def test_upload_sets_created_and_updated_at_to_same_timestamp(
        self,
        mock_create_client,
        _mock_now,
    ):
        client = _Client()
        mock_create_client.return_value = client
        puzzle = SimpleNamespace(
            title="Test",
            size=1,
            grid=[["A"]],
            horizontal_clues=[],
            vertical_clues=[],
        )

        puzzle_id = upload_puzzle(puzzle)

        self.assertEqual("puzzle-1", puzzle_id)
        payload = client.payload_store["crossword_puzzles"]
        self.assertEqual("2026-03-31T10:11:12+00:00", payload["created_at"])
        self.assertEqual("2026-03-31T10:11:12+00:00", payload["updated_at"])
        self.assertNotIn("theme", payload)

    @patch("rebus_generator.workflows.generate.upload.log_canonical_event")
    @patch("rebus_generator.workflows.generate.upload.ClueCanonService")
    @patch("rebus_generator.workflows.generate.upload.ClueCanonStore")
    @patch("rebus_generator.workflows.generate.upload._slots_with_words")
    @patch("rebus_generator.workflows.generate.upload.create_client")
    @patch("rebus_generator.workflows.generate.upload.SUPABASE_SERVICE_ROLE_KEY", "test-key")
    @patch("rebus_generator.workflows.generate.upload.SUPABASE_URL", "https://example.supabase.co")
    def test_upload_defaults_missing_word_type_to_empty_string(
        self,
        mock_create_client,
        mock_slots_with_words,
        mock_store_cls,
        mock_service_cls,
        _mock_log_canonical_event,
    ):
        client = _Client()
        mock_create_client.return_value = client
        mock_slots_with_words.return_value = [
            (SimpleNamespace(direction="H", start_row=0, start_col=0), "A")
        ]
        mock_store = mock_store_cls.return_value
        mock_store.build_clue_definition_payload.return_value = {
            "canonical_definition_id": "canon-1",
        }
        mock_service_cls.return_value.resolve_definition.return_value = SimpleNamespace(
            canonical_definition_id="canon-1",
            action="reuse",
            canonical_definition="Prima literă",
            decision_note=None,
        )
        puzzle = SimpleNamespace(
            title="Test",
            size=1,
            grid=[["A"]],
            horizontal_clues=[
                SimpleNamespace(
                    word_normalized="A",
                    word_original="a",
                    definition="Prima literă",
                    verified=True,
                )
            ],
            vertical_clues=[],
        )

        puzzle_id = upload_puzzle(puzzle)

        self.assertEqual("puzzle-1", puzzle_id)
        clue_rows = client.payload_store["crossword_clues"]
        self.assertEqual("", clue_rows[0]["word_type"])

    @patch("rebus_generator.workflows.generate.upload.ClueCanonService")
    @patch("rebus_generator.workflows.generate.upload.ClueCanonStore")
    @patch("rebus_generator.workflows.generate.upload._slots_with_words")
    @patch("rebus_generator.workflows.generate.upload.create_client")
    @patch("rebus_generator.workflows.generate.upload.SUPABASE_SERVICE_ROLE_KEY", "test-key")
    @patch("rebus_generator.workflows.generate.upload.SUPABASE_URL", "https://example.supabase.co")
    def test_upload_threads_runtime_to_clue_canon_service(
        self,
        mock_create_client,
        mock_slots_with_words,
        mock_store_cls,
        mock_service_cls,
    ):
        client = _Client()
        mock_create_client.return_value = client
        mock_slots_with_words.return_value = [
            (SimpleNamespace(direction="H", start_row=0, start_col=0), "A")
        ]
        mock_store = mock_store_cls.return_value
        mock_store.build_clue_definition_payload.return_value = {
            "canonical_definition_id": "canon-1",
        }
        mock_service_cls.return_value.resolve_definition.return_value = SimpleNamespace(
            canonical_definition_id="canon-1",
            action="reuse",
            canonical_definition="Prima literă",
            decision_note=None,
        )
        ai_client = object()
        runtime = object()
        puzzle = SimpleNamespace(
            title="Test",
            size=1,
            grid=[["A"]],
            horizontal_clues=[
                SimpleNamespace(
                    word_normalized="A",
                    word_original="a",
                    definition="Prima literă",
                    verified=True,
                )
            ],
            vertical_clues=[],
        )

        upload_puzzle(
            puzzle,
            client=ai_client,
            runtime=runtime,
            multi_model=False,
        )

        mock_service_cls.assert_called_once_with(
            store=mock_store,
            client=ai_client,
            runtime=runtime,
            multi_model=False,
        )

    @patch("rebus_generator.workflows.generate.upload.ClueCanonService")
    @patch("rebus_generator.workflows.generate.upload.ClueCanonStore")
    @patch("rebus_generator.workflows.generate.upload._slots_with_words")
    @patch("rebus_generator.workflows.generate.upload.create_client")
    @patch("rebus_generator.workflows.generate.upload.SUPABASE_SERVICE_ROLE_KEY", "test-key")
    @patch("rebus_generator.workflows.generate.upload.SUPABASE_URL", "https://example.supabase.co")
    def test_upload_does_not_insert_puzzle_before_canonical_resolution(
        self,
        mock_create_client,
        mock_slots_with_words,
        mock_store_cls,
        mock_service_cls,
    ):
        client = _Client()
        mock_create_client.return_value = client
        mock_slots_with_words.return_value = [
            (SimpleNamespace(direction="H", start_row=0, start_col=0), "A")
        ]
        mock_store_cls.return_value.build_clue_definition_payload.return_value = {
            "canonical_definition_id": "canon-1",
        }
        mock_service_cls.return_value.resolve_definition.side_effect = RuntimeError("referee failed")
        puzzle = SimpleNamespace(
            title="Test",
            size=1,
            grid=[["A"]],
            horizontal_clues=[
                SimpleNamespace(
                    word_normalized="A",
                    word_original="a",
                    definition="Prima literă",
                    verified=True,
                )
            ],
            vertical_clues=[],
        )

        with self.assertRaisesRegex(RuntimeError, "referee failed"):
            upload_puzzle(puzzle)

        self.assertNotIn("crossword_puzzles", client.payload_store)

    @patch("rebus_generator.workflows.generate.upload.ClueCanonService")
    @patch("rebus_generator.workflows.generate.upload.ClueCanonStore")
    @patch("rebus_generator.workflows.generate.upload._slots_with_words")
    @patch("rebus_generator.workflows.generate.upload.create_client")
    @patch("rebus_generator.workflows.generate.upload.SUPABASE_SERVICE_ROLE_KEY", "test-key")
    @patch("rebus_generator.workflows.generate.upload.SUPABASE_URL", "https://example.supabase.co")
    def test_upload_cleans_up_partial_puzzle_when_clue_insert_fails(
        self,
        mock_create_client,
        mock_slots_with_words,
        mock_store_cls,
        mock_service_cls,
    ):
        client = _Client(fail_inserts={"crossword_clues"})
        mock_create_client.return_value = client
        mock_slots_with_words.return_value = [
            (SimpleNamespace(direction="H", start_row=0, start_col=0), "A")
        ]
        mock_store = mock_store_cls.return_value
        mock_store.build_clue_definition_payload.return_value = {
            "canonical_definition_id": "canon-1",
        }
        mock_service_cls.return_value.resolve_definition.return_value = SimpleNamespace(
            canonical_definition_id="canon-1",
            action="reuse",
            canonical_definition="Prima literă",
            decision_note=None,
        )
        puzzle = SimpleNamespace(
            title="Test",
            size=1,
            grid=[["A"]],
            horizontal_clues=[
                SimpleNamespace(
                    word_normalized="A",
                    word_original="a",
                    definition="Prima literă",
                    verified=True,
                )
            ],
            vertical_clues=[],
        )

        with self.assertRaisesRegex(RuntimeError, "insert failed for crossword_clues"):
            upload_puzzle(puzzle)

        self.assertEqual(
            [
                {"table": "crossword_clues", "filters": {"puzzle_id": "puzzle-1"}},
                {"table": "crossword_puzzles", "filters": {"id": "puzzle-1"}},
            ],
            client.payload_store["_deletes"],
        )
        mock_store.delete_unreferenced_canonicals_by_ids.assert_not_called()

    @patch("rebus_generator.workflows.generate.upload.ClueCanonService")
    @patch("rebus_generator.workflows.generate.upload.ClueCanonStore")
    @patch("rebus_generator.workflows.generate.upload._slots_with_words")
    @patch("rebus_generator.workflows.generate.upload.create_client")
    @patch("rebus_generator.workflows.generate.upload.SUPABASE_SERVICE_ROLE_KEY", "test-key")
    @patch("rebus_generator.workflows.generate.upload.SUPABASE_URL", "https://example.supabase.co")
    def test_upload_deletes_new_unreferenced_canonicals_when_clue_insert_fails(
        self,
        mock_create_client,
        mock_slots_with_words,
        mock_store_cls,
        mock_service_cls,
    ):
        client = _Client(fail_inserts={"crossword_clues"})
        mock_create_client.return_value = client
        mock_slots_with_words.return_value = [
            (SimpleNamespace(direction="H", start_row=0, start_col=0), "A"),
            (SimpleNamespace(direction="V", start_row=0, start_col=0), "A"),
        ]
        mock_store = mock_store_cls.return_value
        mock_store.build_clue_definition_payload.side_effect = lambda *, canonical_definition_id, **kwargs: {
            "canonical_definition_id": canonical_definition_id,
        }
        mock_service_cls.return_value.resolve_definition.side_effect = [
            SimpleNamespace(
                canonical_definition_id="canon-new",
                action="create_new",
                canonical_definition="Prima literă",
                decision_note=None,
                created_new=True,
            ),
            SimpleNamespace(
                canonical_definition_id="canon-reused",
                action="reuse_exact",
                canonical_definition="Prima literă verticală",
                decision_note=None,
                created_new=False,
            ),
        ]
        puzzle = SimpleNamespace(
            title="Test",
            size=1,
            grid=[["A"]],
            horizontal_clues=[
                SimpleNamespace(
                    word_normalized="A",
                    word_original="a",
                    definition="Prima literă",
                    verified=True,
                )
            ],
            vertical_clues=[
                SimpleNamespace(
                    word_normalized="A",
                    word_original="a",
                    definition="Prima literă verticală",
                    verified=True,
                )
            ],
        )

        with self.assertRaisesRegex(RuntimeError, "insert failed for crossword_clues"):
            upload_puzzle(puzzle)

        mock_store.delete_unreferenced_canonicals_by_ids.assert_called_once_with(["canon-new"])


if __name__ == "__main__":
    unittest.main()
