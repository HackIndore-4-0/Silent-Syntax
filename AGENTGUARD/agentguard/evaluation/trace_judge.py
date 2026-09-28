"""Per-trace-step LLM quality judgment + model recommendation.

Runs automatically, in the background, right after every successful
llm_call TraceStep is persisted (agentguard/tracing/recording.py's
StepRecorder._finish) — this is what answers "why isn't my LLM giving
a proper output" and "what model should I use instead" at the time of
tracing, for EVERY call, with no user action required. Distinct from
agentguard/evaluation/benchmark.py's ModelBenchmarkEngine, which is a
heavier, user-triggered, multi-model comparison run against a stored
evaluation suite — that flow is unchanged.

Two independent concerns, deliberately kept separate:
- Quality judgment: one extra LLM call, via the SAME "real provider if
  configured, else a clearly-labeled deterministic stand-in" abstraction
  the rest of the codebase already uses (agentguard/llm/provider.py's
  get_default_provider()) — scoring the actual input/output pair.
- Model recommendation: a zero-extra-LLM-call, deterministic heuristic
  over agentguard/llm/catalog.py plus this call's own real, observed
  tokens/tool-use, never a second guess made *by* the judge model about
  which model is best (that would be circular and unfalsifiable).

Persisted as a Recommendation row (kind=TRACE_JUDGMENT_KIND, no schema
change: reuses the existing agentguard_recommendations table/repository
methods that already back /model-recommendations) with subject_id set
to the judged TraceStep's id and run_id embedded in the recommendation
payload so the dashboard can fetch "all judgments for run X".
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..llm.catalog import MODEL_CATALOG, ModelCatalogEntry, list_catalog
from ..llm.provider import AnthropicProvider, ModelProvider, OpenRouterProvider, get_default_provider
from ..models import Recommendation, TraceStep

logger = logging.getLogger("agentguard.evaluation.trace_judge")

TRACE_JUDGMENT_KIND = "trace_judgment"

_JUDGE_PROMPT = """You are reviewing one real LLM call made by an AI agent, to help the developer improve it. \
Respond with ONLY a JSON object, no other text: \
{{"score": a number from 0.0 to 1.0 rating how well the output actually satisfies the input request, \
"verdict": one of "good", "needs_improvement", "poor", \
"issues": a short JSON list of concrete problems with THIS output (empty list if none), \
"suggested_fix": one or two concrete, actionable sentences on what to change (prompt wording, missing context, \
an output-format constraint, temperature/max_tokens, etc.) to get a better result next time for this exact call \
-- never generic advice}}.

Input sent to the model:
{input_text}

Output the model returned:
{output_text}
"""

_MAX_PROMPT_CHARS = 4000


def _truncate(text: str) -> str:
    return text if len(text) <= _MAX_PROMPT_CHARS else text[:_MAX_PROMPT_CHARS] + "…(truncated)"


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


def _extract_json(raw: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"no JSON object found in judge response: {raw!r}")
    return json.loads(match.group(0))


async def judge_output(provider: ModelProvider, input_text: str, output_text: str) -> dict[str, Any]:
    """Never raises: a judge that can't parse its own model's response
    reports NEEDS_REVIEW-equivalent output, the same conservative
    fallback agentguard/evaluation/dataset/llm_judge.py already uses,
    rather than fabricating a score."""
    prompt = _JUDGE_PROMPT.format(input_text=_truncate(input_text), output_text=_truncate(output_text))
    raw = await provider.complete(prompt)
    try:
        data = _extract_json(raw)
    except (ValueError, json.JSONDecodeError):
        return {
            "score": None,
            "verdict": "needs_improvement",
            "issues": ["judge response was not valid JSON"],
            "suggested_fix": "",
        }
    return {
        "score": data.get("score"),
        "verdict": data.get("verdict") or "needs_improvement",
        "issues": list(data.get("issues") or []),
        "suggested_fix": str(data.get("suggested_fix") or ""),
    }


def _find_catalog_entry(model_name: str | None) -> ModelCatalogEntry | None:
    if not model_name:
        return None
    lowered = model_name.lower()
    for entry in MODEL_CATALOG:
        if entry.model.lower() in lowered or lowered in entry.model.lower():
            return entry
    return None


def recommend_model(
    *,
    model_name: str | None,
    tokens_input: int | None,
    tokens_output: int | None,
    uses_tools: bool,
    score: float | None,
) -> tuple[str | None, str]:
    """Deterministic, falsifiable heuristic — never asks an LLM which
    model is "best" (that would be unfalsifiable and circular with the
    judge call above). Requirements (tool calling, minimum context
    window) are inferred from this call's own real usage, never
    guessed."""
    current = _find_catalog_entry(model_name)
    observed_context = (tokens_input or 0) + (tokens_output or 0)
    candidates = list_catalog(
        requires_tools=True if uses_tools else None,
        min_context_window=observed_context or None,
    )
    if not candidates:
        return model_name, (
            "no catalog model meets this call's observed requirements (tool use and/or context length) "
            "— keeping the current model"
        )

    others = [c for c in candidates if current is None or c.model != current.model]

    if score is not None and score < 0.6 and others:
        best = max(others, key=lambda c: c.input_price_per_1k)
        return best.model, (
            f"output quality scored {score:.2f} ({'needs_improvement' if score >= 0.3 else 'poor'}) with "
            f"{model_name!r}; {best.model} is a higher-tier catalog model with the same tool/context capability"
        )

    if score is not None and score >= 0.85 and current is not None:
        cheaper = [c for c in others if c.input_price_per_1k < current.input_price_per_1k]
        if cheaper:
            best = min(cheaper, key=lambda c: c.input_price_per_1k)
            return best.model, (
                f"output quality already scored {score:.2f} with {model_name!r}; {best.model} meets the same "
                f"tool/context requirements at lower list price (${best.input_price_per_1k}/1k input vs "
                f"${current.input_price_per_1k}/1k)"
            )

    return model_name, "current model's output quality is adequate for this call; no change recommended"


def _build_step_texts(step: TraceStep) -> tuple[str, str]:
    input_text = _stringify(step.input.get("kwargs", step.input) if isinstance(step.input, dict) else step.input)
    output_text = _stringify(step.output)
    return input_text, output_text


def _uses_tools(step: TraceStep) -> bool:
    return isinstance(step.output, dict) and bool(step.output.get("tool_calls"))


async def build_trace_judgment(*, workspace_id: str | None, run_id: str, step: TraceStep) -> Recommendation:
    provider = get_default_provider()
    input_text, output_text = _build_step_texts(step)
    verdict_data = await judge_output(provider, input_text, output_text)
    recommended_model, model_reason = recommend_model(
        model_name=step.model_name,
        tokens_input=step.tokens_input,
        tokens_output=step.tokens_output,
        uses_tools=_uses_tools(step),
        score=verdict_data.get("score"),
    )
    return Recommendation(
        workspace_id=workspace_id,
        kind=TRACE_JUDGMENT_KIND,
        subject_id=step.id,
        recommendation={
            "run_id": run_id,
            "step_id": step.id,
            "model_name": step.model_name,
            "latency_ms": step.latency_ms,
            "cost_usd": step.cost_usd,
            "tokens_input": step.tokens_input,
            "tokens_output": step.tokens_output,
            "score": verdict_data.get("score"),
            "verdict": verdict_data.get("verdict"),
            "issues": verdict_data.get("issues"),
            "suggested_fix": verdict_data.get("suggested_fix"),
            "recommended_model": recommended_model,
            "recommended_model_reason": model_reason,
            "provider_is_real_llm": isinstance(provider, (AnthropicProvider, OpenRouterProvider)),
        },
        reasoning=verdict_data.get("suggested_fix") or "",
        evidence_ids=[step.id],
    )


async def record_trace_judgment(repository: Any, workspace_id: str | None, run_id: str, step: TraceStep) -> None:
    """Never lets a judgment failure propagate — mirrors StepRecorder's
    own "a failed *recording* must never break the agent" rule."""
    try:
        recommendation = await build_trace_judgment(workspace_id=workspace_id, run_id=run_id, step=step)
        await repository.save_recommendation(recommendation)
    except Exception:  # noqa: BLE001 - judging is best-effort, never fatal
        logger.exception(
            "agentguard.evaluation.trace_judge: failed to judge trace step %s for run %s", step.id, run_id
        )
