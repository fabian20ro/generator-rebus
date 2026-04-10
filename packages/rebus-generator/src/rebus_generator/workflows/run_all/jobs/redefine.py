from __future__ import annotations

import copy

from rebus_generator.platform.io.dex_cache import DexProvider
from rebus_generator.platform.llm.models import PRIMARY_MODEL, SECONDARY_MODEL
from rebus_generator.domain.puzzle_metrics import score_puzzle_state
from rebus_generator.platform.io.runtime_logging import log
from rebus_generator.workflows.redefine.rewrite_engine import (
    finish_rewrite_session,
    rewrite_session_finalize_round,
    rewrite_session_initial_rate,
    rewrite_session_initial_verify,
    rewrite_session_prepare_round,
    rewrite_session_score_round,
    start_rewrite_session,
)
from rebus_generator.workflows.generate.verify import (
    _finalize_pair_rating,
    _finalize_pair_verification,
    _run_pair_rate,
    _run_pair_verify,
)
from rebus_generator.workflows.redefine.load import build_working_puzzle, fetch_clues as fetch_redefine_clues
from rebus_generator.workflows.redefine.persist import (
    apply_redefined_puzzle_persistence,
    plan_redefined_puzzle_persistence,
)
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
        self.baseline_model_ids: list[str] = []
        self.baseline_model_label = ""
        self.rewrite_session = None
        self.rewrite_round = None
        self.persistence_plan = None

    def next_steps(self, ctx):
        if self.status != "active":
            return []
        if self.stage == "fetch":
            return [self._non_llm_step("fetch", "redefine_fetch", self._fetch)]
        if self.stage == "baseline_verify":
            return [self._llm_step("baseline_verify", "redefine_baseline_verify", PRIMARY_MODEL.model_id, self._baseline_verify)]
        if self.stage == "baseline_rate":
            return [self._llm_step("baseline_rate", "redefine_baseline_rate", PRIMARY_MODEL.model_id, self._baseline_rate)]
        if self.stage == "baseline_finalize":
            return [self._non_llm_step("baseline_finalize", "redefine_baseline_finalize", self._baseline_finalize)]
        if self.stage == "rewrite_initial_verify":
            return [self._llm_step("rewrite_initial_verify", "redefine_rewrite_initial_verify", PRIMARY_MODEL.model_id, self._rewrite_initial_verify)]
        if self.stage == "rewrite_initial_rate":
            return [self._llm_step("rewrite_initial_rate", "redefine_rewrite_initial_rate", PRIMARY_MODEL.model_id, self._rewrite_initial_rate)]
        if self.stage == "rewrite_prepare_round":
            return [self._llm_step("rewrite_prepare_round", "redefine_rewrite_prepare_round", PRIMARY_MODEL.model_id, self._rewrite_prepare_round)]
        if self.stage == "rewrite_score_round":
            return [self._llm_step("rewrite_score_round", "redefine_rewrite_score_round", PRIMARY_MODEL.model_id, self._rewrite_score_round)]
        if self.stage == "rewrite_finalize_round":
            return [self._non_llm_step("rewrite_finalize_round", "redefine_rewrite_finalize_round", self._rewrite_finalize_round)]
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
        self.rewrite_session = start_rewrite_session(
            self.candidate_puzzle,
            ctx.ai_client,
            rounds=ctx.redefine_rounds,
            theme=theme,
            multi_model=ctx.multi_model,
            verify_candidates=ctx.verify_candidates,
            hybrid_deanchor=True,
            runtime=ctx.runtime,
        )
        log(f"  [{puzzle_id}] {len(self.clue_rows)} clues, title: {self.baseline_puzzle.title}")
        self._progress("baseline_verify", detail=f"clues={len(self.clue_rows)}")
        return None

    def _baseline_verify(self, ctx):
        assert self.baseline_puzzle is not None
        self.baseline_model_ids, self.baseline_model_label = _run_pair_verify(
            self.baseline_puzzle,
            ctx.ai_client,
            runtime=ctx.runtime,
            skip_words=None,
            max_guesses=ctx.verify_candidates,
        )
        self._progress("baseline_rate", detail=f"clues={len(self.clue_rows)}")
        return None

    def _baseline_rate(self, ctx):
        assert self.baseline_puzzle is not None
        self.baseline_model_ids, self.baseline_model_label = _run_pair_rate(
            self.baseline_puzzle,
            ctx.ai_client,
            runtime=ctx.runtime,
            skip_words=None,
            dex=DexProvider.for_puzzle(self.baseline_puzzle),
        )
        self._progress("baseline_finalize", detail=f"clues={len(self.clue_rows)}")
        return None

    def _baseline_finalize(self, ctx):
        assert self.baseline_puzzle is not None
        clues = _finalize_pair_verification(
            self.baseline_puzzle.horizontal_clues + self.baseline_puzzle.vertical_clues,
            model_order=self.baseline_model_ids,
            model_label=self.baseline_model_label,
        )
        split = len(self.baseline_puzzle.horizontal_clues)
        self.baseline_puzzle.horizontal_clues = clues[:split]
        self.baseline_puzzle.vertical_clues = clues[split:]
        _finalize_pair_rating(
            self.baseline_puzzle.horizontal_clues + self.baseline_puzzle.vertical_clues,
            model_order=self.baseline_model_ids,
            model_label=self.baseline_model_label,
        )
        self.baseline_puzzle.assessment = score_puzzle_state(self.baseline_puzzle)
        puzzle_id = str(self.puzzle_row["id"])
        log(
            f"  [{puzzle_id}] baseline min={self.baseline_puzzle.assessment.min_rebus}/10 "
            f"avg={self.baseline_puzzle.assessment.avg_rebus:.1f}/10 "
            f"verified={self.baseline_puzzle.assessment.verified_count}/{self.baseline_puzzle.assessment.total_clues}"
        )
        self._progress("rewrite_initial_verify", detail="baseline_done")
        return self.baseline_puzzle.assessment

    def _rewrite_initial_verify(self, ctx):
        rewrite_session_initial_verify(self.rewrite_session)
        self._progress("rewrite_initial_rate", detail=f"round={self.rewrite_session.round_index}")
        return None

    def _rewrite_initial_rate(self, ctx):
        rewrite_session_initial_rate(self.rewrite_session)
        self._progress("rewrite_prepare_round", detail=f"round={self.rewrite_session.round_index}")
        return None

    def _rewrite_prepare_round(self, ctx):
        self.rewrite_round = rewrite_session_prepare_round(self.rewrite_session)
        if self.rewrite_session.final_result is not None or self.rewrite_round is None:
            self.candidate_puzzle.assessment = score_puzzle_state(self.candidate_puzzle)
            self._progress("persist_prepare", detail="rewrite_done")
            return self.rewrite_session.final_result
        if self.rewrite_round.changed_words:
            self._progress("rewrite_score_round", detail=f"round={self.rewrite_round.round_index}")
        else:
            self._progress("rewrite_finalize_round", detail=f"round={self.rewrite_round.round_index} no_change")
        return self.rewrite_round

    def _rewrite_score_round(self, ctx):
        rewrite_session_score_round(self.rewrite_session)
        self._progress("rewrite_finalize_round", detail=f"round={self.rewrite_round.round_index}")
        return None

    def _rewrite_finalize_round(self, ctx):
        rewrite_session_finalize_round(self.rewrite_session)
        if self.rewrite_session.final_result is not None:
            self.candidate_puzzle.assessment = score_puzzle_state(self.candidate_puzzle)
            assessment = self.candidate_puzzle.assessment
            puzzle_id = str(self.puzzle_row["id"])
            log(
                f"  [{puzzle_id}] candidate min={assessment.min_rebus}/10 "
                f"avg={assessment.avg_rebus:.1f}/10 "
                f"verified={assessment.verified_count}/{assessment.total_clues}"
            )
            self._progress("persist_prepare", detail=f"rewrite_min={assessment.min_rebus}")
            return assessment
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
