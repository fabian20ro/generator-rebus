from __future__ import annotations

from pathlib import Path


class UnsafePathError(ValueError):
    pass


def resolve_under(base_dir: Path, user_path: str) -> Path:
    candidate = (base_dir / user_path).resolve()
    base = base_dir.resolve()
    if candidate != base and base not in candidate.parents:
        raise UnsafePathError(f"Path escapes base directory: {user_path}")
    return candidate
