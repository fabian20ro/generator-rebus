"""Multi-model assessment pipeline.

Simulates production two-model workflow:
  Phase 1: PRIMARY generates definitions for all words
  Phase 2: SECONDARY evaluates pass1, then generates its own definitions
  Phase 3: PRIMARY evaluates pass2, picks best per word, computes composite

Usage:
    python3 -m rebus_generator.cli.assessment [--description "label"] [--generate-temperature 0.2] [--rewrite-temperature 0.3]
"""

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
from .models import AssessmentResult, best_definition, best_guesses, best_verified
from .pipeline import DATASET_PATH, append_results_tsv, run_assessment, write_result_json

_append_results_tsv = append_results_tsv


def _print_report(result: AssessmentResult) -> None:
    log("\n" + "=" * 60)
    log("ASSESSMENT RESULTS (multi-model)")
    log("=" * 60)
    log(f"Composite score: {result.composite:.1f}")
    log(f"Pass rate:       {result.pass_rate:.1%}")
    log(f"Tier-balanced:   {result.tier_balanced_pass_rate:.1%}")
    log(f"Avg semantic:    {result.avg_semantic:.1f}/10")
    log(f"Avg rebus:       {result.avg_rebus:.1f}/10")

    # Source distribution
    pass1_count = sum(1 for c in result.candidates if c.best_source == "pass1")
    pass2_count = len(result.candidates) - pass1_count
    log(f"\nBest-source distribution: pass1={pass1_count}, pass2={pass2_count}")

    log("\nPer-tier breakdown:")
    log(f"  {'Tier':<10} {'Pass Rate':>10} {'Avg Sem':>10} {'Avg Rebus':>10} {'Count':>6}")
    log(f"  {'─' * 10} {'─' * 10} {'─' * 10} {'─' * 10} {'─' * 6}")
    preferred_order = ["low", "medium", "high", "easy", "hard", "short", "rare"]
    tier_names = sorted(
        result.tier_results.keys(),
        key=lambda name: (
            preferred_order.index(name) if name in preferred_order else len(preferred_order),
            name,
        ),
    )
    for tier_name in tier_names:
        tr = result.tier_results.get(tier_name)
        if tr:
            log(
                f"  {tier_name:<10} {tr.pass_rate:>9.0%} "
                f"{tr.avg_semantic:>10.1f} {tr.avg_rebus:>10.1f} {tr.total:>6}"
            )

    failures = [
        c for c in result.candidates
        if not _best_verified(c)
        and _best_definition(c)
        and not _best_definition(c).startswith("[")
    ]
    if failures:
        log(f"\nFailed words ({len(failures)}):")
        for c in failures:
            defn = best_definition(c)[:60]
            guesses = ", ".join(best_guesses(c)) or "[nimic]"
            log(f"  {c.word} ({c.tier}, {c.best_source}): '{defn}...' → guessed '{guesses}'")


def main() -> None:
    handle = install_process_logging(
        run_id=f"assessment_{path_timestamp()}",
        component="assessment",
        tee_console=True,
    )
    parser = argparse.ArgumentParser(description="Run multi-model assessment pipeline")
    parser.add_argument(
        "--dataset", default=str(DATASET_PATH),
        help="Path to dataset.json",
    )
    parser.add_argument(
        "--description", default="multi-model baseline",
        help="Short description for results TSV",
    )
    parser.add_argument(
        "--temperature", type=float, default=None,
        help="Override generate temperature (default: use function default 0.2)",
    )
    parser.add_argument(
        "--generate-temperature", type=float, default=None,
        help="Override pass1 generate temperature (default: --temperature or function default 0.2)",
    )
    parser.add_argument(
        "--rewrite-temperature", type=float, default=None,
        help="Override pass2 generate temperature (default: --temperature or function default 0.2)",
    )
    parser.add_argument(
        "--verify-candidates", type=int, default=VERIFY_CANDIDATE_COUNT,
        help=f"How many verifier candidates to request per definition (default: {VERIFY_CANDIDATE_COUNT})",
    )
    parser.add_argument(
        "--json-out",
        help="Optional path for machine-readable assessment JSON",
    )
    parser.add_argument(
        "--append-tsv",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Append assessment summary to results.tsv (default: True)",
    )
    add_llm_debug_argument(parser)
    try:
        args = parser.parse_args()
        set_llm_debug_enabled(args.debug)

        result = run_assessment(
            Path(args.dataset),
            temperature=args.temperature,
            generate_temperature=args.generate_temperature,
            rewrite_temperature=args.rewrite_temperature,
            verify_candidates=max(1, args.verify_candidates),
        )
        _print_report(result)
        if args.json_out:
            write_result_json(result, Path(args.json_out))
        if args.append_tsv:
            _append_results_tsv(result, args.description)
    finally:
        handle.restore()


if __name__ == "__main__":
    main()
