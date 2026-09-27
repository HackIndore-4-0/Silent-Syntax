"""EvaluationFailureDiagnoser — closes the loop between "this metric
failed" and "here's why, here's where in your code, here's a prompt
you could try instead."

This did NOT exist before: the pre-existing auto-improvement pipeline
(agentguard/improve/) only ever root-causes a Run's POLICY violations
(a state field silently disappearing/exceeding a bound, via
agentguard.reliability.root_cause's causal state-diff) — it has no
path from a failing EvaluationResult (a low faithfulness score, a
failed trajectory check) to a diagnosis at all. And even for Run
failures, "where in code" was only ever populated from an exception
traceback (agentguard/tracing/recording.py), which a quality-scoring
failure never raises — the LLM call succeeded, it just answered badly.

This module closes both gaps:
- WHERE: reuses recording.py's call-site capture (now populated on
  EVERY TraceStep, success or failure — see _find_call_site()) via the
  EvaluationResult.source_step_id anchor (see engine.py's
  build_eval_case_from_run()).
- WHY: the failing metric's own `reason`, grounded in the actual
  prompt/messages sent and the actual output scored (both read
  straight from the TraceStep, never re-derived or guessed).
- WHAT PROMPT: the SAME ModelProvider abstraction already used by
  make_llm_judge (agentguard.llm.provider.get_default_provider) — a
  real LLM call if one is configured, the clearly-labeled
  deterministic stand-in otherwise. A stand-in response has no
  "suggested_prompt" key, so `available=False` is reported rather than
  a fabricated suggestion — the same rule every other "real if
  configured" component in this codebase already follows.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..llm.provider import ModelProvider, get_default_provider
from ..storage.repository import RunRepository

_PROMPT_SUGGESTION_TEMPLATE = """An LLM call in an agent pipeline scored poorly on an evaluation metric. \
Suggest a concrete, rewritten prompt that would likely fix the specific failure described below. \
Respond with ONLY a JSON object, no other text: \
{{"suggested_prompt": "the full rewritten prompt text", "explanation": "one or two sentences on what changed and why"}}.

Metric: {metric}
Why it failed: {reason}

Original prompt (the actual messages sent):
{original_prompt}

Actual output that was scored:
{actual_output}
"""


@dataclass
class EvaluationDiagnosis:
    evaluation_result_id: str
    metric: str
    score: float | None
    why: str
    code_file: str | None
    code_function: str | None
    code_lineno: int | None
    original_prompt: Any
    actual_output: Any
    suggested_prompt: str | None
    suggestion_explanation: str
    judge_model_used: str | None
    available: bool
    unavailable_reason: str = ""


def _extract_prompt(step_input: dict[str, Any] | None) -> Any:
    """TraceStep.input is {"args": [...], "kwargs": {...}}; the real
    prompt/messages live under kwargs["messages"] (chat-shaped calls)
    or kwargs["prompt"] (completion-shaped calls) for every provider
    this codebase's own extractors already understand (see
    agentguard/tracing/llm_wrap.py). Falls back to the whole input
    dict when neither key is present, rather than silently returning
    nothing."""
    if not step_input:
        return None
    kwargs = step_input.get("kwargs") or {}
    if "messages" in kwargs:
        return kwargs["messages"]
    if "prompt" in kwargs:
        return kwargs["prompt"]
    return step_input


def _extract_json(raw: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object found in response: {raw!r}")
    return json.loads(match.group(0))


def _unavailable(evaluation_result_id: str, result: dict[str, Any], step: dict[str, Any] | None, reason: str) -> EvaluationDiagnosis:
    return EvaluationDiagnosis(
        evaluation_result_id=evaluation_result_id,
        metric=result["metric"],
        score=result.get("score"),
        why=result.get("reason", ""),
        code_file=(step or {}).get("code_file"),
        code_function=(step or {}).get("code_function"),
        code_lineno=(step or {}).get("code_lineno"),
        original_prompt=_extract_prompt((step or {}).get("input")) if step else None,
        actual_output=(step or {}).get("output"),
        suggested_prompt=None,
        suggestion_explanation="",
        judge_model_used=None,
        available=False,
        unavailable_reason=reason,
    )


async def diagnose_evaluation_failure(
    repository: RunRepository,
    evaluation_result_id: str,
    *,
    provider: ModelProvider | None = None,
) -> EvaluationDiagnosis:
    """Loads the EvaluationResult, resolves its source_step_id back to
    the real TraceStep, and produces a WHY / WHERE / suggested-prompt
    diagnosis. Never fabricates a suggestion the provider didn't
    actually generate — `available=False` + `unavailable_reason` is
    the honest result whenever there's nothing real to report."""
    result = await repository.get_evaluation_result(evaluation_result_id)
    if result is None:
        raise ValueError(f"no EvaluationResult with id {evaluation_result_id!r}")

    step_id = result.get("source_step_id")
    step = await repository.get_trace_step(step_id) if step_id else None
    if step is None:
        return _unavailable(
            evaluation_result_id, result, None,
            "this EvaluationResult has no source_step_id (or the step no longer exists) — "
            "cannot locate the originating TraceStep's prompt or code location",
        )

    original_prompt = _extract_prompt(step.get("input"))
    resolved_provider = provider or get_default_provider()
    prompt = _PROMPT_SUGGESTION_TEMPLATE.format(
        metric=result["metric"],
        reason=result.get("reason", ""),
        original_prompt=json.dumps(original_prompt, default=str, indent=2),
        actual_output=json.dumps(step.get("output"), default=str, indent=2),
    )

    try:
        raw = await resolved_provider.complete(prompt)
        data = _extract_json(raw)
    except (ValueError, json.JSONDecodeError, Exception) as exc:  # noqa: BLE001 - a provider failure must never crash the caller
        return _unavailable(evaluation_result_id, result, step, f"prompt-suggestion provider call failed: {exc}")

    suggested_prompt = data.get("suggested_prompt")
    if not suggested_prompt:
        return _unavailable(
            evaluation_result_id, result, step,
            "the configured provider did not return a suggested_prompt "
            "(the deterministic stand-in never fabricates one — configure a real LLM provider for this feature)",
        )

    return EvaluationDiagnosis(
        evaluation_result_id=evaluation_result_id,
        metric=result["metric"],
        score=result.get("score"),
        why=result.get("reason", ""),
        code_file=step.get("code_file"),
        code_function=step.get("code_function"),
        code_lineno=step.get("code_lineno"),
        original_prompt=original_prompt,
        actual_output=step.get("output"),
        suggested_prompt=suggested_prompt,
        suggestion_explanation=data.get("explanation", ""),
        judge_model_used=getattr(resolved_provider, "model", type(resolved_provider).__name__),
        available=True,
    )
