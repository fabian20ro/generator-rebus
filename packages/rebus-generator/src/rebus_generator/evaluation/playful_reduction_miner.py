"""Mine review-only playful two-letter reductions from the current word list."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from rebus_generator.domain.diacritics import normalize
from rebus_generator.domain.guards.definition_guards import validate_definition_text


VOWELS = set("AEIOUĂÂÎ")


@dataclass(frozen=True)
class PlayfulShortCandidate:
    answer: str
    source_word: str
    source_original: str
    proposed_definition: str
    segmentation: str
    confidence: float
    rejection_reasons: list[str]


def _display_original(row: dict) -> str:
    return str(row.get("original") or row.get("normalized") or "").strip()


def _candidate_answers(normalized: str) -> list[tuple[str, str, float]]:
    if len(normalized) < 4:
        return []
    first = normalized[0]
    candidates: list[tuple[str, str, float]] = []
    for index, char in enumerate(normalized[1:], start=1):
        if char in VOWELS or not char.isalpha():
            continue
        answer = first + char
        confidence = round(0.45 + min(0.4, index / max(1, len(normalized)) * 0.5), 3)
        segmentation = f"{first} ... {char}"
        candidates.append((answer, segmentation, confidence))
    candidates.sort(key=lambda item: (-item[2], item[0]))
    return candidates


def mine_playful_short_candidates(
    words: list[dict],
    *,
    max_candidates_per_word: int = 2,
) -> list[PlayfulShortCandidate]:
    mined: list[PlayfulShortCandidate] = []
    seen: set[tuple[str, str]] = set()
    for row in words:
        source_word = normalize(str(row.get("normalized") or ""))
        if len(source_word) < 4:
            continue
        original = _display_original(row)
        if not original:
            continue
        proposed_definition = original[:1].upper() + original[1:] + "!"
        for answer, segmentation, confidence in _candidate_answers(source_word)[:max_candidates_per_word]:
            key = (answer, source_word)
            if key in seen:
                continue
            seen.add(key)
            rejection_reasons: list[str] = []
            rejection = validate_definition_text(answer, proposed_definition)
            if rejection is not None:
                rejection_reasons.append(rejection)
            mined.append(
                PlayfulShortCandidate(
                    answer=answer,
                    source_word=source_word,
                    source_original=original,
                    proposed_definition=proposed_definition,
                    segmentation=segmentation,
                    confidence=confidence,
                    rejection_reasons=rejection_reasons,
                )
            )
    mined.sort(key=lambda item: (item.rejection_reasons != [], -item.confidence, item.answer, item.source_word))
    return mined


def load_words(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_candidates(candidates: list[PlayfulShortCandidate], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(candidate) for candidate in candidates], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
