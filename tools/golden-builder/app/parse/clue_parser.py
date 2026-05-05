from __future__ import annotations

import re


def _split_defs(body: str) -> list[str]:
    parts = [p.strip(" -\t") for p in re.split(r"\s-\s|\s-|-\s", body) if p.strip(" -\t")]
    return [p for p in parts if p]


def parse_clues(text: str) -> tuple[list[str], list[str]]:
    across: list[str] = []
    down: list[str] = []
    section = "across"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if "vertical" in low:
            section = "down"
            continue
        if "orizontal" in low:
            section = "across"
            continue
        m = re.match(r"^(\d+)\.(.*)$", line)
        if not m:
            continue
        defs = _split_defs(m.group(2).strip())
        if section == "across":
            across.extend(defs)
        else:
            down.extend(defs)
    return across, down
