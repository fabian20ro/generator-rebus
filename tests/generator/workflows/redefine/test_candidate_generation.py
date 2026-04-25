import unittest
from unittest.mock import MagicMock

from rebus_generator.platform.llm.models import ModelConfig
from rebus_generator.workflows.redefine.candidate_generation import (
    RewriteCandidateRequest,
    RewriteCandidateResult,
    RewriteCandidateGeneratorImpl,
    RewriteLLMPort,
    RewriteAuditPort,
)

class CandidateGenerationTests(unittest.TestCase):
    def setUp(self):
        self.llm_port = MagicMock(spec=RewriteLLMPort)
        self.audit_port = MagicMock(spec=RewriteAuditPort)
        self.generator = RewriteCandidateGeneratorImpl(
            llm_port=self.llm_port,
            audit_port=self.audit_port,
        )
        self.model = ModelConfig(
            registry_key="test-model",
            model_id="test-model",
            display_name="Test Model",
            max_completion_tokens=100,
        )

    def test_placeholder_clue_produces_fresh_only_candidate(self):
        request = RewriteCandidateRequest(
            word="TEST",
            word_original="test",
            word_type="noun",
            theme="Test",
            current_definition="[Definiție negenerată]",
        )
        self.llm_port.generate.return_value = "O variantă fresh."

        results = self.generator.generate([request], model=self.model)

        self.assertIn("TEST", results)
        result = results["TEST"]
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].source, "generate")
        self.assertEqual(result.candidates[0].strategy_label, "fresh_only")
        self.assertEqual(result.candidates[0].definition, "O variantă fresh.")
        self.assertEqual(result.candidates[0].model_id, self.model.model_id)
        self.assertEqual(result.candidates[0].generated_by, self.model.display_name)

        self.llm_port.generate.assert_called_once_with(request, model_id=self.model.model_id)
        self.llm_port.rewrite.assert_not_called()

    def test_normal_clue_produces_rewrite_candidate(self):
        request = RewriteCandidateRequest(
            word="TEST",
            word_original="test",
            word_type="noun",
            theme="Test",
            current_definition="Definiție veche",
            wrong_guess="gresit",
            wrong_guesses=("gresit", "altul"),
            rating_feedback="prea vag",
            bad_example_definition="exemplu prost",
            bad_example_reason="e prost",
            failure_history=(("veche2", ("gresit2",)),),
            dex_definitions="dex def",
            canonical_examples=("canon1",),
        )
        self.llm_port.rewrite.return_value = "O variantă rescrisă."

        results = self.generator.generate([request], model=self.model)

        self.assertIn("TEST", results)
        result = results["TEST"]
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].source, "rewrite")
        self.assertEqual(result.candidates[0].strategy_label, "rewrite")
        self.assertEqual(result.candidates[0].definition, "O variantă rescrisă.")
        self.assertEqual(result.candidates[0].model_id, self.model.model_id)
        self.assertEqual(result.candidates[0].generated_by, self.model.display_name)

        self.llm_port.rewrite.assert_called_once_with(request, model_id=self.model.model_id)
        self.llm_port.generate.assert_not_called()

    def test_hybrid_mode_produces_rewrite_and_fresh_candidates(self):
        request = RewriteCandidateRequest(
            word="TEST",
            word_original="test",
            word_type="noun",
            theme="Test",
            current_definition="Definiție veche",
            use_hybrid_fresh_generate=True,
        )
        self.llm_port.rewrite.return_value = "O variantă rescrisă."
        self.llm_port.generate.return_value = "O variantă fresh."

        results = self.generator.generate([request], model=self.model)

        self.assertIn("TEST", results)
        result = results["TEST"]
        self.assertEqual(len(result.candidates), 2)

        self.assertEqual(result.candidates[0].source, "rewrite")
        self.assertEqual(result.candidates[0].strategy_label, "rewrite")
        self.assertEqual(result.candidates[0].definition, "O variantă rescrisă.")

        self.assertEqual(result.candidates[1].source, "generate")
        self.assertEqual(result.candidates[1].strategy_label, "fresh_generate")
        self.assertEqual(result.candidates[1].definition, "O variantă fresh.")

    def test_hybrid_mode_deduplicates_by_normalized_text(self):
        request = RewriteCandidateRequest(
            word="TEST",
            word_original="test",
            word_type="noun",
            theme="Test",
            current_definition="Definiție veche",
            use_hybrid_fresh_generate=True,
        )
        self.llm_port.rewrite.return_value = "O variantă IDENTICĂ. "
        self.llm_port.generate.return_value = "O   variantă   IDENTICĂ."

        results = self.generator.generate([request], model=self.model)

        self.assertIn("TEST", results)
        result = results["TEST"]
        # Only the first one should be kept (the rewrite)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].source, "rewrite")

    def test_guard_rejected_definition_returns_no_candidate_and_emits_audit(self):
        request = RewriteCandidateRequest(
            word="TEST",
            word_original="test",
            word_type="noun",
            theme="Test",
            current_definition="Definiție veche",
        )
        # Using a word that explicitly contains the answer, which validate_definition_text_with_details will catch
        self.llm_port.rewrite.return_value = "O variantă care conține test în ea."

        results = self.generator.generate([request], model=self.model)

        self.assertIn("TEST", results)
        result = results["TEST"]
        self.assertEqual(len(result.candidates), 0)
        self.assertIn("contains answer or family", result.rejection_reason)

        # Verify the audit port was called with a GuardRejection
        self.audit_port.candidate_rejected.assert_called_once()
        rejection = self.audit_port.candidate_rejected.call_args[0][0]
        self.assertEqual(rejection.word, "TEST")
        self.assertEqual(rejection.model_id, self.model.model_id)
        self.assertIn("contains answer or family", rejection.reason)
        self.assertEqual(rejection.definition_preview, "O variantă care conține test în ea.")

    def test_rewrite_diagnostics_preserves_last_structural_rejection_when_no_candidate(self):
        request = RewriteCandidateRequest(
            word="TEST",
            word_original="test",
            word_type="noun",
            theme="Test",
            current_definition="Definiție veche",
        )
        from rebus_generator.platform.llm.ai_clues import RewriteAttemptResult
        self.llm_port.rewrite.return_value = RewriteAttemptResult(
            definition="",
            last_rejection="prea scurt"
        )

        results = self.generator.generate([request], model=self.model)

        self.assertIn("TEST", results)
        result = results["TEST"]
        self.assertEqual(len(result.candidates), 0)
        # Verify the fallback rejection reason is extracted
        self.assertEqual(result.rejection_reason, "prea scurt")
