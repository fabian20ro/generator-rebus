import unittest
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from generator.core.model_manager import PRIMARY_MODEL, SECONDARY_MODEL
from generator.run_all import build_parser
from generator.supervisor import (
    ClaimState,
    DeterministicFailureQuarantine,
    RunAllContext,
    RunAllSupervisor,
    StepState,
    SupervisorWorkItem,
)
from generator.supervisor.jobs.base import JobState


class _FakeRuntime:
    def __init__(self, current_model=None):
        self.primary = PRIMARY_MODEL
        self.secondary = SECONDARY_MODEL
        self.current_model = current_model
        self.switch_count = 0
        self.activation_count = 0
        self.switch_callback = None

    @property
    def current_model_id(self):
        return self.current_model.model_id if self.current_model else ""

    @property
    def current_model_label(self):
        return self.current_model.display_name if self.current_model else ""

    def sync(self):
        return {}

    def activate(self, model):
        previous = self.current_model.model_id if self.current_model else ""
        if not self.current_model or self.current_model.model_id != model.model_id:
            if self.current_model:
                self.switch_count += 1
            self.activation_count += 1
            self.current_model = model
            if previous and self.switch_callback is not None:
                self.switch_callback(previous, model.model_id, self)
        return model

    def activate_primary(self):
        return self.activate(PRIMARY_MODEL)

    def activate_secondary(self):
        return self.activate(SECONDARY_MODEL)


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


class RunAllSupervisorTests(unittest.TestCase):
    def test_parser_accepts_topics_and_debug(self):
        args = build_parser().parse_args(["--topics", "retitle,redefine", "--debug"])

        self.assertEqual("retitle,redefine", args.topics)
        self.assertTrue(args.debug)

    def test_refill_starts_one_job_per_topic_slot(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["generate", "retitle"],
            topic_caps={"generate": 1, "retitle": 1},
        )
        generate_item = _item("generate", "generate:1", preferred_model_id=SECONDARY_MODEL.model_id)
        retitle_item = _item("retitle", "retitle:1")
        build_job = lambda item: _StaticJob(item)

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

    @patch("generator.supervisor.scheduler.log")
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

    @patch("generator.supervisor.scheduler.log")
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

        supervisor._on_model_switch(PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id, runtime)

        message = log_mock.call_args.args[0]
        self.assertIn("[run_all switch]", message)
        self.assertIn("queues_model=", message)
        self.assertIn("active_slots=", message)

    @patch("generator.supervisor.pollers.fetch_redefine_puzzles")
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

    @patch("generator.supervisor.pollers.build_candidate_pairs")
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

    @patch("generator.supervisor.pollers.fetch_retitle_puzzles")
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
                patch("generator.supervisor.jobs.simplify.load_simplify_bucket", return_value=({("APA", "", ""): [row_left, row_right]}, [pair])),
                patch("generator.supervisor.jobs.simplify.compare_simplify_pairs", return_value={"l::r": vote}),
                patch("generator.supervisor.jobs.simplify.find_simplify_pair_rows", return_value=(row_left, row_right)),
                patch("generator.supervisor.jobs.simplify.should_rewrite_survivor", return_value=False),
                patch("generator.supervisor.jobs.simplify.choose_existing_survivor", return_value=SimpleNamespace(definition="stanga")),
                patch("generator.supervisor.jobs.simplify.apply_simplify_merge", return_value="survivor"),
                patch("generator.supervisor.jobs.simplify.refresh_simplify_bucket_rows"),
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
        baseline_puzzle = SimpleNamespace(
            title="Titlu",
            horizontal_clues=[SimpleNamespace(active_version=lambda: SimpleNamespace(assessment=SimpleNamespace(verified=True)))],
            vertical_clues=[],
            assessment=None,
        )
        candidate_puzzle = SimpleNamespace(assessment=SimpleNamespace(min_rebus=0, avg_rebus=0.0, verified_count=0, total_clues=0))
        with (
            patch("generator.supervisor.jobs.redefine.fetch_redefine_clues", return_value=[{"id": "c1"}]),
            patch("generator.supervisor.jobs.redefine.build_working_puzzle", side_effect=[baseline_puzzle, candidate_puzzle]),
            patch("generator.supervisor.jobs.redefine._run_pair_verify", return_value=([PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id], "gemma + eurollm")),
            patch("generator.supervisor.jobs.redefine._run_pair_rate", return_value=([PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id], "gemma + eurollm")),
            patch("generator.supervisor.jobs.redefine.DexProvider.for_puzzle", return_value=SimpleNamespace()),
            patch("generator.supervisor.jobs.redefine._finalize_pair_verification", return_value=baseline_puzzle.horizontal_clues),
            patch("generator.supervisor.jobs.redefine._finalize_pair_rating"),
            patch("generator.supervisor.jobs.redefine.score_puzzle_state", return_value=SimpleNamespace(min_rebus=1, avg_rebus=2.0, verified_count=1, total_clues=1)),
        ):
            job._fetch(_context(runtime))
            self.assertEqual("baseline_verify", job.stage)
            self.assertEqual("baseline_verify", job.next_steps(_context(runtime))[0].step_id)
            job._baseline_verify(_context(runtime))
            self.assertEqual("baseline_rate", job.stage)
            job._baseline_rate(_context(runtime))
            self.assertEqual("baseline_finalize", job.stage)
            job._baseline_finalize(_context(runtime))
            self.assertEqual("rewrite_initial_verify", job.stage)

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
            patch("generator.supervisor.jobs.retitle.fetch_retitle_clues", return_value=[{"word_normalized": "APA", "definition": "Apa"}]),
            patch("generator.supervisor.jobs.retitle.fetch_retitle_puzzles", return_value=[]),
            patch("generator.supervisor.jobs.retitle._generate_batch_candidates", return_value=[(SimpleNamespace(), "Titlu Nou")]),
            patch("generator.supervisor.jobs.retitle._rate_batch_candidates"),
            patch("generator.supervisor.jobs.retitle._finalize_title_result", return_value=SimpleNamespace(title="Titlu Nou", used_fallback=False, score=8, score_complete=True)),
            patch("generator.supervisor.jobs.retitle.apply_title_update", return_value=True),
        ):
            job._fetch(ctx)
            self.assertEqual("generate_primary", job.stage)
            job._generate_primary(ctx)
            self.assertEqual("rate_primary", job.stage)
            job._rate_primary(ctx)
            self.assertEqual("generate_secondary", job.stage)
            job._round_finalize(ctx)
            self.assertEqual("generate_primary", job.stage)


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
        self.assertIn("single-process", text)
        self.assertIn("in-memory", text)
        self.assertIn("active puzzle jobs", text)
        self.assertIn("not a durable event bus", text)

    def test_docs_and_code_drop_legacy_unattended_wrappers(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        arch = Path("GENERATOR_ARCH.md").read_text(encoding="utf-8")
        run_all_source = Path("generator/run_all.py").read_text(encoding="utf-8")

        self.assertIn("./run_all.sh", readme)
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
