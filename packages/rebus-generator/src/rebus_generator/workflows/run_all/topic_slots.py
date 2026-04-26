from __future__ import annotations

import time
from dataclasses import dataclass

from rebus_generator.platform.io.runtime_logging import log

from .types import StepState, SupervisorWorkItem


@dataclass
class TopicSlotProgressor:
    """Owns per-topic slot admission and ready-unit collection."""

    supervisor: object

    def collect_ready_units(self) -> list[StepState]:
        now = time.monotonic()
        units: list[StepState] = []
        for topic in self.supervisor.topics:
            slot = self.supervisor.slots[topic]
            job = slot.active_job
            if (
                job is None
                or job.status != "active"
                or job.available_after > now
                or job.running_step_id is not None
            ):
                continue
            self.supervisor._observe_job_stage(job)
            units.extend(job.plan_ready_units(self.supervisor.ctx))
        return units

    def refill(self) -> int:
        admitted = 0
        if self.supervisor._admission_frozen():
            return 0
        for topic in self.supervisor.topics:
            slot = self.supervisor.slots[topic]
            if slot.active_job is not None:
                continue
            item = self._next_item_for_topic(topic)
            if item is None:
                continue
            job = self.supervisor._build_job(item)
            slot.active_job = job
            self.supervisor._note_job_started(job)
            admitted += 1
            log(
                f"[run_all start] topic={topic} job={job.item_id} task={job.task_kind} "
                f"preferred={job.preferred_model_id} stage={job.stage}"
            )
        return admitted

    def _next_item_for_topic(self, topic: str) -> SupervisorWorkItem | None:
        item = self.supervisor._next_pending_for_topic(topic)
        if item is not None:
            return item
        return self.supervisor._poll_one_topic(topic)
