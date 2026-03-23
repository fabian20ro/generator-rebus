"""Central size-specific tuning for puzzle generation."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class SizeSettings:
    max_rarity: int
    max_backtracks: int
    target_blacks: int
    solved_candidates: int
    attempt_budget: int
    max_two_letter_slots: int
    min_candidates_per_slot: int
    template_attempts: int = 300
    min_preparation_attempts: int = 1
    max_full_width_slots: int | None = None


SIZE_SETTINGS: dict[int, SizeSettings] = {
    7: SizeSettings(
        max_rarity=3,
        max_backtracks=80_000,
        target_blacks=6,
        solved_candidates=4,
        attempt_budget=55,
        max_two_letter_slots=4,
        min_candidates_per_slot=16,
        template_attempts=500,
        min_preparation_attempts=1,
    ),
    8: SizeSettings(
        max_rarity=3,
        max_backtracks=110_000,
        target_blacks=8,
        solved_candidates=4,
        attempt_budget=70,
        max_two_letter_slots=6,
        min_candidates_per_slot=16,
        template_attempts=700,
        min_preparation_attempts=1,
    ),
    9: SizeSettings(
        max_rarity=4,
        max_backtracks=140_000,
        target_blacks=11,
        solved_candidates=5,
        attempt_budget=85,
        max_two_letter_slots=8,
        min_candidates_per_slot=18,
        template_attempts=900,
        min_preparation_attempts=16,
    ),
    10: SizeSettings(
        max_rarity=4,
        max_backtracks=200_000,
        target_blacks=16,
        solved_candidates=5,
        attempt_budget=100,
        max_two_letter_slots=10,
        min_candidates_per_slot=18,
        template_attempts=1_100,
        min_preparation_attempts=24,
    ),
    11: SizeSettings(
        max_rarity=4,
        max_backtracks=400_000,
        target_blacks=16,
        solved_candidates=4,
        attempt_budget=110,
        max_two_letter_slots=18,
        min_candidates_per_slot=14,
        template_attempts=1_300,
        min_preparation_attempts=32,
        max_full_width_slots=5,
    ),
    12: SizeSettings(
        max_rarity=4,
        max_backtracks=500_000,
        target_blacks=20,
        solved_candidates=4,
        attempt_budget=120,
        max_two_letter_slots=22,
        min_candidates_per_slot=10,
        template_attempts=1_500,
        min_preparation_attempts=40,
        max_full_width_slots=5,
    ),
    13: SizeSettings(
        max_rarity=5,
        max_backtracks=650_000,
        target_blacks=28,
        solved_candidates=3,
        attempt_budget=130,
        max_two_letter_slots=30,
        min_candidates_per_slot=8,
        template_attempts=1_650,
        min_preparation_attempts=1,
        max_full_width_slots=6,
    ),
    14: SizeSettings(
        max_rarity=5,
        max_backtracks=850_000,
        target_blacks=40,
        solved_candidates=2,
        attempt_budget=140,
        max_two_letter_slots=40,
        min_candidates_per_slot=6,
        template_attempts=1_900,
        min_preparation_attempts=1,
        max_full_width_slots=7,
    ),
    15: SizeSettings(
        max_rarity=5,
        max_backtracks=420_000,
        target_blacks=60,
        solved_candidates=1,
        attempt_budget=40,
        max_two_letter_slots=50,
        min_candidates_per_slot=4,
        template_attempts=1_800,
        min_preparation_attempts=1,
    ),
}


SUPPORTED_GRID_SIZES = tuple(sorted(SIZE_SETTINGS))
DEFAULT_BATCH_SIZES = (7, 8, 9, 10, 11, 12, 13, 14, 15)
OVERNIGHT_LOOP_SIZES = DEFAULT_BATCH_SIZES


def get_size_settings(size: int) -> SizeSettings:
    try:
        return SIZE_SETTINGS[size]
    except KeyError as exc:
        raise ValueError(f"Unsupported rebus size: {size}") from exc


def get_min_preparation_attempts(size: int) -> int:
    return get_size_settings(size).min_preparation_attempts


def build_relaxed_variants(size: int) -> list[SizeSettings]:
    settings = get_size_settings(size)
    return [
        settings,
        replace(
            settings,
            max_rarity=min(5, settings.max_rarity + 1),
            max_backtracks=settings.max_backtracks * 2,
            target_blacks=settings.target_blacks + 2,
            attempt_budget=settings.attempt_budget + 20,
            max_two_letter_slots=settings.max_two_letter_slots + 2,
            min_candidates_per_slot=max(8, settings.min_candidates_per_slot - 4),
        ),
        replace(
            settings,
            max_rarity=5,
            max_backtracks=settings.max_backtracks * 3,
            target_blacks=settings.target_blacks + 4,
            solved_candidates=max(3, settings.solved_candidates - 1),
            attempt_budget=settings.attempt_budget + 35,
            max_two_letter_slots=settings.max_two_letter_slots + (6 if settings.max_two_letter_slots >= 16 else 4),
            min_candidates_per_slot=max(6, settings.min_candidates_per_slot - 8),
        ),
    ]
