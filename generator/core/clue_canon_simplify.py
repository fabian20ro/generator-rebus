"""Continuous canonical fanout simplifier."""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import random
import signal
import time

from .ai_clues import (
    has_prompt_residue,
    rewrite_merged_canonical_definition,
    validate_merged_canonical_definition,
)
from .definition_referee import compare_definition_variants_attempt
from .clue_canon import content_tokens, lexical_similarity, normalize_definition_text
from .clue_canon_store import ClueCanonStore
from .clue_canon_types import CanonicalDefinition, ClueDefinitionRecord
from .model_manager import PRIMARY_MODEL, SECONDARY_MODEL
from .runtime_logging import log, path_timestamp

DEFAULT_BATCH_SIZE = 40
DEFAULT_IDLE_SLEEP_SECONDS = 30
DEFAULT_SIMPLIFY_STATE_PATH = Path("build/clue_canon/simplify_state.json")
SIMPLIFY_STATE_VERSION = 1


@dataclass(frozen=True)
class SimplifyCandidatePair:
    key: str
    word: str
    word_type: str
    usage_label: str
    left_id: str
    right_id: str
    left_definition: str
    right_definition: str
    left_definition_norm: str
    right_definition_norm: str
    weight: float


@dataclass
class SimplifyStats:
    pairs_sampled: int = 0
    pairs_compared: int = 0
    pairs_same_sense: int = 0
    pairs_merged: int = 0
    compare_invalid: int = 0
    rewrite_invalid: int = 0
    rewrite_fallback_existing: int = 0
    db_failures: int = 0
    canonical_rows_before: int = 0
    canonical_rows_current: int = 0
    top_words_reduced: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class SimplifyRunState:
    seed: int
    rng_state: str
    report_dir: str
    stats: dict[str, object]
    attempted_pair_keys: list[str]
    cooldown_pair_keys: list[str]
    current_batch: list[dict[str, object]]
    word: str | None
    batch_size: int
    idle_sleep_seconds: int
    dry_run: bool
    apply: bool
    pool_version: int = 0


SimplifyApprovedMerge = tuple[
    SimplifyCandidatePair,
    CanonicalDefinition,
    CanonicalDefinition,
    str,
    bool,
]


def _canonical_bucket_key(row: CanonicalDefinition) -> tuple[str, str, str]:
    return (row.word_normalized, row.word_type, row.usage_label)


def _pair_key(left: CanonicalDefinition, right: CanonicalDefinition) -> str:
    ordered = sorted([left.id, right.id])
    return f"{ordered[0]}::{ordered[1]}"


def _pair_weight(
    left: CanonicalDefinition,
    right: CanonicalDefinition,
    *,
    bucket_size: int,
) -> float:
    shared = len(set(content_tokens(left.definition)) & set(content_tokens(right.definition)))
    similarity = lexical_similarity(left.definition_norm, right.definition_norm)
    return max(0.1, float(bucket_size) + shared * 2.0 + similarity)


def _should_skip_source_definition(definition: str) -> bool:
    return has_prompt_residue(definition)


def _likely_simplify_pair(left: CanonicalDefinition, right: CanonicalDefinition) -> bool:
    if left.word_normalized != right.word_normalized:
        return False
    if left.word_type != right.word_type:
        return False
    if left.usage_label != right.usage_label:
        return False
    if left.definition_norm == right.definition_norm:
        return False
    if _should_skip_source_definition(left.definition) or _should_skip_source_definition(right.definition):
        return False
    shared = len(set(content_tokens(left.definition)) & set(content_tokens(right.definition)))
    similarity = lexical_similarity(left.definition_norm, right.definition_norm)
    return shared >= 2 or similarity >= 0.86 or (shared >= 1 and similarity >= 0.74)


def build_candidate_pairs(
    canonicals: list[CanonicalDefinition],
    *,
    attempted_pair_keys: set[str] | None = None,
    cooldown_pair_keys: set[str] | None = None,
) -> list[SimplifyCandidatePair]:
    attempted = attempted_pair_keys or set()
    cooldown = cooldown_pair_keys or set()
    buckets: dict[tuple[str, str, str], list[CanonicalDefinition]] = defaultdict(list)
    for row in canonicals:
        if row.superseded_by:
            continue
        buckets[_canonical_bucket_key(row)].append(row)
    pairs: list[SimplifyCandidatePair] = []
    for (_word, _word_type, _usage_label), rows in buckets.items():
        if len(rows) < 2:
            continue
        rows = sorted(rows, key=lambda row: row.id)
        for index, left in enumerate(rows):
            for right in rows[index + 1 :]:
                key = _pair_key(left, right)
                if key in attempted or key in cooldown:
                    continue
                if not _likely_simplify_pair(left, right):
                    continue
                pairs.append(
                    SimplifyCandidatePair(
                        key=key,
                        word=left.word_normalized,
                        word_type=left.word_type,
                        usage_label=left.usage_label,
                        left_id=left.id,
                        right_id=right.id,
                        left_definition=left.definition,
                        right_definition=right.definition,
                        left_definition_norm=left.definition_norm,
                        right_definition_norm=right.definition_norm,
                        weight=_pair_weight(left, right, bucket_size=len(rows)),
                    )
                )
    pairs.sort(key=lambda pair: (-pair.weight, pair.word, pair.left_id, pair.right_id))
    return pairs


def sample_candidate_batch(
    pairs: list[SimplifyCandidatePair],
    *,
    batch_size: int,
    rng: random.Random,
) -> list[SimplifyCandidatePair]:
    if batch_size <= 0 or not pairs:
        return []
    remaining = list(pairs)
    selected: list[SimplifyCandidatePair] = []
    used_ids: set[str] = set()
    while remaining and len(selected) < batch_size:
        weights = [pair.weight for pair in remaining]
        chosen = remaining.pop(rng.choices(range(len(remaining)), weights=weights, k=1)[0])
        if chosen.left_id in used_ids or chosen.right_id in used_ids:
            continue
        selected.append(chosen)
        used_ids.add(chosen.left_id)
        used_ids.add(chosen.right_id)
    return selected


def select_candidate_batch(
    pairs: list[SimplifyCandidatePair],
    *,
    batch_size: int,
) -> list[SimplifyCandidatePair]:
    if batch_size <= 0 or not pairs:
        return []
    selected: list[SimplifyCandidatePair] = []
    used_ids: set[str] = set()
    for pair in pairs:
        if pair.left_id in used_ids or pair.right_id in used_ids:
            continue
        selected.append(pair)
        used_ids.add(pair.left_id)
        used_ids.add(pair.right_id)
        if len(selected) >= batch_size:
            break
    return selected


def choose_existing_survivor(
    left: CanonicalDefinition,
    right: CanonicalDefinition,
) -> CanonicalDefinition:
    return sorted(
        [left, right],
        key=lambda row: (
            0 if row.verified else 1,
            -(row.usage_count or 0),
            -(row.semantic_score or -1),
            -(row.rebus_score or -1),
            -(row.creativity_score or -1),
            len(row.definition or ""),
            row.id,
        ),
    )[0]


def should_rewrite_survivor(
    left: CanonicalDefinition,
    right: CanonicalDefinition,
) -> bool:
    return not (_strong_existing_survivor(left) or _strong_existing_survivor(right))


def _strong_existing_survivor(row: CanonicalDefinition) -> bool:
    if row.verified:
        return True
    semantic = int(row.semantic_score or 0)
    rebus = int(row.rebus_score or 0)
    creativity = int(row.creativity_score or 0)
    return semantic >= 8 and rebus >= 7 and creativity >= 5


def _survivor_record_from_definition(
    source: CanonicalDefinition,
    *,
    definition: str,
) -> ClueDefinitionRecord:
    clean_definition = str(definition or "").strip()
    return ClueDefinitionRecord(
        id="",
        word_normalized=source.word_normalized,
        word_original=source.word_original_seed,
        definition=clean_definition,
        definition_norm=normalize_definition_text(clean_definition),
        word_type=source.word_type,
        usage_label=source.usage_label,
        verified=bool(source.verified),
        semantic_score=source.semantic_score,
        rebus_score=source.rebus_score,
        creativity_score=source.creativity_score,
    )


def _serialize_pair(pair: SimplifyCandidatePair) -> dict[str, object]:
    return asdict(pair)


def _deserialize_pair(payload: dict[str, object]) -> SimplifyCandidatePair:
    return SimplifyCandidatePair(
        key=str(payload.get("key") or ""),
        word=str(payload.get("word") or ""),
        word_type=str(payload.get("word_type") or ""),
        usage_label=str(payload.get("usage_label") or ""),
        left_id=str(payload.get("left_id") or ""),
        right_id=str(payload.get("right_id") or ""),
        left_definition=str(payload.get("left_definition") or ""),
        right_definition=str(payload.get("right_definition") or ""),
        left_definition_norm=str(payload.get("left_definition_norm") or ""),
        right_definition_norm=str(payload.get("right_definition_norm") or ""),
        weight=float(payload.get("weight") or 0.0),
    )


def _stats_from_payload(payload: dict[str, object] | None) -> SimplifyStats:
    payload = payload or {}
    stats = SimplifyStats()
    stats.pairs_sampled = int(payload.get("pairs_sampled") or 0)
    stats.pairs_compared = int(payload.get("pairs_compared") or 0)
    stats.pairs_same_sense = int(payload.get("pairs_same_sense") or 0)
    stats.pairs_merged = int(payload.get("pairs_merged") or 0)
    stats.compare_invalid = int(payload.get("compare_invalid") or 0)
    stats.rewrite_invalid = int(payload.get("rewrite_invalid") or 0)
    stats.rewrite_fallback_existing = int(payload.get("rewrite_fallback_existing") or 0)
    stats.db_failures = int(payload.get("db_failures") or 0)
    stats.canonical_rows_before = int(payload.get("canonical_rows_before") or 0)
    stats.canonical_rows_current = int(payload.get("canonical_rows_current") or 0)
    stats.top_words_reduced = [
        (str(item[0]), int(item[1]))
        for item in list(payload.get("top_words_reduced") or [])
        if isinstance(item, (list, tuple)) and len(item) == 2
    ]
    return stats


def _stats_to_payload(stats: SimplifyStats) -> dict[str, object]:
    return {
        "pairs_sampled": stats.pairs_sampled,
        "pairs_compared": stats.pairs_compared,
        "pairs_same_sense": stats.pairs_same_sense,
        "pairs_merged": stats.pairs_merged,
        "compare_invalid": stats.compare_invalid,
        "rewrite_invalid": stats.rewrite_invalid,
        "rewrite_fallback_existing": stats.rewrite_fallback_existing,
        "db_failures": stats.db_failures,
        "canonical_rows_before": stats.canonical_rows_before,
        "canonical_rows_current": stats.canonical_rows_current,
        "top_words_reduced": list(stats.top_words_reduced),
    }


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _default_state_path() -> Path:
    DEFAULT_SIMPLIFY_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    return DEFAULT_SIMPLIFY_STATE_PATH


def _write_state(
    state_path: Path,
    *,
    rng: random.Random,
    report_dir: Path,
    stats: SimplifyStats,
    attempted_pair_keys: set[str],
    cooldown_pair_keys: set[str],
    current_batch: list[SimplifyCandidatePair],
    word: str | None,
    batch_size: int,
    idle_sleep_seconds: int,
    dry_run: bool,
    apply: bool,
    pool_version: int,
) -> None:
    payload = {
        "version": SIMPLIFY_STATE_VERSION,
        "seed": 0,
        "rng_state": repr(rng.getstate()),
        "report_dir": str(report_dir),
        "stats": _stats_to_payload(stats),
        "attempted_pair_keys": sorted(attempted_pair_keys),
        "cooldown_pair_keys": sorted(cooldown_pair_keys),
        "current_batch": [_serialize_pair(pair) for pair in current_batch],
        "word": word,
        "batch_size": batch_size,
        "idle_sleep_seconds": idle_sleep_seconds,
        "dry_run": dry_run,
        "apply": apply,
        "pool_version": pool_version,
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_state(
    state_path: Path,
    *,
    dry_run: bool,
    apply: bool,
    word: str | None,
    batch_size: int,
    idle_sleep_seconds: int,
) -> tuple[random.Random, Path, SimplifyStats, set[str], set[str], list[SimplifyCandidatePair], int] | None:
    if not state_path.exists():
        return None
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    if int(payload.get("version") or 0) != SIMPLIFY_STATE_VERSION:
        raise SystemExit(f"Unsupported simplify state version in {state_path}")
    if bool(payload.get("dry_run")) != dry_run or bool(payload.get("apply")) != apply:
        raise SystemExit(f"Simplify state {state_path} exists with different mode flags")
    if str(payload.get("word") or "") != str(word or ""):
        raise SystemExit(f"Simplify state {state_path} exists with different --word")
    if int(payload.get("batch_size") or 0) != batch_size:
        raise SystemExit(f"Simplify state {state_path} exists with different --batch-size")
    if int(payload.get("idle_sleep_seconds") or 0) != idle_sleep_seconds:
        raise SystemExit(f"Simplify state {state_path} exists with different --idle-sleep-seconds")
    rng = random.Random()
    rng.setstate(ast.literal_eval(str(payload.get("rng_state"))))
    return (
        rng,
        Path(str(payload.get("report_dir"))),
        _stats_from_payload(dict(payload.get("stats") or {})),
        {str(item) for item in list(payload.get("attempted_pair_keys") or []) if str(item)},
        {str(item) for item in list(payload.get("cooldown_pair_keys") or []) if str(item)},
        [_deserialize_pair(dict(item)) for item in list(payload.get("current_batch") or []) if isinstance(item, dict)],
        int(payload.get("pool_version") or 0),
    )


def _refresh_bucket_rows(
    store: ClueCanonStore,
    buckets: dict[tuple[str, str, str], list[CanonicalDefinition]],
    *,
    touched_words: set[str],
    word_filter: str | None,
) -> None:
    rows = store.fetch_active_canonical_variants_for_words(sorted(touched_words))
    for key in list(buckets):
        if key[0] in touched_words:
            buckets.pop(key, None)
    for row in rows:
        if word_filter and row.word_normalized != word_filter:
            continue
        buckets[_canonical_bucket_key(row)].append(row)


def _all_bucket_rows(
    store: ClueCanonStore,
    *,
    word_filter: str | None,
) -> dict[tuple[str, str, str], list[CanonicalDefinition]]:
    rows = store.fetch_active_canonical_variants(word_normalized=word_filter)
    buckets: dict[tuple[str, str, str], list[CanonicalDefinition]] = defaultdict(list)
    for row in rows:
        buckets[_canonical_bucket_key(row)].append(row)
    return buckets


def load_simplify_bucket(
    store: ClueCanonStore,
    *,
    word: str | None,
    batch_size: int,
) -> tuple[dict[tuple[str, str, str], list[CanonicalDefinition]], list[SimplifyCandidatePair]]:
    normalized_word = str(word or "").strip().upper() or None
    buckets = _all_bucket_rows(store, word_filter=normalized_word)
    pairs = build_candidate_pairs(
        [row for rows in buckets.values() for row in rows],
    )
    return buckets, select_candidate_batch(pairs, batch_size=batch_size)


def _find_pair_rows(
    pair: SimplifyCandidatePair,
    buckets: dict[tuple[str, str, str], list[CanonicalDefinition]],
) -> tuple[CanonicalDefinition, CanonicalDefinition] | None:
    rows = buckets.get((pair.word, pair.word_type, pair.usage_label), [])
    by_id = {row.id: row for row in rows}
    left = by_id.get(pair.left_id)
    right = by_id.get(pair.right_id)
    if left is None or right is None:
        return None
    return left, right


def find_simplify_pair_rows(
    pair: SimplifyCandidatePair,
    buckets: dict[tuple[str, str, str], list[CanonicalDefinition]],
) -> tuple[CanonicalDefinition, CanonicalDefinition] | None:
    return _find_pair_rows(pair, buckets)


def _run_compare_phase(client, runtime, pairs: list[SimplifyCandidatePair], *, model_id: str) -> dict[str, object]:
    if runtime is not None:
        runtime.activate(PRIMARY_MODEL if model_id == PRIMARY_MODEL.model_id else SECONDARY_MODEL)
    results: dict[str, object] = {}
    for pair in pairs:
        results[pair.key] = compare_definition_variants_attempt(
            client,
            pair.word,
            len(pair.word),
            pair.left_definition,
            pair.right_definition,
            model=model_id,
        )
    return results


def compare_simplify_pairs(
    client,
    runtime,
    pairs: list[SimplifyCandidatePair],
    *,
    model_id: str,
) -> dict[str, object]:
    return _run_compare_phase(client, runtime, pairs, model_id=model_id)


def _apply_merge(
    *,
    store: ClueCanonStore,
    left: CanonicalDefinition,
    right: CanonicalDefinition,
    survivor_definition: str,
    dry_run: bool,
) -> str:
    best_source = choose_existing_survivor(left, right)
    if dry_run:
        return f"dry-run:{best_source.word_normalized}:{normalize_definition_text(survivor_definition)}"
    survivor = store.create_canonical_definition(
        _survivor_record_from_definition(best_source, definition=survivor_definition)
    )
    if survivor is None:
        raise RuntimeError("failed to create survivor canonical")
    store.repoint_clues_by_canonical_ids(
        [left.id, right.id],
        canonical_definition_id=survivor.id,
    )
    store.mark_canonicals_superseded(
        [left.id, right.id],
        superseded_by=survivor.id,
    )
    return survivor.id


def apply_simplify_merge(
    *,
    store: ClueCanonStore,
    left: CanonicalDefinition,
    right: CanonicalDefinition,
    survivor_definition: str,
    dry_run: bool,
) -> str:
    return _apply_merge(
        store=store,
        left=left,
        right=right,
        survivor_definition=survivor_definition,
        dry_run=dry_run,
    )


def _update_top_reductions(stats: SimplifyStats, *, word: str) -> None:
    current = {existing_word: reduction for existing_word, reduction in stats.top_words_reduced}
    current[word] = current.get(word, 0) + 1
    stats.top_words_reduced = sorted(current.items(), key=lambda item: (-item[1], item[0]))[:20]


def update_top_reductions(stats: SimplifyStats, *, word: str) -> None:
    _update_top_reductions(stats, word=word)


def refresh_simplify_bucket_rows(
    store: ClueCanonStore,
    buckets: dict[tuple[str, str, str], list[CanonicalDefinition]],
    *,
    touched_words: set[str],
    word_filter: str | None,
) -> None:
    _refresh_bucket_rows(
        store,
        buckets,
        touched_words=touched_words,
        word_filter=word_filter,
    )


def _build_summary(
    *,
    stats: SimplifyStats,
    report_dir: Path,
    state_path: Path,
    batch_size: int,
    word: str | None,
    runtime,
) -> dict[str, object]:
    return {
        **_stats_to_payload(stats),
        "model_switches": getattr(runtime, "switch_count", 0),
        "model_activations": getattr(runtime, "activation_count", 0),
        "report_dir": str(report_dir),
        "state_path": str(state_path),
        "batch_size": batch_size,
        "word": word,
    }


def run_simplify_fanout(
    *,
    store: ClueCanonStore,
    client,
    runtime,
    dry_run: bool,
    apply: bool,
    batch_size: int = DEFAULT_BATCH_SIZE,
    state_path: str | None = None,
    report_dir: str | None = None,
    seed: int | None = None,
    idle_sleep_seconds: int = DEFAULT_IDLE_SLEEP_SECONDS,
    word: str | None = None,
    stop_after_idle_cycles: int | None = None,
    max_batches: int | None = None,
) -> int:
    if dry_run == apply:
        raise SystemExit("Specify exactly one of --dry-run or --apply")
    normalized_word = str(word or "").strip().upper() or None
    state_file = Path(state_path) if state_path else _default_state_path()
    loaded = _load_state(
        state_file,
        dry_run=dry_run,
        apply=apply,
        word=normalized_word,
        batch_size=batch_size,
        idle_sleep_seconds=idle_sleep_seconds,
    )
    if loaded is None:
        rng = random.Random(seed if seed is not None else int(time.time()))
        current_report_dir = Path(report_dir) if report_dir else Path("build/clue_canon_simplify") / path_timestamp()
        stats = SimplifyStats()
        attempted_pair_keys: set[str] = set()
        cooldown_pair_keys: set[str] = set()
        current_batch: list[SimplifyCandidatePair] = []
        pool_version = 0
    else:
        rng, current_report_dir, stats, attempted_pair_keys, cooldown_pair_keys, current_batch, pool_version = loaded
        if report_dir:
            current_report_dir = Path(report_dir)
    current_report_dir.mkdir(parents=True, exist_ok=True)
    merges_path = current_report_dir / "merges.jsonl"
    skipped_path = current_report_dir / "skipped.jsonl"
    summary_path = current_report_dir / "summary.json"

    buckets = _all_bucket_rows(store, word_filter=normalized_word)
    all_rows = [row for rows in buckets.values() for row in rows]
    if not stats.canonical_rows_before:
        stats.canonical_rows_before = len(all_rows)
    stats.canonical_rows_current = len(all_rows)

    stop_requested = False

    def _request_stop(_signum=None, _frame=None):
        nonlocal stop_requested
        stop_requested = True
        log("[simplify] stop requested; flushing after current step")

    previous_int = signal.signal(signal.SIGINT, _request_stop)
    previous_term = signal.signal(signal.SIGTERM, _request_stop)
    idle_cycles = 0
    batches_run = 0
    try:
        while True:
            all_rows = [row for rows in buckets.values() for row in rows]
            pair_pool = build_candidate_pairs(
                all_rows,
                attempted_pair_keys=attempted_pair_keys,
                cooldown_pair_keys=cooldown_pair_keys,
            )
            current_pair_keys = {pair.key for pair in pair_pool}
            attempted_pair_keys &= current_pair_keys
            cooldown_pair_keys &= current_pair_keys
            if current_batch:
                batch_pairs = [pair for pair in current_batch if _find_pair_rows(pair, buckets) is not None]
                current_batch = []
            else:
                batch_pairs = sample_candidate_batch(pair_pool, batch_size=batch_size, rng=rng)
            if not batch_pairs:
                idle_cycles += 1
                _write_state(
                    state_file,
                    rng=rng,
                    report_dir=current_report_dir,
                    stats=stats,
                    attempted_pair_keys=attempted_pair_keys,
                    cooldown_pair_keys=cooldown_pair_keys,
                    current_batch=[],
                    word=normalized_word,
                    batch_size=batch_size,
                    idle_sleep_seconds=idle_sleep_seconds,
                    dry_run=dry_run,
                    apply=apply,
                    pool_version=pool_version,
                )
                summary = _build_summary(
                    stats=stats,
                    report_dir=current_report_dir,
                    state_path=state_file,
                    batch_size=batch_size,
                    word=normalized_word,
                    runtime=runtime,
                )
                summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
                if stop_requested:
                    return 0
                if stop_after_idle_cycles is not None and idle_cycles >= stop_after_idle_cycles:
                    return 0
                log(f"[simplify-idle] pairs=0 sleeping={idle_sleep_seconds}s")
                time.sleep(idle_sleep_seconds)
                buckets = _all_bucket_rows(store, word_filter=normalized_word)
                continue

            idle_cycles = 0
            current_batch = list(batch_pairs)
            _write_state(
                state_file,
                rng=rng,
                report_dir=current_report_dir,
                stats=stats,
                attempted_pair_keys=attempted_pair_keys,
                cooldown_pair_keys=cooldown_pair_keys,
                current_batch=current_batch,
                word=normalized_word,
                batch_size=batch_size,
                idle_sleep_seconds=idle_sleep_seconds,
                dry_run=dry_run,
                apply=apply,
                pool_version=pool_version,
            )
            stats.pairs_sampled += len(batch_pairs)
            log(f"[simplify-batch] sampled={len(batch_pairs)} pool={len(pair_pool)}")
            phase1 = _run_compare_phase(client, runtime, batch_pairs, model_id=PRIMARY_MODEL.model_id)
            phase2 = _run_compare_phase(client, runtime, batch_pairs, model_id=SECONDARY_MODEL.model_id)
            stats.pairs_compared += len(batch_pairs) * 2
            approved_pairs: list[tuple[SimplifyCandidatePair, CanonicalDefinition, CanonicalDefinition, str]] = []
            for pair in batch_pairs:
                attempted_pair_keys.add(pair.key)
                first = phase1[pair.key]
                second = phase2[pair.key]
                if first.vote is None or second.vote is None:
                    stats.compare_invalid += 1
                    cooldown_pair_keys.add(pair.key)
                    _append_jsonl(skipped_path, {
                        "word": pair.word,
                        "pair_key": pair.key,
                        "reason": "compare_invalid",
                        "phase1_status": first.parse_status,
                        "phase2_status": second.parse_status,
                    })
                    continue
                if not first.vote.same_meaning or not second.vote.same_meaning:
                    _append_jsonl(skipped_path, {
                        "word": pair.word,
                        "pair_key": pair.key,
                        "reason": "not_same_meaning",
                    })
                    continue
                found = _find_pair_rows(pair, buckets)
                if found is None:
                    _append_jsonl(skipped_path, {
                        "word": pair.word,
                        "pair_key": pair.key,
                        "reason": "pair_no_longer_active",
                    })
                    continue
                left, right = found
                stats.pairs_same_sense += 1
                survivor_source = choose_existing_survivor(left, right)
                if should_rewrite_survivor(left, right):
                    rewrite = rewrite_merged_canonical_definition(
                        client,
                        word=pair.word,
                        definition_a=left.definition,
                        definition_b=right.definition,
                        model=SECONDARY_MODEL.model_id,
                    )
                    approved_pairs.append((pair, left, right, rewrite.definition, True))
                else:
                    approved_pairs.append((pair, left, right, survivor_source.definition, False))
            if approved_pairs:
                if runtime is not None:
                    runtime.activate(PRIMARY_MODEL)
            touched_words: set[str] = set()
            for pair, left, right, rewritten_definition, rewrite_attempted in approved_pairs:
                survivor_source = choose_existing_survivor(left, right)
                survivor_definition = rewritten_definition
                rewrite_validated = False
                if rewrite_attempted:
                    validation = validate_merged_canonical_definition(
                        client,
                        word=pair.word,
                        answer_length=len(pair.word),
                        definition_a=left.definition,
                        definition_b=right.definition,
                        candidate_definition=rewritten_definition,
                        model=PRIMARY_MODEL.model_id,
                    )
                    rewrite_validated = validation.accepted
                    if not validation.accepted:
                        stats.rewrite_invalid += 1
                        stats.rewrite_fallback_existing += 1
                        survivor_definition = survivor_source.definition
                try:
                    survivor_id = _apply_merge(
                        store=store,
                        left=left,
                        right=right,
                        survivor_definition=survivor_definition,
                        dry_run=dry_run,
                    )
                except Exception as exc:
                    stats.db_failures += 1
                    cooldown_pair_keys.add(pair.key)
                    _append_jsonl(skipped_path, {
                        "word": pair.word,
                        "pair_key": pair.key,
                        "reason": "db_failure",
                        "error": str(exc),
                    })
                    continue
                stats.pairs_merged += 1
                _update_top_reductions(stats, word=pair.word)
                _append_jsonl(merges_path, {
                    "word": pair.word,
                    "pair_key": pair.key,
                    "left_id": left.id,
                    "right_id": right.id,
                    "survivor_id": survivor_id,
                    "survivor_definition": survivor_definition,
                    "rewrite_attempted": rewrite_attempted,
                    "rewrite_validated": rewrite_validated,
                })
                touched_words.add(pair.word)
            current_batch = []
            if touched_words:
                _refresh_bucket_rows(
                    store,
                    buckets,
                    touched_words=touched_words,
                    word_filter=normalized_word,
                )
            stats.canonical_rows_current = len([row for rows in buckets.values() for row in rows])
            summary = _build_summary(
                stats=stats,
                report_dir=current_report_dir,
                state_path=state_file,
                batch_size=batch_size,
                word=normalized_word,
                runtime=runtime,
            )
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            _write_state(
                state_file,
                rng=rng,
                report_dir=current_report_dir,
                stats=stats,
                attempted_pair_keys=attempted_pair_keys,
                cooldown_pair_keys=cooldown_pair_keys,
                current_batch=[],
                word=normalized_word,
                batch_size=batch_size,
                idle_sleep_seconds=idle_sleep_seconds,
                dry_run=dry_run,
                apply=apply,
                pool_version=pool_version + 1,
            )
            pool_version += 1
            log(
                f"[simplify-batch-done] merged={stats.pairs_merged} same_sense={stats.pairs_same_sense} "
                f"compare_invalid={stats.compare_invalid} rewrite_invalid={stats.rewrite_invalid}"
            )
            batches_run += 1
            if max_batches is not None and batches_run >= max_batches:
                return 0
            if stop_requested:
                return 0
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
