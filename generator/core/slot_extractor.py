"""Extract word slots from a grid template and compute intersections."""

from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Intersection:
    other_slot_id: int
    this_position: int   # index within this slot
    other_position: int  # index within the other slot


@dataclass
class Slot:
    id: int
    direction: str  # 'H' or 'V'
    start_row: int
    start_col: int
    length: int
    cells: list[tuple[int, int]]
    intersections: list[Intersection] = field(default_factory=list)


def extract_slots(grid: list[list[bool]]) -> list[Slot]:
    """Extract all word slots (horizontal and vertical) from a grid template.

    A slot is a contiguous run of letter cells (True) with length >= 2.
    """
    rows = len(grid)
    cols = len(grid[0]) if grid else 0
    slots: list[Slot] = []
    slot_id = 0

    # cell_to_slots maps (row, col) -> list of (slot_id, position_in_slot)
    cell_to_slots: dict[tuple[int, int], list[tuple[int, int]]] = {}

    # Horizontal slots
    for r in range(rows):
        run_start = None
        run_cells: list[tuple[int, int]] = []
        for c in range(cols + 1):
            if c < cols and grid[r][c]:
                if run_start is None:
                    run_start = c
                    run_cells = []
                run_cells.append((r, c))
            else:
                if run_start is not None and len(run_cells) >= 2:
                    slot = Slot(
                        id=slot_id,
                        direction="H",
                        start_row=r,
                        start_col=run_start,
                        length=len(run_cells),
                        cells=list(run_cells),
                    )
                    for pos, cell in enumerate(run_cells):
                        cell_to_slots.setdefault(cell, []).append((slot_id, pos))
                    slots.append(slot)
                    slot_id += 1
                run_start = None
                run_cells = []

    # Vertical slots
    for c in range(cols):
        run_start = None
        run_cells: list[tuple[int, int]] = []
        for r in range(rows + 1):
            if r < rows and grid[r][c]:
                if run_start is None:
                    run_start = r
                    run_cells = []
                run_cells.append((r, c))
            else:
                if run_start is not None and len(run_cells) >= 2:
                    slot = Slot(
                        id=slot_id,
                        direction="V",
                        start_row=run_start,
                        start_col=c,
                        length=len(run_cells),
                        cells=list(run_cells),
                    )
                    for pos, cell in enumerate(run_cells):
                        cell_to_slots.setdefault(cell, []).append((slot_id, pos))
                    slots.append(slot)
                    slot_id += 1
                run_start = None
                run_cells = []

    # Compute intersections
    for cell, slot_refs in cell_to_slots.items():
        if len(slot_refs) >= 2:
            for i in range(len(slot_refs)):
                for j in range(i + 1, len(slot_refs)):
                    sid_a, pos_a = slot_refs[i]
                    sid_b, pos_b = slot_refs[j]
                    slots[sid_a].intersections.append(
                        Intersection(other_slot_id=sid_b, this_position=pos_a, other_position=pos_b)
                    )
                    slots[sid_b].intersections.append(
                        Intersection(other_slot_id=sid_a, this_position=pos_b, other_position=pos_a)
                    )

    return slots
