"""Redefine existing puzzles in Supabase with improved definitions."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from rebus_generator.platform.persistence.supabase_ops import create_rebus_client as create_supabase_client

from rebus_generator.domain.puzzle_metrics import evaluate_puzzle_state, score_puzzle_state
from rebus_generator.platform.config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL, VERIFY_CANDIDATE_COUNT
from rebus_generator.platform.llm.llm_client import create_client as create_ai_client
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.io.runtime_logging import (
    add_llm_debug_argument,
    install_process_logging,
    log,
    path_timestamp,
    set_llm_debug_enabled,
)
from .load import fetch_puzzles
from .runtime import REDEFINE_ROUNDS, redefine_puzzle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Redefine existing puzzle definitions in Supabase",
    )
    parser.add_argument("--date", help="Filter puzzles by creation date (YYYY-MM-DD)")
    parser.add_argument("--puzzle-id", help="Redefine a specific puzzle by UUID")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Redefine all puzzles in the database",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print changes without updating Supabase",
    )
    parser.add_argument(
        "--multi-model",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use two-model cross-validation (default: True)",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=REDEFINE_ROUNDS,
        help=f"Number of rewrite rounds (default: {REDEFINE_ROUNDS})",
    )
    parser.add_argument(
        "--verify-candidates",
        type=int,
        default=VERIFY_CANDIDATE_COUNT,
        help=f"How many verifier candidates to request per clue (default: {VERIFY_CANDIDATE_COUNT})",
    )
    add_llm_debug_argument(parser)
    return parser


def main() -> None:
    run_dir = Path("build/redefine_runs") / path_timestamp()
    log_path = run_dir / "run.log"
    audit_path = run_dir / "audit.jsonl"
    handle = install_process_logging(
        run_id=run_dir.name,
        component="redefine",
        log_path=log_path,
        audit_path=audit_path,
        tee_console=True,
    )
    parser = build_parser()
    try:
        args = parser.parse_args()
        set_llm_debug_enabled(args.debug)
        log(f"Run log: {log_path}")
        log(f"Audit log: {audit_path}")

        if not args.date and not args.puzzle_id and not args.all:
            parser.error("Specify --date, --puzzle-id, or --all")

        if args.date and not re.match(r"^\d{4}-\d{2}-\d{2}$", args.date):
            parser.error("--date must be in YYYY-MM-DD format")

        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            log("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
            sys.exit(1)

        supabase = create_supabase_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        client = create_ai_client()

        puzzles = fetch_puzzles(
            supabase,
            date=args.date,
            puzzle_id=args.puzzle_id,
        )

        if not puzzles:
            log("No puzzles found matching the criteria.")
            return

        log(f"Found {len(puzzles)} puzzle(s) to redefine")
        if args.dry_run:
            log("(dry run — no updates will be made)\n")
        else:
            log("")

        runtime = LmRuntime(multi_model=args.multi_model)

        total_updated = 0
        total_puzzles = 0
        failed = 0

        for puzzle_row in puzzles:
            try:
                count = redefine_puzzle(
                    supabase,
                    puzzle_row,
                    client,
                    dry_run=args.dry_run,
                    multi_model=args.multi_model,
                    rounds=args.rounds,
                    verify_candidates=max(1, args.verify_candidates),
                    runtime=runtime,
                )
                total_updated += count
                total_puzzles += 1
            except Exception as exc:
                puzzle_id = puzzle_row.get("id", "?")
                log(f"  [{puzzle_id}] Error: {exc}")
                failed += 1

        log(
            f"\nSummary: {total_puzzles} puzzles processed, "
            f"{total_updated} definitions improved, {failed} failed"
        )
    finally:
        handle.restore()


if __name__ == "__main__":
    main()
