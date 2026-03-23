import importlib.util
import json
import sys
import tempfile
import unittest
import io
from unittest import mock
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "prompt_autoresearch.py"
    spec = importlib.util.spec_from_file_location("prompt_autoresearch_test_module", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PromptAutoresearchTests(unittest.TestCase):
    def _tiny_prompt_tree(self, root: Path) -> Path:
        prompts = root / "prompts"
        (prompts / "system").mkdir(parents=True, exist_ok=True)
        (prompts / "system" / "verify.md").write_text("prompt\n", encoding="utf-8")
        return prompts

    def test_validate_state_detects_incumbent_mismatch(self):
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            paths = mod.family_paths(root)
            paths["state"].write_text("{}", encoding="utf-8")
            paths["families"].write_text("{}", encoding="utf-8")
            paths["incumbent"].write_text("{}", encoding="utf-8")
            paths["incumbent_prompts"].mkdir(parents=True)
            original_prompts_dir = mod.runner.PROMPTS_DIR
            prompts = root / "prompts"
            (prompts / "system").mkdir(parents=True)
            (prompts / "system" / "verify.md").write_text("same\n", encoding="utf-8")
            (paths["incumbent_prompts"] / "system").mkdir(parents=True, exist_ok=True)
            (paths["incumbent_prompts"] / "system" / "verify.md").write_text("same\n", encoding="utf-8")
            mod.runner.PROMPTS_DIR = prompts
            try:
                valid, reason = mod.validate_state(
                    state_dir=root,
                    state={"status": "idle", "incumbent_composite": 81.9, "incumbent_pass_rate": 0.386, "attempted_experiments": []},
                    families=mod.default_families(),
                    incumbent={"composite": 73.3, "pass_rate": 0.3},
                    campaign_log=None,
                )
            finally:
                mod.runner.PROMPTS_DIR = original_prompts_dir

        self.assertFalse(valid)
        self.assertEqual("incumbent metrics mismatch", reason)

    def test_bootstrap_from_campaign_uses_replayed_keep_as_incumbent(self):
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            baseline = {
                "composite": 73.3,
                "pass_rate": 0.3,
                "avg_semantic": 9.1,
                "avg_rebus": 8.1,
                "protected_control_summary": {"high": {"pass_rate": 0.867}},
                "candidates": [],
            }
            keep_payload = {
                "composite": 81.9,
                "pass_rate": 0.386,
                "avg_semantic": 9.0,
                "avg_rebus": 8.1,
                "protected_control_summary": {"high": {"pass_rate": 0.933}},
                "candidates": [],
            }
            baseline_json = root / "baseline.json"
            keep_json = root / "exp002.json"
            baseline_json.write_text(json.dumps(baseline), encoding="utf-8")
            keep_json.write_text(json.dumps(keep_payload), encoding="utf-8")
            log_path = root / "campaign.json"
            log_path.write_text(
                json.dumps(
                    [
                        {"name": "exp001", "assessment_json": str(keep_json), "assessment_description": "x"},
                        {"name": "exp002", "assessment_json": str(keep_json), "assessment_description": "y"},
                    ]
                ),
                encoding="utf-8",
            )
            prompts = root / "prompts"
            (prompts / "system").mkdir(parents=True)
            (prompts / "system" / "verify.md").write_text("prompt\n", encoding="utf-8")
            original_prompts_dir = mod.runner.PROMPTS_DIR
            original_results = mod.runner.RESULTS_TSV
            mod.runner.PROMPTS_DIR = prompts
            mod.runner.RESULTS_TSV = root / "results.tsv"
            try:
                state, families, incumbent = mod.bootstrap_from_campaign(
                    state_dir=root / "state_dir",
                    campaign_log=log_path,
                    baseline_json=baseline_json,
                    seed_prompts_dir=None,
                )
            finally:
                mod.runner.PROMPTS_DIR = original_prompts_dir
                mod.runner.RESULTS_TSV = original_results

        self.assertEqual(81.9, incumbent["composite"])
        self.assertEqual(81.9, state["incumbent_composite"])
        self.assertEqual(2, len(state["attempted_experiments"]))

    def test_select_next_experiment_respects_family_priority(self):
        mod = _load_module()
        families = mod.default_families()
        state = {
            "attempted_experiments": [],
            "current_family": None,
        }

        next_exp = mod.select_next_experiment(state, families)

        self.assertEqual("definition_positive_examples", next_exp.family)
        self.assertEqual("exp053", next_exp.name)

    def test_select_next_experiment_respects_v2_family_priority(self):
        mod = _load_module()
        families = mod.default_families("v2")
        state = {
            "attempted_experiments": [],
            "current_family": None,
            "experiment_set": "v2",
        }

        next_exp = mod.select_next_experiment(state, families)

        self.assertEqual("short_word_exactness", next_exp.family)
        self.assertEqual("v2exp001", next_exp.name)

    def test_select_next_experiment_respects_v3_family_priority(self):
        mod = _load_module()
        families = mod.default_families("v3")
        state = {
            "attempted_experiments": [],
            "current_family": None,
            "experiment_set": "v3",
        }

        next_exp = mod.select_next_experiment(state, families)

        self.assertEqual("system_factor_temperatures", next_exp.family)
        self.assertEqual("v3exp001", next_exp.name)

    def test_main_rebuild_state_exits_without_running_trials(self):
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "state"
            state = {
                "campaign_id": "x",
                "status": "idle",
                "current_family": None,
                "current_experiment": None,
                "incumbent_composite": 81.9,
                "incumbent_pass_rate": 0.386,
                "attempted_experiments": [],
            }
            families = mod.default_families("v2")
            incumbent = {"composite": 81.9, "pass_rate": 0.386}
            with mock.patch.object(sys, "argv", ["prompt_autoresearch.py", "--state-dir", str(state_dir), "--experiment-set", "v2", "--rebuild-state"]), \
                 mock.patch.object(mod, "rebuild_state_from_campaign", return_value=(state, families, incumbent)), \
                 mock.patch.object(mod, "validate_state", return_value=(True, None)), \
                 mock.patch.object(mod, "run_supervisor") as run_supervisor, \
                 mock.patch("sys.stdout", new_callable=io.StringIO):
                mod.main()

        run_supervisor.assert_not_called()

    def test_select_next_experiment_unlocks_bundles_only_after_signal(self):
        mod = _load_module()
        families = mod.default_families()
        for name in ("definition_positive_examples", "definition_guidance", "definition_rewrite_bundles"):
            families[name]["stale"] = True
        state = {
            "attempted_experiments": [f"exp{i:03d}" for i in range(1, 85)],
            "current_family": None,
        }

        self.assertIsNone(mod.select_next_experiment(state, families))

        families["definition_positive_examples"]["has_signal"] = True
        families["rate_rules"]["has_signal"] = True
        families["definition_rate_bundles"]["stale"] = False
        state["attempted_experiments"] = [f"exp{i:03d}" for i in range(1, 93)]

        next_exp = mod.select_next_experiment(state, families)

        self.assertEqual("definition_rate_bundles", next_exp.family)
        self.assertEqual("exp093", next_exp.name)

    def test_recover_if_interrupted_restores_incumbent_prompts(self):
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            prompts = root / "prompts"
            (prompts / "user").mkdir(parents=True)
            target = prompts / "user" / "rewrite.md"
            target.write_text("trial state\n", encoding="utf-8")

            snapshot = root / "snapshots" / "incumbent_prompts" / "user"
            snapshot.mkdir(parents=True)
            (snapshot / "rewrite.md").write_text("incumbent state\n", encoding="utf-8")

            original_prompts_dir = mod.runner.PROMPTS_DIR
            mod.runner.PROMPTS_DIR = prompts
            try:
                state = {
                    "status": "running",
                    "active_trial": {"id": "exp039"},
                    "current_experiment": "exp039",
                    "stop_reason": None,
                }
                mod.recover_if_interrupted(root, state)
            finally:
                mod.runner.PROMPTS_DIR = original_prompts_dir

            self.assertEqual("incumbent state\n", target.read_text(encoding="utf-8"))
            self.assertEqual("interrupted", state["status"])
            self.assertIsNone(state["active_trial"])

    def test_replay_campaign_log_reclassifies_entries(self):
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base_payload = {
                "composite": 80.0,
                "pass_rate": 0.4,
                "avg_semantic": 9.0,
                "avg_rebus": 8.0,
                "protected_control_summary": {"high": {"pass_rate": 1.0}},
                "candidates": [
                    {"word": "A", "tier": "low", "verified": False},
                    {"word": "ADAPOST", "tier": "high", "verified": True},
                ],
            }
            keep_payload = {
                "composite": 81.0,
                "pass_rate": 0.414,
                "avg_semantic": 9.0,
                "avg_rebus": 8.1,
                "protected_control_summary": {"high": {"pass_rate": 1.0}},
                "candidates": [
                    {"word": "A", "tier": "low", "verified": True},
                    {"word": "ADAPOST", "tier": "high", "verified": True},
                ],
            }
            discard_payload = {
                "composite": 79.0,
                "pass_rate": 0.386,
                "avg_semantic": 9.1,
                "avg_rebus": 8.1,
                "protected_control_summary": {"high": {"pass_rate": 0.0}},
                "candidates": [
                    {"word": "A", "tier": "low", "verified": False},
                    {"word": "ADAPOST", "tier": "high", "verified": False},
                ],
            }
            keep_json = root / "exp049.json"
            discard_json = root / "exp050.json"
            keep_json.write_text(json.dumps(keep_payload), encoding="utf-8")
            discard_json.write_text(json.dumps(discard_payload), encoding="utf-8")
            log_path = root / "campaign.json"
            log_path.write_text(
                json.dumps(
                    [
                        {"name": "exp049", "assessment_json": str(keep_json), "assessment_description": "x"},
                        {"name": "exp050", "assessment_json": str(discard_json), "assessment_description": "y"},
                    ]
                ),
                encoding="utf-8",
            )

            reclassified, incumbent, families = mod.replay_campaign_log(
                log_path,
                incumbent_payload=base_payload,
            )

            self.assertEqual("keep", reclassified[0]["status"])
            self.assertEqual("discard", reclassified[1]["status"])
            self.assertEqual(81.0, incumbent["composite"])
            self.assertEqual(1, families["definition_negative_examples"]["keeps"])

    def test_persist_campaign_state_writes_matching_state_and_incumbent(self):
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state = {
                "status": "idle",
                "incumbent_composite": 81.9,
                "incumbent_pass_rate": 0.386,
                "attempted_experiments": [],
            }
            families = mod.default_families()
            incumbent = {"composite": 81.9, "pass_rate": 0.386}

            mod.persist_campaign_state(root, state, families, incumbent)

            written_state = json.loads((root / "state.json").read_text())
            written_incumbent = json.loads((root / "incumbent.json").read_text())

        self.assertEqual(written_state["incumbent_composite"], written_incumbent["composite"])
        self.assertEqual(written_state["incumbent_pass_rate"], written_incumbent["pass_rate"])

    def test_resume_existing_state_rebuilds_on_inconsistent_incumbent(self):
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state_dir = root / "state_dir"
            paths = mod.family_paths(state_dir)
            paths["state"].parent.mkdir(parents=True, exist_ok=True)
            baseline = {
                "composite": 73.3,
                "pass_rate": 0.3,
                "avg_semantic": 9.1,
                "avg_rebus": 8.1,
                "protected_control_summary": {"high": {"pass_rate": 0.867}},
                "candidates": [],
            }
            keep_payload = {
                "composite": 81.9,
                "pass_rate": 0.386,
                "avg_semantic": 9.0,
                "avg_rebus": 8.1,
                "protected_control_summary": {"high": {"pass_rate": 0.933}},
                "candidates": [],
            }
            baseline_json = root / "baseline.json"
            keep_json = root / "exp002.json"
            baseline_json.write_text(json.dumps(baseline), encoding="utf-8")
            keep_json.write_text(json.dumps(keep_payload), encoding="utf-8")
            log_path = root / "campaign.json"
            log_path.write_text(
                json.dumps([{"name": "exp001", "assessment_json": str(keep_json), "assessment_description": "x"}]),
                encoding="utf-8",
            )
            prompts = root / "prompts"
            (prompts / "system").mkdir(parents=True)
            (prompts / "system" / "verify.md").write_text("prompt\n", encoding="utf-8")
            (paths["incumbent_prompts"] / "system").mkdir(parents=True)
            (paths["incumbent_prompts"] / "system" / "verify.md").write_text("prompt\n", encoding="utf-8")
            paths["state"].write_text(
                json.dumps({"status": "idle", "incumbent_composite": 81.9, "incumbent_pass_rate": 0.386, "attempted_experiments": []}),
                encoding="utf-8",
            )
            paths["families"].write_text(json.dumps(mod.default_families()), encoding="utf-8")
            paths["incumbent"].write_text(json.dumps({"composite": 73.3, "pass_rate": 0.3}), encoding="utf-8")
            original_prompts_dir = mod.runner.PROMPTS_DIR
            original_results = mod.runner.RESULTS_TSV
            mod.runner.PROMPTS_DIR = prompts
            mod.runner.RESULTS_TSV = root / "results.tsv"
            try:
                state, _families, incumbent = mod.resume_existing_state(
                    state_dir=state_dir,
                    campaign_log=log_path,
                    baseline_json=baseline_json,
                    seed_prompts_dir=None,
                )
            finally:
                mod.runner.PROMPTS_DIR = original_prompts_dir
                mod.runner.RESULTS_TSV = original_results

        self.assertEqual(81.9, incumbent["composite"])
        self.assertEqual(81.9, state["incumbent_composite"])
        self.assertIn("rebuilt state after validation failure", state["stop_reason"])
        self.assertEqual(str(state_dir / "snapshots" / "incumbent_prompts"), state["incumbent_prompt_snapshot"])

    def test_run_supervisor_non_keep_preserves_incumbent_and_sets_next_experiment(self):
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state_dir = root / "state"
            prompts = self._tiny_prompt_tree(root)
            original_prompts_dir = mod.runner.PROMPTS_DIR
            mod.runner.PROMPTS_DIR = prompts
            try:
                paths = mod.family_paths(state_dir)
                mod.copy_prompt_tree(paths["incumbent_prompts"])
                state = {
                    "campaign_id": "x",
                    "status": "idle",
                    "current_family": None,
                    "current_experiment": None,
                    "incumbent_composite": 81.9,
                    "incumbent_pass_rate": 0.386,
                    "incumbent_prompt_snapshot": str(paths["incumbent_prompts"]),
                    "active_trial": None,
                    "stop_reason": None,
                    "campaign_log": None,
                    "baseline_json": None,
                    "attempted_experiments": [],
                    "stale_family_streak": 0,
                    "heartbeat_ts": None,
                }
                families = mod.default_families()
                incumbent = {
                    "composite": 81.9,
                    "pass_rate": 0.386,
                    "protected_control_summary": {"high": {"pass_rate": 0.933}},
                    "candidates": [],
                }
                result = {
                    "composite": 79.0,
                    "pass_rate": 0.300,
                    "avg_semantic": 9.0,
                    "avg_rebus": 8.0,
                    "protected_control_summary": {"high": {"pass_rate": 0.800}},
                    "candidates": [],
                }
                with mock.patch.object(mod, "load_or_initialize_state", return_value=(state, families, incumbent)), \
                     mock.patch.object(mod, "recover_if_interrupted"), \
                     mock.patch.object(mod.runner, "snapshot_results_tsv", return_value="snap"), \
                     mock.patch.object(mod.runner, "restore_results_tsv"), \
                     mock.patch.object(mod.runner, "apply_experiment", return_value=True), \
                     mock.patch.object(mod.runner, "append_results_row"), \
                     mock.patch.object(mod.runner, "run_assessment", return_value=result):
                    exit_code = mod.run_supervisor(
                        state_dir=state_dir,
                        campaign_log=None,
                        baseline_json=None,
                        seed_prompts_dir=None,
                        max_trials=1,
                        description_prefix="autoresearch/",
                        dry_run=False,
                    )
            finally:
                mod.runner.PROMPTS_DIR = original_prompts_dir

            self.assertEqual(0, exit_code)
            written_state = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
            written_incumbent = json.loads((state_dir / "incumbent.json").read_text(encoding="utf-8"))
            self.assertEqual(81.9, written_incumbent["composite"])
            self.assertEqual(81.9, written_state["incumbent_composite"])
            self.assertEqual("idle", written_state["status"])
            self.assertEqual("max trials reached", written_state["stop_reason"])
            self.assertEqual("exp054", written_state["current_experiment"])

    def test_run_supervisor_keep_updates_incumbent(self):
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state_dir = root / "state"
            prompts = self._tiny_prompt_tree(root)
            original_prompts_dir = mod.runner.PROMPTS_DIR
            mod.runner.PROMPTS_DIR = prompts
            try:
                paths = mod.family_paths(state_dir)
                mod.copy_prompt_tree(paths["incumbent_prompts"])
                state = {
                    "campaign_id": "x",
                    "status": "idle",
                    "current_family": None,
                    "current_experiment": None,
                    "incumbent_composite": 81.9,
                    "incumbent_pass_rate": 0.386,
                    "incumbent_prompt_snapshot": str(paths["incumbent_prompts"]),
                    "active_trial": None,
                    "stop_reason": None,
                    "campaign_log": None,
                    "baseline_json": None,
                    "attempted_experiments": [],
                    "stale_family_streak": 0,
                    "heartbeat_ts": None,
                }
                families = mod.default_families()
                incumbent = {
                    "composite": 81.9,
                    "pass_rate": 0.386,
                    "protected_control_summary": {"high": {"pass_rate": 0.933}},
                    "candidates": [],
                }
                result = {
                    "composite": 82.4,
                    "pass_rate": 0.400,
                    "avg_semantic": 9.1,
                    "avg_rebus": 8.2,
                    "protected_control_summary": {"high": {"pass_rate": 0.933}},
                    "candidates": [],
                }
                with mock.patch.object(mod, "load_or_initialize_state", return_value=(state, families, incumbent)), \
                     mock.patch.object(mod, "recover_if_interrupted"), \
                     mock.patch.object(mod.runner, "snapshot_results_tsv", return_value="snap"), \
                     mock.patch.object(mod.runner, "restore_results_tsv"), \
                     mock.patch.object(mod.runner, "apply_experiment", return_value=True), \
                     mock.patch.object(mod.runner, "append_results_row"), \
                     mock.patch.object(mod.runner, "run_assessment", return_value=result):
                    exit_code = mod.run_supervisor(
                        state_dir=state_dir,
                        campaign_log=None,
                        baseline_json=None,
                        seed_prompts_dir=None,
                        max_trials=1,
                        description_prefix="autoresearch/",
                        dry_run=False,
                    )
            finally:
                mod.runner.PROMPTS_DIR = original_prompts_dir

            self.assertEqual(0, exit_code)
            written_state = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
            written_incumbent = json.loads((state_dir / "incumbent.json").read_text(encoding="utf-8"))
            self.assertEqual(82.4, written_incumbent["composite"])
            self.assertEqual(82.4, written_state["incumbent_composite"])

    def test_run_supervisor_continuous_runs_until_no_viable_family(self):
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state_dir = root / "state"
            prompts = self._tiny_prompt_tree(root)
            original_prompts_dir = mod.runner.PROMPTS_DIR
            mod.runner.PROMPTS_DIR = prompts
            try:
                paths = mod.family_paths(state_dir)
                mod.copy_prompt_tree(paths["incumbent_prompts"])
                state = {
                    "campaign_id": "x",
                    "status": "idle",
                    "current_family": None,
                    "current_experiment": None,
                    "incumbent_composite": 81.9,
                    "incumbent_pass_rate": 0.386,
                    "incumbent_prompt_snapshot": str(paths["incumbent_prompts"]),
                    "active_trial": None,
                    "stop_reason": None,
                    "campaign_log": None,
                    "baseline_json": None,
                    "attempted_experiments": [],
                    "stale_family_streak": 0,
                    "heartbeat_ts": None,
                }
                families = mod.default_families()
                incumbent = {
                    "composite": 81.9,
                    "pass_rate": 0.386,
                    "protected_control_summary": {"high": {"pass_rate": 0.933}},
                    "candidates": [],
                }
                result = {
                    "composite": 79.0,
                    "pass_rate": 0.300,
                    "avg_semantic": 9.0,
                    "avg_rebus": 8.0,
                    "protected_control_summary": {"high": {"pass_rate": 0.800}},
                    "candidates": [],
                }
                exp = mod.runner.get_experiment("exp049")
                with mock.patch.object(mod, "load_or_initialize_state", return_value=(state, families, incumbent)), \
                     mock.patch.object(mod, "recover_if_interrupted"), \
                     mock.patch.object(mod.runner, "snapshot_results_tsv", return_value="snap"), \
                     mock.patch.object(mod.runner, "restore_results_tsv"), \
                     mock.patch.object(mod.runner, "apply_experiment", return_value=True), \
                     mock.patch.object(mod.runner, "append_results_row"), \
                     mock.patch.object(mod.runner, "run_assessment", return_value=result), \
                     mock.patch.object(mod, "select_next_experiment", side_effect=[exp, None]):
                    exit_code = mod.run_supervisor(
                        state_dir=state_dir,
                        campaign_log=None,
                        baseline_json=None,
                        seed_prompts_dir=None,
                        max_trials=None,
                        description_prefix="autoresearch/",
                        dry_run=False,
                    )
            finally:
                mod.runner.PROMPTS_DIR = original_prompts_dir

            self.assertEqual(0, exit_code)
            written_state = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual("stopped", written_state["status"])
            self.assertEqual("no viable families remaining", written_state["stop_reason"])

    def test_validate_state_detects_skipped_experiment_leakage(self):
        mod = _load_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            state_dir = root / "state"
            paths = mod.family_paths(state_dir)
            seed = paths["seed_prompts"] / "system"
            incumbent = paths["incumbent_prompts"] / "system"
            prompts = root / "prompts" / "system"
            seed.mkdir(parents=True, exist_ok=True)
            incumbent.mkdir(parents=True, exist_ok=True)
            prompts.mkdir(parents=True, exist_ok=True)
            base_text = (mod.runner.PROMPTS_DIR / "system" / "rewrite.md").read_text(encoding="utf-8")
            seed_text = base_text.replace(
                "- Pentru OF, păstrezi interjecția de durere ori regret și excluzi exclamația vagă de tip AH.\n",
                "",
            )
            seed_file = seed / "rewrite.md"
            incumbent_file = incumbent / "rewrite.md"
            prompt_file = prompts / "rewrite.md"
            seed_file.write_text(seed_text, encoding="utf-8")
            leaked_text = seed_text.replace(
                "- Max 15 cuvinte.\n",
                "- Pentru OF, păstrezi interjecția de durere ori regret și excluzi exclamația vagă de tip AH.\n- Max 15 cuvinte.\n",
                1,
            )
            incumbent_file.write_text(leaked_text, encoding="utf-8")
            prompt_file.write_text(leaked_text, encoding="utf-8")
            paths["state"].parent.mkdir(parents=True, exist_ok=True)
            paths["state"].write_text(
                json.dumps(
                    {
                        "status": "idle",
                        "incumbent_composite": 81.9,
                        "incumbent_pass_rate": 0.386,
                        "attempted_experiments": ["v2exp001"],
                        "experiment_set": "v2",
                    }
                ),
                encoding="utf-8",
            )
            paths["families"].write_text(json.dumps(mod.default_families("v2")), encoding="utf-8")
            paths["incumbent"].write_text(json.dumps({"composite": 81.9, "pass_rate": 0.386}), encoding="utf-8")
            paths["trials"].mkdir(parents=True, exist_ok=True)
            (paths["trials"] / "v2exp001.json").write_text(json.dumps({"status": "skipped"}), encoding="utf-8")
            original_prompts_dir = mod.runner.PROMPTS_DIR
            mod.runner.PROMPTS_DIR = root / "prompts"
            try:
                valid, reason = mod.validate_state(
                    state_dir=state_dir,
                    state=json.loads(paths["state"].read_text(encoding="utf-8")),
                    families=mod.default_families("v2"),
                    incumbent={"composite": 81.9, "pass_rate": 0.386},
                    campaign_log=None,
                )
            finally:
                mod.runner.PROMPTS_DIR = original_prompts_dir

        self.assertFalse(valid)
        self.assertIn("skipped experiment leakage", reason)


if __name__ == "__main__":
    unittest.main()
