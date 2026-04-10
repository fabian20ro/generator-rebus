#!/usr/bin/env python3
"""Generate and publish a batch of rebus puzzles."""

from __future__ import annotations

import argparse
from pathlib import Path

from rebus_generator.platform.config import VERIFY_CANDIDATE_COUNT
from rebus_generator.platform.io.runtime_logging import (
    add_llm_debug_argument,
    install_process_logging,
    log,
    path_timestamp,
    set_llm_debug_enabled,
)
from rebus_generator.domain.size_tuning import DEFAULT_BATCH_SIZES, SUPPORTED_GRID_SIZES
from .models import MAX_REWRITE_ROUNDS
from .runtime import run_batch


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate and publish a batch of rebus puzzles.")
    parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=list(DEFAULT_BATCH_SIZES),
        choices=list(SUPPORTED_GRID_SIZES),
        help="Puzzle sizes to generate in order",
    )
    parser.add_argument("--words", default="build/words.json", help="Path to words.json cache")
    parser.add_argument("--output-root", default="build/batch", help="Directory where batch artifacts are written")
    parser.add_argument(
        "--rewrite-rounds",
        type=int,
        default=MAX_REWRITE_ROUNDS,
        help="Automatic define/verify rewrite rounds for failed clues",
    )
    parser.add_argument(
        "--preparation-attempts",
        type=int,
        default=3,
        help="How many candidate puzzles to try before giving up on a size",
    )
    parser.add_argument("--seed", type=int, default=None, help="Optional RNG seed for reproducible batch generation")
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
    add_llm_debug_argument(parser)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    output_root = Path(args.output_root)
    preview_run_dir = output_root / path_timestamp()
    preview_run_dir.mkdir(parents=True, exist_ok=True)
    log_path = preview_run_dir / "run.log"
    audit_path = preview_run_dir / "audit.jsonl"
    handle = install_process_logging(
        run_id=preview_run_dir.name,
        component="batch_publish",
        log_path=log_path,
        audit_path=audit_path,
        tee_console=True,
    )
    try:
        set_llm_debug_enabled(args.debug)
        log(f"Run log: {log_path}")
        log(f"Audit log: {audit_path}")
        manifest = run_batch(
            sizes=args.sizes,
            output_root=output_root,
            words_path=Path(args.words),
            rewrite_rounds=args.rewrite_rounds,
            preparation_attempts=args.preparation_attempts,
            seed=args.seed,
            run_dir=preview_run_dir,
            multi_model=args.multi_model,
            verify_candidates=max(1, args.verify_candidates),
        )
        log("\nBatch complete:")
        for item in manifest:
            log(
                f"  {item['title']} -> {item['puzzle_id']} "
                f"(verify {item['verification_passed']}/{item['verification_total']})"
            )
    finally:
        handle.restore()


if __name__ == "__main__":
    main()
