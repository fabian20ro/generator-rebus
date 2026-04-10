from __future__ import annotations

from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.workflows.retitle.batch import _RetitleBatchState, generate_title_results_batch
from rebus_generator.workflows.retitle.generate import generate_creative_title_result

from .load import fetch_clues, fetch_puzzles, select_duplicate_puzzles_for_retitle, select_puzzles_for_retitle
from .persist import apply_title_update, prepare_title_update

RETITLE_BATCH_SIZE = 10


def retitle_puzzle(
    supabase,
    puzzle_row: dict,
    ai_client,
    rate_client,
    *,
    dry_run: bool = False,
    multi_model: bool = True,
    runtime: LmRuntime | None = None,
    forbidden_title_keys: set[str] | None = None,
) -> bool:
    puzzle_id = puzzle_row["id"]
    clues = fetch_clues(supabase, puzzle_id)
    if not clues:
        return False

    words = [c["word_normalized"] for c in clues if c.get("word_normalized")]
    definitions = [c["definition"] for c in clues if c.get("definition")]
    if not words or not definitions:
        return False

    title_result = generate_creative_title_result(
        words,
        definitions,
        client=ai_client,
        rate_client=rate_client,
        runtime=runtime,
        multi_model=multi_model,
        forbidden_title_keys=forbidden_title_keys,
    )
    prepared = prepare_title_update(
        puzzle_row,
        title_result,
        rate_client,
        multi_model=multi_model,
        runtime=runtime,
        forbidden_title_keys=forbidden_title_keys,
        words=words,
    )
    return apply_title_update(supabase, puzzle_row, prepared, dry_run=dry_run)


__all__ = [
    "RETITLE_BATCH_SIZE",
    "_RetitleBatchState",
    "apply_title_update",
    "fetch_clues",
    "fetch_puzzles",
    "generate_creative_title_result",
    "generate_title_results_batch",
    "prepare_title_update",
    "retitle_puzzle",
    "select_duplicate_puzzles_for_retitle",
    "select_puzzles_for_retitle",
]

