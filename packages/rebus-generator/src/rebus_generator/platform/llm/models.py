"""Model registry and reasoning policy helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class ReasoningTransportConfig:
    no_thinking_value: str | None = "none"
    thinking_value_by_effort: Mapping[str, str | None] = field(default_factory=dict)
    prefer_omit_for_binary_reasoning: bool = False


@dataclass(frozen=True)
class ResolvedReasoningOptions:
    abstract_effort: str | None
    request_options: Mapping[str, str] = field(default_factory=dict)
    reasoning_enabled: bool = False

    def with_request_options(self, request_options: Mapping[str, str]) -> "ResolvedReasoningOptions":
        return ResolvedReasoningOptions(
            abstract_effort=self.abstract_effort,
            request_options=dict(request_options),
            reasoning_enabled=self.reasoning_enabled,
        )


@dataclass(frozen=True)
class ModelConfig:
    registry_key: str
    model_id: str
    display_name: str
    max_completion_tokens: int
    context_length: int = 8192
    reasoning_by_purpose: Mapping[str, str | None] = field(default_factory=dict)
    reasoning_transport: ReasoningTransportConfig = field(
        default_factory=ReasoningTransportConfig
    )


MODEL_CATALOG: dict[str, ModelConfig] = {
    "gemma4_26b_a4b": ModelConfig(
        registry_key="gemma4_26b_a4b",
        model_id="google/gemma-4-26b-a4b",
        display_name="gemma-4",
        max_completion_tokens=6000,
        reasoning_by_purpose={
            "default": "low",
            "definition_generate": "low",
            "definition_rewrite": "low",
            "definition_verify": "none",
            "definition_rate": "low",
            "clue_compare": "low",
            "title_generate": "low",
            "title_rate": "none",
        },
        reasoning_transport=ReasoningTransportConfig(
            thinking_value_by_effort={"default": None},
            prefer_omit_for_binary_reasoning=True
        ),
    ),
    "gpt_oss_20b": ModelConfig(
        registry_key="gpt_oss_20b",
        model_id="openai/gpt-oss-20b",
        display_name="gpt-oss-20b",
        max_completion_tokens=2000,
        reasoning_by_purpose={
            "default": "low",
            "definition_generate": "medium",
            "definition_rewrite": "medium",
            "definition_rate": "medium",
            "clue_compare": "medium",
        },
    ),
    "eurollm_22b": ModelConfig(
        registry_key="eurollm_22b",
        model_id="eurollm-22b-instruct-2512-mlx-nvfp4",
        display_name="eurollm-22b",
        max_completion_tokens=200,
        reasoning_by_purpose={"default": None},
    ),
}
ACTIVE_MODEL_KEYS = ("gemma4_26b_a4b", "eurollm_22b")
MODEL_CONFIGS = tuple(MODEL_CATALOG.values())
PRIMARY_MODEL = MODEL_CATALOG[ACTIVE_MODEL_KEYS[0]]
SECONDARY_MODEL = MODEL_CATALOG[ACTIVE_MODEL_KEYS[1]]
_REASONING_ALIAS_MAP = {"off": "none", "on": "medium"}
_VALID_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}
_USE_MODEL_REASONING = object()


def get_model_by_key(model_key: str) -> ModelConfig:
    normalized = str(model_key or "").strip()
    if normalized not in MODEL_CATALOG:
        raise KeyError(f"Unknown model registry key: {normalized}")
    return MODEL_CATALOG[normalized]


def get_active_primary_model() -> ModelConfig:
    return get_model_by_key(ACTIVE_MODEL_KEYS[0])


def get_active_secondary_model() -> ModelConfig:
    return get_model_by_key(ACTIVE_MODEL_KEYS[1])


def get_active_models(*, multi_model: bool) -> tuple[ModelConfig, ...]:
    active = (get_active_primary_model(),)
    if multi_model:
        active += (get_active_secondary_model(),)
    return active


def get_active_model_labels(*, multi_model: bool) -> list[str]:
    return [config.display_name for config in get_active_models(multi_model=multi_model)]


def get_model_config(model_id: str) -> ModelConfig | None:
    normalized = str(model_id or "").strip()
    for config in MODEL_CONFIGS:
        if config.model_id == normalized or config.registry_key == normalized:
            return config
    return None


def resolve_reasoning_effort(
    model: str | ModelConfig | None,
    *,
    purpose: str = "default",
    reasoning_effort_override: str | None | object = _USE_MODEL_REASONING,
) -> str | None:
    config = model if isinstance(model, ModelConfig) else get_model_config(str(model or ""))
    if not config:
        return None

    # If the model does not support reasoning at all (default is None), always omit.
    # This prevents crashing legacy models like EuroLLM even if an override says "none".
    if config.reasoning_by_purpose.get("default") is None:
        return None

    if reasoning_effort_override is not _USE_MODEL_REASONING:
        if reasoning_effort_override is None:
            return None
        return _normalize_reasoning_effort(str(reasoning_effort_override))

    effort = config.reasoning_by_purpose.get(
        purpose,
        config.reasoning_by_purpose.get("default"),
    )
    if effort is None:
        return None
    return _normalize_reasoning_effort(effort)


def chat_reasoning_options(
    model: str | ModelConfig | None,
    *,
    purpose: str = "default",
    reasoning_effort_override: str | None | object = _USE_MODEL_REASONING,
) -> dict[str, str]:
    return dict(
        resolve_chat_reasoning_request(
            model,
            purpose=purpose,
            reasoning_effort_override=reasoning_effort_override,
        ).request_options
    )


def resolve_chat_reasoning_request(
    model: str | ModelConfig | None,
    *,
    purpose: str = "default",
    reasoning_effort_override: str | None | object = _USE_MODEL_REASONING,
) -> ResolvedReasoningOptions:
    effort = resolve_reasoning_effort(
        model,
        purpose=purpose,
        reasoning_effort_override=reasoning_effort_override,
    )
    if effort is None:
        return ResolvedReasoningOptions(
            abstract_effort=None,
            request_options={},
            reasoning_enabled=False,
        )
    config = model if isinstance(model, ModelConfig) else get_model_config(str(model or ""))
    transport = (
        config.reasoning_transport if config else ReasoningTransportConfig()
    )
    request_effort = _transport_reasoning_effort(transport, effort)
    request_options = (
        {"reasoning_effort": request_effort} if request_effort is not None else {}
    )
    return ResolvedReasoningOptions(
        abstract_effort=effort,
        request_options=request_options,
        reasoning_enabled=effort != "none",
    )


def chat_max_tokens(model: str | ModelConfig | None) -> int:
    config = model if isinstance(model, ModelConfig) else get_model_config(str(model or ""))
    if not config:
        raise KeyError(f"Unknown model for max token lookup: {model}")
    return config.max_completion_tokens


def _normalize_reasoning_effort(effort: str) -> str:
    normalized = str(effort or "").strip().lower()
    normalized = _REASONING_ALIAS_MAP.get(normalized, normalized)
    if normalized not in _VALID_REASONING_EFFORTS:
        raise ValueError(f"Unsupported reasoning_effort value: {effort}")
    return normalized


def _transport_reasoning_effort(
    transport: ReasoningTransportConfig,
    effort: str,
) -> str | None:
    if effort == "none":
        return transport.no_thinking_value
    mapping = transport.thinking_value_by_effort
    if effort in mapping:
        return mapping[effort]
    if "default" in mapping:
        return mapping["default"]
    return effort
