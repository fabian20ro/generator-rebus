"""High-level LM Studio runtime orchestration."""

from __future__ import annotations

import time
import urllib.error
from dataclasses import dataclass
from typing import Callable

from .models import ModelConfig, PRIMARY_MODEL, SECONDARY_MODEL
from .lm_studio_api import (
    _wait_for_unload_model,
    get_loaded_model_instances,
    load_model,
    unload_instance,
)
from rebus_generator.platform.io.runtime_logging import log


@dataclass
class LmRuntime:
    multi_model: bool = False
    primary: ModelConfig = PRIMARY_MODEL
    secondary: ModelConfig = SECONDARY_MODEL
    current_model: ModelConfig | None = None
    switch_count: int = 0
    activation_count: int = 0
    unload_count: int = 0
    activation_seconds_total: float = 0.0
    unload_seconds_total: float = 0.0
    switch_callback: Callable[[str, str, "LmRuntime", str], None] | None = None
    nested_activation_callback: Callable[[str, str, dict[str, str]], None] | None = None

    @property
    def current_model_id(self) -> str:
        return self.current_model.model_id if self.current_model else ""

    @property
    def current_model_label(self) -> str:
        return self.current_model.display_name if self.current_model else ""

    def _sync_current_model(self) -> dict[str, str]:
        instances = get_loaded_model_instances()
        if self.primary.model_id in instances:
            self.current_model = self.primary
        elif self.secondary.model_id in instances:
            self.current_model = self.secondary
        elif not instances:
            self.current_model = None
        else:
            self.current_model = None
        return instances

    def sync(self) -> dict[str, str]:
        return self._sync_current_model()

    def _unload_model_ids(self, model_ids: list[str]) -> None:
        for model_id in model_ids:
            instances = get_loaded_model_instances()
            instance_id = instances.get(model_id)
            if not instance_id:
                continue
            started_at = time.monotonic()
            try:
                unload_instance(instance_id, model_id=model_id)
                _wait_for_unload_model(model_id)
            except urllib.error.HTTPError as exc:
                refreshed = get_loaded_model_instances()
                if model_id not in refreshed:
                    log(f"Unload raced away for {model_id}; refreshed live state")
                    continue
                raise RuntimeError(
                    f"Failed to unload active model {model_id}: {exc}"
                ) from exc
            self.unload_count += 1
            self.unload_seconds_total += time.monotonic() - started_at

    def activate(self, model: ModelConfig, *, reason: str = "") -> ModelConfig:
        target = self.primary if (not self.multi_model and model.model_id == self.secondary.model_id) else model

        instances = self._sync_current_model()
        if set(instances.keys()) == {target.model_id}:
            self.current_model = target
            log(f"Model already active: {target.display_name}")
            return target

        prior_model_id = self.current_model.model_id if self.current_model else None
        active_step = getattr(self, "_run_all_active_step", None)
        if (
            active_step is not None
            and prior_model_id
            and prior_model_id != target.model_id
            and self.nested_activation_callback is not None
        ):
            self.nested_activation_callback(prior_model_id, target.model_id, dict(active_step))

        other_model_ids = [model_id for model_id in instances if model_id != target.model_id]
        if other_model_ids:
            self._unload_model_ids(other_model_ids)
            instances = self._sync_current_model()

        if target.model_id in instances and len(instances) == 1:
            self.current_model = target
            if prior_model_id and prior_model_id != target.model_id:
                self.switch_count += 1
            log(f"Model already active: {target.display_name}")
            return target

        if instances:
            self._unload_model_ids(list(instances.keys()))
            self._sync_current_model()

        last_error: Exception | None = None
        for attempt in range(2):
            started_at = time.monotonic()
            try:
                load_model(target)
                instances = self._sync_current_model()
                if target.model_id not in instances:
                    raise RuntimeError(
                        f"LM Studio did not activate expected model {target.model_id}"
                    )
                if set(instances.keys()) != {target.model_id}:
                    extras = ", ".join(sorted(model_id for model_id in instances if model_id != target.model_id))
                    raise RuntimeError(
                        f"LM Studio left extra models active while loading {target.model_id}: {extras}"
                    )
                self.current_model = target
                if prior_model_id and prior_model_id != target.model_id:
                    self.switch_count += 1
                    if self.switch_callback is not None:
                        self.switch_callback(prior_model_id, target.model_id, self, reason)
                self.activation_count += 1
                self.activation_seconds_total += time.monotonic() - started_at
                return target
            except Exception as exc:
                last_error = exc
                instances = self._sync_current_model()
                if instances:
                    self._unload_model_ids(list(instances.keys()))
                    self._sync_current_model()
                if attempt == 0:
                    log(f"Retrying model activation for {target.display_name} after live-state refresh")
                    continue
        raise RuntimeError(
            f"Could not activate LM Studio model {target.display_name}"
        ) from last_error

    def activate_primary(self, *, reason: str = "") -> ModelConfig:
        return self.activate(self.primary, reason=reason)

    def ensure_active(self, model: ModelConfig, *, reason: str = "") -> ModelConfig:
        return self.activate(model, reason=reason)

    def activate_secondary(self, *, reason: str = "") -> ModelConfig:
        if not self.multi_model:
            return self.activate_primary(reason=reason)
        return self.activate(self.secondary, reason=reason)

    def activate_initial_evaluator(self, *, reason: str = "") -> ModelConfig:
        if not self.multi_model:
            return self.activate_primary(reason=reason)
        self.activate_primary(reason=f"{reason}:initial_eval_p1")
        return self.activate_secondary(reason=f"{reason}:initial_eval_p2")

    def alternate(self, *, reason: str = "") -> ModelConfig:
        if not self.multi_model:
            return self.activate_primary(reason=reason)
        if self.current_model and self.current_model.model_id == self.secondary.model_id:
            return self.activate_primary(reason=reason)
        return self.activate_secondary(reason=reason)


ModelSession = LmRuntime
