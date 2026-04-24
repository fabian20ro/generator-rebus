"""Managed non-DEX answer supply for grid generation and clue prompts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from rebus_generator.domain.diacritics import normalize
from rebus_generator.domain.guards.definition_guards import validate_definition_text


@dataclass(frozen=True)
class AnswerSupplyEntry:
    answer: str
    definition: str
    original: str = ""
    source: str = ""
    tone: str = ""
    priority: int = 100
    enabled_for_grid: bool = False
    enabled_for_prompt: bool = True
    approved: bool = False
    source_note: str = ""

    @property
    def clue_support_score(self) -> float:
        if self.source == "curated_ro_plate" and self.tone == "factual":
            return 6.0
        if self.source.startswith("curated_"):
            return 4.0
        if self.source == "playful_split":
            return 2.5
        return 1.0

    @property
    def rarity_level(self) -> int:
        if self.source == "playful_split":
            return 4
        if self.source.startswith("curated_"):
            return 2
        return 3


class AnswerSupplyProvider:
    """Read-only facade over approved non-DEX answer supply entries."""

    def entries_for(self, word: str, *, prompt_only: bool = False, grid_only: bool = False) -> list[AnswerSupplyEntry]:
        return answer_supply_entries_for(word, prompt_only=prompt_only, grid_only=grid_only)

    def valid_entries_for(self, word: str) -> list[AnswerSupplyEntry]:
        return valid_answer_supply_entries_for(word)

    def prompt_context(self, word: str) -> str:
        return answer_supply_prompt_context(word)

    def get_definition_context(self, word: str, dex_definitions: str = "") -> str:
        dex_text = str(dex_definitions or "").strip()
        supply_text = self.prompt_context(word)
        if dex_text and supply_text:
            return f"{dex_text}\nDefiniții extra non-DEX:\n{supply_text}"
        return dex_text or supply_text

    def augmented_word_rows(self, raw_words: list[dict]) -> list[dict]:
        return augment_word_rows_for_answer_supply(raw_words)


_DATA_PATH = Path(__file__).with_name("answer_supply.json")
_SOURCE_PRIORITY = {
    "dex": 0,
    "curated_ro_plate": 20,
    "curated_cc_tld": 30,
    "curated:tld": 30,
    "curated:letter": 35,
    "curated:linguistics": 35,
    "playful_split": 80,
}
_TONE_PRIORITY = {"factual": 0, "colloquial": 10, "generic": 20, "playful": 30}
_FORBIDDEN_TERM_EXTRAS: dict[str, tuple[str, ...]] = {
    "SEM": ("semantic", "semem", "semnificație", "semnificatie"),
}


def _coerce_bool(value: object, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


@lru_cache(maxsize=1)
def load_answer_supply_entries() -> tuple[AnswerSupplyEntry, ...]:
    try:
        raw_entries = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except Exception:
        raw_entries = []
    entries: list[AnswerSupplyEntry] = []
    for raw in raw_entries:
        answer = normalize(str(raw.get("answer") or raw.get("normalized") or ""))
        definition = str(raw.get("definition") or "").strip()
        if not answer or not definition:
            continue
        entries.append(
            AnswerSupplyEntry(
                answer=answer,
                original=str(raw.get("original") or answer).strip(),
                definition=definition,
                source=str(raw.get("source") or "").strip(),
                tone=str(raw.get("tone") or "").strip(),
                priority=int(raw.get("priority") or 100),
                enabled_for_grid=_coerce_bool(raw.get("enabled_for_grid"), default=False),
                enabled_for_prompt=_coerce_bool(raw.get("enabled_for_prompt"), default=True),
                approved=_coerce_bool(raw.get("approved"), default=False),
                source_note=str(raw.get("source_note") or "").strip(),
            )
        )
    return tuple(sorted(entries, key=_entry_sort_key))


def _entry_sort_key(entry: AnswerSupplyEntry) -> tuple[int, int, int, str]:
    source_rank = _SOURCE_PRIORITY.get(entry.source, 50)
    tone_rank = _TONE_PRIORITY.get(entry.tone, 20)
    return (source_rank, tone_rank, entry.priority, entry.definition)


def all_answer_supply_entries() -> list[AnswerSupplyEntry]:
    return list(load_answer_supply_entries())


def answer_supply_entries_for(
    word: str,
    *,
    approved_only: bool = True,
    prompt_only: bool = False,
    grid_only: bool = False,
) -> list[AnswerSupplyEntry]:
    norm = normalize(word)
    result: list[AnswerSupplyEntry] = []
    for entry in load_answer_supply_entries():
        if entry.answer != norm:
            continue
        if approved_only and not entry.approved:
            continue
        if prompt_only and not entry.enabled_for_prompt:
            continue
        if grid_only and not entry.enabled_for_grid:
            continue
        result.append(entry)
    return result


def valid_answer_supply_entries_for(word: str) -> list[AnswerSupplyEntry]:
    norm = normalize(word)
    return [
        entry
        for entry in answer_supply_entries_for(norm, prompt_only=True)
        if validate_definition_text(norm, entry.definition) is None
    ]


def answer_supply_prompt_context(word: str) -> str:
    entries = valid_answer_supply_entries_for(word)
    if not entries:
        return ""
    lines = [
        f"- [{entry.source}/{entry.tone}] {entry.definition}"
        for entry in entries
    ]
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


def validate_answer_supply(entries: Iterable[AnswerSupplyEntry] | None = None) -> list[str]:
    errors: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for entry in entries or load_answer_supply_entries():
        key = (entry.answer, entry.source, entry.definition)
        if key in seen:
            errors.append(f"duplicate answer supply entry: {entry.answer} {entry.source} {entry.definition}")
        seen.add(key)
        if entry.approved and (entry.enabled_for_prompt or entry.enabled_for_grid):
            rejection = validate_definition_text(entry.answer, entry.definition)
            if rejection is not None:
                errors.append(f"{entry.answer} {entry.source}: {rejection} :: {entry.definition}")
    return errors


def best_grid_entries_by_answer() -> dict[str, AnswerSupplyEntry]:
    best: dict[str, AnswerSupplyEntry] = {}
    for entry in load_answer_supply_entries():
        if not entry.approved or not entry.enabled_for_grid:
            continue
        if validate_definition_text(entry.answer, entry.definition) is not None:
            continue
        current = best.get(entry.answer)
        if current is None or _entry_sort_key(entry) < _entry_sort_key(current):
            best[entry.answer] = entry
    return best


def augment_word_rows_for_answer_supply(raw_words: list[dict]) -> list[dict]:
    """Return base words enriched with approved extra answer rows/metadata."""
    best_entries = best_grid_entries_by_answer()
    result = [dict(row) for row in raw_words]
    row_by_answer: dict[str, dict] = {
        normalize(str(row.get("normalized") or "")): row
        for row in result
        if row.get("normalized")
    }
    for answer, entry in best_entries.items():
        payload = {
            "normalized": answer,
            "original": entry.original or answer,
            "length": len(answer),
            "rarity_level": entry.rarity_level,
            "word_type": "",
            "clue_support_score": entry.clue_support_score,
            "source": entry.source,
        }
        existing = row_by_answer.get(answer)
        if existing is None:
            result.append(payload)
            row_by_answer[answer] = payload
            continue
        existing["clue_support_score"] = max(
            float(existing.get("clue_support_score") or 0.0),
            entry.clue_support_score,
        )
        existing.setdefault("source", entry.source)
        if not existing.get("original"):
            existing["original"] = entry.original or answer
    return result
