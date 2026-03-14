"""Structured metrics for batch puzzle generation runs."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class WordMetric:
    word: str
    length: int
    rarity: int | None = None
    definition_rounds: int = 0
    final_verified: bool = False
    semantic_score: int | None = None
    guessability_score: int | None = None
    was_blocker: bool = False
    english_meaning_detected: bool = False
    model_generated: str = ""
    model_verified: str = ""
    elapsed_ms: int = 0


@dataclass
class PuzzleMetric:
    size: int = 0
    fill_attempts: int = 0
    fill_elapsed_ms: int = 0
    word_count: int = 0
    avg_word_length: float = 0.0
    avg_rarity: float = 0.0
    definition_first_pass_rate: float = 0.0
    definition_final_pass_rate: float = 0.0
    avg_semantic: float = 0.0
    avg_guessability: float = 0.0
    blocker_count: int = 0
    blocker_words: list[str] = field(default_factory=list)
    model_switches: int = 0
    total_elapsed_ms: int = 0


@dataclass
class BatchMetric:
    timestamp: str = ""
    seed: int = 0
    models_used: list[str] = field(default_factory=list)
    puzzles: list[PuzzleMetric] = field(default_factory=list)
    word_metrics: list[WordMetric] = field(default_factory=list)
    total_elapsed_ms: int = 0


def write_metrics(batch: BatchMetric, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(batch), f, ensure_ascii=False, indent=2)


def load_word_difficulty(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def update_word_difficulty(
    word_metrics: list[WordMetric],
    difficulty_path: Path,
) -> None:
    existing = load_word_difficulty(difficulty_path)
    for wm in word_metrics:
        entry = existing.get(wm.word, {"attempts": 0, "successes": 0, "total_semantic": 0.0})
        entry["attempts"] = entry.get("attempts", 0) + 1
        if wm.final_verified:
            entry["successes"] = entry.get("successes", 0) + 1
        if wm.semantic_score is not None:
            entry["total_semantic"] = entry.get("total_semantic", 0.0) + wm.semantic_score
        attempts = entry["attempts"]
        total_sem = entry.get("total_semantic", 0.0)
        entry["avg_semantic"] = round(total_sem / attempts, 2) if attempts > 0 else 0.0
        existing[wm.word] = entry

    difficulty_path.parent.mkdir(parents=True, exist_ok=True)
    with open(difficulty_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
