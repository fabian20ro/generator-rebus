import unittest
from io import StringIO

from generator.clue_canon import _build_initial_clusters, _merge_word_batch
from generator.core.clue_canon import (
    aggregate_referee_votes,
    build_definition_record,
    build_exact_groups,
    choose_canonical_winner,
    classify_disagreement_bucket,
    generate_near_duplicate_candidates,
    normalize_definition_text,
)
from generator.core.clue_canon_types import BackfillStats, DefinitionComparisonVote, DefinitionRefereeResult


class ClueCanonTests(unittest.TestCase):
    def test_normalize_definition_text_collapses_case_punctuation_and_spacing(self):
        self.assertEqual(
            "prepozitie care indica locul sau destinatia",
            normalize_definition_text("  Prepoziție, care indică locul sau destinația! "),
        )

    def test_build_exact_groups_uses_normalized_definition(self):
        rows = [
            build_definition_record({
                "id": "1",
                "word_normalized": "LA",
                "word_original": "la",
                "definition": "Prepoziție care indică locul.",
            }),
            build_definition_record({
                "id": "2",
                "word_normalized": "LA",
                "word_original": "la",
                "definition": "prepoziție care indică locul",
            }),
            build_definition_record({
                "id": "3",
                "word_normalized": "LA",
                "word_original": "la",
                "definition": "Prepoziție care indică destinația.",
            }),
        ]

        groups = build_exact_groups(rows)

        self.assertEqual(2, len(groups))
        self.assertEqual(sorted([2, 1]), sorted(len(group) for group in groups))

    def test_choose_canonical_winner_prefers_verified_then_scores(self):
        rows = [
            build_definition_record({
                "id": "1",
                "word_normalized": "APA",
                "word_original": "apă",
                "definition": "Substanță lichidă esențială pentru viață.",
                "verified": False,
                "semantic_score": 10,
                "rebus_score": 10,
                "creativity_score": 10,
            }),
            build_definition_record({
                "id": "2",
                "word_normalized": "APA",
                "word_original": "apă",
                "definition": "Lichid esențial pentru viață.",
                "verified": True,
                "semantic_score": 8,
                "rebus_score": 8,
                "creativity_score": 5,
            }),
        ]

        winner = choose_canonical_winner(rows)

        self.assertEqual("2", winner.id)

    def test_generate_near_duplicate_candidates_finds_similar_same_word_defs(self):
        rows = [
            build_definition_record({
                "id": "1",
                "word_normalized": "ZI",
                "word_original": "zi",
                "definition": "Perioadă de 24 de ore.",
            }),
            build_definition_record({
                "id": "2",
                "word_normalized": "ZI",
                "word_original": "zi",
                "definition": "Unitate de timp de 24 de ore.",
            }),
            build_definition_record({
                "id": "3",
                "word_normalized": "ZI",
                "word_original": "zi",
                "definition": "Interval de lumină dintre răsărit și apus.",
            }),
        ]

        candidates = generate_near_duplicate_candidates(rows)

        self.assertTrue(any(
            {candidate.left.id, candidate.right.id} == {"1", "2"}
            for candidate in candidates
        ))

    def test_aggregate_referee_votes_and_disagreement_bucket(self):
        result = aggregate_referee_votes([
            DefinitionComparisonVote(model_id="m1", same_meaning=True, better="A"),
            DefinitionComparisonVote(model_id="m1", same_meaning=True, better="A"),
            DefinitionComparisonVote(model_id="m1", same_meaning=True, better="A"),
            DefinitionComparisonVote(model_id="m2", same_meaning=True, better="B"),
            DefinitionComparisonVote(model_id="m2", same_meaning=True, better="B"),
            DefinitionComparisonVote(model_id="m2", same_meaning=False, better="equal"),
        ])

        self.assertEqual(5, result.same_meaning_votes)
        self.assertEqual(3, result.better_a_votes)
        self.assertEqual(2, result.better_b_votes)
        self.assertTrue(result.disagreement)
        self.assertEqual(3, classify_disagreement_bucket(result))

    def test_merge_word_batch_batches_referee_requests_without_changing_word_results(self):
        class _Service:
            def __init__(self):
                self.batches = []

            def _run_referee_batch(self, requests):
                self.batches.append([request.request_id for request in requests])
                return {
                    request.request_id: DefinitionRefereeResult(
                        same_meaning_votes=6,
                        better_a_votes=0,
                        better_b_votes=6,
                        equal_votes=0,
                        votes=[],
                    )
                    for request in requests
                }

        service = _Service()
        stats = BackfillStats()
        review = StringIO()
        bucket_batch = [
            (
                "LA",
                _build_initial_clusters([
                    build_definition_record({
                        "id": "1",
                        "word_normalized": "LA",
                        "word_original": "la",
                        "definition": "Prepoziție care indică locul.",
                    }),
                    build_definition_record({
                        "id": "2",
                        "word_normalized": "LA",
                        "word_original": "la",
                        "definition": "Prepoziție care indică destinația sau locul.",
                    }),
                ], stats),
            ),
            (
                "SI",
                _build_initial_clusters([
                    build_definition_record({
                        "id": "3",
                        "word_normalized": "SI",
                        "word_original": "și",
                        "definition": "Conjuncție care leagă termeni.",
                    }),
                    build_definition_record({
                        "id": "4",
                        "word_normalized": "SI",
                        "word_original": "și",
                        "definition": "Conjuncție care unește termeni sau propoziții.",
                    }),
                ], stats),
            ),
        ]

        merged = _merge_word_batch(
            service,
            bucket_batch,
            review,
            stats,
            referee_batch_size=50,
        )

        self.assertEqual(1, len(service.batches))
        self.assertEqual(2, len(service.batches[0]))
        self.assertEqual({"LA": 1, "SI": 1}, {word: len(clusters) for word, clusters in merged})
        self.assertEqual(2, stats.near_merges)


if __name__ == "__main__":
    unittest.main()
