"""Curated additive clues for short words that are hard to define safely."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from rebus_generator.domain.diacritics import normalize
from rebus_generator.domain.guards.definition_guards import validate_definition_text


@dataclass(frozen=True)
class ShortWordClue:
    normalized: str
    definition: str
    original: str = ""
    source: str = ""
    category: str = ""
    priority: int = 100


_DATA_PATH = Path(__file__).with_name("short_word_clues.json")
_FORBIDDEN_TERM_EXTRAS: dict[str, tuple[str, ...]] = {
    "SEM": ("semantic", "semem", "semnificație", "semnificatie"),
}


@lru_cache(maxsize=1)
def _load_short_word_clues() -> tuple[ShortWordClue, ...]:
    try:
        raw_entries = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw_entries = []
    clues: list[ShortWordClue] = []
    for raw in raw_entries:
        normalized = normalize(str(raw.get("normalized") or ""))
        definition = str(raw.get("definition") or "").strip()
        if not normalized or not definition:
            continue
        clues.append(
            ShortWordClue(
                normalized=normalized,
                original=str(raw.get("original") or "").strip(),
                definition=definition,
                source=str(raw.get("source") or "").strip(),
                category=str(raw.get("category") or "").strip(),
                priority=int(raw.get("priority") or 100),
            )
        )
    return tuple(sorted(clues, key=lambda clue: (clue.normalized, clue.priority)))


def short_word_clues_for(word: str) -> list[ShortWordClue]:
    norm = normalize(word)
    return [clue for clue in _load_short_word_clues() if clue.normalized == norm]


def valid_short_word_clues_for(word: str) -> list[ShortWordClue]:
    norm = normalize(word)
    return [
        clue
        for clue in short_word_clues_for(norm)
        if validate_definition_text(norm, clue.definition) is None
    ]


def short_word_prompt_context(word: str) -> str:
    clues = valid_short_word_clues_for(word)
    if not clues:
        return ""
    lines = [f"- {clue.definition}" for clue in clues]
    return "\n".join(lines)


def forbidden_short_word_terms(word: str) -> list[str]:
    norm = normalize(word)
    if len(norm) < 2 or len(norm) > 3:
        return []
    terms = [norm.lower()]
    terms.extend(_FORBIDDEN_TERM_EXTRAS.get(norm, ()))
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(term)
    return result
