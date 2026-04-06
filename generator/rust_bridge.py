"""Rust phase-1 bridge for crossword generation."""

import json
import random
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .core.markdown_io import write_filled_grid
from .core.quality import QualityReport
from .core.runtime_logging import log
from .phases.download import run as download_words

@dataclass
class Candidate:
    score: float
    report: QualityReport
    template: list[list[bool]]
    markdown: str
    metadata: dict[str, list[dict]] = field(default_factory=dict)
    stats: dict[str, int | float] = field(default_factory=dict)


REPO_ROOT = Path(__file__).resolve().parent.parent
RUST_ENGINE_BINARY = (
    REPO_ROOT / "crossword_engine" / "target" / "release" / "crossword_phase1"
)
RUST_ENGINE_DEBUG_BINARY = (
    REPO_ROOT / "crossword_engine" / "target" / "debug" / "crossword_phase1"
)


def _load_words(words_path: Path) -> list[dict]:
    if not words_path.exists():
        words_path.parent.mkdir(parents=True, exist_ok=True)
        download_words("-", str(words_path))
    with open(words_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _metadata_by_word(raw_words: list[dict]) -> dict[str, list[dict]]:
    metadata: dict[str, list[dict]] = {}
    for word in raw_words:
        normalized = word.get("normalized", "")
        if not normalized:
            continue
        metadata.setdefault(normalized, []).append(word)
    return metadata


def _normalize_metadata_pool(
    metadata: dict[str, dict] | dict[str, list[dict]] | None,
) -> dict[str, list[dict]]:
    if not metadata:
        return {}
    normalized: dict[str, list[dict]] = {}
    for word, value in metadata.items():
        if isinstance(value, list):
            normalized[word] = [dict(entry) for entry in value]
        else:
            normalized[word] = [dict(value)]
    return normalized


def _rust_binary_path() -> Path:
    if RUST_ENGINE_BINARY.exists():
        return RUST_ENGINE_BINARY
    if RUST_ENGINE_DEBUG_BINARY.exists():
        return RUST_ENGINE_DEBUG_BINARY
    raise RuntimeError(
        "Rust phase-1 binary missing. Run `run_batch_loop.sh` or "
        "`cargo build --release --manifest-path crossword_engine/Cargo.toml` first."
    )


def _quality_report_from_payload(payload: dict) -> QualityReport:
    return QualityReport(
        score=float(payload.get("score", 0.0)),
        word_count=int(payload.get("word_count", 0)),
        average_length=float(payload.get("average_length", 0.0)),
        average_rarity=float(payload.get("average_rarity", 0.0)),
        two_letter_words=int(payload.get("two_letter_words", 0)),
        three_letter_words=int(payload.get("three_letter_words", 0)),
        high_rarity_words=int(payload.get("high_rarity_words", 0)),
        uncommon_letter_words=int(payload.get("uncommon_letter_words", 0)),
        friendly_words=int(payload.get("friendly_words", 0)),
        max_rarity=int(payload.get("max_rarity", 0)),
        average_definability=float(payload.get("average_definability", 0.0)),
    )


def _render_markdown_from_rust_payload(
    title: str,
    template: list[list[bool]],
    filled_grid_payload: list[list[str | None]],
    slots_payload: list[dict],
    words_payload: list[dict],
) -> str:
    size = len(template)
    grid_out: list[list[str | None]] = []
    for row_index in range(size):
        rendered_row: list[str | None] = []
        for col_index in range(size):
            if not template[row_index][col_index]:
                rendered_row.append(None)
            else:
                rendered_row.append(filled_grid_payload[row_index][col_index])
        grid_out.append(rendered_row)

    h_words: list[list[str]] = [[] for _ in range(size)]
    h_originals: list[list[str]] = [[] for _ in range(size)]
    v_words: list[list[str]] = [[] for _ in range(size)]
    v_originals: list[list[str]] = [[] for _ in range(size)]
    word_by_slot = {int(word["slot_id"]): word for word in words_payload}
    for slot in slots_payload:
        slot_id = int(slot["id"])
        word = word_by_slot[slot_id]
        original = word.get("normalized", "")
        if slot["direction"] == "H":
            h_words[int(slot["start_row"])].append(word["normalized"])
            h_originals[int(slot["start_row"])].append(original)
        else:
            v_words[int(slot["start_col"])].append(word["normalized"])
            v_originals[int(slot["start_col"])].append(original)

    return write_filled_grid(
        size, grid_out, h_words, v_words, h_originals, v_originals, title=title
    )


def _best_candidate_rust(
    size: int,
    title: str,
    *,
    words_path: Path,
    metadata: dict[str, list[dict]],
    rng: random.Random,
    preparation_attempts: int = 1,
) -> Candidate:
    seed = rng.randint(1, 10_000_000)
    command = [
        str(_rust_binary_path()),
        "--size",
        str(size),
        "--words",
        str(words_path),
        "--seed",
        str(seed),
        "--preparation-attempts",
        str(max(1, preparation_attempts)),
    ]
    result = subprocess.run(
        command,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.stderr:
        log(result.stderr.rstrip("\n"))
    if result.returncode != 0:
        raise RuntimeError(
            f"Rust phase-1 failed for {size}x{size} with exit {result.returncode}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Rust phase-1 returned invalid JSON: {exc}") from exc

    template = [[bool(cell) for cell in row] for row in payload["template"]]
    report = _quality_report_from_payload(payload["quality"])
    markdown = _render_markdown_from_rust_payload(
        title,
        template,
        payload["filled_grid"],
        payload["slots"],
        payload["words"],
    )
    stats = dict(payload.get("stats", {}))
    log(
        f"  Rust phase-1 {size}x{size}: score={report.score:.1f} "
        f"two={report.two_letter_words} three={report.three_letter_words} "
        f"elapsed_ms={int(stats.get('elapsed_ms', 0) or 0)} "
        f"nodes={int(stats.get('solver_nodes', 0) or 0)} "
        f"solved={int(stats.get('solved_candidates', 0) or 0)}"
    )
    return Candidate(
        score=report.score,
        report=report,
        template=template,
        markdown=markdown,
        metadata=_normalize_metadata_pool(metadata),
        stats=stats,
    )


def _best_candidate(
    size: int,
    title: str,
    raw_words: list[dict],
    rng: random.Random,
    seen_template_fingerprints: set[str] | None = None,
    *,
    words_path: Path | None = None,
    word_metadata: dict[str, dict] | dict[str, list[dict]] | None = None,
    preparation_attempts: int = 1,
) -> Candidate:
    if words_path is None:
        raise ValueError("Rust phase-1 requires `words_path`.")
    candidate = _best_candidate_rust(
        size,
        title,
        words_path=words_path,
        metadata=_normalize_metadata_pool(word_metadata)
        or _metadata_by_word(raw_words),
        rng=rng,
        preparation_attempts=preparation_attempts,
    )
    if seen_template_fingerprints is not None and size == 7:
        seen_template_fingerprints.add(_template_fingerprint(candidate.template))
    return candidate




def _template_fingerprint(template: list[list[bool]]) -> str:
    return "|".join("".join("." if cell else "#" for cell in row) for row in template)


