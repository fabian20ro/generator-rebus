"""Compatibility shim for the unified LM runtime."""

from .lm_runtime import LmRuntime, ModelSession

__all__ = ["LmRuntime", "ModelSession"]
