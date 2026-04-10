from __future__ import annotations

from .types import DeterministicFailureQuarantine, StableItemProgress, StepState


def runtime_load_seconds_total(supervisor) -> float:
    return supervisor.ledger.runtime_load_seconds_total(supervisor)


def stable_progress(supervisor, stable_key: str, *, topic: str) -> StableItemProgress:
    return supervisor.ledger.stable_progress(supervisor, stable_key, topic=topic)


def observe_job_stage(supervisor, job) -> StableItemProgress:
    return supervisor.ledger.observe_job_stage(supervisor, job)


def note_job_started(supervisor, job) -> None:
    supervisor.ledger.note_job_started(supervisor, job)


def note_job_finished(supervisor, job, *, outcome: str) -> None:
    supervisor.ledger.note_job_finished(supervisor, job, outcome=outcome)


def should_deprioritize_live_item(supervisor, *, topic: str, stable_key: str) -> bool:
    return supervisor.ledger.should_deprioritize_live_item(topic=topic, stable_key=stable_key)


def active_generate_size_exclusions(supervisor) -> set[int]:
    return supervisor.ledger.active_generate_size_exclusions()


def generate_size_penalty_map(supervisor) -> dict[int, int]:
    return supervisor.ledger.generate_size_penalty_map()


def record_generate_size_failure(supervisor, job, signature: str) -> None:
    supervisor.ledger.record_generate_size_failure(job, signature)


def should_continue_after_quarantine(supervisor, job, step: StepState, exc: Exception) -> bool:
    return supervisor.ledger.should_continue_after_quarantine(job, step, exc)


def record_failure_occurrence(supervisor, job, step: StepState, signature: str, exc: Exception) -> None:
    supervisor.ledger.record_failure_occurrence(
        supervisor,
        job,
        step,
        signature,
        exc,
        quarantine_error_cls=DeterministicFailureQuarantine,
    )
