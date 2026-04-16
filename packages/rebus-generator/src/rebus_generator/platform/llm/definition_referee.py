import time
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
    short_form_max_tokens,
)
from .models import PRIMARY_MODEL, SECONDARY_MODEL, chat_max_tokens
from .prompt_builders import (
    _build_clue_tiebreak_prompt,
    _build_clue_compare_prompt,
    _build_puzzle_tiebreak_prompt,
)
from rebus_generator.platform.io.runtime_logging import log


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
    for attempt in range(2):
        compare_started = time.monotonic()
        try:
            response = _chat_completion_create(
                client,
                model=resolved_model,
                messages=[
                    {"role": "system", "content": load_system_prompt("clue_compare")},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=max_tokens,
                purpose="clue_compare",
            )
            elapsed = time.monotonic() - compare_started
            if elapsed > 10.0:
                log(
                    "  [slow compare "
                    f"model={resolved_model} attempt={attempt + 1} seconds={elapsed:.2f}]"
                )
            data = _extract_json_object(response.choices[0].message.content or "")
            if not data:
                log(
                    "  [invalid compare json "
                    f"model={resolved_model} attempt={attempt + 1} seconds={elapsed:.2f}]"
                )
                if attempt == 1:
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
                f"model={resolved_model} attempt={attempt + 1} seconds={elapsed:.2f} error={exc}]"
            )
            if attempt == 0:
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

    vote_steps = [
        (PRIMARY_MODEL, False, "primary"),
    ]
    if multi_model:
        vote_steps.append((SECONDARY_MODEL, True, "secondary"))
    
    active_request_ids = [request.request_id for request in requests]
    request_by_id = {request.request_id: request for request in requests}
    votes_by_request_id: dict[str, list[DefinitionComparisonVote]] = {
        request.request_id: [] for request in requests
    }
    attempts_by_request_id: dict[str, list[DefinitionComparisonAttempt]] = {
        request.request_id: [] for request in requests
    }
    results: dict[str, DefinitionRefereeResult] = {}
    total_votes = 0
    phase1_requests = 0
    phase2_requests = 0
    invalid_compare_json_primary = 0
    invalid_compare_json_secondary = 0
    step_metrics: list[dict[str, object]] = []

    for step_index, (model_config, swap, model_role) in enumerate(vote_steps):
        if not active_request_ids:
            break
        requests_started = len(active_request_ids)
        if model_role == "primary":
            phase1_requests += requests_started
        else:
            phase2_requests += requests_started
        if runtime is not None:
            runtime.activate(model_config, reason="definition_referee")
        completed_ids: list[str] = []
        for request_id in list(active_request_ids):
            request = request_by_id[request_id]
            left = request.definition_b if swap else request.definition_a
            right = request.definition_a if swap else request.definition_b
            attempt = _compare_definition_variant_attempt(
                client,
                request.word,
                request.answer_length,
                left,
                right,
                model=model_config.model_id,
            )
            vote = _remap_swapped_vote(attempt.vote) if (swap and attempt.vote is not None) else attempt.vote
            attempts_by_request_id[request_id].append(
                DefinitionComparisonAttempt(
                    model_id=attempt.model_id,
                    model_role=model_role,
                    valid_vote=attempt.valid_vote,
                    parse_status=attempt.parse_status,
                    latency_seconds=attempt.latency_seconds,
                    vote=vote,
                    error_message=attempt.error_message,
                )
            )
            if vote is not None:
                votes_by_request_id[request_id].append(vote)
            elif attempt.parse_status == "invalid_json":
                if model_role == "primary":
                    invalid_compare_json_primary += 1
                else:
                    invalid_compare_json_secondary += 1
            total_votes += 1
            diagnostics = _build_referee_diagnostics(request_id, attempts_by_request_id[request_id])
            if step_index == 0:
                continue
            results[request_id] = _with_diagnostics(
                aggregate_referee_votes(votes_by_request_id[request_id]),
                diagnostics,
            )
            completed_ids.append(request_id)
        if completed_ids:
            completed = set(completed_ids)
            active_request_ids = [
                request_id
                for request_id in active_request_ids
                if request_id not in completed
            ]
        step_metrics.append({
            "step_index": step_index,
            "model_id": model_config.model_id,
            "model_role": model_role,
            "requests_started": requests_started,
            "requests_completed_after_step": len(completed_ids),
            "requests_remaining_after_step": len(active_request_ids),
        })

    for request_id in active_request_ids:
        results[request_id] = _with_diagnostics(
            aggregate_referee_votes(votes_by_request_id[request_id]),
            _build_referee_diagnostics(request_id, attempts_by_request_id[request_id]),
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
            temperature=0.0,
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
            temperature=0.0,
            max_tokens=max_tokens,
            purpose="puzzle_tiebreaker",
        )
        return _pick_tiebreak_winner(response.choices[0].message.content or "")
    except Exception:
        return "A"
