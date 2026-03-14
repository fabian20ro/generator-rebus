"""Typed internal pipeline state for clue generation, evaluation, and selection."""

from __future__ import annotations

from dataclasses import dataclass, field

from .clue_rating import (
    append_rating_to_note,
    extract_feedback,
    extract_guessability_score,
    extract_semantic_score,
    extract_wrong_guess,
)
from .markdown_io import ClueEntry, PuzzleData


@dataclass
class ClueScores:
    semantic_exactness: int | None = None
    answer_targeting: int | None = None
    ambiguity_risk: int | None = None
    family_leakage: bool = False
    language_integrity: int | None = None


@dataclass
class ClueFailureReason:
    kind: str
    message: str


@dataclass
class ClueAssessment:
    verified: bool | None = None
    wrong_guess: str = ""
    feedback: str = ""
    scores: ClueScores = field(default_factory=ClueScores)
    failure_reason: ClueFailureReason | None = None
    rarity_only_override: bool = False


@dataclass
class ClueCandidateVersion:
    definition: str
    round_index: int
    source: str
    assessment: ClueAssessment = field(default_factory=ClueAssessment)


@dataclass
class WorkingClue:
    row_number: int
    word_normalized: str
    word_original: str
    start_row: int = 0
    start_col: int = 0
    current: ClueCandidateVersion = field(default_factory=lambda: ClueCandidateVersion("", 0, "initial"))
    best: ClueCandidateVersion | None = None
    history: list[ClueCandidateVersion] = field(default_factory=list)
    locked: bool = False

    def active_version(self) -> ClueCandidateVersion:
        return self.best or self.current


@dataclass
class PuzzleAssessment:
    definition_score: float = 0.0
    avg_exactness: float = 0.0
    avg_targeting: float = 0.0
    ambiguity_count: int = 0
    short_word_burden: int = 0
    rare_word_burden: int = 0
    blocker_words: list[str] = field(default_factory=list)


@dataclass
class WorkingPuzzle:
    title: str
    size: int
    grid: list[list[str]]
    horizontal_clues: list[WorkingClue]
    vertical_clues: list[WorkingClue]
    assessment: PuzzleAssessment = field(default_factory=PuzzleAssessment)
    metadata: dict[str, object] = field(default_factory=dict)


def all_working_clues(puzzle: WorkingPuzzle) -> list[WorkingClue]:
    return puzzle.horizontal_clues + puzzle.vertical_clues


def _assessment_from_entry(entry: ClueEntry) -> ClueAssessment:
    semantic = extract_semantic_score(entry.verify_note)
    targeting = extract_guessability_score(entry.verify_note)
    wrong_guess = extract_wrong_guess(entry.verify_note)
    feedback = extract_feedback(entry.verify_note)
    return ClueAssessment(
        verified=entry.verified,
        wrong_guess=wrong_guess,
        feedback=feedback,
        scores=ClueScores(
            semantic_exactness=semantic,
            answer_targeting=targeting,
            ambiguity_risk=(11 - targeting) if targeting is not None else None,
            family_leakage=False,
            language_integrity=10,
        ),
        failure_reason=None,
    )


def _entry_from_version(clue: WorkingClue, version: ClueCandidateVersion) -> ClueEntry:
    return ClueEntry(
        row_number=clue.row_number,
        word_normalized=clue.word_normalized,
        word_original=clue.word_original,
        definition=version.definition,
        verified=version.assessment.verified,
        verify_note=render_verify_note(version.assessment),
        start_row=clue.start_row,
        start_col=clue.start_col,
    )


def render_verify_note(assessment: ClueAssessment) -> str:
    note = ""
    if assessment.wrong_guess:
        note = f"AI a ghicit: {assessment.wrong_guess}"
    if assessment.scores.semantic_exactness is not None and assessment.scores.answer_targeting is not None:
        note = append_rating_to_note(
            note,
            semantic_score=assessment.scores.semantic_exactness,
            guessability_score=assessment.scores.answer_targeting,
            feedback=assessment.feedback,
        )
    elif assessment.feedback:
        note = assessment.feedback
    return note


def working_clue_from_entry(entry: ClueEntry) -> WorkingClue:
    current = ClueCandidateVersion(
        definition=entry.definition,
        round_index=0,
        source="import",
        assessment=_assessment_from_entry(entry),
    )
    return WorkingClue(
        row_number=entry.row_number,
        word_normalized=entry.word_normalized,
        word_original=entry.word_original,
        start_row=entry.start_row,
        start_col=entry.start_col,
        current=current,
        best=None,
        history=[current] if entry.definition else [],
        locked=False,
    )


def _split_compound_entry(entry: ClueEntry) -> list[ClueEntry]:
    words = [w.strip() for w in entry.word_normalized.split(" - ") if w.strip()]
    originals = [o.strip() for o in entry.word_original.split(" - ")] if entry.word_original else [""] * len(words)
    while len(originals) < len(words):
        originals.append("")
    if len(words) <= 1:
        return [entry]
    return [
        ClueEntry(
            row_number=entry.row_number,
            word_normalized=word,
            word_original=original,
            definition=entry.definition,
            verified=entry.verified,
            verify_note=entry.verify_note,
            start_row=entry.start_row,
            start_col=entry.start_col,
        )
        for word, original in zip(words, originals)
    ]


def working_puzzle_from_puzzle(puzzle: PuzzleData, *, split_compound: bool = False) -> WorkingPuzzle:
    horizontal_entries = getattr(puzzle, "horizontal_clues", [])
    vertical_entries = getattr(puzzle, "vertical_clues", [])
    if split_compound:
        horizontal_entries = [split for clue in horizontal_entries for split in _split_compound_entry(clue)]
        vertical_entries = [split for clue in vertical_entries for split in _split_compound_entry(clue)]
    return WorkingPuzzle(
        title=getattr(puzzle, "title", ""),
        size=getattr(puzzle, "size", len(getattr(puzzle, "grid", [])) or 0),
        grid=[list(row) for row in getattr(puzzle, "grid", [])],
        horizontal_clues=[working_clue_from_entry(clue) for clue in horizontal_entries],
        vertical_clues=[working_clue_from_entry(clue) for clue in vertical_entries],
    )


def puzzle_from_working_state(state: WorkingPuzzle) -> PuzzleData:
    return PuzzleData(
        title=state.title,
        size=state.size,
        grid=[list(row) for row in state.grid],
        horizontal_clues=[_entry_from_version(clue, clue.active_version()) for clue in state.horizontal_clues],
        vertical_clues=[_entry_from_version(clue, clue.active_version()) for clue in state.vertical_clues],
    )


def set_current_definition(
    clue: WorkingClue,
    definition: str,
    *,
    round_index: int,
    source: str,
) -> None:
    clue.current = ClueCandidateVersion(
        definition=definition,
        round_index=round_index,
        source=source,
        assessment=ClueAssessment(),
    )
    clue.history.append(clue.current)


def update_current_assessment(
    clue: WorkingClue,
    *,
    verified: bool | None = None,
    wrong_guess: str | None = None,
    feedback: str | None = None,
    scores: ClueScores | None = None,
    failure_reason: ClueFailureReason | None = None,
) -> None:
    if verified is not None:
        clue.current.assessment.verified = verified
    if wrong_guess is not None:
        clue.current.assessment.wrong_guess = wrong_guess
    if feedback is not None:
        clue.current.assessment.feedback = feedback
    if scores is not None:
        clue.current.assessment.scores = scores
    clue.current.assessment.failure_reason = failure_reason
