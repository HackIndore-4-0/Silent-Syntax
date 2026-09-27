"""wrap_llm_client — vendor-agnostic LLM call tracing + token/cost
bookkeeping.

Patches known call methods on an SDK client instance (OpenAI-shaped:
client.chat.completions.create; Anthropic-shaped: client.messages.create)
so every call automatically records a TraceStep(kind="llm_call") via the
same core @traceable uses, with built-in duck-typed response extractors
or a caller-supplied `extract` callable. Never imports openai/anthropic
types — pure attribute/shape inspection at call time, matching
agentguard/llm/provider.py's existing "never require an SDK just to
reference it" convention.
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Callable

from .. import context as run_context
from . import _pending
from .context import reset_current_step, set_current_step
from .recording import StepRecorder, require_run, traced_call

ExtractFn = Callable[[Any], "dict[str, Any] | None"]


def _try_parse_json(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw  # not JSON — return the raw string rather than lose it


def _extract_openai_shaped(response: Any) -> dict[str, Any] | None:
    usage = getattr(response, "usage", None)
    choices = getattr(response, "choices", None)
    if usage is None or not choices:
        return None
    text = None
    tool_calls: list[dict[str, Any]] | None = None
    try:
        message = choices[0].message
        text = message.content
        raw_tool_calls = getattr(message, "tool_calls", None)
        if raw_tool_calls:
            tool_calls = []
            for tc in raw_tool_calls:
                fn = getattr(tc, "function", None)
                tool_calls.append(
                    {
                        "id": getattr(tc, "id", None),
                        "name": getattr(fn, "name", None) if fn else None,
                        "arguments": _try_parse_json(getattr(fn, "arguments", None)) if fn else None,
                    }
                )
    except Exception:  # noqa: BLE001 - best-effort extraction, never fatal
        pass
    return {
        "input_tokens": getattr(usage, "prompt_tokens", None),
        "output_tokens": getattr(usage, "completion_tokens", None),
        "text": text,
        "tool_calls": tool_calls,
    }


def _extract_anthropic_shaped(response: Any) -> dict[str, Any] | None:
    usage = getattr(response, "usage", None)
    content = getattr(response, "content", None)
    if usage is None or not content:
        return None
    text = None
    tool_calls: list[dict[str, Any]] | None = None
    try:
        text_blocks = [b.text for b in content if getattr(b, "type", None) == "text"]
        text = "\n".join(text_blocks) if text_blocks else None
        tool_use_blocks = [b for b in content if getattr(b, "type", None) == "tool_use"]
        if tool_use_blocks:
            tool_calls = [
                {"id": getattr(b, "id", None), "name": getattr(b, "name", None), "arguments": getattr(b, "input", None)}
                for b in tool_use_blocks
            ]
    except Exception:  # noqa: BLE001 - best-effort extraction, never fatal
        pass
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "text": text,
        "tool_calls": tool_calls,
    }


_BUILTIN_EXTRACTORS: "list[ExtractFn]" = [_extract_openai_shaped, _extract_anthropic_shaped]

# (dotted name for TraceStep.name, attribute path to patch)
_KNOWN_CALL_PATHS: "list[tuple[str, list[str]]]" = [
    ("chat.completions.create", ["chat", "completions", "create"]),
    ("messages.create", ["messages", "create"]),
]


def wrap_llm_client(client: Any, *, extract: ExtractFn | None = None) -> Any:
    """Patch `client`'s known call method(s) in place and return it
    (mutated, not copied — callers keep using the same object)."""
    for call_name, path in _KNOWN_CALL_PATHS:
        obj = client
        found = True
        for attr in path[:-1]:
            obj = getattr(obj, attr, None)
            if obj is None:
                found = False
                break
        if not found or not hasattr(obj, path[-1]):
            continue
        original = getattr(obj, path[-1])
        if getattr(original, "_agentguard_traced", False):
            continue  # idempotent double-wrap guard
        setattr(obj, path[-1], _make_traced_call(original, call_name, extract))
    return client


def _extract_usage(response: Any, extract: ExtractFn | None) -> dict[str, Any] | None:
    if extract is not None:
        result = extract(response)
        if result is not None:
            return result
    for fn in _BUILTIN_EXTRACTORS:
        result = fn(response)
        if result is not None:
            return result
    return None


def _build_output(extracted: dict[str, Any] | None) -> Any:
    """The value recorded as TraceStep.output. Plain text when that's
    all there is (unchanged from before — every existing caller/test
    that expects a bare string keeps working), else a small dict
    surfacing the model's own tool-call/tool-use decision (name +
    arguments) alongside any text, since that decision is otherwise
    invisible in the trace until the caller's own code acts on it."""
    if extracted is None:
        return None
    tool_calls = extracted.get("tool_calls")
    if tool_calls:
        return {"text": extracted.get("text"), "tool_calls": tool_calls}
    return extracted.get("text")


def _record_tokens(extracted: dict[str, Any] | None) -> tuple[int | None, int | None, float | None]:
    """record_tokens() is plain synchronous code (agentguard/context.py)
    — no async/await needed here in either the sync or async call path."""
    if extracted is None:
        return None, None, None
    tokens_input = extracted.get("input_tokens")
    tokens_output = extracted.get("output_tokens")
    cost_usd = extracted.get("cost_usd")
    if tokens_input is not None or tokens_output is not None or cost_usd is not None:
        try:
            run_context.record_tokens(input_tokens=tokens_input, output_tokens=tokens_output, cost_usd=cost_usd)
        except RuntimeError:
            pass  # no active run somehow slipped through require_run() — never crash on bookkeeping
    return tokens_input, tokens_output, cost_usd


def _make_traced_call(original: Callable[..., Any], call_name: str, extract: ExtractFn | None) -> Callable[..., Any]:
    is_coroutine = inspect.iscoroutinefunction(original)

    if is_coroutine:

        async def async_call(*args: Any, **kwargs: Any) -> Any:
            call_input = {"args": list(args), "kwargs": kwargs}
            async with traced_call("llm_call", call_name, call_input) as step:
                try:
                    response = await original(*args, **kwargs)
                except Exception as exc:
                    await step.finish_failure(exc)
                    raise
                extracted = _extract_usage(response, extract)
                tokens_input, tokens_output, cost_usd = _record_tokens(extracted)
                await step.finish_success(
                    _build_output(extracted),
                    tokens_input=tokens_input,
                    tokens_output=tokens_output,
                    cost_usd=cost_usd,
                )
                return response

        async_call._agentguard_traced = True  # type: ignore[attr-defined]
        return async_call

    def sync_call(*args: Any, **kwargs: Any) -> Any:
        call_input = {"args": list(args), "kwargs": kwargs}
        ctx = require_run()
        recorder = StepRecorder(ctx, "llm_call", call_name, call_input)
        token = set_current_step(recorder.step_id)
        try:
            from ..otel.instrumentation import start_trace_step_span

            with start_trace_step_span(call_name, "llm_call"):
                try:
                    response = original(*args, **kwargs)
                except Exception as exc:
                    _pending.schedule(recorder.finish_failure(exc))
                    raise
                extracted = _extract_usage(response, extract)
                tokens_input, tokens_output, cost_usd = _record_tokens(extracted)
                _pending.schedule(
                    recorder.finish_success(
                        _build_output(extracted),
                        tokens_input=tokens_input,
                        tokens_output=tokens_output,
                        cost_usd=cost_usd,
                    )
                )
                return response
        finally:
            reset_current_step(token)

    sync_call._agentguard_traced = True  # type: ignore[attr-defined]
    return sync_call
