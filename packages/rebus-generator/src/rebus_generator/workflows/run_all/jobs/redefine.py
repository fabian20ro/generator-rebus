from __future__ import annotations

import copy

from rebus_generator.platform.io.dex_cache import DexProvider
from rebus_generator.platform.llm.models import PRIMARY_MODEL, SECONDARY_MODEL
from rebus_generator.domain.puzzle_metrics import score_puzzle_state
from rebus_generator.platform.io.runtime_logging import log
from rebus_generator.workflows.redefine.rewrite_engine import (
    finish_rewrite_session,
)
from rebus_generator.workflows.generate.verify import (
    _run_pair_rate,
    _run_pair_verify,
    _finalize_pair_rating,
    _finalize_pair_verification,
    rate_clue_with_model,
    verify_clue_with_model,
)
from rebus_generator.workflows.redefine.load import build_working_puzzle, fetch_clues as fetch_redefine_clues
from rebus_generator.workflows.redefine.persist import (
    apply_redefined_puzzle_persistence,
    plan_redefined_puzzle_persistence,
)
from rebus_generator.workflows.redefine.rewrite_rounds import (
    rewrite_session_finalize_round,
    rewrite_session_prepare_round,
    rewrite_session_score_round,
)
from rebus_generator.workflows.run_all.rewrite_units import RunAllRewriteSession
from .base import JobState


def _clue_row_sort_key(row: dict) -> tuple[object, ...]:
    direction = "V" if str(row.get("direction") or "").strip().lower() in {"v", "vertical"} else "H"
    return (
        0 if direction == "H" else 1,
        int(row.get("clue_number") or 0),
        int(row.get("start_row") or 0),
        int(row.get("start_col") or 0),
        row.get("id") or "",
    )


class RedefineJobState(JobState):
    def __init__(self, item) -> None:
        super().__init__(item)
        self.stage = "fetch"
        self.puzzle_row = copy.deepcopy(dict(item.payload["puzzle_row"]))
        self.clue_rows: list[dict] = []
        self.baseline_puzzle = None
        self.candidate_puzzle = None
        self.baseline_verify_done: dict[str, set[str]] = {}
        self.baseline_rate_done: dict[str, set[str]] = {}
        self.rewrite_session: RunAllRewriteSession | None = None
        self.persistence_plan = None

    def next_steps(self, ctx):
        if self.status != "active":
            return []
        if self.stage == "fetch":
            return [self._non_llm_step("fetch", "redefine_fetch", self._fetch)]
        if self.stage == "baseline_verify":
            return [self._non_llm_step("baseline_verify", "redefine_baseline_verify_compat", self._baseline_verify)]
        if self.stage == "baseline_rate":
            return [self._non_llm_step("baseline_rate", "redefine_baseline_rate_compat", self._baseline_rate)]
        if self.stage == "baseline_finalize":
            return [self._non_llm_step("baseline_finalize", "redefine_baseline_finalize", self._baseline_finalize)]
        return self.plan_ready_units(ctx)

    def plan_ready_units(self, ctx):
        if self.status != "active":
            return []
        if self.stage == "fetch":
            return [self._non_llm_step("fetch", "redefine_fetch", self._fetch)]
        if self.stage == "baseline_verify":
            for model_id in self._model_order(ctx):
                pending = [clue for clue in self._baseline_clues() if clue.word_normalized not in self.baseline_verify_done.get(model_id, set())]
                if pending:
                    return [
                        self._llm_step(
                            f"baseline_verify:{model_id}:{clue.word_normalized}",
                            "redefine_baseline_verify",
                            model_id,
                            lambda _ctx, clue=clue, model_id=model_id: verify_clue_with_model(
                                clue,
                                _ctx.ai_client,
                                model_id=model_id,
                                max_guesses=_ctx.verify_candidates,
                            ),
                        )
                        for clue in pending
                    ]
            return [self._non_llm_step("baseline_verify_finalize", "redefine_baseline_verify_finalize", self._baseline_verify_finalize)]
        if self.stage == "baseline_rate":
            for model_id in self._model_order(ctx):
                pending = [clue for clue in self._baseline_clues() if clue.word_normalized not in self.baseline_rate_done.get(model_id, set())]
                if pending:
                    return [
                        self._llm_step(
                            f"baseline_rate:{model_id}:{clue.word_normalized}",
                            "redefine_baseline_rate",
                            model_id,
                            lambda _ctx, clue=clue, model_id=model_id: rate_clue_with_model(
                                clue,
                                _ctx.ai_client,
                                dex=DexProvider.for_puzzle(self.baseline_puzzle),
                                model_id=model_id,
                            ),
                        )
                        for clue in pending
                    ]
            return [self._non_llm_step("baseline_rate_finalize", "redefine_baseline_rate_finalize", self._baseline_rate_finalize)]
        if self.stage == "baseline_finalize":
            return [self._non_llm_step("baseline_finalize", "redefine_baseline_finalize", self._baseline_finalize)]
        if self.stage in {"rewrite_initial_verify", "rewrite_initial_rate", "rewrite_prepare_round", "generate_candidates", "evaluate_verify", "evaluate_rate", "select_candidates", "finalize_round"}:
            return self._rewrite_units()
        if self.stage == "persist_prepare":
            return [self._llm_step("persist_prepare", "redefine_persist_prepare", PRIMARY_MODEL.model_id, self._persist_prepare)]
        if self.stage == "persist_apply":
            return [self._non_llm_step("persist_apply", "redefine_persist_apply", self._persist_apply)]
        return []

    def _fetch(self, ctx):
        puzzle_id = str(self.puzzle_row["id"])
        self.clue_rows = sorted(fetch_redefine_clues(ctx.supabase, puzzle_id), key=_clue_row_sort_key)
        if not self.clue_rows:
            log(f"  [{puzzle_id}] No clues found, skipping")
            return self._complete(0, detail="no_clues")
        self.baseline_puzzle = build_working_puzzle(self.puzzle_row, self.clue_rows)
        self.candidate_puzzle = build_working_puzzle(self.puzzle_row, self.clue_rows)
        theme = getattr(self.candidate_puzzle, "title", None) or self.puzzle_row.get("title") or "Puzzle rebus"
        self.rewrite_session = RunAllRewriteSession(
            puzzle=self.candidate_puzzle,
            client=ctx.ai_client,
            theme=theme,
            rounds=ctx.redefine_rounds,
            multi_model=ctx.multi_model,
            dex=DexProvider.for_puzzle(self.candidate_puzzle),
            verify_candidates=ctx.verify_candidates,
            hybrid_deanchor=True,
            runtime=ctx.runtime,
        )
        self.baseline_verify_done = {model_id: set() for model_id in self._model_order(ctx)}
        self.baseline_rate_done = {model_id: set() for model_id in self._model_order(ctx)}
        log(f"  [{puzzle_id}] {len(self.clue_rows)} clues, title: {self.baseline_puzzle.title}")
        self._progress("baseline_verify", detail=f"clues={len(self.clue_rows)}")
        return None

    def apply_unit_result(self, unit, result, ctx) -> None:
        parts = unit.step_id.split(":")
        if unit.purpose == "redefine_baseline_verify":
            self.baseline_verify_done.setdefault(parts[1], set()).add(parts[2])
            return
        if unit.purpose == "redefine_baseline_rate":
            self.baseline_rate_done.setdefault(parts[1], set()).add(parts[2])
            return
        if self.rewrite_session is not None:
            self._apply_rewrite_result(unit, result)

    def _baseline_clues(self):
        return self.baseline_puzzle.horizontal_clues + self.baseline_puzzle.vertical_clues

    def _model_order(self, ctx):
        return [PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id] if ctx.multi_model else [PRIMARY_MODEL.model_id]

    def _baseline_verify_finalize(self, ctx):
        label = "gemma + eurollm" if ctx.multi_model else PRIMARY_MODEL.display_name
        clues = _finalize_pair_verification(
            self._baseline_clues(),
            model_order=self._model_order(ctx),
            model_label=label,
        )
        split = len(self.baseline_puzzle.horizontal_clues)
        self.baseline_puzzle.horizontal_clues = clues[:split]
        self.baseline_puzzle.vertical_clues = clues[split:]
        self._progress("baseline_finalize", detail=f"clues={len(self.clue_rows)}")
        return None

    def _baseline_rate_finalize(self, ctx):
        label = "gemma + eurollm" if ctx.multi_model else PRIMARY_MODEL.display_name
        _finalize_pair_rating(
            self._baseline_clues(),
            model_order=self._model_order(ctx),
            model_label=label,
        )
        self._progress("baseline_finalize", detail=f"clues={len(self.clue_rows)}")
        return None

    def _baseline_finalize(self, ctx):
        assert self.baseline_puzzle is not None
        self.baseline_puzzle.assessment = score_puzzle_state(self.baseline_puzzle)
        puzzle_id = str(self.puzzle_row["id"])
        log(
            f"  [{puzzle_id}] baseline min={self.baseline_puzzle.assessment.min_rebus}/10 "
            f"avg={self.baseline_puzzle.assessment.avg_rebus:.1f}/10 "
            f"verified={self.baseline_puzzle.assessment.verified_count}/{self.baseline_puzzle.assessment.total_clues}"
        )
        self._progress("rewrite_initial_verify", detail="baseline_done")
        return self.baseline_puzzle.assessment

    def _baseline_verify(self, ctx):
        model_order, label = _run_pair_verify(
            self.baseline_puzzle,
            ctx.ai_client,
            runtime=ctx.runtime,
            skip_words=None,
            max_guesses=ctx.verify_candidates,
        )
        clues = _finalize_pair_verification(
            self._baseline_clues(),
            model_order=model_order,
            model_label=label,
        )
        split = len(self.baseline_puzzle.horizontal_clues)
        self.baseline_puzzle.horizontal_clues = clues[:split]
        self.baseline_puzzle.vertical_clues = clues[split:]
        self._progress("baseline_rate", detail=f"clues={len(self.clue_rows)}")
        return clues

    def _baseline_rate(self, ctx):
        model_order, label = _run_pair_rate(
            self.baseline_puzzle,
            ctx.ai_client,
            runtime=ctx.runtime,
            skip_words=None,
            dex=DexProvider.for_puzzle(self.baseline_puzzle),
        )
        _finalize_pair_rating(
            self._baseline_clues(),
            model_order=model_order,
            model_label=label,
        )
        self._progress("baseline_finalize", detail=f"clues={len(self.clue_rows)}")
        return None

    def _rewrite_units(self):
        assert self.rewrite_session is not None
        if self.stage == "rewrite_initial_verify":
            units = self.rewrite_session.initial_verify_units(
                lambda step_id, purpose, model_id, phase, runner, coalesce_key=None: self._llm_step(
                    step_id,
                    purpose,
                    model_id,
                    runner,
                    phase=phase,
                    coalesce_key=coalesce_key,
                )
            )
            return units or [self._non_llm_step("rewrite_initial_verify_finalize", "rewrite_initial_verify_finalize", self._rewrite_finalize_initial_verify)]
        if self.stage == "rewrite_initial_rate":
            units = self.rewrite_session.initial_rate_units(
                lambda step_id, purpose, model_id, phase, runner, coalesce_key=None: self._llm_step(
                    step_id,
                    purpose,
                    model_id,
                    runner,
                    phase=phase,
                    coalesce_key=coalesce_key,
                )
            )
            return units or [self._non_llm_step("rewrite_initial_rate_finalize", "rewrite_initial_rate_finalize", self._rewrite_finalize_initial_rate)]
        if self.stage == "rewrite_prepare_round":
            return [self._non_llm_step("rewrite_prepare_round", "rewrite_prepare_round", self._rewrite_prepare_round)]
        if self.stage == "generate_candidates":
            units = self.rewrite_session.generation_units(
                lambda step_id, purpose, model_id, phase, runner, coalesce_key=None: self._llm_step(
                    step_id,
                    purpose,
                    model_id,
                    runner,
                    phase=phase,
                    coalesce_key=coalesce_key,
                )
            )
            return units or [self._non_llm_step("rewrite_generation_finalize", "rewrite_generation_finalize", self._rewrite_finalize_generation)]
        if self.stage == "evaluate_verify":
            units = self.rewrite_session.evaluation_verify_units(
                lambda step_id, purpose, model_id, phase, runner, coalesce_key=None: self._llm_step(
                    step_id,
                    purpose,
                    model_id,
                    runner,
                    phase=phase,
                    coalesce_key=coalesce_key,
                )
            )
            return units or [self._non_llm_step("rewrite_eval_verify_finalize", "rewrite_eval_verify_finalize", self._rewrite_start_rate)]
        if self.stage == "evaluate_rate":
            units = self.rewrite_session.evaluation_rate_units(
                lambda step_id, purpose, model_id, phase, runner, coalesce_key=None: self._llm_step(
                    step_id,
                    purpose,
                    model_id,
                    runner,
                    phase=phase,
                    coalesce_key=coalesce_key,
                )
            )
            return units or [self._non_llm_step("rewrite_select_candidates", "rewrite_select_candidates", self._rewrite_select_candidates)]
        if self.stage == "select_candidates":
            return [self._non_llm_step("rewrite_select_candidates", "rewrite_select_candidates", self._rewrite_select_candidates)]
        if self.stage == "finalize_round":
            return [self._non_llm_step("rewrite_finalize_round", "rewrite_finalize_round", self._rewrite_finalize_round)]
        return []

    def _apply_rewrite_result(self, unit, result) -> None:
        assert self.rewrite_session is not None
        parts = unit.step_id.split(":")
        if unit.purpose == "rewrite_initial_verify":
            self.rewrite_session.note_initial_verify_done(parts[1], parts[2])
        elif unit.purpose == "rewrite_initial_rate":
            self.rewrite_session.note_initial_rate_done(parts[1], parts[2])
        elif unit.purpose == "rewrite_generate_candidate":
            self.rewrite_session.apply_generation_result(result.value or {})
        elif unit.purpose == "rewrite_evaluate_candidate_verify":
            self.rewrite_session.apply_candidate_verify_result(result.value or {})
        elif unit.purpose == "rewrite_evaluate_candidate_rate":
            self.rewrite_session.apply_candidate_rate_result(result.value or {})

    def _rewrite_finalize_initial_verify(self, ctx):
        self.rewrite_session.finalize_initial_verify()
        self._progress("rewrite_initial_rate", detail=f"round={self.rewrite_session.round_index}")
        return None

    def _rewrite_finalize_initial_rate(self, ctx):
        self.rewrite_session.finalize_initial_rate()
        self._progress("rewrite_prepare_round", detail=f"round={self.rewrite_session.round_index}")
        return None

    def _rewrite_prepare_round(self, ctx):
        if not hasattr(self.rewrite_session, "prepare_round"):
            round_state = rewrite_session_prepare_round(self.rewrite_session)
            if round_state is None:
                finish_rewrite_session(self.rewrite_session)
                self.candidate_puzzle.assessment = score_puzzle_state(self.candidate_puzzle)
                assessment = self.candidate_puzzle.assessment
                puzzle_id = str(self.puzzle_row["id"])
                log(
                    f"  [{puzzle_id}] candidate min={assessment.min_rebus}/10 "
                    f"avg={assessment.avg_rebus:.1f}/10 "
                    f"verified={assessment.verified_count}/{assessment.total_clues}"
                )
                self._progress("persist_prepare", detail=f"rewrite_min={assessment.min_rebus}")
                return self.rewrite_session.final_result
            self._progress("rewrite_score_round", detail=f"round={round_state.round_index}")
            return round_state
        self.rewrite_session.prepare_round()
        if self.rewrite_session.phase == "done":
            result = self.rewrite_session.finish()
            self.candidate_puzzle.assessment = score_puzzle_state(self.candidate_puzzle)
            assessment = self.candidate_puzzle.assessment
            puzzle_id = str(self.puzzle_row["id"])
            log(
                f"  [{puzzle_id}] candidate min={assessment.min_rebus}/10 "
                f"avg={assessment.avg_rebus:.1f}/10 "
                f"verified={assessment.verified_count}/{assessment.total_clues}"
            )
            self._progress("persist_prepare", detail=f"rewrite_min={assessment.min_rebus}")
            return result
        self._progress(self.rewrite_session.phase, detail=f"round={self.rewrite_session.round_index}")
        return None

    def _rewrite_score_round(self, ctx):
        rewrite_session_score_round(self.rewrite_session)
        self._progress("rewrite_finalize_round", detail=f"round={self.rewrite_session.round_index}")
        return None

    def _rewrite_finalize_generation(self, ctx):
        self.rewrite_session.finalize_generation()
        self._progress(self.rewrite_session.phase, detail=f"round={self.rewrite_session.round_index}")
        return None

    def _rewrite_start_rate(self, ctx):
        self._progress("evaluate_rate", detail=f"round={self.rewrite_session.round_index}")
        return None

    def _rewrite_select_candidates(self, ctx):
        self.rewrite_session.select_candidates()
        self._progress("finalize_round", detail=f"round={self.rewrite_session.round_index}")
        return None

    def _rewrite_finalize_round(self, ctx):
        if hasattr(self.rewrite_session, "finalize_round"):
            self.rewrite_session.finalize_round()
        else:
            rewrite_session_finalize_round(self.rewrite_session)
        self._progress("rewrite_prepare_round", detail=f"round={self.rewrite_session.round_index}")
        return None

    def _persist_prepare(self, ctx):
        finish_rewrite_session(self.rewrite_session)
        self.persistence_plan = plan_redefined_puzzle_persistence(
            ctx.supabase,
            self.puzzle_row,
            self.clue_rows,
            self.baseline_puzzle,
            self.candidate_puzzle,
            ctx.ai_client,
            dry_run=ctx.dry_run,
            multi_model=ctx.multi_model,
            runtime=ctx.runtime,
        )
        self._progress("persist_apply", detail=f"updates={len(self.persistence_plan.clue_updates)}")
        return self.persistence_plan

    def _persist_apply(self, ctx):
        updated = apply_redefined_puzzle_persistence(
            ctx.supabase,
            self.puzzle_row,
            self.clue_rows,
            self.persistence_plan,
            dry_run=ctx.dry_run,
        )
        return self._complete(updated, detail=f"updated={updated}")
