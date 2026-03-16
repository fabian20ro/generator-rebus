"""Phase 7: Upload a verified puzzle to Supabase."""

from __future__ import annotations
import json
import sys
from supabase import create_client
from ..config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
from ..core.markdown_io import parse_markdown
from ..core.slot_extractor import Slot, extract_slots


def _grid_to_json(grid: list[list[str]]) -> tuple[str, str]:
    """Convert grid to template (bool[][]) and solution (string[][]) JSON."""
    template = []
    solution = []
    for row in grid:
        t_row = []
        s_row = []
        for cell in row:
            if cell == "#":
                t_row.append(False)
                s_row.append(None)
            else:
                t_row.append(True)
                s_row.append(cell)
        template.append(t_row)
        solution.append(s_row)
    return json.dumps(template), json.dumps(solution)


def _clean_definition(definition: str) -> str:
    return definition.split("→", 1)[0].strip()


def _slots_with_words(grid: list[list[str]]) -> list[tuple[Slot, str]]:
    """Extract slots from the grid and read the word at each slot position."""
    template = [[cell != "#" for cell in row] for row in grid]
    slots = extract_slots(template)
    return [(slot, "".join(grid[r][c] for r, c in slot.cells)) for slot in slots]


def upload_puzzle(puzzle, force: bool = False, *, difficulty: int = 3, description: str = "") -> str:
    """Upload a parsed puzzle object and return the puzzle ID."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
        sys.exit(1)

    if not puzzle.grid:
        print("Error: no grid found in puzzle")
        sys.exit(1)

    # Check all definitions are verified
    all_clues = puzzle.horizontal_clues + puzzle.vertical_clues
    if not force:
        unverified = [c for c in all_clues if c.verified is False]
        if unverified:
            print(f"Error: {len(unverified)} definitions failed verification.")
            print("Fix them and re-verify, or use --force to upload anyway.")
            for c in unverified:
                print(f"  ✗ {c.word_normalized}: {c.verify_note}")
            sys.exit(1)

    # Build grid JSON
    grid_template_json, grid_solution_json = _grid_to_json(puzzle.grid)

    # Find word positions in the grid for clue records
    slots_with_words = _slots_with_words(puzzle.grid)
    h_positions = [(s.start_row, s.start_col, word)
                   for s, word in slots_with_words if s.direction == "H"]
    v_positions = [(s.start_row, s.start_col, word)
                   for s, word in slots_with_words if s.direction == "V"]

    # Build coordinate lookup for matching clues to grid positions
    h_slot_by_word: dict[str, list[tuple[int, int, str]]] = {}
    for r, c, word in h_positions:
        h_slot_by_word.setdefault(word, []).append((r, c, word))
    v_slot_by_word: dict[str, list[tuple[int, int, str]]] = {}
    for r, c, word in v_positions:
        v_slot_by_word.setdefault(word, []).append((r, c, word))

    # Match clues to grid positions by word, consuming from the list
    clue_records = []
    clue_number = 1

    for clue in puzzle.horizontal_clues:
        positions = h_slot_by_word.get(clue.word_normalized, [])
        if positions:
            r, c, word = positions.pop(0)
            clue_records.append({
                "direction": "H",
                "start_row": r,
                "start_col": c,
                "length": len(word),
                "word_normalized": clue.word_normalized,
                "word_original": clue.word_original or clue.word_normalized.lower(),
                "clue_number": clue_number,
                "definition": _clean_definition(clue.definition or ""),
            })
            clue_number += 1

    v_clue_number = 1
    for clue in puzzle.vertical_clues:
        positions = v_slot_by_word.get(clue.word_normalized, [])
        if positions:
            r, c, word = positions.pop(0)
            clue_records.append({
                "direction": "V",
                "start_row": r,
                "start_col": c,
                "length": len(word),
                "word_normalized": clue.word_normalized,
                "word_original": clue.word_original or clue.word_normalized.lower(),
                "clue_number": v_clue_number,
                "definition": _clean_definition(clue.definition or ""),
            })
            v_clue_number += 1

    print(f"Uploading puzzle: {puzzle.title or 'Untitled'}")
    print(f"  Grid: {puzzle.size}x{puzzle.size}")
    print(f"  Clues: {len(clue_records)}")

    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    # Insert puzzle
    puzzle_data = {
        "title": puzzle.title or "Rebus",
        "theme": description or puzzle.title or "",
        "grid_size": puzzle.size,
        "grid_template": grid_template_json,
        "grid_solution": grid_solution_json,
        "difficulty": difficulty,
        "published": False,
    }

    result = client.table("crossword_puzzles").insert(puzzle_data).execute()
    puzzle_id = result.data[0]["id"]
    print(f"  Puzzle ID: {puzzle_id}")

    # Insert clues
    if clue_records:
        for record in clue_records:
            record["puzzle_id"] = puzzle_id
        client.table("crossword_clues").insert(clue_records).execute()

    print(f"Uploaded! Puzzle ID: {puzzle_id}")
    print(f"Run 'python rebus.py activate {puzzle_id}' to publish it.")
    return puzzle_id


def run(input_file: str, output_file: str, **kwargs) -> None:
    """Upload a puzzle to Supabase."""
    force = kwargs.get("force", False)

    print(f"Reading puzzle from {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        puzzle = parse_markdown(f.read())

    upload_puzzle(puzzle, force=force)
