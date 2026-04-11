"""Shared quality report types and lexical hints."""

from __future__ import annotations

from dataclasses import asdict, dataclass


# Shared with clue prompts to steer obvious Romanian/English homographs.
ENGLISH_HOMOGRAPH_HINTS: dict[str, str] = {
    "AI": "formă a verbului «a avea»",
    "AN": "unitate de timp egală cu 12 luni",
    "AT": "domeniul online al Austriei",
    "CLOU": "moment culminant",
    "FAR": "lumină de semnalizare pe coastă",
    "IN": "plantă textilă cu flori albastre",
    "OF": "interjecție care exprimă durere sau regret",
    "OS": "țesut dur al scheletului",
}


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
    middle_window_average_length: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)
