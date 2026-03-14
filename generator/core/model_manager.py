"""LM Studio model load/unload via REST API for multi-model workflows."""

from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass

from ..config import LMSTUDIO_BASE_URL


@dataclass(frozen=True)
class ModelConfig:
    model_id: str
    display_name: str
    context_length: int = 8192
    gpu_offload: float = 1.0


PRIMARY_MODEL = ModelConfig(
    model_id="openai/gpt-oss-20b",
    display_name="gpt-oss-20b",
)
SECONDARY_MODEL = ModelConfig(
    model_id="qwen/qwen3.5-27b",
    display_name="qwen-3.5-27b",
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
    try:
        data = _get_json("/api/v1/models")
        return [m.get("id", "") for m in data.get("data", [])]
    except Exception:
        return []


def load_model(config: ModelConfig) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] Loading model: {config.display_name} ({config.model_id})...")
    _post_json("/api/v1/models/load", {
        "model": config.model_id,
        "context_length": config.context_length,
        "gpu_offload": config.gpu_offload,
    })
    _wait_for_model(config.model_id)
    print(f"[{time.strftime('%H:%M:%S')}] Model loaded: {config.display_name}")


def unload_model(config: ModelConfig) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] Unloading model: {config.display_name}...")
    try:
        _post_json("/api/v1/models/unload", {
            "instance_id": config.model_id,
        })
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
        print(f"  Model unload skipped ({config.display_name}): {e}")
    print(f"[{time.strftime('%H:%M:%S')}] Model unloaded: {config.display_name}")


def ensure_model_loaded(config: ModelConfig) -> None:
    loaded = get_loaded_models()
    if config.model_id in loaded:
        print(f"[{time.strftime('%H:%M:%S')}] Model already active: {config.display_name}")
        return
    load_model(config)


def switch_model(from_model: ModelConfig, to_model: ModelConfig) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] Switching model: {from_model.display_name} -> {to_model.display_name}")
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
