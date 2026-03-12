"""Heuristics for word-pool filtering and puzzle quality scoring."""

from __future__ import annotations

from dataclasses import dataclass, asdict


UNCOMMON_LETTERS = set("QWXYK")


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

    def to_dict(self) -> dict:
        return asdict(self)


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

    score = 1000.0
    score += avg_length * 14.0
    score += friendly * 4.0
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
    score -= high_rarity * 18.0
    score -= uncommon * 10.0
    if size == 7:
        score -= max(0, two_letter - 2) * 18.0
    elif size == 10:
        score -= max(0, two_letter - 5) * 12.0
    elif size == 12:
        score -= max(0, two_letter - 8) * 10.0
    else:
        score -= max(0, two_letter - 9) * 8.0

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
    )
