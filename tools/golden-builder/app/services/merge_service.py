from __future__ import annotations

from pathlib import Path


def merge_jsonl(input_dir: Path, output_file: Path) -> int:
    files = sorted(input_dir.glob("*.jsonl"))
    count = 0
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as out:
        for path in files:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                out.write(line + "\n")
                count += 1
    return count
