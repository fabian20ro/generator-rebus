"""Typed internal pipeline state for clue generation, evaluation, and selection."""

from __future__ import annotations

from dataclasses import dataclass, field

from .clue_rating import (
    append_rating_to_note,
    extract_creativity_score,
    extract_feedback,
    extract_guessability_score,
    extract_rebus_score,
    extract_verify_candidates,
    extract_semantic_score,
    extract_wrong_guess,
)
from rebus_generator.platform.io.markdown_io import ClueEntry, PuzzleData


@dataclass
class ClueScores:
    semantic_exactness: int | None = None
    answer_targeting: int | None = None
    ambiguity_risk: int | None = None
    family_leakage: bool = False
    language_integrity: int | None = None
    creativity: int | None = None
    rebus_score: int | None = None


@dataclass
class ClueFailureReason:
    kind: str
    message: str


@dataclass
class ClueAssessment:
    verified: bool | None = None
    verify_candidates: list[str] = field(default_factory=list)
    verify_votes: dict[str, list[str]] = field(default_factory=dict)
    verify_vote_sources: dict[str, str] = field(default_factory=dict)
    verify_complete: bool = True
    wrong_guess: str = ""
    feedback: str = ""
    rating_votes: dict[str, object] = field(default_factory=dict)
    rating_vote_sources: dict[str, str] = field(default_factory=dict)
    rating_complete: bool = True
    scores: ClueScores = field(default_factory=ClueScores)
    failure_reason: ClueFailureReason | None = None
    rewrite_rejection_reason: str = ""
    rarity_only_override: bool = False
    form_mismatch: bool = False
    form_mismatch_detail: str = ""
    verified_by: str = ""
    rated_by: str = ""


@dataclass
class ClueCandidateVersion:
    definition: str
    round_index: int
    source: str
    generated_by: str = ""
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
    word_type: str = ""

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
    avg_creativity: float = 0.0
    avg_rebus: float = 0.0
    min_rebus: int = 0
    verified_count: int = 0
    total_clues: int = 0
    pass_rate: float = 0.0
    scores_complete: bool = True
    verify_incomplete_count: int = 0
    rating_incomplete_count: int = 0
    incomplete_words: list[str] = field(default_factory=list)


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
    verify_candidates = extract_verify_candidates(entry.verify_note)
    wrong_guess = ""
    if not entry.verified:
        wrong_guess = extract_wrong_guess(entry.verify_note)
    feedback = extract_feedback(entry.verify_note)
    creativity = extract_creativity_score(entry.verify_note)
    rebus = extract_rebus_score(entry.verify_note)
    return ClueAssessment(
        verified=entry.verified,
        verify_candidates=verify_candidates,
        wrong_guess=wrong_guess,
        feedback=feedback,
        scores=ClueScores(
            semantic_exactness=semantic,
            answer_targeting=targeting,
            ambiguity_risk=(11 - targeting) if targeting is not None else None,
            family_leakage=False,
            language_integrity=10,
            creativity=creativity,
            rebus_score=rebus,
        ),
        failure_reason=None,
    )


def _entry_from_version(clue: WorkingClue, version: ClueCandidateVersion) -> ClueEntry:
    return ClueEntry(
        row_number=clue.row_number,
        word_normalized=clue.word_normalized,
        word_original=clue.word_original,
        word_type=clue.word_type,
        definition=version.definition,
        verified=version.assessment.verified,
        verify_note=render_verify_note(version.assessment),
        start_row=clue.start_row,
        start_col=clue.start_col,
    )


def render_verify_note(assessment: ClueAssessment) -> str:
    note = ""
    if len(assessment.verify_candidates) > 1:
        note = f"AI a propus: {', '.join(assessment.verify_candidates)}"
    elif assessment.verify_candidates:
        note = f"AI a ghicit: {assessment.verify_candidates[0]}"
    elif assessment.wrong_guess:
        note = f"AI a ghicit: {assessment.wrong_guess}"
    if assessment.scores.semantic_exactness is not None and assessment.scores.answer_targeting is not None:
        note = append_rating_to_note(
            note,
            semantic_score=assessment.scores.semantic_exactness,
            guessability_score=assessment.scores.answer_targeting,
            feedback=assessment.feedback,
            creativity_score=assessment.scores.creativity,
            rebus_score=assessment.scores.rebus_score,
        )
    elif assessment.feedback:
        note = assessment.feedback
    return note


def working_clue_from_entry(entry: ClueEntry) -> WorkingClue:
    current = ClueCandidateVersion(
        definition=entry.definition,
        round_index=0,
        source="import",
        generated_by="",
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
        word_type=getattr(entry, "word_type", ""),
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
            word_type=getattr(entry, "word_type", ""),
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
    generated_by: str = "",
) -> None:
    clue.current = ClueCandidateVersion(
        definition=definition,
        round_index=round_index,
        source=source,
        generated_by=generated_by,
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
