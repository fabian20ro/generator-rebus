from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from rebus_generator.platform.llm.llm_dispatch import WorkItem, WorkVote, run_single_model_workload
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.llm.models import PRIMARY_MODEL, ModelConfig
from rebus_generator.platform.persistence.supabase_ops import execute_logged_update
from rebus_generator.platform.io.runtime_logging import log
from rebus_generator.domain.guards.title_guards import normalize_title_key, review_title_candidate as _review_title_candidate
from rebus_generator.workflows.retitle.rate import rate_title_creativity, rate_title_creativity_pair
from rebus_generator.workflows.retitle.sanitize import FALLBACK_TITLES, TitleGenerationResult

from .load import stored_title_score


@dataclass
class PreparedTitleUpdate:
    apply: bool
    new_title: str
    new_score: int | None
    old_title: str
    old_score: int | None
    reason: str = ""
    backfill_old_score: int | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def backfill_title_score(
    supabase,
    puzzle_row: dict,
    score: int,
    *,
    dry_run: bool,
) -> None:
    if dry_run:
        puzzle_row["title_score"] = score
        return
    execute_logged_update(
        supabase,
        "crossword_puzzles",
        {"title_score": score, "updated_at": now_iso()},
        eq_filters={"id": puzzle_row["id"]},
    )
    puzzle_row["title_score"] = score


def resolve_old_title_score(
    puzzle_row: dict,
    words: list[str],
    rate_client,
    *,
    multi_model: bool,
    runtime: LmRuntime,
) -> tuple[int, bool, str | None]:
    stored_score = stored_title_score(puzzle_row)
    if stored_score is not None:
        return stored_score, False, None
    old_title = puzzle_row.get("title", "")
    reviewed = _review_title_candidate(old_title, input_words=words)
    if not reviewed.valid:
        return 0, True, reviewed.feedback
    if multi_model:
        rating = rate_title_creativity_pair(old_title, words, rate_client, runtime=runtime)
        if not rating.complete:
            return 0, False, "evaluare incompletă"
        return rating.score, True, None
    score_model = PRIMARY_MODEL
    items = [
        WorkItem[dict[str, object], tuple[int, str]](
            item_id="old_title",
            task_kind="title_rate_single",
            payload={"title": old_title, "words": list(words)},
            pending_models={score_model.model_id},
        )
    ]

    def _runner(item: WorkItem[dict[str, object], tuple[int, str]], model: ModelConfig) -> WorkVote[tuple[int, str]]:
        score, feedback = rate_title_creativity(
            str(item.payload["title"]),
            list(item.payload["words"]),
            rate_client,
            model_config=model,
        )
        if score <= 0 and feedback in {"api error", "parse error"}:
            return WorkVote(model_id=model.model_id, value=None, source=feedback, terminal=True, terminal_reason=feedback)
        return WorkVote(model_id=model.model_id, value=(score, feedback), source="ok")

    run_single_model_workload(
        runtime=runtime,
        model=score_model,
        items=items,
        purpose="title_rate",
        runner=_runner,
        task_label="title_rate_single",
    )
    vote = items[0].votes.get(score_model.model_id)
    if vote is None or vote.value is None:
        return 0, False, "evaluare incompletă"
    old_score, _ = vote.value
    return old_score, True, None


def prepare_title_update(
    puzzle_row: dict,
    title_result: TitleGenerationResult,
    rate_client,
    *,
    multi_model: bool,
    runtime: LmRuntime | None,
    forbidden_title_keys: set[str] | None,
    words: list[str],
) -> PreparedTitleUpdate:
    puzzle_id = puzzle_row["id"]
    old_title = puzzle_row.get("title", "")
    old_title_key = normalize_title_key(old_title)
    if title_result.used_fallback:
        log(f'  [{puzzle_id}] "{old_title}" -> skipped, no valid title candidate')
        return PreparedTitleUpdate(False, "", None, old_title, stored_title_score(puzzle_row), "fallback")
    new_title = title_result.title
    new_title_key = normalize_title_key(new_title)
    if new_title_key == old_title_key:
        log(f'  [{puzzle_id}] "{old_title}" -> unchanged')
        return PreparedTitleUpdate(False, new_title, title_result.score if title_result.score_complete else None, old_title, stored_title_score(puzzle_row), "unchanged")
    if forbidden_title_keys and new_title_key in forbidden_title_keys:
        log(f'  [{puzzle_id}] "{old_title}" -> "{new_title}" — skipped, duplicate normalized title')
        return PreparedTitleUpdate(False, new_title, title_result.score if title_result.score_complete else None, old_title, stored_title_score(puzzle_row), "duplicate")
    is_fallback = old_title in FALLBACK_TITLES
    runtime = runtime or LmRuntime(multi_model=multi_model)
    if not is_fallback:
        old_score, should_backfill_old_score, invalid_reason = resolve_old_title_score(
            puzzle_row,
            words,
            rate_client,
            multi_model=multi_model,
            runtime=runtime,
        )
        if invalid_reason:
            log(f'  [{puzzle_id}] "{old_title}" old title invalid -> score=0 ({invalid_reason})')
        elif should_backfill_old_score:
            log(f'  [{puzzle_id}] "{old_title}" old title_score resolved -> {old_score}')
        new_score = title_result.score
        if new_score <= old_score:
            log(
                f'  [{puzzle_id}] "{old_title}" (score={old_score}) '
                f'-> "{new_title}" (score={new_score}) — skipped, not better'
            )
            if should_backfill_old_score:
                puzzle_row["title_score"] = old_score
            return PreparedTitleUpdate(
                False,
                new_title,
                new_score,
                old_title,
                old_score,
                "not_better",
                old_score if should_backfill_old_score else None,
            )
        log(f'  [{puzzle_id}] "{old_title}" (score={old_score}) -> "{new_title}" (score={new_score})')
        if should_backfill_old_score:
            puzzle_row["title_score"] = old_score
        return PreparedTitleUpdate(True, new_title, new_score if title_result.score_complete else None, old_title, old_score)
    log(f'  [{puzzle_id}] "{old_title}" (fallback) -> "{new_title}" (score={title_result.score})')
    return PreparedTitleUpdate(
        True,
        new_title,
        title_result.score if title_result.score_complete else None,
        old_title,
        stored_title_score(puzzle_row),
    )


def apply_title_update(
    supabase,
    puzzle_row: dict,
    prepared: PreparedTitleUpdate,
    *,
    dry_run: bool,
) -> bool:
    puzzle_id = puzzle_row["id"]
    if not prepared.apply:
        if prepared.backfill_old_score is not None:
            backfill_title_score(supabase, puzzle_row, prepared.backfill_old_score, dry_run=dry_run)
        return False
    if not dry_run:
        execute_logged_update(
            supabase,
            "crossword_puzzles",
            {"title": prepared.new_title, "title_score": prepared.new_score, "updated_at": now_iso()},
            eq_filters={"id": puzzle_id},
        )
    puzzle_row["title"] = prepared.new_title
    puzzle_row["title_score"] = prepared.new_score
    return True
