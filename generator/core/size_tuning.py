"""Central size lists and batch retry floors."""

from __future__ import annotations

SUPPORTED_GRID_SIZES = (7, 8, 9, 10, 11, 12, 13, 14, 15)
DEFAULT_BATCH_SIZES = (7, 8, 9, 10, 11, 12, 13, 14, 15)
OVERNIGHT_LOOP_SIZES = DEFAULT_BATCH_SIZES

MIN_PREPARATION_ATTEMPTS: dict[int, int] = {
    7: 1,
    8: 1,
    9: 16,
    10: 24,
    11: 32,
    12: 40,
    13: 1,
    14: 1,
    15: 1,
}


def get_min_preparation_attempts(size: int) -> int:
    try:
        return MIN_PREPARATION_ATTEMPTS[size]
    except KeyError as exc:
        raise ValueError(f"Unsupported rebus size: {size}") from exc
