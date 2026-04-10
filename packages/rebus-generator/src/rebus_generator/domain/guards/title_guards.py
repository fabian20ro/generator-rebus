from __future__ import annotations

import re
from dataclasses import dataclass

from rebus_generator.domain.diacritics import normalize
from rebus_generator.domain.text_rules import contains_normalized_forbidden_word
from rebus_generator.platform.llm.llm_text import clean_llm_text_response

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


def normalize_title_key(title: str) -> str:
    cleaned = " ".join(title.strip().strip('"').strip("'").split())
    cleaned = cleaned.rstrip(".,;:!?…")
    return normalize(cleaned)


@dataclass(frozen=True)
class TitleCandidateReview:
    title: str
    valid: bool
    feedback: str = ""


def contains_mixed_script(title: str) -> bool:
    has_latin = any(("A" <= ch.upper() <= "Z") or ch in "ĂÂÎȘŞȚŢăâîșşțţ" for ch in title)
    has_cyrillic = any("\u0400" <= ch <= "\u04ff" for ch in title)
    return has_latin and has_cyrillic


def contains_non_romanian_tokens(title: str) -> bool:
    tokens = re.findall(r"[A-Za-zĂÂÎȘŞȚŢăâîșşțţ]+", title.lower())
    return any(token in TITLE_NON_ROMANIAN_MARKERS for token in tokens)


def _clean_title(title: str) -> str:
    cleaned = " ".join(clean_llm_text_response(title).split())
    return cleaned.rstrip(".,;:!?…")


def _is_all_caps_title(title: str) -> bool:
    letters = [ch for ch in title if ch.isalpha()]
    return bool(letters) and all(ch.upper() == ch for ch in letters)


def review_title_candidate(title: str, input_words: list[str] | None = None) -> TitleCandidateReview:
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
    if contains_mixed_script(cleaned) or contains_non_romanian_tokens(cleaned):
        return TitleCandidateReview(cleaned, False, "limba mixta")
    if len(cleaned) > 100:
        return TitleCandidateReview(cleaned, False, "peste 100 de caractere")
    if input_words and contains_normalized_forbidden_word(cleaned, input_words, min_length=3):
        return TitleCandidateReview(cleaned, False, "contine cuvant-solutie")
    return TitleCandidateReview(cleaned, True)

