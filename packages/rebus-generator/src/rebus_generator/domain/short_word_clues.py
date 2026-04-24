"""Compatibility wrappers for the shared answer supply registry."""

from __future__ import annotations

from dataclasses import dataclass

from rebus_generator.domain.diacritics import normalize
from rebus_generator.domain.answer_supply import (
    answer_supply_entries_for,
    answer_supply_prompt_context,
    forbidden_short_word_terms,
    valid_answer_supply_entries_for,
)


@dataclass(frozen=True)
class ShortWordClue:
    normalized: str
    definition: str
    original: str = ""
    source: str = ""
    category: str = ""
    priority: int = 100


def short_word_clues_for(word: str) -> list[ShortWordClue]:
    norm = normalize(word)
    return [
        ShortWordClue(
            normalized=entry.answer,
            original=entry.original,
            definition=entry.definition,
            source=entry.source,
            category=entry.tone,
            priority=entry.priority,
        )
        for entry in answer_supply_entries_for(norm, prompt_only=True)
        if len(entry.answer) <= 3
    ]


def valid_short_word_clues_for(word: str) -> list[ShortWordClue]:
    norm = normalize(word)
    return [
        ShortWordClue(
            normalized=entry.answer,
            original=entry.original,
            definition=entry.definition,
            source=entry.source,
            category=entry.tone,
            priority=entry.priority,
        )
        for entry in valid_answer_supply_entries_for(norm)
        if len(entry.answer) <= 3
    ]


def short_word_prompt_context(word: str) -> str:
    return answer_supply_prompt_context(word)
