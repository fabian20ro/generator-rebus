import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class BenchmarkPhase1ScriptTests(unittest.TestCase):
    def _load_module(self):
        repo_root = Path(__file__).resolve().parents[3]
        script_path = repo_root / "tools" / "scripts" / "benchmark_phase1.py"
        spec = importlib.util.spec_from_file_location("benchmark_phase1_test_module", script_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def test_main_records_failure_rows_and_keeps_running(self):
        module = self._load_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            words_path = tmp_path / "words.json"
            words_path.write_text("[]", encoding="utf-8")
            output_dir = tmp_path / "out"

            def _fake_best_candidate(size, *_args, **_kwargs):
                if size == 14:
                    raise RuntimeError("unsolved 14x14")
                return SimpleNamespace(score=123.45, stats={"status": "solved", "chosen_black_count": 7})

            stdout = io.StringIO()
            argv = [
                "benchmark_phase1.py",
                "--sizes",
                "13",
                "14",
                "--step-budgets",
                "5000",
                "--words",
                str(words_path),
                "--output-dir",
                str(output_dir),
            ]
            with (
                patch.object(module, "_ensure_rust_binary"),
                patch.object(module, "rebuild_dictionary_profile"),
                patch.object(module, "_metadata_by_word", return_value={}),
                patch.object(module, "_best_candidate", side_effect=_fake_best_candidate),
                patch.object(sys, "argv", argv),
                patch.object(sys, "stdout", stdout),
            ):
                module.main()

            saved = sorted(output_dir.glob("benchmark_*.json"))
            self.assertEqual(1, len(saved))
            rows = json.loads(saved[0].read_text(encoding="utf-8"))
            self.assertEqual(2, len(rows))
            self.assertEqual("solved", rows[0]["status"])
            self.assertEqual(123.45, rows[0]["rust_score"])
            self.assertEqual("failed", rows[1]["status"])
            self.assertIn("unsolved 14x14", rows[1]["error"])
            self.assertGreaterEqual(rows[1]["rust_elapsed_sec"], 0.0)


if __name__ == "__main__":
    unittest.main()
