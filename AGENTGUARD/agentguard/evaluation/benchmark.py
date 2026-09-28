"""ModelBenchmarkEngine — Phase 8 (design doc §11): runs one suite
through N models via a caller-supplied `model_call_fn` and recommends
one against an EXPLICIT objective (never a single blended "best
model" score — see the design doc's own critique of a bare leaderboard
table leaving the actual decision to a human reading it).

`model_call_fn` is deliberately caller-supplied rather than hardcoded
to litellm: a caller wanting governed calls (cost ceilings, fallback)
can supply one that goes through
agentguard.tracing.litellm_wrap.traced_acompletion inside a monitored
run; a caller running a standalone benchmark outside any @monitor
context can supply a bare `litellm.acompletion` wrapper instead. This
engine has no opinion on that, matching how DeepEvalEvaluator/
RagasEvaluator don't dictate the judge model's client library either.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..models import EvaluationSuite, ModelBenchmark, ModelBenchmarkResult, Recommendation
from ..reliability.model_profile import ModelProfileEngine
from ..storage.repository import RunRepository
from .engine import EvaluationEngine
from .evaluators.base import EvalCase

logger = logging.getLogger("agentguard.evaluation.benchmark")


@dataclass
class ModelCallResult:
    actual_output: Any
    cost_usd: float | None = None
    latency_ms: float = 0.0
    tokens_input: int | None = None
    """Real token count from the provider call, when the caller's
    model_call_fn can report it — None (never guessed) otherwise.
    Only consumed by the best_long_context objective."""


ModelCallFn = Callable[[str, EvalCase], Awaitable[ModelCallResult]]

SUPPORTED_OBJECTIVES = (
    "cheapest_above_quality_threshold",
    "fastest_above_quality_threshold",
    "highest_quality_within_budget",
    "best_tool_calling_reliability",
    "best_long_context",
)
"""best_long_context is explicitly long-context-SHAPED, not a claim
about a purpose-built long-context benchmark dataset (see the design
doc §26's own "not ready for the objective-query layer yet" note about
what a REAL long-context benchmark needs): it ranks candidates by
quality among those whose real avg_tokens_input (from
ModelCallResult.tokens_input) clears `min_tokens_input` — a genuine,
non-fabricated filter over whatever cases the caller actually
benchmarked with, not a claim that this dataset IS a long-context
benchmark suite."""


@dataclass
class ModelRecommendation:
    benchmark_id: str
    objective: str
    model: str | None
    reasoning: str
    alternatives: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    recommendation_id: str | None = None
    """The persisted Recommendation row's id, set once save_recommendation()
    has run — None only for the "no model met the objective" early return,
    which never saves a Recommendation at all."""


class ModelBenchmarkEngine:
    def __init__(self, repository: RunRepository, evaluation_engine: EvaluationEngine, model_call_fn: ModelCallFn) -> None:
        self._repository = repository
        self._evaluation_engine = evaluation_engine
        self._model_call_fn = model_call_fn

    async def run(
        self,
        suite: EvaluationSuite,
        cases: list[EvalCase],
        models: list[str],
        *,
        dataset_version_id: str | None = None,
        workspace_id: str | None = None,
        benchmark: ModelBenchmark | None = None,
    ) -> ModelBenchmark:
        """`benchmark`: when the caller (e.g. the dashboard API route) has
        already constructed+saved the ModelBenchmark row itself — to hand
        the id back to the client immediately, before the slow per-model
        LLM calls below even start — reuse that row instead of creating a
        second one. When omitted (the original, still-supported call
        shape), this method creates and saves it as before."""
        if benchmark is None:
            benchmark = ModelBenchmark(
                workspace_id=workspace_id, dataset_version_id=dataset_version_id, suite_id=suite.id, models=list(models)
            )
            await self._repository.save_model_benchmark(benchmark)

        for model in models:
            call_results = await asyncio.gather(
                *(self._safe_model_call(model, case) for case in cases)
            )
            # A failed (model, case) call comes back as None (logged inside
            # _safe_model_call) instead of raising -- one bad case or one
            # bad model must never abort every other case/model in the
            # benchmark, and a call that errored out must never be scored
            # as if `None`/an error string were the model's real answer
            # (that would misreport a call failure as a bad response).
            successful = [(case, cr) for case, cr in zip(cases, call_results) if cr is not None]
            if not successful:
                continue  # every case failed for this model -- nothing to evaluate

            model_cases = [
                EvalCase(
                    input=case.input,
                    actual_output=call_result.actual_output,
                    expected_output=case.expected_output,
                    retrieval_context=case.retrieval_context,
                    tools_called=case.tools_called,
                    source_run_id=case.source_run_id,
                    trace_steps=case.trace_steps,
                )
                for case, call_result in successful
            ]

            evaluation_run = await self._evaluation_engine.run_suite(suite, model_cases, workspace_id=workspace_id)
            results = await self._repository.list_evaluation_results(evaluation_run.id)

            n_cases = len(successful)
            for i, result_row in enumerate(results):
                case, call_result = successful[i % n_cases]
                bench_result = ModelBenchmarkResult(
                    benchmark_id=benchmark.id,
                    model=model,
                    example_id=case.source_run_id,
                    evaluation_result_id=result_row["id"],
                    cost_usd=call_result.cost_usd,
                    latency_ms=call_result.latency_ms,
                    tokens_input=call_result.tokens_input,
                )
                await self._repository.save_model_benchmark_result(bench_result)

        return benchmark

    async def _safe_model_call(self, model: str, case: EvalCase) -> "ModelCallResult | None":
        """Isolates one (model, case) LLM call: a provider error (bad
        model id, missing/invalid API key, rate limit, timeout, ...) is
        logged and turned into `None` here instead of propagating out of
        run() -- otherwise a single failing call aborts the ENTIRE
        benchmark (every other case, every other model), and a retry of
        the whole job re-spends real money re-querying every model that
        had already succeeded, only to fail at the same call again."""
        try:
            return await self._model_call_fn(model, case)
        except Exception:
            logger.exception(
                "model call failed during benchmark: model=%r source_run_id=%r",
                model, case.source_run_id,
            )
            return None

    async def recommend(
        self,
        benchmark_id: str,
        objective: str,
        *,
        quality_metric: str | None = None,
        quality_threshold: float = 0.7,
        cost_budget_usd: float | None = None,
        min_tokens_input: int | None = None,
    ) -> ModelRecommendation:
        if objective not in SUPPORTED_OBJECTIVES:
            raise ValueError(f"unsupported objective {objective!r}; supported: {SUPPORTED_OBJECTIVES}")
        if objective != "best_tool_calling_reliability" and quality_metric is None:
            raise ValueError(f"objective {objective!r} requires quality_metric")

        benchmark = await self._repository.get_model_benchmark(benchmark_id)

        if objective == "best_tool_calling_reliability":
            return await self._recommend_by_tool_reliability(benchmark, benchmark_id)

        bench_rows = await self._repository.list_model_benchmark_results(benchmark_id)

        per_model: dict[str, list[dict[str, Any]]] = {}
        for row in bench_rows:
            eval_result = await self._repository.get_evaluation_result(row["evaluation_result_id"])
            if eval_result is None or eval_result.get("metric") != quality_metric or not eval_result.get("available"):
                continue
            per_model.setdefault(row["model"], []).append({**row, "score": eval_result.get("score")})

        candidates = []
        for model, rows in per_model.items():
            scores = [r["score"] for r in rows if r["score"] is not None]
            if not scores:
                continue
            costs = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
            tokens = [r["tokens_input"] for r in rows if r.get("tokens_input") is not None]
            candidates.append(
                {
                    "model": model,
                    "avg_score": sum(scores) / len(scores),
                    "avg_cost": sum(costs) / len(costs) if costs else None,
                    "avg_latency": sum(r["latency_ms"] for r in rows) / len(rows),
                    "avg_tokens_input": sum(tokens) / len(tokens) if tokens else None,
                    "evidence_ids": [r["id"] for r in rows],
                }
            )

        if objective == "cheapest_above_quality_threshold":
            eligible = sorted(
                (c for c in candidates if c["avg_score"] >= quality_threshold and c["avg_cost"] is not None),
                key=lambda c: c["avg_cost"],
            )
        elif objective == "fastest_above_quality_threshold":
            eligible = sorted(
                (c for c in candidates if c["avg_score"] >= quality_threshold), key=lambda c: c["avg_latency"]
            )
        elif objective == "highest_quality_within_budget":
            eligible = sorted(
                (c for c in candidates if cost_budget_usd is None or (c["avg_cost"] or 0.0) <= cost_budget_usd),
                key=lambda c: -c["avg_score"],
            )
        else:  # best_long_context
            eligible = sorted(
                (
                    c for c in candidates
                    if c["avg_tokens_input"] is not None
                    and (min_tokens_input is None or c["avg_tokens_input"] >= min_tokens_input)
                ),
                key=lambda c: -c["avg_score"],
            )

        if not eligible:
            return ModelRecommendation(
                benchmark_id=benchmark_id, objective=objective, model=None,
                reasoning="no model met the objective's constraints",
            )

        best = eligible[0]
        alternatives = [c["model"] for c in eligible[1:]]
        reasoning = (
            f"{best['model']}: avg_score={best['avg_score']:.3f} on '{quality_metric}', "
            f"avg_cost_usd={best['avg_cost']}, avg_latency_ms={best['avg_latency']:.1f} — objective={objective}"
        )

        recommendation = Recommendation(
            workspace_id=(benchmark or {}).get("workspace_id"),
            kind="model",
            subject_id=benchmark_id,
            recommendation={"model": best["model"], "objective": objective, "alternatives": alternatives},
            reasoning=reasoning,
            evidence_ids=best["evidence_ids"],
        )
        await self._repository.save_recommendation(recommendation)

        return ModelRecommendation(
            benchmark_id=benchmark_id, objective=objective, model=best["model"],
            reasoning=reasoning, alternatives=alternatives, evidence_ids=best["evidence_ids"],
            recommendation_id=recommendation.id,
        )

    async def _recommend_by_tool_reliability(
        self, benchmark: dict[str, Any] | None, benchmark_id: str
    ) -> ModelRecommendation:
        """Reuses ModelProfileEngine's own RELIABLE/DEGRADED/UNRELIABLE/
        INSUFFICIENT_DATA classification directly (design doc §11) —
        this objective is about a model's REAL historical tool-calling
        success rate across every call this workspace has ever made to
        it, not just this one benchmark's cases, so it reads from
        TraceStep(kind="llm_call") history via the same engine
        `/api/v2/models` already uses, rather than re-deriving a
        parallel notion of "reliability" from benchmark rows."""
        models = list((benchmark or {}).get("models") or [])
        engine = ModelProfileEngine(self._repository)

        candidates = []
        for model in models:
            profile = await engine.profile(model)
            if profile.reliability == "INSUFFICIENT_DATA" or profile.success_rate is None:
                continue
            candidates.append(profile)

        if not candidates:
            return ModelRecommendation(
                benchmark_id=benchmark_id, objective="best_tool_calling_reliability", model=None,
                reasoning="no candidate model has enough call history for a reliability verdict",
            )

        candidates.sort(key=lambda p: (-p.success_rate, p.latency_mean_ms or float("inf")))
        best = candidates[0]
        alternatives = [p.model for p in candidates[1:]]
        reasoning = (
            f"{best.model}: success_rate={best.success_rate:.3f} ({best.reliability}) over "
            f"{best.sample_count} historical calls, latency_mean_ms={best.latency_mean_ms} — "
            "objective=best_tool_calling_reliability"
        )

        recommendation = Recommendation(
            workspace_id=(benchmark or {}).get("workspace_id"),
            kind="model",
            subject_id=benchmark_id,
            recommendation={"model": best.model, "objective": "best_tool_calling_reliability", "alternatives": alternatives},
            reasoning=reasoning,
            evidence_ids=[],  # evidence is ModelProfileEngine's own aggregate, not discrete result rows
        )
        await self._repository.save_recommendation(recommendation)

        return ModelRecommendation(
            benchmark_id=benchmark_id, objective="best_tool_calling_reliability", model=best.model,
            reasoning=reasoning, alternatives=alternatives, evidence_ids=[],
            recommendation_id=recommendation.id,
        )
