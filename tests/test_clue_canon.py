import unittest
from io import StringIO
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import patch
import json

from generator.clue_canon import (
    _MergeState,
    _PendingReferee,
    _QueuedWord,
    _WorkingCluster,
    STATE_VERSION,
    _apply_terminal_outcome,
    _collect_referee_launch_batch,
    _build_referee_outcomes,
    _apply_clusters,
    _build_initial_clusters,
    _collect_pending_referees,
    _config_matches_state,
    _load_state,
    _queued_word_from_state,
    _queued_word_to_state,
    _merge_word_batch,
    run_backfill,
    build_parser,
    run_audit,
)
from generator.core.clue_canon import (
    ClueCanonService,
    CanonicalDefinition,
    aggregate_referee_votes,
    build_definition_record,
    build_exact_groups,
    choose_canonical_winner,
    classify_disagreement_bucket,
    generate_near_duplicate_candidates,
    lexical_similarity,
    normalize_definition_text,
)
from generator.core.clue_canon_types import (
    BackfillStats,
    DefinitionComparisonAttempt,
    DefinitionComparisonVote,
    DefinitionRefereeDiagnostics,
    DefinitionRefereeResult,
)
from generator.core.model_manager import PRIMARY_MODEL, SECONDARY_MODEL


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
        self.assertFalse(result.disagreement)
        self.assertIsNone(classify_disagreement_bucket(result))

    def test_merge_word_batch_batches_referee_requests_without_changing_word_results(self):
        class _Service:
            def __init__(self):
                self.batches = []
                self.store = SimpleNamespace(fetch_canonical_variants=lambda *_args, **_kwargs: [])

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
                        "verified": True,
                    }),
                    build_definition_record({
                        "id": "2",
                        "word_normalized": "LA",
                        "word_original": "la",
                        "definition": "Prepoziție care indică destinația sau locul.",
                        "verified": True,
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
                        "verified": True,
                    }),
                    build_definition_record({
                        "id": "4",
                        "word_normalized": "SI",
                        "word_original": "și",
                        "definition": "Conjuncție care unește termeni sau propoziții.",
                        "verified": True,
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

    def test_resolve_definition_promote_new_creates_new_canonical_without_overwriting_old(self):
        class _Store:
            def __init__(self):
                self.created: list[dict] = []
                self.bumped: list[str] = []
                self.attached: list[tuple[str, str | None, str]] = []
                self.existing = CanonicalDefinition(
                    id="canon-old",
                    word_normalized="APA",
                    word_original_seed="apa",
                    definition="Lichid vital.",
                    definition_norm=normalize_definition_text("Lichid vital."),
                    verified=True,
                    semantic_score=8,
                    rebus_score=8,
                    creativity_score=5,
                )

            def is_enabled(self):
                return True

            def find_exact_canonical(self, *_args, **_kwargs):
                return None

            def fetch_canonical_variants(self, *_args, **_kwargs):
                return [self.existing]

            def create_canonical_definition(self, record):
                created = CanonicalDefinition(
                    id="canon-new",
                    word_normalized=record.word_normalized,
                    word_original_seed=record.word_original,
                    definition=record.definition,
                    definition_norm=record.definition_norm,
                    word_type=record.word_type,
                    usage_label=record.usage_label,
                    verified=record.verified,
                    semantic_score=record.semantic_score,
                    rebus_score=record.rebus_score,
                    creativity_score=record.creativity_score,
                    usage_count=1,
                )
                self.created.append({"id": created.id, "definition": created.definition})
                return created

            def bump_usage(self, canonical_id, _word):
                self.bumped.append(canonical_id)
                return self.existing if canonical_id == self.existing.id else None

            def insert_alias(self, **_kwargs):
                raise AssertionError("promote_new should not alias-overwrite the old canonical")

            def attach_clue(self, clue_id, puzzle_id, *, canonical_definition_id):
                self.attached.append((clue_id, puzzle_id, canonical_definition_id))

        service = ClueCanonService(store=_Store())
        service._likely_matches = lambda _record: [service.store.existing]
        service._run_referee = lambda *_args, **_kwargs: DefinitionRefereeResult(
            same_meaning_votes=6,
            better_a_votes=6,
            better_b_votes=0,
            equal_votes=0,
            votes=[],
        )

        decision = service.resolve_definition(
            word_normalized="APA",
            word_original="apa",
            definition="Substanță lichidă necesară vieții.",
            clue_id="clue-1",
            puzzle_id="puzzle-1",
            verified=True,
            semantic_score=9,
            rebus_score=8,
            creativity_score=6,
        )

        self.assertEqual("promote_new", decision.action)
        self.assertEqual("canon-new", decision.canonical_definition_id)
        self.assertEqual("Substanță lichidă necesară vieții.", decision.canonical_definition)

    def test_apply_clusters_creates_and_attaches_singleton_canonical(self):
        class _Store:
            def __init__(self):
                self.created = []
                self.attached = []
                self.aliases = []

            def create_canonical_definition(self, record):
                self.created.append(record.definition)
                return SimpleNamespace(id="canon-1")

            def attach_clue(self, clue_id, puzzle_id, *, canonical_definition_id):
                self.attached.append((clue_id, puzzle_id, canonical_definition_id))

            def insert_alias(self, **kwargs):
                self.aliases.append(kwargs)

        store = _Store()
        stats = BackfillStats()
        clusters = _build_initial_clusters([
            build_definition_record({
                "id": "1",
                "word_normalized": "APA",
                "word_original": "apa",
                "definition": "Lichid vital.",
            })
        ], stats)

        _apply_clusters(store, "APA", clusters, dry_run=False)

        self.assertEqual(["Lichid vital."], store.created)
        self.assertEqual([("1", "", "canon-1")], store.attached)
        self.assertEqual([], store.aliases)

    def test_apply_clusters_uses_batch_store_helpers(self):
        class _Store:
            def __init__(self):
                self.attach_batches = []
                self.alias_batches = []

            def create_canonical_definition(self, record):
                return SimpleNamespace(id="canon-1")

            def attach_clues(self, clue_ids, *, canonical_definition_id):
                self.attach_batches.append((list(clue_ids), canonical_definition_id))
                return 1

            def insert_aliases(self, *, canonical_definition_id, word_normalized, aliases):
                self.alias_batches.append((canonical_definition_id, word_normalized, list(aliases)))
                return 1

        store = _Store()
        clusters = [
            _WorkingCluster(
                primary=build_definition_record({
                    "id": "1",
                    "word_normalized": "APA",
                    "word_original": "apa",
                    "definition": "Lichid vital.",
                }),
                members=[
                    build_definition_record({
                        "id": "1",
                        "word_normalized": "APA",
                        "word_original": "apa",
                        "definition": "Lichid vital.",
                    }),
                    build_definition_record({
                        "id": "2",
                        "word_normalized": "APA",
                        "word_original": "apa",
                        "definition": "Substanță lichidă.",
                    }),
                ],
            )
        ]

        clue_batches, alias_batches = _apply_clusters(store, "APA", clusters, dry_run=False)

        self.assertEqual(1, clue_batches)
        self.assertEqual(1, alias_batches)
        self.assertEqual([(["1", "2"], "canon-1")], store.attach_batches)
        self.assertEqual(1, len(store.alias_batches))
        self.assertEqual(1, len(store.alias_batches[0][2]))

    def test_collect_pending_referees_exact_merge_reuses_existing_canonical_without_request(self):
        stats = BackfillStats()
        primary = build_definition_record({
            "id": "1",
            "word_normalized": "APA",
            "word_original": "apa",
            "definition": "Lichid vital.",
            "verified": True,
        })
        queued = _QueuedWord(
            merge_state=_MergeState(
                word="APA",
                clusters=[_WorkingCluster(primary=primary, members=[primary])],
                selected=[
                    _WorkingCluster(
                        primary=build_definition_record({
                            "id": "canon-1",
                            "word_normalized": "APA",
                            "word_original": "apa",
                            "definition": "Lichid vital.",
                            "verified": True,
                        }),
                        members=[],
                        canonical_id="canon-1",
                    )
                ],
            ),
            input_count=1,
        )

        requests, pending, resolved, next_request_id = _collect_pending_referees(
            [queued],
            max_requests=10,
            next_request_id=1,
            stats=stats,
        )

        self.assertEqual([], requests)
        self.assertEqual([], pending)
        self.assertEqual(1, len(resolved))
        self.assertEqual(1, next_request_id)
        self.assertEqual(1, queued.candidate_pairs_considered)
        self.assertEqual(0, queued.referee_requests_submitted)
        _apply_terminal_outcome(
            queued,
            resolved[0].outcome,
            stats=stats,
            review_handle=StringIO(),
        )
        self.assertEqual(1, stats.exact_merges)
        self.assertEqual(1, len(queued.merge_state.selected[0].members))
        self.assertTrue(queued.merge_state.finished())

    def test_collect_pending_referees_skips_boilerplate_overlap_pairs(self):
        stats = BackfillStats()
        current = build_definition_record({
            "id": "2",
            "word_normalized": "LA",
            "word_original": "la",
            "definition": "Indică locul unei acțiuni.",
        })
        existing = build_definition_record({
            "id": "1",
            "word_normalized": "LA",
            "word_original": "la",
            "definition": "Locul unde se desfășoară o acțiune.",
        })
        self.assertLess(lexical_similarity(current.definition_norm, existing.definition_norm), 0.9)
        queued = _QueuedWord(
            merge_state=_MergeState(
                word="LA",
                clusters=[_WorkingCluster(primary=current, members=[current])],
                selected=[_WorkingCluster(primary=existing, members=[existing])],
                boilerplate_tokens=("indica", "locul", "actiune"),
            ),
            input_count=1,
        )

        requests, pending, resolved, _next_request_id = _collect_pending_referees(
            [queued],
            max_requests=10,
            next_request_id=1,
            stats=stats,
        )

        self.assertEqual([], requests)
        self.assertEqual([], pending)
        self.assertEqual(1, len(resolved))
        self.assertEqual(0, queued.referee_requests_submitted)
        _apply_terminal_outcome(
            queued,
            resolved[0].outcome,
            stats=stats,
            review_handle=StringIO(),
        )
        self.assertEqual(2, len(queued.merge_state.selected))

    def test_collect_referee_launch_batch_keeps_requests_when_immediate_resolutions_exist(self):
        stats = BackfillStats()
        review = StringIO()

        exact_current = build_definition_record({
            "id": "1",
            "word_normalized": "APA",
            "word_original": "apa",
            "definition": "Lichid vital.",
            "verified": True,
        })
        compare_current = build_definition_record({
            "id": "2",
            "word_normalized": "APA",
            "word_original": "apa",
            "definition": "Substanță lichidă vitală.",
            "verified": True,
        })
        existing_exact = build_definition_record({
            "id": "canon-1",
            "word_normalized": "APA",
            "word_original": "apa",
            "definition": "Lichid vital.",
            "verified": True,
        })
        existing_compare = build_definition_record({
            "id": "canon-2",
            "word_normalized": "APA",
            "word_original": "apa",
            "definition": "Substanță lichidă esențială.",
            "verified": True,
        })

        queued = _QueuedWord(
            merge_state=_MergeState(
                word="APA",
                clusters=[
                    _WorkingCluster(primary=exact_current, members=[exact_current]),
                    _WorkingCluster(primary=compare_current, members=[compare_current]),
                ],
                selected=[
                    _WorkingCluster(primary=existing_exact, members=[], canonical_id="canon-1"),
                    _WorkingCluster(primary=existing_compare, members=[], canonical_id="canon-2"),
                ],
            ),
            input_count=2,
        )

        requests, pending, _next_request_id, immediate_resolved_words = _collect_referee_launch_batch(
            [queued],
            max_requests=10,
            min_requests_to_launch=4,
            next_request_id=1,
            stats=stats,
            review_handle=review,
        )

        self.assertEqual(1, immediate_resolved_words)
        self.assertEqual(1, len(requests))
        self.assertEqual("APA", requests[0].word)
        self.assertEqual(1, len(pending))
        self.assertEqual(1, stats.exact_merges)
        self.assertTrue(queued.merge_state.waiting)

    def test_collect_referee_launch_batch_refills_after_immediate_resolution(self):
        stats = BackfillStats()
        review = StringIO()

        queued_words = []
        for word, index in (("APA", "1"), ("SI", "2"), ("LA", "3"), ("IN", "4")):
            exact = build_definition_record({
                "id": f"{index}a",
                "word_normalized": word,
                "word_original": word.lower(),
                "definition": f"Definiție exactă {word}.",
                "verified": True,
            })
            compare = build_definition_record({
                "id": f"{index}b",
                "word_normalized": word,
                "word_original": word.lower(),
                "definition": f"Substanță lichidă vitală {word}.",
                "verified": True,
            })
            queued_words.append(
                _QueuedWord(
                    merge_state=_MergeState(
                        word=word,
                        clusters=[
                            _WorkingCluster(primary=exact, members=[exact]),
                            _WorkingCluster(primary=compare, members=[compare]),
                        ],
                        selected=[
                            _WorkingCluster(
                                primary=build_definition_record({
                                    "id": f"canon-{index}a",
                                    "word_normalized": word,
                                    "word_original": word.lower(),
                                    "definition": f"Definiție exactă {word}.",
                                    "verified": True,
                                }),
                                members=[],
                                canonical_id=f"canon-{index}a",
                            ),
                            _WorkingCluster(
                                primary=build_definition_record({
                                    "id": f"canon-{index}b",
                                    "word_normalized": word,
                                    "word_original": word.lower(),
                                    "definition": f"Substanță lichidă esențială {word}.",
                                    "verified": True,
                                }),
                                members=[],
                                canonical_id=f"canon-{index}b",
                            ),
                        ],
                    ),
                    input_count=2,
                )
            )

        requests, pending, _next_request_id, immediate_resolved_words = _collect_referee_launch_batch(
            queued_words,
            max_requests=10,
            min_requests_to_launch=4,
            next_request_id=1,
            stats=stats,
            review_handle=review,
        )

        self.assertEqual(4, immediate_resolved_words)
        self.assertEqual(4, len(requests))
        self.assertEqual(4, len(pending))

    def test_queued_word_state_roundtrip_preserves_defer_fields(self):
        record = build_definition_record({
            "id": "1",
            "word_normalized": "APA",
            "word_original": "apa",
            "definition": "Lichid vital.",
        })
        item = _QueuedWord(
            merge_state=_MergeState(
                word="APA",
                clusters=[_WorkingCluster(primary=record, members=[record])],
                selected=[],
                boilerplate_tokens=("lichid",),
            ),
            input_count=1,
            comparisons_done=7,
            unresolved=True,
            deferred=True,
            defer_reason="stagnation_budget",
            defer_remaining_clusters=4,
            candidate_pairs_considered=9,
            referee_requests_submitted=5,
            consecutive_non_merge_comparisons=7,
            last_merge_comparison=3,
        )

        restored = _queued_word_from_state(
            _queued_word_to_state(item),
            state_version=STATE_VERSION,
        )

        self.assertTrue(restored.deferred)
        self.assertEqual("stagnation_budget", restored.defer_reason)
        self.assertEqual(4, restored.defer_remaining_clusters)
        self.assertEqual(7, restored.consecutive_non_merge_comparisons)
        self.assertEqual(3, restored.last_merge_comparison)
        self.assertEqual(("lichid",), restored.merge_state.boilerplate_tokens)

    def test_build_referee_outcomes_marks_missing_model_votes_as_terminal_error(self):
        diagnostics = DefinitionRefereeDiagnostics(
            request_id="cmp-1",
            attempts=[
                DefinitionComparisonAttempt(
                    model_id=PRIMARY_MODEL.model_id,
                    model_role="primary",
                    valid_vote=True,
                    parse_status="ok",
                    vote=DefinitionComparisonVote(
                        model_id=PRIMARY_MODEL.model_id,
                        same_meaning=True,
                        better="A",
                    ),
                ),
                DefinitionComparisonAttempt(
                    model_id=SECONDARY_MODEL.model_id,
                    model_role="secondary",
                    valid_vote=False,
                    parse_status="invalid_json",
                ),
            ],
            primary_valid_votes=1,
            secondary_valid_votes=0,
        )
        outcomes = _build_referee_outcomes(
            [_PendingReferee(request_id="cmp-1", state_index=0, existing_index=2)],
            {
                "cmp-1": DefinitionRefereeResult(
                    same_meaning_votes=1,
                    better_a_votes=1,
                    better_b_votes=0,
                    equal_votes=0,
                    votes=[
                        DefinitionComparisonVote(
                            model_id=PRIMARY_MODEL.model_id,
                            same_meaning=True,
                            better="A",
                        )
                    ],
                    diagnostics=diagnostics,
                )
            },
        )

        self.assertEqual(1, len(outcomes))
        self.assertEqual("error_missing_model_votes", outcomes[0].outcome.kind)
        self.assertEqual(("secondary",), outcomes[0].outcome.missing_model_roles)

    def test_state_v2_migrates_compare_position_into_candidate_indexes(self):
        payload = {
            "word": "APA",
            "input_count": 1,
            "merge_state": {
                "word": "APA",
                "clusters": [{
                    "primary": {
                        "id": "1",
                        "word_normalized": "APA",
                        "word_original": "apa",
                        "definition": "Lichid vital.",
                        "definition_norm": "lichid vital",
                    },
                    "members": [{
                        "id": "1",
                        "word_normalized": "APA",
                        "word_original": "apa",
                        "definition": "Lichid vital.",
                        "definition_norm": "lichid vital",
                    }],
                }],
                "selected": [
                    {
                        "primary": {
                            "id": "c1",
                            "word_normalized": "APA",
                            "word_original": "apa",
                            "definition": "Sens unu.",
                            "definition_norm": "sens unu",
                        },
                        "members": [],
                        "canonical_id": "c1",
                    },
                    {
                        "primary": {
                            "id": "c2",
                            "word_normalized": "APA",
                            "word_original": "apa",
                            "definition": "Sens doi.",
                            "definition_norm": "sens doi",
                        },
                        "members": [],
                        "canonical_id": "c2",
                    },
                ],
                "next_cluster_index": 0,
                "current": {
                    "primary": {
                        "id": "1",
                        "word_normalized": "APA",
                        "word_original": "apa",
                        "definition": "Lichid vital.",
                        "definition_norm": "lichid vital",
                    },
                    "members": [{
                        "id": "1",
                        "word_normalized": "APA",
                        "word_original": "apa",
                        "definition": "Lichid vital.",
                        "definition_norm": "lichid vital",
                    }],
                },
                "compare_index": 1,
                "waiting": True,
            },
        }

        restored = _queued_word_from_state(payload, state_version=2)

        self.assertEqual([1], restored.merge_state.candidate_indexes)
        self.assertEqual(0, restored.merge_state.compare_index)
        self.assertEqual("legacy-pending", restored.merge_state.pending_request_id)

    def test_run_backfill_prefetches_queue_words_once_per_refill_wave(self):
        class _Store:
            def __init__(self):
                self.prefetch_calls = []
                self.rows = [
                    {
                        "id": "1",
                        "word_normalized": "APA",
                        "word_original": "apa",
                        "definition": "Lichid vital.",
                        "verify_note": "",
                        "verified": True,
                    },
                    {
                        "id": "2",
                        "word_normalized": "SI",
                        "word_original": "si",
                        "definition": "Conjuncție.",
                        "verify_note": "",
                        "verified": True,
                    },
                ]

            def is_enabled(self):
                return True

            def fetch_clue_rows(self, *args, **kwargs):
                raise AssertionError("backfill should use filtered source fetch")

            def fetch_backfill_source_rows(self, *, word_normalized=None, extra_fields=()):
                return [row for row in self.rows if not word_normalized or row["word_normalized"] == word_normalized]

            def count_clue_rows(self, *, verified=None, canonical_missing_only=False, word_normalized=None):
                counts = {
                    (None, False, None): 4,
                    (True, False, None): 3,
                    (True, True, None): 2,
                }
                return counts[(verified, canonical_missing_only, word_normalized)]

            def prefetch_canonical_variants(self, words_normalized):
                self.prefetch_calls.append(list(words_normalized))
                return {word: [] for word in words_normalized}

            def fetch_canonical_variants(self, *_args, **_kwargs):
                return []

            def create_canonical_definition(self, _record):
                return SimpleNamespace(id="canon-1")

            def attach_clues(self, *_args, **_kwargs):
                return 1

            def insert_aliases(self, *_args, **_kwargs):
                return 0

        store = _Store()
        report_dir = Path("build/clue_canon/test_prefetch")
        if report_dir.exists():
            for path in sorted(report_dir.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
        state_path = Path("build/clue_canon/test_prefetch_state.json")
        if state_path.exists():
            state_path.unlink()

        with patch("generator.clue_canon.ClueCanonStore", return_value=store), \
             patch("generator.clue_canon.create_client", return_value=object()), \
             patch("generator.clue_canon.ClueCanonService", return_value=SimpleNamespace(_run_referee_adaptive_batch=lambda _requests: None)), \
             patch("generator.clue_canon.LmRuntime", return_value=SimpleNamespace(switch_count=0, activation_count=0)), \
             patch("generator.clue_canon.install_process_logging", return_value=SimpleNamespace(restore=lambda: None)), \
             patch("generator.clue_canon.path_timestamp", return_value="test_prefetch"):
            status = run_backfill(
                dry_run=True,
                apply=False,
                word=None,
                limit=None,
                min_count=1,
                referee_batch_size=50,
                resume=False,
                state_path=str(state_path),
                progress_every=25,
                word_queue_size=50,
            )

        self.assertEqual(0, status)
        self.assertEqual([["APA", "SI"]], store.prefetch_calls)
        summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(2, summary["eligible_rows"])
        self.assertEqual(2, summary["eligible_rows_all"])
        self.assertEqual(2, summary["verified_null_rows"])
        self.assertEqual(0, summary["unverified_null_rows"])
        self.assertEqual(2, summary["eligible_words"])
        self.assertEqual(1, summary["already_canonicalized_rows_skipped"])

    def test_run_backfill_word_filter_only_fetches_requested_word(self):
        class _Store:
            def __init__(self):
                self.word_args = []

            def is_enabled(self):
                return True

            def fetch_backfill_source_rows(self, *, word_normalized=None, extra_fields=()):
                self.word_args.append(word_normalized)
                return [
                    {
                        "id": "1",
                        "word_normalized": "APA",
                        "word_original": "apa",
                        "definition": "Lichid vital.",
                        "verify_note": "",
                        "verified": True,
                    }
                ]

            def count_clue_rows(self, *, verified=None, canonical_missing_only=False, word_normalized=None):
                counts = {
                    (None, False, "APA"): 3,
                    (True, False, "APA"): 2,
                    (True, True, "APA"): 1,
                }
                return counts[(verified, canonical_missing_only, word_normalized)]

            def prefetch_canonical_variants(self, words_normalized):
                return {word: [] for word in words_normalized}

            def fetch_canonical_variants(self, *_args, **_kwargs):
                return []

            def create_canonical_definition(self, _record):
                return SimpleNamespace(id="canon-1")

            def attach_clues(self, *_args, **_kwargs):
                return 1

            def insert_aliases(self, *_args, **_kwargs):
                return 0

        store = _Store()
        state_path = Path("build/clue_canon/test_word_state.json")
        if state_path.exists():
            state_path.unlink()

        with patch("generator.clue_canon.ClueCanonStore", return_value=store), \
             patch("generator.clue_canon.create_client", return_value=object()), \
             patch("generator.clue_canon.ClueCanonService", return_value=SimpleNamespace(_run_referee_adaptive_batch=lambda _requests: None)), \
             patch("generator.clue_canon.LmRuntime", return_value=SimpleNamespace(switch_count=0, activation_count=0)), \
             patch("generator.clue_canon.install_process_logging", return_value=SimpleNamespace(restore=lambda: None)), \
             patch("generator.clue_canon.path_timestamp", return_value="test_word_filter"):
            status = run_backfill(
                dry_run=True,
                apply=False,
                word="APA",
                limit=None,
                min_count=1,
                referee_batch_size=50,
                resume=False,
                state_path=str(state_path),
                progress_every=25,
                word_queue_size=50,
            )

        self.assertEqual(0, status)
        self.assertEqual(["APA"], store.word_args)

    def test_run_backfill_unverified_exact_reuses_existing_canonical_without_referee(self):
        class _Store:
            def __init__(self):
                self.attach_calls = []
                self.create_calls = 0

            def is_enabled(self):
                return True

            def fetch_backfill_source_rows(self, *, word_normalized=None, extra_fields=()):
                return [
                    {
                        "id": "1",
                        "word_normalized": "LA",
                        "word_original": "la",
                        "definition": "Prepoziție pentru loc.",
                        "verify_note": "",
                        "verified": False,
                    }
                ]

            def count_clue_rows(self, *, verified=None, canonical_missing_only=False, word_normalized=None):
                counts = {
                    (None, False, None): 1,
                    (True, False, None): 0,
                    (True, True, None): 0,
                }
                return counts[(verified, canonical_missing_only, word_normalized)]

            def prefetch_canonical_variants(self, words_normalized):
                return {word: self.fetch_canonical_variants(word) for word in words_normalized}

            def fetch_canonical_variants(self, *_args, **_kwargs):
                return [
                    SimpleNamespace(
                        id="canon-la",
                        word_normalized="LA",
                        word_original_seed="la",
                        definition="Prepoziție pentru loc.",
                        definition_norm="prepozitie pentru loc",
                        word_type="",
                        usage_label="",
                        verified=True,
                        semantic_score=8,
                        rebus_score=7,
                        creativity_score=6,
                        usage_count=3,
                    )
                ]

            def create_canonical_definition(self, _record):
                self.create_calls += 1
                return SimpleNamespace(id="canon-new")

            def attach_clues(self, clue_ids, *, canonical_definition_id):
                self.attach_calls.append((list(clue_ids), canonical_definition_id))
                return 1

            def insert_aliases(self, *_args, **_kwargs):
                return 0

        store = _Store()
        report_dir = Path("build/clue_canon/test_unverified_exact_reuse")
        if report_dir.exists():
            shutil.rmtree(report_dir)
        state_path = Path("build/clue_canon/test_unverified_exact_reuse_state.json")
        if state_path.exists():
            state_path.unlink()

        with patch("generator.clue_canon.ClueCanonStore", return_value=store), \
             patch("generator.clue_canon.create_client", return_value=object()), \
             patch("generator.clue_canon.ClueCanonService", return_value=SimpleNamespace(_run_referee_adaptive_batch=lambda _requests: (_ for _ in ()).throw(AssertionError("referee should not run")))), \
             patch("generator.clue_canon.LmRuntime", return_value=SimpleNamespace(switch_count=0, activation_count=0)), \
             patch("generator.clue_canon.install_process_logging", return_value=SimpleNamespace(restore=lambda: None)), \
             patch("generator.clue_canon.path_timestamp", return_value="test_unverified_exact_reuse"):
            status = run_backfill(
                dry_run=False,
                apply=True,
                word=None,
                limit=None,
                min_count=1,
                referee_batch_size=50,
                resume=False,
                state_path=str(state_path),
                progress_every=25,
                word_queue_size=50,
            )

        self.assertEqual(0, status)
        self.assertEqual(0, store.create_calls)
        self.assertEqual([(["1"], "canon-la")], store.attach_calls)
        summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(1, summary["unverified_attached_rows"])
        self.assertEqual(1, summary["unverified_exact_reuses"])
        self.assertEqual(0, summary["unverified_singleton_canonicals_created"])

    def test_run_backfill_unverified_creates_singleton_without_referee(self):
        class _Store:
            def __init__(self):
                self.attach_calls = []
                self.create_calls = 0

            def is_enabled(self):
                return True

            def fetch_backfill_source_rows(self, *, word_normalized=None, extra_fields=()):
                return [
                    {
                        "id": "1",
                        "word_normalized": "LA",
                        "word_original": "la",
                        "definition": "Prepoziție pentru loc.",
                        "verify_note": "",
                        "verified": False,
                    }
                ]

            def count_clue_rows(self, *, verified=None, canonical_missing_only=False, word_normalized=None):
                counts = {
                    (None, False, None): 1,
                    (True, False, None): 0,
                    (True, True, None): 0,
                }
                return counts[(verified, canonical_missing_only, word_normalized)]

            def prefetch_canonical_variants(self, words_normalized):
                return {word: [] for word in words_normalized}

            def fetch_canonical_variants(self, *_args, **_kwargs):
                return []

            def create_canonical_definition(self, _record):
                self.create_calls += 1
                return SimpleNamespace(id="canon-new")

            def attach_clues(self, clue_ids, *, canonical_definition_id):
                self.attach_calls.append((list(clue_ids), canonical_definition_id))
                return 1

            def insert_aliases(self, *_args, **_kwargs):
                return 0

        store = _Store()
        report_dir = Path("build/clue_canon/test_unverified_singleton")
        if report_dir.exists():
            shutil.rmtree(report_dir)
        state_path = Path("build/clue_canon/test_unverified_singleton_state.json")
        if state_path.exists():
            state_path.unlink()

        with patch("generator.clue_canon.ClueCanonStore", return_value=store), \
             patch("generator.clue_canon.create_client", return_value=object()), \
             patch("generator.clue_canon.ClueCanonService", return_value=SimpleNamespace(_run_referee_adaptive_batch=lambda _requests: (_ for _ in ()).throw(AssertionError("referee should not run")))), \
             patch("generator.clue_canon.LmRuntime", return_value=SimpleNamespace(switch_count=0, activation_count=0)), \
             patch("generator.clue_canon.install_process_logging", return_value=SimpleNamespace(restore=lambda: None)), \
             patch("generator.clue_canon.path_timestamp", return_value="test_unverified_singleton"):
            status = run_backfill(
                dry_run=False,
                apply=True,
                word=None,
                limit=None,
                min_count=1,
                referee_batch_size=50,
                resume=False,
                state_path=str(state_path),
                progress_every=25,
                word_queue_size=50,
            )

        self.assertEqual(0, status)
        self.assertEqual(1, store.create_calls)
        self.assertEqual([(["1"], "canon-new")], store.attach_calls)
        summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(1, summary["unverified_attached_rows"])
        self.assertEqual(0, summary["unverified_exact_reuses"])
        self.assertEqual(1, summary["unverified_singleton_canonicals_created"])

    def test_run_backfill_resume_drops_stale_pending_words(self):
        class _Store:
            def __init__(self):
                self.created = []

            def is_enabled(self):
                return True

            def fetch_backfill_source_rows(self, *, word_normalized=None, extra_fields=()):
                return [
                    {
                        "id": "1",
                        "word_normalized": "APA",
                        "word_original": "apa",
                        "definition": "Lichid vital.",
                        "verify_note": "",
                        "verified": True,
                    }
                ]

            def count_clue_rows(self, *, verified=None, canonical_missing_only=False, word_normalized=None):
                counts = {
                    (None, False, None): 1,
                    (True, False, None): 1,
                    (True, True, None): 1,
                }
                return counts[(verified, canonical_missing_only, word_normalized)]

            def prefetch_canonical_variants(self, words_normalized):
                return {word: [] for word in words_normalized}

            def fetch_canonical_variants(self, *_args, **_kwargs):
                return []

            def create_canonical_definition(self, record):
                self.created.append(record.word_normalized)
                return SimpleNamespace(id=f"canon-{record.word_normalized}")

            def attach_clues(self, *_args, **_kwargs):
                return 1

            def insert_aliases(self, *_args, **_kwargs):
                return 0

        store = _Store()
        report_dir = Path("build/clue_canon/test_resume_pending")
        if report_dir.exists():
            shutil.rmtree(report_dir)
        state_path = Path("build/clue_canon/test_resume_pending_state.json")
        state_path.write_text(json.dumps({
            "version": 2,
            "dry_run": True,
            "apply": False,
            "word": None,
            "limit": None,
            "min_count": 1,
            "referee_batch_size": 50,
            "progress_every": 25,
            "word_queue_size": 50,
            "report_dir": str(report_dir),
            "review_path": str(report_dir / "disagreements.jsonl"),
            "quarantine_path": str(report_dir / "quarantine.jsonl"),
            "stats": {},
            "completed_words": [],
            "pending_words": ["STALE", "APA"],
            "active_words": [],
        }), encoding="utf-8")

        with patch("generator.clue_canon.ClueCanonStore", return_value=store), \
             patch("generator.clue_canon.create_client", return_value=object()), \
             patch("generator.clue_canon.ClueCanonService", return_value=SimpleNamespace(_run_referee_adaptive_batch=lambda _requests: None)), \
             patch("generator.clue_canon.LmRuntime", return_value=SimpleNamespace(switch_count=0, activation_count=0)), \
             patch("generator.clue_canon.install_process_logging", return_value=SimpleNamespace(restore=lambda: None)):
            status = run_backfill(
                dry_run=True,
                apply=False,
                word=None,
                limit=None,
                min_count=1,
                referee_batch_size=50,
                resume=False,
                state_path=str(state_path),
                progress_every=25,
                word_queue_size=50,
            )

        self.assertEqual(0, status)
        summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(1, summary["resume_pending_words_dropped"])
        self.assertEqual(0, summary["resume_active_words_dropped"])

    def test_run_backfill_resume_preserves_active_word_missing_from_current_bucket(self):
        class _Store:
            def __init__(self):
                self.created = []

            def is_enabled(self):
                return True

            def fetch_backfill_source_rows(self, *, word_normalized=None, extra_fields=()):
                return [
                    {
                        "id": "1",
                        "word_normalized": "APA",
                        "word_original": "apa",
                        "definition": "Lichid vital.",
                        "verify_note": "",
                        "verified": True,
                    }
                ]

            def count_clue_rows(self, *, verified=None, canonical_missing_only=False, word_normalized=None):
                counts = {
                    (None, False, None): 1,
                    (True, False, None): 1,
                    (True, True, None): 1,
                }
                return counts[(verified, canonical_missing_only, word_normalized)]

            def prefetch_canonical_variants(self, words_normalized):
                return {word: [] for word in words_normalized}

            def fetch_canonical_variants(self, *_args, **_kwargs):
                return []

            def create_canonical_definition(self, record):
                self.created.append(record.word_normalized)
                return SimpleNamespace(id=f"canon-{record.word_normalized}")

            def attach_clues(self, *_args, **_kwargs):
                return 1

            def insert_aliases(self, *_args, **_kwargs):
                return 0

        store = _Store()
        report_dir = Path("build/clue_canon/test_resume_active")
        if report_dir.exists():
            shutil.rmtree(report_dir)
        state_path = Path("build/clue_canon/test_resume_active_state.json")
        active_payload = {
            "word": "OLD",
            "input_count": 1,
            "merge_state": {
                "word": "OLD",
                "clusters": [{
                    "primary": {
                        "id": "old-1",
                        "word_normalized": "OLD",
                        "word_original": "old",
                        "definition": "Definiție veche.",
                        "definition_norm": "definitie veche",
                        "word_type": "",
                        "usage_label": "",
                        "verified": True,
                        "semantic_score": None,
                        "rebus_score": None,
                        "creativity_score": None,
                        "verify_note": "",
                        "canonical_definition_id": None,
                    },
                    "members": [{
                        "id": "old-1",
                        "word_normalized": "OLD",
                        "word_original": "old",
                        "definition": "Definiție veche.",
                        "definition_norm": "definitie veche",
                        "word_type": "",
                        "usage_label": "",
                        "verified": True,
                        "semantic_score": None,
                        "rebus_score": None,
                        "creativity_score": None,
                        "verify_note": "",
                        "canonical_definition_id": None,
                    }],
                    "canonical_id": None,
                    "same_meaning_votes": None,
                    "winner_votes": None,
                    "decision_note": "",
                }],
                "selected": [],
                "next_cluster_index": 0,
                "current": None,
                "compare_index": 0,
                "waiting": False,
            },
            "comparisons_done": 0,
            "unresolved": False,
        }
        state_path.write_text(json.dumps({
            "version": 2,
            "dry_run": True,
            "apply": False,
            "word": None,
            "limit": None,
            "min_count": 1,
            "referee_batch_size": 50,
            "progress_every": 25,
            "word_queue_size": 50,
            "report_dir": str(report_dir),
            "review_path": str(report_dir / "disagreements.jsonl"),
            "quarantine_path": str(report_dir / "quarantine.jsonl"),
            "stats": {},
            "completed_words": [],
            "pending_words": [],
            "active_words": [active_payload],
        }), encoding="utf-8")

        with patch("generator.clue_canon.ClueCanonStore", return_value=store), \
             patch("generator.clue_canon.create_client", return_value=object()), \
             patch("generator.clue_canon.ClueCanonService", return_value=SimpleNamespace(_run_referee_adaptive_batch=lambda _requests: None)), \
             patch("generator.clue_canon.LmRuntime", return_value=SimpleNamespace(switch_count=0, activation_count=0)), \
             patch("generator.clue_canon.install_process_logging", return_value=SimpleNamespace(restore=lambda: None)):
            status = run_backfill(
                dry_run=True,
                apply=False,
                word=None,
                limit=None,
                min_count=1,
                referee_batch_size=50,
                resume=False,
                state_path=str(state_path),
                progress_every=25,
                word_queue_size=50,
            )

        self.assertEqual(0, status)
        summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(2, summary["processed_words"])
        self.assertEqual(0, summary["resume_active_words_dropped"])

    def test_run_backfill_resume_quarantines_stale_waiting_words(self):
        class _Store:
            def is_enabled(self):
                return True

            def fetch_backfill_source_rows(self, *, word_normalized=None, extra_fields=()):
                return []

            def count_clue_rows(self, *, verified=None, canonical_missing_only=False, word_normalized=None):
                counts = {
                    (None, False, None): 0,
                    (True, False, None): 0,
                    (True, True, None): 0,
                }
                return counts[(verified, canonical_missing_only, word_normalized)]

            def prefetch_canonical_variants(self, words_normalized):
                return {word: [] for word in words_normalized}

            def fetch_canonical_variants(self, *_args, **_kwargs):
                return []

            def create_canonical_definition(self, record):
                return SimpleNamespace(id=f"canon-{record.word_normalized}")

            def attach_clues(self, *_args, **_kwargs):
                return 1

            def insert_aliases(self, *_args, **_kwargs):
                return 0

        store = _Store()
        report_dir = Path("build/clue_canon/test_resume_stale_wait")
        if report_dir.exists():
            shutil.rmtree(report_dir)
        state_path = Path("build/clue_canon/test_resume_stale_wait_state.json")
        active_payload = {
            "word": "LA",
            "input_count": 1,
            "merge_state": {
                "word": "LA",
                "clusters": [{
                    "primary": {
                        "id": "la-1",
                        "word_normalized": "LA",
                        "word_original": "la",
                        "definition": "Prepoziție pentru loc.",
                        "definition_norm": "prepozitie pentru loc",
                    },
                    "members": [{
                        "id": "la-1",
                        "word_normalized": "LA",
                        "word_original": "la",
                        "definition": "Prepoziție pentru loc.",
                        "definition_norm": "prepozitie pentru loc",
                    }],
                }],
                "selected": [],
                "next_cluster_index": 0,
                "current": {
                    "primary": {
                        "id": "la-1",
                        "word_normalized": "LA",
                        "word_original": "la",
                        "definition": "Prepoziție pentru loc.",
                        "definition_norm": "prepozitie pentru loc",
                    },
                    "members": [{
                        "id": "la-1",
                        "word_normalized": "LA",
                        "word_original": "la",
                        "definition": "Prepoziție pentru loc.",
                        "definition_norm": "prepozitie pentru loc",
                    }],
                },
                "candidate_indexes": [3],
                "compare_index": 0,
                "waiting": True,
                "pending_request_id": "cmp-9",
            },
            "comparisons_done": 12,
            "unresolved": False,
        }
        state_path.write_text(json.dumps({
            "version": 3,
            "dry_run": True,
            "apply": False,
            "word": None,
            "limit": None,
            "min_count": 1,
            "referee_batch_size": 50,
            "progress_every": 25,
            "word_queue_size": 50,
            "report_dir": str(report_dir),
            "review_path": str(report_dir / "disagreements.jsonl"),
            "quarantine_path": str(report_dir / "quarantine.jsonl"),
            "stats": {},
            "completed_words": [],
            "pending_words": [],
            "active_words": [active_payload],
        }), encoding="utf-8")

        with patch("generator.clue_canon.ClueCanonStore", return_value=store), \
             patch("generator.clue_canon.create_client", return_value=object()), \
             patch("generator.clue_canon.ClueCanonService", return_value=SimpleNamespace(_run_referee_adaptive_batch=lambda _requests: None)), \
             patch("generator.clue_canon.LmRuntime", return_value=SimpleNamespace(switch_count=0, activation_count=0)), \
             patch("generator.clue_canon.install_process_logging", return_value=SimpleNamespace(restore=lambda: None)):
            status = run_backfill(
                dry_run=True,
                apply=False,
                word=None,
                limit=None,
                min_count=1,
                referee_batch_size=50,
                resume=False,
                state_path=str(state_path),
                progress_every=25,
                word_queue_size=50,
            )

        self.assertEqual(0, status)
        summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
        quarantine_rows = [
            json.loads(line)
            for line in (report_dir / "quarantine.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(1, summary["resume_stale_wait_words"])
        self.assertEqual(1, summary["deferred_due_to_resume_stale_wait"])
        self.assertEqual("resume_stale_wait", quarantine_rows[0]["reason"])
        self.assertTrue(quarantine_rows[0]["deferred"])
        self.assertEqual("cmp-9", quarantine_rows[0]["pending_request_id"])
        self.assertEqual([3], quarantine_rows[0]["candidate_indexes"])

    def test_run_backfill_resume_drops_malformed_active_and_dedupes_pending(self):
        class _Store:
            def __init__(self):
                self.created = []

            def is_enabled(self):
                return True

            def fetch_backfill_source_rows(self, *, word_normalized=None, extra_fields=()):
                return [
                    {
                        "id": "1",
                        "word_normalized": "APA",
                        "word_original": "apa",
                        "definition": "Lichid vital.",
                        "verify_note": "",
                        "verified": True,
                    }
                ]

            def count_clue_rows(self, *, verified=None, canonical_missing_only=False, word_normalized=None):
                counts = {
                    (None, False, None): 1,
                    (True, False, None): 1,
                    (True, True, None): 1,
                }
                return counts[(verified, canonical_missing_only, word_normalized)]

            def prefetch_canonical_variants(self, words_normalized):
                return {word: [] for word in words_normalized}

            def fetch_canonical_variants(self, *_args, **_kwargs):
                return []

            def create_canonical_definition(self, record):
                self.created.append(record.word_normalized)
                return SimpleNamespace(id=f"canon-{record.word_normalized}")

            def attach_clues(self, *_args, **_kwargs):
                return 1

            def insert_aliases(self, *_args, **_kwargs):
                return 0

        store = _Store()
        report_dir = Path("build/clue_canon/test_resume_dedupe")
        if report_dir.exists():
            shutil.rmtree(report_dir)
        state_path = Path("build/clue_canon/test_resume_dedupe_state.json")
        state_path.write_text(json.dumps({
            "version": 2,
            "dry_run": True,
            "apply": False,
            "word": None,
            "limit": None,
            "min_count": 1,
            "referee_batch_size": 50,
            "progress_every": 25,
            "word_queue_size": 50,
            "report_dir": str(report_dir),
            "review_path": str(report_dir / "disagreements.jsonl"),
            "quarantine_path": str(report_dir / "quarantine.jsonl"),
            "stats": {},
            "completed_words": ["DONE"],
            "pending_words": ["APA", "APA", "DONE", "STALE"],
            "active_words": [{"word": "", "merge_state": {}}],
        }), encoding="utf-8")

        with patch("generator.clue_canon.ClueCanonStore", return_value=store), \
             patch("generator.clue_canon.create_client", return_value=object()), \
             patch("generator.clue_canon.ClueCanonService", return_value=SimpleNamespace(_run_referee_adaptive_batch=lambda _requests: None)), \
             patch("generator.clue_canon.LmRuntime", return_value=SimpleNamespace(switch_count=0, activation_count=0)), \
             patch("generator.clue_canon.install_process_logging", return_value=SimpleNamespace(restore=lambda: None)):
            status = run_backfill(
                dry_run=True,
                apply=False,
                word=None,
                limit=None,
                min_count=1,
                referee_batch_size=50,
                resume=False,
                state_path=str(state_path),
                progress_every=25,
                word_queue_size=50,
            )

        self.assertEqual(0, status)
        summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(1, summary["resume_active_words_dropped"])
        self.assertEqual(2, summary["resume_pending_words_dropped"])
        self.assertEqual(1, summary["resume_words_deduped"])

    def test_run_backfill_throttles_checkpoint_writes_during_referee_churn(self):
        class _Store:
            def __init__(self):
                self.rows = [
                    {
                        "id": "1",
                        "word_normalized": "APA",
                        "word_original": "apa",
                        "definition": "Lichid vital.",
                        "verify_note": "",
                        "verified": True,
                    },
                    {
                        "id": "2",
                        "word_normalized": "APA",
                        "word_original": "apa",
                        "definition": "Lichid vital esențial.",
                        "verify_note": "",
                        "verified": True,
                    },
                ]

            def is_enabled(self):
                return True

            def fetch_backfill_source_rows(self, *, word_normalized=None, extra_fields=()):
                return list(self.rows)

            def count_clue_rows(self, *, verified=None, canonical_missing_only=False, word_normalized=None):
                counts = {
                    (None, False, None): 2,
                    (True, False, None): 2,
                    (True, True, None): 2,
                }
                return counts[(verified, canonical_missing_only, word_normalized)]

            def prefetch_canonical_variants(self, words_normalized):
                return {word: [] for word in words_normalized}

            def fetch_canonical_variants(self, *_args, **_kwargs):
                return []

            def create_canonical_definition(self, _record):
                return SimpleNamespace(id="canon-1")

            def attach_clues(self, *_args, **_kwargs):
                return 1

            def insert_aliases(self, *_args, **_kwargs):
                return 1

        adaptive = SimpleNamespace(
            results={
                "cmp-1": DefinitionRefereeResult(
                    same_meaning_votes=2,
                    better_a_votes=0,
                    better_b_votes=2,
                    equal_votes=0,
                    votes=[],
                )
            },
            total_votes=2,
            phase1_requests=1,
            phase2_requests=1,
            invalid_compare_json_primary=0,
            invalid_compare_json_secondary=0,
        )
        store = _Store()
        report_dir = Path("build/clue_canon/test_checkpoint")
        if report_dir.exists():
            for path in sorted(report_dir.rglob("*"), reverse=True):
                if path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
        state_path = Path("build/clue_canon/test_checkpoint_state.json")
        if state_path.exists():
            state_path.unlink()

        with patch("generator.clue_canon.ClueCanonStore", return_value=store), \
             patch("generator.clue_canon.create_client", return_value=object()), \
             patch("generator.clue_canon.ClueCanonService", return_value=SimpleNamespace(_run_referee_adaptive_batch=lambda _requests: adaptive)), \
             patch("generator.clue_canon.LmRuntime", return_value=SimpleNamespace(switch_count=0, activation_count=0)), \
             patch("generator.clue_canon.install_process_logging", return_value=SimpleNamespace(restore=lambda: None)), \
             patch("generator.clue_canon.path_timestamp", return_value="test_checkpoint"), \
             patch("generator.clue_canon._write_state") as write_state, \
             patch("generator.clue_canon.time.monotonic", return_value=0.0):
            status = run_backfill(
                dry_run=True,
                apply=False,
                word=None,
                limit=None,
                min_count=1,
                referee_batch_size=50,
                resume=False,
                state_path=str(state_path),
                progress_every=25,
                word_queue_size=50,
            )

        self.assertEqual(0, status)
        self.assertEqual(4, write_state.call_count)
        summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
        self.assertIn("referee_batches_launched", summary)
        self.assertIn("avg_requests_per_referee_batch", summary)
        self.assertIn("avg_votes_per_model_activation", summary)
        self.assertIn("switches_per_committed_word", summary)

    def test_run_backfill_advances_stagnant_word_by_keeping_clusters_separate(self):
        class _Store:
            def __init__(self):
                self.rows = [
                    {
                        "id": "1",
                        "word_normalized": "APA",
                        "word_original": "apa",
                        "definition": "Sens alfa comun pentru testul unu.",
                        "verify_note": "",
                        "verified": True,
                    },
                    {
                        "id": "2",
                        "word_normalized": "APA",
                        "word_original": "apa",
                        "definition": "Sens alfa comun pentru testul doi.",
                        "verify_note": "",
                        "verified": True,
                    },
                    {
                        "id": "3",
                        "word_normalized": "APA",
                        "word_original": "apa",
                        "definition": "Sens alfa comun pentru testul trei.",
                        "verify_note": "",
                        "verified": True,
                    },
                ]
                self.created = 0
                self.attach_batches = []

            def is_enabled(self):
                return True

            def fetch_backfill_source_rows(self, *, word_normalized=None, extra_fields=()):
                return list(self.rows)

            def count_clue_rows(self, *, verified=None, canonical_missing_only=False, word_normalized=None):
                counts = {
                    (None, False, None): 3,
                    (True, False, None): 3,
                    (True, True, None): 3,
                }
                return counts[(verified, canonical_missing_only, word_normalized)]

            def prefetch_canonical_variants(self, words_normalized):
                return {word: [] for word in words_normalized}

            def fetch_canonical_variants(self, *_args, **_kwargs):
                return []

            def create_canonical_definition(self, _record):
                self.created += 1
                return SimpleNamespace(id=f"canon-{self.created}")

            def attach_clues(self, clue_ids, *, canonical_definition_id):
                self.attach_batches.append((list(clue_ids), canonical_definition_id))
                return 1

            def insert_aliases(self, *, canonical_definition_id, word_normalized, aliases):
                return 1

        def _adaptive(_requests):
            return SimpleNamespace(
                results={
                    request.request_id: DefinitionRefereeResult(
                        same_meaning_votes=0,
                        better_a_votes=0,
                        better_b_votes=0,
                        equal_votes=2,
                        votes=[],
                    )
                    for request in _requests
                },
                total_votes=len(_requests) * 2,
                phase1_requests=len(_requests),
                phase2_requests=len(_requests),
                invalid_compare_json_primary=0,
                invalid_compare_json_secondary=0,
            )

        store = _Store()
        report_dir = Path("build/clue_canon/test_defer")
        if report_dir.exists():
            shutil.rmtree(report_dir)
        state_path = Path("build/clue_canon/test_defer_state.json")
        if state_path.exists():
            state_path.unlink()

        with patch("generator.clue_canon.ClueCanonStore", return_value=store), \
             patch("generator.clue_canon.create_client", return_value=object()), \
             patch("generator.clue_canon.ClueCanonService", return_value=SimpleNamespace(_run_referee_adaptive_batch=_adaptive)), \
             patch("generator.clue_canon.LmRuntime", return_value=SimpleNamespace(switch_count=0, activation_count=0)), \
             patch("generator.clue_canon.install_process_logging", return_value=SimpleNamespace(restore=lambda: None)), \
             patch("generator.clue_canon.path_timestamp", return_value="test_defer"):
            status = run_backfill(
                dry_run=False,
                apply=True,
                word=None,
                limit=None,
                min_count=1,
                referee_batch_size=50,
                resume=False,
                state_path=str(state_path),
                progress_every=25,
                word_queue_size=50,
                max_stagnant_comparisons=2,
            )

        self.assertEqual(0, status)
        summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
        quarantine_rows = [
            json.loads(line)
            for line in (report_dir / "quarantine.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

        self.assertEqual(0, summary["deferred_words"])
        self.assertEqual(0, summary["deferred_due_to_stagnation"])
        self.assertEqual(3, summary["keep_separate_decisions"])
        self.assertEqual("distinct_sense_survivor", quarantine_rows[0]["reason"])
        self.assertFalse(quarantine_rows[0]["deferred"])

    def test_build_parser_supports_backfill_resume_and_audit(self):
        parser = build_parser()

        backfill_args = parser.parse_args(["backfill", "--apply", "--resume", "--progress-every", "5"])
        audit_args = parser.parse_args(["audit", "--output", "build/clue_canon/audit.json"])

        self.assertTrue(backfill_args.resume)
        self.assertEqual(5, backfill_args.progress_every)
        self.assertEqual("audit", audit_args.command)

    def test_run_audit_reports_missing_canonicals_and_legacy_refs(self):
        fake_store = SimpleNamespace(is_enabled=lambda: True)
        rows = [
            {
                "id": "c1",
                "puzzle_id": "p1",
                "canonical_definition_id": None,
                "definition_source": "legacy",
            }
        ]
        output_path = Path("build/clue_canon/test_audit.json")
        if output_path.exists():
            output_path.unlink()

        with patch("generator.clue_canon.ClueCanonStore", return_value=fake_store), \
             patch("generator.clue_canon._fetch_clue_rows", return_value=rows), \
             patch("generator.clue_canon._direct_legacy_code_refs", return_value=[{"file": "generator/x.py", "line": "10", "kind": "legacy_select"}]):
            status = run_audit(output=str(output_path))

        self.assertEqual(1, status)
        report = output_path.read_text(encoding="utf-8")
        self.assertIn('"null_canonical_definition_id": 1', report)
        self.assertIn('"legacy_definition_rows": 1', report)
        self.assertIn("legacy_select", report)

    def test_config_matches_legacy_state_without_word_queue_size(self):
        state = {
            "dry_run": False,
            "apply": True,
            "word": "",
            "limit": None,
            "min_count": 1,
            "referee_batch_size": 50,
            "progress_every": 25,
        }

        self.assertTrue(
            _config_matches_state(
                state,
                dry_run=False,
                apply=True,
                word=None,
                limit=None,
                min_count=1,
                referee_batch_size=50,
                progress_every=25,
                word_queue_size=50,
            )
        )

    def test_load_state_accepts_legacy_current_word_only_checkpoint(self):
        state_path = Path("build/clue_canon/test_legacy_state.json")
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            "version": 1,
            "dry_run": False,
            "apply": True,
            "word": None,
            "limit": None,
            "min_count": 1,
            "referee_batch_size": 50,
            "progress_every": 25,
            "report_dir": "build/clue_canon/r1",
            "review_path": "build/clue_canon/r1/disagreements.jsonl",
            "quarantine_path": "build/clue_canon/r1/quarantine.jsonl",
            "stats": {},
            "completed_words": [],
            "current_word": {
                "word": "APA",
                "input_count": 1,
                "merge_state": {
                    "word": "APA",
                    "clusters": [],
                    "selected": [],
                    "next_cluster_index": 0,
                    "current": None,
                    "compare_index": 0,
                    "waiting": False,
                },
                "comparisons_done": 0,
                "unresolved": False,
            },
        }), encoding="utf-8")

        loaded = _load_state(
            state_path,
            dry_run=False,
            apply=True,
            word=None,
            limit=None,
            min_count=1,
            referee_batch_size=50,
            progress_every=25,
            word_queue_size=50,
        )

        self.assertIsNotNone(loaded)
        self.assertIn("current_word", loaded)

    def test_load_state_rejects_unknown_future_version(self):
        state_path = Path("build/clue_canon/test_future_state.json")
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({
            "version": 999,
            "dry_run": False,
            "apply": True,
            "word": None,
            "limit": None,
            "min_count": 1,
            "referee_batch_size": 50,
            "progress_every": 25,
            "word_queue_size": 50,
        }), encoding="utf-8")

        with self.assertRaises(SystemExit) as ctx:
            _load_state(
                state_path,
                dry_run=False,
                apply=True,
                word=None,
                limit=None,
                min_count=1,
                referee_batch_size=50,
                progress_every=25,
                word_queue_size=50,
            )

        self.assertIn("unsupported version 999", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
