import json
import unittest
from types import SimpleNamespace

from generator.phases.theme import (
    FALLBACK_TITLES,
    TITLE_MIN_CREATIVITY,
    _sanitize_title,
    generate_creative_title,
    generate_title_for_final_puzzle,
    generate_title_from_words,
    generate_title_from_words_and_definitions,
    rate_title_creativity,
)


class _FakeClient:
    def __init__(self, content):
        self.last_user_content = ""

        def _create(**kwargs):
            self.last_user_content = kwargs["messages"][-1]["content"]
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=_create
            )
        )


class _SequentialClient:
    """Returns a different response for each call."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self._index = 0
        self.last_user_content = ""

        def _create(**kwargs):
            self.last_user_content = kwargs["messages"][-1]["content"]
            content = self._responses[min(self._index, len(self._responses) - 1)]
            self._index += 1
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=_create
            )
        )


def _fake_rate_client(score: int, feedback: str = "ok"):
    """Create a client that returns a fixed creativity rating."""
    return _FakeClient(json.dumps({"creativity_score": score, "feedback": feedback}))


class ThemeTests(unittest.TestCase):
    def test_generate_title_uses_model_output_when_valid(self):
        title = generate_title_from_words(
            ["AUR", "ARGINT", "BRONZ"],
            client=_FakeClient("Metale și Ecouri"),
        )

        self.assertEqual("Metale și Ecouri", title)

    def test_generate_title_rejects_redundant_rebus_title(self):
        title = generate_title_from_words(
            ["AUR", "ARGINT", "BRONZ"],
            client=_FakeClient("Rebus Românesc"),
        )

        self.assertNotIn("Rebus", title)
        self.assertNotIn("Românesc", title)
        self.assertTrue(title)

    def test_generate_title_truncates_very_long_model_output(self):
        title = generate_title_from_words(
            ["AUR", "ARGINT", "BRONZ"],
            client=_FakeClient("Acesta este foarte lung dar util pentru test"),
        )

        self.assertEqual(4, len(title.split()))

    def test_final_title_generation_uses_definitions_context(self):
        client = _FakeClient("Ecouri de Toamnă")
        rate_client = _fake_rate_client(8)

        title = generate_title_from_words_and_definitions(
            ["NATURA", "FRUNZA"],
            ["Frunză uscată de toamnă", "Ce ține de lumea vie"],
            client=client,
        )

        self.assertEqual("Ecouri de Toamnă", title)

    def test_final_title_prompt_uses_definitions(self):
        words = [
            "EXTRAORDINAR",
            "SPECTACOL",
            "MUNTE",
            "FOC",
        ]
        clues = [
            SimpleNamespace(word_normalized=w, definition=f"Definiția {w}")
            for w in words
        ]
        puzzle = SimpleNamespace(
            horizontal_clues=clues,
            vertical_clues=[],
        )
        gen_client = _FakeClient("Univers Creativ")
        rate_client = _fake_rate_client(8)

        generate_title_for_final_puzzle(puzzle, client=gen_client, rate_client=rate_client)

        prompt = gen_client.last_user_content
        # Prompt should contain definitions, not raw word list
        self.assertIn("Definiția EXTRAORDINAR", prompt)
        self.assertIn("Definiția MUNTE", prompt)
        # Words should NOT appear as a comma-separated list
        self.assertNotIn("EXTRAORDINAR, SPECTACOL", prompt)

    def test_final_title_passes_all_words(self):
        words = [
            "EXTRAORDINAR",
            "SPECTACOL",
            "PLIMBARE",
            "GALAXIE",
            "TABLOU",
            "VERDE",
            "MUNTE",
            "ARTA",
        ]
        clues = [
            SimpleNamespace(word_normalized=w, definition=f"Definiția {w}")
            for w in words
        ]
        puzzle = SimpleNamespace(
            horizontal_clues=clues,
            vertical_clues=[],
        )
        gen_client = _FakeClient("Univers Creativ")
        rate_client = _fake_rate_client(8)

        generate_title_for_final_puzzle(puzzle, client=gen_client, rate_client=rate_client)

        prompt = gen_client.last_user_content
        # All definitions should be in the prompt
        for w in words:
            self.assertIn(f"Definiția {w}", prompt)

    def test_sanitize_rejects_title_containing_two_input_words(self):
        result = _sanitize_title(
            "Munte și Plimbare",
            words=["MUNTE", "PLIMBARE"],
            input_words=["MUNTE", "PLIMBARE"],
        )
        self.assertIn(result, FALLBACK_TITLES)

    def test_sanitize_case_insensitive_word_check(self):
        # Two 4+ char words both present → rejected
        result = _sanitize_title(
            "Munte și Verde",
            words=["munte", "verde"],
            input_words=["munte", "verde"],
        )
        self.assertIn(result, FALLBACK_TITLES)

    def test_sanitize_diacritics_normalized_word_check(self):
        # Two diacritical words both match after normalization → rejected
        result = _sanitize_title(
            "Față și Țară",
            words=["FATA", "TARA"],
            input_words=["FATA", "TARA"],
        )
        self.assertIn(result, FALLBACK_TITLES)

    def test_rate_title_creativity_parses_json(self):
        client = _FakeClient('{"creativity_score": 7, "feedback": "bun titlu"}')
        score, feedback = rate_title_creativity("Test", ["A", "B"], client)
        self.assertEqual(7, score)
        self.assertEqual("bun titlu", feedback)

    def test_creative_loop_accepts_high_score(self):
        gen_client = _FakeClient("Orizont Aprins")
        rate_client = _fake_rate_client(7)

        title = generate_creative_title(
            ["AER", "MUNTE"],
            ["Gaz din atmosferă", "Formă de relief"],
            client=gen_client,
            rate_client=rate_client,
        )

        self.assertEqual("Orizont Aprins", title)

    def test_creative_loop_retries_on_low_score(self):
        gen_responses = [
            "Ecou Banal",
            "Alt Ecou",
            "Ecou Fabulos",
        ]
        rate_responses = [
            json.dumps({"creativity_score": 3, "feedback": "generic"}),
            json.dumps({"creativity_score": 3, "feedback": "tot generic"}),
            json.dumps({"creativity_score": 8, "feedback": "excelent"}),
        ]
        gen_client = _SequentialClient(gen_responses)
        rate_client = _SequentialClient(rate_responses)

        title = generate_creative_title(
            ["AER", "MUNTE"],
            ["Gaz din atmosferă", "Formă de relief"],
            client=gen_client,
            rate_client=rate_client,
        )

        self.assertEqual("Ecou Fabulos", title)

    def test_creative_loop_uses_best_after_exhaustion(self):
        gen_responses = ["Ecou Prim", "Ecou Doi", "Ecou Trei", "Ecou Patru",
                         "Ecou Cinci", "Ecou Sase", "Ecou Sapte"]
        rate_responses = [
            json.dumps({"creativity_score": 2, "feedback": "slab"}),
            json.dumps({"creativity_score": 4, "feedback": "mediocru"}),
            json.dumps({"creativity_score": 3, "feedback": "nu prea"}),
            json.dumps({"creativity_score": 1, "feedback": "groaznic"}),
            json.dumps({"creativity_score": 4, "feedback": "mediocru"}),
            json.dumps({"creativity_score": 2, "feedback": "slab"}),
            json.dumps({"creativity_score": 3, "feedback": "nu prea"}),
        ]
        gen_client = _SequentialClient(gen_responses)
        rate_client = _SequentialClient(rate_responses)

        title = generate_creative_title(
            ["AER", "MUNTE"],
            ["Gaz din atmosferă", "Formă de relief"],
            client=gen_client,
            rate_client=rate_client,
        )

        self.assertEqual("Ecou Doi", title)

    def test_creative_loop_includes_rejected_in_prompt(self):
        gen_responses = ["Ecou Palid", "Ecou Doiun"]
        rate_responses = [
            json.dumps({"creativity_score": 2, "feedback": "prea banal"}),
            json.dumps({"creativity_score": 8, "feedback": "excelent"}),
        ]
        gen_client = _SequentialClient(gen_responses)
        rate_client = _SequentialClient(rate_responses)

        generate_creative_title(
            ["AER", "MUNTE"],
            ["Gaz din atmosferă"],
            client=gen_client,
            rate_client=rate_client,
        )

        self.assertIn("Ecou Palid", gen_client.last_user_content)
        self.assertIn("prea banal", gen_client.last_user_content)

    def test_sanitize_strips_trailing_comma_after_truncation(self):
        result = _sanitize_title(
            "Alfa Beta Gama Delta Epsilon",
            words=["X"],
        )
        # Truncated to 4 words, no trailing punctuation
        self.assertEqual("Alfa Beta Gama Delta", result)
        self.assertFalse(result.endswith(","))

    def test_sanitize_strips_trailing_punctuation(self):
        result = _sanitize_title(
            "Suflet și Lumină,",
            words=["X"],
        )
        self.assertEqual("Suflet și Lumină", result)

    def test_sanitize_rejects_comma_separated_word_list(self):
        result = _sanitize_title(
            "Suflet, Tunet, Platină",
            words=["SUFLET", "TUNET"],
        )
        self.assertIn(result, FALLBACK_TITLES)

    def test_sanitize_allows_single_long_word_in_title(self):
        result = _sanitize_title(
            "Sub Munte",
            words=["MUNTE"],
            input_words=["MUNTE"],
        )
        self.assertEqual("Sub Munte", result)

    def test_sanitize_rejects_two_long_words_in_title(self):
        result = _sanitize_title(
            "Munte și Aero",
            words=["MUNTE", "AERO"],
            input_words=["MUNTE", "AERO"],
        )
        self.assertIn(result, FALLBACK_TITLES)

    def test_fallback_pool_minimum_size(self):
        self.assertGreaterEqual(len(FALLBACK_TITLES), 20)

    def test_creative_prompt_does_not_contain_raw_words(self):
        gen_client = _FakeClient("Orizont Aprins")
        rate_client = _fake_rate_client(7)

        generate_creative_title(
            ["MUNTE", "PADURE", "CASCADA"],
            ["Formă de relief înaltă", "Arbori mulți la un loc", "Apă care cade"],
            client=gen_client,
            rate_client=rate_client,
        )

        prompt = gen_client.last_user_content
        # Prompt should have definitions but NOT a comma-separated word list
        self.assertIn("Formă de relief înaltă", prompt)
        self.assertNotIn("MUNTE, PADURE, CASCADA", prompt)
        self.assertNotIn("MUNTE", prompt)


if __name__ == "__main__":
    unittest.main()
