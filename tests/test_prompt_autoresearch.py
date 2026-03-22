import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "prompt_autoresearch.py"
    spec = importlib.util.spec_from_file_location("prompt_autoresearch_test_module", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PromptAutoresearchTests(unittest.TestCase):
    def test_select_next_experiment_respects_family_priority(self):
        mod = _load_module()
        families = mod.default_families()
        state = {
            "attempted_experiments": [],
            "current_family": None,
        }

        next_exp = mod.select_next_experiment(state, families)

        self.assertEqual("definition_examples", next_exp.family)
        self.assertEqual("exp049", next_exp.name)

    def test_select_next_experiment_unlocks_bundles_only_after_signal(self):
        mod = _load_module()
        families = mod.default_families()
        for name in ("definition_examples", "definition_rewrite_bundles"):
            families[name]["stale"] = True
        state = {
            "attempted_experiments": [f"exp{i:03d}" for i in range(1, 85)],
            "current_family": None,
        }

        self.assertIsNone(mod.select_next_experiment(state, families))

        families["definition_examples"]["has_signal"] = True
        families["rate_exactness"]["has_signal"] = True
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
            self.assertEqual(1, families["definition_examples"]["keeps"])


if __name__ == "__main__":
    unittest.main()
