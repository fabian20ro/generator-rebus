import unittest

from rebus_generator.domain.clue_rating import (
    append_rating_to_note,
    extract_creativity_score,
    extract_feedback,
    extract_guessability_score,
    extract_rebus_score,
    extract_semantic_score,
    extract_wrong_guess,
)


class ClueRatingTests(unittest.TestCase):
    def test_append_and_extract_scores_with_rebus(self):
        note = append_rating_to_note(
            "AI a ghicit: BARIL",
            semantic_score=8,
            guessability_score=4,
            feedback="duce spre un sinonim mai comun",
            creativity_score=7,
            rebus_score=5,
        )

        self.assertEqual(8, extract_semantic_score(note))
        self.assertEqual(5, extract_rebus_score(note))
        self.assertEqual(7, extract_creativity_score(note))
        self.assertEqual("duce spre un sinonim mai comun", extract_feedback(note))
        self.assertEqual("BARIL", extract_wrong_guess(note))
        self.assertIn("Scor rebus: 5/10", note)
        self.assertIn("Scor creativitate: 7/10", note)

    def test_backward_compat_old_guessability_label(self):
        old_note = "Scor semantic: 8/10 | Scor ghicibilitate: 6/10 | bun"

        self.assertEqual(8, extract_semantic_score(old_note))
        self.assertEqual(6, extract_guessability_score(old_note))
        self.assertEqual(6, extract_rebus_score(old_note))
        self.assertIsNone(extract_creativity_score(old_note))
        self.assertEqual("bun", extract_feedback(old_note))

    def test_append_without_creativity_uses_guessability_label(self):
        note = append_rating_to_note(
            "",
            semantic_score=9,
            guessability_score=7,
            feedback="clară",
        )

        self.assertIn("Scor ghicibilitate: 7/10", note)
        self.assertNotIn("Scor rebus:", note)
        self.assertNotIn("Scor creativitate:", note)

    def test_extractors_handle_missing_note(self):
        self.assertIsNone(extract_semantic_score(""))
        self.assertIsNone(extract_guessability_score(""))
        self.assertIsNone(extract_rebus_score(""))
        self.assertIsNone(extract_creativity_score(""))
        self.assertEqual("", extract_feedback(""))
        self.assertEqual("", extract_wrong_guess(""))

    def test_extract_feedback_filters_new_labels(self):
        note = (
            "AI a ghicit: BARIL | Scor semantic: 8/10 | "
            "Scor rebus: 5/10 | Scor creativitate: 7/10 | feedback util"
        )

        self.assertEqual("feedback util", extract_feedback(note))


if __name__ == "__main__":
    unittest.main()
