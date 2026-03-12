"""Phase 7: Upload a verified puzzle to Supabase."""

from __future__ import annotations
import json
import sys
from supabase import create_client
from ..config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
from ..core.markdown_io import parse_markdown, ClueEntry


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


def _extract_individual_clues(clues: list[ClueEntry], direction: str,
                              grid: list[list[str]]) -> list[dict]:
    """Extract individual clue records for database insertion."""
    size = len(grid)
    records = []
    clue_number = 1

    for clue in clues:
        if not clue.definition:
            continue

        records.append({
            "direction": direction,
            "start_row": clue.start_row,
            "start_col": clue.start_col,
            "length": len(clue.word_normalized),
            "word_normalized": clue.word_normalized,
            "word_original": clue.word_original or clue.word_normalized.lower(),
            "clue_number": clue_number,
            "definition": clue.definition,
        })
        clue_number += 1

    return records


def _find_word_positions(grid: list[list[str]], direction: str) -> list[tuple[int, int, str]]:
    """Find all word start positions and the words in the grid."""
    size = len(grid)
    words = []

    if direction == "H":
        for r in range(size):
            word_start = None
            current_word = ""
            for c in range(size + 1):
                if c < size and grid[r][c] != "#":
                    if word_start is None:
                        word_start = c
                    current_word += grid[r][c]
                else:
                    if word_start is not None and len(current_word) >= 2:
                        words.append((r, word_start, current_word))
                    word_start = None
                    current_word = ""
    else:  # V
        for c in range(size):
            word_start = None
            current_word = ""
            for r in range(size + 1):
                if r < size and grid[r][c] != "#":
                    if word_start is None:
                        word_start = r
                    current_word += grid[r][c]
                else:
                    if word_start is not None and len(current_word) >= 2:
                        words.append((word_start, c, current_word))
                    word_start = None
                    current_word = ""

    return words


def upload_puzzle(puzzle, force: bool = False) -> str:
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
    h_positions = _find_word_positions(puzzle.grid, "H")
    v_positions = _find_word_positions(puzzle.grid, "V")

    # Build clue records with positions
    clue_records = []
    clue_number = 1

    # Match horizontal clues to grid positions
    h_pos_idx = 0
    for clue in puzzle.horizontal_clues:
        if h_pos_idx < len(h_positions):
            r, c, word = h_positions[h_pos_idx]
            if word == clue.word_normalized:
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
                h_pos_idx += 1

    # Match vertical clues to grid positions
    v_pos_idx = 0
    v_clue_number = 1
    for clue in puzzle.vertical_clues:
        if v_pos_idx < len(v_positions):
            r, c, word = v_positions[v_pos_idx]
            if word == clue.word_normalized:
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
                v_pos_idx += 1

    print(f"Uploading puzzle: {puzzle.title or 'Untitled'}")
    print(f"  Grid: {puzzle.size}x{puzzle.size}")
    print(f"  Clues: {len(clue_records)}")

    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    # Insert puzzle
    puzzle_data = {
        "title": puzzle.title or "Rebus",
        "theme": puzzle.title or "",
        "grid_size": puzzle.size,
        "grid_template": grid_template_json,
        "grid_solution": grid_solution_json,
        "difficulty": 3,
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
