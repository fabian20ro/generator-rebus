"""Unified long-running supervisor with one active job slot per topic.

Current boundary:
- one supervisor process
- one active job slot per topic
- local puzzle claims by `puzzle_id`
- local simplify exclusion by `word_normalized`
- step-level scheduling across active topic jobs

This remains a single-process supervisor/orchestrator with typed jobs and local
resource claims. Not a durable event bus: no replay, no pub/sub subscriber
graph, no multi-consumer idempotency, no cross-process leases.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import concurrent.futures
import fcntl
import json
import os
import random
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import batch_publish as batch_publish_pipeline
from .batch_publish import MAX_REWRITE_ROUNDS as GENERATE_REWRITE_ROUNDS, PreparedPuzzle, publish_prepared_puzzle
from .clue_canon import DEFAULT_SIMPLIFY_BATCH_SIZE
from .config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL, VERIFY_CANDIDATE_COUNT
from .core.clue_canon_simplify import (
    _append_jsonl,
    SimplifyStats,
    apply_simplify_merge,
    build_candidate_pairs,
    choose_existing_survivor,
    compare_simplify_pairs,
    find_simplify_pair_rows,
    load_simplify_bucket,
    refresh_simplify_bucket_rows,
    should_rewrite_survivor,
    update_top_reductions,
)
from .core.ai_clues import rewrite_merged_canonical_definition, validate_merged_canonical_definition
from .core.clue_canon_store import ClueCanonStore
from .core.dex_cache import DexProvider
from .core.llm_client import create_client as create_ai_client
from .core.llm_dispatch import initial_generation_model
from .core.lm_runtime import LmRuntime
from .core.metrics import BatchMetric, write_metrics, update_word_difficulty
from .core.model_manager import PRIMARY_MODEL, SECONDARY_MODEL
from .core.pipeline_state import puzzle_from_working_state, working_puzzle_from_puzzle
from .core.puzzle_metrics import score_puzzle_state
from .core.runtime_logging import (
    add_llm_debug_argument,
    install_process_logging,
    log,
    path_timestamp,
    set_llm_debug_enabled,
    utc_timestamp,
)
from .core.supabase_ops import create_service_role_client
from .loop_controller import select_auto_size
from .phases.define import generate_definitions_for_puzzle
from .phases.verify import (
    _finalize_pair_rating,
    _finalize_pair_verification,
    _run_pair_rate,
    _run_pair_verify,
)
from .phases.theme import MAX_TITLE_ROUNDS
from .redefine import (
    REDEFINE_ROUNDS,
    build_working_puzzle,
    fetch_clues as fetch_redefine_clues,
    fetch_puzzles as fetch_redefine_puzzles,
    persist_redefined_puzzle,
    rewrite_puzzle_definitions,
)
from .retitle import (
    RETITLE_BATCH_SIZE,
    _RetitleBatchState,
    _apply_title_result,
    _finalize_title_result,
    _generate_batch_candidates,
    _rate_batch_candidates,
    fetch_clues as fetch_retitle_clues,
    fetch_puzzles as fetch_retitle_puzzles,
    normalize_title_key,
    select_puzzles_for_retitle,
)
from .rust_bridge import _rust_binary_path

SUPPORTED_TOPICS = ("generate", "redefine", "retitle", "simplify")
DEFAULT_IDLE_SLEEP_SECONDS = 15
DEFAULT_HEARTBEAT_SECONDS = 30
DEFAULT_RETRY_LIMIT = 2
WORKER_POLL_SLEEP_SECONDS = 1
LOCK_PATH = Path("/tmp/generator_rebus_run_all.lock")


@dataclass
class RunAllContext:
    supabase: object
    ai_client: object
    rate_client: object
    runtime: LmRuntime
    store: ClueCanonStore
    run_dir: Path
    batch_output_root: Path
    words_path: Path
    multi_model: bool
    dry_run: bool
    generate_rewrite_rounds: int
    redefine_rounds: int
    verify_candidates: int
    simplify_batch_size: int


@dataclass
class SupervisorWorkItem:
    item_id: str
    topic: str
    task_kind: str
    preferred_model_id: str
    target_models: tuple[str, ...]
    payload: dict[str, Any] = field(default_factory=dict)
    puzzle_id: str | None = None
    words: set[str] = field(default_factory=set)
    attempts: int = 0
    available_after: float = 0.0
    admitted_at: float = field(default_factory=time.monotonic)


@dataclass
class ClaimState:
    topic_by_puzzle_id: dict[str, str] = field(default_factory=dict)
    simplify_words: set[str] = field(default_factory=set)
    puzzle_words: dict[str, set[str]] = field(default_factory=dict)

    def has_puzzle(self, puzzle_id: str | None) -> bool:
        return bool(puzzle_id) and puzzle_id in self.topic_by_puzzle_id

    def puzzle_word_conflict(self, words: set[str]) -> bool:
        return bool(words & self.simplify_words)

    def simplify_word_conflict(self, words: set[str]) -> bool:
        for active_words in self.puzzle_words.values():
            if words & active_words:
                return True
        return bool(words & self.simplify_words)

    def claim(self, item: SupervisorWorkItem | "JobState") -> None:
        if item.puzzle_id:
            self.topic_by_puzzle_id[item.puzzle_id] = item.topic
            self.puzzle_words[item.puzzle_id] = set(item.words)
        if item.topic == "simplify":
            self.simplify_words.update(item.words)

    def release(self, item: SupervisorWorkItem | "JobState") -> None:
        if item.puzzle_id:
            self.topic_by_puzzle_id.pop(item.puzzle_id, None)
            self.puzzle_words.pop(item.puzzle_id, None)
        if item.topic == "simplify":
            for word in set(item.words):
                self.simplify_words.discard(word)


@dataclass
class StepState:
    step_id: str
    job_id: str
    topic: str
    kind: str
    purpose: str
    model_id: str | None
    runner: Callable[[RunAllContext], object] = field(repr=False)
    execution_mode: str = "inline_non_llm"


@dataclass
class WorkerTask:
    step: StepState
    future: concurrent.futures.Future[object]
    started_at: float


class JobState:
    def __init__(self, item: SupervisorWorkItem) -> None:
        self.item = item
        self.topic = item.topic
        self.task_kind = item.task_kind
        self.item_id = item.item_id
        self.puzzle_id = item.puzzle_id
        self.words = set(item.words)
        self.preferred_model_id = item.preferred_model_id
        self.target_models = item.target_models
        self.stage = "init"
        self.status = "active"
        self.result: object = None
        self.available_after = 0.0
        self.started_at = time.monotonic()
        self.updated_at = self.started_at
        self.last_error = ""
        self.progress_detail = ""
        self.running_step_id: str | None = None

    def next_steps(self, ctx: RunAllContext) -> list[StepState]:
        raise NotImplementedError

    def _non_llm_step(
        self,
        step_id: str,
        purpose: str,
        runner: Callable[[RunAllContext], object],
        *,
        execution_mode: str = "inline_non_llm",
    ) -> StepState:
        return StepState(
            step_id=step_id,
            job_id=self.item_id,
            topic=self.topic,
            kind="non_llm",
            purpose=purpose,
            model_id=None,
            runner=runner,
            execution_mode=execution_mode,
        )

    def _background_step(self, step_id: str, purpose: str, runner: Callable[[RunAllContext], object]) -> StepState:
        return self._non_llm_step(
            step_id,
            purpose,
            runner,
            execution_mode="background_non_llm",
        )

    def _llm_step(
        self,
        step_id: str,
        purpose: str,
        model_id: str,
        runner: Callable[[RunAllContext], object],
    ) -> StepState:
        kind = "gemma" if model_id == PRIMARY_MODEL.model_id else "eurollm"
        return StepState(
            step_id=step_id,
            job_id=self.item_id,
            topic=self.topic,
            kind=kind,
            purpose=purpose,
            model_id=model_id,
            runner=runner,
            execution_mode="llm",
        )

    def _complete(self, result: object = None, *, stage: str = "done", detail: str = "") -> object:
        self.result = result
        self.stage = stage
        self.status = "complete"
        self.updated_at = time.monotonic()
        if detail:
            self.progress_detail = detail
        return result

    def _progress(self, stage: str, detail: str = "") -> None:
        self.stage = stage
        self.updated_at = time.monotonic()
        if detail:
            self.progress_detail = detail


class GenerateJobState(JobState):
    def __init__(self, item: SupervisorWorkItem) -> None:
        super().__init__(item)
        self.stage = "select_size"
        self.size = int(item.payload["size"])
        self.index = int(item.payload["index"])
        self.run_dir: Path | None = None
        self.raw_words: list[dict[str, object]] = []
        self.word_metadata: dict[str, list[dict[str, object]]] = {}
        self.batch_rng = random.Random()
        self.effective_attempts = 0
        self.attempt_index = 0
        self.seen_template_fingerprints: set[str] = set()
        self.candidate = None
        self.resolved_metadata: dict[str, dict[str, object]] = {}
        self.working_puzzle = None
        self.first_passed = 0
        self.final_passed = 0
        self.total = 0
        self.best_prepared: PreparedPuzzle | None = None

    def next_steps(self, ctx: RunAllContext) -> list[StepState]:
        if self.status != "active":
            return []
        if self.stage == "select_size":
            return [self._non_llm_step("select_size", "generate_select_size", self._select_size)]
        if self.stage == "fill_grid":
            return [self._background_step("fill_grid", "generate_fill_grid", self._fill_grid)]
        if self.stage == "define_initial":
            return [self._llm_step("define_initial", "generate_define_initial", PRIMARY_MODEL.model_id, self._define_initial)]
        if self.stage == "rewrite_evaluate":
            return [self._llm_step("rewrite_evaluate", "generate_rewrite_evaluate", PRIMARY_MODEL.model_id, self._rewrite_evaluate)]
        if self.stage == "title":
            return [self._llm_step("title", "generate_title", PRIMARY_MODEL.model_id, self._title)]
        if self.stage == "publish":
            return [self._non_llm_step("publish", "generate_publish", self._publish)]
        return []

    def _select_size(self, ctx: RunAllContext) -> object:
        self.run_dir = ctx.batch_output_root / f"{path_timestamp()}_{self.size}x{self.size}_{self.index:02d}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.raw_words = batch_publish_pipeline._load_words(ctx.words_path)
        self.word_metadata = batch_publish_pipeline._metadata_by_word(self.raw_words)
        self.batch_rng = random.Random(random.SystemRandom().randint(1, 10_000_000))
        self.effective_attempts = batch_publish_pipeline._preparation_attempts_for_size(self.size, 3)
        self._progress("fill_grid", detail=f"size={self.size}")
        return None

    def _fill_grid(self, ctx: RunAllContext) -> object:
        self.attempt_index += 1
        provisional_title = f"Puzzle {self.index}"
        self.candidate = batch_publish_pipeline._best_candidate(
            self.size,
            provisional_title,
            self.raw_words,
            rng=self.batch_rng,
            seen_template_fingerprints=self.seen_template_fingerprints if self.size == 7 else None,
            words_path=ctx.words_path,
            word_metadata=self.word_metadata,
            preparation_attempts=1,
        )
        puzzle = batch_publish_pipeline.parse_markdown(self.candidate.markdown)
        puzzle.title = ""
        self.resolved_metadata = batch_publish_pipeline._choose_metadata_variants_for_puzzle(
            puzzle, self.candidate.metadata
        )
        self.working_puzzle = puzzle
        self._progress("define_initial", detail=f"attempt={self.attempt_index}/{self.effective_attempts}")
        return None

    def _define_initial(self, ctx: RunAllContext) -> object:
        assert self.working_puzzle is not None
        generate_definitions_for_puzzle(
            self.working_puzzle,
            ctx.ai_client,
            metadata=self.resolved_metadata,
            runtime=ctx.runtime,
            model_config=PRIMARY_MODEL,
        )
        state = working_puzzle_from_puzzle(self.working_puzzle, split_compound=False)
        batch_publish_pipeline._backfill_generated_model(state, PRIMARY_MODEL.display_name)
        batch_publish_pipeline._inject_word_metadata(state, self.resolved_metadata)
        self.working_puzzle = state
        self._progress("rewrite_evaluate", detail=f"attempt={self.attempt_index}/{self.effective_attempts}")
        return None

    def _rewrite_evaluate(self, ctx: RunAllContext) -> object:
        assert self.working_puzzle is not None
        dex = batch_publish_pipeline.DexProvider.for_puzzle(self.working_puzzle)
        self.first_passed, self.final_passed, self.total = batch_publish_pipeline._rewrite_failed_clues(
            self.working_puzzle,
            ctx.ai_client,
            ctx.generate_rewrite_rounds,
            multi_model=ctx.multi_model,
            dex=dex,
            verify_candidates=ctx.verify_candidates,
            runtime=ctx.runtime,
        )
        batch_publish_pipeline._restore_best_versions(self.working_puzzle)
        self.working_puzzle.assessment = score_puzzle_state(self.working_puzzle, self.candidate.report)
        self._progress("title", detail=f"verified={self.final_passed}/{self.total}")
        return self.working_puzzle.assessment

    def _title(self, ctx: RunAllContext) -> object:
        assert self.working_puzzle is not None
        rendered_for_title = puzzle_from_working_state(self.working_puzzle)
        title_result = batch_publish_pipeline.generate_title_for_final_puzzle_result(
            rendered_for_title,
            client=ctx.ai_client,
            rate_client=ctx.ai_client,
            runtime=ctx.runtime,
            multi_model=ctx.multi_model,
        )
        self.working_puzzle.title = title_result.title
        prepared = PreparedPuzzle(
            title=title_result.title,
            title_score=title_result.score,
            candidate=self.candidate,
            puzzle=copy.deepcopy(self.working_puzzle),
            first_passed=self.first_passed,
            final_passed=self.final_passed,
            total=self.total,
            definition_score=self.working_puzzle.assessment.definition_score,
            blocking_words=[clue.word_normalized for clue in batch_publish_pipeline._blocking_clues(self.working_puzzle)],
            assessment=copy.deepcopy(self.working_puzzle.assessment),
        )
        self.best_prepared = batch_publish_pipeline._better_prepared_puzzle(
            self.best_prepared,
            prepared,
            client=ctx.ai_client,
            runtime=ctx.runtime,
        )
        if self.best_prepared and batch_publish_pipeline._is_publishable(self.best_prepared):
            self._progress("publish", detail=f"title={self.best_prepared.title}")
            return self.best_prepared
        if self.attempt_index < self.effective_attempts:
            log(
                "Rejected generated puzzle after quality gate: "
                + ", ".join(prepared.blocking_words[:10])
            )
            self._progress("fill_grid", detail=f"retry={self.attempt_index + 1}/{self.effective_attempts}")
            return prepared
        raise RuntimeError(
            f"Could not prepare a publishable {self.size}x{self.size} puzzle. "
            f"Missing definitions for: {', '.join(prepared.blocking_words[:12])}"
        )

    def _publish(self, ctx: RunAllContext) -> object:
        assert self.best_prepared is not None
        assert self.run_dir is not None
        puzzle_dir = self.run_dir / f"{self.index:02d}_{self.size}x{self.size}"
        puzzle_start = time.monotonic()
        manifest_item, puzzle_metric, word_metrics = publish_prepared_puzzle(
            prepared=self.best_prepared,
            index=self.index,
            total_puzzles=1,
            size=self.size,
            puzzle_dir=puzzle_dir,
            multi_model=ctx.multi_model,
        )
        puzzle_metric.total_elapsed_ms = int((time.monotonic() - puzzle_start) * 1000)
        write_metrics(
            BatchMetric(
                timestamp=utc_timestamp(),
                seed=0,
                models_used=batch_publish_pipeline.get_active_model_labels(multi_model=ctx.multi_model),
                puzzles=[puzzle_metric],
                word_metrics=word_metrics,
                total_elapsed_ms=puzzle_metric.total_elapsed_ms,
            ),
            self.run_dir / "metrics.json",
        )
        update_word_difficulty(word_metrics, ctx.words_path.parent / "word_difficulty.json")
        (self.run_dir / "manifest.json").write_text(
            json.dumps([manifest_item], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self._complete([manifest_item], detail=f"size={self.size} puzzle_id={manifest_item['puzzle_id']}")


class RedefineJobState(JobState):
    def __init__(self, item: SupervisorWorkItem) -> None:
        super().__init__(item)
        self.stage = "fetch"
        self.puzzle_row = copy.deepcopy(dict(item.payload["puzzle_row"]))
        self.clue_rows: list[dict] = []
        self.baseline_puzzle = None
        self.candidate_puzzle = None
        self.baseline_model_ids: list[str] = []
        self.baseline_model_label = ""
        self.baseline_passed = 0
        self.baseline_total = 0

    def next_steps(self, ctx: RunAllContext) -> list[StepState]:
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
        if self.stage == "rewrite":
            return [self._llm_step("rewrite", "redefine_rewrite", PRIMARY_MODEL.model_id, self._rewrite)]
        if self.stage == "persist":
            return [self._llm_step("persist", "redefine_persist", PRIMARY_MODEL.model_id, self._persist)]
        return []

    def _fetch(self, ctx: RunAllContext) -> object:
        puzzle_id = str(self.puzzle_row["id"])
        self.clue_rows = sorted(fetch_redefine_clues(ctx.supabase, puzzle_id), key=_clue_row_sort_key)
        if not self.clue_rows:
            log(f"  [{puzzle_id}] No clues found, skipping")
            return self._complete(0, detail="no_clues")
        self.baseline_puzzle = build_working_puzzle(self.puzzle_row, self.clue_rows)
        self.candidate_puzzle = build_working_puzzle(self.puzzle_row, self.clue_rows)
        log(f"  [{puzzle_id}] {len(self.clue_rows)} clues, title: {self.baseline_puzzle.title}")
        self._progress("baseline_verify", detail=f"clues={len(self.clue_rows)}")
        return None

    def _baseline_verify(self, ctx: RunAllContext) -> object:
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

    def _baseline_rate(self, ctx: RunAllContext) -> object:
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

    def _baseline_finalize(self, ctx: RunAllContext) -> object:
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
        self.baseline_passed = sum(
            1
            for clue in self.baseline_puzzle.horizontal_clues + self.baseline_puzzle.vertical_clues
            if clue.active_version().assessment.verified is True
        )
        self.baseline_total = len(self.baseline_puzzle.horizontal_clues) + len(self.baseline_puzzle.vertical_clues)
        puzzle_id = str(self.puzzle_row["id"])
        log(
            f"  [{puzzle_id}] baseline min={self.baseline_puzzle.assessment.min_rebus}/10 "
            f"avg={self.baseline_puzzle.assessment.avg_rebus:.1f}/10 "
            f"verified={self.baseline_puzzle.assessment.verified_count}/{self.baseline_puzzle.assessment.total_clues}"
        )
        self._progress("rewrite", detail="baseline_done")
        return self.baseline_puzzle.assessment

    def _rewrite(self, ctx: RunAllContext) -> object:
        assert self.candidate_puzzle is not None
        rewrite_puzzle_definitions(
            self.candidate_puzzle,
            ctx.ai_client,
            rounds=ctx.redefine_rounds,
            multi_model=ctx.multi_model,
            verify_candidates=ctx.verify_candidates,
            runtime=ctx.runtime,
        )
        self.candidate_puzzle.assessment = score_puzzle_state(self.candidate_puzzle)
        puzzle_id = str(self.puzzle_row["id"])
        assessment = self.candidate_puzzle.assessment
        log(
            f"  [{puzzle_id}] candidate min={assessment.min_rebus}/10 "
            f"avg={assessment.avg_rebus:.1f}/10 "
            f"verified={assessment.verified_count}/{assessment.total_clues}"
        )
        self._progress("persist", detail=f"rewrite_min={assessment.min_rebus}")
        return assessment

    def _persist(self, ctx: RunAllContext) -> object:
        assert self.baseline_puzzle is not None
        assert self.candidate_puzzle is not None
        updated = persist_redefined_puzzle(
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
        return self._complete(updated, detail=f"updated={updated}")


class RetitleJobState(JobState):
    def __init__(self, item: SupervisorWorkItem) -> None:
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

    def next_steps(self, ctx: RunAllContext) -> list[StepState]:
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
        if self.stage == "persist":
            return [self._non_llm_step("persist", "retitle_persist", self._persist)]
        return []

    def _fetch(self, ctx: RunAllContext) -> object:
        puzzle_id = str(self.puzzle_row["id"])
        clues = fetch_retitle_clues(ctx.supabase, puzzle_id)
        if not clues:
            log(f"  [{puzzle_id}] No clues found, skipping")
            return self._complete(False, detail="no_clues")
        self.words_list = [c["word_normalized"] for c in clues if c.get("word_normalized")]
        self.definitions = [c["definition"] for c in clues if c.get("definition")]
        if not self.words_list or not self.definitions:
            log(f"  [{puzzle_id}] Missing words or definitions, skipping")
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

    def _generate_with_model(self, ctx: RunAllContext, model) -> object:
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

    def _generate_primary(self, ctx: RunAllContext) -> object:
        return self._generate_with_model(ctx, PRIMARY_MODEL)

    def _generate_secondary(self, ctx: RunAllContext) -> object:
        return self._generate_with_model(ctx, SECONDARY_MODEL)

    def _rate_current(self, ctx: RunAllContext) -> object:
        assert self.title_state is not None
        assert self.pending_title is not None
        _rate_batch_candidates(
            [(self.title_state, self.pending_title)],
            ctx.rate_client,
            generator_model=self.pending_generator_model,
            runtime=ctx.runtime,
            rating_model=SECONDARY_MODEL if self.pending_generator_model.model_id == PRIMARY_MODEL.model_id else PRIMARY_MODEL,
            round_idx=self.round_idx,
        )
        if self.title_state.done:
            self._progress("persist", detail=f"title={self.title_state.final_result.title}")
            return self.title_state.final_result
        next_stage = (
            "generate_secondary"
            if self.pending_generator_model.model_id == PRIMARY_MODEL.model_id and ctx.multi_model
            else "round_finalize"
        )
        self.pending_title = None
        self._progress(next_stage, detail=f"round={self.round_idx}")
        return None

    def _rate_primary(self, ctx: RunAllContext) -> object:
        return self._rate_current(ctx)

    def _rate_secondary(self, ctx: RunAllContext) -> object:
        return self._rate_current(ctx)

    def _round_finalize(self, ctx: RunAllContext) -> object:
        assert self.title_state is not None
        if self.title_state.done or self.round_idx >= MAX_TITLE_ROUNDS:
            result = _finalize_title_result(self.title_state)
            self._progress("persist", detail=f"title={result.title}")
            return result
        self.round_idx += 1
        self.pending_title = None
        self._progress("generate_primary", detail=f"round={self.round_idx}")
        return None

    def _persist(self, ctx: RunAllContext) -> object:
        assert self.title_state is not None
        title_result = _finalize_title_result(self.title_state)
        changed = _apply_title_result(
            ctx.supabase,
            self.puzzle_row,
            title_result,
            ctx.rate_client,
            dry_run=ctx.dry_run,
            multi_model=ctx.multi_model,
            runtime=ctx.runtime,
            forbidden_title_keys=self.forbidden_title_keys,
            words=self.words_list,
        )
        return self._complete(changed, detail=f"changed={changed}")


class SimplifyJobState(JobState):
    def __init__(self, item: SupervisorWorkItem) -> None:
        super().__init__(item)
        self.stage = "fetch_bucket"
        self.word = str(item.payload["word"])
        self.buckets: dict[tuple[str, str, str], list[object]] = {}
        self.batch_pairs: list[object] = []
        self.primary_votes: dict[str, object] = {}
        self.secondary_votes: dict[str, object] = {}
        self.approved_pairs: list[tuple[object, object, object, str, bool]] = []
        self.stats = SimplifyStats()
        self.report_dir: Path | None = None
        self.merges_path: Path | None = None
        self.skipped_path: Path | None = None

    def next_steps(self, ctx: RunAllContext) -> list[StepState]:
        if self.status != "active":
            return []
        if self.stage == "fetch_bucket":
            return [self._non_llm_step("fetch_bucket", "simplify_fetch_bucket", self._fetch_bucket)]
        if self.stage == "compare_gemma":
            return [self._llm_step("compare_gemma", "simplify_compare_gemma", PRIMARY_MODEL.model_id, self._compare_gemma)]
        if self.stage == "compare_eurollm":
            return [self._llm_step("compare_eurollm", "simplify_compare_eurollm", SECONDARY_MODEL.model_id, self._compare_eurollm)]
        if self.stage == "rewrite_or_choose_survivor":
            return [self._llm_step("rewrite_or_choose_survivor", "simplify_rewrite_or_choose_survivor", PRIMARY_MODEL.model_id, self._rewrite_or_choose_survivor)]
        if self.stage == "apply_merge":
            return [self._non_llm_step("apply_merge", "simplify_apply_merge", self._apply_merge)]
        return []

    def _fetch_bucket(self, ctx: RunAllContext) -> object:
        self.report_dir = ctx.run_dir / "simplify" / self.word
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.merges_path = self.report_dir / "merges.jsonl"
        self.skipped_path = self.report_dir / "skipped.jsonl"
        self.buckets, self.batch_pairs = load_simplify_bucket(
            ctx.store,
            word=self.word,
            batch_size=ctx.simplify_batch_size,
        )
        if not self.batch_pairs:
            return self._complete(0, detail=f"word={self.word} no_pairs")
        self.stats.pairs_sampled += len(self.batch_pairs)
        self._progress("compare_gemma", detail=f"pairs={len(self.batch_pairs)}")
        return None

    def _compare_gemma(self, ctx: RunAllContext) -> object:
        self.primary_votes = compare_simplify_pairs(
            ctx.ai_client,
            ctx.runtime,
            self.batch_pairs,
            model_id=PRIMARY_MODEL.model_id,
        )
        self._progress("compare_eurollm", detail=f"pairs={len(self.batch_pairs)}")
        return self.primary_votes

    def _compare_eurollm(self, ctx: RunAllContext) -> object:
        self.secondary_votes = compare_simplify_pairs(
            ctx.ai_client,
            ctx.runtime,
            self.batch_pairs,
            model_id=SECONDARY_MODEL.model_id,
        )
        self.stats.pairs_compared += len(self.batch_pairs) * 2
        self._progress("rewrite_or_choose_survivor", detail=f"pairs={len(self.batch_pairs)}")
        return self.secondary_votes

    def _rewrite_or_choose_survivor(self, ctx: RunAllContext) -> object:
        self.approved_pairs = []
        for pair in self.batch_pairs:
            first = self.primary_votes[pair.key]
            second = self.secondary_votes[pair.key]
            if first.vote is None or second.vote is None:
                self.stats.compare_invalid += 1
                if self.skipped_path is not None:
                    _append_jsonl(self.skipped_path, {
                        "word": pair.word,
                        "pair_key": pair.key,
                        "reason": "compare_invalid",
                        "phase1_status": first.parse_status,
                        "phase2_status": second.parse_status,
                    })
                continue
            if not first.vote.same_meaning or not second.vote.same_meaning:
                if self.skipped_path is not None:
                    _append_jsonl(self.skipped_path, {
                        "word": pair.word,
                        "pair_key": pair.key,
                        "reason": "not_same_meaning",
                    })
                continue
            found = find_simplify_pair_rows(pair, self.buckets)
            if found is None:
                if self.skipped_path is not None:
                    _append_jsonl(self.skipped_path, {
                        "word": pair.word,
                        "pair_key": pair.key,
                        "reason": "pair_no_longer_active",
                    })
                continue
            left, right = found
            self.stats.pairs_same_sense += 1
            if should_rewrite_survivor(left, right):
                rewrite = rewrite_merged_canonical_definition(
                    ctx.ai_client,
                    word=pair.word,
                    definition_a=left.definition,
                    definition_b=right.definition,
                    model=SECONDARY_MODEL.model_id,
                )
                rewritten_definition = rewrite.definition
                validation = validate_merged_canonical_definition(
                    ctx.ai_client,
                    word=pair.word,
                    answer_length=len(pair.word),
                    definition_a=left.definition,
                    definition_b=right.definition,
                    candidate_definition=rewritten_definition,
                    model=PRIMARY_MODEL.model_id,
                )
                if not validation.accepted:
                    self.stats.rewrite_invalid += 1
                    self.stats.rewrite_fallback_existing += 1
                    rewritten_definition = choose_existing_survivor(left, right).definition
                self.approved_pairs.append((pair, left, right, rewritten_definition, True))
                continue
            survivor_definition = choose_existing_survivor(left, right).definition
            self.approved_pairs.append((pair, left, right, survivor_definition, False))
        self._progress("apply_merge", detail=f"approved={len(self.approved_pairs)}")
        return self.approved_pairs

    def _apply_merge(self, ctx: RunAllContext) -> object:
        touched_words: set[str] = set()
        for pair, left, right, survivor_definition, rewrite_attempted in self.approved_pairs:
            try:
                survivor_id = apply_simplify_merge(
                    store=ctx.store,
                    left=left,
                    right=right,
                    survivor_definition=survivor_definition,
                    dry_run=ctx.dry_run,
                )
            except Exception as exc:
                self.stats.db_failures += 1
                if self.skipped_path is not None:
                    _append_jsonl(self.skipped_path, {
                        "word": pair.word,
                        "pair_key": pair.key,
                        "reason": "db_failure",
                        "error": str(exc),
                    })
                continue
            self.stats.pairs_merged += 1
            update_top_reductions(self.stats, word=pair.word)
            if self.merges_path is not None:
                _append_jsonl(self.merges_path, {
                    "word": pair.word,
                    "pair_key": pair.key,
                    "left_id": left.id,
                    "right_id": right.id,
                    "survivor_id": survivor_id,
                    "survivor_definition": survivor_definition,
                    "rewrite_attempted": rewrite_attempted,
                })
            touched_words.add(pair.word)
        if touched_words:
            refresh_simplify_bucket_rows(
                ctx.store,
                self.buckets,
                touched_words=touched_words,
                word_filter=self.word,
            )
        return self._complete(
            self.stats.pairs_merged,
            detail=(
                f"word={self.word} merged={self.stats.pairs_merged} "
                f"same_sense={self.stats.pairs_same_sense} compare_invalid={self.stats.compare_invalid}"
            ),
        )


@dataclass
class TopicSlot:
    topic: str
    active_job: JobState | None = None
    completed_count: int = 0
    failed_count: int = 0
    backoff_until: float = 0.0


def _clue_row_sort_key(row: dict) -> tuple[object, ...]:
    direction = "V" if str(row.get("direction") or "").strip().lower() in {"v", "vertical"} else "H"
    return (
        0 if direction == "H" else 1,
        int(row.get("clue_number") or 0),
        int(row.get("start_row") or 0),
        int(row.get("start_col") or 0),
        row.get("id") or "",
    )


class RunAllSupervisor:
    def __init__(
        self,
        *,
        context: RunAllContext,
        topics: list[str],
        topic_caps: dict[str, int],
        idle_sleep_seconds: int = DEFAULT_IDLE_SLEEP_SECONDS,
        heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
        retry_limit: int = DEFAULT_RETRY_LIMIT,
        debug: bool = False,
    ) -> None:
        self.ctx = context
        self.topics = [topic for topic in topics if topic in SUPPORTED_TOPICS]
        self.topic_caps = {topic: 1 for topic in self.topics}
        self.requested_topic_caps = {topic: max(1, int(topic_caps.get(topic, 1))) for topic in self.topics}
        self.idle_sleep_seconds = max(1, int(idle_sleep_seconds))
        self.heartbeat_seconds = max(1, int(heartbeat_seconds))
        self.retry_limit = max(0, int(retry_limit))
        self.debug = bool(debug)
        self.pending_items: list[SupervisorWorkItem] = []
        self.slots = {topic: TopicSlot(topic=topic) for topic in self.topics}
        self.claims = ClaimState()
        self.completed = 0
        self.failed = 0
        self.last_heartbeat_at = 0.0
        self.worker_executor: concurrent.futures.ThreadPoolExecutor | None = None
        self.worker_task: WorkerTask | None = None
        self.ctx.runtime.switch_callback = self._on_model_switch

    def run(self, *, max_cycles: int | None = None) -> None:
        cycles = 0
        while True:
            cycles += 1
            self._poll_worker_task()
            self._maybe_heartbeat(force=cycles == 1)
            self._refill_slots()
            ran_work = self._run_ready_steps()
            self._poll_worker_task()
            self._finalize_finished_jobs()
            if max_cycles is not None and cycles >= max_cycles:
                return
            if ran_work:
                continue
            if self.worker_task is not None:
                time.sleep(WORKER_POLL_SLEEP_SECONDS)
                continue
            if self._refill_slots():
                continue
            log(
                f"[run_all idle] topics={','.join(self.topics)} "
                f"sleep={self.idle_sleep_seconds}s {self._queue_snapshot_text()}"
            )
            time.sleep(self.idle_sleep_seconds)

    def _run_ready_steps(self) -> bool:
        self._poll_worker_task()
        ran_any = False
        steps = self._collect_steps()
        inline_steps = [step for step in steps if step.execution_mode == "inline_non_llm"]
        for step in inline_steps:
            self._run_step(step, lane="supervisor")
            ran_any = True
            self._poll_worker_task()
            self._finalize_finished_jobs()
        if self.worker_task is None:
            background_steps = [step for step in self._collect_steps() if step.execution_mode == "background_non_llm"]
            if background_steps:
                self._submit_background_step(background_steps[0])
                ran_any = True
        llm_steps = [step for step in self._collect_steps() if step.execution_mode == "llm"]
        if not llm_steps:
            return ran_any
        model_id = self._choose_model_for_steps(llm_steps)
        self._ensure_model_active(model_id)
        batch = [step for step in llm_steps if step.model_id == model_id]
        topic_counts = Counter(step.topic for step in batch)
        topic_text = " ".join(f"{topic}={topic_counts.get(topic, 0)}" for topic in self.topics)
        log(
            f"[run_all batch] model={model_id} steps={len(batch)} "
            f"topics=({topic_text}) {self._queue_snapshot_text()}"
        )
        for step in batch:
            self._run_step(step, lane="llm")
            ran_any = True
            self._poll_worker_task()
            self._finalize_finished_jobs()
        return ran_any

    def _collect_steps(self) -> list[StepState]:
        now = time.monotonic()
        steps: list[StepState] = []
        for topic in self.topics:
            slot = self.slots[topic]
            job = slot.active_job
            if (
                job is None
                or job.status != "active"
                or job.available_after > now
                or job.running_step_id is not None
            ):
                continue
            steps.extend(job.next_steps(self.ctx))
        return steps

    def _choose_model_for_steps(self, steps: list[StepState]) -> str:
        self.ctx.runtime.sync()
        current_model_id = self.ctx.runtime.current_model_id
        ready_by_model = Counter(step.model_id for step in steps if step.model_id)
        if current_model_id and ready_by_model.get(current_model_id, 0) > 0:
            return current_model_id
        for model_id in (PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id):
            if ready_by_model.get(model_id, 0) > 0:
                return model_id
        return PRIMARY_MODEL.model_id

    def _ensure_model_active(self, model_id: str) -> None:
        if model_id == PRIMARY_MODEL.model_id:
            self.ctx.runtime.activate_primary()
            return
        if model_id == SECONDARY_MODEL.model_id:
            self.ctx.runtime.activate_secondary()
            return
        self.ctx.runtime.activate(PRIMARY_MODEL)

    def _run_step(self, step: StepState, *, lane: str) -> None:
        job = self._job_by_id(step.job_id)
        if job is None or job.status != "active":
            return
        job.running_step_id = step.step_id
        log(
            f"[run_all step] topic={job.topic} job={job.item_id} stage={job.stage} "
            f"step={step.step_id} purpose={step.purpose} lane={lane} model={step.model_id or '-'}"
        )
        try:
            step.runner(self.ctx)
        except KeyboardInterrupt:
            job.running_step_id = None
            raise
        except SystemExit as exc:
            job.running_step_id = None
            self._handle_step_error(
                job,
                step,
                RuntimeError(f"supervisor boundary violation: SystemExit escaped step: {exc}"),
            )
        except Exception as exc:
            job.running_step_id = None
            self._handle_step_error(job, step, exc)
        else:
            job.running_step_id = None

    def _submit_background_step(self, step: StepState) -> None:
        job = self._job_by_id(step.job_id)
        if job is None or job.status != "active":
            return
        if self.worker_executor is None:
            self.worker_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="run_all_worker")
        job.running_step_id = step.step_id
        log(
            f"[run_all step] topic={job.topic} job={job.item_id} stage={job.stage} "
            f"step={step.step_id} purpose={step.purpose} lane=worker model=-"
        )
        future = self.worker_executor.submit(step.runner, self.ctx)
        self.worker_task = WorkerTask(step=step, future=future, started_at=time.monotonic())

    def _poll_worker_task(self) -> bool:
        if self.worker_task is None or not self.worker_task.future.done():
            return False
        task = self.worker_task
        self.worker_task = None
        job = self._job_by_id(task.step.job_id)
        if job is not None:
            job.running_step_id = None
        try:
            task.future.result()
        except KeyboardInterrupt:
            raise
        except SystemExit as exc:
            if job is not None:
                self._handle_step_error(
                    job,
                    task.step,
                    RuntimeError(f"supervisor boundary violation: SystemExit escaped worker step: {exc}"),
                )
        except Exception as exc:
            if job is not None:
                self._handle_step_error(job, task.step, exc)
        return True

    def _handle_step_error(self, job: JobState, step: StepState, exc: Exception) -> None:
        job.item.attempts += 1
        job.last_error = str(exc)
        job.running_step_id = None
        if job.item.attempts <= self.retry_limit:
            backoff_seconds = min(60, 5 * (2 ** (job.item.attempts - 1)))
            job.available_after = time.monotonic() + backoff_seconds
            log(
                f"[run_all retry] topic={job.topic} job={job.item_id} step={step.step_id} "
                f"attempt={job.item.attempts} backoff_seconds={backoff_seconds} error={exc}"
            )
            return
        job.status = "failed"
        job.result = exc
        log(
            f"[run_all failed] topic={job.topic} job={job.item_id} step={step.step_id} "
            f"attempts={job.item.attempts} error={exc}",
            level="ERROR",
        )

    def _job_by_id(self, item_id: str) -> JobState | None:
        for slot in self.slots.values():
            if slot.active_job is not None and slot.active_job.item_id == item_id:
                return slot.active_job
        return None

    def _finalize_finished_jobs(self) -> None:
        for topic in self.topics:
            slot = self.slots[topic]
            job = slot.active_job
            if job is None or job.status not in {"complete", "failed"}:
                continue
            self.claims.release(job)
            elapsed_ms = int((time.monotonic() - job.started_at) * 1000)
            if job.status == "complete":
                slot.completed_count += 1
                self.completed += 1
                log(
                    f"[run_all finalize] topic={job.topic} job={job.item_id} outcome=complete "
                    f"elapsed_ms={elapsed_ms} persisted=yes detail={job.progress_detail or '-'} result={job.result!r}"
                )
            else:
                slot.failed_count += 1
                self.failed += 1
                log(
                    f"[run_all finalize] topic={job.topic} job={job.item_id} outcome=failed "
                    f"elapsed_ms={elapsed_ms} persisted=no detail={job.last_error or '-'}"
                )
            slot.active_job = None

    def _refill_slots(self) -> int:
        admitted = 0
        if self._admission_frozen():
            return 0
        for topic in self.topics:
            slot = self.slots[topic]
            if slot.active_job is not None:
                continue
            item = self._next_pending_for_topic(topic)
            if item is None:
                item = self._poll_one_topic(topic)
            if item is None:
                continue
            job = self._build_job(item)
            slot.active_job = job
            admitted += 1
            log(
                f"[run_all start] topic={topic} job={job.item_id} task={job.task_kind} "
                f"preferred={job.preferred_model_id} stage={job.stage}"
            )
        return admitted

    def _admission_frozen(self) -> bool:
        step_counts = self._runnable_counts_by_model()
        self.ctx.runtime.sync()
        current_model_id = self.ctx.runtime.current_model_id
        if not current_model_id:
            return False
        other_model_id = SECONDARY_MODEL.model_id if current_model_id == PRIMARY_MODEL.model_id else PRIMARY_MODEL.model_id
        return step_counts.get(current_model_id, 0) > 0 and step_counts.get(other_model_id, 0) > 0

    def _next_pending_for_topic(self, topic: str) -> SupervisorWorkItem | None:
        now = time.monotonic()
        for index, item in enumerate(self.pending_items):
            if item.topic != topic or item.available_after > now:
                continue
            return self.pending_items.pop(index)
        return None

    def _poll_one_topic(self, topic: str) -> SupervisorWorkItem | None:
        if topic == "generate":
            return self._poll_generate()
        if topic == "redefine":
            return self._poll_redefine()
        if topic == "retitle":
            return self._poll_retitle()
        if topic == "simplify":
            return self._poll_simplify()
        return None

    def _poll_generate(self) -> SupervisorWorkItem | None:
        size = select_auto_size(client=self.ctx.supabase)
        preferred_model = initial_generation_model(self.ctx.runtime).model_id
        item = SupervisorWorkItem(
            item_id=f"generate:size:{size}:{int(time.time() * 1000)}",
            topic="generate",
            task_kind="generate",
            preferred_model_id=preferred_model,
            target_models=self._targets_for_topic("generate"),
            payload={"size": size, "index": self.completed + self.failed + len(self.pending_items) + 1},
        )
        self._admit_item(item)
        return self._next_pending_for_topic("generate")

    def _poll_redefine(self) -> SupervisorWorkItem | None:
        rows = fetch_redefine_puzzles(self.ctx.supabase)
        for row in rows:
            puzzle_id = str(row.get("id") or "")
            if self.claims.has_puzzle(puzzle_id):
                continue
            words = self._fetch_puzzle_words(puzzle_id)
            if self.claims.puzzle_word_conflict(words):
                continue
            item = SupervisorWorkItem(
                item_id=f"redefine:puzzle:{puzzle_id}",
                topic="redefine",
                task_kind="redefine",
                preferred_model_id=PRIMARY_MODEL.model_id,
                target_models=self._targets_for_topic("redefine"),
                payload={"puzzle_row": row},
                puzzle_id=puzzle_id,
                words=words,
            )
            self._admit_item(item)
            return self._next_pending_for_topic("redefine")
        return None

    def _poll_retitle(self) -> SupervisorWorkItem | None:
        rows = select_puzzles_for_retitle(fetch_retitle_puzzles(self.ctx.supabase))
        for row in rows:
            puzzle_id = str(row.get("id") or "")
            if self.claims.has_puzzle(puzzle_id):
                continue
            words = self._fetch_puzzle_words(puzzle_id)
            if self.claims.puzzle_word_conflict(words):
                continue
            item = SupervisorWorkItem(
                item_id=f"retitle:puzzle:{puzzle_id}",
                topic="retitle",
                task_kind="retitle",
                preferred_model_id=PRIMARY_MODEL.model_id,
                target_models=self._targets_for_topic("retitle"),
                payload={"puzzle_row": row},
                puzzle_id=puzzle_id,
                words=words,
            )
            self._admit_item(item)
            return self._next_pending_for_topic("retitle")
        return None

    def _poll_simplify(self) -> SupervisorWorkItem | None:
        pairs = build_candidate_pairs(
            [
                row
                for row in self.ctx.store.fetch_active_canonical_variants()
                if row.word_normalized not in self.claims.simplify_words
                and not self.claims.simplify_word_conflict({row.word_normalized})
            ]
        )
        seen_words: set[str] = set()
        for pair in pairs:
            if pair.word in seen_words:
                continue
            words = {pair.word}
            if self.claims.simplify_word_conflict(words):
                continue
            item = SupervisorWorkItem(
                item_id=f"simplify:word:{pair.word}:{pair.left_id}:{pair.right_id}",
                topic="simplify",
                task_kind="simplify",
                preferred_model_id=PRIMARY_MODEL.model_id,
                target_models=self._targets_for_topic("simplify"),
                payload={"word": pair.word},
                words=words,
            )
            self._admit_item(item)
            return self._next_pending_for_topic("simplify")
        return None

    def _build_job(self, item: SupervisorWorkItem) -> JobState:
        if item.topic == "generate":
            return GenerateJobState(item)
        if item.topic == "redefine":
            return RedefineJobState(item)
        if item.topic == "retitle":
            return RetitleJobState(item)
        if item.topic == "simplify":
            return SimplifyJobState(item)
        raise ValueError(f"Unsupported topic {item.topic}")

    def _fetch_puzzle_words(self, puzzle_id: str) -> set[str]:
        rows = self.ctx.store.fetch_clue_rows(
            puzzle_id=puzzle_id,
            extra_fields=("word_normalized",),
        )
        return {
            str(row.get("word_normalized") or "").strip().upper()
            for row in rows
            if str(row.get("word_normalized") or "").strip()
        }

    def _targets_for_topic(self, topic: str) -> tuple[str, ...]:
        if not self.ctx.multi_model:
            return (PRIMARY_MODEL.model_id,)
        return (PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id)

    def _admit_item(self, item: SupervisorWorkItem) -> None:
        self.pending_items.append(item)
        self.claims.claim(item)
        log(
            f"[run_all admit] topic={item.topic} item={item.item_id} task={item.task_kind} "
            f"preferred={item.preferred_model_id} targets={','.join(item.target_models)} "
            f"{self._queue_snapshot_text()}"
        )

    def _runnable_counts_by_model(self) -> dict[str, int]:
        counts = {PRIMARY_MODEL.model_id: 0, SECONDARY_MODEL.model_id: 0}
        for step in self._collect_steps():
            if step.model_id:
                counts[step.model_id] = counts.get(step.model_id, 0) + 1
        return counts

    def _queue_counts_by_topic(self) -> dict[str, int]:
        counts = {topic: 0 for topic in self.topics}
        for topic in self.topics:
            slot = self.slots[topic]
            if slot.active_job is not None:
                counts[topic] += 1
        for item in self.pending_items:
            counts[item.topic] = counts.get(item.topic, 0) + 1
        return counts

    def _active_slot_text(self) -> str:
        return " ".join(
            f"{topic}={(self.slots[topic].active_job.item_id if self.slots[topic].active_job is not None else '-')}"
            for topic in self.topics
        )

    def _worker_slot_text(self) -> str:
        if self.worker_task is None:
            return "-"
        return f"{self.worker_task.step.topic}:{self.worker_task.step.step_id}"

    def _queue_snapshot_text(self) -> str:
        model_counts = self._runnable_counts_by_model()
        topic_counts = self._queue_counts_by_topic()
        model_text = " ".join(f"{model}={count}" for model, count in sorted(model_counts.items()))
        topic_text = " ".join(f"{topic}={count}" for topic, count in sorted(topic_counts.items()))
        return (
            f"queues_model=({model_text}) queues_topic=({topic_text}) "
            f"active_slots=({self._active_slot_text()}) worker={self._worker_slot_text()} "
            f"completed={self.completed} failed={self.failed}"
        )

    def _on_model_switch(self, previous_model_id: str, next_model_id: str, runtime: LmRuntime) -> None:
        log(
            f"[run_all switch] from={previous_model_id or '-'} to={next_model_id} "
            f"reason=current_queue_empty switch_count={runtime.switch_count} "
            f"{self._queue_snapshot_text()}"
        )

    def _maybe_heartbeat(self, *, force: bool) -> None:
        if not self.debug:
            return
        now = time.monotonic()
        if not force and (now - self.last_heartbeat_at) < self.heartbeat_seconds:
            return
        self.last_heartbeat_at = now
        self.ctx.runtime.sync()
        blocked = sum(
            1
            for slot in self.slots.values()
            if slot.active_job is not None and slot.active_job.available_after > time.monotonic()
        )
        log(
            f"[run_all heartbeat] loaded={self.ctx.runtime.current_model_label or '-'} "
            f"blocked={blocked} worker={self._worker_slot_text()} {self._queue_snapshot_text()}"
        )

    def close(self) -> None:
        if self.worker_executor is not None:
            self.worker_executor.shutdown(wait=True, cancel_futures=False)
            self.worker_executor = None


def _parse_topics(value: str | None) -> list[str]:
    if not value:
        return list(SUPPORTED_TOPICS)
    topics = [topic.strip().lower() for topic in value.split(",") if topic.strip()]
    invalid = [topic for topic in topics if topic not in SUPPORTED_TOPICS]
    if invalid:
        raise SystemExit(f"Unsupported topics: {', '.join(invalid)}")
    deduped: list[str] = []
    for topic in topics:
        if topic not in deduped:
            deduped.append(topic)
    return deduped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified long-running supervisor for generation and improvement.")
    parser.add_argument(
        "--topics",
        help="Comma-separated topics: generate,redefine,retitle,simplify (default: all).",
    )
    parser.add_argument("--words", default="generator/output/words.json", help="Path to words.json cache.")
    parser.add_argument("--output-root", default="generator/output/run_all_runs", help="Supervisor artifact root.")
    parser.add_argument("--generate-cap", type=int, default=1)
    parser.add_argument("--redefine-cap", type=int, default=1)
    parser.add_argument("--retitle-cap", type=int, default=1)
    parser.add_argument("--simplify-cap", type=int, default=1)
    parser.add_argument("--idle-sleep-seconds", type=int, default=DEFAULT_IDLE_SLEEP_SECONDS)
    parser.add_argument("--heartbeat-seconds", type=int, default=DEFAULT_HEARTBEAT_SECONDS)
    parser.add_argument("--rewrite-rounds", type=int, default=GENERATE_REWRITE_ROUNDS)
    parser.add_argument("--rounds", type=int, default=REDEFINE_ROUNDS)
    parser.add_argument("--verify-candidates", type=int, default=VERIFY_CANDIDATE_COUNT)
    parser.add_argument("--simplify-batch-size", type=int, default=DEFAULT_SIMPLIFY_BATCH_SIZE)
    parser.add_argument(
        "--multi-model",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use the configured two-model workflow (default: True).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not persist DB changes for non-generation topics.")
    add_llm_debug_argument(parser)
    return parser


@contextlib.contextmanager
def _singleton_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise SystemExit(f"Another run_all supervisor already holds {path}") from exc
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    try:
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _preflight(*, topics: list[str]) -> None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
    create_service_role_client()
    runtime = LmRuntime(multi_model=True)
    runtime.sync()
    if "generate" in topics:
        _rust_binary_path()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    topics = _parse_topics(args.topics)
    if args.dry_run and "generate" in topics:
        parser.error("--dry-run is not supported when generate topic is enabled")

    run_root = Path(args.output_root)
    run_dir = run_root / path_timestamp()
    log_path = run_dir / "run.log"
    audit_path = run_dir / "audit.jsonl"
    handle = install_process_logging(
        run_id=run_dir.name,
        component="run_all",
        log_path=log_path,
        audit_path=audit_path,
        tee_console=True,
    )
    try:
        set_llm_debug_enabled(bool(args.debug))
        log(f"Run log: {log_path}")
        log(f"Audit log: {audit_path}")
        log(f"Topics: {','.join(topics)}")
        with _singleton_lock(LOCK_PATH):
            _preflight(topics=topics)
            supabase = create_service_role_client()
            runtime = LmRuntime(multi_model=args.multi_model)
            context = RunAllContext(
                supabase=supabase,
                ai_client=create_ai_client(),
                rate_client=create_ai_client(),
                runtime=runtime,
                store=ClueCanonStore(client=supabase),
                run_dir=run_dir,
                batch_output_root=run_dir / "batch",
                words_path=Path(args.words),
                multi_model=args.multi_model,
                dry_run=bool(args.dry_run),
                generate_rewrite_rounds=max(1, args.rewrite_rounds),
                redefine_rounds=max(1, args.rounds),
                verify_candidates=max(1, args.verify_candidates),
                simplify_batch_size=max(1, args.simplify_batch_size),
            )
            supervisor = RunAllSupervisor(
                context=context,
                topics=topics,
                topic_caps={
                    "generate": args.generate_cap,
                    "redefine": args.redefine_cap,
                    "retitle": args.retitle_cap,
                    "simplify": args.simplify_cap,
                },
                idle_sleep_seconds=max(1, args.idle_sleep_seconds),
                heartbeat_seconds=max(1, args.heartbeat_seconds),
                debug=bool(args.debug),
            )
            try:
                supervisor.run()
            finally:
                supervisor.close()
        return 0
    finally:
        handle.restore()


if __name__ == "__main__":
    raise SystemExit(main())
