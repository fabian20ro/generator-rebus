from __future__ import annotations

from dataclasses import dataclass, field

from rebus_generator.domain.pipeline_state import PuzzleAssessment, WorkingPuzzle
from rebus_generator.platform.io.rust_bridge import Candidate


@dataclass
class PreparedPuzzle:
    title: str
    title_score: int
    candidate: Candidate
    puzzle: WorkingPuzzle
    first_passed: int
    final_passed: int
    total: int
    definition_score: float
    blocking_words: list[str]
    assessment: PuzzleAssessment = field(default_factory=PuzzleAssessment)


PUZZLE_TIEBREAK_DELTA = 0.25
MIN_PUBLISHABLE_PASS_RATE = 0.1
MAX_REWRITE_ROUNDS = 30
