import unittest
import threading
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from rebus_generator.platform.llm.llm_client import _chat_completion_create, configure_run_llm_policy, llm_top_p, reset_run_llm_state
from rebus_generator.platform.llm.models import PRIMARY_MODEL, SECONDARY_MODEL
from rebus_generator.cli.run_all import _preflight, build_parser
from rebus_generator.domain.pipeline_state import ClueAssessment, PuzzleAssessment, WorkingClue, WorkingPuzzle
from rebus_generator.platform.io.markdown_io import ClueEntry, PuzzleData
from rebus_generator.workflows.run_all import (
    ClaimState,
    DeterministicFailureQuarantine,
    RunAllContext,
    RunAllSupervisor,
    StepState,
    SupervisorWorkItem,
)
from rebus_generator.workflows.run_all.jobs.base import JobState
from rebus_generator.workflows.run_all.jobs.generate import GenerateJobState
from rebus_generator.workflows.run_all.rewrite_units import RunAllRewriteSession
from rebus_generator.workflows.run_all.types import RunAllStallDetected, StableItemProgress, UnitResult


class _FakeRuntime:
    def __init__(self, current_model=None):
        self.primary = PRIMARY_MODEL
        self.secondary = SECONDARY_MODEL
        self.current_model = current_model
        self.switch_count = 0
        self.activation_count = 0
        self.unload_count = 0
        self.activation_seconds_total = 0.0
        self.unload_seconds_total = 0.0
        self.switch_callback = None

    @property
    def current_model_id(self):
        return self.current_model.model_id if self.current_model else ""

    @property
    def current_model_label(self):
        return self.current_model.display_name if self.current_model else ""

    def sync(self):
        return {}

    def activate(self, model, *, reason=""):
        previous = self.current_model.model_id if self.current_model else ""
        if not self.current_model or self.current_model.model_id != model.model_id:
            if self.current_model:
                self.switch_count += 1
            self.activation_count += 1
            self.current_model = model
            if previous and self.switch_callback is not None:
                self.switch_callback(previous, model.model_id, self, reason)
        return model

    def activate_primary(self, *, reason=""):
        return self.activate(PRIMARY_MODEL, reason=reason)

    def activate_secondary(self, *, reason=""):
        return self.activate(SECONDARY_MODEL, reason=reason)


def _context(runtime):
    return RunAllContext(
        supabase=object(),
        ai_client=object(),
        rate_client=object(),
        runtime=runtime,
        store=SimpleNamespace(),
        run_dir=Path("/tmp/run_all_test"),
        batch_output_root=Path("/tmp/run_all_batch"),
        words_path=Path("/tmp/words.json"),
        multi_model=True,
        dry_run=False,
        generate_rewrite_rounds=30,
        redefine_rounds=7,
        verify_candidates=3,
        simplify_batch_size=5,
    )


class _StaticJob(JobState):
    def __init__(self, item, *, steps=None, status="active", stage="ready"):
        super().__init__(item)
        self._steps = steps or []
        self.status = status
        self.stage = stage

    def next_steps(self, ctx):
        return list(self._steps) if self.status == "active" else []


def _item(topic: str, item_id: str, *, preferred_model_id=PRIMARY_MODEL.model_id, puzzle_id=None, words=None):
    return SupervisorWorkItem(
        item_id=item_id,
        topic=topic,
        task_kind=topic,
        preferred_model_id=preferred_model_id,
        target_models=(PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id),
        puzzle_id=puzzle_id,
        words=set(words or set()),
    )


def _model_step(item_id: str, topic: str, model_id: str) -> StepState:
    return StepState(
        step_id=f"{topic}:{model_id}",
        job_id=item_id,
        topic=topic,
        kind="gemma" if model_id == PRIMARY_MODEL.model_id else "eurollm",
        purpose="test_step",
        model_id=model_id,
        runner=lambda ctx: None,
        execution_mode="llm",
    )


def _run_planned_unit(job, unit, ctx) -> UnitResult:
    result = unit.execute(ctx)
    normalized = result if isinstance(result, UnitResult) else UnitResult(value=result)
    job.apply_unit_result(unit, normalized, ctx)
    return normalized


class RunAllSupervisorTests(unittest.TestCase):
    def setUp(self):
        reset_run_llm_state()

    def tearDown(self):
        reset_run_llm_state()

    def test_parser_accepts_topics_and_debug(self):
        args = build_parser().parse_args(["--topics", "retitle,redefine", "--debug"])

        self.assertEqual("retitle,redefine", args.topics)
        self.assertTrue(args.debug)

    def test_parser_accepts_llm_tuning_flags(self):
        args = build_parser().parse_args([
            "--llm-preflight",
            "--llm-stall-seconds",
            "600",
            "--llm-truncation-threshold",
            "4",
            "--gemma-verify-reasoning",
            "none",
        ])

        self.assertTrue(args.llm_preflight)
        self.assertEqual(600, args.llm_stall_seconds)
        self.assertEqual(4, args.llm_truncation_threshold)
        self.assertEqual("none", args.gemma_verify_reasoning)

    def test_supervisor_init_seeds_runtime_load_seconds_before_ledger_exists(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        runtime.activation_seconds_total = 12.5
        runtime.unload_seconds_total = 3.5

        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["generate"],
            topic_caps={"generate": 1},
        )

        self.assertEqual(16.0, supervisor.load_seconds_at_last_completion)

    def test_refill_starts_one_job_per_topic_slot(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["generate", "retitle"],
            topic_caps={"generate": 1, "retitle": 1},
        )
        generate_item = _item("generate", "generate:1", preferred_model_id=SECONDARY_MODEL.model_id)
        retitle_item = _item("retitle", "retitle:1")
        def build_job(item): return _StaticJob(item)

        with (
            patch.object(supervisor, "_poll_generate", return_value=generate_item),
            patch.object(supervisor, "_poll_retitle", return_value=retitle_item),
            patch.object(supervisor, "_build_job", side_effect=build_job),
        ):
            admitted = supervisor._refill_slots()

        self.assertEqual(2, admitted)
        self.assertEqual("generate:1", supervisor.slots["generate"].active_job.item_id)
        self.assertEqual("retitle:1", supervisor.slots["retitle"].active_job.item_id)

    def test_admission_frozen_when_both_models_have_runnable_steps(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["generate", "retitle"],
            topic_caps={"generate": 1, "retitle": 1},
        )
        primary_item = _item("retitle", "retitle:1")
        secondary_item = _item("generate", "generate:1", preferred_model_id=SECONDARY_MODEL.model_id)
        supervisor.slots["retitle"].active_job = _StaticJob(
            primary_item,
            steps=[_model_step("retitle:1", "retitle", PRIMARY_MODEL.model_id)],
        )
        supervisor.slots["generate"].active_job = _StaticJob(
            secondary_item,
            steps=[_model_step("generate:1", "generate", SECONDARY_MODEL.model_id)],
        )

        self.assertTrue(supervisor._admission_frozen())

    def test_refill_skips_when_frozen(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["generate", "redefine", "retitle"],
            topic_caps={"generate": 1, "redefine": 1, "retitle": 1},
        )
        supervisor.slots["generate"].active_job = _StaticJob(
            _item("generate", "generate:1", preferred_model_id=SECONDARY_MODEL.model_id),
            steps=[_model_step("generate:1", "generate", SECONDARY_MODEL.model_id)],
        )
        supervisor.slots["redefine"].active_job = _StaticJob(
            _item("redefine", "redefine:1"),
            steps=[_model_step("redefine:1", "redefine", PRIMARY_MODEL.model_id)],
        )

        with patch.object(supervisor, "_poll_retitle", return_value=_item("retitle", "retitle:1")) as poll_mock:
            admitted = supervisor._refill_slots()

        self.assertEqual(0, admitted)
        poll_mock.assert_not_called()

    def test_finalize_then_refill_starts_next_same_topic_job(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["retitle"],
            topic_caps={"retitle": 1},
        )
        finished = _StaticJob(_item("retitle", "retitle:done"), status="complete", stage="done")
        supervisor.slots["retitle"].active_job = finished
        pending = _item("retitle", "retitle:next")
        supervisor.pending_items.append(pending)

        with patch.object(supervisor, "_build_job", side_effect=lambda item: _StaticJob(item)):
            supervisor._finalize_finished_jobs()
            admitted = supervisor._refill_slots()

        self.assertEqual(1, admitted)
        self.assertEqual("retitle:next", supervisor.slots["retitle"].active_job.item_id)
        self.assertEqual(1, supervisor.completed)

    @patch("rebus_generator.workflows.run_all.scheduler.log")
    def test_admit_logs_targets_and_active_slots(self, log_mock):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["retitle"],
            topic_caps={"retitle": 1},
        )
        item = _item("retitle", "retitle:puzzle:1", puzzle_id="1", words={"APA"})

        supervisor._admit_item(item)

        message = log_mock.call_args.args[0]
        self.assertIn("topic=retitle", message)
        self.assertIn("targets=", message)
        self.assertIn("queues_model=", message)
        self.assertIn("active_slots=", message)

    @patch("rebus_generator.workflows.run_all.scheduler.log")
    def test_switch_callback_logs_queue_snapshot(self, log_mock):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["retitle"],
            topic_caps={"retitle": 1},
        )
        supervisor.slots["retitle"].active_job = _StaticJob(
            _item("retitle", "retitle:1", preferred_model_id=SECONDARY_MODEL.model_id),
            steps=[_model_step("retitle:1", "retitle", SECONDARY_MODEL.model_id)],
        )

        supervisor._on_model_switch(PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id, runtime, "test")

        message = log_mock.call_args.args[0]
        self.assertIn("[run_all switch]", message)
        self.assertIn("queues_model=", message)
        self.assertIn("active_slots=", message)

    @patch("rebus_generator.workflows.run_all.pollers.fetch_redefine_puzzles")
    def test_redefine_poll_skips_puzzle_if_word_owned_by_simplify(self, fetch_mock):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["redefine"],
            topic_caps={"redefine": 1},
        )
        supervisor.claims.simplify_words.add("APA")
        supervisor.ctx.store = SimpleNamespace(
            fetch_clue_rows=lambda puzzle_id, extra_fields=(): [{"word_normalized": "APA"}]
        )
        fetch_mock.return_value = [{"id": "p1"}]

        admitted = supervisor._poll_redefine()

        self.assertIsNone(admitted)
        self.assertEqual([], supervisor.pending_items)

    @patch("rebus_generator.workflows.run_all.pollers.build_candidate_pairs")
    def test_simplify_poll_skips_word_owned_by_active_puzzle(self, build_pairs_mock):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["simplify"],
            topic_caps={"simplify": 1},
        )
        supervisor.claims.topic_by_puzzle_id["p1"] = "redefine"
        supervisor.claims.puzzle_words["p1"] = {"APA"}
        supervisor.ctx.store = SimpleNamespace(fetch_active_canonical_variants=lambda: [SimpleNamespace(word_normalized="APA")])
        build_pairs_mock.return_value = [SimpleNamespace(word="APA", left_id="l", right_id="r")]

        admitted = supervisor._poll_simplify()

        self.assertIsNone(admitted)
        self.assertEqual([], supervisor.pending_items)

    @patch("rebus_generator.workflows.run_all.pollers.fetch_retitle_puzzles")
    def test_retitle_poll_skips_same_puzzle_claimed_elsewhere(self, fetch_mock):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["retitle"],
            topic_caps={"retitle": 1},
        )
        supervisor.claims.topic_by_puzzle_id["p1"] = "redefine"
        fetch_mock.return_value = [{"id": "p1", "title": "Titlu"}]

        admitted = supervisor._poll_retitle()

        self.assertIsNone(admitted)
        self.assertEqual([], supervisor.pending_items)

    @patch("rebus_generator.workflows.run_all.pollers.fetch_retitle_puzzles")
    def test_retitle_poll_deprioritizes_no_change_item_when_fresh_exists(self, fetch_mock):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["retitle"],
            topic_caps={"retitle": 1},
        )
        supervisor.stable_item_progress["retitle:puzzle:p1"] = StableItemProgress(
            topic="retitle",
            stable_key="retitle:puzzle:p1",
            no_progress_admissions=2,
        )
        supervisor.ctx.store = SimpleNamespace(
            fetch_clue_rows=lambda puzzle_id, extra_fields=(): [
                {"word_normalized": "APA" if puzzle_id == "p1" else "NOR"}
            ]
        )
        fetch_mock.return_value = [
            {"id": "p1", "title": "Titlu vechi", "created_at": "2026-04-01T00:00:00+00:00"},
            {"id": "p2", "title": "Titlu nou", "created_at": "2026-04-02T00:00:00+00:00"},
        ]

        admitted = supervisor._poll_retitle()

        self.assertIsNotNone(admitted)
        self.assertEqual("retitle:puzzle:p2", admitted.item_id)

    def test_system_exit_in_step_becomes_topic_failure_not_process_exit(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["simplify", "retitle"],
            topic_caps={"simplify": 1, "retitle": 1},
            retry_limit=0,
        )
        simplify_item = _item("simplify", "simplify:1")
        retitle_item = _item("retitle", "retitle:1")
        simplify_step = StepState(
            step_id="simplify:bad",
            job_id="simplify:1",
            topic="simplify",
            kind="non_llm",
            purpose="boom",
            model_id=None,
            runner=lambda ctx: (_ for _ in ()).throw(SystemExit("bad state")),
        )
        supervisor.slots["simplify"].active_job = _StaticJob(simplify_item, steps=[simplify_step])
        supervisor.slots["retitle"].active_job = _StaticJob(retitle_item)

        ran = supervisor._run_ready_steps()
        supervisor._finalize_finished_jobs()

        self.assertTrue(ran)
        self.assertEqual(1, supervisor.failed)
        self.assertIsNone(supervisor.slots["simplify"].active_job)
        self.assertIsNotNone(supervisor.slots["retitle"].active_job)

    def test_keyboard_interrupt_propagates(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["simplify"],
            topic_caps={"simplify": 1},
            retry_limit=0,
        )
        interrupt_step = StepState(
            step_id="simplify:interrupt",
            job_id="simplify:1",
            topic="simplify",
            kind="non_llm",
            purpose="interrupt",
            model_id=None,
            runner=lambda ctx: (_ for _ in ()).throw(KeyboardInterrupt()),
        )
        supervisor.slots["simplify"].active_job = _StaticJob(_item("simplify", "simplify:1"), steps=[interrupt_step])

        with self.assertRaises(KeyboardInterrupt):
            supervisor._run_ready_steps()

    def test_identical_failures_quarantine_and_stop_run(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["generate"],
            topic_caps={"generate": 1},
            retry_limit=5,
        )
        failure_step = StepState(
            step_id="rewrite_evaluate",
            job_id="generate:size:13:1",
            topic="generate",
            kind="gemma",
            purpose="generate_rewrite_evaluate",
            model_id=PRIMARY_MODEL.model_id,
            runner=lambda ctx: (_ for _ in ()).throw(KeyError(PRIMARY_MODEL.model_id)),
            execution_mode="llm",
        )
        item = _item("generate", "generate:size:13:1", preferred_model_id=SECONDARY_MODEL.model_id)
        item.payload = {"size": 13, "index": 1}
        supervisor.slots["generate"].active_job = _StaticJob(item, steps=[failure_step])

        supervisor._run_ready_steps()
        supervisor.slots["generate"].active_job.available_after = 0
        supervisor._run_ready_steps()
        supervisor.slots["generate"].active_job.available_after = 0

        with self.assertRaises(DeterministicFailureQuarantine):
            supervisor._run_ready_steps()

        self.assertIn(13, supervisor.generate_size_penalty_map())
        self.assertIn(13, supervisor.active_generate_size_exclusions())

    def test_generate_fill_grid_unsat_size_quarantines_size_without_stopping_run(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["generate"],
            topic_caps={"generate": 1},
            retry_limit=5,
        )
        failure_step = StepState(
            step_id="fill_grid",
            job_id="generate:size:14:1",
            topic="generate",
            kind="non_llm",
            purpose="generate_fill_grid",
            model_id=None,
            runner=lambda ctx: (_ for _ in ()).throw(
                RuntimeError(
                    "Rust phase-1 failed for 14x14 with exit 1: black_step 0 size=14 "
                    "target_blacks=35 attempt_budget=126\n"
                    "black_step 0 solved_candidates=0 best_avg_len=0\n"
                    "could not generate a valid filled grid for 14x14"
                )
            ),
            execution_mode="inline_non_llm",
        )
        item = _item("generate", "generate:size:14:1", preferred_model_id=SECONDARY_MODEL.model_id)
        item.payload = {"size": 14, "index": 1}
        supervisor.slots["generate"].active_job = _StaticJob(item, steps=[failure_step], stage="fill_grid")

        for _ in range(3):
            supervisor._run_ready_steps()
            if supervisor.slots["generate"].active_job is not None:
                supervisor.slots["generate"].active_job.available_after = 0

        supervisor._finalize_finished_jobs()

        self.assertEqual(1, supervisor.failed)
        self.assertIsNone(supervisor.slots["generate"].active_job)
        self.assertIn(14, supervisor.generate_size_penalty_map())
        self.assertIn(14, supervisor.active_generate_size_exclusions())

    def test_generate_rewrite_prepare_dead_end_quarantines_size_without_stopping_run(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["generate"],
            topic_caps={"generate": 1},
            retry_limit=5,
        )
        failure_step = StepState(
            step_id="rewrite_prepare_round",
            job_id="generate:size:14:1",
            topic="generate",
            kind="non_llm",
            purpose="rewrite_prepare_round",
            model_id=None,
            runner=lambda ctx: (_ for _ in ()).throw(
                RuntimeError(
                    "Could not prepare a publishable 14x14 puzzle. "
                    "Quality gate failed: missing definitions: IT; "
                    "incomplete pair evaluation: verify=0, rate=19 "
                    "(GLODAS, SES, GATER)"
                )
            ),
            execution_mode="inline_non_llm",
        )
        item = _item("generate", "generate:size:14:1", preferred_model_id=SECONDARY_MODEL.model_id)
        item.payload = {"size": 14, "index": 1}
        supervisor.slots["generate"].active_job = _StaticJob(
            item,
            steps=[failure_step],
            stage="rewrite_prepare_round",
        )

        for _ in range(3):
            supervisor._run_ready_steps()
            if supervisor.slots["generate"].active_job is not None:
                supervisor.slots["generate"].active_job.available_after = 0

        supervisor._finalize_finished_jobs()

        self.assertEqual(1, supervisor.failed)
        self.assertIsNone(supervisor.slots["generate"].active_job)
        self.assertIn(14, supervisor.generate_size_penalty_map())
        self.assertIn(14, supervisor.active_generate_size_exclusions())

    def test_stall_detection_stops_when_switch_churn_grows_without_completion(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["retitle"],
            topic_caps={"retitle": 1},
        )
        supervisor.ctx.llm_stall_seconds = 60
        supervisor.last_completion_at -= 120
        supervisor.last_progress_at -= 120
        runtime.switch_count = 4

        with self.assertRaises(RunAllStallDetected):
            supervisor._maybe_raise_stall()

    def test_recent_stage_progress_suppresses_stall(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["retitle"],
            topic_caps={"retitle": 1},
        )
        supervisor.ctx.llm_stall_seconds = 60
        supervisor.last_completion_at -= 3600
        supervisor.last_progress_at = supervisor.started_at
        runtime.switch_count = 10
        runtime.activation_seconds_total = 70.0
        supervisor._note_progress("stage:retitle:rerank")

        supervisor._maybe_raise_stall()

    @patch("rebus_generator.workflows.run_all.pollers.fetch_redefine_puzzles")
    def test_redefine_poll_deprioritizes_repeated_no_progress_item_when_fresh_exists(self, fetch_mock):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["redefine"],
            topic_caps={"redefine": 1},
        )
        supervisor.stable_item_progress["redefine:puzzle:p1"] = StableItemProgress(
            topic="redefine",
            stable_key="redefine:puzzle:p1",
            no_progress_admissions=2,
        )
        supervisor.ctx.store = SimpleNamespace(
            fetch_clue_rows=lambda puzzle_id, extra_fields=(): [
                {"word_normalized": "APA" if puzzle_id == "p1" else "NOR"}
            ]
        )
        fetch_mock.return_value = [{"id": "p1"}, {"id": "p2"}]

        admitted = supervisor._poll_redefine()

        self.assertIsNotNone(admitted)
        self.assertEqual("redefine:puzzle:p2", admitted.item_id)

    def test_simplify_job_ignores_stale_global_state_file(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["simplify"],
            topic_caps={"simplify": 1},
            retry_limit=0,
        )
        supervisor.ctx.store = SimpleNamespace()
        item = _item("simplify", "simplify:word:APA", words={"APA"})
        item.payload = {"word": "APA"}
        supervisor.slots["simplify"].active_job = supervisor._build_job(item)
        state_path = Path("build/clue_canon/simplify_state.json")
        previous = state_path.read_text(encoding="utf-8") if state_path.exists() else None
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text('{"word":"ALTCEVA"}', encoding="utf-8")
        pair = SimpleNamespace(
            key="l::r",
            word="APA",
            word_type="",
            usage_label="",
            left_id="l",
            right_id="r",
            left_definition="stanga",
            right_definition="dreapta",
        )
        row_left = SimpleNamespace(id="l", definition="stanga", word_normalized="APA")
        row_right = SimpleNamespace(id="r", definition="dreapta", word_normalized="APA")
        vote = SimpleNamespace(vote=SimpleNamespace(same_meaning=True), parse_status="ok")
        try:
            with (
                patch("rebus_generator.workflows.run_all.jobs.simplify.load_simplify_bucket", return_value=({("APA", "", ""): [row_left, row_right]}, [pair])),
                patch("rebus_generator.workflows.run_all.jobs.simplify.compare_definition_variants_attempt", return_value=vote),
                patch("rebus_generator.workflows.run_all.jobs.simplify.find_simplify_pair_rows", return_value=(row_left, row_right)),
                patch("rebus_generator.workflows.run_all.jobs.simplify.should_rewrite_survivor", return_value=False),
                patch("rebus_generator.workflows.run_all.jobs.simplify.choose_existing_survivor", return_value=SimpleNamespace(definition="stanga")),
                patch("rebus_generator.workflows.run_all.jobs.simplify.apply_simplify_merge", return_value="survivor"),
                patch("rebus_generator.workflows.run_all.jobs.simplify.refresh_simplify_bucket_rows"),
            ):
                while supervisor.slots["simplify"].active_job is not None:
                    supervisor._run_ready_steps()
                    supervisor._finalize_finished_jobs()
        finally:
            if previous is None:
                state_path.unlink(missing_ok=True)
            else:
                state_path.write_text(previous, encoding="utf-8")

        self.assertEqual(1, supervisor.completed)
        self.assertEqual(0, supervisor.failed)

    def test_worker_lane_can_overlap_with_llm_lane(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["generate", "retitle"],
            topic_caps={"generate": 1, "retitle": 1},
        )
        self.addCleanup(supervisor.close)
        started = threading.Event()
        release = threading.Event()
        llm_calls: list[str] = []

        def _worker(_ctx):
            started.set()
            release.wait(1)

        generate_step = StepState(
            step_id="fill_grid",
            job_id="generate:1",
            topic="generate",
            kind="non_llm",
            purpose="generate_fill_grid",
            model_id=None,
            runner=_worker,
            execution_mode="background_non_llm",
        )
        retitle_step = StepState(
            step_id="generate_primary",
            job_id="retitle:1",
            topic="retitle",
            kind="gemma",
            purpose="retitle_generate_primary",
            model_id=PRIMARY_MODEL.model_id,
            runner=lambda _ctx: llm_calls.append("retitle"),
            execution_mode="llm",
        )
        supervisor.slots["generate"].active_job = _StaticJob(_item("generate", "generate:1"), steps=[generate_step])
        supervisor.slots["retitle"].active_job = _StaticJob(_item("retitle", "retitle:1"), steps=[retitle_step])

        try:
            ran = supervisor._run_ready_steps()
            self.assertTrue(ran)
            self.assertTrue(started.is_set())
            self.assertEqual(["retitle"], llm_calls)
            self.assertIsNotNone(supervisor.worker_task)
        finally:
            release.set()
            supervisor._poll_worker_task()

    def test_drain_does_not_rerun_same_unchanged_unit_forever(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["retitle"],
            topic_caps={"retitle": 1},
        )
        calls: list[str] = []
        step = StepState(
            step_id="generate_primary",
            job_id="retitle:1",
            topic="retitle",
            kind="gemma",
            purpose="retitle_generate_primary",
            model_id=PRIMARY_MODEL.model_id,
            runner=lambda _ctx: calls.append("once"),
            execution_mode="llm",
        )
        supervisor.slots["retitle"].active_job = _StaticJob(_item("retitle", "retitle:1"), steps=[step])

        ran = supervisor._run_ready_steps()

        self.assertTrue(ran)
        self.assertEqual(["once"], calls)

    def test_run_ready_steps_drains_loaded_model_before_switching(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["generate", "retitle", "simplify"],
            topic_caps={"generate": 1, "retitle": 1, "simplify": 1},
        )
        calls: list[str] = []
        generate_job = _StaticJob(
            _item("generate", "generate:1"),
            steps=[StepState(
                step_id="generate:gemma",
                job_id="generate:1",
                topic="generate",
                kind="gemma",
                purpose="generate_define",
                model_id=PRIMARY_MODEL.model_id,
                runner=lambda _ctx: (calls.append("generate"), setattr(generate_job, "status", "complete")),
                execution_mode="llm",
            )],
        )
        retitle_job = _StaticJob(
            _item("retitle", "retitle:1"),
            steps=[StepState(
                step_id="retitle:gemma",
                job_id="retitle:1",
                topic="retitle",
                kind="gemma",
                purpose="retitle_generate",
                model_id=PRIMARY_MODEL.model_id,
                runner=lambda _ctx: (calls.append("retitle"), setattr(retitle_job, "status", "complete")),
                execution_mode="llm",
            )],
        )
        simplify_job = _StaticJob(
            _item("simplify", "simplify:1"),
            steps=[StepState(
                step_id="simplify:eurollm",
                job_id="simplify:1",
                topic="simplify",
                kind="eurollm",
                purpose="simplify_compare",
                model_id=SECONDARY_MODEL.model_id,
                runner=lambda _ctx: (calls.append("simplify"), setattr(simplify_job, "status", "complete")),
                execution_mode="llm",
            )],
        )
        supervisor.slots["generate"].active_job = generate_job
        supervisor.slots["retitle"].active_job = retitle_job
        supervisor.slots["simplify"].active_job = simplify_job

        supervisor._run_ready_steps()

        self.assertEqual(["generate", "retitle", "simplify"], calls)
        self.assertEqual(SECONDARY_MODEL.model_id, runtime.current_model_id)
        self.assertEqual(1, runtime.switch_count)
        self.assertEqual(1, supervisor.loaded_model_drain_switches)

    def test_switch_counts_only_after_loaded_model_drains(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["simplify"],
            topic_caps={"simplify": 1},
        )
        job = _StaticJob(
            _item("simplify", "simplify:1"),
            steps=[StepState(
                step_id="simplify:eurollm",
                job_id="simplify:1",
                topic="simplify",
                kind="eurollm",
                purpose="simplify_compare",
                model_id=SECONDARY_MODEL.model_id,
                runner=lambda _ctx: setattr(job, "status", "complete"),
                execution_mode="llm",
            )],
        )
        supervisor.slots["simplify"].active_job = job

        supervisor._run_ready_steps()

        self.assertEqual(1, runtime.switch_count)
        self.assertEqual(1, supervisor.loaded_model_drain_switches)

    def test_choose_model_prefers_current_if_it_has_work(self):
        runtime = _FakeRuntime(current_model=SECONDARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["generate", "simplify"],
            topic_caps={"generate": 1, "simplify": 1},
        )
        units = [
            StepState(step_id="s1", job_id="j1", topic="simplify", kind="k", purpose="p", model_id=SECONDARY_MODEL.model_id, runner=lambda x: x),
            StepState(step_id="g1", job_id="j2", topic="generate", kind="k", purpose="p", model_id=PRIMARY_MODEL.model_id, runner=lambda x: x),
        ]
        
        chosen = supervisor._choose_model_for_units(units)
        
        # Should stay on SECONDARY because it has a ready unit
        self.assertEqual(SECONDARY_MODEL.model_id, chosen)

    def test_choose_model_prefers_primary_if_current_has_no_work(self):
        runtime = _FakeRuntime(current_model=SECONDARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["generate"],
            topic_caps={"generate": 1},
        )
        units = [
            StepState(step_id="g1", job_id="j1", topic="generate", kind="k", purpose="p", model_id=PRIMARY_MODEL.model_id, runner=lambda x: x),
        ]
        
        chosen = supervisor._choose_model_for_units(units)
        
        # Should switch to PRIMARY because SECONDARY has no ready units
        self.assertEqual(PRIMARY_MODEL.model_id, chosen)

    def test_generate_fill_grid_runs_on_worker_lane(self):
        item = _item("generate", "generate:1", preferred_model_id=SECONDARY_MODEL.model_id)
        item.payload = {"size": 13, "index": 1}
        job = RunAllSupervisor(
            context=_context(_FakeRuntime(current_model=PRIMARY_MODEL)),
            topics=["generate"],
            topic_caps={"generate": 1},
        )._build_job(item)

        job.stage = "fill_grid"
        step = job.next_steps(_context(_FakeRuntime(current_model=PRIMARY_MODEL)))[0]

        self.assertEqual("background_non_llm", step.execution_mode)
        self.assertEqual("fill_grid", step.step_id)

    @patch("rebus_generator.workflows.run_all.jobs.generate.RunAllRewriteSession", return_value=SimpleNamespace())
    @patch("rebus_generator.workflows.run_all.jobs.generate.DexProvider.for_puzzle", return_value=SimpleNamespace())
    @patch("rebus_generator.workflows.run_all.generate_attempt.apply_scored_canonical_fallbacks")
    @patch("rebus_generator.workflows.run_all.jobs.generate.generate_definition_for_working_clue", return_value="Gaz din atmosferă")
    def test_generate_define_initial_injects_metadata_into_working_state(self, _mock_define, _mock_apply, _mock_dex, _mock_session):
        item = _item("generate", "generate:size:13:1", preferred_model_id=SECONDARY_MODEL.model_id)
        item.payload = {"size": 13, "index": 1}
        job = GenerateJobState(item)
        job.stage = "define_initial"
        job.attempt_index = 1
        job.effective_attempts = 3
        job.working_puzzle = PuzzleData(
            title="",
            size=3,
            grid=[["A", "E", "R"]],
            horizontal_clues=[ClueEntry(1, "AER", "", "")],
            vertical_clues=[],
        )
        job.resolved_metadata = {
            "AER": {"normalized": "AER", "original": "aer", "word_type": "N"}
        }
        ctx = _context(_FakeRuntime(current_model=PRIMARY_MODEL))

        define_units = job.plan_ready_units(ctx)
        self.assertEqual(1, len(define_units))
        self.assertEqual("generate_define_initial", define_units[0].purpose)
        self.assertEqual(PRIMARY_MODEL.model_id, define_units[0].model_id)
        self.assertEqual("define_initial", define_units[0].phase)
        _run_planned_unit(job, define_units[0], ctx)

        finalize_units = job.plan_ready_units(ctx)
        self.assertEqual(1, len(finalize_units))
        self.assertEqual("generate_define_finalize", finalize_units[0].purpose)
        _run_planned_unit(job, finalize_units[0], ctx)

        self.assertEqual("rewrite_initial_verify", job.stage)
        self.assertEqual("N", job.working_puzzle.horizontal_clues[0].word_type)
        self.assertEqual("aer", job.working_puzzle.horizontal_clues[0].word_original)
        self.assertEqual(PRIMARY_MODEL.display_name, job.working_puzzle.horizontal_clues[0].current.generated_by)
        self.assertEqual(
            "aer",
            job.working_puzzle.metadata["resolved_word_metadata"]["AER"]["original"],
        )

    @patch("rebus_generator.workflows.run_all.jobs.generate.generate_definition_for_working_clue")
    def test_generate_define_initial_tracks_duplicate_words_by_clue_ref(self, mock_define):
        item = _item("generate", "generate:size:14:1", preferred_model_id=PRIMARY_MODEL.model_id)
        item.payload = {"size": 14, "index": 1}
        job = GenerateJobState(item)
        job.stage = "define_initial"
        job.attempt_index = 1
        job.effective_attempts = 3
        first = WorkingClue(row_number=1, word_normalized="IT", word_original="it")
        second = WorkingClue(row_number=2, word_normalized="IT", word_original="it")
        job.working_puzzle = WorkingPuzzle("", 14, [], [first, second], [])
        job.dex_provider = SimpleNamespace(get=lambda *_args, **_kwargs: "")
        mock_define.side_effect = ["Definiție pentru primul IT.", "Definiție pentru al doilea IT."]
        ctx = _context(_FakeRuntime(current_model=PRIMARY_MODEL))

        define_units = job.plan_ready_units(ctx)
        self.assertEqual(2, len(define_units))
        self.assertNotEqual(define_units[0].step_id, define_units[1].step_id)
        for unit in define_units:
            _run_planned_unit(job, unit, ctx)

        self.assertEqual("Definiție pentru primul IT.", first.current.definition)
        self.assertEqual("Definiție pentru al doilea IT.", second.current.definition)
        self.assertEqual(2, len(job.define_done_refs))
        self.assertEqual("generate_define_finalize", job.plan_ready_units(ctx)[0].purpose)

    def test_redefine_job_splits_baseline_into_verify_rate_finalize(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["redefine"],
            topic_caps={"redefine": 1},
        )
        item = _item("redefine", "redefine:puzzle:p1", puzzle_id="p1")
        item.payload = {"puzzle_row": {"id": "p1", "title": "Titlu"}}
        job = supervisor._build_job(item)
        baseline_clue = SimpleNamespace(word_normalized="APA")
        baseline_puzzle = SimpleNamespace(
            title="Titlu",
            horizontal_clues=[baseline_clue],
            vertical_clues=[],
            assessment=None,
        )
        candidate_puzzle = SimpleNamespace(
            title="Titlu",
            horizontal_clues=[],
            vertical_clues=[],
            assessment=SimpleNamespace(min_rebus=0, avg_rebus=0.0, verified_count=0, total_clues=0),
        )
        with (
            patch("rebus_generator.workflows.run_all.jobs.redefine.fetch_redefine_clues", return_value=[{"id": "c1"}]),
            patch("rebus_generator.workflows.run_all.jobs.redefine.build_working_puzzle", side_effect=[baseline_puzzle, candidate_puzzle]),
            patch("rebus_generator.workflows.run_all.jobs.redefine.verify_clue_with_model", return_value=["APA"]),
            patch("rebus_generator.workflows.run_all.jobs.redefine.rate_clue_with_model", return_value=SimpleNamespace(score=8)),
            patch("rebus_generator.workflows.run_all.jobs.redefine.DexProvider.for_puzzle", return_value=SimpleNamespace()),
            patch("rebus_generator.workflows.run_all.jobs.redefine.RunAllRewriteSession", return_value=SimpleNamespace()),
            patch("rebus_generator.workflows.run_all.jobs.redefine._finalize_pair_verification", return_value=baseline_puzzle.horizontal_clues),
            patch("rebus_generator.workflows.run_all.jobs.redefine._finalize_pair_rating"),
            patch("rebus_generator.workflows.run_all.jobs.redefine.score_puzzle_state", return_value=SimpleNamespace(min_rebus=1, avg_rebus=2.0, verified_count=1, total_clues=1)),
        ):
            ctx = _context(runtime)
            _run_planned_unit(job, job.plan_ready_units(ctx)[0], ctx)
            self.assertEqual("baseline_verify", job.stage)
            first_verify = job.plan_ready_units(ctx)
            self.assertEqual("redefine_baseline_verify", first_verify[0].purpose)
            self.assertEqual(PRIMARY_MODEL.model_id, first_verify[0].model_id)
            _run_planned_unit(job, first_verify[0], ctx)
            second_verify = job.plan_ready_units(ctx)
            self.assertEqual(SECONDARY_MODEL.model_id, second_verify[0].model_id)
            _run_planned_unit(job, second_verify[0], ctx)
            _run_planned_unit(job, job.plan_ready_units(ctx)[0], ctx)
            self.assertEqual("baseline_rate", job.stage)
            first_rate = job.plan_ready_units(ctx)
            self.assertEqual("redefine_baseline_rate", first_rate[0].purpose)
            self.assertEqual(PRIMARY_MODEL.model_id, first_rate[0].model_id)
            _run_planned_unit(job, first_rate[0], ctx)
            second_rate = job.plan_ready_units(ctx)
            self.assertEqual(SECONDARY_MODEL.model_id, second_rate[0].model_id)
            _run_planned_unit(job, second_rate[0], ctx)
            _run_planned_unit(job, job.plan_ready_units(ctx)[0], ctx)
            self.assertEqual("baseline_finalize", job.stage)
            _run_planned_unit(job, job.plan_ready_units(ctx)[0], ctx)
            self.assertEqual("rewrite_initial_verify", job.stage)

    def test_redefine_job_yields_after_bounded_rewrite_round_and_resumes_same_session(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["redefine"],
            topic_caps={"redefine": 1},
        )
        item = _item("redefine", "redefine:puzzle:p1", puzzle_id="p1")
        item.payload = {"puzzle_row": {"id": "p1", "title": "Titlu"}}
        job = supervisor._build_job(item)
        ctx = _context(runtime)

        class _FakeRewriteSession:
            def __init__(self):
                self.round_index = 1
                self.phase = "prepare_round"
                self.generated = []
                self.verified = []
                self.rated = []

            def prepare_round(self):
                self.phase = "generate_candidates"

            def generation_units(self, factory):
                if self.generated:
                    return []
                return [
                    factory(
                        "rewrite_generate:gemma:APA:rewrite",
                        "rewrite_generate_candidate",
                        PRIMARY_MODEL.model_id,
                        "generate_candidates",
                        lambda _ctx: {
                            "word": "APA",
                            "strategy": "rewrite",
                            "source": "rewrite",
                            "definition": "Definiție nouă",
                        },
                        coalesce_key="rewrite_generate_candidate:gemma",
                    )
                ]

            def apply_generation_result(self, payload):
                self.generated.append(payload)

            def finalize_generation(self):
                self.phase = "evaluate_verify"

            def evaluation_verify_units(self, factory):
                if self.verified:
                    return []
                return [
                    factory(
                        "rewrite_eval_verify:gemma:APA:1",
                        "rewrite_evaluate_candidate_verify",
                        PRIMARY_MODEL.model_id,
                        "evaluate_verify",
                        lambda _ctx: {
                            "word": "APA",
                            "definition": "Definiție nouă",
                            "model_id": PRIMARY_MODEL.model_id,
                            "verify_votes": ["APA"],
                            "verify_vote_source": "ok",
                        },
                        coalesce_key="rewrite_evaluate_candidate_verify:gemma",
                    )
                ]

            def apply_candidate_verify_result(self, payload):
                self.verified.append(payload)

            def evaluation_rate_units(self, factory):
                if self.rated:
                    return []
                return [
                    factory(
                        "rewrite_eval_rate:gemma:APA:1",
                        "rewrite_evaluate_candidate_rate",
                        PRIMARY_MODEL.model_id,
                        "evaluate_rate",
                        lambda _ctx: {
                            "word": "APA",
                            "definition": "Definiție nouă",
                            "model_id": PRIMARY_MODEL.model_id,
                            "rating": SimpleNamespace(score=8),
                            "rating_vote_source": "ok",
                        },
                        coalesce_key="rewrite_evaluate_candidate_rate:gemma",
                    )
                ]

            def apply_candidate_rate_result(self, payload):
                self.rated.append(payload)

            def select_candidates(self):
                self.phase = "finalize_round"

            def finalize_round(self):
                self.round_index = 2
                self.phase = "prepare_round"

        rewrite_session = _FakeRewriteSession()
        job.rewrite_session = rewrite_session
        job.candidate_puzzle = SimpleNamespace(assessment=None)
        job.stage = "rewrite_prepare_round"

        prepare_unit = job.plan_ready_units(ctx)[0]
        self.assertEqual("rewrite_prepare_round", prepare_unit.purpose)
        _run_planned_unit(job, prepare_unit, ctx)
        self.assertEqual("generate_candidates", job.stage)

        generation_unit = job.plan_ready_units(ctx)[0]
        self.assertEqual("rewrite_generate_candidate", generation_unit.purpose)
        self.assertEqual("rewrite_generate_candidate:gemma", generation_unit.coalesce_key)
        _run_planned_unit(job, generation_unit, ctx)
        _run_planned_unit(job, job.plan_ready_units(ctx)[0], ctx)
        self.assertEqual("evaluate_verify", job.stage)

        verify_unit = job.plan_ready_units(ctx)[0]
        self.assertEqual("rewrite_evaluate_candidate_verify", verify_unit.purpose)
        _run_planned_unit(job, verify_unit, ctx)
        _run_planned_unit(job, job.plan_ready_units(ctx)[0], ctx)
        self.assertEqual("evaluate_rate", job.stage)

        rate_unit = job.plan_ready_units(ctx)[0]
        self.assertEqual("rewrite_evaluate_candidate_rate", rate_unit.purpose)
        _run_planned_unit(job, rate_unit, ctx)
        _run_planned_unit(job, job.plan_ready_units(ctx)[0], ctx)
        self.assertEqual("finalize_round", job.stage)

        _run_planned_unit(job, job.plan_ready_units(ctx)[0], ctx)

        self.assertIs(job.rewrite_session, rewrite_session)
        self.assertEqual(2, job.rewrite_session.round_index)
        self.assertEqual("rewrite_prepare_round", job.stage)

    def test_redefine_job_applies_scored_canonical_fallback_before_scoring(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["redefine"],
            topic_caps={"redefine": 1},
        )
        item = _item("redefine", "redefine:puzzle:p1", puzzle_id="p1")
        item.payload = {"puzzle_row": {"id": "p1", "title": "Titlu"}}
        job = supervisor._build_job(item)
        ctx = _context(runtime)
        calls: list[str] = []

        class _DoneRewriteSession:
            round_index = 2
            phase = "done"

            def prepare_round(self):
                self.phase = "done"

            def finish(self):
                calls.append("finish")
                return SimpleNamespace(initial_passed=0, final_passed=0, total=1)

        job.rewrite_session = _DoneRewriteSession()
        job.puzzle_row = {"id": "p1", "title": "Titlu"}
        job.baseline_puzzle = SimpleNamespace()
        job.candidate_puzzle = SimpleNamespace(assessment=None)
        job.stage = "rewrite_prepare_round"

        with (
            patch("rebus_generator.workflows.run_all.jobs.redefine.apply_scored_canonical_fallbacks", side_effect=lambda *args, **kwargs: calls.append("fallback") or {}),
            patch("rebus_generator.workflows.run_all.jobs.redefine.score_puzzle_state", side_effect=lambda puzzle: calls.append("score") or SimpleNamespace(min_rebus=1, avg_rebus=1.0, verified_count=0, total_clues=1)),
        ):
            _run_planned_unit(job, job.plan_ready_units(ctx)[0], ctx)

        self.assertEqual(["finish", "fallback", "score"], calls)
        self.assertEqual("resolve_canonicals", job.stage)

    def test_generate_job_skips_title_when_puzzle_is_still_unresolved_after_fallback(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        ctx = _context(runtime)
        item = _item("generate", "generate:size:14:1", preferred_model_id=PRIMARY_MODEL.model_id)
        item.payload = {"size": 14, "index": 1}
        job = GenerateJobState(item)
        job.stage = "rewrite_prepare_round"
        job.attempt_index = 1
        job.effective_attempts = 3
        job.candidate = SimpleNamespace(report=None)
        clue = WorkingClue(row_number=1, word_normalized="AN", word_original="an")
        clue.current.definition = "[NECLAR]"
        clue.current.assessment = ClueAssessment(verified=False)
        job.working_puzzle = WorkingPuzzle("", 14, [], [clue], [])
        calls: list[str] = []

        class _DoneRewriteSession:
            round_index = 1
            phase = "done"

            def prepare_round(self):
                self.phase = "done"

            def finish(self):
                calls.append("finish")
                return SimpleNamespace(initial_passed=0, final_passed=0, total=1)

        job.rewrite_session = _DoneRewriteSession()

        with (
            patch("rebus_generator.workflows.run_all.generate_attempt.apply_scored_canonical_fallbacks", side_effect=lambda **kwargs: calls.append("fallback") or {}),
            patch(
                "rebus_generator.workflows.run_all.generate_attempt.score_puzzle_state",
                side_effect=lambda *_args, **_kwargs: calls.append("score") or PuzzleAssessment(
                    definition_score=0.0,
                    verified_count=0,
                    total_clues=1,
                    scores_complete=False,
                    verify_incomplete_count=1,
                    rating_incomplete_count=0,
                    incomplete_words=["AN"],
                ),
            ),
            patch("rebus_generator.workflows.run_all.jobs.generate.generate_title_for_final_puzzle_result") as title_mock,
        ):
            _run_planned_unit(job, job.plan_ready_units(ctx)[0], ctx)

        self.assertEqual(["finish", "fallback", "score"], calls)
        title_mock.assert_not_called()
        self.assertEqual("fill_grid", job.stage)

    def test_generate_job_advances_to_title_after_placeholder_is_repaired(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        ctx = _context(runtime)
        item = _item("generate", "generate:size:14:1", preferred_model_id=PRIMARY_MODEL.model_id)
        item.payload = {"size": 14, "index": 1}
        job = GenerateJobState(item)
        job.stage = "rewrite_prepare_round"
        job.attempt_index = 1
        job.effective_attempts = 3
        job.candidate = SimpleNamespace(report=None)
        clue = WorkingClue(row_number=1, word_normalized="CAR", word_original="car")
        clue.current.definition = "[Definiție negenerată]"
        clue.current.assessment = ClueAssessment(verified=False)
        job.working_puzzle = WorkingPuzzle("", 14, [], [clue], [])
        calls: list[str] = []

        class _DoneRewriteSession:
            round_index = 1
            phase = "done"

            def prepare_round(self):
                self.phase = "done"

            def finish(self):
                calls.append("finish")
                return SimpleNamespace(initial_passed=0, final_passed=1, total=1)

        def _apply_fallback(**_kwargs):
            calls.append("fallback")
            clue.current.definition = "Vehicul cu patru roți pentru transport."
            clue.current.assessment = ClueAssessment(
                verified=True,
                verify_complete=True,
                rating_complete=True,
                verify_candidates=["CAR"],
                scores=SimpleNamespace(
                    semantic_exactness=9,
                    answer_targeting=8,
                    ambiguity_risk=3,
                    family_leakage=False,
                    language_integrity=10,
                    creativity=6,
                    rebus_score=8,
                ),
            )
            clue.best = clue.current
            return {("H", 0, 0): "canon-1"}

        job.rewrite_session = _DoneRewriteSession()

        with (
            patch("rebus_generator.workflows.run_all.generate_attempt.apply_scored_canonical_fallbacks", side_effect=_apply_fallback),
            patch(
                "rebus_generator.workflows.run_all.generate_attempt.score_puzzle_state",
                side_effect=lambda *_args, **_kwargs: calls.append("score") or PuzzleAssessment(
                    definition_score=17.0,
                    verified_count=1,
                    total_clues=1,
                    scores_complete=True,
                ),
            ),
        ):
            _run_planned_unit(job, job.plan_ready_units(ctx)[0], ctx)

        self.assertEqual(["finish", "fallback", "score"], calls)
        self.assertEqual("title", job.stage)

    def test_generate_publish_threads_client_and_runtime_to_publish_helper(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        ctx = _context(runtime)
        ctx.ai_client = object()
        item = _item("generate", "generate:size:15:1", preferred_model_id=PRIMARY_MODEL.model_id)
        item.payload = {"size": 15, "index": 1}
        job = GenerateJobState(item)
        job.stage = "publish"
        job.index = 1
        job.size = 15
        job.best_prepared = SimpleNamespace(
            puzzle=SimpleNamespace(metadata={}),
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            job.run_dir = Path(tmpdir)
            with (
                patch(
                    "rebus_generator.workflows.run_all.jobs.generate.publish_prepared_puzzle",
                    return_value=(
                        {"puzzle_id": "p1"},
                        SimpleNamespace(total_elapsed_ms=0),
                        [],
                    ),
                ) as publish_mock,
                patch("rebus_generator.workflows.run_all.jobs.generate.write_metrics"),
                patch("rebus_generator.workflows.run_all.jobs.generate.update_word_difficulty"),
            ):
                result = job._publish(ctx)

        publish_mock.assert_called_once()
        kwargs = publish_mock.call_args.kwargs
        self.assertIs(ctx.ai_client, kwargs["client"])
        self.assertIs(ctx.runtime, kwargs["runtime"])
        self.assertTrue(kwargs["multi_model"])
        self.assertEqual([{"puzzle_id": "p1"}], result)
        self.assertEqual("done", job.stage)

    def test_generate_define_finalize_uses_unresolved_only_fallback_policy(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        ctx = _context(runtime)
        item = _item("generate", "generate:size:14:1", preferred_model_id=PRIMARY_MODEL.model_id)
        item.payload = {"size": 14, "index": 1}
        job = GenerateJobState(item)
        job.stage = "define_initial"
        clue = WorkingClue(row_number=1, word_normalized="CAR", word_original="car")
        clue.current.definition = "[NECLAR]"
        job.working_puzzle = WorkingPuzzle("", 14, [], [clue], [])
        job.define_done_refs = {("H", 0, 0, 0)}
        policy_names: list[str] = []

        class _CaptureRewriteSession:
            def __init__(self, *, puzzle, **_kwargs):
                self.puzzle = puzzle

        def _apply_fallback(**kwargs):
            policy_names.append(kwargs["policy"].__name__)
            clue.current.definition = "Vehicul cu patru roți."
            clue.best = clue.current
            return {("H", 0, 0): "canon-1"}

        job.dex_provider = SimpleNamespace(
            get=lambda *_args, **_kwargs: "",
            uncertain_short_definitions=lambda: [],
        )

        with (
            patch("rebus_generator.workflows.run_all.generate_attempt.apply_scored_canonical_fallbacks", side_effect=_apply_fallback),
            patch("rebus_generator.workflows.run_all.jobs.generate.RunAllRewriteSession", _CaptureRewriteSession),
        ):
            _run_planned_unit(job, job.plan_ready_units(ctx)[0], ctx)

        self.assertEqual(["generate_unresolved_definition_fallback_policy"], policy_names)
        self.assertEqual("Vehicul cu patru roți.", clue.current.definition)
        self.assertEqual("rewrite_initial_verify", job.stage)

    def test_generate_define_finalize_rescues_placeholder_from_dex(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        ctx = _context(runtime)
        item = _item("generate", "generate:size:14:1", preferred_model_id=PRIMARY_MODEL.model_id)
        item.payload = {"size": 14, "index": 1}
        job = GenerateJobState(item)
        job.stage = "define_initial"
        clue = WorkingClue(row_number=1, word_normalized="AN", word_original="an")
        clue.current.definition = "[Definiție negenerată]"
        job.working_puzzle = WorkingPuzzle("", 14, [], [clue], [])
        job.define_done_refs = {("H", 0, 0, 0)}

        class _CaptureRewriteSession:
            def __init__(self, *, puzzle, **_kwargs):
                self.puzzle = puzzle

        job.dex_provider = SimpleNamespace(
            get=lambda *_args, **_kwargs: '- Definiție directă DEX pentru „an”: Perioadă de douăsprezece luni.',
            uncertain_short_definitions=lambda: [{"word": "AN", "definition": "Perioadă de douăsprezece luni."}],
        )

        with (
            patch("rebus_generator.workflows.run_all.generate_attempt.apply_scored_canonical_fallbacks", return_value={}),
            patch("rebus_generator.workflows.run_all.jobs.generate.RunAllRewriteSession", _CaptureRewriteSession),
        ):
            _run_planned_unit(job, job.plan_ready_units(ctx)[0], ctx)

        self.assertEqual("Perioadă de douăsprezece luni.", clue.current.definition)
        self.assertEqual("generate_rescue_dex", clue.current.source)
        self.assertEqual("dex_rescue", clue.current.generated_by)
        self.assertEqual("rewrite_initial_verify", job.stage)

    def test_generate_define_finalize_rescues_placeholder_from_short_word_overlay(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        ctx = _context(runtime)
        item = _item("generate", "generate:size:14:1", preferred_model_id=PRIMARY_MODEL.model_id)
        item.payload = {"size": 14, "index": 1}
        job = GenerateJobState(item)
        job.stage = "define_initial"
        clues = [
            WorkingClue(row_number=1, word_normalized="IT", word_original="iț"),
            WorkingClue(row_number=2, word_normalized="TM", word_original="TM"),
            WorkingClue(row_number=3, word_normalized="MM", word_original="MM"),
            WorkingClue(row_number=4, word_normalized="CJ", word_original="CJ"),
            WorkingClue(row_number=5, word_normalized="PH", word_original="PH"),
            WorkingClue(row_number=6, word_normalized="IR", word_original="IR"),
        ]
        for clue in clues:
            clue.current.definition = "[Definiție negenerată]"
        job.working_puzzle = WorkingPuzzle("", 14, [], clues, [])
        job.define_done_refs = {("H", 0, 0, 0)}

        class _CaptureRewriteSession:
            def __init__(self, *, puzzle, **_kwargs):
                self.puzzle = puzzle

        job.dex_provider = SimpleNamespace(
            get=lambda *_args, **_kwargs: "",
            uncertain_short_definitions=lambda: [],
        )

        with (
            patch("rebus_generator.workflows.run_all.generate_attempt.apply_scored_canonical_fallbacks", return_value={}),
            patch("rebus_generator.workflows.run_all.jobs.generate.RunAllRewriteSession", _CaptureRewriteSession),
        ):
            _run_planned_unit(job, job.plan_ready_units(ctx)[0], ctx)

        self.assertEqual("Domeniul web al țării cu Roma capitală.", clues[0].current.definition)
        self.assertEqual("Indicativ auto pentru județul Timiș.", clues[1].current.definition)
        self.assertEqual("Indicativ auto pentru județul Maramureș.", clues[2].current.definition)
        self.assertEqual("Indicativ auto pentru județul Cluj.", clues[3].current.definition)
        self.assertEqual("Indicativ auto pentru județul Prahova.", clues[4].current.definition)
        self.assertEqual("Domeniul web al țării sau teritoriului cu capitala Tehran.", clues[5].current.definition)
        self.assertTrue(all(clue.current.source == "generate_rescue_answer_supply" for clue in clues))
        self.assertTrue(all(clue.current.generated_by == "answer_supply" for clue in clues))
        self.assertEqual("rewrite_initial_verify", job.stage)

    def test_redefine_persist_prepare_uses_run_all_rewrite_session_finish(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["redefine"],
            topic_caps={"redefine": 1},
        )
        item = _item("redefine", "redefine:puzzle:p1", puzzle_id="p1")
        item.payload = {"puzzle_row": {"id": "p1", "title": "Titlu"}}
        job = supervisor._build_job(item)
        ctx = _context(runtime)
        finish_calls: list[str] = []
        persistence_plan = SimpleNamespace(clue_updates=[{"id": "c1"}])

        class _FakeRewriteSession:
            round_index = 2

            def finish(self):
                finish_calls.append("finish")
                return SimpleNamespace(initial_passed=1, final_passed=1, total=1)

        job.rewrite_session = _FakeRewriteSession()
        job.puzzle_row = {"id": "p1", "title": "Titlu"}
        job.clue_rows = [{"id": "c1"}]
        job.baseline_puzzle = SimpleNamespace()
        job.candidate_puzzle = SimpleNamespace()
        job.stage = "persist_prepare"

        with patch(
            "rebus_generator.workflows.run_all.jobs.redefine.plan_redefined_puzzle_persistence",
            return_value=persistence_plan,
        ) as plan_mock:
            unit = job.plan_ready_units(ctx)[0]
            self.assertEqual("redefine_persist_prepare", unit.purpose)
            result = _run_planned_unit(job, unit, ctx)

        self.assertEqual(["finish"], finish_calls)
        plan_mock.assert_called_once()
        self.assertIs(result.value, persistence_plan)
        self.assertIs(job.persistence_plan, persistence_plan)
        self.assertEqual("persist_apply", job.stage)

    def test_run_all_rewrite_session_finish_is_idempotent(self):
        session = RunAllRewriteSession.__new__(RunAllRewriteSession)
        clue = SimpleNamespace(
            word_normalized="APA",
            current=SimpleNamespace(
                assessment=SimpleNamespace(verified=False),
            ),
            active_version=lambda: SimpleNamespace(definition="Definiție curentă"),
        )
        dex_calls: list[str] = []
        audit_calls: list[tuple] = []
        session.puzzle = object()
        session.initial_passed = 1
        session.generation_model_switches = 3
        session.outcomes = {
            "APA": SimpleNamespace(
                word="APA",
                initial_semantic=1,
                initial_rebus=1,
                final_semantic=0,
                final_rebus=0,
                was_candidate=True,
                had_error=False,
                terminal_reason="",
                selected_strategy="",
            )
        }
        session.dex = SimpleNamespace(
            uncertain_short_definitions=lambda: dex_calls.append("dex") or [{"word": "APA", "definition": "DEX scurt"}]
        )
        session.final_result = None
        session.clues = lambda: [clue]

        with (
            patch("rebus_generator.workflows.run_all.rewrite_units._restore_best_versions") as restore_mock,
            patch("rebus_generator.workflows.run_all.rewrite_units._extract_semantic_score", return_value=1),
            patch("rebus_generator.workflows.run_all.rewrite_units._extract_rebus_score", return_value=1),
            patch("rebus_generator.workflows.run_all.rewrite_units.audit", side_effect=lambda *args, **kwargs: audit_calls.append((args, kwargs))),
        ):
            first = RunAllRewriteSession.finish(session)
            second = RunAllRewriteSession.finish(session)

        self.assertIs(first, second)
        self.assertIs(session.final_result, first)
        self.assertEqual(["dex"], dex_calls)
        self.assertEqual(1, restore_mock.call_count)
        self.assertEqual(1, len(audit_calls))
        self.assertEqual("rewrite_no_change", session.outcomes["APA"].terminal_reason)

    def test_retitle_job_yields_across_round_phases(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["retitle"],
            topic_caps={"retitle": 1},
        )
        item = _item("retitle", "retitle:puzzle:p1", puzzle_id="p1")
        item.payload = {"puzzle_row": {"id": "p1", "title": "Vechi"}}
        job = supervisor._build_job(item)
        ctx = _context(runtime)
        with (
            patch("rebus_generator.workflows.run_all.jobs.retitle.fetch_retitle_clues", return_value=[{"word_normalized": "APA", "definition": "Apa"}]),
            patch("rebus_generator.workflows.run_all.jobs.retitle.fetch_retitle_puzzles", return_value=[]),
            patch("rebus_generator.workflows.run_all.jobs.retitle._generate_candidate_with_active_model", side_effect=["Titlu Nou", None]),
            patch("rebus_generator.workflows.run_all.jobs.retitle.rate_title_creativity", side_effect=[(7, "ok-a"), (7, "ok-b")]),
        ):
            _run_planned_unit(job, job.plan_ready_units(ctx)[0], ctx)
            self.assertEqual("generate_primary", job.stage)

            generate_primary = job.plan_ready_units(ctx)[0]
            self.assertEqual("retitle_generate_primary", generate_primary.purpose)
            self.assertEqual(PRIMARY_MODEL.model_id, generate_primary.model_id)
            _run_planned_unit(job, generate_primary, ctx)
            self.assertEqual("rate_primary", job.stage)

            first_rate = job.plan_ready_units(ctx)[0]
            self.assertEqual("retitle_rate_primary", first_rate.purpose)
            self.assertEqual(PRIMARY_MODEL.model_id, first_rate.model_id)
            _run_planned_unit(job, first_rate, ctx)

            second_rate = job.plan_ready_units(ctx)[0]
            self.assertEqual("retitle_rate_primary", second_rate.purpose)
            self.assertEqual(SECONDARY_MODEL.model_id, second_rate.model_id)
            _run_planned_unit(job, second_rate, ctx)

            _run_planned_unit(job, job.plan_ready_units(ctx)[0], ctx)
            self.assertEqual("generate_secondary", job.stage)

            generate_secondary = job.plan_ready_units(ctx)[0]
            self.assertEqual("retitle_generate_secondary", generate_secondary.purpose)
            _run_planned_unit(job, generate_secondary, ctx)
            self.assertEqual("round_finalize", job.stage)

            _run_planned_unit(job, job.plan_ready_units(ctx)[0], ctx)
            self.assertEqual("generate_primary", job.stage)
            self.assertEqual(2, job.round_idx)

    def test_close_writes_run_summary_with_llm_stats(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = _context(runtime)
            ctx.run_dir = Path(tmpdir)
            supervisor = RunAllSupervisor(
                context=ctx,
                topics=["retitle"],
                topic_caps={"retitle": 1},
            )
            configure_run_llm_policy(
                reasoning_overrides={(PRIMARY_MODEL.model_id, "definition_rate"): "minimal"},
                truncation_threshold=3,
            )
            client = SimpleNamespace(
                chat=SimpleNamespace(
                    completions=SimpleNamespace(
                        create=lambda **kwargs: SimpleNamespace(
                            choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(content="ok", reasoning_content=""))],
                            usage=SimpleNamespace(
                                completion_tokens=12,
                                completion_tokens_details=SimpleNamespace(reasoning_tokens=3),
                            ),
                        )
                    )
                )
            )
            _chat_completion_create(
                client,
                model=PRIMARY_MODEL.model_id,
                messages=[{"role": "user", "content": "test"}],
                temperature=0.0,
                max_tokens=300,
                purpose="definition_rate",
            )
            supervisor.stop_reason = "test_stop"
            supervisor.close()

            summary = json.loads((Path(tmpdir) / "run_summary.json").read_text(encoding="utf-8"))
            self.assertEqual("test_stop", summary["stop_reason"])
            self.assertIn("activation_overhead_seconds", summary)
            self.assertIn("loaded_model_drain_switches", summary)
            self.assertIn("completed_by_topic", summary)
            self.assertIn("completions_per_hour", summary)
            self.assertIn("dominant_failures", summary)
            self.assertIn("truncations_by_purpose", summary)
            self.assertIn("truncations_by_model_purpose", summary)
            self.assertIn("truncations_by_model_purpose_max_tokens_reasoning", summary)
            self.assertEqual(1, sum(summary["truncations_by_model_purpose_max_tokens_reasoning"].values()))
            self.assertIn("definition_rate", summary["llm"]["per_purpose"])
            self.assertIn("retitle", summary["topics"])

    @patch("rebus_generator.workflows.run_all.scheduler.write_summary_snapshot")
    def test_heartbeat_refreshes_summary_snapshot_periodically(self, mock_snapshot):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["retitle"],
            topic_caps={"retitle": 1},
        )
        supervisor.last_summary_snapshot_at = supervisor.started_at - supervisor.summary_snapshot_seconds - 1

        supervisor._maybe_heartbeat(force=False)

        mock_snapshot.assert_called_once_with(supervisor)


class RunAllPreflightTests(unittest.TestCase):
    def tearDown(self):
        reset_run_llm_state()

    def test_preflight_smoke_calls_forward_global_top_p(self):
        class _PreflightRuntime:
            def __init__(self, multi_model=False):
                self.multi_model = multi_model
                self.primary = PRIMARY_MODEL
                self.secondary = SECONDARY_MODEL

            def sync(self):
                return {}

            def activate(self, model, **kwargs):
                return model

        class _Client:
            def __init__(self):
                self.calls = []
                self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

            def create(self, **kwargs):
                self.calls.append(kwargs)
                return SimpleNamespace(
                    choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="ok", reasoning_content=""))],
                    usage=None,
                )

        client = _Client()

        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = Path(tmpdir) / "preflight.json"
            with (
                patch("rebus_generator.cli.run_all.SUPABASE_URL", "https://test.supabase.co"),
                patch("rebus_generator.cli.run_all.SUPABASE_SERVICE_ROLE_KEY", "test-key"),
                patch("rebus_generator.cli.run_all.create_service_role_client"),
                patch("rebus_generator.cli.run_all._rust_binary_path"),
                patch("rebus_generator.cli.run_all.LmRuntime", _PreflightRuntime),
                patch("rebus_generator.cli.run_all._preflight_unload_all"),
                patch("rebus_generator.cli.run_all.create_ai_client", return_value=client),
            ):
                _preflight(topics=["generate"], artifact_path=artifact, multi_model=False)

        self.assertEqual(1, len(client.calls))
        self.assertEqual(llm_top_p(), client.calls[0]["top_p"])

    def test_preflight_writes_artifact_and_aborts_on_secondary_load_failure(self):
        class _PreflightRuntime:
            def __init__(self, multi_model=False):
                self.multi_model = multi_model
                self.primary = PRIMARY_MODEL
                self.secondary = SECONDARY_MODEL
                self.current_model = None

            def sync(self):
                return {}

            def activate(self, model, **kwargs):
                if model.model_id == SECONDARY_MODEL.model_id:
                    raise RuntimeError(
                        "Failed to load model eurollm-22b-instruct-2512-mlx-nvfp4: insufficient system resources"
                    )
                self.current_model = model
                return model

        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = Path(tmpdir) / "preflight.json"
            with (
                patch("rebus_generator.cli.run_all.SUPABASE_URL", "https://test.supabase.co"),
                patch("rebus_generator.cli.run_all.SUPABASE_SERVICE_ROLE_KEY", "test-key"),
                patch("rebus_generator.cli.run_all.create_service_role_client"),
                patch("rebus_generator.cli.run_all._rust_binary_path"),
                patch("rebus_generator.cli.run_all.LmRuntime", _PreflightRuntime),
                patch("rebus_generator.cli.run_all._preflight_unload_all"),
                patch("rebus_generator.cli.run_all._chat_completion_create", return_value=SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="GUAS"))]
                )),
            ):
                with self.assertRaises(SystemExit):
                    _preflight(topics=["generate", "retitle"], artifact_path=artifact, multi_model=True)

            report = json.loads(artifact.read_text(encoding="utf-8"))
            self.assertEqual("lmstudio_resource_guard", report["blocking_error"]["signature"])
            self.assertEqual("failed", report["models"][-1]["status"])


class ClaimStateTests(unittest.TestCase):
    def test_simplify_words_conflict_with_active_puzzle_words(self):
        claims = ClaimState()
        claims.topic_by_puzzle_id["p1"] = "redefine"
        claims.puzzle_words["p1"] = {"APA", "NOR"}

        self.assertTrue(claims.simplify_word_conflict({"APA"}))
        self.assertFalse(claims.simplify_word_conflict({"SOARE"}))

    def test_release_clears_claims_for_later_reuse(self):
        claims = ClaimState()
        item = _item("redefine", "redefine:puzzle:p1", puzzle_id="p1", words={"APA"})

        claims.claim(item)
        self.assertTrue(claims.has_puzzle("p1"))
        claims.release(item)

        self.assertFalse(claims.has_puzzle("p1"))
        self.assertFalse(claims.simplify_word_conflict({"APA"}))


class RunAllReadmeContractTests(unittest.TestCase):
    def test_readme_documents_run_all_local_claim_boundaries(self):
        text = Path("README.md").read_text(encoding="utf-8").lower()

        self.assertIn("run_all", text)
        self.assertIn("run_all.sh", text)
        self.assertIn("production entrypoint", text)

    def test_docs_and_code_drop_legacy_unattended_wrappers(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        arch = Path("GENERATOR_ARCH.md").read_text(encoding="utf-8")
        run_all_source = Path("packages/rebus-generator/src/rebus_generator/cli/run_all.py").read_text(encoding="utf-8")

        self.assertIn("run_all.sh", readme)
        self.assertNotIn("run_batch_loop.sh", readme)
        self.assertNotIn("run_definition_improve.sh", readme)
        self.assertNotIn("run_title_improve.sh", readme)
        self.assertNotIn("run_clue_canon_simplify.sh", readme)
        self.assertNotIn("run_batch_loop.sh", arch)
        self.assertNotIn("run_simplify_fanout", run_all_source)
        self.assertNotIn("run_batch(", run_all_source)
        self.assertNotIn("DEFAULT_SIMPLIFY_STATE_PATH", run_all_source)

    def test_legacy_unattended_wrapper_files_removed(self):
        self.assertFalse(Path("run_batch_loop.sh").exists())
        self.assertFalse(Path("run_definition_improve.sh").exists())
        self.assertFalse(Path("run_title_improve.sh").exists())
        self.assertFalse(Path("run_clue_canon_simplify.sh").exists())
