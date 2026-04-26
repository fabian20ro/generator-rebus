import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from postgrest.types import ReturnMethod

from rebus_generator.platform.persistence.supabase_ops import (
    execute_logged_insert,
    execute_logged_update,
    reset_supabase_usage_stats,
    supabase_usage_stats_snapshot,
)


class SupabaseOpsTests(unittest.TestCase):
    def tearDown(self):
        reset_supabase_usage_stats()

    def test_execute_logged_update_defaults_to_minimal_return(self):
        client = MagicMock()
        query = MagicMock()
        query.eq.return_value = query
        query.execute.return_value = SimpleNamespace(data=[])
        client.table.return_value.update.return_value = query

        execute_logged_update(
            client,
            "crossword_clues",
            {"verified": True},
            eq_filters={"id": "c1"},
        )

        client.table.return_value.update.assert_called_once_with(
            {"verified": True},
            returning=ReturnMethod.minimal,
        )
        stats = supabase_usage_stats_snapshot()
        self.assertEqual(1, stats["minimal_return_count"])
        self.assertEqual({"crossword_clues": 1}, stats["mutation_calls_by_table"])

    def test_execute_logged_insert_keeps_representation_when_requested(self):
        client = MagicMock()
        query = MagicMock()
        query.execute.return_value = SimpleNamespace(data=[{"id": "p1"}])
        client.table.return_value.insert.return_value = query

        result = execute_logged_insert(
            client,
            "crossword_puzzles",
            {"title": "Test"},
            returning=ReturnMethod.representation,
        )

        self.assertEqual([{"id": "p1"}], result.data)
        client.table.return_value.insert.assert_called_once_with(
            {"title": "Test"},
            returning=ReturnMethod.representation,
        )
        self.assertEqual(0, supabase_usage_stats_snapshot()["minimal_return_count"])


if __name__ == "__main__":
    unittest.main()
