import json
import unittest
from types import SimpleNamespace

from generator.core.ai_clues import (
    DefinitionRating,
    _clean_response,
    _definition_describes_english_meaning,
    _build_generate_prompt,
    compute_rebus_score,
    generate_definition,
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
                "creativity_score": 6,
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
                "creativity_score": 5,
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
        prompt = _build_generate_prompt("aer", "AER", 3)
        self.assertNotIn("ATENȚIE", prompt)

    def test_rate_english_meaning_forces_low_scores(self):
        client = _RecordingClient([
            json.dumps({
                "semantic_score": 10,
                "guessability_score": 10,
                "creativity_score": 10,
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


    def test_generate_prompt_includes_forbidden_stems(self):
        prompt = _build_generate_prompt("tibetan", "TIBETAN", 7)
        self.assertIn("TIBET", prompt)
        self.assertIn("interzise", prompt)

    def test_generate_prompt_forbidden_for_short_word(self):
        prompt = _build_generate_prompt("at", "AT", 2)
        self.assertNotIn("interzise", prompt)

    def test_compute_rebus_score_formula(self):
        self.assertEqual(7, compute_rebus_score(6, 10))
        self.assertEqual(5, compute_rebus_score(5, 5))
        self.assertEqual(8, compute_rebus_score(8, 8))

    def test_rate_definition_returns_creativity_score(self):
        client = _RecordingClient([
            json.dumps({
                "semantic_score": 8,
                "guessability_score": 7,
                "creativity_score": 9,
                "feedback": "surpriză de domeniu",
            })
        ])

        rating = rate_definition(
            client,
            word="RIAL",
            original="rial",
            definition="Se plătește la șah",
            answer_length=4,
        )

        self.assertEqual(9, rating.creativity_score)
        self.assertEqual(8, rating.semantic_score)
        self.assertEqual(7, rating.guessability_score)


    def test_generate_prompt_includes_word_type(self):
        prompt = _build_generate_prompt("lovi", "LOVI", 4, word_type="V")
        self.assertIn("Categorie gramaticală: verb", prompt)

    def test_generate_prompt_no_word_type_for_empty(self):
        prompt = _build_generate_prompt("casă", "CASA", 4, word_type="")
        self.assertNotIn("Categorie gramaticală", prompt)

    def test_generate_prompt_no_word_type_for_unknown(self):
        prompt = _build_generate_prompt("casă", "CASA", 4, word_type="X")
        self.assertNotIn("Categorie gramaticală", prompt)

    def test_rewrite_prompt_includes_word_type(self):
        from generator.core.ai_clues import _build_rewrite_prompt
        prompt = _build_rewrite_prompt(
            "lovi", "LOVI", "A atinge cu forță", "[niciun feedback]", "", word_type="V",
        )
        self.assertIn("Categorie gramaticală: verb", prompt)

    def test_rate_prompt_includes_word_type(self):
        from generator.core.ai_clues import _build_rate_prompt
        prompt = _build_rate_prompt("casă", "CASA", "Locuință", 4, word_type="N")
        self.assertIn("Categorie gramaticală: substantiv", prompt)

    def test_rate_definition_returns_none_on_unparseable_response(self):
        """When JSON parsing fails, rate_definition returns None (not 5/5/5)."""
        client = _RecordingClient([
            "This is not JSON at all",
            "Still not JSON",
        ])

        rating = rate_definition(
            client,
            word="CASA",
            original="casă",
            definition="Locuință",
            answer_length=4,
        )

        self.assertIsNone(rating)

    def test_rate_definition_extracts_json_from_markdown_fence(self):
        """eurollm-22b wraps JSON in ```json ... ``` — must still parse."""
        fenced_json = (
            '```json\n'
            '{"semantic_score": 9, "guessability_score": 8, '
            '"creativity_score": 7, "feedback": "Corect"}\n'
            '```'
        )
        client = _RecordingClient([fenced_json])

        rating = rate_definition(
            client,
            word="CASA",
            original="casă",
            definition="Locuință",
            answer_length=4,
        )

        self.assertIsNotNone(rating)
        self.assertEqual(9, rating.semantic_score)
        self.assertEqual(8, rating.guessability_score)
        self.assertEqual(7, rating.creativity_score)

    def test_rewrite_prompt_includes_failure_history(self):
        """Failure history should appear as numbered list in the prompt."""
        client = _RecordingClient(["Parte a conjugării verbale"])

        rewrite_definition(
            client,
            word="AR",
            original="ar",
            theme="",
            previous_definition="Verb auxiliar",
            wrong_guess="DE",
            failure_history=[
                ("Condiționare", "DE"),
                ("Auxiliar verbal", "AL"),
                ("Verb auxiliar", "DE"),
            ],
        )

        prompt = client.prompts[0]
        self.assertIn("Încercări anterioare eșuate", prompt)
        self.assertIn("'Condiționare' → ghicit: DE", prompt)
        self.assertIn("'Auxiliar verbal' → ghicit: AL", prompt)

    def test_rewrite_prompt_omits_history_when_none(self):
        """No history section when failure_history is None."""
        client = _RecordingClient(["Definiție mai bună"])

        rewrite_definition(
            client,
            word="CASA",
            original="casă",
            theme="",
            previous_definition="Locuință",
            wrong_guess="",
        )

        self.assertNotIn("Încercări anterioare eșuate", client.prompts[0])


if __name__ == "__main__":
    unittest.main()
