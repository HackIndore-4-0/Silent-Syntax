"""Framework template library for the Skills Generator (agentguard/skills/frameworks.py)."""
from __future__ import annotations

from agentguard.skills.frameworks import FRAMEWORK_TEMPLATES


class TestFrameworkTemplates:
    def test_all_three_frameworks_are_registered(self):
        assert set(FRAMEWORK_TEMPLATES) == {"plain_python", "langgraph", "generic"}

    def test_plain_python_body_shows_the_monitor_decorator_pattern(self):
        body = FRAMEWORK_TEMPLATES["plain_python"].body
        assert "@guard.monitor(policy=Policy(" in body
        assert "__PROJECT_NAME__" in body

    def test_langgraph_body_shows_traceable_and_monitor(self):
        body = FRAMEWORK_TEMPLATES["langgraph"].body
        assert "@traceable" in body
        assert "@guard.monitor(policy=Policy(" in body
        assert "graph.ainvoke" in body or "your_graph.ainvoke" in body

    def test_generic_body_instructs_detection_before_any_code(self):
        body = FRAMEWORK_TEMPLATES["generic"].body
        assert "Inspect this repository" in body
        assert "@guard.monitor(policy=Policy(" in body

    def test_bodies_never_contain_a_real_looking_api_key(self):
        for template in FRAMEWORK_TEMPLATES.values():
            assert "AGENTGUARD_API_KEY=agp_live_" not in template.body
