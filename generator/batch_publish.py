#!/usr/bin/env python3
"""Generate and publish a batch of rebus puzzles."""

from __future__ import annotations

import argparse
import copy
import json
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

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
from .core.metrics import (
    BatchMetric,
    PuzzleMetric,
    WordMetric,
    update_word_difficulty,
    write_metrics,
)
from .core.model_manager import (
    PRIMARY_MODEL,
    SECONDARY_MODEL,
    ensure_model_loaded,
    switch_model,
)
from .core.grid_template import generate_incremental_template, generate_procedural_template, validate_template
from .core.plateau import has_plateaued
from .core.markdown_io import (
    ClueEntry,
    parse_markdown,
    write_filled_grid,
    write_grid_template,
    write_with_definitions,
)
from .core.quality import PRESET_DEFINITIONS, QualityReport, filter_word_records, score_words
from .core.size_tuning import (
    DEFAULT_BATCH_SIZES,
    SUPPORTED_GRID_SIZES,
    SizeSettings,
    build_relaxed_variants,
    get_min_preparation_attempts,
)
from .core.pipeline_state import (
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
from .core.selection_engine import choose_clue_version, choose_puzzle_assessment
from .core.slot_extractor import Slot, extract_slots
from .core.word_index import WordEntry, WordIndex
from .core.constraint_solver import solve
from .phases.activate import set_published
from .phases.define import generate_definitions_for_puzzle, generate_definitions_for_state
from .phases.download import run as download_words
from .phases.theme import generate_title_for_final_puzzle
from .phases.upload import upload_puzzle
from .phases.verify import rate_working_puzzle, verify_working_puzzle


class TeeStream:
    """Write stdout/stderr both to console and to a log file."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
            stream.flush()
        return len(data)

    def flush(self):
        for stream in self.streams:
            stream.flush()


@dataclass
class Candidate:
    score: float
    report: QualityReport
    template: list[list[bool]]
    markdown: str
    metadata: dict[str, dict] = field(default_factory=dict)


@dataclass
class PreparedPuzzle:
    title: str
    candidate: Candidate
    puzzle: object
    passed: int
    total: int
    definition_score: float
    blocking_words: list[str]
    assessment: PuzzleAssessment = field(default_factory=PuzzleAssessment)


LOCKED_SEMANTIC = 9
LOCKED_REBUS = 8
PUZZLE_TIEBREAK_DELTA = 0.25
MAX_CONSECUTIVE_FAILURES = 5
PLATEAU_LOOKBACK = 7
MAX_REWRITE_ROUNDS = 30


def _load_words(words_path: Path) -> list[dict]:
    if not words_path.exists():
        words_path.parent.mkdir(parents=True, exist_ok=True)
        download_words("-", str(words_path))
    with open(words_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_index(raw_words: list[dict], size: int, settings: SizeSettings) -> tuple[WordIndex, dict[str, dict]]:
    filtered = filter_word_records(raw_words, max_rarity=settings.max_rarity, max_length=size)
    metadata = {word["normalized"]: word for word in filtered}
    entries = [WordEntry(word["normalized"], word["original"]) for word in filtered]
    return WordIndex(entries), metadata


def _slot_capacity_ok(slots: list[Slot], word_index: WordIndex, settings: SizeSettings) -> bool:
    if sum(1 for slot in slots if slot.length == 2) > settings.max_two_letter_slots:
        return False
    for slot in slots:
        required = 1 if slot.length >= 10 else settings.min_candidates_per_slot
        if word_index.count_matching([None] * slot.length) < required:
            return False
    return True


def _render_filled_markdown(
    size: int,
    template: list[list[bool]],
    slots: list[Slot],
    assignment: dict[int, WordEntry],
    title: str,
) -> str:
    grid_out: list[list[str | None]] = []
    for row in range(size):
        rendered_row = []
        for col in range(size):
            rendered_row.append(None)
        grid_out.append(rendered_row)

    h_words: list[list[str]] = [[] for _ in range(size)]
    h_originals: list[list[str]] = [[] for _ in range(size)]
    v_words: list[list[str]] = [[] for _ in range(size)]
    v_originals: list[list[str]] = [[] for _ in range(size)]

    for slot in slots:
        word = assignment[slot.id]
        for index, (row, col) in enumerate(slot.cells):
            grid_out[row][col] = word.normalized[index]
        if slot.direction == "H":
            h_words[slot.start_row].append(word.normalized)
            h_originals[slot.start_row].append(word.original)
        else:
            v_words[slot.start_col].append(word.normalized)
            v_originals[slot.start_col].append(word.original)

    return write_filled_grid(size, grid_out, h_words, v_words, h_originals, v_originals, title=title)


def _generate_candidate(
    size: int,
    settings: SizeSettings,
    word_index: WordIndex,
    metadata: dict[str, dict],
    title: str,
    rng: random.Random | None = None,
    seen_template_fingerprints: set[str] | None = None,
    template: list[list[bool]] | None = None,
) -> Candidate | None:
    if rng is None:
        rng = random.Random(0)
    if template is None:
        template = _choose_template(size, settings, rng)
    if template is None:
        return None
    valid, _reason = validate_template(template)
    if not valid:
        return None
    if seen_template_fingerprints is not None:
        fingerprint = _template_fingerprint(template)
        if fingerprint in seen_template_fingerprints:
            return None
        if size == 7:
            seen_template_fingerprints.add(fingerprint)

    slots = extract_slots(template)
    if not _slot_capacity_ok(slots, word_index, settings):
        return None
    if settings.max_full_width_slots is not None:
        full_width = sum(1 for s in slots if s.length == size)
        if full_width > settings.max_full_width_slots:
            return None

    grid: list[list[str | None]] = [
        [None if template[row][col] else "#" for col in range(size)]
        for row in range(size)
    ]
    assignment: dict[int, WordEntry] = {}
    used_words: set[str] = set()
    result = solve(
        slots,
        word_index,
        assignment,
        used_words,
        grid,
        settings.max_backtracks,
        allow_reuse=size >= 15,
        rng=rng,
    )
    if result is None:
        return None

    words = [result[slot.id].normalized for slot in slots]
    report = score_words(words, metadata, size)
    markdown = _render_filled_markdown(size, template, slots, result, title)
    return Candidate(score=report.score, report=report, template=template, markdown=markdown, metadata=metadata)


def _try_incremental_template(
    size: int,
    settings: SizeSettings,
    rng: random.Random,
    word_index: WordIndex,
) -> list[list[bool]] | None:
    """Build an incremental template once — expensive, so call once per variant."""
    def solver_fn(template):
        slots = extract_slots(template)
        if not _slot_capacity_ok(slots, word_index, settings):
            return False
        grid = [[None if t else "#" for t in row] for row in template]
        return solve(slots, word_index, {}, set(), grid, settings.max_backtracks, rng=rng) is not None

    return generate_incremental_template(
        size, solver_fn, max_blacks=settings.target_blacks + 4, rng=rng,
    )


def _choose_template(
    size: int,
    settings: SizeSettings,
    rng: random.Random,
) -> list[list[bool]] | None:
    """Cheap procedural fallback — safe to call per attempt."""
    blacks = rng.choice([
        settings.target_blacks - 2,
        settings.target_blacks - 1,
        settings.target_blacks,
        settings.target_blacks + 1,
        settings.target_blacks + 2,
    ])
    return generate_procedural_template(
        size,
        target_blacks=max(1, blacks),
        max_attempts=settings.template_attempts,
        rng=rng,
    )


def _best_candidate(
    size: int,
    title: str,
    raw_words: list[dict],
    rng: random.Random,
    seen_template_fingerprints: set[str] | None = None,
) -> Candidate:
    best: Candidate | None = None

    for variant_index, settings in enumerate(build_relaxed_variants(size), start=1):
        word_index, metadata = _build_index(raw_words, size, settings)

        incremental_template = _try_incremental_template(size, settings, rng, word_index)
        if incremental_template is not None:
            print(f"  Incremental template found for variant {variant_index}")

        solved = 0
        print(
            f"Selecting best {size}x{size} candidate "
            f"(variant {variant_index}, target solved: {settings.solved_candidates}, "
            f"attempt budget: {settings.attempt_budget}, max_rarity: {settings.max_rarity})..."
        )
        for attempt in range(1, settings.attempt_budget + 1):
            candidate = _generate_candidate(
                size,
                settings,
                word_index,
                metadata,
                title,
                rng,
                seen_template_fingerprints=seen_template_fingerprints if size == 7 else None,
                template=incremental_template,
            )
            if candidate is None:
                print(f"  Attempt {attempt}: no solution")
                if solved == 0 and attempt >= 25:
                    print("  No solved candidates yet; relaxing settings")
                    break
                continue
            solved += 1
            print(
                f"  Attempt {attempt}: score={candidate.score:.1f} "
                f"two={candidate.report.two_letter_words} "
                f"avg_rarity={candidate.report.average_rarity:.2f}"
            )
            if best is None or candidate.score > best.score:
                best = candidate
            return best

    if best is not None:
        return best
    raise RuntimeError(f"Could not generate a valid filled grid for {size}x{size}")


def _coerce_working_clue(clue: WorkingClue | ClueEntry) -> WorkingClue:
    if isinstance(clue, WorkingClue):
        return clue
    return working_clue_from_entry(clue)


def _extract_semantic_score(clue: WorkingClue) -> int | None:
    clue = _coerce_working_clue(clue)
    return clue.active_version().assessment.scores.semantic_exactness


def _extract_guessability_score(clue: WorkingClue) -> int | None:
    clue = _coerce_working_clue(clue)
    return clue.active_version().assessment.scores.answer_targeting


def _extract_rebus_score(clue: WorkingClue) -> int | None:
    clue = _coerce_working_clue(clue)
    return clue.active_version().assessment.scores.rebus_score


def _needs_rewrite(clue: WorkingClue, min_rebus: int = RATE_MIN_REBUS) -> bool:
    """Return True when a clue should be rewritten.

    We rewrite based on quality score, not raw verify failure alone.
    A clue can be semantically good yet still fail exact-match verification
    because the local model prefers a synonym or a more common variant.
    """
    clue = _coerce_working_clue(clue)
    if clue.word_normalized in PRESET_DEFINITIONS:
        return False
    definition = clue.current.definition
    if not definition or definition.startswith("["):
        return True

    semantic_score = _extract_semantic_score(clue)
    rebus_score = _extract_rebus_score(clue)
    if semantic_score is None or rebus_score is None:
        return True
    if semantic_score >= LOCKED_SEMANTIC and rebus_score >= LOCKED_REBUS:
        return False

    if semantic_score < RATE_MIN_SEMANTIC:
        return True

    rarity_override = clue.current.assessment.rarity_only_override
    if rarity_override and semantic_score >= RATE_MIN_SEMANTIC:
        return False

    return rebus_score < min_rebus


def _blocking_clues(puzzle: WorkingPuzzle) -> list[WorkingClue]:
    return [
        clue for clue in all_working_clues(puzzle)
        if not clue.active_version().definition
        or clue.active_version().definition.startswith("[")
    ]


def _clue_eval(clue: WorkingClue) -> tuple[int, int, int]:
    semantic_score = _extract_semantic_score(clue) or 0
    rebus_score = _extract_rebus_score(clue) or 0
    verified_score = 1 if clue.active_version().assessment.verified is True else 0
    return (semantic_score + rebus_score, rebus_score, verified_score)


def _compact_log_text(text: str) -> str:
    return " ".join((text or "").split())


def _is_locked_clue(clue: WorkingClue) -> bool:
    clue = _coerce_working_clue(clue)
    return clue.locked


def _template_fingerprint(template: list[list[bool]]) -> str:
    return "|".join("".join("." if cell else "#" for cell in row) for row in template)


def _inject_word_types(state: WorkingPuzzle, metadata: dict[str, dict]) -> None:
    for clue in all_working_clues(state):
        word_meta = metadata.get(clue.word_normalized, {})
        clue.word_type = word_meta.get("word_type", "")


def _preparation_attempts_for_size(size: int, requested_attempts: int) -> int:
    return max(requested_attempts, get_min_preparation_attempts(size))


def _score_puzzle_state(puzzle: WorkingPuzzle, candidate_report: QualityReport | None = None) -> PuzzleAssessment:
    clues = all_working_clues(puzzle)
    if not clues:
        return PuzzleAssessment()
    exact_scores = [clue.active_version().assessment.scores.semantic_exactness or 0 for clue in clues]
    rebus_scores = [clue.active_version().assessment.scores.rebus_score or 0 for clue in clues]
    creativity_scores = [clue.active_version().assessment.scores.creativity or 0 for clue in clues]
    targeting_scores = [clue.active_version().assessment.scores.answer_targeting or 0 for clue in clues]
    ambiguity_count = sum(
        1 for clue in clues
        if (clue.active_version().assessment.scores.ambiguity_risk or 0) >= (11 - RATE_MIN_REBUS)
    )
    short_word_burden = sum(1 for clue in clues if len(clue.word_normalized) <= 3)
    rare_word_burden = candidate_report.high_rarity_words if candidate_report else 0
    blocker_words = [clue.word_normalized for clue in clues if _needs_rewrite(clue)]
    non_preset_rebus = [
        clue.active_version().assessment.scores.rebus_score or 0
        for clue in clues
        if clue.word_normalized not in PRESET_DEFINITIONS
    ]
    return PuzzleAssessment(
        definition_score=sum(e + r for e, r in zip(exact_scores, rebus_scores)) / len(clues),
        avg_exactness=sum(exact_scores) / len(exact_scores),
        avg_targeting=sum(targeting_scores) / len(targeting_scores),
        ambiguity_count=ambiguity_count,
        short_word_burden=short_word_burden,
        rare_word_burden=rare_word_burden,
        blocker_words=blocker_words,
        avg_creativity=sum(creativity_scores) / len(creativity_scores),
        avg_rebus=sum(rebus_scores) / len(rebus_scores),
        min_rebus=min(non_preset_rebus) if non_preset_rebus else 0,
    )


def _update_best_clue_version(clue: WorkingClue, client=None) -> None:
    if clue.best is None:
        clue.best = copy.deepcopy(clue.current)
    elif clue.current.definition:
        def _tiebreak(a_text: str, b_text: str) -> str:
            if client is None:
                return "A"
            return choose_better_clue_variant(
                client,
                clue.word_normalized,
                len(clue.word_normalized),
                a_text,
                b_text,
            )

        chosen, decision = choose_clue_version(clue.best, clue.current, tiebreaker=_tiebreak)
        if decision.used_tiebreak:
            print(
                f"  Tie-break definiție {clue.word_normalized}: "
                f"A='{_compact_log_text(decision.a_summary)}' | "
                f"B='{_compact_log_text(decision.b_summary)}' | "
                f"câștigă {decision.winner} | "
                f"aleasă='{_compact_log_text(decision.winner_summary)}'"
            )
        elif decision.reason == "deterministic_rank" and chosen.definition == clue.best.definition and clue.current.definition != clue.best.definition:
            print(f"  Păstrez definiția mai bună pentru {clue.word_normalized}")
        clue.best = copy.deepcopy(chosen)

    semantic_score = clue.best.assessment.scores.semantic_exactness or 0
    rebus_score = clue.best.assessment.scores.rebus_score or 0
    clue.locked = semantic_score >= LOCKED_SEMANTIC and rebus_score >= LOCKED_REBUS


def _merge_best_clue_variants(
    best_clues: list[ClueEntry],
    current_clues: list[ClueEntry],
    client=None,
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
            )

        chosen, _ = choose_clue_version(
            best_working.active_version(),
            current_working.active_version(),
            tiebreaker=_tiebreak,
        )
        if client is not None:
            print(
                f"  Tie-break definiție {best_working.word_normalized}: "
                f"A='{_compact_log_text(best_working.active_version().definition)}' | "
                f"B='{_compact_log_text(current_working.active_version().definition)}' | "
                f"aleasă='{_compact_log_text(chosen.definition)}'"
            )
        chosen_working = copy.deepcopy(best_working if chosen.definition == best_working.active_version().definition else current_working)
        chosen_working.best = copy.deepcopy(chosen)
        chosen_working.current = copy.deepcopy(chosen)
        merged.append(puzzle_from_working_state(WorkingPuzzle("", 0, [], [chosen_working], [])).horizontal_clues[0])
    return merged


def _restore_best_versions(puzzle: WorkingPuzzle) -> None:
    for clue in all_working_clues(puzzle):
        if clue.best is not None:
            clue.current = copy.deepcopy(clue.best)


def _compute_difficulty(report: QualityReport) -> int:
    """Star rating based on word rarity levels."""
    max_r = report.max_rarity
    avg_r = report.average_rarity
    if max_r <= 3:
        return 1
    if max_r >= 5 and avg_r >= 3.0:
        return 5
    if max_r >= 5 and avg_r < 3.0:
        return 4
    if avg_r < 2.0:
        return 2
    return 3


def _is_publishable(prepared: PreparedPuzzle) -> bool:
    return not prepared.blocking_words


def _better_prepared_puzzle(
    best: PreparedPuzzle | None,
    candidate: PreparedPuzzle,
    client=None,
) -> PreparedPuzzle:
    if best is None:
        return candidate

    best_publishable = _is_publishable(best)
    candidate_publishable = _is_publishable(candidate)
    if candidate_publishable != best_publishable:
        return candidate if candidate_publishable else best

    score_delta = candidate.assessment.definition_score - best.assessment.definition_score
    if abs(score_delta) > PUZZLE_TIEBREAK_DELTA:
        return candidate if score_delta > 0 else best

    def _tiebreak(a_summary: str, b_summary: str) -> str:
        if client is None:
            return "A"
        return choose_better_puzzle_variant(client, a_summary, b_summary)

    winner, decision = choose_puzzle_assessment(best.assessment, candidate.assessment, tiebreaker=_tiebreak)
    if decision.used_tiebreak:
        chosen = candidate if winner == "B" else best
        print(
            "Puzzle tie-break: "
            f"A='{_compact_log_text(decision.a_summary)}' | "
            f"B='{_compact_log_text(decision.b_summary)}' | "
            f"câștigă {decision.winner} | "
            f"ales='{_compact_log_text(decision.winner_summary)}'"
        )
        return chosen

    return candidate if score_delta > 0 else best


def _synthesize_failure_reason(clue: WorkingClue) -> str:
    clue = _coerce_working_clue(clue)
    assessment = clue.current.assessment
    if assessment.scores.family_leakage:
        return "Folosește aceeași familie lexicală ca răspunsul."
    if assessment.wrong_guess:
        return f"Duce la alt răspuns: {assessment.wrong_guess}."
    if assessment.feedback:
        normalized_feedback = assessment.feedback.lower()
        if ("rar" in normalized_feedback or "comun" in normalized_feedback) and (assessment.scores.semantic_exactness or 0) >= 8:
            return "Definiția trebuie făcută mai exactă, nu tratată ca defect doar pentru raritate."
        return assessment.feedback
    if assessment.failure_reason:
        return assessment.failure_reason.message

    semantic_score = assessment.scores.semantic_exactness or 0
    rebus_score = assessment.scores.rebus_score or 0
    if semantic_score < RATE_MIN_SEMANTIC:
        return "Definiția nu este suficient de exactă pentru răspunsul intenționat."
    if rebus_score < RATE_MIN_REBUS:
        return "Definiția este prea vagă sau duce spre alt răspuns mai comun."
    return "Definiția trebuie făcută mai exactă."


def _rewrite_failed_clues(
    puzzle: WorkingPuzzle,
    client,
    rounds: int,
    multi_model: bool = False,
) -> tuple[int, int]:
    theme = puzzle.title or "Puzzle intern"
    if multi_model:
        ensure_model_loaded(PRIMARY_MODEL)
        current_model = PRIMARY_MODEL
        try:
            switch_model(PRIMARY_MODEL, SECONDARY_MODEL)
            current_model = SECONDARY_MODEL
        except Exception as e:
            print(f"  Model switch failed: {e} — continuing with {current_model.display_name}")
        print(f"  Model activ (evaluare inițială): {current_model.display_name}")
    else:
        current_model = PRIMARY_MODEL
    preset_skip = {c.word_normalized for c in all_working_clues(puzzle) if c.word_normalized in PRESET_DEFINITIONS}
    passed, total = verify_working_puzzle(puzzle, client, skip_words=preset_skip)
    rate_working_puzzle(puzzle, client, skip_words=preset_skip)
    for clue in all_working_clues(puzzle):
        _update_best_clue_version(clue, client=client)

    consecutive_failures: dict[str, int] = {}
    stuck_words: set[str] = set()
    min_rebus_history: list[int] = []

    for round_index in range(1, rounds + 1):
        current_scores = [
            _extract_rebus_score(c) or 0
            for c in all_working_clues(puzzle)
            if c.word_normalized not in PRESET_DEFINITIONS
        ]
        current_min = min(current_scores) if current_scores else 0
        min_rebus_history.append(current_min)

        if has_plateaued(min_rebus_history, PLATEAU_LOOKBACK):
            blockers = _blocking_clues(puzzle)
            if blockers:
                blocker_words = [c.word_normalized for c in blockers]
                print(f"  Plateau after {round_index} rounds with undefinable words: "
                      f"{', '.join(blocker_words)}")
            else:
                print(f"  Plateau after {round_index} rounds (min_rebus={current_min})")
            break

        round_min_rebus = current_min + 1
        candidates = [
            clue for clue in all_working_clues(puzzle)
            if _needs_rewrite(clue, min_rebus=round_min_rebus)
            and clue.word_normalized not in stuck_words
        ]

        if not candidates:
            break

        if multi_model:
            print(f"  Model activ (rescriere): {current_model.display_name}")
        failed_count = sum(1 for c in candidates if c.current.assessment.verified is False)
        low_rated_count = sum(
            1 for c in candidates
            if (
                c.current.assessment.verified is True
                and (
                    (_extract_semantic_score(c) or 0) < RATE_MIN_SEMANTIC
                    or (_extract_rebus_score(c) or 0) < RATE_MIN_REBUS
                )
            )
        )
        unrated_count = len(candidates) - failed_count - low_rated_count
        print(
            f"Rewrite round {round_index}: {len(candidates)} candidates "
            f"({failed_count} failed, {low_rated_count} low-rated, {unrated_count} unrated)"
        )

        changed_words: set[str] = set()
        for clue in candidates:
            if _is_locked_clue(clue):
                print(f"  {clue.word_normalized}: blocat la {LOCKED_SEMANTIC}/{LOCKED_REBUS}")
                continue
            wrong_guess = clue.current.assessment.wrong_guess
            rating_feedback = clue.current.assessment.feedback
            bad_example_definition = clue.current.definition if round_index >= 2 else ""
            bad_example_reason = _synthesize_failure_reason(clue) if round_index >= 2 else ""
            try:
                if clue.current.definition.startswith("["):
                    new_definition = generate_definition(
                        client, clue.word_normalized, clue.word_original, theme, retries=3,
                        word_type=clue.word_type,
                    )
                else:
                    new_definition = rewrite_definition(
                        client,
                        clue.word_normalized,
                        clue.word_original,
                        theme,
                        clue.current.definition,
                        wrong_guess,
                        rating_feedback=rating_feedback,
                        bad_example_definition=bad_example_definition,
                        bad_example_reason=bad_example_reason,
                        word_type=clue.word_type,
                    )
            except Exception as e:
                print(f"  Rewrite failed for {clue.word_normalized}: {e}")
                continue
            if new_definition and new_definition != clue.current.definition:
                changed_words.add(clue.word_normalized)
                consecutive_failures[clue.word_normalized] = 0
                print(
                    f"  {clue.word_normalized}: "
                    f"'{_compact_log_text(clue.current.definition)}' -> "
                    f"'{_compact_log_text(new_definition)}'"
                )
                set_current_definition(clue, new_definition, round_index=round_index, source="rewrite")
            else:
                consecutive_failures[clue.word_normalized] = consecutive_failures.get(clue.word_normalized, 0) + 1
                if consecutive_failures[clue.word_normalized] >= MAX_CONSECUTIVE_FAILURES:
                    stuck_words.add(clue.word_normalized)
                    print(f"  {clue.word_normalized}: marcată ca blocată după {consecutive_failures[clue.word_normalized]} încercări eșuate consecutive")

        skip_words = ({c.word_normalized for c in all_working_clues(puzzle)} - changed_words) | preset_skip
        if multi_model:
            next_model = SECONDARY_MODEL if current_model == PRIMARY_MODEL else PRIMARY_MODEL
            try:
                switch_model(current_model, next_model)
                current_model = next_model
            except Exception as e:
                print(f"  Model switch failed: {e} — continuing with {current_model.display_name}")
            print(f"  Model activ (evaluare): {current_model.display_name}")
        passed, total = verify_working_puzzle(puzzle, client, skip_words=skip_words)
        rate_working_puzzle(puzzle, client, skip_words=skip_words)
        for clue in all_working_clues(puzzle):
            if clue.word_normalized not in changed_words:
                continue
            _update_best_clue_version(clue, client=client)
            if clue.locked:
                print(f"  {clue.word_normalized}: definiție blocată la {LOCKED_SEMANTIC}/{LOCKED_REBUS}")

    _restore_best_versions(puzzle)
    passed = sum(1 for clue in all_working_clues(puzzle) if clue.current.assessment.verified)
    total = len(all_working_clues(puzzle))
    return passed, total


def _prepare_puzzle_for_publication(
    index: int,
    total_puzzles: int,
    size: int,
    raw_words: list[dict],
    client,
    rewrite_rounds: int,
    preparation_attempts: int,
    seen_template_fingerprints: set[str] | None = None,
    multi_model: bool = False,
) -> PreparedPuzzle:
    best_prepared: PreparedPuzzle | None = None
    effective_attempts = _preparation_attempts_for_size(size, preparation_attempts)

    for attempt_index in range(1, effective_attempts + 1):
        if attempt_index > 1:
            print(
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
        )
        puzzle = parse_markdown(candidate.markdown)
        puzzle.title = ""
        if multi_model:
            ensure_model_loaded(PRIMARY_MODEL)
        generate_definitions_for_puzzle(puzzle, client, metadata=candidate.metadata)
        state = working_puzzle_from_puzzle(puzzle, split_compound=False)
        _inject_word_types(state, candidate.metadata)
        passed, total = _rewrite_failed_clues(state, client, rewrite_rounds, multi_model=multi_model)
        _restore_best_versions(state)
        state.assessment = _score_puzzle_state(state, candidate.report)
        blockers = _blocking_clues(state)
        rendered_for_title = puzzle_from_working_state(state)
        title = generate_title_for_final_puzzle(
            rendered_for_title,
            client=client,
            rate_client=client,
            multi_model=multi_model,
        )
        state.title = title
        print(f"Titlu final: {title}")
        prepared = PreparedPuzzle(
            title=title,
            candidate=candidate,
            puzzle=copy.deepcopy(state),
            passed=passed,
            total=total,
            definition_score=state.assessment.definition_score,
            blocking_words=[clue.word_normalized for clue in blockers],
            assessment=copy.deepcopy(state.assessment),
        )
        best_prepared = _better_prepared_puzzle(best_prepared, prepared, client=client)

        if blockers:
            print(
                "Rejected puzzle after quality gate: "
                + ", ".join(clue.word_normalized for clue in blockers[:10])
            )
        elif _is_publishable(best_prepared):
            print(f"  Puzzle publicabil la tentativa {attempt_index}/{effective_attempts}")
            break

    if best_prepared is None:
        raise RuntimeError(f"Failed to prepare any {size}x{size} puzzle candidate")
    return best_prepared


def _collect_word_metrics(puzzle: WorkingPuzzle) -> list[WordMetric]:
    metrics = []
    for clue in all_working_clues(puzzle):
        version = clue.active_version()
        semantic = version.assessment.scores.semantic_exactness
        targeting = version.assessment.scores.answer_targeting
        creativity = version.assessment.scores.creativity
        rebus = version.assessment.scores.rebus_score
        metrics.append(WordMetric(
            word=clue.word_normalized,
            length=len(clue.word_normalized),
            definition_rounds=len(clue.history),
            final_verified=version.assessment.verified is True,
            semantic_score=semantic,
            guessability_score=targeting,
            creativity_score=creativity,
            rebus_score=rebus,
            was_blocker=_needs_rewrite(clue),
            english_meaning_detected=False,
        ))
    return metrics


def _clear_verification_state(puzzle: WorkingPuzzle):
    clean = copy.deepcopy(puzzle)
    for clue in all_working_clues(clean):
        version = clue.active_version()
        version.assessment.verified = None
        version.assessment.wrong_guess = ""
        version.assessment.feedback = ""
        version.assessment.failure_reason = None
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
) -> list[dict]:
    raw_words = _load_words(words_path)
    client = create_client()
    rng_seed = seed if seed is not None else random.SystemRandom().randint(1, 10_000_000)
    batch_rng = random.Random(rng_seed)
    setattr(client, "_batch_rng", batch_rng)
    if run_dir is None:
        run_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    seen_7x7_templates: set[str] = set()
    batch_start = time.monotonic()
    all_word_metrics: list[WordMetric] = []
    puzzle_metrics: list[PuzzleMetric] = []
    print(f"Batch seed: {rng_seed}")
    ensure_model_loaded(PRIMARY_MODEL)
    if multi_model:
        print(f"Multi-model mode: {PRIMARY_MODEL.display_name} + {SECONDARY_MODEL.display_name}")

    for index, size in enumerate(sizes, start=1):
        puzzle_dir = run_dir / f"{index:02d}_{size}x{size}"
        puzzle_start = time.monotonic()
        print(f"\n=== Puzzle {index}/{len(sizes)}: {size}x{size} ===")

        prepared = _prepare_puzzle_for_publication(
            index=index,
            total_puzzles=len(sizes),
            size=size,
            raw_words=raw_words,
            client=client,
            rewrite_rounds=rewrite_rounds,
            preparation_attempts=preparation_attempts,
            seen_template_fingerprints=seen_7x7_templates if size == 7 else None,
            multi_model=multi_model,
        )
        if prepared.blocking_words:
            print("\n--- Detailed rejection report ---")
            blocking_set = set(prepared.blocking_words)
            try:
                for clue in all_working_clues(prepared.puzzle):
                    if clue.word_normalized in blocking_set:
                        version = clue.active_version()
                        semantic = version.assessment.scores.semantic_exactness
                        rebus = version.assessment.scores.rebus_score
                        reason = _synthesize_failure_reason(clue)
                        print(
                            f"  {clue.word_normalized}: "
                            f"def='{_compact_log_text(version.definition)}' "
                            f"semantic={semantic}/10 rebus={rebus}/10 "
                            f"motiv: {reason}"
                        )
            except (AttributeError, TypeError):
                print(f"  Blocked words: {', '.join(prepared.blocking_words[:12])}")
            print("--- End rejection report ---\n")
            raise RuntimeError(
                f"Could not prepare a publishable {size}x{size} puzzle. "
                f"Missing definitions for: {', '.join(prepared.blocking_words[:12])}"
            )

        puzzle_elapsed_ms = int((time.monotonic() - puzzle_start) * 1000)

        template_path = puzzle_dir / "template.md"
        filled_path = puzzle_dir / "filled.md"
        rendered_puzzle = puzzle_from_working_state(prepared.puzzle)
        _write_text(template_path, write_grid_template(size, prepared.candidate.template))
        _write_text(filled_path, write_with_definitions(rendered_puzzle))

        defs_puzzle = _clear_verification_state(prepared.puzzle)
        defs_path = puzzle_dir / "defs.md"
        verified_path = puzzle_dir / "verified.md"
        _write_text(defs_path, write_with_definitions(puzzle_from_working_state(defs_puzzle)))
        _write_text(verified_path, write_with_definitions(rendered_puzzle))

        non_preset_rebus = [
            c.active_version().assessment.scores.rebus_score or 0
            for c in all_working_clues(prepared.puzzle)
            if c.word_normalized not in PRESET_DEFINITIONS
        ]
        min_rebus = min(non_preset_rebus) if non_preset_rebus else 10
        models_used_desc = [PRIMARY_MODEL.display_name]
        if multi_model:
            models_used_desc.append(SECONDARY_MODEL.display_name)
        description = f"Scor rebus: {min_rebus}/10 | Modele: {', '.join(models_used_desc)}"
        difficulty = _compute_difficulty(prepared.candidate.report)
        puzzle_id = upload_puzzle(
            puzzle_from_working_state(defs_puzzle),
            difficulty=difficulty,
            description=description,
        )
        set_published(puzzle_id, True)

        word_metrics = _collect_word_metrics(prepared.puzzle)
        all_word_metrics.extend(word_metrics)
        clues = all_working_clues(prepared.puzzle)
        verified_count = sum(1 for c in clues if c.active_version().assessment.verified is True)
        semantic_scores = [c.active_version().assessment.scores.semantic_exactness or 0 for c in clues]
        guess_scores = [c.active_version().assessment.scores.answer_targeting or 0 for c in clues]
        rebus_scores = [c.active_version().assessment.scores.rebus_score or 0 for c in clues]
        creativity_scores = [c.active_version().assessment.scores.creativity or 0 for c in clues]
        puzzle_metrics.append(PuzzleMetric(
            size=size,
            word_count=len(clues),
            definition_first_pass_rate=prepared.passed / prepared.total if prepared.total else 0.0,
            definition_final_pass_rate=verified_count / len(clues) if clues else 0.0,
            avg_semantic=sum(semantic_scores) / len(semantic_scores) if semantic_scores else 0.0,
            avg_guessability=sum(guess_scores) / len(guess_scores) if guess_scores else 0.0,
            avg_creativity=sum(creativity_scores) / len(creativity_scores) if creativity_scores else 0.0,
            avg_rebus=sum(rebus_scores) / len(rebus_scores) if rebus_scores else 0.0,
            min_rebus=min_rebus,
            blocker_count=len(prepared.blocking_words),
            blocker_words=prepared.blocking_words,
            total_elapsed_ms=puzzle_elapsed_ms,
        ))

        manifest.append({
            "index": index,
            "size": size,
            "title": prepared.title,
            "puzzle_id": puzzle_id,
            "score": prepared.candidate.score,
            "quality": prepared.candidate.report.to_dict(),
            "verification_passed": prepared.passed,
            "verification_total": prepared.total,
            "output_dir": str(puzzle_dir),
            "template_path": str(template_path),
            "seed": rng_seed,
            "template_fingerprint": _template_fingerprint(prepared.candidate.template),
        })
        _write_text(run_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    batch_elapsed_ms = int((time.monotonic() - batch_start) * 1000)
    models_used = [PRIMARY_MODEL.display_name]
    if multi_model:
        models_used.append(SECONDARY_MODEL.display_name)
    batch_metric = BatchMetric(
        timestamp=datetime.now().isoformat(),
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
    parser = argparse.ArgumentParser(description="Generate and publish a batch of rebus puzzles.")
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
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    output_root = Path(args.output_root)
    preview_run_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S")
    preview_run_dir.mkdir(parents=True, exist_ok=True)
    log_path = preview_run_dir / "run.log"

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    with open(log_path, "a", encoding="utf-8") as log_file:
        tee = TeeStream(original_stdout, log_file)
        sys.stdout = tee
        sys.stderr = tee
        try:
            print(f"Run log: {log_path}")
            manifest = run_batch(
                sizes=args.sizes,
                output_root=output_root,
                words_path=Path(args.words),
                rewrite_rounds=args.rewrite_rounds,
                preparation_attempts=args.preparation_attempts,
                seed=args.seed,
                run_dir=preview_run_dir,
                multi_model=args.multi_model,
            )
            print("\nBatch complete:")
            for item in manifest:
                print(
                    f"  {item['title']} -> {item['puzzle_id']} "
                    f"(verify {item['verification_passed']}/{item['verification_total']})"
                )
        finally:
            sys.stdout = original_stdout
            sys.stderr = original_stderr


if __name__ == "__main__":
    main()
