"""Plateau detection for iterative improvement loops."""

from __future__ import annotations


def has_plateaued(score_history: list[int], lookback: int) -> bool:
    """True when the latest score hasn't improved vs `lookback` steps ago.

    Returns False when the history is shorter than `lookback` entries,
    since there isn't enough data to judge improvement.
    """
    if len(score_history) < lookback:
        return False
    return score_history[-1] <= score_history[-lookback]
