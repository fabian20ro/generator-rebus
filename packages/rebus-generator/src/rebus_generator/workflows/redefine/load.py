from __future__ import annotations

from dataclasses import dataclass, field

from rebus_generator.platform.io.markdown_io import ClueEntry
from rebus_generator.platform.persistence.clue_canon_store import ClueCanonStore
from rebus_generator.platform.persistence.supabase_ops import record_supabase_select
from rebus_generator.domain.pipeline_state import WorkingClue, WorkingPuzzle, working_clue_from_entry


@dataclass
class PlannedClueUpdate:
    row_id: str
    clue_ref: str
    candidate_definition: str
    canonical_definition: str
    update_payload: dict[str, object]
    canonical_action: str
    canonical_detail: str | None


@dataclass
class RedefinePersistencePlan:
    clue_updates: list[PlannedClueUpdate]
    metadata_payload: dict[str, object] | None
    touched_canonical_ids: list[str] = field(default_factory=list)


def fetch_puzzles(
    supabase,
    *,
    date: str | None = None,
    puzzle_id: str | None = None,
    columns: str = "*",
) -> list[dict]:
    record_supabase_select("crossword_puzzles", broad=columns.strip() == "*", columns=columns)
    query = supabase.table("crossword_puzzles").select(columns)
    if puzzle_id:
        query = query.eq("id", puzzle_id)
    if date:
        query = query.gte("created_at", f"{date}T00:00:00").lte(
            "created_at", f"{date}T23:59:59"
        )
    result = query.execute()
    rows = result.data or []
    return sorted(rows, key=_puzzle_sort_key)


def fetch_run_all_candidates(supabase, *, limit: int = 200) -> list[dict]:
    try:
        result = supabase.rpc("run_all_redefine_candidates", {"limit_count": limit}).execute()
        record_supabase_select("rpc:run_all_redefine_candidates", columns="candidate_columns")
        return sorted(result.data or [], key=_puzzle_sort_key)
    except Exception:
        columns = (
            "id,title,grid_size,created_at,repaired_at,description,"
            "rebus_score_min,rebus_score_avg,definition_score,verified_count,total_clues,pass_rate"
        )
        return fetch_puzzles(supabase, columns=columns)[:limit]


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
    return ClueCanonStore(client=supabase).fetch_clue_rows(puzzle_id=puzzle_id)


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


def clue_key(direction: str | None, start_row: int | None, start_col: int | None) -> tuple[str, int, int]:
    return (_direction_code(direction), int(start_row or 0), int(start_col or 0))


def clue_row_sort_key(row: dict) -> tuple[object, ...]:
    direction = _direction_code(row.get("direction"))
    return (
        0 if direction == "H" else 1,
        int(row.get("clue_number") or 0),
        int(row.get("start_row") or 0),
        int(row.get("start_col") or 0),
        row.get("id") or "",
    )


def working_clue_map(puzzle: WorkingPuzzle) -> dict[tuple[str, int, int], WorkingClue]:
    mapping: dict[tuple[str, int, int], WorkingClue] = {}
    for direction, clues in (("H", puzzle.horizontal_clues), ("V", puzzle.vertical_clues)):
        for clue in clues:
            mapping[clue_key(direction, clue.start_row, clue.start_col)] = clue
    return mapping


def build_working_puzzle(puzzle_row: dict, clue_rows: list[dict]) -> WorkingPuzzle:
    horizontal_clues: list[WorkingClue] = []
    vertical_clues: list[WorkingClue] = []
    for idx, row in enumerate(sorted(clue_rows, key=clue_row_sort_key)):
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
    return WorkingPuzzle(
        title=puzzle_row.get("title", "") or "",
        size=puzzle_row.get("grid_size", 0) or 0,
        grid=[],
        horizontal_clues=horizontal_clues,
        vertical_clues=vertical_clues,
    )
