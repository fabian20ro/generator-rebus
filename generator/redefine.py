"""Redefine existing puzzles in Supabase with improved definitions."""

from __future__ import annotations

import argparse
import copy
import re
import sys

from supabase import create_client as create_supabase_client

from .core.score_helpers import _compact_log_text
from .config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL, VERIFY_CANDIDATE_COUNT
from .core.ai_clues import create_client as create_ai_client
from .core.model_manager import PRIMARY_MODEL, ensure_model_loaded
from .core.pipeline_state import (
    ClueCandidateVersion,
    ClueAssessment,
    WorkingClue,
    WorkingPuzzle,
    all_working_clues,
)
from .core.rewrite_engine import run_rewrite_loop
from .core.runtime_logging import install_process_logging, path_timestamp

REDEFINE_ROUNDS = 7


def fetch_puzzles(
    supabase,
    *,
    date: str | None = None,
    puzzle_id: str | None = None,
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
    return result.data or []


def fetch_clues(supabase, puzzle_id: str) -> list[dict]:
    """Fetch all clues for a puzzle with fields needed for rewriting."""
    result = (
        supabase.table("crossword_clues")
        .select("id, puzzle_id, word_normalized, word_original, definition, direction, start_row, start_col, length")
        .eq("puzzle_id", puzzle_id)
        .execute()
    )
    return result.data or []


def build_working_puzzle(puzzle_row: dict, clue_rows: list[dict]) -> WorkingPuzzle:
    """Convert Supabase rows into a WorkingPuzzle for the rewrite loop."""
    horizontal_clues: list[WorkingClue] = []
    vertical_clues: list[WorkingClue] = []

    for idx, row in enumerate(clue_rows):
        current = ClueCandidateVersion(
            definition=row.get("definition", ""),
            round_index=0,
            source="db_import",
            assessment=ClueAssessment(),
        )
        clue = WorkingClue(
            row_number=idx + 1,
            word_normalized=row.get("word_normalized", ""),
            word_original=row.get("word_original", "") or "",
            start_row=row.get("start_row", 0) or 0,
            start_col=row.get("start_col", 0) or 0,
            current=current,
            best=None,
            history=[current],
        )

        direction = (row.get("direction") or "horizontal").lower()
        if direction in {"v", "vertical"}:
            vertical_clues.append(clue)
        else:
            horizontal_clues.append(clue)

    title = puzzle_row.get("title", "") or ""
    size = puzzle_row.get("grid_size", 0) or 0

    return WorkingPuzzle(
        title=title,
        size=size,
        grid=[],
        horizontal_clues=horizontal_clues,
        vertical_clues=vertical_clues,
    )


def rewrite_puzzle_definitions(
    puzzle: WorkingPuzzle,
    client,
    *,
    rounds: int = REDEFINE_ROUNDS,
    multi_model: bool = True,
    verify_candidates: int = VERIFY_CANDIDATE_COUNT,
) -> dict[str, ClueCandidateVersion]:
    """Run the verify-rate-rewrite loop and return best versions per word.

    Returns a mapping of word_normalized -> best ClueCandidateVersion after
    the rewrite loop completes.
    """
    result = run_rewrite_loop(
        puzzle,
        client,
        rounds=rounds,
        theme=puzzle.title or "Puzzle rebus",
        multi_model=multi_model,
        verify_candidates=verify_candidates,
    )
    return result.improved_versions


def redefine_puzzle(
    supabase,
    puzzle_row: dict,
    client,
    *,
    dry_run: bool = False,
    multi_model: bool = True,
    rounds: int = REDEFINE_ROUNDS,
    verify_candidates: int = VERIFY_CANDIDATE_COUNT,
) -> int:
    """Redefine definitions for one puzzle. Returns count of updated clues."""
    puzzle_id = puzzle_row["id"]
    clue_rows = fetch_clues(supabase, puzzle_id)
    if not clue_rows:
        print(f"  [{puzzle_id}] No clues found, skipping")
        return 0

    puzzle = build_working_puzzle(puzzle_row, clue_rows)
    print(f"  [{puzzle_id}] {len(clue_rows)} clues, title: {puzzle.title}")

    improved = rewrite_puzzle_definitions(
        puzzle,
        client,
        rounds=rounds,
        multi_model=multi_model,
        verify_candidates=verify_candidates,
    )

    if not improved:
        print(f"  [{puzzle_id}] No definitions improved")
        return 0

    # Map word_normalized back to clue DB IDs
    word_to_clue_id = {row["word_normalized"]: row["id"] for row in clue_rows}

    updated_count = 0
    for word, version in improved.items():
        clue_id = word_to_clue_id.get(word)
        if not clue_id:
            continue
        old_def = next(
            (r["definition"] for r in clue_rows if r["word_normalized"] == word), ""
        )
        print(
            f"  [{puzzle_id}] {word}: "
            f"'{_compact_log_text(old_def)}' -> '{_compact_log_text(version.definition)}'"
        )
        if not dry_run:
            supabase.table("crossword_clues").update(
                {"definition": version.definition}
            ).eq("id", clue_id).execute()
        updated_count += 1

    return updated_count


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
    return parser


def main() -> None:
    handle = install_process_logging(
        run_id=f"redefine_{path_timestamp()}",
        component="redefine",
        tee_console=True,
    )
    parser = build_parser()
    try:
        args = parser.parse_args()

        if not args.date and not args.puzzle_id and not args.all:
            parser.error("Specify --date, --puzzle-id, or --all")

        if args.date and not re.match(r"^\d{4}-\d{2}-\d{2}$", args.date):
            parser.error("--date must be in YYYY-MM-DD format")

        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
            sys.exit(1)

        supabase = create_supabase_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        client = create_ai_client()

        puzzles = fetch_puzzles(
            supabase,
            date=args.date,
            puzzle_id=args.puzzle_id,
        )

        if not puzzles:
            print("No puzzles found matching the criteria.")
            return

        print(f"Found {len(puzzles)} puzzle(s) to redefine")
        if args.dry_run:
            print("(dry run — no updates will be made)\n")
        else:
            print()

        if args.multi_model:
            ensure_model_loaded(PRIMARY_MODEL)

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
                )
                total_updated += count
                total_puzzles += 1
            except Exception as exc:
                puzzle_id = puzzle_row.get("id", "?")
                print(f"  [{puzzle_id}] Error: {exc}")
                failed += 1

        print(
            f"\nSummary: {total_puzzles} puzzles processed, "
            f"{total_updated} definitions improved, {failed} failed"
        )
    finally:
        handle.restore()


if __name__ == "__main__":
    main()
