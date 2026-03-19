"""Multi-model assessment pipeline.

Simulates production two-model workflow:
  Phase 1: PRIMARY generates definitions for all words
  Phase 2: SECONDARY evaluates pass1, then generates its own definitions
  Phase 3: PRIMARY evaluates pass2, picks best per word, computes composite

Usage:
    python3 -m generator.assessment.run_assessment [--description "label"] [--temperature 0.1]
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
from ..core.model_manager import (
    PRIMARY_MODEL,
    SECONDARY_MODEL,
    ensure_model_loaded,
    switch_model,
)


DATASET_PATH = Path(__file__).parent / "dataset.json"
RESULTS_PATH = Path(__file__).parent / "results.tsv"


@dataclass
class WordCandidate:
    word: str
    tier: str
    display_word: str
    length: int
    word_type: str
    dex_definitions: str
    # Pass 1: PRIMARY generates, SECONDARY evaluates
    pass1_definition: str = ""
    pass1_guess: str = ""
    pass1_verified: bool = False
    pass1_semantic: int = 0
    pass1_rebus: int = 0
    pass1_rated: bool = False
    # Pass 2: SECONDARY generates, PRIMARY evaluates
    pass2_definition: str = ""
    pass2_guess: str = ""
    pass2_verified: bool = False
    pass2_semantic: int = 0
    pass2_rebus: int = 0
    pass2_rated: bool = False
    # Final
    best_source: str = "pass1"


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
    candidates: list[WordCandidate] = field(default_factory=list)
    tier_results: dict[str, TierResult] = field(default_factory=dict)

    @property
    def pass_rate(self) -> float:
        total = len(self.candidates)
        passed = sum(1 for c in self.candidates if _best_verified(c))
        return passed / total if total else 0.0

    @property
    def avg_semantic(self) -> float:
        rated = [c for c in self.candidates if _best_rated(c)]
        return sum(_best_semantic(c) for c in rated) / len(rated) if rated else 0.0

    @property
    def avg_rebus(self) -> float:
        rated = [c for c in self.candidates if _best_rated(c)]
        return sum(_best_rebus(c) for c in rated) / len(rated) if rated else 0.0

    @property
    def composite(self) -> float:
        return self.pass_rate * 100 + self.avg_semantic * 3 + self.avg_rebus * 2


def _candidate_rank(verified: bool, semantic: int, rebus: int) -> tuple:
    return (int(verified), semantic + rebus, rebus)


def _best_verified(c: WordCandidate) -> bool:
    return c.pass1_verified if c.best_source == "pass1" else c.pass2_verified


def _best_rated(c: WordCandidate) -> bool:
    return c.pass1_rated if c.best_source == "pass1" else c.pass2_rated


def _best_semantic(c: WordCandidate) -> int:
    return c.pass1_semantic if c.best_source == "pass1" else c.pass2_semantic


def _best_rebus(c: WordCandidate) -> int:
    return c.pass1_rebus if c.best_source == "pass1" else c.pass2_rebus


def _best_definition(c: WordCandidate) -> str:
    return c.pass1_definition if c.best_source == "pass1" else c.pass2_definition


def _pick_best(c: WordCandidate) -> None:
    rank1 = _candidate_rank(c.pass1_verified, c.pass1_semantic, c.pass1_rebus)
    rank2 = _candidate_rank(c.pass2_verified, c.pass2_semantic, c.pass2_rebus)
    if rank1 == rank2:
        has_def1 = bool(c.pass1_definition) and not c.pass1_definition.startswith("[")
        has_def2 = bool(c.pass2_definition) and not c.pass2_definition.startswith("[")
        c.best_source = "pass2" if has_def2 and not has_def1 else "pass1"
    else:
        c.best_source = "pass2" if rank2 > rank1 else "pass1"


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


def _generate_for_word(
    client, word: str, display_word: str, word_type: str,
    dex_definitions: str, temperature: float | None,
) -> str:
    try:
        return generate_definition(
            client, word, display_word, "assessment",
            retries=3, word_type=word_type, dex_definitions=dex_definitions,
            temperature=temperature,
        )
    except Exception as e:
        print(f"  Generate failed: {e}")
        return "[Definiție negenerată]"


def _verify_for_word(client, definition: str, length: int) -> str:
    """Returns the guessed word, or empty string on failure."""
    try:
        return verify_definition(client, definition, length)
    except Exception as e:
        print(f"  Verify failed: {e}")
        return ""


def _rate_for_word(
    client, word: str, display_word: str, definition: str,
    length: int, word_type: str, dex_definitions: str,
) -> tuple[int, int, bool]:
    """Returns (semantic, rebus, rated)."""
    try:
        rating = rate_definition(
            client, word, display_word, definition, length,
            word_type=word_type, dex_definitions=dex_definitions,
        )
        if rating is not None:
            rebus = compute_rebus_score(rating.guessability_score, rating.creativity_score)
            return rating.semantic_score, rebus, True
        print(f"  Rating failed (JSON parse error)")
    except Exception as e:
        print(f"  Rate failed: {e}")
    return 0, 0, False


def run_assessment(
    dataset_path: Path = DATASET_PATH,
    temperature: float | None = None,
) -> AssessmentResult:
    """Run the multi-model generate → cross-verify → cross-rate pipeline."""
    dataset = _load_dataset(dataset_path)
    client = create_client()
    result = AssessmentResult()
    phase_times: dict[str, float] = {}

    candidates = [
        WordCandidate(
            word=entry["word"],
            tier=entry["tier"],
            display_word=entry["display_word"],
            length=entry["length"],
            word_type=entry.get("word_type", ""),
            dex_definitions=entry.get("dex_definitions", ""),
        )
        for entry in dataset
    ]
    result.candidates = candidates
    n = len(candidates)

    print(f"Running multi-model assessment on {n} words...")
    total_start = time.monotonic()

    # ── Phase 1: PRIMARY generates ────────────────────────────────
    print(f"\n{'='*60}")
    print(f"PHASE 1: {PRIMARY_MODEL.display_name} generates definitions")
    print(f"{'='*60}")
    phase_start = time.monotonic()
    ensure_model_loaded(PRIMARY_MODEL)

    for i, c in enumerate(candidates, 1):
        print(f"\n[{i}/{n}] {c.word} (tier={c.tier}, len={c.length})")
        c.pass1_definition = _generate_for_word(
            client, c.word, c.display_word, c.word_type,
            c.dex_definitions, temperature,
        )
        if c.pass1_definition.startswith("["):
            print(f"  Skipping — no definition generated")

    phase_times["phase1_generate"] = time.monotonic() - phase_start

    # ── Phase 2: SECONDARY evaluates pass1 + generates pass2 ─────
    print(f"\n{'='*60}")
    print(f"PHASE 2: {SECONDARY_MODEL.display_name} evaluates + generates")
    print(f"{'='*60}")
    phase_start = time.monotonic()
    switch_model(PRIMARY_MODEL, SECONDARY_MODEL)

    # 2a: cross-model verify + rate of pass1 definitions
    print(f"\n--- Phase 2a: evaluate pass1 definitions ---")
    for i, c in enumerate(candidates, 1):
        if c.pass1_definition.startswith("["):
            continue
        print(f"\n[{i}/{n}] {c.word} — verify+rate pass1")
        guess = _verify_for_word(client, c.pass1_definition, c.length)
        c.pass1_guess = guess
        c.pass1_verified = normalize(guess) == c.word
        symbol = "✓" if c.pass1_verified else "✗"
        print(f"  {symbol} Guess: {guess} (expected: {c.word})")

        semantic, rebus, rated = _rate_for_word(
            client, c.word, c.display_word, c.pass1_definition,
            c.length, c.word_type, c.dex_definitions,
        )
        c.pass1_semantic = semantic
        c.pass1_rebus = rebus
        c.pass1_rated = rated
        if rated:
            print(f"  Rating: semantic={semantic} rebus={rebus}")

    # 2b: SECONDARY generates fresh definitions for all words
    print(f"\n--- Phase 2b: generate pass2 definitions ---")
    for i, c in enumerate(candidates, 1):
        print(f"\n[{i}/{n}] {c.word} (tier={c.tier}, len={c.length})")
        c.pass2_definition = _generate_for_word(
            client, c.word, c.display_word, c.word_type,
            c.dex_definitions, temperature,
        )
        if c.pass2_definition.startswith("["):
            print(f"  Skipping — no definition generated")

    phase_times["phase2_eval_and_generate"] = time.monotonic() - phase_start

    # ── Phase 3: PRIMARY evaluates pass2 + picks best ─────────────
    print(f"\n{'='*60}")
    print(f"PHASE 3: {PRIMARY_MODEL.display_name} evaluates + selects best")
    print(f"{'='*60}")
    phase_start = time.monotonic()
    switch_model(SECONDARY_MODEL, PRIMARY_MODEL)

    # 3a: cross-model verify + rate of pass2 definitions
    print(f"\n--- Phase 3a: evaluate pass2 definitions ---")
    for i, c in enumerate(candidates, 1):
        if c.pass2_definition.startswith("["):
            continue
        print(f"\n[{i}/{n}] {c.word} — verify+rate pass2")
        guess = _verify_for_word(client, c.pass2_definition, c.length)
        c.pass2_guess = guess
        c.pass2_verified = normalize(guess) == c.word
        symbol = "✓" if c.pass2_verified else "✗"
        print(f"  {symbol} Guess: {guess} (expected: {c.word})")

        semantic, rebus, rated = _rate_for_word(
            client, c.word, c.display_word, c.pass2_definition,
            c.length, c.word_type, c.dex_definitions,
        )
        c.pass2_semantic = semantic
        c.pass2_rebus = rebus
        c.pass2_rated = rated
        if rated:
            print(f"  Rating: semantic={semantic} rebus={rebus}")

    # 3b: pick best definition per word
    print(f"\n--- Phase 3b: selecting best definitions ---")
    for c in candidates:
        _pick_best(c)
        best_def = _best_definition(c)[:50]
        print(f"  {c.word}: best={c.best_source} — '{best_def}...'")

    phase_times["phase3_eval_and_select"] = time.monotonic() - phase_start

    # ── Compute per-tier results ──────────────────────────────────
    for c in candidates:
        tier = c.tier
        if tier not in result.tier_results:
            result.tier_results[tier] = TierResult(tier=tier)
        tr = result.tier_results[tier]
        tr.total += 1
        if _best_verified(c):
            tr.passed += 1
        if _best_rated(c):
            tr.semantic_sum += _best_semantic(c)
            tr.rebus_sum += _best_rebus(c)
            tr.rated_count += 1

    elapsed = time.monotonic() - total_start
    print(f"\nAssessment completed in {elapsed:.0f}s")
    print(f"  Phase timings:")
    for phase, t in phase_times.items():
        print(f"    {phase}: {t:.0f}s")
    return result


def _print_report(result: AssessmentResult) -> None:
    print("\n" + "=" * 60)
    print("ASSESSMENT RESULTS (multi-model)")
    print("=" * 60)
    print(f"Composite score: {result.composite:.1f}")
    print(f"Pass rate:       {result.pass_rate:.1%}")
    print(f"Avg semantic:    {result.avg_semantic:.1f}/10")
    print(f"Avg rebus:       {result.avg_rebus:.1f}/10")

    # Source distribution
    pass1_count = sum(1 for c in result.candidates if c.best_source == "pass1")
    pass2_count = len(result.candidates) - pass1_count
    print(f"\nBest-source distribution: pass1={pass1_count}, pass2={pass2_count}")

    print("\nPer-tier breakdown:")
    print(f"  {'Tier':<10} {'Pass Rate':>10} {'Avg Sem':>10} {'Avg Rebus':>10} {'Count':>6}")
    print(f"  {'─' * 10} {'─' * 10} {'─' * 10} {'─' * 10} {'─' * 6}")
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
            print(
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
        print(f"\nFailed words ({len(failures)}):")
        for c in failures:
            defn = _best_definition(c)[:60]
            guess = c.pass1_guess if c.best_source == "pass1" else c.pass2_guess
            print(f"  {c.word} ({c.tier}, {c.best_source}): '{defn}...' → guessed '{guess}'")


def _append_results_tsv(result: AssessmentResult, description: str) -> None:
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
    args = parser.parse_args()

    result = run_assessment(Path(args.dataset), temperature=args.temperature)
    _print_report(result)
    _append_results_tsv(result, args.description)


if __name__ == "__main__":
    main()
