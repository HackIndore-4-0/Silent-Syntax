"""Evaluator interface.

Phase 1 ships one concrete evaluator (rule_based.ConstraintAdherenceEvaluator).
Later phases add LLM-as-judge and embedding/NLI evaluators behind this
same interface, and @monitor already accepts a list of Evaluator
instances so those can be plugged in without changing the decorator.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import EvalResult, Run


class Evaluator(ABC):
    """Base class for anything that scores a completed Run."""

    name: str = "evaluator"

    @abstractmethod
    def evaluate(self, run: Run) -> EvalResult:
        """Score `run` using its initial_state/final_state and policy."""
        raise NotImplementedError
