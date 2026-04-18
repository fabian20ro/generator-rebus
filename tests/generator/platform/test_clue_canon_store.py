import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from postgrest.exceptions import APIError

from rebus_generator.platform.persistence.clue_canon_store import ClueCanonStore


class ClueCanonStoreTests(unittest.TestCase):
    @patch("rebus_generator.platform.persistence.clue_canon_store.create_service_role_client", side_effect=RuntimeError("missing env"))
    def test_store_fails_fast_when_client_creation_fails(self, _mock_create):
        with self.assertRaisesRegex(RuntimeError, "missing env"):
            ClueCanonStore()

    def test_build_clue_definition_payload_only_writes_pointer_and_state(self):
        store = ClueCanonStore(client=MagicMock())

        payload = store.build_clue_definition_payload(
            canonical_definition_id="canon-1",
            verify_note="ok",
            verified=True,
        )

        self.assertEqual(
            {
                "canonical_definition_id": "canon-1",
                "verify_note": "ok",
                "verified": True,
            },
            payload,
        )

    def test_fetch_clue_rows_reads_effective_view_without_definition_source(self):
        client = MagicMock()
        query = MagicMock()
        query.eq.return_value = query
        query.is_.return_value = query
        query.range.return_value = query
        query.execute.return_value = SimpleNamespace(data=[{"id": "c1", "definition": "Definiție"}])
        client.table.return_value.select.return_value = query

        store = ClueCanonStore(client=client)
        rows = store.fetch_clue_rows(puzzle_id="p1")

        self.assertEqual([{"id": "c1", "definition": "Definiție"}], rows)
        client.table.assert_called_with("crossword_clue_effective")
        selected = client.table.return_value.select.call_args.args[0]
        self.assertNotIn("definition_source", selected)

    def test_fetch_raw_clue_rows_reads_crossword_clues(self):
        client = MagicMock()
        query = MagicMock()
        query.order.return_value = query
        query.range.return_value = query
        query.execute.return_value = SimpleNamespace(data=[{"id": "c1", "canonical_definition_id": "canon-1"}])
        client.table.return_value.select.return_value = query

        store = ClueCanonStore(client=client)
        rows = store.fetch_raw_clue_rows()

        self.assertEqual([{"id": "c1", "canonical_definition_id": "canon-1"}], rows)
        client.table.assert_called_with("crossword_clues")

    def test_prefetch_canonical_variants_fetches_many_words_in_one_query(self):
        client = MagicMock()
        query = MagicMock()
        query.in_.return_value = query
        query.execute.return_value = SimpleNamespace(data=[
            {
                "id": "canon-1",
                "word_normalized": "APA",
                "word_original_seed": "apa",
                "definition": "Lichid vital.",
                "definition_norm": "lichid vital",
                "word_type": "",
                "usage_label": "",
                "verified": True,
                "semantic_score": 8,
                "rebus_score": 7,
                "creativity_score": 6,
                "usage_count": 3,
                "superseded_by": None,
            },
            {
                "id": "canon-2",
                "word_normalized": "SI",
                "word_original_seed": "si",
                "definition": "Conjuncție.",
                "definition_norm": "conjunctie",
                "word_type": "",
                "usage_label": "",
                "verified": True,
                "semantic_score": 7,
                "rebus_score": 7,
                "creativity_score": 5,
                "usage_count": 2,
                "superseded_by": None,
            },
        ])
        client.table.return_value.select.return_value = query
        store = ClueCanonStore(client=client)

        prefetched = store.prefetch_canonical_variants(["si", "apa"])

        query.in_.assert_called_once_with("word_normalized", ["APA", "SI"])
        self.assertEqual(["APA", "SI"], sorted(prefetched))
        self.assertEqual("Lichid vital.", prefetched["APA"][0].definition)
        self.assertEqual("Conjuncție.", store.fetch_canonical_variants("SI")[0].definition)

    def test_fetch_canonical_variants_uses_stable_fallback_order_after_reset(self):
        client = MagicMock()
        query = MagicMock()
        query.eq.return_value = query
        query.execute.return_value = SimpleNamespace(data=[
            {
                "id": "canon-z",
                "word_normalized": "LA",
                "word_original_seed": "la",
                "definition": "Zidire lexicală.",
                "definition_norm": "zidire lexicala",
                "word_type": "",
                "usage_label": "",
                "verified": False,
                "semantic_score": None,
                "rebus_score": None,
                "creativity_score": None,
                "usage_count": 0,
                "superseded_by": None,
            },
            {
                "id": "canon-a",
                "word_normalized": "LA",
                "word_original_seed": "la",
                "definition": "Abordare lexicală.",
                "definition_norm": "abordare lexicala",
                "word_type": "",
                "usage_label": "",
                "verified": False,
                "semantic_score": None,
                "rebus_score": None,
                "creativity_score": None,
                "usage_count": 0,
                "superseded_by": None,
            },
        ])
        client.table.return_value.select.return_value = query
        store = ClueCanonStore(client=client)

        rows = store.fetch_canonical_variants("LA")

        self.assertEqual(["canon-a", "canon-z"], [row.id for row in rows])

    def test_fetch_canonical_definitions_by_ids_skips_invalid_ids(self):
        client = MagicMock()
        query = MagicMock()
        query.in_.return_value = query
        query.execute.return_value = SimpleNamespace(data=[])
        client.table.return_value.select.return_value = query
        store = ClueCanonStore(client=client)

        rows = store.fetch_canonical_definitions_by_ids(["not-a-uuid", "", "123"])

        self.assertEqual({}, rows)
        query.in_.assert_not_called()

    def test_fetch_canonical_rows_by_ids_returns_full_rows(self):
        client = MagicMock()
        query = MagicMock()
        query.in_.return_value = query
        query.execute.return_value = SimpleNamespace(data=[
            {
                "id": "123e4567-e89b-12d3-a456-426614174000",
                "word_normalized": "LA",
                "word_original_seed": "la",
                "definition": "Prepoziție.",
                "definition_norm": "prepozitie",
                "word_type": "",
                "usage_label": "",
                "verified": True,
                "semantic_score": 8,
                "rebus_score": 7,
                "creativity_score": 6,
                "usage_count": 2,
                "superseded_by": None,
            }
        ])
        client.table.return_value.select.return_value = query
        store = ClueCanonStore(client=client)

        rows = store.fetch_canonical_rows_by_ids(["123e4567-e89b-12d3-a456-426614174000"])

        self.assertEqual(1, len(rows))
        self.assertEqual("LA", rows[0].word_normalized)

    def test_fetch_clue_rows_for_canonical_ids_returns_sorted_rows(self):
        client = MagicMock()
        query = MagicMock()
        query.in_.return_value = query
        query.execute.return_value = SimpleNamespace(data=[
            {
                "id": "c2",
                "canonical_definition_id": "canon-2",
                "verify_note": "",
                "verified": False,
                "definition": "Def 2",
            },
            {
                "id": "c1",
                "canonical_definition_id": "canon-1",
                "verify_note": "Scor semantic: 8/10",
                "verified": True,
                "definition": "Def 1",
            },
        ])
        client.table.return_value.select.return_value = query
        store = ClueCanonStore(client=client)

        rows = store.fetch_clue_rows_for_canonical_ids(["canon-2", "canon-1"])

        self.assertEqual(["canon-1", "canon-2"], [row["canonical_definition_id"] for row in rows])

    @patch("rebus_generator.platform.persistence.clue_canon_store.execute_logged_insert")
    def test_create_canonical_definition_recovers_from_duplicate_conflict_via_exact_db_reload(self, mock_insert):
        client = MagicMock()
        store = ClueCanonStore(client=client)
        record = SimpleNamespace(
            word_normalized="LA",
            word_original="la",
            definition="Prepoziție care indică locul.",
            definition_norm="prepozitie care indica locul",
            word_type="",
            usage_label="",
            verified=True,
            semantic_score=8,
            rebus_score=7,
            creativity_score=6,
        )
        existing = SimpleNamespace(
            id="canon-1",
            word_normalized="LA",
            definition="Prepoziție care indică locul.",
            definition_norm="prepozitie care indica locul",
            word_type="",
            usage_label="",
            verified=True,
            semantic_score=8,
            rebus_score=7,
            creativity_score=6,
            usage_count=3,
        )

        mock_insert.side_effect = APIError({"code": "23505", "message": "dup"})

        with patch.object(store, "find_exact_canonical", return_value=None), \
             patch.object(store, "find_exact_canonical_db", side_effect=[None, existing]), \
             patch.object(store, "bump_usage", return_value=existing) as bump_usage:
            created = store.create_canonical_definition(record)

        self.assertEqual(existing, created)
        bump_usage.assert_called_once_with("canon-1", "LA")


if __name__ == "__main__":
    unittest.main()
