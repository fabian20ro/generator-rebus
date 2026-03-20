import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from generator.phases.theme import (
    FALLBACK_TITLES,
    TITLE_MIN_CREATIVITY,
    _fallback_title,
    _generate_single_title,
    _sanitize_title,
    _try_switch_model,
    generate_creative_title,
    generate_title_for_final_puzzle,
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


class _RaisingClient:
    """Client that always raises on create()."""

    def __init__(self):
        def _create(**kwargs):
            raise ConnectionError("network down")

        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=_create)
        )


def _fake_rate_client(score: int, feedback: str = "ok"):
    """Create a client that returns a fixed creativity rating."""
    return _FakeClient(json.dumps({"creativity_score": score, "feedback": feedback}))


# ---------------------------------------------------------------------------
# _generate_single_title (Level 1)
# ---------------------------------------------------------------------------

class GenerateSingleTitleTests(unittest.TestCase):
    def test_returns_raw_output(self):
        client = _FakeClient("  Metale și Ecouri  ")
        result = _generate_single_title(
            definitions=["Metal prețios", "Sunet repetat"],
            client=client,
        )
        self.assertEqual("  Metale și Ecouri  ", result)

    def test_returns_empty_on_failure(self):
        result = _generate_single_title(
            definitions=["Ceva"],
            client=_RaisingClient(),
        )
        self.assertEqual("", result)

    def test_falls_back_to_words_prompt(self):
        client = _FakeClient("Orizont Aprins")
        result = _generate_single_title(
            definitions=[],
            client=client,
            words=["MUNTE", "PADURE"],
        )
        self.assertEqual("Orizont Aprins", result)
        self.assertIn("MUNTE", client.last_user_content)
        self.assertIn("PADURE", client.last_user_content)

    def test_returns_empty_when_no_input(self):
        client = _FakeClient("Should Not Reach")
        result = _generate_single_title(
            definitions=[],
            client=client,
            words=None,
        )
        self.assertEqual("", result)

    def test_definitions_prompt_contains_definitions(self):
        client = _FakeClient("Ecou")
        _generate_single_title(
            definitions=["Formă de relief", "Lichid vital"],
            client=client,
        )
        self.assertIn("Formă de relief", client.last_user_content)
        self.assertIn("Lichid vital", client.last_user_content)


# ---------------------------------------------------------------------------
# _try_switch_model
# ---------------------------------------------------------------------------

class TrySwitchModelTests(unittest.TestCase):
    def test_noop_when_disabled(self):
        sentinel = object()
        result = _try_switch_model(sentinel, multi_model=False)
        self.assertIs(sentinel, result)

    def test_noop_when_no_current(self):
        result = _try_switch_model(None, multi_model=True)
        self.assertIsNone(result)

    @patch("generator.core.model_manager.ensure_model_loaded")
    def test_returns_current_on_failure(self, mock_ensure):
        mock_ensure.side_effect = RuntimeError("load failed")
        from generator.core.model_manager import PRIMARY_MODEL
        result = _try_switch_model(PRIMARY_MODEL, multi_model=True)
        self.assertIs(PRIMARY_MODEL, result)

    @patch("generator.core.model_manager.ensure_model_loaded")
    def test_switches_to_secondary(self, mock_ensure):
        from generator.core.model_manager import PRIMARY_MODEL, SECONDARY_MODEL
        result = _try_switch_model(PRIMARY_MODEL, multi_model=True)
        mock_ensure.assert_called_once_with(SECONDARY_MODEL)
        self.assertIs(SECONDARY_MODEL, result)

    @patch("generator.core.model_manager.ensure_model_loaded")
    def test_switches_to_primary(self, mock_ensure):
        from generator.core.model_manager import PRIMARY_MODEL, SECONDARY_MODEL
        result = _try_switch_model(SECONDARY_MODEL, multi_model=True)
        mock_ensure.assert_called_once_with(PRIMARY_MODEL)
        self.assertIs(PRIMARY_MODEL, result)


# ---------------------------------------------------------------------------
# _fallback_title
# ---------------------------------------------------------------------------

class FallbackTitleTests(unittest.TestCase):
    def test_returns_value_from_pool(self):
        title = _fallback_title()
        self.assertIn(title, FALLBACK_TITLES)

    def test_fallback_pool_minimum_size(self):
        self.assertGreaterEqual(len(FALLBACK_TITLES), 20)


# ---------------------------------------------------------------------------
# _sanitize_title
# ---------------------------------------------------------------------------

class SanitizeTitleTests(unittest.TestCase):
    def test_valid_title_passes_through(self):
        result = _sanitize_title("Metale și Ecouri")
        self.assertEqual("Metale și Ecouri", result)

    def test_rejects_redundant_rebus_title(self):
        result = _sanitize_title("Rebus Românesc")
        self.assertNotIn("Rebus", result)
        self.assertNotIn("Românesc", result)
        self.assertIn(result, FALLBACK_TITLES)

    def test_rejects_very_long_output(self):
        result = _sanitize_title("Acesta este foarte lung dar util pentru test")
        self.assertIn(result, FALLBACK_TITLES)

    def test_rejects_title_containing_two_input_words(self):
        result = _sanitize_title(
            "Munte și Plimbare",
            input_words=["MUNTE", "PLIMBARE"],
        )
        self.assertIn(result, FALLBACK_TITLES)

    def test_case_insensitive_word_check(self):
        result = _sanitize_title(
            "Munte și Verde",
            input_words=["munte", "verde"],
        )
        self.assertIn(result, FALLBACK_TITLES)

    def test_diacritics_normalized_word_check(self):
        result = _sanitize_title(
            "Față și Țară",
            input_words=["FATA", "TARA"],
        )
        self.assertIn(result, FALLBACK_TITLES)

    def test_rejects_five_word_title_instead_of_truncating(self):
        result = _sanitize_title("Alfa Beta Gama Delta Epsilon")
        self.assertIn(result, FALLBACK_TITLES)

    def test_rejects_obvious_english_title(self):
        result = _sanitize_title("Jazz Sunset Echoes")
        self.assertIn(result, FALLBACK_TITLES)

    def test_strips_trailing_punctuation(self):
        result = _sanitize_title("Suflet și Lumină,")
        self.assertEqual("Suflet și Lumină", result)

    def test_rejects_comma_separated_word_list(self):
        result = _sanitize_title("Suflet, Tunet, Platină")
        self.assertIn(result, FALLBACK_TITLES)

    def test_allows_single_long_word_in_title(self):
        result = _sanitize_title(
            "Sub Munte",
            input_words=["MUNTE"],
        )
        self.assertEqual("Sub Munte", result)

    def test_rejects_two_long_words_in_title(self):
        result = _sanitize_title(
            "Munte și Aero",
            input_words=["MUNTE", "AERO"],
        )
        self.assertIn(result, FALLBACK_TITLES)


# ---------------------------------------------------------------------------
# rate_title_creativity
# ---------------------------------------------------------------------------

class RateTitleCreativityTests(unittest.TestCase):
    def test_parses_json(self):
        client = _FakeClient('{"creativity_score": 7, "feedback": "bun titlu"}')
        score, feedback = rate_title_creativity("Test", ["A", "B"], client)
        self.assertEqual(7, score)
        self.assertEqual("bun titlu", feedback)


# ---------------------------------------------------------------------------
# generate_creative_title (Level 2)
# ---------------------------------------------------------------------------

class CreativeTitleTests(unittest.TestCase):
    def test_accepts_high_score(self):
        gen_client = _FakeClient("Orizont Aprins")
        rate_client = _fake_rate_client(7)

        title = generate_creative_title(
            ["AER", "MUNTE"],
            ["Gaz din atmosferă", "Formă de relief"],
            client=gen_client,
            rate_client=rate_client,
        )

        self.assertEqual("Orizont Aprins", title)

    def test_retries_on_low_score(self):
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

    def test_uses_best_after_exhaustion(self):
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

    def test_includes_rejected_in_prompt(self):
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

    def test_prompt_does_not_contain_raw_words(self):
        gen_client = _FakeClient("Orizont Aprins")
        rate_client = _fake_rate_client(7)

        generate_creative_title(
            ["MUNTE", "PADURE", "CASCADA"],
            ["Formă de relief înaltă", "Arbori mulți la un loc", "Apă care cade"],
            client=gen_client,
            rate_client=rate_client,
        )

        prompt = gen_client.last_user_content
        self.assertIn("Formă de relief înaltă", prompt)
        self.assertNotIn("MUNTE, PADURE, CASCADA", prompt)
        self.assertNotIn("MUNTE", prompt)

    def test_definitions_context_accepted(self):
        client = _FakeClient("Ecouri de Toamnă")
        rate_client = _fake_rate_client(8)

        title = generate_creative_title(
            ["NATURA", "FRUNZA"],
            ["Frunză uscată de toamnă", "Ce ține de lumea vie"],
            client=client,
            rate_client=rate_client,
        )

        self.assertEqual("Ecouri de Toamnă", title)


# ---------------------------------------------------------------------------
# generate_title_for_final_puzzle (Level 3)
# ---------------------------------------------------------------------------

class FinalPuzzleTitleTests(unittest.TestCase):
    def test_prompt_uses_definitions(self):
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
        self.assertIn("Definiția EXTRAORDINAR", prompt)
        self.assertIn("Definiția MUNTE", prompt)
        self.assertNotIn("EXTRAORDINAR, SPECTACOL", prompt)

    def test_passes_all_words(self):
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
        for w in words:
            self.assertIn(f"Definiția {w}", prompt)


if __name__ == "__main__":
    unittest.main()
