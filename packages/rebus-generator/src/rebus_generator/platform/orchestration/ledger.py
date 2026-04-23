from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field

from rebus_generator.platform.io.runtime_logging import audit, log

GENERATE_SIZE_COOLDOWN_SECONDS = 60 * 60
FAIRNESS_NO_PROGRESS_ADMISSIONS = 2
DETERMINISTIC_FAILURE_THRESHOLD = 3


@dataclass
class StableItemProgress:
    topic: str
    stable_key: str
    seen_stages: set[str] = field(default_factory=set)
    last_stage: str = ""
    last_stage_change_at: float = 0.0
    no_progress_admissions: int = 0
    last_started_at: float = 0.0
    last_finished_at: float = 0.0
    last_outcome: str = ""


@dataclass
class RunLedger:
    topics: list[str]
    started_at: float
    retry_count_at_last_completion: int = 0
    switch_count_at_last_completion: int = 0
    load_seconds_at_last_completion: float = 0.0
    topic_last_success_at: dict[str, float] = field(init=False)
    topic_started_counts: dict[str, int] = field(init=False)
    topic_quarantined_counts: dict[str, int] = field(init=False)
    failure_signature_counts: Counter[tuple[str, str, str, str]] = field(default_factory=Counter)
    topic_failure_signature_counts: dict[str, Counter[str]] = field(init=False)
    generate_size_cooldowns: dict[int, float] = field(default_factory=dict)
    generate_size_penalties: Counter[int] = field(default_factory=Counter)
    stable_item_progress: dict[str, StableItemProgress] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.topic_last_success_at = {topic: 0.0 for topic in self.topics}
        self.topic_started_counts = {topic: 0 for topic in self.topics}
        self.topic_quarantined_counts = {topic: 0 for topic in self.topics}
        self.topic_failure_signature_counts = {topic: Counter() for topic in self.topics}

    def runtime_load_seconds_total(self, supervisor) -> float:
        return float(getattr(supervisor.ctx.runtime, "activation_seconds_total", 0.0)) + float(
            getattr(supervisor.ctx.runtime, "unload_seconds_total", 0.0)
        )

    def stable_progress(self, supervisor, stable_key: str, *, topic: str) -> StableItemProgress:
        progress = self.stable_item_progress.get(stable_key)
        if progress is None:
            progress = StableItemProgress(topic=topic, stable_key=stable_key, last_stage_change_at=self.started_at)
            self.stable_item_progress[stable_key] = progress
        return progress

    def observe_job_stage(self, supervisor, job) -> StableItemProgress:
        stable_key = supervisor._stable_item_key(job)
        progress = self.stable_progress(supervisor, stable_key, topic=job.topic)
        stage = str(job.stage or "").strip()
        if stage and stage != progress.last_stage:
            progress.last_stage = stage
            progress.last_stage_change_at = time.monotonic()
            progress.seen_stages.add(stage)
        return progress

    def note_job_started(self, supervisor, job) -> None:
        progress = self.observe_job_stage(supervisor, job)
        progress.last_started_at = time.monotonic()
        job._stage_seen_baseline = len(progress.seen_stages)
        self.topic_started_counts[job.topic] = self.topic_started_counts.get(job.topic, 0) + 1

    def note_job_finished(self, supervisor, job, *, outcome: str) -> None:
        progress = self.observe_job_stage(supervisor, job)
        progress.last_finished_at = time.monotonic()
        progress.last_outcome = outcome
        baseline = int(getattr(job, "_stage_seen_baseline", len(progress.seen_stages)))
        if len(progress.seen_stages) > baseline:
            progress.no_progress_admissions = 0
            return
        progress.no_progress_admissions += 1

    def should_deprioritize_live_item(self, *, topic: str, stable_key: str) -> bool:
        if topic not in {"redefine", "retitle", "simplify"}:
            return False
        progress = self.stable_item_progress.get(stable_key)
        return bool(progress and progress.no_progress_admissions >= FAIRNESS_NO_PROGRESS_ADMISSIONS)

    def deprioritize_live_item(self, supervisor, *, topic: str, stable_key: str, reason: str) -> None:
        progress = self.stable_progress(supervisor, stable_key, topic=topic)
        progress.no_progress_admissions = max(progress.no_progress_admissions, FAIRNESS_NO_PROGRESS_ADMISSIONS)
        log(f"[run_all {topic} no_change deprioritized] item={stable_key} reason={reason}")

    def active_generate_size_exclusions(self) -> set[int]:
        now = time.monotonic()
        expired = [size for size, until in self.generate_size_cooldowns.items() if until <= now]
        for size in expired:
            self.generate_size_cooldowns.pop(size, None)
        return {size for size, until in self.generate_size_cooldowns.items() if until > now}

    def generate_size_penalty_map(self) -> dict[int, int]:
        return {size: penalty for size, penalty in self.generate_size_penalties.items() if penalty > 0}

    def record_generate_size_failure(self, job, signature: str) -> None:
        if job.topic != "generate":
            return
        size = int(job.item.payload.get("size") or 0)
        if size <= 0:
            return
        self.generate_size_penalties[size] += 1
        self.generate_size_cooldowns[size] = time.monotonic() + GENERATE_SIZE_COOLDOWN_SECONDS
        log(
            f"[run_all generate_cooldown] size={size} signature={signature} "
            f"cooldown={GENERATE_SIZE_COOLDOWN_SECONDS}s penalty={self.generate_size_penalties[size]}"
        )

    @staticmethod
    def _is_generate_size_dead_end(step_id: str, exc: Exception) -> bool:
        lowered = str(exc).lower()
        if step_id == "fill_grid":
            return "rust phase-1 failed for" in lowered and "could not generate a valid filled grid" in lowered
        if step_id == "rewrite_prepare_round":
            return (
                lowered.startswith("could not prepare a publishable")
                and ("missing definitions:" in lowered or "incomplete pair evaluation:" in lowered)
            )
        return False

    @staticmethod
    def should_continue_after_quarantine(job, step, exc: Exception) -> bool:
        if job.topic != "generate":
            return False
        return RunLedger._is_generate_size_dead_end(step.step_id, exc)

    def record_failure_occurrence(self, supervisor, job, step, signature: str, exc: Exception, *, quarantine_error_cls) -> None:
        failure_key = (job.topic, supervisor._stable_item_key(job), step.step_id, signature)
        self.failure_signature_counts[failure_key] += 1
        self.topic_failure_signature_counts.setdefault(job.topic, Counter())[signature] += 1
        count = self.failure_signature_counts[failure_key]
        if count < DETERMINISTIC_FAILURE_THRESHOLD:
            return
        continue_run = self.should_continue_after_quarantine(job, step, exc)
        job.status = "failed"
        job.result = exc
        job.last_error = str(exc)
        self.topic_quarantined_counts[job.topic] = self.topic_quarantined_counts.get(job.topic, 0) + 1
        self.record_generate_size_failure(job, signature)
        message = (
            f"Quarantined deterministic failure after {count} occurrences: "
            f"topic={job.topic} item={supervisor._stable_item_key(job)} stage={step.step_id} signature={signature}"
        )
        audit(
            "run_all_quarantine",
            component="run_all",
            payload={
                "topic": job.topic,
                "item": supervisor._stable_item_key(job),
                "stage": step.step_id,
                "signature": signature,
                "count": count,
            },
        )
        if continue_run:
            log(f"[run_all quarantine] {message} action=continue", level="WARN")
            return
        log(f"[run_all quarantine] {message}", level="ERROR")
        raise quarantine_error_cls(message)
