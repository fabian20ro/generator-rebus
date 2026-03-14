import unittest

from generator.core.word_index import WordEntry, WordIndex
from generator.core.slot_extractor import Slot, Intersection, extract_slots
from generator.core.constraint_solver import (
    forward_check,
    get_pattern,
    select_mrv,
    solve,
)


def _build_index(words: list[str]) -> WordIndex:
    return WordIndex([WordEntry(w, w) for w in words])


class WordIndexBitsetTests(unittest.TestCase):
    def test_find_matching_returns_same_as_before(self):
        idx = _build_index(["AB", "AC", "BC", "BA"])
        result = idx.find_matching([None, None])
        self.assertEqual(4, len(result))

    def test_find_matching_with_constraints(self):
        idx = _build_index(["AB", "AC", "BC", "BA"])
        result = idx.find_matching(["A", None])
        words = {w.normalized for w in result}
        self.assertEqual({"AB", "AC"}, words)

    def test_count_matching_consistent_with_find(self):
        idx = _build_index(["ABC", "ABD", "AXC", "XBC"])
        pattern = ["A", None, "C"]
        found = len(idx.find_matching(pattern))
        counted = idx.count_matching(pattern)
        self.assertEqual(found, counted)

    def test_has_matching_with_no_exclude(self):
        idx = _build_index(["AB", "AC", "BC"])
        self.assertTrue(idx.has_matching(["A", None]))
        self.assertFalse(idx.has_matching(["X", None]))

    def test_has_matching_with_exclude_mask(self):
        idx = _build_index(["AB", "AC", "BC"])
        # AB is at index 0, AC at index 1
        # Exclude both A* words
        ab_idx = idx.word_to_index("AB")
        ac_idx = idx.word_to_index("AC")
        exclude = (1 << ab_idx) | (1 << ac_idx)
        # "A" at pos 0 matches AB, AC — both excluded → False
        self.assertFalse(idx.has_matching(["A", None], exclude_mask=exclude))
        # No constraint → matches all, BC not excluded → True
        self.assertTrue(idx.has_matching([None, None], exclude_mask=exclude))

    def test_word_to_index_returns_correct_index(self):
        idx = _build_index(["ABC", "DEF", "GHI"])
        self.assertEqual(0, idx.word_to_index("ABC"))
        self.assertEqual(1, idx.word_to_index("DEF"))
        self.assertEqual(2, idx.word_to_index("GHI"))
        self.assertIsNone(idx.word_to_index("XYZ"))

    def test_count_matching_empty_pattern(self):
        idx = _build_index(["AB", "CD"])
        # All wildcards = all words of that length
        self.assertEqual(2, idx.count_matching([None, None]))

    def test_find_matching_no_results(self):
        idx = _build_index(["AB", "CD"])
        result = idx.find_matching(["X", None])
        self.assertEqual([], result)


class MRVDegreeTests(unittest.TestCase):
    def test_mrv_prefers_higher_degree_on_tie(self):
        """When two slots have the same candidate count, prefer higher degree."""
        idx = _build_index(["AB", "CD", "EF"])

        # Two slots, both length 2, same candidate count (3)
        slot_low_degree = Slot(
            id=0, direction="H", start_row=0, start_col=0,
            length=2, cells=[(0, 0), (0, 1)],
            intersections=[
                Intersection(other_slot_id=2, this_position=0, other_position=0),
            ],
        )
        slot_high_degree = Slot(
            id=1, direction="H", start_row=1, start_col=0,
            length=2, cells=[(1, 0), (1, 1)],
            intersections=[
                Intersection(other_slot_id=2, this_position=0, other_position=1),
                Intersection(other_slot_id=3, this_position=1, other_position=0),
            ],
        )
        # Dummy intersecting slots (assigned, so they don't affect degree)
        slot_v1 = Slot(id=2, direction="V", start_row=0, start_col=0,
                       length=2, cells=[(0, 0), (1, 0)], intersections=[])
        slot_v2 = Slot(id=3, direction="V", start_row=1, start_col=1,
                       length=2, cells=[(1, 1), (2, 1)], intersections=[])

        slots = [slot_low_degree, slot_high_degree, slot_v1, slot_v2]
        assignment = {}  # No slots assigned
        grid = [[None] * 2 for _ in range(3)]
        used_words: set[str] = set()

        selected = select_mrv(slots, assignment, idx, grid, used_words)
        # slot_high_degree has degree=2 vs slot_low_degree degree=1
        self.assertEqual(1, selected.id)


class ForwardCheckTests(unittest.TestCase):
    def test_forward_check_with_used_masks(self):
        """forward_check uses bitmask exclusion correctly."""
        idx = _build_index(["AB", "CD"])
        slot_h = Slot(
            id=0, direction="H", start_row=0, start_col=0,
            length=2, cells=[(0, 0), (0, 1)],
            intersections=[
                Intersection(other_slot_id=1, this_position=0, other_position=0),
            ],
        )
        slot_v = Slot(
            id=1, direction="V", start_row=0, start_col=0,
            length=2, cells=[(0, 0), (1, 0)],
            intersections=[
                Intersection(other_slot_id=0, this_position=0, other_position=0),
            ],
        )
        slots = [slot_h, slot_v]
        grid = [[None, None], [None, None]]
        assignment = {0: WordEntry("AB", "ab")}
        used_words = {"AB"}
        used_masks = {2: 1 << idx.word_to_index("AB")}

        grid[0][0] = "A"
        grid[0][1] = "B"

        # slot_v needs a word starting with "A" — only "AB" matches but it's used
        result = forward_check(
            slot_h, slots, idx, grid, assignment, used_words,
            used_masks=used_masks,
        )
        self.assertFalse(result)


class SolveTests(unittest.TestCase):
    def test_solve_simple_grid(self):
        """Solve a simple 3x3 grid with center black."""
        words = ["AB", "CD", "AC", "BD"]
        idx = _build_index(words)

        grid_template = [
            [True, True],
            [True, True],
        ]
        slots = extract_slots(grid_template)
        grid = [[None, None], [None, None]]

        result = solve(slots, idx, {}, set(), grid, max_backtracks=1000)
        self.assertIsNotNone(result)
        self.assertEqual(len(slots), len(result))


if __name__ == "__main__":
    unittest.main()
