import unittest

from rebus_generator.domain.pipeline_state import (
    ClueAssessment,
    ClueCandidateVersion,
    ClueScores,
    PuzzleAssessment,
)
from rebus_generator.domain.selection_engine import (
    choose_clue_version,
    choose_puzzle_assessment,
    clue_rank,
    stable_tie_rng,
)


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

    def test_equivalent_normalized_definitions_prefer_verified_version(self):
        first = _version("Definiție exactă", verified=False, semantic=10, rebus=10)
        second = _version("  definiție exactă ", verified=True, semantic=8, rebus=7)

        chosen, decision = choose_clue_version(first, second)

        self.assertIs(chosen, second)
        self.assertEqual("equivalent_after_normalization", decision.reason)
        self.assertEqual("B", decision.winner)

    def test_equal_rank_clue_choice_is_stable_for_same_seed(self):
        first = _version("Definiție A", verified=True, semantic=8, rebus=8)
        second = _version("Definiție B", verified=True, semantic=8, rebus=8)

        chosen_one, decision_one = choose_clue_version(
            first,
            second,
            rng=stable_tie_rng("word", "same-seed"),
        )
        chosen_two, decision_two = choose_clue_version(
            first,
            second,
            rng=stable_tie_rng("word", "same-seed"),
        )

        self.assertEqual(chosen_one.definition, chosen_two.definition)
        self.assertEqual("random_equal_tie", decision_one.reason)
        self.assertEqual(decision_one.winner, decision_two.winner)

    def test_equal_rank_clue_choice_can_flip_for_different_seeds(self):
        first = _version("Definiție A", verified=True, semantic=8, rebus=8)
        second = _version("Definiție B", verified=True, semantic=8, rebus=8)

        winners = {
            choose_clue_version(first, second, rng=stable_tie_rng("word", seed))[1].winner
            for seed in range(32)
        }

        self.assertEqual({"A", "B"}, winners)

    def test_equal_rank_puzzle_choice_is_stable_for_same_seed(self):
        left = PuzzleAssessment(
            definition_score=9.0,
            verified_count=8,
            total_clues=10,
            min_rebus=7,
            avg_rebus=7.5,
            blocker_words=["X"],
        )
        right = PuzzleAssessment(
            definition_score=9.0,
            verified_count=8,
            total_clues=10,
            min_rebus=7,
            avg_rebus=7.5,
            blocker_words=["Y"],
        )

        winner_one, _ = choose_puzzle_assessment(left, right, rng=stable_tie_rng("puzzle", 1))
        winner_two, _ = choose_puzzle_assessment(left, right, rng=stable_tie_rng("puzzle", 1))

        self.assertEqual(winner_one, winner_two)


if __name__ == "__main__":
    unittest.main()
