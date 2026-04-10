"""Maintain steady-state canonical clue definitions."""

from __future__ import annotations

import argparse
from pathlib import Path
import re

from rebus_generator.workflows.canonicals.simplify import (
    DEFAULT_BATCH_SIZE as DEFAULT_SIMPLIFY_BATCH_SIZE,
    DEFAULT_IDLE_SLEEP_SECONDS,
    run_simplify_fanout,
)
from rebus_generator.platform.llm.llm_client import create_client
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.io.runtime_logging import (
    add_llm_debug_argument,
    install_process_logging,
    log,
    path_timestamp,
    set_llm_debug_enabled,
)
from .audit import run_audit


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Maintain canonical clue definitions.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit", help="Audit canonical clue library health.")
    audit.add_argument("--output", help="Write audit JSON to this path.")

    simplify = subparsers.add_parser("simplify-fanout", help="Continuously simplify canonical fanout.")
    simplify.add_argument("--dry-run", action="store_true", help="Analyze without DB writes.")
    simplify.add_argument("--apply", action="store_true", help="Persist simplifier merges.")
    simplify.add_argument("--batch-size", type=int, default=DEFAULT_SIMPLIFY_BATCH_SIZE, help="Pairs per batch.")
    simplify.add_argument("--state-path", help="Checkpoint path for resumable simplify state.")
    simplify.add_argument("--report-dir", help="Write simplify reports under this directory.")
    simplify.add_argument("--seed", type=int, help="Random seed for pair sampling.")
    simplify.add_argument(
        "--idle-sleep-seconds",
        type=int,
        default=DEFAULT_IDLE_SLEEP_SECONDS,
        help="Sleep this long before retrying when no eligible pairs exist.",
    )
    simplify.add_argument("--word", help="Only simplify one normalized word.")
    add_llm_debug_argument(simplify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    set_llm_debug_enabled(bool(getattr(args, "debug", False)))
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
    if args.command == "simplify-fanout":
        report_dir = Path(args.report_dir) if args.report_dir else Path("build/clue_canon_simplify") / path_timestamp()
        log_path = report_dir / "run.log"
        audit_path = report_dir / "audit.jsonl"
        handle = install_process_logging(
            run_id=report_dir.name,
            component="clue_canon_simplify",
            log_path=log_path,
            audit_path=audit_path,
            tee_console=True,
        )
        try:
            store = ClueCanonStore()
            client = create_client()
            runtime = LmRuntime(multi_model=True)
            log(f"Run log: {log_path}")
            log(f"Audit log: {audit_path}")
            log(
                "Simplify config: "
                f"mode={'dry-run' if args.dry_run else 'apply'} batch_size={args.batch_size} "
                f"idle_sleep_seconds={args.idle_sleep_seconds} word={args.word or '-'}"
            )
            return run_simplify_fanout(
                store=store,
                client=client,
                runtime=runtime,
                dry_run=args.dry_run,
                apply=args.apply,
                batch_size=args.batch_size,
                state_path=args.state_path,
                report_dir=str(report_dir),
                seed=args.seed,
                idle_sleep_seconds=args.idle_sleep_seconds,
                word=args.word,
            )
        finally:
            handle.restore()
    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
