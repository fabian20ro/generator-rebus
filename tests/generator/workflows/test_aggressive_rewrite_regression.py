import unittest
from unittest.mock import MagicMock, patch

from rebus_generator.platform.llm.models import PRIMARY_MODEL
from rebus_generator.domain.pipeline_state import (
    ClueAssessment,
    ClueCandidateVersion,
    ClueScores,
    WorkingClue,
    WorkingPuzzle,
    all_working_clues,
)
from rebus_generator.workflows.redefine.rewrite_rounds import rewrite_session_prepare_round
from rebus_generator.workflows.run_all.rewrite_units import RunAllRewriteSession


def _make_clue(word: str, rebus_score: int, verified: bool = True, semantic_score: int = 8) -> WorkingClue:
    clue = WorkingClue(
        row_number=1,
        word_normalized=word,
        word_original=word.lower(),
        current=ClueCandidateVersion(
            definition=f"Def for {word}",
            round_index=0,
            source="import",
            assessment=ClueAssessment(
                verified=verified,
                scores=ClueScores(
                    semantic_exactness=semantic_score,
                    answer_targeting=rebus_score,
                    rebus_score=rebus_score,
                    language_integrity=10,
                )
            ),
        ),
        history=[],
    )
    clue.history = [clue.current]
    return clue


class AggressiveRewriteRegressionTests(unittest.TestCase):
    def test_run_all_aggressive_staging_and_plateau_guard(self):
        """Verify that run_all rewrite session uses 3-stage targets and guards plateau."""
        puzzle = WorkingPuzzle(
            title="Test", size=7, grid=[],
            horizontal_clues=[
                _make_clue("LOW", 2),   # Needs fix (rebus 2 < 6)
                _make_clue("MID", 6),   # Already at target 6, should wait for next stage
            ],
            vertical_clues=[],
        )
        
        runtime = MagicMock()
        runtime.primary = PRIMARY_MODEL
        runtime.multi_model = False
        
        session = RunAllRewriteSession(
            puzzle=puzzle, client=MagicMock(), rounds=20, theme="Test",
            multi_model=False, dex=MagicMock(), verify_candidates=1,
            hybrid_deanchor=False, runtime=runtime
        )
        session.build_initial_outcomes()
        
        # Stage 1: current_min is 2 (< 6). Target should be 6.
        session.prepare_round()
        self.assertEqual(session.current_round.round_min_rebus, 6)
        
        # Only LOW should be selected because MID (6) is already at target 6
        self.assertIn("LOW", session.current_round.clues_by_word)
        self.assertNotIn("MID", session.current_round.clues_by_word)

        # Plateau test: If we have multiple rounds at min=2, it should NOT plateau
        session.min_rebus_history = [2, 2, 2, 2, 2, 2, 2, 2]
        session.round_index = 5
        session.phase = "prepare_round"
        session.prepare_round()
        self.assertNotEqual(session.phase, "done")

        # Now test plateau when min >= 6
        # MUST update clues to have scores >= 6 so current_min >= 6
        for clue in all_working_clues(session.puzzle):
            clue.current.assessment.scores.rebus_score = 6
            
        session.min_rebus_history = [6, 6, 6, 6, 6, 6, 6, 6]
        session.round_index = 5
        session.prepare_round()
        self.assertEqual(session.phase, "done")

    def test_redefine_aggressive_staging_and_no_limit(self):
        """Verify standalone redefine uses 3-stage targets and removed candidate limits."""
        # Create 20 low-scoring clues
        clues = [_make_clue(f"W{i}", 2) for i in range(20)]
        puzzle = WorkingPuzzle(
            title="Test", size=7, grid=[],
            horizontal_clues=clues,
            vertical_clues=[],
        )
        
        session = MagicMock()
        session.puzzle = puzzle
        session.rounds = 7
        session.round_index = 1
        session.min_rebus_history = []
        session.stuck_words = set()
        session.outcomes = {c.word_normalized: MagicMock() for c in clues}
        session.initialized = True
        session.final_result = None
        session.multi_model = False
        session.hybrid_deanchor = False
        session.hybrid_attempted_words = set()
        
        # CRITICAL: Patch _build_pending_candidates to avoid real LLM calls/hangs
        with patch("rebus_generator.workflows.redefine.rewrite_rounds.log"), \
             patch("rebus_generator.workflows.redefine.rewrite_rounds.finish_rewrite_session"), \
             patch("rebus_generator.workflows.redefine.rewrite_rounds._build_pending_candidates") as mock_build:
            
            mock_build.return_value = ([], False, "") # No candidates, no error
            
            round_state = rewrite_session_prepare_round(session)
            
            # Check target staging: min is 2, so target must be 6
            self.assertIsNotNone(round_state)
            self.assertEqual(round_state.round_min_rebus, 6)
            
            # Check limit removal: All 20 should be selected (previously limited to 12)
            self.assertEqual(len(round_state.candidates), 20)

    def test_no_quarantine_in_failures(self):
        """Verify that words are no longer added to stuck_words after failures."""
        puzzle = WorkingPuzzle(title="T", size=7, grid=[], horizontal_clues=[_make_clue("FAIL", 2)], vertical_clues=[])
        runtime = MagicMock()
        runtime.primary = PRIMARY_MODEL
        
        session = RunAllRewriteSession(
            puzzle=puzzle, client=MagicMock(), rounds=7, theme="T",
            multi_model=False, dex=MagicMock(), verify_candidates=1,
            hybrid_deanchor=False, runtime=runtime
        )
        session.build_initial_outcomes()
        
        # Simulate 10 failures (previously capped at 5)
        for _ in range(10):
            session._note_generation_failure("FAIL", had_error=True, rejection_reason="test")
            
        self.assertNotIn("FAIL", session.stuck_words)
        self.assertEqual(session.consecutive_failures["FAIL"], 10)
