import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from generator.core.size_tuning import OVERNIGHT_LOOP_SIZES
from generator.loop_controller import LoopRunResult, build_batch_command, build_parser, run_cycle, run_size


class LoopControllerTests(unittest.TestCase):
    def test_loop_parser_defaults_to_sizes_seven_through_twelve(self):
        parser = build_parser()
        args = parser.parse_args([])

        self.assertEqual(list(OVERNIGHT_LOOP_SIZES), args.sizes)

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
