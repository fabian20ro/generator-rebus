from __future__ import annotations

import random
import re
from dataclasses import dataclass

from rebus_generator.domain.diacritics import normalize
from rebus_generator.domain.text_rules import contains_normalized_forbidden_word
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

TITLE_ENGLISH_MARKERS = {
    "blue", "dream", "dreams", "echo", "echoes", "fire", "fires", "gold",
    "jazz", "light", "lights", "mirror", "mirrors", "moon", "night", "nights",
    "river", "rivers", "shadow", "shadows", "silent", "sky", "skies", "sunset",
    "whisper", "whispers",
}

TITLE_NON_ROMANIAN_MARKERS = {
    "and", "but", "with", "without", "the", "in", "of", "from", "into",
    "world", "life", "silent", "beyond", "other",
}


def _fallback_title() -> str:
    return random.choice(FALLBACK_TITLES)


def normalize_title_key(title: str) -> str:
    cleaned = " ".join(title.strip().strip('"').strip("'").split())
    cleaned = cleaned.rstrip(".,;:!?…")
    return normalize(cleaned)


@dataclass(frozen=True)
class TitleCandidateReview:
    title: str
    valid: bool
    feedback: str = ""


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


def _contains_mixed_script(title: str) -> bool:
    has_latin = any(("A" <= ch.upper() <= "Z") or ch in "ĂÂÎȘŞȚŢăâîșşțţ" for ch in title)
    has_cyrillic = any("\u0400" <= ch <= "\u04ff" for ch in title)
    return has_latin and has_cyrillic


def _contains_non_romanian_tokens(title: str) -> bool:
    tokens = re.findall(r"[A-Za-zĂÂÎȘŞȚŢăâîșşțţ]+", title.lower())
    return any(token in TITLE_NON_ROMANIAN_MARKERS for token in tokens)


def _clean_title(title: str) -> str:
    cleaned = " ".join(clean_llm_text_response(title).split())
    return cleaned.rstrip(".,;:!?…")


def _is_all_caps_title(title: str) -> bool:
    letters = [ch for ch in title if ch.isalpha()]
    return bool(letters) and all(ch.upper() == ch for ch in letters)


def _review_title_candidate(title: str, input_words: list[str] | None = None) -> TitleCandidateReview:
    cleaned = _clean_title(title)
    if not cleaned:
        return TitleCandidateReview(cleaned, False, "titlu gol")
    if cleaned.count(",") >= 2:
        return TitleCandidateReview(cleaned, False, "lista de cuvinte")

    blocked = {"rebus", "romanesc", "românesc", "puzzle", "titlu"}
    if set(cleaned.lower().split()) & blocked:
        return TitleCandidateReview(cleaned, False, "termeni generici interzisi")
    if len(cleaned.split()) >= 6:
        return TitleCandidateReview(cleaned, False, "prea multe cuvinte")
    if _is_all_caps_title(cleaned):
        return TitleCandidateReview(cleaned, False, "all caps")
    english_hits = sum(1 for token in cleaned.lower().split() if token in TITLE_ENGLISH_MARKERS)
    if english_hits >= 2:
        return TitleCandidateReview(cleaned, False, "prea multe marcaje englezesti")
    if _contains_mixed_script(cleaned) or _contains_non_romanian_tokens(cleaned):
        return TitleCandidateReview(cleaned, False, "limba mixta")
    if len(cleaned) > 100:
        return TitleCandidateReview(cleaned, False, "peste 100 de caractere")
    if input_words and contains_normalized_forbidden_word(cleaned, input_words, min_length=3):
        return TitleCandidateReview(cleaned, False, "contine cuvant-solutie")
    return TitleCandidateReview(cleaned, True)


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
