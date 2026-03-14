import json
import unittest
from types import SimpleNamespace

from generator.core.ai_clues import (
    DefinitionRating,
    _clean_response,
    _definition_describes_english_meaning,
    _build_generate_prompt,
    rate_definition,
    rewrite_definition,
)


class _RecordingClient:
    def __init__(self, responses):
        self.prompts = []
        queue = list(responses)

        def _create(**kwargs):
            self.prompts.append(kwargs["messages"][-1]["content"])
            content = queue.pop(0)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=_create
            )
        )


class AiCluesTests(unittest.TestCase):
    def test_rewrite_prompt_omits_bad_example_by_default(self):
        client = _RecordingClient(["Definiție mai bună"])

        rewrite_definition(
            client,
            word="ARACI",
            original="araci",
            theme="",
            previous_definition="Prezintă un fapt în mod clar și convingător.",
            wrong_guess="",
            rating_feedback="Prea vagă pentru răspunsul exact.",
        )

        self.assertNotIn("Exemplu de definiție rea de evitat", client.prompts[0])

    def test_rewrite_prompt_includes_bad_example_when_provided(self):
        client = _RecordingClient(["Definiție mai bună"])

        rewrite_definition(
            client,
            word="ARACI",
            original="araci",
            theme="",
            previous_definition="Prezintă un fapt în mod clar și convingător.",
            wrong_guess="ALTUL",
            rating_feedback="Duce la alt răspuns.",
            bad_example_definition="Prezintă un fapt în mod clar și convingător.",
            bad_example_reason="Duce la alt răspuns: ALTUL.",
        )

        prompt = client.prompts[0]
        self.assertIn("Exemplu de definiție rea de evitat", prompt)
        self.assertIn("Duce la alt răspuns: ALTUL.", prompt)

    def test_rate_definition_does_not_penalize_rarity_only_feedback(self):
        client = _RecordingClient([
            json.dumps({
                "semantic_score": 9,
                "guessability_score": 4,
                "feedback": "Răspunsul este rar și mai puțin comun.",
            })
        ])

        rating = rate_definition(
            client,
            word="ARACI",
            original="araci",
            definition="Bețe de sprijin pentru viță",
            answer_length=5,
        )

        self.assertEqual(9, rating.semantic_score)
        self.assertEqual(4, rating.guessability_score)
        self.assertTrue(rating.rarity_only_override)

    def test_rate_definition_keeps_legitimate_guessability_penalty(self):
        client = _RecordingClient([
            json.dumps({
                "semantic_score": 9,
                "guessability_score": 4,
                "feedback": "Definiția duce la un sinonim mai uzual.",
            })
        ])

        rating = rate_definition(
            client,
            word="ARACI",
            original="araci",
            definition="Bețe de sprijin pentru viță",
            answer_length=5,
        )

        self.assertEqual(4, rating.guessability_score)
        self.assertIn("sinonim", rating.feedback)


    def test_clean_response_strips_model_tokens(self):
        self.assertEqual("ZI", _clean_response("<|channel|>ZI"))
        self.assertEqual("", _clean_response("<|channel|>"))
        self.assertEqual("AER", _clean_response("<|endoftext|>AER"))
        self.assertEqual("CASA", _clean_response("CASA<|im_end|>"))

    def test_clean_response_takes_first_line(self):
        self.assertEqual("CASA", _clean_response("CASA\naltceva pe linia doi"))

    def test_definition_describes_english_meaning_detects_engleza(self):
        self.assertTrue(_definition_describes_english_meaning("AN", "Articol nehotărât în limba engleză"))

    def test_definition_describes_english_meaning_detects_patterns(self):
        self.assertTrue(_definition_describes_english_meaning("AN", "Articol nehotărât"))
        self.assertTrue(_definition_describes_english_meaning("OF", "Prepoziție de posesie"))
        self.assertTrue(_definition_describes_english_meaning("IN", "Prepoziție care indică poziția"))
        self.assertTrue(_definition_describes_english_meaning("HAT", "O pălărie mare"))
        self.assertTrue(_definition_describes_english_meaning("NAT", "Traducere a adreselor IP"))

    def test_definition_describes_english_meaning_passes_romanian(self):
        self.assertFalse(_definition_describes_english_meaning("AN", "Unitate de timp egală cu 12 luni"))
        self.assertFalse(_definition_describes_english_meaning("OF", "Interjecție de durere"))
        self.assertFalse(_definition_describes_english_meaning("IN", "Plantă textilă cu flori albastre"))
        self.assertFalse(_definition_describes_english_meaning("CASA", "Locuință"))

    def test_generate_prompt_includes_homograph_hint(self):
        prompt = _build_generate_prompt("an", "AN", 2)
        self.assertIn("ATENȚIE", prompt)
        self.assertIn("unitate de timp", prompt)
        self.assertIn("NU defini ca și cum ar fi un cuvânt englezesc", prompt)

    def test_generate_prompt_no_hint_for_normal_word(self):
        prompt = _build_generate_prompt("casă", "CASA", 4)
        self.assertNotIn("ATENȚIE", prompt)

    def test_rate_english_meaning_forces_low_scores(self):
        client = _RecordingClient([
            json.dumps({
                "semantic_score": 10,
                "guessability_score": 10,
                "feedback": "Definiția este perfectă.",
            })
        ])

        rating = rate_definition(
            client,
            word="AN",
            original="an",
            definition="Articol nehotărât în limba engleză",
            answer_length=2,
        )

        self.assertEqual(1, rating.semantic_score)
        self.assertEqual(1, rating.guessability_score)


if __name__ == "__main__":
    unittest.main()
