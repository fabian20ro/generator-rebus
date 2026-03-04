"""In-memory word index with length-bucketed positional lookup."""

from __future__ import annotations
import random


class WordEntry:
    __slots__ = ("normalized", "original")

    def __init__(self, normalized: str, original: str):
        self.normalized = normalized
        self.original = original

    def __repr__(self) -> str:
        return f"WordEntry({self.normalized!r}, {self.original!r})"


class WordIndex:
    """Fast pattern-matching index for crossword word lookup.

    Words are bucketed by length. A positional index maps
    (length, position, char) to sets of word indices for fast
    constraint intersection.
    """

    def __init__(self, words: list[WordEntry]):
        self.by_length: dict[int, list[WordEntry]] = {}
        self.positional: dict[str, set[int]] = {}

        for word in words:
            length = len(word.normalized)
            if length < 2:
                continue
            bucket = self.by_length.setdefault(length, [])
            idx = len(bucket)
            bucket.append(word)
            for pos, ch in enumerate(word.normalized):
                key = f"{length}:{pos}:{ch}"
                self.positional.setdefault(key, set()).add(idx)

    def find_matching(self, pattern: list[str | None]) -> list[WordEntry]:
        """Find all words matching a pattern.

        Pattern is a list where None = wildcard, string = required character.
        Example: [None, 'A', None, 'E'] finds 4-letter words with A at pos 1, E at pos 3.
        """
        length = len(pattern)
        bucket = self.by_length.get(length)
        if not bucket:
            return []

        result_indices: set[int] | None = None

        for pos, ch in enumerate(pattern):
            if ch is None:
                continue
            key = f"{length}:{pos}:{ch}"
            matching = self.positional.get(key)
            if matching is None:
                return []
            if result_indices is None:
                result_indices = set(matching)
            else:
                result_indices &= matching
            if not result_indices:
                return []

        if result_indices is None:
            return list(bucket)

        return [bucket[i] for i in result_indices]

    def count_matching(self, pattern: list[str | None]) -> int:
        """Count words matching a pattern (faster than find_matching for MRV)."""
        length = len(pattern)
        bucket = self.by_length.get(length)
        if not bucket:
            return 0

        result_indices: set[int] | None = None

        for pos, ch in enumerate(pattern):
            if ch is None:
                continue
            key = f"{length}:{pos}:{ch}"
            matching = self.positional.get(key)
            if matching is None:
                return 0
            if result_indices is None:
                result_indices = set(matching)
            else:
                result_indices &= matching
            if not result_indices:
                return 0

        if result_indices is None:
            return len(bucket)

        return len(result_indices)

    def word_count(self) -> int:
        return sum(len(b) for b in self.by_length.values())

    def length_stats(self) -> dict[int, int]:
        return {k: len(v) for k, v in sorted(self.by_length.items())}
