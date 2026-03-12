"""Phase 5: Generate definitions for each word using LM Studio."""

from __future__ import annotations
from openai import OpenAI
from ..core.markdown_io import parse_markdown, write_with_definitions, ClueEntry
from ..core.ai_clues import create_client, generate_definition


def _split_and_define(clues: list[ClueEntry], client: OpenAI,
                      theme: str) -> list[ClueEntry]:
    """Split compound clue entries and generate definitions for each word."""
    result = []
    for clue in clues:
        # Split "WORD1 - WORD2 - WORD3" into individual clues
        words = [w.strip() for w in clue.word_normalized.split(" - ") if w.strip()]
        originals = [o.strip() for o in clue.word_original.split(" - ")] if clue.word_original else [""] * len(words)

        # Pad originals if shorter
        while len(originals) < len(words):
            originals.append("")

        for word, original in zip(words, originals):
            if clue.definition:
                # Already has a definition, keep it
                result.append(ClueEntry(
                    row_number=clue.row_number,
                    word_normalized=word,
                    word_original=original,
                    definition=clue.definition,
                ))
            else:
                print(f"  Defining: {word} ({original or '?'})...")
                try:
                    definition = generate_definition(client, word, original, theme)
                except Exception as e:
                    definition = f"[Definiție lipsă: {e}]"
                print(f"    → {definition}")
                result.append(ClueEntry(
                    row_number=clue.row_number,
                    word_normalized=word,
                    word_original=original,
                    definition=definition,
                ))

    return result


def generate_definitions_for_puzzle(puzzle, client: OpenAI) -> None:
    """Expand clues and generate definitions in-place for the whole puzzle."""
    theme = puzzle.title or "Rebus Românesc"
    print(f"Theme: {theme}")

    print("Generating horizontal definitions...")
    puzzle.horizontal_clues = _split_and_define(puzzle.horizontal_clues, client, theme)

    print("Generating vertical definitions...")
    puzzle.vertical_clues = _split_and_define(puzzle.vertical_clues, client, theme)


def run(input_file: str, output_file: str, **kwargs) -> None:
    """Generate definitions for all words in the puzzle."""
    print(f"Reading puzzle from {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        puzzle = parse_markdown(f.read())

    client = create_client()
    generate_definitions_for_puzzle(puzzle, client)

    md = write_with_definitions(puzzle)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md)

    total = len(puzzle.horizontal_clues) + len(puzzle.vertical_clues)
    print(f"Generated {total} definitions. Saved to {output_file}")
