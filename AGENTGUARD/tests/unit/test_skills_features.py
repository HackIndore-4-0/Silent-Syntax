"""Feature-category library for the Skills Generator (agentguard/skills/features.py)."""
from __future__ import annotations

from agentguard.skills.features import FEATURE_CATEGORIES


class TestFeatureCategories:
    def test_all_six_categories_are_registered(self):
        assert set(FEATURE_CATEGORIES) == {"rag", "safety", "agentic", "hitl", "trajectory", "benchmarking"}

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
