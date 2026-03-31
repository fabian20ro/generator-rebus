"""Shared types for canonical clue deduplication and reuse."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ClueDefinitionRecord:
    id: str
    word_normalized: str
    word_original: str
    definition: str
    definition_norm: str
    verified: bool = False
    semantic_score: int | None = None
    rebus_score: int | None = None
    creativity_score: int | None = None
    verify_note: str = ""
    canonical_definition_id: str | None = None


@dataclass(frozen=True)
class CanonicalDefinition:
    id: str
    word_normalized: str
    word_original_seed: str
    definition: str
    definition_norm: str
    verified: bool = False
    semantic_score: int | None = None
    rebus_score: int | None = None
    creativity_score: int | None = None
    usage_count: int = 0


@dataclass(frozen=True)
class DefinitionComparisonVote:
    model_id: str
    same_meaning: bool
    better: str
    reason: str = ""


@dataclass(frozen=True)
class DefinitionRefereeResult:
    same_meaning_votes: int
    better_a_votes: int
    better_b_votes: int
    equal_votes: int
    votes: list[DefinitionComparisonVote]

    @property
    def winner_votes(self) -> int:
        return max(self.better_a_votes, self.better_b_votes)

    @property
    def winner(self) -> str:
        if self.better_a_votes > self.better_b_votes:
            return "A"
        if self.better_b_votes > self.better_a_votes:
            return "B"
        return "equal"

    @property
    def merge_allowed(self) -> bool:
        return self.same_meaning_votes >= 4 and self.winner in {"A", "B"} and self.winner_votes >= 5

    @property
    def disagreement(self) -> bool:
        return self.same_meaning_votes >= 4 and self.winner in {"A", "B"} and self.winner_votes in {3, 4}


@dataclass(frozen=True)
class NearDuplicateCandidate:
    left: ClueDefinitionRecord
    right: ClueDefinitionRecord
    shared_tokens: int = 0
    similarity: float = 0.0


@dataclass(frozen=True)
class CanonicalDecision:
    canonical_definition: str
    canonical_definition_norm: str
    canonical_definition_id: str | None
    action: str
    same_meaning_votes: int | None = None
    winner_votes: int | None = None
    decision_note: str = ""


@dataclass
class BackfillStats:
    total_rows: int = 0
    exact_merges: int = 0
    near_merges: int = 0
    disagreement_3_of_6: int = 0
    disagreement_4_of_6: int = 0
    standalone_canonicals: int = 0
    reduced_words: list[tuple[str, int]] = field(default_factory=list)
