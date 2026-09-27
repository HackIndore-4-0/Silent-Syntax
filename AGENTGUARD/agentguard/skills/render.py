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
    {"key": "claude-sonnet-5", "label": "claude-sonnet-5 — highest accuracy",
     "note": "Use when judge accuracy matters more than cost, e.g. compliance-sensitive safety metrics or final pre-release evaluation."},
)


_HEADER_TEMPLATE = """# Integrate AgentGuard into this project

Project: __PROJECT_NAME__ (project_id: __PROJECT_ID__)
API URL: __API_BASE_URL__
Install: pip install agentguard

## 1. Set environment variables

    AGENTGUARD_API_KEY=${AGENTGUARD_API_KEY}
    AGENTGUARD_DATABASE_URL=${AGENTGUARD_DATABASE_URL}
"""

_JUDGE_MODEL_TEMPLATE = """## Recommended judge model: __JUDGE_MODEL__

Evaluation metrics need a judge LLM to score outputs. __JUDGE_MODEL__ was
selected for this Skill; pass it as the registry's default so every
selected metric below uses it unless a metric overrides it:

    from agentguard.evaluation import build_suite_evaluator_registry
    evaluators = build_suite_evaluator_registry(suite, repository=repository, default_model="__JUDGE_MODEL__")
"""

_FOOTER = """
## Verify

Run your agent once. Check __API_BASE_URL__/#/runs for the new Run.
"""


def compose_skill(request: SkillRequest) -> str:
    framework = FRAMEWORK_TEMPLATES.get(request.framework)
    if framework is None:
        raise UnknownFrameworkError(request.framework)

    sections = [_HEADER_TEMPLATE, framework.body]
    if request.selected_categories:
        sections.append(_JUDGE_MODEL_TEMPLATE)

    for category_key in request.selected_categories:
        category = FEATURE_CATEGORIES.get(category_key)
        if category is None:
            raise UnknownCategoryError(category_key)
        requested = request.selected_metrics.get(category_key, ())
        # Filter to this category's own metric_keys so a mismatched
        # request (a metric that's real but belongs to a different
        # category) never leaks into the wrong section.
        valid_selected = tuple(m for m in requested if m in category.metric_keys)
        sections.append(category.render_body(valid_selected))

    sections.append(_FOOTER)

    markdown = "\n".join(sections)
    return (
        markdown
        .replace("__PROJECT_NAME__", request.project_name)
        .replace("__PROJECT_ID__", request.project_id)
        .replace("__API_BASE_URL__", request.api_base_url)
        .replace("__JUDGE_MODEL__", request.judge_model)
    )


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
