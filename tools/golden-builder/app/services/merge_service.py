from __future__ import annotations

from pathlib import Path


def merge_jsonl(base_dir: Path) -> tuple[int, Path]:
    base_dir.mkdir(parents=True, exist_ok=True)
    output_path = base_dir / "merged.jsonl"
    files = sorted(p for p in base_dir.glob("*.jsonl") if p.name != output_path.name)
    count = 0
    with output_path.open("w", encoding="utf-8") as out:
        for path in files:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                out.write(line + "\n")
                count += 1
    return count, output_path
