"""Detect lexical-family leakage between an answer and a clue definition."""

from __future__ import annotations

import re

from dataclasses import dataclass

from .diacritics import normalize


@dataclass(frozen=True)
class FamilyMatch:
    matched_token: str
    matched_stem: str
    answer_stem: str
    leak_kind: str


# Ordered longest-first so that e.g. "inter" matches before "in"
ROMANIAN_PREFIXES = (
    "contra",
    "supra", "super", "inter", "trans", "ultra", "extra",
    "anti", "auto", "post", "semi",
    "pre", "des", "dez", "sub",
    "ne", "re", "in", "im",
)


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


def _strip_prefixes(token: str) -> str:
    """Strip a single Romanian prefix if the remainder is >= 4 chars."""
    for prefix in ROMANIAN_PREFIXES:
        if token.startswith(prefix) and len(token) - len(prefix) >= 4:
            return token[len(prefix):]
    return token


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
    return clue_family_match(answer, definition) is not None


def clue_family_match(answer: str, definition: str) -> FamilyMatch | None:
    """Return first lexical-family leak, if any."""
    if not answer or not definition:
        return None

    answer_tokens = _normalized_tokens(answer)
    if not answer_tokens:
        return None
    answer_token = answer_tokens[0]
    answer_stem = _strip_suffixes(answer_token)

    answer_stems = {answer_stem}
    answer_root = _strip_prefixes(answer_stem)
    answer_has_prefix = answer_root != answer_stem
    if answer_has_prefix:
        answer_stems.add(answer_root)

    for token in _normalized_tokens(definition):
        if token == answer_token:
            return FamilyMatch(
                matched_token=token,
                matched_stem=token,
                answer_stem=answer_token,
                leak_kind="exact_answer",
            )
        token_stem = _strip_suffixes(token)
        token_stems = {token_stem}
        # Only strip prefixes from definition tokens when the answer
        # itself has a prefix, to avoid false positives (e.g. SUBSTANTA
        # vs DISTANTA both stripping to "stanta").
        if answer_has_prefix:
            token_root = _strip_prefixes(token_stem)
            if token_root != token_stem:
                token_stems.add(token_root)
        for a in answer_stems:
            for t in token_stems:
                if _shared_root(a, t):
                    return FamilyMatch(
                        matched_token=token,
                        matched_stem=t,
                        answer_stem=a,
                        leak_kind="family_root",
                    )

    return None


def words_share_family(a: str, b: str) -> bool:
    """Return True if two word forms are obvious family variants."""
    return clue_uses_same_family(a, b)


def forbidden_definition_stems(answer: str) -> list[str]:
    """Compute forbidden word forms for LLM prompts to avoid family leakage."""
    tokens = _normalized_tokens(answer)
    if not tokens:
        return []
    token = tokens[0]
    stem = _strip_suffixes(token)
    if len(stem) < 4:
        return []

    forms = {token.upper()}
    if stem != token:
        forms.add(stem.upper())

    root = _strip_prefixes(stem)
    if root != stem:
        forms.add(root.upper())
    else:
        # Add shortest prefix of stem that _shared_root would catch
        short_len = max(4, len(stem) - 2)
        if short_len < len(stem):
            forms.add(stem[:short_len].upper())

    return sorted(forms)
