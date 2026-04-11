from __future__ import annotations

import re

from rebus_generator.domain.diacritics import normalize


def _normalize_definition_text(text: str) -> str:
    normalized = normalize(text or "").lower()
    return " ".join(re.findall(r"[a-z0-9]+", normalized))


def canonical_quality_evidence_present(row) -> bool:
    return bool(
        getattr(row, "verified", False)
        or getattr(row, "semantic_score", None) is not None
        or getattr(row, "rebus_score", None) is not None
        or getattr(row, "creativity_score", None) is not None
    )


def canonical_reset_safe_sort_key(row) -> tuple[object, ...]:
    definition_norm = str(
        getattr(row, "definition_norm", "") or _normalize_definition_text(getattr(row, "definition", ""))
    )
    definition = str(getattr(row, "definition", "") or "")
    row_id = str(getattr(row, "id", "") or "")
    if not canonical_quality_evidence_present(row):
        return (
            1,
            definition_norm,
            len(definition),
            row_id,
        )
    return (
        0,
        0 if getattr(row, "verified", False) else 1,
        -(getattr(row, "semantic_score", None) or -1),
        -(getattr(row, "rebus_score", None) or -1),
        -(getattr(row, "creativity_score", None) or -1),
        -(getattr(row, "usage_count", None) or 0),
        len(definition),
        row_id,
    )


def canonical_is_strong(row) -> bool:
    if getattr(row, "verified", False):
        return True
    if not canonical_quality_evidence_present(row):
        return False
    semantic = int(getattr(row, "semantic_score", None) or 0)
    rebus = int(getattr(row, "rebus_score", None) or 0)
    creativity = int(getattr(row, "creativity_score", None) or 0)
    return semantic >= 8 and rebus >= 7 and creativity >= 5


def canonical_is_known_weak(row) -> bool:
    return canonical_quality_evidence_present(row) and not canonical_is_strong(row)
