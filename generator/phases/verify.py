"""Phase 6: Verify definitions by asking AI to guess the word, then rate quality."""

from __future__ import annotations

from openai import OpenAI

from ..core.markdown_io import parse_markdown, write_with_definitions, ClueEntry
from ..core.ai_clues import (
    RATE_MIN_GUESSABILITY,
    RATE_MIN_SEMANTIC,
    create_client,
    rate_definition,
    verify_definition,
)
from ..core.clue_rating import (
    append_rating_to_note,
    extract_guessability_score,
    extract_semantic_score,
)
from ..core.diacritics import normalize


def _verify_clues(clues: list[ClueEntry], client: OpenAI) -> list[ClueEntry]:
    """Verify each clue by asking AI to guess the word."""
    result = []
    for clue in clues:
        if not clue.definition or clue.definition.startswith("["):
            clue.verified = False
            clue.verify_note = "Definiție lipsă"
            result.append(clue)
            continue

        print(f"  Verifying: {clue.word_normalized} - {clue.definition[:50]}...")
        try:
            guess = verify_definition(client, clue.definition, len(clue.word_normalized))
        except Exception as e:
            guess = f"[Eroare: {e}]"
        guess_normalized = normalize(guess)

        if guess_normalized == clue.word_normalized:
            clue.verified = True
            clue.verify_note = ""
            print(f"    ✓ AI a ghicit corect: {guess}")
        else:
            clue.verified = False
            clue.verify_note = f"AI a ghicit: {guess}"
            print(f"    ✗ AI a ghicit: {guess} (expected: {clue.word_normalized})")

        result.append(clue)

    return result


def _rate_clues(clues: list[ClueEntry], client: OpenAI) -> None:
    """Rate each usable clue definition quality in-place."""
    for clue in clues:
        if not clue.definition or clue.definition.startswith("["):
            continue

        try:
            rating = rate_definition(
                client,
                clue.word_normalized,
                clue.word_original,
                clue.definition,
                len(clue.word_normalized),
            )
        except Exception:
            rating = None

        semantic_score = rating.semantic_score if rating else 5
        guessability_score = rating.guessability_score if rating else 5
        feedback = rating.feedback if rating else ""
        clue.verify_note = append_rating_to_note(
            clue.verify_note,
            semantic_score=semantic_score,
            guessability_score=guessability_score,
            feedback=feedback,
        )

        semantic_ok = semantic_score >= RATE_MIN_SEMANTIC
        guessability_ok = guessability_score >= RATE_MIN_GUESSABILITY
        symbol = "★" if semantic_ok and guessability_ok else "⚠"
        print(
            f"    {symbol} {clue.word_normalized}: "
            f"semantic {semantic_score}/10, ghicibilitate {guessability_score}/10"
            f" — {feedback or 'fără feedback'}"
        )


def verify_puzzle(puzzle, client: OpenAI) -> tuple[int, int]:
    """Verify all clue definitions in-place and return (passed, total)."""
    print("Verifying horizontal definitions...")
    puzzle.horizontal_clues = _verify_clues(puzzle.horizontal_clues, client)

    print("Verifying vertical definitions...")
    puzzle.vertical_clues = _verify_clues(puzzle.vertical_clues, client)

    total = len(puzzle.horizontal_clues) + len(puzzle.vertical_clues)
    passed = sum(1 for c in puzzle.horizontal_clues + puzzle.vertical_clues if c.verified)
    return passed, total


def rate_puzzle(puzzle, client: OpenAI) -> tuple[float, float, int]:
    """Rate all usable definitions in-place."""
    print("Rating horizontal definitions...")
    _rate_clues(puzzle.horizontal_clues, client)

    print("Rating vertical definitions...")
    _rate_clues(puzzle.vertical_clues, client)

    semantic_scores = []
    guessability_scores = []
    for clue in puzzle.horizontal_clues + puzzle.vertical_clues:
        if not clue.verify_note:
            continue
        try:
            semantic_score = extract_semantic_score(clue.verify_note)
            guessability_score = extract_guessability_score(clue.verify_note)
            if semantic_score is None or guessability_score is None:
                continue
            semantic_scores.append(semantic_score)
            guessability_scores.append(guessability_score)
        except (ValueError, IndexError):
            continue

    avg_semantic = sum(semantic_scores) / len(semantic_scores) if semantic_scores else 0.0
    avg_guessability = (
        sum(guessability_scores) / len(guessability_scores) if guessability_scores else 0.0
    )
    return avg_semantic, avg_guessability, len(semantic_scores)


def run(input_file: str, output_file: str, **kwargs) -> None:
    """Verify all definitions by AI guessing, then rate quality."""
    print(f"Reading puzzle from {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        puzzle = parse_markdown(f.read())

    client = create_client()
    passed, total = verify_puzzle(puzzle, client)

    avg_semantic, avg_guessability, rated = rate_puzzle(puzzle, client)

    md = write_with_definitions(puzzle)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md)

    print(
        f"Verification: {passed}/{total} passed. "
        f"Avg semantic: {avg_semantic:.1f}/10. "
        f"Avg guessability: {avg_guessability:.1f}/10. "
        f"({rated} rated). Saved to {output_file}"
    )
