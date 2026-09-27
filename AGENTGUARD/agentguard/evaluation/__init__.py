"""AI Evaluation Platform — Phase 1: core evaluator abstraction + storage.

See the "AgentGuard Eval Platform" design doc for the full architecture
this grows into (dataset validation, trajectory evaluation, model
benchmarking, evaluation recommendation). This package's Phase 1 scope
is intentionally narrow: prove the trace-native pipeline end to end —
build an EvalCase from already-persisted Run/TraceStep data (no new
instrumentation), score it with a MetricEvaluator, persist the result —
with no external judge-model dependency yet (that's the DeepEval
adapter, a later phase).

Distinct on purpose from `agentguard.evaluators` (that package scores a
completed Run against its Policy for the Decision Engine — a different
subsystem that happens to share the word "evaluator").
"""
from ..models import EvaluationResult, EvaluationRun, EvaluationSuite, SuiteMetric
from .diagnose import EvaluationDiagnosis, diagnose_evaluation_failure
from .engine import EvaluationEngine, build_eval_case_from_run
from .evaluators.base import EvalCase, MetricEvaluator
from .evaluators.custom import CustomEvaluator
from .evaluators.deepeval_adapter import DeepEvalEvaluator
from .evaluators.handoff import ChainHandoffEvaluator, HandoffEvaluator
from .evaluators.ragas_adapter import RagasEvaluator
from .evaluators.trajectory import TrajectoryEvaluator, TrajectoryFinding, TrajectoryJudgeVerdict, compute_step_count_baseline
from .metrics_catalog import (
    DEEPEVAL_CATALOG,
    MissingMetricConfigError,
    UnknownMetricError,
    build_deepeval_evaluator,
    list_deepeval_catalog,
)
from .registry import build_default_evaluator_registry, build_suite_evaluator_registry

__all__ = [
    "EvaluationSuite",
    "SuiteMetric",
    "EvaluationRun",
    "EvaluationResult",
    "EvalCase",
    "MetricEvaluator",
    "CustomEvaluator",
    "DeepEvalEvaluator",
    "RagasEvaluator",
    "TrajectoryEvaluator",
    "TrajectoryFinding",
    "TrajectoryJudgeVerdict",
    "compute_step_count_baseline",
    "HandoffEvaluator",
    "ChainHandoffEvaluator",
    "EvaluationEngine",
    "build_eval_case_from_run",
    "EvaluationDiagnosis",
    "diagnose_evaluation_failure",
    "DEEPEVAL_CATALOG",
    "list_deepeval_catalog",
    "build_deepeval_evaluator",
    "UnknownMetricError",
    "MissingMetricConfigError",
    "build_suite_evaluator_registry",
    "build_default_evaluator_registry",
]
