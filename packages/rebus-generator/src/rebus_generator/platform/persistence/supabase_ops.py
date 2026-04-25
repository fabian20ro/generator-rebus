"""Shared Supabase helpers for service-role access and logged mutations."""

from __future__ import annotations

import httpx
from supabase import create_client, ClientOptions

from ..config import SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from rebus_generator.platform.io.runtime_logging import log


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
):
    query = client.table(table).update(payload)
    for field, value in eq_filters.items():
        query = query.eq(field, value)
    result = query.execute()
    filters_text = ", ".join(f"{field}={value}" for field, value in eq_filters.items())
    keys_text = ", ".join(sorted(payload))
    row_count = len(result.data or [])
    log(
        f"[supabase update] table={table} filters=({filters_text}) "
        f"payload_keys=[{keys_text}] rows={row_count}"
    )
    return result


def execute_logged_insert(
    client,
    table: str,
    payload: dict[str, object] | list[dict[str, object]],
):
    result = client.table(table).insert(payload).execute()
    row_count = len(result.data or [])
    if isinstance(payload, list):
        keys = sorted({key for row in payload for key in row})
    else:
        keys = sorted(payload)
    keys_text = ", ".join(keys)
    log(
        f"[supabase insert] table={table} payload_keys=[{keys_text}] rows={row_count}"
    )
    return result
