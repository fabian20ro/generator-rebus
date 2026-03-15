"""Phase 4: Find a theme for the filled grid using LM Studio."""

from __future__ import annotations

import json
import sys

from ..core.ai_clues import create_client
from ..core.diacritics import normalize
from ..core.markdown_io import parse_markdown, write_with_definitions


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


THEME_SYSTEM_PROMPT = (
    "Ești editor de titluri pentru rebusuri românești. "
    "Primești cuvintele și definițiile finale ale unui rebus. "
    "Scrii un titlu scurt, spiritual, sigur creativ, poate chiar absurd, "
    "2-4 cuvinte, fără ghilimele, fără explicații, fără punct. "
    "Surprinde cititorul. Evită titlurile generice și banale. "
    "Nu folosești cuvintele Rebus, Românesc, Puzzle, Titlu."
)

TITLE_RATE_SYSTEM_PROMPT = (
    "Evaluezi creativitatea unui titlu de rebus românesc.\n"
    "Titlul trebuie să fie spiritual, creativ, poate chiar absurd.\n"
    "Un titlu generic de dicționar primește 2-3.\n"
    "Un titlu care surprinde sau provoacă un zâmbet primește 7-10.\n"
    "Răspunzi STRICT JSON: {\"creativity_score\": <1-10>, \"feedback\": \"<motiv scurt>\"}"
)

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
]


def _fallback_title(words: list[str]) -> str:
    if not words:
        return FALLBACK_TITLES[0]
    seed = sum(sum(ord(ch) for ch in word) for word in words)
    return FALLBACK_TITLES[seed % len(FALLBACK_TITLES)]


def _sanitize_title(title: str, words: list[str], input_words: list[str] | None = None) -> str:
    cleaned = " ".join(title.strip().strip('"').strip("'").split())
    if not cleaned:
        return _fallback_title(words)

    blocked = {"rebus", "romanesc", "românesc", "puzzle", "titlu"}
    lowered = cleaned.lower()
    if any(token in lowered for token in blocked):
        return _fallback_title(words)

    parts = cleaned.split()
    if len(parts) > 4:
        cleaned = " ".join(parts[:4])

    if input_words:
        title_upper = normalize(cleaned)
        for word in input_words:
            if normalize(word) in title_upper:
                return _fallback_title(words)

    return cleaned


def rate_title_creativity(title: str, words: list[str], client) -> tuple[int, str]:
    """Rate title creativity. Returns (score, feedback)."""
    prompt = (
        f"Titlul: \"{title}\"\n"
        f"Cuvintele rebusului: {', '.join(words[:10])}\n\n"
        "Evaluează creativitatea titlului."
    )
    try:
        response = client.chat.completions.create(
            model="default",
            messages=[
                {"role": "system", "content": TITLE_RATE_SYSTEM_PROMPT},
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
        return _fallback_title(words)

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

        prompt = (
            "Cuvintele rebusului sunt:\n"
            f"{', '.join(words)}\n\n"
            "Definițiile finale sunt:\n"
            + "\n".join(f"- {definition}" for definition in definitions[:20])
            + "\n\n"
            "Dă un titlu scurt pentru rebus."
            + rejected_context
        )

        if multi_model and current_model is not None:
            from ..core.model_manager import (
                PRIMARY_MODEL,
                SECONDARY_MODEL,
                switch_model,
            )

        try:
            response = client.chat.completions.create(
                model="default",
                messages=[
                    {"role": "system", "content": THEME_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=50,
            )
            raw_title = response.choices[0].message.content or ""
        except Exception:
            raw_title = ""

        sanitized = _sanitize_title(raw_title, words, input_words=words)
        if sanitized in [t for t, _ in rejected] or sanitized in FALLBACK_TITLES:
            continue

        if multi_model and current_model is not None:
            try:
                next_model = SECONDARY_MODEL if current_model == PRIMARY_MODEL else PRIMARY_MODEL
                switch_model(current_model, next_model)
                current_model = next_model
            except Exception:
                pass

        score, feedback = rate_title_creativity(sanitized, words, rate_client)
        print(f"  Title round {round_idx}: \"{sanitized}\" -> creativity={score}/10 ({feedback})")

        if score > best_score:
            best_score = score
            best_title = sanitized

        if score >= TITLE_MIN_CREATIVITY:
            if multi_model and current_model is not None:
                try:
                    next_model = SECONDARY_MODEL if current_model == PRIMARY_MODEL else PRIMARY_MODEL
                    switch_model(current_model, next_model)
                except Exception:
                    pass
            return sanitized

        rejected.append((sanitized, feedback))

        if multi_model and current_model is not None:
            try:
                next_model = SECONDARY_MODEL if current_model == PRIMARY_MODEL else PRIMARY_MODEL
                switch_model(current_model, next_model)
                current_model = next_model
            except Exception:
                pass

    return best_title if best_title is not None else _fallback_title(words)


def generate_title_from_words(words: list[str], client=None) -> str:
    if not words:
        return _fallback_title(words)

    prompt = (
        "Lista de cuvinte este:\n"
        f"{', '.join(words)}\n\n"
        "Dă un titlu scurt pentru rebus."
    )

    if client is None:
        client = create_client()

    try:
        response = client.chat.completions.create(
            model="default",
            messages=[
                {"role": "system", "content": THEME_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=50,
        )
        raw_title = response.choices[0].message.content or ""
    except Exception:
        raw_title = ""

    return _sanitize_title(raw_title, words)


def generate_title_from_words_and_definitions(
    words: list[str],
    definitions: list[str],
    client=None,
) -> str:
    if not words:
        return _fallback_title(words)

    prompt = (
        "Cuvintele rebusului sunt:\n"
        f"{', '.join(words)}\n\n"
        "Definițiile finale sunt:\n"
        + "\n".join(f"- {definition}" for definition in definitions[:20])
        + "\n\n"
        "Dă un titlu scurt pentru rebus."
    )

    if client is None:
        client = create_client()

    try:
        response = client.chat.completions.create(
            model="default",
            messages=[
                {"role": "system", "content": THEME_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=50,
        )
        raw_title = response.choices[0].message.content or ""
    except Exception:
        raw_title = ""

    return _sanitize_title(raw_title, words)


def generate_title_for_puzzle(puzzle, client=None) -> str:
    return generate_title_from_words(_collect_words(puzzle), client=client)


def generate_title_for_final_puzzle(
    puzzle,
    client=None,
    rate_client=None,
    multi_model: bool = False,
    current_model=None,
) -> str:
    all_words = _collect_words(puzzle)
    sorted_words = sorted(all_words, key=len, reverse=True)
    if len(sorted_words) <= 6:
        longest_words = sorted_words
    else:
        sixth_length = len(sorted_words[5])
        longest_words = [w for w in sorted_words if len(w) >= sixth_length]

    if client is None:
        client = create_client()

    return generate_creative_title(
        longest_words,
        _collect_definitions(puzzle),
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

    print("Generating title with LM Studio...")
    theme = generate_title_from_words(words)

    print(f"Theme: {theme}")
    puzzle.title = theme

    md = write_with_definitions(puzzle)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"Saved themed puzzle to {output_file}")
