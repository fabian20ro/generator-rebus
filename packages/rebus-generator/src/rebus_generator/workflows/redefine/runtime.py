from __future__ import annotations

from rebus_generator.platform.config import VERIFY_CANDIDATE_COUNT
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.domain.pipeline_state import WorkingPuzzle
from rebus_generator.domain.puzzle_metrics import evaluate_puzzle_state, score_puzzle_state
from rebus_generator.platform.io.runtime_logging import log
from rebus_generator.workflows.canonicals.scored_fallbacks import (
    apply_scored_canonical_fallbacks as apply_shared_scored_canonical_fallbacks,
    redefine_scored_fallback_policy,
)
from rebus_generator.workflows.redefine.rewrite_engine import run_rewrite_loop

from .load import build_working_puzzle, clue_row_sort_key, fetch_clues, fetch_puzzles, working_clue_map
from .persist import persist_redefined_puzzle

REDEFINE_ROUNDS = 7

def apply_scored_canonical_fallbacks(
    supabase,
    puzzle_row: dict,
    baseline_puzzle: WorkingPuzzle,
    candidate_puzzle: WorkingPuzzle,
    *,
    client=None,
    runtime: LmRuntime | None = None,
    multi_model: bool = True,
) -> dict[tuple[str, int, int], str]:
    return apply_shared_scored_canonical_fallbacks(
        target_puzzle=candidate_puzzle,
        puzzle_identity=str(puzzle_row.get("id") or ""),
        policy=redefine_scored_fallback_policy,
        reference_puzzle=baseline_puzzle,
        store_client=supabase,
        client=client,
        runtime=runtime,
        multi_model=multi_model,
        seed_parts=(str(puzzle_row.get("id") or ""),),
    )


def rewrite_puzzle_definitions(
    puzzle: WorkingPuzzle,
    client,
    *,
    rounds: int = REDEFINE_ROUNDS,
    multi_model: bool = True,
    verify_candidates: int = VERIFY_CANDIDATE_COUNT,
    runtime: LmRuntime | None = None,
) -> object:
    return run_rewrite_loop(
        puzzle,
        client,
        rounds=rounds,
        theme=puzzle.title or "Puzzle rebus",
        multi_model=multi_model,
        verify_candidates=verify_candidates,
        hybrid_deanchor=True,
        runtime=runtime,
    )


def redefine_puzzle(
    supabase,
    puzzle_row: dict,
    client,
    *,
    dry_run: bool = False,
    multi_model: bool = True,
    rounds: int = REDEFINE_ROUNDS,
    verify_candidates: int = VERIFY_CANDIDATE_COUNT,
    runtime: LmRuntime | None = None,
) -> int:
    puzzle_id = puzzle_row["id"]
    clue_rows = sorted(fetch_clues(supabase, puzzle_id), key=clue_row_sort_key)
    if not clue_rows:
        log(f"  [{puzzle_id}] No clues found, skipping")
        return 0

    baseline_puzzle = build_working_puzzle(puzzle_row, clue_rows)
    log(f"  [{puzzle_id}] {len(clue_rows)} clues, title: {baseline_puzzle.title}")
    runtime = runtime or LmRuntime(multi_model=multi_model)
    baseline_eval = evaluate_puzzle_state(
        baseline_puzzle,
        client,
        multi_model=multi_model,
        verify_candidates=verify_candidates,
        runtime=runtime,
    )
    baseline_puzzle.assessment = baseline_eval.assessment
    log(
        f"  [{puzzle_id}] baseline min={baseline_eval.assessment.min_rebus}/10 "
        f"avg={baseline_eval.assessment.avg_rebus:.1f}/10 "
        f"verified={baseline_eval.assessment.verified_count}/{baseline_eval.assessment.total_clues}"
    )

    candidate_puzzle = build_working_puzzle(puzzle_row, clue_rows)
    rewrite_puzzle_definitions(
        candidate_puzzle,
        client,
        rounds=rounds,
        multi_model=multi_model,
        verify_candidates=verify_candidates,
        runtime=runtime,
    )
    apply_scored_canonical_fallbacks(
        supabase,
        puzzle_row,
        baseline_puzzle,
        candidate_puzzle,
        client=client,
        runtime=runtime,
        multi_model=multi_model,
    )
    candidate_puzzle.assessment = score_puzzle_state(candidate_puzzle)
    log(
        f"  [{puzzle_id}] candidate min={candidate_puzzle.assessment.min_rebus}/10 "
        f"avg={candidate_puzzle.assessment.avg_rebus:.1f}/10 "
        f"verified={candidate_puzzle.assessment.verified_count}/{candidate_puzzle.assessment.total_clues}"
    )
    return persist_redefined_puzzle(
        supabase,
        puzzle_row,
        clue_rows,
        baseline_puzzle,
        candidate_puzzle,
        client,
        dry_run=dry_run,
        multi_model=multi_model,
        runtime=runtime,
    )


__all__ = [
    "REDEFINE_ROUNDS",
    "build_working_puzzle",
    "clue_row_sort_key",
    "fetch_clues",
    "fetch_puzzles",
    "redefine_puzzle",
    "rewrite_puzzle_definitions",
    "apply_scored_canonical_fallbacks",
]
