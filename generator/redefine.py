"""Redefine existing puzzles in Supabase with improved definitions."""

from __future__ import annotations

import argparse
import copy
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from supabase import create_client as create_supabase_client

from .core.markdown_io import ClueEntry
from .core.puzzle_metrics import (
    build_puzzle_description,
    evaluate_puzzle_state,
    puzzle_metadata_payload,
    score_puzzle_state,
)
from .config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL, VERIFY_CANDIDATE_COUNT
from .core.llm_client import create_client as create_ai_client
from .core.clue_canon import ClueCanonService
from .core.clue_canon_store import ClueCanonStore
from .core.clue_logging import clue_label_from_row, log_canonical_event, log_definition_event
from .core.lm_runtime import LmRuntime
from .core.model_manager import get_active_model_labels
from .core.pipeline_state import (
    WorkingClue,
    WorkingPuzzle,
    all_working_clues,
    puzzle_from_working_state,
    working_clue_from_entry,
)
from .core.rewrite_engine import run_rewrite_loop
from .core.runtime_logging import (
    add_llm_debug_argument,
    install_process_logging,
    log,
    path_timestamp,
    set_llm_debug_enabled,
)
from .core.supabase_ops import execute_logged_update

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
    rows = result.data or []
    return sorted(rows, key=_puzzle_sort_key)


def _puzzle_sort_key(row: dict) -> tuple[object, ...]:
    created_at = str(row.get("created_at") or "")
    repaired_at = str(row.get("repaired_at") or "")
    return (
        0 if row.get("repaired_at") is None else 1,
        0 if _needs_metadata_backfill(row) else 1,
        created_at if row.get("repaired_at") is None else repaired_at,
        row.get("created_at") is None,
        created_at,
        str(row.get("id") or ""),
    )


def fetch_clues(supabase, puzzle_id: str) -> list[dict]:
    """Fetch all clues for a puzzle with fields needed for rewriting."""
    return ClueCanonStore(client=supabase).fetch_clue_rows(puzzle_id=puzzle_id)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _models_used(multi_model: bool) -> list[str]:
    return get_active_model_labels(multi_model=multi_model)


def _needs_metadata_backfill(puzzle_row: dict) -> bool:
    required = (
        "description",
        "rebus_score_min",
        "rebus_score_avg",
        "definition_score",
        "verified_count",
        "total_clues",
        "pass_rate",
    )
    for field in required:
        value = puzzle_row.get(field)
        if value is None:
            return True
        if field == "description" and not str(value).strip():
            return True
    return False


def _direction_code(direction: str | None) -> str:
    return "V" if (direction or "").strip().lower() in {"v", "vertical"} else "H"


def _clue_key(direction: str | None, start_row: int | None, start_col: int | None) -> tuple[str, int, int]:
    return (_direction_code(direction), int(start_row or 0), int(start_col or 0))


def _clue_row_sort_key(row: dict) -> tuple[object, ...]:
    direction = _direction_code(row.get("direction"))
    return (
        0 if direction == "H" else 1,
        int(row.get("clue_number") or 0),
        int(row.get("start_row") or 0),
        int(row.get("start_col") or 0),
        row.get("id") or "",
    )


def _build_metadata_payload(assessment, *, multi_model: bool) -> dict[str, object]:
    description = build_puzzle_description(assessment, _models_used(multi_model))
    payload = puzzle_metadata_payload(assessment, description=description)
    timestamp = _now_iso()
    payload["updated_at"] = timestamp
    payload["repaired_at"] = timestamp
    return payload


def _persist_puzzle_metadata(
    supabase,
    puzzle_id: str,
    payload: dict[str, object],
    *,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    execute_logged_update(
        supabase,
        "crossword_puzzles",
        payload,
        eq_filters={"id": puzzle_id},
    )


def _apply_clue_version(target: WorkingClue, source: WorkingClue) -> None:
    final_version = copy.deepcopy(source.active_version())
    target.current = copy.deepcopy(final_version)
    target.best = copy.deepcopy(final_version)
    target.locked = source.locked


def _clue_update_payload(store: ClueCanonStore, row: dict, desired: dict[str, object]) -> dict[str, object]:
    return store.build_clue_definition_payload(
        canonical_definition_id=desired.get("canonical_definition_id"),
        verify_note=str(desired["verify_note"]),
        verified=bool(desired["verified"]),
    )


def _desired_clue_payloads(puzzle: WorkingPuzzle) -> dict[tuple[str, int, int], dict[str, object]]:
    rendered = puzzle_from_working_state(puzzle)
    payloads: dict[tuple[str, int, int], dict[str, object]] = {}
    for direction, clues in (("H", rendered.horizontal_clues), ("V", rendered.vertical_clues)):
        for clue in clues:
            payloads[_clue_key(direction, clue.start_row, clue.start_col)] = {
                "definition": clue.definition,
                "verify_note": clue.verify_note or "",
                "verified": bool(clue.verified),
            }
    return payloads


def _working_clue_map(puzzle: WorkingPuzzle) -> dict[tuple[str, int, int], WorkingClue]:
    mapping: dict[tuple[str, int, int], WorkingClue] = {}
    for direction, clues in (("H", puzzle.horizontal_clues), ("V", puzzle.vertical_clues)):
        for clue in clues:
            mapping[_clue_key(direction, clue.start_row, clue.start_col)] = clue
    return mapping


def build_working_puzzle(puzzle_row: dict, clue_rows: list[dict]) -> WorkingPuzzle:
    """Convert Supabase rows into a WorkingPuzzle for the rewrite loop."""
    horizontal_clues: list[WorkingClue] = []
    vertical_clues: list[WorkingClue] = []

    for idx, row in enumerate(sorted(clue_rows, key=_clue_row_sort_key)):
        clue = working_clue_from_entry(
            ClueEntry(
                row_number=int(row.get("clue_number") or idx + 1),
                word_normalized=row.get("word_normalized", ""),
                word_original=row.get("word_original", "") or "",
                definition=row.get("definition", "") or "",
                verified=row.get("verified"),
                verify_note=row.get("verify_note", "") or "",
                start_row=int(row.get("start_row", 0) or 0),
                start_col=int(row.get("start_col", 0) or 0),
            )
        )
        clue.current.source = "db_import"
        if clue.history:
            clue.history[0].source = "db_import"
        clue.word_type = str(row.get("word_type") or "")

        if _direction_code(row.get("direction")) == "V":
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
    runtime: LmRuntime | None = None,
) -> object:
    """Run the verify-rate-rewrite loop."""
    return run_rewrite_loop(
        puzzle,
        client,
        rounds=rounds,
        theme=puzzle.title or "Puzzle rebus",
        multi_model=multi_model,
        verify_candidates=verify_candidates,
        hybrid_deanchor=True,
        runtime=runtime,
    )


def redefine_puzzle(
    supabase,
    puzzle_row: dict,
    client,
    *,
    dry_run: bool = False,
    multi_model: bool = True,
    rounds: int = REDEFINE_ROUNDS,
    verify_candidates: int = VERIFY_CANDIDATE_COUNT,
    runtime: LmRuntime | None = None,
) -> int:
    """Redefine definitions for one puzzle. Returns count of updated clues."""
    puzzle_id = puzzle_row["id"]
    clue_rows = sorted(fetch_clues(supabase, puzzle_id), key=_clue_row_sort_key)
    if not clue_rows:
        log(f"  [{puzzle_id}] No clues found, skipping")
        return 0

    baseline_puzzle = build_working_puzzle(puzzle_row, clue_rows)
    log(f"  [{puzzle_id}] {len(clue_rows)} clues, title: {baseline_puzzle.title}")
    runtime = runtime or LmRuntime(multi_model=multi_model)
    baseline_eval = evaluate_puzzle_state(
        baseline_puzzle,
        client,
        multi_model=multi_model,
        verify_candidates=verify_candidates,
        runtime=runtime,
    )
    baseline_puzzle.assessment = baseline_eval.assessment
    log(
        f"  [{puzzle_id}] baseline min={baseline_eval.assessment.min_rebus}/10 "
        f"avg={baseline_eval.assessment.avg_rebus:.1f}/10 "
        f"verified={baseline_eval.assessment.verified_count}/{baseline_eval.assessment.total_clues}"
    )

    candidate_puzzle = build_working_puzzle(puzzle_row, clue_rows)
    rewrite_puzzle_definitions(
        candidate_puzzle,
        client,
        rounds=rounds,
        multi_model=multi_model,
        verify_candidates=verify_candidates,
        runtime=runtime,
    )
    candidate_puzzle.assessment = score_puzzle_state(candidate_puzzle)
    log(
        f"  [{puzzle_id}] candidate min={candidate_puzzle.assessment.min_rebus}/10 "
        f"avg={candidate_puzzle.assessment.avg_rebus:.1f}/10 "
        f"verified={candidate_puzzle.assessment.verified_count}/{candidate_puzzle.assessment.total_clues}"
    )

    desired_payloads = _desired_clue_payloads(candidate_puzzle)
    candidate_clues = _working_clue_map(candidate_puzzle)
    persistence_puzzle = copy.deepcopy(baseline_puzzle)
    persistence_clues = _working_clue_map(persistence_puzzle)
    clue_canon = ClueCanonService(
        store=ClueCanonStore(client=supabase),
        client=client,
        runtime=runtime,
    )
    clue_store = clue_canon.store

    updated_count = 0
    for row in clue_rows:
        key = _clue_key(row.get("direction"), row.get("start_row"), row.get("start_col"))
        desired = desired_payloads.get(key)
        target_clue = persistence_clues.get(key)
        source_clue = candidate_clues.get(key)
        if not desired or not target_clue or not source_clue:
            continue
        final_version = source_clue.active_version()
        decision = clue_canon.resolve_definition(
            word_normalized=source_clue.word_normalized,
            word_original=source_clue.word_original,
            definition=str(desired["definition"]),
            word_type=source_clue.word_type,
            verified=bool(desired.get("verified")),
            semantic_score=final_version.assessment.scores.semantic_exactness,
            rebus_score=final_version.assessment.scores.rebus_score,
            creativity_score=final_version.assessment.scores.creativity,
        )
        if not decision.canonical_definition_id:
            raise RuntimeError(
                f"Canonical clue resolution produced no canonical_definition_id for {source_clue.word_normalized}"
            )
        desired = dict(desired)
        desired["definition"] = decision.canonical_definition
        desired["canonical_definition_id"] = decision.canonical_definition_id
        clue_ref = clue_label_from_row(row)
        log_canonical_event(
            decision.action,
            puzzle_id=puzzle_id,
            clue_ref=clue_ref,
            candidate_definition=str(desired_payloads.get(key, {}).get("definition") or ""),
            canonical_definition=decision.canonical_definition,
            detail=decision.decision_note or None,
        )
        update_payload = _clue_update_payload(clue_store, row, desired)
        current = {
            "definition": row.get("definition", "") or "",
            "verify_note": row.get("verify_note", "") or "",
            "verified": bool(row.get("verified")),
            "canonical_definition_id": row.get("canonical_definition_id"),
        }
        comparable_current = {field: current[field] for field in update_payload}
        if comparable_current == update_payload:
            continue

        word = row.get("word_normalized", "")
        log_definition_event(
            "redefine",
            puzzle_id=puzzle_id,
            clue_ref=clue_ref,
            before=current["definition"],
            after=desired["definition"],
            detail=f"verified={bool(desired.get('verified'))}",
        )
        if not dry_run:
            execute_logged_update(
                supabase,
                "crossword_clues",
                update_payload,
                eq_filters={"id": row["id"], "puzzle_id": puzzle_id},
            )
        row.update(update_payload)
        row["definition"] = desired["definition"]
        _apply_clue_version(target_clue, source_clue)
        persistence_puzzle.assessment = score_puzzle_state(persistence_puzzle)
        metadata_payload = _build_metadata_payload(
            persistence_puzzle.assessment,
            multi_model=multi_model,
        )
        _persist_puzzle_metadata(
            supabase,
            puzzle_id,
            metadata_payload,
            dry_run=dry_run,
        )
        updated_count += 1

    if updated_count == 0:
        if _needs_metadata_backfill(puzzle_row):
            log(f"  [{puzzle_id}] backfill metadata")
            _persist_puzzle_metadata(
                supabase,
                puzzle_id,
                _build_metadata_payload(
                    baseline_puzzle.assessment,
                    multi_model=multi_model,
                ),
                dry_run=dry_run,
            )
        else:
            log(f"  [{puzzle_id}] No clue or metadata changes")

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
    add_llm_debug_argument(parser)
    return parser


def main() -> None:
    run_dir = Path("generator/output/redefine_runs") / path_timestamp()
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
