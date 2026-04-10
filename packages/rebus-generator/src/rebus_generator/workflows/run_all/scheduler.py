from __future__ import annotations

import concurrent.futures
import re
import time
from collections import Counter

from rebus_generator.platform.orchestration import RunLedger
from rebus_generator.platform.llm.llm_client import llm_run_retry_count, llm_run_stats_snapshot
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.llm.models import PRIMARY_MODEL, SECONDARY_MODEL
from rebus_generator.platform.io.runtime_logging import log
from .jobs import build_job
from .pollers import poll_generate, poll_redefine, poll_retitle, poll_simplify
from .reporting import (
    dominant_failure_global_text,
    dominant_failure_text,
    format_age_seconds,
    maybe_heartbeat,
    maybe_raise_stall,
    queue_snapshot_text,
    write_summary_artifacts,
)
from .state import (
    active_generate_size_exclusions,
    generate_size_penalty_map,
    note_job_finished,
    note_job_started,
    observe_job_stage,
    record_failure_occurrence,
    record_generate_size_failure,
    runtime_load_seconds_total,
    should_deprioritize_live_item,
    stable_progress,
)
from .types import (
    ClaimState,
    DeterministicFailureQuarantine,
    RunAllContext,
    RunAllStallDetected,
    StableItemProgress,
    StepState,
    SupervisorWorkItem,
    TopicSlot,
    WorkerTask,
)

DEFAULT_IDLE_SLEEP_SECONDS = 15
DEFAULT_HEARTBEAT_SECONDS = 30
DEFAULT_RETRY_LIMIT = 2
WORKER_POLL_SLEEP_SECONDS = 1
_WHITESPACE_RE = re.compile(r"\s+")


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
        self.started_at = time.monotonic()
        self.last_completion_at = self.started_at
        self.last_progress_at = self.started_at
        self.last_heartbeat_at = 0.0
        initial_load_seconds = float(getattr(self.ctx.runtime, "activation_seconds_total", 0.0)) + float(
            getattr(self.ctx.runtime, "unload_seconds_total", 0.0)
        )
        self.ledger = RunLedger(
            topics=self.topics,
            started_at=self.started_at,
            retry_count_at_last_completion=0,
            switch_count_at_last_completion=self.ctx.runtime.switch_count,
            load_seconds_at_last_completion=initial_load_seconds,
        )
        self.worker_executor: concurrent.futures.ThreadPoolExecutor | None = None
        self.worker_task: WorkerTask | None = None
        self.topic_last_success_at = self.ledger.topic_last_success_at
        self.topic_started_counts = self.ledger.topic_started_counts
        self.topic_quarantined_counts = self.ledger.topic_quarantined_counts
        self.failure_signature_counts = self.ledger.failure_signature_counts
        self.topic_failure_signature_counts = self.ledger.topic_failure_signature_counts
        self.generate_size_cooldowns = self.ledger.generate_size_cooldowns
        self.generate_size_penalties = self.ledger.generate_size_penalties
        self.stable_item_progress = self.ledger.stable_item_progress
        self.retry_count_at_last_completion = self.ledger.retry_count_at_last_completion
        self.switch_count_at_last_completion = self.ledger.switch_count_at_last_completion
        self.load_seconds_at_last_completion = self.ledger.load_seconds_at_last_completion
        self.stop_reason = ""
        self.summary_written = False
        self.ctx.runtime.switch_callback = self._on_model_switch

    def run(self, *, max_cycles: int | None = None) -> None:
        cycles = 0
        try:
            while True:
                cycles += 1
                self._poll_worker_task()
                self._maybe_heartbeat(force=cycles == 1)
                self._maybe_raise_stall()
                self._refill_slots()
                ran_work = self._run_ready_steps()
                self._poll_worker_task()
                self._finalize_finished_jobs()
                self._maybe_raise_stall()
                if max_cycles is not None and cycles >= max_cycles:
                    self.stop_reason = f"max_cycles:{max_cycles}"
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
        except DeterministicFailureQuarantine as exc:
            self.stop_reason = str(exc)
            raise
        except RunAllStallDetected as exc:
            self.stop_reason = str(exc)
            raise
        except KeyboardInterrupt:
            self.stop_reason = "keyboard_interrupt"
            raise

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
            self._observe_job_stage(job)
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
            self._note_progress(f"step:{job.topic}:{step.step_id}")

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
        else:
            if job is not None:
                self._note_progress(f"worker:{job.topic}:{task.step.step_id}")
        return True

    def _handle_step_error(self, job, step: StepState, exc: Exception) -> None:
        job.item.attempts += 1
        job.last_error = str(exc)
        job.running_step_id = None
        signature = self._normalize_error_signature(exc)
        self._record_failure_occurrence(job, step, signature, exc)
        if job.status == "failed":
            return
        if job.item.attempts <= self.retry_limit:
            backoff_seconds = min(60, 5 * (2 ** (job.item.attempts - 1)))
            job.available_after = time.monotonic() + backoff_seconds
            log(
                f"[run_all retry] topic={job.topic} job={job.item_id} step={step.step_id} "
                f"attempt={job.item.attempts} backoff_seconds={backoff_seconds} "
                f"signature={signature} error={exc}"
            )
            return
        job.status = "failed"
        job.result = exc
        self._record_generate_size_failure(job, signature)
        log(
            f"[run_all failed] topic={job.topic} job={job.item_id} step={step.step_id} "
            f"attempts={job.item.attempts} signature={signature} error={exc}",
            level="ERROR",
        )
        self._note_progress(f"error:{job.topic}:{step.step_id}")

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
            self._observe_job_stage(job)
            self.claims.release(job)
            elapsed_ms = int((time.monotonic() - job.started_at) * 1000)
            if job.status == "complete":
                slot.completed_count += 1
                self.completed += 1
                now = time.monotonic()
                self.topic_last_success_at[job.topic] = now
                self.last_completion_at = now
                self.retry_count_at_last_completion = llm_run_retry_count()
                self.switch_count_at_last_completion = self.ctx.runtime.switch_count
                self.load_seconds_at_last_completion = self._runtime_load_seconds_total()
                self.ledger.retry_count_at_last_completion = self.retry_count_at_last_completion
                self.ledger.switch_count_at_last_completion = self.switch_count_at_last_completion
                self.ledger.load_seconds_at_last_completion = self.load_seconds_at_last_completion
                self._note_job_finished(job, outcome="complete")
                log(
                    f"[run_all finalize] topic={job.topic} job={job.item_id} outcome=complete "
                    f"elapsed_ms={elapsed_ms} persisted=yes detail={job.progress_detail or '-'} result={job.result!r}"
                )
            else:
                slot.failed_count += 1
                self.failed += 1
                self._note_job_finished(job, outcome="failed")
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
            self._note_job_started(job)
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

    def _runtime_load_seconds_total(self) -> float:
        if not hasattr(self, "ledger"):
            return float(getattr(self.ctx.runtime, "activation_seconds_total", 0.0)) + float(
                getattr(self.ctx.runtime, "unload_seconds_total", 0.0)
            )
        return runtime_load_seconds_total(self)

    def _stable_progress(self, stable_key: str, *, topic: str) -> StableItemProgress:
        return stable_progress(self, stable_key, topic=topic)

    def _observe_job_stage(self, job) -> StableItemProgress:
        previous_stage = ""
        stable_key = self._stable_item_key(job)
        existing = self.stable_item_progress.get(stable_key)
        if existing is not None:
            previous_stage = existing.last_stage
        progress = observe_job_stage(self, job)
        if progress.last_stage and progress.last_stage != previous_stage:
            self._note_progress(f"stage:{job.topic}:{progress.last_stage}")
        return progress

    def _note_job_started(self, job) -> None:
        note_job_started(self, job)

    def _note_job_finished(self, job, *, outcome: str) -> None:
        note_job_finished(self, job, outcome=outcome)

    def should_deprioritize_live_item(self, *, topic: str, stable_key: str) -> bool:
        return should_deprioritize_live_item(self, topic=topic, stable_key=stable_key)

    def _targets_for_topic(self, topic: str) -> tuple[str, ...]:
        if not self.ctx.multi_model:
            return (PRIMARY_MODEL.model_id,)
        return (PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id)

    def active_generate_size_exclusions(self) -> set[int]:
        return active_generate_size_exclusions(self)

    def generate_size_penalty_map(self) -> dict[int, int]:
        return generate_size_penalty_map(self)

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

    def _queue_snapshot_text(self) -> str:
        return queue_snapshot_text(self)

    def _on_model_switch(self, previous_model_id: str, next_model_id: str, runtime: LmRuntime) -> None:
        log(
            f"[run_all switch] from={previous_model_id or '-'} to={next_model_id} "
            f"reason=current_queue_empty switch_count={runtime.switch_count} "
            f"{self._queue_snapshot_text()}"
        )

    def _format_age_seconds(self, started_at: float) -> str:
        return format_age_seconds(self, started_at)

    def _dominant_failure_text(self, topic: str) -> str:
        return dominant_failure_text(self, topic)

    def _normalize_error_signature(self, exc: Exception) -> str:
        raw = str(exc).strip() or exc.__class__.__name__
        if raw.startswith("'") and raw.endswith("'") and len(raw) > 1:
            raw = raw[1:-1]
        lowered = raw.lower()
        if "failed to load model" in lowered and "insufficient system resources" in lowered:
            return "lmstudio_resource_guard"
        if isinstance(exc, KeyError):
            return f"KeyError:{raw}"
        normalized = _WHITESPACE_RE.sub(" ", raw)
        if len(normalized) > 160:
            normalized = normalized[:157] + "..."
        return f"{exc.__class__.__name__}:{normalized}"

    def _stable_item_key(self, job) -> str:
        return job.item.stable_key()

    def _record_generate_size_failure(self, job, signature: str) -> None:
        record_generate_size_failure(self, job, signature)

    def _record_failure_occurrence(self, job, step: StepState, signature: str, exc: Exception) -> None:
        record_failure_occurrence(self, job, step, signature, exc)

    def _maybe_heartbeat(self, *, force: bool) -> None:
        maybe_heartbeat(self, force=force)

    def _dominant_failure_global_text(self) -> str:
        return dominant_failure_global_text(self)

    def _maybe_raise_stall(self) -> None:
        maybe_raise_stall(self)

    def _note_progress(self, _reason: str) -> None:
        now = time.monotonic()
        self.last_progress_at = now
        self.retry_count_at_last_completion = llm_run_retry_count()
        self.switch_count_at_last_completion = self.ctx.runtime.switch_count
        self.load_seconds_at_last_completion = self._runtime_load_seconds_total()
        self.ledger.retry_count_at_last_completion = self.retry_count_at_last_completion
        self.ledger.switch_count_at_last_completion = self.switch_count_at_last_completion
        self.ledger.load_seconds_at_last_completion = self.load_seconds_at_last_completion

    def close(self) -> None:
        if not self.stop_reason:
            self.stop_reason = "closed"
        write_summary_artifacts(self)
        if self.worker_executor is not None:
            self.worker_executor.shutdown(wait=True, cancel_futures=False)
            self.worker_executor = None
