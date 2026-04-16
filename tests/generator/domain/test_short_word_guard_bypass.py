import unittest
from rebus_generator.domain.guards.definition_guards import validate_definition_text

class TestShortWordGuardBypass(unittest.TestCase):
    def test_short_word_bypass(self):
        # Length 2: self-mention allowed
        self.assertIsNone(validate_definition_text("SA", "Pronumele posesiv sa."))
        
        # Length 3: self-mention allowed
        self.assertIsNone(validate_definition_text("DAC", "Un dac de pe columnă."))
        
        # Length 4: self-mention NOT allowed
        self.assertEqual(validate_definition_text("ARDE", "Când arde ceva."), "contains answer or family word")

        # Length 2: family word allowed
        self.assertIsNone(validate_definition_text("OS", "Partea osoasă a corpului."))

if __name__ == "__main__":
    unittest.main()
