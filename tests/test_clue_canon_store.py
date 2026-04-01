import unittest
from unittest.mock import MagicMock, patch

from generator.core.clue_canon_store import ClueCanonStore


class ClueCanonStoreTests(unittest.TestCase):
    @patch("generator.core.clue_canon_store.create_service_role_client", side_effect=RuntimeError("missing env"))
    def test_store_disables_cleanly_when_client_creation_fails(self, _mock_create):
        store = ClueCanonStore()

        self.assertFalse(store.is_enabled())
        self.assertEqual([], store.fetch_canonical_variants("LA"))
        self.assertIsNone(store.find_exact_canonical("LA", "prepozitie"))

    def test_build_clue_definition_payload_respects_available_columns(self):
        store = ClueCanonStore(client=MagicMock())
        with patch.object(store, "supports_canonical_definition_column", return_value=True), patch.object(
            store,
            "supports_legacy_definition_column",
            return_value=False,
        ), patch.object(store, "has_crossword_clues_column", side_effect=lambda column: column in {"verify_note", "verified"}):
            payload = store.build_clue_definition_payload(
                definition="Definiție canonică",
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

    def test_hydrate_clue_definitions_prefers_canonical_then_legacy(self):
        store = ClueCanonStore(client=MagicMock())
        rows = [
            {"id": "c1", "canonical_definition_id": "canon-1"},
            {"id": "c2", "canonical_definition_id": None},
        ]
        with patch.object(store, "fetch_canonical_definitions_by_ids", return_value={"canon-1": "Definiție canonică"}), patch.object(
            store,
            "supports_legacy_definition_column",
            return_value=True,
        ), patch.object(store, "_fetch_legacy_definitions", return_value={"c2": "Definiție legacy"}):
            hydrated = store.hydrate_clue_definitions(rows, puzzle_id="p1")

        self.assertEqual("Definiție canonică", hydrated[0]["definition"])
        self.assertEqual("canonical", hydrated[0]["definition_source"])
        self.assertEqual("Definiție legacy", hydrated[1]["definition"])
        self.assertEqual("legacy", hydrated[1]["definition_source"])


if __name__ == "__main__":
    unittest.main()
