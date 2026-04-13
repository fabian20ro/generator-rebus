from __future__ import annotations

import copy

from rebus_generator.platform.config import VERIFY_CANDIDATE_COUNT
from rebus_generator.platform.io.clue_logging import clue_label_from_working_clue, log_definition_event
from rebus_generator.platform.io.dex_cache import DexProvider
from rebus_generator.platform.io.runtime_logging import log
from rebus_generator.platform.llm.ai_clues import RewriteAttemptResult, generate_definition, rewrite_definition
from rebus_generator.platform.llm.definition_referee import choose_better_clue_variant
from rebus_generator.platform.llm.llm_dispatch import next_generation_model, run_single_model_call
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.domain.pipeline_state import WorkingClue, WorkingPuzzle, all_working_clues, set_current_definition
from rebus_generator.domain.plateau import has_plateaued
from rebus_generator.domain.selection_engine import choose_clue_version, stable_tie_rng
from rebus_generator.domain.guards.definition_guards import validate_definition_text
from rebus_generator.domain.score_helpers import (
    LOCKED_REBUS,
    LOCKED_SEMANTIC,
    MAX_CONSECUTIVE_FAILURES,
    PLATEAU_LOOKBACK,
    _compact_log_text,
    _extract_rebus_score,
    _extract_semantic_score,
    _is_locked_clue,
    _needs_rewrite,
    _synthesize_failure_reason,
    _update_best_clue_version,
)

from .rewrite_session import (
    PendingCandidate,
    RewriteLoopResult,
    RewriteRoundState,
    RewriteSession,
    finish_rewrite_session,
    start_rewrite_session,
)

HYBRID_REBUS_THRESHOLD = 4
MAX_REWRITE_CANDIDATES_PER_ROUND = 12


def _definition_key(text: str) -> str:
    return " ".join((text or "").split()).lower()


def _should_try_hybrid(
    clue: WorkingClue,
    *,
    hybrid_attempted_words: set[str],
    hybrid_deanchor: bool,
) -> bool:
    if not hybrid_deanchor or clue.word_normalized in hybrid_attempted_words:
        return False
    if not clue.current.definition or clue.current.definition.startswith("["):
        return False
    if clue.current.assessment.verified is False:
        return True
    return (_extract_rebus_score(clue) or 0) <= HYBRID_REBUS_THRESHOLD


def _rewrite_priority(clue: WorkingClue) -> tuple[object, ...]:
    assessment = clue.current.assessment
    return (
        0 if assessment.verified is False else 1,
        _extract_rebus_score(clue) or 0,
        _extract_semantic_score(clue) or 0,
        clue.word_normalized,
    )


def _build_pending_candidates(
    clue: WorkingClue,
    *,
    client,
    theme: str,
    current_model,
    generation_runtime: LmRuntime,
    clue_canon,
    wrong_guess: str,
    wrong_guesses: list[str],
    rating_feedback: str,
    bad_example_definition: str,
    bad_example_reason: str,
    dex_defs: str,
    failure_history: list[tuple[str, list[str]]],
    use_hybrid: bool,
) -> tuple[list[PendingCandidate], bool, str]:
    from . import rewrite_engine as facade

    pending: list[PendingCandidate] = []
    seen: set[str] = set()
    rewrite_rejection_reason = ""

    def _maybe_add(definition: str, *, source: str, strategy_label: str) -> None:
        nonlocal rewrite_rejection_reason
        cleaned = (definition or "").strip()
        if not cleaned or cleaned == clue.current.definition:
            return
        rejection = validate_definition_text(clue.word_normalized, cleaned)
        if rejection:
            if not rewrite_rejection_reason:
                rewrite_rejection_reason = rejection
            log(
                f"    [rewrite rejected {clue.word_normalized}: {rejection}; definition={cleaned[:120]}]",
                level="WARN",
            )
            return
        key = _definition_key(cleaned)
        if key in seen:
            return
        seen.add(key)
        pending.append(PendingCandidate(source=source, definition=cleaned, generated_by=current_model.display_name, strategy_label=strategy_label))

    had_error = False
    existing_canonical_definitions = clue_canon.fetch_prompt_examples(clue.word_normalized) if clue_canon is not None else []
    if clue.current.definition.startswith("["):
        generated = run_single_model_call(
            runtime=generation_runtime,
            model=current_model,
            purpose="definition_generate",
            task_label="rewrite_generate",
            callback=lambda model: facade.generate_definition(
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
            callback=lambda model: facade.rewrite_definition(
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
                callback=lambda model: facade.generate_definition(
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
):
    from . import rewrite_engine as facade

    skip_words = ({c.word_normalized for c in all_working_clues(puzzle)} - {clue.word_normalized}) | preset_skip
    set_current_definition(clue, candidate.definition, round_index=round_index, source=candidate.source, generated_by=candidate.generated_by)
    facade.verify_working_puzzle(puzzle, client, skip_words=skip_words, runtime=scoring_runtime, max_guesses=verify_candidates)
    facade.rate_working_puzzle(puzzle, client, skip_words=skip_words, dex=dex, runtime=scoring_runtime)
    return copy.deepcopy(clue.current)


def _select_hybrid_candidate(
    clue: WorkingClue,
    candidates: list[tuple[PendingCandidate, object]],
    *,
    client,
    runtime: LmRuntime,
    model_config,
) -> tuple[PendingCandidate, object]:
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

    chosen_version, _ = choose_clue_version(
        version_a,
        version_b,
        tiebreaker=_tiebreak,
        rng=stable_tie_rng(
            "_select_hybrid_candidate",
            clue.word_normalized,
            version_a.definition,
            version_b.definition,
        ),
    )
    if _definition_key(chosen_version.definition) == _definition_key(version_b.definition):
        return candidate_b, version_b
    return candidate_a, version_a


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

    # Only exit early due to plateau if the floor is already decent (>= 6)
    if current_min >= 6 and has_plateaued(session.min_rebus_history, PLATEAU_LOOKBACK):
        log(f"  Plateau after {session.round_index} rounds (min_rebus={current_min})")
        finish_rewrite_session(session)
        return None

    # 3-Stage Target Logic
    if current_min < 6:
        round_min_rebus = 6
    elif current_min == 6:
        round_min_rebus = 7
    else:
        round_min_rebus = 8

    all_candidates = [
        clue
        for clue in all_working_clues(session.puzzle)
        if _needs_rewrite(clue, min_rebus=round_min_rebus)
        # Remove stuck_words check to keep trying for full duration
    ]
    if not all_candidates:
        finish_rewrite_session(session)
        return None

    # Remove candidate count limit (MAX_REWRITE_CANDIDATES_PER_ROUND)
    candidates = sorted(all_candidates, key=_rewrite_priority)

    if session.multi_model:
        log(f"  Model activ (rescriere): {session.current_model.display_name}")

    failed_count = sum(1 for clue in candidates if clue.current.assessment.verified is False)
    low_rated_count = sum(
        1
        for clue in candidates
        if clue.current.assessment.verified is True
        and (((_extract_semantic_score(clue) or 0) < LOCKED_SEMANTIC) or ((_extract_rebus_score(clue) or 0) < LOCKED_REBUS))
    )
    log(
        f"Rewrite round {session.round_index}: {len(candidates)} candidates "
        f"(selected from {len(all_candidates)}) "
        f"({failed_count} failed, {low_rated_count} low-rated, {len(candidates) - failed_count - low_rated_count} unrated)"
    )

    round_state = RewriteRoundState(round_index=session.round_index, round_min_rebus=round_min_rebus, candidates=candidates)
    for clue in candidates:
        outcome = session.outcomes[clue.word_normalized]
        outcome.was_candidate = True
        clue_ref = clue_label_from_working_clue(clue)
        if _is_locked_clue(clue):
            log(f"  {clue_ref}: blocată la {LOCKED_SEMANTIC}/{LOCKED_REBUS}")
            continue
        failure_history = [
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
            wrong_guess=clue.current.assessment.wrong_guess,
            wrong_guesses=list(clue.current.assessment.verify_candidates),
            rating_feedback=clue.current.assessment.feedback,
            bad_example_definition=clue.current.definition if session.round_index >= 2 else "",
            bad_example_reason=_synthesize_failure_reason(clue) if session.round_index >= 2 else "",
            dex_defs=session.dex.get(clue.word_normalized, clue.word_original) or "",
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
                log_definition_event("rewrite-candidate", clue_ref=clue_ref, before=clue.current.definition, after=only.definition, detail=only.strategy_label if use_hybrid else only.source)
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
            # Quarantine disabled: keep trying for the full duration of the available rounds

    session.current_model = next_generation_model(session.runtime, session.current_model)
    if session.multi_model:
        log(f"  Model selectat (evaluare): {session.current_model.display_name}")
    session.current_round = round_state
    return round_state


def rewrite_session_score_round(session: RewriteSession) -> None:
    from . import rewrite_engine as facade

    round_state = session.current_round
    if round_state is None:
        return
    for clue in round_state.candidates:
        pending_candidates = round_state.pending_candidates_by_word.get(clue.word_normalized)
        if not pending_candidates:
            continue
        if len(pending_candidates) == 1:
            only = pending_candidates[0]
            set_current_definition(clue, only.definition, round_index=round_state.round_index, source=only.source, generated_by=only.generated_by)
            session.outcomes[clue.word_normalized].selected_strategy = (
                "rewrite_only" if only.strategy_label == "rewrite"
                else "fresh_only" if only.strategy_label == "fresh_generate"
                else only.strategy_label
            )
            continue

        chosen_candidate, chosen_version = _select_hybrid_candidate(
            clue,
            [
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
            ],
            client=session.client,
            runtime=session.runtime,
            model_config=session.current_model,
        )
        clue.current = copy.deepcopy(chosen_version)
        round_state.evaluated_words.add(clue.word_normalized)
        session.outcomes[clue.word_normalized].selected_strategy = chosen_candidate.strategy_label
        log(f"  {clue.word_normalized}: ales {chosen_candidate.strategy_label} -> '{_compact_log_text(chosen_version.definition)}'")

    pending_collective_words = round_state.changed_words - round_state.evaluated_words
    if pending_collective_words:
        skip_words = ({c.word_normalized for c in all_working_clues(session.puzzle)} - pending_collective_words) | session.preset_skip
        facade.verify_working_puzzle(session.puzzle, session.client, skip_words=skip_words, runtime=session.scoring_runtime, max_guesses=session.verify_candidates)
        facade.rate_working_puzzle(session.puzzle, session.client, skip_words=skip_words, dex=session.dex, runtime=session.scoring_runtime)


def rewrite_session_finalize_round(session: RewriteSession) -> None:
    round_state = session.current_round
    if round_state is None:
        return
    for clue in all_working_clues(session.puzzle):
        if clue.word_normalized not in round_state.changed_words:
            continue
        _update_best_clue_version(
            clue,
            tiebreaker=lambda a_text, b_text, word=clue.word_normalized: run_single_model_call(
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
            ),
        )
        if clue.locked:
            log(f"  {clue.word_normalized}: definiție blocată la {LOCKED_SEMANTIC}/{LOCKED_REBUS}")
    session.current_round = None
    session.round_index += 1
    if session.round_index > session.rounds:
        finish_rewrite_session(session)


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
    clue_canon=None,
    runtime: LmRuntime | None = None,
) -> RewriteLoopResult:
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
    from .rewrite_session import rewrite_session_initial_rate, rewrite_session_initial_verify

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
