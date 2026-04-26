"""Shared Supabase helpers for service-role access and logged mutations."""

from __future__ import annotations

from collections import Counter

import httpx
from postgrest.types import ReturnMethod
from supabase import create_client, ClientOptions

from ..config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from rebus_generator.platform.io.runtime_logging import log

_SELECT_CALLS_BY_TABLE: Counter[str] = Counter()
_MUTATION_CALLS_BY_TABLE: Counter[str] = Counter()
_MINIMAL_RETURN_COUNT = 0
_BROAD_SELECT_COUNT = 0


def reset_supabase_usage_stats() -> None:
    global _MINIMAL_RETURN_COUNT, _BROAD_SELECT_COUNT
    _SELECT_CALLS_BY_TABLE.clear()
    _MUTATION_CALLS_BY_TABLE.clear()
    _MINIMAL_RETURN_COUNT = 0
    _BROAD_SELECT_COUNT = 0


def record_supabase_select(table: str, *, broad: bool = False, columns: str = "") -> None:
    global _BROAD_SELECT_COUNT
    _SELECT_CALLS_BY_TABLE[table] += 1
    if broad or columns.strip() == "*":
        _BROAD_SELECT_COUNT += 1
        log(f'[supabase select warning] table={table} broad_select="{columns or "*"}"', level="WARN")


def _record_supabase_mutation(table: str, *, returning: ReturnMethod | None) -> None:
    global _MINIMAL_RETURN_COUNT
    _MUTATION_CALLS_BY_TABLE[table] += 1
    if returning == ReturnMethod.minimal:
        _MINIMAL_RETURN_COUNT += 1


def supabase_usage_stats_snapshot() -> dict[str, object]:
    return {
        "select_calls_by_table": dict(_SELECT_CALLS_BY_TABLE),
        "mutation_calls_by_table": dict(_MUTATION_CALLS_BY_TABLE),
        "minimal_return_count": _MINIMAL_RETURN_COUNT,
        "broad_select_count": _BROAD_SELECT_COUNT,
    }


def _call_with_returning(method, payload, returning: ReturnMethod):
    try:
        return method(payload, returning=returning)
    except TypeError:
        return method(payload)


def create_rebus_client(url: str, key: str):
    """Factory to create a Supabase client with explicit httpx configuration to avoid warnings."""
    options = ClientOptions(
        httpx_client=httpx.Client(timeout=30.0)
    )
    return create_client(url, key, options=options)


def create_service_role_client():
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
    return create_rebus_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)


def execute_logged_update(
    client,
    table: str,
    payload: dict[str, object],
    *,
    eq_filters: dict[str, object],
    returning: ReturnMethod = ReturnMethod.minimal,
):
    _record_supabase_mutation(table, returning=returning)
    query = _call_with_returning(client.table(table).update, payload, returning)
    for field, value in eq_filters.items():
        query = query.eq(field, value)
    result = query.execute()
    filters_text = ", ".join(f"{field}={value}" for field, value in eq_filters.items())
    keys_text = ", ".join(sorted(payload))
    row_count = "minimal" if returning == ReturnMethod.minimal else len(result.data or [])
    log(
        f"[supabase update] table={table} filters=({filters_text}) "
        f"payload_keys=[{keys_text}] rows={row_count}"
    )
    return result


def execute_logged_insert(
    client,
    table: str,
    payload: dict[str, object] | list[dict[str, object]],
    *,
    returning: ReturnMethod = ReturnMethod.representation,
):
    _record_supabase_mutation(table, returning=returning)
    result = _call_with_returning(client.table(table).insert, payload, returning).execute()
    row_count = "minimal" if returning == ReturnMethod.minimal else len(result.data or [])
    if isinstance(payload, list):
        keys = sorted({key for row in payload for key in row})
    else:
        keys = sorted(payload)
    keys_text = ", ".join(keys)
    log(
        f"[supabase insert] table={table} payload_keys=[{keys_text}] rows={row_count}"
    )
    return result
