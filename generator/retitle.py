"""Retitle existing puzzles in Supabase with improved creative titles."""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from supabase import create_client as create_supabase_client

from .config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from .core.ai_clues import create_client as create_ai_client
from .core.clue_canon_store import ClueCanonStore
from .core.lm_runtime import LmRuntime
from .core.model_manager import PRIMARY_MODEL, SECONDARY_MODEL, ModelConfig
from .core.runtime_logging import install_process_logging, log, path_timestamp
from .core.supabase_ops import execute_logged_update
from .phases.theme import (
    FALLBACK_TITLES,
    MAX_TITLE_ROUNDS,
    NO_TITLE_LABEL,
    TITLE_MIN_CREATIVITY,
    TitleGenerationResult,
    _build_rejected_context,
    _generate_candidate_with_active_model,
    _phase_label,
    _review_title_candidate,
    generate_creative_title_result,
    normalize_title_key,
    rate_title_creativity,
)


RETITLE_BATCH_SIZE = 10


@dataclass
class _RetitleBatchState:
    puzzle_row: dict
    words: list[str]
    definitions: list[str]
    forbidden_title_keys: set[str]
    best_result: TitleGenerationResult | None = None
    final_result: TitleGenerationResult | None = None
    rejected: list[tuple[str, str]] = field(default_factory=list)
    rejected_by_model: dict[str, list[tuple[str, str]]] = field(default_factory=lambda: {
        PRIMARY_MODEL.model_id: [],
        SECONDARY_MODEL.model_id: [],
    })

    @property
    def puzzle_id(self) -> str:
        return str(self.puzzle_row.get("id") or "")

    @property
    def done(self) -> bool:
        return self.final_result is not None


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
    rows = sorted(result.data or [], key=_puzzle_sort_key)

    if fallbacks_only:
        fallback_set = set(FALLBACK_TITLES)
        rows = [r for r in rows if r.get("title") in fallback_set]

    return rows


def _puzzle_sort_key(row: dict) -> tuple[bool, str, str]:
    return (
        row.get("created_at") is None,
        str(row.get("created_at") or ""),
        str(row.get("id") or ""),
    )


def _title_counts(rows: list[dict]) -> Counter[str]:
    return Counter(
        key for key in (normalize_title_key(row.get("title", "") or "") for row in rows) if key
    )


def select_puzzles_for_retitle(rows: list[dict]) -> list[dict]:
    return sorted(
        rows,
        key=lambda row: (
            _stored_title_score(row) is not None,
            row.get("created_at") is None,
            str(row.get("created_at") or ""),
            str(row.get("id") or ""),
        ),
    )


def select_duplicate_puzzles_for_retitle(rows: list[dict], *, global_rows: list[dict]) -> list[dict]:
    counts = _title_counts(global_rows)
    duplicate_keys = {key for key, count in counts.items() if count > 1}

    selected = [row for row in rows if normalize_title_key(row.get("title", "") or "") in duplicate_keys]
    return sorted(
        selected,
        key=lambda row: (
            -counts.get(normalize_title_key(row.get("title", "") or ""), 0),
            row.get("created_at") is None,
            str(row.get("created_at") or ""),
            str(row.get("id") or ""),
        ),
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stored_title_score(puzzle_row: dict) -> int | None:
    value = puzzle_row.get("title_score")
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_clues(supabase, puzzle_id: str) -> list[dict]:
    """Fetch all clues for a puzzle."""
    return ClueCanonStore(client=supabase).fetch_clue_rows(
        puzzle_id=puzzle_id,
        extra_fields=("word_normalized",),
    )


def _finalize_title_result(state: _RetitleBatchState) -> TitleGenerationResult:
    if state.final_result is not None:
        return state.final_result
    if state.best_result is not None and state.best_result.score > 0:
        return state.best_result
    return TitleGenerationResult(NO_TITLE_LABEL, 0, "niciun titlu valid", used_fallback=True)


def _backfill_title_score(
    supabase,
    puzzle_row: dict,
    score: int,
    *,
    dry_run: bool,
) -> None:
    if dry_run:
        puzzle_row["title_score"] = score
        return
    execute_logged_update(
        supabase,
        "crossword_puzzles",
        {"title_score": score, "updated_at": _now_iso()},
        eq_filters={"id": puzzle_row["id"]},
    )
    puzzle_row["title_score"] = score


def _resolve_old_title_score(
    puzzle_row: dict,
    words: list[str],
    rate_client,
    *,
    multi_model: bool,
    runtime: LmRuntime,
) -> tuple[int, bool, str | None]:
    stored_score = _stored_title_score(puzzle_row)
    if stored_score is not None:
        return stored_score, False, None

    old_title = puzzle_row.get("title", "")
    reviewed = _review_title_candidate(old_title, input_words=words)
    if not reviewed.valid:
        return 0, True, reviewed.feedback

    score_model = runtime.activate_secondary() if multi_model else runtime.activate_primary()
    old_score, _ = rate_title_creativity(old_title, words, rate_client, model_config=score_model)
    return old_score, True, None


def _update_best_result(state: _RetitleBatchState, result: TitleGenerationResult) -> None:
    if (
        state.best_result is None
        or result.score > state.best_result.score
        or (
            result.score == state.best_result.score
            and len(result.title.split()) < len(state.best_result.title.split())
        )
    ):
        state.best_result = result
    if result.score >= TITLE_MIN_CREATIVITY:
        state.final_result = result


def _generate_batch_candidates(
    states: list[_RetitleBatchState],
    client,
    *,
    active_model: ModelConfig,
    round_idx: int,
) -> list[tuple[_RetitleBatchState, str]]:
    valid_candidates: list[tuple[_RetitleBatchState, str]] = []
    for state in states:
        if state.done:
            continue
        rejected_context = _build_rejected_context(
            state.rejected_by_model.setdefault(active_model.model_id, [])
        )
        raw_title = _generate_candidate_with_active_model(
            state.definitions,
            state.words,
            client,
            active_model=active_model,
            rejected_context=rejected_context,
            empty_retry_instruction="Răspunde obligatoriu cu un singur titlu concret de 2-5 cuvinte, exclusiv în limba română.",
        )
        if not raw_title.strip():
            log(
                f'  [{state.puzzle_id}] Title round {round_idx} [{active_model.display_name}]: "(gol)" -> creativity=0/10 (titlu gol)'
            )
            continue

        reviewed = _review_title_candidate(raw_title, input_words=state.words)
        display_title = reviewed.title or raw_title.strip() or "(gol)"
        if not reviewed.valid:
            log(
                f'  [{state.puzzle_id}] Title round {round_idx} [{active_model.display_name}]: "{display_title}" -> creativity=0/10 ({reviewed.feedback})'
            )
            state.rejected.append((display_title, reviewed.feedback))
            state.rejected_by_model.setdefault(active_model.model_id, []).append((display_title, reviewed.feedback))
            continue

        title_key = normalize_title_key(reviewed.title)
        rejected_keys = {normalize_title_key(title) for title, _ in state.rejected}
        if reviewed.title in FALLBACK_TITLES:
            state.rejected.append((reviewed.title, "fallback generic"))
            state.rejected_by_model.setdefault(active_model.model_id, []).append((reviewed.title, "fallback generic"))
            continue
        if title_key in rejected_keys:
            state.rejected.append((reviewed.title, "titlu deja respins"))
            state.rejected_by_model.setdefault(active_model.model_id, []).append((reviewed.title, "titlu deja respins"))
            continue
        if title_key and title_key in state.forbidden_title_keys:
            log(
                f'  [{state.puzzle_id}] Title round {round_idx} [{active_model.display_name}]: "{reviewed.title}" -> creativity=0/10 (titlu deja folosit)'
            )
            state.rejected.append((reviewed.title, "titlu deja folosit"))
            state.rejected_by_model.setdefault(active_model.model_id, []).append((reviewed.title, "titlu deja folosit"))
            continue

        valid_candidates.append((state, reviewed.title))
    return valid_candidates


def _rate_batch_candidates(
    candidates: list[tuple[_RetitleBatchState, str]],
    rate_client,
    *,
    generator_model: ModelConfig,
    rating_model: ModelConfig,
    round_idx: int,
) -> None:
    for state, title in candidates:
        score, feedback = rate_title_creativity(
            title,
            state.words,
            rate_client,
            model_config=rating_model,
        )
        log(
            f'  [{state.puzzle_id}] Title round {round_idx} [{_phase_label(generator_model, rating_model)}]: "{title}" -> creativity={score}/10 ({feedback})'
        )
        result = TitleGenerationResult(title, score, feedback)
        _update_best_result(state, result)
        if state.done:
            continue
        state.rejected.append((title, feedback))
        state.rejected_by_model.setdefault(generator_model.model_id, []).append((title, feedback))


def generate_title_results_batch(
    states: list[_RetitleBatchState],
    client,
    rate_client,
    *,
    runtime: LmRuntime,
    multi_model: bool,
) -> dict[str, TitleGenerationResult]:
    if not states:
        return {}

    for round_idx in range(1, MAX_TITLE_ROUNDS + 1):
        pending = [state for state in states if not state.done]
        if not pending:
            break

        primary_model = runtime.activate_primary()
        primary_candidates = _generate_batch_candidates(
            pending,
            client,
            active_model=primary_model,
            round_idx=round_idx,
        )

        if multi_model:
            secondary_model = runtime.activate_secondary()
            _rate_batch_candidates(
                primary_candidates,
                rate_client,
                generator_model=primary_model,
                rating_model=secondary_model,
                round_idx=round_idx,
            )

            pending = [state for state in states if not state.done]
            if not pending:
                break

            secondary_candidates = _generate_batch_candidates(
                pending,
                client,
                active_model=secondary_model,
                round_idx=round_idx,
            )
            primary_model = runtime.activate_primary()
            _rate_batch_candidates(
                secondary_candidates,
                rate_client,
                generator_model=secondary_model,
                rating_model=primary_model,
                round_idx=round_idx,
            )
        else:
            _rate_batch_candidates(
                primary_candidates,
                rate_client,
                generator_model=primary_model,
                rating_model=primary_model,
                round_idx=round_idx,
            )

    return {state.puzzle_id: _finalize_title_result(state) for state in states}


def _apply_title_result(
    supabase,
    puzzle_row: dict,
    title_result: TitleGenerationResult,
    rate_client,
    *,
    dry_run: bool,
    multi_model: bool,
    runtime: LmRuntime | None,
    forbidden_title_keys: set[str] | None,
    words: list[str],
) -> bool:
    puzzle_id = puzzle_row["id"]
    old_title = puzzle_row.get("title", "")
    old_title_key = normalize_title_key(old_title)

    if title_result.used_fallback:
        log(f'  [{puzzle_id}] "{old_title}" -> skipped, no valid title candidate')
        return False

    new_title = title_result.title
    new_title_key = normalize_title_key(new_title)
    if new_title_key == old_title_key:
        log(f'  [{puzzle_id}] "{old_title}" -> unchanged')
        return False
    if forbidden_title_keys and new_title_key in forbidden_title_keys:
        log(f'  [{puzzle_id}] "{old_title}" -> "{new_title}" — skipped, duplicate normalized title')
        return False

    is_fallback = old_title in FALLBACK_TITLES
    runtime = runtime or LmRuntime(multi_model=multi_model)

    if not is_fallback:
        old_score, should_backfill_old_score, invalid_reason = _resolve_old_title_score(
            puzzle_row,
            words,
            rate_client,
            multi_model=multi_model,
            runtime=runtime,
        )
        if invalid_reason:
            log(f'  [{puzzle_id}] "{old_title}" old title invalid -> score=0 ({invalid_reason})')
        elif should_backfill_old_score:
            log(f'  [{puzzle_id}] "{old_title}" old title_score resolved -> {old_score}')
        new_score = title_result.score
        if new_score <= old_score:
            if should_backfill_old_score:
                _backfill_title_score(supabase, puzzle_row, old_score, dry_run=dry_run)
            log(
                f'  [{puzzle_id}] "{old_title}" (score={old_score}) '
                f'-> "{new_title}" (score={new_score}) — skipped, not better'
            )
            return False
        log(
            f'  [{puzzle_id}] "{old_title}" (score={old_score}) '
            f'-> "{new_title}" (score={new_score})'
        )
    else:
        log(f'  [{puzzle_id}] "{old_title}" (fallback) -> "{new_title}" (score={title_result.score})')

    if not dry_run:
        execute_logged_update(
            supabase,
            "crossword_puzzles",
            {"title": new_title, "title_score": title_result.score, "updated_at": _now_iso()},
            eq_filters={"id": puzzle_id},
        )
    puzzle_row["title"] = new_title
    puzzle_row["title_score"] = title_result.score
    return True


def retitle_puzzle(
    supabase,
    puzzle_row: dict,
    ai_client,
    rate_client,
    *,
    dry_run: bool = False,
    multi_model: bool = True,
    runtime: LmRuntime | None = None,
    forbidden_title_keys: set[str] | None = None,
) -> bool:
    """Generate a new title for a puzzle. Returns True if title changed."""
    puzzle_id = puzzle_row["id"]

    clues = fetch_clues(supabase, puzzle_id)
    if not clues:
        log(f"  [{puzzle_id}] No clues found, skipping")
        return False

    words = [c["word_normalized"] for c in clues if c.get("word_normalized")]
    definitions = [c["definition"] for c in clues if c.get("definition")]

    if not words or not definitions:
        log(f"  [{puzzle_id}] Missing words or definitions, skipping")
        return False

    title_result = generate_creative_title_result(
        words,
        definitions,
        client=ai_client,
        rate_client=rate_client,
        runtime=runtime,
        multi_model=multi_model,
        forbidden_title_keys=forbidden_title_keys,
    )
    return _apply_title_result(
        supabase,
        puzzle_row,
        title_result,
        rate_client,
        dry_run=dry_run,
        multi_model=multi_model,
        runtime=runtime,
        forbidden_title_keys=forbidden_title_keys,
        words=words,
    )


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
    return parser


def main() -> None:
    run_dir = Path("generator/output/retitle_runs") / path_timestamp()
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
        runtime.activate_primary()

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
                    changed = _apply_title_result(
                        supabase,
                        puzzle_row,
                        batch_results[puzzle_id],
                        rate_client,
                        dry_run=args.dry_run,
                        multi_model=multi_model,
                        runtime=runtime,
                        forbidden_title_keys=forbidden_title_keys,
                        words=batch_words[puzzle_id],
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
