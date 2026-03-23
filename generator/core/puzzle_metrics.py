"""Shared puzzle assessment and persistence helpers."""

from __future__ import annotations

from dataclasses import dataclass

from .ai_clues import RATE_MIN_REBUS
from .dex_cache import DexProvider
from .lm_runtime import LmRuntime
from .pipeline_state import PuzzleAssessment, WorkingPuzzle, all_working_clues
from .quality import QualityReport
from .score_helpers import _needs_rewrite
from ..config import VERIFY_CANDIDATE_COUNT
from ..phases.verify import rate_working_puzzle, verify_working_puzzle


@dataclass(frozen=True)
class PuzzleEvaluationResult:
    assessment: PuzzleAssessment
    passed: int
    total: int
    evaluator_model: str


def score_puzzle_state(
    puzzle: WorkingPuzzle,
    candidate_report: QualityReport | None = None,
) -> PuzzleAssessment:
    clues = all_working_clues(puzzle)
    if not clues:
        return PuzzleAssessment()

    exact_scores = [clue.active_version().assessment.scores.semantic_exactness or 0 for clue in clues]
    rebus_scores = [clue.active_version().assessment.scores.rebus_score or 0 for clue in clues]
    creativity_scores = [clue.active_version().assessment.scores.creativity or 0 for clue in clues]
    targeting_scores = [clue.active_version().assessment.scores.answer_targeting or 0 for clue in clues]
    ambiguity_count = sum(
        1
        for clue in clues
        if (clue.active_version().assessment.scores.ambiguity_risk or 0) >= (11 - RATE_MIN_REBUS)
    )
    short_word_burden = sum(1 for clue in clues if len(clue.word_normalized) <= 3)
    rare_word_burden = candidate_report.high_rarity_words if candidate_report else 0
    blocker_words = [clue.word_normalized for clue in clues if _needs_rewrite(clue)]
    non_preset_rebus = [clue.active_version().assessment.scores.rebus_score or 0 for clue in clues]

    return PuzzleAssessment(
        definition_score=sum(e + r for e, r in zip(exact_scores, rebus_scores)) / len(clues),
        avg_exactness=sum(exact_scores) / len(exact_scores),
        avg_targeting=sum(targeting_scores) / len(targeting_scores),
        ambiguity_count=ambiguity_count,
        short_word_burden=short_word_burden,
        rare_word_burden=rare_word_burden,
        blocker_words=blocker_words,
        avg_creativity=sum(creativity_scores) / len(creativity_scores),
        avg_rebus=sum(rebus_scores) / len(rebus_scores),
        min_rebus=min(non_preset_rebus) if non_preset_rebus else 0,
        verified_count=sum(1 for clue in clues if clue.active_version().assessment.verified is True),
        total_clues=len(clues),
        pass_rate=sum(1 for clue in clues if clue.active_version().assessment.verified is True) / len(clues),
    )


def evaluate_puzzle_state(
    puzzle: WorkingPuzzle,
    client,
    *,
    multi_model: bool = False,
    verify_candidates: int = VERIFY_CANDIDATE_COUNT,
    dex: DexProvider | None = None,
    candidate_report: QualityReport | None = None,
    runtime: LmRuntime | None = None,
) -> PuzzleEvaluationResult:
    if dex is None:
        dex = DexProvider.for_puzzle(puzzle)

    runtime = runtime or LmRuntime(multi_model=multi_model)
    current_model = runtime.activate_initial_evaluator()
    passed, total = verify_working_puzzle(
        puzzle,
        client,
        model_label=current_model.display_name,
        model_name=current_model.model_id,
        max_guesses=verify_candidates,
    )
    rate_working_puzzle(
        puzzle,
        client,
        dex=dex,
        model_label=current_model.display_name,
        model_name=current_model.model_id,
    )
    assessment = score_puzzle_state(puzzle, candidate_report)
    return PuzzleEvaluationResult(
        assessment=assessment,
        passed=passed,
        total=total,
        evaluator_model=current_model.display_name,
    )


def build_puzzle_description(assessment: PuzzleAssessment, models_used: list[str]) -> str:
    models_desc = ", ".join(models_used) if models_used else "-"
    return (
        f"Scor rebus: {assessment.min_rebus}/10 | "
        f"Medie rebus: {assessment.avg_rebus:.1f}/10 | "
        f"Verificate: {assessment.verified_count}/{assessment.total_clues} | "
        f"Modele: {models_desc}"
    )


def puzzle_metadata_payload(
    assessment: PuzzleAssessment,
    *,
    description: str,
) -> dict[str, object]:
    return {
        "description": description,
        "rebus_score_min": assessment.min_rebus,
        "rebus_score_avg": round(assessment.avg_rebus, 3),
        "definition_score": round(assessment.definition_score, 3),
        "verified_count": assessment.verified_count,
        "total_clues": assessment.total_clues,
        "pass_rate": round(assessment.pass_rate, 4),
    }
