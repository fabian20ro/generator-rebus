import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from postgrest.exceptions import APIError

from generator.core.clue_canon_store import ClueCanonStore


class ClueCanonStoreTests(unittest.TestCase):
    @patch("generator.core.clue_canon_store.create_service_role_client", side_effect=RuntimeError("missing env"))
    def test_store_disables_cleanly_when_client_creation_fails(self, _mock_create):
        store = ClueCanonStore()

        self.assertFalse(store.is_enabled())
        self.assertEqual([], store.fetch_canonical_variants("LA"))
        self.assertIsNone(store.find_exact_canonical("LA", "prepozitie"))

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

    def test_fetch_clue_rows_reads_effective_view(self):
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

    def test_fetch_backfill_source_rows_filters_unresolved_and_word(self):
        client = MagicMock()
        query = MagicMock()
        query.eq.return_value = query
        query.is_.return_value = query
        query.range.return_value = query
        query.execute.return_value = SimpleNamespace(data=[{"id": "c1", "word_normalized": "APA"}])
        client.table.return_value.select.return_value = query

        store = ClueCanonStore(client=client)
        rows = store.fetch_backfill_source_rows(word_normalized="apa")

        self.assertEqual([{"id": "c1", "word_normalized": "APA"}], rows)
        self.assertIn(("word_normalized", "APA"), [call.args for call in query.eq.call_args_list])
        query.is_.assert_called_once_with("canonical_definition_id", "null")

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
            },
        ])
        client.table.return_value.select.return_value = query
        store = ClueCanonStore(client=client)

        with patch.object(store, "is_enabled", return_value=True):
            prefetched = store.prefetch_canonical_variants(["si", "apa"])

        query.in_.assert_called_once_with("word_normalized", ["APA", "SI"])
        self.assertEqual(["APA", "SI"], sorted(prefetched))
        self.assertEqual("Lichid vital.", prefetched["APA"][0].definition)
        self.assertEqual("Conjuncție.", store.fetch_canonical_variants("SI")[0].definition)

    @patch("generator.core.clue_canon_store.execute_logged_insert")
    def test_insert_aliases_dedupes_and_bulk_inserts_once(self, mock_insert):
        client = MagicMock()
        query = MagicMock()
        query.eq.return_value = query
        query.in_.return_value = query
        query.execute.return_value = SimpleNamespace(data=[{"definition_norm": "existing"}])
        client.table.return_value.select.return_value = query
        store = ClueCanonStore(client=client)

        with patch.object(store, "is_enabled", return_value=True):
            batches = store.insert_aliases(
                canonical_definition_id="canon-1",
                word_normalized="apa",
                aliases=[
                    {
                        "definition": "Existing",
                        "definition_norm": "existing",
                        "source_clue_id": "c1",
                        "match_type": "exact",
                        "same_meaning_votes": None,
                        "winner_votes": None,
                        "decision_source": "heuristic",
                        "decision_note": "",
                    },
                    {
                        "definition": "Nou",
                        "definition_norm": "nou",
                        "source_clue_id": "c2",
                        "match_type": "near",
                        "same_meaning_votes": 6,
                        "winner_votes": 6,
                        "decision_source": "llm",
                        "decision_note": "merged",
                    },
                    {
                        "definition": "Nou duplicat",
                        "definition_norm": "nou",
                        "source_clue_id": "c3",
                        "match_type": "near",
                        "same_meaning_votes": 6,
                        "winner_votes": 6,
                        "decision_source": "llm",
                        "decision_note": "merged",
                    },
                ],
            )

        self.assertEqual(1, batches)
        mock_insert.assert_called_once()
        inserted_rows = mock_insert.call_args.args[2]
        self.assertEqual(1, len(inserted_rows))
        self.assertEqual("nou", inserted_rows[0]["definition_norm"])

    def test_fetch_canonical_definitions_by_ids_skips_invalid_ids(self):
        client = MagicMock()
        query = MagicMock()
        query.in_.return_value = query
        query.execute.return_value = SimpleNamespace(data=[])
        client.table.return_value.select.return_value = query
        store = ClueCanonStore(client=client)
        with patch.object(store, "is_enabled", return_value=True):
            rows = store.fetch_canonical_definitions_by_ids(["not-a-uuid", "", "123"])

        self.assertEqual({}, rows)
        query.in_.assert_not_called()

    @patch("generator.core.clue_canon_store.execute_logged_insert")
    def test_create_canonical_definition_recovers_from_duplicate_conflict(self, mock_insert):
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

        with patch.object(store, "is_enabled", return_value=True), \
             patch.object(store, "find_exact_canonical", side_effect=[None, existing]), \
             patch.object(store, "bump_usage", return_value=existing) as bump_usage, \
             patch.object(store, "fetch_canonical_variants", return_value=[existing]):
            created = store.create_canonical_definition(record)

        self.assertEqual(existing, created)
        bump_usage.assert_called_once_with("canon-1", "LA")


if __name__ == "__main__":
    unittest.main()
