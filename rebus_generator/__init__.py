"""Repo-root bootstrap package for the src-based rebus_generator layout."""

from __future__ import annotations

from pathlib import Path


_PACKAGE_DIR = (
    Path(__file__).resolve().parent.parent
    / "packages"
    / "rebus-generator"
    / "src"
    / "rebus_generator"
)

if not _PACKAGE_DIR.is_dir():
    raise ImportError(f"Missing rebus_generator package dir: {_PACKAGE_DIR}")

__path__ = [str(_PACKAGE_DIR)]
