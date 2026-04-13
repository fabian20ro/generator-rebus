from __future__ import annotations

import copy

from rebus_generator.platform.llm.ai_clues import consensus_score
from rebus_generator.platform.llm.models import PRIMARY_MODEL, SECONDARY_MODEL
from rebus_generator.domain.guards.title_guards import normalize_title_key
from rebus_generator.workflows.retitle.generate import _generate_candidate_with_active_model
from rebus_generator.workflows.retitle.batch import (
    _RetitleBatchState,
    _finalize_title_result,
    _update_best_result,
)
from rebus_generator.workflows.retitle.load import (
    fetch_clues as fetch_retitle_clues,
    fetch_puzzles as fetch_retitle_puzzles,
    stored_title_score as _stored_title_score,
)
from rebus_generator.workflows.retitle.persist import PreparedTitleUpdate, apply_title_update, prepare_title_update
from rebus_generator.workflows.retitle.rate import _combine_title_feedback, rate_title_creativity
from rebus_generator.workflows.retitle.sanitize import FALLBACK_TITLES, MAX_TITLE_ROUNDS, _build_rejected_context
from .base import JobState


class RetitleJobState(JobState):
    def __init__(self, item) -> None:
        super().__init__(item)
        self.stage = "fetch"
        self.puzzle_row = copy.deepcopy(dict(item.payload["puzzle_row"]))
        self.words_list: list[str] = []
        self.definitions: list[str] = []
        self.forbidden_title_keys: set[str] = set()
        self.title_state: _RetitleBatchState | None = None
        self.pending_title: str | None = None
        self.pending_generator_model = PRIMARY_MODEL
        self.pending_rating_votes: dict[str, tuple[int, str] | None] = {}
        self.round_idx = 1
        self.prepared_update: PreparedTitleUpdate | None = None
        self.old_title_rating_votes: dict[str, tuple[int, str] | None] = {}

    def next_steps(self, ctx):
        return self.plan_ready_units(ctx)

    def plan_ready_units(self, ctx):
        if self.status != "active":
            return []
        if self.stage == "fetch":
            return [self._non_llm_step("fetch", "retitle_fetch", self._fetch)]
        if self.stage == "generate_primary":
            return [self._llm_step("generate_primary", "retitle_generate_primary", PRIMARY_MODEL.model_id, self._generate_primary)]
        if self.stage == "rate_primary":
            return self._rate_units(ctx)
        if self.stage == "generate_secondary":
            return [self._llm_step("generate_secondary", "retitle_generate_secondary", SECONDARY_MODEL.model_id, self._generate_secondary)]
        if self.stage == "rate_secondary":
            return self._rate_units(ctx)
        if self.stage == "round_finalize":
            return [self._non_llm_step("round_finalize", "retitle_round_finalize", self._round_finalize)]
        if self.stage == "resolve_old_score":
            return self._resolve_old_score_units(ctx)
        if self.stage == "persist":
            return [self._non_llm_step("persist", "retitle_persist", self._persist)]
        return []

    def _fetch(self, ctx):
        puzzle_id = str(self.puzzle_row["id"])
        clues = fetch_retitle_clues(ctx.supabase, puzzle_id)
        if not clues:
            return self._complete(False, detail="no_clues")
        self.words_list = [c["word_normalized"] for c in clues if c.get("word_normalized")]
        self.definitions = [c["definition"] for c in clues if c.get("definition")]
        if not self.words_list or not self.definitions:
            return self._complete(False, detail="missing_words_or_definitions")
        self.forbidden_title_keys = {
            normalize_title_key(row.get("title", "") or "")
            for row in fetch_retitle_puzzles(ctx.supabase)
            if str(row.get("id") or "") != str(self.puzzle_row.get("id") or "")
            and normalize_title_key(row.get("title", "") or "")
        }
        self.title_state = _RetitleBatchState(
            puzzle_row=self.puzzle_row,
            words=self.words_list,
            definitions=self.definitions,
            forbidden_title_keys=self.forbidden_title_keys,
        )
        self._progress("generate_primary", detail=f"round={self.round_idx} clues={len(self.words_list)}")
        return None

    def apply_unit_result(self, unit, result, ctx) -> None:
        if unit.purpose in {"retitle_rate_primary", "retitle_rate_secondary"}:
            model_id = unit.model_id or PRIMARY_MODEL.model_id
            self.pending_rating_votes[model_id] = result.value
            return
        if unit.purpose == "retitle_resolve_old_score":
            model_id = unit.model_id or PRIMARY_MODEL.model_id
            self.old_title_rating_votes[model_id] = result.value

    def _generate_with_model(self, ctx, model):
        assert self.title_state is not None
        candidate = _generate_candidate_with_active_model(
            self.title_state.definitions,
            self.title_state.words,
            ctx.ai_client,
            active_model=model,
            rejected_context=_build_rejected_context(
                self.title_state.rejected_by_model.setdefault(model.model_id, [])
            ),
            empty_retry_instruction="Răspunde obligatoriu cu un singur titlu concret de 2-5 cuvinte, exclusiv în limba română.",
        )
        if candidate:
            self.pending_title = candidate
            self.pending_generator_model = model
            self._progress(
                "rate_primary" if model.model_id == PRIMARY_MODEL.model_id else "rate_secondary",
                detail=f"round={self.round_idx} title={self.pending_title}",
            )
            return self.pending_title
        next_stage = "generate_secondary" if model.model_id == PRIMARY_MODEL.model_id and ctx.multi_model else "round_finalize"
        self._progress(next_stage, detail=f"round={self.round_idx} no_candidate")
        return None

    def _generate_primary(self, ctx):
        return self._generate_with_model(ctx, PRIMARY_MODEL)

    def _generate_secondary(self, ctx):
        return self._generate_with_model(ctx, SECONDARY_MODEL)

    def _rate_units(self, ctx):
        if self.pending_title is None:
            return [self._non_llm_step("rate_finalize", "retitle_rate_finalize", self._rate_finalize)]
        units = []
        for model in self._rating_models(ctx):
            if model.model_id in self.pending_rating_votes:
                continue
            purpose = "retitle_rate_primary" if self.stage == "rate_primary" else "retitle_rate_secondary"
            units.append(
                self._llm_step(
                    f"{self.stage}:{model.model_id}",
                    purpose,
                    model.model_id,
                    lambda _ctx, model=model: rate_title_creativity(
                        self.pending_title or "",
                        self.words_list,
                        _ctx.rate_client,
                        model_config=model,
                    ),
                )
            )
        if units:
            return units
        return [self._non_llm_step("rate_finalize", "retitle_rate_finalize", self._rate_finalize)]

    def _rating_models(self, ctx):
        return [PRIMARY_MODEL, SECONDARY_MODEL] if ctx.multi_model else [self.pending_generator_model]

    def _rate_current(self, ctx):
        assert self.title_state is not None
        assert self.pending_title is not None
        votes = {model_id: vote for model_id, vote in self.pending_rating_votes.items() if vote is not None}
        if ctx.multi_model and len(votes) < 2:
            return None
        if not ctx.multi_model and not votes:
            return None
        ordered_models = [model.model_id for model in self._rating_models(ctx) if model.model_id in votes]
        if ctx.multi_model:
            first_score, first_feedback = votes[ordered_models[0]]
            second_score, second_feedback = votes[ordered_models[1]]
            score = consensus_score(first_score, second_score)
            feedback = _combine_title_feedback(first_feedback, second_feedback)
            complete = True
        else:
            score, feedback = votes[ordered_models[0]]
            complete = True
        result = type("_TitleResult", (), {"title": self.pending_title, "score": score, "feedback": feedback, "score_complete": complete, "used_fallback": False})()
        _update_best_result(self.title_state, result)
        if self.title_state.done:
            title_result = self.title_state.final_result
            if self._needs_old_score_resolution():
                self._progress("resolve_old_score", detail=f"title={title_result.title}")
            else:
                self.prepared_update = prepare_title_update(
                    self.puzzle_row,
                    title_result,
                    ctx.rate_client,
                    multi_model=ctx.multi_model,
                    runtime=None,
                    forbidden_title_keys=self.forbidden_title_keys,
                    words=self.words_list,
                )
                self._progress("persist", detail=f"title={title_result.title}")
            return title_result
        next_stage = (
            "generate_secondary"
            if self.pending_generator_model.model_id == PRIMARY_MODEL.model_id and ctx.multi_model
            else "round_finalize"
        )
        self.pending_title = None
        self.pending_rating_votes = {}
        self._progress(next_stage, detail=f"round={self.round_idx}")
        return None

    def _needs_old_score_resolution(self) -> bool:
        return _stored_title_score(self.puzzle_row) is None and self.puzzle_row.get("title", "") not in FALLBACK_TITLES

    def _rate_finalize(self, ctx):
        return self._rate_current(ctx)

    def _round_finalize(self, ctx):
        assert self.title_state is not None
        if self.title_state.done or self.round_idx >= MAX_TITLE_ROUNDS:
            result = _finalize_title_result(self.title_state)
            if self._needs_old_score_resolution():
                self._progress("resolve_old_score", detail=f"title={result.title}")
            else:
                self.prepared_update = prepare_title_update(
                    self.puzzle_row,
                    result,
                    ctx.rate_client,
                    multi_model=ctx.multi_model,
                    runtime=None,
                    forbidden_title_keys=self.forbidden_title_keys,
                    words=self.words_list,
                )
                self._progress("persist", detail=f"title={result.title}")
            return result
        self.round_idx += 1
        self.pending_title = None
        self._progress("generate_primary", detail=f"round={self.round_idx}")
        return None

    def _resolve_old_score(self, ctx):
        assert self.title_state is not None
        self.prepared_update = prepare_title_update(
            self.puzzle_row,
            _finalize_title_result(self.title_state),
            ctx.rate_client,
            multi_model=ctx.multi_model,
            runtime=None,
            forbidden_title_keys=self.forbidden_title_keys,
            words=self.words_list,
        )
        self._progress("persist", detail=f"title={self.prepared_update.new_title}")
        return self.prepared_update

    def _resolve_old_score_units(self, ctx):
        units = []
        for model in self._rating_models(ctx):
            if model.model_id in self.old_title_rating_votes:
                continue
            old_title = str(self.puzzle_row.get("title") or "")
            units.append(
                self._llm_step(
                    f"resolve_old_score:{model.model_id}",
                    "retitle_resolve_old_score",
                    model.model_id,
                    lambda _ctx, model=model, old_title=old_title: rate_title_creativity(
                        old_title,
                        self.words_list,
                        _ctx.rate_client,
                        model_config=model,
                    ),
                )
            )
        if units:
            return units
        return [self._non_llm_step("resolve_old_score_finalize", "retitle_resolve_old_score_finalize", self._resolve_old_score_finalize)]

    def _resolve_old_score_finalize(self, ctx):
        if ctx.multi_model and len(self.old_title_rating_votes) >= 2:
            ordered = [model.model_id for model in self._rating_models(ctx)]
            first_score, first_feedback = self.old_title_rating_votes[ordered[0]]
            second_score, second_feedback = self.old_title_rating_votes[ordered[1]]
            self.puzzle_row["title_score"] = consensus_score(first_score, second_score)
            _ = _combine_title_feedback(first_feedback, second_feedback)
        elif self.old_title_rating_votes:
            self.puzzle_row["title_score"] = next(iter(self.old_title_rating_votes.values()))[0]
        return self._resolve_old_score(ctx)

    def _persist(self, ctx):
        changed = apply_title_update(
            ctx.supabase,
            self.puzzle_row,
            self.prepared_update,
            dry_run=ctx.dry_run,
        )
        return self._complete(changed, detail=f"changed={changed}")
