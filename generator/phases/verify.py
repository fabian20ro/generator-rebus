"""Phase 6: Verify definitions by asking AI to guess the word."""

from __future__ import annotations
import sys
from openai import OpenAI
from ..config import LMSTUDIO_BASE_URL
from ..core.markdown_io import parse_markdown, write_with_definitions, ClueEntry
from ..core.diacritics import normalize


def _verify_definition(client: OpenAI, definition: str) -> str:
    """Ask AI to guess the word from a definition. Returns the guessed word."""
    prompt = (
        f"Ghicește cuvântul din această definiție de rebus: \"{definition}\"\n"
        "Răspunde cu un singur cuvânt, fără explicații."
    )

    try:
        response = client.chat.completions.create(
            model="default",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=20,
        )
        guess = response.choices[0].message.content.strip().strip('"').strip("'")
        # Take only the first word
        guess = guess.split()[0] if guess.split() else guess
        return guess
    except Exception as e:
        return f"[Eroare: {e}]"


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
        guess = _verify_definition(client, clue.definition)
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


def run(input_file: str, output_file: str, **kwargs) -> None:
    """Verify all definitions by AI guessing."""
    print(f"Reading puzzle from {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        puzzle = parse_markdown(f.read())

    client = OpenAI(base_url=f"{LMSTUDIO_BASE_URL}/v1", api_key="not-needed")

    print("Verifying horizontal definitions...")
    puzzle.horizontal_clues = _verify_clues(puzzle.horizontal_clues, client)

    print("Verifying vertical definitions...")
    puzzle.vertical_clues = _verify_clues(puzzle.vertical_clues, client)

    md = write_with_definitions(puzzle)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md)

    total = len(puzzle.horizontal_clues) + len(puzzle.vertical_clues)
    passed = sum(1 for c in puzzle.horizontal_clues + puzzle.vertical_clues if c.verified)
    print(f"Verification: {passed}/{total} passed. Saved to {output_file}")
