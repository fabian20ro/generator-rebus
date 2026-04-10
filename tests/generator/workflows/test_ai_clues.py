import json
import unittest
from types import SimpleNamespace
from unittest import mock

from rebus_generator.platform.llm.llm_client import (
    _chat_completion_create,
    _clean_response,
    configure_run_llm_policy,
    llm_run_stats_snapshot,
    reset_run_llm_state,
    short_form_max_tokens,
)
from rebus_generator.platform.llm.ai_clues import (
    DefinitionRating,
    RATE_MAX_TOKENS,
    RewriteAttemptResult,
    VERIFY_MAX_TOKENS,
    consensus_score,
    compute_rebus_score,
    generate_definition,
    rate_definition,
    rewrite_definition,
    verify_definition_candidates,
)
from rebus_generator.platform.llm.prompt_builders import (
    _extract_usage_suffix_from_dex,
    _normalize_definition_usage_suffix,
    _build_verify_prompt,
    _definition_describes_english_meaning,
    _build_generate_prompt,
    _build_rate_prompt,
    _build_rewrite_prompt,
)
from rebus_generator.domain.guards.definition_guards import (
    contains_english_markers,
)
from rebus_generator.platform.llm.definition_referee import (
    DefinitionComparisonVote,
    choose_better_clue_variant,
    compare_definition_variants,
    run_definition_referee,
    run_definition_referee_batch,
    run_definition_referee_adaptive_batch,
)
from rebus_generator.domain.clue_canon_types import DefinitionRefereeInput
from rebus_generator.platform.llm.models import PRIMARY_MODEL, SECONDARY_MODEL, chat_max_tokens
from rebus_generator.prompts.loader import load_system_prompt


class _RecordingClient:
    def __init__(self, responses):
        self.prompts = []
        self.calls = []
        queue = list(responses)

        def _create(**kwargs):
            self.calls.append(kwargs)
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


class _QueuedResponseClient:
    def __init__(self, responses):
        self.calls = []
        queue = list(responses)

        def _create(**kwargs):
            self.calls.append(kwargs)
            return queue.pop(0)

        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=_create
            )
        )


def _chat_response(
    *,
    content: str = "",
    reasoning_content: str = "",
    finish_reason: str = "stop",
    completion_tokens: int | None = None,
    reasoning_tokens: int | None = None,
):
    usage = None
    if completion_tokens is not None or reasoning_tokens is not None:
        usage = SimpleNamespace(
            completion_tokens=completion_tokens,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning_tokens),
        )
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(
                    content=content,
                    reasoning_content=reasoning_content,
                ),
            )
        ],
        usage=usage,
    )


def _attempt(
    *,
    model_id: str,
    model_role: str = "",
    same_meaning: bool | None = None,
    better: str = "equal",
    valid_vote: bool = True,
    parse_status: str = "ok",
):
    from rebus_generator.domain.clue_canon_types import DefinitionComparisonAttempt

    vote = None
    if valid_vote and same_meaning is not None:
        vote = DefinitionComparisonVote(
            model_id=model_id,
            same_meaning=same_meaning,
            better=better,
        )
    return DefinitionComparisonAttempt(
        model_id=model_id,
        model_role=model_role,
        valid_vote=valid_vote,
        parse_status=parse_status,
        vote=vote,
    )


class AiCluesTests(unittest.TestCase):
    def setUp(self):
        reset_run_llm_state()

    def tearDown(self):
        reset_run_llm_state()

    def test_generate_definition_sends_medium_reasoning_effort_for_primary_model(self):
        client = _RecordingClient(["Locuință pentru oameni."])

        generate_definition(
            client,
            word="CASA",
            original="casa",
            theme="",
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual("low", client.calls[0]["reasoning_effort"])
        self.assertEqual(chat_max_tokens(PRIMARY_MODEL), client.calls[0]["max_tokens"])

    def test_rate_definition_sends_medium_reasoning_effort_for_primary_model(self):
        client = _RecordingClient([
            json.dumps({
                "semantic_score": 9,
                "guessability_score": 6,
                "creativity_score": 5,
                "feedback": "Definiția este corectă.",
            })
        ])

        rating = rate_definition(
            client,
            word="ARACI",
            original="araci",
            definition="Bețe de sprijin pentru viță",
            answer_length=5,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertIsNotNone(rating)
        self.assertEqual("low", client.calls[0]["reasoning_effort"])
        self.assertEqual(RATE_MAX_TOKENS, client.calls[0]["max_tokens"])

    def test_verify_definition_candidates_omits_reasoning_effort_for_primary_model(self):
        client = _RecordingClient(["CASA"])

        verify_definition_candidates(
            client,
            definition="Locuință pentru oameni.",
            answer_length=4,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertNotIn("reasoning_effort", client.calls[0])
        self.assertEqual(VERIFY_MAX_TOKENS, client.calls[0]["max_tokens"])

    def test_rate_definition_omits_reasoning_effort_for_secondary_model(self):
        client = _RecordingClient([
            json.dumps({
                "semantic_score": 9,
                "guessability_score": 6,
                "creativity_score": 5,
                "feedback": "Definiția este corectă.",
            })
        ])

        rating = rate_definition(
            client,
            word="ARACI",
            original="araci",
            definition="Bețe de sprijin pentru viță",
            answer_length=5,
            model=SECONDARY_MODEL.model_id,
        )

        self.assertIsNotNone(rating)
        self.assertNotIn("reasoning_effort", client.calls[0])
        self.assertEqual(chat_max_tokens(SECONDARY_MODEL), client.calls[0]["max_tokens"])

    def test_consensus_score_penalizes_disagreement(self):
        self.assertEqual(10, consensus_score(10, 10))
        self.assertEqual(7, consensus_score(10, 6))
        self.assertEqual(6, consensus_score(9, 5))

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
            model=PRIMARY_MODEL.model_id,
        )

        self.assertNotIn("Exemplu de definiție rea de evitat", client.prompts[0])
        self.assertEqual(chat_max_tokens(PRIMARY_MODEL), client.calls[0]["max_tokens"])

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
            model=PRIMARY_MODEL.model_id,
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
            model=PRIMARY_MODEL.model_id,
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
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual(4, rating.guessability_score)
        self.assertIn("sinonim", rating.feedback)

    def test_rate_definition_retries_with_stricter_json_instruction(self):
        client = _RecordingClient([
            "semantic_score: 9, guessability_score: 4",
            json.dumps({
                "semantic_score": 9,
                "guessability_score": 6,
                "creativity_score": 5,
                "feedback": "Definiția este corectă.",
            }),
        ])

        rating = rate_definition(
            client,
            word="ARACI",
            original="araci",
            definition="Bețe de sprijin pentru viță",
            answer_length=5,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertIsNotNone(rating)
        self.assertEqual(2, len(client.prompts))
        self.assertIn("strict cu un singur obiect JSON valid", client.prompts[1])

    def test_generate_definition_includes_existing_canonical_definitions(self):
        client = _RecordingClient(["Locuință pentru oameni."])

        generate_definition(
            client,
            word="CASA",
            original="casa",
            theme="",
            existing_canonical_definitions=["Clădire destinată locuirii."],
            model=PRIMARY_MODEL.model_id,
        )

        self.assertIn("Definiții canonice deja folosite", client.prompts[0])
        self.assertIn("Clădire destinată locuirii.", client.prompts[0])

    def test_compare_definition_variants_parses_structured_json(self):
        client = _RecordingClient([
            json.dumps({
                "same_meaning": True,
                "better": "B",
            })
        ])

        vote = compare_definition_variants(
            client,
            word="LA",
            answer_length=2,
            definition_a="Prepoziție care indică locul.",
            definition_b="Prepoziție care indică destinația sau locul.",
            model=PRIMARY_MODEL.model_id,
        )

        self.assertTrue(vote.same_meaning)
        self.assertEqual("B", vote.better)
        self.assertEqual("", vote.reason)

    def test_compare_definition_variants_uses_low_reasoning_and_model_budget(self):
        client = _RecordingClient([
            json.dumps({
                "same_meaning": True,
                "better": "A",
            })
        ])

        compare_definition_variants(
            client,
            word="LA",
            answer_length=2,
            definition_a="Prepoziție care indică locul.",
            definition_b="Prepoziție care indică destinația sau locul.",
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual("low", client.calls[0]["reasoning_effort"])
        self.assertEqual(
            short_form_max_tokens(
                model=PRIMARY_MODEL.model_id,
                purpose="clue_compare",
                requested_max_tokens=chat_max_tokens(PRIMARY_MODEL),
            ),
            client.calls[0]["max_tokens"],
        )

    def test_choose_better_clue_variant_uses_short_form_budget(self):
        client = _RecordingClient(["B"])

        choice = choose_better_clue_variant(
            client,
            word="LA",
            answer_length=2,
            definition_a="Prepoziție care indică locul.",
            definition_b="Prepoziție care indică destinația.",
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual("B", choice)
        self.assertEqual(
            short_form_max_tokens(
                model=PRIMARY_MODEL.model_id,
                purpose="clue_tiebreaker",
                requested_max_tokens=chat_max_tokens(PRIMARY_MODEL),
            ),
            client.calls[0]["max_tokens"],
        )

    def test_chat_completion_logs_reasoning_budget_warning(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(
                completion_tokens=100,
                completion_tokens_details=SimpleNamespace(reasoning_tokens=90),
            ),
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kwargs: response)
            )
        )

        with mock.patch("rebus_generator.platform.llm.llm_client.log") as mock_log:
            _chat_completion_create(
                client,
                model=PRIMARY_MODEL.model_id,
                messages=[{"role": "user", "content": "test"}],
                temperature=0.0,
                max_tokens=100,
                purpose="definition_generate",
            )

        logged_messages = [call.args[0] for call in mock_log.call_args_list]
        self.assertTrue(any("warn reasoning_budget" in message for message in logged_messages))

    def test_chat_completion_retries_without_thinking_when_reasoning_hits_budget(self):
        client = _QueuedResponseClient([
            _chat_response(
                reasoning_content="plan",
                finish_reason="length",
                completion_tokens=4000,
                reasoning_tokens=4000,
            ),
            _chat_response(content="raspuns final"),
        ])

        response = _chat_completion_create(
            client,
            model=PRIMARY_MODEL.model_id,
            messages=[{"role": "user", "content": "test"}],
            temperature=0.0,
            max_tokens=4000,
            purpose="definition_generate",
        )

        self.assertEqual(2, len(client.calls))
        self.assertEqual("low", client.calls[0]["reasoning_effort"])
        self.assertEqual(4000, client.calls[0]["max_tokens"])
        self.assertEqual("none", client.calls[1]["reasoning_effort"])
        self.assertEqual(200, client.calls[1]["max_tokens"])
        self.assertEqual("raspuns final", response.choices[0].message.content)

    def test_chat_completion_retries_verify_when_hidden_reasoning_hits_budget(self):
        client = _QueuedResponseClient([
            _chat_response(
                reasoning_content="plan",
                finish_reason="length",
                completion_tokens=4000,
                reasoning_tokens=3997,
            ),
            _chat_response(content="GUAS"),
        ])

        response = _chat_completion_create(
            client,
            model=PRIMARY_MODEL.model_id,
            messages=[{"role": "user", "content": "test"}],
            temperature=0.0,
            max_tokens=4000,
            purpose="definition_verify",
        )

        self.assertEqual(2, len(client.calls))
        self.assertNotIn("reasoning_effort", client.calls[0])
        self.assertEqual(4000, client.calls[0]["max_tokens"])
        self.assertEqual("none", client.calls[1]["reasoning_effort"])
        self.assertEqual(200, client.calls[1]["max_tokens"])
        self.assertEqual("GUAS", response.choices[0].message.content)

    def test_chat_completion_retries_without_thinking_within_ten_tokens_of_budget(self):
        client = _QueuedResponseClient([
            _chat_response(
                reasoning_content="plan",
                finish_reason="length",
                completion_tokens=4000,
                reasoning_tokens=3995,
            ),
            _chat_response(content="raspuns final"),
        ])

        _chat_completion_create(
            client,
            model=PRIMARY_MODEL.model_id,
            messages=[{"role": "user", "content": "test"}],
            temperature=0.0,
            max_tokens=4000,
            purpose="definition_generate",
        )

        self.assertEqual(2, len(client.calls))
        self.assertEqual("none", client.calls[1]["reasoning_effort"])
        self.assertEqual(200, client.calls[1]["max_tokens"])

    def test_verify_definition_does_not_retry_outer_loop_after_no_thinking_retry_terminal_blank(self):
        client = _QueuedResponseClient([
            _chat_response(
                reasoning_content="plan",
                finish_reason="length",
                completion_tokens=4000,
                reasoning_tokens=3997,
            ),
            _chat_response(content=""),
        ])

        result = verify_definition_candidates(
            client,
            definition="Locuință pentru oameni.",
            answer_length=4,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual([], result.candidates)
        self.assertEqual("no_thinking_retry", result.response_source)
        self.assertEqual(2, len(client.calls))

    def test_rate_definition_does_not_retry_outer_loop_after_no_thinking_retry_terminal_blank(self):
        client = _QueuedResponseClient([
            _chat_response(
                reasoning_content="plan",
                finish_reason="length",
                completion_tokens=4000,
                reasoning_tokens=3997,
            ),
            _chat_response(content=""),
        ])

        rating = rate_definition(
            client,
            word="ARACI",
            original="araci",
            definition="Bețe de sprijin pentru viță",
            answer_length=5,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertIsNone(rating)
        self.assertEqual(2, len(client.calls))

    def test_generate_definition_does_not_retry_outer_loop_after_no_thinking_retry_invalid_definition(self):
        client = _QueuedResponseClient([
            _chat_response(
                reasoning_content="plan",
                finish_reason="length",
                completion_tokens=4000,
                reasoning_tokens=3997,
            ),
            _chat_response(content="casa"),
        ])

        definition = generate_definition(
            client,
            word="CASA",
            original="casa",
            theme="",
            retries=3,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual("[Definiție negenerată]", definition)
        self.assertEqual(2, len(client.calls))

    def test_chat_completion_does_not_retry_when_reasoning_stays_below_margin(self):
        client = _QueuedResponseClient([
            _chat_response(
                reasoning_content="plan",
                finish_reason="length",
                completion_tokens=4000,
                reasoning_tokens=3989,
            ),
        ])

        response = _chat_completion_create(
            client,
            model=PRIMARY_MODEL.model_id,
            messages=[{"role": "user", "content": "test"}],
            temperature=0.0,
            max_tokens=4000,
            purpose="definition_generate",
        )

        self.assertEqual(1, len(client.calls))
        self.assertEqual("plan", response.choices[0].message.reasoning_content)

    def test_chat_completion_does_not_retry_verify_when_reasoning_stays_below_margin(self):
        client = _QueuedResponseClient([
            _chat_response(
                reasoning_content="plan",
                finish_reason="length",
                completion_tokens=4000,
                reasoning_tokens=3989,
            ),
        ])

        response = _chat_completion_create(
            client,
            model=PRIMARY_MODEL.model_id,
            messages=[{"role": "user", "content": "test"}],
            temperature=0.0,
            max_tokens=4000,
            purpose="definition_verify",
        )

        self.assertEqual(1, len(client.calls))
        self.assertNotIn("reasoning_effort", client.calls[0])
        self.assertEqual("plan", response.choices[0].message.reasoning_content)

    def test_chat_completion_does_not_retry_when_visible_content_exists(self):
        client = _QueuedResponseClient([
            _chat_response(
                content="schita",
                reasoning_content="plan",
                finish_reason="length",
                completion_tokens=4000,
                reasoning_tokens=4000,
            ),
        ])

        response = _chat_completion_create(
            client,
            model=PRIMARY_MODEL.model_id,
            messages=[{"role": "user", "content": "test"}],
            temperature=0.0,
            max_tokens=4000,
            purpose="definition_generate",
        )

        self.assertEqual(1, len(client.calls))
        self.assertEqual("schita", response.choices[0].message.content)

    def test_chat_completion_does_not_retry_verify_when_visible_content_exists(self):
        client = _QueuedResponseClient([
            _chat_response(
                content="GUAS",
                reasoning_content="plan",
                finish_reason="length",
                completion_tokens=4000,
                reasoning_tokens=3997,
            ),
        ])

        response = _chat_completion_create(
            client,
            model=PRIMARY_MODEL.model_id,
            messages=[{"role": "user", "content": "test"}],
            temperature=0.0,
            max_tokens=4000,
            purpose="definition_verify",
        )

        self.assertEqual(1, len(client.calls))
        self.assertNotIn("reasoning_effort", client.calls[0])
        self.assertEqual("GUAS", response.choices[0].message.content)

    def test_chat_completion_does_not_retry_when_reasoning_is_already_disabled(self):
        client = _QueuedResponseClient([
            _chat_response(
                reasoning_content="plan",
                finish_reason="length",
                completion_tokens=200,
                reasoning_tokens=200,
            ),
        ])

        response = _chat_completion_create(
            client,
            model=SECONDARY_MODEL.model_id,
            messages=[{"role": "user", "content": "test"}],
            temperature=0.0,
            max_tokens=200,
            purpose="definition_generate",
        )

        self.assertEqual(1, len(client.calls))
        self.assertNotIn("reasoning_effort", client.calls[0])
        self.assertEqual("plan", response.choices[0].message.reasoning_content)

    def test_chat_completion_logs_retry_without_thinking(self):
        client = _QueuedResponseClient([
            _chat_response(
                reasoning_content="plan",
                finish_reason="length",
                completion_tokens=4000,
                reasoning_tokens=4000,
            ),
            _chat_response(content="raspuns final"),
        ])

        with mock.patch("rebus_generator.platform.llm.llm_client.log") as mock_log:
            _chat_completion_create(
                client,
                model=PRIMARY_MODEL.model_id,
                messages=[{"role": "user", "content": "test"}],
                temperature=0.0,
                max_tokens=4000,
                purpose="definition_generate",
            )

        logged_messages = [call.args[0] for call in mock_log.call_args_list]
        self.assertTrue(any("retry without_thinking" in message for message in logged_messages))
        self.assertTrue(any("retry_max_tokens=200" in message for message in logged_messages))

    def test_run_policy_truncation_threshold_triggers_adaptive_downgrade(self):
        configure_run_llm_policy(
            reasoning_overrides={
                (PRIMARY_MODEL.model_id, "definition_rate"): "minimal",
            },
            truncation_threshold=2,
        )
        client = _QueuedResponseClient([
            _chat_response(reasoning_content="plan", finish_reason="length", completion_tokens=240, reasoning_tokens=239),
            _chat_response(content="{}"),
            _chat_response(reasoning_content="plan", finish_reason="length", completion_tokens=240, reasoning_tokens=239),
            _chat_response(content="{}"),
            _chat_response(content='{"semantic_score": 8, "guessability_score": 6, "creativity_score": 5, "feedback": "ok"}'),
        ])

        _chat_completion_create(
            client,
            model=PRIMARY_MODEL.model_id,
            messages=[{"role": "user", "content": "test"}],
            temperature=0.0,
            max_tokens=300,
            purpose="definition_rate",
        )
        _chat_completion_create(
            client,
            model=PRIMARY_MODEL.model_id,
            messages=[{"role": "user", "content": "test"}],
            temperature=0.0,
            max_tokens=300,
            purpose="definition_rate",
        )
        _chat_completion_create(
            client,
            model=PRIMARY_MODEL.model_id,
            messages=[{"role": "user", "content": "test"}],
            temperature=0.0,
            max_tokens=300,
            purpose="definition_rate",
        )

        self.assertEqual("minimal", client.calls[0]["reasoning_effort"])
        self.assertEqual(240, client.calls[0]["max_tokens"])
        self.assertEqual("minimal", client.calls[2]["reasoning_effort"])
        self.assertEqual("none", client.calls[4]["reasoning_effort"])
        snapshot = llm_run_stats_snapshot()
        self.assertIn(f"{PRIMARY_MODEL.model_id}|definition_rate", snapshot["adaptive_downgrades"])

    def test_run_policy_bounds_short_form_tokens_and_reasoning(self):
        configure_run_llm_policy(
            reasoning_overrides={
                (PRIMARY_MODEL.model_id, "definition_verify"): "none",
                (PRIMARY_MODEL.model_id, "title_generate"): "none",
                (PRIMARY_MODEL.model_id, "title_rate"): "none",
                (PRIMARY_MODEL.model_id, "clue_compare"): "none",
                (PRIMARY_MODEL.model_id, "clue_tiebreaker"): "none",
            },
            truncation_threshold=3,
        )
        client = _QueuedResponseClient([
            _chat_response(content="GUAS"),
            _chat_response(content="Titlu"),
            _chat_response(content='{"creativity_score": 7, "feedback": "ok"}'),
            _chat_response(content='{"same_meaning": true, "better": "A"}'),
            _chat_response(content="B"),
        ])

        _chat_completion_create(
            client,
            model=PRIMARY_MODEL.model_id,
            messages=[{"role": "user", "content": "verify"}],
            temperature=0.0,
            max_tokens=400,
            purpose="definition_verify",
        )
        _chat_completion_create(
            client,
            model=PRIMARY_MODEL.model_id,
            messages=[{"role": "user", "content": "title"}],
            temperature=0.0,
            max_tokens=400,
            purpose="title_generate",
        )
        _chat_completion_create(
            client,
            model=PRIMARY_MODEL.model_id,
            messages=[{"role": "user", "content": "rate"}],
            temperature=0.0,
            max_tokens=300,
            purpose="title_rate",
        )
        _chat_completion_create(
            client,
            model=PRIMARY_MODEL.model_id,
            messages=[{"role": "user", "content": "compare"}],
            temperature=0.0,
            max_tokens=4000,
            purpose="clue_compare",
        )
        _chat_completion_create(
            client,
            model=PRIMARY_MODEL.model_id,
            messages=[{"role": "user", "content": "tiebreak"}],
            temperature=0.0,
            max_tokens=4000,
            purpose="clue_tiebreaker",
        )

        self.assertEqual(256, client.calls[0]["max_tokens"])
        self.assertEqual("none", client.calls[0]["reasoning_effort"])
        self.assertEqual(256, client.calls[1]["max_tokens"])
        self.assertEqual("none", client.calls[1]["reasoning_effort"])
        self.assertEqual(224, client.calls[2]["max_tokens"])
        self.assertEqual("none", client.calls[2]["reasoning_effort"])
        self.assertEqual(320, client.calls[3]["max_tokens"])
        self.assertEqual("none", client.calls[3]["reasoning_effort"])
        self.assertEqual(256, client.calls[4]["max_tokens"])
        self.assertEqual("none", client.calls[4]["reasoning_effort"])

    def test_chat_completion_retries_title_rate_when_truncated_json_is_invalid(self):
        configure_run_llm_policy(
            reasoning_overrides={(PRIMARY_MODEL.model_id, "title_rate"): "none"},
            truncation_threshold=3,
        )
        client = _QueuedResponseClient([
            _chat_response(
                content='{"creativity_score": 7',
                finish_reason="length",
                completion_tokens=224,
                reasoning_tokens=150,
            ),
            _chat_response(content='{"creativity_score": 7, "feedback": "ok"}'),
        ])

        response = _chat_completion_create(
            client,
            model=PRIMARY_MODEL.model_id,
            messages=[{"role": "user", "content": "rate"}],
            temperature=0.0,
            max_tokens=300,
            purpose="title_rate",
        )

        self.assertEqual(2, len(client.calls))
        self.assertEqual(224, client.calls[0]["max_tokens"])
        self.assertEqual("none", client.calls[1]["reasoning_effort"])
        self.assertEqual(200, client.calls[1]["max_tokens"])
        self.assertEqual('{"creativity_score": 7, "feedback": "ok"}', response.choices[0].message.content)

    def test_chat_completion_retries_compare_when_truncated_json_is_invalid(self):
        configure_run_llm_policy(
            reasoning_overrides={(PRIMARY_MODEL.model_id, "clue_compare"): "none"},
            truncation_threshold=3,
        )
        client = _QueuedResponseClient([
            _chat_response(
                content='{"same_meaning": true',
                finish_reason="length",
                completion_tokens=320,
                reasoning_tokens=210,
            ),
            _chat_response(content='{"same_meaning": true, "better": "A"}'),
        ])

        response = _chat_completion_create(
            client,
            model=PRIMARY_MODEL.model_id,
            messages=[{"role": "user", "content": "compare"}],
            temperature=0.0,
            max_tokens=4000,
            purpose="clue_compare",
        )

        self.assertEqual(2, len(client.calls))
        self.assertEqual(320, client.calls[0]["max_tokens"])
        self.assertEqual("none", client.calls[1]["reasoning_effort"])
        self.assertEqual(200, client.calls[1]["max_tokens"])
        self.assertEqual('{"same_meaning": true, "better": "A"}', response.choices[0].message.content)

    def test_rate_definition_marks_no_thinking_retry_source(self):
        response = _chat_response(
            content='{"semantic_score": 8, "guessability_score": 6, "creativity_score": 5, "feedback": "ok"}'
        )
        setattr(response, "_response_source", "no_thinking_retry")
        with mock.patch("rebus_generator.platform.llm.ai_clues._chat_completion_create", return_value=response):
            rating = rate_definition(
                object(),
                word="ARACI",
                original="araci",
                definition="Bețe de sprijin pentru viță",
                answer_length=5,
                model=PRIMARY_MODEL.model_id,
            )

        self.assertIsNotNone(rating)
        self.assertEqual("no_thinking_retry", rating.response_source)

    def test_run_definition_referee_remaps_swapped_votes_back_to_original_orientation(self):
        class _Runtime:
            def __init__(self):
                self.activated = []

            def activate(self, model):
                self.activated.append(model.model_id)
                return model

        runtime = _Runtime()
        client = object()
        attempts = [
            _attempt(model_id=PRIMARY_MODEL.model_id, same_meaning=True, better="A"),
            _attempt(model_id=SECONDARY_MODEL.model_id, same_meaning=True, better="A"),
        ]

        with mock.patch("rebus_generator.platform.llm.definition_referee._compare_definition_variant_attempt", side_effect=attempts):
            result = run_definition_referee(
                client,
                runtime,
                word="LA",
                answer_length=2,
                definition_a="A",
                definition_b="B",
            )

        self.assertEqual(2, result.same_meaning_votes)
        self.assertEqual(1, result.better_a_votes)
        self.assertEqual(1, result.better_b_votes)

    def test_run_definition_referee_batch_groups_activations_by_model(self):
        class _Runtime:
            def __init__(self):
                self.activated = []

            def activate(self, model):
                self.activated.append(model.model_id)
                return model

        runtime = _Runtime()
        client = object()
        requests = [
            DefinitionRefereeInput(
                request_id="r1",
                word="LA",
                answer_length=2,
                definition_a="A1",
                definition_b="B1",
            ),
            DefinitionRefereeInput(
                request_id="r2",
                word="SI",
                answer_length=2,
                definition_a="A2",
                definition_b="B2",
            ),
        ]

        attempts = [
            _attempt(model_id=PRIMARY_MODEL.model_id, same_meaning=True, better="A"),
            _attempt(model_id=PRIMARY_MODEL.model_id, same_meaning=True, better="B"),
            _attempt(model_id=SECONDARY_MODEL.model_id, same_meaning=True, better="A"),
            _attempt(model_id=SECONDARY_MODEL.model_id, same_meaning=True, better="B"),
        ]

        with mock.patch("rebus_generator.platform.llm.definition_referee._compare_definition_variant_attempt", side_effect=attempts):
            results = run_definition_referee_batch(client, runtime, requests)

        self.assertEqual([PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id], runtime.activated)
        self.assertEqual(2, results["r1"].same_meaning_votes)
        self.assertEqual(1, results["r1"].better_a_votes)
        self.assertEqual(1, results["r1"].better_b_votes)
        self.assertEqual(2, results["r2"].same_meaning_votes)
        self.assertEqual(1, results["r2"].better_a_votes)
        self.assertEqual(1, results["r2"].better_b_votes)

    def test_run_definition_referee_adaptive_batch_stops_after_two_clear_non_matches(self):
        class _Runtime:
            def __init__(self):
                self.activated = []

            def activate(self, model):
                self.activated.append(model.model_id)
                return model

        runtime = _Runtime()
        client = object()
        votes = [
            DefinitionComparisonVote(model_id=PRIMARY_MODEL.model_id, same_meaning=False, better="equal"),
            DefinitionComparisonVote(model_id=SECONDARY_MODEL.model_id, same_meaning=False, better="equal"),
        ]
        requests = [
            DefinitionRefereeInput(
                request_id="r1",
                word="LA",
                answer_length=2,
                definition_a="A1",
                definition_b="B1",
            )
        ]

        attempts = [
            _attempt(model_id=PRIMARY_MODEL.model_id, same_meaning=False, better="equal"),
            _attempt(model_id=SECONDARY_MODEL.model_id, same_meaning=False, better="equal"),
        ]

        with mock.patch("rebus_generator.platform.llm.definition_referee._compare_definition_variant_attempt", side_effect=attempts):
            result = run_definition_referee_adaptive_batch(client, runtime, requests)

        self.assertEqual([PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id], runtime.activated)
        self.assertEqual(2, result.total_votes)
        self.assertEqual(1, result.phase1_requests)
        self.assertEqual(1, result.phase2_requests)
        self.assertEqual(0, result.results["r1"].same_meaning_votes)
        self.assertEqual(2, len(result.step_metrics))
        self.assertEqual(1, result.step_metrics[0]["requests_started"])
        self.assertEqual(0, result.step_metrics[1]["requests_remaining_after_step"])


    def test_clean_response_strips_model_tokens(self):
        self.assertEqual("ZI", _clean_response("<|channel|>ZI"))
        self.assertEqual("", _clean_response("<|channel|>"))
        self.assertEqual("AER", _clean_response("<|endoftext|>AER"))
        self.assertEqual("CASA", _clean_response("CASA<|im_end|>"))

    def test_clean_response_takes_first_line(self):
        self.assertEqual("CASA", _clean_response("CASA\naltceva pe linia doi"))

    def test_clean_response_strips_markdown_emphasis(self):
        self.assertEqual("CASA", _clean_response("**CASA**"))
        self.assertEqual("AER", _clean_response("`AER`"))

    def test_clean_response_strips_definitia_noua_prefix(self):
        self.assertEqual(
            "Material de bază în grădinărit, rezultat din descompunerea vegetală.",
            _clean_response("Definiția nouă:** Material de bază în grădinărit, rezultat din descompunerea vegetală."),
        )

    def test_clean_response_strips_english_definition_prefix_and_translation(self):
        self.assertEqual(
            "Vânt care bate slab",
            _clean_response("Definition: `Vânt care bate slab` (Wind that blows weakly)."),
        )

    def test_clean_response_picks_definition_after_final_choice_label(self):
        self.assertEqual(
            "Acționat prin forța unor lichide sub presiune.",
            _clean_response('Final choice:\n"Acționat prin forța unor lichide sub presiune."'),
        )

    def test_contains_english_markers_handles_romanian_diacritics(self):
        self.assertFalse(
            contains_english_markers("Acționat prin forța unor lichide sub presiune.")
        )

    def test_contains_english_markers_detects_actual_english(self):
        self.assertTrue(
            contains_english_markers("Powered by fluid pressure in a technical system.")
        )

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
        self.assertIn("nu un singur cuvânt izolat", prompt)

    def test_extract_usage_suffix_from_dex_prefers_highest_precedence(self):
        dex = "- Pronume personal, în limbaj arhaic.\n- Formă învechită."
        self.assertEqual("(arh.)", _extract_usage_suffix_from_dex(dex))

    def test_extract_usage_suffix_from_dex_returns_none_when_no_explicit_label(self):
        dex = "- Țesut dur al scheletului."
        self.assertIsNone(_extract_usage_suffix_from_dex(dex))

    def test_normalize_definition_usage_suffix_appends_required_suffix(self):
        self.assertEqual(
            "Pronume personal de persoana I singular (arh.)",
            _normalize_definition_usage_suffix(
                "Pronume personal de persoana I singular",
                "(arh.)",
            ),
        )

    def test_normalize_definition_usage_suffix_replaces_existing_suffix(self):
        self.assertEqual(
            "Pronume personal de persoana I singular (arh.)",
            _normalize_definition_usage_suffix(
                "Pronume personal de persoana I singular (reg.)",
                "(arh.)",
            ),
        )

    def test_normalize_definition_usage_suffix_removes_gratuitous_suffix(self):
        self.assertEqual(
            "Locuință obișnuită",
            _normalize_definition_usage_suffix("Locuință obișnuită (reg.)", None),
        )

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
            model=PRIMARY_MODEL.model_id,
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
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual(9, rating.creativity_score)
        self.assertEqual(8, rating.semantic_score)
        self.assertEqual(7, rating.guessability_score)


    def test_generate_prompt_includes_word_type(self):
        prompt = _build_generate_prompt("lovi", "LOVI", 4, word_type="V")
        self.assertIn("Categorie gramaticală: verb", prompt)

    def test_generate_prompt_includes_usage_label_instruction(self):
        prompt = _build_generate_prompt(
            "az",
            "AZ",
            2,
            dex_definitions="- Pronume personal, în limbaj arhaic.",
        )
        self.assertIn("Marcaj DEX explicit: (arh.)", prompt)
        self.assertIn("încheie definiția exact cu (arh.)", prompt)

    def test_generate_prompt_no_word_type_for_empty(self):
        prompt = _build_generate_prompt("casă", "CASA", 4, word_type="")
        self.assertNotIn("Categorie gramaticală", prompt)

    def test_generate_prompt_no_word_type_for_unknown(self):
        prompt = _build_generate_prompt("casă", "CASA", 4, word_type="X")
        self.assertNotIn("Categorie gramaticală", prompt)

    def test_rewrite_prompt_includes_word_type(self):
        prompt = _build_rewrite_prompt(
            "lovi", "LOVI", "A atinge cu forță", "[niciun feedback]", "", word_type="V",
        )
        self.assertIn("Categorie gramaticală: verb", prompt)

    def test_rewrite_prompt_includes_usage_label_instruction(self):
        prompt = _build_rewrite_prompt(
            "az",
            "AZ",
            "Pronume personal",
            "[niciun feedback]",
            "",
            dex_definitions="- Pronume personal, în limbaj arhaic.",
        )
        self.assertIn("Marcaj DEX explicit: (arh.)", prompt)
        self.assertIn("Păstrează sau restaurează", prompt)

    def test_build_verify_prompt_includes_candidate_count(self):
        prompt = _build_verify_prompt("Țesut dur al scheletului", 2, max_guesses=3)
        self.assertIn("maximum 3", prompt)
        self.assertIn("Răspunsuri", prompt)

    def test_build_verify_prompt_includes_usage_label_context(self):
        prompt = _build_verify_prompt("Pronume personal de persoana I singular (arh.)", 2, max_guesses=3)
        self.assertIn("Marcaj de uz explicit în definiție: (arh.)", prompt)

    def test_verify_definition_candidates_parses_numbered_lines(self):
        client = _RecordingClient(["1. BAR\n2. TUN\n3. ARC"])

        result = verify_definition_candidates(
            client,
            "Recipient mare pentru vin",
            answer_length=3,
            max_guesses=3,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual(["BAR", "TUN", "ARC"], result.candidates)

    def test_generate_definition_passes_explicit_model(self):
        client = _RecordingClient(["Locuință obișnuită"])

        generate_definition(
            client,
            word="CASA",
            original="casă",
            theme="",
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual(PRIMARY_MODEL.model_id, client.calls[0]["model"])
        self.assertEqual(chat_max_tokens(PRIMARY_MODEL), client.calls[0]["max_tokens"])

    def test_verify_definition_candidates_passes_explicit_model(self):
        client = _RecordingClient(["1. BAR\n2. TUN"])

        verify_definition_candidates(
            client,
            "Recipient mare pentru vin",
            answer_length=3,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual(PRIMARY_MODEL.model_id, client.calls[0]["model"])

    def test_verify_definition_candidates_uses_secondary_global_budget(self):
        client = _RecordingClient(["1. BAR\n2. TUN"])

        verify_definition_candidates(
            client,
            "Recipient mare pentru vin",
            answer_length=3,
            model=SECONDARY_MODEL.model_id,
        )

        self.assertEqual(chat_max_tokens(SECONDARY_MODEL), client.calls[0]["max_tokens"])

    def test_rate_definition_passes_explicit_model(self):
        client = _RecordingClient([
            json.dumps({
                "semantic_score": 8,
                "guessability_score": 7,
                "creativity_score": 6,
                "feedback": "Corect.",
            })
        ])

        rate_definition(
            client,
            word="CASA",
            original="casă",
            definition="Locuință",
            answer_length=4,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual(PRIMARY_MODEL.model_id, client.calls[0]["model"])

    def test_verify_definition_candidates_filters_wrong_lengths(self):
        client = _RecordingClient(["1. BARIL\n2. TUN\n3. ARC"])

        result = verify_definition_candidates(
            client,
            "Recipient mare pentru vin",
            answer_length=3,
            max_guesses=3,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual(["TUN", "ARC"], result.candidates)

    def test_rate_prompt_includes_word_type(self):
        prompt = _build_rate_prompt("casă", "CASA", "Locuință", 4, word_type="N")
        self.assertIn("Categorie gramaticală: substantiv", prompt)

    def test_rate_prompt_includes_usage_label_and_bias_context(self):
        prompt = _build_rate_prompt(
            "az",
            "AZ",
            "Pronume personal de persoana I singular (arh.)",
            2,
            dex_definitions="- Pronume personal, în limbaj arhaic.",
        )
        self.assertIn("Marcaj DEX permis: (arh.)", prompt)
        self.assertIn("Eticheta corespunde explicit unui sens DEX marcat", prompt)

    def test_verify_prompt_includes_word_type(self):
        prompt = _build_verify_prompt("Locuință", 4, word_type="N")
        self.assertIn("Categorie gramaticală: substantiv", prompt)
        self.assertIn("Definiție: Locuință", prompt)

    def test_generate_definition_appends_required_suffix(self):
        client = _RecordingClient(["Pronume personal de persoana I singular"])

        definition = generate_definition(
            client,
            word="AZ",
            original="az",
            theme="",
            dex_definitions="- Pronume personal, în limbaj arhaic.",
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual("Pronume personal de persoana I singular (arh.)", definition)

    def test_generate_definition_removes_gratuitous_suffix_when_dex_has_no_label(self):
        client = _RecordingClient(["Locuință obișnuită (reg.)"])

        definition = generate_definition(
            client,
            word="CASA",
            original="casă",
            theme="",
            dex_definitions="- Locuință.",
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual("Locuință obișnuită", definition)

    def test_rewrite_definition_restores_required_suffix(self):
        client = _RecordingClient(["Pronume personal de persoana I singular"])

        definition = rewrite_definition(
            client,
            word="AZ",
            original="az",
            theme="",
            previous_definition="Pronume personal",
            wrong_guess="EU",
            dex_definitions="- Pronume personal, în limbaj arhaic.",
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual("Pronume personal de persoana I singular (arh.)", definition)

    def test_system_prompts_include_usage_label_examples(self):
        definition_prompt = load_system_prompt("definition")
        verify_prompt = load_system_prompt("verify")
        rate_prompt = load_system_prompt("rate")

        self.assertIn("AZ -> Pronume personal de persoana I singular (arh.)", definition_prompt)
        self.assertIn("Definiție: Pronume personal de persoana I singular (arh.)", verify_prompt)
        self.assertIn(
            "`Pronume personal de persoana I singular (arh.)` pentru un răspuns rar ca `AZ`",
            rate_prompt,
        )
        self.assertIn(
            "`Locuință (reg.)` pentru un răspuns comun ca `CASĂ`",
            rate_prompt,
        )

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
            model=PRIMARY_MODEL.model_id,
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
            model=PRIMARY_MODEL.model_id,
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
                ("Condiționare", ["DE", "LA"]),
                ("Auxiliar verbal", ["AL"]),
                ("Verb auxiliar", ["DE", "A"]),
            ],
            model=PRIMARY_MODEL.model_id,
        )

        prompt = client.prompts[0]
        self.assertIn("Încercări anterioare eșuate", prompt)
        self.assertIn("'Condiționare' → propus: DE, LA", prompt)
        self.assertIn("'Auxiliar verbal' → propus: AL", prompt)

    def test_rewrite_prompt_prefers_all_wrong_guesses(self):
        client = _RecordingClient(["Parte a conjugării verbale"])

        rewrite_definition(
            client,
            word="AR",
            original="ar",
            theme="",
            previous_definition="Verb auxiliar",
            wrong_guess="DE",
            wrong_guesses=["DE", "LA", "PE"],
            model=PRIMARY_MODEL.model_id,
        )

        prompt = client.prompts[0]
        self.assertIn("Rezolvitorul a propus: DE, LA, PE", prompt)

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
            model=PRIMARY_MODEL.model_id,
        )

        self.assertNotIn("Încercări anterioare eșuate", client.prompts[0])

    def test_generate_definition_retries_after_single_word_gloss(self):
        client = _RecordingClient([
            "Pământ",
            "Pământ fertil, brun-închis și afânat.",
        ])

        definition = generate_definition(
            client,
            word="MUL",
            original="mul",
            theme="",
            retries=2,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual("Pământ fertil, brun-închis și afânat.", definition)
        self.assertEqual(2, len(client.prompts))
        self.assertIn("single-word gloss", client.prompts[1])
        self.assertIn("minimum 2 cuvinte", client.prompts[1])

    def test_rewrite_definition_retries_after_single_word_gloss(self):
        client = _RecordingClient([
            "Pământ",
            "Pământ fertil, brun-închis și afânat.",
        ])

        definition = rewrite_definition(
            client,
            word="MUL",
            original="mul",
            theme="",
            previous_definition="Pământ",
            wrong_guess="ARG",
            retries=2,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual("Pământ fertil, brun-închis și afânat.", definition)
        self.assertEqual(2, len(client.prompts))
        self.assertIn("single-word gloss", client.prompts[1])
        self.assertIn("minimum 2 cuvinte", client.prompts[1])

    def test_generate_definition_retries_after_dangling_ending(self):
        client = _RecordingClient([
            "Împușcarea unui câine asupra unei",
            "Îndemnarea unui câine să atace o persoană.",
        ])

        definition = generate_definition(
            client,
            word="AMUTARE",
            original="amuțare",
            theme="",
            retries=2,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual("Îndemnarea unui câine să atace o persoană.", definition)
        self.assertEqual(2, len(client.prompts))
        self.assertIn("dangling ending", client.prompts[1])

    def test_rewrite_definition_retries_after_dangling_ending(self):
        client = _RecordingClient([
            "Împușcarea unui câine asupra unei",
            "Îndemnarea unui câine să atace o persoană.",
        ])

        definition = rewrite_definition(
            client,
            word="AMUTARE",
            original="amuțare",
            theme="",
            previous_definition="Împușcarea unui câine.",
            wrong_guess="ÎMPUSCARE",
            retries=2,
            model=PRIMARY_MODEL.model_id,
        )

        self.assertEqual("Îndemnarea unui câine să atace o persoană.", definition)
        self.assertEqual(2, len(client.prompts))
        self.assertIn("dangling ending", client.prompts[1])

    def test_rewrite_definition_returns_last_structural_rejection_when_requested(self):
        client = _RecordingClient(["Pământ"])

        result = rewrite_definition(
            client,
            word="MUL",
            original="mul",
            theme="",
            previous_definition="Pământ",
            wrong_guess="ARG",
            retries=1,
            model=PRIMARY_MODEL.model_id,
            return_diagnostics=True,
        )

        self.assertEqual(
            RewriteAttemptResult(
                definition="Pământ",
                last_rejection="single-word gloss",
            ),
            result,
        )


if __name__ == "__main__":
    unittest.main()
