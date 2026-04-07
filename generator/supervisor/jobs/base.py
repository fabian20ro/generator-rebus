from __future__ import annotations

import time
from typing import Callable

from ...core.model_manager import PRIMARY_MODEL, SECONDARY_MODEL
from ..types import RunAllContext, StepState, SupervisorWorkItem


class JobState:
    def __init__(self, item: SupervisorWorkItem) -> None:
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

    def next_steps(self, ctx: RunAllContext) -> list[StepState]:
        raise NotImplementedError

    def _non_llm_step(
        self,
        step_id: str,
        purpose: str,
        runner: Callable[[RunAllContext], object],
        *,
        execution_mode: str = "inline_non_llm",
    ) -> StepState:
        return StepState(
            step_id=step_id,
            job_id=self.item_id,
            topic=self.topic,
            kind="non_llm",
            purpose=purpose,
            model_id=None,
            runner=runner,
            execution_mode=execution_mode,
        )

    def _background_step(self, step_id: str, purpose: str, runner: Callable[[RunAllContext], object]) -> StepState:
        return self._non_llm_step(step_id, purpose, runner, execution_mode="background_non_llm")

    def _llm_step(
        self,
        step_id: str,
        purpose: str,
        model_id: str,
        runner: Callable[[RunAllContext], object],
    ) -> StepState:
        kind = "gemma" if model_id == PRIMARY_MODEL.model_id else "eurollm"
        return StepState(
            step_id=step_id,
            job_id=self.item_id,
            topic=self.topic,
            kind=kind,
            purpose=purpose,
            model_id=model_id,
            runner=runner,
            execution_mode="llm",
        )

    def _complete(self, result: object = None, *, stage: str = "done", detail: str = "") -> object:
        self.result = result
        self.stage = stage
        self.status = "complete"
        self.updated_at = time.monotonic()
        if detail:
            self.progress_detail = detail
        return result

    def _progress(self, stage: str, detail: str = "") -> None:
        self.stage = stage
        self.updated_at = time.monotonic()
        if detail:
            self.progress_detail = detail

