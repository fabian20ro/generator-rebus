from __future__ import annotations

import re

from dataclasses import dataclass

from rebus_generator.domain.clue_family import clue_family_match
from rebus_generator.domain.diacritics import normalize

ENGLISH_MARKERS = {
    "accurate", "accurately", "actually", "answer", "attached", "big", "by", "common", "correct",
    "definition", "english", "fantasy", "feedback", "file", "for", "get", "guess",
    "guessability", "law", "length", "numerical", "precisely", "pressure", "powered",
    "response", "semantic", "system", "technical", "the", "very", "with", "without", "word",
}
_ENGLISH_MEANING_PATTERNS: dict[str, list[str]] = {
    "AN": ["articol nehotărât", "articol nehotarat"],
    "OF": [
        "prepoziție de posesie",
        "prepozitie de posesie",
        "indică posesia",
        "indica posesia",
    ],
    "IN": [
        "prepoziție de loc",
        "prepozitie de loc",
        "indică poziția",
        "indica pozitia",
        "prepoziție care indică",
    ],
    "AT": [
        "prepoziție care indică locul",
        "prepozitie care indica locul",
        "prepoziție de loc",
    ],
    "HAT": ["pălărie", "palarie"],
    "NAT": ["network address", "traducere a adreselor", "adreselor ip"],
    "IDE": ["dezvoltare software", "editor și compilator", "mediu de dezvoltare"],
    "REF": ["referință", "referinta"],
}
_PROMPT_RESIDUE_MARKERS = (
    "definiția:",
    "definitia:",
    "propusă:",
    "propusa:",
    "```",
    "{\"",
)
_TRAILING_USAGE_SUFFIX_RE = re.compile(
    r"(?:\s+\((?:arh|inv|reg|tehn|pop|fam|arg|livr)\.\))+\s*$",
    flags=re.IGNORECASE,
)
DANGLING_ENDING_MARKERS = {
    "a", "ai", "al", "ale", "asupra", "ca", "că", "cu", "de", "din", "după", "dupa",
    "fără", "fara", "in", "în", "la", "o", "ori", "pe", "pentru", "prin", "sau", "si",
    "spre", "un", "unei", "unor", "unui", "și",
}


@dataclass(frozen=True)
class DefinitionRejectionDetails:
    reason: str
    matched_token: str = ""
    matched_stem: str = ""
    leak_kind: str = ""


def strip_trailing_usage_suffixes(definition: str) -> str:
    return _TRAILING_USAGE_SUFFIX_RE.sub("", definition or "").strip()


def definition_describes_english_meaning(word: str, definition: str) -> bool:
    if not definition:
        return False
    lower_def = definition.lower()
    if "engleză" in lower_def or "engleza" in lower_def or "english" in lower_def:
        return True
    patterns = _ENGLISH_MEANING_PATTERNS.get(word.upper(), [])
    return any(pattern in lower_def for pattern in patterns)


def has_prompt_residue(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in _PROMPT_RESIDUE_MARKERS)


def _latin_word_tokens(text: str | None) -> list[str]:
    if not text:
        return []
    normalized = normalize(text).lower()
    return re.findall(r"[a-z]+", normalized)


def contains_english_markers(text: str | None) -> bool:
    return any(token in ENGLISH_MARKERS for token in _latin_word_tokens(text))


def _definition_mentions_answer_detail(answer: str, definition: str) -> DefinitionRejectionDetails | None:
    if not definition:
        return None
    answer_norm = normalize(answer).lower()
    normalized_definition = normalize(definition).lower()
    pattern = rf"\b{re.escape(answer_norm)}\b"
    match = re.search(pattern, normalized_definition)
    if match is None:
        return None
    return DefinitionRejectionDetails(
        reason="contains answer or family word",
        matched_token=match.group(0),
        matched_stem=answer_norm,
        leak_kind="exact_answer",
    )


def _short_answer_family_leak_detail(answer: str, definition: str) -> DefinitionRejectionDetails | None:
    answer_norm = normalize(answer).lower()
    if len(answer_norm) > 3 or len(answer_norm) < 2:
        return None
    for token in re.findall(r"[A-Za-zĂÂÎȘȘȚăâîșț0-9]+", normalize(definition)):
        token_norm = token.lower()
        if token_norm == answer_norm:
            return DefinitionRejectionDetails(
                reason="contains answer or family word",
                matched_token=token_norm,
                matched_stem=answer_norm,
                leak_kind="short_answer_family",
            )
        if len(token_norm) <= 1:
            continue
        if token_norm.startswith(answer_norm) and len(token_norm) > len(answer_norm):
            return DefinitionRejectionDetails(
                reason="contains answer or family word",
                matched_token=token_norm,
                matched_stem=answer_norm,
                leak_kind="short_answer_family",
            )
        if answer_norm.startswith(token_norm) and len(token_norm) >= max(2, len(answer_norm) - 1):
            return DefinitionRejectionDetails(
                reason="contains answer or family word",
                matched_token=token_norm,
                matched_stem=token_norm,
                leak_kind="short_answer_family",
            )
    return None


def _last_word(text: str) -> str:
    tokens = re.findall(r"[A-Za-zĂÂÎȘȘȚăâîșț0-9]+", normalize(text))
    return tokens[-1].lower() if tokens else ""


def validate_definition_text(word: str, definition: str) -> str | None:
    details = validate_definition_text_with_details(word, definition)
    return details.reason if details is not None else None


def validate_definition_text_with_details(word: str, definition: str) -> DefinitionRejectionDetails | None:
    clean_definition = strip_trailing_usage_suffixes(definition)
    if len(clean_definition) < 5:
        return DefinitionRejectionDetails(reason=f"too short ({len(clean_definition)} chars)")
    if len(re.findall(r"[A-Za-zĂÂÎȘȘȚăâîșț0-9]+", clean_definition)) < 2:
        return DefinitionRejectionDetails(reason="single-word gloss")
    if _last_word(clean_definition) in DANGLING_ENDING_MARKERS:
        return DefinitionRejectionDetails(reason="dangling ending")
    
    mentions_answer = _definition_mentions_answer_detail(word, clean_definition)
    if mentions_answer is not None:
        return mentions_answer
    same_family = clue_family_match(word, clean_definition)
    if same_family is not None:
        return DefinitionRejectionDetails(
            reason="contains answer or family word",
            matched_token=same_family.matched_token,
            matched_stem=same_family.matched_stem,
            leak_kind=same_family.leak_kind,
        )
    short_family = _short_answer_family_leak_detail(word, clean_definition)
    if short_family is not None:
        return short_family

    if contains_english_markers(clean_definition):
        return DefinitionRejectionDetails(reason="English markers detected")
    if definition_describes_english_meaning(word, clean_definition):
        return DefinitionRejectionDetails(reason="English meaning")
    return None


def _clean_verify_chunk(text: str | None) -> str:
    chunk = (text or "").strip().strip('"').strip("'")
    chunk = re.sub(r"<\|[^|]*\|>", "", chunk).strip()
    chunk = re.sub(
        r"^\s*(?:[-*•]+|\d+[.)]\s*|(?:Răspunsuri|Raspunsuri|Răspuns|Raspuns|Cuvinte):\s*)",
        "",
        chunk,
        flags=re.IGNORECASE,
    ).strip()
    token_match = re.search(r"[A-Za-zĂÂÎȘȘȚăâîșț0-9]+", chunk)
    return token_match.group(0) if token_match else ""


def extract_verify_candidates(raw: str, answer_length: int, max_guesses: int) -> list[str]:
    pieces = re.split(r"[\n,;/|]+", raw or "")
    candidates: list[str] = []
    seen: set[str] = set()

    def _append(candidate: str) -> None:
        normalized = normalize(candidate)
        if not normalized or len(normalized) != answer_length:
            return
        if contains_english_markers(candidate) or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(candidate.upper())

    for piece in pieces:
        candidate = _clean_verify_chunk(piece)
        if candidate:
            _append(candidate)
        if len(candidates) >= max_guesses:
            return candidates[:max_guesses]

    if candidates:
        return candidates[:max_guesses]

    fallback_tokens = re.findall(r"[A-Za-zĂÂÎȘȘȚăâîșț0-9]+", raw or "")
    for token in fallback_tokens:
        _append(token)
        if len(candidates) >= max_guesses:
            break
    return candidates[:max_guesses]
