"""Skills Generator — composes prebuilt Markdown integration guides
("Skills") for a project, parameterized by framework and selected
AgentGuard features/metrics. No persistence: every Skill is generated
on demand and handed back as a string; the caller copies it and pastes
it to an external AI coding agent, which performs the actual
integration in the TARGET repository. This package never touches any
repository other than reading `metrics_catalog.py`'s existing metadata.
"""
from .features import FEATURE_CATEGORIES, FeatureCategory
from .frameworks import FRAMEWORK_TEMPLATES, FrameworkTemplate
from .render import SkillRequest, compose_skill, list_skill_options

__all__ = [
    "FEATURE_CATEGORIES",
    "FeatureCategory",
    "FRAMEWORK_TEMPLATES",
    "FrameworkTemplate",
    "SkillRequest",
    "compose_skill",
    "list_skill_options",
]
