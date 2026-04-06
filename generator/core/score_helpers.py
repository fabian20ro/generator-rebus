"""Shared scoring helpers for clue evaluation, extracted from batch_publish."""

from __future__ import annotations

import copy

from .ai_clues import (
    RATE_MIN_REBUS,
    RATE_MIN_SEMANTIC,
)
from .definition_referee import choose_better_clue_variant
from .markdown_io import ClueEntry
from .model_manager import PRIMARY_MODEL
from .pipeline_state import (
    WorkingClue,
    WorkingPuzzle,
    all_working_clues,
    working_clue_from_entry,
)
from .runtime_logging import log
from .selection_engine import choose_clue_version

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOCKED_SEMANTIC = 9
LOCKED_REBUS = 8
MAX_CONSECUTIVE_FAILURES = 5
PLATEAU_LOOKBACK = 7

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _coerce_working_clue(clue: WorkingClue | ClueEntry) -> WorkingClue:
    if isinstance(clue, WorkingClue):
        return clue
    return working_clue_from_entry(clue)


def _extract_semantic_score(clue: WorkingClue) -> int | None:
    clue = _coerce_working_clue(clue)
    return clue.active_version().assessment.scores.semantic_exactness


def _extract_guessability_score(clue: WorkingClue) -> int | None:
    clue = _coerce_working_clue(clue)
    return clue.active_version().assessment.scores.answer_targeting


def _extract_rebus_score(clue: WorkingClue) -> int | None:
    clue = _coerce_working_clue(clue)
    return clue.active_version().assessment.scores.rebus_score


def _needs_rewrite(clue: WorkingClue, min_rebus: int = RATE_MIN_REBUS) -> bool:
    """Return True when a clue should be rewritten.

    Multistep exact solve is the main target.
    High semantic/rebus scores are not enough when verification still lands on
    a different answer.
    """
    clue = _coerce_working_clue(clue)
    definition = clue.current.definition
    if not definition or definition.startswith("["):
        return True

    semantic_score = _extract_semantic_score(clue)
    rebus_score = _extract_rebus_score(clue)
    if semantic_score is None or rebus_score is None:
        return True
    if clue.current.assessment.verified is False:
        return True

    rarity_override = clue.current.assessment.rarity_only_override
    if rarity_override and semantic_score >= RATE_MIN_SEMANTIC:
        return False

    if semantic_score >= LOCKED_SEMANTIC and rebus_score >= LOCKED_REBUS:
        return False

    if semantic_score < RATE_MIN_SEMANTIC:
        return True

    return rebus_score < min_rebus


def _is_locked_clue(clue: WorkingClue) -> bool:
    clue = _coerce_working_clue(clue)
    return clue.locked


def _compact_log_text(text: str) -> str:
    return " ".join((text or "").split())


def _synthesize_failure_reason(clue: WorkingClue) -> str:
    clue = _coerce_working_clue(clue)
    assessment = clue.current.assessment
    if assessment.scores.family_leakage:
        return "Folosește aceeași familie lexicală ca răspunsul."
    if assessment.verify_candidates:
        return f"Duce la alte răspunsuri: {', '.join(assessment.verify_candidates)}."
    if assessment.wrong_guess:
        return f"Duce la alt răspuns: {assessment.wrong_guess}."
    if assessment.feedback:
        normalized_feedback = assessment.feedback.lower()
        if ("rar" in normalized_feedback or "comun" in normalized_feedback) and (assessment.scores.semantic_exactness or 0) >= 8:
            return "Definiția trebuie făcută mai exactă, nu tratată ca defect doar pentru raritate."
        return assessment.feedback
    if assessment.rewrite_rejection_reason:
        return assessment.rewrite_rejection_reason
    if assessment.failure_reason:
        return assessment.failure_reason.message

    semantic_score = assessment.scores.semantic_exactness or 0
    rebus_score = assessment.scores.rebus_score or 0
    if semantic_score < RATE_MIN_SEMANTIC:
        return "Definiția nu este suficient de exactă pentru răspunsul intenționat."
    if rebus_score < RATE_MIN_REBUS:
        return "Definiția este prea vagă sau duce spre alt răspuns mai comun."
    return "Definiția trebuie făcută mai exactă."


def _update_best_clue_version(clue: WorkingClue, client=None, model_name: str | None = None) -> None:
    if clue.best is None:
        clue.best = copy.deepcopy(clue.current)
    elif clue.current.definition:
        def _tiebreak(a_text: str, b_text: str) -> str:
            if client is None:
                return "A"
            return choose_better_clue_variant(
                client,
                clue.word_normalized,
                len(clue.word_normalized),
                a_text,
                b_text,
                model=model_name or PRIMARY_MODEL.model_id,
            )

        chosen, decision = choose_clue_version(clue.best, clue.current, tiebreaker=_tiebreak)
        if decision.used_tiebreak:
            log(
                f"  Tie-break definiție {clue.word_normalized}: "
                f"A='{_compact_log_text(decision.a_summary)}' | "
                f"B='{_compact_log_text(decision.b_summary)}' | "
                f"câștigă {decision.winner} | "
                f"aleasă='{_compact_log_text(decision.winner_summary)}'"
            )
        elif decision.reason == "deterministic_rank" and chosen.definition == clue.best.definition and clue.current.definition != clue.best.definition:
            log(f"  Păstrez definiția mai bună pentru {clue.word_normalized}")
        clue.best = copy.deepcopy(chosen)

    semantic_score = clue.best.assessment.scores.semantic_exactness or 0
    rebus_score = clue.best.assessment.scores.rebus_score or 0
    clue.locked = (
        clue.best.assessment.verified is True
        and semantic_score >= LOCKED_SEMANTIC
        and rebus_score >= LOCKED_REBUS
    )


def _restore_best_versions(puzzle: WorkingPuzzle) -> None:
    for clue in all_working_clues(puzzle):
        if clue.best is not None:
            clue.current = copy.deepcopy(clue.best)
