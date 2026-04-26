import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rebus_generator.domain.pipeline_state import (
    ClueScores,
    PuzzleAssessment,
    WorkingPuzzle,
    working_clue_from_entry,
)
from rebus_generator.domain.quality import QualityReport
from rebus_generator.platform.io.markdown_io import ClueEntry
from rebus_generator.platform.io.rust_bridge import Candidate
from rebus_generator.workflows.run_all.generate_attempt import (
    finalize_rewritten_attempt,
    finalize_titled_attempt,
    rescue_unresolved_generated_definitions,
)


def _quality_report() -> QualityReport:
    return QualityReport(
        score=1.0,
        word_count=1,
        average_length=4.0,
        average_rarity=1.0,
        two_letter_words=0,
        three_letter_words=0,
        high_rarity_words=0,
        uncommon_letter_words=0,
        friendly_words=1,
    )


def _candidate() -> Candidate:
    return Candidate(
        score=1.0,
        report=_quality_report(),
        template=[[True]],
        markdown="",
    )


def _puzzle(
    *,
    definition: str = "Definiție clară",
    assessment: PuzzleAssessment | None = None,
) -> WorkingPuzzle:
    clue = working_clue_from_entry(ClueEntry(1, "APA", "apă", definition))
    clue.current.assessment.verify_complete = True
    clue.current.assessment.rating_complete = True
    clue.current.assessment.verified = True
    clue.current.assessment.scores = ClueScores(
        semantic_exactness=8,
        answer_targeting=7,
        creativity=6,
        rebus_score=7,
    )
    return WorkingPuzzle(
        title="",
        size=1,
        grid=[["A"]],
        horizontal_clues=[clue],
        vertical_clues=[],
        assessment=assessment or PuzzleAssessment(
            definition_score=15,
            verified_count=1,
            total_clues=1,
            scores_complete=True,
            min_rebus=7,
        ),
    )


class GenerateAttemptTests(unittest.TestCase):
    @patch("rebus_generator.workflows.run_all.generate_attempt.apply_scored_canonical_fallbacks")
    def test_unresolved_rescue_applies_canonical_fallback_before_dex_rescue(self, mock_fallback):
        puzzle = _puzzle(definition="[NECLAR]")

        def apply_fallback(**kwargs):
            kwargs["target_puzzle"].horizontal_clues[0].current.definition = "Sens canonic"

        mock_fallback.side_effect = apply_fallback
        dex = SimpleNamespace(
            uncertain_short_definitions=lambda: [],
            get=Mock(side_effect=AssertionError("DEX should not run after canonical fallback")),
        )

        rescue_unresolved_generated_definitions(
            puzzle=puzzle,
            puzzle_identity="p1",
            dex=dex,
            client=object(),
            runtime=object(),
            multi_model=True,
            seed_parts=("seed",),
        )

        self.assertEqual("Sens canonic", puzzle.horizontal_clues[0].current.definition)
        dex.get.assert_not_called()
        mock_fallback.assert_called_once()

    @patch("rebus_generator.workflows.run_all.generate_attempt.validate_definition_text", return_value=None)
    @patch("rebus_generator.workflows.run_all.generate_attempt.apply_scored_canonical_fallbacks")
    def test_dex_rescue_strips_labels_and_dedupes_candidates(self, mock_fallback, mock_validate):
        puzzle = _puzzle(definition="[NECLAR]")
        dex = SimpleNamespace(
            uncertain_short_definitions=lambda: [
                {"word": "APA", "definition": "Definiție directă DEX: Lichid transparent"},
            ],
            get=lambda *_args: "\n- Sens bază: Lichid transparent\n- Sens bază: Râu curgător",
        )

        rescue_unresolved_generated_definitions(
            puzzle=puzzle,
            puzzle_identity="p1",
            dex=dex,
            client=object(),
            runtime=object(),
            multi_model=True,
            seed_parts=("seed",),
        )

        self.assertEqual("Lichid transparent", puzzle.horizontal_clues[0].current.definition)
        mock_validate.assert_called_once_with("APA", "Lichid transparent")
        mock_fallback.assert_called_once()

    @patch("rebus_generator.workflows.run_all.generate_attempt.apply_scored_canonical_fallbacks")
    @patch("rebus_generator.workflows.run_all.generate_attempt.score_puzzle_state")
    def test_rewritten_attempt_with_complete_scores_routes_to_title(self, mock_score, _mock_fallback):
        assessment = PuzzleAssessment(
            definition_score=15,
            verified_count=1,
            total_clues=1,
            scores_complete=True,
            min_rebus=7,
        )
        mock_score.return_value = assessment
        puzzle = _puzzle(assessment=assessment)
        rewrite_result = SimpleNamespace(initial_passed=1, final_passed=1, total=1)

        decision, best = finalize_rewritten_attempt(
            puzzle=puzzle,
            puzzle_identity="p1",
            candidate=_candidate(),
            best_prepared=None,
            rewrite_result=rewrite_result,
            size=5,
            index=1,
            attempt_index=1,
            effective_attempts=2,
            client=object(),
            runtime=object(),
            multi_model=True,
        )

        self.assertEqual("title", decision.next_stage)
        self.assertEqual("verified=1/1", decision.detail)
        self.assertIsNone(best)

    @patch("rebus_generator.workflows.run_all.generate_attempt.apply_scored_canonical_fallbacks")
    @patch("rebus_generator.workflows.run_all.generate_attempt.score_puzzle_state")
    def test_rewritten_attempt_with_incomplete_scores_prepares_best_and_retries(self, mock_score, _mock_fallback):
        assessment = PuzzleAssessment(
            definition_score=4,
            verified_count=0,
            total_clues=1,
            scores_complete=False,
            verify_incomplete_count=1,
            incomplete_words=["APA"],
        )
        mock_score.return_value = assessment
        puzzle = _puzzle(assessment=assessment)
        rewrite_result = SimpleNamespace(initial_passed=0, final_passed=0, total=1)

        decision, best = finalize_rewritten_attempt(
            puzzle=puzzle,
            puzzle_identity="p1",
            candidate=_candidate(),
            best_prepared=None,
            rewrite_result=rewrite_result,
            size=5,
            index=1,
            attempt_index=1,
            effective_attempts=2,
            client=object(),
            runtime=object(),
            multi_model=True,
        )

        self.assertEqual("fill_grid", decision.next_stage)
        self.assertEqual("retry=2/2", decision.detail)
        self.assertIs(decision.prepared, best)
        self.assertFalse(best.assessment.scores_complete)

    def test_titled_attempt_publishes_when_best_prepared_is_publishable(self):
        puzzle = _puzzle()

        decision, best = finalize_titled_attempt(
            title="Râuri",
            title_score=8,
            puzzle=puzzle,
            candidate=_candidate(),
            best_prepared=None,
            first_passed=1,
            final_passed=1,
            total=1,
            size=5,
            attempt_index=1,
            effective_attempts=2,
            client=object(),
            runtime=object(),
        )

        self.assertEqual("publish", decision.next_stage)
        self.assertEqual("title=Râuri", decision.detail)
        self.assertIs(decision.prepared, best)
        self.assertEqual("Râuri", best.title)

    def test_titled_attempt_retries_when_quality_gate_fails_before_final_attempt(self):
        puzzle = _puzzle(
            assessment=PuzzleAssessment(
                definition_score=0,
                verified_count=0,
                total_clues=1,
                scores_complete=True,
            )
        )

        decision, best = finalize_titled_attempt(
            title="Slab",
            title_score=3,
            puzzle=puzzle,
            candidate=_candidate(),
            best_prepared=None,
            first_passed=0,
            final_passed=0,
            total=1,
            size=5,
            attempt_index=1,
            effective_attempts=2,
            client=object(),
            runtime=object(),
        )

        self.assertEqual("fill_grid", decision.next_stage)
        self.assertEqual("retry=2/2", decision.detail)
        self.assertIs(decision.prepared, best)

    def test_final_titled_attempt_quality_failure_raises_with_detail(self):
        puzzle = _puzzle(
            assessment=PuzzleAssessment(
                definition_score=0,
                verified_count=0,
                total_clues=1,
                scores_complete=False,
                verify_incomplete_count=1,
                incomplete_words=["APA"],
            )
        )

        with self.assertRaisesRegex(RuntimeError, "no consensus-verified clue.*incomplete pair evaluation"):
            finalize_titled_attempt(
                title="Slab",
                title_score=3,
                puzzle=puzzle,
                candidate=_candidate(),
                best_prepared=None,
                first_passed=0,
                final_passed=0,
                total=1,
                size=5,
                attempt_index=2,
                effective_attempts=2,
                client=object(),
                runtime=object(),
            )


if __name__ == "__main__":
    unittest.main()
