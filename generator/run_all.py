"""Unified long-running supervisor for generation and improvement topics.

Current boundary:
- one supervisor process
- local puzzle claims by `puzzle_id`
- local simplify exclusion by `word_normalized`
- admission freeze when both model queues are non-empty

This is a single-process supervisor/orchestrator with queued work items and
local resource claims. It is not a durable event bus: no replay, no pub/sub
subscriber graph, no multi-consumer idempotency, no cross-process leases.
Manual legacy entrypoints or a second process can still race because claims are
in-memory only.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .batch_publish import run_batch
from .batch_publish import MAX_REWRITE_ROUNDS as GENERATE_REWRITE_ROUNDS
from .clue_canon import DEFAULT_SIMPLIFY_BATCH_SIZE
from .config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL, VERIFY_CANDIDATE_COUNT
from .core.clue_canon_store import ClueCanonStore
from .core.clue_canon_simplify import build_candidate_pairs, run_simplify_fanout
from .core.llm_client import create_client as create_ai_client
from .core.llm_dispatch import initial_generation_model
from .core.lm_runtime import LmRuntime
from .core.model_manager import PRIMARY_MODEL, SECONDARY_MODEL
from .core.runtime_logging import (
    add_llm_debug_argument,
    install_process_logging,
    log,
    path_timestamp,
    set_llm_debug_enabled,
)
from .core.supabase_ops import create_service_role_client
from .loop_controller import select_auto_size
from .redefine import (
    REDEFINE_ROUNDS,
    fetch_puzzles as fetch_redefine_puzzles,
    redefine_puzzle,
)
from .retitle import (
    RETITLE_BATCH_SIZE,
    fetch_puzzles as fetch_retitle_puzzles,
    normalize_title_key,
    retitle_puzzle,
    select_puzzles_for_retitle,
)
from .rust_bridge import _rust_binary_path

SUPPORTED_TOPICS = ("generate", "redefine", "retitle", "simplify")
DEFAULT_TOPIC_CAPS = {
    "generate": 1,
    "redefine": 2,
    "retitle": 2,
    "simplify": 1,
}
DEFAULT_IDLE_SLEEP_SECONDS = 15
DEFAULT_HEARTBEAT_SECONDS = 30
DEFAULT_RETRY_LIMIT = 2
LOCK_PATH = Path("/tmp/generator_rebus_run_all.lock")


@dataclass
class RunAllContext:
    supabase: object
    ai_client: object
    rate_client: object
    runtime: LmRuntime
    store: ClueCanonStore
    run_dir: Path
    batch_output_root: Path
    words_path: Path
    multi_model: bool
    dry_run: bool
    generate_rewrite_rounds: int
    redefine_rounds: int
    verify_candidates: int
    simplify_batch_size: int


@dataclass
class SupervisorWorkItem:
    item_id: str
    topic: str
    task_kind: str
    preferred_model_id: str
    target_models: tuple[str, ...]
    run: Callable[[RunAllContext], object]
    puzzle_id: str | None = None
    words: set[str] = field(default_factory=set)
    attempts: int = 0
    available_after: float = 0.0
    admitted_at: float = field(default_factory=time.monotonic)


@dataclass
class ClaimState:
    topic_by_puzzle_id: dict[str, str] = field(default_factory=dict)
    simplify_words: set[str] = field(default_factory=set)
    puzzle_words: dict[str, set[str]] = field(default_factory=dict)

    def has_puzzle(self, puzzle_id: str | None) -> bool:
        return bool(puzzle_id) and puzzle_id in self.topic_by_puzzle_id

    def puzzle_word_conflict(self, words: set[str]) -> bool:
        return bool(words & self.simplify_words)

    def simplify_word_conflict(self, words: set[str]) -> bool:
        for active_words in self.puzzle_words.values():
            if words & active_words:
                return True
        return bool(words & self.simplify_words)

    def claim(self, item: SupervisorWorkItem) -> None:
        if item.puzzle_id:
            self.topic_by_puzzle_id[item.puzzle_id] = item.topic
            self.puzzle_words[item.puzzle_id] = set(item.words)
        if item.topic == "simplify":
            self.simplify_words.update(item.words)

    def release(self, item: SupervisorWorkItem) -> None:
        if item.puzzle_id:
            self.topic_by_puzzle_id.pop(item.puzzle_id, None)
            self.puzzle_words.pop(item.puzzle_id, None)
        if item.topic == "simplify":
            for word in set(item.words):
                self.simplify_words.discard(word)


class RunAllSupervisor:
    def __init__(
        self,
        *,
        context: RunAllContext,
        topics: list[str],
        topic_caps: dict[str, int],
        idle_sleep_seconds: int = DEFAULT_IDLE_SLEEP_SECONDS,
        heartbeat_seconds: int = DEFAULT_HEARTBEAT_SECONDS,
        retry_limit: int = DEFAULT_RETRY_LIMIT,
        debug: bool = False,
    ) -> None:
        self.ctx = context
        self.topics = [topic for topic in topics if topic in SUPPORTED_TOPICS]
        self.topic_caps = {topic: max(1, int(topic_caps.get(topic, 1))) for topic in self.topics}
        self.idle_sleep_seconds = max(1, int(idle_sleep_seconds))
        self.heartbeat_seconds = max(1, int(heartbeat_seconds))
        self.retry_limit = max(0, int(retry_limit))
        self.debug = bool(debug)
        self.pending_items: list[SupervisorWorkItem] = []
        self.active_item: SupervisorWorkItem | None = None
        self.claims = ClaimState()
        self.completed = 0
        self.failed = 0
        self.last_heartbeat_at = 0.0
        self.ctx.runtime.switch_callback = self._on_model_switch

    def run(self, *, max_cycles: int | None = None) -> None:
        cycles = 0
        while True:
            cycles += 1
            self._maybe_heartbeat(force=cycles == 1)
            admitted = self._poll_topics()
            next_item = self._choose_next_item()
            if next_item is None:
                if max_cycles is not None and cycles >= max_cycles:
                    return
                if admitted:
                    continue
                log(
                    f"[run_all idle] topics={','.join(self.topics)} "
                    f"sleep={self.idle_sleep_seconds}s {self._queue_snapshot_text()}"
                )
                time.sleep(self.idle_sleep_seconds)
                continue
            self._run_item(next_item)
            if max_cycles is not None and cycles >= max_cycles:
                return

    def _poll_topics(self) -> int:
        if self._admission_frozen():
            return 0
        admitted = 0
        for topic in self.topics:
            free_slots = self._free_slots(topic)
            if free_slots <= 0:
                continue
            if topic == "generate":
                admitted += self._poll_generate(free_slots)
            elif topic == "redefine":
                admitted += self._poll_redefine(free_slots)
            elif topic == "retitle":
                admitted += self._poll_retitle(free_slots)
            elif topic == "simplify":
                admitted += self._poll_simplify(free_slots)
        return admitted

    def _admission_frozen(self) -> bool:
        queue_by_model = self._queue_counts_by_model()
        if self.ctx.runtime.sync and callable(getattr(self.ctx.runtime, "sync", None)):
            self.ctx.runtime.sync()
        current_model_id = self.ctx.runtime.current_model_id
        if not current_model_id:
            return False
        other_model_id = (
            SECONDARY_MODEL.model_id
            if current_model_id == PRIMARY_MODEL.model_id
            else PRIMARY_MODEL.model_id
        )
        return queue_by_model.get(current_model_id, 0) > 0 and queue_by_model.get(other_model_id, 0) > 0

    def _free_slots(self, topic: str) -> int:
        in_topic = sum(1 for item in self.pending_items if item.topic == topic)
        if self.active_item is not None and self.active_item.topic == topic:
            in_topic += 1
        return max(0, self.topic_caps.get(topic, 0) - in_topic)

    def _poll_generate(self, free_slots: int) -> int:
        admitted = 0
        for _ in range(free_slots):
            size = select_auto_size(client=self.ctx.supabase)
            preferred_model = initial_generation_model(self.ctx.runtime).model_id
            item = SupervisorWorkItem(
                item_id=f"generate:size:{size}:{int(time.time() * 1000)}",
                topic="generate",
                task_kind="generate",
                preferred_model_id=preferred_model,
                target_models=self._targets_for_topic("generate"),
                run=lambda ctx, size=size, index=admitted + 1: self._run_generate(ctx, size=size, index=index),
            )
            self._admit_item(item)
            admitted += 1
        return admitted

    def _poll_redefine(self, free_slots: int) -> int:
        admitted = 0
        rows = fetch_redefine_puzzles(self.ctx.supabase)
        for row in rows:
            if admitted >= free_slots:
                break
            puzzle_id = str(row.get("id") or "")
            if self.claims.has_puzzle(puzzle_id):
                continue
            words = self._fetch_puzzle_words(puzzle_id)
            if self.claims.puzzle_word_conflict(words):
                continue
            item = SupervisorWorkItem(
                item_id=f"redefine:puzzle:{puzzle_id}",
                topic="redefine",
                task_kind="redefine",
                preferred_model_id=PRIMARY_MODEL.model_id,
                target_models=self._targets_for_topic("redefine"),
                run=lambda ctx, row=row: redefine_puzzle(
                    ctx.supabase,
                    row,
                    ctx.ai_client,
                    dry_run=ctx.dry_run,
                    multi_model=ctx.multi_model,
                    rounds=ctx.redefine_rounds,
                    verify_candidates=ctx.verify_candidates,
                    runtime=ctx.runtime,
                ),
                puzzle_id=puzzle_id,
                words=words,
            )
            self._admit_item(item)
            admitted += 1
        return admitted

    def _poll_retitle(self, free_slots: int) -> int:
        admitted = 0
        all_rows = fetch_retitle_puzzles(self.ctx.supabase)
        rows = select_puzzles_for_retitle(all_rows)
        for row in rows:
            if admitted >= free_slots:
                break
            puzzle_id = str(row.get("id") or "")
            if self.claims.has_puzzle(puzzle_id):
                continue
            words = self._fetch_puzzle_words(puzzle_id)
            if self.claims.puzzle_word_conflict(words):
                continue
            item = SupervisorWorkItem(
                item_id=f"retitle:puzzle:{puzzle_id}",
                topic="retitle",
                task_kind="retitle",
                preferred_model_id=PRIMARY_MODEL.model_id,
                target_models=self._targets_for_topic("retitle"),
                run=lambda ctx, row=row: retitle_puzzle(
                    ctx.supabase,
                    row,
                    ctx.ai_client,
                    ctx.rate_client,
                    dry_run=ctx.dry_run,
                    multi_model=ctx.multi_model,
                    runtime=ctx.runtime,
                    forbidden_title_keys=self._forbidden_title_keys(exclude_puzzle_id=str(row.get("id") or "")),
                ),
                puzzle_id=puzzle_id,
                words=words,
            )
            self._admit_item(item)
            admitted += 1
        return admitted

    def _poll_simplify(self, free_slots: int) -> int:
        admitted = 0
        if free_slots <= 0:
            return 0
        pairs = build_candidate_pairs(
            [
                row
                for row in self.ctx.store.fetch_active_canonical_variants()
                if row.word_normalized not in self.claims.simplify_words
                and not self.claims.simplify_word_conflict({row.word_normalized})
            ]
        )
        seen_words: set[str] = set()
        for pair in pairs:
            if admitted >= free_slots:
                break
            if pair.word in seen_words:
                continue
            words = {pair.word}
            if self.claims.simplify_word_conflict(words):
                continue
            item = SupervisorWorkItem(
                item_id=f"simplify:word:{pair.word}:{pair.left_id}:{pair.right_id}",
                topic="simplify",
                task_kind="simplify",
                preferred_model_id=PRIMARY_MODEL.model_id,
                target_models=self._targets_for_topic("simplify"),
                run=lambda ctx, word=pair.word: run_simplify_fanout(
                    store=ctx.store,
                    client=ctx.ai_client,
                    runtime=ctx.runtime,
                    dry_run=ctx.dry_run,
                    apply=not ctx.dry_run,
                    batch_size=ctx.simplify_batch_size,
                    report_dir=str(ctx.run_dir / "simplify" / word),
                    idle_sleep_seconds=0,
                    word=word,
                    stop_after_idle_cycles=0,
                    max_batches=1,
                ),
                words=words,
            )
            self._admit_item(item)
            seen_words.add(pair.word)
            admitted += 1
        return admitted

    def _fetch_puzzle_words(self, puzzle_id: str) -> set[str]:
        rows = self.ctx.store.fetch_clue_rows(
            puzzle_id=puzzle_id,
            extra_fields=("word_normalized",),
        )
        return {
            str(row.get("word_normalized") or "").strip().upper()
            for row in rows
            if str(row.get("word_normalized") or "").strip()
        }

    def _forbidden_title_keys(self, *, exclude_puzzle_id: str) -> set[str]:
        rows = fetch_retitle_puzzles(self.ctx.supabase)
        return {
            normalize_title_key(row.get("title", "") or "")
            for row in rows
            if str(row.get("id") or "") != exclude_puzzle_id and normalize_title_key(row.get("title", "") or "")
        }

    def _targets_for_topic(self, topic: str) -> tuple[str, ...]:
        if not self.ctx.multi_model:
            return (PRIMARY_MODEL.model_id,)
        return (PRIMARY_MODEL.model_id, SECONDARY_MODEL.model_id)

    def _admit_item(self, item: SupervisorWorkItem) -> None:
        self.pending_items.append(item)
        self.claims.claim(item)
        log(
            f"[run_all admit] topic={item.topic} item={item.item_id} task={item.task_kind} "
            f"preferred={item.preferred_model_id} targets={','.join(item.target_models)} "
            f"{self._queue_snapshot_text()}"
        )

    def _choose_next_item(self) -> SupervisorWorkItem | None:
        now = time.monotonic()
        ready = [item for item in self.pending_items if item.available_after <= now]
        if not ready:
            return None
        self.ctx.runtime.sync()
        current_model_id = self.ctx.runtime.current_model_id
        if current_model_id:
            for item in ready:
                if item.preferred_model_id == current_model_id:
                    return item
        if current_model_id:
            other_model_id = (
                SECONDARY_MODEL.model_id
                if current_model_id == PRIMARY_MODEL.model_id
                else PRIMARY_MODEL.model_id
            )
            for item in ready:
                if item.preferred_model_id == other_model_id:
                    self.ctx.runtime.activate(
                        SECONDARY_MODEL if other_model_id == SECONDARY_MODEL.model_id else PRIMARY_MODEL
                    )
                    return item
        preferred = ready[0].preferred_model_id
        if preferred == PRIMARY_MODEL.model_id:
            self.ctx.runtime.activate(PRIMARY_MODEL)
        elif preferred == SECONDARY_MODEL.model_id:
            self.ctx.runtime.activate(SECONDARY_MODEL)
        return ready[0]

    def _run_item(self, item: SupervisorWorkItem) -> None:
        self.active_item = item
        self.pending_items = [pending for pending in self.pending_items if pending.item_id != item.item_id]
        started_at = time.monotonic()
        log(
            f"[run_all start] topic={item.topic} item={item.item_id} task={item.task_kind} "
            f"preferred={item.preferred_model_id}"
        )
        try:
            result = item.run(self.ctx)
        except Exception as exc:
            item.attempts += 1
            if item.attempts <= self.retry_limit:
                item.available_after = time.monotonic() + min(60, 5 * (2 ** (item.attempts - 1)))
                self.pending_items.append(item)
                log(
                    f"[run_all retry] topic={item.topic} item={item.item_id} attempt={item.attempts} "
                    f"backoff_seconds={int(item.available_after - time.monotonic())} error={exc}"
                )
            else:
                self.claims.release(item)
                self.failed += 1
                log(
                    f"[run_all failed] topic={item.topic} item={item.item_id} "
                    f"attempts={item.attempts} error={exc}",
                    level="ERROR",
                )
        else:
            self.claims.release(item)
            self.completed += 1
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            log(
                f"[run_all done] topic={item.topic} item={item.item_id} "
                f"elapsed_ms={elapsed_ms} result={result!r}"
            )
        finally:
            self.active_item = None
            self._maybe_heartbeat(force=False)

    def _run_generate(self, ctx: RunAllContext, *, size: int, index: int) -> object:
        run_dir = ctx.batch_output_root / f"{path_timestamp()}_{size}x{size}_{index:02d}"
        return run_batch(
            sizes=[size],
            output_root=ctx.batch_output_root,
            words_path=ctx.words_path,
            rewrite_rounds=ctx.generate_rewrite_rounds,
            preparation_attempts=3,
            run_dir=run_dir,
            multi_model=ctx.multi_model,
            verify_candidates=ctx.verify_candidates,
            runtime=ctx.runtime,
        )

    def _queue_counts_by_model(self) -> dict[str, int]:
        counts = {PRIMARY_MODEL.model_id: 0, SECONDARY_MODEL.model_id: 0}
        if not self.ctx.multi_model:
            counts = {PRIMARY_MODEL.model_id: 0}
        for item in self.pending_items:
            counts[item.preferred_model_id] = counts.get(item.preferred_model_id, 0) + 1
        return counts

    def _queue_counts_by_topic(self) -> dict[str, int]:
        counts = {topic: 0 for topic in self.topics}
        for item in self.pending_items:
            counts[item.topic] = counts.get(item.topic, 0) + 1
        return counts

    def _queue_snapshot_text(self) -> str:
        model_counts = self._queue_counts_by_model()
        topic_counts = self._queue_counts_by_topic()
        model_text = " ".join(f"{model}={count}" for model, count in sorted(model_counts.items()))
        topic_text = " ".join(f"{topic}={count}" for topic, count in sorted(topic_counts.items()))
        active = self.active_item.item_id if self.active_item is not None else "-"
        return (
            f"queues_model=({model_text}) queues_topic=({topic_text}) "
            f"active={active} completed={self.completed} failed={self.failed}"
        )

    def _on_model_switch(self, previous_model_id: str, next_model_id: str, runtime: LmRuntime) -> None:
        log(
            f"[run_all switch] from={previous_model_id or '-'} to={next_model_id} "
            f"reason=current_queue_empty switch_count={runtime.switch_count} "
            f"{self._queue_snapshot_text()}"
        )

    def _maybe_heartbeat(self, *, force: bool) -> None:
        if not self.debug:
            return
        now = time.monotonic()
        if not force and (now - self.last_heartbeat_at) < self.heartbeat_seconds:
            return
        self.last_heartbeat_at = now
        self.ctx.runtime.sync()
        log(
            f"[run_all heartbeat] loaded={self.ctx.runtime.current_model_label or '-'} "
            f"{self._queue_snapshot_text()}"
        )


def _parse_topics(value: str | None) -> list[str]:
    if not value:
        return list(SUPPORTED_TOPICS)
    topics = [topic.strip().lower() for topic in value.split(",") if topic.strip()]
    invalid = [topic for topic in topics if topic not in SUPPORTED_TOPICS]
    if invalid:
        raise SystemExit(f"Unsupported topics: {', '.join(invalid)}")
    deduped: list[str] = []
    for topic in topics:
        if topic not in deduped:
            deduped.append(topic)
    return deduped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified long-running supervisor for generation and improvement.")
    parser.add_argument(
        "--topics",
        help="Comma-separated topics: generate,redefine,retitle,simplify (default: all).",
    )
    parser.add_argument("--words", default="generator/output/words.json", help="Path to words.json cache.")
    parser.add_argument("--output-root", default="generator/output/run_all_runs", help="Supervisor artifact root.")
    parser.add_argument("--generate-cap", type=int, default=DEFAULT_TOPIC_CAPS["generate"])
    parser.add_argument("--redefine-cap", type=int, default=DEFAULT_TOPIC_CAPS["redefine"])
    parser.add_argument("--retitle-cap", type=int, default=DEFAULT_TOPIC_CAPS["retitle"])
    parser.add_argument("--simplify-cap", type=int, default=DEFAULT_TOPIC_CAPS["simplify"])
    parser.add_argument("--idle-sleep-seconds", type=int, default=DEFAULT_IDLE_SLEEP_SECONDS)
    parser.add_argument("--heartbeat-seconds", type=int, default=DEFAULT_HEARTBEAT_SECONDS)
    parser.add_argument("--rewrite-rounds", type=int, default=GENERATE_REWRITE_ROUNDS)
    parser.add_argument("--rounds", type=int, default=REDEFINE_ROUNDS)
    parser.add_argument("--verify-candidates", type=int, default=VERIFY_CANDIDATE_COUNT)
    parser.add_argument("--simplify-batch-size", type=int, default=DEFAULT_SIMPLIFY_BATCH_SIZE)
    parser.add_argument(
        "--multi-model",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use the configured two-model workflow (default: True).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not persist DB changes for non-generation topics.")
    add_llm_debug_argument(parser)
    return parser


@contextlib.contextmanager
def _singleton_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise SystemExit(f"Another run_all supervisor already holds {path}") from exc
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    try:
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _preflight(*, topics: list[str]) -> None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
    create_service_role_client()
    runtime = LmRuntime(multi_model=True)
    runtime.sync()
    if "generate" in topics:
        _rust_binary_path()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    topics = _parse_topics(args.topics)
    if args.dry_run and "generate" in topics:
        parser.error("--dry-run is not supported when generate topic is enabled")

    run_root = Path(args.output_root)
    run_dir = run_root / path_timestamp()
    log_path = run_dir / "run.log"
    audit_path = run_dir / "audit.jsonl"
    handle = install_process_logging(
        run_id=run_dir.name,
        component="run_all",
        log_path=log_path,
        audit_path=audit_path,
        tee_console=True,
    )
    try:
        set_llm_debug_enabled(bool(args.debug))
        log(f"Run log: {log_path}")
        log(f"Audit log: {audit_path}")
        log(f"Topics: {','.join(topics)}")
        with _singleton_lock(LOCK_PATH):
            _preflight(topics=topics)
            supabase = create_service_role_client()
            runtime = LmRuntime(multi_model=args.multi_model)
            context = RunAllContext(
                supabase=supabase,
                ai_client=create_ai_client(),
                rate_client=create_ai_client(),
                runtime=runtime,
                store=ClueCanonStore(client=supabase),
                run_dir=run_dir,
                batch_output_root=run_dir / "batch",
                words_path=Path(args.words),
                multi_model=args.multi_model,
                dry_run=bool(args.dry_run),
                generate_rewrite_rounds=max(1, args.rewrite_rounds),
                redefine_rounds=max(1, args.rounds),
                verify_candidates=max(1, args.verify_candidates),
                simplify_batch_size=max(1, args.simplify_batch_size),
            )
            supervisor = RunAllSupervisor(
                context=context,
                topics=topics,
                topic_caps={
                    "generate": args.generate_cap,
                    "redefine": args.redefine_cap,
                    "retitle": args.retitle_cap,
                    "simplify": args.simplify_cap,
                },
                idle_sleep_seconds=max(1, args.idle_sleep_seconds),
                heartbeat_seconds=max(1, args.heartbeat_seconds),
                debug=bool(args.debug),
            )
            supervisor.run()
        return 0
    finally:
        handle.restore()


if __name__ == "__main__":
    raise SystemExit(main())
