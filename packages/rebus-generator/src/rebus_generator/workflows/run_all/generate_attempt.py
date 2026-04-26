from __future__ import annotations

import copy
from dataclasses import dataclass

from rebus_generator.domain.guards.definition_guards import validate_definition_text
from rebus_generator.domain.pipeline_state import WorkingPuzzle, all_working_clues, set_current_definition
from rebus_generator.domain.puzzle_metrics import score_puzzle_state
from rebus_generator.domain.score_helpers import _restore_best_versions
from rebus_generator.domain.short_word_clues import valid_short_word_clues_for
from rebus_generator.platform.io.dex_cache import DexProvider
from rebus_generator.platform.io.runtime_logging import log
from rebus_generator.workflows.canonicals.scored_fallbacks import (
    apply_scored_canonical_fallbacks,
    generate_scored_fallback_policy,
    generate_unresolved_definition_fallback_policy,
)
from rebus_generator.workflows.generate.models import PreparedPuzzle
from rebus_generator.workflows.generate.prepare import (
    _build_prepared_puzzle,
    _should_skip_title_generation,
)
from rebus_generator.workflows.generate.quality_gate import (
    _better_prepared_puzzle,
    _describe_publishability_failure,
    _is_publishable,
)


@dataclass(frozen=True)
class GenerateAttemptDecision:
    next_stage: str
    detail: str
    prepared: PreparedPuzzle | None = None
    result: object = None

    @property
    def retrying(self) -> bool:
        return self.next_stage == "fill_grid"


def _is_unresolved_definition(definition: str) -> bool:
    text = str(definition or "").strip()
    return not text or text in {"[NECLAR]", "[Definiție negenerată]"} or text.startswith("[")


def _strip_dex_rescue_label(text: str) -> str:
    stripped = str(text or "").strip()
    if ": " in stripped and (
        stripped.startswith("Definiție directă DEX")
        or stripped.startswith("Sens bază")
    ):
        return stripped.split(": ", 1)[1].strip()
    return stripped


def _dex_rescue_candidates(*, raw_dex: str, uncertain_short_definition: str) -> list[str]:
    candidates: list[str] = []
    for candidate in [uncertain_short_definition]:
        cleaned = _strip_dex_rescue_label(candidate)
        if cleaned:
            candidates.append(cleaned)
    for raw_line in raw_dex.splitlines():
        line = raw_line.strip()
        if line.startswith("- "):
            line = line[2:].strip()
        cleaned = _strip_dex_rescue_label(line)
        if cleaned:
            candidates.append(cleaned)
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(candidate.strip())
    return deduped


def _definition_rescue_candidates(
    *,
    word: str,
    raw_dex: str,
    uncertain_short_definition: str,
) -> list[tuple[str, str, str]]:
    candidates: list[tuple[str, str, str]] = [
        (candidate, "generate_rescue_dex", "dex_rescue")
        for candidate in _dex_rescue_candidates(
            raw_dex=raw_dex,
            uncertain_short_definition=uncertain_short_definition,
        )
    ]
    candidates.extend(
        (clue.definition, "generate_rescue_answer_supply", "answer_supply")
        for clue in valid_short_word_clues_for(word)
    )
    deduped: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for definition, source, generated_by in candidates:
        key = definition.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append((definition, source, generated_by))
    return deduped


def rescue_unresolved_generated_definitions(
    *,
    puzzle: WorkingPuzzle,
    puzzle_identity: str,
    dex: DexProvider,
    client,
    runtime,
    multi_model: bool,
    seed_parts: tuple[object, ...],
) -> None:
    apply_scored_canonical_fallbacks(
        target_puzzle=puzzle,
        puzzle_identity=puzzle_identity,
        policy=generate_unresolved_definition_fallback_policy,
        client=client,
        runtime=runtime,
        multi_model=multi_model,
        seed_parts=seed_parts,
    )
    unresolved_short_defs = {
        str(entry.get("word") or ""): str(entry.get("definition") or "")
        for entry in getattr(dex, "uncertain_short_definitions", lambda: [])()
    }
    for clue in all_working_clues(puzzle):
        if not _is_unresolved_definition(clue.current.definition):
            continue
        raw_dex = dex.get(clue.word_normalized, clue.word_original) or ""
        for candidate, source, generated_by in _definition_rescue_candidates(
            word=clue.word_normalized,
            raw_dex=raw_dex,
            uncertain_short_definition=unresolved_short_defs.get(clue.word_normalized, ""),
        ):
            rejection = validate_definition_text(clue.word_normalized, candidate)
            if rejection is not None:
                continue
            set_current_definition(
                clue,
                candidate,
                round_index=0,
                source=source,
                generated_by=generated_by,
            )
            clue.best = copy.deepcopy(clue.current)
            log(f"  [{puzzle_identity}] definition rescue {clue.word_normalized} -> '{candidate}'")
            break


def finalize_rewritten_attempt(
    *,
    puzzle: WorkingPuzzle,
    puzzle_identity: str,
    candidate,
    best_prepared: PreparedPuzzle | None,
    rewrite_result,
    size: int,
    index: int,
    attempt_index: int,
    effective_attempts: int,
    client,
    runtime,
    multi_model: bool,
) -> tuple[GenerateAttemptDecision, PreparedPuzzle | None]:
    _restore_best_versions(puzzle)
    apply_scored_canonical_fallbacks(
        target_puzzle=puzzle,
        puzzle_identity=puzzle_identity,
        policy=generate_scored_fallback_policy,
        client=client,
        runtime=runtime,
        multi_model=multi_model,
        seed_parts=(size, index, attempt_index),
    )
    puzzle.assessment = score_puzzle_state(puzzle, candidate.report)
    first_passed = rewrite_result.initial_passed
    final_passed = rewrite_result.final_passed
    total = rewrite_result.total
    if not _should_skip_title_generation(puzzle):
        return (
            GenerateAttemptDecision(
                next_stage="title",
                detail=f"verified={final_passed}/{total}",
                result=rewrite_result,
            ),
            best_prepared,
        )

    prepared = _build_prepared_puzzle(
        title="",
        title_score=0,
        candidate=candidate,
        puzzle=puzzle,
        first_passed=first_passed,
        final_passed=final_passed,
        total=total,
    )
    best_prepared = _better_prepared_puzzle(
        best_prepared,
        prepared,
        client=client,
        runtime=runtime,
    )
    if attempt_index < effective_attempts:
        log(
            "Rejected generated puzzle before title generation: "
            + _describe_publishability_failure(prepared)
        )
        return (
            GenerateAttemptDecision(
                next_stage="fill_grid",
                detail=f"retry={attempt_index + 1}/{effective_attempts}",
                prepared=prepared,
                result=rewrite_result,
            ),
            best_prepared,
        )
    raise RuntimeError(
        f"Could not prepare a publishable {size}x{size} puzzle. "
        f"Quality gate failed: {_describe_publishability_failure(prepared)}"
    )


def finalize_titled_attempt(
    *,
    title: str,
    title_score: int,
    puzzle: WorkingPuzzle,
    candidate,
    best_prepared: PreparedPuzzle | None,
    first_passed: int,
    final_passed: int,
    total: int,
    size: int,
    attempt_index: int,
    effective_attempts: int,
    client,
    runtime,
) -> tuple[GenerateAttemptDecision, PreparedPuzzle | None]:
    puzzle.title = title
    prepared = _build_prepared_puzzle(
        title=title,
        title_score=title_score,
        candidate=candidate,
        puzzle=puzzle,
        first_passed=first_passed,
        final_passed=final_passed,
        total=total,
    )
    best_prepared = _better_prepared_puzzle(
        best_prepared,
        prepared,
        client=client,
        runtime=runtime,
    )
    if best_prepared and _is_publishable(best_prepared):
        return (
            GenerateAttemptDecision(
                next_stage="publish",
                detail=f"title={best_prepared.title}",
                prepared=best_prepared,
            ),
            best_prepared,
        )
    if attempt_index < effective_attempts:
        log(
            "Rejected generated puzzle after quality gate: "
            + _describe_publishability_failure(prepared)
        )
        return (
            GenerateAttemptDecision(
                next_stage="fill_grid",
                detail=f"retry={attempt_index + 1}/{effective_attempts}",
                prepared=prepared,
            ),
            best_prepared,
        )
    raise RuntimeError(
        f"Could not prepare a publishable {size}x{size} puzzle. "
        f"Quality gate failed: {_describe_publishability_failure(prepared)}"
    )
