"""Repair published puzzles by improving low-scoring or unscored entries."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from supabase import create_client as create_supabase_client

from rebus_generator.platform.config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL, VERIFY_CANDIDATE_COUNT
from rebus_generator.platform.llm.llm_client import create_client as create_ai_client
from rebus_generator.workflows.canonicals.domain_service import ClueCanonService
from rebus_generator.platform.persistence.clue_canon_store import ClueCanonStore
from rebus_generator.platform.io.clue_logging import clue_label, clue_label_from_row, log_canonical_event, log_definition_event
from rebus_generator.domain.clue_rating import extract_creativity_score, extract_rebus_score, extract_semantic_score
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.llm.models import get_active_model_labels
from rebus_generator.domain.pipeline_state import all_working_clues, render_verify_note
from rebus_generator.platform.llm.prompt_runtime import preload_runtime_prompts, prompt_runtime_audit
from rebus_generator.domain.puzzle_metrics import (
    build_puzzle_description,
    evaluate_puzzle_state,
    puzzle_metadata_payload,
    score_puzzle_state,
)
from rebus_generator.workflows.redefine.rewrite_engine import run_rewrite_loop
from rebus_generator.platform.io.runtime_logging import (
    add_llm_debug_argument,
    install_process_logging,
    log,
    path_timestamp,
    set_llm_debug_enabled,
)
from rebus_generator.platform.persistence.supabase_ops import execute_logged_update
from rebus_generator.workflows.retitle.generate import generate_creative_title_result
from rebus_generator.workflows.retitle.sanitize import TitleGenerationResult
from rebus_generator.workflows.redefine.load import build_working_puzzle

REPAIR_ROUNDS = 7
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _models_used(multi_model: bool) -> list[str]:
    return get_active_model_labels(multi_model=multi_model)


def fetch_puzzles(
    supabase,
    *,
    puzzle_id: str | None = None,
) -> list[dict]:
    query = supabase.table("crossword_puzzles").select("*").eq("published", True)
    if puzzle_id:
        query = query.eq("id", puzzle_id)
    result = query.execute()
    return result.data or []


def fetch_clues(supabase, puzzle_id: str) -> list[dict]:
    return ClueCanonStore(client=supabase).fetch_clue_rows(puzzle_id=puzzle_id)


def _priority_key(row: dict) -> tuple[object, ...]:
    score = row.get("rebus_score_min")
    created_at = row.get("created_at") or ""
    repaired_at = row.get("repaired_at") or created_at
    if score is None:
        return (0, created_at, row.get("id") or "")
    return (1, int(score), repaired_at, row.get("id") or "")


def select_puzzles_for_repair(rows: list[dict], *, limit: int) -> list[dict]:
    ordered = sorted(rows, key=_priority_key)
    return ordered[:limit]


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


def _collect_title_inputs(puzzle) -> tuple[list[str], list[str]]:
    words: list[str] = []
    definitions: list[str] = []
    for clue in all_working_clues(puzzle):
        if clue.word_normalized:
            words.append(clue.word_normalized)
        if clue.current.definition and not clue.current.definition.startswith("["):
            definitions.append(clue.current.definition)
    unique_words = list(dict.fromkeys(words))
    return unique_words, definitions


def _build_description(assessment, *, multi_model: bool) -> str:
    return build_puzzle_description(assessment, _models_used(multi_model))


def _stored_title_score(puzzle_row: dict) -> int | None:
    value = puzzle_row.get("title_score")
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _generate_title(
    puzzle,
    ai_client,
    rate_client,
    *,
    multi_model: bool,
    runtime: LmRuntime,
) -> TitleGenerationResult:
    words, definitions = _collect_title_inputs(puzzle)
    if not words or not definitions:
        return TitleGenerationResult(
            puzzle.title or "Rebus",
            0,
            "titlu existent pastrat",
            score_complete=False,
        )
    return generate_creative_title_result(
        words,
        definitions,
        client=ai_client,
        rate_client=rate_client,
        runtime=runtime,
        multi_model=multi_model,
    )


def _persist_puzzle_metadata(supabase, puzzle_id: str, payload: dict[str, object], *, dry_run: bool) -> None:
    if dry_run:
        return
    execute_logged_update(
        supabase,
        "crossword_puzzles",
        payload,
        eq_filters={"id": puzzle_id},
    )


def _persist_clues(
    supabase,
    puzzle_id: str,
    clue_rows: list[dict],
    puzzle,
    *,
    ai_client,
    runtime: LmRuntime,
    dry_run: bool,
) -> None:
    if dry_run:
        return

    clue_canon = ClueCanonService(
        store=ClueCanonStore(client=supabase),
        client=ai_client,
        runtime=runtime,
    )
    clue_store = clue_canon.store
    row_by_key = {
        ((row.get("direction") or "").upper(), row.get("start_row"), row.get("start_col")): row
        for row in clue_rows
    }
    key_to_id = {
        ((row.get("direction") or "").upper(), row.get("start_row"), row.get("start_col")): row["id"]
        for row in clue_rows
    }
    for direction, clues in (("H", puzzle.horizontal_clues), ("V", puzzle.vertical_clues)):
        for clue in clues:
            clue_id = key_to_id.get((direction, clue.start_row, clue.start_col))
            if not clue_id:
                continue
            row = row_by_key.get((direction, clue.start_row, clue.start_col), {})
            active = clue.active_version()
            verify_note = render_verify_note(active.assessment)
            decision = clue_canon.resolve_definition(
                word_normalized=clue.word_normalized,
                word_original=clue.word_original,
                definition=active.definition,
                word_type=clue.word_type,
                verified=bool(active.assessment.verified),
                semantic_score=extract_semantic_score(verify_note),
                rebus_score=extract_rebus_score(verify_note),
                creativity_score=extract_creativity_score(verify_note),
            )
            if not decision.canonical_definition_id:
                raise RuntimeError(
                    f"Canonical clue resolution produced no canonical_definition_id for {clue.word_normalized}"
                )
            clue_ref = clue_label(
                word=clue.word_normalized,
                direction=direction,
                clue_number=getattr(clue, "row_number", None),
                start_row=clue.start_row,
                start_col=clue.start_col,
            )
            log_canonical_event(
                decision.action,
                puzzle_id=puzzle_id,
                clue_ref=clue_ref,
                candidate_definition=active.definition,
                canonical_definition=decision.canonical_definition,
                detail=decision.decision_note or None,
            )
            if row:
                log_definition_event(
                    "repair-persist",
                    puzzle_id=puzzle_id,
                    clue_ref=clue_label_from_row(row),
                    before=str(row.get("definition") or ""),
                    after=decision.canonical_definition,
                    detail=f"verified={bool(active.assessment.verified)}",
                )
            execute_logged_update(
                supabase,
                "crossword_clues",
                clue_store.build_clue_definition_payload(
                    canonical_definition_id=decision.canonical_definition_id,
                    verify_note=verify_note,
                    verified=bool(active.assessment.verified),
                ),
                eq_filters={"id": clue_id, "puzzle_id": puzzle_id},
            )


def repair_puzzle(
    supabase,
    puzzle_row: dict,
    ai_client,
    rate_client,
    *,
    dry_run: bool = False,
    multi_model: bool = True,
    rounds: int = REPAIR_ROUNDS,
    verify_candidates: int = VERIFY_CANDIDATE_COUNT,
    runtime: LmRuntime | None = None,
) -> str:
    puzzle_id = puzzle_row["id"]
    clue_rows = fetch_clues(supabase, puzzle_id)
    if not clue_rows:
        log(f"  [{puzzle_id}] No clues found, skipping")
        return "skipped"

    runtime = runtime or LmRuntime(multi_model=multi_model)
    baseline_puzzle = build_working_puzzle(puzzle_row, clue_rows)
    baseline_eval = evaluate_puzzle_state(
        baseline_puzzle,
        ai_client,
        multi_model=multi_model,
        verify_candidates=verify_candidates,
        runtime=runtime,
    )
    baseline_puzzle.assessment = baseline_eval.assessment
    baseline_description = _build_description(baseline_eval.assessment, multi_model=multi_model)
    log(
        f"  [{puzzle_id}] baseline min={baseline_eval.assessment.min_rebus}/10 "
        f"avg={baseline_eval.assessment.avg_rebus:.1f}/10 "
        f"verified={baseline_eval.assessment.verified_count}/{baseline_eval.assessment.total_clues}"
    )

    if _needs_metadata_backfill(puzzle_row):
        baseline_payload = puzzle_metadata_payload(
            baseline_eval.assessment,
            description=baseline_description,
        )
        log(f"  [{puzzle_id}] backfill metadata")
        _persist_puzzle_metadata(supabase, puzzle_id, baseline_payload, dry_run=dry_run)

    candidate_puzzle = build_working_puzzle(puzzle_row, clue_rows)
    rewrite_result = run_rewrite_loop(
        candidate_puzzle,
        ai_client,
        rounds=rounds,
        theme=candidate_puzzle.title or "Puzzle rebus",
        multi_model=multi_model,
        verify_candidates=verify_candidates,
        hybrid_deanchor=True,
        runtime=runtime,
    )
    candidate_puzzle.assessment = score_puzzle_state(candidate_puzzle)
    log(
        f"  [{puzzle_id}] candidate min={candidate_puzzle.assessment.min_rebus}/10 "
        f"avg={candidate_puzzle.assessment.avg_rebus:.1f}/10 "
        f"verified={candidate_puzzle.assessment.verified_count}/{candidate_puzzle.assessment.total_clues}"
    )

    if candidate_puzzle.assessment.min_rebus <= baseline_eval.assessment.min_rebus:
        log(f"  [{puzzle_id}] rejected — score not better")
        return "rejected"

    title_result = _generate_title(
        candidate_puzzle,
        ai_client,
        rate_client,
        multi_model=multi_model,
        runtime=runtime,
    )
    if title_result.used_fallback and puzzle_row.get("title"):
        candidate_puzzle.title = puzzle_row.get("title") or "Rebus"
        title_score = _stored_title_score(puzzle_row)
    else:
        candidate_puzzle.title = title_result.title or candidate_puzzle.title or puzzle_row.get("title") or "Rebus"
        title_score = title_result.score if title_result.score_complete else None
    repaired_at = _now_iso()
    description = _build_description(candidate_puzzle.assessment, multi_model=multi_model)
    puzzle_payload = {
        "title": candidate_puzzle.title,
        "title_score": title_score,
        "updated_at": repaired_at,
        "repaired_at": repaired_at,
        **puzzle_metadata_payload(candidate_puzzle.assessment, description=description),
    }
    log(f"  [{puzzle_id}] accepted — '{puzzle_row.get('title', '')}' -> '{candidate_puzzle.title}'")
    _persist_puzzle_metadata(supabase, puzzle_id, puzzle_payload, dry_run=dry_run)
    _persist_clues(
        supabase,
        puzzle_id,
        clue_rows,
        candidate_puzzle,
        ai_client=ai_client,
        runtime=runtime,
        dry_run=dry_run,
    )
    log(
        f"  [{puzzle_id}] rewrite summary "
        f"{rewrite_result.initial_passed}/{rewrite_result.total} -> "
        f"{rewrite_result.final_passed}/{rewrite_result.total}"
    )
    return "accepted"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repair published puzzles in Supabase")
    parser.add_argument("--puzzle-id", help="Repair a specific puzzle by UUID")
    parser.add_argument("--limit", type=int, default=1, help="How many puzzles to process (default: 1)")
    parser.add_argument("--dry-run", action="store_true", help="Print changes without updating Supabase")
    parser.add_argument(
        "--multi-model",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use two-model cross-validation (default: True)",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=REPAIR_ROUNDS,
        help=f"Number of rewrite rounds (default: {REPAIR_ROUNDS})",
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
    handle = install_process_logging(
        run_id=f"repair_{path_timestamp()}",
        component="repair_puzzles",
        tee_console=True,
    )
    parser = build_parser()
    try:
        args = parser.parse_args()
        set_llm_debug_enabled(args.debug)
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            log("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
            sys.exit(1)

        preload = preload_runtime_prompts()
        audit = prompt_runtime_audit(PROJECT_ROOT)
        log(
            "Prompt runtime: "
            f"system={len(preload['system'])} user={len(preload['user'])} "
            f"git={audit.get('git_head') or '-'} dirty={len(audit.get('dirty_prompt_files', []))}"
        )
        dirty_prompt_files = audit.get("dirty_prompt_files", [])
        if dirty_prompt_files:
            log(f"Dirty prompt files: {', '.join(dirty_prompt_files)}")

        supabase = create_supabase_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        ai_client = create_ai_client()
        rate_client = create_ai_client()

        rows = fetch_puzzles(supabase, puzzle_id=args.puzzle_id)
        if not rows:
            log("No published puzzles found matching the criteria.")
            return
        selected = rows if args.puzzle_id else select_puzzles_for_repair(rows, limit=max(1, args.limit))
        log(f"Found {len(selected)} puzzle(s) to repair")
        if args.dry_run:
            log("(dry run — no updates will be made)\n")
        else:
            log("")

        counters = {"accepted": 0, "rejected": 0, "skipped": 0, "failed": 0}
        runtime = LmRuntime(multi_model=args.multi_model)
        for puzzle_row in selected:
            try:
                status = repair_puzzle(
                    supabase,
                    puzzle_row,
                    ai_client,
                    rate_client,
                    dry_run=args.dry_run,
                    multi_model=args.multi_model,
                    rounds=args.rounds,
                    verify_candidates=max(1, args.verify_candidates),
                    runtime=runtime,
                )
                counters[status] = counters.get(status, 0) + 1
            except Exception as exc:
                puzzle_id = puzzle_row.get("id", "?")
                log(f"  [{puzzle_id}] Error: {exc}")
                counters["failed"] += 1

        log(
            "\nSummary: "
            f"{counters['accepted']} accepted, "
            f"{counters['rejected']} rejected, "
            f"{counters['skipped']} skipped, "
            f"{counters['failed']} failed"
        )
    finally:
        handle.restore()


if __name__ == "__main__":
    main()
