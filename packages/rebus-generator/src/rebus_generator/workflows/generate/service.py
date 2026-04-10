#!/usr/bin/env python3
"""Generate and publish a batch of rebus puzzles."""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

from rebus_generator.platform.config import VERIFY_CANDIDATE_COUNT
from rebus_generator.platform.io.dex_cache import DexProvider
from rebus_generator.platform.io.metrics import BatchMetric, update_word_difficulty, write_metrics
from rebus_generator.platform.io.runtime_logging import (
    add_llm_debug_argument,
    install_process_logging,
    log,
    path_timestamp,
    set_llm_debug_enabled,
    utc_timestamp,
)
from rebus_generator.platform.io.rust_bridge import (
    Candidate,
    _best_candidate,
    _load_words,
    _metadata_by_word,
    _template_fingerprint,
)
from rebus_generator.platform.llm.llm_client import create_client
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.llm.models import get_active_model_labels
from rebus_generator.domain.pipeline_state import all_working_clues
from rebus_generator.domain.score_helpers import (
    LOCKED_REBUS,
    _compact_log_text,
    _needs_rewrite,
    _restore_best_versions,
    _synthesize_failure_reason,
    _update_best_clue_version,
)
from rebus_generator.domain.size_tuning import DEFAULT_BATCH_SIZES, SUPPORTED_GRID_SIZES
from rebus_generator.workflows.retitle.generate import generate_title_for_final_puzzle_result

from .models import MAX_REWRITE_ROUNDS, PreparedPuzzle
from .prepare import (
    _backfill_generated_model,
    _blocking_clues,
    _choose_metadata_variants_for_puzzle,
    _inject_word_metadata,
    _merge_best_clue_variants,
    _preparation_attempts_for_size,
    _prepare_puzzle_for_publication,
    _rewrite_failed_clues,
)
from .publish import (
    _clear_verification_state,
    _collect_word_metrics,
    _write_text,
    publish_prepared_puzzle,
)
from .quality_gate import _better_prepared_puzzle, _compute_difficulty, _is_publishable


def run_batch(
    sizes: list[int],
    output_root: Path,
    words_path: Path,
    rewrite_rounds: int,
    preparation_attempts: int,
    seed: int | None = None,
    run_dir: Path | None = None,
    multi_model: bool = False,
    verify_candidates: int = VERIFY_CANDIDATE_COUNT,
    runtime: LmRuntime | None = None,
) -> list[dict]:
    raw_words = _load_words(words_path)
    word_metadata = _metadata_by_word(raw_words)
    client = create_client()
    rng_seed = seed if seed is not None else random.SystemRandom().randint(1, 10_000_000)
    setattr(client, "_batch_rng", random.Random(rng_seed))
    if run_dir is None:
        run_dir = output_root / path_timestamp()
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    seen_7x7_templates: set[str] = set()
    batch_start = time.monotonic()
    all_word_metrics = []
    puzzle_metrics = []
    log(f"Batch seed: {rng_seed}")
    runtime = runtime or LmRuntime(multi_model=multi_model)
    if multi_model:
        log(f"Multi-model mode: {' + '.join(get_active_model_labels(multi_model=True))}")

    for index, size in enumerate(sizes, start=1):
        puzzle_dir = run_dir / f"{index:02d}_{size}x{size}"
        puzzle_start = time.monotonic()
        log(f"\n=== Puzzle {index}/{len(sizes)}: {size}x{size} ===")

        prepared = _prepare_puzzle_for_publication(
            index=index,
            total_puzzles=len(sizes),
            size=size,
            raw_words=raw_words,
            words_path=words_path,
            client=client,
            rewrite_rounds=rewrite_rounds,
            preparation_attempts=preparation_attempts,
            seen_template_fingerprints=seen_7x7_templates if size == 7 else None,
            multi_model=multi_model,
            verify_candidates=verify_candidates,
            word_metadata=word_metadata,
            runtime=runtime,
        )
        if prepared.blocking_words:
            log("\n--- Detailed rejection report ---")
            blocking_set = set(prepared.blocking_words)
            try:
                for clue in all_working_clues(prepared.puzzle):
                    if clue.word_normalized in blocking_set:
                        version = clue.active_version()
                        semantic = version.assessment.scores.semantic_exactness
                        rebus = version.assessment.scores.rebus_score
                        reason = _synthesize_failure_reason(clue)
                        log(
                            f"  {clue.word_normalized}: "
                            f"def='{_compact_log_text(version.definition)}' "
                            f"semantic={semantic}/10 rebus={rebus}/10 "
                            f"motiv: {reason}"
                        )
            except (AttributeError, TypeError):
                log(f"  Blocked words: {', '.join(prepared.blocking_words[:12])}")
            log("--- End rejection report ---\n")
            raise RuntimeError(
                f"Could not prepare a publishable {size}x{size} puzzle. "
                f"Missing definitions for: {', '.join(prepared.blocking_words[:12])}"
            )

        manifest_item, puzzle_metric, word_metrics = publish_prepared_puzzle(
            prepared=prepared,
            index=index,
            total_puzzles=len(sizes),
            size=size,
            puzzle_dir=puzzle_dir,
            multi_model=multi_model,
        )
        all_word_metrics.extend(word_metrics)
        puzzle_metric.total_elapsed_ms = int((time.monotonic() - puzzle_start) * 1000)
        puzzle_metrics.append(puzzle_metric)
        manifest_item["seed"] = rng_seed
        manifest.append(manifest_item)
        _write_text(run_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    batch_metric = BatchMetric(
        timestamp=utc_timestamp(),
        seed=rng_seed,
        models_used=get_active_model_labels(multi_model=multi_model),
        puzzles=puzzle_metrics,
        word_metrics=all_word_metrics,
        total_elapsed_ms=int((time.monotonic() - batch_start) * 1000),
    )
    write_metrics(batch_metric, run_dir / "metrics.json")
    update_word_difficulty(all_word_metrics, words_path.parent / "word_difficulty.json")
    return manifest


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
