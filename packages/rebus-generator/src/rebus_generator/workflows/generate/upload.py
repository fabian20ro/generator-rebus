"""Phase 7: Upload a verified puzzle to Supabase."""

from __future__ import annotations
from datetime import datetime, timezone
import json
import sys
from rebus_generator.platform.persistence.supabase_ops import create_rebus_client as create_client
from rebus_generator.platform.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
from rebus_generator.workflows.canonicals.domain_service import ClueCanonService
from rebus_generator.workflows.canonicals.planner import CanonicalPersistencePlanner, CanonicalInput
from rebus_generator.platform.persistence.clue_canon_store import ClueCanonStore
from rebus_generator.platform.io.clue_logging import clue_label_from_row, log_canonical_event
from rebus_generator.platform.io.markdown_io import parse_markdown
from rebus_generator.platform.io.runtime_logging import log
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.domain.slot_extractor import Slot, extract_slots


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
    return definition.split("→", 1)[0].strip()


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
        unverified = [c for c in all_clues if c.verified is False]
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
        if positions:
            r, c, word = positions.pop(0)
            clue_records.append({
                "direction": "H",
                "start_row": r,
                "start_col": c,
                "length": len(word),
                "word_normalized": clue.word_normalized,
                "word_original": clue.word_original or clue.word_normalized.lower(),
                "word_type": getattr(clue, "word_type", "") or "",
                "clue_number": clue_number,
            })
            clue_records[-1]["_candidate_definition"] = _clean_definition(clue.definition or "")
            clue_number += 1

    v_clue_number = 1
    for clue in puzzle.vertical_clues:
        positions = v_slot_by_word.get(clue.word_normalized, [])
        if positions:
            r, c, word = positions.pop(0)
            clue_records.append({
                "direction": "V",
                "start_row": r,
                "start_col": c,
                "length": len(word),
                "word_normalized": clue.word_normalized,
                "word_original": clue.word_original or clue.word_normalized.lower(),
                "word_type": getattr(clue, "word_type", "") or "",
                "clue_number": v_clue_number,
            })
            clue_records[-1]["_candidate_definition"] = _clean_definition(clue.definition or "")
            v_clue_number += 1

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
        "published": False,
    }
    if metadata:
        puzzle_data.update(metadata)
    puzzle_data["created_at"] = created_timestamp
    puzzle_data["updated_at"] = created_timestamp

    puzzle_id = ""
    created_canonical_ids: list[str] = []
    try:
        inputs = []
        record_by_index = {}
        for idx, record in enumerate(clue_records):
            resolved_record = dict(record)
            original_definition = str(resolved_record.pop("_candidate_definition", "") or "")
            record_by_index[idx] = (resolved_record, original_definition)
            inputs.append(
                CanonicalInput(
                    word_normalized=resolved_record["word_normalized"],
                    word_original=resolved_record["word_original"],
                    definition=original_definition,
                    word_type=str(resolved_record.get("word_type") or ""),
                    clue_id=str(idx), # Use index as temporary ID
                )
            )
            
        planner = CanonicalPersistencePlanner(resolver=clue_canon, builder=clue_store)
        plan = planner.plan(inputs)
        
        resolved_clue_records: list[dict[str, object]] = []
        canonical_events: list[tuple[str, dict[str, object], str, object]] = []
        
        for persistence in plan.clue_persistences:
            idx = int(persistence.clue_id)
            resolved_record, original_definition = record_by_index[idx]
            resolved_record.update(persistence.payload)
            resolved_clue_records.append(resolved_record)
            canonical_events.append((persistence.action, resolved_record, original_definition, persistence))
            
        created_canonical_ids = plan.touched_canonical_ids

        result = supabase.table("crossword_puzzles").insert(puzzle_data).execute()
        puzzle_id = result.data[0]["id"]
        log(f"  Puzzle ID: {puzzle_id}")

        if resolved_clue_records:
            for record in resolved_clue_records:
                record["puzzle_id"] = puzzle_id
            supabase.table("crossword_clues").insert(resolved_clue_records).execute()
            for action, record, original_definition, decision in canonical_events:
                log_canonical_event(
                    action,
                    puzzle_id=puzzle_id,
                    clue_ref=clue_label_from_row(record),
                    candidate_definition=original_definition,
                    canonical_definition=decision.canonical_definition,
                    detail=decision.detail,
                )
            for record in resolved_clue_records:
                log(
                    f"  [DB] {clue_label_from_row(record)}: "
                    f"{(record.get('canonical_definition_id') or '')[:80]}"
                )
    except Exception:
        if puzzle_id:
            _delete_uploaded_puzzle(supabase, puzzle_id)
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


def _delete_uploaded_puzzle(supabase, puzzle_id: str) -> None:
    try:
        supabase.table("crossword_clues").delete().eq("puzzle_id", puzzle_id).execute()
        supabase.table("crossword_puzzles").delete().eq("id", puzzle_id).execute()
        log(f"  [cleanup] removed partial upload for puzzle_id={puzzle_id}")
    except Exception as cleanup_exc:
        log(f"  [cleanup failed] puzzle_id={puzzle_id} error={cleanup_exc}", level="WARN")
