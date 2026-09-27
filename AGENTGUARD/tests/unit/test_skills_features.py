"""Feature-category library for the Skills Generator (agentguard/skills/features.py)."""
from __future__ import annotations

from agentguard.skills.features import FEATURE_CATEGORIES


class TestFeatureCategories:
    def test_all_seven_categories_are_registered(self):
        assert set(FEATURE_CATEGORIES) == {
            "rag", "safety", "agentic", "other", "hitl", "trajectory", "benchmarking",
        }

    def test_every_deepeval_catalog_metric_is_reachable_from_some_category(self):
        """Regression test: summarization/json_correctness/prompt_alignment/
        geval had no UI path at all before -- every real catalog metric
        must be selectable from somewhere, not just the 15 originally
        wired into rag/safety/agentic."""
        from agentguard.evaluation.metrics_catalog import DEEPEVAL_CATALOG

        all_offered = {m for cat in FEATURE_CATEGORIES.values() for m in cat.metric_keys}
        assert all_offered == set(DEEPEVAL_CATALOG)

    def test_other_category_covers_the_previously_unreachable_metrics(self):
        other = FEATURE_CATEGORIES["other"]
        assert other.metric_keys == (
            "deepeval.summarization", "deepeval.json_correctness",
            "deepeval.prompt_alignment", "deepeval.geval",
        )

    def test_rag_category_metric_keys_match_the_real_catalog(self):
        rag = FEATURE_CATEGORIES["rag"]
        assert rag.metric_keys == (
            "deepeval.answer_relevancy", "deepeval.faithfulness", "deepeval.contextual_precision",
            "deepeval.contextual_recall", "deepeval.contextual_relevancy", "deepeval.hallucination",
        )

    def test_safety_category_metric_keys(self):
        safety = FEATURE_CATEGORIES["safety"]
        assert safety.metric_keys == (
            "deepeval.bias", "deepeval.toxicity", "deepeval.non_advice",
            "deepeval.misuse", "deepeval.pii_leakage", "deepeval.role_violation",
        )

    def test_agentic_category_metric_keys(self):
        agentic = FEATURE_CATEGORIES["agentic"]
        assert agentic.metric_keys == (
            "deepeval.tool_correctness", "deepeval.argument_correctness", "deepeval.task_completion",
        )

    def test_hitl_trajectory_benchmarking_have_no_metric_keys(self):
        for key in ("hitl", "trajectory", "benchmarking"):
            assert FEATURE_CATEGORIES[key].metric_keys == ()

    def test_metric_category_render_body_lists_only_selected_metrics_with_real_descriptions(self):
        rag = FEATURE_CATEGORIES["rag"]
        body = rag.render_body(("deepeval.faithfulness",))
        assert "deepeval.faithfulness" in body
        assert "Does the output factually align with the retrieval context?" in body
        assert "deepeval.answer_relevancy" not in body  # not selected -> not listed

    def test_metric_category_render_body_with_no_selection_omits_the_metric_list(self):
        rag = FEATURE_CATEGORIES["rag"]
        body = rag.render_body(())
        assert "This project will be scored on" not in body

    def test_metric_category_render_body_with_no_selection_is_empty(self):
        """Regression test: a category checked in the UI but with zero
        individual metrics expanded/checked underneath used to render a
        useless "(No specific metrics selected...)" stub section. It
        must now be empty, so compose_skill can omit the section
        entirely rather than showing dead weight in the generated Skill."""
        rag = FEATURE_CATEGORIES["rag"]
        assert rag.render_body(()) == ""

    def test_metrics_needing_required_config_render_a_placeholder_config_with_a_todo(self):
        """Regression test: deepeval.non_advice/.misuse/.role_violation
        need required_config (advice_types/domain/role respectively).
        Without a config= placeholder, the generated Skill's suite would
        raise MissingMetricConfigError the moment it's actually run --
        looks correct, breaks at eval time. The rendered code must
        include real config so it works as-is, plus a TODO flagging that
        the placeholder should be reviewed."""
        safety = FEATURE_CATEGORIES["safety"]
        body = safety.render_body(("deepeval.non_advice",))
        assert "config={" in body
        assert "'advice_types'" in body
        assert "TODO" in body

    def test_metrics_without_required_config_render_no_config_kwarg(self):
        safety = FEATURE_CATEGORIES["safety"]
        body = safety.render_body(("deepeval.bias",))
        assert "config=" not in body

    def test_json_correctness_gets_manual_only_guidance_not_a_bogus_placeholder(self):
        """expected_schema needs a real Pydantic class reference -- there
        is no JSON-safe literal that could stand in for it (unlike
        advice_types/domain/role, which are plain strings/lists). Must
        render a comment explaining this rather than a fake string value
        that would produce technically-valid-looking but wrong code."""
        other = FEATURE_CATEGORIES["other"]
        body = other.render_body(("deepeval.json_correctness",))
        assert "expected_schema" in body
        assert "config={" not in body  # no bogus literal for a class reference
        assert "deepeval.json_correctness" in body

    def test_geval_renders_a_placeholder_name_and_evaluation_steps(self):
        other = FEATURE_CATEGORIES["other"]
        body = other.render_body(("deepeval.geval",))
        assert "'name'" in body
        assert "'evaluation_steps'" in body
        assert "TODO" in body

    def test_non_metric_category_render_body_ignores_its_argument(self):
        hitl = FEATURE_CATEGORIES["hitl"]
        assert hitl.render_body(()) == hitl.render_body(("anything",))
        assert "perform_action" in hitl.render_body(())

    def test_no_top_level_deepeval_import(self):
        import ast
        import inspect

        from agentguard.skills import features

        tree = ast.parse(inspect.getsource(features))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                assert (node.module or "").split(".")[0] != "deepeval"
            elif isinstance(node, ast.Import):
                assert node.names[0].name.split(".")[0] != "deepeval"
