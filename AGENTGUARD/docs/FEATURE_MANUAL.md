# AgentGuard — Complete Feature Manual

A reference for every shipped capability across the SDK, dashboard, and AI Evaluation Platform, each with a runnable how-to-use example. See also the interactive version: `README.md` for the quickstart, and the published artifact version of this manual for a browsable UI.

## Table of contents

- [0. Install & Setup](#0-install--setup)
- [0. Configuration](#0-configuration)
- [1. @monitor & Policy](#1-monitor--policy)
- [2. Retry / Replan](#2-retry--replan)
- [3. Human Approval](#3-human-approval)
- [4. Risk Scoring](#4-risk-scoring)
- [5. Root Cause](#5-root-cause)
- [6. Reliability Report](#6-reliability-report)
- [7. Tool & Model Reliability Profiles](#7-tool--model-reliability-profiles)
- [8. Agent Behavior Fingerprinting](#8-agent-behavior-fingerprinting)
- [9. Cross-run Failure Clustering](#9-cross-run-failure-clustering)
- [10. Checkpoints & Rollback](#10-checkpoints--rollback)
- [11. Counterfactual Analysis](#11-counterfactual-analysis)
- [12. Audit Trail](#12-audit-trail)
- [13. Replay](#13-replay)
- [14. OpenTelemetry](#14-opentelemetry)
- [15. Fine-grained Tracing](#15-fine-grained-tracing)
- [16. Regression Comparison](#16-regression-comparison)
- [17. CI/CD Reliability Gate](#17-cicd-reliability-gate)
- [18. Auto-Improvement Pipeline](#18-auto-improvement-pipeline)
- [19. MCP Client / Server](#19-mcp-client--server)
- [20. A2A Agent Card](#20-a2a-agent-card)
- [21. LLM Gateway](#21-llm-gateway)
- [22. Evaluation Platform — Core](#22-evaluation-platform--core)
- [23. DeepEval / Ragas Adapters](#23-deepeval--ragas-adapters)
- [24. Golden Dataset Validation](#24-golden-dataset-validation)
- [25. Evidence-Source Connectors](#25-evidence-source-connectors)
- [26. Trajectory Evaluation](#26-trajectory-evaluation)
- [27. Multi-agent Handoff Evaluation](#27-multi-agent-handoff-evaluation)
- [28. Evaluation Metric Recommendation](#28-evaluation-metric-recommendation)
- [29. Model Benchmarking](#29-model-benchmarking)
- [30. Evaluator-of-Evaluators](#30-evaluator-of-evaluators)
- [31. Evaluation Failure Diagnosis](#31-evaluation-failure-diagnosis)
- [32. Async Job Queue](#32-async-job-queue)
- [33. Auth & Workspaces](#33-auth--workspaces)
- [34. Dashboard Tour](#34-dashboard-tour)
- [35. CLI Reference](#35-cli-reference)
- [36. REST API Reference](#36-rest-api-reference)
- [37. Design Principles](#37-design-principles)

---

## 0. Install & Setup

```bash
uv venv --python 3.12
. .venv/Scripts/activate        # Windows; source .venv/bin/activate on macOS/Linux

uv pip install -e ".[server,dev,llm,otel-otlp,mcp,litellm,deepeval,pdf,docx,mongo]"

docker run -d --name agentguard-pg \
  -e POSTGRES_USER=agentguard -e POSTGRES_PASSWORD=agentguard -e POSTGRES_DB=agentguard \
  -p 5434:5432 postgres:16

cp .env.example .env
export AGENTGUARD_DATABASE_URL=postgresql://agentguard:agentguard@localhost:5434/agentguard
python -m agentguard.cli init-db

cd dashboard-src && npm install && npm run build && cd ..
uvicorn server.api:app --reload --port 8000   # open http://localhost:8000
```

Avoid installing `ragas` into the same venv as `litellm`/`deepeval` — a known `langchain-community`/`langchain-core` version conflict; give it its own venv.

## 0. Configuration

| Variable | Purpose |
|---|---|
| `AGENTGUARD_DATABASE_URL` | Postgres connection string |
| `AGENTGUARD_API_KEY` | Default key for the `AgentGuard(...)` client |
| `ANTHROPIC_API_KEY` | Activates the real LLM-as-Judge / prompt-diagnosis provider |
| `AGENTGUARD_DSPY_MODEL` | Activates the real DSPy auto-improvement optimizer |
| `AGENTGUARD_OTEL_EXPORTER` | `console` (default) or `otlp` |
| `AGENTGUARD_VALIDATION_DB_URL` / `_QUERY` | Enables the `dataset_validation` job kind in `agentguard worker` |

Without `ANTHROPIC_API_KEY`/`AGENTGUARD_DSPY_MODEL`, every LLM-backed feature (judge, diagnosis, optimizer) runs on a clearly-labeled deterministic stand-in — never a silent fake real-model call.

## 1. @monitor & Policy

**@monitor decorator** — wraps any async or sync function. Drives the Decision Engine: `RUNNING → EVALUATING → CONTINUE | RETRY | REPLAN | HUMAN | STOP | FAILED | ROLLED_BACK`.

```python
from agentguard import monitor, Policy

@monitor(policy=Policy(max_cost=60000, require_approval=["payment"]))
async def checkout_agent(task: str) -> str:
    ...
    return "order placed"
```

**Policy** — declarative safety boundary: budget ceiling, forbidden/approval-gated actions, retry/replan bounds, uncertainty fallback. Versioned — a completed run's policy version never changes retroactively.

```python
from agentguard import Policy

policy = Policy(
    max_cost=60000,
    require_approval=["payment"],
    forbidden_actions=["drop_database"],
    default_on_uncertain="human",
    retry_limit=2, on_retry_exhausted="replan", max_replans=1,
    human_timeout_s=120, on_tool_exhausted="replan",
)
```

**Named, versioned policies** — a policy control portal, saves new versions without ever mutating a prior one.

```python
from agentguard.policy.registry import PolicyRegistry

registry = PolicyRegistry(repository)
await registry.create("checkout_policy", Policy(max_cost=60000, retry_limit=2))          # v1
await registry.save_new_version("checkout_policy", Policy(max_cost=60000, retry_limit=5)) # v2
```

## 2. Retry / Replan

**Retry** — raise `TransientError`; bounded by `policy.retry_limit`, falls through to `on_retry_exhausted`.

```python
from agentguard import monitor, update_state, Policy
from agentguard.errors import TransientError

@monitor(policy=Policy(max_cost=60000, retry_limit=2))
async def my_agent(task):
    if first_attempt_failed():
        raise TransientError("tool timed out")
    update_state(max_budget=52000)
    return "done"
```

**Replan** — raise `ReplanRequested` with fresh context; bounded by `policy.max_replans`.

```python
from agentguard import monitor, get_replan_context, Policy
from agentguard.errors import ReplanRequested

@monitor(policy=Policy(max_cost=60000, max_replans=1))
async def my_agent(task):
    context = get_replan_context()  # {} on first attempt
    if goal_has_drifted():
        raise ReplanRequested("goal drift detected", context={"new_strategy": "..."})
```

## 3. Human Approval

**perform_action** — blocks on a real, WebSocket-resolved human review for any action listed in `policy.require_approval`; also triggered automatically for low-confidence + high-impact evaluations. Deterministic timeout (`human_timeout_s`) → deny, never hangs forever.

```python
from agentguard import monitor, perform_action, Policy

@monitor(policy=Policy(max_cost=60000, require_approval=["payment"]))
async def checkout_agent(task):
    await perform_action("payment", amount=500)  # raises HumanRejected on reject/timeout
    return "payment approved"
```

Resolve pending requests from the dashboard's WebSocket channel, or via `POST /api/runs/{run_id}/human-decision`.

## 4. Risk Scoring

**RiskEngine** — a documented, deterministic formula (`0.45·impact + 0.30·policy_violation + 0.15·uncertainty + 0.05·goal_drift + 0.05·tool_reliability`) — never a black box. Every score carries its own factor/weight breakdown.

```bash
# Automatic on every @monitor'd run. Inspect via:
agentguard report <run_id>
# or the dashboard's Risk & Confidence page
```

## 5. Root Cause

**RootCauseEngine** — a causal diff over the ordered state-snapshot history (S1, S2, S3...) of a run — reports the *earliest* deviation, not just where the final evaluator noticed it.

```python
root_cause = await repository.get_root_cause(run_id)
# {"earliest_deviation": "S3", "expected": {...}, "observed": {...},
#  "confidence": 0.97, "explanation": "max_budget disappeared during state transition S3"}
```

## 6. Reliability Report

**Six-dimension report** — Correctness, Goal Completion, Constraint Adherence, Decision Consistency, Tool Usage, Behavioral Reliability. `available:false` — never a fabricated number — for a dimension with no evidence.

```bash
agentguard report <run_id> [--json]
# GET /api/v2/runs/{run_id}/reliability-report
```

## 7. Tool & Model Reliability Profiles

**ToolProfileEngine / ModelProfileEngine** — success/failure/timeout/retry/duplicate-call rates, mean/p95 latency, `RELIABLE`/`DEGRADED`/`UNRELIABLE`/`INSUFFICIENT_DATA` classification (min sample size gated). Alternative-tool/model recovery only ever substitutes an explicitly registered fallback.

```python
from agentguard.tools.registry import get_registry
import agentguard

get_registry().register_tool("search_api", my_search_fn)
get_registry().register_tool("search_api_backup", my_backup_search_fn)
get_registry().register_alternative("search_api", "search_api_backup", reliability_threshold=0.8)

@agentguard.monitor(policy=agentguard.Policy(max_cost=60000, on_tool_exhausted="replan"))
async def shopping_agent(task):
    result = await agentguard.context.call_tool("search_api", "laptop", primary_attempts=2)
# Dashboard: Tools / Models pages · GET /api/v2/tools, /api/v2/models
```

## 8. Agent Behavior Fingerprinting

**FingerprintEngine** — baseline vs. current-run deviation across tool calls, retries, latency, completion/failure rate, human-intervention frequency — gated behind a minimum sample size.

```
GET /api/v2/agents/{agent_name}/fingerprint
# Dashboard: Agent Behavior page — select an agent from the dropdown
```

## 9. Cross-run Failure Clustering

**FailureClusterEngine ("Problems")** — groups failed runs by exception+code-location (or root-cause explanation) across the whole history, with a documented priority score. Feeds straight into the improvement pipeline.

```
GET  /api/v2/problems
POST /api/v2/problems/{cluster_key}/status       {"status": "resolved", "triaged_by": "..."}
POST /api/v2/problems/{cluster_key}/propose-fix  {"triaged_by": "..."}
# Dashboard: Problems page
```

## 10. Checkpoints & Rollback

**rollback() / seed_recovery_state()** — a hashed, JSON-safe snapshot per state transition. Roll back to any checkpoint and resume execution seeded from the restored state.

```python
from agentguard.recovery import rollback, seed_recovery_state

result = await rollback(repository, run_id, "S2")   # a Checkpoint.id or its label
seed_recovery_state(result.restored_state, parent_run_id=run_id, checkpoint_id=result.checkpoint.id)
recovered = await safe_agent("retry the task")       # a normal @monitor call
# Dashboard: Checkpoints / Rollback page · POST /api/v2/runs/{id}/rollback
```

## 11. Counterfactual Analysis

**generate_counterfactual()** — "what would have happened" reconstructed from a real, separately executed recovery run — always labeled ACTUAL vs. COUNTERFACTUAL, never a guess.

```
GET /api/v2/runs/{run_id}/counterfactual
# Dashboard: Run Detail → Recovery tab
```

## 12. Audit Trail

**SHA-256 hash chain** — `event_hash = SHA256(canonical_json(payload) + previous_hash)` over every meaningful event. Independently verifiable — recomputes and reports the first tampered event, if any.

```bash
agentguard verify-audit <run_id> [--json]
agentguard tail <run_id> [--follow]
# POST /api/v2/runs/{run_id}/audit/verify · Dashboard: Audit page
```

## 13. Replay

**SAFE/DRY-RUN replay** — reconstructs the full decision context at every step from persisted data only — never re-executes agent code or a tool callable.

```python
from agentguard.replay import replay
session = await replay(repository, run_id)   # reads only
```
```bash
agentguard replay <run_id> [--json]
# Dashboard: Replay / Compare page
```

## 14. OpenTelemetry

**Spans + console/OTLP export** — `agentguard.invoke_agent`, `agentguard.decision`, and per-`TraceStep` spans. Console exporter by default; OTLP/HTTP with the `otel-otlp` extra.

```bash
export AGENTGUARD_OTEL_EXPORTER=otlp
export AGENTGUARD_OTEL_OTLP_ENDPOINT=http://localhost:4318/v1/traces
```

## 15. Fine-grained Tracing

**@traceable / wrap_llm_client** — captures every function/LLM call as a `TraceStep` with real call-site `code_file`/`code_function`/`code_lineno` — populated for *every* call, success or failure, not only exceptions.

```python
from agentguard import traceable, wrap_llm_client, monitor, Policy
import openai

@traceable
async def search_kb(query: str) -> str:
    ...

@monitor(policy=Policy(max_cost=60000))
async def agent(task: str) -> str:
    client = wrap_llm_client(openai.AsyncOpenAI())
    response = await client.chat.completions.create(model="gpt-4o-mini", messages=[...])
    return await search_kb(task)
```

## 16. Regression Comparison

**compare_runs()** — factual per-metric deltas between any two runs (or batches via `RegressionCorpus`) — every metric shows both sides, no winner ever declared.

```python
from agentguard.regression.compare import compare_runs
result = await compare_runs(repository, run_a_id, run_b_id, label_a="v1", label_b="v2")
```
```bash
agentguard compare <run_a> <run_b> [--json]
# Dashboard: Replay / Compare page
```

## 17. CI/CD Reliability Gate

**agentguard ci-gate** — blocks a deployment if any protected metric regresses past a threshold; unavailable metrics are skipped, never scored pass/fail.

```bash
agentguard ci-gate --baseline-runs=<id1,id2> --candidate-runs=<id3,id4> --threshold=5
echo $?   # 0 = allowed, non-zero = blocked
```

## 18. Auto-Improvement Pipeline

**propose_improvement()** — root cause → deterministic recommendation → optimizer (real DSPy or a labeled stand-in) → candidate → historical regression evaluation. **Nothing ever auto-deploys** — only an explicit human `approve_candidate()`/`reject_candidate()` call changes status.

```python
from agentguard.improve.workflow import propose_improvement, approve_candidate

candidate, evaluation = await propose_improvement(
    repository, failed_run_id, corpus_run_ids=[failed_run_id], candidate_agent_fn=my_fixed_agent
)
await approve_candidate(repository, candidate.id, approved_by="dev@example.com")
# Dashboard: Recommendations page · GET /api/improvements
```

## 19. MCP Client / Server

**register_mcp_server() + agentguard mcp-serve** — client: register any FastMCP-compatible server's tools straight into the ToolRegistry. Server: exposes a workspace's own governance data (recent runs, reliability report, audit verification, tool reliability) read-only over stdio.

```python
from agentguard.mcp import register_mcp_server
await register_mcp_server("python", args=["my_mcp_server.py"], prefix="kb")
```
```bash
agentguard mcp-serve --api-key <workspace-api-key>
```

## 20. A2A Agent Card

**build_agent_card()** — a small, honest identity manifest — no fabricated skills or invocable endpoint unless an author explicitly declares them.

```
GET /api/v2/agents/{agent_name}/card
```

## 21. LLM Gateway

**traced_completion / traced_acompletion** — any LiteLLM-supported provider, with real per-call cost/latency/token tracking, policy-driven allow-lists and ceilings, and reliability-gated automatic fallback to a registered alternative model.

```python
from agentguard import monitor, Policy, LLMGatewayPolicy, ModelAlternative
from agentguard.tracing import traced_acompletion

gateway = LLMGatewayPolicy(
    allowed_models=["gpt-4o-mini", "claude-haiku-4-5"],
    max_cost_per_call_usd=0.05,
    fallback_chain=[ModelAlternative(primary_model="gpt-4o-mini", fallback_model="claude-haiku-4-5")],
)

@monitor(policy=Policy(max_cost=60000, llm_gateway=gateway))
async def agent(task: str) -> str:
    response = await traced_acompletion(model="gpt-4o-mini", messages=[{"role": "user", "content": task}])
    return response.choices[0].message.content
# Dashboard: Models page · GET /api/v2/models
```

## 22. Evaluation Platform — Core

Trace-native evaluation: evaluators read already-captured `TraceStep` data — no re-instrumentation required.

**MetricEvaluator / EvaluationEngine**

```python
from agentguard.evaluation import (
    EvaluationEngine, EvaluationSuite, SuiteMetric, build_eval_case_from_run, CustomEvaluator,
)

suite = EvaluationSuite(name="support_bot_suite", metrics=[SuiteMetric(evaluator="quality", threshold=0.7)])
await repository.save_evaluation_suite(suite)

run = await repository.get_run(run_id)
steps = await repository.list_trace_steps_for_run(run_id)
case = build_eval_case_from_run(run, steps)

engine = EvaluationEngine(repository, {"quality": CustomEvaluator("quality", my_scoring_fn)})
evaluation_run = await engine.run_suite(suite, [case])
# Dashboard: Eval Runs page · GET /api/v2/eval-runs, /api/v2/suites
```

## 23. DeepEval / Ragas Adapters

**DeepEvalEvaluator / RagasEvaluator** — wraps any DeepEval metric (Faithfulness, Answer/Contextual Relevancy/Precision/Recall, G-Eval, ...) or Ragas single-turn metric as a `MetricEvaluator` — never touches their own tracing/dataset machinery.

```python
from deepeval.metrics import FaithfulnessMetric
from agentguard.evaluation import DeepEvalEvaluator

evaluator = DeepEvalEvaluator("deepeval.faithfulness", FaithfulnessMetric(threshold=0.8))
engine = EvaluationEngine(repository, {"deepeval.faithfulness": evaluator})
```

## 24. Golden Dataset Validation

**GoldenDatasetValidator** — checks a question/expected-answer pair against real retrieved evidence: no evidence → `UNSUPPORTED` deterministically (judge never called); an uncited or out-of-set citation → rejected as `NEEDS_REVIEW`; a `VERIFIED` verdict is re-checked by a second, adversarially-framed judge call before it's trusted.

```python
from agentguard.evaluation.dataset import (
    Dataset, DatasetVersion, DatasetExample, GoldenDatasetValidator, StaticEvidenceSource, make_llm_judge,
)
from agentguard.llm.provider import get_default_provider

dataset = Dataset(name="refund_policy_qa"); await repository.save_dataset(dataset)
version = DatasetVersion(dataset_id=dataset.id, version=1); await repository.save_dataset_version(version)
example = DatasetExample(dataset_version_id=version.id, question="refund window?", expected_answer="30 days")
await repository.save_dataset_example(example)

source = StaticEvidenceSource({"refund window?": [("refunds allowed within 30 days", 0.92)]})
validator = GoldenDatasetValidator(repository, source, make_llm_judge(get_default_provider()))
validated = await validator.validate_example(example)
# validated.golden_status -> VERIFIED | INCORRECT | UNSUPPORTED | AMBIGUOUS | OUTDATED | NEEDS_REVIEW
# Dashboard: Datasets → Dataset Quality page
```

## 25. Evidence-Source Connectors

A source's query shape is fixed at registration time by the developer — never an LLM-generated query against a live database.

```python
from agentguard.evaluation.dataset import (
    PostgresEvidenceSource, DocumentEvidenceSource, URLEvidenceSource, MongoEvidenceSource, VectorEvidenceSource,
)

PostgresEvidenceSource(dsn, "SELECT text, score FROM policies WHERE to_tsvector(text) @@ plainto_tsquery($1)")
DocumentEvidenceSource(["policy.pdf", "handbook.docx"])                 # real PDF/DOCX chunking + word-overlap ranking
URLEvidenceSource(url, extract_fn=lambda data: [(p["text"], p["score"]) for p in data["passages"]])
MongoEvidenceSource(conn_str, "db", "policies", filter_fn=lambda q: {"$text": {"$search": q}})
VectorEvidenceSource(query_fn=my_pinecone_or_chroma_search)              # wraps any vector DB's own search
```

## 26. Trajectory Evaluation

**TrajectoryEvaluator** — deterministic-first: duplicate-tool-call, missing-recovery, and step-count-anomaly findings computed *before* any judge call. A judge is only invoked for genuinely subjective residue.

```python
from agentguard.evaluation import TrajectoryEvaluator, compute_step_count_baseline

mean, std, n = await compute_step_count_baseline(repository, "support_bot")
evaluator = TrajectoryEvaluator(baseline_mean_steps=mean, baseline_std_steps=std)
engine = EvaluationEngine(repository, {"trajectory": evaluator})
```

## 27. Multi-agent Handoff Evaluation

**HandoffEvaluator / ChainHandoffEvaluator** — compares a parent run's `final_state` against a child's `initial_state` (single hop), or walks the *full* `parent_run_id` chain back to root and reports the weakest hop as the overall score.

```python
from agentguard.evaluation import HandoffEvaluator, ChainHandoffEvaluator

engine = EvaluationEngine(repository, {
    "handoff": HandoffEvaluator(repository),
    "handoff_chain": ChainHandoffEvaluator(repository),
})
```

## 28. Evaluation Metric Recommendation

**EvaluationRecommendationEngine** — recommends a starter metric suite from an agent's *actual* recently traced shape (retrieval/SQL/handoff-shaped calls) — every recommended metric carries a real, falsifiable reason.

```python
from agentguard.evaluation.recommend import EvaluationRecommendationEngine

suite = await EvaluationRecommendationEngine(repository).recommend("support_bot")
# GET /api/v2/suites/recommend?agent_name=support_bot · Dashboard: Suites page
```

## 29. Model Benchmarking

**ModelBenchmarkEngine** — runs the same suite through N models and recommends one against an explicit objective — never a single blended score.

```python
from agentguard.evaluation.benchmark import ModelBenchmarkEngine

bench = ModelBenchmarkEngine(repository, engine, my_model_call_fn)
benchmark = await bench.run(suite, cases, ["gpt-4o-mini", "gpt-4o", "claude-haiku-4-5"])
rec = await bench.recommend(benchmark.id, "cheapest_above_quality_threshold",
                             quality_metric="quality", quality_threshold=0.8)
# objectives: cheapest_above_quality_threshold · fastest_above_quality_threshold ·
#             highest_quality_within_budget · best_tool_calling_reliability · best_long_context
# Dashboard: Model Benchmarks page · GET /api/v2/benchmarks
```

## 30. Evaluator-of-Evaluators

**JudgeProfileEngine** — profiles a metric/judge's own reliability by re-running it against real human-labeled calibration examples — same `RELIABLE`/`DEGRADED`/`UNRELIABLE`/`INSUFFICIENT_DATA` discipline as tool/model profiles.

```python
from agentguard.models import JudgeCalibrationExample
from agentguard.reliability.judge_profile import JudgeProfileEngine

await repository.save_judge_calibration_example(
    JudgeCalibrationExample(metric="quality", case_input="...", case_actual_output="...", human_label={"score": 0.9})
)
profile = await JudgeProfileEngine(repository, {"quality": evaluator}).profile("quality")
```

## 31. Evaluation Failure Diagnosis

**diagnose_evaluation_failure()** — for a failing evaluation result: **why** (the metric's own grounded reason), **where in your code** (the real call site — captured for every traced call, not only exceptions), and a **suggested prompt rewrite** (real LLM call if configured, never a fabricated suggestion otherwise).

```python
from agentguard.evaluation import diagnose_evaluation_failure

diagnosis = await diagnose_evaluation_failure(repository, evaluation_result_id)
print(diagnosis.why, diagnosis.code_file, diagnosis.code_lineno, diagnosis.suggested_prompt)

# POST /api/v2/eval-results/{result_id}/diagnose
# Dashboard: Eval Run Detail → "Diagnose" button on any failed row
```

## 32. Async Job Queue

**agentguard worker** — durable queue (real `FOR UPDATE SKIP LOCKED` claim, retry/fail lifecycle) for work too long-running for the in-process fire-and-forget trace writer — e.g. validating a large dataset.

```python
from agentguard.jobs import Worker
from agentguard.models import Job

await repository.enqueue_job(Job(kind="dataset_validation", payload={"dataset_version_id": version.id}))
```
```bash
agentguard worker --poll-interval 2.0
# requires AGENTGUARD_VALIDATION_DB_URL / AGENTGUARD_VALIDATION_QUERY for the
# dataset_validation kind; otherwise it processes only kinds you register yourself:
```
```python
worker = Worker(repository, {"my_kind": my_handler})
await worker.run_forever()
```

## 33. Auth & Workspaces

**Signup / API keys / teams** — full multi-tenant auth: signup, login, password reset, session cookies, API keys (create/rotate/revoke), workspaces, projects, team members. Isolation enforced at the query layer everywhere — a resource in another workspace 404s, indistinguishable from "doesn't exist."

```
POST /api/auth/signup | login | logout | forgot-password | reset-password
GET  /api/auth/me
GET/POST /api/settings/api-keys       POST .../api-keys/{id}/revoke | rotate
GET/POST /api/workspace/members
```

## 34. Dashboard Tour

React app (Vite + React Router).

| Group | Pages |
|---|---|
| — | Overview |
| Observability | Runs/Traces (9 detail tabs), Latency, Tokens & Cost, Errors, Tools, Models |
| Reliability | Evaluations, Risk & Confidence, Agent Behavior |
| Recovery | Interventions, Checkpoints/Rollback, Replay/Compare |
| Governance | Policy Management, Audit |
| Improvement | Problems, Failure Patterns, Recommendations |
| Evaluation Platform | Eval Runs (+Diagnose), Datasets (+Quality), Suites, Model Benchmarks, Eval Recommendations |
| Settings | API Keys, Profile, Team/Users |

```bash
cd dashboard-src && npm run dev     # hot reload, proxies /api to your server
cd dashboard-src && npm run build   # production build into ../dashboard/
```

## 35. CLI Reference

```
agentguard init-db                                   create/upgrade the Postgres schema
agentguard tail <run_id> [--follow]                  chronological audit-event stream
agentguard replay <run_id> [--json]                  SAFE/DRY-RUN reconstruction
agentguard report <run_id> [--json]                  six-dimension Reliability Report
agentguard compare <run_a> <run_b> [--json]          factual per-metric deltas
agentguard ci-gate --baseline-runs=... --candidate-runs=... --threshold=5
agentguard verify-audit <run_id> [--json]            re-verify the SHA-256 hash chain
agentguard worker [--poll-interval 2.0]              durable job-queue worker (blocks)
agentguard mcp-serve --api-key <key>                 read-only MCP server over stdio (blocks)
```

## 36. REST API Reference

Authenticated, workspace-scoped `/api/v2/*`:

```
GET  /api/v2/overview | runs | runs/{id} | latency | tokens | evaluations | risk | recovery
GET  /api/v2/tools | models | agents | failure-patterns | problems
GET  /api/v2/policies | improvements
GET  /api/v2/suites | suites/recommend
GET  /api/v2/eval-runs | eval-runs/{id}
POST /api/v2/eval-results/{id}/diagnose
GET  /api/v2/datasets | datasets/{id}/versions/{v}
GET  /api/v2/benchmarks | benchmarks/{id}
GET  /api/v2/eval-recommendations
```

## 37. Design Principles

No fabricated data · no silent uncertainty-as-success · every score explainable · nothing auto-deploys without a human · isolation enforced server-side · deterministic stand-ins always clearly labeled when no real LLM/DSPy provider is configured.

---

*This manual reflects the codebase as of this session. Re-verify any command against the current repository before relying on it in a script.*
