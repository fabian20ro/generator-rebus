"""Phase 7: Upload a verified puzzle to Supabase."""

from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import sys
from rebus_generator.platform.persistence.supabase_ops import create_rebus_client as create_client
from rebus_generator.platform.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
from rebus_generator.workflows.canonicals.domain_service import ClueCanonService
from rebus_generator.workflows.canonicals.planner import CanonicalPersistencePlanner
from rebus_generator.platform.persistence.clue_canon_store import ClueCanonStore
from rebus_generator.platform.io.clue_logging import clue_label_from_row, log_canonical_event
from rebus_generator.platform.io.markdown_io import parse_markdown
from rebus_generator.platform.io.runtime_logging import log
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.domain.slot_extractor import Slot, extract_slots
from rebus_generator.domain.clue_rating import (
    extract_creativity_score,
    extract_rebus_score,
    extract_semantic_score,
)


def _grid_to_json(grid: list[list[str]]) -> tuple[str, str]:
    """Convert grid to template (bool[][]) and solution (string[][]) JSON."""
    template = []
    solution = []
    for row in grid:
        t_row = []
        s_row = []
        for cell in row:
            if cell == "#":
                t_row.append(False)
                s_row.append(None)
            else:
                t_row.append(True)
                s_row.append(cell)
        template.append(t_row)
        solution.append(s_row)
    return json.dumps(template), json.dumps(solution)


def _clean_definition(definition: str) -> str:
    return definition.strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slots_with_words(grid: list[list[str]]) -> list[tuple[Slot, str]]:
    """Extract slots from the grid and read the word at each slot position."""
    template = [[cell != "#" for cell in row] for row in grid]
    slots = extract_slots(template)
    return [(slot, "".join(grid[r][c] for r, c in slot.cells)) for slot in slots]


def upload_puzzle(
    puzzle,
    force: bool = False,
    *,
    difficulty: int = 3,
    description: str = "",
    metadata: dict[str, object] | None = None,
    client=None,
    runtime: LmRuntime | None = None,
    multi_model: bool = True,
    published: bool = False,
) -> str:
    """Upload a parsed puzzle object and return the puzzle ID."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        log("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
        sys.exit(1)

    if not puzzle.grid:
        log("Error: no grid found in puzzle")
        sys.exit(1)

    # Check all definitions are verified
    all_clues = puzzle.horizontal_clues + puzzle.vertical_clues
    if not force:
        unverified = [c for c in all_clues if c.verified is not True]
        if unverified:
            log(f"Error: {len(unverified)} definitions failed verification.")
            log("Fix them and re-verify, or use --force to upload anyway.")
            for c in unverified:
                log(f"  ✗ {c.word_normalized}: {c.verify_note}")
            sys.exit(1)

    # Build grid JSON
    grid_template_json, grid_solution_json = _grid_to_json(puzzle.grid)
    log(f"Preparing upload payload: {puzzle.title or 'Untitled'}")

    # Find word positions in the grid for clue records
    slots_with_words = _slots_with_words(puzzle.grid)
    if len(all_clues) != len(slots_with_words):
        raise ValueError(
            f"slot-clue mismatch: grid has {len(slots_with_words)} slots, puzzle has {len(all_clues)} clues"
        )
    h_positions = [(s.start_row, s.start_col, word)
                   for s, word in slots_with_words if s.direction == "H"]
    v_positions = [(s.start_row, s.start_col, word)
                   for s, word in slots_with_words if s.direction == "V"]

    # Build coordinate lookup for matching clues to grid positions
    h_slot_by_word: dict[str, list[tuple[int, int, str]]] = {}
    for r, c, word in h_positions:
        h_slot_by_word.setdefault(word, []).append((r, c, word))
    v_slot_by_word: dict[str, list[tuple[int, int, str]]] = {}
    for r, c, word in v_positions:
        v_slot_by_word.setdefault(word, []).append((r, c, word))

    # Match clues to grid positions by word, consuming from the list
    clue_records = []
    clue_number = 1

    for clue in puzzle.horizontal_clues:
        positions = h_slot_by_word.get(clue.word_normalized, [])
        if not positions:
            raise ValueError(f"missing horizontal slot for clue {clue.word_normalized}")
        r, c, word = positions.pop(0)
        clue_records.append(
            {
                "direction": "H",
                "start_row": r,
                "start_col": c,
                "length": len(word),
                "word_normalized": clue.word_normalized,
                "word_original": clue.word_original or clue.word_normalized.lower(),
                "word_type": getattr(clue, "word_type", "") or "",
                "clue_number": clue_number,
            }
        )
        clue_records[-1].update(_candidate_assessment_payload(clue))
        clue_number += 1

    v_clue_number = 1
    for clue in puzzle.vertical_clues:
        positions = v_slot_by_word.get(clue.word_normalized, [])
        if not positions:
            raise ValueError(f"missing vertical slot for clue {clue.word_normalized}")
        r, c, word = positions.pop(0)
        clue_records.append(
            {
                "direction": "V",
                "start_row": r,
                "start_col": c,
                "length": len(word),
                "word_normalized": clue.word_normalized,
                "word_original": clue.word_original or clue.word_normalized.lower(),
                "word_type": getattr(clue, "word_type", "") or "",
                "clue_number": v_clue_number,
            }
        )
        clue_records[-1].update(_candidate_assessment_payload(clue))
        v_clue_number += 1

    unmatched_slots = sum(
        len(rows) for rows in (*h_slot_by_word.values(), *v_slot_by_word.values())
    )
    if unmatched_slots:
        raise ValueError(f"slot-clue mismatch: {unmatched_slots} grid slots have no matching clue")

    log(f"Uploading puzzle: {puzzle.title or 'Untitled'}")
    log(f"  Grid: {puzzle.size}x{puzzle.size}")
    log(f"  Clues: {len(clue_records)}")

    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    clue_store = ClueCanonStore(client=supabase)
    clue_canon = ClueCanonService(
        store=clue_store,
        client=client,
        runtime=runtime,
        multi_model=multi_model,
        track_usage=False,
        preserve_candidate_text=True,
    )
    created_timestamp = _now_iso()

    # Insert puzzle
    puzzle_data = {
        "title": puzzle.title or "Rebus",
        "description": description or None,
        "grid_size": puzzle.size,
        "grid_template": grid_template_json,
        "grid_solution": grid_solution_json,
        "difficulty": difficulty,
        "published": published,
    }
    if metadata:
        puzzle_data.update(metadata)
    puzzle_data["created_at"] = created_timestamp
    puzzle_data["updated_at"] = created_timestamp

    puzzle_id = ""
    created_canonical_ids: list[str] = []
    try:
        planner = CanonicalPersistencePlanner(resolver=clue_canon, builder=clue_store)
        plan = planner.plan_new_puzzle_clues(clue_records)
        resolved_clue_records = [planned.record for planned in plan.clues]
        created_canonical_ids = plan.touched_canonical_ids

        publication_key = _publication_key(puzzle_data, resolved_clue_records)
        result = supabase.rpc(
            "publish_crossword_puzzle_atomic",
            {
                "p_publication_key": publication_key,
                "p_puzzle": puzzle_data,
                "p_clues": resolved_clue_records,
            },
        ).execute()
        puzzle_id = _rpc_puzzle_id(result.data)
        log(f"  Puzzle ID: {puzzle_id}")

        if resolved_clue_records:
            for record in resolved_clue_records:
                record["puzzle_id"] = puzzle_id
            for planned in plan.clues:
                event = planned.canonical_event
                log_canonical_event(
                    event.action,
                    puzzle_id=puzzle_id,
                    clue_ref=clue_label_from_row(planned.record),
                    candidate_definition=event.candidate_definition,
                    canonical_definition=event.canonical_definition,
                    detail=event.detail,
                )
            for record in resolved_clue_records:
                log(
                    f"  [DB] {clue_label_from_row(record)}: "
                    f"{(record.get('canonical_definition_id') or '')[:80]}"
                )
    except Exception:
        if created_canonical_ids:
            clue_store.delete_unreferenced_canonicals_by_ids(created_canonical_ids)
        raise

    log(f"Uploaded! Puzzle ID: {puzzle_id}")
    log(f"Run 'python -m rebus_generator activate {puzzle_id}' to publish it.")
    return puzzle_id


def run(input_file: str, output_file: str, **kwargs) -> None:
    """Upload a puzzle to Supabase."""
    force = kwargs.get("force", False)

    log(f"Reading puzzle from {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        puzzle = parse_markdown(f.read())

    upload_puzzle(puzzle, force=force)


def _publication_key(puzzle_data: dict[str, object], clue_records: list[dict[str, object]]) -> str:
    stable_puzzle = {
        key: value
        for key, value in puzzle_data.items()
        if key not in {"created_at", "updated_at"}
    }
    payload = json.dumps(
        {"puzzle": stable_puzzle, "clues": clue_records},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _rpc_puzzle_id(data) -> str:
    if isinstance(data, str) and data:
        return data
    if isinstance(data, dict):
        value = data.get("id") or data.get("publish_crossword_puzzle_atomic")
        if value:
            return str(value)
    if isinstance(data, list) and data:
        return _rpc_puzzle_id(data[0])
    raise RuntimeError("atomic publication returned no puzzle ID")


def _candidate_assessment_payload(clue) -> dict[str, object]:
    verify_note = str(getattr(clue, "verify_note", "") or "")
    return {
        "_candidate_definition": _clean_definition(clue.definition or ""),
        "_verified": clue.verified is True,
        "_verify_note": verify_note,
        "_semantic_score": extract_semantic_score(verify_note),
        "_rebus_score": extract_rebus_score(verify_note),
        "_creativity_score": extract_creativity_score(verify_note),
    }
