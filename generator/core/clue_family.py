"""Detect lexical-family leakage between an answer and a clue definition."""

from __future__ import annotations

import re

from .diacritics import normalize


ROMANIAN_SUFFIXES = (
    "urilor",
    "iilor",
    "ismului",
    "istului",
    "itatea",
    "itati",
    "ității",
    "iitate",
    "oarele",
    "oarea",
    "ația",
    "ație",
    "ații",
    "iune",
    "iuni",
    "mente",
    "ment",
    "oasa",
    "oase",
    "elor",
    "ilor",
    "ului",
    "iști",
    "iste",
    "istă",
    "isti",
    "iile",
    "iile",
    "iile",
    "iile",
    "ilor",
    "elor",
    "ului",
    "ate",
    "ată",
    "ati",
    "atei",
    "atie",
    "atii",
    "isme",
    "ism",
    "ist",
    "ica",
    "ice",
    "ici",
    "ică",
    "ețe",
    "ețea",
    "etea",
    "ete",
    "ele",
    "ele",
    "ilor",
    "ilor",
    "ilor",
    "ilor",
    "ul",
    "ui",
    "ei",
    "ii",
    "ia",
    "ie",
    "ea",
    "ele",
    "elei",
    "elor",
    "elor",
    "le",
    "lor",
    "ta",
    "te",
    "ti",
    "ți",
    "a",
    "ă",
    "e",
    "i",
    "u",
)


def _normalized_tokens(text: str) -> list[str]:
    normalized = normalize(text).lower()
    return re.findall(r"[a-z]+", normalized)


def _strip_suffixes(token: str) -> str:
    stem = token
    changed = True
    while changed:
        changed = False
        for suffix in ROMANIAN_SUFFIXES:
            if len(stem) - len(suffix) < 4:
                continue
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
                changed = True
                break
    return stem


def _shared_root(answer_stem: str, token_stem: str) -> bool:
    if answer_stem == token_stem:
        return True
    min_len = min(len(answer_stem), len(token_stem))
    if min_len < 4:
        return False
    shared = 0
    for a_ch, b_ch in zip(answer_stem, token_stem):
        if a_ch != b_ch:
            break
        shared += 1
    return shared >= max(4, min_len - 1)


def clue_uses_same_family(answer: str, definition: str) -> bool:
    """Return True if the clue leaks the answer or an obvious close-family form."""
    if not answer or not definition:
        return False

    answer_tokens = _normalized_tokens(answer)
    if not answer_tokens:
        return False
    answer_token = answer_tokens[0]
    answer_stem = _strip_suffixes(answer_token)

    for token in _normalized_tokens(definition):
        if token == answer_token:
            return True
        token_stem = _strip_suffixes(token)
        if _shared_root(answer_stem, token_stem):
            return True

    return False
