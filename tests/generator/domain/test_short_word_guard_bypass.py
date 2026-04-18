import unittest
from rebus_generator.domain.guards.definition_guards import validate_definition_text

class TestShortWordLeakGuard(unittest.TestCase):
    def test_short_word_leakage_is_rejected(self):
        self.assertEqual(validate_definition_text("SA", "Pronumele posesiv sa."), "contains answer or family word")
        self.assertEqual(validate_definition_text("DAC", "Un dac de pe columnă."), "contains answer or family word")
        self.assertEqual(validate_definition_text("OS", "Partea osoasă a corpului."), "contains answer or family word")
        self.assertEqual(validate_definition_text("OUA", "Mai multe ouă."), "contains answer or family word")
        self.assertEqual(validate_definition_text("ARDE", "Când arde ceva."), "contains answer or family word")

    def test_short_word_without_leakage_still_passes(self):
        self.assertIsNone(validate_definition_text("OS", "Țesut dur al scheletului."))
        self.assertIsNone(validate_definition_text("OUA", "Produse de găină, în coajă."))

if __name__ == "__main__":
    unittest.main()
