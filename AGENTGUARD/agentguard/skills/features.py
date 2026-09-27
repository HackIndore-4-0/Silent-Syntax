"""Feature-category library for the Skills Generator. Each category
maps onto something this repo's evaluation platform already does (no
new concept invented here) — metric-backed categories (rag/safety/
agentic) render their body from `metrics_catalog.DEEPEVAL_CATALOG`'s
real descriptions, so a future new catalog metric is available here
with zero duplication. `agentguard.evaluation.metrics_catalog` itself
has no top-level `deepeval` import, so importing it here does not
require the `deepeval` extra either.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from ..evaluation.metrics_catalog import DEEPEVAL_CATALOG

FeatureCategoryKey = Literal["rag", "safety", "agentic", "hitl", "trajectory", "benchmarking"]


@dataclass(frozen=True)
class FeatureCategory:
    key: str
    label: str
    description: str
    metric_keys: tuple[str, ...]
    render_body: Callable[[tuple[str, ...]], str]


def _metric_lines(selected: tuple[str, ...]) -> str:
    lines = []
    for metric_key in selected:
        spec = DEEPEVAL_CATALOG.get(metric_key)
        if spec is None:
            continue
        lines.append(f"- {metric_key} — {spec.description}")
    return "\n".join(lines)


# Placeholder values for catalog metrics whose required_config is a
# plain JSON-safe literal (str/list) -- rendered directly into the suite
# so the Skill works as copy-pasted rather than raising
# MissingMetricConfigError at run-trigger time. The TODO comment flags
# that the placeholder should be reviewed, not left silently wrong.
_REQUIRED_CONFIG_PLACEHOLDERS: dict[str, object] = {
    "advice_types": ["financial", "medical"],
    "domain": "customer support",
    "role": "customer support agent",
    "name": "Custom check",
    "evaluation_steps": ["Describe the specific criteria this output must satisfy"],
}

# required_config params with NO possible JSON-safe literal placeholder
# (expected_schema needs a real Pydantic class reference, not a string)
# -- these metrics get manual-construction guidance instead of a bogus
# config value that would look valid but be wrong.
_MANUAL_ONLY_CONFIG_PARAMS = {"expected_schema"}


def _metric_suite_lines(selected: tuple[str, ...]) -> str:
    lines = []
    for key in selected:
        spec = DEEPEVAL_CATALOG.get(key)
        required = spec.required_config if spec else ()
        if any(p in _MANUAL_ONLY_CONFIG_PARAMS for p in required):
            manual_params = ", ".join(p for p in required if p in _MANUAL_ONLY_CONFIG_PARAMS)
            lines.append(
                f"        # {key} needs {manual_params} -- a real Python object (e.g. a Pydantic\n"
                f"        # model class), not something a config dict can express. Construct it\n"
                f"        # directly instead of via SuiteMetric:\n"
                f"        #   from deepeval.metrics import JsonCorrectnessMetric\n"
                f"        #   JsonCorrectnessMetric(expected_schema=YourSchema, threshold=0.7)"
            )
        elif required:
            config_items = ", ".join(
                f"{param!r}: {_REQUIRED_CONFIG_PLACEHOLDERS.get(param, f'REPLACE_ME_{param}')!r}"
                for param in required
            )
            lines.append(
                f'        SuiteMetric(evaluator="{key}", threshold=0.7, config={{{config_items}}}),'
                f"  # TODO: review this placeholder config for {key} before running"
            )
        else:
            lines.append(f'        SuiteMetric(evaluator="{key}", threshold=0.7),')
    return "\n".join(lines)


def _render_metric_category(label: str, selected: tuple[str, ...]) -> str:
    if not selected:
        # A category ticked in the UI with zero individual metrics
        # expanded/checked underneath has nothing useful to say -- return
        # empty so compose_skill omits the section entirely, instead of a
        # dead "(No specific metrics selected...)" stub.
        return ""
    return f"""## {label} (selected)

This project will be scored on:
{_metric_lines(selected)}

Register a suite:

    from agentguard.evaluation import EvaluationSuite, SuiteMetric
    suite = EvaluationSuite(name="__PROJECT_NAME___quality", metrics=[
{_metric_suite_lines(selected)}
    ])
    # POST this to /api/v2/suites, then POST /api/v2/eval-runs to trigger a run
"""


def _render_hitl(_selected: tuple[str, ...]) -> str:
    return """## Human-in-the-loop approval

For any action that should require a human sign-off before proceeding
(e.g. spending above a budget, sending an email, deleting data), use
`agentguard.perform_action` inside your agent function:

    import agentguard
    from agentguard import Policy

    @guard.monitor(policy=Policy(max_cost=60000, require_approval=["risky_action_name"], human_timeout_s=300))
    async def your_agent_entry_point(task: str) -> str:
        ...
        await agentguard.perform_action("risky_action_name", amount=1234)
        ...

If nobody responds within `human_timeout_s`, the action is denied by
default (fail closed) and the run stops with an
`agentguard.errors.HumanRejected` exception — catch it if your agent
should degrade gracefully instead of crashing.
"""


def _render_trajectory(_selected: tuple[str, ...]) -> str:
    return """## Agent trajectory evaluation

To catch an agent calling the same tool redundantly, failing to recover
from an error, or taking an abnormal number of steps compared to its
own history, register the deterministic trajectory evaluator:

    from agentguard.evaluation import EvaluationSuite, SuiteMetric

    suite = EvaluationSuite(name="__PROJECT_NAME___trajectory", metrics=[
        SuiteMetric(evaluator="trajectory", threshold=0.7),
    ])
    # POST this to /api/v2/suites, then POST /api/v2/eval-runs against a
    # real completed run's id to see duplicate-tool-call, missing-recovery,
    # and step-count-anomaly findings.
"""


def _render_benchmarking(_selected: tuple[str, ...]) -> str:
    return """## Model benchmarking

To compare this agent's behavior across multiple LLM models on the same
evaluation suite and get a recommendation for the cheapest/fastest/
highest-quality model within a budget, use ModelBenchmarkEngine once you
have an evaluation suite defined (see the sections above):

    from agentguard.evaluation.benchmark import ModelBenchmarkEngine
    # See docs/FEATURE_MANUAL.md section 29 for the full wiring example.
"""


FEATURE_CATEGORIES: dict[str, FeatureCategory] = {
    "rag": FeatureCategory(
        key="rag", label="RAG evaluation",
        description="Faithfulness, relevancy, and context-quality checks for retrieval-augmented agents.",
        metric_keys=(
            "deepeval.answer_relevancy", "deepeval.faithfulness", "deepeval.contextual_precision",
            "deepeval.contextual_recall", "deepeval.contextual_relevancy", "deepeval.hallucination",
        ),
        render_body=lambda selected: _render_metric_category("RAG evaluation", selected),
    ),
    "safety": FeatureCategory(
        key="safety", label="Safety checks",
        description="Bias, toxicity, unauthorized advice, and PII-leakage checks.",
        metric_keys=(
            "deepeval.bias", "deepeval.toxicity", "deepeval.non_advice",
            "deepeval.misuse", "deepeval.pii_leakage", "deepeval.role_violation",
        ),
        render_body=lambda selected: _render_metric_category("Safety checks", selected),
    ),
    "agentic": FeatureCategory(
        key="agentic", label="Agentic / tool-use evaluation",
        description="Correct tool calls, correct arguments, and task completion checks.",
        metric_keys=("deepeval.tool_correctness", "deepeval.argument_correctness", "deepeval.task_completion"),
        render_body=lambda selected: _render_metric_category("Agentic / tool-use evaluation", selected),
    ),
    "other": FeatureCategory(
        key="other", label="Other checks",
        description="Summarization quality, JSON-schema correctness, prompt-alignment, and custom G-Eval criteria.",
        metric_keys=(
            "deepeval.summarization", "deepeval.json_correctness",
            "deepeval.prompt_alignment", "deepeval.geval",
        ),
        render_body=lambda selected: _render_metric_category("Other checks", selected),
    ),
    "hitl": FeatureCategory(
        key="hitl", label="Human-in-the-loop approval",
        description="Block a risky action on a real human decision, with a deterministic timeout.",
        metric_keys=(),
        render_body=_render_hitl,
    ),
    "trajectory": FeatureCategory(
        key="trajectory", label="Agent trajectory evaluation",
        description="Deterministic checks for duplicate tool calls, missing recovery, and step-count anomalies.",
        metric_keys=(),
        render_body=_render_trajectory,
    ),
    "benchmarking": FeatureCategory(
        key="benchmarking", label="Model benchmarking",
        description="Compare this agent across multiple LLM models on the same evaluation suite.",
        metric_keys=(),
        render_body=_render_benchmarking,
    ),
}
