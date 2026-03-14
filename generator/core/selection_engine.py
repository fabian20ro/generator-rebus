"""Centralized clue and puzzle selection decisions."""

from __future__ import annotations

from dataclasses import dataclass

from .diacritics import normalize
from .pipeline_state import ClueCandidateVersion, PuzzleAssessment


@dataclass(frozen=True)
class SelectionDecision:
    winner: str
    used_tiebreak: bool
    reason: str
    a_summary: str
    b_summary: str
    winner_summary: str


def _normalized_definition(text: str) -> str:
    return " ".join(normalize(text or "").lower().split())


def clue_versions_equivalent(a: ClueCandidateVersion, b: ClueCandidateVersion) -> bool:
    return _normalized_definition(a.definition) == _normalized_definition(b.definition)


def clue_rank(version: ClueCandidateVersion) -> tuple[int, int, int, int, int]:
    scores = version.assessment.scores
    semantic = scores.semantic_exactness or 0
    rebus = scores.rebus_score or 0
    verified = 1 if version.assessment.verified is True else 0
    language = scores.language_integrity or 0
    family_penalty = 0 if scores.family_leakage else 1
    return (semantic + rebus, rebus, verified, language, family_penalty)


def choose_clue_version(
    a: ClueCandidateVersion,
    b: ClueCandidateVersion,
    *,
    tiebreaker=None,
) -> tuple[ClueCandidateVersion, SelectionDecision]:
    a_summary = a.definition
    b_summary = b.definition
    if clue_versions_equivalent(a, b):
        return a, SelectionDecision(
            winner="A",
            used_tiebreak=False,
            reason="equivalent_after_normalization",
            a_summary=a_summary,
            b_summary=b_summary,
            winner_summary=a_summary,
        )

    a_rank = clue_rank(a)
    b_rank = clue_rank(b)
    if a_rank > b_rank:
        return a, SelectionDecision("A", False, "deterministic_rank", a_summary, b_summary, a_summary)
    if b_rank > a_rank:
        return b, SelectionDecision("B", False, "deterministic_rank", a_summary, b_summary, b_summary)

    winner = "A"
    if tiebreaker is not None:
        winner = tiebreaker(a.definition, b.definition)
    chosen = a if winner != "B" else b
    return chosen, SelectionDecision(
        winner="B" if winner == "B" else "A",
        used_tiebreak=True,
        reason="llm_tiebreak",
        a_summary=a_summary,
        b_summary=b_summary,
        winner_summary=chosen.definition,
    )


def puzzle_rank(assessment: PuzzleAssessment) -> tuple[int, float, float]:
    publishable = 1 if not assessment.blocker_words else 0
    return (publishable, assessment.definition_score, assessment.avg_rebus)


def choose_puzzle_assessment(
    a: PuzzleAssessment,
    b: PuzzleAssessment,
    *,
    tiebreaker=None,
) -> tuple[str, SelectionDecision]:
    a_summary = (
        f"score={a.definition_score:.2f}, blockers={len(a.blocker_words)}, "
        f"rebus={a.avg_rebus:.2f}, ambiguity={a.ambiguity_count}"
    )
    b_summary = (
        f"score={b.definition_score:.2f}, blockers={len(b.blocker_words)}, "
        f"rebus={b.avg_rebus:.2f}, ambiguity={b.ambiguity_count}"
    )
    a_rank = puzzle_rank(a)
    b_rank = puzzle_rank(b)
    if a_rank > b_rank:
        return "A", SelectionDecision("A", False, "deterministic_rank", a_summary, b_summary, a_summary)
    if b_rank > a_rank:
        return "B", SelectionDecision("B", False, "deterministic_rank", a_summary, b_summary, b_summary)

    winner = "A"
    if tiebreaker is not None:
        winner = tiebreaker(a_summary, b_summary)
    winner_summary = a_summary if winner != "B" else b_summary
    return ("B" if winner == "B" else "A"), SelectionDecision(
        winner="B" if winner == "B" else "A",
        used_tiebreak=True,
        reason="llm_tiebreak",
        a_summary=a_summary,
        b_summary=b_summary,
        winner_summary=winner_summary,
    )
