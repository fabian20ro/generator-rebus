import unittest

from generator.core.clue_family import clue_uses_same_family, forbidden_definition_stems


class ClueFamilyTests(unittest.TestCase):
    def test_same_family_variants_are_rejected(self):
        self.assertTrue(clue_uses_same_family("NATURAL", "Formă naturală a lucrurilor"))
        self.assertTrue(clue_uses_same_family("NATURAL", "La plural: naturale"))
        self.assertTrue(clue_uses_same_family("NATURAL", "Stare de naturalețe"))

    def test_unrelated_words_do_not_overfire(self):
        self.assertFalse(clue_uses_same_family("NATURAL", "Care ține de firea omului"))
        self.assertFalse(clue_uses_same_family("MARE", "Întindere cu apă sărată"))

    def test_prefix_family_neinceput_catches_inceput(self):
        self.assertTrue(clue_uses_same_family("NEINCEPUT", "Un nou început"))

    def test_prefix_family_reinceput_catches_inceput(self):
        self.assertTrue(clue_uses_same_family("REINCEPUT", "Un nou început"))

    def test_prefix_family_postbelic_catches_belic(self):
        self.assertTrue(clue_uses_same_family("POSTBELIC", "Perioadă belică"))

    def test_prefix_stripping_skips_short_remainder(self):
        # "rece" starts with "re" but remainder "ce" is only 2 chars — should NOT strip
        self.assertFalse(clue_uses_same_family("RECE", "Temperatură joasă"))

    def test_no_false_positive_substanta_vs_distanta(self):
        # Both words happen to share "stanta" after bogus prefix stripping,
        # but neither is actually prefixed — should NOT trigger.
        self.assertFalse(clue_uses_same_family("SUBSTANTA", "O distanta mare"))

    def test_forbidden_stems_tibetan(self):
        self.assertEqual(forbidden_definition_stems("TIBETAN"), ["TIBET", "TIBETAN"])

    def test_forbidden_stems_neinceput(self):
        self.assertEqual(forbidden_definition_stems("NEINCEPUT"), ["INCEPUT", "NEINCEPUT"])

    def test_forbidden_stems_short_word(self):
        self.assertEqual(forbidden_definition_stems("AT"), [])


if __name__ == "__main__":
    unittest.main()
