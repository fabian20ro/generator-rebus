import unittest
from unittest.mock import MagicMock, patch

from generator.assessment.prepare_dataset import _reuse_or_fetch_dex


class ReuseOrFetchDexTests(unittest.TestCase):
    def test_prefers_live_lookup_over_existing_stale_dataset_value(self):
        provider = MagicMock()
        provider.lookup.return_value = (
            "- Definiție directă DEX pentru „FIRISOR”: Diminutiv al lui fir.\n"
            "- Sens bază pentru „fir”: Fiecare dintre elementele lungi și subțiri ale unei fibre textile."
        )
        provider.prefetch.return_value = {}
        provider.get.return_value = provider.lookup.return_value

        with patch("generator.assessment.prepare_dataset.create_provider", return_value=provider):
            result = _reuse_or_fetch_dex(
                ["FIRISOR"],
                {"FIRISOR": {"original": "firișor"}},
                {"FIRISOR": "- Diminutiv al lui fir."},
                fetch_dex=False,
            )

        self.assertIn("Sens bază pentru „fir”", result["FIRISOR"])
        provider.lookup.assert_called_once_with("FIRISOR")
        provider.prefetch.assert_not_called()
        provider.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
