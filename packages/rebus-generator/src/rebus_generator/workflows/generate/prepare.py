from __future__ import annotations

import copy
import random

from rebus_generator.platform.config import VERIFY_CANDIDATE_COUNT
from rebus_generator.platform.io.dex_cache import DexProvider
from rebus_generator.platform.io.markdown_io import ClueEntry, parse_markdown
from rebus_generator.platform.io.runtime_logging import log
from rebus_generator.platform.io.rust_bridge import _best_candidate
from rebus_generator.platform.llm.definition_referee import choose_better_clue_variant
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.llm.models import PRIMARY_MODEL
from rebus_generator.domain.pipeline_state import (
    WorkingClue,
    WorkingPuzzle,
    all_working_clues,
    puzzle_from_working_state,
    working_puzzle_from_puzzle,
)
from rebus_generator.domain.puzzle_metrics import score_puzzle_state
from rebus_generator.domain.score_helpers import _coerce_working_clue, _compact_log_text, _restore_best_versions
from rebus_generator.domain.selection_engine import choose_clue_version, stable_tie_rng
from rebus_generator.domain.size_tuning import get_min_preparation_attempts
from rebus_generator.workflows.generate.define import generate_definitions_for_puzzle
from rebus_generator.workflows.generate.titleing import generate_publication_title
from rebus_generator.workflows.redefine.rewrite_engine import run_rewrite_loop

from .models import PreparedPuzzle
from .quality_gate import _better_prepared_puzzle, _describe_publishability_failure, _is_publishable


def _blocking_clues(puzzle: WorkingPuzzle) -> list[WorkingClue]:
    return [
        clue
        for clue in all_working_clues(puzzle)
        if not clue.active_version().definition
        or clue.active_version().definition.startswith("[")
    ]


def _choose_metadata_variants_for_puzzle(
    puzzle, metadata: dict[str, list[dict]]
) -> dict[str, dict]:
    resolved: dict[str, dict] = {}
    clues = list(getattr(puzzle, "horizontal_clues", [])) + list(
        getattr(puzzle, "vertical_clues", [])
    )
    for clue in clues:
        normalized = clue.word_normalized
        if normalized not in resolved:
            options = metadata.get(normalized) or []
            if options:
                resolved[normalized] = copy.deepcopy(random.choice(options))
            else:
                resolved[normalized] = {
                    "normalized": normalized,
                    "original": normalized.lower(),
                    "word_type": "",
                }
        clue.word_original = resolved[normalized].get("original") or normalized.lower()
    return resolved


def _inject_word_metadata(state: WorkingPuzzle, metadata: dict[str, dict]) -> None:
    for clue in all_working_clues(state):
        word_meta = metadata.get(clue.word_normalized, {})
        clue.word_type = word_meta.get("word_type", "")
        clue.word_original = word_meta.get("original") or clue.word_normalized.lower()
    state.metadata["resolved_word_metadata"] = copy.deepcopy(metadata)


def _preparation_attempts_for_size(size: int, requested_attempts: int) -> int:
    return max(requested_attempts, get_min_preparation_attempts(size))


def _merge_best_clue_variants(
    best_clues: list[ClueEntry],
    current_clues: list[ClueEntry],
    client=None,
    model_name: str | None = None,
) -> list[ClueEntry]:
    merged: list[ClueEntry] = []
    for best_clue, current_clue in zip(best_clues, current_clues):
        best_working = _coerce_working_clue(best_clue)
        current_working = _coerce_working_clue(current_clue)

        def _tiebreak(a_text: str, b_text: str) -> str:
            if client is None:
                return "A"
            return choose_better_clue_variant(
                client,
                best_working.word_normalized,
                len(best_working.word_normalized),
                a_text,
                b_text,
                model=model_name or PRIMARY_MODEL.model_id,
            )

        chosen, _ = choose_clue_version(
            best_working.active_version(),
            current_working.active_version(),
            tiebreaker=_tiebreak,
            rng=stable_tie_rng(
                "merge_best_clue_variants",
                best_working.word_normalized,
                best_working.active_version().definition,
                current_working.active_version().definition,
            ),
        )
        if client is not None:
            log(
                f"  Tie-break definiție {best_working.word_normalized}: "
                f"A='{_compact_log_text(best_working.active_version().definition)}' | "
                f"B='{_compact_log_text(current_working.active_version().definition)}' | "
                f"aleasă='{_compact_log_text(chosen.definition)}'"
            )
        chosen_working = copy.deepcopy(
            best_working
            if chosen.definition == best_working.active_version().definition
            else current_working
        )
        chosen_working.best = copy.deepcopy(chosen)
        chosen_working.current = copy.deepcopy(chosen)
        merged.append(
            puzzle_from_working_state(
                WorkingPuzzle("", 0, [], [chosen_working], [])
            ).horizontal_clues[0]
        )
    return merged


def _backfill_generated_model(puzzle: WorkingPuzzle, model_label: str) -> None:
    for clue in all_working_clues(puzzle):
        if clue.current.definition and not clue.current.generated_by:
            clue.current.generated_by = model_label


def _rewrite_failed_clues(
    puzzle: WorkingPuzzle,
    client,
    rounds: int,
    multi_model: bool = False,
    dex: DexProvider | None = None,
    verify_candidates: int = VERIFY_CANDIDATE_COUNT,
    runtime: LmRuntime | None = None,
) -> tuple[int, int, int]:
    result = run_rewrite_loop(
        puzzle,
        client,
        rounds=rounds,
        theme=puzzle.title or "Puzzle intern",
        multi_model=multi_model,
        dex=dex,
        verify_candidates=verify_candidates,
        runtime=runtime,
    )
    puzzle.metadata["rewrite_model_switches"] = result.model_switches
    return result.initial_passed, result.final_passed, result.total


def _prepare_puzzle_for_publication(
    index: int,
    total_puzzles: int,
    size: int,
    raw_words: list[dict],
    words_path,
    client,
    rewrite_rounds: int,
    preparation_attempts: int,
    seen_template_fingerprints: set[str] | None = None,
    multi_model: bool = False,
    verify_candidates: int = VERIFY_CANDIDATE_COUNT,
    word_metadata: dict[str, dict] | None = None,
    runtime: LmRuntime | None = None,
) -> PreparedPuzzle:
    best_prepared: PreparedPuzzle | None = None
    effective_attempts = _preparation_attempts_for_size(size, preparation_attempts)

    for attempt_index in range(1, effective_attempts + 1):
        if attempt_index > 1:
            log(
                f"Retrying puzzle {index}/{total_puzzles} ({size}x{size}), "
                f"attempt {attempt_index}/{effective_attempts}..."
            )

        provisional_title = f"Puzzle {index}"
        rng = getattr(client, "_batch_rng", random.Random(0))
        candidate = _best_candidate(
            size,
            provisional_title,
            raw_words,
            rng=rng,
            seen_template_fingerprints=seen_template_fingerprints,
            words_path=words_path,
            word_metadata=word_metadata,
            preparation_attempts=1,
        )
        puzzle = parse_markdown(candidate.markdown)
        puzzle.title = ""
        resolved_metadata = _choose_metadata_variants_for_puzzle(
            puzzle, candidate.metadata
        )
        generate_definitions_for_puzzle(
            puzzle,
            client,
            metadata=resolved_metadata,
            runtime=runtime,
            model_config=PRIMARY_MODEL,
        )
        state = working_puzzle_from_puzzle(puzzle, split_compound=False)
        _backfill_generated_model(state, PRIMARY_MODEL.display_name)
        _inject_word_metadata(state, resolved_metadata)
        dex = DexProvider.for_puzzle(state)
        first_passed, final_passed, total = _rewrite_failed_clues(
            state,
            client,
            rewrite_rounds,
            multi_model=multi_model,
            dex=dex,
            verify_candidates=verify_candidates,
            runtime=runtime,
        )
        _restore_best_versions(state)
        state.assessment = score_puzzle_state(state, candidate.report)
        blockers = _blocking_clues(state)
        title_result = generate_publication_title(
            puzzle_from_working_state(state),
            client=client,
            runtime=runtime,
            multi_model=multi_model,
        )
        state.title = title_result.title
        log(f"Titlu final: {title_result.title}")
        prepared = PreparedPuzzle(
            title=title_result.title,
            title_score=title_result.score,
            candidate=candidate,
            puzzle=copy.deepcopy(state),
            first_passed=first_passed,
            final_passed=final_passed,
            total=total,
            definition_score=state.assessment.definition_score,
            blocking_words=[clue.word_normalized for clue in blockers],
            assessment=copy.deepcopy(state.assessment),
        )
        best_prepared = _better_prepared_puzzle(
            best_prepared, prepared, client=client, runtime=runtime
        )

        if _is_publishable(best_prepared):
            log(f"  Puzzle publicabil la tentativa {attempt_index}/{effective_attempts}")
            break
        log(
            "Rejected puzzle after quality gate: "
            + _describe_publishability_failure(prepared)
        )

    if best_prepared is None:
        raise RuntimeError(f"Failed to prepare any {size}x{size} puzzle candidate")
    return best_prepared
