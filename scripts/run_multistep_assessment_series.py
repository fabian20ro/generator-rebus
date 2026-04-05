#!/usr/bin/env python3
"""Run the multistep assessment multiple times and summarize the results.

Usage:
    python3 scripts/run_multistep_assessment_series.py --runs 3 --description-prefix baseline
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from generator.core.runtime_logging import install_process_logging, log, path_timestamp

RESULTS_PATH = PROJECT_ROOT / "generator" / "assessment" / "results.tsv"


def _read_results() -> list[dict]:
    if not RESULTS_PATH.exists():
        return []
    with open(RESULTS_PATH, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def main() -> None:
    handle = install_process_logging(
        run_id=f"assessment_series_{path_timestamp()}",
        component="assessment_series",
        tee_console=True,
    )
    parser = argparse.ArgumentParser(description="Repeat multistep assessment runs")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--dataset", default=str(PROJECT_ROOT / "generator" / "assessment" / "dataset.json"))
    parser.add_argument("--description-prefix", default="baseline")
    parser.add_argument("--temperature", type=float, default=None)
    try:
        args = parser.parse_args()

        if args.runs <= 0:
            log("No runs requested.")
            return

        recorded_results: list[dict] = []
        for index in range(1, args.runs + 1):
            before_rows = _read_results()
            description = f"{args.description_prefix} run {index}/{args.runs}"
            json_out = PROJECT_ROOT / "logs" / f"{description.replace(' ', '_').replace('/', '_')}.json"
            cmd = [
                sys.executable,
                "-m",
                "generator.assessment.run_assessment",
                "--dataset",
                args.dataset,
                "--description",
                description,
                "--json-out",
                str(json_out),
            ]
            if args.temperature is not None:
                cmd.extend(["--temperature", str(args.temperature)])
            log(f"\n=== Running {description} ===")
            result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
            if result.returncode != 0:
                raise SystemExit(result.returncode)
            after_rows = _read_results()
            new_rows = after_rows[len(before_rows):]
            if len(new_rows) != 1:
                log(f"Expected exactly 1 appended row for {description}, found {len(new_rows)}")
                raise SystemExit(1)
            row = new_rows[0]
            recorded_results.append(json.loads(json_out.read_text(encoding="utf-8")))
            log(
                "Recorded: "
                f"composite={row['composite']} "
                f"pass={row['pass_rate']} "
                f"sem={row['avg_semantic']} "
                f"reb={row['avg_rebus']} "
                f"status={row['status']}"
            )

        if not recorded_results:
            log("No new rows appended.")
            return

        composites = [float(row["composite"]) for row in recorded_results]
        passes = [float(row["pass_rate"]) for row in recorded_results]
        semantics = [float(row["avg_semantic"]) for row in recorded_results]
        rebus_scores = [float(row["avg_rebus"]) for row in recorded_results]

        log("\n=== Series Summary ===")
        log(f"Runs:           {len(recorded_results)}")
        log(f"Composite avg:  {sum(composites)/len(composites):.2f}")
        log(f"Pass rate avg:  {sum(passes)/len(passes):.3f}")
        log(f"Semantic avg:   {sum(semantics)/len(semantics):.2f}")
        log(f"Rebus avg:      {sum(rebus_scores)/len(rebus_scores):.2f}")
        log(f"Composite min:  {min(composites):.1f}")
        log(f"Composite max:  {max(composites):.1f}")
    finally:
        handle.restore()


if __name__ == "__main__":
    main()
