"""Phase 4: Find a theme for the filled grid using LM Studio."""

from __future__ import annotations
import sys
from openai import OpenAI
from ..config import LMSTUDIO_BASE_URL
from ..core.markdown_io import parse_markdown, write_with_definitions, ClueEntry


def _collect_words(puzzle) -> list[str]:
    """Collect all unique words from the puzzle clues."""
    words = set()
    for clue in puzzle.horizontal_clues:
        for w in clue.word_normalized.split(" - "):
            w = w.strip()
            if w:
                words.add(w)
    for clue in puzzle.vertical_clues:
        for w in clue.word_normalized.split(" - "):
            w = w.strip()
            if w:
                words.add(w)
    return sorted(words)


def run(input_file: str, output_file: str, **kwargs) -> None:
    """Generate a theme/title for the puzzle using LM Studio."""
    print(f"Reading puzzle from {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        puzzle = parse_markdown(f.read())

    words = _collect_words(puzzle)
    if not words:
        print("Error: no words found in puzzle")
        sys.exit(1)

    print(f"Found {len(words)} words: {', '.join(words[:10])}...")

    client = OpenAI(base_url=f"{LMSTUDIO_BASE_URL}/v1", api_key="not-needed")

    prompt = (
        f"Ai aici o listă de cuvinte dintr-un rebus românesc: {', '.join(words)}.\n"
        "Sugerează o temă scurtă și creativă pentru acest rebus (maxim 5 cuvinte). "
        "Răspunde doar cu tema, fără explicații."
    )

    print("Generating theme with LM Studio...")
    try:
        response = client.chat.completions.create(
            model="default",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            max_tokens=50,
        )
        theme = response.choices[0].message.content.strip().strip('"').strip("'")
    except Exception as e:
        print(f"Warning: LM Studio error: {e}")
        theme = "Rebus Românesc"

    print(f"Theme: {theme}")
    puzzle.title = theme

    md = write_with_definitions(puzzle)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"Saved themed puzzle to {output_file}")
