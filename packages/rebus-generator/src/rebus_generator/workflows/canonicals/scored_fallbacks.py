from __future__ import annotations

import copy
from collections.abc import Callable

from rebus_generator.domain.pipeline_state import (
    ClueAssessment,
    ClueScores,
    WorkingClue,
    WorkingPuzzle,
    working_clue_from_entry,
)
from rebus_generator.domain.score_helpers import (
    LOCKED_REBUS,
    LOCKED_SEMANTIC,
    _definition_missing_or_placeholder,
    _needs_rewrite,
    _pair_evaluation_incomplete,
)
from rebus_generator.platform.io.markdown_io import ClueEntry
from rebus_generator.platform.io.runtime_logging import log
from rebus_generator.platform.llm.ai_clues import compute_rebus_score
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.persistence.clue_canon_store import ClueCanonStore
from rebus_generator.workflows.canonicals.domain_service import ClueCanonService, _extract_usage_label
from rebus_generator.domain.diacritics import normalize

FallbackPolicy = Callable[[WorkingClue, WorkingClue | None], bool]


def _normalized_definition(text: str) -> str:
    return " ".join(normalize(text or "").lower().split())


def _working_clue_map(puzzle: WorkingPuzzle) -> dict[tuple[str, int, int], WorkingClue]:
    mapping: dict[tuple[str, int, int], WorkingClue] = {}
    for direction, clues in (("H", puzzle.horizontal_clues), ("V", puzzle.vertical_clues)):
        for clue in clues:
            mapping[(direction, int(clue.start_row or 0), int(clue.start_col or 0))] = clue
    return mapping


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


def redefine_scored_fallback_policy(clue: WorkingClue, reference_clue: WorkingClue | None) -> bool:
    if reference_clue is None:
        return False
    return (
        _normalized_definition(clue.active_version().definition)
        == _normalized_definition(reference_clue.active_version().definition)
        and _needs_rewrite(clue)
    )


def generate_scored_fallback_policy(clue: WorkingClue, _reference_clue: WorkingClue | None) -> bool:
    return _definition_missing_or_placeholder(clue) or _pair_evaluation_incomplete(clue)


def _fallback_usage_label(clue: WorkingClue, reference_clue: WorkingClue | None) -> str:
    for candidate in (reference_clue, clue):
        if candidate is None or _definition_missing_or_placeholder(candidate):
            continue
        usage_label = _extract_usage_label(candidate.active_version().definition)
        if usage_label:
            return usage_label
    return ""


def apply_scored_canonical_fallbacks(
    *,
    target_puzzle: WorkingPuzzle,
    puzzle_identity: str,
    policy: FallbackPolicy,
    reference_puzzle: WorkingPuzzle | None = None,
    store_client=None,
    client=None,
    runtime: LmRuntime | None = None,
    multi_model: bool = True,
    seed_parts: tuple[object, ...] = (),
) -> dict[tuple[str, int, int], str]:
    store = ClueCanonStore(client=store_client)
    clue_canon = ClueCanonService(
        store=store,
        client=client,
        runtime=runtime,
        multi_model=multi_model,
    )
    target_clues = _working_clue_map(target_puzzle)
    reference_clues = _working_clue_map(reference_puzzle) if reference_puzzle is not None else {}
    selected: dict[tuple[str, int, int], object] = {}
    for key, clue in target_clues.items():
        reference_clue = reference_clues.get(key)
        if not policy(clue, reference_clue):
            continue
        fallback = clue_canon.select_scored_fallback(
            word_normalized=clue.word_normalized,
            word_type=clue.word_type,
            usage_label=_fallback_usage_label(clue, reference_clue),
            seed_parts=(
                puzzle_identity,
                *seed_parts,
                key[0],
                key[1],
                key[2],
                clue.word_normalized,
                reference_clue.active_version().definition if reference_clue is not None else clue.active_version().definition,
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
        clue = target_clues[key]
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
            f"  [{puzzle_identity}] fallback {clue.word_normalized} -> "
            f"'{canonical.definition}' (canonical={canonical.id})"
        )
    return applied
