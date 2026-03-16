"""Build a stratified assessment dataset from word_difficulty.json + DEX cache.

Run once to create dataset.json. After creation, the dataset is fixed —
do not regenerate unless the evaluation criteria fundamentally change.

Usage:
    python3 -m generator.assessment.prepare_dataset
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from ..core.dex_cache import DexProvider, create_provider


@dataclass(frozen=True)
class DatasetEntry:
    word: str
    display_word: str
    length: int
    word_type: str
    dex_definitions: str
    historical_pass_rate: float
    tier: str


TIERS = {
    "easy": 20,
    "medium": 20,
    "hard": 20,
    "short": 20,
    "rare": 20,
}

OUTPUT_DIR = Path(__file__).parent
DIFFICULTY_PATH = Path("generator/output/word_difficulty.json")
WORDS_PATH = Path("generator/output/words.json")


def _load_difficulty() -> dict[str, dict]:
    with open(DIFFICULTY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_words_metadata() -> dict[str, dict]:
    with open(WORDS_PATH, "r", encoding="utf-8") as f:
        words = json.load(f)
    return {w["normalized"]: w for w in words}


def _pass_rate(entry: dict) -> float:
    attempts = entry.get("attempts", 0)
    if attempts == 0:
        return 0.0
    return entry.get("successes", 0) / attempts


def _stratify(
    difficulty: dict[str, dict],
    words_meta: dict[str, dict],
    rng: random.Random,
) -> dict[str, list[str]]:
    """Assign words to tiers based on pass rate, length, and rarity."""
    # Only consider words with 3+ attempts for statistical significance
    eligible = {
        word: data
        for word, data in difficulty.items()
        if data.get("attempts", 0) >= 3 and word in words_meta
    }

    # Pre-compute pass rates
    rates = {word: _pass_rate(data) for word, data in eligible.items()}

    # Tier buckets
    easy_pool: list[str] = []
    medium_pool: list[str] = []
    hard_pool: list[str] = []
    short_pool: list[str] = []
    rare_pool: list[str] = []

    for word, rate in rates.items():
        meta = words_meta[word]
        length = meta.get("length", len(word))
        rarity = meta.get("rarity_level", 1)

        # Short tier: 2-3 letter words
        if length <= 3:
            short_pool.append(word)
            continue

        # Rare tier: rarity >= 3
        if rarity >= 3:
            rare_pool.append(word)
            continue

        # Pass rate tiers (4+ letters, rarity < 3)
        if rate > 0.8:
            easy_pool.append(word)
        elif rate >= 0.3:
            medium_pool.append(word)
        else:
            hard_pool.append(word)

    # Sample from each pool
    result: dict[str, list[str]] = {}
    for tier_name, pool in [
        ("easy", easy_pool),
        ("medium", medium_pool),
        ("hard", hard_pool),
        ("short", short_pool),
        ("rare", rare_pool),
    ]:
        count = TIERS[tier_name]
        rng.shuffle(pool)
        result[tier_name] = pool[:count]

    return result


def _fetch_dex_definitions(
    words: list[str],
    words_meta: dict[str, dict],
    dex: DexProvider,
) -> dict[str, str]:
    """Fetch DEX definitions for all assessment words."""
    originals = {
        w: words_meta[w].get("original", w.lower())
        for w in words
        if w in words_meta
    }
    dex.prefetch(words, originals=originals)
    return {w: dex.get(w, originals.get(w, w.lower())) or "" for w in words}


def build_dataset(seed: int = 42) -> list[DatasetEntry]:
    """Build the stratified assessment dataset."""
    rng = random.Random(seed)
    difficulty = _load_difficulty()
    words_meta = _load_words_metadata()
    tiers = _stratify(difficulty, words_meta, rng)

    all_words = [w for tier_words in tiers.values() for w in tier_words]
    print(f"Fetching DEX definitions for {len(all_words)} words...")
    dex = create_provider()
    dex_defs = _fetch_dex_definitions(all_words, words_meta, dex)

    entries: list[DatasetEntry] = []
    for tier_name, tier_words in tiers.items():
        for word in tier_words:
            meta = words_meta.get(word, {})
            diff = difficulty.get(word, {})
            entries.append(DatasetEntry(
                word=word,
                display_word=meta.get("original", word.lower()),
                length=meta.get("length", len(word)),
                word_type=meta.get("word_type", ""),
                dex_definitions=dex_defs.get(word, ""),
                historical_pass_rate=_pass_rate(diff),
                tier=tier_name,
            ))

    return entries


def main() -> None:
    entries = build_dataset()

    # Report
    tier_counts: dict[str, int] = {}
    for entry in entries:
        tier_counts[entry.tier] = tier_counts.get(entry.tier, 0) + 1
    print(f"\nDataset: {len(entries)} words")
    for tier, count in sorted(tier_counts.items()):
        print(f"  {tier}: {count}")
    dex_coverage = sum(1 for e in entries if e.dex_definitions)
    print(f"  DEX coverage: {dex_coverage}/{len(entries)}")

    # Write
    output_path = OUTPUT_DIR / "dataset.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump([asdict(e) for e in entries], f, ensure_ascii=False, indent=2)
    print(f"\nWritten to {output_path}")


if __name__ == "__main__":
    main()
