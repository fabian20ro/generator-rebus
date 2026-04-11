from __future__ import annotations

from pathlib import Path

from rebus_generator.platform.llm.definition_referee import compare_definition_variants_attempt
from rebus_generator.platform.llm.ai_clues import rewrite_merged_canonical_definition, validate_merged_canonical_definition
from rebus_generator.workflows.canonicals.simplify import (
    _append_jsonl,
    SimplifyStats,
    apply_simplify_merge,
    choose_existing_survivor,
    find_simplify_pair_rows,
    load_simplify_bucket,
    refresh_simplify_bucket_rows,
    should_rewrite_survivor,
    update_top_reductions,
)
from rebus_generator.platform.llm.models import PRIMARY_MODEL, SECONDARY_MODEL
from .base import JobState


class SimplifyJobState(JobState):
    def __init__(self, item) -> None:
        super().__init__(item)
        self.stage = "fetch_bucket"
        self.word = str(item.payload["word"])
        self.buckets: dict[tuple[str, str, str], list[object]] = {}
        self.batch_pairs: list[object] = []
        self.primary_votes: dict[str, object] = {}
        self.secondary_votes: dict[str, object] = {}
        self.approved_pairs: list[tuple[object, object, object, str, bool]] = []
        self.pending_rewrite_pairs: list[tuple[object, object, object]] = []
        self.rewritten_definitions: dict[str, str] = {}
        self.stats = SimplifyStats()
        self.report_dir: Path | None = None
        self.merges_path: Path | None = None
        self.skipped_path: Path | None = None

    def next_steps(self, ctx):
        if self.status != "active":
            return []
        if self.stage == "fetch_bucket":
            return [self._non_llm_step("fetch_bucket", "simplify_fetch_bucket", self._fetch_bucket)]
        if self.stage == "compare_gemma":
            return [self._llm_step("compare_gemma", "simplify_compare_gemma", PRIMARY_MODEL.model_id, self._compare_gemma)]
        if self.stage == "compare_eurollm":
            return [self._llm_step("compare_eurollm", "simplify_compare_eurollm", SECONDARY_MODEL.model_id, self._compare_eurollm)]
        if self.stage == "plan_survivors":
            return [self._non_llm_step("plan_survivors", "simplify_plan_survivors", self._plan_survivors)]
        if self.stage == "rewrite_secondary":
            return [self._llm_step("rewrite_secondary", "simplify_rewrite_secondary", SECONDARY_MODEL.model_id, self._rewrite_secondary)]
        if self.stage == "validate_primary":
            return [self._llm_step("validate_primary", "simplify_validate_primary", PRIMARY_MODEL.model_id, self._validate_primary)]
        if self.stage == "apply_merge":
            return [self._non_llm_step("apply_merge", "simplify_apply_merge", self._apply_merge)]
        return []

    def _fetch_bucket(self, ctx):
        self.report_dir = ctx.run_dir / "simplify" / self.word
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.merges_path = self.report_dir / "merges.jsonl"
        self.skipped_path = self.report_dir / "skipped.jsonl"
        self.buckets, self.batch_pairs = load_simplify_bucket(
            ctx.store,
            word=self.word,
            batch_size=ctx.simplify_batch_size,
        )
        if not self.batch_pairs:
            return self._complete(0, detail=f"word={self.word} no_pairs")
        self.stats.pairs_sampled += len(self.batch_pairs)
        self._progress("compare_gemma", detail=f"pairs={len(self.batch_pairs)}")
        return None

    def _compare_gemma(self, ctx):
        self.primary_votes = {
            pair.key: compare_definition_variants_attempt(
                ctx.ai_client,
                pair.word,
                len(pair.word),
                pair.left_definition,
                pair.right_definition,
                model=PRIMARY_MODEL.model_id,
            )
            for pair in self.batch_pairs
        }
        self._progress("compare_eurollm", detail=f"pairs={len(self.batch_pairs)}")
        return self.primary_votes

    def _compare_eurollm(self, ctx):
        self.secondary_votes = {
            pair.key: compare_definition_variants_attempt(
                ctx.ai_client,
                pair.word,
                len(pair.word),
                pair.left_definition,
                pair.right_definition,
                model=SECONDARY_MODEL.model_id,
            )
            for pair in self.batch_pairs
        }
        self.stats.pairs_compared += len(self.batch_pairs) * 2
        self._progress("plan_survivors", detail=f"pairs={len(self.batch_pairs)}")
        return self.secondary_votes

    def _plan_survivors(self, ctx):
        self.approved_pairs = []
        self.pending_rewrite_pairs = []
        self.rewritten_definitions = {}
        for pair in self.batch_pairs:
            first = self.primary_votes[pair.key]
            second = self.secondary_votes[pair.key]
            if first.vote is None or second.vote is None:
                self.stats.compare_invalid += 1
                if self.skipped_path is not None:
                    _append_jsonl(self.skipped_path, {
                        "word": pair.word,
                        "pair_key": pair.key,
                        "reason": "compare_invalid",
                        "phase1_status": first.parse_status,
                        "phase2_status": second.parse_status,
                    })
                continue
            if not first.vote.same_meaning or not second.vote.same_meaning:
                if self.skipped_path is not None:
                    _append_jsonl(self.skipped_path, {
                        "word": pair.word,
                        "pair_key": pair.key,
                        "reason": "not_same_meaning",
                    })
                continue
            found = find_simplify_pair_rows(pair, self.buckets)
            if found is None:
                if self.skipped_path is not None:
                    _append_jsonl(self.skipped_path, {
                        "word": pair.word,
                        "pair_key": pair.key,
                        "reason": "pair_no_longer_active",
                    })
                continue
            left, right = found
            self.stats.pairs_same_sense += 1
            if should_rewrite_survivor(left, right):
                self.pending_rewrite_pairs.append((pair, left, right))
                continue
            survivor_definition = choose_existing_survivor(left, right).definition
            self.approved_pairs.append((pair, left, right, survivor_definition, False))
        if self.pending_rewrite_pairs:
            self._progress(
                "rewrite_secondary",
                detail=f"approved={len(self.approved_pairs)} rewrites={len(self.pending_rewrite_pairs)}",
            )
            return self.pending_rewrite_pairs
        self._progress("apply_merge", detail=f"approved={len(self.approved_pairs)}")
        return self.approved_pairs

    def _rewrite_secondary(self, ctx):
        for pair, left, right in self.pending_rewrite_pairs:
            rewrite = rewrite_merged_canonical_definition(
                ctx.ai_client,
                word=pair.word,
                definition_a=left.definition,
                definition_b=right.definition,
                model=SECONDARY_MODEL.model_id,
            )
            self.rewritten_definitions[pair.key] = rewrite.definition
        self._progress("validate_primary", detail=f"rewrites={len(self.pending_rewrite_pairs)}")
        return self.rewritten_definitions

    def _validate_primary(self, ctx):
        for pair, left, right in self.pending_rewrite_pairs:
            rewritten_definition = self.rewritten_definitions.get(pair.key, "")
            validation = validate_merged_canonical_definition(
                ctx.ai_client,
                word=pair.word,
                answer_length=len(pair.word),
                definition_a=left.definition,
                definition_b=right.definition,
                candidate_definition=rewritten_definition,
                model=PRIMARY_MODEL.model_id,
            )
            if not validation.accepted:
                self.stats.rewrite_invalid += 1
                self.stats.rewrite_fallback_existing += 1
                rewritten_definition = choose_existing_survivor(left, right).definition
            self.approved_pairs.append((pair, left, right, rewritten_definition, True))
        self._progress("apply_merge", detail=f"approved={len(self.approved_pairs)}")
        return self.approved_pairs

    def _apply_merge(self, ctx):
        touched_words: set[str] = set()
        for pair, left, right, survivor_definition, rewrite_attempted in self.approved_pairs:
            try:
                survivor_id = apply_simplify_merge(
                    store=ctx.store,
                    left=left,
                    right=right,
                    survivor_definition=survivor_definition,
                    dry_run=ctx.dry_run,
                )
            except Exception as exc:
                self.stats.db_failures += 1
                if self.skipped_path is not None:
                    _append_jsonl(self.skipped_path, {
                        "word": pair.word,
                        "pair_key": pair.key,
                        "reason": "db_failure",
                        "error": str(exc),
                    })
                continue
            self.stats.pairs_merged += 1
            update_top_reductions(self.stats, word=pair.word)
            if self.merges_path is not None:
                _append_jsonl(self.merges_path, {
                    "word": pair.word,
                    "pair_key": pair.key,
                    "left_id": left.id,
                    "right_id": right.id,
                    "survivor_id": survivor_id,
                    "survivor_definition": survivor_definition,
                    "rewrite_attempted": rewrite_attempted,
                })
            touched_words.add(pair.word)
        if touched_words:
            refresh_simplify_bucket_rows(
                ctx.store,
                self.buckets,
                touched_words=touched_words,
                word_filter=self.word,
            )
        return self._complete(
            self.stats.pairs_merged,
            detail=(
                f"word={self.word} merged={self.stats.pairs_merged} "
                f"same_sense={self.stats.pairs_same_sense} compare_invalid={self.stats.compare_invalid}"
            ),
        )
