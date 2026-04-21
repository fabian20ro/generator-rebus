"""LM Studio helpers for clue generation, verification, rewrite, and rating."""

from __future__ import annotations

import json
import math
import random
import re
import sys
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

from openai import OpenAI

from ..config import LMSTUDIO_BASE_URL, VERIFY_CANDIDATE_COUNT
from rebus_generator.prompts.loader import load_system_prompt, load_user_template
from rebus_generator.workflows.canonicals.domain_service import content_tokens, lexical_similarity

from rebus_generator.domain.clue_family import clue_uses_same_family, forbidden_definition_stems
from rebus_generator.domain.diacritics import normalize
from .llm_text import clean_llm_text_response
from .models import (
    PRIMARY_MODEL,
    SECONDARY_MODEL,
    chat_max_tokens,
    chat_reasoning_options,
)
from rebus_generator.domain.quality import ENGLISH_HOMOGRAPH_HINTS
from rebus_generator.platform.io.runtime_logging import audit, llm_debug_enabled, log

from rebus_generator.domain.guards.definition_guards import (
    DefinitionRejectionDetails,
    contains_english_markers,
    extract_verify_candidates as _extract_verify_candidates,
    validate_definition_text as _validate_definition,
    validate_definition_text_with_details as _validate_definition_details,
)
from rebus_generator.domain.guards.rating_guards import (
    clamp_score as _clamp_score,
    guard_definition_centric_rating as _guard_definition_centric_rating,
    guard_english_meaning_rating as _guard_english_meaning_rating,
    guard_same_family_rating as _guard_same_family_rating,
)


def _log_definition_rejection(
    *,
    word: str,
    model_id: str,
    purpose: str,
    attempt_index: int | None,
    details: DefinitionRejectionDetails,
    definition: str,
    rewrite: bool,
) -> None:
    compact = []
    if details.matched_token:
        compact.append(f"match={details.matched_token}")
    if details.matched_stem:
        compact.append(f"stem={details.matched_stem}")
    if details.leak_kind:
        compact.append(f"kind={details.leak_kind}")
    prefix = "rewrite rejected" if rewrite else "rejected"
    suffix = (" " + " ".join(compact)) if compact else ""
    log(
        f"    [{prefix} {word}: {details.reason}{suffix}; definition={definition[:120]}]",
        level="WARN",
    )
    audit(
        "definition_rejection",
        component="ai_clues",
        payload={
            "word": word,
            "model_id": model_id,
            "purpose": purpose,
            "attempt_index": attempt_index,
            "reason": details.reason,
            "definition_preview": definition[:200],
            "matched_token": details.matched_token,
            "matched_stem": details.matched_stem,
            "leak_kind": details.leak_kind,
        },
    )
from .prompt_builders import (
    _build_generate_prompt,
    _append_existing_canonical_definitions,
    _build_rewrite_prompt,
    _build_verify_prompt,
    _build_rate_prompt,
    _extract_usage_suffix_from_dex,
    has_prompt_residue,
    _normalize_definition_usage_suffix,
    _augment_definition_retry_prompt,
)
from .definition_referee import _compare_definition_variant_attempt

RATE_MIN_SEMANTIC = 7
RATE_MIN_REBUS = 5
VERIFY_MAX_TOKENS = 300
RATE_MAX_TOKENS = 300
from .llm_client import (
    RESPONSE_SOURCE_NO_THINKING_RETRY,
    RESPONSE_SOURCE_REASONING,
    create_client,
    _resolve_model_name,
    _clean_response,
    _chat_completion_create,
    _extract_json_object,
    extract_json_object_with_status,
    llm_attempt_temperatures,
    record_llm_parse_failure,
)



@dataclass(frozen=True)
class DefinitionRating:
    semantic_score: int
    guessability_score: int
    feedback: str
    creativity_score: int = 1
    rarity_only_override: bool = False
    response_source: str = RESPONSE_SOURCE_REASONING


@dataclass(frozen=True)
class VerifyResult:
    candidates: list[str]
    response_source: str = RESPONSE_SOURCE_REASONING

    @property
    def primary_guess(self) -> str:
        return self.candidates[0] if self.candidates else ""


@dataclass(frozen=True)
class RewriteAttemptResult:
    definition: str
    last_rejection: str = ""


@dataclass(frozen=True)
class MergeRewriteAttemptResult:
    definition: str
    valid: bool


@dataclass(frozen=True)
class MergeRewriteValidationResult:
    accepted: bool




@dataclass(frozen=True)
class DefinitionRatingPair:
    combined: DefinitionRating | None
    votes: dict[str, DefinitionRating]
    complete: bool


def round_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def consensus_score(first: int, second: int) -> int:
    base = (first + second) / 2
    penalty = abs(first - second) / 4
    return _clamp_score(round_half_up(base - penalty))


def combine_definition_feedback(first: str, second: str) -> str:
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


def combine_definition_ratings(
    first: DefinitionRating,
    second: DefinitionRating,
) -> DefinitionRating:
    return DefinitionRating(
        semantic_score=consensus_score(first.semantic_score, second.semantic_score),
        guessability_score=consensus_score(first.guessability_score, second.guessability_score),
        feedback=combine_definition_feedback(first.feedback, second.feedback),
        creativity_score=consensus_score(first.creativity_score, second.creativity_score),
        rarity_only_override=first.rarity_only_override and second.rarity_only_override,
        response_source=first.response_source if first.response_source == second.response_source else "pair",
    )


def _response_source(response) -> str:
    return str(getattr(response, "_response_source", RESPONSE_SOURCE_REASONING))


def compute_rebus_score(guessability: int, creativity: int, answer_length: int = 5) -> int:
    # 2-3 letters: 50/50 balance (high creativity reward)
    if answer_length <= 3:
        return round_half_up(0.5 * guessability + 0.5 * creativity)
    # 7+ letters: 90/10 balance (precision priority)
    if answer_length >= 7:
        return round_half_up(0.9 * guessability + 0.1 * creativity)
    # 4-6 letters: 75/25 balance (standard)
    return round_half_up(0.75 * guessability + 0.25 * creativity)


def generate_definition(
    client: OpenAI,
    word: str,
    original: str,
    theme: str,
    retries: int = 3,
    word_type: str = "",
    dex_definitions: str = "",
    existing_canonical_definitions: list[str] | None = None,
    temperature: float | None = None,
    model: str | None = None,
) -> str:
    """Generate a single clue definition."""
    display_word = original if original else word.lower()
    length = len(word)
    prompt = _build_generate_prompt(
        display_word, word, length, word_type=word_type, dex_definitions=dex_definitions
    )
    prompt = _append_existing_canonical_definitions(
        prompt, existing_canonical_definitions
    )
    resolved_model = _resolve_model_name(model)
    system_prompt = load_system_prompt("definition", model_id=resolved_model)
    required_suffix = _extract_usage_suffix_from_dex(dex_definitions)
    if llm_debug_enabled():
        log(f"  [LLM prompt] word={word} system={len(system_prompt)} chars")
        log(f"  [LLM user prompt]\n{prompt}")

    attempt_temperatures = llm_attempt_temperatures(
        temperature=temperature,
        default_temperature=0.4,
    )
    for attempt_index, attempt_temperature in enumerate(attempt_temperatures):
        try:
            max_tokens = chat_max_tokens(resolved_model)
            response = _chat_completion_create(
                client,
                model=resolved_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=attempt_temperature,
                max_tokens=max_tokens,
                purpose="definition_generate",
            )
            definition = _clean_response(response.choices[0].message.content)
            if definition == "[NECLAR]":
                return definition
            definition = _normalize_definition_usage_suffix(definition, required_suffix)
            if len(definition) > 200:
                definition = definition[:200].rsplit(" ", 1)[0]
            rejection_details = _validate_definition_details(word, definition)
            if rejection_details:
                _log_definition_rejection(
                    word=word,
                    model_id=resolved_model,
                    purpose="definition_generate",
                    attempt_index=attempt_index,
                    details=rejection_details,
                    definition=definition,
                    rewrite=False,
                )
                if _response_source(response) == RESPONSE_SOURCE_NO_THINKING_RETRY:
                    break
                prompt = _augment_definition_retry_prompt(prompt, rejection_details.reason)
                continue
            return definition
        except Exception:
            if attempt_index < len(attempt_temperatures) - 1:
                time.sleep(2)
            else:
                raise

    return "[Definiție negenerată]"


def rewrite_definition(
    client: OpenAI,
    word: str,
    original: str,
    theme: str,
    previous_definition: str,
    wrong_guess: str,
    retries: int = 2,
    rating_feedback: str = "",
    bad_example_definition: str = "",
    bad_example_reason: str = "",
    word_type: str = "",
    dex_definitions: str = "",
    existing_canonical_definitions: list[str] | None = None,
    failure_history: list[tuple[str, list[str]]] | None = None,
    wrong_guesses: list[str] | None = None,
    temperature: float | None = None,
    model: str | None = None,
    return_diagnostics: bool = False,
) -> str | RewriteAttemptResult:
    """Rewrite a failed or low-rated clue using feedback."""
    display_word = original if original else word.lower()
    feedback_parts = []
    if wrong_guesses:
        feedback_parts.append(f"Rezolvitorul a propus: {', '.join(wrong_guesses)}")
    elif wrong_guess:
        feedback_parts.append(f"Rezolvitorul a ghicit: {wrong_guess}")
    if rating_feedback:
        feedback_parts.append(f"Feedback calitate: {rating_feedback}")
    feedback_text = "\n".join(feedback_parts) if feedback_parts else "[niciun feedback]"
    bad_example_text = ""
    if bad_example_definition and bad_example_reason:
        bad_example_text = (
            "\nExemplu de definiție rea de evitat:\n"
            f"- Definiție respinsă: {bad_example_definition}\n"
            f"- Motiv: {bad_example_reason}\n"
            "- Nu produce ceva similar cu această definiție respinsă.\n"
        )
    prompt = _build_rewrite_prompt(
        display_word,
        word,
        previous_definition,
        feedback_text,
        bad_example_text,
        word_type=word_type,
        dex_definitions=dex_definitions,
        failure_history=failure_history,
    )
    prompt = _append_existing_canonical_definitions(
        prompt, existing_canonical_definitions
    )
    required_suffix = _extract_usage_suffix_from_dex(dex_definitions)
    resolved_model = _resolve_model_name(model)
    system_prompt = load_system_prompt("rewrite", model_id=resolved_model)
    if llm_debug_enabled():
        log(f"  [LLM rewrite prompt] word={word} system={len(system_prompt)} chars")
        log(f"  [LLM user prompt]\n{prompt}")

    last_rejection = ""
    attempt_temperatures = llm_attempt_temperatures(
        temperature=temperature,
        default_temperature=0.3,
    )
    for attempt_index, attempt_temperature in enumerate(attempt_temperatures):
        try:
            resolved_model = _resolve_model_name(model)
            max_tokens = chat_max_tokens(resolved_model)
            response = _chat_completion_create(
                client,
                model=resolved_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=attempt_temperature,
                max_tokens=max_tokens,
                purpose="definition_rewrite",
            )
            definition = _clean_response(response.choices[0].message.content)
            if definition == "[NECLAR]":
                result = RewriteAttemptResult(definition=definition)
                return result if return_diagnostics else result.definition
            definition = _normalize_definition_usage_suffix(definition, required_suffix)
            if len(definition) > 200:
                definition = definition[:200].rsplit(" ", 1)[0]
            rejection_details = _validate_definition_details(word, definition)
            if rejection_details:
                last_rejection = rejection_details.reason
                _log_definition_rejection(
                    word=word,
                    model_id=resolved_model,
                    purpose="definition_rewrite",
                    attempt_index=attempt_index,
                    details=rejection_details,
                    definition=definition,
                    rewrite=True,
                )
                if _response_source(response) == RESPONSE_SOURCE_NO_THINKING_RETRY:
                    break
                prompt = _augment_definition_retry_prompt(prompt, rejection_details.reason)
                continue
            result = RewriteAttemptResult(definition=definition)
            return result if return_diagnostics else result.definition
        except Exception:
            if attempt_index < len(attempt_temperatures) - 1:
                time.sleep(2)
            else:
                raise

    result = RewriteAttemptResult(
        definition=previous_definition,
        last_rejection=last_rejection,
    )
    return result if return_diagnostics else result.definition


def verify_definition_candidates(
    client: OpenAI,
    definition: str,
    answer_length: int,
    word_type: str = "",
    max_guesses: int = VERIFY_CANDIDATE_COUNT,
    model: str | None = None,
) -> VerifyResult:
    """Ask AI to suggest up to max_guesses candidate answers for a clue definition."""
    prompt = _build_verify_prompt(
        definition,
        answer_length,
        word_type=word_type,
        max_guesses=max_guesses,
    )

    last_candidates: list[str] = []
    last_source = RESPONSE_SOURCE_REASONING
    attempt_temperatures = llm_attempt_temperatures(
        temperature=0.1,
        default_temperature=0.1,
    )
    for attempt_temperature in attempt_temperatures:
        resolved_model = _resolve_model_name(model)
        max_tokens = min(chat_max_tokens(resolved_model), VERIFY_MAX_TOKENS)
        response = _chat_completion_create(
            client,
            model=resolved_model,
            messages=[
                {"role": "system", "content": load_system_prompt("verify", model_id=resolved_model)},
                {"role": "user", "content": prompt},
            ],

            temperature=attempt_temperature,
            max_tokens=max_tokens,
            purpose="definition_verify",
        )
        raw = response.choices[0].message.content or ""
        candidates = _extract_verify_candidates(
            raw, answer_length, max_guesses=max_guesses
        )
        last_candidates = candidates
        last_source = _response_source(response)
        if candidates:
            return VerifyResult(candidates, response_source=last_source)
        if last_source == RESPONSE_SOURCE_NO_THINKING_RETRY:
            break
        prompt += "\nAtenție: răspunsul anterior nu a fost în română. Răspunde exclusiv în română."

    return VerifyResult(last_candidates, response_source=last_source)


def rate_definition(
    client: OpenAI,
    word: str,
    original: str,
    definition: str,
    answer_length: int,
    word_type: str = "",
    dex_definitions: str = "",
    model: str | None = None,
) -> DefinitionRating | None:
    """Rate a definition's semantic quality and guessability.

    Returns None when the model's response cannot be parsed as valid JSON,
    signaling that the definition should be treated as unrated.
    """
    display_word = original if original else word.lower()
    prompt = _build_rate_prompt(
        display_word,
        word,
        definition,
        answer_length,
        word_type=word_type,
        dex_definitions=dex_definitions,
    )
    resolved_model = _resolve_model_name(model)
    system_prompt = (
        load_system_prompt("rate", model_id=resolved_model)
        .replace("{answer_length}", str(answer_length))
        + "\nRăspunzi strict cu un singur obiect JSON valid și nimic altceva."
    )
    retry_instruction = (
        "\nRăspunsul anterior nu a fost un JSON valid sau complet. "
        "Răspunde acum strict cu un singur obiect JSON valid, fără text suplimentar."
    )
    attempt_temperatures = llm_attempt_temperatures(
        temperature=0.1,
        default_temperature=0.1,
    )
    for attempt_temperature in attempt_temperatures:
        try:
            max_tokens = min(chat_max_tokens(resolved_model), RATE_MAX_TOKENS)
            response = _chat_completion_create(
                client,
                model=resolved_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=attempt_temperature,
                max_tokens=max_tokens,
                purpose="definition_rate",
            )
            raw = response.choices[0].message.content or ""
            data, parse_status = extract_json_object_with_status(raw)
            if data is None:
                record_llm_parse_failure(
                    model=resolved_model,
                    purpose="definition_rate",
                    word=word,
                    response_source=_response_source(response),
                    finish_reason=str(getattr(response.choices[0], "finish_reason", "") or ""),
                    payload_preview=raw,
                    status=parse_status,
                )
                if _response_source(response) == RESPONSE_SOURCE_NO_THINKING_RETRY:
                    return None
                prompt += retry_instruction
                continue
            feedback = str(data.get("feedback", "")).strip()
            if contains_english_markers(feedback):
                prompt += "\nAtenție: feedback-ul anterior nu a fost în română. Refă-l exclusiv în română."
                continue
            rating = DefinitionRating(
                semantic_score=_clamp_score(data.get("semantic_score")),
                guessability_score=_clamp_score(data.get("guessability_score")),
                feedback=feedback,
                creativity_score=_clamp_score(data.get("creativity_score")),
                response_source=_response_source(response),
            )
            rating = _guard_same_family_rating(word, definition, rating)
            rating = _guard_english_meaning_rating(word, definition, rating)
            return _guard_definition_centric_rating(rating)
        except Exception as exc:
            record_llm_parse_failure(
                model=resolved_model,
                purpose="definition_rate",
                word=word,
                response_source="exception",
                finish_reason="exception",
                payload_preview=str(exc),
                status="exception",
            )

    return None






def rewrite_merged_canonical_definition(
    client: OpenAI,
    *,
    word: str,
    definition_a: str,
    definition_b: str,
    model: str | None = None,
) -> MergeRewriteAttemptResult:
    resolved_model = _resolve_model_name(model)
    max_tokens = chat_max_tokens(resolved_model)
    system_prompt = (
        "Ești editor de definiții scurte pentru rebus românesc. "
        "Răspunzi cu o singură definiție românească, simplă, completă, fără markdown, fără etichete, fără explicații."
    )
    prompt = (
        f"Cuvânt: {word}\n"
        f"Definiția A: {definition_a}\n"
        f"Definiția B: {definition_b}\n"
        "Scrie o singură definiție finală care păstrează sensul comun al ambelor definiții.\n"
        "Reguli:\n"
        "- exclusiv română\n"
        "- fără ghilimele\n"
        "- fără liste, fără explicații, fără prefixe\n"
        "- fără aceeași familie lexicală ca răspunsul\n"
        "- mai concisă decât un paragraf\n"
        "- o singură propoziție sau sintagmă completă"
    )
    response = _chat_completion_create(
        client,
        model=resolved_model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        max_tokens=max_tokens,
        purpose="canonical_merge_rewrite",
    )
    definition = _clean_response(response.choices[0].message.content)
    rejection = validate_rewritten_canonical_definition_locally(
        word=word,
        definition_a=definition_a,
        definition_b=definition_b,
        candidate_definition=definition,
    )
    return MergeRewriteAttemptResult(
        definition=definition,
        valid=rejection is None,
    )


def validate_rewritten_canonical_definition_locally(
    *,
    word: str,
    definition_a: str,
    definition_b: str,
    candidate_definition: str,
) -> str | None:
    candidate = _clean_response(candidate_definition)
    if not candidate:
        return "empty"
    if has_prompt_residue(candidate):
        return "prompt_residue"
    rejection = _validate_definition(word, candidate)
    if rejection:
        return rejection
    if len(candidate) > 160:
        return "too_long"
    candidate_tokens = content_tokens(candidate)
    if len(candidate_tokens) < 2:
        return "too_short"
    overlap_a = len(set(candidate_tokens) & set(content_tokens(definition_a)))
    overlap_b = len(set(candidate_tokens) & set(content_tokens(definition_b)))
    similarity_a = lexical_similarity(normalize(candidate), normalize(definition_a))
    similarity_b = lexical_similarity(normalize(candidate), normalize(definition_b))
    if overlap_a <= 0 and similarity_a < 0.45:
        return "weak_overlap_a"
    if overlap_b <= 0 and similarity_b < 0.45:
        return "weak_overlap_b"
    separators = candidate.count(";") + candidate.count(":")
    if separators > max(definition_a.count(";") + definition_a.count(":"), definition_b.count(";") + definition_b.count(":")):
        return "broader_than_sources"
    return None


def validate_merged_canonical_definition(
    client: OpenAI,
    *,
    word: str,
    answer_length: int,
    definition_a: str,
    definition_b: str,
    candidate_definition: str,
    model: str | None = None,
) -> MergeRewriteValidationResult:
    local_rejection = validate_rewritten_canonical_definition_locally(
        word=word,
        definition_a=definition_a,
        definition_b=definition_b,
        candidate_definition=candidate_definition,
    )
    if local_rejection:
        return MergeRewriteValidationResult(
            accepted=False,
        )
    first = _compare_definition_variant_attempt(
        client,
        word,
        answer_length,
        candidate_definition,
        definition_a,
        model=model,
    )
    if first.vote is None or not first.vote.same_meaning:
        return MergeRewriteValidationResult(
            accepted=False,
        )
    second = _compare_definition_variant_attempt(
        client,
        word,
        answer_length,
        candidate_definition,
        definition_b,
        model=model,
    )
    if second.vote is None or not second.vote.same_meaning:
        return MergeRewriteValidationResult(
            accepted=False,
        )
    return MergeRewriteValidationResult(accepted=True)
