import unittest
from unittest.mock import patch

from generator.core.clue_canon_store import ClueCanonStore


class ClueCanonStoreTests(unittest.TestCase):
    @patch("generator.core.clue_canon_store.create_service_role_client", side_effect=RuntimeError("missing env"))
    def test_store_disables_cleanly_when_client_creation_fails(self, _mock_create):
        store = ClueCanonStore()

        self.assertFalse(store.is_enabled())
        self.assertEqual([], store.fetch_canonical_variants("LA"))
        self.assertIsNone(store.find_exact_canonical("LA", "prepozitie"))


if __name__ == "__main__":
    unittest.main()
