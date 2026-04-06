"""State serialization, resumption, and deferred-word logic for clue_canon backfill."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .core.clue_canon_types import (
    BackfillStats,
    ClueDefinitionRecord,
    QueuedWordState,
    WordReducerState,
    WorkingCluster,
)
from .core.lm_runtime import LmRuntime
from .core.runtime_logging import log

_WorkingCluster = WorkingCluster
_MergeState = WordReducerState
_QueuedWord = QueuedWordState

DEFAULT_STATE_PATH = Path("build/clue_canon/backfill_state.json")
STATE_VERSION = 3
STATE_FLUSH_INTERVAL_SECONDS = 10.0
DEFAULT_MAX_STAGNANT_COMPARISONS = 120

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

