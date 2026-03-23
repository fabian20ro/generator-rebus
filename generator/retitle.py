"""Retitle existing puzzles in Supabase with improved creative titles."""

from __future__ import annotations

import argparse
import re
import sys

from supabase import create_client as create_supabase_client

from .config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from .core.ai_clues import create_client as create_ai_client
from .core.lm_runtime import LmRuntime
from .core.runtime_logging import install_process_logging, path_timestamp
from .phases.theme import (
    FALLBACK_TITLES,
    generate_creative_title,
    rate_title_creativity,
)


def fetch_puzzles(
    supabase,
    *,
    date: str | None = None,
    puzzle_id: str | None = None,
    fallbacks_only: bool = False,
) -> list[dict]:
    """Fetch puzzles from Supabase with optional filters."""
    query = supabase.table("crossword_puzzles").select("*")

    if puzzle_id:
        query = query.eq("id", puzzle_id)
    if date:
        query = query.gte("created_at", f"{date}T00:00:00").lte(
            "created_at", f"{date}T23:59:59"
        )

    result = query.execute()
    rows = result.data or []

    if fallbacks_only:
        fallback_set = set(FALLBACK_TITLES)
        rows = [r for r in rows if r.get("title") in fallback_set]

    return rows


def fetch_clues(supabase, puzzle_id: str) -> list[dict]:
    """Fetch all clues for a puzzle."""
    result = (
        supabase.table("crossword_clues")
        .select("word_normalized, definition")
        .eq("puzzle_id", puzzle_id)
        .execute()
    )
    return result.data or []


def retitle_puzzle(
    supabase,
    puzzle_row: dict,
    ai_client,
    rate_client,
    *,
    dry_run: bool = False,
    multi_model: bool = True,
    runtime: LmRuntime | None = None,
) -> bool:
    """Generate a new title for a puzzle. Returns True if title changed."""
    puzzle_id = puzzle_row["id"]
    old_title = puzzle_row.get("title", "")

    clues = fetch_clues(supabase, puzzle_id)
    if not clues:
        print(f"  [{puzzle_id}] No clues found, skipping")
        return False

    words = [c["word_normalized"] for c in clues if c.get("word_normalized")]
    definitions = [c["definition"] for c in clues if c.get("definition")]

    if not words or not definitions:
        print(f"  [{puzzle_id}] Missing words or definitions, skipping")
        return False

    new_title = generate_creative_title(
        words,
        definitions,
        client=ai_client,
        rate_client=rate_client,
        runtime=runtime,
        multi_model=multi_model,
    )

    if new_title == old_title:
        print(f'  [{puzzle_id}] "{old_title}" -> unchanged')
        return False

    is_fallback = old_title in FALLBACK_TITLES
    runtime = runtime or LmRuntime(multi_model=multi_model)

    if not is_fallback:
        score_model = runtime.activate_secondary() if multi_model else runtime.activate_primary()
        old_score, _ = rate_title_creativity(old_title, words, rate_client, model_config=score_model)
        new_score, _ = rate_title_creativity(new_title, words, rate_client, model_config=score_model)
        if new_score <= old_score:
            print(
                f'  [{puzzle_id}] "{old_title}" (score={old_score}) '
                f'-> "{new_title}" (score={new_score}) — skipped, not better'
            )
            return False
        print(
            f'  [{puzzle_id}] "{old_title}" (score={old_score}) '
            f'-> "{new_title}" (score={new_score})'
        )
    else:
        print(f'  [{puzzle_id}] "{old_title}" (fallback) -> "{new_title}"')

    if not dry_run:
        supabase.table("crossword_puzzles").update({"title": new_title}).eq(
            "id", puzzle_id
        ).execute()

    return True


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
        "--multi-model",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use two-model cross-validation (default: True)",
    )
    return parser


def main() -> None:
    handle = install_process_logging(
        run_id=f"retitle_{path_timestamp()}",
        component="retitle",
        tee_console=True,
    )
    parser = build_parser()
    try:
        args = parser.parse_args()

        if not args.date and not args.puzzle_id and not args.all_fallbacks and not args.all:
            parser.error("Specify --date, --puzzle-id, --all-fallbacks, or --all")

        if args.date and not re.match(r"^\d{4}-\d{2}-\d{2}$", args.date):
            parser.error("--date must be in YYYY-MM-DD format")

        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
            sys.exit(1)

        multi_model = args.multi_model

        supabase = create_supabase_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        ai_client = create_ai_client()
        rate_client = create_ai_client()

        puzzles = fetch_puzzles(
            supabase,
            date=args.date,
            puzzle_id=args.puzzle_id,
            fallbacks_only=args.all_fallbacks,
        )

        if not puzzles:
            print("No puzzles found matching the criteria.")
            return

        print(f"Found {len(puzzles)} puzzle(s) to retitle")
        if args.dry_run:
            print("(dry run — no updates will be made)\n")
        else:
            print()

        runtime = LmRuntime(multi_model=multi_model)
        runtime.activate_primary()

        updated = 0
        unchanged = 0
        failed = 0

        for puzzle_row in puzzles:
            try:
                changed = retitle_puzzle(
                    supabase,
                    puzzle_row,
                    ai_client,
                    rate_client,
                    dry_run=args.dry_run,
                    multi_model=multi_model,
                    runtime=runtime,
                )
                if changed:
                    updated += 1
                else:
                    unchanged += 1
            except Exception as exc:
                puzzle_id = puzzle_row.get("id", "?")
                print(f"  [{puzzle_id}] Error: {exc}")
                failed += 1

        print(f"\nSummary: {updated} updated, {unchanged} unchanged, {failed} failed")
    finally:
        handle.restore()


if __name__ == "__main__":
    main()
