import importlib.util
import sys
import tempfile
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
        self.assertEqual("cleanup", mod.EXPERIMENTS[0].family)
        self.assertEqual("verify_examples_short", mod.EXPERIMENTS[12].family)
        self.assertEqual("rewrite_structural_guidance", mod.EXPERIMENTS[36].family)
        self.assertEqual("rewrite_framing", mod.EXPERIMENTS[38].family)
        self.assertEqual("definition_negative_examples", mod.EXPERIMENTS[48].family)
        self.assertEqual("definition_positive_examples", mod.EXPERIMENTS[52].family)
        self.assertEqual("definition_guidance", mod.EXPERIMENTS[57].family)
        self.assertEqual("rate_counterexamples", mod.EXPERIMENTS[60].family)
        self.assertEqual("rate_rules", mod.EXPERIMENTS[64].family)
        self.assertEqual("confirm_bundles", mod.EXPERIMENTS[-1].family)

    def test_v2_campaign_has_40_unique_experiments(self):
        mod = _load_run_experiments_module()

        self.assertEqual(40, len(mod.V2_EXPERIMENTS))
        self.assertEqual(40, len({exp.name for exp in mod.V2_EXPERIMENTS}))
        self.assertEqual("short_word_exactness", mod.V2_EXPERIMENTS[0].family)
        self.assertEqual("rare_technical_noun_rescue", mod.V2_EXPERIMENTS[-1].family)

    def test_v3_campaign_has_16_unique_experiments(self):
        mod = _load_run_experiments_module()

        self.assertEqual(16, len(mod.V3_EXPERIMENTS))
        self.assertEqual(16, len({exp.name for exp in mod.V3_EXPERIMENTS}))
        self.assertEqual("system_factor_temperatures", mod.V3_EXPERIMENTS[0].family)
        self.assertEqual("prompt_dedup_cleanup", mod.V3_EXPERIMENTS[-1].family)
        self.assertEqual("[system]", mod.V3_EXPERIMENTS[0].file)
        self.assertEqual(
            {"generate_temperature": 0.20, "rewrite_temperature": 0.30},
            mod.V3_EXPERIMENTS[0].assessment_overrides,
        )

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

    def test_active_manifest_edit_anchors_exist_in_current_prompts(self):
        mod = _load_run_experiments_module()

        for experiments in (mod.V2_EXPERIMENTS, mod.V3_EXPERIMENTS):
            for exp in experiments:
                for edit in exp.edits:
                    prompt_path = mod.PROMPTS_DIR / edit.file
                    content = prompt_path.read_text(encoding="utf-8")
                    self.assertTrue(
                        edit.find in content or (edit.replace and edit.replace in content),
                        msg=f"{exp.name} missing anchor/replacement in {edit.file}",
                    )

    def test_apply_experiment_skips_when_replacement_already_present(self):
        mod = _load_run_experiments_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            prompts_dir = Path(temp_dir)
            (prompts_dir / "user").mkdir(parents=True)
            target = prompts_dir / "user" / "verify.md"
            target.write_text("Excluzi orice variantă care nu are exact {answer_length} litere.\n", encoding="utf-8")

            original_prompts_dir = mod.PROMPTS_DIR
            mod.PROMPTS_DIR = prompts_dir
            try:
                applied = mod.apply_experiment(
                    mod.Experiment(
                        name="exp001",
                        desc="shorten verify user counting sentence",
                        edits=[
                            mod.PromptEdit(
                                file="user/verify.md",
                                find="Numără literele fiecărei variante înainte de a răspunde. Dacă nu are exact {answer_length} litere, nu o include.",
                                replace="Excluzi orice variantă care nu are exact {answer_length} litere.",
                            )
                        ],
                    )
                )
            finally:
                mod.PROMPTS_DIR = original_prompts_dir

        self.assertFalse(applied)

    def test_best_result_summary_path_lives_under_build_state_root(self):
        mod = _load_run_experiments_module()

        path = mod.best_result_summary_path(Path("generator/prompts"))

        self.assertEqual(
            mod.PROJECT_ROOT / "build" / "prompt_experiment_state" / "prompts" / "best_assessment.json",
            path,
        )

    def test_load_best_result_summary_falls_back_to_legacy_backup_dir_file(self):
        mod = _load_run_experiments_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            backup_dir = Path(temp_dir) / "legacy_backup"
            backup_dir.mkdir(parents=True)
            payload = {"composite": 81.9}
            (backup_dir / mod.BEST_ASSESSMENT_JSON).write_text(
                mod.json.dumps(payload),
                encoding="utf-8",
            )

            loaded = mod.load_best_result_summary(backup_dir)

        self.assertEqual(payload, loaded)

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
        decision = mod.classify_experiment_result(
            {
                "composite": 74.0,
                "pass_rate": 0.343,
                "protected_control_summary": {},
                "candidates": [],
            },
            {
                "composite": 74.2,
                "pass_rate": 0.343,
                "protected_control_summary": {},
                "candidates": [],
            },
            74.2,
        )

        self.assertEqual("uncertain", decision.status)
        self.assertAlmostEqual(-0.2, decision.delta)
        self.assertFalse(decision.protected_regression)
        self.assertFalse(decision.pass_regression)
        self.assertEqual("near_miss", decision.uncertain_reason)

    def test_classify_experiment_result_marks_research_signal_uncertain(self):
        mod = _load_run_experiments_module()
        decision = mod.classify_experiment_result(
            {
                "composite": 73.0,
                "pass_rate": 0.300,
                "protected_control_summary": {"high": {"pass_rate": 0.900}},
                "candidates": [
                    {"word": "A", "tier": "low", "verified": True},
                    {"word": "B", "tier": "medium", "verified": True},
                    {"word": "C", "tier": "medium", "verified": True},
                    {"word": "ADAPOST", "tier": "high", "verified": True},
                ],
            },
            {
                "composite": 74.0,
                "pass_rate": 0.314,
                "protected_control_summary": {"high": {"pass_rate": 0.900}},
                "candidates": [
                    {"word": "A", "tier": "low", "verified": False},
                    {"word": "B", "tier": "medium", "verified": False},
                    {"word": "C", "tier": "medium", "verified": False},
                    {"word": "ADAPOST", "tier": "high", "verified": True},
                ],
            },
            74.0,
        )

        self.assertEqual("uncertain", decision.status)
        self.assertEqual("research_signal", decision.uncertain_reason)
        self.assertTrue(decision.research_signal)

    def test_classify_experiment_result_marks_control_loss_as_discard(self):
        mod = _load_run_experiments_module()
        decision = mod.classify_experiment_result(
            {
                "composite": 74.1,
                "pass_rate": 0.343,
                "protected_control_summary": {"high": {"pass_rate": 0.800}},
                "candidates": [
                    {"word": "ADAPOST", "tier": "high", "verified": False},
                ],
            },
            {
                "composite": 74.2,
                "pass_rate": 0.343,
                "protected_control_summary": {"high": {"pass_rate": 0.900}},
                "candidates": [
                    {"word": "ADAPOST", "tier": "high", "verified": True},
                ],
            },
            74.2,
        )

        self.assertEqual("discard", decision.status)

    def test_classify_experiment_result_marks_primary_fragile_loss_as_discard(self):
        mod = _load_run_experiments_module()
        decision = mod.classify_experiment_result(
            {
                "composite": 74.1,
                "pass_rate": 0.343,
                "protected_control_summary": {"high": {"pass_rate": 0.900}},
                "candidates": [
                    {"word": "AZ", "tier": "low", "verified": False},
                ],
            },
            {
                "composite": 74.0,
                "pass_rate": 0.343,
                "protected_control_summary": {"high": {"pass_rate": 0.900}},
                "candidates": [
                    {"word": "AZ", "tier": "low", "verified": True},
                ],
            },
            74.0,
        )

        self.assertEqual("discard", decision.status)
        self.assertEqual(("AZ",), decision.signal.lost_primary_fragile)

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

    def test_resolve_experiment_window_supports_verify_examples_preset(self):
        mod = _load_run_experiments_module()

        self.assertEqual((13, 36), mod.resolve_experiment_window(
            start_from=None,
            end_at=None,
            preset="verify-examples",
        ))

    def test_resolve_experiment_window_supports_v3_preset(self):
        mod = _load_run_experiments_module()

        self.assertEqual(
            (1, 4),
            mod.resolve_experiment_window(
                start_from=None,
                end_at=None,
                preset="system-factor-temperatures",
                experiment_set="v3",
            ),
        )

    def test_summarize_family_outcomes_marks_family_stale_after_repeated_non_keeps(self):
        mod = _load_run_experiments_module()
        summary = mod.summarize_family_outcomes(
            [
                {
                    "name": "exp013",
                    "family": "verify_examples_short",
                    "status": "discard",
                    "word_signal": {"lost_low_medium": ["SAN"], "lost_high": []},
                },
                {
                    "name": "exp014",
                    "family": "verify_examples_short",
                    "status": "uncertain",
                    "word_signal": {"lost_low_medium": ["SAN"], "lost_high": []},
                },
                {
                    "name": "exp015",
                    "family": "verify_examples_short",
                    "status": "discard",
                    "word_signal": {"lost_low_medium": ["SAN"], "lost_high": []},
                },
            ],
            "verify_examples_short",
        )

        self.assertTrue(summary["stale"])
        self.assertEqual("repeated_collateral_losers", summary["stale_reason"])

    def test_classify_prompt_direction_prefers_verify_family(self):
        mod = _load_run_experiments_module()

        direction = mod.classify_prompt_direction(
            [
                {"name": "exp013", "status": "keep", "delta": 0.9},
                {"name": "exp014", "status": "keep", "delta": 0.4},
                {"name": "exp037", "status": "keep", "delta": 0.2},
                {"name": "exp061", "status": "discard", "delta": -0.4},
            ]
        )

        self.assertEqual("verify-led", direction)

    def test_classify_prompt_direction_stays_noisy_without_target_keeps(self):
        mod = _load_run_experiments_module()

        direction = mod.classify_prompt_direction(
            [
                {"name": "exp001", "status": "discard", "delta": -0.4},
                {"name": "exp002", "status": "uncertain", "delta": -0.1},
                {"name": "exp003", "status": "discard", "delta": -0.6},
            ]
        )

        self.assertEqual("noisy / not yet informative", direction)

    def test_recommend_next_presets_falls_back_to_priority_order(self):
        mod = _load_run_experiments_module()

        self.assertEqual(
            [
                "verify-examples",
                "rewrite-anti-distractor",
                "rate-exactness-calibration",
            ],
            mod.recommend_next_presets(
                [
                    {"name": "exp001", "status": "discard", "delta": -0.4},
                    {"name": "exp002", "status": "uncertain", "delta": -0.1},
                ]
            ),
        )

    def test_summarize_control_watch_marks_repeat_failures(self):
        mod = _load_run_experiments_module()

        summary = mod.summarize_control_watch(
            {
                "candidates": [
                    {"word": "ADAPOST", "verified": False},
                    {"word": "ETAN", "verified": True},
                ]
            },
            {"ADAPOST": False, "ETAN": False},
        )

        self.assertEqual(
            {
                "words": {
                    "ADAPOST": {"verified": False, "repeated_fail": True},
                    "ETAN": {"verified": True, "repeated_fail": False},
                },
                "demote-or-replace": ["ADAPOST"],
            },
            summary,
        )

    def test_summarize_log_control_watch_uses_logged_summaries(self):
        mod = _load_run_experiments_module()

        latest, repeated = mod.summarize_log_control_watch(
            [
                {
                    "name": "exp001",
                    "control_watch": {
                        "words": {
                            "ADAPOST": {"verified": False, "repeated_fail": True},
                            "ETAN": {"verified": False, "repeated_fail": True},
                        },
                        "demote-or-replace": ["ADAPOST", "ETAN"],
                    },
                }
            ]
        )

        self.assertEqual(["ADAPOST", "ETAN"], repeated)
        self.assertEqual(
            {
                "words": {
                    "ADAPOST": {"verified": False, "repeated_fail": True},
                    "ETAN": {"verified": False, "repeated_fail": True},
                },
                "demote-or-replace": ["ADAPOST", "ETAN"],
            },
            latest,
        )


if __name__ == "__main__":
    unittest.main()
