"""Heuristics for word-pool filtering and puzzle quality scoring."""

from __future__ import annotations

from dataclasses import dataclass, asdict


UNCOMMON_LETTERS = set("QWXYK")
FOREIGN_SHORTLIST_BLOCKLIST = {
    "AIR",
    "BIG",
    "CAT",
    "DIG",
    "DOG",
    "GET",
    "LAW",
    "TEN",
}

ENGLISH_HOMOGRAPH_HINTS: dict[str, str] = {
    "AN": "unitate de timp egală cu 12 luni",
    "OF": "interjecție de durere, suspin, regret",
    "AT": "monedă subdivizionară din Laos sau sufix chimic",
    "IN": "plantă textilă cu flori albastre",
    "HAT": "hotărâre fermă, decizie",
    "DARE": "actul de a da, oferire",
    "FI": "infinitivul verbului a fi, a exista",
    "VIS": "experiență mentală din somn",
    "DAR": "cadou, dar și conjuncție adversativă",
    "IDE": "pește de apă dulce din familia ciprinidelor",
    "AS": "carte de joc cu cea mai mare valoare",
    "PAL": "lovitură scurtă cu palma",
    "POT": "recipient de gătit; a putea",
    "CAN": "recipient metalic",
    "FAR": "lumină puternică de semnalizare pe litoral",
}


def _is_toxic_short_loanword(word: dict) -> bool:
    """Reject short foreign-looking entries that consistently poison Romanian clues.

    The source list contains some short ASCII loanwords or English forms that may be
    valid in a broad dictionary sense, but they destabilize a Romanian crossword run:
    the model defines and verifies them in English, and players read them as English.
    Keep this list intentionally small and evidence-driven.
    """
    normalized = word["normalized"]
    original = word.get("original", "")

    if normalized not in FOREIGN_SHORTLIST_BLOCKLIST:
        return False

    return original.isascii() and original.islower()


@dataclass
class QualityReport:
    score: float
    word_count: int
    average_length: float
    average_rarity: float
    two_letter_words: int
    three_letter_words: int
    high_rarity_words: int
    uncommon_letter_words: int
    friendly_words: int
    max_rarity: int = 1
    average_definability: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class WordQualityProfile:
    normalized: str
    length: int
    rarity_level: int | None
    short_fragility: int
    ambiguity_risk: int
    family_leak_risk: int
    foreign_risk: int
    abbreviation_like: bool
    definability_score: float


def assess_word_quality(word: dict) -> WordQualityProfile:
    normalized = word["normalized"]
    length = len(normalized)
    rarity = word.get("rarity_level")
    original = word.get("original", "")
    short_fragility = 4 if length <= 2 else 3 if length == 3 else 1 if length == 4 else 0
    ambiguity_risk = 3 if length <= 3 else 2 if length == 4 else 1 if length <= 6 else 0
    family_leak_risk = 2 if length >= 6 and normalized.endswith(("ARE", "IRE", "ATE", "ISM")) else 0
    foreign_risk = 3 if _is_toxic_short_loanword(word) else 1 if original.isascii() and original.islower() else 0
    abbreviation_like = length <= 3 and original.isascii() and original.islower()
    rarity_value = rarity or 0
    rarity_penalty = (
        rarity_value * 0.5 if rarity_value <= 3 else rarity_value * 0.8
    ) if rarity is not None else 0.0
    definability_score = (
        10.0
        - short_fragility
        - ambiguity_risk
        - family_leak_risk
        - foreign_risk
        - rarity_penalty
    )
    return WordQualityProfile(
        normalized=normalized,
        length=length,
        rarity_level=rarity,
        short_fragility=short_fragility,
        ambiguity_risk=ambiguity_risk,
        family_leak_risk=family_leak_risk,
        foreign_risk=foreign_risk,
        abbreviation_like=abbreviation_like,
        definability_score=max(0.0, definability_score),
    )


def filter_word_records(
    raw_words: list[dict],
    *,
    max_rarity: int,
    max_length: int,
) -> list[dict]:
    """Apply conservative lexical filters before fill."""
    filtered = []
    for word in raw_words:
        normalized = word["normalized"]
        rarity = word.get("rarity_level")
        length = len(normalized)
        profile = assess_word_quality(word)

        if length < 2 or length > max_length:
            continue
        if rarity is not None and rarity > max_rarity:
            continue
        if length <= 2 and rarity is not None and rarity > 2:
            continue
        if length == 3 and rarity is not None and rarity > min(max_rarity, 3):
            continue
        if any(ch in UNCOMMON_LETTERS for ch in normalized) and rarity is not None and rarity >= 4:
            continue
        if _is_toxic_short_loanword(word):
            continue
        if profile.definability_score < 1.5:
            continue
        filtered.append(word)
    return filtered


def score_words(words: list[str], metadata: dict[str, dict], size: int) -> QualityReport:
    """Score a filled puzzle. Higher is better."""
    lengths = [len(word) for word in words]
    rarities = [
        metadata.get(word, {}).get("rarity_level")
        for word in words
        if metadata.get(word, {}).get("rarity_level") is not None
    ]
    avg_length = sum(lengths) / len(lengths) if lengths else 0.0
    avg_rarity = sum(rarities) / len(rarities) if rarities else 2.5
    two_letter = sum(1 for word in words if len(word) == 2)
    three_letter = sum(1 for word in words if len(word) == 3)
    high_rarity = sum(
        1
        for word in words
        if (metadata.get(word, {}).get("rarity_level") or 0) >= 4
    )
    uncommon = sum(1 for word in words if any(ch in UNCOMMON_LETTERS for ch in word))
    friendly = sum(
        1
        for word in words
        if 4 <= len(word) <= 8 and (metadata.get(word, {}).get("rarity_level") or 3) <= 3
    )
    definability_scores = [
        assess_word_quality(metadata.get(word, {"normalized": word, "original": word.lower()})).definability_score
        for word in words
    ]
    avg_definability = sum(definability_scores) / len(definability_scores) if definability_scores else 0.0

    score = 1000.0
    score += avg_length * 14.0
    score += friendly * 4.0
    score += avg_definability * 9.0
    score -= avg_rarity * 40.0
    if size == 7:
        two_letter_penalty = 34.0
        three_letter_penalty = 12.0
    elif size == 10:
        two_letter_penalty = 22.0
        three_letter_penalty = 8.0
    elif size == 12:
        two_letter_penalty = 18.0
        three_letter_penalty = 6.0
    else:
        two_letter_penalty = 14.0
        three_letter_penalty = 5.0
    score -= two_letter * two_letter_penalty
    score -= three_letter * three_letter_penalty
    score -= high_rarity * 28.0
    score -= uncommon * 10.0
    if size == 7:
        score -= max(0, two_letter - 2) * 18.0
    elif size == 10:
        score -= max(0, two_letter - 5) * 12.0
    elif size == 12:
        score -= max(0, two_letter - 8) * 10.0
    else:
        score -= max(0, two_letter - 9) * 8.0

    max_rarity_value = max(rarities) if rarities else 1

    return QualityReport(
        score=score,
        word_count=len(words),
        average_length=avg_length,
        average_rarity=avg_rarity,
        two_letter_words=two_letter,
        three_letter_words=three_letter,
        high_rarity_words=high_rarity,
        uncommon_letter_words=uncommon,
        friendly_words=friendly,
        max_rarity=max_rarity_value,
        average_definability=avg_definability,
    )
