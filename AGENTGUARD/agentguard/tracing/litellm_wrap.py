"""LLM Gateway — provider-agnostic LLM call tracing, cost/latency
tracking, policy enforcement, and reliability-gated fallback, built on
LiteLLM (PyPI `litellm`, pyproject.toml's `litellm` extra).

Why LiteLLM, and why NOT its own callback system: LiteLLM's
`completion`/`acompletion` normalize every supported provider (OpenAI,
Anthropic, Bedrock, Vertex, Azure, ...) into one uniform
`ModelResponse` shape (`.model`, `.usage.prompt_tokens/completion_tokens`,
`.choices[0].message.content/.tool_calls`), and `litellm.completion_cost()`
gives a real, provider-aware cost figure — solving "any LLM provider,
with accurate cost" in one place, no per-vendor duck-typing needed.

LiteLLM also ships a `CustomLogger` callback system built exactly for
this kind of observability integration — but it was NOT used here,
verified empirically (not assumed) while building this: registered
callbacks dispatch on a background worker/queue, asynchronously and
sometimes considerably later than the call itself (confirmed: a
callback for a call made inside one `asyncio.run()` can fire during a
*later, unrelated* `asyncio.run()`, logging a "event loop changed"
warning). By the time a callback fires, the ambient RunContext/
current-step contextvars this module's `traced_call()` depends on may
no longer be in scope, breaking correct parent_step_id nesting and the
"requires an active run" guarantee. So `traced_completion`/
`traced_acompletion` below call `litellm.completion`/`acompletion`
directly and do all recording INLINE, right after the call returns —
exactly the same architecture wrap_llm_client already uses, just fed
by litellm's uniform response shape instead of vendor-specific
duck-typed extractors.

Gateway policy (Policy.llm_gateway, agentguard/models.py's
LLMGatewayPolicy): allowed_models / max_tokens_per_call are checked
BEFORE the call (deterministic, no I/O — same precedent as
forbidden_actions). max_cost_per_call_usd can only be checked AFTER
the call (real cost is unknowable before the provider responds) and is
therefore only ever reported (an audit event), never used to block a
call that already happened and was already paid for. Fallback-on-failure
mirrors call_tool()'s own reliability-gated single-hop fallback almost
exactly, using ModelProfileEngine instead of ToolProfileEngine — and
needs no separate "client registry" the way wrap_llm_client's
vendor-specific design would: a litellm fallback is just a different
`model=` string, litellm itself resolves the provider.
"""
from __future__ import annotations

import time
from typing import Any

from .. import context as run_context
from . import _pending
from .context import reset_current_step, set_current_step
from .recording import StepRecorder, require_run, traced_call


def _extract_output_and_usage(response: Any) -> dict[str, Any]:
    """Uniform across every provider litellm supports — no per-vendor
    branching needed, unlike wrap_llm_client's duck-typed extractors."""
    message = response.choices[0].message
    text = getattr(message, "content", None)
    tool_calls = None
    raw_tool_calls = getattr(message, "tool_calls", None)
    if raw_tool_calls:
        tool_calls = [
            {
                "id": getattr(tc, "id", None),
                "name": getattr(tc.function, "name", None) if getattr(tc, "function", None) else None,
                "arguments": getattr(tc.function, "arguments", None) if getattr(tc, "function", None) else None,
            }
            for tc in raw_tool_calls
        ]
    usage = getattr(response, "usage", None)
    return {
        "text": text,
        "tool_calls": tool_calls,
        "tokens_input": getattr(usage, "prompt_tokens", None) if usage else None,
        "tokens_output": getattr(usage, "completion_tokens", None) if usage else None,
        "model_name": getattr(response, "model", None),
    }


def _build_output(extracted: dict[str, Any]) -> Any:
    if extracted.get("tool_calls"):
        return {"text": extracted.get("text"), "tool_calls": extracted["tool_calls"]}
    return extracted.get("text")


def _compute_cost(response: Any) -> float | None:
    try:
        import litellm

        return litellm.completion_cost(completion_response=response)
    except Exception:  # noqa: BLE001 - cost is best-effort, never fatal
        return None


def _check_gateway_policy(gw: "Any | None", call_kwargs: dict[str, Any]) -> None:
    """Pre-call checks only (allowed_models / max_tokens_per_call) —
    deterministic, no I/O, always safe to call from sync or async code.
    Raises LLMGatewayViolation on violation regardless of
    Policy.llm_gateway.on_violation from the sync path (there is no
    event loop there to block a "human" escalation on — see
    _check_gateway_policy_async for the async, on_violation-aware
    version)."""
    from ..errors import LLMGatewayViolation

    if gw is None:
        return
    model = call_kwargs.get("model")
    if gw.allowed_models and model not in gw.allowed_models:
        raise LLMGatewayViolation("allowed_models", f"model {model!r} is not in the allowed_models list")
    requested_max_tokens = call_kwargs.get("max_tokens")
    if gw.max_tokens_per_call is not None and requested_max_tokens is not None and requested_max_tokens > gw.max_tokens_per_call:
        raise LLMGatewayViolation(
            "max_tokens_per_call", f"requested max_tokens={requested_max_tokens} exceeds ceiling {gw.max_tokens_per_call}"
        )


async def _check_gateway_policy_async(gw: "Any | None", call_kwargs: dict[str, Any]) -> None:
    """Same checks as _check_gateway_policy, but on_violation="human"
    is honored here (async call path only) by routing through the
    existing perform_action()/request_approval() broker."""
    from ..errors import LLMGatewayViolation

    if gw is None:
        return
    model = call_kwargs.get("model")
    violation: tuple[str, str] | None = None
    if gw.allowed_models and model not in gw.allowed_models:
        violation = ("allowed_models", f"model {model!r} is not in the allowed_models list")
    else:
        requested_max_tokens = call_kwargs.get("max_tokens")
        if gw.max_tokens_per_call is not None and requested_max_tokens is not None and requested_max_tokens > gw.max_tokens_per_call:
            violation = (
                "max_tokens_per_call",
                f"requested max_tokens={requested_max_tokens} exceeds ceiling {gw.max_tokens_per_call}",
            )
    if violation is None:
        return
    rule, detail = violation
    if gw.on_violation == "human":
        await run_context.request_approval(f"llm_gateway:{rule}", reason=detail, evidence={"model": model})
        return
    raise LLMGatewayViolation(rule, detail)


def _record_tokens(extracted: dict[str, Any], cost_usd: float | None) -> None:
    """Mirrors llm_wrap.py's _record_tokens: reports usage onto the
    active Run (agentguard/context.py's record_tokens) so Dashboard
    V2's Tokens & Cost page (which reads Run.tokens_input/output/
    estimated_cost_usd, not TraceStep) sees calls made through this
    LiteLLM gateway too. Without this, every traced_completion/
    traced_acompletion call recorded its usage on the TraceStep only,
    and the Tokens & Cost page stayed permanently empty for this path."""
    tokens_input = extracted.get("tokens_input")
    tokens_output = extracted.get("tokens_output")
    model_name = extracted.get("model_name")
    if tokens_input is None and tokens_output is None and cost_usd is None:
        return
    try:
        run_context.record_tokens(
            model_name=model_name, input_tokens=tokens_input, output_tokens=tokens_output, cost_usd=cost_usd,
        )
    except RuntimeError:
        pass  # no active run somehow slipped through require_run() — never crash on bookkeeping


def _check_cost_after_call(ctx: run_context.RunContext, gw: "Any | None", cost_usd: float | None, model: str | None) -> None:
    """Post-call only: real cost is unknowable before the provider
    responds, so a ceiling here can only ever be reported (an audit
    event) — never used to block a call that already happened and was
    already paid for."""
    if gw is None or gw.max_cost_per_call_usd is None or cost_usd is None:
        return
    if cost_usd > gw.max_cost_per_call_usd:
        try:
            ctx.record_event(
                "LLM_GATEWAY_COST_EXCEEDED",
                {"model": model, "cost_usd": cost_usd, "ceiling_usd": gw.max_cost_per_call_usd},
            )
        except Exception:  # noqa: BLE001 - never let bookkeeping crash the agent
            pass


async def _resolve_fallback(repository: Any, gw: "Any | None", model: str) -> "Any | None":
    """The single ModelAlternative to try, or None — mirrors
    call_tool()'s should_fall_back gate exactly, using
    ModelProfileEngine in place of ToolProfileEngine."""
    if gw is None:
        return None
    alt = next((a for a in gw.fallback_chain if a.primary_model == model), None)
    if alt is None:
        return None
    from ..reliability.model_profile import ModelProfileEngine

    profile = await ModelProfileEngine(repository).profile(model)
    should_fall_back = profile.reliability == "INSUFFICIENT_DATA" or (
        profile.success_rate is not None and profile.success_rate < alt.reliability_threshold
    )
    return alt if should_fall_back else None


async def traced_acompletion(**kwargs: Any) -> Any:
    """Async, provider-agnostic traced LLM call: `await
    traced_acompletion(model="gpt-4o-mini", messages=[...])` — a
    drop-in for `litellm.acompletion(...)`, fully instrumented."""
    import litellm

    ctx = require_run()
    gw = ctx.run.policy.llm_gateway
    await _check_gateway_policy_async(gw, kwargs)

    model = kwargs.get("model")
    async with traced_call("llm_call", model or "litellm.acompletion", {"kwargs": kwargs}) as step:
        try:
            response = await litellm.acompletion(**kwargs)
        except Exception as exc:
            from .._runtime import get_repository

            alt = await _resolve_fallback(get_repository(), gw, model) if model else None
            if alt is not None:
                fallback_kwargs = {**kwargs, "model": alt.fallback_model}
                try:
                    response = await litellm.acompletion(**fallback_kwargs)
                except Exception as fallback_exc:
                    await step.finish_failure(fallback_exc, model_name=alt.fallback_model)
                    raise
                ctx.record_event(
                    "MODEL_SUBSTITUTED",
                    {"primary": model, "fallback": alt.fallback_model, "reason": str(exc)},
                )
                extracted = _extract_output_and_usage(response)
                cost_usd = _compute_cost(response)
                _check_cost_after_call(ctx, gw, cost_usd, extracted["model_name"])
                _record_tokens(extracted, cost_usd)
                await step.finish_success(
                    _build_output(extracted),
                    tokens_input=extracted["tokens_input"],
                    tokens_output=extracted["tokens_output"],
                    cost_usd=cost_usd,
                    model_name=extracted["model_name"],
                )
                return response
            await step.finish_failure(exc, model_name=model)
            raise

        extracted = _extract_output_and_usage(response)
        cost_usd = _compute_cost(response)
        _check_cost_after_call(ctx, gw, cost_usd, extracted["model_name"])
        _record_tokens(extracted, cost_usd)
        await step.finish_success(
            _build_output(extracted),
            tokens_input=extracted["tokens_input"],
            tokens_output=extracted["tokens_output"],
            cost_usd=cost_usd,
            model_name=extracted["model_name"],
        )
        return response


def traced_completion(**kwargs: Any) -> Any:
    """Sync, provider-agnostic traced LLM call: `traced_completion(model="gpt-4o-mini",
    messages=[...])` — a drop-in for `litellm.completion(...)`, fully
    instrumented. Only Policy.llm_gateway.on_violation="raise" is
    honored here (see module docstring's design note on the sync path:
    no event loop is available to block a "human" escalation on)."""
    import litellm

    ctx = require_run()
    gw = ctx.run.policy.llm_gateway
    _check_gateway_policy(gw, kwargs)

    model = kwargs.get("model")
    recorder = StepRecorder(ctx, "llm_call", model or "litellm.completion", {"kwargs": kwargs})
    token = set_current_step(recorder.step_id)
    try:
        from ..otel.instrumentation import start_trace_step_span

        with start_trace_step_span(recorder.name, "llm_call"):
            try:
                response = litellm.completion(**kwargs)
            except Exception as exc:
                alt = _resolve_fallback_sync(gw, model)
                if alt is not None:
                    fallback_kwargs = {**kwargs, "model": alt.fallback_model}
                    try:
                        response = litellm.completion(**fallback_kwargs)
                    except Exception as fallback_exc:
                        _pending.schedule(recorder.finish_failure(fallback_exc, model_name=alt.fallback_model))
                        raise
                    ctx.record_event(
                        "MODEL_SUBSTITUTED",
                        {"primary": model, "fallback": alt.fallback_model, "reason": str(exc)},
                    )
                    extracted = _extract_output_and_usage(response)
                    cost_usd = _compute_cost(response)
                    _check_cost_after_call(ctx, gw, cost_usd, extracted["model_name"])
                    _record_tokens(extracted, cost_usd)
                    _pending.schedule(
                        recorder.finish_success(
                            _build_output(extracted),
                            tokens_input=extracted["tokens_input"],
                            tokens_output=extracted["tokens_output"],
                            cost_usd=cost_usd,
                            model_name=extracted["model_name"],
                        )
                    )
                    return response
                _pending.schedule(recorder.finish_failure(exc, model_name=model))
                raise

            extracted = _extract_output_and_usage(response)
            cost_usd = _compute_cost(response)
            _check_cost_after_call(ctx, gw, cost_usd, extracted["model_name"])
            _record_tokens(extracted, cost_usd)
            _pending.schedule(
                recorder.finish_success(
                    _build_output(extracted),
                    tokens_input=extracted["tokens_input"],
                    tokens_output=extracted["tokens_output"],
                    cost_usd=cost_usd,
                    model_name=extracted["model_name"],
                )
            )
            return response
    finally:
        reset_current_step(token)


def _resolve_fallback_sync(gw: "Any | None", model: str | None) -> "Any | None":
    """Sync path cannot await ModelProfileEngine's repository read, so
    the reliability GATE is skipped here — a registered alternative is
    always tried on failure (fail-open toward availability, not
    reliability-gated, for the sync call path only). Documented
    limitation, not silently different behavior: the async path
    (traced_acompletion) is reliability-gated exactly like call_tool()."""
    if gw is None or model is None:
        return None
    return next((a for a in gw.fallback_chain if a.primary_model == model), None)


DEFAULT_BENCHMARK_TIMEOUT_S = 60.0


def _build_benchmark_messages(case: Any) -> list[dict[str, str]]:
    """Regression fix: this used to send ONLY `case.input` — for a RAG
    case, that strips the retrieval_context out entirely, so the
    candidate model answers with zero grounding while faithfulness/
    contextual_* evaluators still score it as if it HAD the source
    run's context. That guarantees low/"wrong" scores that reflect the
    benchmark harness's missing context, not the candidate model's real
    quality. Reconstructing the same context the source run had makes
    the candidate model's response comparable to the original."""
    context = getattr(case, "retrieval_context", None)
    if not context:
        return [{"role": "user", "content": str(case.input)}]
    context_block = "\n\n".join(str(c) for c in context)
    content = (
        f"Context:\n{context_block}\n\n"
        f"Question: {case.input}\n\n"
        "Answer using only the information in the context above."
    )
    return [{"role": "user", "content": content}]


def make_benchmark_model_call_fn(*, timeout_s: float = DEFAULT_BENCHMARK_TIMEOUT_S) -> Any:
    """A bare `litellm.acompletion` wrapper for ModelBenchmarkEngine's
    ModelCallFn (agentguard/evaluation/benchmark.py) — deliberately NOT
    traced_acompletion above. A benchmark experiment calls N candidate
    models outside any @monitor'd run, so there is no RunContext for
    require_run()/traced_call() to attach to, no gateway policy to
    enforce, and nothing to fall back from (the whole point is to
    observe how the candidate model itself performs). It reuses this
    module's own _extract_output_and_usage/_compute_cost/_build_output
    so cost/token extraction stays identical to the traced call path.

    `timeout_s` bounds one call's worst-case latency (litellm passes it
    straight through to the underlying provider client) -- a single
    hung provider request must not stall an entire benchmark run.
    ModelBenchmarkEngine._safe_model_call() catches the resulting
    litellm.Timeout the same way it catches any other provider error."""
    from ..evaluation.benchmark import ModelCallResult

    async def call(model: str, case: Any) -> ModelCallResult:
        import litellm

        messages = _build_benchmark_messages(case)
        started = time.monotonic()
        response = await litellm.acompletion(model=model, messages=messages, timeout=timeout_s)
        latency_ms = (time.monotonic() - started) * 1000.0

        extracted = _extract_output_and_usage(response)
        cost_usd = _compute_cost(response)
        return ModelCallResult(
            actual_output=_build_output(extracted),
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            tokens_input=extracted.get("tokens_input"),
        )

    return call
