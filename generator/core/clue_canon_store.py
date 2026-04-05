"""Supabase adapter for canonical clue library tables."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import re

from postgrest.exceptions import APIError

from .clue_canon_types import CanonicalDefinition, ClueDefinitionRecord
from .runtime_logging import log
from .supabase_ops import create_service_role_client, execute_logged_insert, execute_logged_update

_CANONICAL_SELECT = (
    "id, word_normalized, word_original_seed, definition, definition_norm, "
    "word_type, usage_label, verified, semantic_score, rebus_score, "
    "creativity_score, usage_count"
)
_SCHEMA_CHECKS: tuple[tuple[str, str], ...] = (
    ("canonical_clue_definitions", "id"),
    ("canonical_clue_aliases", "id"),
    ("crossword_clue_effective", "id, canonical_definition_id, definition"),
    ("crossword_clues", "id, canonical_definition_id"),
    ("crossword_puzzles", "id, grid_template, published"),
)
_CLUE_PAGE_SIZE = 1000
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)


class ClueCanonStore:
    def __init__(self, client=None):
        self.client = client
        self._schema_available: bool | None = None
        self._warned = False
        self._word_cache: dict[str, list[CanonicalDefinition]] = {}
        self._alias_cache: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
        self._canonical_lookup: dict[tuple[str, str, str, str], CanonicalDefinition] = {}
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
            self._canonical_lookup[_canonical_identity_key(row.word_normalized, row.word_type, row.usage_label, row.definition_norm)] = row
        return rows[:limit] if limit is not None else list(rows)

    def prefetch_canonical_variants(self, words_normalized: list[str]) -> dict[str, list[CanonicalDefinition]]:
        if not self.is_enabled():
            return {}
        words = sorted({
            str(word or "").strip().upper()
            for word in words_normalized
            if str(word or "").strip()
        })
        if not words:
            return {}
        missing = [word for word in words if word not in self._word_cache]
        if missing:
            fetched: dict[str, list[CanonicalDefinition]] = {word: [] for word in missing}
            for start in range(0, len(missing), _CLUE_PAGE_SIZE):
                chunk = missing[start : start + _CLUE_PAGE_SIZE]
                result = (
                    self.client.table("canonical_clue_definitions")
                    .select(_CANONICAL_SELECT)
                    .in_("word_normalized", chunk)
                    .execute()
                )
                for row_data in result.data or []:
                    row = self._canonical_from_row(row_data)
                    fetched.setdefault(row.word_normalized, []).append(row)
                    self._canonical_lookup[
                        _canonical_identity_key(
                            row.word_normalized,
                            row.word_type,
                            row.usage_label,
                            row.definition_norm,
                        )
                    ] = row
            for word in missing:
                rows = fetched.get(word, [])
                rows.sort(key=_canonical_sort_key)
                self._word_cache[word] = rows
        return {word: list(self._word_cache.get(word, [])) for word in words}

    def find_exact_canonical(
        self,
        word_normalized: str,
        definition_norm: str,
        *,
        word_type: str = "",
        usage_label: str = "",
    ) -> CanonicalDefinition | None:
        if not self.is_enabled():
            return None
        word = str(word_normalized or "").strip().upper()
        self.fetch_canonical_variants(word)
        return self._canonical_lookup.get(
            _canonical_identity_key(word, word_type, usage_label, definition_norm)
        )

    def create_canonical_definition(self, record: ClueDefinitionRecord) -> CanonicalDefinition | None:
        if not self.is_enabled():
            return None
        existing = self.find_exact_canonical(
            record.word_normalized,
            record.definition_norm,
            word_type=record.word_type,
            usage_label=record.usage_label,
        )
        if existing is not None:
            return self.bump_usage(existing.id, record.word_normalized)

        payload = {
            "word_normalized": record.word_normalized,
            "word_original_seed": record.word_original,
            "definition": record.definition,
            "definition_norm": record.definition_norm,
            "word_type": record.word_type,
            "usage_label": record.usage_label,
            "verified": record.verified,
            "semantic_score": record.semantic_score,
            "rebus_score": record.rebus_score,
            "creativity_score": record.creativity_score,
            "usage_count": 1,
            "last_used_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        try:
            result = execute_logged_insert(
                self.client,
                "canonical_clue_definitions",
                payload,
            )
        except APIError as exc:
            if not _is_unique_conflict(exc):
                raise
            self._invalidate_canonical_cache(record.word_normalized)
            existing = self.find_exact_canonical(
                record.word_normalized,
                record.definition_norm,
                word_type=record.word_type,
                usage_label=record.usage_label,
            )
            if existing is not None:
                log(
                    "[canonical conflict recovered] "
                    f"word={record.word_normalized} definition_norm={record.definition_norm}"
                )
                return self.bump_usage(existing.id, record.word_normalized) or existing
            raise RuntimeError(
                "Canonical insert conflicted but exact canonical could not be reloaded: "
                f"word={record.word_normalized} word_type={record.word_type} "
                f"usage_label={record.usage_label} definition_norm={record.definition_norm}"
            ) from exc
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
            word_type=current.word_type,
            usage_label=current.usage_label,
            verified=current.verified,
            semantic_score=current.semantic_score,
            rebus_score=current.rebus_score,
            creativity_score=current.creativity_score,
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
                canonical_definition_id=canonical_definition_id,
            ),
            eq_filters=filters,
        )

    def attach_clues(
        self,
        clue_ids: list[str],
        *,
        canonical_definition_id: str,
    ) -> int:
        if self.client is None:
            return 0
        unique_ids = sorted({
            str(clue_id or "").strip()
            for clue_id in clue_ids
            if str(clue_id or "").strip()
        })
        if not unique_ids:
            return 0
        payload = self.build_clue_definition_payload(
            canonical_definition_id=canonical_definition_id,
        )
        batches = 0
        for start in range(0, len(unique_ids), _CLUE_PAGE_SIZE):
            chunk = unique_ids[start : start + _CLUE_PAGE_SIZE]
            result = (
                self.client.table("crossword_clues")
                .update(payload)
                .in_("id", chunk)
                .execute()
            )
            log(
                "[supabase update] table=crossword_clues "
                f"filters=(id in {len(chunk)} values) payload_keys=[{', '.join(sorted(payload))}] "
                f"rows={len(result.data or [])}"
            )
            batches += 1
        return batches

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

    def insert_aliases(
        self,
        *,
        canonical_definition_id: str,
        word_normalized: str,
        aliases: list[dict[str, object]],
    ) -> int:
        if not self.is_enabled():
            return 0
        word = str(word_normalized or "").strip().upper()
        definition_norms = sorted({
            str(alias.get("definition_norm") or "").strip()
            for alias in aliases
            if str(alias.get("definition_norm") or "").strip()
        })
        existing_norms: set[str] = set()
        if definition_norms:
            for start in range(0, len(definition_norms), _CLUE_PAGE_SIZE):
                chunk = definition_norms[start : start + _CLUE_PAGE_SIZE]
                existing = (
                    self.client.table("canonical_clue_aliases")
                    .select("definition_norm")
                    .eq("canonical_definition_id", canonical_definition_id)
                    .eq("word_normalized", word)
                    .in_("definition_norm", chunk)
                    .execute()
                )
                for row in existing.data or []:
                    existing_norms.add(str(row.get("definition_norm") or "").strip())
        pending_rows: list[dict[str, object]] = []
        for alias in aliases:
            definition_norm = str(alias.get("definition_norm") or "").strip()
            source_key = str(alias.get("source_clue_id") or "")
            dedupe_key = (canonical_definition_id, definition_norm, source_key)
            if not definition_norm:
                continue
            if dedupe_key in self._alias_cache[word]:
                continue
            if definition_norm in existing_norms:
                self._alias_cache[word].add(dedupe_key)
                continue
            pending_rows.append(
                {
                    "canonical_definition_id": canonical_definition_id,
                    "source_clue_id": alias.get("source_clue_id"),
                    "word_normalized": word,
                    "definition": str(alias.get("definition") or ""),
                    "definition_norm": definition_norm,
                    "match_type": str(alias.get("match_type") or ""),
                    "same_meaning_votes": alias.get("same_meaning_votes"),
                    "winner_votes": alias.get("winner_votes"),
                    "decision_source": str(alias.get("decision_source") or ""),
                    "decision_note": str(alias.get("decision_note") or ""),
                }
            )
            existing_norms.add(definition_norm)
            self._alias_cache[word].add(dedupe_key)
        if not pending_rows:
            return 0
        execute_logged_insert(
            self.client,
            "canonical_clue_aliases",
            pending_rows,
        )
        return 1

    def build_clue_definition_payload(
        self,
        *,
        canonical_definition_id: str | None = None,
        verify_note: str | None = None,
        verified: bool | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {}
        if canonical_definition_id is not None:
            payload["canonical_definition_id"] = canonical_definition_id
        if verify_note is not None:
            payload["verify_note"] = verify_note
        if verified is not None:
            payload["verified"] = verified
        return payload

    def fetch_clue_rows(
        self,
        *,
        puzzle_id: str | None = None,
        verified: bool | None = None,
        canonical_missing_only: bool = False,
        word_normalized: str | None = None,
        extra_fields: tuple[str, ...] = (),
    ) -> list[dict]:
        if self.client is None:
            return []
        select_fields = [
            "id",
            "puzzle_id",
            "word_normalized",
            "word_original",
            "word_type",
            "direction",
            "start_row",
            "start_col",
            "length",
            "clue_number",
            "verify_note",
            "verified",
            "canonical_definition_id",
            "definition",
            "definition_source",
        ]
        for field in extra_fields:
            if field not in select_fields:
                select_fields.append(field)
        rows: list[dict] = []
        offset = 0
        while True:
            query = self.client.table("crossword_clue_effective").select(", ".join(select_fields))
            if puzzle_id:
                query = query.eq("puzzle_id", puzzle_id)
            if verified is not None:
                query = query.eq("verified", verified)
            if canonical_missing_only:
                query = query.is_("canonical_definition_id", "null")
            if word_normalized:
                query = query.eq("word_normalized", str(word_normalized or "").strip().upper())
            if puzzle_id:
                batch = query.execute().data or []
            else:
                batch = query.range(offset, offset + _CLUE_PAGE_SIZE - 1).execute().data or []
            rows.extend(batch)
            if puzzle_id or len(batch) < _CLUE_PAGE_SIZE:
                break
            offset += _CLUE_PAGE_SIZE
        return rows

    def fetch_puzzle_rows(
        self,
        *,
        published_only: bool = False,
        puzzle_id: str | None = None,
        limit: int | None = None,
        extra_fields: tuple[str, ...] = (),
    ) -> list[dict]:
        if self.client is None:
            return []
        select_fields = [
            "id",
            "title",
            "published",
            "grid_size",
            "grid_template",
            "created_at",
            "repaired_at",
        ]
        for field in extra_fields:
            if field not in select_fields:
                select_fields.append(field)
        rows: list[dict] = []
        offset = 0
        while True:
            query = self.client.table("crossword_puzzles").select(", ".join(select_fields)).order("id")
            if published_only:
                query = query.eq("published", True)
            if puzzle_id:
                query = query.eq("id", puzzle_id)
            if puzzle_id:
                batch = query.execute().data or []
            else:
                page_size = _CLUE_PAGE_SIZE
                if limit is not None:
                    remaining = limit - len(rows)
                    if remaining <= 0:
                        break
                    page_size = min(page_size, remaining)
                batch = query.range(offset, offset + page_size - 1).execute().data or []
            rows.extend(batch)
            if puzzle_id or len(batch) < _CLUE_PAGE_SIZE or (limit is not None and len(rows) >= limit):
                break
            offset += _CLUE_PAGE_SIZE
        return rows[:limit] if limit is not None else rows

    def fetch_clue_rows_for_puzzle_ids(
        self,
        puzzle_ids: list[str],
        *,
        extra_fields: tuple[str, ...] = (),
    ) -> list[dict]:
        if self.client is None:
            return []
        unique_ids = sorted({
            str(puzzle_id or "").strip()
            for puzzle_id in puzzle_ids
            if str(puzzle_id or "").strip()
        })
        if not unique_ids:
            return []
        select_fields = [
            "id",
            "puzzle_id",
            "direction",
            "start_row",
            "start_col",
            "length",
            "clue_number",
            "definition",
        ]
        for field in extra_fields:
            if field not in select_fields:
                select_fields.append(field)
        rows: list[dict] = []
        for start in range(0, len(unique_ids), _CLUE_PAGE_SIZE):
            chunk = unique_ids[start : start + _CLUE_PAGE_SIZE]
            offset = 0
            while True:
                query = (
                    self.client.table("crossword_clue_effective")
                    .select(", ".join(select_fields))
                    .in_("puzzle_id", chunk)
                    .order("puzzle_id")
                    .order("direction")
                    .order("clue_number")
                    .range(offset, offset + _CLUE_PAGE_SIZE - 1)
                )
                batch = query.execute().data or []
                rows.extend(batch)
                if len(batch) < _CLUE_PAGE_SIZE:
                    break
                offset += _CLUE_PAGE_SIZE
        return rows

    def fetch_backfill_source_rows(
        self,
        *,
        word_normalized: str | None = None,
        extra_fields: tuple[str, ...] = (),
    ) -> list[dict]:
        return self.fetch_clue_rows(
            canonical_missing_only=True,
            word_normalized=word_normalized,
            extra_fields=extra_fields,
        )

    def count_clue_rows(
        self,
        *,
        verified: bool | None = None,
        canonical_missing_only: bool = False,
        word_normalized: str | None = None,
    ) -> int:
        if self.client is None:
            return 0
        query = self.client.table("crossword_clue_effective").select("id", count="exact", head=True)
        if verified is not None:
            query = query.eq("verified", verified)
        if canonical_missing_only:
            query = query.is_("canonical_definition_id", "null")
        if word_normalized:
            query = query.eq("word_normalized", str(word_normalized or "").strip().upper())
        result = query.execute()
        return int(getattr(result, "count", 0) or 0)

    def fetch_canonical_definitions_by_ids(self, canonical_ids: list[str]) -> dict[str, str]:
        if not canonical_ids or not self.is_enabled():
            return {}
        unique_ids = sorted({
            canonical_id.strip()
            for canonical_id in canonical_ids
            if canonical_id and _UUID_RE.match(canonical_id.strip())
        })
        definitions: dict[str, str] = {}
        for start in range(0, len(unique_ids), _CLUE_PAGE_SIZE):
            chunk = unique_ids[start : start + _CLUE_PAGE_SIZE]
            if not chunk:
                continue
            result = (
                self.client.table("canonical_clue_definitions")
                .select("id, definition")
                .in_("id", chunk)
                .execute()
            )
            for row in result.data or []:
                definitions[str(row.get("id") or "")] = str(row.get("definition") or "")
        return definitions

    def _prime_canonical_cache(self, row: CanonicalDefinition) -> None:
        self._canonical_lookup[
            _canonical_identity_key(row.word_normalized, row.word_type, row.usage_label, row.definition_norm)
        ] = row
        current = [item for item in self._word_cache.get(row.word_normalized, []) if item.id != row.id]
        current.append(row)
        current.sort(key=_canonical_sort_key)
        self._word_cache[row.word_normalized] = current

    def _invalidate_canonical_cache(self, word_normalized: str) -> None:
        word = str(word_normalized or "").strip().upper()
        self._word_cache.pop(word, None)
        keys_to_remove = [
            key
            for key in self._canonical_lookup
            if key[0] == word
        ]
        for key in keys_to_remove:
            self._canonical_lookup.pop(key, None)

    @staticmethod
    def _canonical_from_row(row: dict) -> CanonicalDefinition:
        return CanonicalDefinition(
            id=str(row.get("id") or ""),
            word_normalized=str(row.get("word_normalized") or ""),
            word_original_seed=str(row.get("word_original_seed") or ""),
            definition=str(row.get("definition") or ""),
            definition_norm=str(row.get("definition_norm") or ""),
            word_type=str(row.get("word_type") or ""),
            usage_label=str(row.get("usage_label") or ""),
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


def _canonical_identity_key(
    word_normalized: str,
    word_type: str,
    usage_label: str,
    definition_norm: str,
) -> tuple[str, str, str, str]:
    return (
        str(word_normalized or "").strip().upper(),
        str(word_type or "").strip().upper(),
        str(usage_label or "").strip().lower(),
        str(definition_norm or "").strip(),
    )


def _is_unique_conflict(exc: APIError) -> bool:
    return str(getattr(exc, "code", "") or "") == "23505"
