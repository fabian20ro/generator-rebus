"""Phase 1: Download words from Supabase and prepare for grid filling."""

from __future__ import annotations
import json
import sys
from rebus_generator.platform.persistence.supabase_ops import create_rebus_client as create_client
from rebus_generator.platform.config import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
from rebus_generator.domain.diacritics import normalize
from rebus_generator.platform.io.runtime_logging import log


def run(input_file: str, output_file: str, **kwargs) -> None:
    """Download all words from Supabase, normalize, deduplicate, save as JSON."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        log("Error: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in .env")
        sys.exit(1)

    log(f"Connecting to Supabase: {SUPABASE_URL[:30]}...")
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

    log("Downloading words...")
    # Fetch all words in batches (Supabase default limit is 1000)
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
        log(f"  Downloaded {len(all_words)} words...")

    log(f"Total raw words: {len(all_words)}")

    # Normalize and deduplicate
    seen: set[str] = set()
    unique_words: list[dict[str, str | int]] = []
    for row in all_words:
        original = row["word"].strip()
        if not original:
            continue
        normalized = normalize(original)
        if len(normalized) < 2:
            continue
        # Skip words with non-alpha characters
        if not normalized.isalpha():
            continue
        if normalized not in seen:
            seen.add(normalized)
            unique_words.append({
                "normalized": normalized,
                "original": original,
                "length": len(normalized),
                "rarity_level": row.get("rarity_level"),
                "word_type": row.get("type", ""),
            })

    log(f"Unique words after normalization: {len(unique_words)}")

    # Sort by length for easier inspection
    unique_words.sort(key=lambda w: (w["length"], w["normalized"]))

    # Length distribution
    length_dist: dict[int, int] = {}
    for w in unique_words:
        length_dist[w["length"]] = length_dist.get(w["length"], 0) + 1
    log("Length distribution:")
    for length in sorted(length_dist):
        log(f"  {length} letters: {length_dist[length]} words")

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(unique_words, f, ensure_ascii=False, indent=None)

    log(f"Saved to {output_file}")
