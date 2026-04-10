from __future__ import annotations

import random
from dataclasses import dataclass

from rebus_generator.domain.guards.title_guards import (
    TITLE_ENGLISH_MARKERS,
    TITLE_NON_ROMANIAN_MARKERS,
    TitleCandidateReview,
    contains_mixed_script as _contains_mixed_script,
    contains_non_romanian_tokens as _contains_non_romanian_tokens,
    review_title_candidate as _review_title_candidate,
    normalize_title_key,
)
from rebus_generator.platform.llm.llm_text import clean_llm_text_response

TITLE_MIN_CREATIVITY = 8
MAX_TITLE_ROUNDS = 7
NO_TITLE_LABEL = "Fara titlu"
MAX_REJECTED_HINTS = 5
MAX_REPEATED_REASON_HINTS = 2
TITLE_GENERATE_MAX_TOKENS = 400
TITLE_RATE_MAX_TOKENS = 300

FALLBACK_TITLES = [
    "Fir de Cuvinte",
    "Sensuri Comune",
    "Noduri de Sens",
    "Semne Încrucișate",
    "Puncte Comune",
    "Umbra Cuvintelor",
    "Joc de Cuvinte",
    "Căi Încrucișate",
    "Labirint de Idei",
    "Prisme și Ecouri",
    "Oglinzi Paralele",
    "Răscruce de Gânduri",
    "Spirale Ascunse",
    "Între Rânduri",
    "Carusel Lexical",
    "Mozaic de Sensuri",
    "Ferestre Deschise",
    "Punți Nevăzute",
    "Ecou de Litere",
    "Orizont Fragmentat",
]

def _fallback_title() -> str:
    return random.choice(FALLBACK_TITLES)


def _clean_title(title: str) -> str:
    cleaned = " ".join(clean_llm_text_response(title).split())
    return cleaned.rstrip(".,;:!?…")


@dataclass(frozen=True)
class TitleGenerationResult:
    title: str
    score: int
    feedback: str
    used_fallback: bool = False
    score_complete: bool = True


@dataclass(frozen=True)
class TitleRatingResult:
    score: int
    feedback: str
    complete: bool
    votes: dict[str, tuple[int, str]]


@dataclass(frozen=True)
class TitleGenerateAttempt:
    title: str
    response_source: str


def _sanitize_title(title: str, input_words: list[str] | None = None) -> str:
    reviewed = _review_title_candidate(title, input_words=input_words)
    return reviewed.title if reviewed.valid else _fallback_title()


def _generator_retry_instruction(reason: str) -> str:
    if reason == "prea multe cuvinte":
        return "Rescrie în maximum 5 cuvinte."
    if reason == "limba mixta":
        return "Rescrie exclusiv în limba română, fără niciun cuvânt străin sau alfabet nelatin."
    if reason == "contine cuvant-solutie":
        return "Rescrie fără să folosești cuvinte din rebus."
    if reason == "termeni generici interzisi":
        return "Rescrie fără cuvintele Rebus, Românesc, Puzzle sau Titlu."
    if reason == "titlu gol":
        return "Răspunde obligatoriu cu un singur titlu concret, nu gol."
    return "Rescrie cu un titlu mai scurt și mai precis."


def _build_rejected_context(rejected: list[tuple[str, str]]) -> str:
    if not rejected:
        return ""
    relevant = rejected[-MAX_REJECTED_HINTS:]
    lines = []
    repeated_reasons: dict[str, int] = {}
    for title, reason in relevant:
        repeated_reasons[reason] = repeated_reasons.get(reason, 0) + 1
        lines.append(f'- "{title}" ({reason})')
    hints = []
    for reason, count in repeated_reasons.items():
        if count >= MAX_REPEATED_REASON_HINTS:
            hints.append(_generator_retry_instruction(reason))
    hint_text = "\n".join(f"- {hint}" for hint in hints[:2])
    suffix = f"\nCorecții obligatorii:\n{hint_text}" if hint_text else ""
    return "\n\nNU repeta aceste forme respinse:\n" + "\n".join(lines) + suffix
