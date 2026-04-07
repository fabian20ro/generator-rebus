"""Loaded-model-aware scheduler for short pair-eval tasks."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from .lm_runtime import LmRuntime
from .model_manager import ModelConfig, PRIMARY_MODEL, SECONDARY_MODEL
from .runtime_logging import log

PayloadT = TypeVar("PayloadT")
ValueT = TypeVar("ValueT")


@dataclass
class WorkVote(Generic[ValueT]):
    model_id: str
    value: ValueT | None = None
    source: str = ""
    terminal: bool = False
    terminal_reason: str = ""


@dataclass
class WorkConclusion:
    complete: bool = False
    failed: bool = False
    skip_models: set[str] = field(default_factory=set)
    terminal_reason: str = ""


@dataclass
class WorkItem(Generic[PayloadT, ValueT]):
    item_id: str
    task_kind: str
    payload: PayloadT
    status: str = "pending"
    pending_models: set[str] = field(default_factory=set)
    votes: dict[str, WorkVote[ValueT]] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    terminal_reason: str = ""


@dataclass(frozen=True)
class WorkStep(Generic[PayloadT, ValueT]):
    model_id: str
    purpose: str
    runner: Callable[[WorkItem[PayloadT, ValueT], ModelConfig], WorkVote[ValueT]]
    prerequisite: Callable[[WorkItem[PayloadT, ValueT]], bool] = lambda item: True
    apply_result: Callable[[WorkItem[PayloadT, ValueT], WorkVote[ValueT]], None] | None = None
    can_conclude: Callable[[WorkItem[PayloadT, ValueT]], WorkConclusion] = lambda item: WorkConclusion()


@dataclass(frozen=True)
class SchedulerStats:
    completed: int
    failed: int
    per_model_batches: dict[str, int]
    per_model_calls: dict[str, int]
    switch_count: int
    activation_count: int


def _default_apply_result(item: WorkItem[PayloadT, ValueT], vote: WorkVote[ValueT]) -> None:
    item.votes[vote.model_id] = vote
    item.sources[vote.model_id] = vote.source
    item.pending_models.discard(vote.model_id)


class ModelAwareScheduler(Generic[PayloadT, ValueT]):
    def __init__(
        self,
        *,
        runtime: LmRuntime,
        models: list[ModelConfig],
        steps: list[WorkStep[PayloadT, ValueT]],
        task_label: str,
    ) -> None:
        self.runtime = runtime
        self.models = list(models)
        self.models_by_id = {model.model_id: model for model in self.models}
        self.steps_by_model = {step.model_id: step for step in steps}
        self.task_label = task_label
        self._batch_counts: dict[str, int] = defaultdict(int)
        self._call_counts: dict[str, int] = defaultdict(int)

    def run(self, items: list[WorkItem[PayloadT, ValueT]]) -> SchedulerStats:
        while True:
            ready = self._ready_by_model(items)
            if not any(ready.values()):
                break
            model_id = self._choose_model(ready)
            batch = ready.get(model_id, [])
            if not batch:
                break
            model = self._ensure_active(self.models_by_id[model_id])
            self._batch_counts[model_id] += 1
            log(
                f"[scheduler batch] task={self.task_label} model={model.display_name} "
                f"size={len(batch)} loaded={getattr(self.runtime, 'current_model_label', '') or '-'}"
            )
            step = self.steps_by_model[model_id]
            for item in batch:
                if item.status != "pending":
                    continue
                vote = step.runner(item, model)
                self._call_counts[model_id] += 1
                apply_result = step.apply_result or _default_apply_result
                apply_result(item, vote)
                conclusion = step.can_conclude(item)
                if conclusion.skip_models:
                    item.pending_models.difference_update(conclusion.skip_models)
                if vote.terminal and item.status == "pending":
                    item.status = "failed"
                    item.terminal_reason = vote.terminal_reason or conclusion.terminal_reason or "terminal_vote"
                elif conclusion.failed and item.status == "pending":
                    item.status = "failed"
                    item.terminal_reason = conclusion.terminal_reason or "failed"
                elif conclusion.complete and item.status == "pending":
                    item.status = "complete"
                    item.terminal_reason = conclusion.terminal_reason
            self._finalize_fully_drained(items)

        completed = sum(1 for item in items if item.status == "complete")
        failed = sum(1 for item in items if item.status == "failed")
        return SchedulerStats(
            completed=completed,
            failed=failed,
            per_model_batches=dict(self._batch_counts),
            per_model_calls=dict(self._call_counts),
            switch_count=int(getattr(self.runtime, "switch_count", 0) or 0),
            activation_count=int(getattr(self.runtime, "activation_count", 0) or 0),
        )

    def _ready_by_model(self, items: list[WorkItem[PayloadT, ValueT]]) -> dict[str, list[WorkItem[PayloadT, ValueT]]]:
        ready: dict[str, list[WorkItem[PayloadT, ValueT]]] = {model.model_id: [] for model in self.models}
        for item in items:
            if item.status != "pending":
                continue
            for model_id in list(item.pending_models):
                step = self.steps_by_model.get(model_id)
                if step is None:
                    continue
                if not step.prerequisite(item):
                    continue
                ready.setdefault(model_id, []).append(item)
        return ready

    def _choose_model(self, ready: dict[str, list[WorkItem[PayloadT, ValueT]]]) -> str:
        if hasattr(self.runtime, "sync"):
            self.runtime.sync()
        current_id = str(getattr(self.runtime, "current_model_id", "") or "")
        if current_id and ready.get(current_id):
            return current_id
        for model in self.models:
            if ready.get(model.model_id):
                return model.model_id
        return self.models[0].model_id

    def _ensure_active(self, model: ModelConfig) -> ModelConfig:
        primary = getattr(self.runtime, "primary", PRIMARY_MODEL)
        if model.model_id == getattr(primary, "model_id", "") and hasattr(self.runtime, "activate_primary"):
            active = self.runtime.activate_primary()
            if self._is_model_like(active, model.model_id):
                return active
        secondary = getattr(self.runtime, "secondary", SECONDARY_MODEL)
        if model.model_id == getattr(secondary, "model_id", "") and hasattr(self.runtime, "activate_secondary"):
            active = self.runtime.activate_secondary()
            if self._is_model_like(active, model.model_id):
                return active
        if hasattr(self.runtime, "ensure_active"):
            active = self.runtime.ensure_active(model)
            if self._is_model_like(active, model.model_id):
                return active
        if hasattr(self.runtime, "activate"):
            active = self.runtime.activate(model)
            if self._is_model_like(active, model.model_id):
                return active
        return model

    @staticmethod
    def _is_model_like(active: object, expected_model_id: str) -> bool:
        return str(getattr(active, "model_id", "") or "") == expected_model_id

    def _finalize_fully_drained(self, items: list[WorkItem[PayloadT, ValueT]]) -> None:
        for item in items:
            if item.status != "pending":
                continue
            if item.pending_models:
                continue
            item.status = "complete"
