import unittest
from rebus_generator.domain.guards.definition_guards import validate_definition_text, validate_definition_text_with_details

class TestShortWordLeakGuard(unittest.TestCase):
    def test_short_word_leakage_is_rejected(self):
        self.assertEqual(validate_definition_text("SA", "Pronumele posesiv sa."), "contains answer or family word")
        self.assertEqual(validate_definition_text("DAC", "Un dac de pe columnă."), "contains answer or family word")
        self.assertEqual(validate_definition_text("OS", "Partea osoasă a corpului."), "contains answer or family word")
        self.assertEqual(validate_definition_text("OUA", "Mai multe ouă."), "contains answer or family word")
        self.assertEqual(validate_definition_text("ARDE", "Când arde ceva."), "contains answer or family word")

    def test_rejection_details_exact_answer(self):
        details = validate_definition_text_with_details("DAC", "Un dac de pe columnă.")
        self.assertIsNotNone(details)
        self.assertEqual("contains answer or family word", details.reason)
        self.assertEqual("dac", details.matched_token)
        self.assertEqual("exact_answer", details.leak_kind)

    def test_rejection_details_short_family(self):
        details = validate_definition_text_with_details("OS", "Partea osoasă a corpului.")
        self.assertIsNotNone(details)
        self.assertEqual("contains answer or family word", details.reason)
        self.assertEqual("osoasa", details.matched_token)
        self.assertEqual("short_answer_family", details.leak_kind)

    def test_short_word_without_leakage_still_passes(self):
        self.assertIsNone(validate_definition_text("OS", "Țesut dur al scheletului."))
        self.assertIsNone(validate_definition_text("OUA", "Produse de găină, în coajă."))

if __name__ == "__main__":
    unittest.main()
