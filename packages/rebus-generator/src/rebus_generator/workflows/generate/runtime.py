from __future__ import annotations

import json
import random
import time
from pathlib import Path

from rebus_generator.platform.config import VERIFY_CANDIDATE_COUNT
from rebus_generator.platform.io.metrics import BatchMetric, update_word_difficulty, write_metrics
from rebus_generator.platform.io.runtime_logging import log, path_timestamp, utc_timestamp
from rebus_generator.platform.io.rust_bridge import Candidate, _best_candidate, _load_words, _metadata_by_word, _template_fingerprint
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
from .publish import _clear_verification_state, _collect_word_metrics, publish_prepared_puzzle
from .quality_gate import (
    _better_prepared_puzzle,
    _compute_difficulty,
    _describe_publishability_failure,
    _is_publishable,
)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


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
        if not _is_publishable(prepared):
            log("\n--- Detailed rejection report ---")
            try:
                for clue in all_working_clues(prepared.puzzle):
                    if (
                        clue.word_normalized in set(prepared.blocking_words)
                        or clue.word_normalized in set(prepared.assessment.incomplete_words)
                        or clue.active_version().assessment.verified is not True
                    ):
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
                log(f"  Failure detail: {_describe_publishability_failure(prepared)}")
            log("--- End rejection report ---\n")
            raise RuntimeError(
                f"Could not prepare a publishable {size}x{size} puzzle. "
                f"Quality gate failed: {_describe_publishability_failure(prepared)}"
            )

        manifest_item, puzzle_metric, word_metrics = publish_prepared_puzzle(
            prepared=prepared,
            index=index,
            total_puzzles=len(sizes),
            size=size,
            puzzle_dir=puzzle_dir,
            client=client,
            runtime=runtime,
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


__all__ = [
    "Candidate",
    "LOCKED_REBUS",
    "MAX_REWRITE_ROUNDS",
    "PreparedPuzzle",
    "_backfill_generated_model",
    "_best_candidate",
    "_better_prepared_puzzle",
    "_blocking_clues",
    "_choose_metadata_variants_for_puzzle",
    "_clear_verification_state",
    "_collect_word_metrics",
    "_compact_log_text",
    "_compute_difficulty",
    "_inject_word_metadata",
    "_is_publishable",
    "_load_words",
    "_merge_best_clue_variants",
    "_metadata_by_word",
    "_needs_rewrite",
    "_preparation_attempts_for_size",
    "_prepare_puzzle_for_publication",
    "_restore_best_versions",
    "_rewrite_failed_clues",
    "_synthesize_failure_reason",
    "_template_fingerprint",
    "_update_best_clue_version",
    "generate_title_for_final_puzzle_result",
    "publish_prepared_puzzle",
    "run_batch",
]
