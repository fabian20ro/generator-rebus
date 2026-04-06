import unittest
from types import SimpleNamespace
from unittest.mock import patch

from generator.phases.upload import upload_puzzle


class _InsertQuery:
    def __init__(self, payload_store, table_name):
        self.payload_store = payload_store
        self.table_name = table_name

    def execute(self):
        if self.table_name == "crossword_puzzles":
            return SimpleNamespace(data=[{"id": "puzzle-1"}])
        return SimpleNamespace(data=[])


class _Table:
    def __init__(self, payload_store, table_name):
        self.payload_store = payload_store
        self.table_name = table_name

    def insert(self, payload):
        self.payload_store[self.table_name] = payload
        return _InsertQuery(self.payload_store, self.table_name)


class _Client:
    def __init__(self):
        self.payload_store = {}

    def table(self, name):
        return _Table(self.payload_store, name)


class UploadPhaseTests(unittest.TestCase):
    @patch("generator.phases.upload._now_iso", return_value="2026-03-31T10:11:12+00:00")
    @patch("generator.phases.upload.create_client")
    @patch("generator.phases.upload.SUPABASE_SERVICE_ROLE_KEY", "test-key")
    @patch("generator.phases.upload.SUPABASE_URL", "https://example.supabase.co")
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

    @patch("generator.phases.upload.log_canonical_event")
    @patch("generator.phases.upload.ClueCanonService")
    @patch("generator.phases.upload.ClueCanonStore")
    @patch("generator.phases.upload._slots_with_words")
    @patch("generator.phases.upload.create_client")
    @patch("generator.phases.upload.SUPABASE_SERVICE_ROLE_KEY", "test-key")
    @patch("generator.phases.upload.SUPABASE_URL", "https://example.supabase.co")
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


if __name__ == "__main__":
    unittest.main()
