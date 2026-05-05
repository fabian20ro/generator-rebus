from __future__ import annotations

import re
from pathlib import Path

SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class UnsafePathError(ValueError):
    pass


def sanitize_name(raw: str, *, default: str) -> str:
    candidate = Path(raw).name.strip()
    if not candidate:
        candidate = default
    if not SAFE_NAME_RE.fullmatch(candidate):
        raise UnsafePathError(f"Unsafe path token: {raw}")
    return candidate
