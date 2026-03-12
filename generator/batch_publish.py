#!/usr/bin/env python3
"""Generate and publish a batch of rebus puzzles."""

from __future__ import annotations

import argparse
import copy
import json
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .core.ai_clues import create_client, rewrite_definition
from .core.grid_template import ALL_TEMPLATES, generate_procedural_template, parse_template
from .core.markdown_io import ClueEntry, parse_markdown, write_filled_grid, write_with_definitions
from .core.quality import QualityReport, filter_word_records, score_words
from .core.slot_extractor import Slot, extract_slots
from .core.word_index import WordEntry, WordIndex
from .core.constraint_solver import solve
from .phases.activate import set_published
from .phases.define import generate_definitions_for_puzzle
from .phases.download import run as download_words
from .phases.upload import upload_puzzle
from .phases.verify import verify_puzzle


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
    markdown: str


def _easy_large_template(size: int) -> list[list[bool]] | None:
    if size != 15:
        return None
    patterns = [
        "..#..#..#..#...",
        "...#...#...#...",
    ]
    pattern = random.choice(patterns)
    grid: list[list[bool]] = []
    for row_index in range(size):
        if row_index % 3 == 2:
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
        if word_index.count_matching([None] * slot.length) < settings.min_candidates_per_slot:
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
) -> Candidate | None:
    template = None
    hardcoded_templates = ALL_TEMPLATES.get(size, [])
    hardcoded_probability = 0.4 if size in (7, 10) else 0.05
    if size == 15 and random.random() < 0.7:
        template = _easy_large_template(size)
    elif hardcoded_templates and random.random() < hardcoded_probability:
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

    slots = extract_slots(template)
    if not _slot_capacity_ok(slots, word_index, settings):
        return None

    grid: list[list[str | None]] = [
        [None if template[row][col] else "#" for col in range(size)]
        for row in range(size)
    ]
    assignment: dict[int, WordEntry] = {}
    used_words: set[str] = set()
    result = solve(slots, word_index, assignment, used_words, grid, settings.max_backtracks)
    if result is None:
        return None

    words = [result[slot.id].normalized for slot in slots]
    report = score_words(words, metadata, size)
    markdown = _render_filled_markdown(size, template, slots, result, title)
    return Candidate(score=report.score, report=report, markdown=markdown)


def _best_candidate(size: int, title: str, raw_words: list[dict]) -> Candidate:
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
            candidate = _generate_candidate(size, settings, word_index, metadata, title)
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


def _rewrite_failed_clues(puzzle, client, rounds: int) -> tuple[int, int]:
    theme = puzzle.title or "Rebus Românesc"
    passed, total = verify_puzzle(puzzle, client)
    for round_index in range(1, rounds + 1):
        failed = [clue for clue in _all_clues(puzzle) if clue.verified is False]
        if not failed:
            break
        print(f"Rewrite round {round_index}: {len(failed)} failed clues")
        for clue in failed:
            wrong_guess = clue.verify_note.removeprefix("AI a ghicit:").strip()
            try:
                new_definition = rewrite_definition(
                    client,
                    clue.word_normalized,
                    clue.word_original,
                    theme,
                    clue.definition,
                    wrong_guess,
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
    return passed, total


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


def run_batch(sizes: list[int], output_root: Path, words_path: Path, rewrite_rounds: int) -> list[dict]:
    raw_words = _load_words(words_path)
    client = create_client()
    run_dir = output_root / datetime.now().strftime("%Y%m%d_%H%M%S")
    manifest: list[dict] = []

    for index, size in enumerate(sizes, start=1):
        title = f"Rebus Românesc {size}x{size} #{index}"
        puzzle_dir = run_dir / f"{index:02d}_{size}x{size}"
        print(f"\n=== Puzzle {index}/{len(sizes)}: {title} ===")

        candidate = _best_candidate(size, title, raw_words)
        filled_path = puzzle_dir / "filled.md"
        _write_text(filled_path, candidate.markdown)

        puzzle = parse_markdown(candidate.markdown)
        generate_definitions_for_puzzle(puzzle, client)
        passed, total = _rewrite_failed_clues(puzzle, client, rewrite_rounds)

        defs_puzzle = _clear_verification_state(puzzle)
        defs_path = puzzle_dir / "defs.md"
        verified_path = puzzle_dir / "verified.md"
        _write_text(defs_path, write_with_definitions(defs_puzzle))
        _write_text(verified_path, write_with_definitions(puzzle))

        puzzle_id = upload_puzzle(defs_puzzle)
        set_published(puzzle_id, True)

        manifest.append({
            "index": index,
            "size": size,
            "title": title,
            "puzzle_id": puzzle_id,
            "score": candidate.score,
            "quality": candidate.report.to_dict(),
            "verification_passed": passed,
            "verification_total": total,
            "output_dir": str(puzzle_dir),
        })
        _write_text(run_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and publish a batch of rebus puzzles.")
    parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=[7, 7, 10, 15, 15],
        choices=[7, 10, 15],
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
    args = parser.parse_args()

    manifest = run_batch(
        sizes=args.sizes,
        output_root=Path(args.output_root),
        words_path=Path(args.words),
        rewrite_rounds=args.rewrite_rounds,
    )
    print("\nBatch complete:")
    for item in manifest:
        print(
            f"  {item['title']} -> {item['puzzle_id']} "
            f"(verify {item['verification_passed']}/{item['verification_total']})"
        )


if __name__ == "__main__":
    main()
