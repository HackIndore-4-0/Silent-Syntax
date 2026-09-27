"""TraceStep tracing — @traceable and wrap_llm_client (agentguard/tracing/).

Covers: basic capture, sync-function support, exception/traceback/code-
location capture with unchanged re-raise, parent/child nesting (within
@traceable and across @traceable/LLM calls), wrap_llm_client's built-in
OpenAI-shaped/Anthropic-shaped extractors and a caller-supplied
override, the outside-a-run RuntimeError, trace-write-failure
swallowing, and hash-chain well-formedness.
"""
from __future__ import annotations

import logging

import pytest

import agentguard
from agentguard import Policy, monitor, traceable, wrap_llm_client
from agentguard.audit.chain import verify_audit_chain

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


def _trace_events(events: list[dict], event_type: str) -> list[dict]:
    return [e for e in events if e["event_type"] == event_type]


async def test_basic_capture_async(fake_repository):
    @traceable
    async def step(x: int) -> int:
        return x * 2

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> int:
        return await step(21)

    result = await agent("task")
    assert result == 42

    run_id = next(iter(fake_repository.runs))
    steps = await fake_repository.list_trace_steps_for_run(run_id)
    assert len(steps) == 1
    assert steps[0]["kind"] == "function"
    assert steps[0]["outcome"] == "success"
    assert steps[0]["output"] == 42
    assert steps[0]["input"] == {"args": [21], "kwargs": {}}
    assert steps[0]["latency_ms"] >= 0

    events = await fake_repository.list_audit_events(run_id)
    trace_events = _trace_events(events, "TRACE_STEP")
    assert len(trace_events) == 1


async def test_successful_call_still_captures_a_real_call_site(fake_repository):
    """A step that succeeds (no exception) has no traceback to derive
    code_file/function/lineno from — but the call site (this test
    function, the line that actually invoked the traced call) must
    still be captured, since that's the only code-location evidence
    agentguard.evaluation.diagnose has for a call that ran fine but
    scored badly on a quality metric."""

    @traceable
    async def step(x: int) -> int:
        return x * 2

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> int:
        return await step(21)  # the line this test expects code_lineno to point at

    await agent("task")

    run_id = next(iter(fake_repository.runs))
    steps = await fake_repository.list_trace_steps_for_run(run_id)
    assert steps[0]["outcome"] == "success"
    assert steps[0]["code_file"] == __file__
    assert steps[0]["code_function"] == "agent"
    assert isinstance(steps[0]["code_lineno"], int)


async def test_sync_function_inside_async_monitor_agent(fake_repository):
    @traceable
    def step(x: int) -> int:
        return x + 1

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> int:
        return step(41)

    result = await agent("task")
    assert result == 42

    run_id = next(iter(fake_repository.runs))
    steps = await fake_repository.list_trace_steps_for_run(run_id)
    assert len(steps) == 1
    assert steps[0]["outcome"] == "success"
    assert steps[0]["output"] == 42


def test_sync_function_inside_sync_monitor_agent(fake_repository):
    @traceable
    def step(x: int) -> int:
        return x + 1

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    def agent(task: str) -> int:
        return step(41)

    result = agent("task")
    assert result == 42

    run_id = next(iter(fake_repository.runs))
    steps = fake_repository.trace_steps
    matching = [s for s in steps if s.run_id == run_id]
    assert len(matching) == 1
    assert matching[0].outcome == "success"
    assert matching[0].output == 42


async def test_exception_capture_and_unchanged_reraise(fake_repository):
    @traceable
    async def failing_step() -> None:
        raise ValueError("boom")

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> None:
        await failing_step()

    with pytest.raises(ValueError, match="boom"):
        await agent("task")

    run_id = next(iter(fake_repository.runs))
    steps = await fake_repository.list_trace_steps_for_run(run_id)
    assert len(steps) == 1
    step = steps[0]
    assert step["outcome"] == "failure"
    assert step["exception_type"] == "ValueError"
    assert step["exception_message"] == "boom"
    assert step["traceback_text"]
    assert "boom" in step["traceback_text"]
    assert step["code_file"] == __file__
    assert step["code_function"] == "failing_step"
    assert isinstance(step["code_lineno"], int)


async def test_nesting_between_traceable_calls(fake_repository):
    @traceable
    async def inner(x: int) -> int:
        return x * 2

    @traceable
    async def outer(x: int) -> int:
        return await inner(x) + 1

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> int:
        return await outer(10)

    result = await agent("task")
    assert result == 21

    run_id = next(iter(fake_repository.runs))
    steps = await fake_repository.list_trace_steps_for_run(run_id)
    assert len(steps) == 2
    outer_step = next(s for s in steps if s["name"].endswith("outer"))
    inner_step = next(s for s in steps if s["name"].endswith("inner"))
    assert outer_step["parent_step_id"] is None
    assert inner_step["parent_step_id"] == outer_step["id"]


class _FakeUsage:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeOpenAIResponse:
    def __init__(self, text, prompt_tokens, completion_tokens, model="gpt-4o-mini"):
        self.choices = [_FakeChoice(text)]
        self.usage = _FakeUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        self.model = model


class _FakeOpenAIClient:
    class _Completions:
        async def create(self, **kwargs):
            return _FakeOpenAIResponse("hello from openai-shaped fake", 10, 5)

    class _Chat:
        def __init__(self):
            self.completions = _FakeOpenAIClient._Completions()

    def __init__(self):
        self.chat = _FakeOpenAIClient._Chat()


class _FakeContentBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeAnthropicResponse:
    def __init__(self, text, input_tokens, output_tokens, model="claude-3-5-sonnet-latest"):
        self.content = [_FakeContentBlock(text)]
        self.usage = _FakeUsage(input_tokens=input_tokens, output_tokens=output_tokens)
        self.model = model


class _FakeAnthropicClient:
    class _Messages:
        async def create(self, **kwargs):
            return _FakeAnthropicResponse("hello from anthropic-shaped fake", 7, 3)

    def __init__(self):
        self.messages = _FakeAnthropicClient._Messages()


async def test_wrap_llm_client_openai_shaped(fake_repository):
    client = wrap_llm_client(_FakeOpenAIClient())

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        response = await client.chat.completions.create(model="gpt-4o-mini", messages=[])
        return response.choices[0].message.content

    result = await agent("task")
    assert result == "hello from openai-shaped fake"

    run_id = next(iter(fake_repository.runs))
    steps = await fake_repository.list_trace_steps_for_run(run_id)
    assert len(steps) == 1
    assert steps[0]["kind"] == "llm_call"
    assert steps[0]["tokens_input"] == 10
    assert steps[0]["tokens_output"] == 5
    assert steps[0]["model_name"] == "gpt-4o-mini"  # regression: wrap_llm_client used to never set this
    assert steps[0]["output"] == "hello from openai-shaped fake"  # plain text, unchanged when no tool call

    run = await fake_repository.get_run(run_id)
    assert run["tokens_input"] == 10
    assert run["tokens_output"] == 5


async def test_wrap_llm_client_anthropic_shaped(fake_repository):
    client = wrap_llm_client(_FakeAnthropicClient())

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        response = await client.messages.create(model="claude", messages=[])
        return response.content[0].text

    result = await agent("task")
    assert result == "hello from anthropic-shaped fake"

    run_id = next(iter(fake_repository.runs))
    steps = await fake_repository.list_trace_steps_for_run(run_id)
    assert len(steps) == 1
    assert steps[0]["tokens_input"] == 7
    assert steps[0]["tokens_output"] == 3
    assert steps[0]["model_name"] == "claude-3-5-sonnet-latest"  # regression: wrap_llm_client used to never set this
    assert steps[0]["output"] == "hello from anthropic-shaped fake"  # plain text, unchanged when no tool call


class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.type = "function"
        self.function = _FakeFunction(name, arguments)


class _FakeOpenAIToolCallMessage:
    def __init__(self, tool_calls):
        self.content = None
        self.tool_calls = tool_calls


class _FakeOpenAIToolCallChoice:
    def __init__(self, tool_calls):
        self.message = _FakeOpenAIToolCallMessage(tool_calls)


class _FakeOpenAIToolCallResponse:
    def __init__(self, tool_calls, prompt_tokens, completion_tokens):
        self.choices = [_FakeOpenAIToolCallChoice(tool_calls)]
        self.usage = _FakeUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)


class _FakeOpenAIToolCallClient:
    class _Completions:
        async def create(self, **kwargs):
            tool_calls = [_FakeToolCall("call_1", "charge_customer", '{"amount": 500}')]
            return _FakeOpenAIToolCallResponse(tool_calls, 12, 4)

    class _Chat:
        def __init__(self):
            self.completions = _FakeOpenAIToolCallClient._Completions()

    def __init__(self):
        self.chat = _FakeOpenAIToolCallClient._Chat()


async def test_wrap_llm_client_openai_tool_call_captured(fake_repository):
    client = wrap_llm_client(_FakeOpenAIToolCallClient())

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str):
        return await client.chat.completions.create(model="gpt-4o-mini", messages=[])

    await agent("task")

    run_id = next(iter(fake_repository.runs))
    steps = await fake_repository.list_trace_steps_for_run(run_id)
    assert len(steps) == 1
    output = steps[0]["output"]
    assert output["text"] is None
    assert output["tool_calls"] == [{"id": "call_1", "name": "charge_customer", "arguments": {"amount": 500}}]


class _FakeToolUseBlock:
    def __init__(self, id, name, input):
        self.type = "tool_use"
        self.id = id
        self.name = name
        self.input = input


class _FakeAnthropicToolUseResponse:
    def __init__(self, tool_use_blocks, input_tokens, output_tokens):
        self.content = tool_use_blocks
        self.usage = _FakeUsage(input_tokens=input_tokens, output_tokens=output_tokens)


class _FakeAnthropicToolUseClient:
    class _Messages:
        async def create(self, **kwargs):
            blocks = [_FakeToolUseBlock("toolu_1", "charge_customer", {"amount": 500})]
            return _FakeAnthropicToolUseResponse(blocks, 9, 2)

    def __init__(self):
        self.messages = _FakeAnthropicToolUseClient._Messages()


async def test_wrap_llm_client_anthropic_tool_use_captured(fake_repository):
    client = wrap_llm_client(_FakeAnthropicToolUseClient())

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str):
        return await client.messages.create(model="claude", messages=[])

    await agent("task")

    run_id = next(iter(fake_repository.runs))
    steps = await fake_repository.list_trace_steps_for_run(run_id)
    assert len(steps) == 1
    output = steps[0]["output"]
    assert output["text"] is None
    assert output["tool_calls"] == [{"id": "toolu_1", "name": "charge_customer", "arguments": {"amount": 500}}]


class _UnknownShapeResponse:
    def __init__(self, payload):
        self.payload = payload


class _FakeUnknownClient:
    class _Messages:
        async def create(self, **kwargs):
            return _UnknownShapeResponse({"text": "custom shape", "tokens": {"in": 4, "out": 2}})

    def __init__(self):
        self.messages = _FakeUnknownClient._Messages()


async def test_wrap_llm_client_custom_extract_override(fake_repository):
    def extract(response):
        return {
            "input_tokens": response.payload["tokens"]["in"],
            "output_tokens": response.payload["tokens"]["out"],
            "text": response.payload["text"],
        }

    client = wrap_llm_client(_FakeUnknownClient(), extract=extract)

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        response = await client.messages.create()
        return response.payload["text"]

    result = await agent("task")
    assert result == "custom shape"

    run_id = next(iter(fake_repository.runs))
    steps = await fake_repository.list_trace_steps_for_run(run_id)
    assert steps[0]["tokens_input"] == 4
    assert steps[0]["tokens_output"] == 2


async def test_nesting_across_traceable_and_llm_call(fake_repository):
    client = wrap_llm_client(_FakeOpenAIClient())

    @traceable
    async def research(question: str) -> str:
        response = await client.chat.completions.create(model="gpt-4o-mini", messages=[])
        return response.choices[0].message.content

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> str:
        return await research(task)

    await agent("task")

    run_id = next(iter(fake_repository.runs))
    steps = await fake_repository.list_trace_steps_for_run(run_id)
    assert len(steps) == 2
    outer = next(s for s in steps if s["kind"] == "function")
    llm = next(s for s in steps if s["kind"] == "llm_call")
    assert llm["parent_step_id"] == outer["id"]


def test_traceable_outside_a_run_raises():
    @traceable
    async def step() -> None:
        return None

    with pytest.raises(RuntimeError, match="active @monitor-wrapped run"):
        import asyncio

        asyncio.run(step())


def test_wrap_llm_client_outside_a_run_raises():
    client = wrap_llm_client(_FakeOpenAIClient())

    with pytest.raises(RuntimeError, match="active @monitor-wrapped run"):
        import asyncio

        asyncio.run(client.chat.completions.create())


async def test_trace_write_failure_is_swallowed_and_logged(fake_repository, caplog):
    class _BrokenRepository(InMemoryRunRepository):
        async def save_trace_step(self, step):
            raise RuntimeError("db down")

    broken = _BrokenRepository()
    # copy over the run bookkeeping so get_run/list_audit_events still work
    agentguard.configure(broken)

    @traceable
    async def step(x: int) -> int:
        return x * 2

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> int:
        return await step(21)

    with caplog.at_level(logging.ERROR, logger="agentguard.tracing"):
        result = await agent("task")

    assert result == 42  # the traced function's own result is unaffected

    run_id = next(iter(broken.runs))
    events = await broken.list_audit_events(run_id)
    assert _trace_events(events, "TRACE_STEP") == []
    failed = _trace_events(events, "TRACE_WRITE_FAILED")
    assert len(failed) == 1

    assert any("failed to record trace step" in r.message for r in caplog.records)
    assert any(r.exc_info is not None for r in caplog.records)


async def test_hash_chain_intact_after_tracing(fake_repository):
    @traceable
    async def step(x: int) -> int:
        return x * 2

    @monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def agent(task: str) -> int:
        return await step(21)

    await agent("task")
    run_id = next(iter(fake_repository.runs))
    result = await verify_audit_chain(fake_repository, run_id)
    assert result["intact"] is True
