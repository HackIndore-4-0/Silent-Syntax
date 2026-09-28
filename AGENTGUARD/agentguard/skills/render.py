"""The Skills Generator composer — pure string composition, no I/O, no
database. `compose_skill()` builds one Markdown document from a header
+ the selected framework's body + (if any category is selected) a judge-
model recommendation section + one section per selected feature
category, then does a single literal `.replace()` pass for the
`__PROJECT_NAME__`/`__PROJECT_ID__`/`__API_BASE_URL__`/`__JUDGE_MODEL__`
tokens (never `.format()` — the code samples inside these templates
contain their own `{`/`}` braces, e.g. dict literals, which `.format()`
would misread as placeholders).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .features import FEATURE_CATEGORIES
from .frameworks import FRAMEWORK_TEMPLATES


class UnknownFrameworkError(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(f"unknown framework key: {key!r}")
        self.key = key


class UnknownCategoryError(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(f"unknown feature category key: {key!r}")
        self.key = key


class UnknownJudgeModelError(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(f"unknown judge model key: {key!r}")
        self.key = key


@dataclass(frozen=True)
class SkillRequest:
    project_id: str
    project_name: str
    api_base_url: str
    framework: str
    selected_categories: tuple[str, ...] = field(default_factory=tuple)
    selected_metrics: dict[str, tuple[str, ...]] = field(default_factory=dict)
    judge_model: str = "gpt-4o-mini"


JUDGE_MODEL_OPTIONS = (
    {"key": "gpt-4o-mini", "label": "gpt-4o-mini — fast & cheap (Recommended default)",
     "note": "Best for everyday RAG/safety checks where per-run cost matters more than maximum judge accuracy."},
    {"key": "gpt-4.1", "label": "gpt-4.1 — balanced",
     "note": "Noticeably more accurate judging than gpt-4o-mini at moderate extra cost; a good default for safety-critical categories."},
    {"key": "gpt-4o", "label": "gpt-4o — highest accuracy",
     "note": "Use when judge accuracy matters more than cost, e.g. compliance-sensitive safety metrics or final pre-release evaluation."},
)
# Only OpenAI model names: DeepEval's default model resolution (used
# whenever a metric's `model=` is a bare string, which is what
# build_suite_evaluator_registry's default_model becomes) treats any
# string as an OpenAI model — there is no Anthropic/other-provider
# wrapper anywhere in agentguard/evaluation/, confirmed empirically
# (a bare "claude-sonnet-5" string raises OpenAI's own "API key not
# configured" error rather than calling Anthropic). Offering a
# non-OpenAI name here would produce a Skill that looks right and fails.
_JUDGE_MODEL_KEYS = frozenset(m["key"] for m in JUDGE_MODEL_OPTIONS)


_HEADER_TEMPLATE = """# Integrate AgentGuard into this project

Project: __PROJECT_NAME__ (project_id: __PROJECT_ID__)
API URL: __API_BASE_URL__

Follow the numbered steps below IN ORDER. Each one ends with a
**Verify:** line — confirm it before moving to the next step. Do not
skip a Verify and continue if it doesn't check out; fix the step
first.
"""

_INSTALL_STEP_BODY = """AgentGuard is not published on PyPI yet — install it directly from source:

    git clone https://github.com/HackIndore-4-0/Silent-Syntax.git
    cd Silent-Syntax/AGENTGUARD
    pip install -e .
"""
_INSTALL_STEP_VERIFY = (
    'Run `python -c "import agentguard; print(agentguard.__version__)"` — '
    "it must print a version number, not an ImportError."
)

_ENV_STEP_BODY = """    AGENTGUARD_API_KEY=${AGENTGUARD_API_KEY}
    AGENTGUARD_DATABASE_URL=${AGENTGUARD_DATABASE_URL}
"""
_ENV_STEP_VERIFY = (
    "Confirm both variables are set in the shell that will run your agent "
    "(`echo $AGENTGUARD_API_KEY` on macOS/Linux/bash, `echo $env:AGENTGUARD_API_KEY` "
    "on PowerShell) — neither should print empty. These only persist for the "
    "current shell session unless exported permanently."
)

_TRACING_STEP_BODY = """Tracing is what populates the dashboard's Trace/Steps view and gives
every evaluation metric something real to score — do this regardless of
which evaluation features you picked below. Instrument every LLM call
and every significant tool call. Pick whichever of these three matches
how your agent actually calls its LLM:

    # (a) You call litellm directly (litellm.acompletion/.completion) --
    #     this covers OpenRouter too, via model="openrouter/<provider>/<model>":
    from agentguard.tracing import traced_acompletion
    response = await traced_acompletion(model="openrouter/openai/gpt-4o-mini", messages=[...])

    # (b) You call an LLM client SDK directly (OpenAI, Anthropic, or a
    #     raw OpenRouter client) and don't want to change every call site --
    #     wrap the client ONCE, every method call through it is then traced:
    from agentguard.tracing import wrap_llm_client
    client = wrap_llm_client(your_openai_or_anthropic_or_openrouter_client)

    # (c) You call the LLM via a raw HTTP request (e.g. `requests`/`httpx`
    #     directly against OpenRouter's API, no client library) -- wrap the
    #     function that makes the call instead:
    from agentguard.tracing import traceable

    @traceable
    async def call_llm(messages: list) -> str:
        response = await your_http_client.post(...)  # unchanged
        return response.json()["choices"][0]["message"]["content"]

Also wrap any significant tool/function call (a search, a database
query, a file write) the same way as (c) — `@traceable` works on any
function, not just LLM calls.
"""
_TRACING_STEP_VERIFY = (
    "Run the agent once, open the new Run in the dashboard, and check the "
    "Trace/Steps tab — you should see one step per traced_acompletion/"
    "@traceable call (with real latency, and tokens/cost for llm_call "
    "steps), not just the single top-level @monitor span."
)

_JUDGE_MODEL_STEP_BODY = """Evaluation metrics need a judge LLM to score outputs. __JUDGE_MODEL__ was
selected for this Skill; pass it as the registry's default so every
selected metric uses it unless a metric overrides it:

    from agentguard.evaluation import build_suite_evaluator_registry
    evaluators = build_suite_evaluator_registry(suite, repository=repository, default_model="__JUDGE_MODEL__")
"""
_JUDGE_MODEL_STEP_VERIFY = (
    "Call build_suite_evaluator_registry(...) for the suite you're about to build "
    "in the next step(s) and confirm it doesn't raise MissingMetricConfigError — "
    "if it does, that metric's config needs reviewing (see its TODO comment)."
)

_FUNCTION_REFERENCE = """## AgentGuard function reference

Beyond `@monitor`, these are the functions available inside a monitored
run (call them from anywhere in your agent's code while it's executing
under `@guard.monitor`):

    import agentguard

    # State snapshots -- recorded on the dashboard's Run timeline.
    agentguard.update_state(**kwargs)      # merge kwargs into the run's current state
    agentguard.get_state()                 # read the current state back
    agentguard.reset_state()               # clear it

    # Real token/cost usage (Tokens & Cost dashboard page) -- only call
    # with numbers you actually have, never an estimate.
    agentguard.record_tokens(model_name="gpt-4o-mini", input_tokens=120, output_tokens=45, cost_usd=0.0009)

    # Policy-gated actions -- runs the forbidden/require_approval checks
    # from your Policy before letting the action proceed.
    finding = await agentguard.perform_action("some_action", **evidence)
    # ...or, to also read back a reviewer's edited parameters on approval:
    result = await agentguard.perform_action_with_result("some_action", **evidence)

    # Low-level human approval primitive (perform_action calls this for
    # you when an action is in Policy.require_approval -- call it
    # directly only if you need to ask for approval outside that check):
    decision = await agentguard.request_approval("some_action", reason="why this needs a human")

    # Blocks until any fire-and-forget background trace writes finish --
    # call at the very end of a script/test so nothing is lost on exit.
    await agentguard.wait_for_background_tasks()

    # Drop-in replacements for litellm.acompletion/.completion that
    # automatically record an "llm_call" trace step (model, tokens, cost):
    from agentguard.tracing import traced_acompletion, traced_completion
    response = await traced_acompletion(model="gpt-4o-mini", messages=[...])

    # Wraps ANY LLM client object (OpenAI, Anthropic, etc.) so every
    # call through it is traced the same way, when you can't switch to
    # traced_acompletion directly:
    from agentguard.tracing import wrap_llm_client
    client = wrap_llm_client(your_openai_or_anthropic_client)
"""

_DONE_FOOTER = """---

You're done. Every step above carried its own **Verify:** — if all of
them passed, the integration is complete.
"""


def _step_block(index: int, title: str, body: str, verify: str) -> str:
    return f"### Step {index}: {title}\n\n{body}\n**Verify:** {verify}\n"


def compose_skill(request: SkillRequest) -> str:
    framework = FRAMEWORK_TEMPLATES.get(request.framework)
    if framework is None:
        raise UnknownFrameworkError(request.framework)
    if request.judge_model not in _JUDGE_MODEL_KEYS:
        raise UnknownJudgeModelError(request.judge_model)

    # Each step is (title, body, verify) -- numbered sequentially below,
    # so a variable number of selected categories still produces clean
    # "Step 1, 2, 3, ..." numbering rather than gaps or hardcoded numbers
    # baked into static template text.
    steps: list[tuple[str, str, str]] = [
        ("Install AgentGuard", _INSTALL_STEP_BODY, _INSTALL_STEP_VERIFY),
        ("Set environment variables", _ENV_STEP_BODY, _ENV_STEP_VERIFY),
        (framework.step_title, framework.body, framework.verify),
        ("Trace your LLM and tool calls", _TRACING_STEP_BODY, _TRACING_STEP_VERIFY),
    ]

    if request.selected_categories:
        steps.append(("Configure the judge model", _JUDGE_MODEL_STEP_BODY, _JUDGE_MODEL_STEP_VERIFY))

    for category_key in request.selected_categories:
        category = FEATURE_CATEGORIES.get(category_key)
        if category is None:
            raise UnknownCategoryError(category_key)
        requested = request.selected_metrics.get(category_key, ())
        # Filter to this category's own metric_keys so a mismatched
        # request (a metric that's real but belongs to a different
        # category) never leaks into the wrong section.
        valid_selected = tuple(m for m in requested if m in category.metric_keys)
        body = category.render_body(valid_selected)
        if body:  # a metric category checked with nothing selected renders "" -- omit it entirely
            steps.append((category.label, body, category.verify))

    step_blocks = [_step_block(i, title, body, verify) for i, (title, body, verify) in enumerate(steps, start=1)]
    sections = [_HEADER_TEMPLATE, *step_blocks, _FUNCTION_REFERENCE, _DONE_FOOTER]

    markdown = "\n".join(sections)
    # A single-pass substitution: chained .replace() calls rescan the
    # WHOLE string after each call, so a substituted value (e.g. a
    # project name containing the literal text "__JUDGE_MODEL__" --
    # project names are freely settable via POST /api/projects) would
    # get corrupted by a LATER .replace() call matching inside what was
    # just inserted. re.sub with a single combined pattern never rescans
    # its own replacement text.
    token_values = {
        "__PROJECT_NAME__": request.project_name,
        "__PROJECT_ID__": request.project_id,
        "__API_BASE_URL__": request.api_base_url,
        "__JUDGE_MODEL__": request.judge_model,
    }
    pattern = re.compile("|".join(re.escape(token) for token in token_values))
    return pattern.sub(lambda m: token_values[m.group(0)], markdown)


def list_skill_options() -> dict:
    from ..evaluation.metrics_catalog import DEEPEVAL_CATALOG

    return {
        "frameworks": [
            {"key": t.key, "label": t.label, "description": t.description}
            for t in FRAMEWORK_TEMPLATES.values()
        ],
        "categories": [
            {
                "key": c.key,
                "label": c.label,
                "description": c.description,
                "metrics": [
                    {"key": m, "description": DEEPEVAL_CATALOG[m].description}
                    for m in c.metric_keys
                ],
            }
            for c in FEATURE_CATEGORIES.values()
        ],
        "judge_models": list(JUDGE_MODEL_OPTIONS),
    }
