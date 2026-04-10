"""Prompt loader for production prompt assets."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


_PROMPTS_ROOT = Path(__file__).resolve().parent
_SYSTEM_PATHS = {
    "definition": _PROMPTS_ROOT / "production" / "definition" / "system.md",
    "verify": _PROMPTS_ROOT / "production" / "verify" / "system.md",
    "rate": _PROMPTS_ROOT / "production" / "rate" / "system.md",
    "rewrite": _PROMPTS_ROOT / "production" / "rewrite" / "system.md",
    "theme": _PROMPTS_ROOT / "production" / "title" / "theme_system.md",
    "title_rate": _PROMPTS_ROOT / "production" / "title" / "rate_system.md",
    "clue_compare": _PROMPTS_ROOT / "production" / "compare" / "system.md",
    "clue_tiebreaker": _PROMPTS_ROOT / "production" / "tiebreak" / "clue_system.md",
    "puzzle_tiebreaker": _PROMPTS_ROOT / "production" / "tiebreak" / "puzzle_system.md",
}
_USER_PATHS = {
    "generate": _PROMPTS_ROOT / "production" / "definition" / "user.md",
    "verify": _PROMPTS_ROOT / "production" / "verify" / "user.md",
    "rate": _PROMPTS_ROOT / "production" / "rate" / "user.md",
    "rewrite": _PROMPTS_ROOT / "production" / "rewrite" / "user.md",
    "title_generate": _PROMPTS_ROOT / "production" / "title" / "generate_user.md",
    "title_rate": _PROMPTS_ROOT / "production" / "title" / "rate_user.md",
    "clue_compare": _PROMPTS_ROOT / "production" / "compare" / "user.md",
    "clue_tiebreak": _PROMPTS_ROOT / "production" / "tiebreak" / "clue_user.md",
    "puzzle_tiebreak": _PROMPTS_ROOT / "production" / "tiebreak" / "puzzle_user.md",
}


@lru_cache(maxsize=32)
def load_system_prompt(name: str) -> str:
    path = _SYSTEM_PATHS[name]
    return path.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=32)
def load_user_template(name: str) -> str:
    path = _USER_PATHS[name]
    return path.read_text(encoding="utf-8").strip()
