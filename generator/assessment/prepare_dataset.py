"""Build the multistep assessment dataset from March-17 batch mining.

Default composition:
- 30 low words (avg rebus < 5)
- 25 medium words (avg rebus 6-7, repeated, low variance)
- 15 high/control words (avg rebus 9-10, stable)

The selection is intentionally length-aware to avoid overfitting the eval set
to 2-3 letter crossword words just because they appear more often in batches.

Usage:
    python3 -m generator.assessment.prepare_dataset
    python3 -m generator.assessment.prepare_dataset --source-date 20260317
    python3 -m generator.assessment.prepare_dataset --fetch-dex
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..core.dex_cache import create_provider


OUTPUT_DIR = Path(__file__).parent
OUTPUT_PATH = OUTPUT_DIR / "dataset.json"
WORDS_PATH = Path("generator/output/words.json")
DEFAULT_SOURCE_DATE = "20260317"
DEFAULT_CANDIDATES_DIR = Path("build/assessment_candidates")


@dataclass(frozen=True)
class DatasetEntry:
    word: str
    display_word: str
    length: int
    word_type: str
    dex_definitions: str
    tier: str
    avg_rebus_score: float
    appearances: int
    min_rebus_score: int
    max_rebus_score: int


def _load_words_metadata() -> dict[str, dict]:
    with open(WORDS_PATH, "r", encoding="utf-8") as f:
        words = json.load(f)
    return {w["normalized"]: w for w in words}


def _load_existing_dex(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        entries = json.load(f)
    return {
        entry["word"]: entry.get("dex_definitions", "")
        for entry in entries
        if entry.get("word")
    }


def _read_tsv(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = []
        for row in reader:
            rows.append({
                "word": row["word"],
                "avg_score": float(row["avg_score"]),
                "hits": int(row["hits"]),
                "min_score": int(row["min_score"]),
                "max_score": int(row["max_score"]),
                "length": int(row["length"]),
                "verified_pass": int(row["verified_pass"]),
                "verified_fail": int(row["verified_fail"]),
            })
        return rows


def _sort_low(row: dict) -> tuple:
    short_penalty = 1 if row["length"] <= 3 else 0
    return (short_penalty, row["avg_score"], -row["hits"], row["min_score"], row["word"])


def _sort_medium(row: dict) -> tuple:
    short_penalty = 1 if row["length"] <= 3 else 0
    score_range = row["max_score"] - row["min_score"]
    return (short_penalty, score_range, -row["hits"], row["avg_score"], row["word"])


def _sort_high(row: dict) -> tuple:
    short_penalty = 1 if row["length"] <= 3 else 0
    return (short_penalty, -row["hits"], -row["avg_score"], row["word"])


def _pick_with_short_cap(
    candidates: list[dict],
    count: int,
    max_short: int,
    sort_key,
) -> list[dict]:
    ordered = sorted(candidates, key=sort_key)
    selected: list[dict] = []
    short_count = 0

    for row in ordered:
        is_short = row["length"] <= 3
        if is_short and short_count >= max_short:
            continue
        selected.append(row)
        if is_short:
            short_count += 1
        if len(selected) == count:
            return selected

    for row in ordered:
        if row in selected:
            continue
        selected.append(row)
        if len(selected) == count:
            return selected

    return selected


def _build_selection(
    low_rows: list[dict],
    high_rows: list[dict],
    *,
    low_count: int,
    medium_count: int,
    high_count: int,
    max_short_low: int,
    max_short_medium: int,
    max_short_high: int,
    medium_min_hits: int,
    medium_max_range: int,
) -> dict[str, list[dict]]:
    primary_low_candidates = [row for row in low_rows if row["avg_score"] < 5.0]
    secondary_low_candidates = [
        row for row in low_rows
        if row["avg_score"] >= 5.0 and row["min_score"] < 5
    ]
    medium_candidates = [
        row for row in low_rows
        if 6.0 <= row["avg_score"] < 8.0
        and row["hits"] >= medium_min_hits
        and (row["max_score"] - row["min_score"]) <= medium_max_range
    ]
    high_candidates = [
        row for row in high_rows
        if row["avg_score"] >= 9.0 and row["verified_fail"] == 0
    ]

    selection = {
        "low": _pick_with_short_cap(
            primary_low_candidates + secondary_low_candidates,
            low_count,
            max_short_low,
            _sort_low,
        ),
        "medium": _pick_with_short_cap(medium_candidates, medium_count, max_short_medium, _sort_medium),
        "high": _pick_with_short_cap(high_candidates, high_count, max_short_high, _sort_high),
    }

    expected = {"low": low_count, "medium": medium_count, "high": high_count}
    for tier_name, rows in selection.items():
        if len(rows) < expected[tier_name]:
            raise ValueError(
                f"Not enough {tier_name} candidates: wanted {expected[tier_name]}, got {len(rows)}"
            )
    return selection


def _reuse_or_fetch_dex(
    words: list[str],
    words_meta: dict[str, dict],
    existing_dex: dict[str, str],
    *,
    fetch_dex: bool,
) -> dict[str, str]:
    dex_defs = {word: existing_dex.get(word, "") for word in words}
    dex = create_provider()

    # Always refresh from local cache / Supabase when available so dataset.json
    # does not keep stale pre-expansion DEX text forever.
    for word in words:
        original = words_meta.get(word, {}).get("original", word.lower())
        live_defs = dex.lookup(word)
        if live_defs:
            dex_defs[word] = live_defs

    if not fetch_dex:
        return dex_defs

    missing = [word for word in words if not dex_defs.get(word)]
    if not missing:
        return dex_defs

    originals = {
        word: words_meta[word].get("original", word.lower())
        for word in missing
        if word in words_meta
    }
    dex.prefetch(missing, originals=originals)
    for word in missing:
        dex_defs[word] = dex.get(word, originals.get(word, word.lower())) or ""
    return dex_defs


def build_dataset(
    *,
    source_date: str = DEFAULT_SOURCE_DATE,
    candidates_dir: Path = DEFAULT_CANDIDATES_DIR,
    low_count: int = 30,
    medium_count: int = 25,
    high_count: int = 15,
    max_short_low: int = 8,
    max_short_medium: int = 10,
    max_short_high: int = 5,
    medium_min_hits: int = 2,
    medium_max_range: int = 2,
    fetch_dex: bool = False,
) -> list[DatasetEntry]:
    low_path = candidates_dir / f"{source_date}_low_words.tsv"
    high_path = candidates_dir / f"{source_date}_high_words.tsv"
    if not low_path.exists() or not high_path.exists():
        raise FileNotFoundError(
            f"Missing candidate TSVs for {source_date}. "
            f"Expected {low_path} and {high_path}."
        )

    words_meta = _load_words_metadata()
    existing_dex = _load_existing_dex(OUTPUT_PATH)
    low_rows = _read_tsv(low_path)
    high_rows = _read_tsv(high_path)
    selection = _build_selection(
        low_rows,
        high_rows,
        low_count=low_count,
        medium_count=medium_count,
        high_count=high_count,
        max_short_low=max_short_low,
        max_short_medium=max_short_medium,
        max_short_high=max_short_high,
        medium_min_hits=medium_min_hits,
        medium_max_range=medium_max_range,
    )

    chosen_words = [row["word"] for rows in selection.values() for row in rows]
    dex_defs = _reuse_or_fetch_dex(
        chosen_words,
        words_meta,
        existing_dex,
        fetch_dex=fetch_dex,
    )

    entries: list[DatasetEntry] = []
    for tier_name in ("low", "medium", "high"):
        for row in selection[tier_name]:
            meta = words_meta.get(row["word"])
            if not meta:
                continue
            entries.append(DatasetEntry(
                word=row["word"],
                display_word=meta.get("original", row["word"].lower()),
                length=meta.get("length", row["length"]),
                word_type=meta.get("word_type", ""),
                dex_definitions=dex_defs.get(row["word"], ""),
                tier=tier_name,
                avg_rebus_score=row["avg_score"],
                appearances=row["hits"],
                min_rebus_score=row["min_score"],
                max_rebus_score=row["max_score"],
            ))
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description="Build March-17 multistep assessment dataset")
    parser.add_argument("--source-date", default=DEFAULT_SOURCE_DATE)
    parser.add_argument("--candidates-dir", default=str(DEFAULT_CANDIDATES_DIR))
    parser.add_argument("--low-count", type=int, default=30)
    parser.add_argument("--medium-count", type=int, default=25)
    parser.add_argument("--high-count", type=int, default=15)
    parser.add_argument("--max-short-low", type=int, default=8)
    parser.add_argument("--max-short-medium", type=int, default=10)
    parser.add_argument("--max-short-high", type=int, default=5)
    parser.add_argument("--medium-min-hits", type=int, default=2)
    parser.add_argument("--medium-max-range", type=int, default=2)
    parser.add_argument("--fetch-dex", action="store_true")
    args = parser.parse_args()

    entries = build_dataset(
        source_date=args.source_date,
        candidates_dir=Path(args.candidates_dir),
        low_count=args.low_count,
        medium_count=args.medium_count,
        high_count=args.high_count,
        max_short_low=args.max_short_low,
        max_short_medium=args.max_short_medium,
        max_short_high=args.max_short_high,
        medium_min_hits=args.medium_min_hits,
        medium_max_range=args.medium_max_range,
        fetch_dex=args.fetch_dex,
    )

    tier_counts: dict[str, int] = {}
    short_counts: dict[str, int] = {}
    for entry in entries:
        tier_counts[entry.tier] = tier_counts.get(entry.tier, 0) + 1
        if entry.length <= 3:
            short_counts[entry.tier] = short_counts.get(entry.tier, 0) + 1

    print(f"Dataset: {len(entries)} words")
    for tier in ("low", "medium", "high"):
        print(
            f"  {tier}: {tier_counts.get(tier, 0)} "
            f"(short={short_counts.get(tier, 0)})"
        )

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump([asdict(entry) for entry in entries], f, ensure_ascii=False, indent=2)
    print(f"Written to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
