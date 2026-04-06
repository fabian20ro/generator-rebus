"""Shared rewrite loop used by batch publish and redefine."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from .ai_clues import (
    RewriteAttemptResult,
    generate_definition,
    rewrite_definition,
)
from .definition_referee import choose_better_clue_variant
from .clue_canon import ClueCanonService
from .clue_logging import clue_label_from_working_clue, log_definition_event
from .lm_runtime import LmRuntime
from .pipeline_state import (
    ClueCandidateVersion,
    WorkingClue,
    WorkingPuzzle,
    all_working_clues,
    set_current_definition,
)
from .plateau import has_plateaued
from .selection_engine import choose_clue_version
from .runtime_logging import audit, log
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
    selected_strategy: str = ""


@dataclass
class RewriteLoopResult:
    initial_passed: int
    final_passed: int
    total: int
    model_switches: int
    outcomes: dict[str, RewriteWordOutcome] = field(default_factory=dict)
    improved_versions: dict[str, ClueCandidateVersion] = field(default_factory=dict)


HYBRID_REBUS_THRESHOLD = 4


@dataclass(frozen=True)
class PendingCandidate:
    source: str
    definition: str
    generated_by: str
    strategy_label: str


def _definition_key(text: str) -> str:
    return " ".join((text or "").split()).lower()


def _should_try_hybrid(
    clue: WorkingClue,
    *,
    hybrid_attempted_words: set[str],
    hybrid_deanchor: bool,
) -> bool:
    if not hybrid_deanchor:
        return False
    if clue.word_normalized in hybrid_attempted_words:
        return False
    if not clue.current.definition or clue.current.definition.startswith("["):
        return False
    if clue.current.assessment.verified is False:
        return True
    rebus_score = _extract_rebus_score(clue) or 0
    return rebus_score <= HYBRID_REBUS_THRESHOLD


def _build_pending_candidates(
    clue: WorkingClue,
    *,
    client,
    theme: str,
    current_model,
    clue_canon: ClueCanonService | None,
    wrong_guess: str,
    wrong_guesses: list[str],
    rating_feedback: str,
    bad_example_definition: str,
    bad_example_reason: str,
    dex_defs: str,
    failure_history: list[tuple[str, list[str]]],
    use_hybrid: bool,
) -> tuple[list[PendingCandidate], bool, str]:
    pending: list[PendingCandidate] = []
    seen: set[str] = set()

    def _maybe_add(definition: str, *, source: str, strategy_label: str) -> None:
        cleaned = (definition or "").strip()
        if not cleaned or cleaned == clue.current.definition:
            return
        key = _definition_key(cleaned)
        if key in seen:
            return
        seen.add(key)
        pending.append(
            PendingCandidate(
                source=source,
                definition=cleaned,
                generated_by=current_model.display_name,
                strategy_label=strategy_label,
            )
        )

    had_error = False
    rewrite_rejection_reason = ""
    existing_canonical_definitions = (
        clue_canon.fetch_prompt_examples(clue.word_normalized) if clue_canon is not None else []
    )
    if clue.current.definition.startswith("["):
        _maybe_add(
            generate_definition(
                client,
                clue.word_normalized,
                clue.word_original,
                theme,
                retries=3,
                word_type=clue.word_type,
                dex_definitions=dex_defs,
                existing_canonical_definitions=existing_canonical_definitions,
                model=current_model.model_id,
            ),
            source="generate",
            strategy_label="fresh_only",
        )
        return pending, had_error, rewrite_rejection_reason

    try:
        rewrite_result = rewrite_definition(
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
            existing_canonical_definitions=existing_canonical_definitions,
            failure_history=failure_history or None,
            model=current_model.model_id,
            return_diagnostics=True,
        )
        if isinstance(rewrite_result, RewriteAttemptResult):
            rewrite_candidate = rewrite_result.definition
            rewrite_rejection_reason = rewrite_result.last_rejection
        else:
            rewrite_candidate = str(rewrite_result or "")
        _maybe_add(rewrite_candidate, source="rewrite", strategy_label="rewrite")
    except Exception as exc:
        had_error = True
        log(f"  Rewrite failed for {clue.word_normalized}: {exc}")

    if use_hybrid:
        try:
            fresh_candidate = generate_definition(
                client,
                clue.word_normalized,
                clue.word_original,
                theme,
                retries=3,
                word_type=clue.word_type,
                dex_definitions=dex_defs,
                existing_canonical_definitions=existing_canonical_definitions,
                model=current_model.model_id,
            )
            _maybe_add(fresh_candidate, source="generate", strategy_label="fresh_generate")
        except Exception as exc:
            had_error = True
            log(f"  Fresh generate failed for {clue.word_normalized}: {exc}")

    return pending, had_error, rewrite_rejection_reason


def _evaluate_single_candidate(
    puzzle: WorkingPuzzle,
    clue: WorkingClue,
    candidate: PendingCandidate,
    *,
    client,
    evaluator_model,
    preset_skip: set[str],
    dex: DexProvider,
    round_index: int,
    verify_candidates: int,
) -> ClueCandidateVersion:
    skip_words = ({c.word_normalized for c in all_working_clues(puzzle)} - {clue.word_normalized}) | preset_skip
    set_current_definition(
        clue,
        candidate.definition,
        round_index=round_index,
        source=candidate.source,
        generated_by=candidate.generated_by,
    )
    verify_working_puzzle(
        puzzle,
        client,
        skip_words=skip_words,
        model_label=evaluator_model.display_name,
        model_name=evaluator_model.model_id,
        max_guesses=verify_candidates,
    )
    rate_working_puzzle(
        puzzle,
        client,
        skip_words=skip_words,
        dex=dex,
        model_label=evaluator_model.display_name,
        model_name=evaluator_model.model_id,
    )
    return copy.deepcopy(clue.current)


def _select_hybrid_candidate(
    clue: WorkingClue,
    candidates: list[tuple[PendingCandidate, ClueCandidateVersion]],
    *,
    client,
    model_name: str,
) -> tuple[PendingCandidate, ClueCandidateVersion]:
    if len(candidates) == 1:
        return candidates[0]

    (candidate_a, version_a), (candidate_b, version_b) = candidates[0], candidates[1]

    def _tiebreak(a_text: str, b_text: str) -> str:
        return choose_better_clue_variant(
            client,
            clue.word_normalized,
            len(clue.word_normalized),
            a_text,
            b_text,
            model=model_name,
        )

    chosen_version, _decision = choose_clue_version(version_a, version_b, tiebreaker=_tiebreak)
    if _definition_key(chosen_version.definition) == _definition_key(version_b.definition):
        return candidate_b, version_b
    return candidate_a, version_a


def run_rewrite_loop(
    puzzle: WorkingPuzzle,
    client,
    *,
    rounds: int,
    theme: str,
    multi_model: bool = False,
    dex: DexProvider | None = None,
    verify_candidates: int = VERIFY_CANDIDATE_COUNT,
    hybrid_deanchor: bool = False,
    clue_canon: ClueCanonService | None = None,
    runtime: LmRuntime | None = None,
) -> RewriteLoopResult:
    """Verify, rate, rewrite, and restore best clue versions for a puzzle."""
    if dex is None:
        dex = DexProvider.for_puzzle(puzzle)

    if clue_canon is None:
        try:
            clue_canon = ClueCanonService()
        except RuntimeError as exc:
            log(f"  Canonical prompt examples unavailable: {exc}")
            clue_canon = None
    runtime = runtime or LmRuntime(multi_model=multi_model)
    current_model = runtime.activate_initial_evaluator()
    if multi_model:
        log(f"  Model activ (evaluare inițială): {current_model.display_name}")

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
        _update_best_clue_version(clue, client=client, model_name=current_model.model_id)

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
    hybrid_attempted_words: set[str] = set()

    for round_index in range(1, rounds + 1):
        current_scores = [_extract_rebus_score(c) or 0 for c in all_working_clues(puzzle)]
        current_min = min(current_scores) if current_scores else 0
        min_rebus_history.append(current_min)

        if has_plateaued(min_rebus_history, PLATEAU_LOOKBACK):
            log(f"  Plateau after {round_index} rounds (min_rebus={current_min})")
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
            log(f"  Model activ (rescriere): {current_model.display_name}")

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
        log(
            f"Rewrite round {round_index}: {len(candidates)} candidates "
            f"({failed_count} failed, {low_rated_count} low-rated, {unrated_count} unrated)"
        )

        changed_words: set[str] = set()
        pending_candidates_by_word: dict[str, list[PendingCandidate]] = {}
        for clue in candidates:
            outcome = outcomes[clue.word_normalized]
            outcome.was_candidate = True
            clue_ref = clue_label_from_working_clue(clue)
            if _is_locked_clue(clue):
                log(f"  {clue_ref}: blocată la {LOCKED_SEMANTIC}/{LOCKED_REBUS}")
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
            use_hybrid = _should_try_hybrid(
                clue,
                hybrid_attempted_words=hybrid_attempted_words,
                hybrid_deanchor=hybrid_deanchor,
            )
            if use_hybrid:
                hybrid_attempted_words.add(clue.word_normalized)
            pending_candidates, had_error, rewrite_rejection_reason = _build_pending_candidates(
                clue,
                client=client,
                theme=theme,
                current_model=current_model,
                clue_canon=clue_canon,
                wrong_guess=wrong_guess,
                wrong_guesses=wrong_guesses,
                rating_feedback=rating_feedback,
                bad_example_definition=bad_example_definition,
                bad_example_reason=bad_example_reason,
                dex_defs=dex_defs,
                failure_history=failure_history,
                use_hybrid=use_hybrid,
            )
            outcome.had_error = outcome.had_error or had_error

            if pending_candidates:
                clue.current.assessment.rewrite_rejection_reason = ""
                outcome.changed_definition = True
                changed_words.add(clue.word_normalized)
                consecutive_failures[clue.word_normalized] = 0
                pending_candidates_by_word[clue.word_normalized] = pending_candidates
                if len(pending_candidates) == 1:
                    only = pending_candidates[0]
                    log_definition_event(
                        "rewrite-candidate",
                        clue_ref=clue_ref,
                        before=clue.current.definition,
                        after=only.definition,
                        detail=only.strategy_label if use_hybrid else only.source,
                    )
                else:
                    log(
                        f"  {clue_ref}: hybrid "
                        f"rewrite='{_compact_log_text(pending_candidates[0].definition)}' | "
                        f"fresh='{_compact_log_text(pending_candidates[1].definition)}'"
                    )
            else:
                if rewrite_rejection_reason:
                    clue.current.assessment.rewrite_rejection_reason = rewrite_rejection_reason
                consecutive_failures[clue.word_normalized] = consecutive_failures.get(clue.word_normalized, 0) + 1
                if consecutive_failures[clue.word_normalized] >= MAX_CONSECUTIVE_FAILURES:
                    stuck_words.add(clue.word_normalized)
                    log(
                        f"  {clue_ref}: marcată ca blocată după "
                        f"{consecutive_failures[clue.word_normalized]} încercări eșuate consecutive"
                    )

        current_model = runtime.alternate()
        if multi_model:
            log(f"  Model activ (evaluare): {current_model.display_name}")

        evaluated_words: set[str] = set()
        for clue in candidates:
            pending_candidates = pending_candidates_by_word.get(clue.word_normalized)
            if not pending_candidates:
                continue
            if len(pending_candidates) == 1:
                only = pending_candidates[0]
                set_current_definition(
                    clue,
                    only.definition,
                    round_index=round_index,
                    source=only.source,
                    generated_by=only.generated_by,
                )
                if only.strategy_label == "rewrite":
                    outcomes[clue.word_normalized].selected_strategy = "rewrite_only"
                elif only.strategy_label == "fresh_generate":
                    outcomes[clue.word_normalized].selected_strategy = "fresh_only"
                else:
                    outcomes[clue.word_normalized].selected_strategy = only.strategy_label
                continue

            evaluated_versions = [
                (
                    candidate,
                    _evaluate_single_candidate(
                        puzzle,
                        clue,
                        candidate,
                        client=client,
                        evaluator_model=current_model,
                        preset_skip=preset_skip,
                        dex=dex,
                        round_index=round_index,
                        verify_candidates=verify_candidates,
                    ),
                )
                for candidate in pending_candidates
            ]
            chosen_candidate, chosen_version = _select_hybrid_candidate(
                clue,
                evaluated_versions,
                client=client,
                model_name=current_model.model_id,
            )
            clue.current = copy.deepcopy(chosen_version)
            evaluated_words.add(clue.word_normalized)
            outcomes[clue.word_normalized].selected_strategy = chosen_candidate.strategy_label
            log(
                f"  {clue.word_normalized}: ales {chosen_candidate.strategy_label} -> "
                f"'{_compact_log_text(chosen_version.definition)}'"
            )

        pending_collective_words = changed_words - evaluated_words
        if pending_collective_words:
            skip_words = ({c.word_normalized for c in all_working_clues(puzzle)} - pending_collective_words) | preset_skip
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
            _update_best_clue_version(clue, client=client, model_name=current_model.model_id)
            if clue.locked:
                log(f"  {clue.word_normalized}: definiție blocată la {LOCKED_SEMANTIC}/{LOCKED_REBUS}")

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
        log(f"  [DEX audit] {clue.word_normalized}: short DEX not included în redefinire ({reason})")
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
        model_switches=runtime.switch_count,
        outcomes=outcomes,
        improved_versions=improved_versions,
    )
