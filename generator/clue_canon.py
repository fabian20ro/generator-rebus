"""Backfill and maintain canonical clue definitions."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .core.ai_clues import create_client
from .core.clue_canon import (
    ClueCanonService,
    build_definition_record,
    build_exact_groups,
    choose_canonical_winner,
    classify_disagreement_bucket,
    content_tokens,
    lexical_similarity,
    update_reduction_stats,
)
from .core.clue_canon_store import ClueCanonStore
from .core.clue_canon_types import BackfillStats, CanonicalDefinition, ClueDefinitionRecord
from .core.clue_rating import (
    extract_creativity_score,
    extract_rebus_score,
    extract_semantic_score,
)
from .core.lm_runtime import LmRuntime
from .core.runtime_logging import path_timestamp

PAGE_SIZE = 1000


@dataclass
class _WorkingCluster:
    primary: ClueDefinitionRecord
    members: list[ClueDefinitionRecord] = field(default_factory=list)
    same_meaning_votes: int | None = None
    winner_votes: int | None = None
    decision_note: str = ""


def _fetch_clue_rows(store: ClueCanonStore) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        batch = (
            store.client.table("crossword_clues")
            .select(
                "id, puzzle_id, word_normalized, word_original, definition, "
                "verify_note, verified, canonical_definition_id"
            )
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def _enrich_rows(rows: list[dict]) -> list[ClueDefinitionRecord]:
    result: list[ClueDefinitionRecord] = []
    for row in rows:
        row = dict(row)
        note = str(row.get("verify_note") or "")
        row["semantic_score"] = extract_semantic_score(note)
        row["rebus_score"] = extract_rebus_score(note)
        row["creativity_score"] = extract_creativity_score(note)
        result.append(build_definition_record(row))
    return result


def _likely_cluster_match(left: ClueDefinitionRecord, right: ClueDefinitionRecord) -> bool:
    shared = len(set(content_tokens(left.definition)) & set(content_tokens(right.definition)))
    similarity = lexical_similarity(left.definition_norm, right.definition_norm)
    return shared >= 2 or similarity >= 0.82


def _word_buckets(
    records: list[ClueDefinitionRecord],
    *,
    target_word: str | None,
    limit: int | None,
    min_count: int,
) -> list[tuple[str, list[ClueDefinitionRecord]]]:
    grouped: dict[str, list[ClueDefinitionRecord]] = defaultdict(list)
    for record in records:
        grouped[record.word_normalized].append(record)
    items = []
    target = str(target_word or "").strip().upper()
    for word, rows in grouped.items():
        if target and word != target:
            continue
        if len(rows) < max(min_count, 1):
            continue
        items.append((word, rows))
    items.sort(key=lambda item: (-len(item[1]), item[0]))
    if limit is not None:
        items = items[:limit]
    return items


def _build_initial_clusters(rows: list[ClueDefinitionRecord], stats: BackfillStats) -> list[_WorkingCluster]:
    clusters: list[_WorkingCluster] = []
    for group in build_exact_groups(rows):
        winner = choose_canonical_winner(group)
        stats.exact_merges += max(0, len(group) - 1)
        clusters.append(_WorkingCluster(primary=winner, members=list(group)))
    clusters.sort(key=lambda cluster: _cluster_sort_key(cluster.primary))
    return clusters


def _merge_clusters(
    service: ClueCanonService,
    word: str,
    clusters: list[_WorkingCluster],
    review_handle,
    stats: BackfillStats,
) -> list[_WorkingCluster]:
    selected: list[_WorkingCluster] = []
    for cluster in clusters:
        merged = False
        for index, existing in enumerate(list(selected)):
            if not _likely_cluster_match(cluster.primary, existing.primary):
                continue
            result = service._run_referee(
                cluster.primary,
                CanonicalDefinition(
                    id=existing.primary.id or f"cluster-{existing.primary.definition_norm}",
                    word_normalized=existing.primary.word_normalized,
                    word_original_seed=existing.primary.word_original,
                    definition=existing.primary.definition,
                    definition_norm=existing.primary.definition_norm,
                    verified=existing.primary.verified,
                    semantic_score=existing.primary.semantic_score,
                    rebus_score=existing.primary.rebus_score,
                    creativity_score=existing.primary.creativity_score,
                    usage_count=len(existing.members),
                ),
            )
            if result.merge_allowed and result.winner == "B":
                existing.members.extend(cluster.members)
                existing.same_meaning_votes = result.same_meaning_votes
                existing.winner_votes = result.winner_votes
                existing.decision_note = "existing canonical kept"
                stats.near_merges += 1
                merged = True
                break
            if result.merge_allowed and result.winner == "A":
                cluster.members.extend(existing.members)
                cluster.same_meaning_votes = result.same_meaning_votes
                cluster.winner_votes = result.winner_votes
                cluster.decision_note = "new candidate promoted"
                selected[index] = cluster
                stats.near_merges += 1
                merged = True
                break
            bucket = classify_disagreement_bucket(result)
            if bucket == 3:
                stats.disagreement_3_of_6 += 1
            elif bucket == 4:
                stats.disagreement_4_of_6 += 1
            if result.disagreement:
                review_handle.write(json.dumps({
                    "word": word,
                    "definition_a": cluster.primary.definition,
                    "definition_b": existing.primary.definition,
                    "same_meaning_votes": result.same_meaning_votes,
                    "better_a_votes": result.better_a_votes,
                    "better_b_votes": result.better_b_votes,
                    "equal_votes": result.equal_votes,
                }, ensure_ascii=False) + "\n")
                merged = True
                selected.append(cluster)
                break
        if not merged:
            selected.append(cluster)
    return selected


def _apply_clusters(
    store: ClueCanonStore,
    word: str,
    clusters: list[_WorkingCluster],
    *,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    for cluster in clusters:
        decision = store.create_canonical_definition(cluster.primary)
        if decision is None:
            continue
        for member in cluster.members:
            if member.id:
                store.attach_clue(
                    member.id,
                    "",
                    canonical_definition_id=decision.id,
                    definition=decision.definition,
                )
            if member.id == cluster.primary.id:
                continue
            store.insert_alias(
                canonical_definition_id=decision.id,
                word_normalized=word,
                definition=member.definition,
                definition_norm=member.definition_norm,
                source_clue_id=member.id or None,
                match_type="exact" if member.definition_norm == cluster.primary.definition_norm else "near",
                same_meaning_votes=cluster.same_meaning_votes if member.definition_norm != cluster.primary.definition_norm else None,
                winner_votes=cluster.winner_votes if member.definition_norm != cluster.primary.definition_norm else None,
                decision_source="llm" if member.definition_norm != cluster.primary.definition_norm else "heuristic",
                decision_note=cluster.decision_note,
            )


def _cluster_sort_key(record: ClueDefinitionRecord) -> tuple[object, ...]:
    return (
        0 if record.verified else 1,
        -(record.semantic_score or -1),
        -(record.rebus_score or -1),
        -(record.creativity_score or -1),
        len(record.definition),
        record.id,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Backfill canonical clue definitions.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backfill = subparsers.add_parser("backfill", help="Build canonical clue library from crossword_clues.")
    backfill.add_argument("--dry-run", action="store_true", help="Analyze without DB writes.")
    backfill.add_argument("--apply", action="store_true", help="Persist canonical rows and clue links.")
    backfill.add_argument("--word", help="Only process one normalized word.")
    backfill.add_argument("--limit", type=int, help="Only process the top N hottest words.")
    backfill.add_argument("--min-count", type=int, default=2, help="Only process words with at least this many clues.")
    return parser


def run_backfill(*, dry_run: bool, apply: bool, word: str | None, limit: int | None, min_count: int) -> int:
    if dry_run == apply:
        raise SystemExit("Specify exactly one of --dry-run or --apply")

    store = ClueCanonStore()
    if not store.is_enabled():
        raise SystemExit("Canonical clue schema unavailable")

    client = create_client()
    runtime = LmRuntime(multi_model=True)
    service = ClueCanonService(store=store, client=client, runtime=runtime)

    rows = _fetch_clue_rows(store)
    records = _enrich_rows(rows)
    stats = BackfillStats(total_rows=len(records))
    buckets = _word_buckets(records, target_word=word, limit=limit, min_count=min_count)

    report_dir = Path("build/clue_canon") / path_timestamp()
    report_dir.mkdir(parents=True, exist_ok=True)
    review_path = report_dir / "disagreements.jsonl"

    processed_words = 0
    with review_path.open("w", encoding="utf-8") as review_handle:
        for bucket_word, bucket_rows in buckets:
            before = len(bucket_rows)
            clusters = _build_initial_clusters(bucket_rows, stats)
            clusters = _merge_clusters(service, bucket_word, clusters, review_handle, stats)
            after = len(clusters)
            stats.standalone_canonicals += after
            update_reduction_stats(stats, word=bucket_word, before=before, after=after)
            _apply_clusters(store, bucket_word, clusters, dry_run=dry_run)
            processed_words += 1
            print(f"[{bucket_word}] clues={before} canonicals={after}")

    summary = {
        "total_rows": stats.total_rows,
        "processed_words": processed_words,
        "exact_merges": stats.exact_merges,
        "near_merges": stats.near_merges,
        "disagreement_3_of_6": stats.disagreement_3_of_6,
        "disagreement_4_of_6": stats.disagreement_4_of_6,
        "standalone_canonicals": stats.standalone_canonicals,
        "top_reductions": stats.reduced_words,
        "disagreement_report": str(review_path),
        "mode": "dry-run" if dry_run else "apply",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    (report_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "backfill":
        return run_backfill(
            dry_run=args.dry_run,
            apply=args.apply,
            word=args.word,
            limit=args.limit,
            min_count=args.min_count,
        )
    raise SystemExit(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
