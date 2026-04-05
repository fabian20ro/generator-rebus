"""Low-level LM Studio REST helpers plus central model registry."""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Mapping

from ..config import LMSTUDIO_BASE_URL
from .runtime_logging import log


@dataclass(frozen=True)
class ModelConfig:
    registry_key: str
    model_id: str
    display_name: str
    context_length: int = 8192
    reasoning_by_purpose: Mapping[str, str | None] = field(default_factory=dict)


@dataclass(frozen=True)
class LoadedModelInstance:
    model_id: str
    instance_id: str


MODEL_CATALOG: dict[str, ModelConfig] = {
    "gemma4_26b_a4b": ModelConfig(
        registry_key="gemma4_26b_a4b",
        model_id="google/gemma-4-26b-a4b",
        display_name="gemma-4",
        reasoning_by_purpose={
            "default": "none",
            "definition_generate": "medium",
            "definition_rewrite": "medium",
            "definition_rate": "medium",
            "clue_compare": "none",
        },
    ),
    "gpt_oss_20b": ModelConfig(
        registry_key="gpt_oss_20b",
        model_id="openai/gpt-oss-20b",
        display_name="gpt-oss-20b",
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
        reasoning_by_purpose={
            "default": None,
        },
    ),
}
ACTIVE_MODEL_KEYS = ("gemma4_26b_a4b", "eurollm_22b")


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


PRIMARY_MODEL = get_active_primary_model()
SECONDARY_MODEL = get_active_secondary_model()
MODEL_CONFIGS = tuple(MODEL_CATALOG.values())
_REASONING_ALIAS_MAP = {
    "off": "none",
    "on": "medium",
}
_VALID_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}


def get_model_config(model_id: str) -> ModelConfig | None:
    normalized = str(model_id or "").strip()
    for config in MODEL_CONFIGS:
        if config.model_id == normalized or config.registry_key == normalized:
            return config
    return None


def chat_reasoning_options(
    model: str | ModelConfig | None,
    *,
    purpose: str = "default",
) -> dict[str, str]:
    config = model if isinstance(model, ModelConfig) else get_model_config(str(model or ""))
    if not config:
        return {}
    effort = config.reasoning_by_purpose.get(
        purpose,
        config.reasoning_by_purpose.get("default"),
    )
    if effort is None:
        return {}
    normalized_effort = _normalize_reasoning_effort(effort)
    return {"reasoning_effort": normalized_effort}


def _normalize_reasoning_effort(effort: str) -> str:
    normalized = str(effort or "").strip().lower()
    normalized = _REASONING_ALIAS_MAP.get(normalized, normalized)
    if normalized not in _VALID_REASONING_EFFORTS:
        raise ValueError(f"Unsupported reasoning_effort value: {effort}")
    return normalized


def _api_url(path: str) -> str:
    return f"{LMSTUDIO_BASE_URL}{path}"


def _post_json(path: str, body: dict, timeout: float = 120.0) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        _api_url(path),
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(path: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(_api_url(path), method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_loaded_models() -> list[str]:
    """Return model IDs that have at least one loaded instance."""
    try:
        data = _get_json("/api/v1/models")
        return [
            m["key"]
            for m in data.get("models", [])
            if m.get("loaded_instances")
        ]
    except Exception:
        return []


def load_model(config: ModelConfig) -> None:
    log(f"Loading model: {config.display_name} ({config.model_id})...")
    _post_json("/api/v1/models/load", {
        "model": config.model_id,
        "context_length": config.context_length,
    })
    _wait_for_model(config.model_id)
    log(f"Model loaded: {config.display_name}")


def unload_instance(instance_id: str, *, model_id: str = "") -> None:
    label = model_id or instance_id
    log(f"Unloading model: {label} (instance: {instance_id})")
    _post_json("/api/v1/models/unload", {"instance_id": instance_id})
    log(f"Model unloaded: {label}")


def list_loaded_model_instances() -> list[LoadedModelInstance]:
    """Return loaded LM Studio instances with usable instance IDs only."""
    try:
        data = _get_json("/api/v1/models")
    except Exception:
        return []

    result: list[LoadedModelInstance] = []
    for model in data.get("models", []):
        model_id = str(model.get("key") or "").strip()
        if not model_id:
            continue
        for raw_instance in model.get("loaded_instances", []) or []:
            instance_id = ""
            if isinstance(raw_instance, dict):
                instance_id = str(
                    raw_instance.get("identifier")
                    or raw_instance.get("id")
                    or ""
                ).strip()
            elif isinstance(raw_instance, str):
                instance_id = raw_instance.strip()
            if not instance_id:
                log(f"Skipping loaded model without instance id: {model_id}")
                continue
            result.append(LoadedModelInstance(model_id=model_id, instance_id=instance_id))
    return result


def get_loaded_model_instances() -> dict[str, str]:
    """Return {model_key: instance_id} for loaded models."""
    return {
        entry.model_id: entry.instance_id
        for entry in list_loaded_model_instances()
    }


def unload_model(config: ModelConfig) -> None:
    log(f"Unloading model: {config.display_name}...")
    instance_id = get_loaded_model_instances().get(config.model_id)
    if not instance_id:
        log(f"  Model unload skipped ({config.display_name}): no loaded instance found")
        return
    try:
        unload_instance(instance_id, model_id=config.model_id)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        log(f"  Model unload skipped ({config.display_name}): {e}")


def ensure_model_loaded(config: ModelConfig) -> None:
    instances = get_loaded_model_instances()
    if config.model_id in instances:
        log(f"Model already active: {config.display_name}")
        return
    for model_key, inst_id in instances.items():
        try:
            unload_instance(inst_id, model_id=model_key)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
            log(f"  Unload skipped ({model_key}): {e}")
        time.sleep(2)
    load_model(config)


def switch_model(from_model: ModelConfig, to_model: ModelConfig) -> None:
    log(f"Switching model: {from_model.display_name} -> {to_model.display_name}")
    unload_model(from_model)
    time.sleep(2)
    load_model(to_model)


def _wait_for_model(model_id: str, timeout: float = 120.0, poll_interval: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        loaded = get_loaded_models()
        if model_id in loaded:
            return
        time.sleep(poll_interval)
    raise TimeoutError(f"Model {model_id} did not load within {timeout}s")
