"""Prompt loader — reads .md files from generator/prompts/{system,user}/."""

from __future__ import annotations

import importlib.resources
from functools import lru_cache

_PACKAGE = "generator.prompts"


@lru_cache(maxsize=32)
def load_system_prompt(name: str) -> str:
    """Load a system prompt by name (e.g. 'definition' -> system/definition.md)."""
    ref = importlib.resources.files(f"{_PACKAGE}.system").joinpath(f"{name}.md")
    return ref.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=32)
def load_user_template(name: str) -> str:
    """Load a user prompt template by name (e.g. 'generate' -> user/generate.md)."""
    ref = importlib.resources.files(f"{_PACKAGE}.user").joinpath(f"{name}.md")
    return ref.read_text(encoding="utf-8").strip()
