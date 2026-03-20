"""Phase 4: Find a theme for the filled grid using LM Studio."""

from __future__ import annotations

import json
import random
import sys

from ..core.ai_clues import create_client
from ..core.diacritics import normalize
from ..core.markdown_io import parse_markdown, write_with_definitions
from ..prompts.loader import load_system_prompt, load_user_template


def _collect_words(puzzle) -> list[str]:
    """Collect all unique words from the puzzle clues."""
    words = set()
    for clue in puzzle.horizontal_clues:
        for w in clue.word_normalized.split(" - "):
            w = w.strip()
            if w:
                words.add(w)
    for clue in puzzle.vertical_clues:
        for w in clue.word_normalized.split(" - "):
            w = w.strip()
            if w:
                words.add(w)
    return sorted(words)


def _collect_definitions(puzzle) -> list[str]:
    definitions = []
    for clue in puzzle.horizontal_clues + puzzle.vertical_clues:
        if clue.definition and not clue.definition.startswith("["):
            definitions.append(clue.definition.strip())
    return definitions

TITLE_MIN_CREATIVITY = 5
MAX_TITLE_ROUNDS = 7

FALLBACK_TITLES = [
    "Fir de Cuvinte",
    "Sensuri Comune",
    "Noduri de Sens",
    "Semne Încrucișate",
    "Puncte Comune",
    "Umbra Cuvintelor",
    "Joc de Cuvinte",
    "Căi Încrucișate",
    "Labirint de Idei",
    "Prisme și Ecouri",
    "Oglinzi Paralele",
    "Răscruce de Gânduri",
    "Spirale Ascunse",
    "Între Rânduri",
    "Carusel Lexical",
    "Mozaic de Sensuri",
    "Ferestre Deschise",
    "Punți Nevăzute",
    "Ecou de Litere",
    "Orizont Fragmentat",
]

TITLE_ENGLISH_MARKERS = {
    "blue",
    "dream",
    "dreams",
    "echo",
    "echoes",
    "fire",
    "fires",
    "gold",
    "jazz",
    "light",
    "lights",
    "mirror",
    "mirrors",
    "moon",
    "night",
    "nights",
    "river",
    "rivers",
    "shadow",
    "shadows",
    "silent",
    "sky",
    "skies",
    "sunset",
    "whisper",
    "whispers",
}


def _fallback_title() -> str:
    return random.choice(FALLBACK_TITLES)


def _sanitize_title(title: str, input_words: list[str] | None = None) -> str:
    cleaned = " ".join(title.strip().strip('"').strip("'").split())
    cleaned = cleaned.rstrip(".,;:!?…")
    if not cleaned:
        return _fallback_title()

    # Reject comma-separated word lists (2+ commas)
    if cleaned.count(",") >= 2:
        return _fallback_title()

    blocked = {"rebus", "romanesc", "românesc", "puzzle", "titlu"}
    title_tokens = set(cleaned.lower().split())
    if title_tokens & blocked:
        return _fallback_title()

    parts = cleaned.split()
    if len(parts) > 4:
        return _fallback_title()

    english_hits = sum(1 for token in cleaned.lower().split() if token in TITLE_ENGLISH_MARKERS)
    if english_hits >= 2:
        return _fallback_title()

    # Only reject if 2+ input words of length >= 4 appear in the title
    if input_words:
        title_upper = normalize(cleaned)
        match_count = sum(
            1 for word in input_words
            if len(word) >= 4 and normalize(word) in title_upper
        )
        if match_count >= 2:
            return _fallback_title()

    return cleaned


def _try_switch_model(current_model, multi_model: bool):
    """Switch to the other model if multi_model is enabled. Returns new current_model."""
    if not multi_model or current_model is None:
        return current_model
    from ..core.model_manager import PRIMARY_MODEL, SECONDARY_MODEL, ensure_model_loaded
    next_model = SECONDARY_MODEL if current_model == PRIMARY_MODEL else PRIMARY_MODEL
    try:
        ensure_model_loaded(next_model)
        return next_model
    except Exception:
        return current_model


def rate_title_creativity(title: str, words: list[str], client) -> tuple[int, str]:
    """Rate title creativity. Returns (score, feedback)."""
    prompt = load_user_template("title_rate").format(
        title=title,
        words=", ".join(words[:10]),
    )
    try:
        response = client.chat.completions.create(
            model="default",
            messages=[
                {"role": "system", "content": load_system_prompt("title_rate")},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=100,
        )
        raw = response.choices[0].message.content or ""
        data = json.loads(raw)
        return int(data.get("creativity_score", 0)), str(data.get("feedback", ""))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return 0, "parse error"
    except Exception:
        return 0, "api error"


# ---------------------------------------------------------------------------
# Level 1 — single LLM call
# ---------------------------------------------------------------------------

def _generate_single_title(
    definitions: list[str],
    client,
    rejected_context: str = "",
    temperature: float = 0.9,
    words: list[str] | None = None,
) -> str:
    """Make one LLM call to generate a title. Returns raw string."""
    if definitions:
        content_section = (
            "Definițiile din rebus sunt:\n"
            + "\n".join(f"- {d}" for d in definitions[:15])
            + "\n\nCe temă leagă aceste definiții?"
        )
    elif words:
        content_section = (
            "Lista de cuvinte este:\n"
            + ", ".join(words[:15])
        )
    else:
        return ""

    prompt = load_user_template("title_generate").format(
        content_section=content_section,
        rejected_context=rejected_context,
    )

    try:
        response = client.chat.completions.create(
            model="default",
            messages=[
                {"role": "system", "content": load_system_prompt("theme")},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=50,
        )
        return response.choices[0].message.content or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Level 2 — retry loop with rating
# ---------------------------------------------------------------------------

def generate_creative_title(
    words: list[str],
    definitions: list[str],
    client,
    rate_client=None,
    multi_model: bool = False,
    current_model=None,
) -> str:
    """Generate a creative title with quality evaluation loop."""
    if not words:
        return _fallback_title()

    if rate_client is None:
        rate_client = client

    best_title: str | None = None
    best_score = 0
    rejected: list[tuple[str, str]] = []

    for round_idx in range(1, MAX_TITLE_ROUNDS + 1):
        rejected_context = ""
        if rejected:
            rejected_lines = "\n".join(
                f"- \"{t}\" (motiv: {f})" for t, f in rejected
            )
            rejected_context = (
                f"\n\nTitluri respinse anterior (nu le repeta, fii mai creativ):\n"
                f"{rejected_lines}"
            )

        raw_title = _generate_single_title(
            definitions, client, rejected_context, words=words,
        )

        sanitized = _sanitize_title(raw_title, input_words=words)
        if sanitized in [t for t, _ in rejected] or sanitized in FALLBACK_TITLES:
            continue

        current_model = _try_switch_model(current_model, multi_model)

        score, feedback = rate_title_creativity(sanitized, words, rate_client)
        print(f"  Title round {round_idx}: \"{sanitized}\" -> creativity={score}/10 ({feedback})")

        if score > best_score or (score == best_score and best_title and len(sanitized.split()) < len(best_title.split())):
            best_score = score
            best_title = sanitized

        if score >= TITLE_MIN_CREATIVITY:
            _try_switch_model(current_model, multi_model)
            return sanitized

        rejected.append((sanitized, feedback))
        current_model = _try_switch_model(current_model, multi_model)

    return best_title if best_title is not None else _fallback_title()


# ---------------------------------------------------------------------------
# Level 3 — puzzle API
# ---------------------------------------------------------------------------

def generate_title_for_final_puzzle(
    puzzle,
    client=None,
    rate_client=None,
    multi_model: bool = False,
    current_model=None,
) -> str:
    all_words = _collect_words(puzzle)
    definitions = _collect_definitions(puzzle)

    if client is None:
        client = create_client()

    return generate_creative_title(
        all_words,
        definitions,
        client=client,
        rate_client=rate_client or client,
        multi_model=multi_model,
        current_model=current_model,
    )


def run(input_file: str, output_file: str, **kwargs) -> None:
    """Generate a theme/title for the puzzle using LM Studio."""
    print(f"Reading puzzle from {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        puzzle = parse_markdown(f.read())

    words = _collect_words(puzzle)
    if not words:
        print("Error: no words found in puzzle")
        sys.exit(1)

    print(f"Found {len(words)} words: {', '.join(words[:10])}...")

    definitions = _collect_definitions(puzzle)
    client = create_client()

    print("Generating title with LM Studio...")
    theme = generate_creative_title(words, definitions, client=client, rate_client=client)

    print(f"Theme: {theme}")
    puzzle.title = theme

    md = write_with_definitions(puzzle)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"Saved themed puzzle to {output_file}")
