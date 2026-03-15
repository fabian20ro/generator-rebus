"""Grid templates for Romanian rebus puzzles.

Templates define the placement of black squares (#) and letter cells (.).
Constraints:
- On the same row or column, blacks must be >= 3 cells apart
- No single-letter word slots (minimum length 2)
- All letter cells form a single connected component
"""

from __future__ import annotations

import random
from collections import deque
from typing import Callable

from .size_tuning import get_size_settings


def validate_template(grid: list[list[bool]]) -> tuple[bool, str]:
    """Validate a grid template meets all constraints."""
    rows = len(grid)
    cols = len(grid[0]) if grid else 0

    # Check same-row spacing: blacks must be >= 3 cells apart
    for r in range(rows):
        black_cols = [c for c in range(cols) if not grid[r][c]]
        for i in range(len(black_cols) - 1):
            if black_cols[i + 1] - black_cols[i] < 3:
                return False, f"Blacks too close on row {r}: cols {black_cols[i]},{black_cols[i+1]}"

    # Check same-column spacing: blacks must be >= 3 cells apart
    for c in range(cols):
        black_rows = [r for r in range(rows) if not grid[r][c]]
        for i in range(len(black_rows) - 1):
            if black_rows[i + 1] - black_rows[i] < 3:
                return False, f"Blacks too close on col {c}: rows {black_rows[i]},{black_rows[i+1]}"

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
    if not _is_connected(grid):
        return False, "Not connected"

    return True, "OK"


def _is_connected(grid: list[list[bool]]) -> bool:
    """Check that all letter cells form a single connected component."""
    rows = len(grid)
    cols = len(grid[0]) if grid else 0

    start = None
    letter_count = 0
    for r in range(rows):
        for c in range(cols):
            if grid[r][c]:
                letter_count += 1
                if start is None:
                    start = (r, c)

    if start is None:
        return False

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

    return len(visited) == letter_count


def _black_spacing_ok(grid: list[list[bool]], r: int, c: int, size: int) -> bool:
    """Fast per-cell check: no black within distance 1 or 2 on same row or column."""
    for d in (-2, -1, 1, 2):
        if 0 <= c + d < size and not grid[r][c + d]:
            return False
        if 0 <= r + d < size and not grid[r + d][c]:
            return False
    return True


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


def _log_template(grid: list[list[bool]]) -> None:
    """Print a grid as #/. rows for debugging."""
    for row in grid:
        print("  " + " ".join("#" if not cell else "." for cell in row))


def generate_incremental_template(
    size: int,
    solver_fn: Callable[[list[list[bool]]], bool],
    max_blacks: int | None = None,
    min_solver_step: int | None = None,
    rng: random.Random | None = None,
) -> list[list[bool]] | None:
    """Build a grid incrementally: start empty, add one black square at a time until solvable.

    solver_fn(grid) should return True if the grid can be filled with words.
    min_solver_step: skip solver calls before this step (early steps with too few blacks
    produce unsolvable grids with full-width slots, wasting solver time).
    """
    if rng is None:
        rng = random.Random()

    grid = [[True] * size for _ in range(size)]
    effective_max = max_blacks if max_blacks is not None else 3 * size

    print(f"  Incremental template {size}x{size} (max {effective_max} blacks):")

    if (min_solver_step is None or 0 >= min_solver_step) and solver_fn(grid):
        _log_template(grid)
        return grid

    all_cells = [(r, c) for r in range(size) for c in range(size)]

    for step in range(1, effective_max + 1):
        # Lazy candidate evaluation: shuffle all cells, pick first valid one.
        # Mathematically equivalent to building full list + shuffle + pick [0],
        # but avoids expensive _is_connected BFS on cells we never use.
        rng.shuffle(all_cells)
        placed = False
        for r, c in all_cells:
            if not grid[r][c]:
                continue
            if not _black_spacing_ok(grid, r, c, size):
                continue
            grid[r][c] = False
            if _creates_single_letter(grid, r, c, size):
                grid[r][c] = True
                continue
            if not _is_connected(grid):
                grid[r][c] = True
                continue
            # Valid placement found — keep it
            placed = True
            break

        if not placed:
            print(f"  No valid placements at step {step}")
            return None

        if min_solver_step is None or step >= min_solver_step:
            if solver_fn(grid):
                print(f"  Incremental template done after {step} blacks")
                _log_template(grid)
                return grid

    return None


def generate_procedural_template(size: int, target_blacks: int | None = None,
                                  max_attempts: int | None = None,
                                  rng: random.Random | None = None) -> list[list[bool]] | None:
    """Generate a valid template procedurally by randomly placing black squares.

    Uses rejection sampling: place blacks one at a time, validate after each placement.
    """
    settings = get_size_settings(size)
    if target_blacks is None:
        target_blacks = settings.target_blacks
    if max_attempts is None:
        max_attempts = settings.template_attempts
    if rng is None:
        rng = random.Random()

    for _ in range(max_attempts):
        grid = [[True] * size for _ in range(size)]
        blacks_placed = 0
        cells = [(r, c) for r in range(size) for c in range(size)]
        rng.shuffle(cells)

        for r, c in cells:
            if blacks_placed >= target_blacks:
                break

            grid[r][c] = False

            if not _black_spacing_ok(grid, r, c, size):
                grid[r][c] = True
                continue

            if _creates_single_letter(grid, r, c, size):
                grid[r][c] = True
                continue

            blacks_placed += 1

        valid, _ = validate_template(grid)
        if valid:
            return grid

    return None
