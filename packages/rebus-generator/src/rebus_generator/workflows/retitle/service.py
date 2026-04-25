"""Retitle existing puzzles in Supabase with improved creative titles."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from rebus_generator.platform.persistence.supabase_ops import create_rebus_client as create_supabase_client

from rebus_generator.platform.config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from rebus_generator.platform.llm.llm_client import create_client as create_ai_client
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.io.runtime_logging import (
    add_llm_debug_argument,
    install_process_logging,
    log,
    path_timestamp,
    set_llm_debug_enabled,
)
from rebus_generator.domain.guards.title_guards import normalize_title_key
from .batch import _RetitleBatchState, generate_title_results_batch
from .load import (
    fetch_clues,
    fetch_puzzles,
    select_duplicate_puzzles_for_retitle,
    select_puzzles_for_retitle,
    stored_title_score as _stored_title_score,
)
from .persist import apply_title_update, prepare_title_update
from .runtime import RETITLE_BATCH_SIZE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Retitle existing puzzles in Supabase",
    )
    parser.add_argument("--date", help="Filter puzzles by creation date (YYYY-MM-DD)")
    parser.add_argument("--puzzle-id", help="Retitle a specific puzzle by UUID")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Retitle all puzzles in the database",
    )
    parser.add_argument(
        "--duplicates-only",
        action="store_true",
        help="Limit retitle to puzzles whose normalized title appears multiple times",
    )
    parser.add_argument(
        "--all-fallbacks",
        action="store_true",
        help="Retitle all puzzles with fallback titles",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print before/after without updating Supabase",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=RETITLE_BATCH_SIZE,
        help=f"How many puzzles to process together per title-generation batch (default: {RETITLE_BATCH_SIZE})",
    )
    parser.add_argument(
        "--multi-model",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use two-model cross-validation (default: True)",
    )
    add_llm_debug_argument(parser)
    return parser


def main() -> None:
    run_dir = Path("build/retitle_runs") / path_timestamp()
    log_path = run_dir / "run.log"
    audit_path = run_dir / "audit.jsonl"
    handle = install_process_logging(
        run_id=run_dir.name,
        component="retitle",
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

        if not args.date and not args.puzzle_id and not args.all_fallbacks and not args.all and not args.duplicates_only:
            parser.error("Specify --date, --puzzle-id, --all-fallbacks, --all, or --duplicates-only")

        if args.date and not re.match(r"^\d{4}-\d{2}-\d{2}$", args.date):
            parser.error("--date must be in YYYY-MM-DD format")
        if args.batch_size <= 0:
            parser.error("--batch-size must be >= 1")

        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            log("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
            sys.exit(1)

        multi_model = args.multi_model

        supabase = create_supabase_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        ai_client = create_ai_client()
        rate_client = create_ai_client()

        all_puzzles = fetch_puzzles(supabase)
        filtered_rows = fetch_puzzles(
            supabase,
            date=args.date,
            puzzle_id=args.puzzle_id,
            fallbacks_only=args.all_fallbacks,
        )
        puzzles = (
            filtered_rows
            if args.puzzle_id
            else (
                select_duplicate_puzzles_for_retitle(filtered_rows, global_rows=all_puzzles)
                if args.duplicates_only
                else select_puzzles_for_retitle(filtered_rows)
            )
        )

        if not puzzles:
            log("No puzzles found matching the criteria.")
            return

        log(f"Found {len(puzzles)} puzzle(s) to retitle")
        missing_score_count = sum(1 for row in puzzles if _stored_title_score(row) is None)
        if missing_score_count:
            log(f"Prioritized {missing_score_count} puzzle(s) without title_score")
        if args.dry_run:
            log("(dry run — no updates will be made)\n")
        else:
            log("")

        runtime = LmRuntime(multi_model=multi_model)

        updated = 0
        unchanged = 0
        failed = 0
        active_title_keys = {
            row["id"]: normalize_title_key(row.get("title", "") or "")
            for row in all_puzzles
            if row.get("id")
        }

        for start in range(0, len(puzzles), args.batch_size):
            batch_rows = puzzles[start : start + args.batch_size]
            batch_states: list[_RetitleBatchState] = []
            batch_words: dict[str, list[str]] = {}
            skipped_ids: set[str] = set()

            for puzzle_row in batch_rows:
                puzzle_id = puzzle_row.get("id", "?")
                try:
                    clues = fetch_clues(supabase, puzzle_id)
                    if not clues:
                        log(f"  [{puzzle_id}] No clues found, skipping")
                        unchanged += 1
                        skipped_ids.add(puzzle_id)
                        continue

                    words = [c["word_normalized"] for c in clues if c.get("word_normalized")]
                    definitions = [c["definition"] for c in clues if c.get("definition")]
                    if not words or not definitions:
                        log(f"  [{puzzle_id}] Missing words or definitions, skipping")
                        unchanged += 1
                        skipped_ids.add(puzzle_id)
                        continue

                    forbidden_title_keys = {
                        key
                        for other_puzzle_id, key in active_title_keys.items()
                        if other_puzzle_id != puzzle_row.get("id") and key
                    }
                    batch_words[puzzle_id] = words
                    batch_states.append(
                        _RetitleBatchState(
                            puzzle_row=puzzle_row,
                            words=words,
                            definitions=definitions,
                            forbidden_title_keys=forbidden_title_keys,
                        )
                    )
                except Exception as exc:
                    log(f"  [{puzzle_id}] Error: {exc}")
                    failed += 1
                    skipped_ids.add(puzzle_id)

            batch_results = generate_title_results_batch(
                batch_states,
                ai_client,
                rate_client,
                runtime=runtime,
                multi_model=multi_model,
            )

            for puzzle_row in batch_rows:
                puzzle_id = puzzle_row.get("id", "?")
                if puzzle_id in skipped_ids:
                    continue
                try:
                    forbidden_title_keys = {
                        key
                        for other_puzzle_id, key in active_title_keys.items()
                        if other_puzzle_id != puzzle_row.get("id") and key
                    }
                    prepared = prepare_title_update(
                        puzzle_row,
                        batch_results[puzzle_id],
                        rate_client,
                        multi_model=multi_model,
                        runtime=runtime,
                        forbidden_title_keys=forbidden_title_keys,
                        words=batch_words[puzzle_id],
                    )
                    changed = apply_title_update(
                        supabase,
                        puzzle_row,
                        prepared,
                        dry_run=args.dry_run,
                    )
                    if changed:
                        updated += 1
                        active_title_keys[puzzle_row["id"]] = normalize_title_key(
                            puzzle_row.get("title", "") or ""
                        )
                    else:
                        unchanged += 1
                except Exception as exc:
                    log(f"  [{puzzle_id}] Error: {exc}")
                    failed += 1

        log(f"\nSummary: {updated} updated, {unchanged} unchanged, {failed} failed")
    finally:
        handle.restore()


if __name__ == "__main__":
    main()
