from __future__ import annotations

import copy
from dataclasses import dataclass, field

from rebus_generator.platform.config import VERIFY_CANDIDATE_COUNT
from rebus_generator.platform.io.dex_cache import DexProvider
from rebus_generator.platform.io.runtime_logging import audit, log
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.llm.llm_dispatch import initial_generation_model, run_single_model_call
from rebus_generator.workflows.canonicals.domain_service import ClueCanonService
from rebus_generator.domain.pipeline_state import ClueCandidateVersion, WorkingPuzzle, all_working_clues
from rebus_generator.domain.score_helpers import (
    _extract_rebus_score,
    _extract_semantic_score,
    _restore_best_versions,
    _update_best_clue_version,
)
from rebus_generator.platform.llm.definition_referee import choose_better_clue_variant


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
    candidates: list
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
    initial_passed: int = 0
    final_result: RewriteLoopResult | None = None


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
    from . import rewrite_engine as facade

    if dex is None:
        dex = DexProvider.for_puzzle(puzzle)
    if clue_canon is None:
        try:
            clue_canon = ClueCanonService()
        except RuntimeError as exc:
            log(f"  Canonical prompt examples unavailable: {exc}")
            clue_canon = None
    runtime = runtime or facade.LmRuntime(multi_model=multi_model)
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
        scoring_runtime=facade.LmRuntime(multi_model=True),
        current_model=initial_generation_model(runtime),
    )
    if multi_model:
        log(f"  Model selectat (evaluare inițială): {session.current_model.display_name}")
    return session


def rewrite_session_initial_verify(session: RewriteSession) -> tuple[int, int]:
    from . import rewrite_engine as facade

    result = facade.verify_working_puzzle(
        session.puzzle,
        session.client,
        skip_words=session.preset_skip,
        runtime=session.scoring_runtime,
        max_guesses=session.verify_candidates,
    )
    if isinstance(result, tuple) and len(result) == 2:
        passed, total = result
    else:
        clues = list(all_working_clues(session.puzzle))
        passed = sum(1 for clue in clues if clue.current.assessment.verified)
        total = len(clues)
    session.initial_passed = passed
    return passed, total


def rewrite_session_initial_rate(session: RewriteSession) -> None:
    from . import rewrite_engine as facade

    facade.rate_working_puzzle(
        session.puzzle,
        session.client,
        skip_words=session.preset_skip,
        dex=session.dex,
        runtime=session.scoring_runtime,
    )
    for clue in all_working_clues(session.puzzle):
        facade._update_best_clue_version(
            clue,
            tiebreaker=lambda a_text, b_text, word=clue.word_normalized: _dispatch_tiebreak(session, word, a_text, b_text),
        )
    for clue in all_working_clues(session.puzzle):
        session.outcomes[clue.word_normalized] = RewriteWordOutcome(
            word=clue.word_normalized,
            initial_semantic=_extract_semantic_score(clue) or 0,
            initial_rebus=_extract_rebus_score(clue) or 0,
        )
    session.initialized = True


def finish_rewrite_session(session: RewriteSession) -> RewriteLoopResult:
    from . import rewrite_engine as facade

    if session.final_result is not None:
        return session.final_result
    _restore_best_versions(session.puzzle)
    final_passed = sum(1 for clue in all_working_clues(session.puzzle) if clue.current.assessment.verified)
    improved_versions: dict[str, ClueCandidateVersion] = {}
    unresolved = {entry["word"]: entry["definition"] for entry in session.dex.uncertain_short_definitions()}
    for clue in all_working_clues(session.puzzle):
        outcome = session.outcomes[clue.word_normalized]
        outcome.final_semantic = _extract_semantic_score(clue) or 0
        outcome.final_rebus = _extract_rebus_score(clue) or 0
        if outcome.final_rebus > outcome.initial_rebus or outcome.final_semantic > outcome.initial_semantic:
            improved_versions[clue.word_normalized] = copy.deepcopy(clue.active_version())
        unresolved_definition = unresolved.get(clue.word_normalized)
        if unresolved_definition is None or clue.word_normalized in improved_versions:
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
        facade.audit(
            "dex_short_definition_not_included_in_redefinire",
            component="rewrite_engine",
            payload={"word": clue.word_normalized, "definition": unresolved_definition, "reason": reason},
        )
    session.final_result = RewriteLoopResult(
        initial_passed=session.initial_passed,
        final_passed=final_passed,
        total=len(all_working_clues(session.puzzle)),
        model_switches=session.runtime.switch_count,
        outcomes=session.outcomes,
        improved_versions=improved_versions,
    )
    return session.final_result
