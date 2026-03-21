"""Overnight loop controller for repeated batch generation."""

from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .config import VERIFY_CANDIDATE_COUNT
from .core.runtime_logging import human_timestamp, install_process_logging, path_timestamp
from .core.size_tuning import OVERNIGHT_LOOP_SIZES


@dataclass(frozen=True)
class LoopRunResult:
    size: int
    seed: int
    exit_code: int
    latest_run_dir: str


def _latest_batch_dir(output_root: Path) -> str:
    candidates = [path for path in output_root.iterdir() if path.is_dir()] if output_root.exists() else []
    if not candidates:
        return "-"
    latest = max(candidates, key=lambda path: path.stat().st_mtime)
    return str(latest)


def build_batch_command(
    size: int,
    words: str,
    output_root: str,
    rewrite_rounds: int,
    preparation_attempts: int,
    seed: int,
    multi_model: bool = False,
    verify_candidates: int = VERIFY_CANDIDATE_COUNT,
) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "generator.batch_publish",
        "--sizes",
        str(size),
        "--words",
        words,
        "--output-root",
        output_root,
        "--rewrite-rounds",
        str(rewrite_rounds),
        "--preparation-attempts",
        str(preparation_attempts),
        "--seed",
        str(seed),
        "--verify-candidates",
        str(verify_candidates),
    ]
    if multi_model:
        cmd.append("--multi-model")
    return cmd


def run_size(
    size: int,
    *,
    words: str,
    output_root: Path,
    rewrite_rounds: int,
    preparation_attempts: int,
    log_path: Path,
    cwd: Path,
    env: dict[str, str] | None = None,
    multi_model: bool = False,
    verify_candidates: int = VERIFY_CANDIDATE_COUNT,
) -> LoopRunResult:
    seed = random.SystemRandom().randint(1, 10_000_000)
    command = build_batch_command(
        size=size,
        words=words,
        output_root=str(output_root),
        rewrite_rounds=rewrite_rounds,
        preparation_attempts=preparation_attempts,
        seed=seed,
        multi_model=multi_model,
        verify_candidates=verify_candidates,
    )
    with open(log_path, "a", encoding="utf-8") as log_file:
        log_file.write(
            f"[{human_timestamp()}] start size={size} seed={seed}\n"
        )
        log_file.flush()
        result = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        latest_run_dir = _latest_batch_dir(output_root)
        log_file.write(
            f"[{human_timestamp()}] done size={size} seed={seed} "
            f"exit={result.returncode} latest_run_dir={latest_run_dir}\n"
        )
        log_file.flush()
    return LoopRunResult(
        size=size,
        seed=seed,
        exit_code=result.returncode,
        latest_run_dir=latest_run_dir,
    )


def run_cycle(
    sizes: list[int],
    *,
    words: str,
    output_root: Path,
    rewrite_rounds: int,
    preparation_attempts: int,
    log_path: Path,
    cwd: Path,
    env: dict[str, str] | None = None,
    multi_model: bool = False,
    verify_candidates: int = VERIFY_CANDIDATE_COUNT,
) -> list[LoopRunResult]:
    results: list[LoopRunResult] = []
    for size in sizes:
        results.append(
            run_size(
                size,
                words=words,
                output_root=output_root,
                rewrite_rounds=rewrite_rounds,
                preparation_attempts=preparation_attempts,
                log_path=log_path,
                cwd=cwd,
                env=env,
                multi_model=multi_model,
                verify_candidates=verify_candidates,
            )
        )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run overnight rebus batch generation in a loop.")
    parser.add_argument(
        "--sizes",
        nargs="+",
        type=int,
        default=list(OVERNIGHT_LOOP_SIZES),
        help="Puzzle sizes to run in order for each cycle",
    )
    parser.add_argument("--words", default="generator/output/words.json")
    parser.add_argument("--output-root", default="generator/output/batch")
    parser.add_argument("--rewrite-rounds", type=int, default=30)
    parser.add_argument("--preparation-attempts", type=int, default=5)
    parser.add_argument("--sleep-seconds", type=int, default=2)
    parser.add_argument("--log-path", default="generator/output/loop_runner.log")
    parser.add_argument(
        "--multi-model",
        action="store_true",
        default=True,
        help="Alternate between primary and secondary models for cross-validation",
    )
    parser.add_argument(
        "--verify-candidates",
        type=int,
        default=VERIFY_CANDIDATE_COUNT,
        help=f"How many verifier candidates to request per clue (default: {VERIFY_CANDIDATE_COUNT})",
    )
    return parser


def main() -> None:
    handle = install_process_logging(
        run_id=f"loop_controller_{path_timestamp()}",
        component="loop_controller",
        tee_console=True,
    )
    parser = build_parser()
    try:
        args = parser.parse_args()
        cwd = Path.cwd()
        output_root = Path(args.output_root)
        log_path = Path(args.log_path)
        output_root.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(
                f"[{human_timestamp()}] loop started sizes={' '.join(map(str, args.sizes))}\n"
            )

        while True:
            run_cycle(
                args.sizes,
                words=args.words,
                output_root=output_root,
                rewrite_rounds=args.rewrite_rounds,
                preparation_attempts=args.preparation_attempts,
                log_path=log_path,
                cwd=cwd,
                env=os.environ.copy(),
                multi_model=args.multi_model,
                verify_candidates=max(1, args.verify_candidates),
            )
            time.sleep(args.sleep_seconds)
    finally:
        handle.restore()


if __name__ == "__main__":
    main()
