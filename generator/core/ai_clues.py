"""LM Studio helpers for clue generation, verification, rewrite, and rating."""

from __future__ import annotations

import json
import random
import re
import sys
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

from openai import OpenAI

from ..config import LMSTUDIO_BASE_URL, VERIFY_CANDIDATE_COUNT
from ..prompts.loader import load_system_prompt, load_user_template
from .clue_canon import aggregate_referee_votes, content_tokens, lexical_similarity
from .clue_canon_types import (
    DefinitionComparisonAttempt,
    DefinitionComparisonVote,
    DefinitionRefereeDiagnostics,
    DefinitionRefereeInput,
    DefinitionRefereeResult,
)
from .clue_family import clue_uses_same_family, forbidden_definition_stems
from .diacritics import normalize
from .llm_text import clean_llm_text_response
from .model_manager import (
    PRIMARY_MODEL,
    SECONDARY_MODEL,
    chat_max_tokens,
    chat_reasoning_options,
)
from .quality import ENGLISH_HOMOGRAPH_HINTS
from .runtime_logging import llm_debug_enabled, log

WORD_TYPE_LABELS: dict[str, str] = {"V": "verb", "N": "substantiv", "A": "adjectiv"}
USAGE_SUFFIX_PRECEDENCE: list[tuple[str, tuple[str, ...]]] = [
    ("(arh.)", (r"\bARHAIC\b", r"\bARHAISM\b", r"\bARH\.\b", r"\bIN LIMBAJ ARHAIC\b")),
    ("(inv.)", (r"\bINVECHIT\b", r"\bIESIT DIN UZ\b", r"\bINV\.\b")),
    ("(reg.)", (r"\bREGIONAL\b", r"\bREGIONALISM\b", r"\bREG\.\b")),
    ("(tehn.)", (r"\bTEHNIC\b", r"\bTERMEN TEHNIC\b", r"\bTEHN\.\b")),
    ("(pop.)", (r"\bPOPULAR\b", r"\bPOP\.\b")),
    ("(fam.)", (r"\bFAMILIAR\b", r"\bFAM\.\b")),
    ("(arg.)", (r"\bARGOTIC\b", r"\bARGOU\b", r"\bARG\.\b")),
    ("(livr.)", (r"\bLIVRESC\b", r"\bLIVR\.\b")),
]
USAGE_SUFFIXES = {suffix for suffix, _markers in USAGE_SUFFIX_PRECEDENCE}
_TRAILING_USAGE_SUFFIX_RE = re.compile(
    r"(?:\s+\((?:arh|inv|reg|tehn|pop|fam|arg|livr)\.\))+\s*$",
    flags=re.IGNORECASE,
)

RATE_MIN_SEMANTIC = 7
RATE_MIN_REBUS = 5
RETRY_WITHOUT_THINKING_MAX_TOKENS = 200
RETRY_WITHOUT_THINKING_MARGIN = 10
ENGLISH_MARKERS = {
    "accurate",
    "accurately",
    "actually",
    "answer",
    "attached",
    "big",
    "by",
    "common",
    "correct",
    "definition",
    "english",
    "fantasy",
    "feedback",
    "file",
    "fluid",
    "for",
    "get",
    "guess",
    "guessability",
    "law",
    "length",
    "numerical",
    "precise",
    "precisely",
    "pressure",
    "powered",
    "response",
    "semantic",
    "system",
    "technical",
    "the",
    "very",
    "with",
    "without",
    "word",
}
RARITY_MARKERS = {
    "rar",
    "rară",
    "rare",
    "raritate",
    "neuzual",
    "neobișnuit",
    "neobisnuit",
    "puțin",
    "putin",
    "comun",
    "uzual",
    "obisnuit",
}
AMBIGUITY_MARKERS = {
    "alt",
    "altul",
    "ambig",
    "ambigua",
    "ambiguu",
    "sinonim",
    "vag",
    "vagă",
    "vaga",
    "firesc",
    "duce",
    "răspuns",
    "raspuns",
    "familie",
    "lexical",
}

DANGLING_ENDING_MARKERS = {
    "a",
    "ai",
    "al",
    "ale",
    "asupra",
    "ca",
    "că",
    "cu",
    "de",
    "din",
    "după",
    "dupa",
    "fără",
    "fara",
    "in",
    "în",
    "la",
    "o",
    "ori",
    "pe",
    "pentru",
    "prin",
    "sau",
    "si",
    "spre",
    "un",
    "unei",
    "unor",
    "unui",
    "și",
}


@dataclass(frozen=True)
class DefinitionRating:
    semantic_score: int
    guessability_score: int
    feedback: str
    creativity_score: int = 5
    rarity_only_override: bool = False


@dataclass(frozen=True)
class VerifyResult:
    candidates: list[str]

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
class AdaptiveRefereeBatchResult:
    results: dict[str, DefinitionRefereeResult]
    total_votes: int
    phase1_requests: int = 0
    phase2_requests: int = 0
    invalid_compare_json_primary: int = 0
    invalid_compare_json_secondary: int = 0
    step_metrics: list[dict[str, object]] = field(default_factory=list)


def compute_rebus_score(guessability: int, creativity: int) -> int:
    return round(0.75 * guessability + 0.25 * creativity)


def create_client() -> OpenAI:
    return OpenAI(
        base_url=f"{LMSTUDIO_BASE_URL}/v1",
        api_key="not-needed",
        timeout=120.0,
        max_retries=1,
    )


def _resolve_model_name(model: str | None) -> str:
    if not model or not str(model).strip():
        raise ValueError("Explicit LM Studio model_id required")
    return str(model).strip()


def _clean_response(text: str | None) -> str:
    return clean_llm_text_response(text)


class _DebugStreamChannel:
    def __init__(self, label: str):
        self.label = label
        self.started = False
        self.ends_with_newline = False

    def write(self, text: str | None) -> None:
        if not text:
            return
        if not self.started:
            sys.stdout.write(f"  [DEBUG] [{self.label}] ")
            self.started = True
        sys.stdout.write(text)
        sys.stdout.flush()
        self.ends_with_newline = text.endswith("\n")

    def finish(self) -> None:
        if not self.started:
            return
        if not self.ends_with_newline:
            sys.stdout.write("\n")
        sys.stdout.flush()
        self.started = False
        self.ends_with_newline = True


def _finish_debug_channels(*channels: _DebugStreamChannel) -> None:
    for channel in channels:
        channel.finish()


def _debug_message_text(message, attr: str) -> str:
    value = getattr(message, attr, None)
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _log_debug_request(
    *,
    model: str,
    purpose: str,
    temperature: float,
    max_tokens: int,
    reasoning_options: dict[str, str],
    stream: bool,
) -> None:
    parts = [
        "LLM request",
        f"purpose={purpose}",
        f"model={model}",
        f"temperature={temperature}",
        f"max_tokens={max_tokens}",
        f"stream={str(stream).lower()}",
    ]
    reasoning_effort = reasoning_options.get("reasoning_effort")
    if reasoning_effort:
        parts.append(f"reasoning_effort={reasoning_effort}")
    log("  [" + " ".join(parts) + "]")


def _log_debug_response(response) -> None:
    choice = response.choices[0] if getattr(response, "choices", None) else None
    message = getattr(choice, "message", None)
    if message is None:
        return
    reasoning_content = _debug_message_text(message, "reasoning_content")
    content = _debug_message_text(message, "content")
    if reasoning_content:
        log(f"  [LLM thinking] {reasoning_content}", level="DEBUG")
    if content:
        log(f"  [LLM output] {content}", level="DEBUG")


def _build_stream_completion_response(
    *,
    model: str,
    content_parts: list[str],
    reasoning_parts: list[str],
    finish_reason: str | None,
):
    message = SimpleNamespace(
        content="".join(content_parts),
        reasoning_content="".join(reasoning_parts),
        refusal=None,
        role="assistant",
        annotations=None,
        audio=None,
        function_call=None,
        tool_calls=[],
    )
    choice = SimpleNamespace(
        finish_reason=finish_reason,
        index=0,
        logprobs=None,
        message=message,
    )
    return SimpleNamespace(
        choices=[choice],
        usage=None,
        model=model,
    )


def _chat_completion_create_streaming(
    client: OpenAI,
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    reasoning_options: dict[str, str],
):
    reasoning_channel = _DebugStreamChannel("LLM thinking")
    output_channel = _DebugStreamChannel("LLM output")
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reason: str | None = None
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
        **reasoning_options,
    )
    # Support non-streaming mocks and fallbacks
    if not hasattr(response, "__iter__"):
        return response

    try:
        for chunk in response:
            choice = chunk.choices[0] if getattr(chunk, "choices", None) else None
            if choice is None:
                continue
            delta = getattr(choice, "delta", None)
            if delta is None:
                finish_reason = getattr(choice, "finish_reason", finish_reason)
                continue
            reasoning_piece = _debug_message_text(delta, "reasoning_content")
            if reasoning_piece:
                if output_channel.started:
                    output_channel.finish()
                reasoning_parts.append(reasoning_piece)
                reasoning_channel.write(reasoning_piece)
            content_piece = _debug_message_text(delta, "content")
            if content_piece:
                if reasoning_channel.started:
                    reasoning_channel.finish()
                content_parts.append(content_piece)
                output_channel.write(content_piece)
            finish_reason = getattr(choice, "finish_reason", finish_reason)
    finally:
        _finish_debug_channels(reasoning_channel, output_channel)
    return _build_stream_completion_response(
        model=model,
        content_parts=content_parts,
        reasoning_parts=reasoning_parts,
        finish_reason=finish_reason,
    )


def _response_choice(response):
    return response.choices[0] if getattr(response, "choices", None) else None


def _response_message(response):
    choice = _response_choice(response)
    return getattr(choice, "message", None)


def _response_finish_reason(response) -> str | None:
    choice = _response_choice(response)
    return getattr(choice, "finish_reason", None)


def _response_content_text(response) -> str:
    message = _response_message(response)
    return _debug_message_text(message, "content")


def _response_reasoning_text(response) -> str:
    message = _response_message(response)
    return _debug_message_text(message, "reasoning_content")


def _response_reasoning_tokens(response) -> int | None:
    usage = getattr(response, "usage", None)
    details = getattr(usage, "completion_tokens_details", None)
    reasoning_tokens = getattr(details, "reasoning_tokens", None)
    if reasoning_tokens is None:
        return None
    try:
        return int(reasoning_tokens)
    except (TypeError, ValueError):
        return None


def _retry_without_thinking_max_tokens() -> int:
    return RETRY_WITHOUT_THINKING_MAX_TOKENS


def _should_retry_without_thinking(
    response,
    *,
    reasoning_options: dict[str, str],
    max_tokens: int,
) -> bool:
    if reasoning_options.get("reasoning_effort") in {"", "none", None}:
        return False
    if _response_finish_reason(response) != "length":
        return False
    if _response_content_text(response).strip():
        return False
    reasoning_tokens = _response_reasoning_tokens(response)
    if reasoning_tokens is not None:
        return reasoning_tokens >= max(max_tokens - RETRY_WITHOUT_THINKING_MARGIN, 0)
    return bool(_response_reasoning_text(response).strip())


def _log_retry_without_thinking(
    *,
    model: str,
    purpose: str,
    max_tokens: int,
    response,
) -> None:
    reasoning_tokens = _response_reasoning_tokens(response)
    threshold = max(max_tokens - RETRY_WITHOUT_THINKING_MARGIN, 0)
    parts = [
        "retry without_thinking",
        f"purpose={purpose}",
        f"model={model}",
        f"finish_reason={_response_finish_reason(response)}",
        f"trigger_threshold={threshold}",
        f"retry_reasoning=none",
        f"retry_max_tokens={_retry_without_thinking_max_tokens()}",
    ]
    if reasoning_tokens is not None:
        parts.append(f"reasoning_tokens={reasoning_tokens}")
    log("  [" + " ".join(parts) + "]", level="WARN")


def _create_chat_completion_once(
    client: OpenAI,
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    purpose: str,
    reasoning_options: dict[str, str],
):
    debug = llm_debug_enabled()
    if debug:
        _log_debug_request(
            model=model,
            purpose=purpose,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_options=reasoning_options,
            stream=True,
        )
        try:
            response = _chat_completion_create_streaming(
                client,
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning_options=reasoning_options,
            )
        except Exception as exc:
            log(
                f"  [LLM debug stream fallback purpose={purpose} model={model} error={exc}]",
                level="WARN",
            )
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **reasoning_options,
            )
            _log_debug_response(response)
    else:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **reasoning_options,
        )
    _log_if_reasoning_budget_high(
        response,
        model=model,
        purpose=purpose,
        max_tokens=max_tokens,
    )
    _log_if_completion_truncated(
        response,
        model=model,
        purpose=purpose,
        max_tokens=max_tokens,
    )
    return response


def _chat_completion_create(
    client: OpenAI,
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    purpose: str = "default",
):
    reasoning_options = chat_reasoning_options(model, purpose=purpose)
    response = _create_chat_completion_once(
        client,
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        purpose=purpose,
        reasoning_options=reasoning_options,
    )
    if not _should_retry_without_thinking(
        response,
        reasoning_options=reasoning_options,
        max_tokens=max_tokens,
    ):
        return response
    _log_retry_without_thinking(
        model=model,
        purpose=purpose,
        max_tokens=max_tokens,
        response=response,
    )
    return _create_chat_completion_once(
        client,
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=_retry_without_thinking_max_tokens(),
        purpose=purpose,
        reasoning_options=chat_reasoning_options(
            model,
            purpose=purpose,
            reasoning_effort_override="none",
        ),
    )


def _log_if_reasoning_budget_high(
    response,
    *,
    model: str,
    purpose: str,
    max_tokens: int,
) -> None:
    usage = getattr(response, "usage", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    details = getattr(usage, "completion_tokens_details", None)
    reasoning_tokens = getattr(details, "reasoning_tokens", None)
    if not reasoning_tokens or max_tokens <= 0:
        return
    budget_ratio = reasoning_tokens / max_tokens
    completion_ratio = (
        reasoning_tokens / completion_tokens
        if completion_tokens
        else None
    )
    if budget_ratio < 0.75 and (
        completion_ratio is None or completion_ratio < 0.85
    ):
        return
    parts = [
        "warn reasoning_budget",
        f"purpose={purpose}",
        f"model={model}",
        f"max_tokens={max_tokens}",
        f"reasoning_tokens={reasoning_tokens}",
        f"budget_ratio={budget_ratio:.2f}",
    ]
    if completion_tokens is not None:
        parts.append(f"completion_tokens={completion_tokens}")
    if completion_ratio is not None:
        parts.append(f"completion_ratio={completion_ratio:.2f}")
    log("  [" + " ".join(parts) + "]", level="WARN")


def _log_if_completion_truncated(
    response,
    *,
    model: str,
    purpose: str,
    max_tokens: int,
) -> None:
    choice = response.choices[0] if getattr(response, "choices", None) else None
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason != "length":
        return
    usage = getattr(response, "usage", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    details = getattr(usage, "completion_tokens_details", None)
    reasoning_tokens = getattr(details, "reasoning_tokens", None)
    parts = [
        f"completion truncated: purpose={purpose}",
        f"model={model}",
        f"finish_reason={finish_reason}",
        f"max_tokens={max_tokens}",
    ]
    if completion_tokens is not None:
        parts.append(f"completion_tokens={completion_tokens}")
    if reasoning_tokens is not None:
        parts.append(f"reasoning_tokens={reasoning_tokens}")
    log("  [" + " ".join(parts) + "]", level="WARN")


def _latin_word_tokens(text: str | None) -> list[str]:
    if not text:
        return []
    normalized = normalize(text).lower()
    return re.findall(r"[a-z]+", normalized)


def find_english_marker(text: str | None) -> str | None:
    for token in _latin_word_tokens(text):
        if token in ENGLISH_MARKERS:
            return token
    return None


def contains_english_markers(text: str | None) -> bool:
    return find_english_marker(text) is not None


def _definition_mentions_answer(answer: str, definition: str) -> bool:
    if not definition:
        return False
    normalized_definition = normalize(definition).lower()
    pattern = rf"\b{re.escape(answer.lower())}\b"
    return re.search(pattern, normalized_definition) is not None


def _definition_is_invalid(answer: str, definition: str) -> bool:
    return _definition_mentions_answer(answer, definition) or clue_uses_same_family(
        answer, definition
    )


def _same_family_feedback() -> str:
    return "Definiția folosește aceeași familie lexicală ca răspunsul."


def _tokens(text: str) -> set[str]:
    return {
        token.lower() for token in re.findall(r"[A-Za-zĂÂÎȘȘȚăâîșț]+", normalize(text))
    }


def _last_word(text: str) -> str:
    tokens = re.findall(r"[A-Za-zĂÂÎȘȘȚăâîșț0-9]+", normalize(text))
    return tokens[-1].lower() if tokens else ""


def _feedback_is_rarity_only(feedback: str) -> bool:
    if not feedback:
        return False
    tokens = _tokens(feedback)
    return bool(tokens & RARITY_MARKERS) and not bool(tokens & AMBIGUITY_MARKERS)


def _strip_trailing_usage_suffixes(definition: str) -> str:
    return _TRAILING_USAGE_SUFFIX_RE.sub("", definition or "").strip()


def _extract_definition_usage_suffix(definition: str) -> str | None:
    matches = re.findall(
        r"\((?:arh|inv|reg|tehn|pop|fam|arg|livr)\.\)",
        definition or "",
        flags=re.IGNORECASE,
    )
    if not matches:
        return None
    return matches[-1].lower()


def _extract_usage_suffix_from_dex(dex_definitions: str) -> str | None:
    if not dex_definitions:
        return None
    normalized_text = normalize(dex_definitions)
    for suffix, patterns in USAGE_SUFFIX_PRECEDENCE:
        if any(re.search(pattern, normalized_text) for pattern in patterns):
            return suffix
    return None


def _normalize_definition_usage_suffix(
    definition: str, required_suffix: str | None
) -> str:
    base = _strip_trailing_usage_suffixes(definition)
    if not required_suffix or required_suffix not in USAGE_SUFFIXES:
        return base
    if not base:
        return required_suffix
    return f"{base} {required_suffix}"


def _build_usage_label_line(required_suffix: str | None, *, purpose: str) -> str:
    if not required_suffix:
        return ""
    if purpose == "generate":
        return (
            f"Marcaj DEX explicit: {required_suffix}\n"
            f"Dacă definești sensul marcat de DEX, încheie definiția exact cu {required_suffix}. "
            "Folosești maximum un singur sufix de acest tip.\n"
        )
    if purpose == "rewrite":
        return (
            f"Marcaj DEX explicit: {required_suffix}\n"
            f"Păstrează sau restaurează exact sufixul final {required_suffix} dacă rescrii sensul marcat de DEX. "
            "Folosești maximum un singur sufix de acest tip.\n"
        )
    if purpose == "verify":
        return f"Marcaj de uz explicit în definiție: {required_suffix}\n"
    if purpose == "rate":
        return f"Marcaj DEX permis: {required_suffix}\n"
    return ""


_ENGLISH_MEANING_PATTERNS: dict[str, list[str]] = {
    "AN": ["articol nehotărât", "articol nehotarat"],
    "OF": [
        "prepoziție de posesie",
        "prepozitie de posesie",
        "indică posesia",
        "indica posesia",
    ],
    "IN": [
        "prepoziție de loc",
        "prepozitie de loc",
        "indică poziția",
        "indica pozitia",
        "prepoziție care indică",
    ],
    "AT": [
        "prepoziție care indică locul",
        "prepozitie care indica locul",
        "prepoziție de loc",
    ],
    "HAT": ["pălărie", "palarie"],
    "NAT": ["network address", "traducere a adreselor", "adreselor ip"],
    "IDE": ["dezvoltare software", "editor și compilator", "mediu de dezvoltare"],
    "REF": ["referință", "referinta"],
}


def _definition_describes_english_meaning(word: str, definition: str) -> bool:
    if not definition:
        return False
    lower_def = definition.lower()
    if "engleză" in lower_def or "engleza" in lower_def or "english" in lower_def:
        return True
    patterns = _ENGLISH_MEANING_PATTERNS.get(word.upper(), [])
    return any(pattern in lower_def for pattern in patterns)


def _guard_english_meaning_rating(
    word: str,
    definition: str,
    rating: DefinitionRating,
) -> DefinitionRating:
    if not _definition_describes_english_meaning(word, definition):
        return rating
    return DefinitionRating(
        semantic_score=1,
        guessability_score=1,
        feedback="Definiția descrie sensul englezesc, nu cel românesc.",
        creativity_score=1,
    )


def _family_exclusion_note(word: str) -> str:
    """Build a prompt note listing forbidden word forms for family leakage prevention."""
    stems = forbidden_definition_stems(word)
    if not stems:
        return ""
    joined = ", ".join(stems)
    return (
        f"\nATENȚIE — Cuvinte complet interzise în definiție: {joined}.\n"
        "Orice cuvânt care conține aceste rădăcini este interzis.\n"
        "Folosește o perifrază creativă, fără nicio legătură lexicală cu răspunsul."
    )


def _build_generate_prompt(
    display_word: str,
    word: str,
    length: int,
    word_type: str = "",
    dex_definitions: str = "",
) -> str:
    required_suffix = _extract_usage_suffix_from_dex(dex_definitions)
    prompt = load_user_template("generate").format(
        display_word=display_word,
        word=word,
        length=length,
        usage_label_line=_build_usage_label_line(required_suffix, purpose="generate"),
    )
    prompt += (
        "\nDefiniția trebuie să fie o formulare completă, nu un singur cuvânt izolat."
    )
    label = WORD_TYPE_LABELS.get(word_type)
    if label:
        prompt = prompt.replace(
            f"Lungime: {length}", f"Lungime: {length}\nCategorie gramaticală: {label}"
        )
    hint = ENGLISH_HOMOGRAPH_HINTS.get(word.upper())
    if hint:
        prompt += (
            f"\nATENȚIE: Cuvântul {word} este în limba ROMÂNĂ. "
            f"Sensul corect: {hint}. "
            f"NU defini ca și cum ar fi un cuvânt englezesc."
        )
    if dex_definitions:
        prompt += (
            f"\nDefiniții DEX (referință):\n{dex_definitions}\n"
            "Folosește aceste sensuri ca bază, dar reformulează creativ pentru rebus."
        )
    prompt += _family_exclusion_note(word)
    return prompt


def _append_existing_canonical_definitions(
    prompt: str, existing_definitions: list[str] | None
) -> str:
    if not existing_definitions:
        return prompt
    lines = [f"- {definition}" for definition in existing_definitions if definition]
    if not lines:
        return prompt
    return (
        prompt
        + "\nDefiniții canonice deja folosite pentru același cuvânt:\n"
        + "\n".join(lines)
        + "\nEvită să reformulezi aceeași idee aproape identic. "
        "Dacă poți, alege un alt unghi semantic clar distinct."
    )


def _build_rewrite_prompt(
    display_word: str,
    word: str,
    previous_definition: str,
    feedback_text: str,
    bad_example_text: str,
    word_type: str = "",
    dex_definitions: str = "",
    failure_history: list[tuple[str, list[str]]] | None = None,
) -> str:
    required_suffix = _extract_usage_suffix_from_dex(dex_definitions)
    label = WORD_TYPE_LABELS.get(word_type)
    word_type_line = f"Categorie gramaticală: {label}\n" if label else ""
    history_text = ""
    if failure_history:
        recent = failure_history[-5:]
        lines = [
            f"{i}. '{defn}' → propus: {', '.join(guesses) if guesses else '[nimic]'}"
            for i, (defn, guesses) in enumerate(recent, 1)
        ]
        history_text = "\nÎncercări anterioare eșuate:\n" + "\n".join(lines) + "\n"
    prompt = load_user_template("rewrite").format(
        display_word=display_word,
        word=word,
        word_type_line=word_type_line,
        previous_definition=previous_definition,
        feedback_text=feedback_text,
        bad_example_text=bad_example_text,
        failure_history_text=history_text,
        usage_label_line=_build_usage_label_line(required_suffix, purpose="rewrite"),
    )
    prompt += "\nDefiniția nouă trebuie să fie completă și naturală, nu un singur cuvânt izolat."
    hint = ENGLISH_HOMOGRAPH_HINTS.get(word.upper())
    if hint:
        prompt += (
            f"\nATENȚIE: Cuvântul {word} este în limba ROMÂNĂ. "
            f"Sensul corect: {hint}. "
            f"NU defini ca și cum ar fi un cuvânt englezesc."
        )
    if dex_definitions:
        prompt += (
            f"\nDefiniții DEX (referință):\n{dex_definitions}\n"
            "Folosește aceste sensuri ca bază, dar reformulează creativ pentru rebus."
        )
    prompt += _family_exclusion_note(word)
    return prompt


def _word_type_line(word_type: str) -> str:
    label = WORD_TYPE_LABELS.get(word_type)
    return f"Categorie gramaticală: {label}\n" if label else ""


def _build_verify_prompt(
    definition: str,
    answer_length: int,
    word_type: str = "",
    max_guesses: int = VERIFY_CANDIDATE_COUNT,
) -> str:
    used_suffix = _extract_definition_usage_suffix(definition)
    return load_user_template("verify").format(
        word_type_line=_word_type_line(word_type),
        usage_label_line=_build_usage_label_line(used_suffix, purpose="verify"),
        definition=definition,
        answer_length=answer_length,
        max_guesses=max_guesses,
    )


def _build_rate_prompt(
    display_word: str,
    word: str,
    definition: str,
    answer_length: int,
    word_type: str = "",
    dex_definitions: str = "",
) -> str:
    allowed_suffix = _extract_usage_suffix_from_dex(dex_definitions)
    used_suffix = _extract_definition_usage_suffix(definition)
    suffix_status_line = ""
    if used_suffix and allowed_suffix == used_suffix:
        suffix_status_line = (
            f"Eticheta folosită în definiție: {used_suffix}\n"
            "Eticheta corespunde explicit unui sens DEX marcat.\n"
        )
    elif used_suffix and allowed_suffix != used_suffix:
        suffix_status_line = (
            f"Eticheta folosită în definiție: {used_suffix}\n"
            "Eticheta din definiție nu este susținută explicit de DEX pentru acest cuvânt.\n"
        )
    elif allowed_suffix:
        suffix_status_line = (
            f"Eticheta permisă de DEX: {allowed_suffix}\n"
            "Definiția putea folosi această etichetă pentru a disambigua sensul marcat.\n"
        )
    prompt = load_user_template("rate").format(
        display_word=display_word,
        word=word,
        answer_length=answer_length,
        word_type_line=_word_type_line(word_type),
        usage_label_line=_build_usage_label_line(allowed_suffix, purpose="rate"),
        suffix_status_line=suffix_status_line,
        definition=definition,
    )
    if dex_definitions:
        prompt += (
            f"\nDefiniții DEX (referință):\n{dex_definitions}\n"
            "Folosește-le pentru a evalua corectitudinea și originalitatea definiției."
        )
    return prompt


def _build_clue_tiebreak_prompt(
    word: str, answer_length: int, definition_a: str, definition_b: str
) -> str:
    return load_user_template("clue_tiebreak").format(
        word=word,
        answer_length=answer_length,
        definition_a=definition_a,
        definition_b=definition_b,
    )


def _build_clue_compare_prompt(
    word: str, answer_length: int, definition_a: str, definition_b: str
) -> str:
    return load_user_template("clue_compare").format(
        word=word,
        answer_length=answer_length,
        definition_a=definition_a,
        definition_b=definition_b,
    )


def _build_puzzle_tiebreak_prompt(summary_a: str, summary_b: str) -> str:
    return load_user_template("puzzle_tiebreak").format(
        summary_a=summary_a,
        summary_b=summary_b,
    )


def _guard_same_family_rating(
    word: str, definition: str, rating: DefinitionRating
) -> DefinitionRating:
    if not clue_uses_same_family(word, definition):
        return rating
    return DefinitionRating(
        semantic_score=1,
        guessability_score=1,
        feedback=_same_family_feedback(),
        creativity_score=1,
    )


def _guard_definition_centric_rating(rating: DefinitionRating) -> DefinitionRating:
    if rating.semantic_score < 8:
        return rating
    if not _feedback_is_rarity_only(rating.feedback):
        return rating
    return DefinitionRating(
        semantic_score=rating.semantic_score,
        guessability_score=rating.guessability_score,
        feedback=rating.feedback,
        creativity_score=rating.creativity_score,
        rarity_only_override=True,
    )


def _pick_tiebreak_winner(raw: str) -> str:
    cleaned = _clean_response(raw).upper()
    if cleaned.startswith("B"):
        return "B"
    return "A"


def _clamp_score(value: int | str | None, default: int = 5) -> int:
    try:
        score = int(value if value is not None else default)
    except (TypeError, ValueError):
        score = default
    return max(1, min(10, score))


def _validate_definition(word: str, definition: str) -> str | None:
    """Return rejection reason, or None if acceptable."""
    clean_definition = _strip_trailing_usage_suffixes(definition)
    if len(clean_definition) < 5:
        return f"too short ({len(clean_definition)} chars)"
    if len(re.findall(r"[A-Za-zĂÂÎȘȘȚăâîșț0-9]+", clean_definition)) < 2:
        return "single-word gloss"
    if _last_word(clean_definition) in DANGLING_ENDING_MARKERS:
        return "dangling ending"
    if _definition_is_invalid(word, clean_definition):
        return "contains answer or family word"
    english_marker = find_english_marker(clean_definition)
    if english_marker:
        return f"English markers detected (token={english_marker})"
    if _definition_describes_english_meaning(word, clean_definition):
        return "English meaning"
    return None


def _augment_definition_retry_prompt(prompt: str, rejection: str) -> str:
    return (
        prompt
        + f"\nRăspunsul anterior a fost respins: {rejection}."
        + "\nRăspunde cu o definiție completă, naturală, de minimum 2 cuvinte."
        + "\nNu te opri la un gloss minimal și nu lăsa ultimul cuvânt neterminat."
    )


def _clean_verify_chunk(text: str | None) -> str:
    chunk = (text or "").strip().strip('"').strip("'")
    chunk = re.sub(r"<\|[^|]*\|>", "", chunk).strip()
    chunk = re.sub(
        r"^\s*(?:[-*•]+|\d+[.)]\s*|(?:Răspunsuri|Raspunsuri|Răspuns|Raspuns|Cuvinte):\s*)",
        "",
        chunk,
        flags=re.IGNORECASE,
    ).strip()
    token_match = re.search(r"[A-Za-zĂÂÎȘȘȚăâîșț0-9]+", chunk)
    return token_match.group(0) if token_match else ""


def _extract_verify_candidates(
    raw: str, answer_length: int, max_guesses: int
) -> list[str]:
    pieces = re.split(r"[\n,;/|]+", raw or "")
    candidates: list[str] = []
    seen: set[str] = set()

    def _append(candidate: str) -> None:
        normalized = normalize(candidate)
        if not normalized or len(normalized) != answer_length:
            return
        if contains_english_markers(candidate) or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(candidate.upper())

    for piece in pieces:
        candidate = _clean_verify_chunk(piece)
        if candidate:
            _append(candidate)
        if len(candidates) >= max_guesses:
            return candidates[:max_guesses]

    if candidates:
        return candidates[:max_guesses]

    fallback_tokens = re.findall(r"[A-Za-zĂÂÎȘȘȚăâîșț0-9]+", raw or "")
    for token in fallback_tokens:
        _append(token)
        if len(candidates) >= max_guesses:
            break
    return candidates[:max_guesses]


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
    system_prompt = load_system_prompt("definition")
    required_suffix = _extract_usage_suffix_from_dex(dex_definitions)
    log(f"  [LLM prompt] word={word} system={len(system_prompt)} chars")
    log(f"  [LLM user prompt]\n{prompt}")

    for attempt in range(retries):
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
                temperature=temperature if temperature is not None else 0.2,
                max_tokens=max_tokens,
                purpose="definition_generate",
            )
            definition = _clean_response(response.choices[0].message.content)
            if definition == "[NECLAR]":
                return definition
            definition = _normalize_definition_usage_suffix(definition, required_suffix)
            if len(definition) > 200:
                definition = definition[:200].rsplit(" ", 1)[0]
            rejection = _validate_definition(word, definition)
            if rejection:
                log(
                    f"    [rejected {word}: {rejection}; definition={definition[:120]}]",
                    level="WARN",
                )
                prompt = _augment_definition_retry_prompt(prompt, rejection)
                continue
            return definition
        except Exception:
            if attempt < retries - 1:
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
    system_prompt = load_system_prompt("rewrite")
    required_suffix = _extract_usage_suffix_from_dex(dex_definitions)
    log(f"  [LLM rewrite prompt] word={word} system={len(system_prompt)} chars")
    log(f"  [LLM user prompt]\n{prompt}")

    last_rejection = ""
    for attempt in range(retries):
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
                temperature=temperature if temperature is not None else 0.3,
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
            rejection = _validate_definition(word, definition)
            if rejection:
                last_rejection = rejection
                log(
                    f"    [rewrite rejected {word}: {rejection}; definition={definition[:120]}]",
                    level="WARN",
                )
                prompt = _augment_definition_retry_prompt(prompt, rejection)
                continue
            result = RewriteAttemptResult(definition=definition)
            return result if return_diagnostics else result.definition
        except Exception:
            if attempt < retries - 1:
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
    for attempt in range(2):
        resolved_model = _resolve_model_name(model)
        max_tokens = chat_max_tokens(resolved_model)
        response = _chat_completion_create(
            client,
            model=resolved_model,
            messages=[
                {"role": "system", "content": load_system_prompt("verify")},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=max_tokens,
            purpose="definition_verify",
        )
        raw = response.choices[0].message.content or ""
        candidates = _extract_verify_candidates(
            raw, answer_length, max_guesses=max_guesses
        )
        last_candidates = candidates
        if candidates:
            return VerifyResult(candidates)
        prompt += "\nAtenție: răspunsul anterior nu a fost în română. Răspunde exclusiv în română."

    return VerifyResult(last_candidates)


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
    system_prompt = load_system_prompt("rate")

    for attempt in range(2):
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
                temperature=0.0,
                max_tokens=max_tokens,
                purpose="definition_rate",
            )
            raw = response.choices[0].message.content or ""
            fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
            bare_match = re.search(r"\{.*\}", raw, re.DOTALL)
            match = fence_match or bare_match
            if match:
                json_str = (
                    match.group(1)
                    if fence_match and match is fence_match
                    else match.group()
                )
                try:
                    data = json.loads(json_str)
                except json.JSONDecodeError:
                    prompt += (
                        "\nRăspunsul anterior nu a fost JSON valid. "
                        "Răspunde acum strict cu un singur obiect JSON valid, fără text suplimentar."
                    )
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
                )
                rating = _guard_same_family_rating(word, definition, rating)
                rating = _guard_english_meaning_rating(word, definition, rating)
                return _guard_definition_centric_rating(rating)
            prompt += (
                "\nRăspunsul anterior nu a fost JSON valid. "
                "Răspunde acum strict cu un singur obiect JSON valid, fără text suplimentar."
            )
        except Exception:
            pass

    return None


def _extract_json_object(raw: str) -> dict | None:
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw or "", re.DOTALL)
    bare_match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    match = fence_match or bare_match
    if not match:
        return None
    json_str = match.group(1) if fence_match and match is fence_match else match.group()
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        return None


_PROMPT_RESIDUE_MARKERS = (
    "definiția:",
    "definitia:",
    "propusă:",
    "propusa:",
    "```",
    "{\"",
)


def has_prompt_residue(text: str | None) -> bool:
    lower = str(text or "").strip().lower()
    if not lower:
        return False
    return any(marker in lower for marker in _PROMPT_RESIDUE_MARKERS)


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


def compare_definition_variants(
    client: OpenAI,
    word: str,
    answer_length: int,
    definition_a: str,
    definition_b: str,
    *,
    model: str | None = None,
) -> DefinitionComparisonVote:
    attempt = _compare_definition_variant_attempt(
        client,
        word,
        answer_length,
        definition_a,
        definition_b,
        model=model,
    )
    if attempt.vote is not None:
        return attempt.vote
    resolved_model = _resolve_model_name(model)
    return DefinitionComparisonVote(
        model_id=resolved_model,
        same_meaning=False,
        better="equal",
        reason="compare_failed",
    )


def compare_definition_variants_attempt(
    client: OpenAI,
    word: str,
    answer_length: int,
    definition_a: str,
    definition_b: str,
    *,
    model: str | None = None,
) -> DefinitionComparisonAttempt:
    return _compare_definition_variant_attempt(
        client,
        word,
        answer_length,
        definition_a,
        definition_b,
        model=model,
    )


def _compare_definition_variant_attempt(
    client: OpenAI,
    word: str,
    answer_length: int,
    definition_a: str,
    definition_b: str,
    *,
    model: str | None = None,
) -> DefinitionComparisonAttempt:
    prompt = _build_clue_compare_prompt(word, answer_length, definition_a, definition_b)
    retry_prompt = (
        "\nRăspunde strict cu un singur obiect JSON valid de forma "
        '{"same_meaning": true|false, "better": "A"|"B"|"equal"} '
        "fără text suplimentar."
    )
    resolved_model = _resolve_model_name(model)
    max_tokens = chat_max_tokens(resolved_model)
    for attempt in range(2):
        compare_started = time.monotonic()
        try:
            response = _chat_completion_create(
                client,
                model=resolved_model,
                messages=[
                    {"role": "system", "content": load_system_prompt("clue_compare")},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=max_tokens,
                purpose="clue_compare",
            )
            elapsed = time.monotonic() - compare_started
            if elapsed > 10.0:
                log(
                    "  [slow compare "
                    f"model={resolved_model} attempt={attempt + 1} seconds={elapsed:.2f}]"
                )
            data = _extract_json_object(response.choices[0].message.content or "")
            if not data:
                log(
                    "  [invalid compare json "
                    f"model={resolved_model} attempt={attempt + 1} seconds={elapsed:.2f}]"
                )
                if attempt == 1:
                    return DefinitionComparisonAttempt(
                        model_id=resolved_model,
                        model_role="",
                        valid_vote=False,
                        parse_status="invalid_json",
                        latency_seconds=elapsed,
                    )
                prompt += retry_prompt
                continue
            better = str(data.get("better", "equal")).strip().upper()
            if better not in {"A", "B", "EQUAL"}:
                better = "equal"
            else:
                better = better.lower() if better == "EQUAL" else better
            return DefinitionComparisonAttempt(
                model_id=resolved_model,
                model_role="",
                valid_vote=True,
                parse_status="ok",
                latency_seconds=elapsed,
                vote=DefinitionComparisonVote(
                    model_id=resolved_model,
                    same_meaning=bool(data.get("same_meaning")),
                    better=better,
                    reason=str(data.get("reason", "")).strip(),
                ),
            )
        except Exception as exc:
            elapsed = time.monotonic() - compare_started
            log(
                "  [compare exception "
                f"model={resolved_model} attempt={attempt + 1} seconds={elapsed:.2f} error={exc}]"
            )
            if attempt == 0:
                prompt += retry_prompt
                continue
            return DefinitionComparisonAttempt(
                model_id=resolved_model,
                model_role="",
                valid_vote=False,
                parse_status="exception",
                latency_seconds=elapsed,
                error_message=str(exc),
            )
    return DefinitionComparisonAttempt(
        model_id=resolved_model,
        model_role="",
        valid_vote=False,
        parse_status="invalid_json",
    )


def _remap_swapped_vote(vote: DefinitionComparisonVote) -> DefinitionComparisonVote:
    better = vote.better
    if better == "A":
        better = "B"
    elif better == "B":
        better = "A"
    return DefinitionComparisonVote(
        model_id=vote.model_id,
        same_meaning=vote.same_meaning,
        better=better,
        reason=vote.reason,
    )


def _with_diagnostics(
    result: DefinitionRefereeResult,
    diagnostics: DefinitionRefereeDiagnostics,
) -> DefinitionRefereeResult:
    return DefinitionRefereeResult(
        same_meaning_votes=result.same_meaning_votes,
        better_a_votes=result.better_a_votes,
        better_b_votes=result.better_b_votes,
        equal_votes=result.equal_votes,
        votes=result.votes,
        diagnostics=diagnostics,
    )


def _build_referee_diagnostics(
    request_id: str,
    attempts: list[DefinitionComparisonAttempt],
) -> DefinitionRefereeDiagnostics:
    primary_valid_votes = 0
    secondary_valid_votes = 0
    for attempt in attempts:
        if not attempt.valid_vote:
            continue
        if attempt.model_role == "primary":
            primary_valid_votes += 1
        elif attempt.model_role == "secondary":
            secondary_valid_votes += 1
    return DefinitionRefereeDiagnostics(
        request_id=request_id,
        attempts=list(attempts),
        primary_valid_votes=primary_valid_votes,
        secondary_valid_votes=secondary_valid_votes,
    )


def run_definition_referee_batch(
    client: OpenAI,
    runtime,
    requests: list[DefinitionRefereeInput],
) -> dict[str, DefinitionRefereeResult]:
    return run_definition_referee_adaptive_batch(client, runtime, requests).results


def run_definition_referee_adaptive_batch(
    client: OpenAI,
    runtime,
    requests: list[DefinitionRefereeInput],
) -> AdaptiveRefereeBatchResult:
    if not requests:
        return AdaptiveRefereeBatchResult(results={}, total_votes=0)

    vote_steps = (
        (PRIMARY_MODEL, False, "primary"),
        (SECONDARY_MODEL, True, "secondary"),
    )
    active_request_ids = [request.request_id for request in requests]
    request_by_id = {request.request_id: request for request in requests}
    votes_by_request_id: dict[str, list[DefinitionComparisonVote]] = {
        request.request_id: [] for request in requests
    }
    attempts_by_request_id: dict[str, list[DefinitionComparisonAttempt]] = {
        request.request_id: [] for request in requests
    }
    results: dict[str, DefinitionRefereeResult] = {}
    total_votes = 0
    phase1_requests = 0
    phase2_requests = 0
    invalid_compare_json_primary = 0
    invalid_compare_json_secondary = 0
    step_metrics: list[dict[str, object]] = []

    for step_index, (model_config, swap, model_role) in enumerate(vote_steps):
        if not active_request_ids:
            break
        requests_started = len(active_request_ids)
        if model_role == "primary":
            phase1_requests += requests_started
        else:
            phase2_requests += requests_started
        if runtime is not None:
            runtime.activate(model_config)
        completed_ids: list[str] = []
        for request_id in list(active_request_ids):
            request = request_by_id[request_id]
            left = request.definition_b if swap else request.definition_a
            right = request.definition_a if swap else request.definition_b
            attempt = _compare_definition_variant_attempt(
                client,
                request.word,
                request.answer_length,
                left,
                right,
                model=model_config.model_id,
            )
            vote = _remap_swapped_vote(attempt.vote) if (swap and attempt.vote is not None) else attempt.vote
            attempts_by_request_id[request_id].append(
                DefinitionComparisonAttempt(
                    model_id=attempt.model_id,
                    model_role=model_role,
                    valid_vote=attempt.valid_vote,
                    parse_status=attempt.parse_status,
                    latency_seconds=attempt.latency_seconds,
                    vote=vote,
                    error_message=attempt.error_message,
                )
            )
            if vote is not None:
                votes_by_request_id[request_id].append(vote)
            elif attempt.parse_status == "invalid_json":
                if model_role == "primary":
                    invalid_compare_json_primary += 1
                else:
                    invalid_compare_json_secondary += 1
            total_votes += 1
            diagnostics = _build_referee_diagnostics(request_id, attempts_by_request_id[request_id])
            if step_index == 0:
                continue
            results[request_id] = _with_diagnostics(
                aggregate_referee_votes(votes_by_request_id[request_id]),
                diagnostics,
            )
            completed_ids.append(request_id)
        if completed_ids:
            completed = set(completed_ids)
            active_request_ids = [
                request_id
                for request_id in active_request_ids
                if request_id not in completed
            ]
        step_metrics.append({
            "step_index": step_index,
            "model_id": model_config.model_id,
            "model_role": model_role,
            "requests_started": requests_started,
            "requests_completed_after_step": len(completed_ids),
            "requests_remaining_after_step": len(active_request_ids),
        })

    for request_id in active_request_ids:
        results[request_id] = _with_diagnostics(
            aggregate_referee_votes(votes_by_request_id[request_id]),
            _build_referee_diagnostics(request_id, attempts_by_request_id[request_id]),
        )

    return AdaptiveRefereeBatchResult(
        results=results,
        total_votes=total_votes,
        phase1_requests=phase1_requests,
        phase2_requests=phase2_requests,
        invalid_compare_json_primary=invalid_compare_json_primary,
        invalid_compare_json_secondary=invalid_compare_json_secondary,
        step_metrics=step_metrics,
    )


def run_definition_referee(
    client: OpenAI,
    runtime,
    word: str,
    answer_length: int,
    definition_a: str,
    definition_b: str,
) -> DefinitionRefereeResult:
    return run_definition_referee_batch(
        client,
        runtime,
        [
            DefinitionRefereeInput(
                request_id="single",
                word=word,
                answer_length=answer_length,
                definition_a=definition_a,
                definition_b=definition_b,
            )
        ],
    )["single"]


def choose_better_clue_variant(
    client: OpenAI,
    word: str,
    answer_length: int,
    definition_a: str,
    definition_b: str,
    model: str | None = None,
) -> str:
    prompt = _build_clue_tiebreak_prompt(
        word, answer_length, definition_a, definition_b
    )
    try:
        resolved_model = _resolve_model_name(model)
        max_tokens = chat_max_tokens(resolved_model)
        response = _chat_completion_create(
            client,
            model=resolved_model,
            messages=[
                {"role": "system", "content": load_system_prompt("clue_tiebreaker")},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=max_tokens,
            purpose="clue_tiebreaker",
        )
        return _pick_tiebreak_winner(response.choices[0].message.content or "")
    except Exception:
        return "A"


def choose_better_puzzle_variant(
    client: OpenAI,
    summary_a: str,
    summary_b: str,
    model: str | None = None,
) -> str:
    prompt = _build_puzzle_tiebreak_prompt(summary_a, summary_b)
    try:
        resolved_model = _resolve_model_name(model)
        max_tokens = chat_max_tokens(resolved_model)
        response = _chat_completion_create(
            client,
            model=resolved_model,
            messages=[
                {"role": "system", "content": load_system_prompt("puzzle_tiebreaker")},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=max_tokens,
            purpose="puzzle_tiebreaker",
        )
        return _pick_tiebreak_winner(response.choices[0].message.content or "")
    except Exception:
        return "A"
