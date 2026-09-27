"""Evaluator registry builders — turn an EvaluationSuite's SuiteMetric
entries into real MetricEvaluator instances.

Kept separate from `engine.py` (the run_suite state machine) since this
module needs to import `metrics_catalog` plus the non-DeepEval evaluators
(`trajectory`, `handoff`), which `engine.py` has no other reason to know
about.

Two builders, for two different needs:

- `build_suite_evaluator_registry` — per-suite, using that suite's own
  `SuiteMetric.threshold`/`.config` for each DeepEval metric. This is the
  one the job-driven execution path (Phase 3) always uses, because
  EvaluationEngine.run_suite() recomputes `passed` from the SuiteMetric's
  threshold, but the DeepEval metric instance's OWN threshold still
  matters whenever a SuiteMetric leaves threshold unset — so two suites
  using the same "deepeval.faithfulness" key with different thresholds
  need two independently-constructed metric instances, not one shared
  dict entry.
- `build_default_evaluator_registry` — a fixed, class-default-threshold
  dict for discovery/manual use (e.g. "what evaluators are available at
  all"). Catalog keys with required_config (deepeval.geval,
  .json_correctness, .non_advice, .misuse) cannot be constructed
  generically here — there is no sensible default for a required
  `evaluation_steps`/`expected_schema`/`advice_types`/`domain` — so they
  are skipped; a caller who wants one supplies it via `extra_evaluators`.

Both are purely additive: an existing caller who hand-builds a
`dict[str, MetricEvaluator]` (per docs/FEATURE_MANUAL.md's documented
example) is completely unaffected.
"""
from __future__ import annotations

from typing import Any

from .evaluators.base import MetricEvaluator
from .evaluators.handoff import ChainHandoffEvaluator, HandoffEvaluator
from .evaluators.trajectory import TrajectoryEvaluator
from .metrics_catalog import DEEPEVAL_CATALOG, UnknownMetricError, build_deepeval_evaluator


def build_suite_evaluator_registry(
    suite: Any,
    *,
    repository: Any | None = None,
    default_model: Any = None,
    extra_evaluators: dict[str, MetricEvaluator] | None = None,
) -> dict[str, MetricEvaluator]:
    """Resolves only the keys the suite actually references. A
    'deepeval.*' key missing required config raises MissingMetricConfigError
    (a real suite-authoring bug that should fail loudly at run-trigger
    time). An unknown 'deepeval.*' key, or a 'handoff'/'handoff_chain' key
    with no repository supplied, is simply omitted — EvaluationEngine's
    existing 'no evaluator registered' -> available=False path handles
    that gap exactly as it does today. extra_evaluators entries always
    win, letting a caller override or inject an evaluator that needs
    config that can't round-trip through SuiteMetric.config (e.g. a
    JsonCorrectnessMetric with a live Pydantic expected_schema class)."""
    registry: dict[str, MetricEvaluator] = {}

    for metric in suite.metrics:
        key = metric.evaluator
        if key == "trajectory":
            registry[key] = TrajectoryEvaluator()
        elif key == "handoff" and repository is not None:
            registry[key] = HandoffEvaluator(repository)
        elif key == "handoff_chain" and repository is not None:
            registry[key] = ChainHandoffEvaluator(repository)
        elif key.startswith("deepeval."):
            try:
                registry[key] = build_deepeval_evaluator(
                    key, threshold=metric.threshold, model=default_model, **metric.config
                )
            except UnknownMetricError:
                continue

    if extra_evaluators:
        registry.update(extra_evaluators)
    return registry


def build_default_evaluator_registry(
    *,
    repository: Any | None = None,
    default_model: Any = None,
    extra_evaluators: dict[str, MetricEvaluator] | None = None,
) -> dict[str, MetricEvaluator]:
    """Every config-free DEEPEVAL_CATALOG key at its class-default
    threshold, plus trajectory/handoff/handoff_chain — for discovery or
    manual programmatic use, NOT used by the job handler (which needs
    per-suite thresholds/config; see build_suite_evaluator_registry)."""
    registry: dict[str, MetricEvaluator] = {}

    for key, spec in DEEPEVAL_CATALOG.items():
        if spec.required_config:
            continue
        registry[key] = build_deepeval_evaluator(key, model=default_model)

    registry["trajectory"] = TrajectoryEvaluator()
    if repository is not None:
        registry["handoff"] = HandoffEvaluator(repository)
        registry["handoff_chain"] = ChainHandoffEvaluator(repository)

    if extra_evaluators:
        registry.update(extra_evaluators)
    return registry
