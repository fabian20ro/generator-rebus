import json
import unittest
from types import SimpleNamespace

from rebus_generator.domain.guards.title_guards import normalize_title_key, review_title_candidate as _review_title_candidate
from rebus_generator.platform.llm.models import PRIMARY_MODEL, SECONDARY_MODEL, chat_max_tokens
from rebus_generator.prompts.loader import load_system_prompt
from rebus_generator.workflows.retitle.generate import (
    _generate_single_title,
    generate_creative_title,
    generate_creative_title_result,
    generate_title_for_final_puzzle,
)
from rebus_generator.workflows.retitle.rate import (
    rate_title_creativity,
    rate_title_creativity_pair,
)
from rebus_generator.workflows.retitle.sanitize import (
    FALLBACK_TITLES,
    NO_TITLE_LABEL,
    TITLE_GENERATE_MAX_TOKENS,
    TITLE_RATE_MAX_TOKENS,
    _fallback_title,
    _sanitize_title,
)


class _FakeClient:
    def __init__(self, content):
        self.last_user_content = ""
        self.calls = []

        def _create(**kwargs):
            self.calls.append(kwargs)
            self.last_user_content = kwargs["messages"][-1]["content"]
            response = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )
            return [response] if kwargs.get("stream") else response

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
            response = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )
            return [response] if kwargs.get("stream") else response

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
        self.trace = []

    def activate_primary(self, **kwargs):
        self.primary_calls += 1
        self.trace.append("primary")
        return PRIMARY_MODEL

    def activate_secondary(self, **kwargs):
        self.secondary_calls += 1
        self.trace.append("secondary")
        return SECONDARY_MODEL


class _ModelAwareClient:
    def __init__(self, responses_by_model: dict[str, list[str]]):
        self._responses_by_model = {
            model: list(responses) for model, responses in responses_by_model.items()
        }
        self.calls = []

        def _create(**kwargs):
            self.calls.append(kwargs)
            model = kwargs["model"]
            responses = self._responses_by_model[model]
            content = responses.pop(0) if len(responses) > 1 else responses[0]
            response = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )
            return [response] if kwargs.get("stream") else response

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))


class _TraceClient:
    def __init__(self, runtime, content="Orizont Aprins"):
        self.runtime = runtime
        self.content = content
        self.trace_at_calls = []
        self.calls = []

        def _create(**kwargs):
            self.calls.append(kwargs)
            self.trace_at_calls.append(list(self.runtime.trace))
            response = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
            )
            return [response] if kwargs.get("stream") else response

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=_create))


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
        self.assertEqual(TITLE_GENERATE_MAX_TOKENS, client.calls[0]["max_tokens"])
        self.assertEqual("none", client.calls[0]["reasoning_effort"])

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
    def test_normalize_title_key_collapses_case_diacritics_and_spacing(self):
        self.assertEqual(
            normalize_title_key("  Sensuri   românești... "),
            normalize_title_key("sensuri romanesti"),
        )

    def test_valid_title_passes_through(self):
        self.assertEqual("Metale și Ecouri", _sanitize_title("Metale și Ecouri"))

    def test_rejects_redundant_rebus_title(self):
        self.assertIn(_sanitize_title("Rebus Românesc"), FALLBACK_TITLES)

    def test_rejects_five_word_title(self):
        self.assertEqual("Alfa Beta Gama Delta Epsilon", _sanitize_title("Alfa Beta Gama Delta Epsilon"))

    def test_rejects_six_word_title(self):
        reviewed = _review_title_candidate("Alfa Beta Gama Delta Epsilon Zeta")
        self.assertFalse(reviewed.valid)
        self.assertEqual("prea multe cuvinte", reviewed.feedback)

    def test_rejects_all_caps_title(self):
        reviewed = _review_title_candidate("ORIZONT VERDE")
        self.assertFalse(reviewed.valid)
        self.assertEqual("all caps", reviewed.feedback)

    def test_rejects_title_containing_solution_word_with_three_letters(self):
        reviewed = _review_title_candidate("Munte blând", input_words=["MUNTE", "AI"])
        self.assertFalse(reviewed.valid)
        self.assertEqual("contine cuvant-solutie", reviewed.feedback)

    def test_allows_two_letter_solution_words_in_title(self):
        reviewed = _review_title_candidate("Ai timp", input_words=["AI", "AT"])
        self.assertTrue(reviewed.valid)


class RateTitleCreativityTests(unittest.TestCase):
    def test_system_prompt_demands_exact_json_without_markdown(self):
        prompt = load_system_prompt("title_rate")
        self.assertIn("EXACT un singur obiect JSON valid", prompt)
        self.assertIn("Fără markdown.", prompt)
        self.assertIn('EXACT cheile "creativity_score" și "feedback"', prompt)

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
        self.assertNotIn("reasoning_effort", client.calls[0])
        self.assertEqual(SECONDARY_MODEL.model_id, client.calls[0]["model"])
        self.assertEqual(min(chat_max_tokens(SECONDARY_MODEL), TITLE_RATE_MAX_TOKENS), client.calls[0]["max_tokens"])

    def test_extracts_json_from_markdown_fence(self):
        client = _FakeClient('```json\n{"creativity_score": 8, "feedback": "clar"}\n```')
        score, feedback = rate_title_creativity(
            "Test",
            ["A", "B"],
            client,
            model_config=SECONDARY_MODEL,
        )
        self.assertEqual(8, score)
        self.assertEqual("clar", feedback)

    def test_pair_rating_penalizes_disagreement(self):
        runtime = _FakeRuntime()
        client = _ModelAwareClient(
            {
                PRIMARY_MODEL.model_id: [json.dumps({"creativity_score": 10, "feedback": "excelent"})],
                SECONDARY_MODEL.model_id: [json.dumps({"creativity_score": 6, "feedback": "bun"})],
            }
        )

        result = rate_title_creativity_pair("Test", ["A", "B"], client, runtime=runtime)

        self.assertTrue(result.complete)
        self.assertEqual(7, result.score)
        self.assertEqual(["primary", "secondary"], runtime.trace)

    def test_pair_rating_requires_both_models(self):
        runtime = _FakeRuntime()
        client = _ModelAwareClient(
            {
                PRIMARY_MODEL.model_id: [json.dumps({"creativity_score": 8, "feedback": "bun"})],
                SECONDARY_MODEL.model_id: ["not json"],
            }
        )

        result = rate_title_creativity_pair("Test", ["A", "B"], client, runtime=runtime)

        self.assertFalse(result.complete)


class CreativeTitleTests(unittest.TestCase):
    def test_accepts_high_score(self):
        runtime = _FakeRuntime()
        title = generate_creative_title(
            ["AER", "MUNTE"],
            ["Gaz din atmosferă", "Formă de relief"],
            client=_FakeClient("Orizont Aprins"),
            rate_client=_fake_rate_client(8),
            runtime=runtime,
            multi_model=True,
        )
        self.assertEqual("Orizont Aprins", title)
        self.assertEqual("primary", runtime.trace[0])
        self.assertIn("secondary", runtime.trace)

    def test_score_seven_requires_retry(self):
        runtime = _FakeRuntime()
        result = generate_creative_title_result(
            ["AER", "MUNTE"],
            ["Gaz din atmosferă", "Formă de relief"],
            client=_SequentialClient(["Orizont Cald", "Umbre Fine"]),
            rate_client=_SequentialClient([
                json.dumps({"creativity_score": 7, "feedback": "aproape"}),  # (C1, M1)
                json.dumps({"creativity_score": 8, "feedback": "acceptat"}), # (C2, M1)
                json.dumps({"creativity_score": 7, "feedback": "aproape"}),  # (C1, M2)
                json.dumps({"creativity_score": 8, "feedback": "acceptat"}), # (C2, M2)
            ]),
            runtime=runtime,
            multi_model=True,
        )
        self.assertEqual("Umbre Fine", result.title)
        self.assertEqual(8, result.score)

    def test_returns_fara_titlu_when_all_rounds_invalid(self):
        runtime = _FakeRuntime()
        result = generate_creative_title_result(
            ["MUNTE"],
            ["Formă de relief"],
            client=_SequentialClient(["MUNTE"] * 14),
            rate_client=_fake_rate_client(8),
            runtime=runtime,
            multi_model=True,
        )
        self.assertEqual(NO_TITLE_LABEL, result.title)
        self.assertEqual(0, result.score)
        self.assertTrue(result.used_fallback)

    def test_returns_fara_titlu_when_best_score_is_zero(self):
        runtime = _FakeRuntime()
        result = generate_creative_title_result(
            ["AER", "MUNTE"],
            ["Gaz din atmosferă", "Formă de relief"],
            client=_SequentialClient(["Orizont Calm"] * 7),
            rate_client=_SequentialClient([
                json.dumps({"creativity_score": 0, "feedback": "slab"}),
                json.dumps({"creativity_score": 0, "feedback": "slab"}),
            ] * 14),
            runtime=runtime,
            multi_model=True,
        )
        self.assertEqual("Orizont Calm", result.title)
        self.assertEqual(1, result.score)
        self.assertFalse(result.used_fallback)

    def test_retries_generation_with_secondary_when_primary_returns_empty(self):
        runtime = _FakeRuntime()
        gen_client = _ModelAwareClient(
            {
                PRIMARY_MODEL.model_id: ["", ""],
                SECONDARY_MODEL.model_id: ["Umbre Verzi"],
            }
        )
        rate_client = _fake_rate_client(8)

        result = generate_creative_title_result(
            ["AER", "MUNTE"],
            ["Gaz din atmosferă", "Formă de relief"],
            client=gen_client,
            rate_client=rate_client,
            runtime=runtime,
            multi_model=True,
        )

        self.assertEqual("Umbre Verzi", result.title)
        self.assertEqual(["primary", "secondary", "primary", "secondary"], runtime.trace)

    def test_empty_output_does_not_pollute_rejected_context(self):
        runtime = _FakeRuntime()
        gen_client = _ModelAwareClient(
            {
                PRIMARY_MODEL.model_id: ["", "", "Orizont Nou"],
                SECONDARY_MODEL.model_id: ["Umbre Verzi"],
            }
        )
        generate_creative_title_result(
            ["AER", "MUNTE"],
            ["Gaz din atmosferă", "Formă de relief"],
            client=gen_client,
            rate_client=_fake_rate_client(8),
            runtime=runtime,
            multi_model=False,
        )
        self.assertGreaterEqual(len(gen_client.calls), 3)
        self.assertNotIn("(gol)", gen_client.calls[-1]["messages"][-1]["content"])

    def test_single_model_title_rating_stays_on_primary_model(self):
        runtime = _FakeRuntime()
        gen_client = _TraceClient(runtime, content="Orizont Calm")
        result = generate_creative_title_result(
            ["AER", "MUNTE"],
            ["Gaz din atmosferă", "Formă de relief"],
            client=gen_client,
            rate_client=_fake_rate_client(8),
            runtime=runtime,
            multi_model=False,
        )
        self.assertEqual("Orizont Calm", result.title)
        self.assertTrue(result.score_complete)
        self.assertEqual(8, result.score)
        self.assertNotIn("secondary", runtime.trace)

    def test_retries_on_low_score(self):
        runtime = _FakeRuntime()
        title = generate_creative_title(
            ["AER", "MUNTE"],
            ["Gaz din atmosferă", "Formă de relief"],
            client=_SequentialClient(["Ecou Banal", "Alt Ecou", "Ecou Fabulos"]),
            rate_client=_SequentialClient([
                json.dumps({"creativity_score": 3, "feedback": "generic"}),
                json.dumps({"creativity_score": 3, "feedback": "tot generic"}),
                json.dumps({"creativity_score": 3, "feedback": "generic"}),
                json.dumps({"creativity_score": 3, "feedback": "tot generic"}),
                json.dumps({"creativity_score": 8, "feedback": "excelent"}),
                json.dumps({"creativity_score": 8, "feedback": "excelent"}),
            ]),
            runtime=runtime,
            multi_model=True,
        )
        self.assertEqual("Ecou Fabulos", title)

    def test_includes_rejected_in_prompt(self):
        runtime = _FakeRuntime()
        gen_client = _SequentialClient(["Alfa Beta Gama Delta Epsilon Zeta", "Linii Fara Margini", "Ecou Trei"])
        generate_creative_title(
            ["AER", "MUNTE"],
            ["Gaz din atmosferă"],
            client=gen_client,
            rate_client=_SequentialClient([
                json.dumps({"creativity_score": 8, "feedback": "excelent"}),
            ]),
            runtime=runtime,
            multi_model=False,
        )
        self.assertIn("Alfa Beta Gama Delta Epsilon Zeta", gen_client.last_user_content)
        self.assertIn("prea multe cuvinte", gen_client.last_user_content)

    def test_repeated_invalid_reason_adds_correction_hint(self):
        runtime = _FakeRuntime()
        gen_client = _SequentialClient([
            "Alfa Beta Gama Delta Epsilon Zeta",
            "Una Doua Trei Patru Cinci Sase",
            "Ecou Curat",
        ])
        generate_creative_title(
            ["AER", "MUNTE"],
            ["Gaz din atmosferă"],
            client=gen_client,
            rate_client=_fake_rate_client(8),
            runtime=runtime,
            multi_model=False,
        )
        self.assertIn("Corecții obligatorii", gen_client.last_user_content)
        self.assertIn("maximum 5 cuvinte", gen_client.last_user_content)

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
        self.assertEqual(
            [PRIMARY_MODEL.model_id, PRIMARY_MODEL.model_id],
            [call["model"] for call in rate_client.calls[:2]],
        )
        self.assertEqual(
            [SECONDARY_MODEL.model_id, SECONDARY_MODEL.model_id],
            [call["model"] for call in rate_client.calls[2:4]],
        )

    def test_no_secondary_activation_before_first_generation_call(self):
        runtime = _FakeRuntime()
        gen_client = _TraceClient(runtime)
        generate_creative_title(
            ["NATURA"],
            ["Frunză uscată de toamnă"],
            client=gen_client,
            rate_client=_fake_rate_client(8),
            runtime=runtime,
            multi_model=True,
        )
        self.assertEqual(["primary"], gen_client.trace_at_calls[0])

    def test_rejects_mixed_language_title(self):
        reviewed = _review_title_candidate("Umbre in world")
        self.assertFalse(reviewed.valid)
        self.assertEqual("limba mixta", reviewed.feedback)

    def test_retries_when_title_key_forbidden(self):
        runtime = _FakeRuntime()
        title = generate_creative_title(
            ["AER", "MUNTE"],
            ["Gaz din atmosferă", "Formă de relief"],
            client=_SequentialClient(["Sensuri Românești", "Orizont Nou"]),
            rate_client=_fake_rate_client(8),
            runtime=runtime,
            multi_model=True,
            forbidden_title_keys={normalize_title_key("sensuri romanesti")},
        )
        self.assertEqual("Orizont Nou", title)


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
