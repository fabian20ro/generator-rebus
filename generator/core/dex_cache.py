"""Dexonline.ro definition cache backed by Supabase.

Downloads and caches raw HTML from dexonline.ro/definitie/{word}.
Definitions are parsed from HTML on the fly using stdlib html.parser.
Respects robots.txt Crawl-delay: 2.
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
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for d in parser.definitions:
        if d not in seen:
            seen.add(d)
            result.append(d)
    return result


# ---------------------------------------------------------------------------
# Fetch from dexonline.ro
# ---------------------------------------------------------------------------

def fetch_from_dexonline(original: str) -> tuple[str, str]:
    """Fetch definition page from dexonline.ro.

    Returns (html, status) where status is 'ok', 'not_found', or 'error'.
    """
    url = f"https://dexonline.ro/definitie/{urllib.request.quote(original.lower())}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
            # Check if the page actually has definitions
            if "meaningContainer" in html or "tree-def" in html:
                return html, "ok"
            return html, "not_found"
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "", "not_found"
        return "", "error"
    except Exception:
        return "", "error"


# ---------------------------------------------------------------------------
# Supabase cache operations
# ---------------------------------------------------------------------------

def lookup(client, word: str) -> str | None:
    """Look up cached definitions for a word. Returns formatted text or None."""
    normalized = normalize(word)
    try:
        resp = (client.table("dex_definitions")
                .select("html, status")
                .eq("word", normalized)
                .limit(1)
                .execute())
    except Exception:
        return None
    if not resp.data:
        return None
    row = resp.data[0]
    if row["status"] != "ok" or not row.get("html"):
        return None
    defs = parse_definitions_from_html(row["html"])
    if not defs:
        return None
    return "\n".join(f"- {d}" for d in defs[:8])


def lookup_batch(client, words: list[str]) -> dict[str, str]:
    """Look up cached definitions for multiple words. Returns {normalized: formatted_text}."""
    if not words:
        return {}
    normalized_words = [normalize(w) for w in words]
    result: dict[str, str] = {}
    # Supabase IN filter — batch in chunks of 200
    for i in range(0, len(normalized_words), 200):
        chunk = normalized_words[i:i + 200]
        try:
            resp = (client.table("dex_definitions")
                    .select("word, html, status")
                    .in_("word", chunk)
                    .execute())
        except Exception:
            continue
        for row in resp.data:
            if row["status"] != "ok" or not row.get("html"):
                continue
            defs = parse_definitions_from_html(row["html"])
            if defs:
                result[row["word"]] = "\n".join(f"- {d}" for d in defs[:8])
    return result


def store(client, word: str, original: str, html: str, status: str) -> None:
    """Store a definition in the cache (upsert)."""
    normalized = normalize(word)
    now = datetime.now(timezone.utc).isoformat()
    client.table("dex_definitions").upsert({
        "word": normalized,
        "original": original,
        "html": html,
        "status": status,
        "fetched_at": now,
    }).execute()


def get_cached_words(client) -> set[str]:
    """Get the set of all words already in the cache."""
    all_words: set[str] = set()
    offset = 0
    batch_size = 1000
    while True:
        resp = (client.table("dex_definitions")
                .select("word")
                .range(offset, offset + batch_size - 1)
                .execute())
        if not resp.data:
            break
        for row in resp.data:
            all_words.add(row["word"])
        if len(resp.data) < batch_size:
            break
        offset += batch_size
    return all_words


def _wait_for_crawl_delay(client, delay: float = _CRAWL_DELAY) -> None:
    """Wait until at least `delay` seconds have passed since the last fetch."""
    try:
        resp = (client.table("dex_definitions")
                .select("fetched_at")
                .not_.is_("fetched_at", "null")
                .order("fetched_at", desc=True)
                .limit(1)
                .execute())
    except Exception:
        time.sleep(delay)
        return
    if not resp.data:
        return
    last_fetched = resp.data[0]["fetched_at"]
    # Parse ISO timestamp
    try:
        if last_fetched.endswith("Z"):
            last_fetched = last_fetched[:-1] + "+00:00"
        last_dt = datetime.fromisoformat(last_fetched)
        now = datetime.now(timezone.utc)
        elapsed = (now - last_dt).total_seconds()
        remaining = delay - elapsed
        if remaining > 0:
            time.sleep(remaining)
    except Exception:
        time.sleep(delay)


def download_if_missing(
    client, word: str, original: str, delay: float = _CRAWL_DELAY,
) -> str | None:
    """Download and cache a word's definition if not already cached.

    Returns formatted definition text or None.
    """
    existing = lookup(client, word)
    if existing is not None:
        return existing

    _wait_for_crawl_delay(client, delay)
    html, status = fetch_from_dexonline(original)
    store(client, word, original, html, status)

    if status == "ok" and html:
        defs = parse_definitions_from_html(html)
        if defs:
            return "\n".join(f"- {d}" for d in defs[:8])
    return None
