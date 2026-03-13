#!/usr/bin/env python3
"""Generate and publish a batch of rebus puzzles."""

from __future__ import annotations

import argparse
import copy
import json
import random
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .core.ai_clues import (
    RATE_MIN_GUESSABILITY,
    RATE_MIN_SEMANTIC,
    choose_better_clue_variant,
    choose_better_puzzle_variant,
    create_client,
    rewrite_definition,
)
from .core.grid_template import ALL_TEMPLATES, generate_procedural_template, parse_template
from .core.clue_rating import (
    extract_feedback,
    extract_guessability_score,
    extract_semantic_score,
    extract_wrong_guess,
)
from .core.markdown_io import (
    ClueEntry,
    parse_markdown,
    write_filled_grid,
    write_grid_template,
    write_with_definitions,
)
from .core.quality import QualityReport, filter_word_records, score_words
from .core.slot_extractor import Slot, extract_slots
from .core.word_index import WordEntry, WordIndex
from .core.constraint_solver import solve
from .phases.activate import set_published
from .phases.define import generate_definitions_for_puzzle
from .phases.download import run as download_words
from .phases.theme import generate_title_for_final_puzzle
from .phases.upload import upload_puzzle
from .phases.verify import verify_puzzle, rate_puzzle


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
class SizeSettings:
    max_rarity: int
    max_backtracks: int
    target_blacks: int
    solved_candidates: int
    attempt_budget: int
    max_two_letter_slots: int
    min_candidates_per_slot: int


@dataclass
class Candidate:
    score: float
    report: QualityReport
    template: list[list[bool]]
    markdown: str


@dataclass
class PreparedPuzzle:
    title: str
    candidate: Candidate
    puzzle: object
    passed: int
    total: int
    definition_score: float
    blocking_words: list[str]


LOCKED_SCORE = 9
PUZZLE_TIEBREAK_DELTA = 0.25


def _easy_large_template(size: int) -> list[list[bool]] | None:
    if size != 15:
        return None
    patterns = [
        "#..#..#..#..#..",
        "..#..#..#..#..#",
        "#...#...#...#..",
        "..#...#...#...#",
    ]
    grid: list[list[bool]] = []
    for row_index in range(size):
        if row_index % 3 == 2:
            pattern = patterns[(row_index // 3) % len(patterns)]
            grid.append([ch == "." for ch in pattern])
        else:
            grid.append([True] * size)
    return grid


def _easy_medium_template(size: int) -> list[list[bool]] | None:
    if size != 12:
        return None
    patterns = [
        "..#..#..#..#",
        "#..#...#..#.",
        ".#..#..#...#",
        "..#...#..#..",
    ]
    grid: list[list[bool]] = []
    for row_index in range(size):
        if row_index % 3 == 2:
            pattern = patterns[(row_index // 3) % len(patterns)]
            grid.append([ch == "." for ch in pattern])
        else:
            grid.append([True] * size)
    return grid


def _relaxed_variants(settings: SizeSettings) -> list[SizeSettings]:
    return [
        settings,
        SizeSettings(
            max_rarity=min(5, settings.max_rarity + 1),
            max_backtracks=settings.max_backtracks * 2,
            target_blacks=settings.target_blacks + 2,
            solved_candidates=settings.solved_candidates,
            attempt_budget=settings.attempt_budget + 20,
            max_two_letter_slots=settings.max_two_letter_slots + 2,
            min_candidates_per_slot=max(8, settings.min_candidates_per_slot - 4),
        ),
        SizeSettings(
            max_rarity=5,
            max_backtracks=settings.max_backtracks * 3,
            target_blacks=settings.target_blacks + 4,
            solved_candidates=max(3, settings.solved_candidates - 1),
            attempt_budget=settings.attempt_budget + 35,
            max_two_letter_slots=settings.max_two_letter_slots + 4,
            min_candidates_per_slot=max(6, settings.min_candidates_per_slot - 8),
        ),
    ]


def _settings_for_size(size: int) -> SizeSettings:
    if size == 7:
        return SizeSettings(
            max_rarity=3,
            max_backtracks=50000,
            target_blacks=6,
            solved_candidates=4,
            attempt_budget=45,
            max_two_letter_slots=4,
            min_candidates_per_slot=16,
        )
    if size == 10:
        return SizeSettings(
            max_rarity=4,
            max_backtracks=140000,
            target_blacks=16,
            solved_candidates=5,
            attempt_budget=70,
            max_two_letter_slots=10,
            min_candidates_per_slot=18,
        )
    if size == 12:
        return SizeSettings(
            max_rarity=4,
            max_backtracks=180000,
            target_blacks=28,
            solved_candidates=3,
            attempt_budget=60,
            max_two_letter_slots=16,
            min_candidates_per_slot=10,
        )
    return SizeSettings(
        max_rarity=5,
        max_backtracks=350000,
        target_blacks=60,
        solved_candidates=1,
        attempt_budget=40,
        max_two_letter_slots=50,
        min_candidates_per_slot=4,
    )


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
            rendered_row.append(None if not template[row][col] else None)
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
    seen_template_fingerprints: set[str] | None = None,
) -> Candidate | None:
    template = None
    hardcoded_templates = ALL_TEMPLATES.get(size, [])
    if size == 15 and random.random() < 0.7:
        template = _easy_large_template(size)
    elif size == 12 and random.random() < 0.65:
        template = _easy_medium_template(size)
    elif size != 7 and hardcoded_templates and random.random() < 0.4:
        template = parse_template(random.choice(hardcoded_templates))
    else:
        blacks = random.choice([
            settings.target_blacks - 2,
            settings.target_blacks - 1,
            settings.target_blacks,
            settings.target_blacks + 1,
            settings.target_blacks + 2,
        ])
        template = generate_procedural_template(size, target_blacks=max(1, blacks), max_attempts=300)
    if template is None:
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
    )
    if result is None:
        return None

    words = [result[slot.id].normalized for slot in slots]
    report = score_words(words, metadata, size)
    markdown = _render_filled_markdown(size, template, slots, result, title)
    return Candidate(score=report.score, report=report, template=template, markdown=markdown)


def _best_candidate(
    size: int,
    title: str,
    raw_words: list[dict],
    seen_template_fingerprints: set[str] | None = None,
) -> Candidate:
    best: Candidate | None = None

    for variant_index, settings in enumerate(_relaxed_variants(_settings_for_size(size)), start=1):
        word_index, metadata = _build_index(raw_words, size, settings)
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
                seen_template_fingerprints=seen_template_fingerprints if size == 7 else None,
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
            if solved >= settings.solved_candidates:
                return best

        if best is not None:
            return best

    raise RuntimeError(f"Could not generate a valid filled grid for {size}x{size}")


def _all_clues(puzzle) -> list[ClueEntry]:
    return puzzle.horizontal_clues + puzzle.vertical_clues


def _extract_semantic_score(clue) -> int | None:
    return extract_semantic_score(clue.verify_note)


def _extract_guessability_score(clue) -> int | None:
    return extract_guessability_score(clue.verify_note)


def _needs_rewrite(clue) -> bool:
    """Return True when a clue should be rewritten.

    We rewrite based on quality score, not raw verify failure alone.
    A clue can be semantically good yet still fail exact-match verification
    because the local model prefers a synonym or a more common variant.
    """
    if not clue.definition or clue.definition.startswith("["):
        return True

    semantic_score = _extract_semantic_score(clue)
    guessability_score = _extract_guessability_score(clue)
    if semantic_score is None or guessability_score is None:
        return True
    if semantic_score >= LOCKED_SCORE and guessability_score >= LOCKED_SCORE:
        return False

    return (
        semantic_score < RATE_MIN_SEMANTIC
        or guessability_score < RATE_MIN_GUESSABILITY
    )


def _blocking_clues(puzzle) -> list[ClueEntry]:
    return [clue for clue in _all_clues(puzzle) if _needs_rewrite(clue)]


def _clue_eval(clue: ClueEntry) -> tuple[int, int, int]:
    semantic_score = _extract_semantic_score(clue) or 0
    guessability_score = _extract_guessability_score(clue) or 0
    verified_score = 1 if clue.verified is True else 0
    return (semantic_score + guessability_score, guessability_score, verified_score)


def _is_locked_clue(clue: ClueEntry) -> bool:
    semantic_score = _extract_semantic_score(clue)
    guessability_score = _extract_guessability_score(clue)
    return (
        semantic_score is not None
        and guessability_score is not None
        and semantic_score >= LOCKED_SCORE
        and guessability_score >= LOCKED_SCORE
    )


def _template_fingerprint(template: list[list[bool]]) -> str:
    return "|".join("".join("." if cell else "#" for cell in row) for row in template)


def _puzzle_definition_score(puzzle) -> float:
    clues = _all_clues(puzzle)
    if not clues:
        return 0.0
    return sum(_clue_eval(clue)[0] for clue in clues) / len(clues)


def _choose_best_clue(
    best_clue: ClueEntry,
    current_clue: ClueEntry,
    client=None,
) -> ClueEntry:
    best_eval = _clue_eval(best_clue)
    current_eval = _clue_eval(current_clue)
    if current_eval > best_eval:
        return copy.deepcopy(current_clue)
    if best_eval > current_eval:
        return copy.deepcopy(best_clue)
    if client is not None and best_clue.definition and current_clue.definition:
        winner = choose_better_clue_variant(
            client,
            best_clue.word_normalized,
            len(best_clue.word_normalized),
            best_clue.definition,
            current_clue.definition,
        )
        print(
            f"  Tie-break definiție {best_clue.word_normalized}: "
            f"{winner} a câștigat"
        )
        return copy.deepcopy(current_clue if winner == "B" else best_clue)
    return copy.deepcopy(best_clue)


def _merge_best_clue_variants(
    best_clues: list[ClueEntry],
    current_clues: list[ClueEntry],
    client=None,
) -> list[ClueEntry]:
    merged: list[ClueEntry] = []
    for best_clue, current_clue in zip(best_clues, current_clues):
        chosen = _choose_best_clue(best_clue, current_clue, client=client)
        if chosen.definition == best_clue.definition and current_clue.definition != best_clue.definition:
            print(f"  Păstrez definiția mai bună pentru {best_clue.word_normalized}")
        merged.append(chosen)
    return merged


def _restore_best_scored_clues(puzzle, best_snapshot, client=None) -> None:
    puzzle.horizontal_clues = _merge_best_clue_variants(
        best_snapshot.horizontal_clues,
        puzzle.horizontal_clues,
        client=client,
    )
    puzzle.vertical_clues = _merge_best_clue_variants(
        best_snapshot.vertical_clues,
        puzzle.vertical_clues,
        client=client,
    )


def _puzzle_summary(prepared: PreparedPuzzle) -> str:
    clues = _all_clues(prepared.puzzle)
    preview = "\n".join(
        f"- {clue.word_normalized}: {clue.definition}"
        for clue in clues[:10]
        if clue.definition
    )
    blockers = ", ".join(prepared.blocking_words[:8]) if prepared.blocking_words else "niciunul"
    return (
        f"Titlu: {prepared.title or '[fără titlu]'}\n"
        f"Scor definiții: {prepared.definition_score:.2f}\n"
        f"Blocaje: {blockers}\n"
        f"Exemple:\n{preview}"
    )


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

    score_delta = candidate.definition_score - best.definition_score
    if abs(score_delta) > PUZZLE_TIEBREAK_DELTA:
        return candidate if score_delta > 0 else best

    if client is not None:
        winner = choose_better_puzzle_variant(
            client,
            _puzzle_summary(best),
            _puzzle_summary(candidate),
        )
        print(f"Puzzle tie-break: {winner} a câștigat")
        return candidate if winner == "B" else best

    return candidate if score_delta > 0 else best


def _rewrite_failed_clues(puzzle, client, rounds: int) -> tuple[int, int]:
    theme = puzzle.title or "Puzzle intern"
    passed, total = verify_puzzle(puzzle, client)
    rate_puzzle(puzzle, client)
    best_snapshot = copy.deepcopy(puzzle)

    for round_index in range(1, rounds + 1):
        # Rewrite only low-quality definitions. Verify failure alone is not enough:
        # the local verifier often answers with a synonym despite a good clue.
        candidates = [clue for clue in _all_clues(puzzle) if _needs_rewrite(clue)]

        if not candidates:
            break

        failed_count = sum(1 for c in candidates if c.verified is False)
        low_rated_count = sum(
            1 for c in candidates
            if (
                c.verified is True
                and (
                    (_extract_semantic_score(c) or 0) < RATE_MIN_SEMANTIC
                    or (_extract_guessability_score(c) or 0) < RATE_MIN_GUESSABILITY
                )
            )
        )
        unrated_count = len(candidates) - failed_count - low_rated_count
        print(
            f"Rewrite round {round_index}: {len(candidates)} candidates "
            f"({failed_count} failed, {low_rated_count} low-rated, {unrated_count} unrated)"
        )

        for clue in candidates:
            if _is_locked_clue(clue):
                print(f"  {clue.word_normalized}: blocat la {LOCKED_SCORE}/{LOCKED_SCORE}")
                continue
            wrong_guess = extract_wrong_guess(clue.verify_note)
            rating_feedback = extract_feedback(clue.verify_note)
            try:
                new_definition = rewrite_definition(
                    client,
                    clue.word_normalized,
                    clue.word_original,
                    theme,
                    clue.definition,
                    wrong_guess,
                    rating_feedback=rating_feedback,
                )
            except Exception as e:
                print(f"  Rewrite failed for {clue.word_normalized}: {e}")
                continue
            if new_definition and new_definition != clue.definition:
                print(f"  {clue.word_normalized}: {clue.definition} -> {new_definition}")
                clue.definition = new_definition
            clue.verified = None
            clue.verify_note = ""

        passed, total = verify_puzzle(puzzle, client)
        rate_puzzle(puzzle, client)
        _restore_best_scored_clues(best_snapshot, puzzle, client=client)
        for clue in _all_clues(puzzle):
            if _is_locked_clue(clue):
                print(f"  {clue.word_normalized}: definiție blocată la 9/9")
        best_snapshot = copy.deepcopy(puzzle)

    _restore_best_scored_clues(best_snapshot, puzzle, client=client)
    passed = sum(1 for clue in _all_clues(puzzle) if clue.verified)
    total = len(_all_clues(puzzle))
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
) -> PreparedPuzzle:
    best_prepared: PreparedPuzzle | None = None

    for attempt_index in range(1, preparation_attempts + 1):
        if attempt_index > 1:
            print(
                f"Retrying puzzle {index}/{total_puzzles} ({size}x{size}), "
                f"attempt {attempt_index}/{preparation_attempts}..."
            )

        provisional_title = f"Puzzle {index}"
        candidate = _best_candidate(
            size,
            provisional_title,
            raw_words,
            seen_template_fingerprints=seen_template_fingerprints,
        )
        puzzle = parse_markdown(candidate.markdown)
        puzzle.title = ""
        generate_definitions_for_puzzle(puzzle, client)
        passed, total = _rewrite_failed_clues(puzzle, client, rewrite_rounds)
        blockers = _blocking_clues(puzzle)
        title = generate_title_for_final_puzzle(puzzle, client=client)
        puzzle.title = title
        print(f"Titlu final: {title}")
        prepared = PreparedPuzzle(
            title=title,
            candidate=candidate,
            puzzle=copy.deepcopy(puzzle),
            passed=passed,
            total=total,
            definition_score=_puzzle_definition_score(puzzle),
            blocking_words=[clue.word_normalized for clue in blockers],
        )
        best_prepared = _better_prepared_puzzle(best_prepared, prepared, client=client)

        if blockers:
            print(
                "Rejected puzzle after quality gate: "
                + ", ".join(clue.word_normalized for clue in blockers[:10])
            )

    if best_prepared is None:
        raise RuntimeError(f"Failed to prepare any {size}x{size} puzzle candidate")
    return best_prepared


def _clear_verification_state(puzzle):
    clean = copy.deepcopy(puzzle)
    for clue in _all_clues(clean):
        clue.verified = None
        clue.verify_note = ""
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
    run_dir: Path | None = None,
) -> list[dict]:
    raw_words = _load_words(words_path)
    client = create_client()
    if run_dir is None:
        run_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    seen_7x7_templates: set[str] = set()

    for index, size in enumerate(sizes, start=1):
        puzzle_dir = run_dir / f"{index:02d}_{size}x{size}"
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
        )
        if prepared.blocking_words:
            raise RuntimeError(
                f"Could not prepare a publishable {size}x{size} puzzle. "
                f"Still blocked by: {', '.join(prepared.blocking_words[:12])}"
            )

        template_path = puzzle_dir / "template.md"
        filled_path = puzzle_dir / "filled.md"
        _write_text(template_path, write_grid_template(size, prepared.candidate.template))
        _write_text(filled_path, write_with_definitions(prepared.puzzle))

        defs_puzzle = _clear_verification_state(prepared.puzzle)
        defs_path = puzzle_dir / "defs.md"
        verified_path = puzzle_dir / "verified.md"
        _write_text(defs_path, write_with_definitions(defs_puzzle))
        _write_text(verified_path, write_with_definitions(prepared.puzzle))

        puzzle_id = upload_puzzle(defs_puzzle)
        set_published(puzzle_id, True)

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
        })
        _write_text(run_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and publish a batch of rebus puzzles.")
    parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=[7, 7, 10, 12, 12],
        choices=[7, 10, 12, 15],
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
        default=2,
        help="Automatic define/verify rewrite rounds for failed clues",
    )
    parser.add_argument(
        "--preparation-attempts",
        type=int,
        default=3,
        help="How many candidate puzzles to try before giving up on a size",
    )
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
                run_dir=preview_run_dir,
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
