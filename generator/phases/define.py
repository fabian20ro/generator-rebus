"""Phase 5: Generate definitions for each word using LM Studio."""

from __future__ import annotations
import sys
import time
from openai import OpenAI
from ..config import LMSTUDIO_BASE_URL
from ..core.markdown_io import parse_markdown, write_with_definitions, ClueEntry


def _generate_definition(client: OpenAI, word: str, original: str,
                         theme: str, retries: int = 3) -> str:
    """Generate a definition for a single word."""
    display_word = original if original else word.lower()
    prompt = (
        f"Definește cuvântul '{display_word}' ca pentru o definiție de rebus românesc. "
        f"Tema rebusului este: {theme}. "
        "Definiția poate fi o propoziție scurtă sau o expresie. "
        f"Nu include cuvântul '{display_word}' în definiție. "
        "Răspunde doar cu definiția, fără ghilimele sau explicații."
    )

    for attempt in range(retries):
        try:
            response = client.chat.completions.create(
                model="default",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.8,
                max_tokens=100,
            )
            definition = response.choices[0].message.content.strip().strip('"').strip("'")

            # Basic validation
            if len(definition) < 5:
                continue
            if len(definition) > 200:
                definition = definition[:200].rsplit(" ", 1)[0]
            # Check the word itself isn't in the definition
            if display_word.lower() in definition.lower():
                continue
            return definition
        except Exception as e:
            if attempt < retries - 1:
                print(f"  Retry {attempt + 1}: {e}")
                time.sleep(2)
            else:
                return f"[Definiție lipsă: {e}]"

    return "[Definiție negenerată]"


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
                definition = _generate_definition(client, word, original, theme)
                print(f"    → {definition}")
                result.append(ClueEntry(
                    row_number=clue.row_number,
                    word_normalized=word,
                    word_original=original,
                    definition=definition,
                ))

    return result


def run(input_file: str, output_file: str, **kwargs) -> None:
    """Generate definitions for all words in the puzzle."""
    print(f"Reading puzzle from {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        puzzle = parse_markdown(f.read())

    theme = puzzle.title or "Rebus Românesc"
    print(f"Theme: {theme}")

    client = OpenAI(base_url=f"{LMSTUDIO_BASE_URL}/v1", api_key="not-needed")

    print("Generating horizontal definitions...")
    puzzle.horizontal_clues = _split_and_define(puzzle.horizontal_clues, client, theme)

    print("Generating vertical definitions...")
    puzzle.vertical_clues = _split_and_define(puzzle.vertical_clues, client, theme)

    md = write_with_definitions(puzzle)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md)

    total = len(puzzle.horizontal_clues) + len(puzzle.vertical_clues)
    print(f"Generated {total} definitions. Saved to {output_file}")
