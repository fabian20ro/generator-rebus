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
from .llm_dispatch import initial_generation_model, next_generation_model, run_single_model_call
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


@dataclass
class RewriteRoundState:
    round_index: int
    round_min_rebus: int
    candidates: list[WorkingClue]
    changed_words: set[str] = field(default_factory=set)
    pending_candidates_by_word: dict[str, list[PendingCandidate]] = field(default_factory=dict)
    evaluated_words: set[str] = field(default_factory=set)


@dataclass
class RewriteSession:
    puzzle: WorkingPuzzle
    client: object
    rounds: int
    theme: str
    multi_model: bool
    dex: DexProvider
    verify_candidates: int
    hybrid_deanchor: bool
    clue_canon: ClueCanonService | None
    runtime: LmRuntime
    scoring_runtime: LmRuntime
    current_model: object
    preset_skip: set[str] = field(default_factory=set)
    outcomes: dict[str, RewriteWordOutcome] = field(default_factory=dict)
    min_rebus_history: list[int] = field(default_factory=list)
    consecutive_failures: dict[str, int] = field(default_factory=dict)
    stuck_words: set[str] = field(default_factory=set)
    hybrid_attempted_words: set[str] = field(default_factory=set)
    current_round: RewriteRoundState | None = None
    round_index: int = 1
    initialized: bool = False
    final_result: RewriteLoopResult | None = None


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
    generation_runtime: LmRuntime,
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
        generated = run_single_model_call(
            runtime=generation_runtime,
            model=current_model,
            purpose="definition_generate",
            task_label="rewrite_generate",
            callback=lambda model: generate_definition(
                client,
                clue.word_normalized,
                clue.word_original,
                theme,
                retries=3,
                word_type=clue.word_type,
                dex_definitions=dex_defs,
                existing_canonical_definitions=existing_canonical_definitions,
                model=model.model_id,
            ),
        )
        _maybe_add(generated or "", source="generate", strategy_label="fresh_only")
        return pending, had_error, rewrite_rejection_reason

    try:
        rewrite_result = run_single_model_call(
            runtime=generation_runtime,
            model=current_model,
            purpose="definition_rewrite",
            task_label="rewrite_definition",
            callback=lambda model: rewrite_definition(
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
                model=model.model_id,
                return_diagnostics=True,
            ),
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
            fresh_candidate = run_single_model_call(
                runtime=generation_runtime,
                model=current_model,
                purpose="definition_generate",
                task_label="rewrite_fresh_generate",
                callback=lambda model: generate_definition(
                    client,
                    clue.word_normalized,
                    clue.word_original,
                    theme,
                    retries=3,
                    word_type=clue.word_type,
                    dex_definitions=dex_defs,
                    existing_canonical_definitions=existing_canonical_definitions,
                    model=model.model_id,
                ),
            )
            _maybe_add(str(fresh_candidate or ""), source="generate", strategy_label="fresh_generate")
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
    scoring_runtime: LmRuntime,
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
        runtime=scoring_runtime,
        max_guesses=verify_candidates,
    )
    rate_working_puzzle(
        puzzle,
        client,
        skip_words=skip_words,
        dex=dex,
        runtime=scoring_runtime,
    )
    return copy.deepcopy(clue.current)


def _select_hybrid_candidate(
    clue: WorkingClue,
    candidates: list[tuple[PendingCandidate, ClueCandidateVersion]],
    *,
    client,
    runtime: LmRuntime,
    model_config,
) -> tuple[PendingCandidate, ClueCandidateVersion]:
    if len(candidates) == 1:
        return candidates[0]

    (candidate_a, version_a), (candidate_b, version_b) = candidates[0], candidates[1]

    def _tiebreak(a_text: str, b_text: str) -> str:
        return run_single_model_call(
            runtime=runtime,
            model=model_config,
            purpose="clue_tiebreaker",
            task_label="clue_tiebreaker",
            callback=lambda model: choose_better_clue_variant(
                client,
                clue.word_normalized,
                len(clue.word_normalized),
                a_text,
                b_text,
                model=model.model_id,
            ),
        )

    chosen_version, _decision = choose_clue_version(version_a, version_b, tiebreaker=_tiebreak)
    if _definition_key(chosen_version.definition) == _definition_key(version_b.definition):
        return candidate_b, version_b
    return candidate_a, version_a


def _dispatch_tiebreak(session: RewriteSession, word: str, a_text: str, b_text: str) -> str:
    return run_single_model_call(
        runtime=session.runtime,
        model=session.current_model,
        purpose="clue_tiebreaker",
        task_label="clue_tiebreaker",
        callback=lambda model: choose_better_clue_variant(
            session.client,
            word,
            len(word),
            a_text,
            b_text,
            model=model.model_id,
        ),
    )


def start_rewrite_session(
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
) -> RewriteSession:
    if dex is None:
        dex = DexProvider.for_puzzle(puzzle)
    if clue_canon is None:
        try:
            clue_canon = ClueCanonService()
        except RuntimeError as exc:
            log(f"  Canonical prompt examples unavailable: {exc}")
            clue_canon = None
    runtime = runtime or LmRuntime(multi_model=multi_model)
    session = RewriteSession(
        puzzle=puzzle,
        client=client,
        rounds=rounds,
        theme=theme,
        multi_model=multi_model,
        dex=dex,
        verify_candidates=verify_candidates,
        hybrid_deanchor=hybrid_deanchor,
        clue_canon=clue_canon,
        runtime=runtime,
        scoring_runtime=LmRuntime(multi_model=True),
        current_model=initial_generation_model(runtime),
    )
    if multi_model:
        log(f"  Model selectat (evaluare inițială): {session.current_model.display_name}")
    return session


def rewrite_session_initial_verify(session: RewriteSession) -> tuple[int, int]:
    return verify_working_puzzle(
        session.puzzle,
        session.client,
        skip_words=session.preset_skip,
        runtime=session.scoring_runtime,
        max_guesses=session.verify_candidates,
    )


def rewrite_session_initial_rate(session: RewriteSession) -> None:
    rate_working_puzzle(
        session.puzzle,
        session.client,
        skip_words=session.preset_skip,
        dex=session.dex,
        runtime=session.scoring_runtime,
    )
    for clue in all_working_clues(session.puzzle):
        _update_best_clue_version(
            clue,
            tiebreaker=lambda a_text, b_text, word=clue.word_normalized: _dispatch_tiebreak(session, word, a_text, b_text),
        )
    for clue in all_working_clues(session.puzzle):
        sem = _extract_semantic_score(clue) or 0
        reb = _extract_rebus_score(clue) or 0
        session.outcomes[clue.word_normalized] = RewriteWordOutcome(
            word=clue.word_normalized,
            initial_semantic=sem,
            initial_rebus=reb,
        )
    session.initialized = True


def rewrite_session_prepare_round(session: RewriteSession) -> RewriteRoundState | None:
    if session.final_result is not None:
        return None
    if not session.initialized:
        raise RuntimeError("rewrite session not initialized")
    if session.round_index > session.rounds:
        finish_rewrite_session(session)
        return None

    current_scores = [_extract_rebus_score(c) or 0 for c in all_working_clues(session.puzzle)]
    current_min = min(current_scores) if current_scores else 0
    session.min_rebus_history.append(current_min)
    if has_plateaued(session.min_rebus_history, PLATEAU_LOOKBACK):
        log(f"  Plateau after {session.round_index} rounds (min_rebus={current_min})")
        finish_rewrite_session(session)
        return None

    round_min_rebus = current_min + 1
    candidates = [
        clue
        for clue in all_working_clues(session.puzzle)
        if _needs_rewrite(clue, min_rebus=round_min_rebus)
        and clue.word_normalized not in session.stuck_words
    ]
    if not candidates:
        finish_rewrite_session(session)
        return None

    if session.multi_model:
        log(f"  Model activ (rescriere): {session.current_model.display_name}")

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
        f"Rewrite round {session.round_index}: {len(candidates)} candidates "
        f"({failed_count} failed, {low_rated_count} low-rated, {unrated_count} unrated)"
    )

    round_state = RewriteRoundState(
        round_index=session.round_index,
        round_min_rebus=round_min_rebus,
        candidates=candidates,
    )

    for clue in candidates:
        outcome = session.outcomes[clue.word_normalized]
        outcome.was_candidate = True
        clue_ref = clue_label_from_working_clue(clue)
        if _is_locked_clue(clue):
            log(f"  {clue_ref}: blocată la {LOCKED_SEMANTIC}/{LOCKED_REBUS}")
            continue

        wrong_guess = clue.current.assessment.wrong_guess
        wrong_guesses = list(clue.current.assessment.verify_candidates)
        rating_feedback = clue.current.assessment.feedback
        bad_example_definition = clue.current.definition if session.round_index >= 2 else ""
        bad_example_reason = _synthesize_failure_reason(clue) if session.round_index >= 2 else ""
        dex_defs = session.dex.get(clue.word_normalized, clue.word_original) or ""
        failure_history: list[tuple[str, list[str]]] = [
            (
                version.definition,
                list(version.assessment.verify_candidates)
                if version.assessment.verify_candidates
                else ([version.assessment.wrong_guess] if version.assessment.wrong_guess else []),
            )
            for version in clue.history
            if (version.assessment.verify_candidates or version.assessment.wrong_guess) and version.definition
        ]
        use_hybrid = _should_try_hybrid(
            clue,
            hybrid_attempted_words=session.hybrid_attempted_words,
            hybrid_deanchor=session.hybrid_deanchor,
        )
        if use_hybrid:
            session.hybrid_attempted_words.add(clue.word_normalized)
        pending_candidates, had_error, rewrite_rejection_reason = _build_pending_candidates(
            clue,
            client=session.client,
            theme=session.theme,
            current_model=session.current_model,
            generation_runtime=session.runtime,
            clue_canon=session.clue_canon,
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
            round_state.changed_words.add(clue.word_normalized)
            session.consecutive_failures[clue.word_normalized] = 0
            round_state.pending_candidates_by_word[clue.word_normalized] = pending_candidates
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
            session.consecutive_failures[clue.word_normalized] = session.consecutive_failures.get(clue.word_normalized, 0) + 1
            if session.consecutive_failures[clue.word_normalized] >= MAX_CONSECUTIVE_FAILURES:
                session.stuck_words.add(clue.word_normalized)
                log(
                    f"  {clue_ref}: marcată ca blocată după "
                    f"{session.consecutive_failures[clue.word_normalized]} încercări eșuate consecutive"
                )

    session.current_model = next_generation_model(session.runtime, session.current_model)
    if session.multi_model:
        log(f"  Model selectat (evaluare): {session.current_model.display_name}")
    session.current_round = round_state
    return round_state


def rewrite_session_score_round(session: RewriteSession) -> None:
    round_state = session.current_round
    if round_state is None:
        return
    for clue in round_state.candidates:
        pending_candidates = round_state.pending_candidates_by_word.get(clue.word_normalized)
        if not pending_candidates:
            continue
        if len(pending_candidates) == 1:
            only = pending_candidates[0]
            set_current_definition(
                clue,
                only.definition,
                round_index=round_state.round_index,
                source=only.source,
                generated_by=only.generated_by,
            )
            if only.strategy_label == "rewrite":
                session.outcomes[clue.word_normalized].selected_strategy = "rewrite_only"
            elif only.strategy_label == "fresh_generate":
                session.outcomes[clue.word_normalized].selected_strategy = "fresh_only"
            else:
                session.outcomes[clue.word_normalized].selected_strategy = only.strategy_label
            continue

        evaluated_versions = [
            (
                candidate,
                _evaluate_single_candidate(
                    session.puzzle,
                    clue,
                    candidate,
                    client=session.client,
                    scoring_runtime=session.scoring_runtime,
                    preset_skip=session.preset_skip,
                    dex=session.dex,
                    round_index=round_state.round_index,
                    verify_candidates=session.verify_candidates,
                ),
            )
            for candidate in pending_candidates
        ]
        chosen_candidate, chosen_version = _select_hybrid_candidate(
            clue,
            evaluated_versions,
            client=session.client,
            runtime=session.runtime,
            model_config=session.current_model,
        )
        clue.current = copy.deepcopy(chosen_version)
        round_state.evaluated_words.add(clue.word_normalized)
        session.outcomes[clue.word_normalized].selected_strategy = chosen_candidate.strategy_label
        log(
            f"  {clue.word_normalized}: ales {chosen_candidate.strategy_label} -> "
            f"'{_compact_log_text(chosen_version.definition)}'"
        )

    pending_collective_words = round_state.changed_words - round_state.evaluated_words
    if pending_collective_words:
        skip_words = ({c.word_normalized for c in all_working_clues(session.puzzle)} - pending_collective_words) | session.preset_skip
        verify_working_puzzle(
            session.puzzle,
            session.client,
            skip_words=skip_words,
            runtime=session.scoring_runtime,
            max_guesses=session.verify_candidates,
        )
        rate_working_puzzle(
            session.puzzle,
            session.client,
            skip_words=skip_words,
            dex=session.dex,
            runtime=session.scoring_runtime,
        )


def rewrite_session_finalize_round(session: RewriteSession) -> None:
    round_state = session.current_round
    if round_state is None:
        return
    for clue in all_working_clues(session.puzzle):
        if clue.word_normalized not in round_state.changed_words:
            continue
        _update_best_clue_version(
            clue,
            tiebreaker=lambda a_text, b_text, word=clue.word_normalized: _dispatch_tiebreak(session, word, a_text, b_text),
        )
        if clue.locked:
            log(f"  {clue.word_normalized}: definiție blocată la {LOCKED_SEMANTIC}/{LOCKED_REBUS}")
    session.current_round = None
    session.round_index += 1
    if session.round_index > session.rounds:
        finish_rewrite_session(session)


def finish_rewrite_session(session: RewriteSession) -> RewriteLoopResult:
    if session.final_result is not None:
        return session.final_result
    _restore_best_versions(session.puzzle)
    final_passed = sum(1 for clue in all_working_clues(session.puzzle) if clue.current.assessment.verified)
    total = len(all_working_clues(session.puzzle))
    improved_versions: dict[str, ClueCandidateVersion] = {}
    unresolved = {entry["word"]: entry["definition"] for entry in session.dex.uncertain_short_definitions()}
    for clue in all_working_clues(session.puzzle):
        outcome = session.outcomes[clue.word_normalized]
        outcome.final_semantic = _extract_semantic_score(clue) or 0
        outcome.final_rebus = _extract_rebus_score(clue) or 0
        if outcome.final_rebus > outcome.initial_rebus or outcome.final_semantic > outcome.initial_semantic:
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
    session.final_result = RewriteLoopResult(
        initial_passed=sum(1 for clue in all_working_clues(session.puzzle) if clue.history and clue.history[0].assessment.verified),
        final_passed=final_passed,
        total=total,
        model_switches=session.runtime.switch_count,
        outcomes=session.outcomes,
        improved_versions=improved_versions,
    )
    return session.final_result


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
    session = start_rewrite_session(
        puzzle,
        client,
        rounds=rounds,
        theme=theme,
        multi_model=multi_model,
        dex=dex,
        verify_candidates=verify_candidates,
        hybrid_deanchor=hybrid_deanchor,
        clue_canon=clue_canon,
        runtime=runtime,
    )
    rewrite_session_initial_verify(session)
    rewrite_session_initial_rate(session)
    while session.final_result is None:
        round_state = rewrite_session_prepare_round(session)
        if round_state is None:
            break
        if round_state.changed_words:
            rewrite_session_score_round(session)
        rewrite_session_finalize_round(session)
    return finish_rewrite_session(session)
