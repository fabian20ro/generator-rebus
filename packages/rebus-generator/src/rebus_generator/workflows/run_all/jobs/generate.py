from __future__ import annotations

import copy
import json
import random
import time
from pathlib import Path

from rebus_generator.platform.io.dex_cache import DexProvider
from rebus_generator.platform.io.metrics import BatchMetric, update_word_difficulty, write_metrics
from rebus_generator.platform.io.rust_bridge import _best_candidate, _load_words, _metadata_by_word
from rebus_generator.platform.llm.models import PRIMARY_MODEL
from rebus_generator.domain.pipeline_state import puzzle_from_working_state, working_puzzle_from_puzzle
from rebus_generator.domain.puzzle_metrics import score_puzzle_state
from rebus_generator.domain.score_helpers import _restore_best_versions
from rebus_generator.platform.io.runtime_logging import path_timestamp, utc_timestamp, log
from rebus_generator.platform.io.markdown_io import parse_markdown
from rebus_generator.workflows.generate.define import generate_definitions_for_puzzle
from rebus_generator.workflows.generate.models import PreparedPuzzle
from rebus_generator.workflows.generate.prepare import (
    _backfill_generated_model,
    _blocking_clues,
    _choose_metadata_variants_for_puzzle,
    _inject_word_metadata,
    _preparation_attempts_for_size,
    _rewrite_failed_clues,
)
from rebus_generator.workflows.generate.publish import publish_prepared_puzzle
from rebus_generator.workflows.generate.quality_gate import _better_prepared_puzzle, _is_publishable
from rebus_generator.workflows.retitle.generate import generate_title_for_final_puzzle_result
from .base import JobState


class GenerateJobState(JobState):
    def __init__(self, item) -> None:
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

    def next_steps(self, ctx):
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

    def _select_size(self, ctx):
        self.run_dir = ctx.batch_output_root / f"{path_timestamp()}_{self.size}x{self.size}_{self.index:02d}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.raw_words = _load_words(ctx.words_path)
        self.word_metadata = _metadata_by_word(self.raw_words)
        self.batch_rng = random.Random(random.SystemRandom().randint(1, 10_000_000))
        self.effective_attempts = _preparation_attempts_for_size(self.size, 3)
        self._progress("fill_grid", detail=f"size={self.size}")
        return None

    def _fill_grid(self, ctx):
        self.attempt_index += 1
        provisional_title = f"Puzzle {self.index}"
        self.candidate = _best_candidate(
            self.size,
            provisional_title,
            self.raw_words,
            rng=self.batch_rng,
            seen_template_fingerprints=self.seen_template_fingerprints if self.size == 7 else None,
            words_path=ctx.words_path,
            word_metadata=self.word_metadata,
            preparation_attempts=1,
        )
        puzzle = parse_markdown(self.candidate.markdown)
        puzzle.title = ""
        self.resolved_metadata = _choose_metadata_variants_for_puzzle(
            puzzle,
            self.candidate.metadata,
        )
        self.working_puzzle = puzzle
        self._progress("define_initial", detail=f"attempt={self.attempt_index}/{self.effective_attempts}")
        return None

    def _define_initial(self, ctx):
        assert self.working_puzzle is not None
        generate_definitions_for_puzzle(
            self.working_puzzle,
            ctx.ai_client,
            metadata=self.resolved_metadata,
            runtime=ctx.runtime,
            model_config=PRIMARY_MODEL,
        )
        state = working_puzzle_from_puzzle(self.working_puzzle, split_compound=False)
        _backfill_generated_model(state, PRIMARY_MODEL.display_name)
        _inject_word_metadata(state, self.resolved_metadata)
        self.working_puzzle = state
        self._progress("rewrite_evaluate", detail=f"attempt={self.attempt_index}/{self.effective_attempts}")
        return None

    def _rewrite_evaluate(self, ctx):
        assert self.working_puzzle is not None
        dex = DexProvider.for_puzzle(self.working_puzzle)
        self.first_passed, self.final_passed, self.total = _rewrite_failed_clues(
            self.working_puzzle,
            ctx.ai_client,
            ctx.generate_rewrite_rounds,
            multi_model=ctx.multi_model,
            dex=dex,
            verify_candidates=ctx.verify_candidates,
            runtime=ctx.runtime,
        )
        _restore_best_versions(self.working_puzzle)
        self.working_puzzle.assessment = score_puzzle_state(self.working_puzzle, self.candidate.report)
        self._progress("title", detail=f"verified={self.final_passed}/{self.total}")
        return self.working_puzzle.assessment

    def _title(self, ctx):
        assert self.working_puzzle is not None
        rendered_for_title = puzzle_from_working_state(self.working_puzzle)
        title_result = generate_title_for_final_puzzle_result(
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
            blocking_words=[clue.word_normalized for clue in _blocking_clues(self.working_puzzle)],
            assessment=copy.deepcopy(self.working_puzzle.assessment),
        )
        self.best_prepared = _better_prepared_puzzle(
            self.best_prepared,
            prepared,
            client=ctx.ai_client,
            runtime=ctx.runtime,
        )
        if self.best_prepared and _is_publishable(self.best_prepared):
            self._progress("publish", detail=f"title={self.best_prepared.title}")
            return self.best_prepared
        if self.attempt_index < self.effective_attempts:
            log("Rejected generated puzzle after quality gate: " + ", ".join(prepared.blocking_words[:10]))
            self._progress("fill_grid", detail=f"retry={self.attempt_index + 1}/{self.effective_attempts}")
            return prepared
        raise RuntimeError(
            f"Could not prepare a publishable {self.size}x{self.size} puzzle. "
            f"Missing definitions for: {', '.join(prepared.blocking_words[:12])}"
        )

    def _publish(self, ctx):
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
                models_used=[label for label in (ctx.runtime.current_model_label,) if label],
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
