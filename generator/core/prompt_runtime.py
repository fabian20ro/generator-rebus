"""Prompt preloading and runtime audit helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..prompts.loader import load_system_prompt, load_user_template

PROMPT_SYSTEM_NAMES = (
    "definition",
    "rewrite",
    "verify",
    "rate",
    "theme",
    "title_rate",
    "clue_tiebreaker",
    "puzzle_tiebreaker",
)

PROMPT_USER_NAMES = (
    "generate",
    "rewrite",
    "verify",
    "rate",
    "title_generate",
    "title_rate",
    "clue_tiebreak",
    "puzzle_tiebreak",
)


def preload_runtime_prompts() -> dict[str, tuple[str, ...]]:
    for name in PROMPT_SYSTEM_NAMES:
        load_system_prompt(name)
    for name in PROMPT_USER_NAMES:
        load_user_template(name)
    return {
        "system": PROMPT_SYSTEM_NAMES,
        "user": PROMPT_USER_NAMES,
    }


def prompt_runtime_audit(project_root: Path) -> dict[str, object]:
    git_head = ""
    dirty_prompt_files: list[str] = []
    try:
        git_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:
        git_head = ""

    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "generator/prompts"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        dirty_prompt_files = [line[3:] for line in status if len(line) > 3]
    except Exception:
        dirty_prompt_files = []

    return {
        "git_head": git_head,
        "dirty_prompt_files": dirty_prompt_files,
    }
