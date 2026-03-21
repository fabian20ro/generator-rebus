import unittest
from unittest.mock import MagicMock, patch

from generator.core.pipeline_state import (
    ClueAssessment,
    ClueCandidateVersion,
    WorkingClue,
    WorkingPuzzle,
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
    @patch("generator.core.rewrite_engine.audit")
    @patch("generator.core.rewrite_engine._update_best_clue_version")
    @patch("generator.core.rewrite_engine.rate_working_puzzle")
    @patch("generator.core.rewrite_engine.verify_working_puzzle")
    @patch("generator.core.rewrite_engine.rewrite_definition")
    @patch("generator.core.rewrite_engine.ModelSession")
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


if __name__ == "__main__":
    unittest.main()
