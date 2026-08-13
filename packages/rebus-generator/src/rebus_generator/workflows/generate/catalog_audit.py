"""Read-only quality audit for puzzles exposed by the public API."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping

import httpx

from rebus_generator.platform.config import (
    PUBLICATION_MIN_PASS_RATE,
    PUBLICATION_MIN_REBUS_SCORE,
)


_LEGACY_REBUS_SCORE = re.compile(r"Scor rebus:\s*(\d+(?:\.\d+)?)\s*/\s*10")
_LEGACY_VERIFIED_COUNT = re.compile(r"Verificate:\s*(\d+)\s*/\s*(\d+)")


def _minimum_rebus_score(puzzle: Mapping[str, Any]) -> int | float | None:
    value = puzzle.get("rebus_score_min")
    if isinstance(value, (int, float)):
        return value
    match = _LEGACY_REBUS_SCORE.search(str(puzzle.get("description") or ""))
    if not match:
        return None
    parsed = float(match.group(1))
    return int(parsed) if parsed.is_integer() else parsed


def _pass_rate(puzzle: Mapping[str, Any]) -> float | None:
    value = puzzle.get("pass_rate")
    if isinstance(value, (int, float)):
        return float(value)
    match = _LEGACY_VERIFIED_COUNT.search(str(puzzle.get("description") or ""))
    if not match:
        return None
    verified, total = (int(part) for part in match.groups())
    return verified / total if total else 0.0


def audit_catalog(
    puzzles: Iterable[Mapping[str, Any]],
    *,
    min_pass_rate: float,
    min_rebus_score: int,
) -> dict[str, object]:
    failures: list[dict[str, object]] = []
    reasons: Counter[str] = Counter()
    by_size: dict[str, Counter[str]] = {}
    total = 0
    passing = 0

    for puzzle in puzzles:
        total += 1
        pass_rate = _pass_rate(puzzle)
        rebus_score = _minimum_rebus_score(puzzle)
        puzzle_reasons: list[str] = []
        if not isinstance(rebus_score, (int, float)):
            puzzle_reasons.append("missing_min_rebus_score")
        elif rebus_score < min_rebus_score:
            puzzle_reasons.append("low_min_rebus_score")
        if not isinstance(pass_rate, (int, float)):
            puzzle_reasons.append("missing_pass_rate")
        elif pass_rate < min_pass_rate:
            puzzle_reasons.append("low_pass_rate")

        size = str(puzzle.get("grid_size", "unknown"))
        size_counts = by_size.setdefault(size, Counter())
        size_counts["puzzles"] += 1

        if not puzzle_reasons:
            passing += 1
            size_counts["passing"] += 1
            continue

        size_counts["failing"] += 1
        reasons.update(puzzle_reasons)
        failures.append(
            {
                "id": puzzle.get("id"),
                "title": puzzle.get("title"),
                "grid_size": puzzle.get("grid_size"),
                "pass_rate": pass_rate,
                "min_rebus_score": rebus_score,
                "reasons": puzzle_reasons,
            }
        )

    normalized_by_size = {
        size: {
            "puzzles": counts["puzzles"],
            "passing": counts["passing"],
            "failing": counts["failing"],
        }
        for size, counts in sorted(by_size.items(), key=lambda item: item[0])
    }
    return {
        "policy": {
            "min_pass_rate": min_pass_rate,
            "min_rebus_score": min_rebus_score,
        },
        "totals": {
            "puzzles": total,
            "passing": passing,
            "failing": total - passing,
        },
        "reasons": dict(sorted(reasons.items())),
        "by_size": normalized_by_size,
        "failures": failures,
    }


def _load_snapshot(path: Path) -> list[Mapping[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("puzzles")
    if not isinstance(payload, list):
        raise ValueError("catalog input must be a JSON list or an object with a puzzles list")
    return payload


def _fetch_catalog(api_base: str) -> list[Mapping[str, Any]]:
    response = httpx.get(f"{api_base.rstrip('/')}/puzzles", timeout=30.0)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("catalog API must return a JSON list")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit public puzzle quality without mutations.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--api-base", help="Public API base URL; /puzzles is appended.")
    source.add_argument("--input", type=Path, help="Saved /puzzles JSON response.")
    parser.add_argument("--output", type=Path, help="Report path; defaults to stdout.")
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=PUBLICATION_MIN_PASS_RATE,
    )
    parser.add_argument(
        "--min-rebus-score",
        type=int,
        default=PUBLICATION_MIN_REBUS_SCORE,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0 <= args.min_pass_rate <= 1:
        raise ValueError("--min-pass-rate must be between 0 and 1")
    if not 1 <= args.min_rebus_score <= 10:
        raise ValueError("--min-rebus-score must be between 1 and 10")

    puzzles = _load_snapshot(args.input) if args.input else _fetch_catalog(args.api_base)
    report = audit_catalog(
        puzzles,
        min_pass_rate=args.min_pass_rate,
        min_rebus_score=args.min_rebus_score,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
