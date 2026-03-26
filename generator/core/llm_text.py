"""Shared cleanup helpers for LLM plain-text responses."""

from __future__ import annotations

import re


def clean_llm_text_response(text: str | None) -> str:
    text = (text or "").strip().strip('"').strip("'")
    text = re.sub(r"<\|[^|]*\|>", "", text).strip()
    text = re.sub(
        r"^\*{0,2}(Definiția nouă|Definitia noua|Definiție|Definitie|Răspuns|Raspuns|Titlu):?\*{0,2}\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    if "\n" in text:
        text = text.split("\n")[0].strip()
    for left, right in (("**", "**"), ("__", "__"), ("*", "*"), ("_", "_"), ("`", "`")):
        if text.startswith(left) and text.endswith(right) and len(text) > len(left) + len(right):
            text = text[len(left):-len(right)].strip()
    return text
