"""Redefine existing puzzles in Supabase with improved definitions."""

from __future__ import annotations

import argparse
import copy
import re
import sys

from supabase import create_client as create_supabase_client

from .core.score_helpers import (
    LOCKED_REBUS,
    LOCKED_SEMANTIC,
    MAX_CONSECUTIVE_FAILURES,
    PLATEAU_LOOKBACK,
    _compact_log_text,
    _extract_rebus_score,
    _extract_semantic_score,
    _is_locked_clue,
    _needs_rewrite,
    _restore_best_versions,
    _synthesize_failure_reason,
    _update_best_clue_version,
)
from .config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from .core.ai_clues import (
    RATE_MIN_REBUS,
    RATE_MIN_SEMANTIC,
    create_client as create_ai_client,
    generate_definition,
    rewrite_definition,
)
from .core.model_manager import (
    PRIMARY_MODEL,
    SECONDARY_MODEL,
    ensure_model_loaded,
    switch_model,
)
from .core.pipeline_state import (
    ClueCandidateVersion,
    ClueAssessment,
    WorkingClue,
    WorkingPuzzle,
    all_working_clues,
    set_current_definition,
)
from .core.plateau import has_plateaued
from .core.dex_cache import DexProvider
from .phases.verify import rate_working_puzzle, verify_working_puzzle

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
        if direction == "vertical":
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
) -> dict[str, ClueCandidateVersion]:
    """Run the verify-rate-rewrite loop and return best versions per word.

    Returns a mapping of word_normalized -> best ClueCandidateVersion after
    the rewrite loop completes.
    """
    theme = puzzle.title or "Puzzle rebus"

    # Load dex definitions for all words in puzzle
    dex = DexProvider.for_puzzle(puzzle)

    if multi_model:
        ensure_model_loaded(PRIMARY_MODEL)
        current_model = PRIMARY_MODEL
        try:
            switch_model(PRIMARY_MODEL, SECONDARY_MODEL)
            current_model = SECONDARY_MODEL
        except Exception as e:
            print(f"  Model switch failed: {e} — continuing with {current_model.display_name}")
        print(f"  Model activ (evaluare inițială): {current_model.display_name}")
    else:
        current_model = PRIMARY_MODEL

    preset_skip: set[str] = set()

    verify_working_puzzle(puzzle, client, skip_words=preset_skip)
    rate_working_puzzle(puzzle, client, skip_words=preset_skip, dex=dex)
    for clue in all_working_clues(puzzle):
        _update_best_clue_version(clue, client=client)

    # Save round-0 scores for later comparison
    initial_scores: dict[str, tuple[int, int]] = {}
    for clue in all_working_clues(puzzle):
        sem = _extract_semantic_score(clue) or 0
        reb = _extract_rebus_score(clue) or 0
        initial_scores[clue.word_normalized] = (sem, reb)

    consecutive_failures: dict[str, int] = {}
    stuck_words: set[str] = set()
    min_rebus_history: list[int] = []

    for round_index in range(1, rounds + 1):
        current_scores = [
            _extract_rebus_score(c) or 0
            for c in all_working_clues(puzzle)
        ]
        current_min = min(current_scores) if current_scores else 0
        min_rebus_history.append(current_min)

        if has_plateaued(min_rebus_history, PLATEAU_LOOKBACK):
            print(f"  Plateau after {round_index} rounds (min_rebus={current_min})")
            break

        round_min_rebus = current_min + 1
        candidates = [
            clue for clue in all_working_clues(puzzle)
            if _needs_rewrite(clue, min_rebus=round_min_rebus)
            and clue.word_normalized not in stuck_words
        ]

        if not candidates:
            break

        if multi_model:
            print(f"  Model activ (rescriere): {current_model.display_name}")

        print(f"  Rewrite round {round_index}: {len(candidates)} candidates")

        changed_words: set[str] = set()
        for clue in candidates:
            if _is_locked_clue(clue):
                continue
            wrong_guess = clue.current.assessment.wrong_guess
            rating_feedback = clue.current.assessment.feedback
            bad_example_definition = clue.current.definition if round_index >= 2 else ""
            bad_example_reason = _synthesize_failure_reason(clue) if round_index >= 2 else ""
            dex_defs = (dex.get(clue.word_normalized, clue.word_original) or "")
            try:
                if clue.current.definition.startswith("["):
                    new_definition = generate_definition(
                        client, clue.word_normalized, clue.word_original, theme, retries=3,
                        word_type=clue.word_type, dex_definitions=dex_defs,
                    )
                else:
                    new_definition = rewrite_definition(
                        client,
                        clue.word_normalized,
                        clue.word_original,
                        theme,
                        clue.current.definition,
                        wrong_guess,
                        rating_feedback=rating_feedback,
                        bad_example_definition=bad_example_definition,
                        bad_example_reason=bad_example_reason,
                        word_type=clue.word_type,
                        dex_definitions=dex_defs,
                    )
            except Exception as e:
                print(f"  Rewrite failed for {clue.word_normalized}: {e}")
                continue

            if new_definition and new_definition != clue.current.definition:
                changed_words.add(clue.word_normalized)
                consecutive_failures[clue.word_normalized] = 0
                print(
                    f"  {clue.word_normalized}: "
                    f"'{_compact_log_text(clue.current.definition)}' -> "
                    f"'{_compact_log_text(new_definition)}'"
                )
                set_current_definition(clue, new_definition, round_index=round_index, source="rewrite")
            else:
                consecutive_failures[clue.word_normalized] = consecutive_failures.get(clue.word_normalized, 0) + 1
                if consecutive_failures[clue.word_normalized] >= MAX_CONSECUTIVE_FAILURES:
                    stuck_words.add(clue.word_normalized)
                    print(f"  {clue.word_normalized}: stuck after {consecutive_failures[clue.word_normalized]} failures")

        skip_words = ({c.word_normalized for c in all_working_clues(puzzle)} - changed_words) | preset_skip
        if multi_model:
            next_model = SECONDARY_MODEL if current_model == PRIMARY_MODEL else PRIMARY_MODEL
            try:
                switch_model(current_model, next_model)
                current_model = next_model
            except Exception as e:
                print(f"  Model switch failed: {e} — continuing with {current_model.display_name}")
            print(f"  Model activ (evaluare): {current_model.display_name}")
        verify_working_puzzle(puzzle, client, skip_words=skip_words)
        rate_working_puzzle(puzzle, client, skip_words=skip_words, dex=dex)
        for clue in all_working_clues(puzzle):
            if clue.word_normalized not in changed_words:
                continue
            _update_best_clue_version(clue, client=client)
            if clue.locked:
                print(f"  {clue.word_normalized}: locked at {LOCKED_SEMANTIC}/{LOCKED_REBUS}")

    _restore_best_versions(puzzle)

    # Build result: only include clues where definition actually improved
    best_versions: dict[str, ClueCandidateVersion] = {}
    for clue in all_working_clues(puzzle):
        old_sem, old_reb = initial_scores.get(clue.word_normalized, (0, 0))
        new_sem = _extract_semantic_score(clue) or 0
        new_reb = _extract_rebus_score(clue) or 0
        if new_reb > old_reb or new_sem > old_sem:
            best_versions[clue.word_normalized] = copy.deepcopy(clue.active_version())

    return best_versions


def redefine_puzzle(
    supabase,
    puzzle_row: dict,
    client,
    *,
    dry_run: bool = False,
    multi_model: bool = True,
    rounds: int = REDEFINE_ROUNDS,
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
        puzzle, client, rounds=rounds, multi_model=multi_model,
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
    return parser


def main() -> None:
    parser = build_parser()
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


if __name__ == "__main__":
    main()
