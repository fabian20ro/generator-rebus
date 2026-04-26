from __future__ import annotations

from collections import Counter

from rebus_generator.platform.persistence.clue_canon_store import ClueCanonStore
from rebus_generator.platform.persistence.supabase_ops import record_supabase_select
from rebus_generator.domain.guards.title_guards import normalize_title_key
from rebus_generator.workflows.retitle.sanitize import FALLBACK_TITLES


def fetch_puzzles(
    supabase,
    *,
    date: str | None = None,
    puzzle_id: str | None = None,
    fallbacks_only: bool = False,
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
    rows = sorted(result.data or [], key=_puzzle_sort_key)
    if fallbacks_only:
        fallback_set = set(FALLBACK_TITLES)
        rows = [row for row in rows if row.get("title") in fallback_set]
    return rows


def fetch_run_all_candidates(supabase, *, limit: int = 200) -> list[dict]:
    try:
        result = supabase.rpc("run_all_retitle_candidates", {"limit_count": limit}).execute()
        record_supabase_select("rpc:run_all_retitle_candidates", columns="id,title,title_score,created_at")
        return select_puzzles_for_retitle(result.data or [])
    except Exception:
        return select_puzzles_for_retitle(
            fetch_puzzles(supabase, columns="id,title,title_score,created_at")[:limit]
        )


def fetch_title_rows(supabase) -> list[dict]:
    record_supabase_select("crossword_puzzles", columns="id,title")
    result = supabase.table("crossword_puzzles").select("id,title").execute()
    return result.data or []


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
            stored_title_score(row) is not None,
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


def stored_title_score(puzzle_row: dict) -> int | None:
    value = puzzle_row.get("title_score")
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_clues(supabase, puzzle_id: str) -> list[dict]:
    return ClueCanonStore(client=supabase).fetch_clue_rows(
        puzzle_id=puzzle_id,
        extra_fields=("word_normalized",),
    )
