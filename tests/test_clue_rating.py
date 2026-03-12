import unittest

from generator.core.clue_rating import (
    append_rating_to_note,
    extract_feedback,
    extract_guessability_score,
    extract_semantic_score,
    extract_wrong_guess,
)


class ClueRatingTests(unittest.TestCase):
    def test_append_and_extract_scores(self):
        note = append_rating_to_note(
            "AI a ghicit: BARIL",
            semantic_score=8,
            guessability_score=4,
            feedback="duce spre un sinonim mai comun",
        )

        self.assertEqual(8, extract_semantic_score(note))
        self.assertEqual(4, extract_guessability_score(note))
        self.assertEqual("duce spre un sinonim mai comun", extract_feedback(note))
        self.assertEqual("BARIL", extract_wrong_guess(note))

    def test_extractors_handle_missing_note(self):
        self.assertIsNone(extract_semantic_score(""))
        self.assertIsNone(extract_guessability_score(""))
        self.assertEqual("", extract_feedback(""))
        self.assertEqual("", extract_wrong_guess(""))


if __name__ == "__main__":
    unittest.main()
