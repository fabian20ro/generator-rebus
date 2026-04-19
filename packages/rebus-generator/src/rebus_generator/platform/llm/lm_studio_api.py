"""LM Studio REST helpers."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from rebus_generator.platform.config import LMSTUDIO_BASE_URL

from .models import ModelConfig
from rebus_generator.platform.io.runtime_logging import log

_REASONING_ALLOWED_OPTIONS_CACHE: dict[str, tuple[str, ...] | None] = {}


@dataclass(frozen=True)
class LoadedModelInstance:
    model_id: str
    instance_id: str


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
    try:
        data = _get_json("/api/v1/models")
        return [m["key"] for m in data.get("models", []) if m.get("loaded_instances")]
    except Exception:
        return []


def reset_model_capability_cache() -> None:
    _REASONING_ALLOWED_OPTIONS_CACHE.clear()


def get_model_reasoning_allowed_options(
    model_id: str,
    *,
    refresh: bool = False,
) -> tuple[str, ...] | None:
    normalized = str(model_id or "").strip()
    if not normalized:
        return None
    if not refresh and normalized in _REASONING_ALLOWED_OPTIONS_CACHE:
        return _REASONING_ALLOWED_OPTIONS_CACHE[normalized]
    try:
        data = _get_json("/api/v1/models")
    except Exception:
        return _REASONING_ALLOWED_OPTIONS_CACHE.get(normalized)

    result: tuple[str, ...] | None = None
    for model in data.get("models", []):
        if str(model.get("key") or "").strip() != normalized:
            continue
        reasoning = (model.get("capabilities") or {}).get("reasoning") or {}
        allowed = reasoning.get("allowed_options")
        if isinstance(allowed, list):
            values = [str(value or "").strip().lower() for value in allowed]
            cleaned = tuple(value for value in values if value)
            result = cleaned or None
        break
    _REASONING_ALLOWED_OPTIONS_CACHE[normalized] = result
    return result


def load_model(config: ModelConfig) -> None:
    log(f"Loading model: {config.display_name} ({config.model_id})...")
    _post_json(
        "/api/v1/models/load",
        {"model": config.model_id, "context_length": config.context_length},
    )
    _wait_for_model(config.model_id)
    log(f"Model loaded: {config.display_name}")


def unload_instance(instance_id: str, *, model_id: str = "") -> None:
    label = model_id or instance_id
    log(f"Unloading model: {label} (instance: {instance_id})")
    _post_json("/api/v1/models/unload", {"instance_id": instance_id})
    log(f"Model unloaded: {label}")


def list_loaded_model_instances() -> list[LoadedModelInstance]:
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
                instance_id = str(raw_instance.get("identifier") or raw_instance.get("id") or "").strip()
            elif isinstance(raw_instance, str):
                instance_id = raw_instance.strip()
            if not instance_id:
                log(f"Skipping loaded model without instance id: {model_id}")
                continue
            result.append(LoadedModelInstance(model_id=model_id, instance_id=instance_id))
    return result


def get_loaded_model_instances() -> dict[str, str]:
    return {entry.model_id: entry.instance_id for entry in list_loaded_model_instances()}


def unload_model(config: ModelConfig) -> None:
    log(f"Unloading model: {config.display_name}...")
    instance_id = get_loaded_model_instances().get(config.model_id)
    if not instance_id:
        log(f"  Model unload skipped ({config.display_name}): no loaded instance found")
        return
    try:
        unload_instance(instance_id, model_id=config.model_id)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        log(f"  Model unload skipped ({config.display_name}): {exc}")


def ensure_model_loaded(config: ModelConfig) -> None:
    instances = get_loaded_model_instances()
    if config.model_id in instances:
        log(f"Model already active: {config.display_name}")
        return
    for model_key, instance_id in instances.items():
        try:
            unload_instance(instance_id, model_id=model_key)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            log(f"  Unload skipped ({model_key}): {exc}")
        time.sleep(2)
    load_model(config)


def switch_model(from_model: ModelConfig, to_model: ModelConfig) -> None:
    log(f"Switching model: {from_model.display_name} -> {to_model.display_name}")
    unload_model(from_model)
    time.sleep(2)
    load_model(to_model)


def _wait_for_model(
    model_id: str, timeout: float = 60.0, poll_interval: float = 3.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if model_id in get_loaded_models():
            return
        time.sleep(poll_interval)
    raise TimeoutError(f"Model {model_id} did not load within {timeout}s")


def _wait_for_unload_model(
    model_id: str, timeout: float = 60.0, poll_interval: float = 2.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if model_id not in get_loaded_models():
            return
        time.sleep(poll_interval)
    log(f"  [unload timeout] model={model_id} seconds={timeout:.1f}", level="WARN")
