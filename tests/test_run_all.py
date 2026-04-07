import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from generator.core.model_manager import PRIMARY_MODEL, SECONDARY_MODEL
from generator.run_all import (
    ClaimState,
    RunAllContext,
    RunAllSupervisor,
    SupervisorWorkItem,
    build_parser,
)


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
            self.switch_count += 1 if self.current_model else 0
            self.activation_count += 1
            self.current_model = model
            if previous and self.switch_callback is not None:
                self.switch_callback(previous, model.model_id, self)
        return model


def _context(runtime):
    return RunAllContext(
        supabase=object(),
        ai_client=object(),
        rate_client=object(),
        runtime=runtime,
        store=SimpleNamespace(),
        run_dir=SimpleNamespace(),
        batch_output_root=SimpleNamespace(),
        words_path=SimpleNamespace(),
        multi_model=True,
        dry_run=False,
        generate_rewrite_rounds=30,
        redefine_rounds=7,
        verify_candidates=3,
        simplify_batch_size=5,
    )


class RunAllSupervisorTests(unittest.TestCase):
    def test_parser_accepts_topics_and_debug(self):
        args = build_parser().parse_args(["--topics", "retitle,redefine", "--debug"])

        self.assertEqual("retitle,redefine", args.topics)
        self.assertTrue(args.debug)

    def test_choose_next_item_prefers_loaded_model_queue(self):
        runtime = _FakeRuntime(current_model=SECONDARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["generate", "retitle"],
            topic_caps={"generate": 1, "retitle": 1},
        )
        first = SupervisorWorkItem(
            item_id="secondary",
            topic="generate",
            task_kind="generate",
            preferred_model_id=SECONDARY_MODEL.model_id,
            target_models=(PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id),
            run=lambda ctx: None,
        )
        second = SupervisorWorkItem(
            item_id="primary",
            topic="retitle",
            task_kind="retitle",
            preferred_model_id=PRIMARY_MODEL.model_id,
            target_models=(PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id),
            run=lambda ctx: None,
        )
        supervisor.pending_items = [first, second]

        chosen = supervisor._choose_next_item()

        self.assertEqual("secondary", chosen.item_id)

    def test_admission_freezes_when_both_model_queues_have_work(self):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["generate", "retitle"],
            topic_caps={"generate": 1, "retitle": 1},
        )
        supervisor.pending_items = [
            SupervisorWorkItem(
                item_id="primary",
                topic="retitle",
                task_kind="retitle",
                preferred_model_id=PRIMARY_MODEL.model_id,
                target_models=(PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id),
                run=lambda ctx: None,
            ),
            SupervisorWorkItem(
                item_id="secondary",
                topic="generate",
                task_kind="generate",
                preferred_model_id=SECONDARY_MODEL.model_id,
                target_models=(PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id),
                run=lambda ctx: None,
            ),
        ]

        self.assertTrue(supervisor._admission_frozen())

    @patch.object(RunAllSupervisor, "_poll_generate", return_value=0)
    @patch.object(RunAllSupervisor, "_poll_redefine", return_value=0)
    @patch.object(RunAllSupervisor, "_poll_retitle", return_value=0)
    @patch.object(RunAllSupervisor, "_poll_simplify", return_value=0)
    def test_poll_topics_does_not_admit_new_work_when_freeze_active(
        self,
        simplify_mock,
        retitle_mock,
        redefine_mock,
        generate_mock,
    ):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["generate", "redefine"],
            topic_caps={"generate": 1, "redefine": 1},
        )
        supervisor.pending_items = [
            SupervisorWorkItem(
                item_id="primary",
                topic="redefine",
                task_kind="redefine",
                preferred_model_id=PRIMARY_MODEL.model_id,
                target_models=(PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id),
                run=lambda ctx: None,
            ),
            SupervisorWorkItem(
                item_id="secondary",
                topic="generate",
                task_kind="generate",
                preferred_model_id=SECONDARY_MODEL.model_id,
                target_models=(PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id),
                run=lambda ctx: None,
            ),
        ]

        admitted = supervisor._poll_topics()

        self.assertEqual(0, admitted)
        generate_mock.assert_not_called()
        redefine_mock.assert_not_called()
        retitle_mock.assert_not_called()
        simplify_mock.assert_not_called()

    @patch("generator.run_all.log")
    def test_admit_logs_targets_and_queue_counts(self, log_mock):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["retitle"],
            topic_caps={"retitle": 2},
        )
        item = SupervisorWorkItem(
            item_id="retitle:puzzle:1",
            topic="retitle",
            task_kind="retitle",
            preferred_model_id=PRIMARY_MODEL.model_id,
            target_models=(PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id),
            run=lambda ctx: None,
            puzzle_id="1",
            words={"APA"},
        )

        supervisor._admit_item(item)

        message = log_mock.call_args.args[0]
        self.assertIn("topic=retitle", message)
        self.assertIn("targets=", message)
        self.assertIn("queues_model=", message)
        self.assertIn("queues_topic=", message)

    @patch("generator.run_all.log")
    def test_switch_callback_logs_queue_snapshot(self, log_mock):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["retitle"],
            topic_caps={"retitle": 1},
        )
        supervisor.pending_items = [
            SupervisorWorkItem(
                item_id="retitle:puzzle:1",
                topic="retitle",
                task_kind="retitle",
                preferred_model_id=SECONDARY_MODEL.model_id,
                target_models=(PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id),
                run=lambda ctx: None,
            )
        ]

        supervisor._on_model_switch(PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id, runtime)

        message = log_mock.call_args.args[0]
        self.assertIn("[run_all switch]", message)
        self.assertIn("queues_model=", message)
        self.assertIn("queues_topic=", message)

    @patch("generator.run_all.fetch_redefine_puzzles")
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

        admitted = supervisor._poll_redefine(1)

        self.assertEqual(0, admitted)
        self.assertEqual([], supervisor.pending_items)

    @patch("generator.run_all.build_candidate_pairs")
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

        admitted = supervisor._poll_simplify(1)

        self.assertEqual(0, admitted)
        self.assertEqual([], supervisor.pending_items)

    @patch("generator.run_all.fetch_retitle_puzzles")
    def test_retitle_poll_skips_same_puzzle_claimed_elsewhere(self, fetch_mock):
        runtime = _FakeRuntime(current_model=PRIMARY_MODEL)
        supervisor = RunAllSupervisor(
            context=_context(runtime),
            topics=["retitle"],
            topic_caps={"retitle": 1},
        )
        supervisor.claims.topic_by_puzzle_id["p1"] = "redefine"
        fetch_mock.return_value = [{"id": "p1", "title": "Titlu"}]

        admitted = supervisor._poll_retitle(1)

        self.assertEqual(0, admitted)
        self.assertEqual([], supervisor.pending_items)


class ClaimStateTests(unittest.TestCase):
    def test_simplify_words_conflict_with_active_puzzle_words(self):
        claims = ClaimState()
        claims.topic_by_puzzle_id["p1"] = "redefine"
        claims.puzzle_words["p1"] = {"APA", "NOR"}

        self.assertTrue(claims.simplify_word_conflict({"APA"}))
        self.assertFalse(claims.simplify_word_conflict({"SOARE"}))

    def test_release_clears_claims_for_later_reuse(self):
        claims = ClaimState()
        item = SupervisorWorkItem(
            item_id="redefine:puzzle:p1",
            topic="redefine",
            task_kind="redefine",
            preferred_model_id=PRIMARY_MODEL.model_id,
            target_models=(PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id),
            run=lambda ctx: None,
            puzzle_id="p1",
            words={"APA"},
        )

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


if __name__ == "__main__":
    unittest.main()
