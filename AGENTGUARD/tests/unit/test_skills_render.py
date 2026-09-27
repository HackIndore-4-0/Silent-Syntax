"""The Skills Generator composer (agentguard/skills/render.py)."""
from __future__ import annotations

import pytest

from agentguard.skills.render import (
    SkillRequest,
    UnknownCategoryError,
    UnknownFrameworkError,
    compose_skill,
    list_skill_options,
)


def _request(**overrides) -> SkillRequest:
    defaults = dict(
        project_id="proj-123",
        project_name="acme-bot",
        api_base_url="http://127.0.0.1:8000",
        framework="plain_python",
        selected_categories=(),
        selected_metrics={},
    )
    defaults.update(overrides)
    return SkillRequest(**defaults)


class TestComposeSkill:
    def test_header_includes_project_name_id_and_api_url(self):
        markdown = compose_skill(_request())
        assert "acme-bot" in markdown
        assert "proj-123" in markdown
        assert "http://127.0.0.1:8000" in markdown

    def test_api_key_is_always_the_placeholder_never_a_real_secret(self):
        markdown = compose_skill(_request())
        assert "${AGENTGUARD_API_KEY}" in markdown
        # No plausible real-looking key format anywhere in the output.
        assert "agp_live_" not in markdown

    def test_unknown_framework_raises(self):
        with pytest.raises(UnknownFrameworkError):
            compose_skill(_request(framework="does_not_exist"))

    def test_unknown_category_raises(self):
        with pytest.raises(UnknownCategoryError):
            compose_skill(_request(selected_categories=("does_not_exist",)))

    def test_no_categories_selected_still_produces_a_valid_skill(self):
        markdown = compose_skill(_request(selected_categories=()))
        assert "## 2." in markdown  # framework body section present
        assert "RAG evaluation" not in markdown  # no category sections

    def test_selected_category_section_appears_with_only_its_selected_metrics(self):
        markdown = compose_skill(_request(
            selected_categories=("rag",),
            selected_metrics={"rag": ("deepeval.faithfulness",)},
        ))
        assert "RAG evaluation" in markdown
        assert "deepeval.faithfulness" in markdown
        assert "deepeval.contextual_precision" not in markdown

    def test_a_metric_not_belonging_to_its_category_is_ignored_not_rendered(self):
        """Regression guard: requesting a mismatched metric
        (deepeval.bias under "rag") must not produce wrong output -- it's
        filtered out because it isn't in that category's own metric_keys."""
        markdown = compose_skill(_request(
            selected_categories=("rag",),
            selected_metrics={"rag": ("deepeval.faithfulness", "deepeval.bias")},
        ))
        assert "deepeval.faithfulness" in markdown
        assert "deepeval.bias" not in markdown

    def test_a_category_checked_with_zero_selected_metrics_does_not_render_an_empty_list(self):
        markdown = compose_skill(_request(selected_categories=("rag",), selected_metrics={"rag": ()}))
        assert "This project will be scored on" not in markdown

    def test_framework_body_is_included_for_each_framework(self):
        for framework_key, expected_snippet in [
            ("plain_python", "@guard.monitor(policy=Policy("),
            ("langgraph", "@traceable"),
            ("generic", "Inspect this repository"),
        ]:
            markdown = compose_skill(_request(framework=framework_key))
            assert expected_snippet in markdown

    def test_project_name_with_braces_does_not_break_rendering(self):
        """Substitution is plain .replace(), so a project name containing
        '{' or '}' must not raise or corrupt the surrounding code blocks
        (which legitimately contain braces)."""
        markdown = compose_skill(_request(project_name="Acme {Corp}"))
        assert "Acme {Corp}" in markdown
        assert "@guard.monitor(policy=Policy(" in markdown  # code block still intact

    def test_judge_model_defaults_to_gpt_4o_mini(self):
        markdown = compose_skill(_request(selected_categories=("rag",), selected_metrics={"rag": ("deepeval.faithfulness",)}))
        assert "gpt-4o-mini" in markdown

    def test_judge_model_section_appears_only_when_categories_are_selected(self):
        markdown = compose_skill(_request(selected_categories=()))
        assert "Recommended judge model" not in markdown

    def test_custom_judge_model_is_used_in_the_registry_snippet(self):
        markdown = compose_skill(_request(
            selected_categories=("safety",),
            selected_metrics={"safety": ("deepeval.bias",)},
            judge_model="claude-sonnet-5",
        ))
        assert "claude-sonnet-5" in markdown
        assert 'default_model="claude-sonnet-5"' in markdown


class TestListSkillOptions:
    def test_returns_all_three_frameworks(self):
        options = list_skill_options()
        assert {f["key"] for f in options["frameworks"]} == {"plain_python", "langgraph", "generic"}

    def test_returns_all_six_categories_with_their_metrics(self):
        options = list_skill_options()
        by_key = {c["key"]: c for c in options["categories"]}
        assert set(by_key) == {"rag", "safety", "agentic", "hitl", "trajectory", "benchmarking"}
        assert {m["key"] for m in by_key["rag"]["metrics"]} == {
            "deepeval.answer_relevancy", "deepeval.faithfulness", "deepeval.contextual_precision",
            "deepeval.contextual_recall", "deepeval.contextual_relevancy", "deepeval.hallucination",
        }
        assert by_key["hitl"]["metrics"] == []

    def test_returns_judge_model_options(self):
        options = list_skill_options()
        keys = {m["key"] for m in options["judge_models"]}
        assert keys == {"gpt-4o-mini", "gpt-4.1", "claude-sonnet-5"}

    def test_no_top_level_deepeval_import(self):
        import ast
        import inspect

        from agentguard.skills import render

        tree = ast.parse(inspect.getsource(render))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                assert (node.module or "").split(".")[0] != "deepeval"
            elif isinstance(node, ast.Import):
                assert node.names[0].name.split(".")[0] != "deepeval"
