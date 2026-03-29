import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from generator.core.size_tuning import OVERNIGHT_LOOP_SIZES
from generator.loop_controller import (
    LoopRunResult,
    build_batch_command,
    build_parser,
    choose_balanced_size,
    fetch_puzzle_size_counts,
    run_cycle,
    run_size,
)


class LoopControllerTests(unittest.TestCase):
    def test_loop_parser_defaults_to_sizes_seven_through_fifteen(self):
        parser = build_parser()
        args = parser.parse_args([])

        self.assertEqual(list(OVERNIGHT_LOOP_SIZES), args.sizes)

    def test_loop_parser_defaults_to_thirty_rewrite_rounds(self):
        parser = build_parser()
        args = parser.parse_args([])

        self.assertEqual(30, args.rewrite_rounds)

    def test_build_batch_command_runs_one_size_at_a_time(self):
        command = build_batch_command(
            size=11,
            words="generator/output/words.json",
            output_root="generator/output/batch",
            rewrite_rounds=4,
            preparation_attempts=5,
            seed=1234,
        )

        self.assertIn("--sizes", command)
        self.assertEqual("11", command[command.index("--sizes") + 1])

    def test_build_batch_command_includes_multi_model_flag(self):
        command = build_batch_command(
            size=10,
            words="generator/output/words.json",
            output_root="generator/output/batch",
            rewrite_rounds=4,
            preparation_attempts=5,
            seed=42,
            multi_model=True,
        )

        self.assertIn("--multi-model", command)

    def test_build_batch_command_excludes_multi_model_by_default(self):
        command = build_batch_command(
            size=10,
            words="generator/output/words.json",
            output_root="generator/output/batch",
            rewrite_rounds=4,
            preparation_attempts=5,
            seed=42,
        )

        self.assertNotIn("--multi-model", command)

    def test_loop_parser_defaults_to_multi_model_enabled(self):
        parser = build_parser()
        args = parser.parse_args([])

        self.assertTrue(args.multi_model)

    def test_loop_parser_defaults_to_auto_size_disabled(self):
        parser = build_parser()
        args = parser.parse_args([])

        self.assertFalse(args.auto_size)

    @patch("generator.loop_controller.run_size")
    def test_run_cycle_continues_after_failure(self, mock_run_size):
        mock_run_size.side_effect = [
            LoopRunResult(size=7, seed=1, exit_code=1, latest_run_dir="-"),
            LoopRunResult(size=8, seed=2, exit_code=0, latest_run_dir="/tmp/run"),
        ]

        results = run_cycle(
            [7, 8],
            words="generator/output/words.json",
            output_root=Path("generator/output/batch"),
            rewrite_rounds=4,
            preparation_attempts=5,
            log_path=Path("generator/output/loop_runner.log"),
            cwd=Path("/tmp"),
        )

        self.assertEqual([1, 0], [result.exit_code for result in results])
        self.assertEqual(2, mock_run_size.call_count)

    @patch("generator.loop_controller.select_auto_size")
    @patch("generator.loop_controller.run_size")
    def test_run_cycle_auto_size_runs_single_selected_size(self, mock_run_size, mock_select_auto_size):
        mock_select_auto_size.return_value = 11
        mock_run_size.return_value = LoopRunResult(size=11, seed=1, exit_code=0, latest_run_dir="/tmp/run")

        results = run_cycle(
            [7, 8, 9],
            words="generator/output/words.json",
            output_root=Path("generator/output/batch"),
            rewrite_rounds=4,
            preparation_attempts=5,
            log_path=Path("generator/output/loop_runner.log"),
            cwd=Path("/tmp"),
            auto_size=True,
        )

        self.assertEqual([11], [result.size for result in results])
        self.assertEqual(1, mock_run_size.call_count)
        self.assertEqual(11, mock_run_size.call_args.args[0])

    def test_choose_balanced_size_treats_missing_sizes_as_zero_and_uses_smallest_tie_break(self):
        size, inventory = choose_balanced_size({7: 3, 8: 1, 10: 1})

        self.assertEqual(9, size)
        self.assertEqual(0, inventory[9])

    def test_fetch_puzzle_size_counts_aggregates_grid_sizes_from_supabase_rows(self):
        class _Query:
            def __init__(self, batches):
                self._batches = batches
                self._index = 0

            def select(self, _fields):
                return self

            def range(self, _start, _end):
                return self

            def execute(self):
                data = self._batches[self._index] if self._index < len(self._batches) else []
                self._index += 1
                return SimpleNamespace(data=data)

        class _Client:
            def __init__(self, batches):
                self._query = _Query(batches)

            def table(self, name):
                assert name == "crossword_puzzles"
                return self._query

        client = _Client([
            [{"grid_size": 7}, {"grid_size": 7}, {"grid_size": 10}],
            [{"grid_size": 10}, {"grid_size": 15}],
            [],
        ])

        counts = fetch_puzzle_size_counts(client=client, batch_size=3)

        self.assertEqual({7: 2, 10: 2, 15: 1}, counts)

    @patch("generator.loop_controller.subprocess.run")
    def test_run_size_logs_size_seed_and_exit_status(self, mock_subprocess_run):
        mock_subprocess_run.return_value.returncode = 7

        with TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "batch"
            output_root.mkdir(parents=True)
            (output_root / "20260314_123000").mkdir()
            log_path = Path(tmp_dir) / "loop.log"

            result = run_size(
                10,
                words="generator/output/words.json",
                output_root=output_root,
                rewrite_rounds=4,
                preparation_attempts=5,
                log_path=log_path,
                cwd=Path(tmp_dir),
                env={},
            )

            content = log_path.read_text(encoding="utf-8")
            self.assertIn("start size=10", content)
            self.assertIn("exit=7", content)
            self.assertIn("latest_run_dir=", content)
            self.assertEqual(7, result.exit_code)


if __name__ == "__main__":
    unittest.main()
