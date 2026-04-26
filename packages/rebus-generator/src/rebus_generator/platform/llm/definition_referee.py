import time
from collections import Counter
from dataclasses import dataclass, field

from openai import OpenAI

from rebus_generator.prompts.loader import load_system_prompt
from rebus_generator.workflows.canonicals.domain_service import aggregate_referee_votes
from rebus_generator.domain.clue_canon_types import (
    DefinitionComparisonAttempt,
    DefinitionComparisonVote,
    DefinitionRefereeDiagnostics,
    DefinitionRefereeInput,
    DefinitionRefereeResult,
)
from .llm_client import (
    _chat_completion_create,
    _resolve_model_name,
    _extract_json_object,
    _clean_response,
    llm_attempt_temperatures,
    short_form_max_tokens,
)
from .llm_dispatch import WorkItem, WorkStep, WorkVote, run_llm_workload
from .lm_runtime import LmRuntime
from .models import PRIMARY_MODEL, SECONDARY_MODEL, chat_max_tokens
from .prompt_builders import (
    _build_clue_tiebreak_prompt,
    _build_clue_compare_prompt,
    _build_puzzle_tiebreak_prompt,
)
from rebus_generator.platform.io.runtime_logging import log

_REFEREE_BATCH_SIZE_HISTOGRAM: Counter[int] = Counter()


def reset_referee_batch_stats() -> None:
    _REFEREE_BATCH_SIZE_HISTOGRAM.clear()


def referee_batch_stats_snapshot() -> dict[str, object]:
    return {
        "batch_size_histogram": {
            str(size): count
            for size, count in sorted(_REFEREE_BATCH_SIZE_HISTOGRAM.items())
        }
    }


@dataclass(frozen=True)
class AdaptiveRefereeBatchResult:
    results: dict[str, DefinitionRefereeResult]
    total_votes: int
    phase1_requests: int = 0
    phase2_requests: int = 0
    invalid_compare_json_primary: int = 0
    invalid_compare_json_secondary: int = 0
    step_metrics: list[dict[str, object]] = field(default_factory=list)


def _pick_tiebreak_winner(raw: str) -> str:
    cleaned = _clean_response(raw).upper()
    if cleaned.startswith("B"):
        return "B"
    return "A"


def compare_definition_variants(
    client: OpenAI,
    word: str,
    answer_length: int,
    definition_a: str,
    definition_b: str,
    *,
    model: str | None = None,
) -> DefinitionComparisonVote:
    attempt = _compare_definition_variant_attempt(
        client,
        word,
        answer_length,
        definition_a,
        definition_b,
        model=model,
    )
    if attempt.vote is not None:
        return attempt.vote
    resolved_model = _resolve_model_name(model)
    return DefinitionComparisonVote(
        model_id=resolved_model,
        same_meaning=False,
        better="equal",
        reason="compare_failed",
    )


def compare_definition_variants_attempt(
    client: OpenAI,
    word: str,
    answer_length: int,
    definition_a: str,
    definition_b: str,
    *,
    model: str | None = None,
) -> DefinitionComparisonAttempt:
    return _compare_definition_variant_attempt(
        client,
        word,
        answer_length,
        definition_a,
        definition_b,
        model=model,
    )


def _compare_definition_variant_attempt(
    client: OpenAI,
    word: str,
    answer_length: int,
    definition_a: str,
    definition_b: str,
    *,
    model: str | None = None,
) -> DefinitionComparisonAttempt:
    prompt = _build_clue_compare_prompt(word, answer_length, definition_a, definition_b)
    retry_prompt = (
        "\nRăspunde strict cu un singur obiect JSON valid de forma "
        '{"same_meaning": true|false, "better": "A"|"B"|"equal"} '
        "fără text suplimentar."
    )
    resolved_model = _resolve_model_name(model)
    max_tokens = short_form_max_tokens(
        model=resolved_model,
        purpose="clue_compare",
        requested_max_tokens=chat_max_tokens(resolved_model),
    )
    attempt_temperatures = llm_attempt_temperatures(
        temperature=0.1,
        default_temperature=0.1,
    )
    for attempt_index, attempt_temperature in enumerate(attempt_temperatures):
        compare_started = time.monotonic()
        try:
            response = _chat_completion_create(
                client,
                model=resolved_model,
                messages=[
                    {"role": "system", "content": load_system_prompt("clue_compare")},
                    {"role": "user", "content": prompt},
                ],
                temperature=attempt_temperature,
                max_tokens=max_tokens,
                purpose="clue_compare",
            )
            elapsed = time.monotonic() - compare_started
            if elapsed > 10.0:
                log(
                    "  [slow compare "
                    f"model={resolved_model} attempt={attempt_index + 1} seconds={elapsed:.2f}]"
                )
            data = _extract_json_object(response.choices[0].message.content or "")
            if not data:
                log(
                    "  [invalid compare json "
                    f"model={resolved_model} attempt={attempt_index + 1} seconds={elapsed:.2f}]"
                )
                if attempt_index == len(attempt_temperatures) - 1:
                    return DefinitionComparisonAttempt(
                        model_id=resolved_model,
                        model_role="",
                        valid_vote=False,
                        parse_status="invalid_json",
                        latency_seconds=elapsed,
                    )
                prompt += retry_prompt
                continue
            better = str(data.get("better", "equal")).strip().upper()
            if better not in {"A", "B", "EQUAL"}:
                better = "equal"
            else:
                better = better.lower() if better == "EQUAL" else better
            return DefinitionComparisonAttempt(
                model_id=resolved_model,
                model_role="",
                valid_vote=True,
                parse_status="ok",
                latency_seconds=elapsed,
                vote=DefinitionComparisonVote(
                    model_id=resolved_model,
                    same_meaning=bool(data.get("same_meaning")),
                    better=better,
                    reason=str(data.get("reason", "")).strip(),
                ),
            )
        except Exception as exc:
            elapsed = time.monotonic() - compare_started
            log(
                "  [compare exception "
                f"model={resolved_model} attempt={attempt_index + 1} seconds={elapsed:.2f} error={exc}]"
            )
            if attempt_index < len(attempt_temperatures) - 1:
                prompt += retry_prompt
                continue
            return DefinitionComparisonAttempt(
                model_id=resolved_model,
                model_role="",
                valid_vote=False,
                parse_status="exception",
                latency_seconds=elapsed,
                error_message=str(exc),
            )
    return DefinitionComparisonAttempt(
        model_id=resolved_model,
        model_role="",
        valid_vote=False,
        parse_status="invalid_json",
    )


def _remap_swapped_vote(vote: DefinitionComparisonVote) -> DefinitionComparisonVote:
    better = vote.better
    if better == "A":
        better = "B"
    elif better == "B":
        better = "A"
    return DefinitionComparisonVote(
        model_id=vote.model_id,
        same_meaning=vote.same_meaning,
        better=better,
        reason=vote.reason,
    )


def _with_diagnostics(
    result: DefinitionRefereeResult,
    diagnostics: DefinitionRefereeDiagnostics,
) -> DefinitionRefereeResult:
    return DefinitionRefereeResult(
        same_meaning_votes=result.same_meaning_votes,
        better_a_votes=result.better_a_votes,
        better_b_votes=result.better_b_votes,
        equal_votes=result.equal_votes,
        votes=result.votes,
        diagnostics=diagnostics,
    )


def _build_referee_diagnostics(
    request_id: str,
    attempts: list[DefinitionComparisonAttempt],
) -> DefinitionRefereeDiagnostics:
    primary_valid_votes = 0
    secondary_valid_votes = 0
    for attempt in attempts:
        if not attempt.valid_vote:
            continue
        if attempt.model_role == "primary":
            primary_valid_votes += 1
        elif attempt.model_role == "secondary":
            secondary_valid_votes += 1
    return DefinitionRefereeDiagnostics(
        request_id=request_id,
        attempts=list(attempts),
        primary_valid_votes=primary_valid_votes,
        secondary_valid_votes=secondary_valid_votes,
    )


def run_definition_referee_batch(
    client: OpenAI,
    runtime,
    requests: list[DefinitionRefereeInput],
    multi_model: bool = True,
) -> dict[str, DefinitionRefereeResult]:
    return run_definition_referee_adaptive_batch(client, runtime, requests, multi_model=multi_model).results


def run_definition_referee_adaptive_batch(
    client: OpenAI,
    runtime,
    requests: list[DefinitionRefereeInput],
    multi_model: bool = True,
) -> AdaptiveRefereeBatchResult:
    if not requests:
        return AdaptiveRefereeBatchResult(results={}, total_votes=0)
    _REFEREE_BATCH_SIZE_HISTOGRAM[len(requests)] += 1
    if runtime is None:
        runtime = LmRuntime(multi_model=multi_model)

    models = [PRIMARY_MODEL]
    if multi_model:
        models.append(SECONDARY_MODEL)
    model_roles = {
        PRIMARY_MODEL.model_id: "primary",
        SECONDARY_MODEL.model_id: "secondary",
    }
    model_swaps = {
        PRIMARY_MODEL.model_id: False,
        SECONDARY_MODEL.model_id: True,
    }
    model_ids = [model.model_id for model in models]
    items = [
        WorkItem[DefinitionRefereeInput, DefinitionComparisonAttempt](
            item_id=request.request_id,
            task_kind="definition_referee",
            payload=request,
            pending_models=set(model_ids),
        )
        for request in requests
    ]

    def _runner(
        item: WorkItem[DefinitionRefereeInput, DefinitionComparisonAttempt],
        model,
    ) -> WorkVote[DefinitionComparisonAttempt]:
        request = item.payload
        swap = model_swaps.get(model.model_id, False)
        model_role = model_roles.get(model.model_id, "")
        left = request.definition_b if swap else request.definition_a
        right = request.definition_a if swap else request.definition_b
        attempt = _compare_definition_variant_attempt(
            client,
            request.word,
            request.answer_length,
            left,
            right,
            model=model.model_id,
        )
        vote = _remap_swapped_vote(attempt.vote) if (swap and attempt.vote is not None) else attempt.vote
        return WorkVote(
            model_id=model.model_id,
            value=DefinitionComparisonAttempt(
                model_id=attempt.model_id,
                model_role=model_role,
                valid_vote=attempt.valid_vote,
                parse_status=attempt.parse_status,
                latency_seconds=attempt.latency_seconds,
                vote=vote,
                error_message=attempt.error_message,
            ),
            source=attempt.parse_status,
        )

    run_llm_workload(
        runtime=runtime,
        models=models,
        items=items,
        steps=[
            WorkStep(
                model_id=model.model_id,
                purpose="definition_referee",
                runner=_runner,
            )
            for model in models
        ],
        task_label="definition_referee",
    )

    results: dict[str, DefinitionRefereeResult] = {}
    total_votes = 0
    phase1_requests = 0
    phase2_requests = 0
    invalid_compare_json_primary = 0
    invalid_compare_json_secondary = 0
    step_metrics: list[dict[str, object]] = []

    for step_index, model in enumerate(models):
        model_id = model.model_id
        model_role = model_roles[model_id]
        requests_started = sum(1 for item in items if model_id in item.votes)
        if model_role == "primary":
            phase1_requests += requests_started
        else:
            phase2_requests += requests_started
        completed_after_step = len(items) if step_index == len(models) - 1 else 0
        step_metrics.append({
            "step_index": step_index,
            "model_id": model_id,
            "model_role": model_role,
            "requests_started": requests_started,
            "requests_completed_after_step": completed_after_step,
            "requests_remaining_after_step": max(0, len(items) - completed_after_step),
        })

    for item in items:
        attempts: list[DefinitionComparisonAttempt] = []
        votes: list[DefinitionComparisonVote] = []
        for model_id in model_ids:
            vote = item.votes.get(model_id)
            attempt = vote.value if vote is not None else None
            if attempt is None:
                continue
            attempts.append(attempt)
            if attempt.vote is not None:
                votes.append(attempt.vote)
            elif attempt.parse_status == "invalid_json":
                if attempt.model_role == "primary":
                    invalid_compare_json_primary += 1
                elif attempt.model_role == "secondary":
                    invalid_compare_json_secondary += 1
            total_votes += 1
        results[item.item_id] = _with_diagnostics(
            aggregate_referee_votes(votes),
            _build_referee_diagnostics(item.item_id, attempts),
        )

    return AdaptiveRefereeBatchResult(
        results=results,
        total_votes=total_votes,
        phase1_requests=phase1_requests,
        phase2_requests=phase2_requests,
        invalid_compare_json_primary=invalid_compare_json_primary,
        invalid_compare_json_secondary=invalid_compare_json_secondary,
        step_metrics=step_metrics,
    )


def run_definition_referee(
    client: OpenAI,
    runtime,
    word: str,
    answer_length: int,
    definition_a: str,
    definition_b: str,
    multi_model: bool = True,
) -> DefinitionRefereeResult:
    return run_definition_referee_batch(
        client,
        runtime,
        [DefinitionRefereeInput(
            request_id="single",
            word=word,
            answer_length=answer_length,
            definition_a=definition_a,
            definition_b=definition_b,
        )],
        multi_model=multi_model,
    )["single"]


def choose_better_clue_variant(
    client: OpenAI,
    word: str,
    answer_length: int,
    definition_a: str,
    definition_b: str,
    model: str | None = None,
) -> str:
    prompt = _build_clue_tiebreak_prompt(
        word, answer_length, definition_a, definition_b
    )
    try:
        resolved_model = _resolve_model_name(model)
        max_tokens = short_form_max_tokens(
            model=resolved_model,
            purpose="clue_tiebreaker",
            requested_max_tokens=chat_max_tokens(resolved_model),
        )
        response = _chat_completion_create(
            client,
            model=resolved_model,
            messages=[
                {"role": "system", "content": load_system_prompt("clue_tiebreaker", model_id=resolved_model)},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=max_tokens,
            purpose="clue_tiebreaker",
        )
        return _pick_tiebreak_winner(response.choices[0].message.content or "")
    except Exception:
        return "A"


def choose_better_puzzle_variant(
    client: OpenAI,
    summary_a: str,
    summary_b: str,
    model: str | None = None,
) -> str:
    prompt = _build_puzzle_tiebreak_prompt(summary_a, summary_b)
    try:
        resolved_model = _resolve_model_name(model)
        max_tokens = chat_max_tokens(resolved_model)
        response = _chat_completion_create(
            client,
            model=resolved_model,
            messages=[
                {"role": "system", "content": load_system_prompt("puzzle_tiebreaker", model_id=resolved_model)},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=max_tokens,
            purpose="puzzle_tiebreaker",
        )
        return _pick_tiebreak_winner(response.choices[0].message.content or "")
    except Exception:
        return "A"
