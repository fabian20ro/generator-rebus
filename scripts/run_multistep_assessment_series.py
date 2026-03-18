#!/usr/bin/env python3
"""Run the multistep assessment multiple times and summarize the results.

Usage:
    python3 scripts/run_multistep_assessment_series.py --runs 3 --description-prefix baseline
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULTS_PATH = PROJECT_ROOT / "generator" / "assessment" / "results.tsv"


def _read_results() -> list[dict]:
    if not RESULTS_PATH.exists():
        return []
    with open(RESULTS_PATH, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Repeat multistep assessment runs")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--dataset", default=str(PROJECT_ROOT / "generator" / "assessment" / "dataset.json"))
    parser.add_argument("--description-prefix", default="baseline")
    parser.add_argument("--temperature", type=float, default=None)
    args = parser.parse_args()

    if args.runs <= 0:
        print("No runs requested.")
        return

    for index in range(1, args.runs + 1):
        before_rows = _read_results()
        description = f"{args.description_prefix} run {index}/{args.runs}"
        cmd = [
            sys.executable,
            "-m",
            "generator.assessment.run_assessment",
            "--dataset",
            args.dataset,
            "--description",
            description,
        ]
        if args.temperature is not None:
            cmd.extend(["--temperature", str(args.temperature)])
        print(f"\n=== Running {description} ===")
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
        if result.returncode != 0:
            raise SystemExit(result.returncode)
        after_rows = _read_results()
        new_rows = after_rows[len(before_rows):]
        if len(new_rows) != 1:
            print(f"Expected exactly 1 appended row for {description}, found {len(new_rows)}")
            raise SystemExit(1)
        row = new_rows[0]
        print(
            "Recorded: "
            f"composite={row['composite']} "
            f"pass={row['pass_rate']} "
            f"sem={row['avg_semantic']} "
            f"reb={row['avg_rebus']} "
            f"status={row['status']}"
        )

    rows = _read_results()[-args.runs:]
    if not rows:
        print("No new rows appended.")
        return

    composites = [float(row["composite"]) for row in rows]
    passes = [float(row["pass_rate"]) for row in rows]
    semantics = [float(row["avg_semantic"]) for row in rows]
    rebus_scores = [float(row["avg_rebus"]) for row in rows]

    print("\n=== Series Summary ===")
    print(f"Runs:           {len(rows)}")
    print(f"Composite avg:  {sum(composites)/len(composites):.2f}")
    print(f"Pass rate avg:  {sum(passes)/len(passes):.3f}")
    print(f"Semantic avg:   {sum(semantics)/len(semantics):.2f}")
    print(f"Rebus avg:      {sum(rebus_scores)/len(rebus_scores):.2f}")
    print(f"Composite min:  {min(composites):.1f}")
    print(f"Composite max:  {max(composites):.1f}")


if __name__ == "__main__":
    main()
