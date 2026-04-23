"""Prompt benchmark manifest for short-word definition changes."""

from __future__ import annotations

import json
from pathlib import Path

from rebus_generator.domain.short_word_clues import valid_short_word_clues_for

SHORT_WORD_PROMPT_BENCHMARK_WORDS: tuple[str, ...] = (
    "IT",
    "IJE",
    "SEM",
    "OS",
    "OUA",
    "AN",
    "OF",
    "IN",
    "AT",
    "AZ",
    "IE",
    "TI",
    "IZ",
)
SHORT_WORD_PROMPT_BENCHMARK_RUNS = 5
SHORT_WORD_PROMPT_BENCHMARK_METRICS: tuple[str, ...] = (
    "valid_rate",
    "guard_rejection_rate",
    "verify_pass_rate",
    "semantic_score",
    "rebus_score",
)
_DISPLAY_OVERRIDES = {"IT": "iț", "OUA": "ouă"}


def build_short_word_prompt_benchmark_dataset(
    *,
    runs: int = SHORT_WORD_PROMPT_BENCHMARK_RUNS,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for run_index in range(max(1, int(runs))):
        for word in SHORT_WORD_PROMPT_BENCHMARK_WORDS:
            overlay_defs = [clue.definition for clue in valid_short_word_clues_for(word)]
            rows.append(
                {
                    "word": word,
                    "tier": f"short_prompt_r{run_index + 1}",
                    "display_word": _DISPLAY_OVERRIDES.get(word, word.lower()),
                    "length": len(word),
                    "word_type": "",
                    "dex_definitions": "\n".join(f"- {definition}" for definition in overlay_defs),
                }
            )
    return rows


def write_short_word_prompt_benchmark_dataset(path: Path, *, runs: int = SHORT_WORD_PROMPT_BENCHMARK_RUNS) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_short_word_prompt_benchmark_dataset(runs=runs), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
