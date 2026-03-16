"""Phase 5: Generate definitions for each word using LM Studio."""

from __future__ import annotations
from openai import OpenAI
from ..core.markdown_io import parse_markdown, write_with_definitions, ClueEntry
from ..core.ai_clues import create_client, generate_definition
from ..core.dex_cache import lookup_batch
from ..core.pipeline_state import (
    WorkingClue,
    WorkingPuzzle,
    puzzle_from_working_state,
    set_current_definition,
    working_puzzle_from_puzzle,
)


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


def generate_definitions_for_state(
    state: WorkingPuzzle, client: OpenAI, dex_cache: dict[str, str] | None = None,
) -> None:
    theme = state.title or "Rebus Românesc"
    print(f"Theme: {theme}")
    if dex_cache is None:
        dex_cache = {}

    for label, clues in (("horizontal", state.horizontal_clues), ("vertical", state.vertical_clues)):
        print(f"Generating {label} definitions...")
        for clue in clues:
            if clue.current.definition:
                continue
            dex_defs = dex_cache.get(clue.word_normalized, "")
            if dex_defs:
                print(f"  Defining: {clue.word_normalized} ({clue.word_original or '?'}) [DEX context available]")
            else:
                print(f"  Defining: {clue.word_normalized} ({clue.word_original or '?'})...")
            try:
                definition = generate_definition(
                    client, clue.word_normalized, clue.word_original, theme,
                    word_type=clue.word_type, dex_definitions=dex_defs,
                )
            except Exception as e:
                definition = f"[Definiție lipsă: {e}]"
            print(f"    → {definition}")
            set_current_definition(clue, definition, round_index=0, source="generate")
            if clue.best is None:
                clue.best = clue.current


def _load_dex_cache_for_state(state: WorkingPuzzle) -> dict[str, str]:
    """Batch-load dex definitions for all words in the puzzle."""
    from ..core.pipeline_state import all_working_clues as _all_clues
    try:
        from supabase import create_client as _create_sb
        from ..config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            return {}
        sb = _create_sb(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    except Exception:
        return {}
    words = [clue.word_normalized for clue in _all_clues(state) if not clue.current.definition]
    if not words:
        return {}
    cache = lookup_batch(sb, words)
    if cache:
        print(f"  DEX cache: {len(cache)}/{len(words)} words have definitions")
    return cache


def generate_definitions_for_puzzle(
    puzzle, client: OpenAI, metadata: dict[str, dict] | None = None,
) -> None:
    """Expand clues and generate definitions in-place for the whole puzzle."""
    state = working_puzzle_from_puzzle(puzzle, split_compound=True)
    if metadata:
        from ..core.pipeline_state import all_working_clues as _all_clues
        for clue in _all_clues(state):
            word_meta = metadata.get(clue.word_normalized, {})
            clue.word_type = word_meta.get("word_type", "")
    dex_cache = _load_dex_cache_for_state(state)
    generate_definitions_for_state(state, client, dex_cache=dex_cache)
    rendered = puzzle_from_working_state(state)
    puzzle.horizontal_clues = rendered.horizontal_clues
    puzzle.vertical_clues = rendered.vertical_clues


def run(input_file: str, output_file: str, **kwargs) -> None:
    """Generate definitions for all words in the puzzle."""
    print(f"Reading puzzle from {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        puzzle = parse_markdown(f.read())

    client = create_client()
    state = working_puzzle_from_puzzle(puzzle, split_compound=True)
    generate_definitions_for_state(state, client)
    puzzle = puzzle_from_working_state(state)

    md = write_with_definitions(puzzle)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md)

    total = len(puzzle.horizontal_clues) + len(puzzle.vertical_clues)
    print(f"Generated {total} definitions. Saved to {output_file}")
