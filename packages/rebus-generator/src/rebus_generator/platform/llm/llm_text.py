"""Shared cleanup helpers for LLM plain-text responses."""

from __future__ import annotations

import re

_RESPONSE_PREFIX_RE = re.compile(
    r"^\*{0,2}(Definiția nouă|Definitia noua|Definiție|Definitie|Răspuns|Raspuns|"
    r"Titlu|Definition|Refined Definition|Final choice|Final Definition|Answer|Response):?\*{0,2}\s*",
    flags=re.IGNORECASE,
)
_LINE_LEADER_RE = re.compile(r"^\s*(?:(?:[-*•]+)\s+|\d+[.)]\s*)")
_META_LINE_RE = re.compile(
    r"^(?:word|meaning|constraints?|goal|rules?|check|attempt(?: \d+)?|option [a-z]|"
    r"self-correction|final polish|final check|language|length)\b",
    flags=re.IGNORECASE,
)
_ALLOWED_TRAILING_PARENS = {
    "(arh.)",
    "(inv.)",
    "(reg.)",
    "(tehn.)",
    "(pop.)",
    "(fam.)",
    "(arg.)",
    "(livr.)",
}
_ENGLISH_META_MARKERS = {
    "answer",
    "better",
    "blows",
    "choice",
    "definition",
    "final",
    "gentleness",
    "good",
    "precise",
    "rebus",
    "response",
    "simple",
    "standard",
    "that",
    "the",
    "weakly",
    "wind",
    "words",
}


def _strip_wrappers(text: str) -> str:
    value = text.strip().strip('"').strip("'").strip()
    for left, right in (("**", "**"), ("__", "__"), ("*", "*"), ("_", "_"), ("`", "`")):
        if value.startswith(left) and value.endswith(right) and len(value) > len(left) + len(right):
            value = value[len(left):-len(right)].strip()
    return value


def _cleanup_line(line: str) -> str:
    value = _LINE_LEADER_RE.sub("", line or "").strip()
    value = _RESPONSE_PREFIX_RE.sub("", value).strip()
    return _strip_wrappers(value)


def _is_meta_line(line: str) -> bool:
    lower = (line or "").strip().lower()
    if not lower:
        return True
    if lower.endswith(":"):
        return True
    return bool(_META_LINE_RE.match(lower))


def _looks_like_english_meta(inner: str) -> bool:
    tokens = re.findall(r"[A-Za-z]+", inner or "")
    if not tokens:
        return False
    lower_tokens = {token.lower() for token in tokens}
    if re.fullmatch(r"\d+\s+words?", (inner or "").strip(), flags=re.IGNORECASE):
        return True
    return bool(lower_tokens & _ENGLISH_META_MARKERS)


def _strip_trailing_meta(text: str) -> str:
    value = text.strip()
    while True:
        paren_match = re.search(r"\s*(\([^()]*\))\s*\.?\s*$", value)
        if not paren_match:
            break
        whole = paren_match.group(1)
        inner = whole[1:-1].strip()
        if whole.lower() in _ALLOWED_TRAILING_PARENS:
            break
        if _looks_like_english_meta(inner):
            value = value[:paren_match.start()].rstrip()
            continue
        break
    value = re.sub(r"\s+(?:->|[-–—])\s+[A-Z][^.!?]*$", "", value).strip()
    return _strip_wrappers(value)


def _pick_candidate_line(text: str) -> str:
    fallback = ""
    for raw_line in re.split(r"[\r\n]+", text):
        line = _cleanup_line(raw_line)
        if not line:
            continue
        if not fallback:
            fallback = line
        if _is_meta_line(line):
            continue
        return line
    return fallback


def clean_llm_text_response(text: str | None) -> str:
    text = (text or "").strip().strip('"').strip("'")
    text = re.sub(r"<\|[^|]*\|>", "", text).strip()
    text = _pick_candidate_line(text)
    text = _strip_trailing_meta(text)
    return _strip_wrappers(text)
