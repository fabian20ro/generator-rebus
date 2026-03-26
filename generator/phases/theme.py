"""Phase 4: Find a theme for the filled grid using LM Studio."""

from __future__ import annotations

import json
import random
import sys
from dataclasses import dataclass
from collections.abc import Iterable

from ..core.ai_clues import create_client
from ..core.diacritics import normalize
from ..core.lm_runtime import LmRuntime
from ..core.markdown_io import parse_markdown, write_with_definitions
from ..core.model_manager import ModelConfig, PRIMARY_MODEL, SECONDARY_MODEL
from ..core.text_rules import contains_normalized_forbidden_word
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

TITLE_MIN_CREATIVITY = 8
MAX_TITLE_ROUNDS = 7
NO_TITLE_LABEL = "Fara titlu"

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


def normalize_title_key(title: str) -> str:
    cleaned = " ".join(title.strip().strip('"').strip("'").split())
    cleaned = cleaned.rstrip(".,;:!?…")
    return normalize(cleaned)


@dataclass(frozen=True)
class TitleCandidateReview:
    title: str
    valid: bool
    feedback: str = ""


@dataclass(frozen=True)
class TitleGenerationResult:
    title: str
    score: int
    feedback: str
    used_fallback: bool = False


def _clean_title(title: str) -> str:
    cleaned = " ".join(title.strip().strip('"').strip("'").split())
    cleaned = cleaned.rstrip(".,;:!?…")
    return cleaned


def _is_all_caps_title(title: str) -> bool:
    letters = [ch for ch in title if ch.isalpha()]
    return bool(letters) and all(ch.upper() == ch for ch in letters)


def _review_title_candidate(title: str, input_words: list[str] | None = None) -> TitleCandidateReview:
    cleaned = _clean_title(title)
    if not cleaned:
        return TitleCandidateReview(cleaned, False, "titlu gol")
    # Reject comma-separated word lists (2+ commas)
    if cleaned.count(",") >= 2:
        return TitleCandidateReview(cleaned, False, "lista de cuvinte")

    blocked = {"rebus", "romanesc", "românesc", "puzzle", "titlu"}
    title_tokens = set(cleaned.lower().split())
    if title_tokens & blocked:
        return TitleCandidateReview(cleaned, False, "termeni generici interzisi")

    parts = cleaned.split()
    if len(parts) >= 6:
        return TitleCandidateReview(cleaned, False, "prea multe cuvinte")

    if _is_all_caps_title(cleaned):
        return TitleCandidateReview(cleaned, False, "all caps")

    english_hits = sum(1 for token in cleaned.lower().split() if token in TITLE_ENGLISH_MARKERS)
    if english_hits >= 2:
        return TitleCandidateReview(cleaned, False, "prea multe marcaje englezesti")

    if len(cleaned) > 100:
        return TitleCandidateReview(cleaned, False, "peste 100 de caractere")

    if input_words and contains_normalized_forbidden_word(
        cleaned,
        input_words,
        min_length=3,
    ):
        return TitleCandidateReview(cleaned, False, "contine cuvant-solutie")

    return TitleCandidateReview(cleaned, True)


def _sanitize_title(title: str, input_words: list[str] | None = None) -> str:
    reviewed = _review_title_candidate(title, input_words=input_words)
    if reviewed.valid:
        return reviewed.title
    return _fallback_title()

def rate_title_creativity(
    title: str,
    words: list[str],
    client,
    *,
    model_config: ModelConfig,
) -> tuple[int, str]:
    """Rate title creativity. Returns (score, feedback)."""
    prompt = load_user_template("title_rate").format(
        title=title,
        words=", ".join(words[:10]),
    )
    try:
        response = client.chat.completions.create(
            model=model_config.model_id,
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
    *,
    model_config: ModelConfig,
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
            model=model_config.model_id,
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

def generate_creative_title_result(
    words: list[str],
    definitions: list[str],
    client,
    rate_client=None,
    runtime: LmRuntime | None = None,
    multi_model: bool = False,
    forbidden_title_keys: Iterable[str] | None = None,
) -> TitleGenerationResult:
    """Generate a creative title with quality evaluation loop."""
    if not words:
        return TitleGenerationResult(NO_TITLE_LABEL, 0, "fara cuvinte", used_fallback=True)

    if rate_client is None:
        rate_client = client
    if runtime is None:
        runtime = LmRuntime(multi_model=multi_model)

    best_result: TitleGenerationResult | None = None
    rejected: list[tuple[str, str]] = []
    forbidden_keys = {key for key in (forbidden_title_keys or []) if key}

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

        generator_model = runtime.activate_primary()
        raw_title = _generate_single_title(
            definitions,
            client,
            model_config=generator_model,
            rejected_context=rejected_context,
            words=words,
        )

        reviewed = _review_title_candidate(raw_title, input_words=words)
        display_title = reviewed.title or _clean_title(raw_title) or "(gol)"
        if not reviewed.valid:
            print(f'  Title round {round_idx}: "{display_title}" -> creativity=0/10 ({reviewed.feedback})')
            rejected.append((display_title, reviewed.feedback))
            continue

        title_key = normalize_title_key(reviewed.title)
        rejected_keys = {normalize_title_key(title) for title, _ in rejected}
        if reviewed.title in FALLBACK_TITLES:
            rejected.append((reviewed.title, "fallback generic"))
            continue
        if title_key in rejected_keys:
            rejected.append((reviewed.title, "titlu deja respins"))
            continue
        if title_key and title_key in forbidden_keys:
            print(f'  Title round {round_idx}: "{reviewed.title}" -> creativity=0/10 (titlu deja folosit)')
            rejected.append((reviewed.title, "titlu deja folosit"))
            continue

        rating_model = runtime.activate_secondary() if multi_model else generator_model
        score, feedback = rate_title_creativity(
            reviewed.title,
            words,
            rate_client,
            model_config=rating_model,
        )
        print(f"  Title round {round_idx}: \"{reviewed.title}\" -> creativity={score}/10 ({feedback})")

        result = TitleGenerationResult(reviewed.title, score, feedback)

        if (
            best_result is None
            or score > best_result.score
            or (
                score == best_result.score
                and len(reviewed.title.split()) < len(best_result.title.split())
            )
        ):
            best_result = result

        if score >= TITLE_MIN_CREATIVITY:
            return result

        rejected.append((reviewed.title, feedback))

    if best_result is not None and best_result.score > 0:
        return best_result
    return TitleGenerationResult(NO_TITLE_LABEL, 0, "niciun titlu valid", used_fallback=True)


def generate_creative_title(
    words: list[str],
    definitions: list[str],
    client,
    rate_client=None,
    runtime: LmRuntime | None = None,
    multi_model: bool = False,
    forbidden_title_keys: Iterable[str] | None = None,
) -> str:
    return generate_creative_title_result(
        words,
        definitions,
        client,
        rate_client=rate_client,
        runtime=runtime,
        multi_model=multi_model,
        forbidden_title_keys=forbidden_title_keys,
    ).title


# ---------------------------------------------------------------------------
# Level 3 — puzzle API
# ---------------------------------------------------------------------------

def generate_title_for_final_puzzle(
    puzzle,
    client=None,
    rate_client=None,
    runtime: LmRuntime | None = None,
    multi_model: bool = False,
) -> str:
    return generate_title_for_final_puzzle_result(
        puzzle,
        client=client,
        rate_client=rate_client,
        runtime=runtime,
        multi_model=multi_model,
    ).title


def generate_title_for_final_puzzle_result(
    puzzle,
    client=None,
    rate_client=None,
    runtime: LmRuntime | None = None,
    multi_model: bool = False,
) -> TitleGenerationResult:
    all_words = _collect_words(puzzle)
    definitions = _collect_definitions(puzzle)

    if client is None:
        client = create_client()

    return generate_creative_title_result(
        all_words,
        definitions,
        client=client,
        rate_client=rate_client or client,
        runtime=runtime,
        multi_model=multi_model,
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
    runtime = LmRuntime(multi_model=False)
    theme = generate_creative_title(
        words,
        definitions,
        client=client,
        rate_client=client,
        runtime=runtime,
    )

    print(f"Theme: {theme}")
    puzzle.title = theme

    md = write_with_definitions(puzzle)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"Saved themed puzzle to {output_file}")
