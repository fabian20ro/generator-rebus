"""Supabase adapter for canonical clue library tables."""

from __future__ import annotations

from datetime import datetime, timezone
import re
import time

from postgrest.exceptions import APIError

from rebus_generator.domain.clue_canon_ranking import canonical_reset_safe_sort_key
from rebus_generator.domain.clue_canon_types import CanonicalDefinition, ClueDefinitionRecord
from rebus_generator.platform.io.runtime_logging import audit, log
from .supabase_ops import create_service_role_client, execute_logged_insert, execute_logged_update

_CANONICAL_SELECT = (
    "id, word_normalized, word_original_seed, definition, definition_norm, "
    "word_type, usage_label, verified, semantic_score, rebus_score, "
    "creativity_score, usage_count, superseded_by"
)
_CLUE_PAGE_SIZE = 1000
_CONFLICT_RELOAD_RETRY_SECONDS = 0.1
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)


class ClueCanonStore:
    def __init__(self, client=None):
        self.client = client or create_service_role_client()
        self._word_cache: dict[str, list[CanonicalDefinition]] = {}
        self._canonical_lookup: dict[tuple[str, str, str, str], CanonicalDefinition] = {}

    def fetch_canonical_variants(self, word_normalized: str, *, limit: int | None = None) -> list[CanonicalDefinition]:
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

    def fetch_active_canonical_variants(
        self,
        *,
        word_normalized: str | None = None,
        limit: int | None = None,
    ) -> list[CanonicalDefinition]:
        rows: list[CanonicalDefinition] = []
        offset = 0
        while True:
            query = (
                self.client.table("canonical_clue_definitions")
                .select(_CANONICAL_SELECT)
                .is_("superseded_by", "null")
                .order("word_normalized")
                .order("word_type")
                .order("usage_label")
                .order("id")
            )
            if word_normalized:
                query = query.eq("word_normalized", str(word_normalized or "").strip().upper())
            page_size = _CLUE_PAGE_SIZE
            if limit is not None:
                remaining = limit - len(rows)
                if remaining <= 0:
                    break
                page_size = min(page_size, remaining)
            batch = query.range(offset, offset + page_size - 1).execute().data or []
            converted = [self._canonical_from_row(row) for row in batch]
            rows.extend(converted)
            if len(batch) < page_size or (limit is not None and len(rows) >= limit):
                break
            offset += page_size
        for row in rows:
            self._prime_canonical_cache(row)
        return rows[:limit] if limit is not None else rows

    def fetch_active_canonical_variants_for_words(
        self,
        words_normalized: list[str],
    ) -> list[CanonicalDefinition]:
        words = sorted({
            str(word or "").strip().upper()
            for word in words_normalized
            if str(word or "").strip()
        })
        if not words:
            return []
        rows: list[CanonicalDefinition] = []
        for start in range(0, len(words), _CLUE_PAGE_SIZE):
            chunk = words[start : start + _CLUE_PAGE_SIZE]
            result = (
                self.client.table("canonical_clue_definitions")
                .select(_CANONICAL_SELECT)
                .is_("superseded_by", "null")
                .in_("word_normalized", chunk)
                .execute()
            )
            for row in result.data or []:
                converted = self._canonical_from_row(row)
                rows.append(converted)
                self._prime_canonical_cache(converted)
        rows.sort(key=_canonical_sort_key)
        return rows

    def prefetch_canonical_variants(self, words_normalized: list[str]) -> dict[str, list[CanonicalDefinition]]:
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
        word = str(word_normalized or "").strip().upper()
        self.fetch_canonical_variants(word)
        return self._canonical_lookup.get(
            _canonical_identity_key(word, word_type, usage_label, definition_norm)
        )

    def find_exact_canonical_db(
        self,
        word_normalized: str,
        definition_norm: str,
        *,
        word_type: str = "",
        usage_label: str = "",
    ) -> CanonicalDefinition | None:
        word = str(word_normalized or "").strip().upper()
        exact_word_type = str(word_type or "").strip().upper()
        exact_usage_label = str(usage_label or "").strip()
        exact_definition_norm = str(definition_norm or "").strip()
        if not word or not exact_definition_norm:
            return None
        result = (
            self.client.table("canonical_clue_definitions")
            .select(_CANONICAL_SELECT)
            .eq("word_normalized", word)
            .eq("word_type", exact_word_type)
            .eq("usage_label", exact_usage_label)
            .eq("definition_norm", exact_definition_norm)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            return None
        canonical = self._canonical_from_row(rows[0])
        self._prime_canonical_cache(canonical)
        return canonical

    def create_canonical_definition(self, record: ClueDefinitionRecord) -> CanonicalDefinition | None:
        existing = self.find_exact_canonical(
            record.word_normalized,
            record.definition_norm,
            word_type=record.word_type,
            usage_label=record.usage_label,
        )
        if existing is None:
            existing = self.find_exact_canonical_db(
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
            existing = self.find_exact_canonical_db(
                record.word_normalized,
                record.definition_norm,
                word_type=record.word_type,
                usage_label=record.usage_label,
            )
            if existing is None:
                time.sleep(_CONFLICT_RELOAD_RETRY_SECONDS)
                existing = self.find_exact_canonical_db(
                    record.word_normalized,
                    record.definition_norm,
                    word_type=record.word_type,
                    usage_label=record.usage_label,
                )
            if existing is not None:
                audit(
                    "clue_canon_conflict_recovered_direct_exact",
                    component="clue_canon",
                    payload={
                        "word": record.word_normalized,
                        "word_type": record.word_type,
                        "usage_label": record.usage_label,
                        "definition_norm": record.definition_norm,
                    },
                )
                log(
                    "[canonical conflict recovered_direct_exact] "
                    f"word={record.word_normalized} definition_norm={record.definition_norm}"
                )
                return self.bump_usage(existing.id, record.word_normalized) or existing
            audit(
                "clue_canon_conflict_unresolved",
                component="clue_canon",
                payload={
                    "word": record.word_normalized,
                    "word_type": record.word_type,
                    "usage_label": record.usage_label,
                    "definition_norm": record.definition_norm,
                },
            )
            log(
                "[canonical conflict unresolved] "
                f"word={record.word_normalized} definition_norm={record.definition_norm}",
                level="ERROR",
            )
            raise RuntimeError(
                "Canonical insert conflicted but exact canonical could not be reloaded: "
                f"word={record.word_normalized} word_type={record.word_type} "
                f"usage_label={record.usage_label} definition_norm={record.definition_norm}"
            ) from exc
        row = self._canonical_from_row((result.data or [payload])[0])
        self._prime_canonical_cache(row)
        return row

    def bump_usage(self, canonical_id: str, word_normalized: str) -> CanonicalDefinition | None:
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

    def fetch_raw_clue_rows(
        self,
        *,
        extra_fields: tuple[str, ...] = (),
    ) -> list[dict]:
        select_fields = [
            "id",
            "puzzle_id",
            "word_normalized",
            "canonical_definition_id",
        ]
        for field in extra_fields:
            if field not in select_fields:
                select_fields.append(field)
        rows: list[dict] = []
        offset = 0
        while True:
            batch = (
                self.client.table("crossword_clues")
                .select(", ".join(select_fields))
                .order("id")
                .range(offset, offset + _CLUE_PAGE_SIZE - 1)
                .execute()
                .data
                or []
            )
            rows.extend(batch)
            if len(batch) < _CLUE_PAGE_SIZE:
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

    def fetch_canonical_rows(
        self,
        *,
        limit: int | None = None,
        extra_fields: tuple[str, ...] = (),
    ) -> list[dict]:
        if self.client is None:
            return []
        select_fields = [
            "id",
            "word_normalized",
            "definition",
            "superseded_by",
        ]
        for field in extra_fields:
            if field not in select_fields:
                select_fields.append(field)
        rows: list[dict] = []
        offset = 0
        while True:
            page_size = _CLUE_PAGE_SIZE
            if limit is not None:
                remaining = limit - len(rows)
                if remaining <= 0:
                    break
                page_size = min(page_size, remaining)
            batch = (
                self.client.table("canonical_clue_definitions")
                .select(", ".join(select_fields))
                .order("word_normalized")
                .order("id")
                .range(offset, offset + page_size - 1)
                .execute()
                .data
                or []
            )
            rows.extend(batch)
            if len(batch) < page_size or (limit is not None and len(rows) >= limit):
                break
            offset += page_size
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

    def fetch_clue_rows_for_canonical_ids(
        self,
        canonical_ids: list[str],
        *,
        extra_fields: tuple[str, ...] = (),
    ) -> list[dict]:
        if self.client is None:
            return []
        unique_ids = sorted({
            str(canonical_id or "").strip()
            for canonical_id in canonical_ids
            if str(canonical_id or "").strip()
        })
        if not unique_ids:
            return []
        select_fields = [
            "id",
            "puzzle_id",
            "canonical_definition_id",
            "word_normalized",
            "word_original",
            "word_type",
            "verify_note",
            "verified",
            "definition",
        ]
        for field in extra_fields:
            if field not in select_fields:
                select_fields.append(field)
        rows: list[dict] = []
        for start in range(0, len(unique_ids), _CLUE_PAGE_SIZE):
            chunk = unique_ids[start : start + _CLUE_PAGE_SIZE]
            batch = (
                self.client.table("crossword_clue_effective")
                .select(", ".join(select_fields))
                .in_("canonical_definition_id", chunk)
                .execute()
                .data
                or []
            )
            rows.extend(batch)
        rows.sort(
            key=lambda row: (
                str(row.get("canonical_definition_id") or ""),
                0 if str(row.get("verify_note") or "").strip() else 1,
                0 if bool(row.get("verified")) else 1,
                str(row.get("id") or ""),
            )
        )
        return rows

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
        if not canonical_ids:
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

    def fetch_canonical_rows_by_ids(self, canonical_ids: list[str]) -> list[CanonicalDefinition]:
        unique_ids = sorted({
            str(canonical_id or "").strip()
            for canonical_id in canonical_ids
            if str(canonical_id or "").strip() and _UUID_RE.match(str(canonical_id or "").strip())
        })
        if not unique_ids:
            return []
        rows: list[CanonicalDefinition] = []
        for start in range(0, len(unique_ids), _CLUE_PAGE_SIZE):
            chunk = unique_ids[start : start + _CLUE_PAGE_SIZE]
            result = (
                self.client.table("canonical_clue_definitions")
                .select(_CANONICAL_SELECT)
                .in_("id", chunk)
                .execute()
            )
            for row in result.data or []:
                rows.append(self._canonical_from_row(row))
        return rows

    def repoint_clues_by_canonical_ids(
        self,
        source_canonical_ids: list[str],
        *,
        canonical_definition_id: str,
    ) -> int:
        if self.client is None:
            return 0
        unique_ids = sorted({
            str(canonical_id or "").strip()
            for canonical_id in source_canonical_ids
            if str(canonical_id or "").strip()
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
                .in_("canonical_definition_id", chunk)
                .execute()
            )
            log(
                "[supabase update] table=crossword_clues "
                f"filters=(canonical_definition_id in {len(chunk)} values) "
                f"payload_keys=[{', '.join(sorted(payload))}] rows={len(result.data or [])}"
            )
            batches += 1
        return batches

    def mark_canonicals_superseded(
        self,
        canonical_ids: list[str],
        *,
        superseded_by: str,
    ) -> int:
        if self.client is None:
            return 0
        unique_ids = sorted({
            str(canonical_id or "").strip()
            for canonical_id in canonical_ids
            if str(canonical_id or "").strip() and str(canonical_id or "").strip() != superseded_by
        })
        if not unique_ids:
            return 0
        word_map = self.fetch_canonical_definitions_by_word_ids(unique_ids)
        batches = 0
        for start in range(0, len(unique_ids), _CLUE_PAGE_SIZE):
            chunk = unique_ids[start : start + _CLUE_PAGE_SIZE]
            payload = {
                "superseded_by": superseded_by,
                "updated_at": _now_iso(),
            }
            result = (
                self.client.table("canonical_clue_definitions")
                .update(payload)
                .in_("id", chunk)
                .execute()
            )
            log(
                "[supabase update] table=canonical_clue_definitions "
                f"filters=(id in {len(chunk)} values) payload_keys=[{', '.join(sorted(payload))}] "
                f"rows={len(result.data or [])}"
            )
            batches += 1
        self._canonical_lookup = {
            key: value
            for key, value in self._canonical_lookup.items()
            if value.id not in set(unique_ids)
        }
        for row in word_map.values():
            self._invalidate_canonical_cache(row)
        return batches

    def fetch_canonical_definitions_by_word_ids(self, canonical_ids: list[str]) -> dict[str, str]:
        if not canonical_ids or self.client is None:
            return {}
        unique_ids = sorted({
            canonical_id.strip()
            for canonical_id in canonical_ids
            if canonical_id and _UUID_RE.match(canonical_id.strip())
        })
        words: dict[str, str] = {}
        for start in range(0, len(unique_ids), _CLUE_PAGE_SIZE):
            chunk = unique_ids[start : start + _CLUE_PAGE_SIZE]
            result = (
                self.client.table("canonical_clue_definitions")
                .select("id, word_normalized")
                .in_("id", chunk)
                .execute()
            )
            for row in result.data or []:
                words[str(row.get("id") or "")] = str(row.get("word_normalized") or "")
        return words

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
            superseded_by=str(row.get("superseded_by") or "") or None,
        )

def _canonical_sort_key(row: CanonicalDefinition) -> tuple[object, ...]:
    return canonical_reset_safe_sort_key(row)


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
