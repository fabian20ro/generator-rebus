"""Validation guards for AI clue ratings and quality control."""

import re
from .diacritics import normalize
from .clue_family import clue_uses_same_family
from .prompt_builders import _definition_describes_english_meaning

ENGLISH_MARKERS = {
    "accurate", "accurately", "actually", "answer", "attached", "big", "by", "common", "correct",
    "definition", "english", "fantasy", "feedback", "file", "fluid", "for", "get", "guess",
    "guessability", "law", "length", "numerical", "precise", "precisely", "pressure", "powered",
    "response", "semantic", "system", "technical", "the", "very", "with", "without", "word",
}
RARITY_MARKERS = {
    "rar", "rară", "rare", "raritate", "neuzual", "neobișnuit", "neobisnuit",
    "puțin", "putin", "comun", "uzual", "obisnuit",
}
AMBIGUITY_MARKERS = {
    "alt", "altul", "ambig", "ambigua", "ambiguu", "sinonim",
    "vag", "vagă", "vaga", "firesc", "duce", "răspuns", "raspuns", "familie", "lexical",
}

DANGLING_ENDING_MARKERS = {
    "a", "ai", "al", "ale", "asupra", "ca", "că", "cu", "de", "din", "după", "dupa",
    "fără", "fara", "in", "în", "la", "o", "ori", "pe", "pentru", "prin", "sau", "si",
    "spre", "un", "unei", "unor", "unui", "și",
}

def _latin_word_tokens(text: str | None) -> list[str]:
    if not text:
        return []
    normalized = normalize(text).lower()
    return re.findall(r"[a-z]+", normalized)


def find_english_marker(text: str | None) -> str | None:
    for token in _latin_word_tokens(text):
        if token in ENGLISH_MARKERS:
            return token
    return None


def contains_english_markers(text: str | None) -> bool:
    return find_english_marker(text) is not None


def _definition_mentions_answer(answer: str, definition: str) -> bool:
    if not definition:
        return False
    normalized_definition = normalize(definition).lower()
    pattern = rf"\b{re.escape(answer.lower())}\b"
    return re.search(pattern, normalized_definition) is not None


def _definition_is_invalid(answer: str, definition: str) -> bool:
    return _definition_mentions_answer(answer, definition) or clue_uses_same_family(
        answer, definition
    )


def _same_family_feedback() -> str:
    return "Definiția folosește aceeași familie lexicală ca răspunsul."


def _tokens(text: str) -> set[str]:
    return {
        token.lower() for token in re.findall(r"[A-Za-zĂÂÎȘȘȚăâîșț]+", normalize(text))
    }


def _last_word(text: str) -> str:
    tokens = re.findall(r"[A-Za-zĂÂÎȘȘȚăâîșț0-9]+", normalize(text))
    return tokens[-1].lower() if tokens else ""


def _feedback_is_rarity_only(feedback: str) -> bool:
    if not feedback:
        return False
    tokens = _tokens(feedback)
    return bool(tokens & RARITY_MARKERS) and not bool(tokens & AMBIGUITY_MARKERS)


def _validate_definition(word: str, definition: str) -> str | None:
    """Return rejection reason, or None if acceptable."""
    from .prompt_builders import _strip_trailing_usage_suffixes
    clean_definition = _strip_trailing_usage_suffixes(definition)
    if len(clean_definition) < 5:
        return f"too short ({len(clean_definition)} chars)"
    if len(re.findall(r"[A-Za-zĂÂÎȘȘȚăâîșț0-9]+", clean_definition)) < 2:
        return "single-word gloss"
    if _last_word(clean_definition) in DANGLING_ENDING_MARKERS:
        return "dangling ending"
    if _definition_is_invalid(word, clean_definition):
        return "contains answer or family word"
    english_marker = find_english_marker(clean_definition)
    if english_marker:
        return f"English markers detected (token={english_marker})"
    if _definition_describes_english_meaning(word, clean_definition):
        return "English meaning"
    return None


def _guard_english_meaning_rating(
    word: str,
    definition: str,
    rating,
):
    if not _definition_describes_english_meaning(word, definition):
        return rating
    from .ai_clues import DefinitionRating
    return DefinitionRating(
        semantic_score=1,
        guessability_score=1,
        feedback="Definiția descrie sensul englezesc, nu cel românesc.",
        creativity_score=1,
    )


def _guard_same_family_rating(
    word: str, definition: str, rating
):
    if not clue_uses_same_family(word, definition):
        return rating
    from .ai_clues import DefinitionRating
    return DefinitionRating(
        semantic_score=1,
        guessability_score=1,
        feedback=_same_family_feedback(),
        creativity_score=1,
    )


def _guard_definition_centric_rating(rating):
    if rating.semantic_score < 8:
        return rating
    if not _feedback_is_rarity_only(rating.feedback):
        return rating
    from .ai_clues import DefinitionRating
    return DefinitionRating(
        semantic_score=rating.semantic_score,
        guessability_score=rating.guessability_score,
        feedback=rating.feedback,
        creativity_score=rating.creativity_score,
        rarity_only_override=True,
    )


def _clamp_score(value: int | str | None, default: int = 5) -> int:
    try:
        score = int(value if value is not None else default)
    except (TypeError, ValueError):
        score = default
    return max(1, min(10, score))

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

def _extract_verify_candidates(
    raw: str, answer_length: int, max_guesses: int
) -> list[str]:
    from ..config import VERIFY_CANDIDATE_COUNT
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
