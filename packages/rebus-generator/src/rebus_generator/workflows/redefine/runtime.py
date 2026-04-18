from __future__ import annotations

import copy

from rebus_generator.platform.config import VERIFY_CANDIDATE_COUNT
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.domain.diacritics import normalize
from rebus_generator.domain.pipeline_state import (
    ClueAssessment,
    ClueScores,
    WorkingClue,
    WorkingPuzzle,
    working_clue_from_entry,
)
from rebus_generator.domain.puzzle_metrics import evaluate_puzzle_state, score_puzzle_state
from rebus_generator.domain.score_helpers import LOCKED_REBUS, LOCKED_SEMANTIC, _needs_rewrite
from rebus_generator.platform.io.markdown_io import ClueEntry
from rebus_generator.platform.io.runtime_logging import log
from rebus_generator.platform.llm.ai_clues import compute_rebus_score
from rebus_generator.platform.persistence.clue_canon_store import ClueCanonStore
from rebus_generator.workflows.canonicals.domain_service import ClueCanonService, _extract_usage_label
from rebus_generator.workflows.redefine.rewrite_engine import run_rewrite_loop

from .load import build_working_puzzle, clue_row_sort_key, fetch_clues, fetch_puzzles, working_clue_map
from .persist import persist_redefined_puzzle

REDEFINE_ROUNDS = 7


def _normalized_definition(text: str) -> str:
    return " ".join(normalize(text or "").lower().split())


def _next_round_index(clue: WorkingClue) -> int:
    if not clue.history:
        return 1
    return max(version.round_index for version in clue.history) + 1


def _infer_answer_targeting(*, rebus_score: int, creativity_score: int, answer_length: int) -> int:
    best_guess = 1
    best_delta: tuple[int, int] | None = None
    for guessability in range(1, 11):
        computed = compute_rebus_score(guessability, creativity_score, answer_length=answer_length)
        delta = (abs(computed - rebus_score), abs(guessability - rebus_score))
        if best_delta is None or delta < best_delta:
            best_guess = guessability
            best_delta = delta
    return best_guess


def _assessment_from_representative_row(row: dict) -> ClueAssessment:
    clue = working_clue_from_entry(
        ClueEntry(
            row_number=1,
            word_normalized=str(row.get("word_normalized") or ""),
            word_original=str(row.get("word_original") or ""),
            word_type=str(row.get("word_type") or ""),
            definition=str(row.get("definition") or ""),
            verified=row.get("verified"),
            verify_note=str(row.get("verify_note") or ""),
        )
    )
    return copy.deepcopy(clue.current.assessment)


def _synthesize_fallback_assessment(clue: WorkingClue, canonical) -> ClueAssessment:
    creativity = int(canonical.creativity_score or 1)
    rebus = int(canonical.rebus_score or 1)
    targeting = _infer_answer_targeting(
        rebus_score=rebus,
        creativity_score=creativity,
        answer_length=len(clue.word_normalized),
    )
    return ClueAssessment(
        verified=bool(canonical.verified),
        verify_candidates=[clue.word_normalized] if canonical.verified else [],
        feedback="Definiție canonică reutilizată după eșecul redefinirii.",
        scores=ClueScores(
            semantic_exactness=int(canonical.semantic_score or 0),
            answer_targeting=targeting,
            ambiguity_risk=11 - targeting,
            family_leakage=False,
            language_integrity=10,
            creativity=creativity,
            rebus_score=rebus,
        ),
        verified_by="canonical_fallback",
        rated_by="canonical_fallback",
    )


def _apply_canonical_fallback(
    clue: WorkingClue,
    *,
    canonical,
    assessment: ClueAssessment,
) -> None:
    version = copy.deepcopy(clue.current)
    version.definition = canonical.definition
    version.round_index = _next_round_index(clue)
    version.source = "canonical_fallback"
    version.generated_by = "canonical_fallback"
    version.assessment = assessment
    clue.current = version
    clue.best = copy.deepcopy(version)
    clue.history.append(copy.deepcopy(version))
    semantic = clue.current.assessment.scores.semantic_exactness or 0
    rebus = clue.current.assessment.scores.rebus_score or 0
    clue.locked = clue.current.assessment.verified is True and semantic >= LOCKED_SEMANTIC and rebus >= LOCKED_REBUS


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
    store = ClueCanonStore(client=supabase)
    clue_canon = ClueCanonService(
        store=store,
        client=client,
        runtime=runtime,
        multi_model=multi_model,
    )
    baseline_clues = working_clue_map(baseline_puzzle)
    candidate_clues = working_clue_map(candidate_puzzle)
    selected: dict[tuple[str, int, int], object] = {}
    for key, clue in candidate_clues.items():
        baseline_clue = baseline_clues.get(key)
        if baseline_clue is None:
            continue
        if _normalized_definition(clue.active_version().definition) != _normalized_definition(baseline_clue.active_version().definition):
            continue
        if not _needs_rewrite(clue):
            continue
        fallback = clue_canon.select_scored_fallback(
            word_normalized=clue.word_normalized,
            word_type=clue.word_type,
            usage_label=_extract_usage_label(baseline_clue.active_version().definition),
            seed_parts=(
                str(puzzle_row.get("id") or ""),
                key[0],
                key[1],
                key[2],
                clue.word_normalized,
                baseline_clue.active_version().definition,
            ),
        )
        if fallback is None:
            continue
        if _normalized_definition(fallback.definition) == _normalized_definition(clue.active_version().definition):
            continue
        selected[key] = fallback
    if not selected:
        return {}

    representative_rows = store.fetch_clue_rows_for_canonical_ids([canonical.id for canonical in selected.values()])
    representative_by_canonical_id: dict[str, dict] = {}
    for row in representative_rows:
        canonical_id = str(row.get("canonical_definition_id") or "")
        if canonical_id and canonical_id not in representative_by_canonical_id:
            representative_by_canonical_id[canonical_id] = row

    applied: dict[tuple[str, int, int], str] = {}
    for key, canonical in selected.items():
        clue = candidate_clues[key]
        representative = representative_by_canonical_id.get(canonical.id)
        assessment = (
            _assessment_from_representative_row(representative)
            if representative is not None
            else _synthesize_fallback_assessment(clue, canonical)
        )
        _apply_canonical_fallback(
            clue,
            canonical=canonical,
            assessment=assessment,
        )
        applied[key] = canonical.id
        log(
            f"  [{puzzle_row['id']}] fallback {clue.word_normalized} -> "
            f"'{canonical.definition}' (canonical={canonical.id})"
        )
    return applied


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
