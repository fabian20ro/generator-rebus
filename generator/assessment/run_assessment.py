"""Run the assessment pipeline on dataset.json and compute metrics.

Runs: generate → verify → rate for each word in the dataset.
Outputs a composite score and per-tier breakdown.
Appends results to results.tsv.

Usage:
    python3 -m generator.assessment.run_assessment [--prompts-dir generator/prompts]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..core.ai_clues import (
    compute_rebus_score,
    create_client,
    generate_definition,
    rate_definition,
    verify_definition,
)
from ..core.diacritics import normalize


DATASET_PATH = Path(__file__).parent / "dataset.json"
RESULTS_PATH = Path(__file__).parent / "results.tsv"


@dataclass
class WordResult:
    word: str
    tier: str
    definition: str = ""
    guess: str = ""
    verified: bool = False
    semantic_score: int = 0
    rebus_score: int = 0
    creativity_score: int = 0
    rated: bool = False


@dataclass
class TierResult:
    tier: str
    total: int = 0
    passed: int = 0
    semantic_sum: float = 0.0
    rebus_sum: float = 0.0
    rated_count: int = 0

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def avg_semantic(self) -> float:
        return self.semantic_sum / self.rated_count if self.rated_count else 0.0

    @property
    def avg_rebus(self) -> float:
        return self.rebus_sum / self.rated_count if self.rated_count else 0.0


@dataclass
class AssessmentResult:
    word_results: list[WordResult] = field(default_factory=list)
    tier_results: dict[str, TierResult] = field(default_factory=dict)

    @property
    def pass_rate(self) -> float:
        total = len(self.word_results)
        passed = sum(1 for w in self.word_results if w.verified)
        return passed / total if total else 0.0

    @property
    def avg_semantic(self) -> float:
        rated = [w for w in self.word_results if w.rated]
        return sum(w.semantic_score for w in rated) / len(rated) if rated else 0.0

    @property
    def avg_rebus(self) -> float:
        rated = [w for w in self.word_results if w.rated]
        return sum(w.rebus_score for w in rated) / len(rated) if rated else 0.0

    @property
    def composite(self) -> float:
        return self.pass_rate * 100 + self.avg_semantic * 3 + self.avg_rebus * 2


def _load_dataset(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _git_short_hash() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def run_assessment(dataset_path: Path = DATASET_PATH) -> AssessmentResult:
    """Run the full generate → verify → rate pipeline on the assessment dataset."""
    dataset = _load_dataset(dataset_path)
    client = create_client()
    result = AssessmentResult()

    print(f"Running assessment on {len(dataset)} words...")
    start_time = time.monotonic()

    for i, entry in enumerate(dataset, 1):
        word = entry["word"]
        display_word = entry["display_word"]
        length = entry["length"]
        word_type = entry.get("word_type", "")
        dex_definitions = entry.get("dex_definitions", "")
        tier = entry["tier"]

        print(f"\n[{i}/{len(dataset)}] {word} (tier={tier}, len={length})")

        # Generate
        try:
            definition = generate_definition(
                client, word, display_word, "assessment",
                retries=3, word_type=word_type, dex_definitions=dex_definitions,
            )
        except Exception as e:
            print(f"  Generate failed: {e}")
            definition = "[Definiție negenerată]"

        word_result = WordResult(word=word, tier=tier, definition=definition)

        if definition.startswith("["):
            print(f"  Skipping verify/rate — no definition generated")
            result.word_results.append(word_result)
            continue

        # Verify
        try:
            guess = verify_definition(client, definition, length)
            guess_normalized = normalize(guess)
            word_result.guess = guess
            word_result.verified = guess_normalized == word
            symbol = "✓" if word_result.verified else "✗"
            print(f"  {symbol} Guess: {guess} (expected: {word})")
        except Exception as e:
            print(f"  Verify failed: {e}")

        # Rate
        try:
            rating = rate_definition(
                client, word, display_word, definition, length,
                word_type=word_type, dex_definitions=dex_definitions,
            )
            if rating is not None:
                word_result.semantic_score = rating.semantic_score
                guessability = rating.guessability_score
                creativity = rating.creativity_score
                word_result.creativity_score = creativity
                word_result.rebus_score = compute_rebus_score(guessability, creativity)
                word_result.rated = True
                print(
                    f"  Rating: semantic={rating.semantic_score} "
                    f"rebus={word_result.rebus_score} "
                    f"creativity={creativity}"
                )
            else:
                print(f"  Rating failed (JSON parse error)")
        except Exception as e:
            print(f"  Rate failed: {e}")

        result.word_results.append(word_result)

    # Compute per-tier results
    for word_result in result.word_results:
        tier = word_result.tier
        if tier not in result.tier_results:
            result.tier_results[tier] = TierResult(tier=tier)
        tr = result.tier_results[tier]
        tr.total += 1
        if word_result.verified:
            tr.passed += 1
        if word_result.rated:
            tr.semantic_sum += word_result.semantic_score
            tr.rebus_sum += word_result.rebus_score
            tr.rated_count += 1

    elapsed = time.monotonic() - start_time
    print(f"\nAssessment completed in {elapsed:.0f}s")
    return result


def _print_report(result: AssessmentResult) -> None:
    """Print a formatted assessment report."""
    print("\n" + "=" * 60)
    print("ASSESSMENT RESULTS")
    print("=" * 60)
    print(f"Composite score: {result.composite:.1f}")
    print(f"Pass rate:       {result.pass_rate:.1%}")
    print(f"Avg semantic:    {result.avg_semantic:.1f}/10")
    print(f"Avg rebus:       {result.avg_rebus:.1f}/10")

    print("\nPer-tier breakdown:")
    print(f"  {'Tier':<10} {'Pass Rate':>10} {'Avg Sem':>10} {'Avg Rebus':>10} {'Count':>6}")
    print(f"  {'─' * 10} {'─' * 10} {'─' * 10} {'─' * 10} {'─' * 6}")
    for tier_name in ["easy", "medium", "hard", "short", "rare"]:
        tr = result.tier_results.get(tier_name)
        if tr:
            print(
                f"  {tier_name:<10} {tr.pass_rate:>9.0%} "
                f"{tr.avg_semantic:>10.1f} {tr.avg_rebus:>10.1f} {tr.total:>6}"
            )

    # List failures
    failures = [w for w in result.word_results if not w.verified and w.definition and not w.definition.startswith("[")]
    if failures:
        print(f"\nFailed words ({len(failures)}):")
        for w in failures:
            print(f"  {w.word} ({w.tier}): '{w.definition[:60]}...' → guessed '{w.guess}'")


def _append_results_tsv(result: AssessmentResult, description: str) -> None:
    """Append a result row to results.tsv."""
    commit = _git_short_hash()
    header = "commit\tcomposite\tpass_rate\tavg_semantic\tavg_rebus\tstatus\tdescription\n"

    if not RESULTS_PATH.exists():
        with open(RESULTS_PATH, "w", encoding="utf-8") as f:
            f.write(header)

    with open(RESULTS_PATH, "a", encoding="utf-8") as f:
        f.write(
            f"{commit}\t{result.composite:.1f}\t{result.pass_rate:.3f}\t"
            f"{result.avg_semantic:.1f}\t{result.avg_rebus:.1f}\t"
            f"keep\t{description}\n"
        )
    print(f"\nResults appended to {RESULTS_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run prompt assessment pipeline")
    parser.add_argument(
        "--dataset", default=str(DATASET_PATH),
        help="Path to dataset.json",
    )
    parser.add_argument(
        "--description", default="baseline",
        help="Short description for results.tsv",
    )
    args = parser.parse_args()

    result = run_assessment(Path(args.dataset))
    _print_report(result)
    _append_results_tsv(result, args.description)


if __name__ == "__main__":
    main()
