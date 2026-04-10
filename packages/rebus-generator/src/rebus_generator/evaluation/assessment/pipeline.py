from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from rebus_generator.domain.diacritics import normalize
from rebus_generator.platform.config import VERIFY_CANDIDATE_COUNT
from rebus_generator.platform.io.dex_cache import create_provider
from rebus_generator.platform.io.runtime_logging import log
from rebus_generator.platform.llm.ai_clues import (
    compute_rebus_score,
    create_client,
    generate_definition,
    rate_definition,
    verify_definition_candidates,
)
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.llm.models import PRIMARY_MODEL, SECONDARY_MODEL

from .models import (
    AssessmentResult,
    TierResult,
    WordCandidate,
    best_definition,
    best_guesses,
    best_rated,
    best_rebus,
    best_semantic,
    best_verified,
    pick_best,
)

DATASET_PATH = Path(__file__).resolve().parents[1] / "datasets" / "manifests" / "dataset.json"
RESULTS_PATH = Path("build/evaluation/assessment/results.tsv")


def load_dataset(path: Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def refresh_dataset_dex_definitions(dataset: list[dict]) -> list[dict]:
    if not dataset:
        return dataset
    try:
        dex = create_provider()
        words = [entry["word"] for entry in dataset if entry.get("word")]
        originals = {
            entry["word"]: entry.get("display_word") or entry["word"].lower()
            for entry in dataset
            if entry.get("word")
        }
        dex.prefetch(words, originals=originals, fetch_missing=False)
        refreshed = 0
        updated_dataset: list[dict] = []
        for entry in dataset:
            updated = dict(entry)
            word = entry.get("word")
            live_defs = dex.lookup(word) if word else None
            if live_defs:
                updated["dex_definitions"] = live_defs
                refreshed += 1
            updated_dataset.append(updated)
        if refreshed:
            log(f"Refreshed DEX text for {refreshed}/{len(dataset)} assessment words from DexProvider cache")
        return updated_dataset
    except Exception as exc:
        log(f"DEX refresh unavailable; using dataset snapshot ({exc})")
        return dataset


def git_short_hash() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def generate_for_word(client, word: str, display_word: str, word_type: str, dex_definitions: str, temperature: float | None, model_name: str) -> str:
    try:
        return generate_definition(
            client,
            word,
            display_word,
            "assessment",
            retries=3,
            word_type=word_type,
            dex_definitions=dex_definitions,
            temperature=temperature,
            model=model_name,
        )
    except Exception as exc:
        log(f"  Generate failed: {exc}")
        return "[Definiție negenerată]"


def verify_for_word(client, definition: str, length: int, word_type: str, max_guesses: int, model_name: str) -> list[str]:
    try:
        return verify_definition_candidates(
            client,
            definition,
            length,
            word_type=word_type,
            max_guesses=max_guesses,
            model=model_name,
        ).candidates
    except Exception as exc:
        log(f"  Verify failed: {exc}")
        return []


def rate_for_word(client, word: str, display_word: str, definition: str, length: int, word_type: str, dex_definitions: str, model_name: str) -> tuple[int, int, bool]:
    try:
        rating = rate_definition(
            client,
            word,
            display_word,
            definition,
            length,
            word_type=word_type,
            dex_definitions=dex_definitions,
            model=model_name,
        )
        if rating is not None:
            rebus = compute_rebus_score(rating.guessability_score, rating.creativity_score)
            return rating.semantic_score, rebus, True
        log("  Rating failed (JSON parse error)")
    except Exception as exc:
        log(f"  Rate failed: {exc}")
    return 0, 0, False


def run_assessment(
    dataset_path: Path = DATASET_PATH,
    temperature: float | None = None,
    generate_temperature: float | None = None,
    rewrite_temperature: float | None = None,
    verify_candidates: int = VERIFY_CANDIDATE_COUNT,
) -> AssessmentResult:
    dataset = refresh_dataset_dex_definitions(load_dataset(dataset_path))
    client = create_client()
    result = AssessmentResult()
    phase_times: dict[str, float] = {}
    runtime = LmRuntime(multi_model=True)
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
    count = len(candidates)
    log(f"Running multi-model assessment on {count} words...")
    generate_temperature = generate_temperature if generate_temperature is not None else temperature
    rewrite_temperature = rewrite_temperature if rewrite_temperature is not None else temperature
    total_start = time.monotonic()

    log(f"\n{'='*60}")
    log(f"PHASE 1: {PRIMARY_MODEL.display_name} generates definitions")
    log(f"{'='*60}")
    phase_start = time.monotonic()
    runtime.activate_primary()
    for index, candidate in enumerate(candidates, 1):
        log(f"\n[{index}/{count}] {candidate.word} (tier={candidate.tier}, len={candidate.length})")
        candidate.pass1_definition = generate_for_word(
            client,
            candidate.word,
            candidate.display_word,
            candidate.word_type,
            candidate.dex_definitions,
            generate_temperature,
            PRIMARY_MODEL.model_id,
        )
        if candidate.pass1_definition.startswith("["):
            log("  Skipping — no definition generated")
    phase_times["phase1_generate"] = time.monotonic() - phase_start

    log(f"\n{'='*60}")
    log(f"PHASE 2: {SECONDARY_MODEL.display_name} evaluates + generates")
    log(f"{'='*60}")
    phase_start = time.monotonic()
    runtime.activate_secondary()
    log("\n--- Phase 2a: evaluate pass1 definitions ---")
    for index, candidate in enumerate(candidates, 1):
        if candidate.pass1_definition.startswith("["):
            continue
        log(f"\n[{index}/{count}] {candidate.word} — verify+rate pass1")
        guesses = verify_for_word(client, candidate.pass1_definition, candidate.length, candidate.word_type, verify_candidates, SECONDARY_MODEL.model_id)
        candidate.pass1_guesses = guesses
        candidate.pass1_verified = candidate.word in {normalize(guess) for guess in guesses}
        symbol = "✓" if candidate.pass1_verified else "✗"
        log(f"  {symbol} Guesses: {', '.join(guesses) or '[nimic]'} (expected: {candidate.word})")
        semantic, rebus, rated = rate_for_word(
            client,
            candidate.word,
            candidate.display_word,
            candidate.pass1_definition,
            candidate.length,
            candidate.word_type,
            candidate.dex_definitions,
            SECONDARY_MODEL.model_id,
        )
        candidate.pass1_semantic = semantic
        candidate.pass1_rebus = rebus
        candidate.pass1_rated = rated
        if rated:
            log(f"  Rating: semantic={semantic} rebus={rebus}")
    log("\n--- Phase 2b: generate pass2 definitions ---")
    for index, candidate in enumerate(candidates, 1):
        log(f"\n[{index}/{count}] {candidate.word} (tier={candidate.tier}, len={candidate.length})")
        candidate.pass2_definition = generate_for_word(
            client,
            candidate.word,
            candidate.display_word,
            candidate.word_type,
            candidate.dex_definitions,
            rewrite_temperature,
            SECONDARY_MODEL.model_id,
        )
        if candidate.pass2_definition.startswith("["):
            log("  Skipping — no definition generated")
    phase_times["phase2_eval_and_generate"] = time.monotonic() - phase_start

    log(f"\n{'='*60}")
    log(f"PHASE 3: {PRIMARY_MODEL.display_name} evaluates + selects best")
    log(f"{'='*60}")
    phase_start = time.monotonic()
    runtime.activate_primary()
    log("\n--- Phase 3a: evaluate pass2 definitions ---")
    for index, candidate in enumerate(candidates, 1):
        if candidate.pass2_definition.startswith("["):
            continue
        log(f"\n[{index}/{count}] {candidate.word} — verify+rate pass2")
        guesses = verify_for_word(client, candidate.pass2_definition, candidate.length, candidate.word_type, verify_candidates, PRIMARY_MODEL.model_id)
        candidate.pass2_guesses = guesses
        candidate.pass2_verified = candidate.word in {normalize(guess) for guess in guesses}
        symbol = "✓" if candidate.pass2_verified else "✗"
        log(f"  {symbol} Guesses: {', '.join(guesses) or '[nimic]'} (expected: {candidate.word})")
        semantic, rebus, rated = rate_for_word(
            client,
            candidate.word,
            candidate.display_word,
            candidate.pass2_definition,
            candidate.length,
            candidate.word_type,
            candidate.dex_definitions,
            PRIMARY_MODEL.model_id,
        )
        candidate.pass2_semantic = semantic
        candidate.pass2_rebus = rebus
        candidate.pass2_rated = rated
        if rated:
            log(f"  Rating: semantic={semantic} rebus={rebus}")
    log("\n--- Phase 3b: selecting best definitions ---")
    for candidate in candidates:
        pick_best(candidate)
        log(f"  {candidate.word}: best={candidate.best_source} — '{best_definition(candidate)[:50]}...'")
    phase_times["phase3_eval_and_select"] = time.monotonic() - phase_start

    for candidate in candidates:
        tier = candidate.tier
        if tier not in result.tier_results:
            result.tier_results[tier] = TierResult(tier=tier)
        tier_result = result.tier_results[tier]
        tier_result.total += 1
        if best_verified(candidate):
            tier_result.passed += 1
        if best_rated(candidate):
            tier_result.semantic_sum += best_semantic(candidate)
            tier_result.rebus_sum += best_rebus(candidate)
            tier_result.rated_count += 1

    elapsed = time.monotonic() - total_start
    log(f"\nAssessment completed in {elapsed:.0f}s")
    log("  Phase timings:")
    for phase, phase_time in phase_times.items():
        log(f"    {phase}: {phase_time:.0f}s")
    return result


def write_result_json(result: AssessmentResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Assessment JSON written to {path}")


def append_results_tsv(result: AssessmentResult, description: str) -> None:
    commit = git_short_hash()
    header = "commit\tcomposite\tpass_rate\tavg_semantic\tavg_rebus\tstatus\tdescription\n"
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not RESULTS_PATH.exists():
        with open(RESULTS_PATH, "w", encoding="utf-8") as handle:
            handle.write(header)
    with open(RESULTS_PATH, "a", encoding="utf-8") as handle:
        handle.write(
            f"{commit}\t{result.composite:.1f}\t{result.pass_rate:.3f}\t"
            f"{result.avg_semantic:.1f}\t{result.avg_rebus:.1f}\tkeep\t{description}\n"
        )
    log(f"\nResults appended to {RESULTS_PATH}")
