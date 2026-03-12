"""Phase 6: Verify definitions by asking AI to guess the word."""

from __future__ import annotations
from openai import OpenAI
from ..core.markdown_io import parse_markdown, write_with_definitions, ClueEntry
from ..core.ai_clues import create_client, verify_definition
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


def verify_puzzle(puzzle, client: OpenAI) -> tuple[int, int]:
    """Verify all clue definitions in-place and return (passed, total)."""
    print("Verifying horizontal definitions...")
    puzzle.horizontal_clues = _verify_clues(puzzle.horizontal_clues, client)

    print("Verifying vertical definitions...")
    puzzle.vertical_clues = _verify_clues(puzzle.vertical_clues, client)

    total = len(puzzle.horizontal_clues) + len(puzzle.vertical_clues)
    passed = sum(1 for c in puzzle.horizontal_clues + puzzle.vertical_clues if c.verified)
    return passed, total


def run(input_file: str, output_file: str, **kwargs) -> None:
    """Verify all definitions by AI guessing."""
    print(f"Reading puzzle from {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        puzzle = parse_markdown(f.read())

    client = create_client()
    passed, total = verify_puzzle(puzzle, client)

    md = write_with_definitions(puzzle)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"Verification: {passed}/{total} passed. Saved to {output_file}")
