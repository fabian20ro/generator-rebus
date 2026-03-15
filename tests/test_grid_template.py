import unittest

from generator.core.grid_template import (
    _black_spacing_ok,
    _is_connected,
    generate_incremental_template,
    generate_procedural_template,
    validate_template,
)


def _parse(rows: list[str]) -> list[list[bool]]:
    """Parse compact row strings like '#..#' into a grid."""
    return [[ch == "." for ch in row] for row in rows]


class ValidateTemplateTests(unittest.TestCase):
    def test_adjacent_blacks_rejected(self):
        grid = _parse([
            "..##..",
            "......",
            "......",
            "......",
            "......",
            "......",
        ])
        valid, msg = validate_template(grid)
        self.assertFalse(valid)
        self.assertIn("too close", msg.lower())

    def test_one_gap_blacks_rejected(self):
        grid = _parse([
            ".#.#..",
            "......",
            "......",
            "......",
            "......",
            "......",
        ])
        valid, msg = validate_template(grid)
        self.assertFalse(valid)
        self.assertIn("too close", msg.lower())

    def test_two_gap_blacks_accepted(self):
        grid = _parse([
            "#..#..",
            "......",
            "......",
            "#..#..",
            "......",
            "......",
        ])
        valid, msg = validate_template(grid)
        self.assertTrue(valid, f"Expected valid but got: {msg}")

    def test_edge_adjacency_ok(self):
        grid = _parse([
            "#.....",
            "......",
            "......",
            "......",
            "......",
            "......",
        ])
        valid, msg = validate_template(grid)
        self.assertTrue(valid, f"Expected valid but got: {msg}")

    def test_vertical_adjacent_rejected(self):
        grid = _parse([
            "#.....",
            "#.....",
            "......",
            "......",
            "......",
            "......",
        ])
        valid, msg = validate_template(grid)
        self.assertFalse(valid)

    def test_vertical_one_gap_rejected(self):
        grid = _parse([
            "#.....",
            "......",
            "#.....",
            "......",
            "......",
            "......",
        ])
        valid, msg = validate_template(grid)
        self.assertFalse(valid)


class BlackSpacingOkTests(unittest.TestCase):
    def test_no_nearby_blacks(self):
        grid = [[True] * 7 for _ in range(7)]
        self.assertTrue(_black_spacing_ok(grid, 3, 3, 7))

    def test_black_one_away_on_row(self):
        grid = [[True] * 7 for _ in range(7)]
        grid[3][4] = False
        self.assertFalse(_black_spacing_ok(grid, 3, 3, 7))

    def test_black_two_away_on_column(self):
        grid = [[True] * 7 for _ in range(7)]
        grid[5][3] = False
        self.assertFalse(_black_spacing_ok(grid, 3, 3, 7))

    def test_diagonal_black_ok(self):
        grid = [[True] * 7 for _ in range(7)]
        grid[4][4] = False
        self.assertTrue(_black_spacing_ok(grid, 3, 3, 7))

    def test_edge_position_no_crash(self):
        grid = [[True] * 5 for _ in range(5)]
        self.assertTrue(_black_spacing_ok(grid, 0, 0, 5))


class IsConnectedTests(unittest.TestCase):
    def test_all_letters_connected(self):
        grid = [[True] * 5 for _ in range(5)]
        self.assertTrue(_is_connected(grid))

    def test_disconnected_by_black_row(self):
        grid = [
            [True, True, True],
            [False, False, False],
            [True, True, True],
        ]
        self.assertFalse(_is_connected(grid))

    def test_single_black_still_connected(self):
        grid = _parse([
            "...",
            ".#.",
            "...",
        ])
        self.assertTrue(_is_connected(grid))


class IncrementalTemplateTests(unittest.TestCase):
    def test_incremental_starts_empty(self):
        result = generate_incremental_template(
            5,
            solver_fn=lambda g: True,
        )
        self.assertIsNotNone(result)
        blacks = sum(1 for row in result for cell in row if not cell)
        self.assertEqual(0, blacks)

    def test_incremental_adds_blacks_when_needed(self):
        call_count = [0]

        def solver_needs_blacks(grid):
            blacks = sum(1 for row in grid for cell in row if not cell)
            call_count[0] += 1
            return blacks >= 2

        import random
        result = generate_incremental_template(
            5,
            solver_fn=solver_needs_blacks,
            min_solver_step=1,
            rng=random.Random(42),
        )
        self.assertIsNotNone(result)
        blacks = sum(1 for row in result for cell in row if not cell)
        self.assertGreaterEqual(blacks, 2)

    def test_incremental_gives_up_at_max(self):
        result = generate_incremental_template(
            5,
            solver_fn=lambda g: False,
            max_blacks=3,
        )
        self.assertIsNone(result)

    def test_min_solver_step_skips_early_calls(self):
        """Solver should not be called before min_solver_step."""
        solver_calls_at_step = []

        def tracking_solver(grid):
            blacks = sum(1 for row in grid for cell in row if not cell)
            solver_calls_at_step.append(blacks)
            return blacks >= 4

        import random
        result = generate_incremental_template(
            5,
            solver_fn=tracking_solver,
            min_solver_step=3,
            max_blacks=6,
            rng=random.Random(42),
        )
        self.assertIsNotNone(result)
        # All solver calls should have been at step >= 3 (i.e. >= 3 blacks)
        for blacks_count in solver_calls_at_step:
            self.assertGreaterEqual(blacks_count, 3)

    def test_lazy_candidate_picks_valid_cell(self):
        """Lazy evaluation should still produce valid templates."""
        import random
        for seed in range(5):
            result = generate_incremental_template(
                6,
                solver_fn=lambda g: sum(1 for row in g for cell in row if not cell) >= 3,
                min_solver_step=1,
                rng=random.Random(seed),
            )
            self.assertIsNotNone(result, f"Failed with seed {seed}")
            valid, msg = validate_template(result)
            self.assertTrue(valid, f"Seed {seed}: {msg}")


class ProceduralTemplateTests(unittest.TestCase):
    def test_procedural_generator_uses_strict_spacing(self):
        import random
        grid = generate_procedural_template(7, target_blacks=4, rng=random.Random(42))
        if grid is None:
            self.skipTest("Procedural generation didn't produce a grid with this seed")
        # Verify no #.# patterns on any row
        for r in range(len(grid)):
            black_cols = [c for c in range(len(grid[0])) if not grid[r][c]]
            for i in range(len(black_cols) - 1):
                self.assertGreaterEqual(
                    black_cols[i + 1] - black_cols[i], 3,
                    f"Blacks too close on row {r}: cols {black_cols[i]},{black_cols[i+1]}"
                )
        # Verify no #.# patterns on any column
        for c in range(len(grid[0])):
            black_rows = [r for r in range(len(grid)) if not grid[r][c]]
            for i in range(len(black_rows) - 1):
                self.assertGreaterEqual(
                    black_rows[i + 1] - black_rows[i], 3,
                    f"Blacks too close on col {c}: rows {black_rows[i]},{black_rows[i+1]}"
                )


if __name__ == "__main__":
    unittest.main()
