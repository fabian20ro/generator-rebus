from __future__ import annotations

import re

GRID_SIZE = 10


def parse_solution_grid(text: str) -> tuple[list[list[str]], list[str]]:
    warnings: list[str] = []
    tokens = re.findall(r"[A-Za-zĂÂÎȘȚăâîșț]|-", text)
    grid = [["?" for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
    row = 0
    col = 0
    for token in tokens:
        if row >= GRID_SIZE:
            warnings.append("Input longer than 10x10. Tail ignored.")
            break
        if token == "-":
            if col == GRID_SIZE:
                row += 1
                col = 0
                continue
            grid[row][col] = "#"
            col += 1
        else:
            if col == GRID_SIZE:
                row += 1
                col = 0
                if row >= GRID_SIZE:
                    warnings.append("Input longer than 10x10. Tail ignored.")
                    break
            grid[row][col] = token.upper()
            col += 1
    return grid, warnings
