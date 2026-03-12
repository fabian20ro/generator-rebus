"""Phase 6: Verify definitions by asking AI to guess the word, then rate quality."""

from __future__ import annotations
from openai import OpenAI
from ..core.markdown_io import parse_markdown, write_with_definitions, ClueEntry
from ..core.ai_clues import create_client, verify_definition, rate_definition, RATE_MIN_QUALITY
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
            guess = verify_definition(client, clue.definition)
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
            score, feedback = rate_definition(
                client, clue.word_normalized, clue.word_original, clue.definition
            )
        except Exception:
            score, feedback = 5, ""

        note_parts = []
        if clue.verify_note:
            note_parts.append(clue.verify_note)
        note_parts.append(f"Scor: {score}/10")
        if feedback:
            note_parts.append(feedback)
        clue.verify_note = " | ".join(note_parts)

        symbol = "★" if score >= RATE_MIN_QUALITY else "⚠"
        print(f"    {symbol} {clue.word_normalized}: {score}/10 — {feedback or 'fără feedback'}")


def verify_puzzle(puzzle, client: OpenAI) -> tuple[int, int]:
    """Verify all clue definitions in-place and return (passed, total)."""
    print("Verifying horizontal definitions...")
    puzzle.horizontal_clues = _verify_clues(puzzle.horizontal_clues, client)

    print("Verifying vertical definitions...")
    puzzle.vertical_clues = _verify_clues(puzzle.vertical_clues, client)

    total = len(puzzle.horizontal_clues) + len(puzzle.vertical_clues)
    passed = sum(1 for c in puzzle.horizontal_clues + puzzle.vertical_clues if c.verified)
    return passed, total


def rate_puzzle(puzzle, client: OpenAI) -> tuple[float, int]:
    """Rate all usable definitions in-place. Returns (avg_score, rated_count)."""
    print("Rating horizontal definitions...")
    _rate_clues(puzzle.horizontal_clues, client)

    print("Rating vertical definitions...")
    _rate_clues(puzzle.vertical_clues, client)

    scores = []
    for clue in puzzle.horizontal_clues + puzzle.vertical_clues:
        if clue.verify_note and "Scor:" in clue.verify_note:
            try:
                score_str = clue.verify_note.split("Scor:")[1].split("/")[0].strip()
                scores.append(int(score_str))
            except (ValueError, IndexError):
                pass

    avg = sum(scores) / len(scores) if scores else 0.0
    return avg, len(scores)


def run(input_file: str, output_file: str, **kwargs) -> None:
    """Verify all definitions by AI guessing, then rate quality."""
    print(f"Reading puzzle from {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        puzzle = parse_markdown(f.read())

    client = create_client()
    passed, total = verify_puzzle(puzzle, client)

    avg_score, rated = rate_puzzle(puzzle, client)

    md = write_with_definitions(puzzle)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"Verification: {passed}/{total} passed. Avg quality: {avg_score:.1f}/10 ({rated} rated). Saved to {output_file}")
