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
    word_type: str = ""
    definition_rounds: int = 0
    final_verified: bool = False
    semantic_score: int | None = None
    guessability_score: int | None = None
    creativity_score: int | None = None
    rebus_score: int | None = None
    was_blocker: bool = False
    english_meaning_detected: bool = False
    wrong_guess: str = ""
    failure_kind: str = ""
    failure_message: str = ""
    rarity_only_override: bool = False
    form_mismatch: bool = False
    form_mismatch_detail: str = ""
    model_generated: str = ""
    model_verified: str = ""
    model_rated: str = ""
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
    avg_creativity: float = 0.0
    avg_rebus: float = 0.0
    min_rebus: int = 0
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
        entry = existing.get(wm.word, {
            "attempts": 0,
            "successes": 0,
            "blockers": 0,
            "form_mismatch_count": 0,
            "rarity_override_count": 0,
            "total_semantic": 0.0,
            "total_guessability": 0.0,
            "total_rebus": 0.0,
            "min_semantic": None,
            "max_semantic": None,
            "min_rebus": None,
            "max_rebus": None,
            "failure_kind_counts": {},
            "wrong_guess_counts": {},
            "generated_model_counts": {},
            "rated_model_counts": {},
        })
        entry["attempts"] = entry.get("attempts", 0) + 1
        if wm.final_verified:
            entry["successes"] = entry.get("successes", 0) + 1
        if wm.was_blocker:
            entry["blockers"] = entry.get("blockers", 0) + 1
        if wm.form_mismatch:
            entry["form_mismatch_count"] = entry.get("form_mismatch_count", 0) + 1
        if wm.rarity_only_override:
            entry["rarity_override_count"] = entry.get("rarity_override_count", 0) + 1
        if wm.semantic_score is not None:
            entry["total_semantic"] = entry.get("total_semantic", 0.0) + wm.semantic_score
            current_min = entry.get("min_semantic")
            current_max = entry.get("max_semantic")
            entry["min_semantic"] = wm.semantic_score if current_min is None else min(current_min, wm.semantic_score)
            entry["max_semantic"] = wm.semantic_score if current_max is None else max(current_max, wm.semantic_score)
        if wm.guessability_score is not None:
            entry["total_guessability"] = entry.get("total_guessability", 0.0) + wm.guessability_score
        if wm.rebus_score is not None:
            entry["total_rebus"] = entry.get("total_rebus", 0.0) + wm.rebus_score
            current_min = entry.get("min_rebus")
            current_max = entry.get("max_rebus")
            entry["min_rebus"] = wm.rebus_score if current_min is None else min(current_min, wm.rebus_score)
            entry["max_rebus"] = wm.rebus_score if current_max is None else max(current_max, wm.rebus_score)
        if wm.failure_kind:
            failure_counts = entry.setdefault("failure_kind_counts", {})
            failure_counts[wm.failure_kind] = failure_counts.get(wm.failure_kind, 0) + 1
        if wm.wrong_guess:
            wrong_guess_counts = entry.setdefault("wrong_guess_counts", {})
            wrong_guess_counts[wm.wrong_guess] = wrong_guess_counts.get(wm.wrong_guess, 0) + 1
        if wm.model_generated:
            generated_model_counts = entry.setdefault("generated_model_counts", {})
            generated_model_counts[wm.model_generated] = generated_model_counts.get(wm.model_generated, 0) + 1
        if wm.model_rated:
            rated_model_counts = entry.setdefault("rated_model_counts", {})
            rated_model_counts[wm.model_rated] = rated_model_counts.get(wm.model_rated, 0) + 1
        attempts = entry["attempts"]
        total_sem = entry.get("total_semantic", 0.0)
        total_guessability = entry.get("total_guessability", 0.0)
        total_rebus = entry.get("total_rebus", 0.0)
        entry["avg_semantic"] = round(total_sem / attempts, 2) if attempts > 0 else 0.0
        entry["avg_guessability"] = round(total_guessability / attempts, 2) if attempts > 0 else 0.0
        entry["avg_rebus"] = round(total_rebus / attempts, 2) if attempts > 0 else 0.0
        min_semantic = entry.get("min_semantic")
        max_semantic = entry.get("max_semantic")
        min_rebus = entry.get("min_rebus")
        max_rebus = entry.get("max_rebus")
        entry["semantic_spread"] = (max_semantic - min_semantic) if None not in (min_semantic, max_semantic) else None
        entry["rebus_spread"] = (max_rebus - min_rebus) if None not in (min_rebus, max_rebus) else None
        existing[wm.word] = entry

    difficulty_path.parent.mkdir(parents=True, exist_ok=True)
    with open(difficulty_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
