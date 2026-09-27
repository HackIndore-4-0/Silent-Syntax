"""Model adapter abstraction for the LLM-as-Judge evaluator.

`ModelProvider` is the seam that keeps AgentGuard from being hard-coded
to one LLM vendor. `get_default_provider()` picks a real provider when
one is actually configured/reachable in the current environment, and
falls back to `DeterministicTestProvider` — a clearly-labeled,
non-network stand-in — otherwise. See provider.py's module docstring
for exactly what that means in *this* environment.
"""
from .provider import DeterministicTestProvider, ModelProvider, get_default_provider

__all__ = ["ModelProvider", "DeterministicTestProvider", "get_default_provider"]
