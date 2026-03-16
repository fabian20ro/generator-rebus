"""Dexonline.ro definition provider with multi-layer caching.

Architecture (3 cache layers):
  L1 — In-memory dict (per DexProvider instance, i.e. per puzzle run)
  L2 — Supabase ``dex_definitions`` table (persistent, shared across projects)
  L3 — dexonline.ro HTTP fetch (origin, with crawl-delay and exponential backoff)

Usage::

    from supabase import create_client
    sb = create_client(url, key)
    dex = DexProvider(sb)

    # Prefetch all puzzle words (batch L2 query, then L3 for missing)
    dex.prefetch(["CASA", "MARE"], originals={"CASA": "casă", "MARE": "mare"})

    # Single lookup (hits L1 first, then L2, then L3)
    defs = dex.get("CASA", original="casă")

    # Read-only lookup (L1 + L2 only, no HTTP)
    defs = dex.lookup("CASA")

Designed for easy embedding in other projects (propozitii-nostime, word-rarity).
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

from .diacritics import normalize

_USER_AGENT = (
    "Mozilla/5.0 (compatible; generator-rebus/1.0; "
    "+https://github.com/fabian20ro/generator-rebus)"
)
_CRAWL_DELAY = 2.0
_MAX_RETRIES = 3
_MAX_DEFS = 8


# ---------------------------------------------------------------------------
# HTML parsing — extract plain-text definitions from dexonline synthesis tab
# ---------------------------------------------------------------------------

class _DefinitionExtractor(HTMLParser):
    """Extract text from <span class="tree-def html"> elements."""

    def __init__(self):
        super().__init__()
        self._in_def = False
        self._depth = 0
        self._current: list[str] = []
        self.definitions: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "span":
            classes = dict(attrs).get("class", "") or ""
            if "tree-def" in classes:
                self._in_def = True
                self._depth = 1
                self._current = []
                return
        if self._in_def:
            self._depth += 1 if tag in ("span", "div", "p", "b", "i", "a", "em", "strong", "sup", "sub") else 0

    def handle_endtag(self, tag: str) -> None:
        if self._in_def:
            if tag == "span":
                self._depth -= 1
                if self._depth <= 0:
                    text = " ".join("".join(self._current).split()).strip()
                    if text:
                        self.definitions.append(text)
                    self._in_def = False

    def handle_data(self, data: str) -> None:
        if self._in_def:
            self._current.append(data)


def parse_definitions_from_html(html: str) -> list[str]:
    """Extract plain-text definitions from dexonline HTML."""
    parser = _DefinitionExtractor()
    parser.feed(html)
    seen: set[str] = set()
    result: list[str] = []
    for d in parser.definitions:
        if d not in seen:
            seen.add(d)
            result.append(d)
    return result


def _format_definitions(defs: list[str]) -> str:
    """Format a list of definition strings into bullet-point text."""
    return "\n".join(f"- {d}" for d in defs[:_MAX_DEFS])


# ---------------------------------------------------------------------------
# Fetch from dexonline.ro — with exponential backoff
# ---------------------------------------------------------------------------

def fetch_from_dexonline(
    original: str, *, max_retries: int = _MAX_RETRIES,
) -> tuple[str, str]:
    """Fetch definition page from dexonline.ro.

    Retries with exponential backoff (2s, 4s, 8s) on transient errors.
    Does NOT retry on 404 (not_found) or successful responses.

    Returns (html, status) where status is 'ok', 'not_found', or 'error'.
    """
    url = f"https://dexonline.ro/definitie/{urllib.request.quote(original.lower())}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})

    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="replace")
                if "meaningContainer" in html or "tree-def" in html:
                    return html, "ok"
                return html, "not_found"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return "", "not_found"
            if attempt < max_retries:
                time.sleep(2 ** (attempt + 1))
                continue
            return "", "error"
        except Exception:
            if attempt < max_retries:
                time.sleep(2 ** (attempt + 1))
                continue
            return "", "error"

    return "", "error"  # unreachable, but satisfies type checkers


# ---------------------------------------------------------------------------
# Supabase helpers (L2 cache operations)
# ---------------------------------------------------------------------------

def _sb_lookup_single(client, normalized: str) -> tuple[str | None, bool]:
    """Query Supabase for one word. Returns (html_or_None, was_found_in_db)."""
    try:
        resp = (client.table("dex_definitions")
                .select("html, status")
                .eq("word", normalized)
                .limit(1)
                .execute())
    except Exception:
        return None, False
    if not resp.data:
        return None, False
    row = resp.data[0]
    if row["status"] != "ok" or not row.get("html"):
        return None, True  # in DB but no usable content
    return row["html"], True


def _sb_lookup_batch(client, words: list[str]) -> dict[str, str | None]:
    """Batch query Supabase. Returns {normalized: html_or_None} for words found."""
    result: dict[str, str | None] = {}
    for i in range(0, len(words), 200):
        chunk = words[i:i + 200]
        try:
            resp = (client.table("dex_definitions")
                    .select("word, html, status")
                    .in_("word", chunk)
                    .execute())
        except Exception:
            continue
        for row in resp.data:
            if row["status"] == "ok" and row.get("html"):
                result[row["word"]] = row["html"]
            else:
                result[row["word"]] = None  # present but unusable
    return result


def _sb_store(client, word: str, original: str, html: str, status: str) -> None:
    """Upsert a definition into Supabase."""
    now = datetime.now(timezone.utc).isoformat()
    try:
        client.table("dex_definitions").upsert({
            "word": word,
            "original": original,
            "html": html,
            "status": status,
            "fetched_at": now,
        }).execute()
    except Exception:
        pass  # non-critical — word will be re-fetched next time


# ---------------------------------------------------------------------------
# DexProvider — multi-layer cache
# ---------------------------------------------------------------------------

class DexProvider:
    """Multi-layer definition cache: memory -> Supabase -> dexonline.ro.

    One instance per puzzle run. Thread-unsafe (single-threaded pipeline).
    """

    def __init__(self, supabase_client=None):
        self._sb = supabase_client
        # L1: normalized word -> formatted definitions string (or None for known-missing)
        self._memory: dict[str, str | None] = {}
        self._last_fetch_time: float = 0.0  # monotonic clock

    # -- Public API --------------------------------------------------------

    def get(self, word: str, original: str = "") -> str | None:
        """Get formatted definitions, fetching from dexonline if needed.

        Cache resolution order: L1 memory -> L2 Supabase -> L3 dexonline.
        """
        norm = normalize(word)

        # L1: in-memory
        if norm in self._memory:
            return self._memory[norm]

        # L2: Supabase
        if self._sb is not None:
            html, found = _sb_lookup_single(self._sb, norm)
            if found:
                formatted = self._parse_and_cache(norm, html)
                return formatted

        # L3: dexonline.ro
        return self._fetch_and_store(norm, original or word)

    def lookup(self, word: str) -> str | None:
        """Read-only lookup: L1 + L2 only, no HTTP fetch."""
        norm = normalize(word)

        if norm in self._memory:
            return self._memory[norm]

        if self._sb is not None:
            html, found = _sb_lookup_single(self._sb, norm)
            if found:
                return self._parse_and_cache(norm, html)

        return None

    def prefetch(
        self,
        words: list[str],
        originals: dict[str, str] | None = None,
        *,
        fetch_missing: bool = True,
    ) -> dict[str, str]:
        """Batch-load definitions for multiple words.

        1. Skips words already in L1.
        2. Batch-queries L2 (Supabase) for the rest.
        3. If fetch_missing=True, fetches L3 (dexonline) for words not in L2.

        Returns {normalized: formatted_defs} for words that have definitions.
        """
        if originals is None:
            originals = {}

        normalized_map: dict[str, str] = {}  # norm -> original
        to_query: list[str] = []

        for w in words:
            norm = normalize(w)
            if norm in self._memory:
                continue
            if norm not in normalized_map:
                normalized_map[norm] = originals.get(w, w)
                to_query.append(norm)

        # L2 batch query
        if to_query and self._sb is not None:
            sb_results = _sb_lookup_batch(self._sb, to_query)
            for norm, html in sb_results.items():
                self._parse_and_cache(norm, html)
            # Remove found words from to_query
            to_query = [n for n in to_query if n not in sb_results]

        found_count = sum(1 for v in self._memory.values() if v is not None)
        total = len(normalized_map) + sum(1 for n in words if normalize(n) in self._memory and normalize(n) not in normalized_map)

        # L3: fetch missing from dexonline one-by-one
        if fetch_missing and to_query:
            print(f"  DEX: fetching {len(to_query)} words from dexonline.ro...")
            for norm in to_query:
                original = normalized_map.get(norm, norm)
                self._fetch_and_store(norm, original)

        # Report
        cached = sum(1 for w in words if self._memory.get(normalize(w)) is not None)
        total_words = len(set(normalize(w) for w in words))
        if cached:
            print(f"  DEX cache: {cached}/{total_words} words have definitions")

        return {
            normalize(w): self._memory[normalize(w)]
            for w in words
            if self._memory.get(normalize(w)) is not None
        }

    def as_dict(self) -> dict[str, str]:
        """Return a snapshot of all cached definitions as {normalized: formatted}.

        Useful for passing to functions that expect a plain dict.
        """
        return {k: v for k, v in self._memory.items() if v is not None}

    # -- Internal ----------------------------------------------------------

    def _parse_and_cache(self, norm: str, html: str | None) -> str | None:
        """Parse HTML and store formatted result in L1. Returns formatted or None."""
        if not html:
            self._memory[norm] = None
            return None
        defs = parse_definitions_from_html(html)
        if defs:
            formatted = _format_definitions(defs)
            self._memory[norm] = formatted
            return formatted
        self._memory[norm] = None
        return None

    def _respect_crawl_delay(self) -> None:
        """Sleep if needed to respect robots.txt Crawl-delay."""
        if self._last_fetch_time > 0:
            elapsed = time.monotonic() - self._last_fetch_time
            remaining = _CRAWL_DELAY - elapsed
            if remaining > 0:
                time.sleep(remaining)

    def _fetch_and_store(self, norm: str, original: str) -> str | None:
        """Fetch from dexonline, store in L2 and L1. Returns formatted or None."""
        self._respect_crawl_delay()
        html, status = fetch_from_dexonline(original)
        self._last_fetch_time = time.monotonic()

        # Store in L2 (Supabase)
        if self._sb is not None:
            _sb_store(self._sb, norm, original, html, status)

        # Store in L1 (memory)
        if status == "ok" and html:
            return self._parse_and_cache(norm, html)
        self._memory[norm] = None
        return None


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

def create_provider() -> DexProvider:
    """Create a DexProvider with Supabase client from environment config.

    Returns a provider with no Supabase backend if credentials are missing.
    """
    try:
        from supabase import create_client as _create_sb
        from ..config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            return DexProvider()
        sb = _create_sb(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        return DexProvider(sb)
    except BaseException:
        return DexProvider()


# ---------------------------------------------------------------------------
# Legacy compatibility — batch lookup returning a plain dict
# ---------------------------------------------------------------------------

def lookup_batch(client, words: list[str]) -> dict[str, str]:
    """Batch lookup cached definitions. Returns {normalized: formatted_text}.

    Legacy wrapper — new code should use DexProvider.prefetch() instead.
    """
    provider = DexProvider(client)
    return provider.prefetch(words, fetch_missing=False)
