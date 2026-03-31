"""Supabase adapter for canonical clue library tables."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from .clue_canon_types import CanonicalDefinition, ClueDefinitionRecord
from .runtime_logging import log
from .supabase_ops import create_service_role_client, execute_logged_insert, execute_logged_update

_CANONICAL_SELECT = (
    "id, word_normalized, word_original_seed, definition, definition_norm, "
    "verified, semantic_score, rebus_score, creativity_score, usage_count"
)
_SCHEMA_CHECKS: tuple[tuple[str, str], ...] = (
    ("canonical_clue_definitions", "id"),
    ("canonical_clue_aliases", "id"),
    ("crossword_clues", "id, canonical_definition_id"),
)


class ClueCanonStore:
    def __init__(self, client=None):
        self.client = client
        self._schema_available: bool | None = None
        self._warned = False
        self._word_cache: dict[str, list[CanonicalDefinition]] = {}
        self._alias_cache: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
        self._canonical_lookup: dict[tuple[str, str], CanonicalDefinition] = {}
        if self.client is None:
            try:
                self.client = create_service_role_client()
            except Exception as exc:
                self._schema_available = False
                self._warn_once(f"canonical clue library disabled: {exc}")

    def is_enabled(self) -> bool:
        if self.client is None:
            return False
        if self._schema_available is not None:
            return self._schema_available
        try:
            for table, fields in _SCHEMA_CHECKS:
                self.client.table(table).select(fields).limit(1).execute()
        except Exception as exc:
            self._schema_available = False
            self._warn_once(f"canonical clue library schema unavailable: {exc}")
            return False
        self._schema_available = True
        return True

    def fetch_canonical_variants(self, word_normalized: str, *, limit: int | None = None) -> list[CanonicalDefinition]:
        if not self.is_enabled():
            return []
        word = str(word_normalized or "").strip().upper()
        if word in self._word_cache:
            rows = self._word_cache[word]
            return rows[:limit] if limit is not None else list(rows)
        result = (
            self.client.table("canonical_clue_definitions")
            .select(_CANONICAL_SELECT)
            .eq("word_normalized", word)
            .execute()
        )
        rows = [self._canonical_from_row(row) for row in (result.data or [])]
        rows.sort(key=_canonical_sort_key)
        self._word_cache[word] = rows
        for row in rows:
            self._canonical_lookup[(row.word_normalized, row.definition_norm)] = row
        return rows[:limit] if limit is not None else list(rows)

    def find_exact_canonical(self, word_normalized: str, definition_norm: str) -> CanonicalDefinition | None:
        if not self.is_enabled():
            return None
        word = str(word_normalized or "").strip().upper()
        self.fetch_canonical_variants(word)
        return self._canonical_lookup.get((word, definition_norm))

    def create_canonical_definition(self, record: ClueDefinitionRecord) -> CanonicalDefinition | None:
        if not self.is_enabled():
            return None
        existing = self.find_exact_canonical(record.word_normalized, record.definition_norm)
        if existing is not None:
            return self.bump_usage(existing.id, record.word_normalized)

        payload = {
            "word_normalized": record.word_normalized,
            "word_original_seed": record.word_original,
            "definition": record.definition,
            "definition_norm": record.definition_norm,
            "verified": record.verified,
            "semantic_score": record.semantic_score,
            "rebus_score": record.rebus_score,
            "creativity_score": record.creativity_score,
            "usage_count": 1,
            "last_used_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        result = execute_logged_insert(
            self.client,
            "canonical_clue_definitions",
            payload,
        )
        row = self._canonical_from_row((result.data or [payload])[0])
        self._prime_canonical_cache(row)
        return row

    def bump_usage(self, canonical_id: str, word_normalized: str) -> CanonicalDefinition | None:
        if not self.is_enabled():
            return None
        rows = self.fetch_canonical_variants(word_normalized)
        current = next((row for row in rows if row.id == canonical_id), None)
        if current is None:
            return None
        updated_usage = (current.usage_count or 0) + 1
        execute_logged_update(
            self.client,
            "canonical_clue_definitions",
            {
                "usage_count": updated_usage,
                "last_used_at": _now_iso(),
                "updated_at": _now_iso(),
            },
            eq_filters={"id": canonical_id},
        )
        updated = CanonicalDefinition(
            id=current.id,
            word_normalized=current.word_normalized,
            word_original_seed=current.word_original_seed,
            definition=current.definition,
            definition_norm=current.definition_norm,
            verified=current.verified,
            semantic_score=current.semantic_score,
            rebus_score=current.rebus_score,
            creativity_score=current.creativity_score,
            usage_count=updated_usage,
        )
        self._prime_canonical_cache(updated)
        return updated

    def update_canonical_definition(self, canonical_id: str, record: ClueDefinitionRecord) -> CanonicalDefinition | None:
        if not self.is_enabled():
            return None
        payload = {
            "word_original_seed": record.word_original,
            "definition": record.definition,
            "definition_norm": record.definition_norm,
            "verified": record.verified,
            "semantic_score": record.semantic_score,
            "rebus_score": record.rebus_score,
            "creativity_score": record.creativity_score,
            "last_used_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        execute_logged_update(
            self.client,
            "canonical_clue_definitions",
            payload,
            eq_filters={"id": canonical_id},
        )
        rows = self.fetch_canonical_variants(record.word_normalized)
        updated_usage = 1
        for existing in rows:
            if existing.id == canonical_id:
                updated_usage = existing.usage_count
                break
        updated = CanonicalDefinition(
            id=canonical_id,
            word_normalized=record.word_normalized,
            word_original_seed=record.word_original,
            definition=record.definition,
            definition_norm=record.definition_norm,
            verified=record.verified,
            semantic_score=record.semantic_score,
            rebus_score=record.rebus_score,
            creativity_score=record.creativity_score,
            usage_count=updated_usage,
        )
        self._prime_canonical_cache(updated)
        return updated

    def attach_clue(
        self,
        clue_id: str,
        puzzle_id: str | None,
        *,
        canonical_definition_id: str,
        definition: str,
    ) -> None:
        if not self.is_enabled():
            return
        filters = {"id": clue_id}
        if puzzle_id:
            filters["puzzle_id"] = puzzle_id
        execute_logged_update(
            self.client,
            "crossword_clues",
            {
                "canonical_definition_id": canonical_definition_id,
                "definition": definition,
            },
            eq_filters=filters,
        )

    def insert_alias(
        self,
        *,
        canonical_definition_id: str,
        word_normalized: str,
        definition: str,
        definition_norm: str,
        source_clue_id: str | None,
        match_type: str,
        same_meaning_votes: int | None,
        winner_votes: int | None,
        decision_source: str,
        decision_note: str = "",
    ) -> None:
        if not self.is_enabled():
            return
        source_key = source_clue_id or ""
        dedupe_key = (canonical_definition_id, definition_norm, source_key)
        if dedupe_key in self._alias_cache[word_normalized]:
            return
        existing = (
            self.client.table("canonical_clue_aliases")
            .select("id")
            .eq("canonical_definition_id", canonical_definition_id)
            .eq("definition_norm", definition_norm)
            .eq("word_normalized", word_normalized)
            .execute()
        )
        if existing.data:
            self._alias_cache[word_normalized].add(dedupe_key)
            return
        execute_logged_insert(
            self.client,
            "canonical_clue_aliases",
            {
                "canonical_definition_id": canonical_definition_id,
                "source_clue_id": source_clue_id,
                "word_normalized": word_normalized,
                "definition": definition,
                "definition_norm": definition_norm,
                "match_type": match_type,
                "same_meaning_votes": same_meaning_votes,
                "winner_votes": winner_votes,
                "decision_source": decision_source,
                "decision_note": decision_note,
            },
        )
        self._alias_cache[word_normalized].add(dedupe_key)

    def _prime_canonical_cache(self, row: CanonicalDefinition) -> None:
        self._canonical_lookup[(row.word_normalized, row.definition_norm)] = row
        current = [item for item in self._word_cache.get(row.word_normalized, []) if item.id != row.id]
        current.append(row)
        current.sort(key=_canonical_sort_key)
        self._word_cache[row.word_normalized] = current

    @staticmethod
    def _canonical_from_row(row: dict) -> CanonicalDefinition:
        return CanonicalDefinition(
            id=str(row.get("id") or ""),
            word_normalized=str(row.get("word_normalized") or ""),
            word_original_seed=str(row.get("word_original_seed") or ""),
            definition=str(row.get("definition") or ""),
            definition_norm=str(row.get("definition_norm") or ""),
            verified=bool(row.get("verified")),
            semantic_score=_to_int(row.get("semantic_score")),
            rebus_score=_to_int(row.get("rebus_score")),
            creativity_score=_to_int(row.get("creativity_score")),
            usage_count=int(row.get("usage_count") or 0),
        )

    def _warn_once(self, message: str) -> None:
        if self._warned:
            return
        log(f"[clue canon] {message}")
        self._warned = True


def _canonical_sort_key(row: CanonicalDefinition) -> tuple[object, ...]:
    return (
        0 if row.verified else 1,
        -(row.semantic_score or -1),
        -(row.rebus_score or -1),
        -(row.creativity_score or -1),
        -row.usage_count,
        len(row.definition or ""),
        row.id,
    )


def _to_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
