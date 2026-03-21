"""LM Studio model load/unload via REST API for multi-model workflows."""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass

from ..config import LMSTUDIO_BASE_URL
from .runtime_logging import log


@dataclass(frozen=True)
class ModelConfig:
    model_id: str
    display_name: str
    context_length: int = 8192


PRIMARY_MODEL = ModelConfig(
    model_id="openai/gpt-oss-20b",
    display_name="gpt-oss-20b",
)
SECONDARY_MODEL = ModelConfig(
    model_id="eurollm-22b-instruct-2512-mlx-nvfp4",
    display_name="eurollm-22b",
)


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


def unload_model(config: ModelConfig) -> None:
    log(f"Unloading model: {config.display_name}...")
    instance_id = get_loaded_model_instances().get(config.model_id)
    if not instance_id:
        log(f"  Model unload skipped ({config.display_name}): no loaded instance found")
        return
    try:
        _post_json("/api/v1/models/unload", {
            "instance_id": instance_id,
        })
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        log(f"  Model unload skipped ({config.display_name}): {e}")
    else:
        log(f"Model unloaded: {config.display_name}")


def get_loaded_model_instances() -> dict[str, str]:
    """Return {model_key: instance_id} for loaded models."""
    try:
        data = _get_json("/api/v1/models")
    except Exception:
        return {}
    result = {}
    for m in data.get("models", []):
        instances = m.get("loaded_instances", [])
        if not instances:
            continue
        first = instances[0]
        if isinstance(first, dict):
            inst_id = first.get("identifier") or first.get("id") or m["key"]
        elif isinstance(first, str):
            inst_id = first
        else:
            inst_id = m["key"]
        result[m["key"]] = inst_id
    return result


def ensure_model_loaded(config: ModelConfig) -> None:
    instances = get_loaded_model_instances()
    if config.model_id in instances:
        log(f"Model already active: {config.display_name}")
        return
    for model_key, inst_id in instances.items():
        log(f"Unloading model: {model_key} (instance: {inst_id})")
        try:
            _post_json("/api/v1/models/unload", {"instance_id": inst_id})
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
