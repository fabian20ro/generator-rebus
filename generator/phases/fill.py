"""Phase 3: Fill grid with words using CSP backtracking."""

from __future__ import annotations
import json
import sys
from ..core.markdown_io import parse_markdown, write_filled_grid
from ..core.word_index import WordIndex, WordEntry
from ..core.slot_extractor import extract_slots
from ..core.constraint_solver import solve


def run(input_file: str, output_file: str, **kwargs) -> None:
    """Fill the grid template with dictionary words."""
    words_file = kwargs.get("words")
    if not words_file:
        print("Error: --words <words.json> is required for fill phase")
        sys.exit(1)

    max_rarity = kwargs.get("max_rarity", 5)
    max_backtracks = kwargs.get("max_backtracks", 50000)

    # Read grid template
    print(f"Reading grid from {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        puzzle = parse_markdown(f.read())

    if not puzzle.grid:
        print("Error: no grid found in input file")
        sys.exit(1)

    size = len(puzzle.grid)
    print(f"Grid size: {size}x{size}")

    # Convert grid to bool template
    template: list[list[bool]] = []
    for row in puzzle.grid:
        template.append([cell != "#" for cell in row])

    # Load words
    print(f"Loading words from {words_file}...")
    with open(words_file, "r", encoding="utf-8") as f:
        raw_words = json.load(f)

    filtered_words = [
        w for w in raw_words
        if w.get("rarity_level") is None or w["rarity_level"] <= max_rarity
    ]
    print(f"Using {len(filtered_words)} words with rarity <= {max_rarity}")

    word_entries = [WordEntry(w["normalized"], w["original"]) for w in filtered_words]
    word_index = WordIndex(word_entries)
    print(f"Loaded {word_index.word_count()} words")

    # Extract slots
    slots = extract_slots(template)
    h_slots = [s for s in slots if s.direction == "H"]
    v_slots = [s for s in slots if s.direction == "V"]
    print(f"Slots: {len(h_slots)} horizontal, {len(v_slots)} vertical ({len(slots)} total)")

    # Check slot lengths have enough candidates
    for slot in slots:
        pattern = [None] * slot.length
        count = word_index.count_matching(pattern)
        if count < 5:
            print(f"  Warning: slot {slot.direction} at ({slot.start_row},{slot.start_col}) "
                  f"length {slot.length} has only {count} candidates")

    # Solve
    print(f"Solving (max backtracks: {max_backtracks})...")
    grid: list[list[str | None]] = [[None if template[r][c] else "#"
                                      for c in range(size)]
                                     for r in range(size)]

    assignment: dict[int, WordEntry] = {}
    used_words: set[str] = set()

    result = solve(slots, word_index, assignment, used_words, grid, max_backtracks)

    if result is None:
        print("Failed to find a solution. Try a different template or increase max_backtracks.")
        sys.exit(1)

    print(f"Solution found! Used {len(result)} words.")

    # Extract words per row and column
    h_words: list[list[str]] = [[] for _ in range(size)]
    h_originals: list[list[str]] = [[] for _ in range(size)]
    v_words: list[list[str]] = [[] for _ in range(size)]
    v_originals: list[list[str]] = [[] for _ in range(size)]

    for slot in slots:
        word = result[slot.id]
        if slot.direction == "H":
            h_words[slot.start_row].append(word.normalized)
            h_originals[slot.start_row].append(word.original)
        else:
            v_words[slot.start_col].append(word.normalized)
            v_originals[slot.start_col].append(word.original)

    # Convert grid for output
    grid_out: list[list[str | None]] = []
    for r in range(size):
        row = []
        for c in range(size):
            if not template[r][c]:
                row.append(None)
            else:
                row.append(grid[r][c])
        grid_out.append(row)

    md = write_filled_grid(size, grid_out, h_words, v_words, h_originals, v_originals)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"Saved filled grid to {output_file}")
