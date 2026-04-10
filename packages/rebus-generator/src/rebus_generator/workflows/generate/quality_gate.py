from __future__ import annotations

from rebus_generator.platform.llm.definition_referee import choose_better_puzzle_variant
from rebus_generator.platform.llm.llm_dispatch import run_single_model_call
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.llm.models import PRIMARY_MODEL
from rebus_generator.platform.io.runtime_logging import log
from rebus_generator.domain.selection_engine import choose_puzzle_assessment
from rebus_generator.domain.quality import QualityReport
from rebus_generator.domain.score_helpers import _compact_log_text

from .models import MIN_PUBLISHABLE_PASS_RATE, PUZZLE_TIEBREAK_DELTA, PreparedPuzzle


def _compute_difficulty(size: int, report: QualityReport) -> int:
    if size <= 7:
        difficulty = 2
    elif size <= 9:
        difficulty = 3
    elif size <= 11:
        difficulty = 4
    else:
        difficulty = 5
    if report.two_letter_words >= max(4, size // 2):
        difficulty -= 1
    if report.average_length >= 6.0 and report.two_letter_words <= 2:
        difficulty += 1
    return max(1, min(5, difficulty))


def _is_publishable(prepared: PreparedPuzzle) -> bool:
    return (
        not prepared.blocking_words
        and prepared.assessment.pass_rate >= MIN_PUBLISHABLE_PASS_RATE
    )


def _better_prepared_puzzle(
    best: PreparedPuzzle | None,
    candidate: PreparedPuzzle,
    client=None,
    runtime: LmRuntime | None = None,
) -> PreparedPuzzle:
    if best is None:
        return candidate

    best_publishable = _is_publishable(best)
    candidate_publishable = _is_publishable(candidate)
    if candidate_publishable != best_publishable:
        return candidate if candidate_publishable else best

    score_delta = candidate.assessment.definition_score - best.assessment.definition_score
    verified_delta = candidate.assessment.verified_count - best.assessment.verified_count
    if verified_delta != 0:
        return candidate if verified_delta > 0 else best
    if abs(score_delta) > PUZZLE_TIEBREAK_DELTA:
        if candidate.assessment.min_rebus != best.assessment.min_rebus:
            return candidate if candidate.assessment.min_rebus > best.assessment.min_rebus else best
        return candidate if score_delta > 0 else best

    def _tiebreak(a_summary: str, b_summary: str) -> str:
        if client is None:
            return "A"
        if runtime is None:
            return choose_better_puzzle_variant(
                client, a_summary, b_summary, model=PRIMARY_MODEL.model_id
            )
        return run_single_model_call(
            runtime=runtime,
            model=PRIMARY_MODEL,
            purpose="puzzle_tiebreaker",
            task_label="puzzle_tiebreaker",
            callback=lambda model: choose_better_puzzle_variant(
                client,
                a_summary,
                b_summary,
                model=model.model_id,
            ),
        )

    winner, decision = choose_puzzle_assessment(
        best.assessment, candidate.assessment, tiebreaker=_tiebreak
    )
    if decision.used_tiebreak:
        chosen = candidate if winner == "B" else best
        log(
            "Puzzle tie-break: "
            f"A='{_compact_log_text(decision.a_summary)}' | "
            f"B='{_compact_log_text(decision.b_summary)}' | "
            f"câștigă {decision.winner} | "
            f"ales='{_compact_log_text(decision.winner_summary)}'"
        )
        return chosen

    return candidate if score_delta > 0 else best
