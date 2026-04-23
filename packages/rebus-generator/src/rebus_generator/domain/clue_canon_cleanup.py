from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


REFERENCED = "referenced"
UNREFERENCED_SINGLETON_FALLBACK = "unreferenced_singleton_fallback"
UNREFERENCED_BEST_FALLBACK = "unreferenced_best_fallback"
UNREFERENCED_REDUNDANT_DELETABLE = "unreferenced_redundant_deletable"


@dataclass(frozen=True)
class CanonicalCleanupClassification:
    row: dict
    category: str

    @property
    def id(self) -> str:
        return str(self.row.get("id") or "").strip()

    @property
    def deletable(self) -> bool:
        return self.category == UNREFERENCED_REDUNDANT_DELETABLE


def classify_canonical_cleanup_rows(
    canonical_rows: Iterable[dict],
    *,
    referenced_ids: set[str],
    target_ids: set[str] | None = None,
) -> list[CanonicalCleanupClassification]:
    rows = [dict(row) for row in canonical_rows]
    by_bucket: dict[tuple[str, str, str], list[dict]] = {}
    for row in rows:
        by_bucket.setdefault(_bucket_key(row), []).append(row)

    wanted = {
        str(row_id or "").strip()
        for row_id in (target_ids or set())
        if str(row_id or "").strip()
    }
    classifications: list[CanonicalCleanupClassification] = []
    for row in rows:
        row_id = str(row.get("id") or "").strip()
        if not row_id or (wanted and row_id not in wanted):
            continue
        if row_id in referenced_ids:
            classifications.append(CanonicalCleanupClassification(row=row, category=REFERENCED))
            continue
        active_rows = [candidate for candidate in by_bucket.get(_bucket_key(row), []) if _is_active_valid(candidate)]
        if not _is_active_valid(row):
            classifications.append(
                CanonicalCleanupClassification(row=row, category=UNREFERENCED_REDUNDANT_DELETABLE)
            )
            continue
        if len(active_rows) == 1:
            classifications.append(
                CanonicalCleanupClassification(row=row, category=UNREFERENCED_SINGLETON_FALLBACK)
            )
            continue
        best_rank = min(_quality_rank(candidate) for candidate in active_rows)
        category = (
            UNREFERENCED_BEST_FALLBACK
            if _quality_rank(row) == best_rank
            else UNREFERENCED_REDUNDANT_DELETABLE
        )
        classifications.append(CanonicalCleanupClassification(row=row, category=category))
    return classifications


def deletable_canonical_ids(
    canonical_rows: Iterable[dict],
    *,
    referenced_ids: set[str],
    target_ids: set[str] | None = None,
) -> list[str]:
    return sorted(
        classification.id
        for classification in classify_canonical_cleanup_rows(
            canonical_rows,
            referenced_ids=referenced_ids,
            target_ids=target_ids,
        )
        if classification.deletable
    )


def _bucket_key(row: dict) -> tuple[str, str, str]:
    return (
        str(row.get("word_normalized") or "").strip().upper(),
        str(row.get("word_type") or "").strip().upper(),
        str(row.get("usage_label") or "").strip(),
    )


def _is_active_valid(row: dict) -> bool:
    return not str(row.get("superseded_by") or "").strip() and bool(str(row.get("definition") or "").strip())


def _score(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _quality_rank(row: dict) -> tuple[object, ...]:
    return (
        0 if bool(row.get("verified")) else 1,
        -_score(row.get("semantic_score")),
        -_score(row.get("rebus_score")),
        -_score(row.get("creativity_score")),
        -max(0, _score(row.get("usage_count"))),
        -_timestamp(row.get("updated_at")),
    )


def _timestamp(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
