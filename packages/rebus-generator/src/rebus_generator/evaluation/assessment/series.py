from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from rebus_generator.platform.io.runtime_logging import log


def read_results(results_path: Path) -> list[dict]:
    if not results_path.exists():
        return []
    with open(results_path, "r", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def run_assessment_series(
    *,
    project_root: Path,
    results_path: Path,
    runs: int,
    dataset: str,
    description_prefix: str,
    temperature: float | None = None,
) -> None:
    if runs <= 0:
        log("No runs requested.")
        return
    recorded_results: list[dict] = []
    for index in range(1, runs + 1):
        before_rows = read_results(results_path)
        description = f"{description_prefix} run {index}/{runs}"
        json_out = project_root / "logs" / f"{description.replace(' ', '_').replace('/', '_')}.json"
        cmd = [
            sys.executable,
            "-m",
            "rebus_generator.cli.assessment",
            "--dataset",
            dataset,
            "--description",
            description,
            "--json-out",
            str(json_out),
        ]
        if temperature is not None:
            cmd.extend(["--temperature", str(temperature)])
        log(f"\n=== Running {description} ===")
        result = subprocess.run(cmd, cwd=str(project_root))
        if result.returncode != 0:
            raise SystemExit(result.returncode)
        after_rows = read_results(results_path)
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
