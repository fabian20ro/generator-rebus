import json
import tempfile
import unittest
from pathlib import Path

from rebus_generator.workflows.canonicals.runtime import run_audit
from rebus_generator.workflows.canonicals.service import build_parser
from rebus_generator.workflows.canonicals.domain_service import (
    ClueCanonService,
    aggregate_referee_votes,
    build_definition_record,
    build_exact_groups,
    choose_canonical_winner,
    classify_disagreement_bucket,
    generate_near_duplicate_candidates,
    normalize_definition_text,
)
from rebus_generator.domain.clue_canon_types import CanonicalDefinition, DefinitionComparisonVote


def _canonical(
    *,
    canonical_id: str,
    word: str,
    definition: str,
    word_type: str = "",
    usage_label: str = "",
    verified: bool = True,
    usage_count: int = 1,
    semantic_score: int | None = 8,
    rebus_score: int | None = 7,
    creativity_score: int | None = 6,
    superseded_by: str | None = None,
) -> CanonicalDefinition:
    return CanonicalDefinition(
        id=canonical_id,
        word_normalized=word,
        word_original_seed=word.lower(),
        definition=definition,
        definition_norm=normalize_definition_text(definition),
        word_type=word_type,
        usage_label=usage_label,
        verified=verified,
        semantic_score=semantic_score,
        rebus_score=rebus_score,
        creativity_score=creativity_score,
        usage_count=usage_count,
        superseded_by=superseded_by,
    )


class _AuditStore:
    def __init__(self, *, raw_rows, effective_rows, active_canonicals, canonical_rows_by_id):
        self._raw_rows = list(raw_rows)
        self._effective_rows = list(effective_rows)
        self._active_canonicals = list(active_canonicals)
        self._canonical_rows_by_id = dict(canonical_rows_by_id)

    def fetch_raw_clue_rows(self, *, extra_fields=()):
        return list(self._raw_rows)

    def fetch_clue_rows(self, **_kwargs):
        return list(self._effective_rows)

    def fetch_active_canonical_variants(self, **_kwargs):
        return list(self._active_canonicals)

    def fetch_canonical_rows_by_ids(self, canonical_ids):
        return [
            self._canonical_rows_by_id[canonical_id]
            for canonical_id in canonical_ids
            if canonical_id in self._canonical_rows_by_id
        ]


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

    def test_choose_canonical_winner_uses_stable_fallback_after_reset(self):
        rows = [
            build_definition_record({
                "id": "2",
                "word_normalized": "APA",
                "word_original": "apă",
                "definition": "Zonă lichidă.",
                "verified": False,
                "semantic_score": None,
                "rebus_score": None,
                "creativity_score": None,
            }),
            build_definition_record({
                "id": "1",
                "word_normalized": "APA",
                "word_original": "apă",
                "definition": "Arie lichidă.",
                "verified": False,
                "semantic_score": None,
                "rebus_score": None,
                "creativity_score": None,
            }),
        ]

        winner = choose_canonical_winner(rows)

        self.assertEqual("1", winner.id)

    def test_fetch_prompt_examples_filters_out_null_score_canonicals(self):
        store = type("Store", (), {
            "fetch_canonical_variants": lambda _self, _word, limit=None: [
                _canonical(
                    canonical_id="1",
                    word="LA",
                    definition="Null-score veche.",
                    verified=False,
                    usage_count=0,
                    semantic_score=None,
                    rebus_score=None,
                    creativity_score=None,
                ),
                _canonical(
                    canonical_id="2",
                    word="LA",
                    definition="Variantă bună.",
                    verified=True,
                    usage_count=2,
                    semantic_score=8,
                    rebus_score=7,
                    creativity_score=6,
                ),
            ],
        })()
        service = ClueCanonService(store=store)

        examples = service.fetch_prompt_examples("LA", limit=2)

        self.assertEqual(["Variantă bună."], examples)

    def test_select_scored_fallback_excludes_null_scores(self):
        store = type("Store", (), {
            "fetch_canonical_variants": lambda _self, _word, limit=None: [
                _canonical(
                    canonical_id="1",
                    word="LA",
                    definition="Null-score veche.",
                    verified=True,
                    usage_count=0,
                    semantic_score=None,
                    rebus_score=None,
                    creativity_score=None,
                ),
                _canonical(
                    canonical_id="2",
                    word="LA",
                    definition="Variantă bună.",
                    verified=True,
                    usage_count=1,
                    semantic_score=8,
                    rebus_score=7,
                    creativity_score=6,
                ),
            ],
        })()
        service = ClueCanonService(store=store)

        chosen = service.select_scored_fallback(
            word_normalized="LA",
            word_type="",
            usage_label="",
            seed_parts=("p1", "H", 1, 1),
        )

        self.assertIsNotNone(chosen)
        self.assertEqual("2", chosen.id)

    def test_select_scored_fallback_penalizes_high_usage_when_scores_match(self):
        store = type("Store", (), {
            "fetch_canonical_variants": lambda _self, _word, limit=None: [
                _canonical(
                    canonical_id="1",
                    word="LA",
                    definition="Variantă rar folosită.",
                    verified=True,
                    usage_count=0,
                    semantic_score=8,
                    rebus_score=8,
                    creativity_score=8,
                ),
                _canonical(
                    canonical_id="2",
                    word="LA",
                    definition="Variantă des folosită.",
                    verified=True,
                    usage_count=7,
                    semantic_score=8,
                    rebus_score=8,
                    creativity_score=8,
                ),
            ],
        })()
        service = ClueCanonService(store=store)
        counts = {"1": 0, "2": 0}

        for seed in range(100):
            chosen = service.select_scored_fallback(
                word_normalized="LA",
                word_type="",
                usage_label="",
                seed_parts=("p1", "H", 1, seed),
            )
            counts[chosen.id] += 1

        self.assertGreater(counts["1"], counts["2"])

    def test_select_scored_fallback_keeps_lower_scores_in_pool(self):
        store = type("Store", (), {
            "fetch_canonical_variants": lambda _self, _word, limit=None: [
                _canonical(
                    canonical_id="1",
                    word="LA",
                    definition="Variantă excelentă.",
                    verified=True,
                    usage_count=0,
                    semantic_score=9,
                    rebus_score=9,
                    creativity_score=9,
                ),
                _canonical(
                    canonical_id="2",
                    word="LA",
                    definition="Variantă modestă.",
                    verified=True,
                    usage_count=0,
                    semantic_score=1,
                    rebus_score=1,
                    creativity_score=1,
                ),
            ],
        })()
        service = ClueCanonService(store=store)
        counts = {"1": 0, "2": 0}

        for seed in range(100):
            chosen = service.select_scored_fallback(
                word_normalized="LA",
                word_type="",
                usage_label="",
                seed_parts=("p1", "V", 2, seed),
            )
            counts[chosen.id] += 1

        self.assertGreater(counts["1"], counts["2"])
        self.assertGreater(counts["2"], 0)

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
        self.assertFalse(result.disagreement)
        self.assertIsNone(classify_disagreement_bucket(result))

    def test_build_parser_supports_only_audit_and_simplify(self):
        parser = build_parser()

        audit_args = parser.parse_args(["audit"])
        simplify_args = parser.parse_args(["simplify-fanout", "--apply"])

        self.assertEqual("audit", audit_args.command)
        self.assertEqual("simplify-fanout", simplify_args.command)
        with self.assertRaises(SystemExit):
            parser.parse_args(["backfill", "--apply"])

    def test_run_audit_passes_for_clean_canonical_state(self):
        raw_rows = [
            {
                "id": "c1",
                "puzzle_id": "p1",
                "word_normalized": "LA",
                "canonical_definition_id": "123e4567-e89b-12d3-a456-426614174001",
            }
        ]
        effective_rows = [
            {
                "id": "c1",
                "puzzle_id": "p1",
                "definition": "Prepoziție.",
                "canonical_definition_id": "123e4567-e89b-12d3-a456-426614174001",
            }
        ]
        canonical = _canonical(
            canonical_id="123e4567-e89b-12d3-a456-426614174001",
            word="LA",
            definition="Prepoziție.",
        )
        store = _AuditStore(
            raw_rows=raw_rows,
            effective_rows=effective_rows,
            active_canonicals=[canonical],
            canonical_rows_by_id={canonical.id: canonical},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "audit.json"
            exit_code = run_audit(store=store, output=str(output))
            summary = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(0, exit_code)
        self.assertTrue(summary["ok"])
        self.assertEqual(0, summary["missing_effective_rows"])

    def test_run_audit_flags_pointer_and_fanout_issues(self):
        active_duplicates = [
            _canonical(
                canonical_id="123e4567-e89b-12d3-a456-426614174010",
                word="APA",
                definition="Lichid vital.",
            ),
            _canonical(
                canonical_id="123e4567-e89b-12d3-a456-426614174011",
                word="APA",
                definition="Lichid vital.",
            ),
        ]
        fanout_rows = [
            _canonical(
                canonical_id=f"123e4567-e89b-12d3-a456-4266141741{i:02d}",
                word="LA",
                definition=f"Def {i}",
            )
            for i in range(5)
        ]
        superseded = _canonical(
            canonical_id="123e4567-e89b-12d3-a456-426614174099",
            word="SI",
            definition="Conjuncție.",
            superseded_by="123e4567-e89b-12d3-a456-426614174100",
        )
        raw_rows = [
            {"id": "c1", "puzzle_id": "p1", "word_normalized": "LA", "canonical_definition_id": ""},
            {"id": "c2", "puzzle_id": "p1", "word_normalized": "LA", "canonical_definition_id": "bad-id"},
            {
                "id": "c3",
                "puzzle_id": "p1",
                "word_normalized": "SI",
                "canonical_definition_id": "123e4567-e89b-12d3-a456-426614174099",
            },
            {
                "id": "c4",
                "puzzle_id": "p2",
                "word_normalized": "APA",
                "canonical_definition_id": "123e4567-e89b-12d3-a456-426614174012",
            },
        ]
        effective_rows = [
            {"id": "c1", "puzzle_id": "p1", "definition": "x", "canonical_definition_id": ""},
            {"id": "c2", "puzzle_id": "p1", "definition": "x", "canonical_definition_id": "bad-id"},
            {
                "id": "c3",
                "puzzle_id": "p1",
                "definition": "Conjuncție.",
                "canonical_definition_id": "123e4567-e89b-12d3-a456-426614174099",
            },
        ]
        canonical_rows_by_id = {
            superseded.id: superseded,
            "123e4567-e89b-12d3-a456-426614174012": _canonical(
                canonical_id="123e4567-e89b-12d3-a456-426614174012",
                word="APA",
                definition="Altă definiție.",
            ),
        }
        store = _AuditStore(
            raw_rows=raw_rows,
            effective_rows=effective_rows,
            active_canonicals=active_duplicates + fanout_rows,
            canonical_rows_by_id=canonical_rows_by_id,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "audit.json"
            exit_code = run_audit(store=store, output=str(output))
            summary = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(1, exit_code)
        self.assertFalse(summary["ok"])
        self.assertEqual(1, summary["null_canonical_definition_id"])
        self.assertEqual(1, summary["bad_canonical_definition_id"])
        self.assertEqual(1, summary["superseded_canonical_links"])
        self.assertEqual(1, summary["duplicate_active_canonical_identities"])
        self.assertEqual(1, summary["oversized_fanout_buckets"])
        self.assertEqual(1, summary["missing_effective_rows"])


if __name__ == "__main__":
    unittest.main()
