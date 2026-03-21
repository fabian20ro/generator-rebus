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
            edits=[mod.PromptEdit(file="system/verify.md", find="a", replace="b")],
        )

        description = mod.build_assessment_description("march17/", exp)

        self.assertEqual(
            "march17/exp006 | remove technical-word hint from verify | system/verify.md",
            description,
        )

    def test_build_assessment_description_joins_multifile_paths(self):
        mod = _load_run_experiments_module()
        exp = mod.Experiment(
            name="exp073",
            desc="paired verify bundle OF UZ AZ",
            edits=[
                mod.PromptEdit(file="system/verify.md", find="a", replace="b"),
                mod.PromptEdit(file="user/verify.md", find="c", replace="d"),
            ],
        )

        description = mod.build_assessment_description("results/", exp)

        self.assertEqual(
            "results/exp073 | paired verify bundle OF UZ AZ | system/verify.md, user/verify.md",
            description,
        )

    def test_campaign_has_100_unique_experiments(self):
        mod = _load_run_experiments_module()

        self.assertEqual(100, len(mod.EXPERIMENTS))
        self.assertEqual(100, len({exp.name for exp in mod.EXPERIMENTS}))

    def test_cleanup_round_matches_requested_file_order(self):
        mod = _load_run_experiments_module()
        first_round_files = [exp.file for exp in mod.EXPERIMENTS[:12]]

        self.assertEqual(
            [
                "user/verify.md",
                "system/verify.md",
                "system/verify.md",
                "system/definition.md",
                "system/rewrite.md",
                "system/rate.md",
                "user/generate.md",
                "user/rewrite.md",
                "system/verify.md",
                "system/definition.md",
                "user/verify.md",
                "system/rewrite.md",
            ],
            first_round_files,
        )

    def test_manifest_contains_multifile_bundles(self):
        mod = _load_run_experiments_module()
        multifile = [exp for exp in mod.EXPERIMENTS if len(exp.edits) > 1]

        self.assertTrue(multifile)
        self.assertEqual("exp073", multifile[0].name)

    def test_all_manifest_edit_anchors_exist_in_current_prompts(self):
        mod = _load_run_experiments_module()

        for exp in mod.EXPERIMENTS:
            for edit in exp.edits:
                prompt_path = mod.PROMPTS_DIR / edit.file
                content = prompt_path.read_text(encoding="utf-8")
                self.assertIn(edit.find, content, msg=f"{exp.name} missing anchor in {edit.file}")

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

    def test_resolve_experiment_window_defaults_to_full_manifest(self):
        mod = _load_run_experiments_module()

        self.assertEqual((1, 100), mod.resolve_experiment_window(
            start_from=None,
            end_at=None,
            preset="full",
        ))

    def test_resolve_experiment_window_uses_pilot_slice(self):
        mod = _load_run_experiments_module()

        self.assertEqual((1, 12), mod.resolve_experiment_window(
            start_from=None,
            end_at=None,
            preset="pilot",
        ))
        self.assertEqual((5, 8), mod.resolve_experiment_window(
            start_from=5,
            end_at=8,
            preset="pilot",
        ))


if __name__ == "__main__":
    unittest.main()
