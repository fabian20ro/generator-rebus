"""Thin model orchestration wrapper for multi-model workflows."""

from __future__ import annotations

from dataclasses import dataclass

from .model_manager import (
    ModelConfig,
    PRIMARY_MODEL,
    SECONDARY_MODEL,
    ensure_model_loaded,
    switch_model,
)


@dataclass
class ModelSession:
    multi_model: bool = False
    primary: ModelConfig = PRIMARY_MODEL
    secondary: ModelConfig = SECONDARY_MODEL
    current_model: ModelConfig | None = None
    switch_count: int = 0

    def ensure(self, model: ModelConfig) -> ModelConfig:
        if self.current_model is None:
            ensure_model_loaded(model)
            self.current_model = model
            return model
        if self.current_model.model_id == model.model_id:
            ensure_model_loaded(model)
            self.current_model = model
            return model
        switch_model(self.current_model, model)
        self.current_model = model
        self.switch_count += 1
        return model

    def start_primary(self) -> ModelConfig:
        return self.ensure(self.primary)

    def start_secondary(self) -> ModelConfig:
        return self.ensure(self.secondary)

    def activate_initial_evaluator(self) -> ModelConfig:
        if not self.multi_model:
            return self.start_primary()
        self.start_primary()
        return self.start_secondary()

    def alternate(self) -> ModelConfig:
        if not self.multi_model:
            return self.current_model or self.start_primary()
        if self.current_model is None or self.current_model.model_id == self.secondary.model_id:
            return self.ensure(self.primary)
        return self.ensure(self.secondary)
