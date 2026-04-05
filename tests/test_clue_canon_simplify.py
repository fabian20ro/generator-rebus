import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from generator.clue_canon import build_parser
from generator.core.ai_clues import validate_rewritten_canonical_definition_locally
from generator.core.clue_canon_simplify import (
    SimplifyCandidatePair,
    SimplifyStats,
    _apply_merge,
    _default_state_path,
    _load_state,
    _write_state,
    build_candidate_pairs,
    choose_existing_survivor,
    run_simplify_fanout,
    sample_candidate_batch,
)
from generator.core.clue_canon_types import CanonicalDefinition


def _canonical(
    *,
    canonical_id: str,
    word: str,
    definition: str,
    word_type: str = "",
    usage_label: str = "",
    verified: bool = True,
    usage_count: int = 1,
) -> CanonicalDefinition:
    return CanonicalDefinition(
        id=canonical_id,
        word_normalized=word,
        word_original_seed=word.lower(),
        definition=definition,
        definition_norm=definition.lower(),
        word_type=word_type,
        usage_label=usage_label,
        verified=verified,
        semantic_score=8,
        rebus_score=7,
        creativity_score=6,
        usage_count=usage_count,
        superseded_by=None,
    )


class ClueCanonSimplifyTests(unittest.TestCase):
    def test_build_candidate_pairs_stays_within_same_bucket(self):
        pairs = build_candidate_pairs([
            _canonical(canonical_id="1", word="LA", definition="Prepoziție care indică locul."),
            _canonical(canonical_id="2", word="LA", definition="Prepoziție care arată locul."),
            _canonical(canonical_id="3", word="LA", definition="Prepoziție care arată locul.", usage_label="(reg.)"),
            _canonical(canonical_id="4", word="AR", definition="Particulă condițională."),
        ])

        self.assertEqual(1, len(pairs))
        self.assertEqual("LA", pairs[0].word)
        self.assertEqual({"1", "2"}, {pairs[0].left_id, pairs[0].right_id})

    def test_sample_candidate_batch_uses_each_canonical_once(self):
        pairs = [
            SimplifyCandidatePair("1::2", "LA", "", "", "1", "2", "a", "b", "a", "b", 5.0),
            SimplifyCandidatePair("1::3", "LA", "", "", "1", "3", "a", "c", "a", "c", 4.0),
            SimplifyCandidatePair("4::5", "AR", "", "", "4", "5", "d", "e", "d", "e", 3.0),
        ]

        batch = sample_candidate_batch(pairs, batch_size=3, rng=__import__("random").Random(1))

        used = []
        for pair in batch:
            used.extend([pair.left_id, pair.right_id])
        self.assertEqual(len(set(used)), len(used))

    def test_validate_rewritten_definition_rejects_prompt_residue(self):
        rejection = validate_rewritten_canonical_definition_locally(
            word="AI",
            definition_a="Cei posedă ceva.",
            definition_b="Formă verbală de posesie.",
            candidate_definition="**Definiția:** formă verbală de posesie",
        )

        self.assertEqual("prompt_residue", rejection)

    def test_validate_rewritten_definition_rejects_family_leak(self):
        rejection = validate_rewritten_canonical_definition_locally(
            word="LA",
            definition_a="Prepoziție care indică locul.",
            definition_b="Prepoziție de destinație.",
            candidate_definition="Prepoziție la loc.",
        )

        self.assertEqual("contains answer or family word", rejection)

    def test_choose_existing_survivor_prefers_verified_then_usage(self):
        winner = choose_existing_survivor(
            _canonical(canonical_id="1", word="LA", definition="Def 1", verified=False, usage_count=10),
            _canonical(canonical_id="2", word="LA", definition="Def 2", verified=True, usage_count=1),
        )

        self.assertEqual("2", winner.id)

    def test_apply_merge_dry_run_does_not_touch_store(self):
        store = SimpleNamespace()
        survivor_id = _apply_merge(
            store=store,
            left=_canonical(canonical_id="1", word="LA", definition="Prepoziție care indică locul."),
            right=_canonical(canonical_id="2", word="LA", definition="Prepoziție care arată locul."),
            survivor_definition="Prepoziție care indică locul.",
            dry_run=True,
        )

        self.assertTrue(survivor_id.startswith("dry-run:LA:"))

    def test_apply_merge_failure_before_supersede_keeps_sources_unsuperseded(self):
        calls = []

        class _Store:
            def create_canonical_definition(self, record):
                calls.append("create")
                return SimpleNamespace(id="survivor-1")

            def insert_aliases(self, **_kwargs):
                calls.append("aliases")
                return 1

            def repoint_clues_by_canonical_ids(self, *_args, **_kwargs):
                calls.append("repoint")
                raise RuntimeError("boom")

            def mark_canonicals_superseded(self, *_args, **_kwargs):
                calls.append("supersede")

        with self.assertRaises(RuntimeError):
            _apply_merge(
                store=_Store(),
                left=_canonical(canonical_id="1", word="LA", definition="Prepoziție care indică locul."),
                right=_canonical(canonical_id="2", word="LA", definition="Prepoziție care arată locul."),
                survivor_definition="Prepoziție care indică locul.",
                dry_run=False,
            )

        self.assertEqual(["create", "aliases", "repoint"], calls)

    def test_simplify_state_roundtrip_restores_current_batch(self):
        rng = __import__("random").Random(7)
        stats = SimplifyStats(pairs_sampled=3)
        pair = SimplifyCandidatePair("1::2", "LA", "", "", "1", "2", "a", "b", "a", "b", 1.5)
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            report_dir = Path(tmpdir) / "report"
            _write_state(
                state_path,
                rng=rng,
                report_dir=report_dir,
                stats=stats,
                attempted_pair_keys={"1::2"},
                cooldown_pair_keys=set(),
                current_batch=[pair],
                word="LA",
                batch_size=10,
                idle_sleep_seconds=1,
                dry_run=True,
                apply=False,
                pool_version=2,
            )
            loaded = _load_state(
                state_path,
                dry_run=True,
                apply=False,
                word="LA",
                batch_size=10,
                idle_sleep_seconds=1,
            )

        self.assertIsNotNone(loaded)
        _rng, _report_dir, loaded_stats, attempted, cooldown, current_batch, pool_version = loaded
        self.assertEqual(3, loaded_stats.pairs_sampled)
        self.assertEqual({"1::2"}, attempted)
        self.assertEqual([], sorted(cooldown))
        self.assertEqual(["1::2"], [item.key for item in current_batch])
        self.assertEqual(2, pool_version)

    def test_run_simplify_fanout_skips_invalid_compare_and_exits_after_idle(self):
        pair_rows = [
            _canonical(canonical_id="1", word="LA", definition="Prepoziție care indică locul."),
            _canonical(canonical_id="2", word="LA", definition="Prepoziție care arată locul."),
        ]

        class _Store:
            def is_enabled(self):
                return True

            def fetch_active_canonical_variants(self, word_normalized=None):
                return list(pair_rows)

            def fetch_active_canonical_variants_for_words(self, words_normalized):
                return list(pair_rows)

        runtime = SimpleNamespace(
            activation_count=0,
            switch_count=0,
            activate=lambda *_args, **_kwargs: None,
        )

        invalid_attempt = SimpleNamespace(vote=None, parse_status="invalid_json")
        ok_attempt = SimpleNamespace(
            vote=SimpleNamespace(same_meaning=True),
            parse_status="ok",
        )

        with tempfile.TemporaryDirectory() as tmpdir, \
             patch("generator.core.clue_canon_simplify.compare_definition_variants_attempt", side_effect=[invalid_attempt, ok_attempt]), \
             patch("generator.core.clue_canon_simplify.time.sleep", return_value=None):
            result = run_simplify_fanout(
                store=_Store(),
                client=object(),
                runtime=runtime,
                dry_run=True,
                apply=False,
                batch_size=10,
                state_path=str(Path(tmpdir) / "state.json"),
                report_dir=str(Path(tmpdir) / "report"),
                idle_sleep_seconds=0,
                stop_after_idle_cycles=1,
            )
            summary_text = (Path(tmpdir) / "report" / "summary.json").read_text(encoding="utf-8")

        self.assertEqual(0, result)
        self.assertIn('"compare_invalid": 1', summary_text)

    def test_parser_accepts_simplify_fanout_command(self):
        args = build_parser().parse_args(["simplify-fanout", "--dry-run", "--batch-size", "5"])

        self.assertEqual("simplify-fanout", args.command)
        self.assertTrue(args.dry_run)
        self.assertEqual(5, args.batch_size)

    def test_wrapper_defaults_to_apply_mode(self):
        wrapper = Path("run_clue_canon_simplify.sh").read_text(encoding="utf-8")

        self.assertIn('args=(--apply)', wrapper)
        self.assertIn("simplify-fanout", wrapper)


if __name__ == "__main__":
    unittest.main()
