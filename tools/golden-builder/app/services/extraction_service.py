from __future__ import annotations

from app.parse.clue_parser import parse_clues
from app.parse.solution_parser import parse_solution_grid
from app.parse.slot_extractor import extract_words
from app.schemas import PairRow


def build_pairs(puzzle_title: str, clue_text: str, solution_text: str) -> tuple[list[PairRow], list[str]]:
    across_defs, down_defs = parse_clues(clue_text)
    grid, warnings = parse_solution_grid(solution_text)
    across_words, down_words = extract_words(grid)

    rows: list[PairRow] = []
    total = max(len(across_defs), len(across_words))
    for i in range(total):
        rows.append(
            PairRow(
                puzzle_title=puzzle_title,
                solution=across_words[i] if i < len(across_words) else "",
                definition=across_defs[i] if i < len(across_defs) else "",
            )
        )

    total_down = max(len(down_defs), len(down_words))
    for i in range(total_down):
        rows.append(
            PairRow(
                puzzle_title=puzzle_title,
                solution=down_words[i] if i < len(down_words) else "",
                definition=down_defs[i] if i < len(down_defs) else "",
            )
        )

    return rows, warnings
