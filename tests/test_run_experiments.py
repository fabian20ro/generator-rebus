import importlib.util
import sys
import unittest
from pathlib import Path


def _load_run_experiments_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "run_experiments.py"
    spec = importlib.util.spec_from_file_location("run_experiments_test_module", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class RunExperimentsTests(unittest.TestCase):
    def test_build_assessment_description_includes_desc_and_file(self):
        mod = _load_run_experiments_module()
        exp = mod.Experiment(
            name="exp006",
            desc="remove technical-word hint from verify",
            file="system/verify.md",
            find="a",
            replace="b",
        )

        description = mod.build_assessment_description("march17/", exp)

        self.assertEqual(
            "march17/exp006 | remove technical-word hint from verify | system/verify.md",
            description,
        )

    def test_campaign_has_100_unique_experiments(self):
        mod = _load_run_experiments_module()

        self.assertEqual(100, len(mod.EXPERIMENTS))
        self.assertEqual(100, len({exp.name for exp in mod.EXPERIMENTS}))

    def test_first_round_alternates_across_prompt_files(self):
        mod = _load_run_experiments_module()
        first_round_files = [exp.file for exp in mod.EXPERIMENTS[:8]]

        self.assertEqual(
            [
                "system/definition.md",
                "system/rate.md",
                "system/verify.md",
                "system/rewrite.md",
                "user/generate.md",
                "user/verify.md",
                "user/rate.md",
                "user/rewrite.md",
            ],
            first_round_files,
        )

    def test_protected_regression_detects_high_tier_drop(self):
        mod = _load_run_experiments_module()
        current = {
            "protected_control_summary": {
                "high": {"pass_rate": 0.300},
            }
        }
        incumbent = {
            "protected_control_summary": {
                "high": {"pass_rate": 0.400},
            }
        }

        self.assertTrue(mod.protected_regression(current, incumbent))

    def test_classify_experiment_result_marks_borderline_as_uncertain(self):
        mod = _load_run_experiments_module()
        status, delta, has_regression, pass_regression = mod.classify_experiment_result(
            {"composite": 74.0, "pass_rate": 0.343, "protected_control_summary": {}},
            {"composite": 74.2, "pass_rate": 0.343, "protected_control_summary": {}},
            74.2,
        )

        self.assertEqual("uncertain", status)
        self.assertAlmostEqual(-0.2, delta)
        self.assertFalse(has_regression)
        self.assertFalse(pass_regression)


if __name__ == "__main__":
    unittest.main()
