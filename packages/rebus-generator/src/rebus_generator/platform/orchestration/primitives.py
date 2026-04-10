from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

StageExecutionMode = Literal["llm", "inline_non_llm", "background_non_llm"]


@dataclass
class WorkItem:
    item_id: str
    topic: str
    task_kind: str
    preferred_model_id: str
    target_models: tuple[str, ...]
    payload: dict[str, Any] = field(default_factory=dict)
    puzzle_id: str | None = None
    words: set[str] = field(default_factory=set)
    attempts: int = 0
    available_after: float = 0.0
    admitted_at: float = field(default_factory=time.monotonic)

    def stable_key(self) -> str:
        if self.puzzle_id:
            return f"{self.topic}:puzzle:{self.puzzle_id}"
        if self.topic == "generate":
            size = int(self.payload.get("size") or 0)
            return f"{self.topic}:size:{size}"
        if self.topic == "simplify":
            word = str(self.payload.get("word") or "").strip().upper()
            if word:
                return f"{self.topic}:word:{word}"
        if self.words:
            joined = ",".join(sorted(self.words))
            return f"{self.topic}:words:{joined}"
        return self.item_id


@dataclass
class WorkStage:
    step_id: str
    job_id: str
    topic: str
    kind: str
    purpose: str
    model_id: str | None
    runner: Callable[[Any], object] = field(repr=False)
    execution_mode: StageExecutionMode = "inline_non_llm"

