from __future__ import annotations

from rebus_generator.workflows.shared.staged_job import StagedJobState
from ..types import RunAllContext, StepState


class JobState(StagedJobState[RunAllContext]):
    def _non_llm_step(
        self,
        step_id: str,
        purpose: str,
        runner,
        *,
        execution_mode: str = "inline_non_llm",
        phase: str | None = None,
    ) -> StepState:
        return self.non_llm_stage(step_id, purpose, runner, execution_mode=execution_mode, phase=phase)

    def _background_step(self, step_id: str, purpose: str, runner) -> StepState:
        return self.background_stage(step_id, purpose, runner)

    def _llm_step(
        self,
        step_id: str,
        purpose: str,
        model_id: str,
        runner,
        *,
        phase: str | None = None,
        coalesce_key: str | None = None,
    ) -> StepState:
        return self.llm_stage(step_id, purpose, model_id, runner, phase=phase, coalesce_key=coalesce_key)

    def _complete(self, result: object = None, *, stage: str = "done", detail: str = "") -> object:
        return self.complete(result, stage=stage, detail=detail)

    def _progress(self, stage: str, detail: str = "") -> None:
        self.progress(stage, detail)

    def plan_ready_units(self, ctx):
        return self.next_steps(ctx)
