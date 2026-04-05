#!/usr/bin/env python3
"""Generate and publish a batch of rebus puzzles."""

from __future__ import annotations

import argparse
import copy
import json
import random
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import VERIFY_CANDIDATE_COUNT
from .core.ai_clues import (
    RATE_MIN_REBUS,
    RATE_MIN_SEMANTIC,
    choose_better_clue_variant,
    choose_better_puzzle_variant,
    compute_rebus_score,
    create_client,
    generate_definition,
    rewrite_definition,
)
from .core.dex_cache import DexProvider
from .core.lm_runtime import LmRuntime
from .core.markdown_io import (
    ClueEntry,
    parse_markdown,
    write_filled_grid,
    write_grid_template,
    write_with_definitions,
)
from .core.metrics import (
    BatchMetric,
    PuzzleMetric,
    WordMetric,
    update_word_difficulty,
    write_metrics,
)
from .core.model_manager import (
    PRIMARY_MODEL,
    get_active_model_labels,
)
from .core.pipeline_state import (
    ClueAssessment,
    ClueCandidateVersion,
    ClueFailureReason,
    ClueScores,
    PuzzleAssessment,
    WorkingClue,
    WorkingPuzzle,
    all_working_clues,
    puzzle_from_working_state,
    set_current_definition,
    working_clue_from_entry,
    working_puzzle_from_puzzle,
)
from .core.plateau import has_plateaued
from .core.puzzle_metrics import (
    build_puzzle_description,
    puzzle_metadata_payload,
    score_puzzle_state,
)
from .core.quality import QualityReport
from .core.rewrite_engine import run_rewrite_loop
from .core.runtime_logging import (
    install_process_logging,
    log,
    path_timestamp,
    utc_timestamp,
)
from .core.score_helpers import (
    LOCKED_REBUS,
    LOCKED_SEMANTIC,
    MAX_CONSECUTIVE_FAILURES,
    PLATEAU_LOOKBACK,
    _coerce_working_clue,
    _compact_log_text,
    _extract_rebus_score,
    _extract_semantic_score,
    _is_locked_clue,
    _needs_rewrite,
    _restore_best_versions,
    _synthesize_failure_reason,
    _update_best_clue_version,
)
from .core.selection_engine import choose_clue_version, choose_puzzle_assessment
from .core.size_tuning import (
    DEFAULT_BATCH_SIZES,
    SUPPORTED_GRID_SIZES,
    get_min_preparation_attempts,
)
from .phases.activate import set_published
from .phases.define import (
    generate_definitions_for_puzzle,
    generate_definitions_for_state,
)
from .phases.download import run as download_words
from .phases.theme import generate_title_for_final_puzzle_result
from .phases.upload import upload_puzzle
from .phases.verify import rate_working_puzzle, verify_working_puzzle


@dataclass
class Candidate:
    score: float
    report: QualityReport
    template: list[list[bool]]
    markdown: str
    metadata: dict[str, list[dict]] = field(default_factory=dict)
    stats: dict[str, int | float] = field(default_factory=dict)


@dataclass
class PreparedPuzzle:
    title: str
    title_score: int
    candidate: Candidate
    puzzle: object
    first_passed: int
    final_passed: int
    total: int
    definition_score: float
    blocking_words: list[str]
    assessment: PuzzleAssessment = field(default_factory=PuzzleAssessment)


PUZZLE_TIEBREAK_DELTA = 0.25
MIN_PUBLISHABLE_PASS_RATE = 0.1
MAX_REWRITE_ROUNDS = 30
REPO_ROOT = Path(__file__).resolve().parent.parent
RUST_ENGINE_BINARY = (
    REPO_ROOT / "crossword_engine" / "target" / "release" / "crossword_phase1"
)
RUST_ENGINE_DEBUG_BINARY = (
    REPO_ROOT / "crossword_engine" / "target" / "debug" / "crossword_phase1"
)


def _load_words(words_path: Path) -> list[dict]:
    if not words_path.exists():
        words_path.parent.mkdir(parents=True, exist_ok=True)
        download_words("-", str(words_path))
    with open(words_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _metadata_by_word(raw_words: list[dict]) -> dict[str, list[dict]]:
    metadata: dict[str, list[dict]] = {}
    for word in raw_words:
        normalized = word.get("normalized", "")
        if not normalized:
            continue
        metadata.setdefault(normalized, []).append(word)
    return metadata


def _normalize_metadata_pool(
    metadata: dict[str, dict] | dict[str, list[dict]] | None,
) -> dict[str, list[dict]]:
    if not metadata:
        return {}
    normalized: dict[str, list[dict]] = {}
    for word, value in metadata.items():
        if isinstance(value, list):
            normalized[word] = [dict(entry) for entry in value]
        else:
            normalized[word] = [dict(value)]
    return normalized


def _rust_binary_path() -> Path:
    if RUST_ENGINE_BINARY.exists():
        return RUST_ENGINE_BINARY
    if RUST_ENGINE_DEBUG_BINARY.exists():
        return RUST_ENGINE_DEBUG_BINARY
    raise RuntimeError(
        "Rust phase-1 binary missing. Run `run_batch_loop.sh` or "
        "`cargo build --release --manifest-path crossword_engine/Cargo.toml` first."
    )


def _quality_report_from_payload(payload: dict) -> QualityReport:
    return QualityReport(
        score=float(payload.get("score", 0.0)),
        word_count=int(payload.get("word_count", 0)),
        average_length=float(payload.get("average_length", 0.0)),
        average_rarity=float(payload.get("average_rarity", 0.0)),
        two_letter_words=int(payload.get("two_letter_words", 0)),
        three_letter_words=int(payload.get("three_letter_words", 0)),
        high_rarity_words=int(payload.get("high_rarity_words", 0)),
        uncommon_letter_words=int(payload.get("uncommon_letter_words", 0)),
        friendly_words=int(payload.get("friendly_words", 0)),
        max_rarity=int(payload.get("max_rarity", 0)),
        average_definability=float(payload.get("average_definability", 0.0)),
    )


def _render_markdown_from_rust_payload(
    title: str,
    template: list[list[bool]],
    filled_grid_payload: list[list[str | None]],
    slots_payload: list[dict],
    words_payload: list[dict],
) -> str:
    size = len(template)
    grid_out: list[list[str | None]] = []
    for row_index in range(size):
        rendered_row: list[str | None] = []
        for col_index in range(size):
            if not template[row_index][col_index]:
                rendered_row.append(None)
            else:
                rendered_row.append(filled_grid_payload[row_index][col_index])
        grid_out.append(rendered_row)

    h_words: list[list[str]] = [[] for _ in range(size)]
    h_originals: list[list[str]] = [[] for _ in range(size)]
    v_words: list[list[str]] = [[] for _ in range(size)]
    v_originals: list[list[str]] = [[] for _ in range(size)]
    word_by_slot = {int(word["slot_id"]): word for word in words_payload}
    for slot in slots_payload:
        slot_id = int(slot["id"])
        word = word_by_slot[slot_id]
        original = word.get("normalized", "")
        if slot["direction"] == "H":
            h_words[int(slot["start_row"])].append(word["normalized"])
            h_originals[int(slot["start_row"])].append(original)
        else:
            v_words[int(slot["start_col"])].append(word["normalized"])
            v_originals[int(slot["start_col"])].append(original)

    return write_filled_grid(
        size, grid_out, h_words, v_words, h_originals, v_originals, title=title
    )


def _best_candidate_rust(
    size: int,
    title: str,
    *,
    words_path: Path,
    metadata: dict[str, list[dict]],
    rng: random.Random,
    preparation_attempts: int = 1,
) -> Candidate:
    seed = rng.randint(1, 10_000_000)
    command = [
        str(_rust_binary_path()),
        "--size",
        str(size),
        "--words",
        str(words_path),
        "--seed",
        str(seed),
        "--preparation-attempts",
        str(max(1, preparation_attempts)),
    ]
    result = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stderr:
        log(result.stderr.rstrip("\n"))
    if result.returncode != 0:
        raise RuntimeError(
            f"Rust phase-1 failed for {size}x{size} with exit {result.returncode}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Rust phase-1 returned invalid JSON: {exc}") from exc

    template = [[bool(cell) for cell in row] for row in payload["template"]]
    report = _quality_report_from_payload(payload["quality"])
    markdown = _render_markdown_from_rust_payload(
        title,
        template,
        payload["filled_grid"],
        payload["slots"],
        payload["words"],
    )
    stats = dict(payload.get("stats", {}))
    log(
        f"  Rust phase-1 {size}x{size}: score={report.score:.1f} "
        f"two={report.two_letter_words} three={report.three_letter_words} "
        f"elapsed_ms={int(stats.get('elapsed_ms', 0) or 0)} "
        f"nodes={int(stats.get('solver_nodes', 0) or 0)} "
        f"solved={int(stats.get('solved_candidates', 0) or 0)}"
    )
    return Candidate(
        score=report.score,
        report=report,
        template=template,
        markdown=markdown,
        metadata=_normalize_metadata_pool(metadata),
        stats=stats,
    )


def _best_candidate(
    size: int,
    title: str,
    raw_words: list[dict],
    rng: random.Random,
    seen_template_fingerprints: set[str] | None = None,
    *,
    words_path: Path | None = None,
    word_metadata: dict[str, dict] | dict[str, list[dict]] | None = None,
    preparation_attempts: int = 1,
) -> Candidate:
    if words_path is None:
        raise ValueError("Rust phase-1 requires `words_path`.")
    candidate = _best_candidate_rust(
        size,
        title,
        words_path=words_path,
        metadata=_normalize_metadata_pool(word_metadata)
        or _metadata_by_word(raw_words),
        rng=rng,
        preparation_attempts=preparation_attempts,
    )
    if seen_template_fingerprints is not None and size == 7:
        seen_template_fingerprints.add(_template_fingerprint(candidate.template))
    return candidate


def _blocking_clues(puzzle: WorkingPuzzle) -> list[WorkingClue]:
    return [
        clue
        for clue in all_working_clues(puzzle)
        if not clue.active_version().definition
        or clue.active_version().definition.startswith("[")
    ]


def _template_fingerprint(template: list[list[bool]]) -> str:
    return "|".join("".join("." if cell else "#" for cell in row) for row in template)


def _choose_metadata_variants_for_puzzle(
    puzzle, metadata: dict[str, list[dict]]
) -> dict[str, dict]:
    resolved: dict[str, dict] = {}
    clues = list(getattr(puzzle, "horizontal_clues", [])) + list(
        getattr(puzzle, "vertical_clues", [])
    )
    for clue in clues:
        normalized = clue.word_normalized
        if normalized not in resolved:
            options = metadata.get(normalized) or []
            if options:
                resolved[normalized] = copy.deepcopy(random.choice(options))
            else:
                resolved[normalized] = {
                    "normalized": normalized,
                    "original": normalized.lower(),
                    "word_type": "",
                }
        clue.word_original = resolved[normalized].get("original") or normalized.lower()
    return resolved


def _inject_word_metadata(state: WorkingPuzzle, metadata: dict[str, dict]) -> None:
    for clue in all_working_clues(state):
        word_meta = metadata.get(clue.word_normalized, {})
        clue.word_type = word_meta.get("word_type", "")
        clue.word_original = word_meta.get("original") or clue.word_normalized.lower()
    state.metadata["resolved_word_metadata"] = copy.deepcopy(metadata)


def _preparation_attempts_for_size(size: int, requested_attempts: int) -> int:
    return max(requested_attempts, get_min_preparation_attempts(size))


def _merge_best_clue_variants(
    best_clues: list[ClueEntry],
    current_clues: list[ClueEntry],
    client=None,
    model_name: str | None = None,
) -> list[ClueEntry]:
    merged: list[ClueEntry] = []
    for best_clue, current_clue in zip(best_clues, current_clues):
        best_working = _coerce_working_clue(best_clue)
        current_working = _coerce_working_clue(current_clue)

        def _tiebreak(a_text: str, b_text: str) -> str:
            if client is None:
                return "A"
            return choose_better_clue_variant(
                client,
                best_working.word_normalized,
                len(best_working.word_normalized),
                a_text,
                b_text,
                model=model_name or PRIMARY_MODEL.model_id,
            )

        chosen, _ = choose_clue_version(
            best_working.active_version(),
            current_working.active_version(),
            tiebreaker=_tiebreak,
        )
        if client is not None:
            log(
                f"  Tie-break definiție {best_working.word_normalized}: "
                f"A='{_compact_log_text(best_working.active_version().definition)}' | "
                f"B='{_compact_log_text(current_working.active_version().definition)}' | "
                f"aleasă='{_compact_log_text(chosen.definition)}'"
            )
        chosen_working = copy.deepcopy(
            best_working
            if chosen.definition == best_working.active_version().definition
            else current_working
        )
        chosen_working.best = copy.deepcopy(chosen)
        chosen_working.current = copy.deepcopy(chosen)
        merged.append(
            puzzle_from_working_state(
                WorkingPuzzle("", 0, [], [chosen_working], [])
            ).horizontal_clues[0]
        )
    return merged


def _backfill_generated_model(puzzle: WorkingPuzzle, model_label: str) -> None:
    for clue in all_working_clues(puzzle):
        if clue.current.definition and not clue.current.generated_by:
            clue.current.generated_by = model_label


def _compute_difficulty(size: int, report: QualityReport) -> int:
    """Approximate difficulty from grid size and short-word burden, not rarity."""
    if size <= 7:
        difficulty = 2
    elif size <= 9:
        difficulty = 3
    elif size <= 11:
        difficulty = 4
    else:
        difficulty = 5
    if report.two_letter_words >= max(4, size // 2):
        difficulty -= 1
    if report.average_length >= 6.0 and report.two_letter_words <= 2:
        difficulty += 1
    return max(1, min(5, difficulty))


def _is_publishable(prepared: PreparedPuzzle) -> bool:
    return (
        not prepared.blocking_words
        and prepared.assessment.pass_rate >= MIN_PUBLISHABLE_PASS_RATE
    )


def _better_prepared_puzzle(
    best: PreparedPuzzle | None,
    candidate: PreparedPuzzle,
    client=None,
    runtime: LmRuntime | None = None,
) -> PreparedPuzzle:
    if best is None:
        return candidate

    best_publishable = _is_publishable(best)
    candidate_publishable = _is_publishable(candidate)
    if candidate_publishable != best_publishable:
        return candidate if candidate_publishable else best

    score_delta = (
        candidate.assessment.definition_score - best.assessment.definition_score
    )
    verified_delta = (
        candidate.assessment.verified_count - best.assessment.verified_count
    )
    if verified_delta != 0:
        return candidate if verified_delta > 0 else best
    if abs(score_delta) > PUZZLE_TIEBREAK_DELTA:
        if candidate.assessment.min_rebus != best.assessment.min_rebus:
            return (
                candidate
                if candidate.assessment.min_rebus > best.assessment.min_rebus
                else best
            )
        return candidate if score_delta > 0 else best

    def _tiebreak(a_summary: str, b_summary: str) -> str:
        if client is None:
            return "A"
        if runtime is not None:
            model = runtime.activate_primary()
            model_id = model.model_id
        else:
            model_id = PRIMARY_MODEL.model_id
        return choose_better_puzzle_variant(
            client, a_summary, b_summary, model=model_id
        )

    winner, decision = choose_puzzle_assessment(
        best.assessment, candidate.assessment, tiebreaker=_tiebreak
    )
    if decision.used_tiebreak:
        chosen = candidate if winner == "B" else best
        log(
            "Puzzle tie-break: "
            f"A='{_compact_log_text(decision.a_summary)}' | "
            f"B='{_compact_log_text(decision.b_summary)}' | "
            f"câștigă {decision.winner} | "
            f"ales='{_compact_log_text(decision.winner_summary)}'"
        )
        return chosen

    return candidate if score_delta > 0 else best


def _rewrite_failed_clues(
    puzzle: WorkingPuzzle,
    client,
    rounds: int,
    multi_model: bool = False,
    dex: DexProvider | None = None,
    verify_candidates: int = VERIFY_CANDIDATE_COUNT,
    runtime: LmRuntime | None = None,
) -> tuple[int, int, int]:
    result = run_rewrite_loop(
        puzzle,
        client,
        rounds=rounds,
        theme=puzzle.title or "Puzzle intern",
        multi_model=multi_model,
        dex=dex,
        verify_candidates=verify_candidates,
        runtime=runtime,
    )
    puzzle.metadata["rewrite_model_switches"] = result.model_switches
    return result.initial_passed, result.final_passed, result.total


def _prepare_puzzle_for_publication(
    index: int,
    total_puzzles: int,
    size: int,
    raw_words: list[dict],
    words_path: Path | None,
    client,
    rewrite_rounds: int,
    preparation_attempts: int,
    seen_template_fingerprints: set[str] | None = None,
    multi_model: bool = False,
    verify_candidates: int = VERIFY_CANDIDATE_COUNT,
    word_metadata: dict[str, dict] | None = None,
    runtime: LmRuntime | None = None,
) -> PreparedPuzzle:
    best_prepared: PreparedPuzzle | None = None
    effective_attempts = _preparation_attempts_for_size(size, preparation_attempts)

    for attempt_index in range(1, effective_attempts + 1):
        if attempt_index > 1:
            log(
                f"Retrying puzzle {index}/{total_puzzles} ({size}x{size}), "
                f"attempt {attempt_index}/{effective_attempts}..."
            )

        provisional_title = f"Puzzle {index}"
        rng = getattr(client, "_batch_rng", random.Random(0))
        candidate = _best_candidate(
            size,
            provisional_title,
            raw_words,
            rng=rng,
            seen_template_fingerprints=seen_template_fingerprints,
            words_path=words_path,
            word_metadata=word_metadata,
            preparation_attempts=1,
        )
        puzzle = parse_markdown(candidate.markdown)
        puzzle.title = ""
        resolved_metadata = _choose_metadata_variants_for_puzzle(
            puzzle, candidate.metadata
        )
        generate_definitions_for_puzzle(
            puzzle,
            client,
            metadata=resolved_metadata,
            runtime=runtime,
            model_config=PRIMARY_MODEL,
        )
        state = working_puzzle_from_puzzle(puzzle, split_compound=False)
        _backfill_generated_model(state, PRIMARY_MODEL.display_name)
        _inject_word_metadata(state, resolved_metadata)
        # Load dex definitions for rewrite rounds
        _dex = DexProvider.for_puzzle(state)
        first_passed, final_passed, total = _rewrite_failed_clues(
            state,
            client,
            rewrite_rounds,
            multi_model=multi_model,
            dex=_dex,
            verify_candidates=verify_candidates,
            runtime=runtime,
        )
        _restore_best_versions(state)
        state.assessment = score_puzzle_state(state, candidate.report)
        blockers = _blocking_clues(state)
        rendered_for_title = puzzle_from_working_state(state)
        title_result = generate_title_for_final_puzzle_result(
            rendered_for_title,
            client=client,
            rate_client=client,
            runtime=runtime,
            multi_model=multi_model,
        )
        title = title_result.title
        state.title = title
        log(f"Titlu final: {title}")
        prepared = PreparedPuzzle(
            title=title,
            title_score=title_result.score,
            candidate=candidate,
            puzzle=copy.deepcopy(state),
            first_passed=first_passed,
            final_passed=final_passed,
            total=total,
            definition_score=state.assessment.definition_score,
            blocking_words=[clue.word_normalized for clue in blockers],
            assessment=copy.deepcopy(state.assessment),
        )
        best_prepared = _better_prepared_puzzle(
            best_prepared, prepared, client=client, runtime=runtime
        )

        if blockers:
            log(
                "Rejected puzzle after quality gate: "
                + ", ".join(clue.word_normalized for clue in blockers[:10])
            )
        elif _is_publishable(best_prepared):
            log(
                f"  Puzzle publicabil la tentativa {attempt_index}/{effective_attempts}"
            )
            break

    if best_prepared is None:
        raise RuntimeError(f"Failed to prepare any {size}x{size} puzzle candidate")
    return best_prepared


def _collect_word_metrics(puzzle: WorkingPuzzle) -> list[WordMetric]:
    metrics = []
    for clue in all_working_clues(puzzle):
        initial_version = clue.history[0] if clue.history else clue.current
        version = clue.active_version()
        failure_reason = version.assessment.failure_reason
        semantic = version.assessment.scores.semantic_exactness
        targeting = version.assessment.scores.answer_targeting
        creativity = version.assessment.scores.creativity
        rebus = version.assessment.scores.rebus_score
        initial_semantic = initial_version.assessment.scores.semantic_exactness
        initial_rebus = initial_version.assessment.scores.rebus_score
        rewrite_attempted = any(v.round_index > 0 for v in clue.history)
        metrics.append(
            WordMetric(
                word=clue.word_normalized,
                length=len(clue.word_normalized),
                word_type=clue.word_type,
                definition_rounds=len(clue.history),
                initial_verified=initial_version.assessment.verified,
                final_verified=version.assessment.verified is True,
                semantic_score=semantic,
                guessability_score=targeting,
                creativity_score=creativity,
                rebus_score=rebus,
                semantic_delta=(semantic - initial_semantic)
                if semantic is not None and initial_semantic is not None
                else None,
                rebus_delta=(rebus - initial_rebus)
                if rebus is not None and initial_rebus is not None
                else None,
                rewrite_attempted=rewrite_attempted,
                rewrite_changed_definition=version.definition
                != initial_version.definition,
                rewrite_rescued_verify=(
                    initial_version.assessment.verified is False
                    and version.assessment.verified is True
                ),
                was_blocker=_needs_rewrite(clue),
                english_meaning_detected=False,
                wrong_guess=version.assessment.wrong_guess,
                verify_candidates=list(version.assessment.verify_candidates),
                failure_kind=failure_reason.kind if failure_reason else "",
                failure_message=failure_reason.message if failure_reason else "",
                rarity_only_override=version.assessment.rarity_only_override,
                form_mismatch=version.assessment.form_mismatch,
                form_mismatch_detail=version.assessment.form_mismatch_detail,
                model_generated=version.generated_by,
                model_verified=version.assessment.verified_by,
                model_rated=version.assessment.rated_by,
            )
        )
    return metrics


def _clear_verification_state(puzzle: WorkingPuzzle):
    clean = copy.deepcopy(puzzle)
    for clue in all_working_clues(clean):
        version = clue.active_version()
        version.assessment = ClueAssessment()
    return clean


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


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
) -> list[dict]:
    raw_words = _load_words(words_path)
    word_metadata = _metadata_by_word(raw_words)
    client = create_client()
    rng_seed = (
        seed if seed is not None else random.SystemRandom().randint(1, 10_000_000)
    )
    batch_rng = random.Random(rng_seed)
    setattr(client, "_batch_rng", batch_rng)
    if run_dir is None:
        run_dir = output_root / path_timestamp()
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    seen_7x7_templates: set[str] = set()
    batch_start = time.monotonic()
    all_word_metrics: list[WordMetric] = []
    puzzle_metrics: list[PuzzleMetric] = []
    log(f"Batch seed: {rng_seed}")
    runtime = LmRuntime(multi_model=multi_model)
    runtime.activate_primary()
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

        puzzle_elapsed_ms = int((time.monotonic() - puzzle_start) * 1000)

        template_path = puzzle_dir / "template.md"
        filled_path = puzzle_dir / "filled.md"
        rendered_puzzle = puzzle_from_working_state(prepared.puzzle)
        _write_text(
            template_path, write_grid_template(size, prepared.candidate.template)
        )
        _write_text(filled_path, write_with_definitions(rendered_puzzle))

        defs_puzzle = _clear_verification_state(prepared.puzzle)
        defs_path = puzzle_dir / "defs.md"
        verified_path = puzzle_dir / "verified.md"
        _write_text(
            defs_path, write_with_definitions(puzzle_from_working_state(defs_puzzle))
        )
        _write_text(verified_path, write_with_definitions(rendered_puzzle))

        non_preset_rebus = [
            c.active_version().assessment.scores.rebus_score or 0
            for c in all_working_clues(prepared.puzzle)
        ]
        min_rebus = min(non_preset_rebus) if non_preset_rebus else 10
        models_used_desc = get_active_model_labels(multi_model=multi_model)
        description = build_puzzle_description(prepared.assessment, models_used_desc)
        difficulty = _compute_difficulty(size, prepared.candidate.report)
        puzzle_id = upload_puzzle(
            puzzle_from_working_state(defs_puzzle),
            difficulty=difficulty,
            description=description,
            metadata={
                **puzzle_metadata_payload(prepared.assessment, description=description),
                "title_score": prepared.title_score,
            },
        )
        set_published(puzzle_id, True)

        word_metrics = _collect_word_metrics(prepared.puzzle)
        all_word_metrics.extend(word_metrics)
        clues = all_working_clues(prepared.puzzle)
        verified_count = sum(
            1 for c in clues if c.active_version().assessment.verified is True
        )
        rewrite_attempted_words = sum(1 for wm in word_metrics if wm.rewrite_attempted)
        rewrite_changed_words = sum(
            1 for wm in word_metrics if wm.rewrite_changed_definition
        )
        rewrite_rescued_words = sum(
            1 for wm in word_metrics if wm.rewrite_rescued_verify
        )
        semantic_scores = [
            c.active_version().assessment.scores.semantic_exactness or 0 for c in clues
        ]
        guess_scores = [
            c.active_version().assessment.scores.answer_targeting or 0 for c in clues
        ]
        rebus_scores = [
            c.active_version().assessment.scores.rebus_score or 0 for c in clues
        ]
        creativity_scores = [
            c.active_version().assessment.scores.creativity or 0 for c in clues
        ]
        puzzle_metrics.append(
            PuzzleMetric(
                size=size,
                fill_elapsed_ms=int(prepared.candidate.stats.get("elapsed_ms", 0) or 0),
                word_count=len(clues),
                avg_word_length=prepared.candidate.report.average_length,
                avg_rarity=prepared.candidate.report.average_rarity,
                definition_first_pass_rate=prepared.first_passed / prepared.total
                if prepared.total
                else 0.0,
                definition_final_pass_rate=prepared.final_passed / prepared.total
                if prepared.total
                else 0.0,
                avg_semantic=sum(semantic_scores) / len(semantic_scores)
                if semantic_scores
                else 0.0,
                avg_guessability=sum(guess_scores) / len(guess_scores)
                if guess_scores
                else 0.0,
                avg_creativity=sum(creativity_scores) / len(creativity_scores)
                if creativity_scores
                else 0.0,
                avg_rebus=sum(rebus_scores) / len(rebus_scores)
                if rebus_scores
                else 0.0,
                min_rebus=min_rebus,
                rewrite_attempted_words=rewrite_attempted_words,
                rewrite_changed_words=rewrite_changed_words,
                rewrite_rescued_words=rewrite_rescued_words,
                blocker_count=len(prepared.blocking_words),
                blocker_words=prepared.blocking_words,
                model_switches=int(
                    prepared.puzzle.metadata.get("rewrite_model_switches", 0) or 0
                ),
                total_elapsed_ms=puzzle_elapsed_ms,
            )
        )

        manifest.append(
            {
                "index": index,
                "size": size,
                "title": prepared.title,
                "puzzle_id": puzzle_id,
                "score": prepared.candidate.score,
                "quality": prepared.candidate.report.to_dict(),
                "phase1_stats": prepared.candidate.stats,
                "verification_first_passed": prepared.first_passed,
                "verification_final_passed": prepared.final_passed,
                "verification_passed": prepared.final_passed,
                "verification_total": prepared.total,
                "output_dir": str(puzzle_dir),
                "template_path": str(template_path),
                "seed": rng_seed,
                "template_fingerprint": _template_fingerprint(
                    prepared.candidate.template
                ),
            }
        )
        _write_text(
            run_dir / "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2),
        )

    batch_elapsed_ms = int((time.monotonic() - batch_start) * 1000)
    models_used = get_active_model_labels(multi_model=multi_model)
    batch_metric = BatchMetric(
        timestamp=utc_timestamp(),
        seed=rng_seed,
        models_used=models_used,
        puzzles=puzzle_metrics,
        word_metrics=all_word_metrics,
        total_elapsed_ms=batch_elapsed_ms,
    )
    write_metrics(batch_metric, run_dir / "metrics.json")
    difficulty_path = words_path.parent / "word_difficulty.json"
    update_word_difficulty(all_word_metrics, difficulty_path)

    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate and publish a batch of rebus puzzles."
    )
    parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=list(DEFAULT_BATCH_SIZES),
        choices=list(SUPPORTED_GRID_SIZES),
        help="Puzzle sizes to generate in order",
    )
    parser.add_argument(
        "--words",
        default="generator/output/words.json",
        help="Path to words.json cache",
    )
    parser.add_argument(
        "--output-root",
        default="generator/output/batch",
        help="Directory where batch artifacts are written",
    )
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
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional RNG seed for reproducible batch generation",
    )
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
