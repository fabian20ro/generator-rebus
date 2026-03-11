"""Read and write the rebus markdown format.

The markdown format is progressive — each phase adds information:
- generate-grid: Grid with '.' and '#'
- fill: Grid with letters + H/V word lists
- theme: Title added
- define: Definitions added (word [original] - definition)
- verify: Checkmarks ✓/✗ added
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field


@dataclass
class ClueEntry:
    """A single clue in the puzzle."""
    row_number: int       # row number in the H/V section (1-based)
    word_normalized: str  # ASCII uppercase
    word_original: str    # with diacritics (empty if not yet defined)
    definition: str       # empty if not yet defined
    verified: bool | None = None  # None = not verified, True = ✓, False = ✗
    verify_note: str = ""  # note from verification (e.g., "AI guessed: CAPRE")
    start_row: int = 0
    start_col: int = 0


@dataclass
class PuzzleData:
    """Parsed puzzle data from markdown."""
    title: str = "Rebus"
    size: int = 10
    grid: list[list[str]] = field(default_factory=list)  # '#' or letter or '.'
    horizontal_clues: list[ClueEntry] = field(default_factory=list)
    vertical_clues: list[ClueEntry] = field(default_factory=list)


def parse_markdown(content: str) -> PuzzleData:
    """Parse a rebus markdown file into structured data."""
    puzzle = PuzzleData()
    lines = content.strip().split("\n")
    section = None  # 'grid', 'horizontal', 'vertical'
    grid_lines: list[str] = []
    in_code_block = False

    for line in lines:
        stripped = line.strip()

        # Title
        if stripped.startswith("# Rebus"):
            title = stripped[2:].strip()
            if title.startswith("Rebus:"):
                title = title[6:].strip()
            elif title == "Rebus":
                title = ""
            puzzle.title = title
            continue

        # Metadata
        if stripped.startswith("Dimensiune:"):
            try:
                size_str = stripped.split(":")[1].strip().split("x")[0]
                puzzle.size = int(size_str)
            except (ValueError, IndexError):
                pass
            continue

        # Sections
        if stripped == "## Grid":
            section = "grid"
            continue
        if stripped in ("## Orizontal", "### Orizontal"):
            section = "horizontal"
            continue
        if stripped in ("## Vertical", "### Vertical"):
            section = "vertical"
            continue
        if stripped.startswith("## ") and section:
            section = None
            continue

        # Code block markers
        if stripped == "```":
            in_code_block = not in_code_block
            continue

        if not stripped:
            continue

        # Grid lines
        if section == "grid":
            chars = stripped.split()
            if chars and all(len(ch) == 1 for ch in chars):
                grid_lines.append(stripped)

        # Clue lines
        if section in ("horizontal", "vertical"):
            clue = _parse_clue_line(stripped)
            if clue:
                if section == "horizontal":
                    puzzle.horizontal_clues.append(clue)
                else:
                    puzzle.vertical_clues.append(clue)

    # Parse grid
    for row_str in grid_lines:
        row = row_str.split()
        puzzle.grid.append(row)

    if puzzle.grid:
        puzzle.size = len(puzzle.grid)

    return puzzle


def _parse_clue_line(line: str) -> ClueEntry | None:
    """Parse a single clue line.

    Formats:
    - "1. CASA - MARE" (fill phase, multiple words per row)
    - "1. CASA [casă] - Locul unde te simți acasă" (define phase)
    - "1. ✓ CASA [casă] - Locul unde te simți acasă" (verify phase)
    - "1. ✗ OI [oi] - Animale → AI a ghicit: CAPRE" (verify phase, failed)
    """
    # Match: number. [✓✗]? WORD [original]? - definition?
    # Or: number. WORD1 - WORD2 - WORD3 (fill phase, just words separated by -)
    m = re.match(r"^(\d+)\.\s*", line)
    if not m:
        return None

    row_num = int(m.group(1))
    rest = line[m.end():].strip()

    # Check for verify marker
    verified = None
    verify_note = ""
    if rest.startswith("✓ "):
        verified = True
        rest = rest[2:].strip()
    elif rest.startswith("✗ "):
        verified = False
        rest = rest[2:].strip()
        # Extract verify note after →
        if "→" in rest:
            note_idx = rest.index("→")
            verify_note = rest[note_idx + 1:].strip()

    # Try to match WORD [original] - definition
    m2 = re.match(r"^([A-Z]+)\s*\[([^\]]*)\]\s*-\s*(.*)", rest)
    if m2:
        tail = m2.group(3).strip()
        parts = [m2.group(1) + (f" [{m2.group(2)}]" if m2.group(2) else "")]
        parts.extend(p.strip() for p in tail.split(" - ") if p.strip())
        fill_like = all(re.match(r"^[A-Z]+(\s*\[[^\]]*\])?$", p) for p in parts)
        if fill_like:
            parsed_parts: list[tuple[str, str]] = []
            for part in parts:
                m_part = re.match(r"^([A-Z]+)(?:\s*\[([^\]]*)\])?$", part)
                if not m_part:
                    parsed_parts = []
                    break
                parsed_parts.append((m_part.group(1), m_part.group(2) or ""))

            if parsed_parts:
                return ClueEntry(
                    row_number=row_num,
                    word_normalized=" - ".join(word for word, _ in parsed_parts),
                    word_original=" - ".join(original for _, original in parsed_parts),
                    definition="",
                    verified=verified,
                    verify_note=verify_note,
                )

        return ClueEntry(
            row_number=row_num,
            word_normalized=m2.group(1),
            word_original=m2.group(2),
            definition=tail,
            verified=verified,
            verify_note=verify_note,
        )

    # Try plain define/verify format: WORD - definition
    m3 = re.match(r"^([A-Z]+)\s*-\s*(.*)", rest)
    if m3:
        head = m3.group(1)
        tail = m3.group(2).strip()

        # Distinguish a real definition from fill-phase "WORD1 - WORD2 - ..."
        parts = [p.strip() for p in tail.split(" - ") if p.strip()]
        fill_like = bool(parts) and all(re.match(r"^[A-Z]+(\s*\[[^\]]*\])?$", p) for p in parts)
        if not fill_like:
            return ClueEntry(
                row_number=row_num,
                word_normalized=head,
                word_original="",
                definition=tail,
                verified=verified,
                verify_note=verify_note,
            )

    # Try fill-phase format: WORD1 [orig1] - WORD2 [orig2] - ...
    parts = [w.strip() for w in rest.split(" - ") if w.strip()]
    if parts:
        parsed_parts: list[tuple[str, str]] = []
        for part in parts:
            m_part = re.match(r"^([A-Z]+)(?:\s*\[([^\]]*)\])?$", part)
            if not m_part:
                parsed_parts = []
                break
            parsed_parts.append((m_part.group(1), m_part.group(2) or ""))

        if parsed_parts:
            return ClueEntry(
                row_number=row_num,
                word_normalized=" - ".join(word for word, _ in parsed_parts),
                word_original=" - ".join(original for _, original in parsed_parts),
                definition="",
                verified=verified,
                verify_note=verify_note,
            )

    # Fallback: just a word
    word_match = re.match(r"^([A-Z]+)", rest)
    if word_match:
        return ClueEntry(
            row_number=row_num,
            word_normalized=word_match.group(1),
            word_original="",
            definition="",
            verified=verified,
            verify_note=verify_note,
        )

    return None


def write_grid_template(size: int, grid: list[list[bool]]) -> str:
    """Write a grid template (pre-fill) to markdown."""
    lines = [
        "# Rebus",
        "",
        f"Dimensiune: {size}x{size}",
        "",
        "## Grid",
        "",
    ]
    for row in grid:
        line = " ".join("#" if not cell else "." for cell in row)
        lines.append(line)
    lines.append("")
    return "\n".join(lines)


def write_filled_grid(size: int, grid: list[list[str | None]],
                      h_words: list[list[str]],
                      v_words: list[list[str]],
                      h_originals: list[list[str]],
                      v_originals: list[list[str]],
                      title: str = "") -> str:
    """Write a filled grid with word lists to markdown.

    h_words/v_words: words per row/column (normalized)
    h_originals/v_originals: original forms with diacritics
    """
    header = f"# Rebus: {title}" if title else "# Rebus"
    lines = [
        header,
        "",
        f"Dimensiune: {size}x{size}",
        "",
        "## Grid",
        "",
    ]

    for row in grid:
        line = " ".join(cell if cell else "#" for cell in row)
        lines.append(line)

    lines.append("")
    lines.append("## Orizontal")
    lines.append("")

    for i, (words, originals) in enumerate(zip(h_words, h_originals)):
        if words:
            parts = []
            for w, o in zip(words, originals):
                if o and o != w.lower():
                    parts.append(f"{w} [{o}]")
                else:
                    parts.append(w)
            lines.append(f"{i + 1}. {' - '.join(parts)}")

    lines.append("")
    lines.append("## Vertical")
    lines.append("")

    for i, (words, originals) in enumerate(zip(v_words, v_originals)):
        if words:
            parts = []
            for w, o in zip(words, originals):
                if o and o != w.lower():
                    parts.append(f"{w} [{o}]")
                else:
                    parts.append(w)
            lines.append(f"{i + 1}. {' - '.join(parts)}")

    lines.append("")
    return "\n".join(lines)


def write_with_definitions(puzzle: PuzzleData) -> str:
    """Write the full puzzle markdown with definitions."""
    header = f"# Rebus: {puzzle.title}" if puzzle.title else "# Rebus"
    lines = [
        header,
        "",
        f"Dimensiune: {puzzle.size}x{puzzle.size}",
        "",
        "## Grid",
        "",
    ]

    for row in puzzle.grid:
        lines.append(" ".join(row))

    lines.append("")
    lines.append("## Orizontal")
    lines.append("")

    for clue in puzzle.horizontal_clues:
        lines.append(_format_clue(clue))

    lines.append("")
    lines.append("## Vertical")
    lines.append("")

    for clue in puzzle.vertical_clues:
        lines.append(_format_clue(clue))

    lines.append("")
    return "\n".join(lines)


def _format_clue(clue: ClueEntry) -> str:
    """Format a single clue entry as markdown."""
    parts = [f"{clue.row_number}."]

    if clue.verified is True:
        parts.append("✓")
    elif clue.verified is False:
        parts.append("✗")

    if clue.word_original:
        parts.append(f"{clue.word_normalized} [{clue.word_original}]")
    else:
        parts.append(clue.word_normalized)

    if clue.definition:
        parts.append(f"- {clue.definition}")

    if clue.verify_note:
        parts.append(f"→ {clue.verify_note}")

    return " ".join(parts)
