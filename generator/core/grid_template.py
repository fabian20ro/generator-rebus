"""Grid templates for Romanian rebus puzzles.

Templates define the placement of black squares (#) and letter cells (.).
Constraints:
- No two black squares are horizontally or vertically adjacent
- No single-letter word slots (minimum length 2)
- All letter cells form a single connected component
"""

from __future__ import annotations
import random
from collections import deque

# Templates: '#' = black square, '.' = letter cell
# Each template is a list of strings (rows)

TEMPLATES_7x7 = [
    [
        ". . . # . . .",
        ". . . . . . .",
        ". . . # . . .",
        "# . . . . . #",
        ". . . # . . .",
        ". . . . . . .",
        ". . . # . . .",
    ],
    [
        ". . . . . . .",
        ". . # . # . .",
        ". . . . . . .",
        ". # . . . # .",
        ". . . . . . .",
        ". . # . # . .",
        ". . . . . . .",
    ],
    [
        ". . . . # . .",
        ". . . . . . .",
        ". # . . . . .",
        ". . . # . . .",
        ". . . . . # .",
        ". . . . . . .",
        ". . # . . . .",
    ],
    [
        ". . . . . . .",
        "# . . # . . #",
        ". . . . . . .",
        ". . # . # . .",
        ". . . . . . .",
        "# . . # . . #",
        ". . . . . . .",
    ],
    [
        ". . # . . . .",
        ". . . . # . .",
        "# . . . . . .",
        ". . . . . . .",
        ". . . . . . #",
        ". . # . . . .",
        ". . . . # . .",
    ],
]

TEMPLATES_10x10 = [
    [
        ". . . . # . . . . .",
        ". . . . . . . . . .",
        ". . # . . . # . . .",
        ". . . . # . . . . .",
        "# . . . . . . . . #",
        "# . . . . . . . . #",
        ". . . . # . . . . .",
        ". . # . . . # . . .",
        ". . . . . . . . . .",
        ". . . . # . . . . .",
    ],
    [
        ". . . . . # . . . .",
        ". . # . . . . # . .",
        ". . . . # . . . . .",
        "# . . . . . # . . .",
        ". . . # . . . . . .",
        ". . . . . # . . . .",
        ". . . # . . . . . #",
        ". . . . # . . . . .",
        ". . # . . . . # . .",
        ". . . . # . . . . .",
    ],
    [
        ". . . # . . . # . .",
        ". . . . . . . . . .",
        ". . . . . . . . . .",
        "# . . . # . . . . #",
        ". . . . . . # . . .",
        ". . . # . . . . . .",
        "# . . . . # . . . #",
        ". . . . . . . . . .",
        ". . . . . . . . . .",
        ". . # . . . # . . .",
    ],
    [
        ". . . . . . . . . .",
        ". . . # . . . # . .",
        ". # . . . # . . . .",
        ". . . . . . . . . #",
        ". . . # . . # . . .",
        ". . # . . # . . . .",
        "# . . . . . . . . .",
        ". . . # . . . # . .",
        ". . # . . . # . . .",
        ". . . . . . . . . .",
    ],
    [
        ". . . . # . . . . #",
        ". . . . . . # . . .",
        ". # . . . . . . . .",
        ". . . # . . . . . .",
        ". . . . . # . . . .",
        ". . . . # . . . . .",
        ". . . . . # . . . .",
        ". . . . . . . . # .",
        ". . . # . . . . . .",
        "# . . . . # . . . .",
    ],
]

TEMPLATES_15x15 = [
    [
        ". . . . # . . . . . # . . . .",
        ". . . . . . . . . . . . . . .",
        ". . # . . . . # . . . . # . .",
        ". . . . # . . . . # . . . . .",
        "# . . . . . # . . . . . . . #",
        ". . . . . . . . . . . . . . .",
        ". . . # . . . . . # . . . . .",
        ". . . . . # . . . . . # . . .",
        ". . . . # . . . . . # . . . .",
        ". . . . . . . . . . . . . . .",
        "# . . . . . . # . . . . . . #",
        ". . . . # . . . . # . . . . .",
        ". . # . . . . # . . . . # . .",
        ". . . . . . . . . . . . . . .",
        ". . . . # . . . . . # . . . .",
    ],
    [
        ". . . . . # . . . # . . . . .",
        ". . . . . . . . . . . . . . .",
        ". . # . . . . # . . . # . . .",
        "# . . . . # . . . # . . . . #",
        ". . . . . . . . . . . . . . .",
        ". . . # . . . . . . . # . . .",
        ". . . . . . # . . . . . . . .",
        ". # . . . . . . . . . . . # .",
        ". . . . . . . # . . . . . . .",
        ". . . # . . . . . . . # . . .",
        ". . . . . . . . . . . . . . .",
        "# . . . . # . . . # . . . . #",
        ". . # . . . . # . . . # . . .",
        ". . . . . . . . . . . . . . .",
        ". . . . . # . . . # . . . . .",
    ],
]

ALL_TEMPLATES: dict[int, list[list[str]]] = {
    7: TEMPLATES_7x7,
    10: TEMPLATES_10x10,
    15: TEMPLATES_15x15,
}


def parse_template(template: list[str]) -> list[list[bool]]:
    """Parse a template into a 2D boolean grid. True = letter cell, False = black."""
    grid = []
    for row_str in template:
        row = []
        for ch in row_str.split():
            row.append(ch == ".")
        grid.append(row)
    return grid


def validate_template(grid: list[list[bool]]) -> tuple[bool, str]:
    """Validate a grid template meets all constraints."""
    rows = len(grid)
    cols = len(grid[0]) if grid else 0

    # Check no adjacent black squares
    for r in range(rows):
        for c in range(cols):
            if not grid[r][c]:  # black square
                if r + 1 < rows and not grid[r + 1][c]:
                    return False, f"Adjacent black squares at ({r},{c}) and ({r+1},{c})"
                if c + 1 < cols and not grid[r][c + 1]:
                    return False, f"Adjacent black squares at ({r},{c}) and ({r},{c+1})"

    # Check no single-letter slots
    # Horizontal
    for r in range(rows):
        run = 0
        for c in range(cols + 1):
            if c < cols and grid[r][c]:
                run += 1
            else:
                if run == 1:
                    return False, f"Single-letter horizontal slot at row {r}"
                run = 0
    # Vertical
    for c in range(cols):
        run = 0
        for r in range(rows + 1):
            if r < rows and grid[r][c]:
                run += 1
            else:
                if run == 1:
                    return False, f"Single-letter vertical slot at col {c}"
                run = 0

    # Check connectivity
    start = None
    letter_count = 0
    for r in range(rows):
        for c in range(cols):
            if grid[r][c]:
                letter_count += 1
                if start is None:
                    start = (r, c)

    if start is None:
        return False, "No letter cells"

    visited = set()
    queue = deque([start])
    visited.add(start)
    while queue:
        r, c = queue.popleft()
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] and (nr, nc) not in visited:
                visited.add((nr, nc))
                queue.append((nr, nc))

    if len(visited) != letter_count:
        return False, f"Not connected: {len(visited)}/{letter_count} cells reachable"

    return True, "OK"


def generate_procedural_template(size: int, target_blacks: int | None = None,
                                  max_attempts: int = 1000) -> list[list[bool]] | None:
    """Generate a valid template procedurally by randomly placing black squares.

    Uses rejection sampling: place blacks one at a time, validate after each placement.
    """
    if target_blacks is None:
        # Reasonable defaults: ~12-18% black squares
        target_blacks = {7: 6, 10: 12, 15: 25}.get(size, size)

    for _ in range(max_attempts):
        grid = [[True] * size for _ in range(size)]
        blacks_placed = 0
        cells = [(r, c) for r in range(size) for c in range(size)]
        random.shuffle(cells)

        for r, c in cells:
            if blacks_placed >= target_blacks:
                break

            # Try placing a black square
            grid[r][c] = False

            # Quick check: no adjacent black squares
            ok = True
            for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                nr, nc = r + dr, c + dc
                if 0 <= nr < size and 0 <= nc < size and not grid[nr][nc]:
                    ok = False
                    break

            if not ok:
                grid[r][c] = True
                continue

            # Quick check: no single-letter runs created in this row/col
            if _creates_single_letter(grid, r, c, size):
                grid[r][c] = True
                continue

            blacks_placed += 1

        # Final validation
        valid, _ = validate_template(grid)
        if valid:
            return grid

    return None


def _creates_single_letter(grid: list[list[bool]], br: int, bc: int, size: int) -> bool:
    """Check if placing a black at (br, bc) creates any single-letter runs."""
    # Check horizontal runs in row br
    run = 0
    for c in range(size + 1):
        if c < size and grid[br][c]:
            run += 1
        else:
            if run == 1:
                return True
            run = 0

    # Check vertical runs in column bc
    run = 0
    for r in range(size + 1):
        if r < size and grid[r][bc]:
            run += 1
        else:
            if run == 1:
                return True
            run = 0

    # Also check adjacent rows/cols that might be affected
    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        nr, nc = br + dr, bc + dc
        if 0 <= nr < size and 0 <= nc < size and grid[nr][nc]:
            # Check the run containing (nr, nc)
            if dc == 0:  # Check horizontal run of row nr
                run = 0
                for c2 in range(size + 1):
                    if c2 < size and grid[nr][c2]:
                        run += 1
                    else:
                        if run == 1:
                            return True
                        run = 0
            if dr == 0:  # Check vertical run of col nc
                run = 0
                for r2 in range(size + 1):
                    if r2 < size and grid[r2][nc]:
                        run += 1
                    else:
                        if run == 1:
                            return True
                        run = 0

    return False


def get_random_template(size: int) -> list[list[bool]]:
    """Get a random validated template for the given grid size.

    Tries hardcoded templates first, then falls back to procedural generation.
    """
    # Try hardcoded templates
    templates = ALL_TEMPLATES.get(size, [])
    if templates:
        candidates = list(templates)
        random.shuffle(candidates)
        for template_strs in candidates:
            grid = parse_template(template_strs)
            valid, msg = validate_template(grid)
            if valid:
                return grid

    # Fall back to procedural generation
    grid = generate_procedural_template(size)
    if grid is not None:
        return grid

    raise RuntimeError(f"Could not generate a valid template for size {size}")
