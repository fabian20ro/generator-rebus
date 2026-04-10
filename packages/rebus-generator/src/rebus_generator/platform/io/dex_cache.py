"""Dexonline.ro definition provider with multi-layer caching.

Architecture (4 cache layers):
  L1 — In-memory dict (per DexProvider instance, i.e. per puzzle run)
  L2 — Local disk cache (gitignored, shared across local runs)
  L3 — Supabase ``dex_definitions`` table (persistent, shared across projects)
  L4 — dexonline.ro HTTP fetch (origin, with crawl-delay and exponential backoff)

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

import json
import re
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from rebus_generator.domain.diacritics import normalize
from .runtime_logging import audit, log, utc_timestamp

_USER_AGENT = (
    "Mozilla/5.0 (compatible; generator-rebus/1.0; "
    "+https://github.com/fabian20ro/generator-rebus)"
)
_CRAWL_DELAY = 3.0
_MAX_RETRIES = 3
_MAX_DEFS = 8
_SB_BATCH_CHUNK_SIZE = 200
_REDIRECT_TARGET_DEFS = 2
_SHORT_DEF_WORD_LIMIT = 10
_DEF_CONTAINER_TAGS = {"span", "div", "p", "b", "i", "a", "em", "strong", "sup", "sub"}
_DEFAULT_LOCAL_CACHE_DIR = Path(".cache/dex_definitions")

_REDIRECT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("diminutiv", re.compile(r"^diminutiv al lui\s+(.+?)(?:[.;:,)]|$)", re.IGNORECASE)),
    ("augmentativ", re.compile(r"^augmentativ al lui\s+(.+?)(?:[.;:,)]|$)", re.IGNORECASE)),
    ("plural", re.compile(r"^pluralul lui\s+(.+?)(?:[.;:,)]|$)", re.IGNORECASE)),
    ("singular", re.compile(r"^singularul lui\s+(.+?)(?:[.;:,)]|$)", re.IGNORECASE)),
    ("feminin", re.compile(r"^(?:femininul lui|form[ăa] feminin[ăa] a lui)\s+(.+?)(?:[.;:,)]|$)", re.IGNORECASE)),
    ("masculin", re.compile(r"^(?:masculinul lui|form[ăa] masculin[ăa] a lui)\s+(.+?)(?:[.;:,)]|$)", re.IGNORECASE)),
    ("participiu", re.compile(r"^(?:participiul al lui|participiul trecut al lui)\s+(.+?)(?:[.;:,)]|$)", re.IGNORECASE)),
    ("gerunziu", re.compile(r"^gerunziul lui\s+(.+?)(?:[.;:,)]|$)", re.IGNORECASE)),
    ("varianta", re.compile(r"^(?:variant[ăa](?: grafic[ăa]| fonetic[ăa])? a lui|variant[ăa] al lui)\s+(.+?)(?:[.;:,)]|$)", re.IGNORECASE)),
    ("vezi", re.compile(r"^(?:vezi|v\.)\s+(.+?)(?:[.;:,)]|$)", re.IGNORECASE)),
    ("acelasi", re.compile(r"^acela(?:ș|s)i lucru cu\s+(.+?)(?:[.;:,)]|$)", re.IGNORECASE)),
    ("termen", re.compile(r"^(?:termen pentru|nume pentru)\s+(.+?)(?:[.;:,)]|$)", re.IGNORECASE)),
]

_ACTION_PATTERN = re.compile(
    r"^ac(?:ț|t)iunea de a(?:\s+\(se\)|\s+se)?\s+([^\s;:,(=]+)",
    re.IGNORECASE,
)
_FACT_PATTERN = re.compile(
    r"^faptul de a(?:\s+\(se\)|\s+se)?\s+([^\s;:,(=]+)",
    re.IGNORECASE,
)
_PROPERTY_PATTERN = re.compile(
    r"^proprietatea de a fi\s+([^\s;:,(=]+)",
    re.IGNORECASE,
)
_UNIT_FRACTION_PATTERN = re.compile(
    r"^a\s+[^\s;:,(=]+\s+parte dintr-(?:un|o)\s+([^\s;:,(=]+)",
    re.IGNORECASE,
)
_USAGE_CATEGORY_MARKERS = (
    "ARHAISM",
    "REGIONAL",
    "JARGON",
    "ARGOU",
    "TEHNIC",
    "POPULAR",
    "FAMILIAR",
    "LIVRESC",
    "INVECHIT",
)


# ---------------------------------------------------------------------------
# HTML parsing — extract plain-text definitions from dexonline synthesis tab
# ---------------------------------------------------------------------------

class _DefinitionExtractor(HTMLParser):
    """Extract text from <span class="tree-def html"> elements."""

    def __init__(self):
        super().__init__()
        self._in_tree_def = False
        self._tree_depth = 0
        self._tree_current: list[str] = []
        self.tree_definitions: list[str] = []
        self._callout_depth = 0
        self._def_wrapper_depth = 0
        self._in_callout_heading = False
        self._callout_heading_current: list[str] = []
        self._current_category = ""
        self._in_categorized_def = False
        self._categorized_depth = 0
        self._categorized_current: list[str] = []
        self.categorized_definitions: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = dict(attrs).get("class", "") or ""
        if tag == "span":
            if "tree-def" in classes:
                self._in_tree_def = True
                self._tree_depth = 1
                self._tree_current = []
                return
            if self._def_wrapper_depth > 0 and "def" in classes.split():
                self._in_categorized_def = True
                self._categorized_depth = 1
                self._categorized_current = []
                return
        if tag == "div":
            if "callout-secondary" in classes:
                self._callout_depth = 1
                self._current_category = ""
            elif self._callout_depth > 0:
                self._callout_depth += 1
            if "defWrapper" in classes:
                self._def_wrapper_depth += 1
        elif tag == "h3" and self._callout_depth > 0:
            self._in_callout_heading = True
            self._callout_heading_current = []

        if self._in_tree_def:
            self._tree_depth += 1 if tag in _DEF_CONTAINER_TAGS else 0
        if self._in_categorized_def:
            self._categorized_depth += 1 if tag in _DEF_CONTAINER_TAGS else 0

    def handle_endtag(self, tag: str) -> None:
        if self._in_callout_heading and tag == "h3":
            self._current_category = " ".join("".join(self._callout_heading_current).split()).strip()
            self._in_callout_heading = False
            self._callout_heading_current = []

        if self._in_tree_def:
            if tag in _DEF_CONTAINER_TAGS:
                self._tree_depth -= 1
                if self._tree_depth <= 0:
                    text = " ".join("".join(self._tree_current).split()).strip()
                    if text:
                        self.tree_definitions.append(text)
                    self._in_tree_def = False

        if self._in_categorized_def:
            if tag in _DEF_CONTAINER_TAGS:
                self._categorized_depth -= 1
                if self._categorized_depth <= 0:
                    text = " ".join("".join(self._categorized_current).split()).strip()
                    if text and _is_usage_category(self._current_category):
                        self.categorized_definitions.append(f"{self._current_category}: {text}")
                    self._in_categorized_def = False

        if tag == "div":
            if self._callout_depth > 0:
                self._callout_depth -= 1
                if self._callout_depth == 0:
                    self._in_callout_heading = False
                    self._callout_heading_current = []
            if self._def_wrapper_depth > 0:
                self._def_wrapper_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_callout_heading:
            self._callout_heading_current.append(data)
        if self._in_tree_def:
            self._tree_current.append(data)
        if self._in_categorized_def:
            self._categorized_current.append(data)


def _is_usage_category(category: str) -> bool:
    if not category:
        return False
    normalized_category = normalize(category)
    return any(marker in normalized_category for marker in _USAGE_CATEGORY_MARKERS)


def parse_definitions_from_html(html: str) -> list[str]:
    """Extract plain-text definitions from dexonline HTML."""
    parser = _DefinitionExtractor()
    parser.feed(html)
    seen: set[str] = set()
    result: list[str] = []
    for d in parser.tree_definitions + parser.categorized_definitions:
        if d not in seen:
            seen.add(d)
            result.append(d)
    return result


def _format_definitions(defs: list[str]) -> str:
    """Format a list of definition strings into bullet-point text."""
    return "\n".join(f"- {d}" for d in defs[:_MAX_DEFS])


def _definition_word_count(definition: str) -> int:
    return len([part for part in definition.split() if part])


def _extract_redirect_target(definition: str) -> tuple[str, str] | None:
    text = definition.strip()
    if not text:
        return None
    for kind, pattern in _REDIRECT_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        target = match.group(1).strip(" \"'„”()[]")
        target = re.split(r"\s+(?:sau|ori)\s+", target, maxsplit=1, flags=re.IGNORECASE)[0]
        target = re.split(r"\s*[-–]\s*", target, maxsplit=1)[0]
        target = target.strip(" \"'„”()[]")
        if target:
            return kind, target
    return None


def _clean_target_text(target: str) -> str:
    cleaned = target.strip(" \"'„”()[]")
    cleaned = re.sub(r"\([^)]*\)", "", cleaned).strip()
    cleaned = re.split(r"\s*=\s*", cleaned, maxsplit=1)[0]
    cleaned = re.split(r"\s*[/|]\s*", cleaned, maxsplit=1)[0]
    cleaned = cleaned.strip(" \"'„”()[]")
    cleaned = cleaned.rstrip(".,;:!?")
    return cleaned


def _extract_base_lookup(definition: str) -> tuple[str, str] | None:
    text = definition.strip()
    if not text:
        return None

    redirect = _extract_redirect_target(text)
    if redirect is not None:
        kind, target = redirect
        cleaned = _clean_target_text(target)
        if cleaned:
            return kind, cleaned

    for kind, pattern in (
        ("actiune", _ACTION_PATTERN),
        ("fapt", _FACT_PATTERN),
        ("proprietate", _PROPERTY_PATTERN),
        ("unitate", _UNIT_FRACTION_PATTERN),
    ):
        match = pattern.match(text)
        if not match:
            continue
        cleaned = _clean_target_text(match.group(1))
        if cleaned:
            return kind, cleaned

    words = re.findall(r"[A-Za-zĂÂÎȘŞȚŢăâîșşțţ-]+", text)
    if len(words) == 1:
        cleaned = _clean_target_text(words[0])
        if cleaned:
            return "sinonim_scurt", cleaned

    return None


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
    for i in range(0, len(words), _SB_BATCH_CHUNK_SIZE):
        chunk = words[i:i + _SB_BATCH_CHUNK_SIZE]
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
    now = utc_timestamp()
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


def _local_cache_path(cache_dir: Path | None, normalized: str) -> Path | None:
    if cache_dir is None:
        return None
    return cache_dir / f"{normalized}.json"


def _local_lookup_single(cache_dir: Path | None, normalized: str) -> tuple[str | None, bool]:
    path = _local_cache_path(cache_dir, normalized)
    if path is None or not path.exists():
        return None, False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, False
    if data.get("status") != "ok" or not data.get("html"):
        return None, True
    return data["html"], True


def _local_store(cache_dir: Path | None, word: str, original: str, html: str, status: str) -> None:
    path = _local_cache_path(cache_dir, word)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "word": word,
            "original": original,
            "status": status,
            "html": html,
            "fetched_at": utc_timestamp(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# DexProvider — multi-layer cache
# ---------------------------------------------------------------------------

class DexProvider:
    """Multi-layer definition cache: memory -> local disk -> Supabase -> dexonline.ro.

    One instance per puzzle run. Thread-unsafe (single-threaded pipeline).
    """

    _last_fetch_time: float = 0.0  # class-level: shared across instances

    def __init__(self, supabase_client=None, local_cache_dir: Path | str | None = _DEFAULT_LOCAL_CACHE_DIR):
        self._sb = supabase_client
        self._local_cache_dir = Path(local_cache_dir) if local_cache_dir is not None else None
        # L1: normalized word -> formatted definitions string (or None for known-missing)
        self._memory: dict[str, str | None] = {}
        self._uncertain_short_definitions: dict[str, dict[str, str]] = {}

    # -- Public API --------------------------------------------------------

    def get(self, word: str, original: str = "") -> str | None:
        """Get formatted definitions, fetching from dexonline if needed.

        Cache resolution order: L1 memory -> L2 local disk -> L3 Supabase -> L4 dexonline.
        """
        norm = normalize(word)

        # L1: in-memory
        if norm in self._memory:
            return self._memory[norm]

        # L2: local disk cache
        html, found = _local_lookup_single(self._local_cache_dir, norm)
        if found:
            return self._parse_and_cache(norm, html)

        # L3: Supabase
        if self._sb is not None:
            html, found = _sb_lookup_single(self._sb, norm)
            if found:
                _local_store(self._local_cache_dir, norm, original or word, html or "", "ok" if html else "not_found")
                formatted = self._parse_and_cache(norm, html)
                return formatted

        # L4: dexonline.ro
        return self._fetch_and_store(norm, original or word)

    def lookup(self, word: str) -> str | None:
        """Read-only lookup: L1 + local disk + Supabase only, no HTTP fetch."""
        norm = normalize(word)

        if norm in self._memory:
            return self._memory[norm]

        html, found = _local_lookup_single(self._local_cache_dir, norm)
        if found:
            return self._parse_and_cache(norm, html)

        if self._sb is not None:
            html, found = _sb_lookup_single(self._sb, norm)
            if found:
                _local_store(self._local_cache_dir, norm, norm, html or "", "ok" if html else "not_found")
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
        2. Checks L2 local disk cache for the rest.
        3. Batch-queries L3 (Supabase) for the remaining words.
        4. If fetch_missing=True, fetches L4 (dexonline) for the rest.

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

        # L2 local disk cache
        remaining: list[str] = []
        for norm in to_query:
            html, found = _local_lookup_single(self._local_cache_dir, norm)
            if found:
                self._parse_and_cache(norm, html)
            else:
                remaining.append(norm)
        to_query = remaining

        # L3 batch query
        if to_query and self._sb is not None:
            sb_results = _sb_lookup_batch(self._sb, to_query)
            for norm, html in sb_results.items():
                _local_store(
                    self._local_cache_dir,
                    norm,
                    normalized_map.get(norm, norm),
                    html or "",
                    "ok" if html else "not_found",
                )
                self._parse_and_cache(norm, html)
            # Remove found words from to_query
            to_query = [n for n in to_query if n not in sb_results]

        # L4: fetch missing from dexonline one-by-one
        if fetch_missing and to_query:
            log(f"  DEX: fetching {len(to_query)} words from dexonline.ro...")
            for norm in to_query:
                original = normalized_map.get(norm, norm)
                self._fetch_and_store(norm, original)

        # Report
        cached = sum(1 for w in words if self._memory.get(normalize(w)) is not None)
        total_words = len(set(normalize(w) for w in words))
        if cached:
            log(f"  DEX cache: {cached}/{total_words} words have definitions")

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

    def uncertain_short_definitions(self) -> list[dict[str, str]]:
        """Return short single-definition rows that look like unresolved redirects."""
        return list(self._uncertain_short_definitions.values())

    @classmethod
    def for_puzzle(cls, puzzle) -> DexProvider:
        """Create a provider from env config and prefetch all puzzle words.

        Accepts a WorkingPuzzle (lazy import to avoid circular deps).
        Consolidates the repeated create-provider-and-prefetch pattern.
        """
        from rebus_generator.domain.pipeline_state import all_working_clues
        dex = create_provider()
        clues = list(all_working_clues(puzzle))
        if clues:
            dex.prefetch(
                [c.word_normalized for c in clues],
                originals={c.word_normalized: c.word_original for c in clues if c.word_original},
            )
        return dex

    # -- Internal ----------------------------------------------------------

    def _parse_and_cache(self, norm: str, html: str | None) -> str | None:
        """Parse HTML and store formatted result in L1. Returns formatted or None."""
        if not html:
            self._memory[norm] = None
            return None
        defs = parse_definitions_from_html(html)
        defs = self._expand_redirect_definitions(norm, defs)
        if defs:
            formatted = _format_definitions(defs)
            self._memory[norm] = formatted
            return formatted
        self._memory[norm] = None
        return None

    def _expand_redirect_definitions(self, norm: str, defs: list[str]) -> list[str]:
        if not defs:
            return defs
        first_definition = defs[0].strip()
        if _definition_word_count(first_definition) >= _SHORT_DEF_WORD_LIMIT:
            return list(defs)

        expansion = _extract_base_lookup(first_definition)
        if expansion is None:
            self._remember_uncertain_short_definition(norm, first_definition)
            return list(defs)

        _kind, target = expansion
        expanded: list[str] = []
        seen_expanded: set[str] = set()

        labeled = f'Definiție directă DEX pentru „{norm}”: {first_definition}'
        expanded.append(labeled)
        seen_expanded.add(labeled)

        target_norm = normalize(target)
        if target_norm and target_norm != norm:
            base_defs = self._lookup_plain_definitions(target_norm, target)
            for base_def in base_defs[:_REDIRECT_TARGET_DEFS]:
                labeled_base = f"Sens bază pentru „{target}”: {base_def}"
                if labeled_base not in seen_expanded:
                    expanded.append(labeled_base)
                    seen_expanded.add(labeled_base)

        for index, definition in enumerate(defs):
            text = definition.strip()
            if index == 0:
                continue
            if text not in seen_expanded:
                expanded.append(text)
                seen_expanded.add(text)
        return expanded

    def _lookup_plain_definitions(self, norm: str, original: str) -> list[str]:
        if norm in self._memory and self._memory[norm]:
            return [
                line[2:].strip()
                for line in self._memory[norm].splitlines()
                if line.startswith("- ")
            ]

        html, found = _local_lookup_single(self._local_cache_dir, norm)
        if found:
            return parse_definitions_from_html(html or "")

        if self._sb is not None:
            html, found = _sb_lookup_single(self._sb, norm)
            if found:
                _local_store(self._local_cache_dir, norm, original, html or "", "ok" if html else "not_found")
        if not found:
            self._respect_crawl_delay()
            html, status = fetch_from_dexonline(original)
            DexProvider._last_fetch_time = time.monotonic()
            _local_store(self._local_cache_dir, norm, original, html, status)
            if self._sb is not None:
                _sb_store(self._sb, norm, original, html, status)
            if status != "ok":
                return []
        if not html:
            return []
        return parse_definitions_from_html(html)

    def _remember_uncertain_short_definition(self, norm: str, definition: str) -> None:
        entry = {"word": norm, "definition": definition}
        if norm in self._uncertain_short_definitions:
            return
        self._uncertain_short_definitions[norm] = entry
        log(f"    [DEX short/uncertain] {norm}: {definition}")
        audit(
            "dex_short_definition_detected",
            component="dex_cache",
            payload={"word": norm, "definition": definition},
        )

    def _respect_crawl_delay(self) -> None:
        """Sleep if needed to respect robots.txt Crawl-delay."""
        if DexProvider._last_fetch_time > 0:
            elapsed = time.monotonic() - DexProvider._last_fetch_time
            remaining = _CRAWL_DELAY - elapsed
            if remaining > 0:
                time.sleep(remaining)

    def _fetch_and_store(self, norm: str, original: str) -> str | None:
        """Fetch from dexonline, store in local disk + Supabase + L1. Returns formatted or None."""
        self._respect_crawl_delay()
        html, status = fetch_from_dexonline(original)
        DexProvider._last_fetch_time = time.monotonic()
        log(f"    [DEX] {original} -> {status}")

        _local_store(self._local_cache_dir, norm, original, html, status)

        # Store in L3 (Supabase)
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
