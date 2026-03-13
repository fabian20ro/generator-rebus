import unittest

from generator.core.clue_family import clue_uses_same_family


class ClueFamilyTests(unittest.TestCase):
    def test_same_family_variants_are_rejected(self):
        self.assertTrue(clue_uses_same_family("NATURAL", "Formă naturală a lucrurilor"))
        self.assertTrue(clue_uses_same_family("NATURAL", "La plural: naturale"))
        self.assertTrue(clue_uses_same_family("NATURAL", "Stare de naturalețe"))

    def test_unrelated_words_do_not_overfire(self):
        self.assertFalse(clue_uses_same_family("NATURAL", "Care ține de firea omului"))
        self.assertFalse(clue_uses_same_family("MARE", "Întindere cu apă sărată"))


if __name__ == "__main__":
    unittest.main()
