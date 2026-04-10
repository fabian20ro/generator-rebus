from __future__ import annotations

import copy

from rebus_generator.platform.llm.models import PRIMARY_MODEL, SECONDARY_MODEL
from rebus_generator.workflows.retitle.batch import (
    _RetitleBatchState,
    _finalize_title_result,
    _generate_batch_candidates,
    _rate_batch_candidates,
)
from rebus_generator.workflows.retitle.load import (
    fetch_clues as fetch_retitle_clues,
    fetch_puzzles as fetch_retitle_puzzles,
    stored_title_score as _stored_title_score,
)
from rebus_generator.workflows.retitle.persist import apply_title_update, prepare_title_update
from rebus_generator.workflows.retitle.titleing import (
    FALLBACK_TITLES,
    MAX_TITLE_ROUNDS,
    normalize_title_key,
)
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
        self.round_idx = 1
        self.prepared_update = None

    def next_steps(self, ctx):
        if self.status != "active":
            return []
        if self.stage == "fetch":
            return [self._non_llm_step("fetch", "retitle_fetch", self._fetch)]
        if self.stage == "generate_primary":
            return [self._llm_step("generate_primary", "retitle_generate_primary", PRIMARY_MODEL.model_id, self._generate_primary)]
        if self.stage == "rate_primary":
            return [self._llm_step("rate_primary", "retitle_rate_primary", PRIMARY_MODEL.model_id, self._rate_primary)]
        if self.stage == "generate_secondary":
            return [self._llm_step("generate_secondary", "retitle_generate_secondary", SECONDARY_MODEL.model_id, self._generate_secondary)]
        if self.stage == "rate_secondary":
            return [self._llm_step("rate_secondary", "retitle_rate_secondary", SECONDARY_MODEL.model_id, self._rate_secondary)]
        if self.stage == "round_finalize":
            return [self._non_llm_step("round_finalize", "retitle_round_finalize", self._round_finalize)]
        if self.stage == "resolve_old_score":
            return [self._llm_step("resolve_old_score", "retitle_resolve_old_score", PRIMARY_MODEL.model_id, self._resolve_old_score)]
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

    def _generate_with_model(self, ctx, model):
        assert self.title_state is not None
        candidates = _generate_batch_candidates(
            [self.title_state],
            ctx.ai_client,
            runtime=ctx.runtime,
            active_model=model,
            round_idx=self.round_idx,
        )
        if candidates:
            self.pending_title = candidates[0][1]
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

    def _rate_current(self, ctx):
        assert self.title_state is not None
        assert self.pending_title is not None
        _rate_batch_candidates(
            [(self.title_state, self.pending_title)],
            ctx.rate_client,
            generator_model=self.pending_generator_model,
            runtime=ctx.runtime,
            round_idx=self.round_idx,
        )
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
        self._progress(next_stage, detail=f"round={self.round_idx}")
        return None

    def _needs_old_score_resolution(self) -> bool:
        return _stored_title_score(self.puzzle_row) is None and self.puzzle_row.get("title", "") not in FALLBACK_TITLES

    def _rate_primary(self, ctx):
        return self._rate_current(ctx)

    def _rate_secondary(self, ctx):
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
            runtime=ctx.runtime,
            forbidden_title_keys=self.forbidden_title_keys,
            words=self.words_list,
        )
        self._progress("persist", detail=f"title={self.prepared_update.new_title}")
        return self.prepared_update

    def _persist(self, ctx):
        changed = apply_title_update(
            ctx.supabase,
            self.puzzle_row,
            self.prepared_update,
            dry_run=ctx.dry_run,
        )
        return self._complete(changed, detail=f"changed={changed}")
