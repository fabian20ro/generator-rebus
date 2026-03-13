import unittest

from generator.core.quality import filter_word_records


class QualityFilterTests(unittest.TestCase):
    def test_filter_word_records_excludes_toxic_short_loanwords(self):
        words = [
            {"normalized": "GET", "original": "get", "length": 3, "rarity_level": 2},
            {"normalized": "BIG", "original": "big", "length": 3, "rarity_level": 2},
            {"normalized": "CAT", "original": "cat", "length": 3, "rarity_level": 1},
            {"normalized": "SET", "original": "set", "length": 3, "rarity_level": 2},
            {"normalized": "VIS", "original": "vis", "length": 3, "rarity_level": 2},
            {"normalized": "ORC", "original": "orc", "length": 3, "rarity_level": 3},
        ]

        filtered = filter_word_records(words, max_rarity=3, max_length=7)
        filtered_words = {row["normalized"] for row in filtered}

        self.assertNotIn("GET", filtered_words)
        self.assertNotIn("BIG", filtered_words)
        self.assertNotIn("CAT", filtered_words)
        self.assertIn("SET", filtered_words)
        self.assertIn("VIS", filtered_words)
        self.assertIn("ORC", filtered_words)


if __name__ == "__main__":
    unittest.main()
