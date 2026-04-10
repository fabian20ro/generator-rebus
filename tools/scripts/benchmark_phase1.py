#!/usr/bin/env python3
"""Benchmark Rust phase-1 candidate generation."""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rebus_generator.workflows.generate.service import _best_candidate, _metadata_by_word


def _ensure_rust_binary(repo_root: Path) -> None:
    subprocess.run(
        [
            "cargo",
            "build",
            "--release",
            "--manifest-path",
            str(repo_root / "engines" / "crossword-engine" / "Cargo.toml"),
        ],
        cwd=str(repo_root),
        check=True,
    )


def _time_call(fn) -> tuple[float, object]:
    started = time.perf_counter()
    result = fn()
    return time.perf_counter() - started, result


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark Rust phase-1 candidate generation.")
    parser.add_argument("--sizes", nargs="+", type=int, default=[7, 9, 11])
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--words", default="build/words.json")
    parser.add_argument("--output-dir", default="build/benchmarks/phase1")
    args = parser.parse_args()

    repo_root = REPO_ROOT
    words_path = repo_root / args.words
    raw_words = json.loads(words_path.read_text(encoding="utf-8"))
    metadata = _metadata_by_word(raw_words)
    _ensure_rust_binary(repo_root)

    rows = []
    for size in args.sizes:
        rust_elapsed, rust_candidate = _time_call(
            lambda: _best_candidate(
                size,
                "Benchmark",
                raw_words,
                random.Random(args.seed),
                words_path=words_path,
                word_metadata=metadata,
            )
        )
        row = {
            "size": size,
            "rust_elapsed_sec": round(rust_elapsed, 3),
            "rust_score": round(rust_candidate.score, 2),
            "rust_phase1_stats": rust_candidate.stats,
        }
        rows.append(row)
        sys.stdout.write(json.dumps(row, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    out_dir = repo_root / args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"benchmark_{int(time.time())}.json"
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    sys.stdout.write(f"saved {out_path}\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
