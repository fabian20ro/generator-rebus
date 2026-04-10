import unittest

from rebus_generator.domain.text_rules import (
    contains_normalized_forbidden_word,
    normalize_text_for_match,
    tokenize_normalized_words,
)


class TextRulesTests(unittest.TestCase):
    def test_tokenize_normalized_words_collapses_diacritics_and_case(self):
        self.assertEqual(
            ["SIR", "TARA", "2026"],
            tokenize_normalized_words("Șir Țară 2026"),
        )

    def test_normalize_text_for_match_collapses_spacing(self):
        self.assertEqual("AER CURAT", normalize_text_for_match("  aer   curat "))

    def test_detects_forbidden_word_at_min_length(self):
        self.assertTrue(
            contains_normalized_forbidden_word(
                "Munte blând",
                ["MUNTE", "AI"],
                min_length=3,
            )
        )

    def test_ignores_two_letter_words_below_min_length(self):
        self.assertFalse(
            contains_normalized_forbidden_word(
                "Ai timp",
                ["AI", "AT"],
                min_length=3,
            )
        )


if __name__ == "__main__":
    unittest.main()
