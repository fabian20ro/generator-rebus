from __future__ import annotations

import concurrent.futures
import time
from collections import Counter

from ..core.clue_canon_store import ClueCanonStore
from ..core.lm_runtime import LmRuntime
from ..core.model_manager import PRIMARY_MODEL, SECONDARY_MODEL
from ..core.runtime_logging import log
from .jobs import build_job
from .pollers import poll_generate, poll_redefine, poll_retitle, poll_simplify
from .types import ClaimState, RunAllContext, StepState, SupervisorWorkItem, TopicSlot, WorkerTask

DEFAULT_IDLE_SLEEP_SECONDS = 15
DEFAULT_HEARTBEAT_SECONDS = 30
DEFAULT_RETRY_LIMIT = 2
WORKER_POLL_SLEEP_SECONDS = 1


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
        self.topics = list(topics)
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

    def _handle_step_error(self, job, step: StepState, exc: Exception) -> None:
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

    def _job_by_id(self, item_id: str):
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
        return poll_generate(self)

    def _poll_redefine(self) -> SupervisorWorkItem | None:
        return poll_redefine(self)

    def _poll_retitle(self) -> SupervisorWorkItem | None:
        return poll_retitle(self)

    def _poll_simplify(self) -> SupervisorWorkItem | None:
        return poll_simplify(self)

    def _build_job(self, item: SupervisorWorkItem):
        return build_job(item)

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
            if self.slots[topic].active_job is not None:
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
