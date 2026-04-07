from __future__ import annotations

from ..types import SupervisorWorkItem
from .base import JobState
from .generate import GenerateJobState
from .redefine import RedefineJobState
from .retitle import RetitleJobState
from .simplify import SimplifyJobState


def build_job(item: SupervisorWorkItem) -> JobState:
    if item.topic == "generate":
        return GenerateJobState(item)
    if item.topic == "redefine":
        return RedefineJobState(item)
    if item.topic == "retitle":
        return RetitleJobState(item)
    if item.topic == "simplify":
        return SimplifyJobState(item)
    raise ValueError(f"Unsupported topic {item.topic}")


__all__ = [
    "JobState",
    "GenerateJobState",
    "RedefineJobState",
    "RetitleJobState",
    "SimplifyJobState",
    "build_job",
]

