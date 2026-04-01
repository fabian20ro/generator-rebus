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
from .core.clue_canon_types import (
    BackfillStats,
    ClueDefinitionRecord,
    DefinitionRefereeInput,
    DefinitionRefereeResult,
)
from .core.clue_logging import log_canonical_event
from .core.clue_rating import (
    extract_creativity_score,
    extract_rebus_score,
    extract_semantic_score,
)
from .core.lm_runtime import LmRuntime
from .core.runtime_logging import log, path_timestamp

DEFAULT_REFEREE_BATCH_SIZE = 50


@dataclass
class _WorkingCluster:
    primary: ClueDefinitionRecord
    members: list[ClueDefinitionRecord] = field(default_factory=list)
    same_meaning_votes: int | None = None
    winner_votes: int | None = None
    decision_note: str = ""


@dataclass
class _MergeState:
    word: str
    clusters: list[_WorkingCluster]
    selected: list[_WorkingCluster] = field(default_factory=list)
    next_cluster_index: int = 0
    current: _WorkingCluster | None = None
    compare_index: int = 0
    waiting: bool = False

    def finished(self) -> bool:
        return (
            not self.waiting
            and self.current is None
            and self.next_cluster_index >= len(self.clusters)
        )


@dataclass(frozen=True)
class _PendingReferee:
    request_id: str
    state_index: int
    existing_index: int


def _fetch_clue_rows(store: ClueCanonStore) -> list[dict]:
    return store.fetch_clue_rows()


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


def _collect_pending_referees(
    states: list[_MergeState],
    *,
    max_requests: int,
    next_request_id: int,
) -> tuple[list[DefinitionRefereeInput], list[_PendingReferee], int]:
    requests: list[DefinitionRefereeInput] = []
    pending: list[_PendingReferee] = []
    for state_index, state in enumerate(states):
        if state.waiting:
            continue
        while len(requests) < max_requests:
            if state.current is None:
                if state.next_cluster_index >= len(state.clusters):
                    break
                state.current = state.clusters[state.next_cluster_index]
                state.next_cluster_index += 1
                state.compare_index = 0
            while state.compare_index < len(state.selected):
                existing = state.selected[state.compare_index]
                if _likely_cluster_match(state.current.primary, existing.primary):
                    request_id = f"cmp-{next_request_id}"
                    next_request_id += 1
                    requests.append(
                        DefinitionRefereeInput(
                            request_id=request_id,
                            word=state.word,
                            answer_length=len(state.word),
                            definition_a=state.current.primary.definition,
                            definition_b=existing.primary.definition,
                        )
                    )
                    pending.append(
                        _PendingReferee(
                            request_id=request_id,
                            state_index=state_index,
                            existing_index=state.compare_index,
                        )
                    )
                    state.waiting = True
                    break
                state.compare_index += 1
            if state.waiting:
                break
            state.selected.append(state.current)
            state.current = None
        if len(requests) >= max_requests:
            break
    return requests, pending, next_request_id


def _apply_referee_results(
    states: list[_MergeState],
    pending: list[_PendingReferee],
    results: dict[str, DefinitionRefereeResult],
    review_handle,
    stats: BackfillStats,
) -> None:
    for item in pending:
        state = states[item.state_index]
        state.waiting = False
        cluster = state.current
        if cluster is None:
            continue
        existing = state.selected[item.existing_index]
        result = results[item.request_id]
        if result.merge_allowed and result.winner == "B":
            existing.members.extend(cluster.members)
            existing.same_meaning_votes = result.same_meaning_votes
            existing.winner_votes = result.winner_votes
            existing.decision_note = "existing canonical kept"
            stats.near_merges += 1
            log_canonical_event(
                "merge-keep",
                clue_ref=state.word,
                candidate_definition=cluster.primary.definition,
                canonical_definition=existing.primary.definition,
                detail=f"votes same={result.same_meaning_votes}/6 winner={result.winner_votes}/6",
            )
            state.current = None
            continue
        if result.merge_allowed and result.winner == "A":
            cluster.members.extend(existing.members)
            cluster.same_meaning_votes = result.same_meaning_votes
            cluster.winner_votes = result.winner_votes
            cluster.decision_note = "new candidate promoted"
            state.selected[item.existing_index] = cluster
            stats.near_merges += 1
            log_canonical_event(
                "merge-promote",
                clue_ref=state.word,
                candidate_definition=existing.primary.definition,
                canonical_definition=cluster.primary.definition,
                detail=f"votes same={result.same_meaning_votes}/6 winner={result.winner_votes}/6",
            )
            state.current = None
            continue
        bucket = classify_disagreement_bucket(result)
        if bucket == 3:
            stats.disagreement_3_of_6 += 1
        elif bucket == 4:
            stats.disagreement_4_of_6 += 1
        if result.disagreement:
            log_canonical_event(
                "disagreement",
                clue_ref=state.word,
                candidate_definition=cluster.primary.definition,
                canonical_definition=existing.primary.definition,
                detail=(
                    f"same={result.same_meaning_votes}/6 "
                    f"betterA={result.better_a_votes} betterB={result.better_b_votes}"
                ),
            )
            review_handle.write(json.dumps({
                "word": state.word,
                "definition_a": cluster.primary.definition,
                "definition_b": existing.primary.definition,
                "same_meaning_votes": result.same_meaning_votes,
                "better_a_votes": result.better_a_votes,
                "better_b_votes": result.better_b_votes,
                "equal_votes": result.equal_votes,
            }, ensure_ascii=False) + "\n")
            state.selected.append(cluster)
            state.current = None
            continue
        state.compare_index += 1


def _merge_word_batch(
    service: ClueCanonService,
    bucket_batch: list[tuple[str, list[_WorkingCluster]]],
    review_handle,
    stats: BackfillStats,
    *,
    referee_batch_size: int,
) -> list[tuple[str, list[_WorkingCluster]]]:
    # Round-robin word states let backfill collect many pending comparisons
    # before switching models, while preserving per-word merge order.
    states = [
        _MergeState(word=word, clusters=clusters)
        for word, clusters in bucket_batch
    ]
    next_request_id = 1
    while True:
        requests, pending, next_request_id = _collect_pending_referees(
            states,
            max_requests=max(referee_batch_size, 1),
            next_request_id=next_request_id,
        )
        if not requests:
            if all(state.finished() for state in states):
                break
            continue
        log(
            "clue_canon referee batch "
            f"comparisons={len(requests)} words={len({request.word for request in requests})}"
        )
        results = service._run_referee_batch(requests)
        _apply_referee_results(states, pending, results, review_handle, stats)
    return [(state.word, state.selected) for state in states]


def _chunked(items: list[tuple[str, list[ClueDefinitionRecord]]], size: int) -> list[list[tuple[str, list[ClueDefinitionRecord]]]]:
    chunk_size = max(size, 1)
    return [items[index:index + chunk_size] for index in range(0, len(items), chunk_size)]


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
    backfill.add_argument(
        "--referee-batch-size",
        type=int,
        default=DEFAULT_REFEREE_BATCH_SIZE,
        help="How many near-duplicate comparisons to referee per batch before switching models.",
    )
    return parser


def run_backfill(
    *,
    dry_run: bool,
    apply: bool,
    word: str | None,
    limit: int | None,
    min_count: int,
    referee_batch_size: int,
) -> int:
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
        for bucket_chunk in _chunked(buckets, referee_batch_size):
            prepared_chunk = [
                (bucket_word, _build_initial_clusters(bucket_rows, stats))
                for bucket_word, bucket_rows in bucket_chunk
            ]
            merged_chunk = _merge_word_batch(
                service,
                prepared_chunk,
                review_handle,
                stats,
                referee_batch_size=referee_batch_size,
            )
            merged_by_word = dict(merged_chunk)
            for bucket_word, bucket_rows in bucket_chunk:
                clusters = merged_by_word[bucket_word]
                before = len(bucket_rows)
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
        "referee_batch_size": referee_batch_size,
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
            referee_batch_size=args.referee_batch_size,
        )
    raise SystemExit(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
