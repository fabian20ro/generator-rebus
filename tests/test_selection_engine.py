import unittest

from generator.core.pipeline_state import (
    ClueAssessment,
    ClueCandidateVersion,
    ClueScores,
)
from generator.core.selection_engine import choose_clue_version, clue_rank


def _version(
    definition: str,
    *,
    verified: bool,
    semantic: int,
    rebus: int,
    language: int = 10,
):
    return ClueCandidateVersion(
        definition=definition,
        round_index=1,
        source="test",
        assessment=ClueAssessment(
            verified=verified,
            scores=ClueScores(
                semantic_exactness=semantic,
                answer_targeting=rebus,
                rebus_score=rebus,
                language_integrity=language,
            ),
        ),
    )


class SelectionEngineTests(unittest.TestCase):
    def test_verified_candidate_outranks_unverified_higher_score(self):
        verified = _version("Definiție exactă", verified=True, semantic=8, rebus=7)
        flashy = _version("Definiție mai spectaculoasă", verified=False, semantic=10, rebus=10)

        chosen, decision = choose_clue_version(verified, flashy)

        self.assertIs(chosen, verified)
        self.assertEqual("deterministic_rank", decision.reason)

    def test_clue_rank_prefers_verified_first(self):
        verified_rank = clue_rank(_version("A", verified=True, semantic=7, rebus=5))
        unverified_rank = clue_rank(_version("B", verified=False, semantic=10, rebus=10))

        self.assertGreater(verified_rank, unverified_rank)


if __name__ == "__main__":
    unittest.main()
