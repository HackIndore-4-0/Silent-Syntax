# AgentGuard

**A low-code Python SDK and dashboard that make autonomous AI agents observable, governable, and recoverable.**

Wrap any agent function with a single decorator and AgentGuard captures every action, evaluates it against a declarative policy, scores its risk, traces its root cause when things go wrong, and — when it's genuinely unsure — pauses and asks a human before letting the agent continue. Every one of those decisions is persisted to a tamper-evident, hash-chained audit trail and surfaced in a full authenticated dashboard.

```python
from agentguard import monitor, Policy

@monitor(policy=Policy(max_cost=60000, require_approval=["payment"]))
async def checkout_agent(task: str) -> str:
    ...
    return "order placed"
```

That's the entire integration surface for the basic case. Everything else in this document — retries, replans, human approval, risk scoring, root-cause analysis, checkpoints/rollback, hash-chained audit, replay, regression comparison, a CI/CD reliability gate, tool-failure recovery, behavior fingerprinting, and DSPy-optional auto-improvement — builds on the same `@monitor` call.

---

## Table of contents

- [Why AgentGuard](#why-agentguard)
- [Core concepts](#core-concepts)
- [Feature tour](#feature-tour)
- [Install](#install)
- [Quickstart](#quickstart)
- [Configuration](#configuration)
- [SDK usage](#sdk-usage)
- [The CLI](#the-cli)
- [The dashboard](#the-dashboard)
- [REST / WebSocket API](#rest--websocket-api)
- [Project layout](#project-layout)
- [Testing](#testing)
- [Design principles](#design-principles)

---

## Why AgentGuard

Autonomous agents fail in ways that are hard to see and harder to explain after the fact: a budget constraint silently disappears three steps before the evaluator finally notices, a flaky tool gets called five times without anyone knowing, or an agent takes an action nobody would have approved if asked. AgentGuard is a runtime layer that sits between your agent code and the outside world (LLM calls, tool calls, side-effecting actions) and turns every run into:

- a real-time **decision** (continue, retry, replan, escalate to a human, or stop),
- structured, reproducible **evidence** (risk score, confidence, policy findings, root cause),
- a **tamper-evident record** you can replay, audit, and compare against other runs later.

## Core concepts

| Concept | What it is |
|---|---|
| **Run** | One `@monitor`-wrapped execution of an agent function, from `RUNNING` to a terminal state. |
| **Policy** | A declarative safety boundary attached to a run — budget ceiling, forbidden/approval-gated actions, retry/replan bounds, uncertainty fallback. Versioned; a completed run's policy version never changes retroactively. |
| **Decision Engine** | The state machine driving every run: `RUNNING → EVALUATING → CONTINUE \| RETRY \| REPLAN \| HUMAN \| STOP \| FAILED \| ROLLED_BACK`. |
| **Evaluators** | Pluggable checks that produce an `EvalResult` (pass/fail, score, confidence). Ships with a synchronous `ConstraintAdherenceEvaluator` and an async `LLMJudge`. |
| **Risk score** | A documented, deterministic formula (`0.45·impact + 0.30·policy_violation + 0.15·uncertainty + 0.05·goal_drift + 0.05·tool_reliability`) — never a black box. |
| **Root cause** | A causal diff over the ordered state-snapshot history of a run, reporting the *earliest* deviation, not just where the final evaluator noticed it. |
| **Checkpoint** | A hashed, JSON-safe snapshot of agent state, one per state transition, enabling rollback and counterfactual analysis. |
| **Audit trail** | A SHA-256 hash chain (`event_hash = SHA256(canonical_json(payload) + previous_hash)`) over every meaningful event in a run — independently verifiable, tamper-evident. |

## Feature tour

**Runtime control loop**
- `@monitor` decorator (async or sync) wraps any agent function with zero required config.
- RETRY (`TransientError`, bounded by `policy.retry_limit`) and REPLAN (`ReplanRequested`, bounded by `policy.max_replans`), both real Decision Engine states with their own audit rows.
- Live, in-function human approval (`perform_action(...)` blocks on a WebSocket-resolved review) and post-hoc escalation when an evaluation is low-confidence + high-impact.
- Confidence/impact routing table: HIGH+safe → CONTINUE, HIGH+unsafe → STOP, LOW+HIGH-impact → HUMAN, LOW+LOW-impact → `policy.default_on_uncertain`.

**Reliability engine**
- Dynamic per-action **risk scoring** with full factor/weight breakdown.
- **Root-cause analysis** over state-snapshot history.
- A **six-dimension Reliability Report** — Correctness, Goal Completion, Constraint Adherence, Decision Consistency, Tool Usage, Behavioral Reliability — with `available: false` (never a fabricated number) for dimensions a run has no data for.
- **Tool reliability profiles** (success/failure/timeout/retry/duplicate-call rates, mean/p95 latency, `RELIABLE`/`DEGRADED`/`UNRELIABLE`/`INSUFFICIENT_DATA` classification) and **alternative-tool recovery** — only ever substitutes an explicitly registered fallback.
- **Agent behavior fingerprinting** — baseline vs. current-run deviation across tool calls, retries, latency, completion/failure rate, human-intervention frequency, gated behind a minimum sample size.

**Recovery**
- **Checkpoints** per state transition, with independent SHA-256 integrity verification.
- **Rollback** to any checkpoint, recorded as an audit event + a `ROLLED_BACK` decision, with `seed_recovery_state()` to resume execution from the restored state.
- **Counterfactual analysis** — "what would have happened" reconstructed from a real, separately executed recovery run, always labeled `ACTUAL` vs. `COUNTERFACTUAL`.

**Auditability & replay**
- SHA-256 **hash-chained audit trail** covering every run-start/step/policy-check/evaluation/risk/root-cause/decision/checkpoint/human/rollback/completion event; `verify_audit_chain()` recomputes and reports the first tampered event, if any.
- **SAFE/DRY-RUN replay** — reconstructs the full decision context at every step from persisted data only; never re-executes agent code or a tool callable.
- **OpenTelemetry** spans (`agentguard.invoke_agent`, `agentguard.decision`) with console or OTLP/HTTP export.

**Regression & CI**
- **Regression comparison** between any two runs (or two batches, via `RegressionCorpus`) across all reliability dimensions plus risk/confidence/retry/replan/human/failure counts and latency — every metric shows both sides, no winner is ever declared.
- A deterministic **CI/CD reliability gate**: block a deployment if any protected metric regresses past a threshold; unavailable metrics are skipped, never scored as pass/fail.

**Auto-improvement (human-approved, DSPy-optional)**
- Turns a root cause into a structured recommendation (problem / root cause / recommendation / before-after change / expected benefit).
- An optional DSPy-backed optimizer (falls back to a clearly-labeled deterministic stand-in when `dspy` isn't installed/configured) proposes a candidate, which is validated against a historical regression corpus.
- **Nothing ever auto-deploys** — a candidate only ever moves `proposed → approved` or `proposed → rejected`, via an explicit human caller.

**Dashboard & platform**
- Full auth: sign up, log in, password reset, session cookies, API keys (create/rename/rotate/revoke), workspaces, projects, and team members.
- Workspace-level tenant isolation enforced at the query layer — a run/policy/candidate belonging to another workspace 404s, indistinguishable from "doesn't exist."
- A React dashboard (Vite + React Router, see [The dashboard](#the-dashboard) below) covering runs, latency, tokens & cost, errors, tools, evaluations, risk, agent behavior, interventions, checkpoints/rollback, replay/compare, policy management, audit, failure patterns, recommendations, settings, and the full Evaluation Platform below.
- A real Typer **CLI** (`agentguard tail/replay/report/compare/ci-gate/verify-audit/init-db/worker/mcp-serve`).

**Evaluation Platform** (dataset validation, trajectory diagnosis, model benchmarking, prompt-failure diagnosis)
- **Golden dataset validation** against a real connected evidence source (Postgres/Mongo/PDF/DOCX/URL/vector-DB) — deterministic-first, evidence-constrained-judge, adversarial-recheck pipeline; never trusts a bare "looks correct" verdict.
- **Trajectory evaluation** — deterministic duplicate-call/missing-recovery/step-count-anomaly detection over the real `TraceStep` tree, judge escalation only for genuinely subjective residue.
- **Evaluation recommendation** — a starter metric suite inferred from an agent's *actual* traced shape (retrieval/SQL/handoff), never a guessed list.
- **Model benchmarking** — the same suite run through N models via the LiteLLM gateway, with objective-driven recommendation (cheapest/fastest above a quality bar, highest quality within budget, best tool-calling reliability, best long-context).
- **Evaluator-of-evaluators** — judge/metric reliability profiled against real human-labeled calibration examples, same `RELIABLE`/`DEGRADED`/`UNRELIABLE`/`INSUFFICIENT_DATA` discipline as tool/model profiles.
- **Failure diagnosis** — for a failing evaluation result: WHY (the metric's own grounded reason), WHERE in your code (the real call site, captured for every traced call, not only exceptions), and a suggested prompt rewrite (real LLM call if configured, never a fabricated suggestion otherwise).
- **Async job queue** (`agentguard worker`) — a durable, `FOR UPDATE SKIP LOCKED`-based queue for long-running work (large dataset validations), separate from the in-process fire-and-forget trace writer.
- **MCP / A2A interoperability** (`agentguard mcp-serve`) — a read-only MCP server exposing a workspace's governance data, plus an MCP client adapter and a minimal, honest A2A Agent Card.

## Install

```bash
# uv (used throughout this repo's own dev workflow) or plain venv+pip both work
uv venv --python 3.12
# python -m venv .venv                          # plain-venv alternative

. .venv/Scripts/activate        # Windows (PowerShell: .venv\Scripts\Activate.ps1)
# source .venv/bin/activate     # macOS/Linux

uv pip install -e ".[dev]"         # core SDK + pytest
uv pip install -e ".[server]"      # + FastAPI/uvicorn, for the dashboard
```

Optional extras:

```bash
uv pip install -e ".[llm]"         # real Anthropic LLM-as-Judge provider
uv pip install -e ".[otel-otlp]"   # OTLP/HTTP span export
uv pip install -e ".[dspy]"        # real DSPy auto-improvement optimizer
uv pip install -e ".[mcp]"         # MCP client/server (FastMCP) — agentguard/mcp/
uv pip install -e ".[litellm]"     # LLM Gateway — any provider via LiteLLM, cost/latency tracked
uv pip install -e ".[deepeval]"    # DeepEval-backed evaluation metrics
uv pip install -e ".[pdf,docx,mongo]"  # golden-dataset evidence connectors (agentguard/evaluation/dataset/)
```

> `ragas` is a listed extra but installing it into the same venv as `litellm`/`deepeval` is known to conflict on `langchain-community`/`langchain-core` versions — give it its own venv if you need it.

Everything above in one shot:

```bash
uv pip install -e ".[server,dev,llm,otel-otlp,mcp,litellm,deepeval,pdf,docx,mongo]"
```

## Quickstart

```bash
# 1. Postgres (Docker)
docker run -d --name agentguard-pg \
  -e POSTGRES_USER=agentguard -e POSTGRES_PASSWORD=agentguard -e POSTGRES_DB=agentguard \
  -p 5434:5432 postgres:16
# already have a container? just: docker start agentguard-pg

# 2. Environment
cp .env.example .env            # fill in what you need; every value has a documented fallback
export AGENTGUARD_DATABASE_URL=postgresql://agentguard:agentguard@localhost:5434/agentguard

# 3. Schema
python -m agentguard.cli init-db        # applies every migration in order, idempotently

# 4. Dashboard (React + Vite — see [The dashboard](#the-dashboard); rebuild after any dashboard-src/ change)
cd dashboard-src && npm install && npm run build && cd ..

# 5. Try the examples
python examples/basic_agent.py               # one CONTINUE, one STOP
python examples/phase2_agent.py               # RETRY, ROOT CAUSE + STOP, HUMAN
python examples/budget_failure_recovery.py    # the canonical recovery/rollback demo (no DB required)
python examples/tracing_demo.py               # @traceable / wrap_llm_client fine-grained tracing

# 6. Serve
uvicorn server.api:app --reload --port 8000
# open http://localhost:8000 — sign up, and every run above shows up under
# Runs / Traces, with Timeline / Reliability / Recovery / Audit / Replay
# tabs on each run's detail view, plus a full Evaluation Platform section
# (Eval Runs, Datasets, Suites, Model Benchmarks, Eval Recommendations).

agentguard report <run_id>
agentguard replay <run_id>
agentguard tail <run_id>
agentguard verify-audit <run_id>
```

`examples/budget_failure_recovery.py` needs no Postgres at all — it points at `agentguard.storage.memory.InMemoryRunRepository`, the same `RunRepository` implementation the test suite runs against. Swapping in Postgres anywhere is a one-line `agentguard.configure(...)` change; nothing else in the SDK cares which repository is behind it.

## Configuration

All configuration is environment-driven. Copy `.env.example` to `.env` and fill in what you need — see that file for the full list and defaults:

| Variable | Purpose |
|---|---|
| `AGENTGUARD_API_KEY` | Default API key for the `AgentGuard(...)` client. |
| `AGENTGUARD_DATABASE_URL` | Postgres connection string (defaults to a local `agentguard/agentguard` instance). |
| `AGENTGUARD_TEST_DATABASE_URL` | Separate DB for the Postgres integration tests; leave unset to skip them. |
| `ANTHROPIC_API_KEY` | Activates the real `AnthropicProvider` for the LLM-as-Judge evaluator. |
| `AGENTGUARD_OTEL_EXPORTER` / `AGENTGUARD_OTEL_OTLP_ENDPOINT` / `AGENTGUARD_OTEL_CONSOLE` | OpenTelemetry export target. |
| `AGENTGUARD_DSPY_MODEL` | Model name that activates the real DSPy optimizer (requires `pip install agentguard[dspy]`). |

Without `ANTHROPIC_API_KEY` or `AGENTGUARD_DSPY_MODEL`, AgentGuard runs entirely on-network-free, clearly-labeled deterministic stand-ins for the LLM judge and the improvement optimizer — every result they produce is tagged so it's never mistaken for a real model call.

## SDK usage

### Policy

```python
from agentguard import Policy

policy = Policy(
    max_cost=60000,
    require_approval=["payment"],         # perform_action("payment", ...) blocks on a human
    forbidden_actions=["drop_database"],  # raises immediately, no I/O
    default_on_uncertain="human",         # low-confidence + low-impact fallback
    retry_limit=2,
    on_retry_exhausted="replan",          # "replan" or "stop"
    max_replans=1,
    human_timeout_s=120,                  # deterministic timeout -> deny
    on_tool_exhausted="replan",           # "replan" or "human"
)
```

### Retry

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

### Replan

```python
from agentguard import monitor, get_replan_context, Policy
from agentguard.errors import ReplanRequested

@monitor(policy=Policy(max_cost=60000, max_replans=1))
async def my_agent(task):
    context = get_replan_context()  # {} on the first attempt
    if goal_has_drifted():
        raise ReplanRequested("goal drift detected", context={"new_strategy": "..."})
```

### Human approval

```python
from agentguard import monitor, perform_action, Policy

@monitor(policy=Policy(max_cost=60000, require_approval=["payment"]))
async def checkout_agent(task):
    await perform_action("payment", amount=500)  # raises HumanRejected on reject/timeout
    return "payment approved"
```

### Rollback & recovery

```python
from agentguard.recovery import rollback, seed_recovery_state

result = await rollback(repository, run_id, "S2")   # a Checkpoint.id or its label
seed_recovery_state(result.restored_state, parent_run_id=run_id, checkpoint_id=result.checkpoint.id)
recovered = await safe_agent("retry the task")       # a normal @monitor call, seeded from the checkpoint
```

### Tool reliability & alternative-tool recovery

```python
from agentguard.tools.registry import get_registry
import agentguard

get_registry().register_tool("search_api", my_search_fn)
get_registry().register_tool("search_api_backup", my_backup_search_fn)
get_registry().register_alternative("search_api", "search_api_backup", reliability_threshold=0.8)

@agentguard.monitor(policy=agentguard.Policy(max_cost=60000, on_tool_exhausted="replan"))
async def shopping_agent(task):
    result = await agentguard.context.call_tool("search_api", "laptop", primary_attempts=2)
```

### Replay & regression comparison

```python
from agentguard.replay import replay
from agentguard.regression.compare import compare_runs

session = await replay(repository, run_id)          # SAFE / DRY-RUN, reads only
result = await compare_runs(repository, run_a_id, run_b_id, label_a="v1", label_b="v2")
```

### CI/CD reliability gate

```bash
agentguard ci-gate --baseline-runs=<id1,id2> --candidate-runs=<id3,id4> --threshold=5
echo $?   # 0 = allowed, non-zero = blocked
```

### Auto-improvement (human-approved)

```python
from agentguard.improve.workflow import propose_improvement, approve_candidate

candidate, evaluation = await propose_improvement(
    repository, failed_run_id, corpus_run_ids=[failed_run_id], candidate_agent_fn=my_fixed_agent
)
# candidate.status == "proposed" — ALWAYS, until a human calls approve_candidate()
await approve_candidate(repository, candidate.id, approved_by="dev@example.com")
```

### Named, versioned policies

```python
from agentguard.policy.registry import PolicyRegistry

registry = PolicyRegistry(repository)
await registry.create("checkout_policy", Policy(max_cost=60000, retry_limit=2))          # version 1
await registry.save_new_version("checkout_policy", Policy(max_cost=60000, retry_limit=5))  # version 2 (never mutates v1)
```

## The CLI

```bash
agentguard init-db                                          create/upgrade the Postgres schema
agentguard tail <run_id> [--follow]                          chronological audit-event stream
agentguard replay <run_id> [--json]                          SAFE/DRY-RUN step-by-step reconstruction
agentguard report <run_id> [--json]                          six-dimension Reliability Report
agentguard compare <run_a> <run_b> [--json]                  factual per-metric deltas, no winner
agentguard ci-gate --baseline-runs=... --candidate-runs=... --threshold=5
agentguard verify-audit <run_id> [--json]                    re-verify the SHA-256 hash chain
agentguard worker [--poll-interval 2.0]                      durable job-queue worker (blocks until terminated)
agentguard mcp-serve --api-key <key>                          read-only MCP server over stdio (blocks until terminated)
```

`--follow` on `tail` is a correct historical polling stream against the repository, not a live push subscription (AgentGuard's only real-time channel is the dashboard's human-approval WebSocket). `worker` and `mcp-serve` are the two commands that don't return during normal operation — see their own `--help` for configuration (`worker` picks up dataset-validation jobs only once `AGENTGUARD_VALIDATION_DB_URL`/`AGENTGUARD_VALIDATION_QUERY` are set).

## The dashboard

A Vite + React app (source in `dashboard-src/`, `npm run build` outputs into `dashboard/`, which `server/api.py` serves — see [Quickstart](#quickstart)). `npm run dev` inside `dashboard-src/` gives hot reload, proxying `/api/*` to the port set in `dashboard-src/vite.config.js`.

`uvicorn server.api:app --reload`, then open `http://localhost:8000`. Sign up for a workspace, or log in — every route below the auth pages is workspace-scoped, so different sign-ups never see each other's data.

| Group | Pages |
|---|---|
| — | **Overview** — KPIs (total runs, success rate, avg latency, tokens, estimated cost, reliability score), run volume, latency percentiles, success/failure, evaluation summary, recent runs |
| **Observability** | Runs / Traces (with per-run **Overview / Trace / Evaluations / Latency / Tokens / Reliability / Recovery / Audit / Metadata** tabs), Latency, Tokens & Cost, Errors, Tools, Models |
| **Reliability** | Evaluations, Risk & Confidence, Agent Behavior |
| **Recovery** | Interventions, Checkpoints / Rollback, Replay / Compare |
| **Governance** | Policy Management, Audit |
| **Improvement** | Problems, Failure Patterns, Recommendations |
| **Evaluation Platform** | Eval Runs (with per-result **Diagnose** — why / where-in-code / suggested prompt), Datasets (+ Dataset Quality), Suites, Model Benchmarks, Eval Recommendations |
| **Settings** | API Keys, Profile, Team / Users |

Every number the dashboard shows either comes from real, already-persisted data or is explicitly rendered `N/A` — never a fabricated placeholder (e.g. token/cost fields the agent never reported).

## REST / WebSocket API

The original, unauthenticated surface (used by the CLI, examples, and the test suite):

```
GET  /api/runs/{run_id}/risk                 risk assessment history
GET  /api/runs/{run_id}/root-cause           root cause (or {"root_cause": null})
GET  /api/runs/{run_id}/decisions            full decision history
GET  /api/runs/{run_id}/human-decisions      human approval request history
POST /api/runs/{run_id}/human-decision       resolve a pending HUMAN request
WS   /ws/runs/{run_id}/approval              live human-approval channel

GET  /api/runs/{run_id}/timeline             chronological audit-trail timeline
GET  /api/runs/{run_id}/reliability-report   six-dimension Reliability Report
GET  /api/runs/{run_id}/checkpoints          this run's checkpoints (S1, S2, ...)
POST /api/runs/{run_id}/rollback             {"to_checkpoint": "S2"} -> restore state
GET  /api/runs/{run_id}/counterfactual       actual-vs-counterfactual analysis
GET  /api/runs/{run_id}/audit                full hash-chained audit event list
POST /api/runs/{run_id}/audit/verify         recompute + verify the hash chain

GET  /api/runs/{run_id}/replay               SAFE/DRY-RUN step-by-step reconstruction
GET  /api/runs/compare?run_a=&run_b=         factual per-metric deltas between two runs
GET  /api/tools                              distinct tool names with recorded calls
GET  /api/tools/{tool_name}/profile          aggregated reliability profile
GET  /api/tools/alternatives                 registered primary -> fallback mappings
POST /api/tools/alternatives                 register one (also updates the live ToolRegistry)
GET  /api/agents/{agent_id}/fingerprint      behavior baseline vs. current run
GET  /api/improvements                       all improvement candidates
GET  /api/improvements/{id}                  one candidate + its evaluations
POST /api/improvements/{id}/approve           {"approved_by": "..."}
POST /api/improvements/{id}/reject            {"approved_by": "..."}
GET  /api/policies                            named policy definitions
POST /api/policies                            create a new named policy (version 1)
GET  /api/policies/{name}                     current version's fields
GET  /api/policies/{name}/versions            full version history
POST /api/policies/{name}/versions            save a NEW version (never mutates a prior one)
POST /api/ci-gate                             {"baseline_run_ids", "candidate_run_ids", "threshold_pct"}
```

The authenticated dashboard API (`/api/v2/...`, workspace-scoped, backs the dashboard UI above) and the auth/settings surface (`server/auth.py`):

```
POST /api/auth/signup | login | logout | forgot-password | reset-password
GET  /api/auth/me
GET  /api/settings/api-keys           POST .../api-keys   POST .../api-keys/{id}/revoke | rotate
GET  /api/projects                    POST /api/projects
GET  /api/workspace/members           POST /api/workspace/members

GET  /api/v2/overview
GET  /api/v2/runs                     GET /api/v2/runs/{run_id}
GET  /api/v2/runs/{run_id}/reliability-report | timeline | checkpoints | counterfactual | audit | replay
POST /api/v2/runs/{run_id}/audit/verify | rollback
GET  /api/v2/runs/compare
GET  /api/v2/latency | tokens | evaluations | risk | recovery | tools | models | agents | failure-patterns
GET  /api/v2/agents/{agent_name}/fingerprint       GET .../agents/{agent_name}/card
GET  /api/v2/policies                 GET .../policies/{name}     GET .../policies/{name}/versions
POST /api/v2/policies                 POST .../policies/{name}/versions
GET  /api/v2/improvements
GET  /api/v2/problems                 GET .../problems/{cluster_key}
POST /api/v2/problems/{cluster_key}/status | propose-fix

# Evaluation Platform
GET  /api/v2/suites                   GET .../suites/recommend?agent_name=
GET  /api/v2/eval-runs                GET .../eval-runs/{id}
POST /api/v2/eval-results/{result_id}/diagnose        why / where-in-code / suggested prompt
GET  /api/v2/datasets                 GET .../datasets/{id}/versions/{version}
GET  /api/v2/benchmarks               GET .../benchmarks/{id}
GET  /api/v2/eval-recommendations
```

## Project layout

```
agentguard/          the SDK package
  policy/            declarative Policy + runtime PolicyFinding checks; registry.py (versioned policy portal)
  reliability/        RiskEngine, RootCauseEngine, confidence routing, ReliabilityEngine facade,
                      report.py (six-dimension report), tool_profile.py, model_profile.py,
                      fingerprint.py, cluster.py (cross-run failure clustering), judge_profile.py
  decision/           the RUNNING/EVALUATING/CONTINUE/RETRY/REPLAN/HUMAN/STOP/ROLLED_BACK state machine
  llm/                ModelProvider abstraction (DeterministicTestProvider, AnthropicProvider)
  human/              ApprovalBroker (WebSocket/REST-resolvable) + post-hoc resolution
  evaluators/         ConstraintAdherenceEvaluator (sync), LLMJudge (async, off the critical path) —
                      scores a Run against its Policy; NOT the same subsystem as evaluation/ below
  checkpoint/         CheckpointEngine — typed, hashed, serializable state snapshots
  audit/              SHA-256 hash-chain construction/verification
  recovery/           rollback(), seed_recovery_state(), generate_counterfactual()
  replay/             SAFE/DRY-RUN step-by-step run reconstruction
  regression/         compare.py (metric deltas), corpus.py (batch/corpus evaluation)
  tools/              ToolRegistry (explicit primary/alternative registration)
  ci/                 the deterministic CI/CD reliability gate rule
  improve/            recommend.py, optimizer.py (DSPy-optional), workflow.py (approval-gated)
  tracing/            @traceable, wrap_llm_client, litellm_wrap.py (LLM Gateway) — fine-grained
                      TraceStep capture, including real call-site code_file/function/lineno
  evaluation/          the Evaluation Platform — MetricEvaluator/EvalCase/EvaluationEngine,
                      evaluators/ (DeepEval, Ragas, trajectory, handoff/chain-handoff),
                      dataset/ (golden-dataset validation + evidence-source connectors),
                      recommend.py, benchmark.py, diagnose.py (why/where/suggested-prompt)
  jobs/                the async job queue (Worker, handlers.py) — see `agentguard worker`
  mcp/ · a2a/          MCP client/server (FastMCP-based) and A2A Agent Card
  cli/                the `agentguard` Typer CLI
  auth/               user/API-key auth service used by the dashboard
  storage/            RunRepository interface, Postgres impl, in-memory impl, migrations/
server/               dashboard API (FastAPI) — auth, unauthenticated legacy API, and the
                      authenticated workspace-scoped /api/v2 surface, plus WebSocket approvals
dashboard-src/        dashboard SOURCE (Vite + React) — `npm run build` outputs into dashboard/
dashboard/            dashboard BUILD OUTPUT, served by server/api.py — do not hand-edit
tests/                unit, e2e (fake repository), integration (mostly fake repository;
                      test_postgres_repository.py needs a real Postgres and auto-skips without one)
examples/             runnable scripts (budget_failure_recovery.py needs no Postgres)
scripts/              utility scripts (SDK reference PDF generator, dashboard launcher)
docs/                 phase execution reports and the generated SDK reference PDF
```

## Testing

```bash
pytest tests/unit tests/e2e tests/integration -v
```

These need no database — they run against the real Decision Engine, Policy engine, Reliability Engine, and LLM Judge, backed by an in-memory repository fake (`tests/fakes.py`) implementing the same `RunRepository` interface Postgres does.

To also run the Postgres integration tests:

```bash
docker run -d --name agentguard-pg -e POSTGRES_USER=agentguard \
  -e POSTGRES_PASSWORD=agentguard -e POSTGRES_DB=agentguard \
  -p 5432:5432 postgres:16

export AGENTGUARD_TEST_DATABASE_URL=postgresql://agentguard:agentguard@localhost:5432/agentguard
pytest tests/integration -v
```

## Design principles

A few rules the codebase holds itself to, because they shape how you should read every number the SDK produces:

- **No fabricated data.** A dimension/metric with no underlying evidence is reported `available: false` or `N/A` — never defaulted to 0 or guessed.
- **No silent fallback that looks like success.** Uncertainty is routed explicitly (to a human, or a documented policy default), never quietly converted into "safe."
- **Every score is explainable.** Risk scores carry their own factors and weights; reliability reports and comparisons carry their own source data — nothing is a black box.
- **Nothing auto-deploys.** Improvement candidates only ever move to `approved`/`rejected` through an explicit human action.
- **Isolation is enforced server-side.** Workspace ownership is checked at the API/query layer, not only in the frontend.
- **Stand-ins are always labeled.** Without a real LLM/DSPy provider configured, AgentGuard runs on clearly-tagged deterministic substitutes rather than failing or faking a real model call.
