"""Thin CLI/assembly entrypoint for the run_all supervisor."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import os
from pathlib import Path

from .clue_canon import DEFAULT_SIMPLIFY_BATCH_SIZE
from .config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL, VERIFY_CANDIDATE_COUNT
from .core.clue_canon_store import ClueCanonStore
from .core.llm_client import create_client as create_ai_client
from .core.lm_runtime import LmRuntime
from .core.runtime_logging import (
    add_llm_debug_argument,
    install_process_logging,
    log,
    path_timestamp,
    set_llm_debug_enabled,
)
from .core.supabase_ops import create_service_role_client
from .rust_bridge import _rust_binary_path
from .supervisor.scheduler import (
    DEFAULT_HEARTBEAT_SECONDS,
    DEFAULT_IDLE_SLEEP_SECONDS,
    RunAllSupervisor,
)
from .supervisor.types import RunAllContext

SUPPORTED_TOPICS = ("generate", "redefine", "retitle", "simplify")
LOCK_PATH = Path("/tmp/generator_rebus_run_all.lock")


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
    parser.add_argument("--generate-cap", type=int, default=1)
    parser.add_argument("--redefine-cap", type=int, default=1)
    parser.add_argument("--retitle-cap", type=int, default=1)
    parser.add_argument("--simplify-cap", type=int, default=1)
    parser.add_argument("--idle-sleep-seconds", type=int, default=DEFAULT_IDLE_SLEEP_SECONDS)
    parser.add_argument("--heartbeat-seconds", type=int, default=DEFAULT_HEARTBEAT_SECONDS)
    parser.add_argument("--rewrite-rounds", type=int, default=30)
    parser.add_argument("--rounds", type=int, default=7)
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
            try:
                supervisor.run()
            finally:
                supervisor.close()
        return 0
    finally:
        handle.restore()


if __name__ == "__main__":
    raise SystemExit(main())
