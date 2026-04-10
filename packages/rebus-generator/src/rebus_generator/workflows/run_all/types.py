from __future__ import annotations

import concurrent.futures
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class DeterministicFailureQuarantine(RuntimeError):
    """Raised when one stable work item repeats the same failure signature."""


class RunAllStallDetected(RuntimeError):
    """Raised when the run is active but no longer making progress."""


@dataclass
class RunAllContext:
    supabase: object
    ai_client: object
    rate_client: object
    runtime: object
    store: object
    run_dir: Path
    batch_output_root: Path
    words_path: Path
    multi_model: bool
    dry_run: bool
    generate_rewrite_rounds: int
    redefine_rounds: int
    verify_candidates: int
    simplify_batch_size: int
    preflight_enabled: bool = True
    llm_stall_seconds: int = 900
    llm_truncation_threshold: int = 3
    gemma_verify_reasoning: str | None = "minimal"
    gemma_rate_reasoning: str | None = "minimal"
    gemma_title_generate_reasoning: str | None = "minimal"
    gemma_title_rate_reasoning: str | None = "minimal"


@dataclass
class StableItemProgress:
    topic: str
    stable_key: str
    seen_stages: set[str] = field(default_factory=set)
    last_stage: str = ""
    last_stage_change_at: float = 0.0
    no_progress_admissions: int = 0
    last_started_at: float = 0.0
    last_finished_at: float = 0.0
    last_outcome: str = ""


@dataclass
class SupervisorWorkItem:
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
class ClaimState:
    topic_by_puzzle_id: dict[str, str] = field(default_factory=dict)
    simplify_words: set[str] = field(default_factory=set)
    puzzle_words: dict[str, set[str]] = field(default_factory=dict)

    def has_puzzle(self, puzzle_id: str | None) -> bool:
        return bool(puzzle_id) and puzzle_id in self.topic_by_puzzle_id

    def puzzle_word_conflict(self, words: set[str]) -> bool:
        return bool(words & self.simplify_words)

    def simplify_word_conflict(self, words: set[str]) -> bool:
        for active_words in self.puzzle_words.values():
            if words & active_words:
                return True
        return bool(words & self.simplify_words)

    def claim(self, item: SupervisorWorkItem | "JobState") -> None:
        if item.puzzle_id:
            self.topic_by_puzzle_id[item.puzzle_id] = item.topic
            self.puzzle_words[item.puzzle_id] = set(item.words)
        if item.topic == "simplify":
            self.simplify_words.update(item.words)

    def release(self, item: SupervisorWorkItem | "JobState") -> None:
        if item.puzzle_id:
            self.topic_by_puzzle_id.pop(item.puzzle_id, None)
            self.puzzle_words.pop(item.puzzle_id, None)
        if item.topic == "simplify":
            for word in set(item.words):
                self.simplify_words.discard(word)


@dataclass
class StepState:
    step_id: str
    job_id: str
    topic: str
    kind: str
    purpose: str
    model_id: str | None
    runner: Callable[[RunAllContext], object] = field(repr=False)
    execution_mode: str = "inline_non_llm"


@dataclass
class WorkerTask:
    step: StepState
    future: concurrent.futures.Future[object]
    started_at: float


@dataclass
class TopicSlot:
    topic: str
    active_job: "JobState | None" = None
    completed_count: int = 0
    failed_count: int = 0
    backoff_until: float = 0.0
