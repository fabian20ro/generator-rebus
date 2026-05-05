from __future__ import annotations

import json
from pathlib import Path

from app.schemas import PairRow
from app.services.path_safety import sanitize_name


def save_jsonl(output_dir: Path, filename: str, rows: list[PairRow]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_filename = sanitize_name(filename, default="puzzle.jsonl")
    path = output_dir / safe_filename
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            payload = {
                "puzzle_title": row.puzzle_title,
                "solution": row.solution,
                "definition": row.definition,
            }
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return path
