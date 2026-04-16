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
from rebus_generator.workflows.generate.define import generate_definition_for_working_clue
from rebus_generator.workflows.generate.models import PreparedPuzzle
from rebus_generator.workflows.generate.prepare import (
    _backfill_generated_model,
    _blocking_clues,
    _choose_metadata_variants_for_puzzle,
    _inject_word_metadata,
    _preparation_attempts_for_size,
)
from rebus_generator.workflows.generate.publish import publish_prepared_puzzle
from rebus_generator.workflows.generate.quality_gate import (
    _better_prepared_puzzle,
    _describe_publishability_failure,
    _is_publishable,
)
from rebus_generator.workflows.retitle.generate import generate_title_for_final_puzzle_result
from rebus_generator.workflows.run_all.rewrite_units import RunAllRewriteSession
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
        self.dex_provider: DexProvider | None = None
        self.define_done_words: set[str] = set()
        self.first_passed = 0
        self.final_passed = 0
        self.total = 0
        self.best_prepared: PreparedPuzzle | None = None
        self.rewrite_session: RunAllRewriteSession | None = None

    def next_steps(self, ctx):
        return self.plan_ready_units(ctx)

    def plan_ready_units(self, ctx):
        if self.status != "active":
            return []
        if self.stage == "select_size":
            return [self._non_llm_step("select_size", "generate_select_size", self._select_size)]
        if self.stage == "fill_grid":
            return [self._background_step("fill_grid", "generate_fill_grid", self._fill_grid)]
        if self.stage == "define_initial":
            state = self._ensure_define_state()
            dex = self._ensure_dex_provider()
            pending = [
                clue
                for clue in state.horizontal_clues + state.vertical_clues
                if not clue.current.definition and clue.word_normalized not in self.define_done_words
            ]
            if pending:
                return [
                    self._llm_step(
                        f"define_initial:{clue.word_normalized}",
                        "generate_define_initial",
                        PRIMARY_MODEL.model_id,
                        lambda _ctx, clue=clue: generate_definition_for_working_clue(
                            clue,
                            _ctx.ai_client,
                            theme=self._ensure_define_state().title or "Rebus Românesc",
                            dex=dex,
                            model_config=PRIMARY_MODEL,
                        ),
                    )
                    for clue in pending
                ]
            return [self._non_llm_step("define_finalize", "generate_define_finalize", self._finalize_define_initial)]
        if self.stage in {"rewrite_initial_verify", "rewrite_initial_rate", "rewrite_prepare_round", "generate_candidates", "evaluate_verify", "evaluate_rate", "select_candidates", "finalize_round"}:
            return self._rewrite_units()
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
        self.dex_provider = DexProvider.for_puzzle(self.working_puzzle)
        self.define_done_words = set()
        self.rewrite_session = None
        self._progress("define_initial", detail=f"attempt={self.attempt_index}/{self.effective_attempts}")
        return None

    def _ensure_define_state(self):
        assert self.working_puzzle is not None
        if hasattr(self.working_puzzle, "horizontal_clues") and self.working_puzzle.horizontal_clues and hasattr(self.working_puzzle.horizontal_clues[0], "current"):
            return self.working_puzzle
        state = working_puzzle_from_puzzle(self.working_puzzle, split_compound=True)
        for clue in state.horizontal_clues + state.vertical_clues:
            word_meta = self.resolved_metadata.get(clue.word_normalized, {})
            clue.word_type = word_meta.get("word_type", "")
        self.working_puzzle = state
        return state

    def _ensure_dex_provider(self) -> DexProvider:
        state = self._ensure_define_state()
        if self.dex_provider is None:
            self.dex_provider = DexProvider.for_puzzle(state)
        return self.dex_provider

    def apply_unit_result(self, unit, result, ctx) -> None:
        if unit.purpose == "generate_define_initial":
            word = unit.step_id.split(":", 1)[1]
            state = self._ensure_define_state()
            for clue in state.horizontal_clues + state.vertical_clues:
                if clue.word_normalized != word:
                    continue
                clue.current.definition = str(result.value or "")
                clue.current.source = "generate"
                clue.current.generated_by = PRIMARY_MODEL.display_name
                if clue.best is None:
                    clue.best = copy.deepcopy(clue.current)
                self.define_done_words.add(word)
                break
            return
        if self.rewrite_session is not None:
            self._apply_rewrite_result(unit, result)

    def _finalize_define_initial(self, ctx):
        state = self._ensure_define_state()
        _backfill_generated_model(state, PRIMARY_MODEL.display_name)
        _inject_word_metadata(state, self.resolved_metadata)
        self.working_puzzle = state
        self.rewrite_session = RunAllRewriteSession(
            puzzle=self.working_puzzle,
            client=ctx.ai_client,
            rounds=ctx.generate_rewrite_rounds,
            theme=self.working_puzzle.title or "Puzzle intern",
            multi_model=ctx.multi_model,
            dex=self._ensure_dex_provider(),
            verify_candidates=ctx.verify_candidates,
            hybrid_deanchor=False,
            runtime=ctx.runtime,
        )
        self._progress("rewrite_initial_verify", detail=f"attempt={self.attempt_index}/{self.effective_attempts}")
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
        assert self.rewrite_session is not None
        self.rewrite_session.finalize_initial_verify()
        self._progress("rewrite_initial_rate", detail=f"round={self.rewrite_session.round_index}")
        return None

    def _rewrite_finalize_initial_rate(self, ctx):
        assert self.rewrite_session is not None
        self.rewrite_session.finalize_initial_rate()
        self._progress("rewrite_prepare_round", detail=f"round={self.rewrite_session.round_index}")
        return None

    def _rewrite_prepare_round(self, ctx):
        assert self.rewrite_session is not None
        self.rewrite_session.prepare_round()
        if self.rewrite_session.phase == "done":
            result = self.rewrite_session.finish()
            self.first_passed, self.final_passed, self.total = result.initial_passed, result.final_passed, result.total
            _restore_best_versions(self.working_puzzle)
            self.working_puzzle.assessment = score_puzzle_state(self.working_puzzle, self.candidate.report)
            self._progress("title", detail=f"verified={self.final_passed}/{self.total}")
            return result
        self._progress(self.rewrite_session.phase, detail=f"round={self.rewrite_session.round_index}")
        return None

    def _rewrite_finalize_generation(self, ctx):
        assert self.rewrite_session is not None
        self.rewrite_session.finalize_generation()
        self._progress(self.rewrite_session.phase, detail=f"round={self.rewrite_session.round_index}")
        return None

    def _rewrite_start_rate(self, ctx):
        self._progress("evaluate_rate", detail=f"round={self.rewrite_session.round_index}")
        return None

    def _rewrite_select_candidates(self, ctx):
        assert self.rewrite_session is not None
        self.rewrite_session.select_candidates()
        self._progress("finalize_round", detail=f"round={self.rewrite_session.round_index}")
        return None

    def _rewrite_finalize_round(self, ctx):
        assert self.rewrite_session is not None
        self.rewrite_session.finalize_round()
        self._progress("rewrite_prepare_round", detail=f"round={self.rewrite_session.round_index}")
        return None

    def _title(self, ctx):
        assert self.working_puzzle is not None
        rendered_for_title = puzzle_from_working_state(self.working_puzzle)
        title_result = generate_title_for_final_puzzle_result(
            rendered_for_title,
            client=ctx.ai_client,
            rate_client=ctx.ai_client,
            runtime=ctx.runtime,
            multi_model=False,
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
            log(
                "Rejected generated puzzle after quality gate: "
                + _describe_publishability_failure(prepared)
            )
            self._progress("fill_grid", detail=f"retry={self.attempt_index + 1}/{self.effective_attempts}")
            return prepared
        raise RuntimeError(
            f"Could not prepare a publishable {self.size}x{self.size} puzzle. "
            f"Quality gate failed: {_describe_publishability_failure(prepared)}"
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
