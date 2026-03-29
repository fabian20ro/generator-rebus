import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from generator.assessment import run_assessment as mod
from generator.core.model_manager import PRIMARY_MODEL, SECONDARY_MODEL


class _DummyRuntime:
    def __init__(self, multi_model: bool = True):
        self.multi_model = multi_model

    def activate_primary(self) -> None:
        return None

    def activate_secondary(self) -> None:
        return None


class RunAssessmentTests(unittest.TestCase):
    def test_run_assessment_uses_separate_generate_and_rewrite_temperatures(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dataset_path = Path(tmpdir) / "dataset.json"
            dataset_path.write_text(
                json.dumps(
                    [
                        {
                            "word": "CASA",
                            "tier": "low",
                            "display_word": "casă",
                            "length": 4,
                            "word_type": "N",
                            "dex_definitions": "Locuință.",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            generate_calls = []
            verify_calls = []
            rate_calls = []

            def fake_generate(client, word, display_word, word_type, dex_definitions, temperature, model_name):
                generate_calls.append((word, temperature, model_name))
                return f"def:{word}:{temperature}"

            def fake_verify(client, definition, length, word_type, max_guesses, model_name):
                verify_calls.append((definition, model_name, max_guesses))
                return []

            def fake_rate(client, word, display_word, definition, length, word_type, dex_definitions, model_name):
                rate_calls.append((definition, model_name))
                return (8, 7, True)

            with mock.patch.object(mod, "create_client", return_value=object()), \
                 mock.patch.object(mod, "LmRuntime", _DummyRuntime), \
                 mock.patch.object(mod, "_generate_for_word", side_effect=fake_generate), \
                 mock.patch.object(mod, "_verify_for_word", side_effect=fake_verify), \
                 mock.patch.object(mod, "_rate_for_word", side_effect=fake_rate):
                result = mod.run_assessment(
                    dataset_path=dataset_path,
                    generate_temperature=0.15,
                    rewrite_temperature=0.20,
                    verify_candidates=3,
                )

        self.assertEqual(
            [
                ("CASA", 0.15, PRIMARY_MODEL.model_id),
                ("CASA", 0.20, SECONDARY_MODEL.model_id),
            ],
            generate_calls,
        )
        self.assertEqual(
            [
                ("def:CASA:0.15", SECONDARY_MODEL.model_id, 3),
                ("def:CASA:0.2", PRIMARY_MODEL.model_id, 3),
            ],
            verify_calls,
        )
        self.assertEqual(
            [
                ("def:CASA:0.15", SECONDARY_MODEL.model_id),
                ("def:CASA:0.2", PRIMARY_MODEL.model_id),
            ],
            rate_calls,
        )
        self.assertEqual(1, len(result.candidates))

    def test_assessment_result_exposes_tier_balanced_pass_rate(self):
        result = mod.AssessmentResult(
            tier_results={
                "low": mod.TierResult(tier="low", total=30, passed=6),
                "medium": mod.TierResult(tier="medium", total=25, passed=5),
                "high": mod.TierResult(tier="high", total=15, passed=12),
            }
        )

        self.assertAlmostEqual((0.2 + 0.2 + 0.8) / 3, result.tier_balanced_pass_rate)
        self.assertEqual(
            round((0.2 + 0.2 + 0.8) / 3, 3),
            result.to_dict()["tier_balanced_pass_rate"],
        )


if __name__ == "__main__":
    unittest.main()
