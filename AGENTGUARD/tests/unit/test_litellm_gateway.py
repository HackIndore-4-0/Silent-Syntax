"""LLM Gateway (agentguard/tracing/litellm_wrap.py) — provider-agnostic
traced_completion()/traced_acompletion(), built on real litellm calls
via `mock_response`/`mock_tool_calls` (no network, no fakes needed —
litellm's own mock machinery gives a fully realistic ModelResponse)."""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("litellm")

import agentguard
from agentguard import LLMGatewayPolicy, ModelAlternative, Policy, monitor
from agentguard.errors import HumanRejected, LLMGatewayViolation
from agentguard.reliability.model_profile import ModelProfileEngine, build_model_profile
from agentguard.tracing import traced_acompletion, traced_completion

from ..fakes import InMemoryRunRepository


@pytest.fixture(autouse=True)
def fake_repository():
    from agentguard._runtime import reset as reset_repository
    from agentguard.human.broker import reset_broker

    repo = InMemoryRunRepository()
    agentguard.configure(repo)
    reset_broker()
    yield repo
    reset_repository()
    reset_broker()


def _trace_events(events, event_type):
    return [e for e in events if e["event_type"] == event_type]


async def test_default_behavior_unchanged_without_gateway_policy(fake_repository):
    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        response = await traced_acompletion(
            model="gpt-3.5-turbo", messages=[{"role": "user", "content": "hi"}], mock_response="hey there"
        )
        return response.choices[0].message.content

    result = await agent("task")
    assert result == "hey there"

    run_id = next(iter(fake_repository.runs))
    steps = await fake_repository.list_trace_steps_for_run(run_id)
    assert len(steps) == 1
    assert steps[0]["kind"] == "llm_call"
    assert steps[0]["model_name"] == "gpt-3.5-turbo"
    assert steps[0]["tokens_input"] == 10
    assert steps[0]["tokens_output"] == 20
    assert steps[0]["cost_usd"] is not None and steps[0]["cost_usd"] > 0
    assert steps[0]["latency_ms"] >= 0
    assert steps[0]["output"] == "hey there"


async def test_allowed_models_violation_blocks_before_the_call(fake_repository):
    @monitor(policy=Policy(max_cost=60000, llm_gateway=LLMGatewayPolicy(allowed_models=["gpt-4o-mini"])), llm_judge=False)
    async def agent(task: str) -> str:
        response = await traced_acompletion(
            model="gpt-3.5-turbo", messages=[{"role": "user", "content": "hi"}], mock_response="hey"
        )
        return response.choices[0].message.content

    with pytest.raises(LLMGatewayViolation, match="allowed_models"):
        await agent("task")

    run_id = next(iter(fake_repository.runs))
    steps = await fake_repository.list_trace_steps_for_run(run_id)
    assert steps == []  # blocked before any TraceStep was ever opened


async def test_allowed_model_passes_through(fake_repository):
    @monitor(policy=Policy(max_cost=60000, llm_gateway=LLMGatewayPolicy(allowed_models=["gpt-3.5-turbo"])), llm_judge=False)
    async def agent(task: str) -> str:
        response = await traced_acompletion(
            model="gpt-3.5-turbo", messages=[{"role": "user", "content": "hi"}], mock_response="hey"
        )
        return response.choices[0].message.content

    result = await agent("task")
    assert result == "hey"


async def test_max_tokens_per_call_violation_blocks(fake_repository):
    @monitor(policy=Policy(max_cost=60000, llm_gateway=LLMGatewayPolicy(max_tokens_per_call=100)), llm_judge=False)
    async def agent(task: str) -> str:
        return await traced_acompletion(
            model="gpt-3.5-turbo", messages=[{"role": "user", "content": "hi"}], max_tokens=5000, mock_response="hey"
        )

    with pytest.raises(LLMGatewayViolation, match="max_tokens_per_call"):
        await agent("task")


async def test_tool_call_captured_in_output(fake_repository):
    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str):
        return await traced_acompletion(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "charge the customer"}],
            mock_response="",
            mock_tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "charge_customer", "arguments": '{"amount": 500}'}}],
        )

    await agent("task")

    run_id = next(iter(fake_repository.runs))
    steps = await fake_repository.list_trace_steps_for_run(run_id)
    output = steps[0]["output"]
    assert output["tool_calls"] == [{"id": "call_1", "name": "charge_customer", "arguments": '{"amount": 500}'}]


async def test_fallback_engages_on_failure(fake_repository):
    gateway = LLMGatewayPolicy(
        fallback_chain=[ModelAlternative(primary_model="gpt-3.5-turbo", fallback_model="gpt-4o-mini", reliability_threshold=0.8)]
    )

    @monitor(policy=Policy(max_cost=60000, llm_gateway=gateway), llm_judge=False)
    async def agent(task: str) -> str:
        response = await traced_acompletion(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "hi"}],
            mock_response="litellm.InternalServerError",
        )
        return response.choices[0].message.content

    # the fallback model itself must succeed for this to resolve
    import litellm

    original_acompletion = litellm.acompletion

    async def patched_acompletion(**kwargs):
        if kwargs.get("model") == "gpt-4o-mini":
            kwargs["mock_response"] = "fallback response"
        return await original_acompletion(**kwargs)

    litellm.acompletion = patched_acompletion
    try:
        result = await agent("task")
    finally:
        litellm.acompletion = original_acompletion

    assert result == "fallback response"

    run_id = next(iter(fake_repository.runs))
    events = await fake_repository.list_audit_events(run_id)
    substitutions = _trace_events(events, "MODEL_SUBSTITUTED")
    assert len(substitutions) == 1
    assert substitutions[0]["payload"]["primary"] == "gpt-3.5-turbo"
    assert substitutions[0]["payload"]["fallback"] == "gpt-4o-mini"

    steps = await fake_repository.list_trace_steps_for_run(run_id)
    assert steps[0]["model_name"] == "gpt-4o-mini"
    assert steps[0]["outcome"] == "success"


async def test_fallback_skipped_when_primary_profile_is_reliable(fake_repository):
    from agentguard.models import TraceStep

    # Seed 5 prior successful calls for gpt-3.5-turbo so its profile is RELIABLE.
    for _ in range(5):
        await fake_repository.save_trace_step(
            TraceStep(run_id="seed-run", kind="llm_call", name="x", outcome="success", model_name="gpt-3.5-turbo", latency_ms=10)
        )

    gateway = LLMGatewayPolicy(
        fallback_chain=[ModelAlternative(primary_model="gpt-3.5-turbo", fallback_model="gpt-4o-mini", reliability_threshold=0.8)]
    )

    @monitor(policy=Policy(max_cost=60000, llm_gateway=gateway), llm_judge=False)
    async def agent(task: str) -> str:
        return await traced_acompletion(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": "hi"}],
            mock_response="litellm.InternalServerError",
        )

    with pytest.raises(Exception):
        await agent("task")

    run_id = next(iter(fake_repository.runs))
    events = await fake_repository.list_audit_events(run_id)
    assert _trace_events(events, "MODEL_SUBSTITUTED") == []


async def test_on_violation_human_approve_allows_call(fake_repository):
    from agentguard.human.broker import get_broker

    gateway = LLMGatewayPolicy(allowed_models=["gpt-4o-mini"], on_violation="human")
    run_id_holder: dict = {}

    @monitor(policy=Policy(max_cost=60000, llm_gateway=gateway), llm_judge=False)
    async def agent(task: str) -> str:
        run_id_holder["run_id"] = agentguard.context.current_run().run.id
        response = await traced_acompletion(
            model="gpt-3.5-turbo", messages=[{"role": "user", "content": "hi"}], mock_response="hey"
        )
        return response.choices[0].message.content

    async def _resolve_soon():
        for _ in range(50):
            await asyncio.sleep(0.005)
            run_id = run_id_holder.get("run_id")
            if run_id:
                broker = get_broker()
                pending = broker.get_pending(run_id)
                if pending:
                    broker.resolve(pending[0].id, "approved", resolved_by="reviewer@example.com")
                    return
        raise AssertionError("approval request was never published")

    resolver = asyncio.create_task(_resolve_soon())
    result = await agent("task")
    await resolver
    assert result == "hey"


async def test_on_violation_human_reject_blocks_call(fake_repository):
    from agentguard.human.broker import get_broker

    gateway = LLMGatewayPolicy(allowed_models=["gpt-4o-mini"], on_violation="human")
    run_id_holder: dict = {}

    @monitor(policy=Policy(max_cost=60000, llm_gateway=gateway), llm_judge=False)
    async def agent(task: str) -> str:
        run_id_holder["run_id"] = agentguard.context.current_run().run.id
        return await traced_acompletion(model="gpt-3.5-turbo", messages=[{"role": "user", "content": "hi"}], mock_response="hey")

    async def _resolve_soon():
        for _ in range(50):
            await asyncio.sleep(0.005)
            run_id = run_id_holder.get("run_id")
            if run_id:
                broker = get_broker()
                pending = broker.get_pending(run_id)
                if pending:
                    broker.resolve(pending[0].id, "rejected", resolved_by="reviewer@example.com")
                    return
        raise AssertionError("approval request was never published")

    resolver = asyncio.create_task(_resolve_soon())
    with pytest.raises(HumanRejected):
        await agent("task")
    await resolver


async def test_cost_ceiling_reports_but_never_blocks(fake_repository):
    gateway = LLMGatewayPolicy(max_cost_per_call_usd=0.0000001)  # any real cost will exceed this

    @monitor(policy=Policy(max_cost=60000, llm_gateway=gateway), llm_judge=False)
    async def agent(task: str) -> str:
        response = await traced_acompletion(
            model="gpt-3.5-turbo", messages=[{"role": "user", "content": "hi"}], mock_response="hey"
        )
        return response.choices[0].message.content

    result = await agent("task")
    assert result == "hey"  # never blocked

    run_id = next(iter(fake_repository.runs))
    events = await fake_repository.list_audit_events(run_id)
    exceeded = _trace_events(events, "LLM_GATEWAY_COST_EXCEEDED")
    assert len(exceeded) == 1
    assert exceeded[0]["payload"]["model"] == "gpt-3.5-turbo"


def test_model_profile_insufficient_data_below_min_sample_size():
    profile = build_model_profile("gpt-4o-mini", [])
    assert profile.reliability == "INSUFFICIENT_DATA"
    assert profile.success_rate is None


def test_model_profile_classification_boundaries():
    events = [{"outcome": "success", "latency_ms": 100.0} for _ in range(10)]
    profile = build_model_profile("gpt-4o-mini", events)
    assert profile.reliability == "RELIABLE"
    assert profile.success_rate == 1.0

    mixed = [{"outcome": "success", "latency_ms": 100.0} for _ in range(8)] + [
        {"outcome": "failure", "latency_ms": 100.0} for _ in range(2)
    ]
    profile = build_model_profile("gpt-4o-mini", mixed)
    assert profile.reliability == "DEGRADED"

    unreliable = [{"outcome": "success", "latency_ms": 100.0} for _ in range(5)] + [
        {"outcome": "failure", "latency_ms": 100.0} for _ in range(5)
    ]
    profile = build_model_profile("gpt-4o-mini", unreliable)
    assert profile.reliability == "UNRELIABLE"


def test_sync_traced_completion_records_a_trace_step(fake_repository):
    """A plain (non-async) test function: a sync @monitor agent's own
    asyncio.run() must not be called from inside an already-running
    loop, so this must NOT be an `async def` test (pytest-asyncio would
    already have one running) — same rule as Plan A's
    test_sync_function_inside_sync_monitor_agent."""

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    def agent(task: str) -> str:
        response = traced_completion(model="gpt-3.5-turbo", messages=[{"role": "user", "content": "hi"}], mock_response="hey")
        return response.choices[0].message.content

    result = agent("task")
    assert result == "hey"

    run_id = next(iter(fake_repository.runs))
    steps = fake_repository.trace_steps
    matching = [s for s in steps if s.run_id == run_id]
    assert len(matching) == 1
    assert matching[0].model_name == "gpt-3.5-turbo"
    assert matching[0].outcome == "success"
