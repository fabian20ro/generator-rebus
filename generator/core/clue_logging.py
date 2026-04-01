"""Shared clue/canonical log formatting."""

from __future__ import annotations

from .runtime_logging import log


def compact_definition(text: str | None, *, limit: int = 140) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def clue_label(
    *,
    word: str | None = None,
    direction: str | None = None,
    clue_number: int | None = None,
    start_row: int | None = None,
    start_col: int | None = None,
) -> str:
    parts: list[str] = []
    if direction and clue_number is not None:
        parts.append(f"{direction}{clue_number}")
    elif clue_number is not None:
        parts.append(f"#{clue_number}")
    if word:
        parts.append(str(word))
    if start_row is not None and start_col is not None:
        parts.append(f"@{start_row},{start_col}")
    return " ".join(parts) if parts else "clue"


def clue_label_from_row(row: dict) -> str:
    return clue_label(
        word=str(row.get("word_normalized") or ""),
        direction=str(row.get("direction") or "").upper() or None,
        clue_number=_to_int(row.get("clue_number")),
        start_row=_to_int(row.get("start_row")),
        start_col=_to_int(row.get("start_col")),
    )


def clue_label_from_working_clue(clue, *, direction: str | None = None) -> str:
    return clue_label(
        word=getattr(clue, "word_normalized", ""),
        direction=direction,
        clue_number=_to_int(getattr(clue, "row_number", None)),
        start_row=_to_int(getattr(clue, "start_row", None)),
        start_col=_to_int(getattr(clue, "start_col", None)),
    )


def log_definition_event(
    action: str,
    *,
    puzzle_id: str | None = None,
    clue_ref: str,
    before: str | None = None,
    after: str | None = None,
    detail: str | None = None,
) -> None:
    prefix = f"[{puzzle_id}] " if puzzle_id else ""
    message = f"{prefix}{clue_ref} {action}"
    if before is not None:
        message += f" from='{compact_definition(before)}'"
    if after is not None:
        message += f" to='{compact_definition(after)}'"
    if detail:
        message += f" ({detail})"
    log(message)


def log_canonical_event(
    action: str,
    *,
    puzzle_id: str | None = None,
    clue_ref: str,
    candidate_definition: str,
    canonical_definition: str,
    detail: str | None = None,
) -> None:
    log_definition_event(
        f"canonical:{action}",
        puzzle_id=puzzle_id,
        clue_ref=clue_ref,
        before=candidate_definition,
        after=canonical_definition,
        detail=detail,
    )


def _to_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
