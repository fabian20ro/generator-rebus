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
_CLUE_PAGE_SIZE = 1000


class ClueCanonStore:
    def __init__(self, client=None):
        self.client = client
        self._schema_available: bool | None = None
        self._warned = False
        self._word_cache: dict[str, list[CanonicalDefinition]] = {}
        self._alias_cache: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
        self._canonical_lookup: dict[tuple[str, str], CanonicalDefinition] = {}
        self._crossword_clues_columns: dict[str, bool] = {}
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
        if self.client is None:
            return
        filters = {"id": clue_id}
        if puzzle_id:
            filters["puzzle_id"] = puzzle_id
        execute_logged_update(
            self.client,
            "crossword_clues",
            self.build_clue_definition_payload(
                definition=definition,
                canonical_definition_id=canonical_definition_id,
            ),
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

    def has_crossword_clues_column(self, column: str) -> bool:
        if self.client is None:
            return False
        if column in self._crossword_clues_columns:
            return self._crossword_clues_columns[column]
        try:
            self.client.table("crossword_clues").select(f"id, {column}").limit(1).execute()
            available = True
        except Exception as exc:
            if "does not exist" not in str(exc):
                raise
            available = False
        self._crossword_clues_columns[column] = available
        return available

    def supports_legacy_definition_column(self) -> bool:
        return self.has_crossword_clues_column("definition")

    def supports_canonical_definition_column(self) -> bool:
        return self.has_crossword_clues_column("canonical_definition_id")

    def build_clue_definition_payload(
        self,
        *,
        definition: str | None = None,
        canonical_definition_id: str | None = None,
        verify_note: str | None = None,
        verified: bool | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {}
        if canonical_definition_id is not None and self.supports_canonical_definition_column():
            payload["canonical_definition_id"] = canonical_definition_id
        if definition is not None and self.supports_legacy_definition_column():
            payload["definition"] = definition
        if verify_note is not None and self.has_crossword_clues_column("verify_note"):
            payload["verify_note"] = verify_note
        if verified is not None and self.has_crossword_clues_column("verified"):
            payload["verified"] = verified
        return payload

    def fetch_clue_rows(
        self,
        *,
        puzzle_id: str | None = None,
        extra_fields: tuple[str, ...] = (),
    ) -> list[dict]:
        if self.client is None:
            return []
        select_fields = [
            "id",
            "puzzle_id",
            "word_normalized",
            "word_original",
            "direction",
            "start_row",
            "start_col",
            "length",
            "clue_number",
        ]
        for field in ("verify_note", "verified", "canonical_definition_id"):
            if self.has_crossword_clues_column(field):
                select_fields.append(field)
        for field in extra_fields:
            if field not in select_fields and self.has_crossword_clues_column(field):
                select_fields.append(field)
        rows: list[dict] = []
        offset = 0
        while True:
            query = self.client.table("crossword_clues").select(", ".join(select_fields))
            if puzzle_id:
                query = query.eq("puzzle_id", puzzle_id)
                batch = query.execute().data or []
            else:
                batch = query.range(offset, offset + _CLUE_PAGE_SIZE - 1).execute().data or []
            rows.extend(batch)
            if puzzle_id or len(batch) < _CLUE_PAGE_SIZE:
                break
            offset += _CLUE_PAGE_SIZE
        return self.hydrate_clue_definitions(rows, puzzle_id=puzzle_id)

    def hydrate_clue_definitions(
        self,
        rows: list[dict],
        *,
        puzzle_id: str | None = None,
    ) -> list[dict]:
        if not rows:
            return rows
        canonical_ids = [
            str(row.get("canonical_definition_id") or "").strip()
            for row in rows
            if row.get("canonical_definition_id")
        ]
        canonical_by_id = self.fetch_canonical_definitions_by_ids(canonical_ids)
        for row in rows:
            row["definition_source"] = "missing"
            canonical_id = str(row.get("canonical_definition_id") or "").strip()
            canonical_definition = canonical_by_id.get(canonical_id)
            if canonical_definition:
                row["definition"] = canonical_definition
                row["definition_source"] = "canonical"
        missing_rows = [row for row in rows if not row.get("definition")]
        if missing_rows and self.supports_legacy_definition_column():
            legacy_by_id = self._fetch_legacy_definitions(
                [str(row.get("id") or "") for row in missing_rows],
                puzzle_id=puzzle_id,
            )
            for row in missing_rows:
                legacy_definition = legacy_by_id.get(str(row.get("id") or ""))
                if legacy_definition:
                    row["definition"] = legacy_definition
                    row["definition_source"] = "legacy"
                else:
                    row["definition"] = row.get("definition") or ""
        else:
            for row in missing_rows:
                row["definition"] = row.get("definition") or ""
        return rows

    def fetch_canonical_definitions_by_ids(self, canonical_ids: list[str]) -> dict[str, str]:
        if not canonical_ids or not self.is_enabled():
            return {}
        unique_ids = sorted({canonical_id for canonical_id in canonical_ids if canonical_id})
        definitions: dict[str, str] = {}
        for start in range(0, len(unique_ids), _CLUE_PAGE_SIZE):
            chunk = unique_ids[start : start + _CLUE_PAGE_SIZE]
            result = (
                self.client.table("canonical_clue_definitions")
                .select("id, definition")
                .in_("id", chunk)
                .execute()
            )
            for row in result.data or []:
                definitions[str(row.get("id") or "")] = str(row.get("definition") or "")
        return definitions

    def _fetch_legacy_definitions(
        self,
        clue_ids: list[str],
        *,
        puzzle_id: str | None = None,
    ) -> dict[str, str]:
        if not clue_ids or self.client is None or not self.supports_legacy_definition_column():
            return {}
        query = self.client.table("crossword_clues").select("id, definition")
        if puzzle_id:
            query = query.eq("puzzle_id", puzzle_id)
        else:
            query = query.in_("id", clue_ids)
        result = query.execute()
        return {
            str(row.get("id") or ""): str(row.get("definition") or "")
            for row in (result.data or [])
        }

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
