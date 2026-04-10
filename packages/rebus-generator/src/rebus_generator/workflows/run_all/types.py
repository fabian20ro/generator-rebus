from __future__ import annotations

import concurrent.futures
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rebus_generator.platform.orchestration import StableItemProgress, WorkItem, WorkStage


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
    gemma_verify_reasoning: str | None = "none"
    gemma_rate_reasoning: str | None = "minimal"
    gemma_title_generate_reasoning: str | None = "none"
    gemma_title_rate_reasoning: str | None = "none"


SupervisorWorkItem = WorkItem


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


StepState = WorkStage


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
