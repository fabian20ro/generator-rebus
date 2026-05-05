from __future__ import annotations

from pathlib import Path

from app.services.path_safety import resolve_under


def merge_jsonl(base_input_dir: Path, base_output_dir: Path, input_subdir: str, output_file: str) -> int:
    input_dir = resolve_under(base_input_dir, input_subdir)
    output_path = resolve_under(base_output_dir, output_file)
    files = sorted(input_dir.glob("*.jsonl"))
    count = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as out:
        for path in files:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                out.write(line + "\n")
                count += 1
    return count
