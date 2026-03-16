"""Bulk download definitions from dexonline.ro into Supabase cache.

Usage:
    python -m generator.dex_download [--limit N] [--delay 2.0] [--words-file path]

Respects robots.txt Crawl-delay: 2 by checking the last fetched_at timestamp
in the database before each request.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys

from supabase import create_client

from .config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
from .core.dex_cache import (
    _CRAWL_DELAY,
    _wait_for_crawl_delay,
    fetch_from_dexonline,
    get_cached_words,
    parse_definitions_from_html,
    store,
)
from .core.diacritics import normalize


_interrupted = False


def _handle_sigint(sig, frame):
    global _interrupted
    if _interrupted:
        print("\nForce quit.")
        sys.exit(1)
    _interrupted = True
    print("\nInterrupted — finishing current word, then stopping...")


def _load_words(words_file: str | None) -> list[dict]:
    """Load words from JSON file or download from Supabase."""
    if words_file:
        with open(words_file, "r", encoding="utf-8") as f:
            return json.load(f)

    # Download from Supabase (same as download phase)
    print("No --words-file specified. Downloading from Supabase...")
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    all_words = []
    offset = 0
    batch_size = 1000
    while True:
        response = (client.table("words")
                    .select("word,rarity_level,type")
                    .range(offset, offset + batch_size - 1)
                    .execute())
        batch = response.data
        if not batch:
            break
        all_words.extend(batch)
        offset += batch_size
        print(f"  Downloaded {len(all_words)} words...")

    # Normalize and deduplicate
    seen: set[str] = set()
    unique: list[dict] = []
    for row in all_words:
        original = row["word"].strip()
        if not original:
            continue
        normalized = normalize(original)
        if len(normalized) < 2 or not normalized.isalpha():
            continue
        if normalized not in seen:
            seen.add(normalized)
            unique.append({"normalized": normalized, "original": original})

    print(f"Total unique words: {len(unique)}")
    return unique


def main():
    parser = argparse.ArgumentParser(description="Download dexonline definitions into Supabase cache.")
    parser.add_argument("--limit", type=int, default=0, help="Max words to download (0=all)")
    parser.add_argument("--delay", type=float, default=_CRAWL_DELAY, help="Crawl delay in seconds (default: 2.0)")
    parser.add_argument("--words-file", type=str, default=None, help="Path to words.json (optional)")
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
        sys.exit(1)

    signal.signal(signal.SIGINT, _handle_sigint)

    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    # Load word list
    words = _load_words(args.words_file)

    # Get already-cached words
    print("Checking cache for already-downloaded words...")
    cached = get_cached_words(client)
    print(f"Already cached: {len(cached)} words")

    # Filter to only missing words
    to_download = [w for w in words if normalize(w.get("normalized", w.get("word", ""))) not in cached]
    if args.limit > 0:
        to_download = to_download[:args.limit]

    print(f"Words to download: {len(to_download)}")
    if not to_download:
        print("Nothing to do.")
        return

    # Download
    stats = {"ok": 0, "not_found": 0, "error": 0}
    for i, word_rec in enumerate(to_download):
        if _interrupted:
            break

        normalized = word_rec.get("normalized", normalize(word_rec.get("word", "")))
        original = word_rec.get("original", word_rec.get("word", ""))

        _wait_for_crawl_delay(client, args.delay)
        html, status = fetch_from_dexonline(original)
        store(client, normalized, original, html, status)
        stats[status] = stats.get(status, 0) + 1

        n_defs = len(parse_definitions_from_html(html)) if html else 0
        print(f"  [{i + 1}/{len(to_download)}] {original} → {status} ({n_defs} definitions)")

    # Summary
    print(f"\nDone! ok={stats['ok']}, not_found={stats['not_found']}, error={stats['error']}")
    if _interrupted:
        print("(Interrupted — run again to continue where you left off)")


if __name__ == "__main__":
    main()
