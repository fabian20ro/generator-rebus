"""CSP backtracking solver for crossword grid filling.

Uses MRV (Minimum Remaining Values) heuristic and forward checking
to efficiently fill a grid template with words from the dictionary.
"""

from __future__ import annotations
import random
from .word_index import WordIndex, WordEntry
from .slot_extractor import Slot


def get_pattern(slot: Slot, grid: list[list[str | None]]) -> list[str | None]:
    """Get the current pattern for a slot from the grid state."""
    return [grid[r][c] for r, c in slot.cells]


def assign(slot: Slot, word: WordEntry, grid: list[list[str | None]],
           assignment: dict[int, WordEntry], used_words: set[str]) -> None:
    """Assign a word to a slot, updating grid and tracking structures."""
    assignment[slot.id] = word
    used_words.add(word.normalized)
    for i, (r, c) in enumerate(slot.cells):
        grid[r][c] = word.normalized[i]


def unassign(slot: Slot, word: WordEntry, grid: list[list[str | None]],
             assignment: dict[int, WordEntry], used_words: set[str],
             slots: list[Slot]) -> None:
    """Remove a word assignment, restoring the grid state."""
    del assignment[slot.id]
    used_words.discard(word.normalized)
    for i, (r, c) in enumerate(slot.cells):
        # Only clear if no other assigned slot uses this cell
        other_uses = False
        for ix in slot.intersections:
            if ix.other_slot_id in assignment:
                other_slot = slots[ix.other_slot_id]
                if (r, c) in other_slot.cells:
                    other_uses = True
                    break
        if not other_uses:
            grid[r][c] = None


def forward_check(slot: Slot, slots: list[Slot], word_index: WordIndex,
                  grid: list[list[str | None]], assignment: dict[int, WordEntry],
                  used_words: set[str]) -> bool:
    """Check that all unassigned intersecting slots still have valid candidates."""
    for ix in slot.intersections:
        if ix.other_slot_id in assignment:
            continue
        other_slot = slots[ix.other_slot_id]
        pattern = get_pattern(other_slot, grid)
        count = word_index.count_matching(pattern)
        # Subtract already-used words (approximate: count may include used words)
        if count == 0:
            return False
    return True


def select_mrv(slots: list[Slot], assignment: dict[int, WordEntry],
               word_index: WordIndex, grid: list[list[str | None]],
               used_words: set[str]) -> Slot | None:
    """Select the unassigned slot with the fewest matching candidates (MRV)."""
    best_slot = None
    best_count = float("inf")

    for slot in slots:
        if slot.id in assignment:
            continue
        pattern = get_pattern(slot, grid)
        count = word_index.count_matching(pattern)
        # Prefer slots with fewer candidates (fail-first)
        # Break ties by preferring longer slots (harder to fill later)
        if count < best_count or (count == best_count and best_slot is not None
                                   and slot.length > best_slot.length):
            best_count = count
            best_slot = slot

    return best_slot


def solve(slots: list[Slot], word_index: WordIndex,
          assignment: dict[int, WordEntry], used_words: set[str],
          grid: list[list[str | None]], max_backtracks: int = 50000,
          _counter: list[int] | None = None) -> dict[int, WordEntry] | None:
    """Solve the crossword using CSP backtracking with MRV and forward checking.

    Returns the assignment dict if successful, None if no solution found.
    """
    if _counter is None:
        _counter = [0]

    if len(assignment) == len(slots):
        return assignment

    slot = select_mrv(slots, assignment, word_index, grid, used_words)
    if slot is None:
        return assignment  # all assigned

    pattern = get_pattern(slot, grid)
    candidates = [w for w in word_index.find_matching(pattern)
                  if w.normalized not in used_words]
    random.shuffle(candidates)

    for word in candidates:
        _counter[0] += 1
        if _counter[0] > max_backtracks:
            return None

        assign(slot, word, grid, assignment, used_words)

        if forward_check(slot, slots, word_index, grid, assignment, used_words):
            result = solve(slots, word_index, assignment, used_words, grid,
                          max_backtracks, _counter)
            if result is not None:
                return result

        unassign(slot, word, grid, assignment, used_words, slots)

    return None
