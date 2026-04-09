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
from .core.runtime_logging import (
    add_llm_debug_argument,
    format_human_log_line,
    install_process_logging,
    log,
    path_timestamp,
    set_llm_debug_enabled,
)
from .core.size_tuning import OVERNIGHT_LOOP_SIZES, SUPPORTED_GRID_SIZES
from .core.supabase_ops import create_service_role_client


@dataclass(frozen=True)
class LoopRunResult:
    size: int
    seed: int
    exit_code: int
    latest_run_dir: str


def fetch_puzzle_size_counts(*, client=None, batch_size: int = 1000) -> dict[int, int]:
    client = client or create_service_role_client()
    counts: dict[int, int] = {}
    offset = 0
    while True:
        response = (
            client.table("crossword_puzzles")
            .select("grid_size")
            .range(offset, offset + batch_size - 1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            break
        for row in rows:
            size = int(row.get("grid_size") or 0)
            if size in SUPPORTED_GRID_SIZES:
                counts[size] = counts.get(size, 0) + 1
        if len(rows) < batch_size:
            break
        offset += batch_size
    return counts


def choose_balanced_size(
    counts: dict[int, int],
    *,
    supported_sizes: tuple[int, ...] = SUPPORTED_GRID_SIZES,
    excluded_sizes: set[int] | None = None,
    size_penalties: dict[int, int] | None = None,
) -> tuple[int, dict[int, int]]:
    inventory = {size: int(counts.get(size, 0) or 0) for size in supported_sizes}
    excluded = set(excluded_sizes or set())
    penalties = {size: int(size_penalties.get(size, 0) or 0) for size in supported_sizes} if size_penalties else {}
    selectable_sizes = [size for size in supported_sizes if size not in excluded]
    if not selectable_sizes:
        selectable_sizes = list(supported_sizes)
    selected_size = min(
        selectable_sizes,
        key=lambda size: (
            inventory[size] + penalties.get(size, 0),
            inventory[size],
            size,
        ),
    )
    return selected_size, inventory


def select_auto_size(
    *,
    client=None,
    excluded_sizes: set[int] | None = None,
    size_penalties: dict[int, int] | None = None,
) -> int:
    counts = fetch_puzzle_size_counts(client=client)
    selected_size, inventory = choose_balanced_size(
        counts,
        excluded_sizes=excluded_sizes,
        size_penalties=size_penalties,
    )
    summary = " ".join(f"{size}:{inventory[size]}" for size in sorted(inventory))
    excluded_text = ""
    if excluded_sizes:
        excluded_text = " excluded=" + ",".join(str(size) for size in sorted(excluded_sizes))
    penalty_text = ""
    if size_penalties:
        nonzero = {size: penalty for size, penalty in size_penalties.items() if penalty}
        if nonzero:
            penalty_text = " penalties=" + ",".join(f"{size}:{nonzero[size]}" for size in sorted(nonzero))
    log(f"Auto-selected size={selected_size} inventory={summary}{excluded_text}{penalty_text}")
    return selected_size


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
    debug: bool = False,
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
    if debug:
        cmd.append("--debug")
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
    debug: bool = False,
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
        debug=debug,
    )
    with open(log_path, "a", encoding="utf-8") as log_file:
        log_file.write(format_human_log_line(f"start size={size} seed={seed}") + "\n")
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
            format_human_log_line(
                f"done size={size} seed={seed} "
                f"exit={result.returncode} latest_run_dir={latest_run_dir}"
            )
            + "\n"
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
    auto_size: bool = False,
    supabase_client=None,
    debug: bool = False,
) -> list[LoopRunResult]:
    results: list[LoopRunResult] = []
    sizes_to_run = [select_auto_size(client=supabase_client)] if auto_size else list(sizes)
    for size in sizes_to_run:
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
                debug=debug,
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
    parser.add_argument(
        "--auto-size",
        action="store_true",
        help="Choose the next puzzle size from current crossword_puzzles inventory",
    )
    add_llm_debug_argument(parser)
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
        set_llm_debug_enabled(args.debug)
        cwd = Path.cwd()
        output_root = Path(args.output_root)
        log_path = Path(args.log_path)
        output_root.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with open(log_path, "a", encoding="utf-8") as log_file:
            log_file.write(
                format_human_log_line(
                    f"loop started mode={'auto-size' if args.auto_size else 'fixed'} "
                    f"sizes={' '.join(map(str, args.sizes))}"
                )
                + "\n"
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
                auto_size=args.auto_size,
                debug=args.debug,
            )
            time.sleep(args.sleep_seconds)
    finally:
        handle.restore()


if __name__ == "__main__":
    main()
