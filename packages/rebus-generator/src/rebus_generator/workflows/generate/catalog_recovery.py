"""Build a deterministic, read-only recovery and rollout plan for the catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping, Sequence

from rebus_generator.platform.config import (
    PUBLICATION_MIN_PASS_RATE,
    PUBLICATION_MIN_REBUS_SCORE,
)

from .catalog_audit import (
    _fetch_catalog as fetch_catalog,
    _load_snapshot as load_catalog_snapshot,
    _minimum_rebus_score as minimum_rebus_score,
    _pass_rate as pass_rate,
)

DEFAULT_SIZES = tuple(range(7, 16))
DEFAULT_TARGET_PER_SIZE = 3


def _passes_policy(
    puzzle: Mapping[str, Any],
    *,
    min_pass_rate: float,
    min_rebus_score: int,
) -> bool:
    rate = pass_rate(puzzle)
    score = minimum_rebus_score(puzzle)
    return (
        isinstance(rate, (int, float))
        and rate >= min_pass_rate
        and isinstance(score, (int, float))
        and score >= min_rebus_score
    )


def _puzzle_id(puzzle: Mapping[str, Any]) -> str:
    return str(puzzle.get("id") or "").strip()


def _passing_quality(puzzle: Mapping[str, Any]) -> tuple[float, float, str]:
    return (
        float(pass_rate(puzzle) or 0),
        float(minimum_rebus_score(puzzle) or 0),
        str(puzzle.get("repaired_at") or puzzle.get("created_at") or ""),
    )


def _repair_cost(
    puzzle: Mapping[str, Any],
    *,
    min_pass_rate: float,
    min_rebus_score: int,
) -> tuple[float, float, float, str]:
    rate = pass_rate(puzzle)
    score = minimum_rebus_score(puzzle)
    missing = float(not isinstance(rate, (int, float))) + float(
        not isinstance(score, (int, float))
    )
    rate_gap = (
        1.0 if not isinstance(rate, (int, float)) else max(0.0, min_pass_rate - rate)
    )
    score_gap = (
        10.0
        if not isinstance(score, (int, float))
        else max(0.0, min_rebus_score - score)
    )
    return missing, rate_gap, score_gap, _puzzle_id(puzzle)


def build_recovery_plan(
    puzzles: Iterable[Mapping[str, Any]],
    *,
    sizes: Sequence[int] = DEFAULT_SIZES,
    target_per_size: int = DEFAULT_TARGET_PER_SIZE,
    min_pass_rate: float = PUBLICATION_MIN_PASS_RATE,
    min_rebus_score: int = PUBLICATION_MIN_REBUS_SCORE,
) -> dict[str, object]:
    """Select rollout candidates and the cheapest repair queue per requested size."""
    if target_per_size < 1:
        raise ValueError("target_per_size must be at least 1")
    normalized_sizes = tuple(dict.fromkeys(int(size) for size in sizes))
    if not normalized_sizes:
        raise ValueError("sizes must not be empty")

    rows_by_size: dict[int, list[Mapping[str, Any]]] = {
        size: [] for size in normalized_sizes
    }
    for puzzle in puzzles:
        try:
            size = int(puzzle.get("grid_size"))
        except (TypeError, ValueError):
            continue
        if size in rows_by_size and _puzzle_id(puzzle):
            rows_by_size[size].append(puzzle)

    selected_ids: list[str] = []
    deficits: dict[str, int] = {}
    by_size: dict[str, dict[str, object]] = {}
    for size in normalized_sizes:
        rows = rows_by_size[size]
        passing = [
            puzzle
            for puzzle in rows
            if _passes_policy(
                puzzle,
                min_pass_rate=min_pass_rate,
                min_rebus_score=min_rebus_score,
            )
        ]
        passing.sort(key=_puzzle_id)
        passing.sort(key=_passing_quality, reverse=True)
        selected = passing[:target_per_size]
        selected_for_size = [_puzzle_id(puzzle) for puzzle in selected]
        selected_ids.extend(selected_for_size)

        deficit = max(0, target_per_size - len(selected))
        failing = [puzzle for puzzle in rows if puzzle not in passing]
        failing.sort(
            key=lambda puzzle: _repair_cost(
                puzzle,
                min_pass_rate=min_pass_rate,
                min_rebus_score=min_rebus_score,
            )
        )
        repair_queue = [_puzzle_id(puzzle) for puzzle in failing[:deficit]]
        if deficit:
            deficits[str(size)] = deficit
        by_size[str(size)] = {
            "passing": len(passing),
            "selected_ids": selected_for_size,
            "deficit": deficit,
            "repair_queue": repair_queue,
        }

    return {
        "policy": {
            "min_pass_rate": min_pass_rate,
            "min_rebus_score": min_rebus_score,
            "target_per_size": target_per_size,
            "sizes": list(normalized_sizes),
        },
        "rollout_ready": not deficits,
        "selected_ids": selected_ids,
        "deficits": deficits,
        "by_size": by_size,
    }


def _parse_sizes(value: str) -> tuple[int, ...]:
    try:
        sizes = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "sizes must be comma-separated integers"
        ) from exc
    if not sizes:
        raise argparse.ArgumentTypeError("sizes must not be empty")
    return sizes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan catalog recovery without mutations."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--api-base", help="Public API base URL; /puzzles is appended.")
    source.add_argument("--input", type=Path, help="Saved /puzzles JSON response.")
    parser.add_argument("--output", type=Path, help="Plan path; defaults to stdout.")
    parser.add_argument("--sizes", type=_parse_sizes, default=DEFAULT_SIZES)
    parser.add_argument("--target-per-size", type=int, default=DEFAULT_TARGET_PER_SIZE)
    parser.add_argument(
        "--min-pass-rate", type=float, default=PUBLICATION_MIN_PASS_RATE
    )
    parser.add_argument(
        "--min-rebus-score", type=int, default=PUBLICATION_MIN_REBUS_SCORE
    )
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit 2 when one or more requested sizes remain below target.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 <= args.min_pass_rate <= 1:
        raise ValueError("--min-pass-rate must be between 0 and 1")
    if not 1 <= args.min_rebus_score <= 10:
        raise ValueError("--min-rebus-score must be between 1 and 10")

    puzzles = (
        load_catalog_snapshot(args.input)
        if args.input
        else fetch_catalog(args.api_base)
    )
    plan = build_recovery_plan(
        puzzles,
        sizes=args.sizes,
        target_per_size=args.target_per_size,
        min_pass_rate=args.min_pass_rate,
        min_rebus_score=args.min_rebus_score,
    )
    rendered = json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0 if plan["rollout_ready"] or not args.require_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
