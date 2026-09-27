"""MetricEvaluator implementations. `base.py` defines the interface;
`custom.py` is the Phase 1 escape hatch for a plain user function;
`deepeval_adapter.py` (Phase 2, requires `pip install agentguard[deepeval]`)
wraps any DeepEval metric; `trajectory.py` (Phase 6) is the
deterministic-first agent trajectory diagnoser; `handoff.py` (Phase 10)
scores multi-agent handoffs. A Ragas adapter lives in `ragas_adapter.py`.
`builtin.py` is the native, zero-extra-dependency metric suite
(`@agentguard.trace`'s default) — answer relevancy, faithfulness,
hallucination, context precision/recall, toxicity, bias, coherence,
task completion, tool correctness, PII leakage, cost/latency efficiency."""
from .base import EvalCase, MetricEvaluator
from .builtin import (
    ALL_BUILTIN_METRIC_CLASSES,
    AnswerRelevancyMetric,
    BiasMetric,
    CoherenceMetric,
    ContextPrecisionMetric,
    ContextRecallMetric,
    CostEfficiencyMetric,
    FaithfulnessMetric,
    HallucinationMetric,
    LatencyEfficiencyMetric,
    LLMJudgeMetric,
    PIILeakageMetric,
    TaskCompletionMetric,
    ToolCorrectnessMetric,
    ToxicityMetric,
    default_metric_suite,
)
from .custom import CustomEvaluator
from .deepeval_adapter import DeepEvalEvaluator
from .handoff import ChainHandoffEvaluator, HandoffEvaluator
from .ragas_adapter import RagasEvaluator
from .trajectory import TrajectoryEvaluator, TrajectoryFinding, TrajectoryJudgeVerdict, compute_step_count_baseline

__all__ = [
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
    "LLMJudgeMetric",
    "AnswerRelevancyMetric",
    "FaithfulnessMetric",
    "HallucinationMetric",
    "ContextPrecisionMetric",
    "ContextRecallMetric",
    "ToxicityMetric",
    "BiasMetric",
    "CoherenceMetric",
    "TaskCompletionMetric",
    "PIILeakageMetric",
    "ToolCorrectnessMetric",
    "CostEfficiencyMetric",
    "LatencyEfficiencyMetric",
    "ALL_BUILTIN_METRIC_CLASSES",
    "default_metric_suite",
]
