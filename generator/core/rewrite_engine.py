"""Shared rewrite loop used by batch publish and redefine."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .ai_clues import generate_definition, rewrite_definition
from .model_session import ModelSession
from .pipeline_state import (
    ClueCandidateVersion,
    WorkingPuzzle,
    all_working_clues,
    set_current_definition,
)
from .plateau import has_plateaued
from .runtime_logging import audit
from .score_helpers import (
    LOCKED_REBUS,
    LOCKED_SEMANTIC,
    MAX_CONSECUTIVE_FAILURES,
    PLATEAU_LOOKBACK,
    _compact_log_text,
    _extract_rebus_score,
    _extract_semantic_score,
    _is_locked_clue,
    _needs_rewrite,
    _restore_best_versions,
    _synthesize_failure_reason,
    _update_best_clue_version,
)
from ..config import VERIFY_CANDIDATE_COUNT
from ..phases.verify import rate_working_puzzle, verify_working_puzzle
from .dex_cache import DexProvider


@dataclass
class RewriteWordOutcome:
    word: str
    initial_semantic: int = 0
    initial_rebus: int = 0
    final_semantic: int = 0
    final_rebus: int = 0
    was_candidate: bool = False
    changed_definition: bool = False
    had_error: bool = False
    terminal_reason: str = ""


@dataclass
class RewriteLoopResult:
    initial_passed: int
    final_passed: int
    total: int
    model_switches: int
    outcomes: dict[str, RewriteWordOutcome] = field(default_factory=dict)
    improved_versions: dict[str, ClueCandidateVersion] = field(default_factory=dict)


def run_rewrite_loop(
    puzzle: WorkingPuzzle,
    client,
    *,
    rounds: int,
    theme: str,
    multi_model: bool = False,
    dex: DexProvider | None = None,
    verify_candidates: int = VERIFY_CANDIDATE_COUNT,
) -> RewriteLoopResult:
    """Verify, rate, rewrite, and restore best clue versions for a puzzle."""
    if dex is None:
        dex = DexProvider.for_puzzle(puzzle)

    session = ModelSession(multi_model=multi_model)
    current_model = session.activate_initial_evaluator()
    if multi_model:
        print(f"  Model activ (evaluare inițială): {current_model.display_name}")

    preset_skip: set[str] = set()
    verify_working_puzzle(
        puzzle,
        client,
        skip_words=preset_skip,
        model_label=current_model.display_name,
        model_name=current_model.model_id,
        max_guesses=verify_candidates,
    )
    rate_working_puzzle(
        puzzle,
        client,
        skip_words=preset_skip,
        dex=dex,
        model_label=current_model.display_name,
        model_name=current_model.model_id,
    )
    for clue in all_working_clues(puzzle):
        _update_best_clue_version(clue, client=client)

    initial_scores: dict[str, tuple[int, int]] = {}
    outcomes: dict[str, RewriteWordOutcome] = {}
    for clue in all_working_clues(puzzle):
        sem = _extract_semantic_score(clue) or 0
        reb = _extract_rebus_score(clue) or 0
        initial_scores[clue.word_normalized] = (sem, reb)
        outcomes[clue.word_normalized] = RewriteWordOutcome(
            word=clue.word_normalized,
            initial_semantic=sem,
            initial_rebus=reb,
        )

    consecutive_failures: dict[str, int] = {}
    stuck_words: set[str] = set()
    min_rebus_history: list[int] = []

    for round_index in range(1, rounds + 1):
        current_scores = [_extract_rebus_score(c) or 0 for c in all_working_clues(puzzle)]
        current_min = min(current_scores) if current_scores else 0
        min_rebus_history.append(current_min)

        if has_plateaued(min_rebus_history, PLATEAU_LOOKBACK):
            print(f"  Plateau after {round_index} rounds (min_rebus={current_min})")
            break

        round_min_rebus = current_min + 1
        candidates = [
            clue
            for clue in all_working_clues(puzzle)
            if _needs_rewrite(clue, min_rebus=round_min_rebus)
            and clue.word_normalized not in stuck_words
        ]
        if not candidates:
            break

        if multi_model:
            print(f"  Model activ (rescriere): {current_model.display_name}")

        failed_count = sum(1 for c in candidates if c.current.assessment.verified is False)
        low_rated_count = sum(
            1
            for c in candidates
            if (
                c.current.assessment.verified is True
                and (
                    (_extract_semantic_score(c) or 0) < LOCKED_SEMANTIC
                    or (_extract_rebus_score(c) or 0) < LOCKED_REBUS
                )
            )
        )
        unrated_count = len(candidates) - failed_count - low_rated_count
        print(
            f"Rewrite round {round_index}: {len(candidates)} candidates "
            f"({failed_count} failed, {low_rated_count} low-rated, {unrated_count} unrated)"
        )

        changed_words: set[str] = set()
        for clue in candidates:
            outcome = outcomes[clue.word_normalized]
            outcome.was_candidate = True
            if _is_locked_clue(clue):
                print(f"  {clue.word_normalized}: blocat la {LOCKED_SEMANTIC}/{LOCKED_REBUS}")
                continue

            wrong_guess = clue.current.assessment.wrong_guess
            wrong_guesses = list(clue.current.assessment.verify_candidates)
            rating_feedback = clue.current.assessment.feedback
            bad_example_definition = clue.current.definition if round_index >= 2 else ""
            bad_example_reason = _synthesize_failure_reason(clue) if round_index >= 2 else ""
            dex_defs = (dex.get(clue.word_normalized, clue.word_original) or "")
            failure_history: list[tuple[str, list[str]]] = [
                (
                    v.definition,
                    list(v.assessment.verify_candidates)
                    if v.assessment.verify_candidates
                    else ([v.assessment.wrong_guess] if v.assessment.wrong_guess else []),
                )
                for v in clue.history
                if (v.assessment.verify_candidates or v.assessment.wrong_guess) and v.definition
            ]
            try:
                if clue.current.definition.startswith("["):
                    new_definition = generate_definition(
                        client,
                        clue.word_normalized,
                        clue.word_original,
                        theme,
                        retries=3,
                        word_type=clue.word_type,
                        dex_definitions=dex_defs,
                        model=current_model.model_id,
                    )
                else:
                    new_definition = rewrite_definition(
                        client,
                        clue.word_normalized,
                        clue.word_original,
                        theme,
                        clue.current.definition,
                        wrong_guess,
                        wrong_guesses=wrong_guesses or None,
                        rating_feedback=rating_feedback,
                        bad_example_definition=bad_example_definition,
                        bad_example_reason=bad_example_reason,
                        word_type=clue.word_type,
                        dex_definitions=dex_defs,
                        failure_history=failure_history or None,
                        model=current_model.model_id,
                    )
            except Exception as exc:
                outcome.had_error = True
                print(f"  Rewrite failed for {clue.word_normalized}: {exc}")
                continue

            if new_definition and new_definition != clue.current.definition:
                outcome.changed_definition = True
                changed_words.add(clue.word_normalized)
                consecutive_failures[clue.word_normalized] = 0
                print(
                    f"  {clue.word_normalized}: "
                    f"'{_compact_log_text(clue.current.definition)}' -> "
                    f"'{_compact_log_text(new_definition)}'"
                )
                set_current_definition(
                    clue,
                    new_definition,
                    round_index=round_index,
                    source="rewrite",
                    generated_by=current_model.display_name,
                )
            else:
                consecutive_failures[clue.word_normalized] = consecutive_failures.get(clue.word_normalized, 0) + 1
                if consecutive_failures[clue.word_normalized] >= MAX_CONSECUTIVE_FAILURES:
                    stuck_words.add(clue.word_normalized)
                    print(
                        f"  {clue.word_normalized}: marcată ca blocată după "
                        f"{consecutive_failures[clue.word_normalized]} încercări eșuate consecutive"
                    )

        skip_words = ({c.word_normalized for c in all_working_clues(puzzle)} - changed_words) | preset_skip
        current_model = session.alternate()
        if multi_model:
            print(f"  Model activ (evaluare): {current_model.display_name}")
        verify_working_puzzle(
            puzzle,
            client,
            skip_words=skip_words,
            model_label=current_model.display_name,
            model_name=current_model.model_id,
            max_guesses=verify_candidates,
        )
        rate_working_puzzle(
            puzzle,
            client,
            skip_words=skip_words,
            dex=dex,
            model_label=current_model.display_name,
            model_name=current_model.model_id,
        )
        for clue in all_working_clues(puzzle):
            if clue.word_normalized not in changed_words:
                continue
            _update_best_clue_version(clue, client=client)
            if clue.locked:
                print(f"  {clue.word_normalized}: definiție blocată la {LOCKED_SEMANTIC}/{LOCKED_REBUS}")

    _restore_best_versions(puzzle)
    final_passed = sum(1 for clue in all_working_clues(puzzle) if clue.current.assessment.verified)
    total = len(all_working_clues(puzzle))

    improved_versions: dict[str, ClueCandidateVersion] = {}
    unresolved = {entry["word"]: entry["definition"] for entry in dex.uncertain_short_definitions()}
    for clue in all_working_clues(puzzle):
        outcome = outcomes[clue.word_normalized]
        outcome.final_semantic = _extract_semantic_score(clue) or 0
        outcome.final_rebus = _extract_rebus_score(clue) or 0
        if (
            outcome.final_rebus > outcome.initial_rebus
            or outcome.final_semantic > outcome.initial_semantic
        ):
            improved_versions[clue.word_normalized] = copy.deepcopy(clue.active_version())

        unresolved_definition = unresolved.get(clue.word_normalized)
        if unresolved_definition is None:
            continue
        if clue.word_normalized in improved_versions:
            continue
        if not outcome.was_candidate:
            reason = "not_candidate"
        elif outcome.had_error:
            reason = "error"
        elif not outcome.changed_definition:
            reason = "rewrite_no_change"
        else:
            reason = "not_improved"
        outcome.terminal_reason = reason
        print(f"  [DEX audit] {clue.word_normalized}: short DEX not included în redefinire ({reason})")
        audit(
            "dex_short_definition_not_included_in_redefinire",
            component="rewrite_engine",
            payload={
                "word": clue.word_normalized,
                "definition": unresolved_definition,
                "reason": reason,
            },
        )

    return RewriteLoopResult(
        initial_passed=sum(1 for clue in all_working_clues(puzzle) if clue.history and clue.history[0].assessment.verified),
        final_passed=final_passed,
        total=total,
        model_switches=session.switch_count,
        outcomes=outcomes,
        improved_versions=improved_versions,
    )
