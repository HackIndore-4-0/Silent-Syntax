"""The Skills Generator composer (agentguard/skills/render.py)."""
from __future__ import annotations

import pytest

from agentguard.skills.render import (
    SkillRequest,
    UnknownCategoryError,
    UnknownFrameworkError,
    UnknownJudgeModelError,
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

    def test_install_instructions_use_git_clone_not_pypi(self):
        """Regression test: agentguard is NOT published on PyPI (verified
        directly: https://pypi.org/pypi/agentguard/json -> 404) -- a
        "pip install agentguard" instruction would fail for anyone who
        actually followed it. The real distribution mechanism today is
        cloning the source repo and installing it editable."""
        markdown = compose_skill(_request())
        assert "pip install agentguard" not in markdown
        assert "git clone" in markdown
        assert "pip install -e ." in markdown

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
        assert "### Step 3:" in markdown  # framework body step present
        assert "**Verify:**" in markdown
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

    def test_a_category_checked_with_zero_selected_metrics_is_omitted_entirely(self):
        markdown = compose_skill(_request(selected_categories=("rag",), selected_metrics={"rag": ()}))
        assert "RAG evaluation" not in markdown

    def test_tracing_step_is_always_included_and_covers_non_litellm_llm_calls(self):
        """Regression test: tracing was only mentioned in the function
        reference appendix and the LangGraph framework body -- a plain
        Python agent calling an LLM via a raw HTTP client (e.g.
        OpenRouter without litellm) had no guidance at all on how to
        trace that call. Tracing must be its own explicit, always-present
        step covering traced_acompletion (litellm/OpenRouter-via-litellm),
        wrap_llm_client (a client object), and @traceable (a raw HTTP
        call function) as three real options, not just one."""
        markdown = compose_skill(_request(framework="plain_python", selected_categories=()))
        assert "Trace your LLM and tool calls" in markdown
        assert "traced_acompletion" in markdown
        assert "wrap_llm_client" in markdown
        assert "@traceable" in markdown
        assert "openrouter" in markdown.lower()

    def test_function_reference_section_is_always_included(self):
        """The generated Skill should be a complete-enough reference that
        the coding agent doesn't need to guess at AgentGuard's API --
        every major function/decorator gets a one-line usage example,
        regardless of which categories were selected."""
        markdown = compose_skill(_request(selected_categories=()))
        assert "## AgentGuard function reference" in markdown
        for symbol in [
            "perform_action(", "perform_action_with_result(", "request_approval(",
            "update_state(", "get_state(", "reset_state(", "record_tokens(",
            "wait_for_background_tasks(", "traced_acompletion(", "wrap_llm_client(",
        ]:
            assert symbol in markdown, f"missing reference for {symbol}"

    def test_framework_body_is_included_for_each_framework(self):
        for framework_key, expected_snippet in [
            ("plain_python", "@guard.monitor(policy=Policy("),
            ("langgraph", "@traceable"),
            ("generic", "Inspect this repository"),
        ]:
            markdown = compose_skill(_request(framework=framework_key))
            assert expected_snippet in markdown

    def test_a_project_name_containing_a_later_token_is_not_rescanned(self):
        """Regression test: chained .replace() calls rescan the WHOLE
        string after each substitution. If project_name itself contains
        the literal text "__JUDGE_MODEL__" (an edge case, but project
        names are freely settable by any workspace member via
        POST /api/projects), the later .replace("__JUDGE_MODEL__", ...)
        call would substitute inside what was just inserted as the
        project name -- corrupting it. Substitution must happen in a
        single pass so an already-substituted value is never rescanned."""
        markdown = compose_skill(_request(
            project_name="acme __JUDGE_MODEL__ corp",
            selected_categories=("rag",),
            selected_metrics={"rag": ("deepeval.faithfulness",)},
            judge_model="gpt-4.1",
        ))
        assert "acme __JUDGE_MODEL__ corp" in markdown

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
            judge_model="gpt-4.1",
        ))
        assert "gpt-4.1" in markdown
        assert 'default_model="gpt-4.1"' in markdown

    def test_unknown_judge_model_raises(self):
        """judge_model isn't free text -- it must be one of
        JUDGE_MODEL_OPTIONS, same 'explicit gap, never a silent skip'
        rule as UnknownFrameworkError/UnknownCategoryError. Otherwise an
        arbitrary string is spliced verbatim into a Python string literal
        in the generated code (a value containing '\"' would produce
        syntactically broken output)."""
        with pytest.raises(UnknownJudgeModelError):
            compose_skill(_request(judge_model="not-a-real-model"))


class TestListSkillOptions:
    def test_returns_all_three_frameworks(self):
        options = list_skill_options()
        assert {f["key"] for f in options["frameworks"]} == {"plain_python", "langgraph", "generic"}

    def test_returns_all_six_categories_with_their_metrics(self):
        options = list_skill_options()
        by_key = {c["key"]: c for c in options["categories"]}
        assert set(by_key) == {"rag", "safety", "agentic", "other", "hitl", "trajectory", "benchmarking"}
        assert {m["key"] for m in by_key["rag"]["metrics"]} == {
            "deepeval.answer_relevancy", "deepeval.faithfulness", "deepeval.contextual_precision",
            "deepeval.contextual_recall", "deepeval.contextual_relevancy", "deepeval.hallucination",
        }
        assert by_key["hitl"]["metrics"] == []

    def test_returns_judge_model_options(self):
        """Only models DeepEval's default OpenAI-model resolution can
        actually use -- an Anthropic model name here would silently be
        routed to OpenAI (no Anthropic wrapper exists anywhere in
        agentguard/evaluation/), so it's excluded rather than offered as
        a judge model that doesn't work."""
        options = list_skill_options()
        keys = {m["key"] for m in options["judge_models"]}
        assert keys == {"gpt-4o-mini", "gpt-4.1", "gpt-4o"}

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
