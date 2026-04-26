import unittest

from rebus_generator.platform.io.markdown_io import ClueEntry
from rebus_generator.platform.llm.ai_clues import DefinitionRating
from rebus_generator.platform.llm.models import PRIMARY_MODEL, SECONDARY_MODEL
from rebus_generator.domain.pipeline_state import working_clue_from_entry
from rebus_generator.workflows.generate.definition_evaluation import (
    finalize_pair_rating,
    finalize_pair_verification,
)


MODEL_ORDER = [PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id]
MODEL_LABEL = "gemma + eurollm"


def _clue(word: str = "ARACI"):
    return working_clue_from_entry(
        ClueEntry(1, word, word.lower(), "Bețe de sprijin pentru vie")
    )


def _rating(
    semantic: int = 8,
    guessability: int = 6,
    feedback: str = "",
    creativity: int = 6,
) -> DefinitionRating:
    return DefinitionRating(
        semantic_score=semantic,
        guessability_score=guessability,
        feedback=feedback,
        creativity_score=creativity,
    )


class DefinitionEvaluationTests(unittest.TestCase):
    def test_pair_verification_verifies_when_both_models_include_answer(self):
        clue = _clue()
        clue.current.assessment.verify_votes = {
            PRIMARY_MODEL.model_id: ["PARI", "ARACI"],
            SECONDARY_MODEL.model_id: ["ARACI"],
        }

        assessed = finalize_pair_verification(
            [clue],
            model_order=MODEL_ORDER,
            model_label=MODEL_LABEL,
        )[0].current.assessment

        self.assertTrue(assessed.verify_complete)
        self.assertTrue(assessed.verified)
        self.assertEqual("", assessed.wrong_guess)
        self.assertIsNone(assessed.failure_reason)

    def test_pair_verification_handles_single_negative_vote_without_keyerror(self):
        clue = _clue()
        clue.current.assessment.verify_votes = {
            PRIMARY_MODEL.model_id: ["PARI"],
        }

        assessed = finalize_pair_verification(
            [clue],
            model_order=MODEL_ORDER,
            model_label=MODEL_LABEL,
        )[0].current.assessment

        self.assertTrue(assessed.verify_complete)
        self.assertFalse(assessed.verified)
        self.assertEqual("PARI", assessed.wrong_guess)
        self.assertEqual("wrong_guess", assessed.failure_reason.kind)  # type: ignore[union-attr]

    def test_pair_verification_stays_incomplete_with_one_positive_vote(self):
        clue = _clue()
        clue.current.assessment.verify_votes = {
            PRIMARY_MODEL.model_id: ["ARACI"],
        }

        assessed = finalize_pair_verification(
            [clue],
            model_order=MODEL_ORDER,
            model_label=MODEL_LABEL,
        )[0].current.assessment

        self.assertFalse(assessed.verify_complete)
        self.assertFalse(assessed.verified)
        self.assertEqual(["ARACI"], assessed.verify_candidates)
        self.assertEqual("incomplete_pair", assessed.failure_reason.kind)  # type: ignore[union-attr]

    def test_pair_verification_marks_related_form_negative(self):
        clue = working_clue_from_entry(
            ClueEntry(1, "NEINCEPUT", "neînceput", "Care nu a început încă")
        )
        clue.current.assessment.verify_votes = {
            PRIMARY_MODEL.model_id: ["INCEPUT"],
        }

        assessed = finalize_pair_verification(
            [clue],
            model_order=MODEL_ORDER,
            model_label=MODEL_LABEL,
        )[0].current.assessment

        self.assertTrue(assessed.verify_complete)
        self.assertFalse(assessed.verified)
        self.assertTrue(assessed.form_mismatch)
        self.assertEqual("related_form", assessed.failure_reason.kind)  # type: ignore[union-attr]

    def test_pair_rating_uses_pair_consensus_for_two_votes(self):
        clue = _clue()
        clue.current.assessment.rating_votes = {
            PRIMARY_MODEL.model_id: _rating(),
            SECONDARY_MODEL.model_id: _rating(),
        }

        finalize_pair_rating([clue], model_order=MODEL_ORDER, model_label=MODEL_LABEL)

        assessed = clue.current.assessment
        self.assertTrue(assessed.rating_complete)
        self.assertEqual("pair_consensus", assessed.rating_resolution)
        self.assertEqual(MODEL_ORDER, assessed.rating_resolution_models)
        self.assertEqual(8, assessed.scores.semantic_exactness)
        self.assertEqual(6, assessed.scores.answer_targeting)
        self.assertEqual(6, assessed.scores.rebus_score)

    def test_pair_rating_uses_single_model_fallback_for_one_vote(self):
        clue = _clue()
        clue.current.assessment.rating_votes = {
            PRIMARY_MODEL.model_id: _rating(feedback="ok"),
        }

        finalize_pair_rating([clue], model_order=MODEL_ORDER, model_label=MODEL_LABEL)

        assessed = clue.current.assessment
        self.assertTrue(assessed.rating_complete)
        self.assertEqual("single_model_fallback", assessed.rating_resolution)
        self.assertEqual([PRIMARY_MODEL.model_id], assessed.rating_resolution_models)
        self.assertEqual(8, assessed.scores.semantic_exactness)
        self.assertEqual(6, assessed.scores.answer_targeting)
        self.assertEqual(6, assessed.scores.rebus_score)
        self.assertEqual("feedback", assessed.failure_reason.kind)  # type: ignore[union-attr]

    def test_pair_rating_stays_incomplete_without_usable_votes(self):
        clue = _clue()

        finalize_pair_rating([clue], model_order=MODEL_ORDER, model_label=MODEL_LABEL)

        assessed = clue.current.assessment
        self.assertFalse(assessed.rating_complete)
        self.assertEqual("", assessed.rating_resolution)
        self.assertEqual([], assessed.rating_resolution_models)
        self.assertIsNone(assessed.scores.semantic_exactness)
        self.assertEqual("unrated", assessed.failure_reason.kind)  # type: ignore[union-attr]

    def test_pair_rating_feedback_dedupe_keeps_model_order(self):
        duplicate = _clue()
        duplicate.current.assessment.rating_votes = {
            PRIMARY_MODEL.model_id: _rating(feedback="clar"),
            SECONDARY_MODEL.model_id: _rating(feedback="clar"),
        }
        ordered = _clue()
        ordered.current.assessment.rating_votes = {
            PRIMARY_MODEL.model_id: _rating(feedback="primar"),
            SECONDARY_MODEL.model_id: _rating(feedback="secundar"),
        }

        finalize_pair_rating([duplicate, ordered], model_order=MODEL_ORDER, model_label=MODEL_LABEL)

        self.assertEqual("clar", duplicate.current.assessment.feedback)
        self.assertEqual("primar / secundar", ordered.current.assessment.feedback)


if __name__ == "__main__":
    unittest.main()
