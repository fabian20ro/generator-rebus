"""Phase 6: Verify definitions by asking AI to guess the word, then rate quality."""

from __future__ import annotations

from openai import OpenAI

from ..core.markdown_io import parse_markdown, write_with_definitions
from ..core.ai_clues import (
    RATE_MIN_GUESSABILITY,
    RATE_MIN_SEMANTIC,
    create_client,
    rate_definition,
    verify_definition,
    contains_english_markers,
)
from ..core.pipeline_state import (
    ClueAssessment,
    ClueFailureReason,
    ClueScores,
    WorkingClue,
    WorkingPuzzle,
    all_working_clues,
    puzzle_from_working_state,
    working_clue_from_entry,
    working_puzzle_from_puzzle,
)
from ..core.diacritics import normalize


def _build_failure_reason(clue: WorkingClue) -> ClueFailureReason | None:
    assessment = clue.current.assessment
    if assessment.scores.family_leakage:
        return ClueFailureReason("same_family", "Definiția folosește aceeași familie lexicală.")
    if assessment.wrong_guess:
        return ClueFailureReason("wrong_guess", f"AI a ghicit: {assessment.wrong_guess}")
    if assessment.scores.semantic_exactness is not None and assessment.scores.semantic_exactness < RATE_MIN_SEMANTIC:
        return ClueFailureReason("low_semantic", "Definiția nu este suficient de exactă.")
    if assessment.scores.answer_targeting is not None and assessment.scores.answer_targeting < RATE_MIN_GUESSABILITY:
        return ClueFailureReason("low_targeting", "Definiția duce spre alt răspuns sau este prea vagă.")
    if assessment.feedback:
        return ClueFailureReason("feedback", assessment.feedback)
    return None


def _verify_clues(
    clues: list[WorkingClue],
    client: OpenAI,
    skip_words: set[str] | None = None,
) -> list[WorkingClue]:
    """Verify each clue by asking AI to guess the word."""
    result = []
    for clue in clues:
        if not isinstance(clue, WorkingClue):
            clue = working_clue_from_entry(clue)
        if skip_words and clue.word_normalized in skip_words:
            result.append(clue)
            continue
        definition = clue.current.definition
        if not definition or definition.startswith("["):
            clue.current.assessment = ClueAssessment(
                verified=False,
                feedback="Definiție lipsă",
                failure_reason=ClueFailureReason("missing_definition", "Definiție lipsă"),
                scores=ClueScores(
                    semantic_exactness=1,
                    answer_targeting=1,
                    ambiguity_risk=10,
                    family_leakage=False,
                    language_integrity=10,
                ),
            )
            result.append(clue)
            continue

        print(f"  Verifying: {clue.word_normalized} - {definition[:50]}...")
        try:
            guess = verify_definition(client, definition, len(clue.word_normalized))
        except Exception as e:
            guess = f"[Eroare: {e}]"
        guess_normalized = normalize(guess)

        if guess_normalized == clue.word_normalized:
            clue.current.assessment.verified = True
            clue.current.assessment.wrong_guess = ""
            clue.current.assessment.failure_reason = None
            print(f"    ✓ AI a ghicit corect: {guess}")
        else:
            clue.current.assessment.verified = False
            clue.current.assessment.wrong_guess = guess
            clue.current.assessment.failure_reason = ClueFailureReason("wrong_guess", f"AI a ghicit: {guess}")
            print(f"    ✗ AI a ghicit: {guess} (expected: {clue.word_normalized})")

        result.append(clue)

    return result


def _rate_clues(
    clues: list[WorkingClue],
    client: OpenAI,
    skip_words: set[str] | None = None,
) -> None:
    """Rate each usable clue definition quality in-place."""
    for clue in clues:
        if not isinstance(clue, WorkingClue):
            clue = working_clue_from_entry(clue)
        if skip_words and clue.word_normalized in skip_words:
            continue
        definition = clue.current.definition
        if not definition or definition.startswith("["):
            continue

        try:
            rating = rate_definition(
                client,
                clue.word_normalized,
                clue.word_original,
                definition,
                len(clue.word_normalized),
            )
        except Exception:
            rating = None

        semantic_score = rating.semantic_score if rating else 5
        guessability_score = rating.guessability_score if rating else 5
        feedback = rating.feedback if rating else ""
        rarity_override = rating.rarity_only_override if rating else False
        clue.current.assessment.feedback = feedback
        clue.current.assessment.rarity_only_override = rarity_override
        clue.current.assessment.scores = ClueScores(
            semantic_exactness=semantic_score,
            answer_targeting=guessability_score,
            ambiguity_risk=11 - guessability_score,
            family_leakage=False,
            language_integrity=1 if contains_english_markers(definition) else 10,
        )
        clue.current.assessment.failure_reason = _build_failure_reason(clue)

        semantic_ok = semantic_score >= RATE_MIN_SEMANTIC
        guessability_ok = guessability_score >= RATE_MIN_GUESSABILITY
        symbol = "★" if semantic_ok and guessability_ok else "⚠"
        print(
            f"    {symbol} {clue.word_normalized}: "
            f"„{definition}” -> "
            f"semantic {semantic_score}/10, ghicibilitate {guessability_score}/10"
            f" — {feedback or 'fără feedback'}"
        )


def verify_working_puzzle(
    puzzle: WorkingPuzzle,
    client: OpenAI,
    skip_words: set[str] | None = None,
) -> tuple[int, int]:
    """Verify all clue definitions in-place and return (passed, total)."""
    print("Verifying horizontal definitions...")
    puzzle.horizontal_clues = _verify_clues(puzzle.horizontal_clues, client, skip_words=skip_words)

    print("Verifying vertical definitions...")
    puzzle.vertical_clues = _verify_clues(puzzle.vertical_clues, client, skip_words=skip_words)

    total = len(puzzle.horizontal_clues) + len(puzzle.vertical_clues)
    passed = sum(1 for c in all_working_clues(puzzle) if c.current.assessment.verified)
    return passed, total


def rate_working_puzzle(
    puzzle: WorkingPuzzle,
    client: OpenAI,
    skip_words: set[str] | None = None,
) -> tuple[float, float, int]:
    """Rate all usable definitions in-place."""
    print("Rating horizontal definitions...")
    _rate_clues(puzzle.horizontal_clues, client, skip_words=skip_words)

    print("Rating vertical definitions...")
    _rate_clues(puzzle.vertical_clues, client, skip_words=skip_words)

    semantic_scores = []
    guessability_scores = []
    for clue in all_working_clues(puzzle):
        semantic_score = clue.current.assessment.scores.semantic_exactness
        guessability_score = clue.current.assessment.scores.answer_targeting
        if semantic_score is None or guessability_score is None:
            continue
        semantic_scores.append(semantic_score)
        guessability_scores.append(guessability_score)

    avg_semantic = sum(semantic_scores) / len(semantic_scores) if semantic_scores else 0.0
    avg_guessability = (
        sum(guessability_scores) / len(guessability_scores) if guessability_scores else 0.0
    )
    return avg_semantic, avg_guessability, len(semantic_scores)


def verify_puzzle(puzzle, client: OpenAI) -> tuple[int, int]:
    state = working_puzzle_from_puzzle(puzzle, split_compound=False)
    passed, total = verify_working_puzzle(state, client)
    rendered = puzzle_from_working_state(state)
    puzzle.horizontal_clues = rendered.horizontal_clues
    puzzle.vertical_clues = rendered.vertical_clues
    return passed, total


def rate_puzzle(puzzle, client: OpenAI) -> tuple[float, float, int]:
    state = working_puzzle_from_puzzle(puzzle, split_compound=False)
    avg_semantic, avg_guessability, rated = rate_working_puzzle(state, client)
    rendered = puzzle_from_working_state(state)
    puzzle.horizontal_clues = rendered.horizontal_clues
    puzzle.vertical_clues = rendered.vertical_clues
    return avg_semantic, avg_guessability, rated


def run(input_file: str, output_file: str, **kwargs) -> None:
    """Verify all definitions by AI guessing, then rate quality."""
    print(f"Reading puzzle from {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        puzzle = parse_markdown(f.read())

    client = create_client()
    state = working_puzzle_from_puzzle(puzzle, split_compound=False)
    passed, total = verify_working_puzzle(state, client)
    avg_semantic, avg_guessability, rated = rate_working_puzzle(state, client)
    puzzle = puzzle_from_working_state(state)

    md = write_with_definitions(puzzle)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md)

    print(
        f"Verification: {passed}/{total} passed. "
        f"Avg semantic: {avg_semantic:.1f}/10. "
        f"Avg guessability: {avg_guessability:.1f}/10. "
        f"({rated} rated). Saved to {output_file}"
    )
