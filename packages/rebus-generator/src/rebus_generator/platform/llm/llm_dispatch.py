"""Mandatory production dispatch entrypoint for LM Studio-backed LLM work."""

from __future__ import annotations

from typing import TypeVar

from .lm_runtime import LmRuntime
from .model_aware_scheduler import (
    ModelAwareScheduler,
    SchedulerStats,
    WorkConclusion,
    WorkItem,
    WorkStep,
    WorkVote,
)
from .models import ModelConfig

PayloadT = TypeVar("PayloadT")
ValueT = TypeVar("ValueT")


def run_llm_workload(
    *,
    runtime: LmRuntime,
    models: list[ModelConfig],
    items: list[WorkItem[PayloadT, ValueT]],
    steps: list[WorkStep[PayloadT, ValueT]],
    task_label: str,
) -> SchedulerStats:
    return ModelAwareScheduler(
        runtime=runtime,
        models=models,
        steps=steps,
        task_label=task_label,
    ).run(items)


def run_single_model_workload(
    *,
    runtime: LmRuntime,
    model: ModelConfig,
    items: list[WorkItem[PayloadT, ValueT]],
    purpose: str,
    runner,
    task_label: str,
    prerequisite=None,
    apply_result=None,
    can_conclude=None,
) -> SchedulerStats:
    return run_llm_workload(
        runtime=runtime,
        models=[model],
        items=items,
        steps=[
            WorkStep(
                model_id=model.model_id,
                purpose=purpose,
                runner=runner,
                prerequisite=prerequisite or (lambda item: True),
                apply_result=apply_result,
                can_conclude=can_conclude or (lambda item: WorkConclusion(complete=True)),
            )
        ],
        task_label=task_label,
    )


def run_single_model_call(
    *,
    runtime: LmRuntime,
    model: ModelConfig,
    purpose: str,
    task_label: str,
    callback,
) -> ValueT:
    item = WorkItem[dict[str, object], ValueT](
        item_id="single",
        task_kind=task_label,
        payload={},
        pending_models={model.model_id},
    )

    def _runner(work_item: WorkItem[dict[str, object], ValueT], active_model: ModelConfig) -> WorkVote[ValueT]:
        return WorkVote(model_id=active_model.model_id, value=callback(active_model), source="ok")

    run_single_model_workload(
        runtime=runtime,
        model=model,
        items=[item],
        purpose=purpose,
        runner=_runner,
        task_label=task_label,
    )
    vote = item.votes.get(model.model_id)
    return vote.value if vote is not None else None


def initial_generation_model(runtime: LmRuntime) -> ModelConfig:
    return runtime.secondary if getattr(runtime, "multi_model", False) else runtime.primary


def next_generation_model(runtime: LmRuntime, current_model: ModelConfig) -> ModelConfig:
    if not getattr(runtime, "multi_model", False):
        return runtime.primary
    if current_model.model_id == runtime.secondary.model_id:
        return runtime.primary
    return runtime.secondary


__all__ = [
    "SchedulerStats",
    "WorkConclusion",
    "WorkItem",
    "WorkStep",
    "WorkVote",
    "initial_generation_model",
    "next_generation_model",
    "run_single_model_call",
    "run_llm_workload",
    "run_single_model_workload",
]
