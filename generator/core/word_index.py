"""In-memory word index with length-bucketed positional lookup."""

from __future__ import annotations


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
    (length, position, char) to int bitmasks for fast
    constraint intersection using bitwise AND.
    """

    def __init__(self, words: list[WordEntry]):
        self.by_length: dict[int, list[WordEntry]] = {}
        self.positional: dict[str, int] = {}
        self._all_mask: dict[int, int] = {}
        self._word_index: dict[str, int] = {}

        for word in words:
            length = len(word.normalized)
            if length < 2:
                continue
            if word.normalized in self._word_index:
                continue
            bucket = self.by_length.setdefault(length, [])
            idx = len(bucket)
            bucket.append(word)
            self._word_index[word.normalized] = idx
            bit = 1 << idx
            self._all_mask[length] = self._all_mask.get(length, 0) | bit
            for pos, ch in enumerate(word.normalized):
                key = f"{length}:{pos}:{ch}"
                self.positional[key] = self.positional.get(key, 0) | bit

    def _match_mask(self, pattern: list[str | None]) -> int | None:
        """Compute the bitmask of words matching the pattern.

        Returns None if all positions are wildcards (meaning all words match).
        Returns 0 if no words match.
        """
        length = len(pattern)
        if length not in self.by_length:
            return 0

        result: int | None = None
        for pos, ch in enumerate(pattern):
            if ch is None:
                continue
            key = f"{length}:{pos}:{ch}"
            matching = self.positional.get(key)
            if matching is None:
                return 0
            if result is None:
                result = matching
            else:
                result &= matching
            if result == 0:
                return 0
        return result

    def find_matching(self, pattern: list[str | None]) -> list[WordEntry]:
        """Find all words matching a pattern.

        Pattern is a list where None = wildcard, string = required character.
        Example: [None, 'A', None, 'E'] finds 4-letter words with A at pos 1, E at pos 3.
        """
        length = len(pattern)
        bucket = self.by_length.get(length)
        if not bucket:
            return []

        mask = self._match_mask(pattern)
        if mask is None:
            return list(bucket)
        if mask == 0:
            return []

        result = []
        while mask:
            bit = mask & (-mask)
            idx = bit.bit_length() - 1
            result.append(bucket[idx])
            mask ^= bit
        return result

    def count_matching(self, pattern: list[str | None]) -> int:
        """Count words matching a pattern (faster than find_matching for MRV)."""
        length = len(pattern)
        if length not in self.by_length:
            return 0

        mask = self._match_mask(pattern)
        if mask is None:
            return len(self.by_length[length])
        return mask.bit_count()

    def has_matching(self, pattern: list[str | None], exclude_mask: int = 0) -> bool:
        """Check whether at least one word matches, excluding masked-out indices."""
        length = len(pattern)
        if length not in self.by_length:
            return False

        mask = self._match_mask(pattern)
        if mask is None:
            all_bits = self._all_mask.get(length, 0)
            return (all_bits & ~exclude_mask) != 0
        return (mask & ~exclude_mask) != 0

    def word_to_index(self, normalized: str) -> int | None:
        """Return the index of a word within its length bucket, or None."""
        return self._word_index.get(normalized)

    def word_count(self) -> int:
        return sum(len(b) for b in self.by_length.values())

    def length_stats(self) -> dict[int, int]:
        return {k: len(v) for k, v in sorted(self.by_length.items())}
