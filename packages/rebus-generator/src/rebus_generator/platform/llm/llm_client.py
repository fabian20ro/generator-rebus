"""LLM Studio client helpers and streaming transport logic."""

from __future__ import annotations

import json
import random
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from types import SimpleNamespace

from openai import OpenAI

from rebus_generator.platform.io.runtime_logging import llm_debug_enabled, log

from ..config import LMSTUDIO_BASE_URL
from .llm_text import clean_llm_text_response
from .models import PRIMARY_MODEL, chat_reasoning_options

RETRY_WITHOUT_THINKING_MAX_TOKENS = 200
RETRY_WITHOUT_THINKING_MARGIN = 10
RESPONSE_SOURCE_REASONING = "reasoning"
RESPONSE_SOURCE_NO_THINKING_RETRY = "no_thinking_retry"
DEFAULT_TRUNCATION_THRESHOLD = 3
DEFAULT_SLOW_CALL_SECONDS = 20.0
_DEFAULT_REASONING_SENTINEL = object()
_SHORT_FORM_MAX_TOKENS: dict[tuple[str, str], int] = {
    (PRIMARY_MODEL.model_id, "definition_verify"): 256,
    (PRIMARY_MODEL.model_id, "definition_rate"): 240,
    (PRIMARY_MODEL.model_id, "title_generate"): 256,
    (PRIMARY_MODEL.model_id, "title_rate"): 224,
    (PRIMARY_MODEL.model_id, "clue_compare"): 320,
    (PRIMARY_MODEL.model_id, "clue_tiebreaker"): 256,
}
_JSON_SHORT_FORM_PURPOSES = {"title_rate", "clue_compare"}
_CHOICE_SHORT_FORM_PURPOSES = {"clue_tiebreaker"}


@dataclass
class _LlmPurposeStats:
    calls: int = 0
    truncations: int = 0
    retries: int = 0
    no_thinking_retries: int = 0
    slow_calls: int = 0
    completion_tokens_total: int = 0
    reasoning_tokens_total: int = 0
    latency_seconds_total: float = 0.0
    max_observed_latency_seconds: float = 0.0
    usage_samples: int = 0


_RUN_REASONING_OVERRIDES: dict[tuple[str, str], str | None] = {}
_RUN_TRUNCATION_THRESHOLD = DEFAULT_TRUNCATION_THRESHOLD
_RUN_SLOW_CALL_SECONDS = DEFAULT_SLOW_CALL_SECONDS
_ADAPTIVE_DOWNGRADES: set[tuple[str, str]] = set()
_ADAPTIVE_DOWNGRADE_LOGGED: set[tuple[str, str]] = set()
_LLM_STATS: dict[tuple[str, str], _LlmPurposeStats] = defaultdict(_LlmPurposeStats)


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


def _response_completion_tokens(response) -> int | None:
    usage = getattr(response, "usage", None)
    completion_tokens = getattr(usage, "completion_tokens", None)
    if completion_tokens is None:
        return None
    try:
        return int(completion_tokens)
    except (TypeError, ValueError):
        return None


def _retry_without_thinking_max_tokens() -> int:
    return RETRY_WITHOUT_THINKING_MAX_TOKENS


def reset_run_llm_state() -> None:
    _RUN_REASONING_OVERRIDES.clear()
    _ADAPTIVE_DOWNGRADES.clear()
    _ADAPTIVE_DOWNGRADE_LOGGED.clear()
    _LLM_STATS.clear()
    global _RUN_TRUNCATION_THRESHOLD, _RUN_SLOW_CALL_SECONDS
    _RUN_TRUNCATION_THRESHOLD = DEFAULT_TRUNCATION_THRESHOLD
    _RUN_SLOW_CALL_SECONDS = DEFAULT_SLOW_CALL_SECONDS


def configure_run_llm_policy(
    *,
    reasoning_overrides: dict[tuple[str, str], str | None] | None = None,
    truncation_threshold: int = DEFAULT_TRUNCATION_THRESHOLD,
    slow_call_seconds: float = DEFAULT_SLOW_CALL_SECONDS,
) -> None:
    _RUN_REASONING_OVERRIDES.clear()
    if reasoning_overrides:
        _RUN_REASONING_OVERRIDES.update(reasoning_overrides)
    global _RUN_TRUNCATION_THRESHOLD, _RUN_SLOW_CALL_SECONDS
    _RUN_TRUNCATION_THRESHOLD = max(1, int(truncation_threshold))
    _RUN_SLOW_CALL_SECONDS = max(1.0, float(slow_call_seconds))


def llm_run_retry_count() -> int:
    return sum(stats.retries for stats in _LLM_STATS.values())


def llm_run_stats_snapshot() -> dict[str, object]:
    per_model_purpose: dict[str, dict[str, float | int]] = {}
    per_purpose: dict[str, dict[str, float | int | list[str]]] = {}
    for (model, purpose), stats in sorted(_LLM_STATS.items()):
        per_model_purpose[f"{model}|{purpose}"] = _stats_to_dict(stats)
        bucket = per_purpose.setdefault(
            purpose,
            {
                "calls": 0,
                "truncations": 0,
                "retries": 0,
                "no_thinking_retries": 0,
                "slow_calls": 0,
                "completion_tokens_total": 0,
                "reasoning_tokens_total": 0,
                "latency_seconds_total": 0.0,
                "max_observed_latency_seconds": 0.0,
                "usage_samples": 0,
                "models": [],
            },
        )
        bucket["calls"] += stats.calls
        bucket["truncations"] += stats.truncations
        bucket["retries"] += stats.retries
        bucket["no_thinking_retries"] += stats.no_thinking_retries
        bucket["slow_calls"] += stats.slow_calls
        bucket["completion_tokens_total"] += stats.completion_tokens_total
        bucket["reasoning_tokens_total"] += stats.reasoning_tokens_total
        bucket["latency_seconds_total"] += stats.latency_seconds_total
        bucket["max_observed_latency_seconds"] = max(
            float(bucket["max_observed_latency_seconds"]),
            stats.max_observed_latency_seconds,
        )
        bucket["usage_samples"] += stats.usage_samples
        models = bucket["models"]
        if model not in models:
            models.append(model)
    return {
        "adaptive_downgrades": sorted(
            f"{model}|{purpose}" for model, purpose in _ADAPTIVE_DOWNGRADES
        ),
        "per_model_purpose": per_model_purpose,
        "per_purpose": per_purpose,
        "retry_count": llm_run_retry_count(),
    }


def _stats_to_dict(stats: _LlmPurposeStats) -> dict[str, float | int]:
    return {
        "calls": stats.calls,
        "truncations": stats.truncations,
        "retries": stats.retries,
        "no_thinking_retries": stats.no_thinking_retries,
        "slow_calls": stats.slow_calls,
        "completion_tokens_total": stats.completion_tokens_total,
        "reasoning_tokens_total": stats.reasoning_tokens_total,
        "latency_seconds_total": round(stats.latency_seconds_total, 3),
        "max_observed_latency_seconds": round(stats.max_observed_latency_seconds, 3),
        "usage_samples": stats.usage_samples,
    }


def _purpose_stats(model: str, purpose: str) -> _LlmPurposeStats:
    return _LLM_STATS[(model, purpose)]


def _configured_reasoning_override(model: str, purpose: str) -> str | None | object:
    if (model, purpose) in _RUN_REASONING_OVERRIDES:
        return _RUN_REASONING_OVERRIDES[(model, purpose)]
    return _DEFAULT_REASONING_SENTINEL


def _run_policy_enabled() -> bool:
    return bool(_RUN_REASONING_OVERRIDES)


def _adaptive_downgrade_active(model: str, purpose: str) -> bool:
    return _run_policy_enabled() and (model, purpose) in _ADAPTIVE_DOWNGRADES


def _effective_max_tokens(
    *, model: str, purpose: str, requested_max_tokens: int
) -> int:
    if not _run_policy_enabled():
        return requested_max_tokens
    return short_form_max_tokens(
        model=model,
        purpose=purpose,
        requested_max_tokens=requested_max_tokens,
    )


def short_form_max_tokens(
    *, model: str, purpose: str, requested_max_tokens: int
) -> int:
    cap = _SHORT_FORM_MAX_TOKENS.get((model, purpose))
    if cap is None:
        return requested_max_tokens
    return min(requested_max_tokens, cap)


def _effective_reasoning_options(
    *, model: str, purpose: str, max_tokens: int
) -> dict[str, str]:
    if max_tokens < 2000:
        return chat_reasoning_options(
            model, purpose=purpose, reasoning_effort_override="none"
        )
    if _adaptive_downgrade_active(model, purpose):
        return chat_reasoning_options(
            model, purpose=purpose, reasoning_effort_override="none"
        )
    if not _run_policy_enabled():
        return chat_reasoning_options(model, purpose=purpose)
    override = _configured_reasoning_override(model, purpose)
    if override is _DEFAULT_REASONING_SENTINEL:
        return chat_reasoning_options(model, purpose=purpose)
    return chat_reasoning_options(
        model, purpose=purpose, reasoning_effort_override=override
    )


def _record_call_stats(
    *, model: str, purpose: str, max_tokens: int, response, elapsed_seconds: float
) -> None:
    stats = _purpose_stats(model, purpose)
    stats.calls += 1
    stats.latency_seconds_total += elapsed_seconds
    stats.max_observed_latency_seconds = max(
        stats.max_observed_latency_seconds, elapsed_seconds
    )
    completion_tokens = _response_completion_tokens(response)
    reasoning_tokens = _response_reasoning_tokens(response)
    if completion_tokens is not None:
        stats.completion_tokens_total += completion_tokens
        stats.usage_samples += 1
    if reasoning_tokens is not None:
        stats.reasoning_tokens_total += reasoning_tokens
        if completion_tokens is None:
            stats.usage_samples += 1
    if _response_finish_reason(response) == "length":
        stats.truncations += 1
        key = (model, purpose)
        if _run_policy_enabled() and stats.truncations >= _RUN_TRUNCATION_THRESHOLD:
            _ADAPTIVE_DOWNGRADES.add(key)
            if key not in _ADAPTIVE_DOWNGRADE_LOGGED:
                _ADAPTIVE_DOWNGRADE_LOGGED.add(key)
                log(
                    "  [adaptive downgrade "
                    f"purpose={purpose} model={model} truncations={stats.truncations} "
                    "reasoning=none]",
                    level="WARN",
                )
    if (
        purpose in {"clue_compare", "clue_tiebreaker"}
        and elapsed_seconds >= _RUN_SLOW_CALL_SECONDS
    ):
        stats.slow_calls += 1
        log(
            "  [slow llm_call "
            f"purpose={purpose} model={model} elapsed_seconds={elapsed_seconds:.1f} "
            f"threshold={_RUN_SLOW_CALL_SECONDS:.1f} max_tokens={max_tokens}]",
            level="WARN",
        )


def _response_shows_hidden_reasoning_overrun(
    response,
    *,
    max_tokens: int,
) -> bool:
    reasoning_tokens = _response_reasoning_tokens(response)
    if reasoning_tokens is not None:
        return reasoning_tokens >= max(max_tokens - RETRY_WITHOUT_THINKING_MARGIN, 0)
    return bool(_response_reasoning_text(response).strip())


def _should_retry_without_thinking(
    response,
    *,
    purpose: str,
    max_tokens: int,
) -> bool:
    if max_tokens <= _retry_without_thinking_max_tokens():
        return False
    if _response_finish_reason(response) != "length":
        return False
    raw_content = _response_content_text(response)
    if raw_content.strip():
        return _short_form_payload_unusable(raw_content, purpose=purpose)
    return _response_shows_hidden_reasoning_overrun(response, max_tokens=max_tokens)


def _short_form_payload_unusable(raw_content: str, *, purpose: str) -> bool:
    if purpose in _JSON_SHORT_FORM_PURPOSES:
        return _extract_json_object(raw_content) is None
    if purpose in _CHOICE_SHORT_FORM_PURPOSES:
        choice = _clean_response(raw_content).strip().upper()
        return choice not in {"A", "B", "EQUAL"}
    return False


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
    started_at = time.monotonic()
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
    _record_call_stats(
        model=model,
        purpose=purpose,
        max_tokens=max_tokens,
        response=response,
        elapsed_seconds=time.monotonic() - started_at,
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


def _mark_response_source(response, source: str):
    try:
        setattr(response, "_response_source", source)
    except Exception:
        pass
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
    max_tokens = _effective_max_tokens(
        model=model,
        purpose=purpose,
        requested_max_tokens=max_tokens,
    )
    reasoning_options = _effective_reasoning_options(
        model=model,
        purpose=purpose,
        max_tokens=max_tokens,
    )
    response = _create_chat_completion_once(
        client,
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        purpose=purpose,
        reasoning_options=reasoning_options,
    )
    _mark_response_source(response, RESPONSE_SOURCE_REASONING)
    if not _should_retry_without_thinking(
        response,
        purpose=purpose,
        max_tokens=max_tokens,
    ):
        return response
    _log_retry_without_thinking(
        model=model,
        purpose=purpose,
        max_tokens=max_tokens,
        response=response,
    )
    _purpose_stats(model, purpose).retries += 1
    retry_response = _create_chat_completion_once(
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
    _purpose_stats(model, purpose).no_thinking_retries += 1
    return _mark_response_source(retry_response, RESPONSE_SOURCE_NO_THINKING_RETRY)


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
        reasoning_tokens / completion_tokens if completion_tokens else None
    )
    if budget_ratio < 0.75 and (completion_ratio is None or completion_ratio < 0.85):
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
