"""LLM Studio client helpers and streaming transport logic."""

from __future__ import annotations

import json
import random
import re
import sys
import time
from types import SimpleNamespace

from openai import OpenAI

from ..config import LMSTUDIO_BASE_URL
from .llm_text import clean_llm_text_response
from .model_manager import chat_max_tokens, chat_reasoning_options
from .runtime_logging import llm_debug_enabled, log

RETRY_WITHOUT_THINKING_MAX_TOKENS = 200
RETRY_WITHOUT_THINKING_MARGIN = 10

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
