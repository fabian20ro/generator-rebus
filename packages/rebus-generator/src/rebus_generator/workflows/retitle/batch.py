from __future__ import annotations

from dataclasses import dataclass, field

from rebus_generator.platform.llm.llm_dispatch import WorkItem, WorkVote, run_single_model_workload
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.llm.models import PRIMARY_MODEL, SECONDARY_MODEL, ModelConfig
from rebus_generator.platform.io.runtime_logging import log
from rebus_generator.domain.guards.title_guards import normalize_title_key, review_title_candidate as _review_title_candidate
from rebus_generator.workflows.retitle.generate import _generate_candidate_with_active_model
from rebus_generator.workflows.retitle.rate import rate_title_creativity_batch
from rebus_generator.workflows.retitle.sanitize import (
    FALLBACK_TITLES,
    MAX_TITLE_ROUNDS,
    NO_TITLE_LABEL,
    TITLE_MIN_CREATIVITY,
    TitleGenerationResult,
    _build_rejected_context,
)


@dataclass
class _RetitleBatchState:
    puzzle_row: dict
    words: list[str]
    definitions: list[str]
    forbidden_title_keys: set[str]
    best_result: TitleGenerationResult | None = None
    final_result: TitleGenerationResult | None = None
    rejected: list[tuple[str, str]] = field(default_factory=list)
    rejected_by_model: dict[str, list[tuple[str, str]]] = field(default_factory=lambda: {
        PRIMARY_MODEL.model_id: [],
        SECONDARY_MODEL.model_id: [],
    })

    @property
    def puzzle_id(self) -> str:
        return str(self.puzzle_row.get("id") or "")

    @property
    def done(self) -> bool:
        return self.final_result is not None


def _finalize_title_result(state: _RetitleBatchState) -> TitleGenerationResult:
    if state.final_result is not None:
        return state.final_result
    if state.best_result is not None and state.best_result.score > 0:
        return state.best_result
    return TitleGenerationResult(NO_TITLE_LABEL, 0, "niciun titlu valid", used_fallback=True, score_complete=False)


def _update_best_result(state: _RetitleBatchState, result: TitleGenerationResult) -> None:
    if (
        state.best_result is None
        or result.score > state.best_result.score
        or (
            result.score == state.best_result.score
            and len(result.title.split()) < len(state.best_result.title.split())
        )
    ):
        state.best_result = result
    if result.score >= TITLE_MIN_CREATIVITY:
        state.final_result = result


def _generate_batch_candidates(
    states: list[_RetitleBatchState],
    client,
    *,
    runtime: LmRuntime,
    active_model: ModelConfig,
    round_idx: int,
) -> list[tuple[_RetitleBatchState, str]]:
    items: list[WorkItem[_RetitleBatchState, str]] = []
    for index, state in enumerate(states, start=1):
        if state.done:
            continue
        items.append(
            WorkItem(
                item_id=f"{active_model.model_id}:{index}:{state.puzzle_id}",
                task_kind="title_generate",
                payload=state,
                pending_models={active_model.model_id},
            )
        )
    if not items:
        return []

    def _runner(item: WorkItem[_RetitleBatchState, str], model: ModelConfig) -> WorkVote[str]:
        state = item.payload
        rejected_context = _build_rejected_context(
            state.rejected_by_model.setdefault(model.model_id, [])
        )
        raw_title = _generate_candidate_with_active_model(
            state.definitions,
            state.words,
            client,
            active_model=model,
            rejected_context=rejected_context,
            empty_retry_instruction="Răspunde obligatoriu cu un singur titlu concret de 2-5 cuvinte, exclusiv în limba română.",
        )
        return WorkVote(model_id=model.model_id, value=raw_title, source="ok")

    run_single_model_workload(
        runtime=runtime,
        model=active_model,
        items=items,
        purpose="title_generate",
        runner=_runner,
        task_label="retitle_title_generate",
    )

    valid_candidates: list[tuple[_RetitleBatchState, str]] = []
    for item in items:
        state = item.payload
        raw_title = str(item.votes[active_model.model_id].value or "")
        if not raw_title.strip():
            log(
                f'  [{state.puzzle_id}] Title round {round_idx} [{active_model.display_name}]: "(gol)" -> creativity=0/10 (titlu gol)'
            )
            continue

        reviewed = _review_title_candidate(raw_title, input_words=state.words)
        display_title = reviewed.title or raw_title.strip() or "(gol)"
        if not reviewed.valid:
            log(
                f'  [{state.puzzle_id}] Title round {round_idx} [{active_model.display_name}]: "{display_title}" -> creativity=0/10 ({reviewed.feedback})'
            )
            state.rejected.append((display_title, reviewed.feedback))
            state.rejected_by_model.setdefault(active_model.model_id, []).append((display_title, reviewed.feedback))
            continue

        title_key = normalize_title_key(reviewed.title)
        rejected_keys = {normalize_title_key(title) for title, _ in state.rejected}
        if reviewed.title in FALLBACK_TITLES:
            state.rejected.append((reviewed.title, "fallback generic"))
            state.rejected_by_model.setdefault(active_model.model_id, []).append((reviewed.title, "fallback generic"))
            continue
        if title_key in rejected_keys:
            state.rejected.append((reviewed.title, "titlu deja respins"))
            state.rejected_by_model.setdefault(active_model.model_id, []).append((reviewed.title, "titlu deja respins"))
            continue
        if title_key and title_key in state.forbidden_title_keys:
            log(
                f'  [{state.puzzle_id}] Title round {round_idx} [{active_model.display_name}]: "{reviewed.title}" -> creativity=0/10 (titlu deja folosit)'
            )
            state.rejected.append((reviewed.title, "titlu deja folosit"))
            state.rejected_by_model.setdefault(active_model.model_id, []).append((reviewed.title, "titlu deja folosit"))
            continue

        valid_candidates.append((state, reviewed.title))
    return valid_candidates


def _rate_batch_candidates(
    candidates: list[tuple[_RetitleBatchState, str]],
    rate_client,
    *,
    generator_model: ModelConfig,
    runtime: LmRuntime,
    round_idx: int,
    multi_model: bool,
) -> None:
    if not candidates:
        return

    rating_results = rate_title_creativity_batch(
        [(state.puzzle_id, title, state.words) for state, title in candidates],
        rate_client,
        multi_model=multi_model,
        runtime=runtime,
    )

    for state, title in candidates:
        rating = rating_results.get(state.puzzle_id)
        if rating is None or not rating.complete:
            log(
                f'  [{state.puzzle_id}] Title round {round_idx} [{generator_model.display_name} -> pair rated]: "{title}" -> creativity=0/10 (evaluare incompletă)'
            )
            state.rejected.append((title, "evaluare incompletă"))
            state.rejected_by_model.setdefault(generator_model.model_id, []).append((title, "evaluare incompletă"))
            continue

        score = rating.score
        feedback = rating.feedback
        log(
            f'  [{state.puzzle_id}] Title round {round_idx} [{generator_model.display_name} -> pair rated]: "{title}" -> creativity={score}/10 ({feedback})'
        )
        result = TitleGenerationResult(title, score, feedback, score_complete=True)
        _update_best_result(state, result)
        if state.done:
            continue
        state.rejected.append((title, feedback))
        state.rejected_by_model.setdefault(generator_model.model_id, []).append((title, feedback))


def generate_title_results_batch(
    states: list[_RetitleBatchState],
    client,
    rate_client,
    *,
    runtime: LmRuntime,
    multi_model: bool,
) -> dict[str, TitleGenerationResult]:
    if not states:
        return {}

    for round_idx in range(1, MAX_TITLE_ROUNDS + 1):
        pending = [state for state in states if not state.done]
        if not pending:
            break

        primary_candidates = _generate_batch_candidates(
            pending,
            client,
            runtime=runtime,
            active_model=PRIMARY_MODEL,
            round_idx=round_idx,
        )

        if multi_model:
            _rate_batch_candidates(
                primary_candidates,
                rate_client,
                generator_model=PRIMARY_MODEL,
                runtime=runtime,
                round_idx=round_idx,
                multi_model=multi_model,
            )
            pending = [state for state in states if not state.done]
            if not pending:
                break
            secondary_candidates = _generate_batch_candidates(
                pending,
                client,
                runtime=runtime,
                active_model=SECONDARY_MODEL,
                round_idx=round_idx,
            )
            _rate_batch_candidates(
                secondary_candidates,
                rate_client,
                generator_model=SECONDARY_MODEL,
                runtime=runtime,
                round_idx=round_idx,
                multi_model=multi_model,
            )
        else:
            _rate_batch_candidates(
                primary_candidates,
                rate_client,
                generator_model=PRIMARY_MODEL,
                runtime=runtime,
                round_idx=round_idx,
                multi_model=multi_model,
            )

    return {state.puzzle_id: _finalize_title_result(state) for state in states}
