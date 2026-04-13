from __future__ import annotations

import json
import time
from collections import Counter

from rebus_generator.platform.io.runtime_logging import log
from rebus_generator.platform.llm.llm_client import llm_run_retry_count, llm_run_stats_snapshot

from .types import RunAllStallDetected

STALL_MIN_RETRIES = 5
STALL_MIN_SWITCHES = 2
STALL_MIN_LOAD_SECONDS = 15.0


def queue_counts_by_topic(supervisor) -> dict[str, int]:
    counts = {topic: 0 for topic in supervisor.topics}
    for topic in supervisor.topics:
        if supervisor.slots[topic].active_job is not None:
            counts[topic] += 1
    for item in supervisor.pending_items:
        counts[item.topic] = counts.get(item.topic, 0) + 1
    return counts


def active_slot_text(supervisor) -> str:
    parts = []
    now = time.monotonic()
    for topic in supervisor.topics:
        job = supervisor.slots[topic].active_job
        if job is None:
            parts.append(f"{topic}=-")
        else:
            duration = int(now - job.started_at)
            parts.append(f"{topic}={job.item_id}[{duration}s]")
    return " ".join(parts)


def worker_slot_text(supervisor) -> str:
    if supervisor.worker_task is None:
        return "-"
    duration = int(time.monotonic() - supervisor.worker_task.started_at)
    return f"{supervisor.worker_task.step.topic}:{supervisor.worker_task.step.step_id}[{duration}s]"


def queue_snapshot_text(supervisor) -> str:
    model_counts = supervisor._runnable_counts_by_model()
    topic_counts = queue_counts_by_topic(supervisor)
    model_text = " ".join(f"{model}={count}" for model, count in sorted(model_counts.items()))
    topic_text = " ".join(f"{topic}={count}" for topic, count in sorted(topic_counts.items()))
    return (
        f"queues_model=({model_text}) queues_topic=({topic_text}) "
        f"active_slots=({active_slot_text(supervisor)}) worker={worker_slot_text(supervisor)} "
        f"completed={supervisor.completed} failed={supervisor.failed}"
    )


def format_age_seconds(supervisor, started_at: float) -> str:
    if started_at <= 0:
        return "-"
    return f"{int(max(0.0, time.monotonic() - started_at))}s"


def dominant_failure_text(supervisor, topic: str) -> str:
    topic_counts = supervisor.topic_failure_signature_counts.get(topic)
    if not topic_counts:
        return "-"
    signature, count = topic_counts.most_common(1)[0]
    return f"{signature} x{count}"


def maybe_heartbeat(supervisor, *, force: bool) -> None:
    now = time.monotonic()
    supervisor.ctx.runtime.sync()
    current_model = supervisor.ctx.runtime.current_model_label or "-"
    
    # Calculate state signature to detect meaningful changes
    active_jobs = [
        (topic, slot.active_job.item_id if slot.active_job else None)
        for topic, slot in sorted(supervisor.slots.items())
    ]
    worker_job = supervisor.worker_task.step.step_id if supervisor.worker_task else None
    state_signature = (current_model, tuple(active_jobs), worker_job, supervisor.completed, supervisor.failed)
    
    last_sig = getattr(supervisor, "_last_heartbeat_signature", None)
    elapsed = now - supervisor.last_heartbeat_at
    
    if not force and state_signature == last_sig and elapsed < supervisor.heartbeat_seconds:
        return
        
    supervisor.last_heartbeat_at = now
    supervisor._last_heartbeat_signature = state_signature
    
    blocked = sum(
        1
        for slot in supervisor.slots.values()
        if slot.active_job is not None and slot.active_job.available_after > now
    )
    success_text = " ".join(
        f"{topic}={format_age_seconds(supervisor, supervisor.topic_last_success_at.get(topic, 0.0))}"
        for topic in supervisor.topics
    )
    failure_text = " ".join(
        f"{topic}={dominant_failure_text(supervisor, topic)}"
        for topic in supervisor.topics
    )
    log(
        f"[run_all heartbeat] loaded={current_model} "
        f"blocked={blocked} worker={worker_slot_text(supervisor)} "
        f"last_success=({success_text}) dominant_failures=({failure_text}) "
        f"{queue_snapshot_text(supervisor)}"
    )


def dominant_failure_global_text(supervisor) -> str:
    combined: Counter[str] = Counter()
    for topic in supervisor.topics:
        combined.update(supervisor.topic_failure_signature_counts.get(topic, Counter()))
    if not combined:
        return "-"
    signature, count = combined.most_common(1)[0]
    return f"{signature} x{count}"


def maybe_raise_stall(supervisor) -> None:
    stall_seconds = max(0, int(getattr(supervisor.ctx, "llm_stall_seconds", 0)))
    if stall_seconds <= 0:
        return
    idle_seconds = time.monotonic() - getattr(supervisor, "last_progress_at", supervisor.last_completion_at)
    if idle_seconds < stall_seconds:
        return
    retry_delta = llm_run_retry_count() - supervisor.retry_count_at_last_completion
    switch_delta = supervisor.ctx.runtime.switch_count - supervisor.switch_count_at_last_completion
    load_delta = supervisor._runtime_load_seconds_total() - supervisor.load_seconds_at_last_completion
    if (
        retry_delta < STALL_MIN_RETRIES
        and switch_delta < STALL_MIN_SWITCHES
        and load_delta < STALL_MIN_LOAD_SECONDS
    ):
        return
    message = (
        "Throughput stall: "
        f"idle_seconds={int(idle_seconds)} retry_delta={retry_delta} "
        f"switch_delta={switch_delta} load_seconds_delta={load_delta:.1f} "
        f"dominant_failure={dominant_failure_global_text(supervisor)}"
    )
    log(f"[run_all stall] {message}", level="ERROR")
    raise RunAllStallDetected(message)


def topic_summary(supervisor, topic: str) -> dict[str, object]:
    slot = supervisor.slots[topic]
    last_success = supervisor.topic_last_success_at.get(topic, 0.0)
    last_success_age = None if last_success <= 0 else int(max(0.0, time.monotonic() - last_success))
    return {
        "started": supervisor.topic_started_counts.get(topic, 0),
        "completed": slot.completed_count,
        "failed": slot.failed_count,
        "quarantined": supervisor.topic_quarantined_counts.get(topic, 0),
        "last_success_age_seconds": last_success_age,
        "dominant_failure": dominant_failure_text(supervisor, topic),
    }


def build_summary_payload(supervisor) -> dict[str, object]:
    activation_seconds_total = float(getattr(supervisor.ctx.runtime, "activation_seconds_total", 0.0))
    unload_seconds_total = float(getattr(supervisor.ctx.runtime, "unload_seconds_total", 0.0))
    return {
        "stop_reason": supervisor.stop_reason or "closed",
        "started_at_monotonic": round(supervisor.started_at, 3),
        "completed": supervisor.completed,
        "failed": supervisor.failed,
        "switch_count": supervisor.ctx.runtime.switch_count,
        "loaded_model_drain_switches": getattr(supervisor, "loaded_model_drain_switches", 0),
        "nested_activation_warnings": getattr(supervisor, "nested_activation_warnings", 0),
        "unit_trace_path": str(getattr(supervisor, "unit_trace_path", "")),
        "unit_counts_by_purpose": dict(getattr(supervisor, "unit_purpose_counts", {})),
        "topic_drain_counts": dict(getattr(supervisor, "topic_drain_counts", {})),
        "activation_count": getattr(supervisor.ctx.runtime, "activation_count", 0),
        "unload_count": getattr(supervisor.ctx.runtime, "unload_count", 0),
        "activation_seconds_total": round(activation_seconds_total, 3),
        "unload_seconds_total": round(unload_seconds_total, 3),
        "activation_overhead_seconds": round(activation_seconds_total + unload_seconds_total, 3),
        "llm": llm_run_stats_snapshot(),
        "topics": {topic: topic_summary(supervisor, topic) for topic in supervisor.topics},
    }


def write_summary_artifacts(supervisor) -> None:
    if supervisor.summary_written:
        return
    supervisor.summary_written = True
    payload = build_summary_payload(supervisor)
    summary_path = supervisor.ctx.run_dir / "run_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    topic_text = " ".join(
        f"{topic}=started:{payload['topics'][topic]['started']},"
        f"completed:{payload['topics'][topic]['completed']},"
        f"failed:{payload['topics'][topic]['failed']},"
        f"quarantined:{payload['topics'][topic]['quarantined']}"
        for topic in supervisor.topics
    )
    log(
        f"[run_all summary] stop={payload['stop_reason']} "
        f"switches={payload['switch_count']} llm_retries={payload['llm']['retry_count']} "
        f"{topic_text}"
    )
