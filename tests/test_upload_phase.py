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


if __name__ == "__main__":
    unittest.main()
