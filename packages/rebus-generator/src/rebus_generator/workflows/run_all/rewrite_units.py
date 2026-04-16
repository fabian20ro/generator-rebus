from __future__ import annotations

import copy
from dataclasses import dataclass, field

from rebus_generator.domain.pipeline_state import ClueCandidateVersion, WorkingClue, all_working_clues, set_current_definition
from rebus_generator.domain.plateau import has_plateaued
from rebus_generator.domain.diacritics import normalize
from rebus_generator.domain.score_helpers import (
    LOCKED_REBUS,
    LOCKED_SEMANTIC,
    MAX_CONSECUTIVE_FAILURES,
    PLATEAU_LOOKBACK,
    _extract_rebus_score,
    _extract_semantic_score,
    _is_locked_clue,
    _needs_rewrite,
    _restore_best_versions,
    _synthesize_failure_reason,
    _update_best_clue_version,
)
from rebus_generator.domain.selection_engine import choose_clue_version, stable_tie_rng
from rebus_generator.platform.io.clue_logging import clue_label_from_working_clue
from rebus_generator.platform.io.dex_cache import DexProvider
from rebus_generator.platform.io.runtime_logging import audit, log
from rebus_generator.platform.llm.ai_clues import RewriteAttemptResult, generate_definition, rewrite_definition
from rebus_generator.platform.llm.llm_dispatch import initial_generation_model, next_generation_model
from rebus_generator.platform.llm.models import PRIMARY_MODEL, SECONDARY_MODEL
from rebus_generator.workflows.canonicals.domain_service import ClueCanonService
from rebus_generator.workflows.generate.verify import (
    _finalize_pair_rating,
    _finalize_pair_verification,
    rate_clue_with_model,
    verify_clue_with_model,
)
from rebus_generator.workflows.redefine.rewrite_rounds import (
    HYBRID_REBUS_THRESHOLD,
    MAX_REWRITE_CANDIDATES_PER_ROUND,
    _rewrite_priority,
)
from rebus_generator.workflows.redefine.rewrite_session import RewriteLoopResult, RewriteWordOutcome


@dataclass(frozen=True)
class RewriteGenerationRequest:
    word: str
    strategy_label: str
    source: str


@dataclass
class RewriteCandidateEval:
    word: str
    request: RewriteGenerationRequest
    candidate: ClueCandidateVersion
    verify_done_models: set[str] = field(default_factory=set)
    rate_done_models: set[str] = field(default_factory=set)


@dataclass
class RunAllRewriteRound:
    round_index: int
    round_min_rebus: int
    generator_model_id: str
    clues_by_word: dict[str, WorkingClue]
    changed_words: set[str] = field(default_factory=set)
    generation_requests_by_word: dict[str, list[RewriteGenerationRequest]] = field(default_factory=dict)
    pending_candidates_by_word: dict[str, list[ClueCandidateVersion]] = field(default_factory=dict)
    pending_candidate_requests_by_word: dict[str, list[RewriteGenerationRequest]] = field(default_factory=dict)
    generation_done_by_word: dict[str, set[str]] = field(default_factory=dict)
    generation_had_error: dict[str, bool] = field(default_factory=dict)
    generation_rejection_reason: dict[str, str] = field(default_factory=dict)
    evals_by_word: dict[str, list[RewriteCandidateEval]] = field(default_factory=dict)


def _definition_key(text: str) -> str:
    return " ".join((text or "").split()).lower()


class RunAllRewriteSession:
    def __init__(
        self,
        *,
        puzzle,
        client,
        rounds: int,
        theme: str,
        multi_model: bool,
        dex: DexProvider,
        verify_candidates: int,
        hybrid_deanchor: bool,
        runtime,
        clue_canon: ClueCanonService | None = None,
        preset_skip: set[str] | None = None,
    ) -> None:
        self.puzzle = puzzle
        self.client = client
        self.rounds = rounds
        self.theme = theme
        self.multi_model = multi_model
        self.dex = dex
        self.verify_candidates = verify_candidates
        self.hybrid_deanchor = hybrid_deanchor
        self.runtime = runtime
        self.clue_canon = clue_canon
        self.preset_skip = set(preset_skip or set())
        self.phase = "initial_verify"
        self.initial_verify_done: dict[str, set[str]] = {model_id: set() for model_id in self.model_order}
        self.initial_rate_done: dict[str, set[str]] = {model_id: set() for model_id in self.model_order}
        self.outcomes: dict[str, RewriteWordOutcome] = {}
        self.min_rebus_history: list[int] = []
        self.consecutive_failures: dict[str, int] = {}
        self.stuck_words: set[str] = set()
        self.hybrid_attempted_words: set[str] = set()
        self.round_index = 1
        self.current_generation_model_id = initial_generation_model(runtime).model_id
        self.current_round: RunAllRewriteRound | None = None
        self.initial_passed = 0
        self.generation_model_switches = 0
        self.final_result: RewriteLoopResult | None = None

    @property
    def model_order(self) -> list[str]:
        if self.multi_model:
            return [PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id]
        return [PRIMARY_MODEL.model_id]

    def clues(self) -> list[WorkingClue]:
        return list(all_working_clues(self.puzzle))

    def eligible_clues(self) -> list[WorkingClue]:
        return [
            clue
            for clue in self.clues()
            if clue.word_normalized not in self.preset_skip
            and clue.current.definition
            and not clue.current.definition.startswith("[")
        ]

    def build_initial_outcomes(self) -> None:
        for clue in self.clues():
            self.outcomes[clue.word_normalized] = RewriteWordOutcome(
                word=clue.word_normalized,
                initial_semantic=_extract_semantic_score(clue) or 0,
                initial_rebus=_extract_rebus_score(clue) or 0,
            )

    def initial_verify_units(self, unit_factory) -> list[object]:
        clues = self.eligible_clues()
        for model_id in self.model_order:
            pending = [clue for clue in clues if clue.word_normalized not in self.initial_verify_done[model_id]]
            if pending:
                return [
                    unit_factory(
                        step_id=f"rewrite_initial_verify:{model_id}:{clue.word_normalized}",
                        purpose="rewrite_initial_verify",
                        model_id=model_id,
                        phase="initial_verify",
                        runner=lambda _ctx, clue=clue, model_id=model_id: verify_clue_with_model(
                            clue,
                            self.client,
                            model_id=model_id,
                            max_guesses=self.verify_candidates,
                        ),
                        coalesce_key=f"rewrite_initial_verify:{model_id}",
                    )
                    for clue in pending
                ]
        return []

    def note_initial_verify_done(self, model_id: str, word: str) -> None:
        self.initial_verify_done.setdefault(model_id, set()).add(word)

    def finalize_initial_verify(self) -> None:
        label = " + ".join("gemma + eurollm".split()) if self.multi_model else PRIMARY_MODEL.display_name
        clues = _finalize_pair_verification(self.clues(), model_order=self.model_order, model_label=label)
        split = len(self.puzzle.horizontal_clues)
        self.puzzle.horizontal_clues = clues[:split]
        self.puzzle.vertical_clues = clues[split:]
        self.initial_passed = sum(1 for clue in self.clues() if clue.current.assessment.verified)
        self.phase = "initial_rate"

    def initial_rate_units(self, unit_factory) -> list[object]:
        clues = self.eligible_clues()
        for model_id in self.model_order:
            pending = [clue for clue in clues if clue.word_normalized not in self.initial_rate_done[model_id]]
            if pending:
                return [
                    unit_factory(
                        step_id=f"rewrite_initial_rate:{model_id}:{clue.word_normalized}",
                        purpose="rewrite_initial_rate",
                        model_id=model_id,
                        phase="initial_rate",
                        runner=lambda _ctx, clue=clue, model_id=model_id: rate_clue_with_model(
                            clue,
                            self.client,
                            dex=self.dex,
                            model_id=model_id,
                        ),
                        coalesce_key=f"rewrite_initial_rate:{model_id}",
                    )
                    for clue in pending
                ]
        return []

    def note_initial_rate_done(self, model_id: str, word: str) -> None:
        self.initial_rate_done.setdefault(model_id, set()).add(word)

    def finalize_initial_rate(self) -> None:
        label = " + ".join("gemma + eurollm".split()) if self.multi_model else PRIMARY_MODEL.display_name
        _finalize_pair_rating(self.clues(), model_order=self.model_order, model_label=label)
        for clue in self.clues():
            _update_best_clue_version(clue, tiebreaker=lambda _a, _b: "A")
        self.build_initial_outcomes()
        self.phase = "prepare_round"

    def prepare_round(self) -> None:
        if self.round_index > self.rounds:
            self.phase = "done"
            return
        current_scores = [_extract_rebus_score(c) or 0 for c in self.clues()]
        current_min = min(current_scores) if current_scores else 0
        self.min_rebus_history.append(current_min)

        # Only exit early due to plateau if the floor is already decent (>= 6)
        if current_min >= 6 and has_plateaued(self.min_rebus_history, PLATEAU_LOOKBACK):
            log(f"  Plateau after {self.round_index} rounds (min_rebus={current_min})")
            self.phase = "done"
            return

        # 3-Stage Target Logic
        if current_min < 6:
            round_min_rebus = 6
        elif current_min == 6:
            round_min_rebus = 7
        else:
            round_min_rebus = 8

        candidate_clues = [
            clue
            for clue in self.clues()
            if _needs_rewrite(clue, min_rebus=round_min_rebus)
            and clue.word_normalized not in self.stuck_words
            and self.consecutive_failures.get(clue.word_normalized, 0) < MAX_CONSECUTIVE_FAILURES
        ]
        if not candidate_clues:
            self.phase = "done"
            return

        selected = sorted(candidate_clues, key=_rewrite_priority)[:MAX_REWRITE_CANDIDATES_PER_ROUND]
        round_state = RunAllRewriteRound(
            round_index=self.round_index,
            round_min_rebus=round_min_rebus,
            generator_model_id=self.current_generation_model_id,
            clues_by_word={clue.word_normalized: clue for clue in selected},
        )
        for clue in selected:
            outcome = self.outcomes.setdefault(clue.word_normalized, RewriteWordOutcome(word=clue.word_normalized))
            outcome.was_candidate = True
            if _is_locked_clue(clue):
                continue
            requests = self._generation_requests_for_clue(clue)
            if requests:
                round_state.changed_words.add(clue.word_normalized)
                round_state.generation_requests_by_word[clue.word_normalized] = requests
                round_state.generation_done_by_word[clue.word_normalized] = set()
            else:
                self._note_generation_failure(clue.word_normalized, had_error=False, rejection_reason="")
        self.current_round = round_state
        self.phase = "generate_candidates" if round_state.changed_words else "finalize_round"

    def _generation_requests_for_clue(self, clue: WorkingClue) -> list[RewriteGenerationRequest]:
        requests: list[RewriteGenerationRequest] = []
        if clue.current.definition.startswith("["):
            return [RewriteGenerationRequest(word=clue.word_normalized, strategy_label="fresh_only", source="generate")]
        requests.append(RewriteGenerationRequest(word=clue.word_normalized, strategy_label="rewrite", source="rewrite"))
        use_hybrid = (
            self.hybrid_deanchor
            and clue.word_normalized not in self.hybrid_attempted_words
            and clue.current.definition
            and not clue.current.definition.startswith("[")
            and (clue.current.assessment.verified is False or (_extract_rebus_score(clue) or 0) <= HYBRID_REBUS_THRESHOLD)
        )
        if use_hybrid:
            self.hybrid_attempted_words.add(clue.word_normalized)
            requests.append(RewriteGenerationRequest(word=clue.word_normalized, strategy_label="fresh_generate", source="generate"))
        return requests

    def generation_units(self, unit_factory) -> list[object]:
        if self.current_round is None:
            return []
        requests: list[tuple[WorkingClue, RewriteGenerationRequest]] = []
        for word, clue in self.current_round.clues_by_word.items():
            done = self.current_round.generation_done_by_word.get(word, set())
            for request in self.current_round.generation_requests_by_word.get(word, []):
                if request.strategy_label in done:
                    continue
                requests.append((clue, request))
        if not requests:
            return []
        model_id = self.current_round.generator_model_id
        return [
            unit_factory(
                step_id=f"rewrite_generate:{model_id}:{request.word}:{request.strategy_label}",
                purpose="rewrite_generate_candidate",
                model_id=model_id,
                phase="generate_candidates",
                runner=lambda _ctx, clue=clue, request=request, model_id=model_id: self._run_generation_request(
                    clue,
                    request,
                    model_id=model_id,
                ),
                coalesce_key=f"rewrite_generate_candidate:{model_id}",
            )
            for clue, request in requests
        ]

    def _run_generation_request(self, clue: WorkingClue, request: RewriteGenerationRequest, *, model_id: str) -> dict[str, object]:
        existing_canonical_definitions = self.clue_canon.fetch_prompt_examples(clue.word_normalized) if self.clue_canon is not None else []
        dex_defs = self.dex.get(clue.word_normalized, clue.word_original) or ""
        try:
            if request.strategy_label in {"fresh_only", "fresh_generate"}:
                raw = generate_definition(
                    self.client,
                    clue.word_normalized,
                    clue.word_original,
                    self.theme,
                    retries=3,
                    word_type=clue.word_type,
                    dex_definitions=dex_defs,
                    existing_canonical_definitions=existing_canonical_definitions,
                    model=model_id,
                )
                last_rejection = ""
            else:
                rewrite_result = rewrite_definition(
                    self.client,
                    clue.word_normalized,
                    clue.word_original,
                    self.theme,
                    clue.current.definition,
                    clue.current.assessment.wrong_guess,
                    wrong_guesses=list(clue.current.assessment.verify_candidates),
                    rating_feedback=clue.current.assessment.feedback,
                    bad_example_definition=clue.current.definition if self.round_index >= 2 else "",
                    bad_example_reason=_synthesize_failure_reason(clue) if self.round_index >= 2 else "",
                    word_type=clue.word_type,
                    dex_definitions=dex_defs,
                    existing_canonical_definitions=existing_canonical_definitions,
                    failure_history=None,
                    model=model_id,
                    return_diagnostics=True,
                )
                if isinstance(rewrite_result, RewriteAttemptResult):
                    raw = rewrite_result.definition
                    last_rejection = rewrite_result.last_rejection
                else:
                    raw = str(rewrite_result or "")
                    last_rejection = ""
            return {"word": clue.word_normalized, "strategy": request.strategy_label, "source": request.source, "definition": str(raw or ""), "rejection_reason": last_rejection}
        except Exception as exc:
            return {"word": clue.word_normalized, "strategy": request.strategy_label, "source": request.source, "definition": "", "error": str(exc)}

    def apply_generation_result(self, payload: dict[str, object]) -> None:
        if self.current_round is None:
            return
        word = str(payload.get("word") or "")
        strategy = str(payload.get("strategy") or "")
        clue = self.current_round.clues_by_word[word]
        self.current_round.generation_done_by_word.setdefault(word, set()).add(strategy)
        error = str(payload.get("error") or "")
        if error:
            self.current_round.generation_had_error[word] = True
            return
        definition = str(payload.get("definition") or "").strip()
        rejection_reason = str(payload.get("rejection_reason") or "")
        if not definition or _definition_key(definition) == _definition_key(clue.current.definition):
            if rejection_reason:
                self.current_round.generation_rejection_reason[word] = rejection_reason
            return
        key = " ".join(definition.split()).lower()
        existing = {
            " ".join(candidate.definition.split()).lower()
            for candidate in self.current_round.pending_candidates_by_word.get(word, [])
        }
        if key in existing:
            return
        version = copy.deepcopy(clue.current)
        version.definition = definition
        version.round_index = self.current_round.round_index
        version.source = str(payload.get("source") or "")
        version.generated_by = PRIMARY_MODEL.display_name if self.current_round.generator_model_id == PRIMARY_MODEL.model_id else SECONDARY_MODEL.display_name
        version.assessment.verify_votes = {}
        version.assessment.verify_vote_sources = {}
        version.assessment.rating_votes = {}
        version.assessment.rating_vote_sources = {}
        version.assessment.verify_candidates = []
        version.assessment.wrong_guess = ""
        version.assessment.feedback = ""
        version.assessment.rewrite_rejection_reason = ""
        request = RewriteGenerationRequest(word=word, strategy_label=strategy, source=str(payload.get("source") or ""))
        self.current_round.pending_candidates_by_word.setdefault(word, []).append(version)
        self.current_round.pending_candidate_requests_by_word.setdefault(word, []).append(request)

    def finalize_generation(self) -> None:
        if self.current_round is None:
            return
        for word, clue in self.current_round.clues_by_word.items():
            if self.current_round.pending_candidates_by_word.get(word):
                self.consecutive_failures[word] = 0
                continue
            self._note_generation_failure(
                word,
                had_error=bool(self.current_round.generation_had_error.get(word)),
                rejection_reason=self.current_round.generation_rejection_reason.get(word, ""),
            )
            clue.current.assessment.rewrite_rejection_reason = self.current_round.generation_rejection_reason.get(word, "")
        self.current_round.evals_by_word = {}
        for word, candidates in self.current_round.pending_candidates_by_word.items():
            requests = self.current_round.pending_candidate_requests_by_word.get(word, [])
            evals: list[RewriteCandidateEval] = []
            clue = self.current_round.clues_by_word[word]
            for request, candidate in zip(requests, candidates):
                evals.append(RewriteCandidateEval(word=word, request=request, candidate=copy.deepcopy(candidate)))
                evals[-1].candidate = self._build_shadow_candidate(clue, candidate)
            self.current_round.evals_by_word[word] = evals
        self.phase = "evaluate_verify"

    def _build_shadow_candidate(self, clue: WorkingClue, version: ClueCandidateVersion) -> ClueCandidateVersion:
        shadow = copy.deepcopy(clue)
        set_current_definition(
            shadow,
            version.definition,
            round_index=version.round_index,
            source=version.source,
            generated_by=version.generated_by,
        )
        return shadow.current

    def _candidate_shadow_clue(self, clue: WorkingClue, version: ClueCandidateVersion) -> WorkingClue:
        shadow = copy.deepcopy(clue)
        shadow.current = copy.deepcopy(version)
        shadow.best = None
        return shadow

    def evaluation_verify_units(self, unit_factory) -> list[object]:
        if self.current_round is None:
            return []
        for model_id in self.model_order:
            pending: list[tuple[WorkingClue, RewriteCandidateEval]] = []
            for word, evals in self.current_round.evals_by_word.items():
                clue = self.current_round.clues_by_word[word]
                for index, evaluation in enumerate(evals):
                    if model_id in evaluation.verify_done_models:
                        continue
                    pending.append((clue, evaluation))
            if pending:
                return [
                    unit_factory(
                        step_id=f"rewrite_eval_verify:{model_id}:{evaluation.word}:{idx}",
                        purpose="rewrite_evaluate_candidate_verify",
                        model_id=model_id,
                        phase="evaluate_verify",
                        runner=lambda _ctx, clue=clue, evaluation=evaluation, model_id=model_id: self._run_candidate_verify(
                            clue,
                            evaluation,
                            model_id=model_id,
                        ),
                        coalesce_key=f"rewrite_evaluate_candidate_verify:{model_id}",
                    )
                    for idx, (clue, evaluation) in enumerate(pending, start=1)
                ]
        return []

    def _run_candidate_verify(self, clue: WorkingClue, evaluation: RewriteCandidateEval, *, model_id: str) -> dict[str, object]:
        shadow = self._candidate_shadow_clue(clue, evaluation.candidate)
        verify_clue_with_model(
            shadow,
            self.client,
            model_id=model_id,
            max_guesses=self.verify_candidates,
        )
        return {"word": evaluation.word, "definition": evaluation.candidate.definition, "model_id": model_id, "verify_votes": copy.deepcopy(shadow.current.assessment.verify_votes.get(model_id, [])), "verify_vote_source": shadow.current.assessment.verify_vote_sources.get(model_id, "")}

    def apply_candidate_verify_result(self, payload: dict[str, object]) -> None:
        if self.current_round is None:
            return
        word = str(payload.get("word") or "")
        model_id = str(payload.get("model_id") or "")
        definition = str(payload.get("definition") or "")
        for evaluation in self.current_round.evals_by_word.get(word, []):
            if evaluation.candidate.definition != definition:
                continue
            evaluation.candidate.assessment.verify_votes[model_id] = list(payload.get("verify_votes") or [])
            evaluation.candidate.assessment.verify_vote_sources[model_id] = str(payload.get("verify_vote_source") or "")
            evaluation.verify_done_models.add(model_id)
            break

    def evaluation_rate_units(self, unit_factory) -> list[object]:
        if self.current_round is None:
            return []
        primary_model_id = self.model_order[0]
        for model_id in self.model_order:
            pending: list[tuple[WorkingClue, RewriteCandidateEval]] = []
            for word, evals in self.current_round.evals_by_word.items():
                clue = self.current_round.clues_by_word[word]
                for evaluation in evals:
                    if model_id in evaluation.rate_done_models:
                        continue
                    if model_id == primary_model_id:
                        verify_votes = evaluation.candidate.assessment.verify_votes.get(primary_model_id, [])
                        if clue.word_normalized not in [normalize(candidate) for candidate in verify_votes]:
                            evaluation.rate_done_models.update(self.model_order)
                            continue
                    pending.append((clue, evaluation))
            if pending:
                return [
                    unit_factory(
                        step_id=f"rewrite_eval_rate:{model_id}:{evaluation.word}:{idx}",
                        purpose="rewrite_evaluate_candidate_rate",
                        model_id=model_id,
                        phase="evaluate_rate",
                        runner=lambda _ctx, clue=clue, evaluation=evaluation, model_id=model_id: self._run_candidate_rate(
                            clue,
                            evaluation,
                            model_id=model_id,
                        ),
                        coalesce_key=f"rewrite_evaluate_candidate_rate:{model_id}",
                    )
                    for idx, (clue, evaluation) in enumerate(pending, start=1)
                ]
        return []

    def _run_candidate_rate(self, clue: WorkingClue, evaluation: RewriteCandidateEval, *, model_id: str) -> dict[str, object]:
        shadow = self._candidate_shadow_clue(clue, evaluation.candidate)
        rating = rate_clue_with_model(
            shadow,
            self.client,
            dex=self.dex,
            model_id=model_id,
        )
        return {"word": evaluation.word, "definition": evaluation.candidate.definition, "model_id": model_id, "rating": rating, "rating_vote_source": shadow.current.assessment.rating_vote_sources.get(model_id, "")}

    def apply_candidate_rate_result(self, payload: dict[str, object]) -> None:
        if self.current_round is None:
            return
        word = str(payload.get("word") or "")
        model_id = str(payload.get("model_id") or "")
        definition = str(payload.get("definition") or "")
        for evaluation in self.current_round.evals_by_word.get(word, []):
            if evaluation.candidate.definition != definition:
                continue
            if payload.get("rating") is not None:
                evaluation.candidate.assessment.rating_votes[model_id] = payload.get("rating")
            evaluation.candidate.assessment.rating_vote_sources[model_id] = str(payload.get("rating_vote_source") or "")
            evaluation.rate_done_models.add(model_id)
            break

    def select_candidates(self) -> None:
        if self.current_round is None:
            return
        label = "gemma + eurollm" if self.multi_model else PRIMARY_MODEL.display_name
        for word, clue in self.current_round.clues_by_word.items():
            evals = self.current_round.evals_by_word.get(word, [])
            if not evals:
                continue
            finalized_versions: list[tuple[RewriteGenerationRequest, ClueCandidateVersion]] = []
            for evaluation in evals:
                shadow = self._candidate_shadow_clue(clue, evaluation.candidate)
                shadow.current.assessment.verify_votes = copy.deepcopy(evaluation.candidate.assessment.verify_votes)
                shadow.current.assessment.verify_vote_sources = dict(evaluation.candidate.assessment.verify_vote_sources)
                shadow.current.assessment.rating_votes = copy.deepcopy(evaluation.candidate.assessment.rating_votes)
                shadow.current.assessment.rating_vote_sources = dict(evaluation.candidate.assessment.rating_vote_sources)
                _finalize_pair_verification([shadow], model_order=self.model_order, model_label=label)
                _finalize_pair_rating([shadow], model_order=self.model_order, model_label=label)
                finalized_versions.append((evaluation.request, copy.deepcopy(shadow.current)))
            chosen_request, chosen_version = finalized_versions[0]
            for request, version in finalized_versions[1:]:
                chosen_version, _ = choose_clue_version(
                    chosen_version,
                    version,
                    rng=stable_tie_rng(
                        "run_all_rewrite_select",
                        word,
                        chosen_version.definition,
                        version.definition,
                    ),
                )
                if chosen_version.definition == version.definition:
                    chosen_request = request
            clue.current = copy.deepcopy(chosen_version)
            clue.history.append(copy.deepcopy(chosen_version))
            self.outcomes[word].selected_strategy = chosen_request.strategy_label
        self.phase = "finalize_round"

    def finalize_round(self) -> None:
        if self.current_round is None:
            return
        for word in self.current_round.changed_words:
            clue = self.current_round.clues_by_word[word]
            _update_best_clue_version(clue, tiebreaker=lambda _a, _b: "A")
        self.current_round = None
        previous_model = PRIMARY_MODEL if self.current_generation_model_id == PRIMARY_MODEL.model_id else SECONDARY_MODEL
        next_model = next_generation_model(self.runtime, previous_model).model_id
        if next_model != self.current_generation_model_id:
            self.generation_model_switches += 1
        self.current_generation_model_id = next_model
        self.round_index += 1
        self.phase = "prepare_round"

    def _note_generation_failure(self, word: str, *, had_error: bool, rejection_reason: str) -> None:
        self.outcomes[word].had_error = self.outcomes[word].had_error or had_error
        self.consecutive_failures[word] = self.consecutive_failures.get(word, 0) + 1
        if self.consecutive_failures[word] >= MAX_CONSECUTIVE_FAILURES:
            if word not in self.stuck_words:
                log(f"  {word}: quarantined after {self.consecutive_failures[word]} unchanged rounds")
            self.stuck_words.add(word)
        if rejection_reason:
            for clue in self.clues():
                if clue.word_normalized == word:
                    clue.current.assessment.rewrite_rejection_reason = rejection_reason
                    break

    def finish(self) -> RewriteLoopResult:
        if self.final_result is not None:
            return self.final_result
        _restore_best_versions(self.puzzle)
        final_passed = sum(1 for clue in self.clues() if clue.current.assessment.verified)
        improved_versions: dict[str, ClueCandidateVersion] = {}
        unresolved = {entry["word"]: entry["definition"] for entry in self.dex.uncertain_short_definitions()}
        for clue in self.clues():
            outcome = self.outcomes.setdefault(clue.word_normalized, RewriteWordOutcome(word=clue.word_normalized))
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
            elif not outcome.selected_strategy:
                reason = "rewrite_no_change"
            else:
                reason = "not_improved"
            outcome.terminal_reason = reason
            audit(
                "dex_short_definition_not_included_in_redefinire",
                component="rewrite_engine",
                payload={"word": clue.word_normalized, "definition": unresolved_definition, "reason": reason},
            )
        self.final_result = RewriteLoopResult(
            initial_passed=self.initial_passed,
            final_passed=final_passed,
            total=len(self.clues()),
            model_switches=self.generation_model_switches,
            outcomes=self.outcomes,
            improved_versions=improved_versions,
        )
        return self.final_result
