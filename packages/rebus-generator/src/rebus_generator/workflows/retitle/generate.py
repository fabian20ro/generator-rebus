from __future__ import annotations

import sys

from rebus_generator.platform.io.markdown_io import parse_markdown, write_with_definitions
from rebus_generator.platform.io.runtime_logging import log
from rebus_generator.platform.llm.llm_client import (
    RESPONSE_SOURCE_NO_THINKING_RETRY,
    RESPONSE_SOURCE_REASONING,
    _chat_completion_create,
    create_client,
)
from rebus_generator.platform.llm.llm_dispatch import WorkItem, WorkVote, run_single_model_workload
from rebus_generator.platform.llm.lm_runtime import LmRuntime
from rebus_generator.platform.llm.models import ModelConfig, chat_max_tokens, get_active_models
from rebus_generator.prompts.loader import load_system_prompt, load_user_template

from .rate import rate_title_creativity_pair, rate_title_creativity_batch
from .sanitize import (
    FALLBACK_TITLES,
    MAX_TITLE_ROUNDS,
    NO_TITLE_LABEL,
    TITLE_GENERATE_MAX_TOKENS,
    TITLE_MIN_CREATIVITY,
    TitleGenerateAttempt,
    TitleGenerationResult,
    _build_rejected_context,
    _clean_title,
    _review_title_candidate,
    normalize_title_key,
)


def _collect_words(puzzle) -> list[str]:
    words = set()
    for clue in puzzle.horizontal_clues:
        for word in clue.word_normalized.split(" - "):
            word = word.strip()
            if word:
                words.add(word)
    for clue in puzzle.vertical_clues:
        for word in clue.word_normalized.split(" - "):
            word = word.strip()
            if word:
                words.add(word)
    return sorted(words)


def _collect_definitions(puzzle) -> list[str]:
    return [
        clue.definition.strip()
        for clue in puzzle.horizontal_clues + puzzle.vertical_clues
        if clue.definition and not clue.definition.startswith("[")
    ]


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
    if definitions:
        content_section = "Definițiile din rebus sunt:\n" + "\n".join(f"- {definition}" for definition in definitions[:15]) + "\n\nCe temă leagă aceste definiții?"
    elif words:
        content_section = "Lista de cuvinte este:\n" + ", ".join(words[:15])
    else:
        return TitleGenerateAttempt("", RESPONSE_SOURCE_REASONING)

    try:
        response = _chat_completion_create(
            client,
            model=model_config.model_id,
            messages=[
                {"role": "system", "content": load_system_prompt("theme")},
                {"role": "user", "content": load_user_template("title_generate").format(content_section=content_section, rejected_context=rejected_context)},
            ],
            temperature=temperature,
            max_tokens=min(chat_max_tokens(model_config), TITLE_GENERATE_MAX_TOKENS),
            purpose="title_generate",
        )
        return TitleGenerateAttempt(
            response.choices[0].message.content or "",
            str(getattr(response, "_response_source", RESPONSE_SOURCE_REASONING)),
        )
    except Exception:
        return TitleGenerateAttempt("", RESPONSE_SOURCE_REASONING)


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
        return WorkVote(
            model_id=model.model_id,
            value=_generate_single_title(
                item.payload["definitions"],
                client,
                model_config=model,
                rejected_context=str(item.payload["empty_retry_instruction"]),
                words=item.payload["words"],
            ),
            source=RESPONSE_SOURCE_REASONING,
        )

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
    if first_attempt.title.strip() or first_attempt.response_source == RESPONSE_SOURCE_NO_THINKING_RETRY:
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


def generate_creative_title_result(
    words: list[str],
    definitions: list[str],
    client,
    rate_client=None,
    runtime: LmRuntime | None = None,
    multi_model: bool = False,
    forbidden_title_keys=None,
) -> TitleGenerationResult:
    if not words:
        return TitleGenerationResult(NO_TITLE_LABEL, 0, "fara cuvinte", used_fallback=True, score_complete=False)
    if rate_client is None:
        rate_client = client
    if runtime is None:
        runtime = LmRuntime(multi_model=multi_model)

    best_result: TitleGenerationResult | None = None
    rejected: list[tuple[str, str]] = []
    rejected_by_model = {model.model_id: [] for model in get_active_models(multi_model=multi_model)}
    forbidden_keys = {key for key in (forbidden_title_keys or []) if key}
    for round_idx in range(1, MAX_TITLE_ROUNDS + 1):
        round_candidates: list[tuple[str, ModelConfig, TitleCandidateReview]] = []
        
        # Phase 1: Batch Generation
        active_models = list(get_active_models(multi_model=multi_model))
        items = [
            WorkItem[dict[str, object], str](
                item_id=f"gen_{model.model_id}",
                task_kind="title_generate",
                payload={
                    "definitions": list(definitions),
                    "words": list(words),
                    "rejected_context": _build_rejected_context(rejected_by_model[model.model_id]),
                    "empty_retry_instruction": "Răspunde obligatoriu cu un singur titlu concret de 2-5 cuvinte, exclusiv în limba română.",
                },
                pending_models={model.model_id},
            )
            for model in active_models
        ]

        def _generate_runner(item: WorkItem[dict[str, object], str], model: ModelConfig) -> WorkVote[str]:
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
            return WorkVote(
                model_id=model.model_id,
                value=_generate_single_title(
                    item.payload["definitions"],
                    client,
                    model_config=model,
                    rejected_context=str(item.payload["empty_retry_instruction"]),
                    words=item.payload["words"],
                ),
                source=RESPONSE_SOURCE_REASONING,
            )

        from rebus_generator.platform.llm.llm_dispatch import WorkStep, run_llm_workload
        run_llm_workload(
            runtime=runtime,
            models=active_models,
            items=items,
            steps=[
                WorkStep(model_id=model.model_id, purpose="title_generate", runner=_generate_runner)
                for model in active_models
            ],
            task_label="title_generate",
        )

        # Phase 2: Process candidates
        for item, generator_model in zip(items, active_models):
            vote = item.votes.get(generator_model.model_id)
            raw_title = str(vote.value or "") if vote is not None else ""
            model_rejected = rejected_by_model[generator_model.model_id]
            
            if not raw_title.strip():
                log(f'  Title round {round_idx} [{generator_model.display_name}]: "(gol)" -> creativity=0/10 (titlu gol)')
                continue
                
            reviewed = _review_title_candidate(raw_title, input_words=words)
            display_title = reviewed.title or _clean_title(raw_title) or "(gol)"
            
            if not reviewed.valid:
                log(f'  Title round {round_idx} [{generator_model.display_name}]: "{display_title}" -> creativity=0/10 ({reviewed.feedback})')
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
                log(f'  Title round {round_idx} [{generator_model.display_name}]: "{reviewed.title}" -> creativity=0/10 (titlu deja folosit)')
                rejected.append((reviewed.title, "titlu deja folosit"))
                model_rejected.append((reviewed.title, "titlu deja folosit"))
                continue
                
            round_candidates.append((reviewed.title, generator_model, reviewed))

        if not round_candidates:
            continue

        # Phase 3: Batch Rating
        batch_input = [(f"r{round_idx}_{i}", title, words) for i, (title, _, _) in enumerate(round_candidates)]
        ratings = rate_title_creativity_batch(batch_input, rate_client, multi_model=multi_model, runtime=runtime)

        for i, (title, generator_model, reviewed) in enumerate(round_candidates):
            rating = ratings.get(f"r{round_idx}_{i}")
            if not rating or not rating.complete:
                log(f'  Title round {round_idx} [{generator_model.display_name} -> pair rated]: "{title}" -> evaluation failed')
                rejected.append((title, "evaluare incompletă"))
                rejected_by_model[generator_model.model_id].append((title, "evaluare incompletă"))
                continue

            log(f'  Title round {round_idx} [{generator_model.display_name} -> pair rated]: "{title}" -> creativity={rating.score}/10 ({rating.feedback})')
            result = TitleGenerationResult(reviewed.title, rating.score, rating.feedback, score_complete=True)
            
            if (
                best_result is None
                or result.score > best_result.score
                or (result.score == best_result.score and len(reviewed.title.split()) < len(best_result.title.split()))
            ):
                best_result = result
                
            if result.score >= TITLE_MIN_CREATIVITY:
                return result
                
            rejected.append((reviewed.title, rating.feedback))
            rejected_by_model[generator_model.model_id].append((reviewed.title, rating.feedback))

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
    forbidden_title_keys=None,
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
    if client is None:
        client = create_client()
    return generate_creative_title_result(
        _collect_words(puzzle),
        _collect_definitions(puzzle),
        client=client,
        rate_client=rate_client or client,
        runtime=runtime,
        multi_model=multi_model,
    )


def run(input_file: str, output_file: str, **kwargs) -> None:
    log(f"Reading puzzle from {input_file}...")
    puzzle = parse_markdown(open(input_file, "r", encoding="utf-8").read())
    words = _collect_words(puzzle)
    if not words:
        log("Error: no words found in puzzle")
        sys.exit(1)
    log(f"Found {len(words)} words: {', '.join(words[:10])}...")
    client = create_client()
    theme = generate_creative_title(
        words,
        _collect_definitions(puzzle),
        client=client,
        rate_client=client,
        runtime=LmRuntime(multi_model=False),
    )
    log(f"Theme: {theme}")
    puzzle.title = theme
    open(output_file, "w", encoding="utf-8").write(write_with_definitions(puzzle))
    log(f"Saved themed puzzle to {output_file}")
