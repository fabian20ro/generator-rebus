from __future__ import annotations

import json
import re
from dataclasses import dataclass

from rebus_generator.platform.llm.ai_clues import consensus_score
from rebus_generator.platform.llm.llm_client import (
    RESPONSE_SOURCE_NO_THINKING_RETRY,
    RESPONSE_SOURCE_REASONING,
    _chat_completion_create,
    extract_json_object_with_status,
    llm_attempt_temperatures,
)
from rebus_generator.platform.llm.llm_dispatch import (
    WorkConclusion,
    WorkItem,
    WorkStep,
    WorkVote,
    run_llm_workload,
)
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.llm.models import ModelConfig, chat_max_tokens, get_active_models
from rebus_generator.prompts.loader import load_system_prompt, load_user_template
from rebus_generator.domain.diacritics import normalize

from .sanitize import TITLE_RATE_MAX_TOKENS, TitleRatingResult


@dataclass(frozen=True)
class _TitleRatingPayload:
    title: str
    words: list[str]


def rate_title_creativity(
    title: str,
    words: list[str],
    client,
    *,
    model_config: ModelConfig,
) -> tuple[int, str]:
    prompt = load_user_template("title_rate").format(
        title=title,
        words=", ".join(words[:10]),
    )
    attempt_temperatures = llm_attempt_temperatures(
        temperature=0.1,
        default_temperature=0.1,
    )
    for attempt_temperature in attempt_temperatures:
        try:
            response = _chat_completion_create(
                client,
                model=model_config.model_id,
                messages=[
                    {"role": "system", "content": load_system_prompt("title_rate")},
                    {"role": "user", "content": prompt},
                ],
                temperature=attempt_temperature,
                max_tokens=min(chat_max_tokens(model_config), TITLE_RATE_MAX_TOKENS),
                purpose="title_rate",
            )
            raw = response.choices[0].message.content or ""
            data, _parse_status = extract_json_object_with_status(raw)
            if data is not None:
                try:
                    score = int(data.get("creativity_score", 0))
                except (TypeError, ValueError):
                    score = 0
                return max(0, min(10, score)), str(data.get("feedback", "")).strip()
            if str(getattr(response, "_response_source", RESPONSE_SOURCE_REASONING)) == RESPONSE_SOURCE_NO_THINKING_RETRY:
                return 0, "parse error"
            prompt += "\nRăspunsul anterior nu a fost JSON valid. Răspunde acum strict cu un singur obiect JSON valid, fără text suplimentar."
        except Exception:
            return 0, "api error"
    return 0, "parse error"


def _rating_runtime(runtime: LmRuntime | None, *, multi_model: bool) -> LmRuntime:
    if runtime is not None:
        return runtime
    return LmRuntime(multi_model=multi_model)


def _combine_title_feedback(first: str, second: str) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for raw in (first, second):
        text = str(raw or "").strip()
        if not text:
            continue
        key = normalize(text)
        if key in seen:
            continue
        seen.add(key)
        parts.append(text)
    return " / ".join(parts)


def _title_rating_runner(client):
    def _run(item: WorkItem[_TitleRatingPayload, tuple[int, str]], model: ModelConfig) -> WorkVote[tuple[int, str]]:
        score, feedback = rate_title_creativity(
            item.payload.title,
            item.payload.words,
            client,
            model_config=model,
        )
        if score <= 0 and feedback in {"api error", "parse error"}:
            return WorkVote(model_id=model.model_id, value=None, source=feedback, terminal=True, terminal_reason=feedback)
        return WorkVote(model_id=model.model_id, value=(score, feedback), source="ok")

    return _run


def _title_rating_conclusion(item: WorkItem[_TitleRatingPayload, tuple[int, str]], *, expected_votes: int) -> WorkConclusion:
    if any(vote.terminal for vote in item.votes.values()):
        return WorkConclusion(
            failed=True,
            terminal_reason=next(
                (vote.terminal_reason for vote in item.votes.values() if vote.terminal_reason),
                "evaluare incompletă",
            ),
        )
    if len(item.votes) >= expected_votes:
        return WorkConclusion(complete=True)
    return WorkConclusion()


def rate_title_creativity_batch(
    titles: list[tuple[str, str, list[str]]],
    client,
    *,
    multi_model: bool,
    runtime: LmRuntime | None = None,
) -> dict[str, TitleRatingResult]:
    active_models = list(get_active_models(multi_model=multi_model))
    expected_votes = len(active_models)
    pair_mode = multi_model and expected_votes >= 2
    items = [
        WorkItem[_TitleRatingPayload, tuple[int, str]](
            item_id=item_id,
            task_kind="title_rate",
            payload=_TitleRatingPayload(title=title, words=list(words)),
            pending_models={model.model_id for model in active_models},
        )
        for item_id, title, words in titles
    ]
    run_llm_workload(
        runtime=_rating_runtime(runtime, multi_model=multi_model),
        models=active_models,
        items=items,
        steps=[
            WorkStep(
                model_id=model.model_id,
                purpose="title_rate",
                runner=_title_rating_runner(client),
                can_conclude=lambda item, expected_votes=expected_votes: _title_rating_conclusion(item, expected_votes=expected_votes),
            )
            for model in active_models
        ],
        task_label="title_rate",
    )
    results: dict[str, TitleRatingResult] = {}
    ordered_ids = [model.model_id for model in active_models]
    for item in items:
        votes = {
            model_id: item.votes[model_id].value
            for model_id in ordered_ids
            if model_id in item.votes and item.votes[model_id].value is not None
        }
        if not pair_mode:
            if len(votes) != 1:
                results[item.item_id] = TitleRatingResult(
                    0,
                    item.terminal_reason or "evaluare incompletă",
                    False,
                    {model_id: value for model_id, value in votes.items() if value is not None},
                )
                continue
            model_id = ordered_ids[0]
            score, feedback = votes[model_id]
            results[item.item_id] = TitleRatingResult(
                score=score,
                feedback=feedback,
                complete=True,
                votes={model_id: votes[model_id]},
            )
            continue
        if len(votes) != 2:
            results[item.item_id] = TitleRatingResult(
                0,
                item.terminal_reason or "evaluare incompletă",
                False,
                {model_id: value for model_id, value in votes.items() if value is not None},
            )
            continue
        first_score, first_feedback = votes[ordered_ids[0]]
        second_score, second_feedback = votes[ordered_ids[1]]
        results[item.item_id] = TitleRatingResult(
            score=consensus_score(first_score, second_score),
            feedback=_combine_title_feedback(first_feedback, second_feedback),
            complete=True,
            votes={model_id: votes[model_id] for model_id in ordered_ids},
        )
    return results


def rate_title_creativity_pair(
    title: str,
    words: list[str],
    client,
    *,
    runtime: LmRuntime | None = None,
) -> TitleRatingResult:
    return rate_title_creativity_batch([("single", title, words)], client, multi_model=True, runtime=runtime)["single"]
