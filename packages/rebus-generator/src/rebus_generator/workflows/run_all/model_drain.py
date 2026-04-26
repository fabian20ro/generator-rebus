from __future__ import annotations

from dataclasses import dataclass
from collections import Counter

from rebus_generator.platform.llm.llm_dispatch import (
    WorkConclusion as LlmWorkConclusion,
    WorkItem as LlmWorkItem,
    WorkStep as LlmWorkStep,
    WorkVote as LlmWorkVote,
    run_llm_workload,
)
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.llm.models import PRIMARY_MODEL, SECONDARY_MODEL

from .types import StepState


@dataclass(frozen=True)
class DrainedUnit:
    job_id: str
    step_id: str
    phase: str

    @classmethod
    def from_unit(cls, unit: StepState) -> "DrainedUnit":
        return cls(
            job_id=unit.job_id,
            step_id=unit.step_id,
            phase=unit.phase or "",
        )

    def key(self) -> tuple[str, str, str]:
        return (self.job_id, self.step_id, self.phase)


class ModelDrain:
    """Runs ready LLM units while preserving loaded-model drain behavior."""

    def __init__(self, runtime: LmRuntime) -> None:
        self.runtime = runtime

    def choose_model_for_units(self, units: list[StepState]) -> str:
        self.runtime.sync()
        current_model_id = self.runtime.current_model_id
        ready_by_model = Counter(unit.model_id for unit in units if unit.model_id)

        if current_model_id and ready_by_model.get(current_model_id, 0) > 0:
            return current_model_id
        if ready_by_model.get(PRIMARY_MODEL.model_id, 0) > 0:
            return PRIMARY_MODEL.model_id
        if ready_by_model.get(SECONDARY_MODEL.model_id, 0) > 0:
            return SECONDARY_MODEL.model_id
        return current_model_id or PRIMARY_MODEL.model_id

    def drain(
        self,
        units: list[StepState],
        *,
        run_unit,
        after_unit=None,
    ) -> list[DrainedUnit]:
        items: list[LlmWorkItem[StepState, bool]] = [
            LlmWorkItem(
                item_id=f"{unit.job_id}:{unit.step_id}:{unit.phase or ''}",
                task_kind=unit.purpose,
                payload=unit,
                pending_models={unit.model_id} if unit.model_id else set(),
            )
            for unit in units
            if unit.model_id
        ]
        if not items:
            return []

        drained: list[DrainedUnit] = []

        def _runner(item: LlmWorkItem[StepState, bool], model) -> LlmWorkVote[bool]:
            unit = item.payload
            run_unit(unit)
            drained_unit = DrainedUnit.from_unit(unit)
            drained.append(drained_unit)
            if after_unit is not None:
                after_unit(unit)
            return LlmWorkVote(model_id=model.model_id, value=True, source="run_all")

        run_llm_workload(
            runtime=self.runtime,
            models=[PRIMARY_MODEL, SECONDARY_MODEL],
            items=items,
            steps=[
                LlmWorkStep(
                    model_id=PRIMARY_MODEL.model_id,
                    purpose="run_all_llm_unit",
                    runner=_runner,
                    can_conclude=lambda _item: LlmWorkConclusion(complete=True),
                ),
                LlmWorkStep(
                    model_id=SECONDARY_MODEL.model_id,
                    purpose="run_all_llm_unit",
                    runner=_runner,
                    can_conclude=lambda _item: LlmWorkConclusion(complete=True),
                ),
            ],
            task_label="run_all_llm_units",
        )
        return drained
