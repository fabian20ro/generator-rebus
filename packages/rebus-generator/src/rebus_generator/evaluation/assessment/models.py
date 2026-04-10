from __future__ import annotations

from dataclasses import dataclass, field

from rebus_generator.domain.pipeline_state import ClueAssessment, ClueCandidateVersion, ClueScores
from rebus_generator.domain.selection_engine import choose_clue_version, stable_tie_rng


@dataclass
class WordCandidate:
    word: str
    tier: str
    display_word: str
    length: int
    word_type: str
    dex_definitions: str
    pass1_definition: str = ""
    pass1_guesses: list[str] = field(default_factory=list)
    pass1_verified: bool = False
    pass1_semantic: int = 0
    pass1_rebus: int = 0
    pass1_rated: bool = False
    pass2_definition: str = ""
    pass2_guesses: list[str] = field(default_factory=list)
    pass2_verified: bool = False
    pass2_semantic: int = 0
    pass2_rebus: int = 0
    pass2_rated: bool = False
    best_source: str = "pass1"


@dataclass
class TierResult:
    tier: str
    total: int = 0
    passed: int = 0
    semantic_sum: float = 0.0
    rebus_sum: float = 0.0
    rated_count: int = 0

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def avg_semantic(self) -> float:
        return self.semantic_sum / self.rated_count if self.rated_count else 0.0

    @property
    def avg_rebus(self) -> float:
        return self.rebus_sum / self.rated_count if self.rated_count else 0.0


@dataclass
class AssessmentResult:
    candidates: list[WordCandidate] = field(default_factory=list)
    tier_results: dict[str, TierResult] = field(default_factory=dict)

    @property
    def pass_rate(self) -> float:
        total = len(self.candidates)
        passed = sum(1 for c in self.candidates if best_verified(c))
        return passed / total if total else 0.0

    @property
    def avg_semantic(self) -> float:
        rated = [c for c in self.candidates if best_rated(c)]
        return sum(best_semantic(c) for c in rated) / len(rated) if rated else 0.0

    @property
    def avg_rebus(self) -> float:
        rated = [c for c in self.candidates if best_rated(c)]
        return sum(best_rebus(c) for c in rated) / len(rated) if rated else 0.0

    @property
    def tier_balanced_pass_rate(self) -> float:
        if not self.tier_results:
            return 0.0
        included = [tr.pass_rate for tr in self.tier_results.values() if tr.total > 0]
        return sum(included) / len(included) if included else 0.0

    @property
    def composite(self) -> float:
        return self.pass_rate * 100 + self.avg_semantic * 3 + self.avg_rebus * 2

    def to_dict(self) -> dict:
        protected_tiers = {
            name: self.tier_results[name]
            for name in self.tier_results
            if name in {"high", "easy", "control"}
        }
        return {
            "composite": round(self.composite, 1),
            "pass_rate": round(self.pass_rate, 3),
            "tier_balanced_pass_rate": round(self.tier_balanced_pass_rate, 3),
            "avg_semantic": round(self.avg_semantic, 1),
            "avg_rebus": round(self.avg_rebus, 1),
            "tiers": {
                name: {
                    "pass_rate": round(tr.pass_rate, 3),
                    "avg_semantic": round(tr.avg_semantic, 1),
                    "avg_rebus": round(tr.avg_rebus, 1),
                    "count": tr.total,
                }
                for name, tr in sorted(self.tier_results.items())
            },
            "protected_control_summary": {
                name: {
                    "pass_rate": round(tr.pass_rate, 3),
                    "avg_semantic": round(tr.avg_semantic, 1),
                    "avg_rebus": round(tr.avg_rebus, 1),
                    "count": tr.total,
                }
                for name, tr in sorted(protected_tiers.items())
            },
            "candidates": [
                {
                    "word": c.word,
                    "tier": c.tier,
                    "best_source": c.best_source,
                    "definition": best_definition(c),
                    "verified": best_verified(c),
                    "guesses": best_guesses(c),
                    "semantic": best_semantic(c),
                    "rebus": best_rebus(c),
                }
                for c in self.candidates
            ],
        }


def best_verified(candidate: WordCandidate) -> bool:
    return candidate.pass1_verified if candidate.best_source == "pass1" else candidate.pass2_verified


def best_rated(candidate: WordCandidate) -> bool:
    return candidate.pass1_rated if candidate.best_source == "pass1" else candidate.pass2_rated


def best_semantic(candidate: WordCandidate) -> int:
    return candidate.pass1_semantic if candidate.best_source == "pass1" else candidate.pass2_semantic


def best_rebus(candidate: WordCandidate) -> int:
    return candidate.pass1_rebus if candidate.best_source == "pass1" else candidate.pass2_rebus


def best_definition(candidate: WordCandidate) -> str:
    return candidate.pass1_definition if candidate.best_source == "pass1" else candidate.pass2_definition


def best_guesses(candidate: WordCandidate) -> list[str]:
    return candidate.pass1_guesses if candidate.best_source == "pass1" else candidate.pass2_guesses


def pick_best(candidate: WordCandidate) -> None:
    def _version(source: str) -> ClueCandidateVersion:
        definition = candidate.pass1_definition if source == "pass1" else candidate.pass2_definition
        verified = candidate.pass1_verified if source == "pass1" else candidate.pass2_verified
        semantic = candidate.pass1_semantic if source == "pass1" else candidate.pass2_semantic
        rebus = candidate.pass1_rebus if source == "pass1" else candidate.pass2_rebus
        guesses = candidate.pass1_guesses if source == "pass1" else candidate.pass2_guesses
        return ClueCandidateVersion(
            definition=definition,
            round_index=1 if source == "pass1" else 2,
            source=source,
            assessment=ClueAssessment(
                verified=verified,
                verify_candidates=list(guesses),
                scores=ClueScores(
                    semantic_exactness=semantic,
                    answer_targeting=rebus,
                    rebus_score=rebus,
                    language_integrity=10,
                ),
            ),
        )

    chosen, _decision = choose_clue_version(
        _version("pass1"),
        _version("pass2"),
        rng=stable_tie_rng(
            "assessment_pick_best",
            candidate.pass1_definition,
            candidate.pass2_definition,
        ),
    )
    candidate.best_source = "pass2" if chosen.source == "pass2" else "pass1"
