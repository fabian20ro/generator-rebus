#!/usr/bin/env python3
"""Run the multistep assessment multiple times and summarize the results.

Usage:
    python3 tools/scripts/run_multistep_assessment_series.py --runs 3 --description-prefix baseline
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rebus_generator.platform.io.runtime_logging import install_process_logging, log, path_timestamp
from rebus_generator.evaluation.assessment.series import run_assessment_series

RESULTS_PATH = PROJECT_ROOT / "build" / "evaluation" / "assessment" / "results.tsv"


def main() -> None:
    handle = install_process_logging(
        run_id=f"assessment_series_{path_timestamp()}",
        component="assessment_series",
        tee_console=True,
    )
    parser = argparse.ArgumentParser(description="Repeat multistep assessment runs")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--dataset",
        default=str(
            PROJECT_ROOT
            / "packages"
            / "rebus-generator"
            / "src"
            / "rebus_generator"
            / "evaluation"
            / "datasets"
            / "manifests"
            / "dataset.json"
        ),
    )
    parser.add_argument("--description-prefix", default="baseline")
    parser.add_argument("--temperature", type=float, default=None)
    try:
        args = parser.parse_args()

        run_assessment_series(
            project_root=PROJECT_ROOT,
            results_path=RESULTS_PATH,
            runs=args.runs,
            dataset=args.dataset,
            description_prefix=args.description_prefix,
            temperature=args.temperature,
        )
    finally:
        handle.restore()


if __name__ == "__main__":
    main()
