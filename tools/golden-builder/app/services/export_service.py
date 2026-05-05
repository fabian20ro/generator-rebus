from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.schemas import PairRow


def _build_output_name() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"puzzle_{stamp}.jsonl"


def save_jsonl(output_dir: Path, rows: list[PairRow]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / _build_output_name()
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            payload = {
                "puzzle_title": row.puzzle_title,
                "solution": row.solution,
                "definition": row.definition,
            }
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return path
