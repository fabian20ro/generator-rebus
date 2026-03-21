import unittest
from unittest.mock import MagicMock, patch

from generator.assessment.prepare_dataset import (
    CURATED_DATASET_TIERS,
    _build_curated_entries,
    _reuse_or_fetch_dex,
)


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


class CuratedDatasetTests(unittest.TestCase):
    def test_curated_dataset_has_exact_requested_membership(self):
        words_meta = {}
        for tier, words in CURATED_DATASET_TIERS.items():
            for word in words:
                words_meta[word] = {
                    "original": word.lower(),
                    "length": len(word),
                    "word_type": "",
                }

        provider = MagicMock()
        provider.lookup.return_value = ""

        with patch("generator.assessment.prepare_dataset.create_provider", return_value=provider):
            entries = _build_curated_entries(
                curated_tiers=CURATED_DATASET_TIERS,
                words_meta=words_meta,
                existing_dex={},
                fetch_dex=False,
            )

        self.assertEqual(70, len(entries))
        self.assertEqual(CURATED_DATASET_TIERS["low"], [e.word for e in entries if e.tier == "low"])
        self.assertEqual(CURATED_DATASET_TIERS["medium"], [e.word for e in entries if e.tier == "medium"])
        self.assertEqual(CURATED_DATASET_TIERS["high"], [e.word for e in entries if e.tier == "high"])

    def test_curated_dataset_raises_on_missing_word_metadata(self):
        words_meta = {
            word: {"original": word.lower(), "length": len(word), "word_type": ""}
            for word in CURATED_DATASET_TIERS["low"][:-1]
        }
        provider = MagicMock()
        provider.lookup.return_value = ""

        with patch("generator.assessment.prepare_dataset.create_provider", return_value=provider):
            with self.assertRaises(KeyError):
                _build_curated_entries(
                    curated_tiers={"low": CURATED_DATASET_TIERS["low"], "medium": [], "high": []},
                    words_meta=words_meta,
                    existing_dex={},
                    fetch_dex=False,
                )


if __name__ == "__main__":
    unittest.main()
