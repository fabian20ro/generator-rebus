#!/usr/bin/env python3
"""Run the short-word prompt benchmark through the assessment pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rebus_generator.evaluation.assessment.pipeline import run_assessment, write_result_json
from rebus_generator.evaluation.short_word_benchmark import (
    SHORT_WORD_PROMPT_BENCHMARK_RUNS,
    write_short_word_prompt_benchmark_dataset,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run short-word prompt benchmark")
    parser.add_argument("--runs", type=int, default=SHORT_WORD_PROMPT_BENCHMARK_RUNS)
    parser.add_argument(
        "--dataset-out",
        default="build/evaluation/short_word_prompt_benchmark/dataset.json",
    )
    parser.add_argument(
        "--json-out",
        default="build/evaluation/short_word_prompt_benchmark/result.json",
    )
    parser.add_argument("--no-run", action="store_true", help="Only write the dataset")
    args = parser.parse_args()

    dataset_path = write_short_word_prompt_benchmark_dataset(Path(args.dataset_out), runs=args.runs)
    if args.no_run:
        print(dataset_path)
        return
    result = run_assessment(dataset_path)
    write_result_json(result, Path(args.json_out))
    print(args.json_out)


if __name__ == "__main__":
    main()
