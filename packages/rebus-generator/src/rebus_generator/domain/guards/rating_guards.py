from __future__ import annotations

import re

from rebus_generator.domain.clue_family import clue_uses_same_family
from rebus_generator.domain.diacritics import normalize
from .definition_guards import definition_describes_english_meaning

RARITY_MARKERS = {
    "rar", "rară", "rare", "raritate", "neuzual", "neobișnuit", "neobisnuit",
    "puțin", "putin", "comun", "uzual", "obisnuit",
}
AMBIGUITY_MARKERS = {
    "alt", "altul", "ambig", "ambigua", "ambiguu", "sinonim",
    "vag", "vagă", "vaga", "firesc", "duce", "răspuns", "raspuns", "familie", "lexical",
}


def clamp_score(value: int | str | None, default: int = 5) -> int:
    try:
        score = int(value if value is not None else default)
    except (TypeError, ValueError):
        score = default
    return max(1, min(10, score))


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in re.findall(r"[A-Za-zĂÂÎȘȘȚăâîșț]+", normalize(text))}


def _feedback_is_rarity_only(feedback: str) -> bool:
    if not feedback:
        return False
    tokens = _tokens(feedback)
    return bool(tokens & RARITY_MARKERS) and not bool(tokens & AMBIGUITY_MARKERS)


def guard_english_meaning_rating(word: str, definition: str, rating):
    if not definition_describes_english_meaning(word, definition):
        return rating
    from rebus_generator.platform.llm.ai_clues import DefinitionRating

    return DefinitionRating(
        semantic_score=1,
        guessability_score=1,
        feedback="Definiția descrie sensul englezesc, nu cel românesc.",
        creativity_score=1,
    )


def guard_same_family_rating(word: str, definition: str, rating):
    if not clue_uses_same_family(word, definition):
        return rating
    from rebus_generator.platform.llm.ai_clues import DefinitionRating

    return DefinitionRating(
        semantic_score=1,
        guessability_score=1,
        feedback="Definiția folosește aceeași familie lexicală ca răspunsul.",
        creativity_score=1,
    )


def guard_definition_centric_rating(rating):
    if rating.semantic_score < 8:
        return rating
    if not _feedback_is_rarity_only(rating.feedback):
        return rating
    from rebus_generator.platform.llm.ai_clues import DefinitionRating

    return DefinitionRating(
        semantic_score=rating.semantic_score,
        guessability_score=rating.guessability_score,
        feedback=rating.feedback,
        creativity_score=rating.creativity_score,
        rarity_only_override=True,
    )

