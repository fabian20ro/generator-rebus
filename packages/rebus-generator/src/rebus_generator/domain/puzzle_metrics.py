"""Shared puzzle assessment and persistence helpers."""

from __future__ import annotations

from dataclasses import dataclass

from rebus_generator.platform.llm.ai_clues import RATE_MIN_REBUS, compute_rebus_score
from rebus_generator.platform.io.dex_cache import DexProvider
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.llm.models import get_active_models
from .pipeline_state import PuzzleAssessment, WorkingClue, WorkingPuzzle, all_working_clues
from .quality import QualityReport
from .score_helpers import _needs_rewrite
from rebus_generator.platform.config import VERIFY_CANDIDATE_COUNT
from rebus_generator.workflows.generate.verify import rate_working_puzzle, verify_working_puzzle


@dataclass(frozen=True)
class PuzzleEvaluationResult:
    assessment: PuzzleAssessment
    passed: int
    total: int
    evaluator_model: str


def _effective_clue_rebus(clue: WorkingClue) -> int | None:
    assessment = clue.active_version().assessment
    if assessment.scores.rebus_score is not None:
        return assessment.scores.rebus_score

    votes = assessment.rating_votes
    if not votes:
        return None

    ratings = [v for v in votes.values() if v is not None]
    if not ratings:
        return None

    avg_guess = sum(r.guessability_score for r in ratings) / len(ratings)
    avg_creativity = sum(r.creativity_score for r in ratings) / len(ratings)
    return compute_rebus_score(int(avg_guess + 0.5), int(avg_creativity + 0.5))


def score_puzzle_state(
    puzzle: WorkingPuzzle,
    candidate_report: QualityReport | None = None,
) -> PuzzleAssessment:
    clues = all_working_clues(puzzle)
    if not clues:
        return PuzzleAssessment()

    ratable_clues = [
        c for c in clues
        if c.active_version().definition and not c.active_version().definition.startswith("[")
    ]

    verified_count = sum(1 for clue in clues if clue.active_version().assessment.verified is True)
    total_clues = len(clues)
    pass_rate = verified_count / total_clues if total_clues else 0.0

    exact_scores = [c.active_version().assessment.scores.semantic_exactness for c in ratable_clues]
    rebus_scores = [c.active_version().assessment.scores.rebus_score for c in ratable_clues]
    creativity_scores = [c.active_version().assessment.scores.creativity for c in ratable_clues]
    targeting_scores = [c.active_version().assessment.scores.answer_targeting for c in ratable_clues]

    # Effective rebus scores for partial reporting
    effective_rebus = [_effective_clue_rebus(c) for c in ratable_clues]
    non_preset_rebus = [s for s in effective_rebus if s is not None]
    avg_rebus = sum(non_preset_rebus) / len(non_preset_rebus) if non_preset_rebus else 0.0
    min_rebus = min(non_preset_rebus) if non_preset_rebus else 0

    non_none_exact = [s for s in exact_scores if s is not None]
    avg_exactness = sum(non_none_exact) / len(non_none_exact) if non_none_exact else 0.0
    
    non_none_targeting = [s for s in targeting_scores if s is not None]
    avg_targeting = sum(non_none_targeting) / len(non_none_targeting) if non_none_targeting else 0.0

    verify_incomplete_words = [
        clue.word_normalized
        for clue in ratable_clues
        if not clue.active_version().assessment.verify_complete
    ]
    rating_incomplete_words = [
        clue.word_normalized
        for clue in ratable_clues
        if not clue.active_version().assessment.rating_complete
    ]
    incomplete_words = list(dict.fromkeys(verify_incomplete_words + rating_incomplete_words))
    ambiguity_count = sum(
        1
        for clue in ratable_clues
        if (clue.active_version().assessment.scores.ambiguity_risk or 0) >= (11 - RATE_MIN_REBUS)
    )
    scores_complete = all(
        c.active_version().assessment.verify_complete
        and c.active_version().assessment.rating_complete
        and c.active_version().assessment.scores.semantic_exactness is not None
        and c.active_version().assessment.scores.answer_targeting is not None
        and c.active_version().assessment.scores.creativity is not None
        and c.active_version().assessment.scores.rebus_score is not None
        for c in ratable_clues
    )
    short_word_burden = sum(1 for clue in clues if len(clue.word_normalized) <= 3)
    rare_word_burden = candidate_report.high_rarity_words if candidate_report else 0
    blocker_words = [clue.word_normalized for clue in clues if _needs_rewrite(clue)]

    if not scores_complete:
        return PuzzleAssessment(
            short_word_burden=short_word_burden,
            rare_word_burden=rare_word_burden,
            blocker_words=blocker_words,
            verified_count=verified_count,
            total_clues=total_clues,
            pass_rate=pass_rate,
            avg_rebus=avg_rebus,
            min_rebus=min_rebus,
            avg_exactness=avg_exactness,
            avg_targeting=avg_targeting,
            scores_complete=False,
            verify_incomplete_count=len(verify_incomplete_words),
            rating_incomplete_count=len(rating_incomplete_words),
            incomplete_words=incomplete_words,
        )

    return PuzzleAssessment(
        definition_score=sum(e + r for e, r in zip(exact_scores, rebus_scores)) / len(ratable_clues) if ratable_clues else 0.0,
        avg_exactness=avg_exactness,
        avg_targeting=avg_targeting,
        ambiguity_count=ambiguity_count,
        short_word_burden=short_word_burden,
        rare_word_burden=rare_word_burden,
        blocker_words=blocker_words,
        avg_creativity=sum(creativity_scores) / len(creativity_scores) if creativity_scores else 0.0,
        avg_rebus=avg_rebus,
        min_rebus=min_rebus,
        verified_count=verified_count,
        total_clues=total_clues,
        pass_rate=pass_rate,
        scores_complete=True,
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

    runtime = runtime or LmRuntime(multi_model=True)
    passed, total = verify_working_puzzle(
        puzzle,
        client,
        runtime=runtime,
        max_guesses=verify_candidates,
    )
    rate_working_puzzle(
        puzzle,
        client,
        dex=dex,
        runtime=runtime,
    )
    assessment = score_puzzle_state(puzzle, candidate_report)
    return PuzzleEvaluationResult(
        assessment=assessment,
        passed=passed,
        total=total,
        evaluator_model=" + ".join(model.display_name for model in get_active_models(multi_model=True)),
    )


def build_puzzle_description(assessment: PuzzleAssessment, models_used: list[str]) -> str:
    models_desc = ", ".join(models_used) if models_used else "-"
    rebus_text = f"{assessment.min_rebus}/10" if assessment.min_rebus > 0 else "-/10"
    avg_text = f"{assessment.avg_rebus:.1f}/10" if assessment.avg_rebus > 0 else "-/10"

    return (
        f"Scor rebus: {rebus_text} | "
        f"Medie rebus: {avg_text} | "
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
        "rebus_score_min": assessment.min_rebus if assessment.min_rebus > 0 else None,
        "rebus_score_avg": round(assessment.avg_rebus, 3) if assessment.avg_rebus > 0 else None,
        "definition_score": round(assessment.definition_score, 3) if assessment.definition_score > 0 else None,
        "verified_count": assessment.verified_count,
        "total_clues": assessment.total_clues,
        "pass_rate": round(assessment.pass_rate, 4),
    }
