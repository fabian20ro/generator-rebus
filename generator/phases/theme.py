"""Phase 4: Find a theme for the filled grid using LM Studio."""

from __future__ import annotations

import sys

from ..core.ai_clues import create_client
from ..core.markdown_io import parse_markdown, write_with_definitions


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


THEME_SYSTEM_PROMPT = (
    "Ești editor de titluri pentru rebusuri românești. "
    "Primești o listă de cuvinte deja fixate într-o grilă. "
    "Scrii un titlu scurt, natural și memorabil, 2-4 cuvinte, fără ghilimele, fără explicații, fără punct. "
    "Dacă există o temă clară, folosești tema. "
    "Dacă lista este eterogenă, inventezi un titlu neutru și elegant inspirat de registrul lexical. "
    "Nu folosești cuvintele Rebus, Românesc, Puzzle, Titlu."
)

FALLBACK_TITLES = [
    "Fir de Cuvinte",
    "Sensuri Comune",
    "Litere și Legături",
    "Noduri de Sens",
    "Semne Încrucișate",
    "Trasee de Litere",
    "Puncte Comune",
    "Umbra Cuvintelor",
]


def _fallback_title(words: list[str]) -> str:
    if not words:
        return FALLBACK_TITLES[0]
    seed = sum(sum(ord(ch) for ch in word) for word in words)
    return FALLBACK_TITLES[seed % len(FALLBACK_TITLES)]


def _sanitize_title(title: str, words: list[str]) -> str:
    cleaned = " ".join(title.strip().strip('"').strip("'").split())
    if not cleaned:
        return _fallback_title(words)

    blocked = {"rebus", "romanesc", "românesc", "puzzle", "titlu"}
    lowered = cleaned.lower()
    if any(token in lowered for token in blocked):
        return _fallback_title(words)

    parts = cleaned.split()
    if len(parts) > 4:
        cleaned = " ".join(parts[:4])
    return cleaned


def generate_title_from_words(words: list[str], client=None) -> str:
    if not words:
        return _fallback_title(words)

    prompt = (
        "Lista de cuvinte este:\n"
        f"{', '.join(words)}\n\n"
        "Dă un titlu scurt pentru rebus."
    )

    if client is None:
        client = create_client()

    try:
        response = client.chat.completions.create(
            model="default",
            messages=[
                {"role": "system", "content": THEME_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=50,
        )
        raw_title = response.choices[0].message.content or ""
    except Exception:
        raw_title = ""

    return _sanitize_title(raw_title, words)


def generate_title_for_puzzle(puzzle, client=None) -> str:
    return generate_title_from_words(_collect_words(puzzle), client=client)


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

    print("Generating title with LM Studio...")
    theme = generate_title_from_words(words)

    print(f"Theme: {theme}")
    puzzle.title = theme

    md = write_with_definitions(puzzle)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"Saved themed puzzle to {output_file}")
