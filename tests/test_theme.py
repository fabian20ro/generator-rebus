import json
import unittest
from types import SimpleNamespace

from generator.core.model_manager import PRIMARY_MODEL, SECONDARY_MODEL
from generator.phases.theme import (
    FALLBACK_TITLES,
    _fallback_title,
    _generate_single_title,
    _sanitize_title,
    generate_creative_title,
    generate_title_for_final_puzzle,
    rate_title_creativity,
)


class _FakeClient:
    def __init__(self, content):
        self.last_user_content = ""
        self.calls = []

        def _create(**kwargs):
            self.calls.append(kwargs)
            self.last_user_content = kwargs["messages"][-1]["content"]
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))


class _SequentialClient:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self._index = 0
        self.last_user_content = ""
        self.calls = []

        def _create(**kwargs):
            self.calls.append(kwargs)
            self.last_user_content = kwargs["messages"][-1]["content"]
            content = self._responses[min(self._index, len(self._responses) - 1)]
            self._index += 1
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))


class _RaisingClient:
    def __init__(self):
        def _create(**kwargs):
            raise ConnectionError("network down")

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))


class _FakeRuntime:
    def __init__(self):
        self.primary_calls = 0
        self.secondary_calls = 0

    def activate_primary(self):
        self.primary_calls += 1
        return PRIMARY_MODEL

    def activate_secondary(self):
        self.secondary_calls += 1
        return SECONDARY_MODEL


def _fake_rate_client(score: int, feedback: str = "ok"):
    return _FakeClient(json.dumps({"creativity_score": score, "feedback": feedback}))


class GenerateSingleTitleTests(unittest.TestCase):
    def test_returns_raw_output(self):
        client = _FakeClient("  Metale și Ecouri  ")
        result = _generate_single_title(
            definitions=["Metal prețios", "Sunet repetat"],
            client=client,
            model_config=PRIMARY_MODEL,
        )
        self.assertEqual("  Metale și Ecouri  ", result)
        self.assertEqual(PRIMARY_MODEL.model_id, client.calls[0]["model"])

    def test_returns_empty_on_failure(self):
        result = _generate_single_title(
            definitions=["Ceva"],
            client=_RaisingClient(),
            model_config=PRIMARY_MODEL,
        )
        self.assertEqual("", result)

    def test_falls_back_to_words_prompt(self):
        client = _FakeClient("Orizont Aprins")
        result = _generate_single_title(
            definitions=[],
            client=client,
            model_config=PRIMARY_MODEL,
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
            model_config=PRIMARY_MODEL,
            words=None,
        )
        self.assertEqual("", result)


class FallbackTitleTests(unittest.TestCase):
    def test_returns_value_from_pool(self):
        self.assertIn(_fallback_title(), FALLBACK_TITLES)


class SanitizeTitleTests(unittest.TestCase):
    def test_valid_title_passes_through(self):
        self.assertEqual("Metale și Ecouri", _sanitize_title("Metale și Ecouri"))

    def test_rejects_redundant_rebus_title(self):
        self.assertIn(_sanitize_title("Rebus Românesc"), FALLBACK_TITLES)

    def test_rejects_five_word_title(self):
        self.assertIn(_sanitize_title("Alfa Beta Gama Delta Epsilon"), FALLBACK_TITLES)


class RateTitleCreativityTests(unittest.TestCase):
    def test_parses_json(self):
        client = _FakeClient('{"creativity_score": 7, "feedback": "bun titlu"}')
        score, feedback = rate_title_creativity(
            "Test",
            ["A", "B"],
            client,
            model_config=SECONDARY_MODEL,
        )
        self.assertEqual(7, score)
        self.assertEqual("bun titlu", feedback)
        self.assertEqual(SECONDARY_MODEL.model_id, client.calls[0]["model"])


class CreativeTitleTests(unittest.TestCase):
    def test_accepts_high_score(self):
        runtime = _FakeRuntime()
        title = generate_creative_title(
            ["AER", "MUNTE"],
            ["Gaz din atmosferă", "Formă de relief"],
            client=_FakeClient("Orizont Aprins"),
            rate_client=_fake_rate_client(7),
            runtime=runtime,
            multi_model=True,
        )
        self.assertEqual("Orizont Aprins", title)
        self.assertEqual(1, runtime.primary_calls)
        self.assertEqual(1, runtime.secondary_calls)

    def test_retries_on_low_score(self):
        runtime = _FakeRuntime()
        title = generate_creative_title(
            ["AER", "MUNTE"],
            ["Gaz din atmosferă", "Formă de relief"],
            client=_SequentialClient(["Ecou Banal", "Alt Ecou", "Ecou Fabulos"]),
            rate_client=_SequentialClient([
                json.dumps({"creativity_score": 3, "feedback": "generic"}),
                json.dumps({"creativity_score": 3, "feedback": "tot generic"}),
                json.dumps({"creativity_score": 8, "feedback": "excelent"}),
            ]),
            runtime=runtime,
            multi_model=True,
        )
        self.assertEqual("Ecou Fabulos", title)

    def test_includes_rejected_in_prompt(self):
        runtime = _FakeRuntime()
        gen_client = _SequentialClient(["Ecou Palid", "Ecou Doiun"])
        generate_creative_title(
            ["AER", "MUNTE"],
            ["Gaz din atmosferă"],
            client=gen_client,
            rate_client=_SequentialClient([
                json.dumps({"creativity_score": 2, "feedback": "prea banal"}),
                json.dumps({"creativity_score": 8, "feedback": "excelent"}),
            ]),
            runtime=runtime,
            multi_model=True,
        )
        self.assertIn("Ecou Palid", gen_client.last_user_content)
        self.assertIn("prea banal", gen_client.last_user_content)

    def test_uses_explicit_model_ids(self):
        runtime = _FakeRuntime()
        gen_client = _FakeClient("Ecouri de Toamnă")
        rate_client = _fake_rate_client(8)
        generate_creative_title(
            ["NATURA"],
            ["Frunză uscată de toamnă"],
            client=gen_client,
            rate_client=rate_client,
            runtime=runtime,
            multi_model=True,
        )
        self.assertEqual(PRIMARY_MODEL.model_id, gen_client.calls[0]["model"])
        self.assertEqual(SECONDARY_MODEL.model_id, rate_client.calls[0]["model"])


class FinalPuzzleTitleTests(unittest.TestCase):
    def test_prompt_uses_definitions(self):
        puzzle = SimpleNamespace(
            horizontal_clues=[
                SimpleNamespace(word_normalized="EXTRAORDINAR", definition="Definiția EXTRAORDINAR"),
                SimpleNamespace(word_normalized="MUNTE", definition="Definiția MUNTE"),
            ],
            vertical_clues=[],
        )
        runtime = _FakeRuntime()
        gen_client = _FakeClient("Univers Creativ")
        generate_title_for_final_puzzle(
            puzzle,
            client=gen_client,
            rate_client=_fake_rate_client(8),
            runtime=runtime,
        )
        prompt = gen_client.last_user_content
        self.assertIn("Definiția EXTRAORDINAR", prompt)
        self.assertIn("Definiția MUNTE", prompt)


if __name__ == "__main__":
    unittest.main()
