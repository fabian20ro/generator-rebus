#!/usr/bin/env python3
"""Build a 100/100/100 prompt-eval dataset from run metrics."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rebus_generator.evaluation.prompt_eval import (
    build_prompt_eval_dataset,
    write_prompt_eval_dataset,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build prompt eval dataset from metrics.json files")
    parser.add_argument("metrics", nargs="+", help="metrics.json files")
    parser.add_argument("--words", default="build/words.json")
    parser.add_argument("--bucket-size", type=int, default=100)
    parser.add_argument("--output", default="build/evaluation/prompt_eval/dataset.json")
    args = parser.parse_args()

    rows = build_prompt_eval_dataset(
        [Path(path) for path in args.metrics],
        words_path=Path(args.words),
        bucket_size=max(1, args.bucket_size),
    )
    output = write_prompt_eval_dataset(rows, Path(args.output))
    print(output)


if __name__ == "__main__":
    main()
