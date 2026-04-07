"""Phase 4: Find a theme for the filled grid using LM Studio."""

from __future__ import annotations

import json
import random
import re
import sys
from dataclasses import dataclass
from collections.abc import Iterable

from ..core.llm_client import (
    RESPONSE_SOURCE_NO_THINKING_RETRY,
    RESPONSE_SOURCE_REASONING,
    _chat_completion_create,
    create_client,
)
from ..core.ai_clues import consensus_score
from ..core.diacritics import normalize
from ..core.llm_dispatch import (
    WorkConclusion,
    WorkItem,
    WorkStep,
    WorkVote,
    run_llm_workload,
    run_single_model_workload,
)
from ..core.llm_text import clean_llm_text_response
from ..core.lm_runtime import LmRuntime
from ..core.markdown_io import parse_markdown, write_with_definitions
from ..core.model_manager import (
    ModelConfig,
    chat_max_tokens,
    get_active_models,
)
from ..core.runtime_logging import log
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
MAX_REJECTED_HINTS = 5
MAX_REPEATED_REASON_HINTS = 2
TITLE_GENERATE_MAX_TOKENS = 400
TITLE_RATE_MAX_TOKENS = 300

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

TITLE_NON_ROMANIAN_MARKERS = {
    "and",
    "but",
    "with",
    "without",
    "the",
    "in",
    "of",
    "from",
    "into",
    "world",
    "life",
    "silent",
    "beyond",
    "other",
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
    score_complete: bool = True


@dataclass(frozen=True)
class TitleRatingResult:
    score: int
    feedback: str
    complete: bool
    votes: dict[str, tuple[int, str]]


@dataclass(frozen=True)
class TitleGenerateAttempt:
    title: str
    response_source: str = RESPONSE_SOURCE_REASONING


@dataclass(frozen=True)
class _TitleRatingPayload:
    title: str
    words: list[str]


def _contains_mixed_script(title: str) -> bool:
    has_latin = any(("A" <= ch.upper() <= "Z") or ch in "ĂÂÎȘŞȚŢăâîșşțţ" for ch in title)
    has_cyrillic = any("\u0400" <= ch <= "\u04ff" for ch in title)
    return has_latin and has_cyrillic


def _contains_non_romanian_tokens(title: str) -> bool:
    tokens = re.findall(r"[A-Za-zĂÂÎȘŞȚŢăâîșşțţ]+", title.lower())
    return any(token in TITLE_NON_ROMANIAN_MARKERS for token in tokens)


def _clean_title(title: str) -> str:
    cleaned = " ".join(clean_llm_text_response(title).split())
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

    if _contains_mixed_script(cleaned) or _contains_non_romanian_tokens(cleaned):
        return TitleCandidateReview(cleaned, False, "limba mixta")

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


def _generator_retry_instruction(reason: str) -> str:
    if reason == "prea multe cuvinte":
        return "Rescrie în maximum 5 cuvinte."
    if reason == "limba mixta":
        return "Rescrie exclusiv în limba română, fără niciun cuvânt străin sau alfabet nelatin."
    if reason == "contine cuvant-solutie":
        return "Rescrie fără să folosești cuvinte din rebus."
    if reason == "termeni generici interzisi":
        return "Rescrie fără cuvintele Rebus, Românesc, Puzzle sau Titlu."
    if reason == "titlu gol":
        return "Răspunde obligatoriu cu un singur titlu concret, nu gol."
    return "Rescrie cu un titlu mai scurt și mai precis."


def _build_rejected_context(rejected: list[tuple[str, str]]) -> str:
    if not rejected:
        return ""

    relevant = rejected[-MAX_REJECTED_HINTS:]
    lines = []
    repeated_reasons: dict[str, int] = {}
    for title, reason in relevant:
        repeated_reasons[reason] = repeated_reasons.get(reason, 0) + 1
        lines.append(f'- "{title}" ({reason})')
    hints = []
    for reason, count in repeated_reasons.items():
        if count >= MAX_REPEATED_REASON_HINTS:
            hints.append(_generator_retry_instruction(reason))
    hint_text = "\n".join(f"- {hint}" for hint in hints[:2])
    suffix = f"\nCorecții obligatorii:\n{hint_text}" if hint_text else ""
    return (
        "\n\nNU repeta aceste forme respinse:\n"
        + "\n".join(lines)
        + suffix
    )

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
    for attempt in range(2):
        try:
            max_tokens = min(chat_max_tokens(model_config), TITLE_RATE_MAX_TOKENS)
            response = _chat_completion_create(
                client,
                model=model_config.model_id,
                messages=[
                    {"role": "system", "content": load_system_prompt("title_rate")},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=max_tokens,
                purpose="title_rate",
            )
            raw = response.choices[0].message.content or ""
            fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
            bare_match = re.search(r"\{.*\}", raw, re.DOTALL)
            match = fence_match or bare_match
            if match:
                json_str = match.group(1) if fence_match and match is fence_match else match.group()
                try:
                    data = json.loads(json_str)
                except json.JSONDecodeError:
                    if str(getattr(response, "_response_source", RESPONSE_SOURCE_REASONING)) == RESPONSE_SOURCE_NO_THINKING_RETRY:
                        return 0, "parse error"
                    prompt += (
                        "\nRăspunsul anterior nu a fost JSON valid. "
                        "Răspunde acum strict cu un singur obiect JSON valid, fără text suplimentar."
                    )
                    continue
                try:
                    score = int(data.get("creativity_score", 0))
                except (TypeError, ValueError):
                    score = 0
                return max(0, min(10, score)), str(data.get("feedback", "")).strip()
            if str(getattr(response, "_response_source", RESPONSE_SOURCE_REASONING)) == RESPONSE_SOURCE_NO_THINKING_RETRY:
                return 0, "parse error"
            prompt += (
                "\nRăspunsul anterior nu a fost JSON valid. "
                "Răspunde acum strict cu un singur obiect JSON valid, fără text suplimentar."
            )
        except Exception:
            if attempt == 1:
                return 0, "api error"
    return 0, "parse error"


def _pair_rating_runtime(runtime: LmRuntime | None) -> LmRuntime:
    if runtime is not None and getattr(runtime, "multi_model", True):
        return runtime
    return LmRuntime(multi_model=True)


def _combine_title_feedback(first: str, second: str) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for raw in (first, second):
        text = str(raw or "").strip()
        if not text:
            continue
        key = normalize(text)
        if key in seen:
            continue
        seen.add(key)
        parts.append(text)
    return " / ".join(parts)


def _title_rating_runner(client):
    def _run(item: WorkItem[_TitleRatingPayload, tuple[int, str]], model: ModelConfig) -> WorkVote[tuple[int, str]]:
        score, feedback = rate_title_creativity(
            item.payload.title,
            item.payload.words,
            client,
            model_config=model,
        )
        if score <= 0 and feedback in {"api error", "parse error"}:
            return WorkVote(
                model_id=model.model_id,
                value=None,
                source=feedback,
                terminal=True,
                terminal_reason=feedback,
            )
        return WorkVote(
            model_id=model.model_id,
            value=(score, feedback),
            source="ok",
        )

    return _run


def _title_rating_conclusion(item: WorkItem[_TitleRatingPayload, tuple[int, str]]) -> WorkConclusion:
    if any(vote.terminal for vote in item.votes.values()):
        return WorkConclusion(failed=True, terminal_reason=next(
            (vote.terminal_reason for vote in item.votes.values() if vote.terminal_reason),
            "evaluare incompletă",
        ))
    if len(item.votes) >= 2:
        return WorkConclusion(complete=True)
    return WorkConclusion()


def rate_title_creativity_batch(
    titles: list[tuple[str, str, list[str]]],
    client,
    *,
    runtime: LmRuntime | None = None,
) -> dict[str, TitleRatingResult]:
    pair_runtime = _pair_rating_runtime(runtime)
    active_models = list(get_active_models(multi_model=True))
    items = [
        WorkItem[_TitleRatingPayload, tuple[int, str]](
            item_id=item_id,
            task_kind="title_rate",
            payload=_TitleRatingPayload(title=title, words=list(words)),
            pending_models={model.model_id for model in active_models},
        )
        for item_id, title, words in titles
    ]
    runner = _title_rating_runner(client)
    run_llm_workload(
        runtime=pair_runtime,
        models=active_models,
        items=items,
        steps=[
            WorkStep(model_id=model.model_id, purpose="title_rate", runner=runner, can_conclude=_title_rating_conclusion)
            for model in active_models
        ],
        task_label="title_rate",
    )
    results: dict[str, TitleRatingResult] = {}
    ordered_ids = [model.model_id for model in active_models]
    for item in items:
        votes = {
            model_id: item.votes[model_id].value
            for model_id in ordered_ids
            if model_id in item.votes and item.votes[model_id].value is not None
        }
        if len(votes) != 2:
            reason = item.terminal_reason or "evaluare incompletă"
            results[item.item_id] = TitleRatingResult(0, reason, False, {
                model_id: value for model_id, value in votes.items() if value is not None
            })
            continue
        first_score, first_feedback = votes[ordered_ids[0]]
        second_score, second_feedback = votes[ordered_ids[1]]
        results[item.item_id] = TitleRatingResult(
            score=consensus_score(first_score, second_score),
            feedback=_combine_title_feedback(first_feedback, second_feedback),
            complete=True,
            votes={model_id: votes[model_id] for model_id in ordered_ids},
        )
    return results


def rate_title_creativity_pair(
    title: str,
    words: list[str],
    client,
    *,
    runtime: LmRuntime | None = None,
) -> TitleRatingResult:
    return rate_title_creativity_batch(
        [("single", title, words)],
        client,
        runtime=runtime,
    )["single"]


# ---------------------------------------------------------------------------
# Level 1 — single LLM call
# ---------------------------------------------------------------------------

def _generate_single_title(
    definitions: list[str],
    client,
    *,
    model_config: ModelConfig,
    rejected_context: str = "",
    temperature: float = 0.3,
    words: list[str] | None = None,
) -> str:
    return _generate_single_title_attempt(
        definitions,
        client,
        model_config=model_config,
        rejected_context=rejected_context,
        temperature=temperature,
        words=words,
    ).title


def _generate_single_title_attempt(
    definitions: list[str],
    client,
    *,
    model_config: ModelConfig,
    rejected_context: str = "",
    temperature: float = 0.3,
    words: list[str] | None = None,
) -> TitleGenerateAttempt:
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
        return TitleGenerateAttempt("")

    prompt = load_user_template("title_generate").format(
        content_section=content_section,
        rejected_context=rejected_context,
    )

    try:
        max_tokens = min(chat_max_tokens(model_config), TITLE_GENERATE_MAX_TOKENS)
        response = _chat_completion_create(
            client,
            model=model_config.model_id,
            messages=[
                {"role": "system", "content": load_system_prompt("theme")},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            purpose="title_generate",
        )
        return TitleGenerateAttempt(
            response.choices[0].message.content or "",
            response_source=str(getattr(response, "_response_source", RESPONSE_SOURCE_REASONING)),
        )
    except Exception:
        return TitleGenerateAttempt("")

def _generate_candidate_for_model(
    definitions: list[str],
    words: list[str],
    client,
    *,
    runtime: LmRuntime,
    generator_model: ModelConfig,
    rejected_context: str,
    empty_retry_instruction: str,
) -> str:
    items = [
        WorkItem[dict[str, object], str](
            item_id="single",
            task_kind="title_generate",
            payload={
                "definitions": list(definitions),
                "words": list(words),
                "rejected_context": rejected_context,
                "empty_retry_instruction": empty_retry_instruction,
            },
            pending_models={generator_model.model_id},
        )
    ]

    def _runner(item: WorkItem[dict[str, object], str], model: ModelConfig) -> WorkVote[str]:
        first_attempt = _generate_single_title_attempt(
            item.payload["definitions"],
            client,
            model_config=model,
            rejected_context=str(item.payload["rejected_context"]),
            words=item.payload["words"],
        )
        if first_attempt.title.strip():
            return WorkVote(model_id=model.model_id, value=first_attempt.title, source=first_attempt.response_source)
        if first_attempt.response_source == RESPONSE_SOURCE_NO_THINKING_RETRY:
            return WorkVote(
                model_id=model.model_id,
                value=first_attempt.title,
                source=first_attempt.response_source,
                terminal=True,
                terminal_reason="title_empty_after_retry",
            )
        title = _generate_single_title(
            item.payload["definitions"],
            client,
            model_config=model,
            rejected_context=str(item.payload["empty_retry_instruction"]),
            words=item.payload["words"],
        )
        return WorkVote(model_id=model.model_id, value=title, source=RESPONSE_SOURCE_REASONING)

    run_single_model_workload(
        runtime=runtime,
        model=generator_model,
        items=items,
        purpose="title_generate",
        runner=_runner,
        task_label="title_generate",
    )
    vote = items[0].votes.get(generator_model.model_id)
    return str(vote.value or "") if vote is not None else ""


def _generate_candidate_with_active_model(
    definitions: list[str],
    words: list[str],
    client,
    *,
    active_model: ModelConfig,
    rejected_context: str,
    empty_retry_instruction: str,
) -> str:
    first_attempt = _generate_single_title_attempt(
        definitions,
        client,
        model_config=active_model,
        rejected_context=rejected_context,
        words=words,
    )
    if first_attempt.title.strip():
        return first_attempt.title
    if first_attempt.response_source == RESPONSE_SOURCE_NO_THINKING_RETRY:
        return first_attempt.title
    return _generate_single_title(
        definitions,
        client,
        model_config=active_model,
        rejected_context=empty_retry_instruction,
        words=words,
    )


def _phase_label(generator_model: ModelConfig, rating_model: ModelConfig | None = None) -> str:
    if rating_model is None or rating_model.model_id == generator_model.model_id:
        return generator_model.display_name
    return f"{generator_model.display_name} -> rated by {rating_model.display_name}"


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
        return TitleGenerationResult(NO_TITLE_LABEL, 0, "fara cuvinte", used_fallback=True, score_complete=False)

    if rate_client is None:
        rate_client = client
    if runtime is None:
        runtime = LmRuntime(multi_model=multi_model)

    best_result: TitleGenerationResult | None = None
    rejected: list[tuple[str, str]] = []
    rejected_by_model: dict[str, list[tuple[str, str]]] = {
        model.model_id: [] for model in get_active_models(multi_model=True)
    }
    forbidden_keys = {key for key in (forbidden_title_keys or []) if key}
    generator_order = list(get_active_models(multi_model=multi_model))

    for round_idx in range(1, MAX_TITLE_ROUNDS + 1):
        for generator_model in generator_order:
            model_rejected = rejected_by_model.setdefault(generator_model.model_id, [])
            rejected_context = _build_rejected_context(model_rejected)
            raw_title = _generate_candidate_for_model(
                definitions,
                words,
                client,
                runtime=runtime,
                generator_model=generator_model,
                rejected_context=rejected_context,
                empty_retry_instruction="Răspunde obligatoriu cu un singur titlu concret de 2-5 cuvinte, exclusiv în limba română.",
            )
            if not raw_title.strip():
                log(
                    f"  Title round {round_idx} [{generator_model.display_name}]: \"(gol)\" -> creativity=0/10 (titlu gol)"
                )
                continue

            reviewed = _review_title_candidate(raw_title, input_words=words)
            display_title = reviewed.title or _clean_title(raw_title) or "(gol)"
            if not reviewed.valid:
                log(
                    f'  Title round {round_idx} [{generator_model.display_name}]: "{display_title}" -> creativity=0/10 ({reviewed.feedback})'
                )
                rejected.append((display_title, reviewed.feedback))
                model_rejected.append((display_title, reviewed.feedback))
                continue

            title_key = normalize_title_key(reviewed.title)
            rejected_keys = {normalize_title_key(title) for title, _ in rejected}
            if reviewed.title in FALLBACK_TITLES:
                rejected.append((reviewed.title, "fallback generic"))
                model_rejected.append((reviewed.title, "fallback generic"))
                continue
            if title_key in rejected_keys:
                rejected.append((reviewed.title, "titlu deja respins"))
                model_rejected.append((reviewed.title, "titlu deja respins"))
                continue
            if title_key and title_key in forbidden_keys:
                log(
                    f'  Title round {round_idx} [{generator_model.display_name}]: "{reviewed.title}" -> creativity=0/10 (titlu deja folosit)'
                )
                rejected.append((reviewed.title, "titlu deja folosit"))
                model_rejected.append((reviewed.title, "titlu deja folosit"))
                continue

            rating = rate_title_creativity_pair(
                reviewed.title,
                words,
                rate_client,
                runtime=runtime,
            )
            score = rating.score
            feedback = rating.feedback
            log(
                f'  Title round {round_idx} [{generator_model.display_name} -> pair rated]: "{reviewed.title}" -> creativity={score}/10 ({feedback})'
            )

            if not rating.complete:
                rejected.append((reviewed.title, "evaluare incompletă"))
                model_rejected.append((reviewed.title, "evaluare incompletă"))
                continue

            result = TitleGenerationResult(reviewed.title, score, feedback, score_complete=True)

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
            model_rejected.append((reviewed.title, feedback))

    if best_result is not None and best_result.score > 0:
        return best_result
    return TitleGenerationResult(NO_TITLE_LABEL, 0, "niciun titlu valid", used_fallback=True, score_complete=False)


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
    log(f"Reading puzzle from {input_file}...")
    with open(input_file, "r", encoding="utf-8") as f:
        puzzle = parse_markdown(f.read())

    words = _collect_words(puzzle)
    if not words:
        log("Error: no words found in puzzle")
        sys.exit(1)

    log(f"Found {len(words)} words: {', '.join(words[:10])}...")

    definitions = _collect_definitions(puzzle)
    client = create_client()

    log("Generating title with LM Studio...")
    runtime = LmRuntime(multi_model=False)
    theme = generate_creative_title(
        words,
        definitions,
        client=client,
        rate_client=client,
        runtime=runtime,
    )

    log(f"Theme: {theme}")
    puzzle.title = theme

    md = write_with_definitions(puzzle)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md)

    log(f"Saved themed puzzle to {output_file}")
