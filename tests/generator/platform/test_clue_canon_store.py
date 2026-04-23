import unittest
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

from postgrest.exceptions import APIError

from rebus_generator.platform.persistence.clue_canon_store import ClueCanonStore


class _CleanupQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.operation = ""
        self.payload = None
        self.in_filters: list[tuple[str, list[str]]] = []

    def select(self, *_args, **_kwargs):
        self.operation = "select"
        return self

    def update(self, payload):
        self.operation = "update"
        self.payload = payload
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def in_(self, field, values):
        self.in_filters.append((field, list(values)))
        return self

    def execute(self):
        self.client.operations.append((self.table_name, self.operation, list(self.in_filters), self.payload))
        field, values = self.in_filters[-1] if self.in_filters else ("", [])
        if self.table_name == "canonical_clue_definitions" and self.operation == "select":
            if self.client.canonical_rows:
                return SimpleNamespace(data=[
                    row for row in self.client.canonical_rows
                    if str(row.get(field) or "") in values
                ])
            return SimpleNamespace(data=[
                {"id": value, "word_normalized": f"WORD{index}"}
                for index, value in enumerate(values)
            ])
        if self.table_name == "crossword_clues" and self.operation == "select":
            return SimpleNamespace(data=[
                {"canonical_definition_id": value}
                for value in values
                if value in self.client.referenced_ids
            ])
        if self.table_name == "canonical_clue_definitions" and self.operation == "delete":
            return SimpleNamespace(data=[
                {"id": value}
                for value in values
                if value not in self.client.referenced_ids
            ])
        return SimpleNamespace(data=[])


class _CleanupClient:
    def __init__(self, *, referenced_ids: set[str], canonical_rows: list[dict] | None = None):
        self.referenced_ids = referenced_ids
        self.canonical_rows = list(canonical_rows or [])
        self.operations: list[tuple[str, str, list[tuple[str, list[str]]], object]] = []

    def table(self, name):
        return _CleanupQuery(self, name)


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

    def test_delete_unreferenced_canonicals_by_ids_deletes_only_unreferenced(self):
        client = _CleanupClient(referenced_ids={"canon-referenced"})
        store = ClueCanonStore(client=client)

        deleted = store.delete_unreferenced_canonicals_by_ids([
            "canon-free",
            "canon-referenced",
            "",
        ])

        self.assertEqual(1, deleted)
        self.assertIn(
            (
                "canonical_clue_definitions",
                "update",
                [("superseded_by", ["canon-free"])],
                {"superseded_by": None, "updated_at": ANY},
            ),
            client.operations,
        )
        self.assertIn(
            (
                "canonical_clue_definitions",
                "update",
                [("id", ["canon-free"])],
                {"superseded_by": None, "updated_at": ANY},
            ),
            client.operations,
        )
        self.assertIn(
            (
                "canonical_clue_definitions",
                "delete",
                [("id", ["canon-free"])],
                None,
            ),
            client.operations,
        )

    def test_delete_redundant_unreferenced_canonicals_keeps_best_fallback(self):
        client = _CleanupClient(
            referenced_ids=set(),
            canonical_rows=[
                {
                    "id": "canon-best",
                    "word_normalized": "APA",
                    "definition": "best",
                    "word_type": "",
                    "usage_label": "",
                    "verified": True,
                    "semantic_score": 9,
                    "rebus_score": 8,
                    "creativity_score": 6,
                    "usage_count": 2,
                    "superseded_by": None,
                    "updated_at": "2026-04-02T00:00:00+00:00",
                },
                {
                    "id": "canon-worse",
                    "word_normalized": "APA",
                    "definition": "worse",
                    "word_type": "",
                    "usage_label": "",
                    "verified": True,
                    "semantic_score": 6,
                    "rebus_score": 5,
                    "creativity_score": 4,
                    "usage_count": 1,
                    "superseded_by": None,
                    "updated_at": "2026-04-01T00:00:00+00:00",
                },
            ],
        )
        store = ClueCanonStore(client=client)

        deleted = store.delete_redundant_unreferenced_canonicals_by_ids(["canon-best", "canon-worse"])

        self.assertEqual(1, deleted)
        self.assertIn(
            (
                "canonical_clue_definitions",
                "delete",
                [("id", ["canon-worse"])],
                None,
            ),
            client.operations,
        )
        self.assertNotIn(
            (
                "canonical_clue_definitions",
                "delete",
                [("id", ["canon-best"])],
                None,
            ),
            client.operations,
        )


if __name__ == "__main__":
    unittest.main()
