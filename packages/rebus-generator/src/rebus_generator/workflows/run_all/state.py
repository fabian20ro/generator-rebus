from __future__ import annotations

import time
from collections import Counter

from rebus_generator.platform.io.runtime_logging import audit, log

from .types import DeterministicFailureQuarantine, StableItemProgress, StepState

GENERATE_SIZE_COOLDOWN_SECONDS = 60 * 60
FAIRNESS_NO_PROGRESS_ADMISSIONS = 2
DETERMINISTIC_FAILURE_THRESHOLD = 3


def runtime_load_seconds_total(supervisor) -> float:
    return float(getattr(supervisor.ctx.runtime, "activation_seconds_total", 0.0)) + float(
        getattr(supervisor.ctx.runtime, "unload_seconds_total", 0.0)
    )


def stable_progress(supervisor, stable_key: str, *, topic: str) -> StableItemProgress:
    progress = supervisor.stable_item_progress.get(stable_key)
    if progress is None:
        progress = StableItemProgress(topic=topic, stable_key=stable_key, last_stage_change_at=supervisor.started_at)
        supervisor.stable_item_progress[stable_key] = progress
    return progress


def observe_job_stage(supervisor, job) -> StableItemProgress:
    stable_key = supervisor._stable_item_key(job)
    progress = stable_progress(supervisor, stable_key, topic=job.topic)
    stage = str(job.stage or "").strip()
    if stage and stage != progress.last_stage:
        progress.last_stage = stage
        progress.last_stage_change_at = time.monotonic()
        progress.seen_stages.add(stage)
    return progress


def note_job_started(supervisor, job) -> None:
    progress = observe_job_stage(supervisor, job)
    progress.last_started_at = time.monotonic()
    job._stage_seen_baseline = len(progress.seen_stages)
    supervisor.topic_started_counts[job.topic] = supervisor.topic_started_counts.get(job.topic, 0) + 1


def note_job_finished(supervisor, job, *, outcome: str) -> None:
    progress = observe_job_stage(supervisor, job)
    progress.last_finished_at = time.monotonic()
    progress.last_outcome = outcome
    baseline = int(getattr(job, "_stage_seen_baseline", len(progress.seen_stages)))
    if len(progress.seen_stages) > baseline:
        progress.no_progress_admissions = 0
        return
    progress.no_progress_admissions += 1


def should_deprioritize_live_item(supervisor, *, topic: str, stable_key: str) -> bool:
    if topic not in {"redefine", "simplify"}:
        return False
    progress = supervisor.stable_item_progress.get(stable_key)
    return bool(progress and progress.no_progress_admissions >= FAIRNESS_NO_PROGRESS_ADMISSIONS)


def active_generate_size_exclusions(supervisor) -> set[int]:
    now = time.monotonic()
    expired = [size for size, until in supervisor.generate_size_cooldowns.items() if until <= now]
    for size in expired:
        supervisor.generate_size_cooldowns.pop(size, None)
    return {size for size, until in supervisor.generate_size_cooldowns.items() if until > now}


def generate_size_penalty_map(supervisor) -> dict[int, int]:
    return {size: penalty for size, penalty in supervisor.generate_size_penalties.items() if penalty > 0}


def record_generate_size_failure(supervisor, job, signature: str) -> None:
    if job.topic != "generate":
        return
    size = int(job.item.payload.get("size") or 0)
    if size <= 0:
        return
    supervisor.generate_size_penalties[size] += 1
    supervisor.generate_size_cooldowns[size] = time.monotonic() + GENERATE_SIZE_COOLDOWN_SECONDS
    log(
        f"[run_all generate_cooldown] size={size} signature={signature} "
        f"cooldown={GENERATE_SIZE_COOLDOWN_SECONDS}s penalty={supervisor.generate_size_penalties[size]}"
    )


def should_continue_after_quarantine(job, step: StepState, exc: Exception) -> bool:
    if job.topic != "generate" or step.step_id != "fill_grid":
        return False
    lowered = str(exc).lower()
    return "rust phase-1 failed for" in lowered and "could not generate a valid filled grid" in lowered


def record_failure_occurrence(supervisor, job, step: StepState, signature: str, exc: Exception) -> None:
    failure_key = (job.topic, supervisor._stable_item_key(job), step.step_id, signature)
    supervisor.failure_signature_counts[failure_key] += 1
    supervisor.topic_failure_signature_counts.setdefault(job.topic, Counter())[signature] += 1
    count = supervisor.failure_signature_counts[failure_key]
    if count < DETERMINISTIC_FAILURE_THRESHOLD:
        return
    continue_run = should_continue_after_quarantine(job, step, exc)
    job.status = "failed"
    job.result = exc
    job.last_error = str(exc)
    supervisor.topic_quarantined_counts[job.topic] = supervisor.topic_quarantined_counts.get(job.topic, 0) + 1
    record_generate_size_failure(supervisor, job, signature)
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
    raise DeterministicFailureQuarantine(message)
