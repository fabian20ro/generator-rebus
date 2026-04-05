"""Backfill and maintain canonical clue definitions."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import re
import time

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
    ComparisonOutcome,
    DefinitionRefereeInput,
    DefinitionRefereeResult,
    QueuedWordState,
    WorkingCluster,
    WordReducerState,
)
from .core.clue_logging import log_canonical_event
from .core.clue_rating import (
    extract_creativity_score,
    extract_rebus_score,
    extract_semantic_score,
)
from .core.lm_runtime import LmRuntime
from .core.runtime_logging import install_process_logging, log, path_timestamp

DEFAULT_REFEREE_BATCH_SIZE = 50
DEFAULT_PROGRESS_EVERY = 25
DEFAULT_MAX_STAGNANT_COMPARISONS = 120
DEFAULT_MIN_REFEREE_BATCH_TO_SWITCH = 10
DEFAULT_STATE_PATH = Path("build/clue_canon/backfill_state.json")
STATE_VERSION = 3
STATE_FLUSH_INTERVAL_SECONDS = 10.0

@dataclass(frozen=True)
class _PendingReferee:
    request_id: str
    state_index: int
    existing_index: int


@dataclass(frozen=True)
class _ResolvedOutcome:
    state_index: int
    outcome: ComparisonOutcome


_WorkingCluster = WorkingCluster
_MergeState = WordReducerState
_QueuedWord = QueuedWordState


def _fetch_clue_rows(store: ClueCanonStore) -> list[dict]:
    return store.fetch_clue_rows()


def _fetch_backfill_rows(store: ClueCanonStore, *, word: str | None) -> list[dict]:
    return store.fetch_backfill_source_rows(word_normalized=word)


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


def _build_boilerplate_tokens(rows: list[ClueDefinitionRecord]) -> tuple[str, ...]:
    if not rows:
        return ()
    token_counts: defaultdict[str, int] = defaultdict(int)
    for row in rows:
        for token in set(content_tokens(row.definition)):
            token_counts[token] += 1
    threshold = max(1, math.ceil(len(rows) * 0.4))
    return tuple(sorted(token for token, count in token_counts.items() if count >= threshold))


def _informative_tokens(definition: str, boilerplate_tokens: tuple[str, ...]) -> set[str]:
    boilerplate = set(boilerplate_tokens)
    return {token for token in content_tokens(definition) if token not in boilerplate}


def _likely_cluster_match(
    left: ClueDefinitionRecord,
    right: ClueDefinitionRecord,
    *,
    boilerplate_tokens: tuple[str, ...] = (),
) -> bool:
    if left.word_type != right.word_type:
        return False
    if left.usage_label != right.usage_label:
        return False
    shared = len(
        _informative_tokens(left.definition, boilerplate_tokens)
        & _informative_tokens(right.definition, boilerplate_tokens)
    )
    similarity = lexical_similarity(left.definition_norm, right.definition_norm)
    return shared >= 2 or similarity >= 0.90


def _cluster_compare_action(
    left: ClueDefinitionRecord,
    right: ClueDefinitionRecord,
    *,
    boilerplate_tokens: tuple[str, ...] = (),
) -> str:
    if left.word_type != right.word_type:
        return "skip"
    if left.usage_label != right.usage_label:
        return "skip"
    if left.definition_norm == right.definition_norm:
        return "exact_merge"
    return (
        "compare"
        if _likely_cluster_match(left, right, boilerplate_tokens=boilerplate_tokens)
        else "skip"
    )


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


def _existing_canonical_clusters(store: ClueCanonStore, word: str) -> list[_WorkingCluster]:
    clusters: list[_WorkingCluster] = []
    for canonical in store.fetch_canonical_variants(word):
        clusters.append(
            _WorkingCluster(
                primary=ClueDefinitionRecord(
                    id=canonical.id,
                    word_normalized=canonical.word_normalized,
                    word_original=canonical.word_original_seed,
                    definition=canonical.definition,
                    definition_norm=canonical.definition_norm,
                    word_type=canonical.word_type,
                    usage_label=canonical.usage_label,
                    verified=canonical.verified,
                    semantic_score=canonical.semantic_score,
                    rebus_score=canonical.rebus_score,
                    creativity_score=canonical.creativity_score,
                ),
                members=[],
                canonical_id=canonical.id,
            )
        )
    return clusters


def _candidate_score(
    current: _WorkingCluster,
    existing: _WorkingCluster,
    *,
    boilerplate_tokens: tuple[str, ...],
) -> tuple[object, ...]:
    informative_shared = len(
        _informative_tokens(current.primary.definition, boilerplate_tokens)
        & _informative_tokens(existing.primary.definition, boilerplate_tokens)
    )
    similarity = lexical_similarity(
        current.primary.definition_norm,
        existing.primary.definition_norm,
    )
    return (
        informative_shared,
        similarity,
        1 if existing.primary.verified else 0,
        existing.primary.semantic_score or -1,
        -(len(existing.primary.definition or "")),
        existing.primary.id,
    )


def _prime_current_cluster(
    item: _QueuedWord,
    *,
    stats: BackfillStats,
) -> ComparisonOutcome | None:
    state = item.merge_state
    if state.current is None:
        if state.next_cluster_index >= len(state.clusters):
            return None
        state.current = state.clusters[state.next_cluster_index]
        state.next_cluster_index += 1
        state.candidate_indexes = []
        state.compare_index = 0
        state.pending_request_id = ""

    if state.candidate_indexes:
        return None

    if state.current is not None and not state.current.primary.verified:
        for selected_index, existing in enumerate(state.selected):
            item.candidate_pairs_considered += 1
            stats.candidate_pairs_considered += 1
            action = _cluster_compare_action(
                state.current.primary,
                existing.primary,
                boilerplate_tokens=state.boilerplate_tokens,
            )
            if action == "exact_merge":
                return ComparisonOutcome(
                    kind="merge_into_existing",
                    existing_index=selected_index,
                )
        return ComparisonOutcome(kind="keep_separate")

    best_compare_index: int | None = None
    best_score: tuple[object, ...] | None = None
    for selected_index, existing in enumerate(state.selected):
        item.candidate_pairs_considered += 1
        stats.candidate_pairs_considered += 1
        action = _cluster_compare_action(
            state.current.primary,
            existing.primary,
            boilerplate_tokens=state.boilerplate_tokens,
        )
        if action == "exact_merge":
            return ComparisonOutcome(
                kind="merge_into_existing",
                existing_index=selected_index,
            )
        if action != "compare":
            continue
        score = _candidate_score(
            state.current,
            existing,
            boilerplate_tokens=state.boilerplate_tokens,
        )
        if best_score is None or score > best_score:
            best_score = score
            best_compare_index = selected_index

    if best_compare_index is None:
        return ComparisonOutcome(kind="keep_separate")

    state.candidate_indexes = [best_compare_index]
    state.compare_index = 0
    return None


def _collect_pending_referees(
    queued_words: list[_QueuedWord],
    *,
    max_requests: int,
    next_request_id: int,
    stats: BackfillStats,
) -> tuple[list[DefinitionRefereeInput], list[_PendingReferee], list[_ResolvedOutcome], int]:
    requests: list[DefinitionRefereeInput] = []
    pending: list[_PendingReferee] = []
    resolved: list[_ResolvedOutcome] = []
    for state_index, item in enumerate(queued_words):
        state = item.merge_state
        if state.waiting or item.blocked_on_referee_error:
            continue
        while len(requests) < max_requests:
            immediate = _prime_current_cluster(item, stats=stats)
            if immediate is not None:
                resolved.append(_ResolvedOutcome(state_index=state_index, outcome=immediate))
                break
            if state.current is None or not state.candidate_indexes:
                break
            existing_index = state.candidate_indexes[state.compare_index]
            existing = state.selected[existing_index]
            request_id = f"cmp-{next_request_id}"
            next_request_id += 1
            state.pending_request_id = request_id
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
                    existing_index=existing_index,
                )
            )
            state.waiting = True
            item.referee_requests_submitted += 1
            stats.referee_requests_submitted += 1
            break
        if len(requests) >= max_requests:
            break
    return requests, pending, resolved, next_request_id


def _apply_terminal_outcome(
    item: _QueuedWord,
    outcome: ComparisonOutcome,
    *,
    stats: BackfillStats,
    review_handle,
) -> None:
    state = item.merge_state
    cluster = state.current
    if outcome.kind == "error_missing_model_votes":
        item.blocked_on_referee_error = True
        item.referee_error_count += 1
        item.unresolved = True
        stats.referee_error_words += 1
        stats.missing_model_vote_errors += 1
        stats.referee_errors += 1
        item.last_referee_error = {
            "request_id": outcome.request_id,
            "missing_model_roles": list(outcome.missing_model_roles),
            "attempts": [
                {
                    "model_id": attempt.model_id,
                    "model_role": attempt.model_role,
                    "valid_vote": attempt.valid_vote,
                    "parse_status": attempt.parse_status,
                    "latency_seconds": attempt.latency_seconds,
                    "error_message": attempt.error_message,
                }
                for attempt in list(outcome.diagnostics.attempts if outcome.diagnostics else [])
            ],
        }
        state.waiting = False
        state.pending_request_id = ""
        missing = ",".join(outcome.missing_model_roles) or "unknown"
        log(
            f"[referee-error] word={state.word} request_id={outcome.request_id} "
            f"missing_model={missing} attempts={len(outcome.diagnostics.attempts) if outcome.diagnostics else 0}"
        )
        return

    item.blocked_on_referee_error = False
    item.last_referee_error = {}
    state.waiting = False
    state.pending_request_id = ""
    state.candidate_indexes = []
    state.compare_index = 0

    if cluster is None:
        return

    if outcome.kind == "merge_into_existing":
        existing = state.selected[outcome.existing_index or 0]
        existing.members.extend(cluster.members)
        if outcome.result is not None:
            existing.same_meaning_votes = outcome.result.same_meaning_votes
            existing.winner_votes = outcome.result.winner_votes
        existing.decision_note = "existing canonical kept"
        if outcome.result is not None:
            stats.near_merges += 1
        else:
            stats.exact_merges += 1
        stats.merge_decisions += 1
        if outcome.result is not None:
            log_canonical_event(
                "merge-keep",
                clue_ref=state.word,
                candidate_definition=cluster.primary.definition,
                canonical_definition=existing.primary.definition,
                detail=f"votes same={outcome.result.same_meaning_votes} winner={outcome.result.winner_votes}",
            )
        stats.referee_merges += 1
        state.current = None
        return

    if outcome.kind == "promote_new_canonical":
        existing = state.selected[outcome.existing_index or 0]
        cluster.members.extend(existing.members)
        if outcome.result is not None:
            cluster.same_meaning_votes = outcome.result.same_meaning_votes
            cluster.winner_votes = outcome.result.winner_votes
        cluster.decision_note = "new candidate promoted"
        state.selected[outcome.existing_index or 0] = cluster
        stats.near_merges += 1
        stats.promote_decisions += 1
        if outcome.result is not None:
            log_canonical_event(
                "merge-promote",
                clue_ref=state.word,
                candidate_definition=existing.primary.definition,
                canonical_definition=cluster.primary.definition,
                detail=f"votes same={outcome.result.same_meaning_votes} winner={outcome.result.winner_votes}",
            )
        stats.referee_merges += 1
        state.current = None
        return

    if outcome.kind == "keep_separate":
        stats.keep_separate_decisions += 1
        stats.referee_keep_separate += 1
        if outcome.result is not None:
            bucket = classify_disagreement_bucket(outcome.result)
            if bucket == 3:
                stats.disagreement_3_of_6 += 1
            elif bucket == 4:
                stats.disagreement_4_of_6 += 1
            review_handle.write(json.dumps({
                "word": state.word,
                "definition_a": cluster.primary.definition,
                "definition_b": state.selected[outcome.existing_index or 0].primary.definition if outcome.existing_index is not None else "",
                "same_meaning_votes": outcome.result.same_meaning_votes,
                "better_a_votes": outcome.result.better_a_votes,
                "better_b_votes": outcome.result.better_b_votes,
                "equal_votes": outcome.result.equal_votes,
            }, ensure_ascii=False) + "\n")
        state.selected.append(cluster)
        state.current = None
        return

    raise ValueError(f"Unknown comparison outcome: {outcome.kind}")


def _build_referee_outcomes(
    pending: list[_PendingReferee],
    results: dict[str, DefinitionRefereeResult],
) -> list[_ResolvedOutcome]:
    resolved: list[_ResolvedOutcome] = []
    for item in pending:
        result = results[item.request_id]
        diagnostics = result.diagnostics
        if diagnostics is not None and not diagnostics.has_both_model_contributions:
            resolved.append(
                _ResolvedOutcome(
                    state_index=item.state_index,
                    outcome=ComparisonOutcome(
                        kind="error_missing_model_votes",
                        request_id=item.request_id,
                        existing_index=item.existing_index,
                        missing_model_roles=diagnostics.missing_model_roles,
                        diagnostics=diagnostics,
                        result=result,
                    ),
                )
            )
            continue
        if result.merge_allowed and result.winner == "B":
            kind = "merge_into_existing"
        elif result.merge_allowed and result.winner == "A":
            kind = "promote_new_canonical"
        else:
            kind = "keep_separate"
        resolved.append(
            _ResolvedOutcome(
                state_index=item.state_index,
                outcome=ComparisonOutcome(
                    kind=kind,
                    request_id=item.request_id,
                    existing_index=item.existing_index,
                    diagnostics=diagnostics,
                    result=result,
                ),
            )
        )
    return resolved


def _collect_referee_launch_batch(
    active_items: list[_QueuedWord],
    *,
    max_requests: int,
    min_requests_to_launch: int,
    next_request_id: int,
    stats: BackfillStats,
    review_handle,
) -> tuple[list[DefinitionRefereeInput], list[_PendingReferee], int, int]:
    requests: list[DefinitionRefereeInput] = []
    pending: list[_PendingReferee] = []
    immediate_resolved_words = 0

    while len(requests) < max_requests:
        batch_requests, batch_pending, resolved, next_request_id = _collect_pending_referees(
            active_items,
            max_requests=max_requests - len(requests),
            next_request_id=next_request_id,
            stats=stats,
        )
        if resolved:
            immediate_resolved_words += len(resolved)
            for entry in resolved:
                _apply_terminal_outcome(
                    active_items[entry.state_index],
                    entry.outcome,
                    stats=stats,
                    review_handle=review_handle,
                )
        if batch_requests:
            requests.extend(batch_requests)
            pending.extend(batch_pending)
        if len(requests) >= max_requests:
            break
        if len(requests) >= min_requests_to_launch:
            break
        if not resolved:
            break

    return requests, pending, next_request_id, immediate_resolved_words


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
    queued_words = [
        _QueuedWord(
            merge_state=_MergeState(
                word=word,
                clusters=clusters,
                selected=_existing_canonical_clusters(service.store, word),
                boilerplate_tokens=(),
            ),
            input_count=sum(len(cluster.members) for cluster in clusters),
        )
        for word, clusters in bucket_batch
    ]
    states = [
        item.merge_state
        for item in queued_words
    ]
    next_request_id = 1
    while True:
        requests, pending, next_request_id, _immediate_resolved_words = _collect_referee_launch_batch(
            queued_words,
            max_requests=max(referee_batch_size, 1),
            min_requests_to_launch=min(
                max(referee_batch_size, 1),
                DEFAULT_MIN_REFEREE_BATCH_TO_SWITCH,
            ),
            next_request_id=next_request_id,
            stats=stats,
            review_handle=review_handle,
        )
        if not requests:
            if all(state.finished() for state in states):
                break
            continue
        log(
            "clue_canon referee batch "
            f"comparisons={len(requests)} words={len({request.word for request in requests})}"
        )
        adaptive_batch = getattr(service, "_run_referee_adaptive_batch", None)
        if callable(adaptive_batch):
            adaptive = adaptive_batch(requests)
            if adaptive is None:
                results = {}
            else:
                results = adaptive.results
                stats.comparison_requests += len(requests)
                stats.total_votes += adaptive.total_votes
                stats.referee_phase1_requests += adaptive.phase1_requests
                stats.referee_phase2_requests += adaptive.phase2_requests
                stats.invalid_compare_json_primary += adaptive.invalid_compare_json_primary
                stats.invalid_compare_json_secondary += adaptive.invalid_compare_json_secondary
        else:
            results = service._run_referee_batch(requests)
            stats.comparison_requests += len(requests)
            stats.total_votes += len(requests) * 2
            stats.referee_phase1_requests += len(requests)
            stats.referee_phase2_requests += len(requests)
        for entry in _build_referee_outcomes(pending, results):
            _apply_terminal_outcome(
                queued_words[entry.state_index],
                entry.outcome,
                stats=stats,
                review_handle=review_handle,
            )
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
) -> tuple[int, int]:
    if dry_run:
        return 0, 0
    clue_attach_batches = 0
    alias_insert_batches = 0
    for cluster in clusters:
        canonical_id = cluster.canonical_id
        if canonical_id is None:
            decision = store.create_canonical_definition(cluster.primary)
            if decision is None:
                raise RuntimeError(f"Could not create canonical definition for {word}")
            canonical_id = decision.id
        clue_ids = [member.id for member in cluster.members if member.id]
        if clue_ids:
            attach_many = getattr(store, "attach_clues", None)
            if callable(attach_many):
                clue_attach_batches += int(attach_many(clue_ids, canonical_definition_id=canonical_id) or 0)
            else:
                for clue_id in clue_ids:
                    store.attach_clue(
                        clue_id,
                        "",
                        canonical_definition_id=canonical_id,
                    )
                    clue_attach_batches += 1
        aliases = []
        for member in cluster.members:
            if member.id == cluster.primary.id and cluster.canonical_id is None:
                continue
            aliases.append(
                {
                    "definition": member.definition,
                    "definition_norm": member.definition_norm,
                    "source_clue_id": member.id or None,
                    "match_type": "exact" if member.definition_norm == cluster.primary.definition_norm else "near",
                    "same_meaning_votes": cluster.same_meaning_votes if member.definition_norm != cluster.primary.definition_norm else None,
                    "winner_votes": cluster.winner_votes if member.definition_norm != cluster.primary.definition_norm else None,
                    "decision_source": "llm" if member.definition_norm != cluster.primary.definition_norm else "heuristic",
                    "decision_note": cluster.decision_note,
                }
            )
        if aliases:
            insert_many = getattr(store, "insert_aliases", None)
            if callable(insert_many):
                alias_insert_batches += int(insert_many(
                    canonical_definition_id=canonical_id,
                    word_normalized=word,
                    aliases=aliases,
                ) or 0)
            else:
                for alias in aliases:
                    store.insert_alias(
                        canonical_definition_id=canonical_id,
                        word_normalized=word,
                        definition=str(alias["definition"]),
                        definition_norm=str(alias["definition_norm"]),
                        source_clue_id=alias["source_clue_id"],
                        match_type=str(alias["match_type"]),
                        same_meaning_votes=alias["same_meaning_votes"],
                        winner_votes=alias["winner_votes"],
                        decision_source=str(alias["decision_source"]),
                        decision_note=str(alias["decision_note"]),
                    )
                    alias_insert_batches += 1
    return clue_attach_batches, alias_insert_batches


def _record_to_state(record: ClueDefinitionRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "word_normalized": record.word_normalized,
        "word_original": record.word_original,
        "definition": record.definition,
        "definition_norm": record.definition_norm,
        "word_type": record.word_type,
        "usage_label": record.usage_label,
        "verified": record.verified,
        "semantic_score": record.semantic_score,
        "rebus_score": record.rebus_score,
        "creativity_score": record.creativity_score,
        "verify_note": record.verify_note,
        "canonical_definition_id": record.canonical_definition_id,
    }


def _record_from_state(payload: dict[str, object]) -> ClueDefinitionRecord:
    return ClueDefinitionRecord(
        id=str(payload.get("id") or ""),
        word_normalized=str(payload.get("word_normalized") or ""),
        word_original=str(payload.get("word_original") or ""),
        definition=str(payload.get("definition") or ""),
        definition_norm=str(payload.get("definition_norm") or ""),
        word_type=str(payload.get("word_type") or ""),
        usage_label=str(payload.get("usage_label") or ""),
        verified=bool(payload.get("verified")),
        semantic_score=payload.get("semantic_score"),
        rebus_score=payload.get("rebus_score"),
        creativity_score=payload.get("creativity_score"),
        verify_note=str(payload.get("verify_note") or ""),
        canonical_definition_id=payload.get("canonical_definition_id"),
    )


def _cluster_to_state(cluster: _WorkingCluster) -> dict[str, object]:
    return {
        "primary": _record_to_state(cluster.primary),
        "members": [_record_to_state(member) for member in cluster.members],
        "canonical_id": cluster.canonical_id,
        "same_meaning_votes": cluster.same_meaning_votes,
        "winner_votes": cluster.winner_votes,
        "decision_note": cluster.decision_note,
    }


def _cluster_from_state(payload: dict[str, object]) -> _WorkingCluster:
    return _WorkingCluster(
        primary=_record_from_state(dict(payload.get("primary") or {})),
        members=[
            _record_from_state(dict(member))
            for member in list(payload.get("members") or [])
        ],
        canonical_id=payload.get("canonical_id"),
        same_meaning_votes=payload.get("same_meaning_votes"),
        winner_votes=payload.get("winner_votes"),
        decision_note=str(payload.get("decision_note") or ""),
    )


def _merge_state_to_state(state: _MergeState) -> dict[str, object]:
    return {
        "word": state.word,
        "clusters": [_cluster_to_state(cluster) for cluster in state.clusters],
        "selected": [_cluster_to_state(cluster) for cluster in state.selected],
        "boilerplate_tokens": list(state.boilerplate_tokens),
        "next_cluster_index": state.next_cluster_index,
        "current": _cluster_to_state(state.current) if state.current is not None else None,
        "candidate_indexes": list(state.candidate_indexes),
        "compare_index": state.compare_index,
        "waiting": state.waiting,
        "pending_request_id": state.pending_request_id,
    }


def _merge_state_from_state(
    payload: dict[str, object],
    *,
    state_version: int,
) -> _MergeState:
    current_payload = payload.get("current")
    selected = [
        _cluster_from_state(dict(cluster))
        for cluster in list(payload.get("selected") or [])
    ]
    compare_index = int(payload.get("compare_index") or 0)
    candidate_indexes = [
        int(index)
        for index in list(payload.get("candidate_indexes") or [])
    ]
    if state_version < 3 and not candidate_indexes:
        if isinstance(current_payload, dict) and selected:
            candidate_indexes = list(range(compare_index, len(selected)))
            compare_index = 0
    return _MergeState(
        word=str(payload.get("word") or ""),
        clusters=[
            _cluster_from_state(dict(cluster))
            for cluster in list(payload.get("clusters") or [])
        ],
        selected=selected,
        boilerplate_tokens=tuple(
            str(token)
            for token in list(payload.get("boilerplate_tokens") or [])
            if str(token or "").strip()
        ),
        next_cluster_index=int(payload.get("next_cluster_index") or 0),
        current=_cluster_from_state(dict(current_payload)) if isinstance(current_payload, dict) else None,
        candidate_indexes=candidate_indexes,
        compare_index=compare_index,
        waiting=bool(payload.get("waiting")),
        pending_request_id=str(payload.get("pending_request_id") or ("legacy-pending" if bool(payload.get("waiting")) else "")),
    )


def _queued_word_to_state(item: _QueuedWord) -> dict[str, object]:
    return {
        "word": item.word,
        "input_count": item.input_count,
        "merge_state": _merge_state_to_state(item.merge_state),
        "comparisons_done": item.comparisons_done,
        "unresolved": item.unresolved,
        "deferred": item.deferred,
        "defer_reason": item.defer_reason,
        "defer_remaining_clusters": item.defer_remaining_clusters,
        "candidate_pairs_considered": item.candidate_pairs_considered,
        "referee_requests_submitted": item.referee_requests_submitted,
        "consecutive_non_merge_comparisons": item.consecutive_non_merge_comparisons,
        "last_merge_comparison": item.last_merge_comparison,
        "blocked_on_referee_error": item.blocked_on_referee_error,
        "referee_error_count": item.referee_error_count,
        "last_referee_error": item.last_referee_error,
        "resume_stale_wait_info": item.resume_stale_wait_info,
    }


def _queued_word_from_state(
    payload: dict[str, object],
    *,
    state_version: int,
) -> _QueuedWord:
    return _QueuedWord(
        merge_state=_merge_state_from_state(
            dict(payload.get("merge_state") or {}),
            state_version=state_version,
        ),
        input_count=int(payload.get("input_count") or 0),
        comparisons_done=int(payload.get("comparisons_done") or 0),
        unresolved=bool(payload.get("unresolved")),
        deferred=bool(payload.get("deferred")),
        defer_reason=str(payload.get("defer_reason") or ""),
        defer_remaining_clusters=int(payload.get("defer_remaining_clusters") or 0),
        candidate_pairs_considered=int(payload.get("candidate_pairs_considered") or 0),
        referee_requests_submitted=int(payload.get("referee_requests_submitted") or 0),
        consecutive_non_merge_comparisons=int(payload.get("consecutive_non_merge_comparisons") or 0),
        last_merge_comparison=int(payload.get("last_merge_comparison") or 0),
        blocked_on_referee_error=bool(payload.get("blocked_on_referee_error")),
        referee_error_count=int(payload.get("referee_error_count") or 0),
        last_referee_error=dict(payload.get("last_referee_error") or {}),
        resume_stale_wait_info=dict(payload.get("resume_stale_wait_info") or {}),
    )


def _stats_to_state(stats: BackfillStats) -> dict[str, object]:
    return {
        "total_rows": stats.total_rows,
        "eligible_rows": stats.eligible_rows,
        "verified_null_rows": stats.verified_null_rows,
        "unverified_null_rows": stats.unverified_null_rows,
        "eligible_words": stats.eligible_words,
        "already_canonicalized_rows_skipped": stats.already_canonicalized_rows_skipped,
        "exact_merges": stats.exact_merges,
        "near_merges": stats.near_merges,
        "disagreement_3_of_6": stats.disagreement_3_of_6,
        "disagreement_4_of_6": stats.disagreement_4_of_6,
        "standalone_canonicals": stats.standalone_canonicals,
        "comparison_requests": stats.comparison_requests,
        "total_votes": stats.total_votes,
        "singleton_words": stats.singleton_words,
        "resumed_words": stats.resumed_words,
        "unresolved_words": stats.unresolved_words,
        "committed_words": stats.committed_words,
        "deferred_words": stats.deferred_words,
        "deferred_due_to_stagnation": stats.deferred_due_to_stagnation,
        "deferred_due_to_resume_stale_wait": stats.deferred_due_to_resume_stale_wait,
        "referee_error_words": stats.referee_error_words,
        "missing_model_vote_errors": stats.missing_model_vote_errors,
        "keep_separate_decisions": stats.keep_separate_decisions,
        "merge_decisions": stats.merge_decisions,
        "promote_decisions": stats.promote_decisions,
        "referee_batches_launched": stats.referee_batches_launched,
        "referee_phase1_requests": stats.referee_phase1_requests,
        "referee_phase2_requests": stats.referee_phase2_requests,
        "referee_merges": stats.referee_merges,
        "referee_keep_separate": stats.referee_keep_separate,
        "referee_errors": stats.referee_errors,
        "invalid_compare_json_primary": stats.invalid_compare_json_primary,
        "invalid_compare_json_secondary": stats.invalid_compare_json_secondary,
        "resume_stale_wait_words": stats.resume_stale_wait_words,
        "resume_pending_words_dropped": stats.resume_pending_words_dropped,
        "resume_active_words_dropped": stats.resume_active_words_dropped,
        "resume_words_deduped": stats.resume_words_deduped,
        "canonical_prefetch_batches": stats.canonical_prefetch_batches,
        "clue_attach_batches": stats.clue_attach_batches,
        "alias_insert_batches": stats.alias_insert_batches,
        "candidate_pairs_considered": stats.candidate_pairs_considered,
        "referee_requests_submitted": stats.referee_requests_submitted,
        "verified_attached_rows": stats.verified_attached_rows,
        "unverified_attached_rows": stats.unverified_attached_rows,
        "unverified_singleton_canonicals_created": stats.unverified_singleton_canonicals_created,
        "unverified_exact_reuses": stats.unverified_exact_reuses,
        "reduced_words": list(stats.reduced_words),
    }


def _stats_from_state(payload: dict[str, object]) -> BackfillStats:
    stats = BackfillStats(total_rows=int(payload.get("total_rows") or 0))
    stats.eligible_rows = int(payload.get("eligible_rows") or 0)
    stats.verified_null_rows = int(payload.get("verified_null_rows") or 0)
    stats.unverified_null_rows = int(payload.get("unverified_null_rows") or 0)
    stats.eligible_words = int(payload.get("eligible_words") or 0)
    stats.already_canonicalized_rows_skipped = int(payload.get("already_canonicalized_rows_skipped") or 0)
    stats.exact_merges = int(payload.get("exact_merges") or 0)
    stats.near_merges = int(payload.get("near_merges") or 0)
    stats.disagreement_3_of_6 = int(payload.get("disagreement_3_of_6") or 0)
    stats.disagreement_4_of_6 = int(payload.get("disagreement_4_of_6") or 0)
    stats.standalone_canonicals = int(payload.get("standalone_canonicals") or 0)
    stats.comparison_requests = int(payload.get("comparison_requests") or 0)
    stats.total_votes = int(payload.get("total_votes") or 0)
    stats.singleton_words = int(payload.get("singleton_words") or 0)
    stats.resumed_words = int(payload.get("resumed_words") or 0)
    stats.unresolved_words = int(payload.get("unresolved_words") or 0)
    stats.committed_words = int(payload.get("committed_words") or 0)
    stats.deferred_words = int(payload.get("deferred_words") or 0)
    stats.deferred_due_to_stagnation = int(payload.get("deferred_due_to_stagnation") or 0)
    stats.deferred_due_to_resume_stale_wait = int(payload.get("deferred_due_to_resume_stale_wait") or 0)
    stats.referee_error_words = int(payload.get("referee_error_words") or 0)
    stats.missing_model_vote_errors = int(payload.get("missing_model_vote_errors") or 0)
    stats.keep_separate_decisions = int(payload.get("keep_separate_decisions") or 0)
    stats.merge_decisions = int(payload.get("merge_decisions") or 0)
    stats.promote_decisions = int(payload.get("promote_decisions") or 0)
    stats.referee_batches_launched = int(payload.get("referee_batches_launched") or 0)
    stats.referee_phase1_requests = int(payload.get("referee_phase1_requests") or 0)
    stats.referee_phase2_requests = int(payload.get("referee_phase2_requests") or 0)
    stats.referee_merges = int(payload.get("referee_merges") or 0)
    stats.referee_keep_separate = int(payload.get("referee_keep_separate") or 0)
    stats.referee_errors = int(payload.get("referee_errors") or 0)
    stats.invalid_compare_json_primary = int(payload.get("invalid_compare_json_primary") or 0)
    stats.invalid_compare_json_secondary = int(payload.get("invalid_compare_json_secondary") or 0)
    stats.resume_stale_wait_words = int(payload.get("resume_stale_wait_words") or 0)
    stats.resume_pending_words_dropped = int(payload.get("resume_pending_words_dropped") or 0)
    stats.resume_active_words_dropped = int(payload.get("resume_active_words_dropped") or 0)
    stats.resume_words_deduped = int(payload.get("resume_words_deduped") or 0)
    stats.canonical_prefetch_batches = int(payload.get("canonical_prefetch_batches") or 0)
    stats.clue_attach_batches = int(payload.get("clue_attach_batches") or 0)
    stats.alias_insert_batches = int(payload.get("alias_insert_batches") or 0)
    stats.candidate_pairs_considered = int(payload.get("candidate_pairs_considered") or 0)
    stats.referee_requests_submitted = int(payload.get("referee_requests_submitted") or 0)
    stats.verified_attached_rows = int(payload.get("verified_attached_rows") or 0)
    stats.unverified_attached_rows = int(payload.get("unverified_attached_rows") or 0)
    stats.unverified_singleton_canonicals_created = int(payload.get("unverified_singleton_canonicals_created") or 0)
    stats.unverified_exact_reuses = int(payload.get("unverified_exact_reuses") or 0)
    stats.reduced_words = [
        (str(item[0]), int(item[1]))
        for item in list(payload.get("reduced_words") or [])
        if isinstance(item, (list, tuple)) and len(item) == 2
    ]
    return stats


def _default_state_path() -> Path:
    DEFAULT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return DEFAULT_STATE_PATH


def _config_matches_state(
    state: dict[str, object],
    *,
    dry_run: bool,
    apply: bool,
    word: str | None,
    limit: int | None,
    min_count: int,
    referee_batch_size: int,
    progress_every: int,
    word_queue_size: int,
    max_stagnant_comparisons: int = DEFAULT_MAX_STAGNANT_COMPARISONS,
) -> bool:
    legacy_queue_value = state.get("word_queue_size")
    legacy_stagnant_value = state.get("max_stagnant_comparisons")
    return (
        bool(state.get("dry_run")) == dry_run
        and bool(state.get("apply")) == apply
        and str(state.get("word") or "") == str(word or "")
        and state.get("limit") == limit
        and int(state.get("min_count") or 0) == min_count
        and int(state.get("referee_batch_size") or 0) == referee_batch_size
        and int(state.get("progress_every") or 0) == progress_every
        and (
            legacy_queue_value is None
            or legacy_queue_value == ""
            or int(legacy_queue_value) == word_queue_size
        )
        and (
            legacy_stagnant_value is None
            or legacy_stagnant_value == ""
            or int(legacy_stagnant_value) == max_stagnant_comparisons
        )
    )


def _write_state(
    state_path: Path,
    *,
    dry_run: bool,
    apply: bool,
    word: str | None,
    limit: int | None,
    min_count: int,
    referee_batch_size: int,
    progress_every: int,
    word_queue_size: int,
    max_stagnant_comparisons: int = DEFAULT_MAX_STAGNANT_COMPARISONS,
    report_dir: Path,
    review_path: Path,
    quarantine_path: Path,
    stats: BackfillStats,
    completed_words: list[str],
    pending_words: list[str],
    active_words: list[dict[str, object]],
) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": STATE_VERSION,
        "dry_run": dry_run,
        "apply": apply,
        "word": word,
        "limit": limit,
        "min_count": min_count,
        "referee_batch_size": referee_batch_size,
        "progress_every": progress_every,
        "word_queue_size": word_queue_size,
        "max_stagnant_comparisons": max_stagnant_comparisons,
        "report_dir": str(report_dir),
        "review_path": str(review_path),
        "quarantine_path": str(quarantine_path),
        "stats": _stats_to_state(stats),
        "completed_words": completed_words,
        "pending_words": pending_words,
        "active_words": active_words,
    }
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_state(
    state_path: Path,
    *,
    dry_run: bool,
    apply: bool,
    word: str | None,
    limit: int | None,
    min_count: int,
    referee_batch_size: int,
    progress_every: int,
    word_queue_size: int,
    max_stagnant_comparisons: int = DEFAULT_MAX_STAGNANT_COMPARISONS,
) -> dict[str, object] | None:
    if not state_path.exists():
        return None
    state = json.loads(state_path.read_text(encoding="utf-8"))
    version = int(state.get("version") or 1)
    if version not in {1, 2, STATE_VERSION}:
        raise SystemExit(
            f"State file {state_path} has unsupported version {version}. "
            "Delete it or use a different --state-path."
        )
    if not _config_matches_state(
        state,
        dry_run=dry_run,
        apply=apply,
        word=word,
        limit=limit,
        min_count=min_count,
        referee_batch_size=referee_batch_size,
        progress_every=progress_every,
        word_queue_size=word_queue_size,
        max_stagnant_comparisons=max_stagnant_comparisons,
    ):
        raise SystemExit(
            f"State file {state_path} exists with different backfill arguments. "
            "Use a different --state-path or delete the stale state."
        )
    return state


def _build_summary(
    *,
    stats: BackfillStats,
    verified_rows: int,
    unverified_rows: int,
    processed_words: int,
    review_path: Path,
    quarantine_path: Path,
    mode: str,
    referee_batch_size: int,
    word_queue_size: int,
    runtime: LmRuntime,
    state_path: Path | None,
) -> dict[str, object]:
    avg_votes_per_request = (
        stats.total_votes / stats.referee_requests_submitted
        if stats.referee_requests_submitted
        else 0.0
    )
    avg_requests_per_committed_word = (
        stats.referee_requests_submitted / stats.committed_words
        if stats.committed_words
        else 0.0
    )
    avg_requests_per_referee_batch = (
        stats.comparison_requests / stats.referee_batches_launched
        if stats.referee_batches_launched
        else 0.0
    )
    avg_votes_per_model_activation = (
        stats.total_votes / runtime.activation_count
        if runtime.activation_count
        else 0.0
    )
    switches_per_committed_word = (
        runtime.switch_count / stats.committed_words
        if stats.committed_words
        else 0.0
    )
    return {
        "total_rows": stats.total_rows,
        "verified_rows": verified_rows,
        "unverified_rows": unverified_rows,
        "eligible_rows": stats.eligible_rows,
        "eligible_rows_all": stats.eligible_rows,
        "verified_null_rows": stats.verified_null_rows,
        "unverified_null_rows": stats.unverified_null_rows,
        "eligible_words": stats.eligible_words,
        "already_canonicalized_rows_skipped": stats.already_canonicalized_rows_skipped,
        "processed_words": processed_words,
        "exact_merges": stats.exact_merges,
        "near_merges": stats.near_merges,
        "disagreement_3_of_6": stats.disagreement_3_of_6,
        "disagreement_4_of_6": stats.disagreement_4_of_6,
        "standalone_canonicals": stats.standalone_canonicals,
        "comparison_requests": stats.comparison_requests,
        "total_votes": stats.total_votes,
        "singleton_words": stats.singleton_words,
        "resumed_words": stats.resumed_words,
        "unresolved_words": stats.unresolved_words,
        "committed_words": stats.committed_words,
        "deferred_words": stats.deferred_words,
        "deferred_due_to_stagnation": stats.deferred_due_to_stagnation,
        "deferred_due_to_resume_stale_wait": stats.deferred_due_to_resume_stale_wait,
        "referee_error_words": stats.referee_error_words,
        "missing_model_vote_errors": stats.missing_model_vote_errors,
        "keep_separate_decisions": stats.keep_separate_decisions,
        "merge_decisions": stats.merge_decisions,
        "promote_decisions": stats.promote_decisions,
        "referee_batches_launched": stats.referee_batches_launched,
        "referee_phase1_requests": stats.referee_phase1_requests,
        "referee_phase2_requests": stats.referee_phase2_requests,
        "referee_merges": stats.referee_merges,
        "referee_keep_separate": stats.referee_keep_separate,
        "referee_errors": stats.referee_errors,
        "invalid_compare_json_primary": stats.invalid_compare_json_primary,
        "invalid_compare_json_secondary": stats.invalid_compare_json_secondary,
        "resume_stale_wait_words": stats.resume_stale_wait_words,
        "resume_pending_words_dropped": stats.resume_pending_words_dropped,
        "resume_active_words_dropped": stats.resume_active_words_dropped,
        "resume_words_deduped": stats.resume_words_deduped,
        "canonical_prefetch_batches": stats.canonical_prefetch_batches,
        "clue_attach_batches": stats.clue_attach_batches,
        "alias_insert_batches": stats.alias_insert_batches,
        "candidate_pairs_considered": stats.candidate_pairs_considered,
        "referee_requests_submitted": stats.referee_requests_submitted,
        "verified_attached_rows": stats.verified_attached_rows,
        "unverified_attached_rows": stats.unverified_attached_rows,
        "unverified_singleton_canonicals_created": stats.unverified_singleton_canonicals_created,
        "unverified_exact_reuses": stats.unverified_exact_reuses,
        "avg_votes_per_request": avg_votes_per_request,
        "avg_requests_per_committed_word": avg_requests_per_committed_word,
        "avg_requests_per_referee_batch": avg_requests_per_referee_batch,
        "avg_votes_per_model_activation": avg_votes_per_model_activation,
        "switches_per_committed_word": switches_per_committed_word,
        "model_switches": runtime.switch_count,
        "model_activations": runtime.activation_count,
        "top_reductions": stats.reduced_words,
        "disagreement_report": str(review_path),
        "quarantine_report": str(quarantine_path),
        "mode": mode,
        "referee_batch_size": referee_batch_size,
        "word_queue_size": word_queue_size,
        "state_path": str(state_path) if state_path is not None else None,
    }


def _resume_item_is_valid(item: _QueuedWord) -> bool:
    return bool(item.word) and item.merge_state.word == item.word


def _remaining_clusters(state: _MergeState) -> int:
    return max(0, len(state.clusters) - state.next_cluster_index)


def _estimate_remaining_pair_checks(state: _MergeState) -> int:
    selected_count = len(state.selected)
    total = 0
    next_selected_count = selected_count
    if state.current is not None:
        total += max(0, len(state.candidate_indexes) - state.compare_index)
        next_selected_count += 1
    for offset in range(_remaining_clusters(state)):
        total += next_selected_count + offset
    return total


def _should_defer_word(
    item: _QueuedWord,
    *,
    max_stagnant_comparisons: int,
) -> bool:
    return (
        max_stagnant_comparisons > 0
        and not item.deferred
        and not item.merge_state.finished()
        and not item.merge_state.waiting
        and item.consecutive_non_merge_comparisons >= max_stagnant_comparisons
    )


def _defer_word(item: _QueuedWord, *, reason: str) -> None:
    state = item.merge_state
    item.deferred = True
    item.defer_reason = reason
    item.defer_remaining_clusters = _remaining_clusters(state) + (1 if state.current is not None else 0)
    item.unresolved = True
    item.blocked_on_referee_error = False
    if state.current is not None:
        state.selected.append(state.current)
        state.current = None
    if state.next_cluster_index < len(state.clusters):
        state.selected.extend(state.clusters[state.next_cluster_index:])
    state.next_cluster_index = len(state.clusters)
    state.candidate_indexes = []
    state.compare_index = 0
    state.waiting = False
    state.pending_request_id = ""


def _reconcile_resume_state(
    *,
    stats: BackfillStats,
    completed_words: list[str],
    pending_words: list[str],
    active_items: list[_QueuedWord],
    word_order: list[str],
    eligible_word_set: set[str],
) -> tuple[list[str], list[_QueuedWord], list[str]]:
    completed_set = set(completed_words)
    active_kept: list[_QueuedWord] = []
    active_dropped_words: list[str] = []
    active_seen: set[str] = set()
    active_duplicates = 0
    active_dropped = 0
    for item in active_items:
        if not _resume_item_is_valid(item):
            active_dropped += 1
            if item.word:
                active_dropped_words.append(item.word)
            continue
        if item.word in completed_set:
            active_dropped += 1
            active_dropped_words.append(item.word)
            continue
        if item.word in active_seen:
            active_duplicates += 1
            continue
        active_seen.add(item.word)
        active_kept.append(item)

    pending_kept: list[str] = []
    pending_seen: set[str] = set()
    pending_dropped = 0
    pending_duplicates = 0
    for word in pending_words:
        normalized = str(word or "").strip().upper()
        if not normalized:
            pending_dropped += 1
            continue
        if normalized in completed_set or normalized in active_seen:
            pending_dropped += 1
            continue
        if normalized not in eligible_word_set:
            pending_dropped += 1
            continue
        if normalized in pending_seen:
            pending_duplicates += 1
            continue
        pending_seen.add(normalized)
        pending_kept.append(normalized)

    if not pending_kept:
        pending_kept = [
            word
            for word in word_order
            if word not in completed_set and word not in active_seen
        ]

    stats.resume_active_words_dropped += active_dropped
    stats.resume_pending_words_dropped += pending_dropped
    stats.resume_words_deduped += active_duplicates + pending_duplicates
    return pending_kept, active_kept, active_dropped_words


def _is_stale_waiting_resume(item: _QueuedWord) -> bool:
    state = item.merge_state
    return (
        state.waiting
        and state.current is not None
        and (
            bool(state.candidate_indexes)
            or bool(str(state.pending_request_id or "").strip())
        )
    )


def _normalize_stale_waiting_items(
    active_items: list[_QueuedWord],
    *,
    stats: BackfillStats,
) -> list[str]:
    normalized_words: list[str] = []
    for item in active_items:
        if not _is_stale_waiting_resume(item):
            continue
        state = item.merge_state
        log(
            f"[resume-stale-wait] word={item.word} "
            f"pending_request_id={state.pending_request_id or '-'} "
            f"candidate_indexes={state.candidate_indexes}"
        )
        stats.resume_stale_wait_words += 1
        item.resume_stale_wait_info = {
            "pending_request_id": state.pending_request_id,
            "candidate_indexes": list(state.candidate_indexes),
        }
        _defer_word(item, reason="resume_stale_wait")
        normalized_words.append(item.word)
    return normalized_words


def _direct_legacy_code_refs() -> list[dict[str, str]]:
    roots = [Path("generator"), Path("worker/src")]
    patterns = (
        ("literal_legacy_column", re.compile(r"crossword_clues\.definition\b")),
        (
            "legacy_select",
            re.compile(
                r'table\(["\']crossword_clues["\']\)\.select\([^)]*definition',
                flags=re.IGNORECASE | re.DOTALL,
            ),
        ),
        (
            "legacy_update",
            re.compile(
                r'table\(["\']crossword_clues["\']\).*update\([^)]*["\']definition["\']',
                flags=re.IGNORECASE | re.DOTALL,
            ),
        ),
        (
            "legacy_insert",
            re.compile(
                r'table\(["\']crossword_clues["\']\).*insert\([^)]*["\']definition["\']',
                flags=re.IGNORECASE | re.DOTALL,
            ),
        ),
    )
    findings: list[dict[str, str]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".ts", ".tsx", ".js"}:
                continue
            text = path.read_text(encoding="utf-8")
            if "crossword_clues" not in text or "definition" not in text:
                continue
            for label, pattern in patterns:
                match = pattern.search(text)
                if not match:
                    continue
                line_no = text.count("\n", 0, match.start()) + 1
                findings.append(
                    {
                        "file": str(path),
                        "line": str(line_no),
                        "kind": label,
                    }
                )
                break
    return findings


def run_audit(*, output: str | None = None) -> int:
    store = ClueCanonStore()
    if not store.is_enabled():
        raise SystemExit("Canonical clue schema unavailable")

    rows = _fetch_clue_rows(store)
    null_canonical_rows = [row for row in rows if not row.get("canonical_definition_id")]
    legacy_rows = [
        row for row in rows
        if str(row.get("definition_source") or "").strip().lower() == "legacy"
    ]
    per_puzzle_missing: dict[str, int] = defaultdict(int)
    for row in null_canonical_rows:
        per_puzzle_missing[str(row.get("puzzle_id") or "")] += 1
    code_findings = _direct_legacy_code_refs()

    summary = {
        "ok": not null_canonical_rows and not legacy_rows and not code_findings,
        "total_clues": len(rows),
        "null_canonical_definition_id": len(null_canonical_rows),
        "legacy_definition_rows": len(legacy_rows),
        "missing_by_puzzle": dict(sorted(per_puzzle_missing.items())),
        "legacy_code_references": code_findings,
    }
    report_path = Path(output) if output else Path("build/clue_canon") / f"audit_{path_timestamp()}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    log(json.dumps(summary, ensure_ascii=False, indent=2))
    log(f"audit_report={report_path}")
    return 0 if summary["ok"] else 1


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
    backfill.add_argument("--min-count", type=int, default=1, help="Only process words with at least this many clues.")
    backfill.add_argument(
        "--referee-batch-size",
        type=int,
        default=DEFAULT_REFEREE_BATCH_SIZE,
        help="How many near-duplicate comparisons to referee per batch before switching models.",
    )
    backfill.add_argument(
        "--resume",
        action="store_true",
        help="Resume from the state file when present. State auto-resumes by default when args match.",
    )
    backfill.add_argument(
        "--state-path",
        help="Checkpoint path for resumable backfill state.",
    )
    backfill.add_argument(
        "--progress-every",
        type=int,
        default=DEFAULT_PROGRESS_EVERY,
        help="Emit current-word progress after this many referee comparisons.",
    )
    backfill.add_argument(
        "--word-queue-size",
        type=int,
        default=50,
        help="How many words to keep in the referee queue at once.",
    )
    backfill.add_argument(
        "--max-stagnant-comparisons",
        type=int,
        default=DEFAULT_MAX_STAGNANT_COMPARISONS,
        help="Defer a word after this many consecutive non-merge referee comparisons.",
    )

    audit = subparsers.add_parser("audit", help="Validate canonical-only cutover readiness.")
    audit.add_argument("--output", help="Write audit JSON to this path.")
    return parser


def run_backfill(
    *,
    dry_run: bool,
    apply: bool,
    word: str | None,
    limit: int | None,
    min_count: int,
    referee_batch_size: int,
    resume: bool,
    state_path: str | None,
    progress_every: int,
    word_queue_size: int,
    max_stagnant_comparisons: int = DEFAULT_MAX_STAGNANT_COMPARISONS,
) -> int:
    if dry_run == apply:
        raise SystemExit("Specify exactly one of --dry-run or --apply")

    store = ClueCanonStore()
    if not store.is_enabled():
        raise SystemExit("Canonical clue schema unavailable")

    client = create_client()
    runtime = LmRuntime(multi_model=True)
    service = ClueCanonService(store=store, client=client, runtime=runtime)

    total_rows = store.count_clue_rows(word_normalized=word)
    verified_rows = store.count_clue_rows(verified=True, word_normalized=word)
    verified_null_rows = store.count_clue_rows(
        verified=True,
        canonical_missing_only=True,
        word_normalized=word,
    )
    rows = _fetch_backfill_rows(store, word=word)
    records = _enrich_rows(rows)
    eligible_rows = len(records)
    unverified_null_rows = max(0, eligible_rows - verified_null_rows)
    eligible_words = len({record.word_normalized for record in records})
    already_canonicalized_rows_skipped = max(verified_rows - verified_null_rows, 0)
    unverified_rows = max(total_rows - verified_rows, 0)
    buckets = _word_buckets(records, target_word=word, limit=limit, min_count=min_count)
    bucket_map = {bucket_word: bucket_rows for bucket_word, bucket_rows in buckets}

    state_file = Path(state_path) if state_path else _default_state_path()
    prior_state = _load_state(
        state_file,
        dry_run=dry_run,
        apply=apply,
        word=word,
        limit=limit,
        min_count=min_count,
        referee_batch_size=referee_batch_size,
        progress_every=progress_every,
        word_queue_size=word_queue_size,
        max_stagnant_comparisons=max_stagnant_comparisons,
    )
    if prior_state is None and resume and state_path:
        raise SystemExit(f"No resumable state found at {state_file}")

    if prior_state is not None:
        report_dir = Path(str(prior_state["report_dir"]))
        review_path = Path(str(prior_state["review_path"]))
        quarantine_path = Path(str(prior_state["quarantine_path"]))
        stats = _stats_from_state(dict(prior_state.get("stats") or {}))
        stats.resumed_words += 1
        completed_words = list(prior_state.get("completed_words") or [])
        pending_words = [
            str(item)
            for item in list(prior_state.get("pending_words") or [])
            if str(item)
        ]
        active_word_payloads = list(prior_state.get("active_words") or [])
        legacy_current_word = prior_state.get("current_word")
    else:
        report_dir = Path("build/clue_canon") / path_timestamp()
        review_path = report_dir / "disagreements.jsonl"
        quarantine_path = report_dir / "quarantine.jsonl"
        stats = BackfillStats(
            total_rows=total_rows,
            eligible_rows=eligible_rows,
            verified_null_rows=verified_null_rows,
            unverified_null_rows=unverified_null_rows,
            eligible_words=eligible_words,
            already_canonicalized_rows_skipped=already_canonicalized_rows_skipped,
        )
        completed_words = []
        pending_words = []
        active_word_payloads = []
        legacy_current_word = None

    if not stats.total_rows:
        stats.total_rows = total_rows
    if not stats.eligible_rows:
        stats.eligible_rows = eligible_rows
    if not stats.verified_null_rows:
        stats.verified_null_rows = verified_null_rows
    if not stats.unverified_null_rows:
        stats.unverified_null_rows = unverified_null_rows
    if not stats.eligible_words:
        stats.eligible_words = eligible_words
    if not stats.already_canonicalized_rows_skipped:
        stats.already_canonicalized_rows_skipped = already_canonicalized_rows_skipped

    report_dir.mkdir(parents=True, exist_ok=True)
    log_path = report_dir / "run.log"
    audit_path = report_dir / "audit.jsonl"
    handle = install_process_logging(
        run_id=report_dir.name,
        component="clue_canon",
        log_path=log_path,
        audit_path=audit_path,
        tee_console=True,
    )
    processed_word_set = set(completed_words)
    active_items = [
        _queued_word_from_state(dict(item), state_version=int(prior_state.get("version") or STATE_VERSION))
        for item in active_word_payloads
        if isinstance(item, dict)
    ]
    word_order = [bucket_word for bucket_word, _bucket_rows in buckets if bucket_word not in processed_word_set]
    if not active_items and isinstance(legacy_current_word, dict):
        legacy_item = _queued_word_from_state(
            dict(legacy_current_word),
            state_version=int(prior_state.get("version") or STATE_VERSION) if prior_state is not None else STATE_VERSION,
        )
        if _resume_item_is_valid(legacy_item):
            active_items = [legacy_item]
    pending_words, active_items, dropped_active_words = _reconcile_resume_state(
        stats=stats,
        completed_words=completed_words,
        pending_words=pending_words,
        active_items=active_items,
        word_order=word_order,
        eligible_word_set=set(bucket_map),
    )
    stale_wait_words = _normalize_stale_waiting_items(
        active_items,
        stats=stats,
    )
    summary_path = report_dir / "summary.json"
    mode = "dry-run" if dry_run else "apply"
    review_mode = "a" if prior_state is not None else "w"
    quarantine_mode = "a" if prior_state is not None else "w"

    try:
        log(f"Run log: {log_path}")
        log(f"Audit log: {audit_path}")
        log(f"Disagreement report: {review_path}")
        log(f"Quarantine report: {quarantine_path}")
        log(
            "Backfill config: "
            f"mode={mode} words={len(word_order)} eligible_rows_all={eligible_rows} "
            f"verified_null_rows={verified_null_rows} unverified_null_rows={unverified_null_rows} "
            f"queue={word_queue_size} referee_batch={referee_batch_size} progress_every={progress_every} "
            f"max_stagnant={max_stagnant_comparisons}"
        )
        if prior_state is not None:
            log(
                "[resume-reconcile] "
                f"eligible_words={len(bucket_map)} active_kept={len(active_items)} "
                f"active_dropped={stats.resume_active_words_dropped} "
                f"pending_kept={len(pending_words)} pending_dropped={stats.resume_pending_words_dropped} "
                f"deduped={stats.resume_words_deduped}"
            )
            for word in dropped_active_words:
                log(f"[resume_dropped_no_longer_eligible] word={word}")
            for word in stale_wait_words:
                log(f"[resume_stale_wait_deferred] word={word}")

        with (
            review_path.open(review_mode, encoding="utf-8") as review_handle,
            quarantine_path.open(quarantine_mode, encoding="utf-8") as quarantine_handle,
        ):
            next_request_id = 1
            for item in active_items:
                log(f"[resume] word={item.word} comparisons={item.comparisons_done}")

            last_state_flush = 0.0

            def flush_state(*, force: bool = False) -> None:
                nonlocal last_state_flush
                now = time.monotonic()
                if not force and now - last_state_flush < STATE_FLUSH_INTERVAL_SECONDS:
                    return
                _write_state(
                    state_file,
                    dry_run=dry_run,
                    apply=apply,
                    word=word,
                    limit=limit,
                    min_count=min_count,
                    referee_batch_size=referee_batch_size,
                    progress_every=progress_every,
                    word_queue_size=word_queue_size,
                    max_stagnant_comparisons=max_stagnant_comparisons,
                    report_dir=report_dir,
                    review_path=review_path,
                    quarantine_path=quarantine_path,
                    stats=stats,
                    completed_words=completed_words,
                    pending_words=pending_words,
                    active_words=[_queued_word_to_state(item) for item in active_items],
                )
                last_state_flush = now

            def commit_finished(item: _QueuedWord) -> None:
                clusters = item.merge_state.selected
                pending_request_id = item.merge_state.pending_request_id
                candidate_indexes = list(item.merge_state.candidate_indexes)
                after = len(clusters)
                commit_started = time.monotonic()
                stats.standalone_canonicals += after
                update_reduction_stats(stats, word=item.word, before=item.input_count, after=after)
                for cluster in clusters:
                    attached_members = [member for member in cluster.members if member.id]
                    verified_members = [member for member in attached_members if member.verified]
                    unverified_members = [member for member in attached_members if not member.verified]
                    stats.verified_attached_rows += len(verified_members)
                    stats.unverified_attached_rows += len(unverified_members)
                    if unverified_members and cluster.canonical_id is not None:
                        stats.unverified_exact_reuses += len(unverified_members)
                    if (
                        cluster.canonical_id is None
                        and cluster.primary.id
                        and not cluster.primary.verified
                    ):
                        stats.unverified_singleton_canonicals_created += 1
                clue_attach_batches, alias_insert_batches = _apply_clusters(
                    store,
                    item.word,
                    clusters,
                    dry_run=dry_run,
                )
                stats.clue_attach_batches += clue_attach_batches
                stats.alias_insert_batches += alias_insert_batches
                if item.deferred or item.unresolved:
                    stats.unresolved_words += 1
                    quarantine_handle.write(json.dumps({
                        "reason": item.defer_reason or ("referee_error" if item.blocked_on_referee_error else "comparison_unresolved"),
                        "word": item.word,
                        "clues": item.input_count,
                        "canonicals": after,
                        "comparisons": item.comparisons_done,
                        "remaining_clusters": item.defer_remaining_clusters,
                        "deferred": item.deferred,
                        "pending_request_id": (
                            item.resume_stale_wait_info.get("pending_request_id")
                            if item.defer_reason == "resume_stale_wait"
                            else pending_request_id
                        ),
                        "candidate_indexes": (
                            item.resume_stale_wait_info.get("candidate_indexes", [])
                            if item.defer_reason == "resume_stale_wait"
                            else candidate_indexes
                        ),
                        "blocked_on_referee_error": item.blocked_on_referee_error,
                        "last_referee_error": item.last_referee_error,
                    }, ensure_ascii=False) + "\n")
                elif item.input_count > 1 and after == item.input_count:
                    quarantine_handle.write(json.dumps({
                        "reason": "distinct_sense_survivor",
                        "word": item.word,
                        "clues": item.input_count,
                        "canonicals": after,
                        "comparisons": item.comparisons_done,
                        "remaining_clusters": item.defer_remaining_clusters,
                        "deferred": item.deferred,
                    }, ensure_ascii=False) + "\n")
                if item.deferred:
                    stats.deferred_words += 1
                    if item.defer_reason == "stagnation_budget":
                        stats.deferred_due_to_stagnation += 1
                    elif item.defer_reason == "resume_stale_wait":
                        stats.deferred_due_to_resume_stale_wait += 1
                processed_word_set.add(item.word)
                completed_words.append(item.word)
                stats.committed_words += 1
                log(
                    f"[commit] word={item.word} clues={item.input_count} "
                    f"canonicals={after} comparisons={item.comparisons_done} unresolved={item.unresolved} "
                    f"deferred={item.deferred} defer_reason={item.defer_reason or '-'} "
                    f"candidate_pairs={item.candidate_pairs_considered} requests={item.referee_requests_submitted} "
                    f"db_seconds={time.monotonic() - commit_started:.2f}"
                )

            flush_state(force=True)

            while pending_words or active_items:
                cycle_made_progress = False
                words_to_add: list[str] = []
                while pending_words and len(active_items) + len(words_to_add) < max(word_queue_size, 1):
                    bucket_word = pending_words.pop(0)
                    if bucket_word in processed_word_set:
                        continue
                    if bucket_word not in bucket_map:
                        stats.resume_pending_words_dropped += 1
                        log(f"[resume_pending_dropped_no_bucket] word={bucket_word}")
                        continue
                    words_to_add.append(bucket_word)
                if words_to_add:
                    cycle_made_progress = True
                    prefetch_started = time.monotonic()
                    store.prefetch_canonical_variants(words_to_add)
                    stats.canonical_prefetch_batches += 1
                    log(
                        f"[queue-prefetch] words={len(words_to_add)} seconds={time.monotonic() - prefetch_started:.2f}"
                    )
                    for bucket_word in words_to_add:
                        bucket_rows = bucket_map[bucket_word]
                        if len(bucket_rows) == 1:
                            stats.singleton_words += 1
                        active_items.append(
                            _QueuedWord(
                                merge_state=_MergeState(
                                    word=bucket_word,
                                    clusters=_build_initial_clusters(bucket_rows, stats),
                                    selected=_existing_canonical_clusters(store, bucket_word),
                                    boilerplate_tokens=_build_boilerplate_tokens(bucket_rows),
                                ),
                                input_count=len(bucket_rows),
                            )
                        )
                    log(
                        f"[queue] active={len(active_items)} pending={len(pending_words)} "
                        f"added={len(words_to_add)}"
                    )
                    flush_state(force=True)

                deferred_now = []
                for item in active_items:
                    if _should_defer_word(
                        item,
                        max_stagnant_comparisons=max_stagnant_comparisons,
                    ):
                        _defer_word(item, reason="stagnation_budget")
                        deferred_now.append(item.word)
                if deferred_now:
                    cycle_made_progress = True
                    for deferred_word in deferred_now:
                        log(f"[defer] word={deferred_word} reason=stagnation_budget")

                finished_now = [item for item in active_items if item.merge_state.finished()]
                if finished_now:
                    cycle_made_progress = True
                    for item in finished_now:
                        commit_finished(item)
                    active_items = [item for item in active_items if not item.merge_state.finished()]
                    log(f"[queue] active={len(active_items)} pending={len(pending_words)} committed_now={len(finished_now)}")
                    flush_state(force=True)
                    continue

                if not active_items:
                    break

                requests, pending, next_request_id, immediate_resolved_words = _collect_referee_launch_batch(
                    active_items,
                    max_requests=max(referee_batch_size, 1),
                    min_requests_to_launch=min(
                        max(referee_batch_size, 1),
                        DEFAULT_MIN_REFEREE_BATCH_TO_SWITCH,
                    ),
                    next_request_id=next_request_id,
                    stats=stats,
                    review_handle=review_handle,
                )
                if not requests:
                    blocked_words = [
                        item
                        for item in active_items
                        if item.blocked_on_referee_error
                    ]
                    if blocked_words and not pending_words:
                        cycle_made_progress = True
                        for item in blocked_words:
                            _defer_word(item, reason="referee_error")
                            log(f"[defer] word={item.word} reason=referee_error")
                        flush_state(force=True)
                        continue
                    stale_waiting_items = [
                        item
                        for item in active_items
                        if _is_stale_waiting_resume(item)
                    ]
                    if stale_waiting_items:
                        cycle_made_progress = True
                        log(
                            f"[stalled] active={len(active_items)} reason=no_runnable_work "
                            f"stale_waiting={len(stale_waiting_items)}"
                        )
                        for item in stale_waiting_items:
                            log(
                                f"[resume-stale-wait] word={item.word} "
                                f"pending_request_id={item.merge_state.pending_request_id or '-'} "
                                f"candidate_indexes={item.merge_state.candidate_indexes}"
                            )
                            stats.resume_stale_wait_words += 1
                            _defer_word(item, reason="resume_stale_wait")
                        flush_state(force=True)
                        continue
                    if not cycle_made_progress:
                        raise RuntimeError(
                            f"Backfill stalled with {len(active_items)} active words and no runnable work"
                        )
                    flush_state()
                    continue
                if immediate_resolved_words:
                    cycle_made_progress = True
                    log(
                        "[referee-collect] "
                        f"immediate_resolved_words={immediate_resolved_words} "
                        f"compare_ready_words={len({request.word for request in requests})} "
                        f"referee_enqueued_words={len({request.word for request in requests})}"
                    )

                log(
                    "[referee] "
                    f"batch_requests={len(requests)} active_words={len(active_items)} "
                    f"distinct_words={len({request.word for request in requests})} "
                    f"compare_ready_words={len({request.word for request in requests})} "
                    f"immediate_resolved_words={immediate_resolved_words} "
                    f"referee_enqueued_words={len({request.word for request in requests})} "
                    f"launch_floor={DEFAULT_MIN_REFEREE_BATCH_TO_SWITCH} "
                    f"drain_tail={len(requests) < DEFAULT_MIN_REFEREE_BATCH_TO_SWITCH}"
                )
                referee_started = time.monotonic()
                adaptive = service._run_referee_adaptive_batch(requests)
                if adaptive is None:
                    raise RuntimeError("Adaptive referee batch unexpectedly returned no result")
                cycle_made_progress = True
                stats.referee_batches_launched += 1
                stats.comparison_requests += len(requests)
                stats.total_votes += adaptive.total_votes
                stats.referee_phase1_requests += adaptive.phase1_requests
                stats.referee_phase2_requests += adaptive.phase2_requests
                stats.invalid_compare_json_primary += adaptive.invalid_compare_json_primary
                stats.invalid_compare_json_secondary += adaptive.invalid_compare_json_secondary
                log(
                    "[referee] "
                    f"votes={adaptive.total_votes} phase1_requests={adaptive.phase1_requests} "
                    f"phase2_requests={adaptive.phase2_requests} "
                    f"invalid_json_primary={adaptive.invalid_compare_json_primary} "
                    f"invalid_json_secondary={adaptive.invalid_compare_json_secondary} "
                    f"seconds={time.monotonic() - referee_started:.2f}"
                )
                for step_metric in getattr(adaptive, "step_metrics", []):
                    log(
                        "[referee-step] "
                        f"step={step_metric['step_index']} model={step_metric['model_id']} "
                        f"role={step_metric['model_role']} "
                        f"started={step_metric['requests_started']} "
                        f"completed={step_metric['requests_completed_after_step']} "
                        f"remaining={step_metric['requests_remaining_after_step']}"
                    )

                resolved_outcomes = _build_referee_outcomes(pending, adaptive.results)
                touched_indexes = {entry.state_index for entry in pending}
                outcome_by_index = {
                    entry.state_index: entry.outcome
                    for entry in resolved_outcomes
                }
                for entry in resolved_outcomes:
                    _apply_terminal_outcome(
                        active_items[entry.state_index],
                        entry.outcome,
                        stats=stats,
                        review_handle=review_handle,
                    )
                for state_index in touched_indexes:
                    item = active_items[state_index]
                    item.comparisons_done += 1
                    outcome = outcome_by_index[state_index]
                    if outcome.kind in {"merge_into_existing", "promote_new_canonical"}:
                        item.consecutive_non_merge_comparisons = 0
                        item.last_merge_comparison = item.comparisons_done
                    elif outcome.kind == "error_missing_model_votes":
                        item.last_merge_comparison = item.last_merge_comparison
                    else:
                        item.consecutive_non_merge_comparisons += 1
                    if progress_every > 0 and item.comparisons_done % progress_every == 0:
                        log(
                            f"[progress] word={item.word} comparisons={item.comparisons_done} "
                            f"selected_clusters={len(item.merge_state.selected)} "
                            f"remaining_clusters={_remaining_clusters(item.merge_state)} "
                            f"current_compare_index={item.merge_state.compare_index} "
                            f"estimated_remaining_pair_checks={_estimate_remaining_pair_checks(item.merge_state)} "
                            f"consecutive_non_merge={item.consecutive_non_merge_comparisons} "
                            f"deferred={item.deferred} blocked_on_referee_error={item.blocked_on_referee_error}"
                        )
                flush_state()

            flush_state(force=True)

        summary = _build_summary(
            stats=stats,
            verified_rows=verified_rows,
            unverified_rows=unverified_rows,
            processed_words=len(completed_words),
            review_path=review_path,
            quarantine_path=quarantine_path,
            mode=mode,
            referee_batch_size=referee_batch_size,
            word_queue_size=word_queue_size,
            runtime=runtime,
            state_path=state_file,
        )
        log(json.dumps(summary, ensure_ascii=False, indent=2))
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if state_file.exists():
            state_file.unlink()
        return 0
    finally:
        handle.restore()


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
            resume=args.resume,
            state_path=args.state_path,
            progress_every=args.progress_every,
            word_queue_size=args.word_queue_size,
            max_stagnant_comparisons=args.max_stagnant_comparisons,
        )
    if args.command == "audit":
        handle = install_process_logging(
            run_id=f"clue_canon_audit_{path_timestamp()}",
            component="clue_canon_audit",
            tee_console=True,
        )
        try:
            return run_audit(output=args.output)
        finally:
            handle.restore()
    raise SystemExit(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
