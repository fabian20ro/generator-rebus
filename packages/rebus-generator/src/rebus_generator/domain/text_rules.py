"""Shared normalized text helpers for title and clue screening."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .diacritics import normalize

_TOKEN_RE = re.compile(r"[A-Z0-9]+")


def tokenize_normalized_words(text: str) -> list[str]:
    return _TOKEN_RE.findall(normalize(text or ""))


def normalize_text_for_match(text: str) -> str:
    return " ".join(tokenize_normalized_words(text))


def contains_normalized_forbidden_word(
    text: str,
    forbidden_words: Iterable[str],
    *,
    min_length: int = 1,
) -> bool:
    text_tokens = set(tokenize_normalized_words(text))
    if not text_tokens:
        return False
    for word in forbidden_words:
        normalized_word = normalize((word or "").strip())
        if len(normalized_word) < min_length:
            continue
        if normalized_word in text_tokens:
            return True
    return False
