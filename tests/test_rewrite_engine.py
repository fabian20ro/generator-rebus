import unittest
from unittest.mock import MagicMock, patch

from generator.core.pipeline_state import (
    ClueAssessment,
    ClueCandidateVersion,
    ClueScores,
    WorkingClue,
    WorkingPuzzle,
    all_working_clues,
)
from generator.core.rewrite_engine import run_rewrite_loop


def _make_puzzle() -> WorkingPuzzle:
    clue = WorkingClue(
        row_number=1,
        word_normalized="FIRISOR",
        word_original="firișor",
        current=ClueCandidateVersion(
            definition="Diminutiv al lui fir.",
            round_index=0,
            source="import",
            assessment=ClueAssessment(verified=False),
        ),
        history=[],
    )
    clue.history = [clue.current]
    return WorkingPuzzle(
        title="Test",
        size=7,
        grid=[],
        horizontal_clues=[clue],
        vertical_clues=[],
    )


class RewriteEngineTests(unittest.TestCase):
    def _session_mock(self):
        session = MagicMock()
        writer = MagicMock(display_name="gpt-oss-20b", model_id="writer-model")
        evaluator = MagicMock(display_name="eurollm-22b", model_id="evaluator-model")
        session.activate_initial_evaluator.return_value = writer
        session.alternate.return_value = evaluator
        session.switch_count = 0
        return session

    def _evaluation_side_effect(self, score_by_definition: dict[str, tuple[bool, int, int]]):
        def _verify(puzzle, _client, skip_words=None, **_kwargs):
            for clue in all_working_clues(puzzle):
                if skip_words and clue.word_normalized in skip_words:
                    continue
                verdict = score_by_definition.get(clue.current.definition, (False, 3, 3))[0]
                clue.current.assessment.verified = verdict
                clue.current.assessment.verify_candidates = (
                    [clue.word_normalized] if verdict else ["ALTCEVA"]
                )
                clue.current.assessment.wrong_guess = "" if verdict else "ALTCEVA"
            return 0, len(all_working_clues(puzzle))

        def _rate(puzzle, _client, skip_words=None, **_kwargs):
            for clue in all_working_clues(puzzle):
                if skip_words and clue.word_normalized in skip_words:
                    continue
                verified, semantic, rebus = score_by_definition.get(
                    clue.current.definition,
                    (False, 3, 3),
                )
                clue.current.assessment.verified = verified
                clue.current.assessment.feedback = clue.current.definition
                clue.current.assessment.scores = ClueScores(
                    semantic_exactness=semantic,
                    answer_targeting=rebus,
                    creativity=6,
                    rebus_score=rebus,
                )
            return 0.0, 0.0, len(all_working_clues(puzzle))

        return _verify, _rate

    @patch("generator.core.rewrite_engine.audit")
    @patch("generator.core.rewrite_engine._update_best_clue_version")
    @patch("generator.core.rewrite_engine.rate_working_puzzle")
    @patch("generator.core.rewrite_engine.verify_working_puzzle")
    @patch("generator.core.rewrite_engine.rewrite_definition")
    @patch("generator.core.rewrite_engine.LmRuntime")
    def test_unresolved_short_definition_emits_terminal_audit(
        self,
        mock_session_cls,
        mock_rewrite_definition,
        mock_verify,
        mock_rate,
        mock_update_best,
        mock_audit,
    ):
        session = MagicMock()
        session.activate_initial_evaluator.return_value = MagicMock(display_name="gpt-oss-20b")
        session.alternate.return_value = MagicMock(display_name="gpt-oss-20b")
        session.switch_count = 0
        mock_session_cls.return_value = session

        mock_rewrite_definition.return_value = "Diminutiv al lui fir."
        dex = MagicMock()
        dex.get.return_value = ""
        dex.uncertain_short_definitions.return_value = [
            {"word": "FIRISOR", "definition": "Diminutiv al lui fir."}
        ]
        puzzle = _make_puzzle()

        result = run_rewrite_loop(
            puzzle,
            client=MagicMock(),
            rounds=1,
            theme="Test",
            multi_model=False,
            dex=dex,
        )

        self.assertIn("FIRISOR", result.outcomes)
        self.assertEqual("rewrite_no_change", result.outcomes["FIRISOR"].terminal_reason)
        mock_audit.assert_any_call(
            "dex_short_definition_not_included_in_redefinire",
            component="rewrite_engine",
            payload={
                "word": "FIRISOR",
                "definition": "Diminutiv al lui fir.",
                "reason": "rewrite_no_change",
            },
        )

    @patch("generator.core.rewrite_engine.LmRuntime")
    @patch("generator.core.rewrite_engine.generate_definition")
    @patch("generator.core.rewrite_engine.rewrite_definition")
    @patch("generator.core.rewrite_engine.rate_working_puzzle")
    @patch("generator.core.rewrite_engine.verify_working_puzzle")
    def test_hybrid_uses_both_branches_for_failed_clue_and_picks_fresh_better(
        self,
        mock_verify,
        mock_rate,
        mock_rewrite_definition,
        mock_generate_definition,
        mock_session_cls,
    ):
        mock_session_cls.return_value = self._session_mock()
        puzzle = _make_puzzle()
        mock_rewrite_definition.return_value = "variantă rewrite"
        mock_generate_definition.return_value = "variantă fresh"
        verify_side_effect, rate_side_effect = self._evaluation_side_effect(
            {
                "Diminutiv al lui fir.": (False, 3, 3),
                "variantă rewrite": (True, 7, 6),
                "variantă fresh": (True, 9, 8),
            }
        )
        mock_verify.side_effect = verify_side_effect
        mock_rate.side_effect = rate_side_effect
        dex = MagicMock()
        dex.get.return_value = ""
        dex.uncertain_short_definitions.return_value = []

        result = run_rewrite_loop(
            puzzle,
            client=MagicMock(),
            rounds=1,
            theme="Test",
            multi_model=False,
            dex=dex,
            hybrid_deanchor=True,
        )

        self.assertEqual("variantă fresh", puzzle.horizontal_clues[0].current.definition)
        self.assertEqual("fresh_generate", result.outcomes["FIRISOR"].selected_strategy)
        self.assertEqual(1, mock_rewrite_definition.call_count)
        self.assertEqual(1, mock_generate_definition.call_count)

    @patch("generator.core.rewrite_engine.LmRuntime")
    @patch("generator.core.rewrite_engine.generate_definition")
    @patch("generator.core.rewrite_engine.rewrite_definition")
    @patch("generator.core.rewrite_engine.rate_working_puzzle")
    @patch("generator.core.rewrite_engine.verify_working_puzzle")
    def test_hybrid_uses_both_branches_for_low_rebus_verified_clue(
        self,
        mock_verify,
        mock_rate,
        mock_rewrite_definition,
        mock_generate_definition,
        mock_session_cls,
    ):
        mock_session_cls.return_value = self._session_mock()
        puzzle = _make_puzzle()
        puzzle.horizontal_clues[0].current.assessment = ClueAssessment(
            verified=True,
            scores=ClueScores(semantic_exactness=8, answer_targeting=4, creativity=5, rebus_score=4),
        )
        mock_rewrite_definition.return_value = "variantă rewrite"
        mock_generate_definition.return_value = "variantă fresh"
        verify_side_effect, rate_side_effect = self._evaluation_side_effect(
            {
                "Diminutiv al lui fir.": (True, 8, 4),
                "variantă rewrite": (True, 8, 6),
                "variantă fresh": (True, 9, 7),
            }
        )
        mock_verify.side_effect = verify_side_effect
        mock_rate.side_effect = rate_side_effect
        dex = MagicMock()
        dex.get.return_value = ""
        dex.uncertain_short_definitions.return_value = []

        run_rewrite_loop(
            puzzle,
            client=MagicMock(),
            rounds=1,
            theme="Test",
            multi_model=False,
            dex=dex,
            hybrid_deanchor=True,
        )

        self.assertEqual(1, mock_rewrite_definition.call_count)
        self.assertEqual(1, mock_generate_definition.call_count)

    @patch("generator.core.rewrite_engine.LmRuntime")
    @patch("generator.core.rewrite_engine.generate_definition")
    @patch("generator.core.rewrite_engine.rewrite_definition")
    @patch("generator.core.rewrite_engine.rate_working_puzzle")
    @patch("generator.core.rewrite_engine.verify_working_puzzle")
    def test_verified_rebus_five_plus_uses_rewrite_only(
        self,
        mock_verify,
        mock_rate,
        mock_rewrite_definition,
        mock_generate_definition,
        mock_session_cls,
    ):
        mock_session_cls.return_value = self._session_mock()
        puzzle = _make_puzzle()
        puzzle.horizontal_clues[0].current.assessment = ClueAssessment(
            verified=True,
            scores=ClueScores(semantic_exactness=8, answer_targeting=5, creativity=5, rebus_score=5),
        )
        mock_rewrite_definition.return_value = "variantă rewrite"
        verify_side_effect, rate_side_effect = self._evaluation_side_effect(
            {
                "Diminutiv al lui fir.": (True, 8, 5),
                "variantă rewrite": (True, 9, 6),
            }
        )
        mock_verify.side_effect = verify_side_effect
        mock_rate.side_effect = rate_side_effect
        dex = MagicMock()
        dex.get.return_value = ""
        dex.uncertain_short_definitions.return_value = []

        result = run_rewrite_loop(
            puzzle,
            client=MagicMock(),
            rounds=1,
            theme="Test",
            multi_model=False,
            dex=dex,
            hybrid_deanchor=True,
        )

        self.assertEqual(1, mock_rewrite_definition.call_count)
        self.assertEqual(0, mock_generate_definition.call_count)
        self.assertEqual("rewrite_only", result.outcomes["FIRISOR"].selected_strategy)

    @patch("generator.core.rewrite_engine.LmRuntime")
    @patch("generator.core.rewrite_engine.generate_definition")
    @patch("generator.core.rewrite_engine.rewrite_definition")
    @patch("generator.core.rewrite_engine.rate_working_puzzle")
    @patch("generator.core.rewrite_engine.verify_working_puzzle")
    def test_hybrid_runs_only_once_per_clue_then_falls_back_to_rewrite(
        self,
        mock_verify,
        mock_rate,
        mock_rewrite_definition,
        mock_generate_definition,
        mock_session_cls,
    ):
        mock_session_cls.return_value = self._session_mock()
        puzzle = _make_puzzle()
        mock_rewrite_definition.side_effect = ["slab rewrite", "bun rewrite"]
        mock_generate_definition.return_value = "slab fresh"
        verify_side_effect, rate_side_effect = self._evaluation_side_effect(
            {
                "Diminutiv al lui fir.": (False, 3, 3),
                "slab rewrite": (False, 4, 4),
                "slab fresh": (False, 4, 4),
                "bun rewrite": (True, 9, 7),
            }
        )
        mock_verify.side_effect = verify_side_effect
        mock_rate.side_effect = rate_side_effect
        dex = MagicMock()
        dex.get.return_value = ""
        dex.uncertain_short_definitions.return_value = []

        result = run_rewrite_loop(
            puzzle,
            client=MagicMock(),
            rounds=2,
            theme="Test",
            multi_model=False,
            dex=dex,
            hybrid_deanchor=True,
        )

        self.assertEqual(2, mock_rewrite_definition.call_count)
        self.assertEqual(1, mock_generate_definition.call_count)
        self.assertEqual("bun rewrite", puzzle.horizontal_clues[0].current.definition)
        self.assertEqual("rewrite_only", result.outcomes["FIRISOR"].selected_strategy)

    @patch("generator.core.rewrite_engine.LmRuntime")
    @patch("generator.core.rewrite_engine.generate_definition")
    @patch("generator.core.rewrite_engine.rewrite_definition")
    @patch("generator.core.rewrite_engine.rate_working_puzzle")
    @patch("generator.core.rewrite_engine.verify_working_puzzle")
    def test_hybrid_keeps_valid_branch_when_other_branch_is_unchanged(
        self,
        mock_verify,
        mock_rate,
        mock_rewrite_definition,
        mock_generate_definition,
        mock_session_cls,
    ):
        mock_session_cls.return_value = self._session_mock()
        puzzle = _make_puzzle()
        mock_rewrite_definition.return_value = "Diminutiv al lui fir."
        mock_generate_definition.return_value = "variantă fresh"
        verify_side_effect, rate_side_effect = self._evaluation_side_effect(
            {
                "Diminutiv al lui fir.": (False, 3, 3),
                "variantă fresh": (True, 9, 8),
            }
        )
        mock_verify.side_effect = verify_side_effect
        mock_rate.side_effect = rate_side_effect
        dex = MagicMock()
        dex.get.return_value = ""
        dex.uncertain_short_definitions.return_value = []

        result = run_rewrite_loop(
            puzzle,
            client=MagicMock(),
            rounds=1,
            theme="Test",
            multi_model=False,
            dex=dex,
            hybrid_deanchor=True,
        )

        self.assertEqual("variantă fresh", puzzle.horizontal_clues[0].current.definition)
        self.assertEqual("fresh_only", result.outcomes["FIRISOR"].selected_strategy)

    @patch("generator.core.rewrite_engine.LmRuntime")
    @patch("generator.core.rewrite_engine.generate_definition")
    @patch("generator.core.rewrite_engine.rewrite_definition")
    @patch("generator.core.rewrite_engine.rate_working_puzzle")
    @patch("generator.core.rewrite_engine.verify_working_puzzle")
    def test_hybrid_leaves_clue_unchanged_when_both_branches_no_op(
        self,
        mock_verify,
        mock_rate,
        mock_rewrite_definition,
        mock_generate_definition,
        mock_session_cls,
    ):
        mock_session_cls.return_value = self._session_mock()
        puzzle = _make_puzzle()
        original_definition = puzzle.horizontal_clues[0].current.definition
        mock_rewrite_definition.return_value = original_definition
        mock_generate_definition.return_value = original_definition
        verify_side_effect, rate_side_effect = self._evaluation_side_effect(
            {
                original_definition: (False, 3, 3),
            }
        )
        mock_verify.side_effect = verify_side_effect
        mock_rate.side_effect = rate_side_effect
        dex = MagicMock()
        dex.get.return_value = ""
        dex.uncertain_short_definitions.return_value = []

        result = run_rewrite_loop(
            puzzle,
            client=MagicMock(),
            rounds=1,
            theme="Test",
            multi_model=False,
            dex=dex,
            hybrid_deanchor=True,
        )

        self.assertEqual(original_definition, puzzle.horizontal_clues[0].current.definition)
        self.assertFalse(result.outcomes["FIRISOR"].changed_definition)


if __name__ == "__main__":
    unittest.main()
