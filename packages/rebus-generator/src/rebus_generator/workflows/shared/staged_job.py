from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Generic, TypeVar

from rebus_generator.platform.llm.models import PRIMARY_MODEL, SECONDARY_MODEL
from rebus_generator.platform.orchestration import StageExecutionMode, WorkItem, WorkStage

ContextT = TypeVar("ContextT")


@dataclass(frozen=True)
class StageTransition:
    stage: str
    detail: str = ""
    status: str = "active"


StageSpec = WorkStage


class StagedJobState(Generic[ContextT]):
    def __init__(self, item: WorkItem) -> None:
        self.item = item
        self.topic = item.topic
        self.task_kind = item.task_kind
        self.item_id = item.item_id
        self.puzzle_id = item.puzzle_id
        self.words = set(item.words)
        self.preferred_model_id = item.preferred_model_id
        self.target_models = item.target_models
        self.stage = "init"
        self.status = "active"
        self.result: object = None
        self.available_after = 0.0
        self.started_at = time.monotonic()
        self.updated_at = self.started_at
        self.last_error = ""
        self.progress_detail = ""
        self.running_step_id: str | None = None

    def next_steps(self, ctx: ContextT) -> list[StageSpec]:
        raise NotImplementedError

    def _stage_spec(
        self,
        step_id: str,
        purpose: str,
        runner,
        *,
        execution_mode: StageExecutionMode,
        model_id: str | None,
        kind: str,
    ) -> StageSpec:
        return StageSpec(
            step_id=step_id,
            job_id=self.item_id,
            topic=self.topic,
            kind=kind,
            purpose=purpose,
            model_id=model_id,
            runner=runner,
            execution_mode=execution_mode,
        )

    def non_llm_stage(self, step_id: str, purpose: str, runner, *, execution_mode: StageExecutionMode = "inline_non_llm") -> StageSpec:
        return self._stage_spec(
            step_id,
            purpose,
            runner,
            execution_mode=execution_mode,
            model_id=None,
            kind="non_llm",
        )

    def background_stage(self, step_id: str, purpose: str, runner) -> StageSpec:
        return self.non_llm_stage(step_id, purpose, runner, execution_mode="background_non_llm")

    def llm_stage(self, step_id: str, purpose: str, model_id: str, runner) -> StageSpec:
        kind = "gemma" if model_id == PRIMARY_MODEL.model_id else "eurollm"
        return self._stage_spec(
            step_id,
            purpose,
            runner,
            execution_mode="llm",
            model_id=model_id,
            kind=kind,
        )

    def progress(self, stage: str, detail: str = "") -> StageTransition:
        self.stage = stage
        self.updated_at = time.monotonic()
        if detail:
            self.progress_detail = detail
        return StageTransition(stage=stage, detail=detail)

    def complete(self, result: object = None, *, stage: str = "done", detail: str = "") -> object:
        self.result = result
        self.stage = stage
        self.status = "complete"
        self.updated_at = time.monotonic()
        if detail:
            self.progress_detail = detail
        return result

    def fail(self, exc: Exception, *, stage: str | None = None, detail: str = "") -> Exception:
        if stage:
            self.stage = stage
        self.status = "failed"
        self.result = exc
        self.last_error = str(exc)
        self.updated_at = time.monotonic()
        if detail:
            self.progress_detail = detail
        return exc

