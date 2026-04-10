from __future__ import annotations

from collections import Counter

from rebus_generator.platform.persistence.clue_canon_store import ClueCanonStore
from rebus_generator.workflows.retitle.titleing import FALLBACK_TITLES, normalize_title_key


def fetch_puzzles(
    supabase,
    *,
    date: str | None = None,
    puzzle_id: str | None = None,
    fallbacks_only: bool = False,
) -> list[dict]:
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
        rows = [row for row in rows if row.get("title") in fallback_set]
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
