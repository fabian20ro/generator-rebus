import unittest

from generator.core.lm_runtime import LmRuntime
from generator.core.model_aware_scheduler import (
    ModelAwareScheduler,
    WorkConclusion,
    WorkItem,
    WorkStep,
    WorkVote,
)
from generator.core.model_manager import PRIMARY_MODEL, SECONDARY_MODEL


class _FakeRuntime:
    def __init__(self, current=None):
        self.primary = PRIMARY_MODEL
        self.secondary = SECONDARY_MODEL
        self.current_model = current
        self.switch_count = 0
        self.activation_count = 0
        self.trace = []

    @property
    def current_model_id(self):
        return self.current_model.model_id if self.current_model else ""

    @property
    def current_model_label(self):
        return self.current_model.display_name if self.current_model else ""

    def sync(self):
        return {}

    def ensure_active(self, model):
        if self.current_model and self.current_model.model_id != model.model_id:
            self.switch_count += 1
        if not self.current_model or self.current_model.model_id != model.model_id:
            self.activation_count += 1
        self.current_model = model
        self.trace.append(model.model_id)
        return model


class SchedulerTests(unittest.TestCase):
    def test_keeps_loaded_model_until_its_queue_is_empty(self):
        runtime = _FakeRuntime(current=SECONDARY_MODEL)
        items = [
            WorkItem(item_id="a", task_kind="x", payload={"secondary": True}, pending_models={SECONDARY_MODEL.model_id}),
            WorkItem(item_id="b", task_kind="x", payload={"secondary": True}, pending_models={SECONDARY_MODEL.model_id}),
        ]

        def runner(item, model):
            return WorkVote(model_id=model.model_id, value=True, source="ok")

        scheduler = ModelAwareScheduler(
            runtime=runtime,
            models=[PRIMARY_MODEL, SECONDARY_MODEL],
            steps=[
                WorkStep(model_id=PRIMARY_MODEL.model_id, purpose="x", runner=runner, prerequisite=lambda item: item.payload.get("primary", False), can_conclude=lambda item: WorkConclusion(complete=True)),
                WorkStep(model_id=SECONDARY_MODEL.model_id, purpose="x", runner=runner, prerequisite=lambda item: item.payload.get("secondary", False), can_conclude=lambda item: WorkConclusion(complete=True)),
            ],
            task_label="test",
        )

        scheduler.run(items)

        self.assertEqual([SECONDARY_MODEL.model_id], runtime.trace)
        self.assertEqual(0, runtime.switch_count)

    def test_switches_only_after_loaded_queue_drains(self):
        runtime = _FakeRuntime(current=SECONDARY_MODEL)
        items = [
            WorkItem(item_id="a", task_kind="x", payload={"secondary": True}, pending_models={SECONDARY_MODEL.model_id}),
            WorkItem(item_id="b", task_kind="x", payload={"primary": True}, pending_models={PRIMARY_MODEL.model_id}),
        ]

        def runner(item, model):
            return WorkVote(model_id=model.model_id, value=True, source="ok")

        scheduler = ModelAwareScheduler(
            runtime=runtime,
            models=[PRIMARY_MODEL, SECONDARY_MODEL],
            steps=[
                WorkStep(model_id=PRIMARY_MODEL.model_id, purpose="x", runner=runner, prerequisite=lambda item: item.payload.get("primary", False), can_conclude=lambda item: WorkConclusion(complete=True)),
                WorkStep(model_id=SECONDARY_MODEL.model_id, purpose="x", runner=runner, prerequisite=lambda item: item.payload.get("secondary", False), can_conclude=lambda item: WorkConclusion(complete=True)),
            ],
            task_label="test",
        )

        scheduler.run(items)

        self.assertEqual([SECONDARY_MODEL.model_id, PRIMARY_MODEL.model_id], runtime.trace)
        self.assertEqual(1, runtime.switch_count)

    def test_terminal_item_is_not_requeued_for_other_model(self):
        runtime = _FakeRuntime(current=PRIMARY_MODEL)
        item = WorkItem(item_id="a", task_kind="verify", payload={}, pending_models={PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id})

        def primary_runner(item, model):
            return WorkVote(model_id=model.model_id, value=None, source="no_thinking_retry", terminal=True, terminal_reason="empty_after_retry")

        def secondary_runner(item, model):
            return WorkVote(model_id=model.model_id, value=True, source="ok")

        scheduler = ModelAwareScheduler(
            runtime=runtime,
            models=[PRIMARY_MODEL, SECONDARY_MODEL],
            steps=[
                WorkStep(model_id=PRIMARY_MODEL.model_id, purpose="x", runner=primary_runner, can_conclude=lambda item: WorkConclusion(failed=True, skip_models={SECONDARY_MODEL.model_id})),
                WorkStep(model_id=SECONDARY_MODEL.model_id, purpose="x", runner=secondary_runner, can_conclude=lambda item: WorkConclusion(complete=True)),
            ],
            task_label="test",
        )

        stats = scheduler.run([item])

        self.assertEqual("failed", item.status)
        self.assertEqual([PRIMARY_MODEL.model_id], runtime.trace)
        self.assertEqual(1, stats.failed)


if __name__ == "__main__":
    unittest.main()
