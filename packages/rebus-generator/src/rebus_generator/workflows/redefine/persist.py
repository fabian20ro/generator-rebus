from __future__ import annotations

import copy
from datetime import datetime, timezone
import time

from rebus_generator.domain.puzzle_metrics import build_puzzle_description, puzzle_metadata_payload, score_puzzle_state
from rebus_generator.platform.io.clue_logging import clue_label_from_row, log_canonical_event, log_definition_event
from rebus_generator.platform.io.runtime_logging import log
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.llm.models import get_active_model_labels
from rebus_generator.platform.persistence.clue_canon_store import ClueCanonStore
from rebus_generator.platform.persistence.supabase_ops import execute_logged_update
from rebus_generator.workflows.canonicals.domain_service import ClueCanonService
from rebus_generator.domain.pipeline_state import WorkingClue, WorkingPuzzle, puzzle_from_working_state

from .load import PlannedClueUpdate, RedefinePersistencePlan, clue_key, working_clue_map


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_metadata_payload(assessment, *, multi_model: bool) -> dict[str, object]:
    description = build_puzzle_description(assessment, get_active_model_labels(multi_model=multi_model))
    payload = puzzle_metadata_payload(assessment, description=description)
    timestamp = now_iso()
    payload["updated_at"] = timestamp
    payload["repaired_at"] = timestamp
    return payload


def persist_puzzle_metadata(
    supabase,
    puzzle_id: str,
    payload: dict[str, object],
    *,
    dry_run: bool,
) -> None:
    _execute_logged_update_with_retry(
        supabase,
        "crossword_puzzles",
        payload,
        eq_filters={"id": puzzle_id},
        puzzle_id=puzzle_id,
        dry_run=dry_run,
    )


def apply_clue_version(target: WorkingClue, source: WorkingClue) -> None:
    final_version = copy.deepcopy(source.active_version())
    target.current = copy.deepcopy(final_version)
    target.best = copy.deepcopy(final_version)
    target.locked = source.locked


def clue_update_payload(store: ClueCanonStore, row: dict, desired: dict[str, object]) -> dict[str, object]:
    return store.build_clue_definition_payload(
        canonical_definition_id=desired.get("canonical_definition_id"),
        verify_note=str(desired["verify_note"]),
        verified=bool(desired["verified"]),
    )


def _is_retryable_persist_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "500" in text and "json could not be generated" in text
    ) or (
        "500" in text and "postgrest" in text
    ) or (
        "could not be generated" in text
    )


def _execute_logged_update_with_retry(
    supabase,
    table: str,
    payload: dict[str, object],
    *,
    eq_filters: dict[str, object],
    puzzle_id: str,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            execute_logged_update(
                supabase,
                table,
                payload,
                eq_filters=eq_filters,
            )
            return
        except Exception as exc:
            if not _is_retryable_persist_error(exc) or attempt >= attempts:
                raise RuntimeError(f"redefine_persist_apply failed for puzzle {puzzle_id}: {exc}") from exc
            delay = min(8.0, 1.5 * (2 ** (attempt - 1)))
            log(f"  [{puzzle_id}] persist retry {attempt}/{attempts} after retryable error: {exc}")
            time.sleep(delay)


def desired_clue_payloads(puzzle: WorkingPuzzle) -> dict[tuple[str, int, int], dict[str, object]]:
    rendered = puzzle_from_working_state(puzzle)
    payloads: dict[tuple[str, int, int], dict[str, object]] = {}
    for direction, clues in (("H", rendered.horizontal_clues), ("V", rendered.vertical_clues)):
        for clue in clues:
            payloads[clue_key(direction, clue.start_row, clue.start_col)] = {
                "definition": clue.definition,
                "verify_note": clue.verify_note or "",
                "verified": bool(clue.verified),
            }
    return payloads


def resolve_redefined_puzzle_canonicals(
    supabase,
    puzzle_row: dict,
    clue_rows: list[dict],
    candidate_puzzle: WorkingPuzzle,
    client,
    *,
    runtime: LmRuntime | None = None,
    multi_model: bool = True,
) -> dict[tuple[str, int, int], CanonicalDecision]:
    desired_payloads = desired_clue_payloads(candidate_puzzle)
    candidate_clues = working_clue_map(candidate_puzzle)
    clue_canon = ClueCanonService(
        store=ClueCanonStore(client=supabase),
        client=client,
        runtime=runtime,
        multi_model=multi_model,
    )
    decisions: dict[tuple[str, int, int], CanonicalDecision] = {}
    for row in clue_rows:
        key = clue_key(row.get("direction"), row.get("start_row"), row.get("start_col"))
        desired = desired_payloads.get(key)
        source_clue = candidate_clues.get(key)
        if not desired or not source_clue:
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
        decisions[key] = decision
    return decisions


def plan_redefined_puzzle_persistence(
    supabase,
    puzzle_row: dict,
    clue_rows: list[dict],
    baseline_puzzle: WorkingPuzzle,
    candidate_puzzle: WorkingPuzzle,
    client,
    *,
    decisions: dict[tuple[str, int, int], CanonicalDecision] | None = None,
    dry_run: bool = False,
    multi_model: bool = True,
    runtime: LmRuntime | None = None,
) -> RedefinePersistencePlan:
    puzzle_id = puzzle_row["id"]
    desired_payloads = desired_clue_payloads(candidate_puzzle)
    candidate_clues = working_clue_map(candidate_puzzle)
    persistence_puzzle = copy.deepcopy(baseline_puzzle)
    persistence_clues = working_clue_map(persistence_puzzle)
    clue_canon = ClueCanonService(
        store=ClueCanonStore(client=supabase),
        client=client,
        runtime=runtime,
    )
    clue_store = clue_canon.store
    clue_updates: list[PlannedClueUpdate] = []

    if decisions is None:
        decisions = resolve_redefined_puzzle_canonicals(
            supabase,
            puzzle_row,
            clue_rows,
            candidate_puzzle,
            client,
            runtime=runtime,
            multi_model=multi_model,
        )

    for row in clue_rows:
        key = clue_key(row.get("direction"), row.get("start_row"), row.get("start_col"))
        desired = desired_payloads.get(key)
        target_clue = persistence_clues.get(key)
        source_clue = candidate_clues.get(key)
        if not desired or not target_clue or not source_clue:
            continue

        decision = decisions.get(key)
        if decision is None:
            continue

        desired = dict(desired)
        desired["definition"] = decision.canonical_definition
        desired["canonical_definition_id"] = decision.canonical_definition_id
        clue_ref = clue_label_from_row(row)
        update_payload = clue_update_payload(clue_store, row, desired)
        current = {
            "definition": row.get("definition", "") or "",
            "verify_note": row.get("verify_note", "") or "",
            "verified": bool(row.get("verified")),
            "canonical_definition_id": row.get("canonical_definition_id"),
        }
        comparable_current = {field: current[field] for field in update_payload}
        if comparable_current == update_payload:
            continue
        apply_clue_version(target_clue, source_clue)
        clue_updates.append(
            PlannedClueUpdate(
                row_id=str(row["id"]),
                clue_ref=clue_ref,
                candidate_definition=str(desired_payloads.get(key, {}).get("definition") or ""),
                canonical_definition=decision.canonical_definition,
                update_payload=update_payload,
                canonical_action=decision.action,
                canonical_detail=decision.decision_note or None,
            )
        )
    metadata_payload = None
    from .load import _needs_metadata_backfill
    if clue_updates:
        persistence_puzzle.assessment = score_puzzle_state(persistence_puzzle)
        metadata_payload = build_metadata_payload(persistence_puzzle.assessment, multi_model=multi_model)
    elif _needs_metadata_backfill(puzzle_row):
        metadata_payload = build_metadata_payload(baseline_puzzle.assessment, multi_model=multi_model)
    return RedefinePersistencePlan(clue_updates=clue_updates, metadata_payload=metadata_payload)


def apply_redefined_puzzle_persistence(
    supabase,
    puzzle_row: dict,
    clue_rows: list[dict],
    plan: RedefinePersistencePlan,
    *,
    dry_run: bool = False,
) -> int:
    puzzle_id = puzzle_row["id"]
    rows_by_id = {str(row["id"]): row for row in clue_rows}
    for update in plan.clue_updates:
        row = rows_by_id.get(update.row_id)
        if row is None:
            continue
        log_canonical_event(
            update.canonical_action,
            puzzle_id=puzzle_id,
            clue_ref=update.clue_ref,
            candidate_definition=update.candidate_definition,
            canonical_definition=update.canonical_definition,
            detail=update.canonical_detail,
        )
        log_definition_event(
            "redefine",
            puzzle_id=puzzle_id,
            clue_ref=update.clue_ref,
            before=row.get("definition", "") or "",
            after=update.canonical_definition,
            detail=f"verified={bool(update.update_payload.get('verified'))}",
        )
        if not dry_run:
            _execute_logged_update_with_retry(
                supabase,
                "crossword_clues",
                update.update_payload,
                eq_filters={"id": row["id"], "puzzle_id": puzzle_id},
                puzzle_id=puzzle_id,
                dry_run=dry_run,
            )
        row.update(update.update_payload)
        row["definition"] = update.canonical_definition
        if plan.metadata_payload is not None:
            persist_puzzle_metadata(supabase, puzzle_id, plan.metadata_payload, dry_run=dry_run)
    if plan.metadata_payload is not None and not plan.clue_updates:
        persist_puzzle_metadata(supabase, puzzle_id, plan.metadata_payload, dry_run=dry_run)
    return len(plan.clue_updates)


def persist_redefined_puzzle(
    supabase,
    puzzle_row: dict,
    clue_rows: list[dict],
    baseline_puzzle: WorkingPuzzle,
    candidate_puzzle: WorkingPuzzle,
    client,
    *,
    dry_run: bool = False,
    multi_model: bool = True,
    runtime: LmRuntime | None = None,
) -> int:
    plan = plan_redefined_puzzle_persistence(
        supabase,
        puzzle_row,
        clue_rows,
        baseline_puzzle,
        candidate_puzzle,
        client,
        dry_run=dry_run,
        multi_model=multi_model,
        runtime=runtime,
    )
    return apply_redefined_puzzle_persistence(
        supabase,
        puzzle_row,
        clue_rows,
        plan,
        dry_run=dry_run,
    )
