# AgentGuard Phase 2 Execution Report

## 1. Baseline

Recorded **before** any Phase 2 code was written, against the Phase 1
codebase exactly as found.

```
command:  .venv/Scripts/python.exe -m pytest tests/unit tests/e2e -v
passed:   23
failed:   0
skipped:  0
```

```
command:  .venv/Scripts/python.exe -m pytest tests/integration -v
passed:   0
failed:   0
skipped:  4   (AGENTGUARD_TEST_DATABASE_URL not set)
```

Application status: SDK-only; no server process was running.
Database connectivity: none configured — no `AGENTGUARD_DATABASE_URL`,
no reachable PostgreSQL, no `docker` binary available in this
environment (`docker --version` → command not found). This is an
environment limitation, not a code defect — the Postgres-only
integration suite (`tests/integration/test_postgres_repository.py`)
auto-skips by design when no test database is configured, and it did.
API health: not applicable at baseline (no live process). Phase 2
smoke-tests the API for real, below (§11), using the in-memory fake
repository since no live Postgres is available in this environment.

Phase 1 had **zero regressions to fix** — the baseline was clean, so
Phase 2 proceeded directly to implementation.

## 2. Phase 2 Objective

Extend the existing Phase 1 Core Loop with: RETRY decision, REPLAN
decision, HUMAN decision, declarative policy configuration, causal
state-diff Root-Cause Engine, dynamic per-action Risk Scoring,
Confidence/Uncertainty handling, LLM-as-Judge evaluation, a human
approval WebSocket interface, stronger decision evidence and
explainability, automated tests for all of the above, and this
execution report — while preserving Phase 1's
`RUNNING -> EVALUATING -> CONTINUE/RETRY/REPLAN/HUMAN/STOP` flow and
explicitly **not** implementing checkpoint/rollback, counterfactual
analysis, tool reliability profiles, alternative-tool recovery,
behavior fingerprinting, hash-chained audit trail, agent-to-agent
constraint propagation, advanced prompt-injection/security systems, a
chaos testing harness, a CI/CD reliability gate, DSPy auto-improvement,
replay UI, regression comparison UI, a full reliability report,
multi-tenancy, production data governance, or a ClickHouse migration.

## 3. Components Implemented

| Component | Files | Implemented | Tested | Evidence | Status |
|---|---|---|---|---|---|
| Policy Engine | `agentguard/policy/engine.py`, `agentguard/models.py` (`Policy`, `PolicyFinding`) | Yes | Yes (`tests/unit/test_policy.py`, 10 tests) | §4, §6–8 | DONE |
| RETRY | `agentguard/decision/engine.py`, `agentguard/decorator.py`, `agentguard/errors.py` (`TransientError`) | Yes | Yes (`tests/unit/test_decision_engine.py`, `tests/e2e/test_scenario_retry.py`) | §6 | DONE |
| REPLAN | same + `agentguard/errors.py` (`ReplanRequested`), `agentguard/context.py` (`get_replan_context`) | Yes | Yes (`tests/e2e/test_scenario_retry.py`) | §6 | DONE |
| HUMAN | `agentguard/human/`, `agentguard/context.py` (`perform_action`, `request_approval`), `agentguard/decorator.py` | Yes | Yes (`tests/e2e/test_scenario_human.py`, `tests/integration/test_websocket_approval.py`) | §8 | DONE |
| Root Cause Engine | `agentguard/reliability/root_cause.py` | Yes | Yes (`tests/unit/test_root_cause.py`, 7 tests) | §7 | DONE |
| Risk Engine | `agentguard/reliability/risk.py`, `agentguard/reliability/confidence.py` | Yes | Yes (`tests/unit/test_risk.py`, 10 tests) | §7 | DONE |
| Confidence Engine | `agentguard/reliability/confidence.py`, consumed by `decision/engine.py` | Yes | Yes (`tests/unit/test_decision_engine.py` routing-table tests) | §4 | DONE |
| LLM Judge | `agentguard/evaluators/llm_judge.py`, `agentguard/llm/provider.py` | Yes (deterministic stand-in — see §9 limitation) | Yes (`tests/unit/test_llm_judge.py`, `tests/integration/test_llm_judge_async.py`) | §9, §13 | DONE (with stated limitation) |
| Decision Engine | `agentguard/decision/engine.py` | Yes | Yes (21 unit tests) | §4, §6–8 | DONE |
| WebSocket approval | `agentguard/human/broker.py`, `server/api.py` (`/ws/runs/{run_id}/approval`) | Yes | Yes (`tests/integration/test_websocket_approval.py`) | §12 | DONE |
| Database | `agentguard/storage/migrations/0002_phase2.sql`, `agentguard/storage/postgres.py`, `agentguard/storage/models.py` | Yes | Not executed against a live Postgres (none available — see §1, §10) | §10 | DONE, unverified against live Postgres |
| API | `server/api.py` | Yes | Yes (`tests/integration/test_websocket_approval.py`) | §11 | DONE |
| Dashboard | `dashboard/index.html` | Yes | Manually verified: JS syntax-checked with `node --check`; every endpoint it calls hit live via `starlette.testclient.TestClient` and returned the expected shape (§11). No browser was available in this environment to click-test it interactively — stated as a limitation. | §11, §15 | DONE, not interactively browser-tested |

## 4. Objective Verification Matrix

| Objective | Required | Implemented | Tested | Execution Evidence | Status |
|---|---|---|---|---|---|
| RETRY decision | Yes | Yes | Yes | §6 | PASS |
| REPLAN decision | Yes | Yes | Yes | §6 | PASS |
| HUMAN decision | Yes | Yes | Yes | §8 | PASS |
| Declarative policy configuration | Yes | Yes | Yes | §4 in README, `tests/unit/test_policy.py` | PASS |
| Causal state-diff Root-Cause Engine | Yes | Yes | Yes | §7 | PASS |
| Dynamic per-action Risk Scoring | Yes | Yes | Yes | §7 | PASS |
| Confidence / Uncertainty handling | Yes | Yes | Yes | routing-table unit tests, §7 | PASS |
| LLM-as-Judge evaluation | Yes | Yes (deterministic stand-in provider; real-provider code path exists but unexercised — §9) | Yes | §9, §13 | PASS (limitation documented) |
| Human approval WebSocket interface | Yes | Yes | Yes | §8, §12 | PASS |
| Stronger decision evidence / explainability | Yes | Yes | Yes | every `Decision` carries `risk_score`, `confidence`, `policy_findings`, `evidence` — §6–8 | PASS |
| Automated tests for all Phase 2 behavior | Yes | Yes | Yes | §5 | PASS |
| Phase 2 execution report | Yes | Yes | — | this document | PASS |
| Phase 1 behavior preserved | Yes | Yes | Yes | §14 (23/23 original tests still pass, unmodified) | PASS |
| Sync/async boundary preserved (LLM off critical path) | Yes | Yes | Yes | §13 (timing evidence) | PASS |
| Checkpoint/rollback NOT implemented | Required absent | Absent | N/A | §17 | PASS |
| Counterfactual analysis NOT implemented | Required absent | Absent | N/A | §17 | PASS |
| Tool reliability profiles NOT implemented | Required absent | Absent (neutral placeholder factor only) | N/A | §17 | PASS |
| Behavior fingerprinting NOT implemented | Required absent | Absent | N/A | §17 | PASS |

## 5. Test Results

```
command:  .venv/Scripts/python.exe -m pytest tests/ -v
total:    90
passed:   86
failed:   0
skipped:  4   (tests/integration/test_postgres_repository.py — no AGENTGUARD_TEST_DATABASE_URL)
```

Breakdown:

```
tests/unit          63 passed
tests/e2e            14 passed
tests/integration     9 passed, 4 skipped
```

`tests/unit` (63): `test_decision_engine.py` (21), `test_policy.py`
(10), `test_risk.py` (10), `test_root_cause.py` (7), `test_llm_judge.py`
(6), `test_evaluators.py` (5, unmodified Phase 1), `test_models.py` (4,
unmodified Phase 1).

`tests/e2e` (14): `test_monitor_flow.py` (6, unmodified Phase 1),
`test_scenario_retry.py` (3), `test_scenario_root_cause.py` (1),
`test_scenario_human.py` (4).

`tests/integration` (9 passed + 4 skipped): `test_reliability_pipeline.py`
(3), `test_llm_judge_async.py` (2), `test_websocket_approval.py` (4),
`test_postgres_repository.py` (4, skipped — no live Postgres).

## 6. E2E RETRY Evidence

Scenario: a transient tool failure on the first attempt is retried
(bounded by `Policy.retry_limit`) and the run succeeds on the second
attempt.

```
run_id: 9b01b8e5-03df-49dc-90bd-68a86fa590c7
attempts made: 2
final status: continue
run.retry_count: 1
result: "selected a 52000 laptop"
spans persisted: 2 (one per attempt, agentguard.attempt=1 and 2)

decision 1: outcome=retry, reason="price lookup service timed out",
            retry_count=1, evidence={"exception": "...", "tool": "price_api"}
decision 2: outcome=continue, reason="high-confidence safe evaluation",
            risk_score=0.0, confidence=1.0
```

Also verified: exceeding `retry_limit` falls through to REPLAN
(`policy.on_retry_exhausted="replan"`, default) with its own fresh
retry budget, and to STOP when `on_retry_exhausted="stop"` — both are
automated tests in `tests/e2e/test_scenario_retry.py`
(`test_retry_limit_exceeded_falls_through_to_replan`,
`test_retry_limit_exceeded_stops_when_configured`), both passing.

## 7. E2E ROOT-CAUSE Evidence

Scenario: exactly the canonical spec example —
`max_budget=60000` present at S1/S2, silently disappears at S3, the
agent re-sets it to `67000` at S4, and the deterministic evaluator only
ever sees S4 (the final state).

```
run_id: 860b04ee-3f1b-4117-8c7c-8aef9fba09d9
state sequence: S1, S2, S3, S4
final status: stop

Deterministic evaluator's evidence (only sees the final state):
  evaluator=constraint_adherence, passed=false,
  expected=60000.0, observed=67000

Root-Cause Engine's evidence (sees the whole ordered history):
  earliest_deviation=S3
  expected={"max_budget": 60000}
  observed={"max_budget": null}
  confidence=0.97
  explanation="max_budget disappeared during state transition S3"

Risk assessment:
  risk_score=0.75, impact=1.0, confidence=1.0
  factors={"impact":1.0,"policy_violation":1.0,"uncertainty":0.0,
           "goal_drift":0.0,"tool_reliability":0.0}

Decision: outcome=stop,
  reason="high-confidence unsafe evaluation: constraint_adherence",
  risk_score=0.75, confidence=1.0,
  policy_findings=[{"rule":"max_cost","violated":true,"severity":"high",
                     "expected":60000.0,"observed":67000}]
```

`root_cause.earliest_deviation` (`S3`) is confirmed different from
where the evaluator's own evidence points (`S4`/final state) —
`tests/e2e/test_scenario_root_cause.py::test_root_cause_precedes_the_final_violation`
asserts this explicitly and passes.

## 8. E2E HUMAN Evidence

Trigger: `Policy(require_approval=["payment"])` +
`await agentguard.perform_action("payment", ...)` inside the agent.

**APPROVE:**
```
run_id: 6d01eaeb-f907-4c41-82fd-a774282ec85b
final status: continue
result: "payment approved, order placed"
human_decision: {action: "payment", risk_score: 0.525, confidence: 1.0,
  status: "approved", resolved_by: "reviewer@example.com"}
```

**REJECT:**
```
run_id: 719841e9-5bd4-436f-8d8d-e6689326e0f1
raised: HumanRejected("human rejected action 'payment': ...")
final status: stop
decision: {outcome: "stop",
  reason: "human rejected action 'payment': action 'payment' is listed in policy.require_approval"}
```

**TIMEOUT** (`human_timeout_s=0.05`, i.e. a deterministic, configurable
bound — never waits forever):
```
run_id: 83866ae4-37e2-4fd8-bdf4-ceb8e7e3a786
raised: HumanRejected(...)
final status: stop
human_decision: {status: "timeout", timeout_s: 0.05,
  requested_at: 22:27:37.090223, resolved_at: 22:27:37.151878, resolved_by: null}
```

**WebSocket request evidence** (real FastAPI WebSocket handshake via
`starlette.testclient.TestClient` against `server/api.py`, agent
running on a separate thread/event loop to simulate a real
agent-process ↔ dashboard-process split —
`tests/integration/test_websocket_approval.py::test_websocket_receives_pending_approval_and_can_approve`):
```
WS connect  /ws/runs/{run_id}/approval
WS receive  {"type": "approval_request", "request": {"action": "payment", "run_id": "..."}}
WS send     {"outcome": "approved", "resolved_by": "dash-user"}
WS receive  {"type": "human_decision_ack", "resolved": true, "live": true}
agent coroutine resumes, returns "payment approved"
```

**REST resolution evidence**
(`test_rest_endpoint_resolves_pending_human_decision`):
```
POST /api/runs/{run_id}/human-decision  {"outcome": "rejected"}  -> 200
  {"run_id": "...", "resolved": true, "live": true, "decision": null}
GET  /api/runs/{run_id}  -> 200  {"status": "stop", ...}
```

## 9. LLM-Judge Evidence

**Provider**: `DeterministicTestProvider`
(`agentguard/llm/provider.py`). **Model**: none — this is not a real
model call.

**Stated limitation**: this development environment has no
`ANTHROPIC_API_KEY` and no outbound network access configured for one
(`env | grep -i ANTHROPIC` → empty; no network egress attempted). Per
the Phase 2 spec's explicit instruction, the provider abstraction
(`ModelProvider`) and a real implementation (`AnthropicProvider`, using
the official `anthropic` SDK's async client, lazily imported) both
exist and `get_default_provider()` would select `AnthropicProvider`
automatically the moment `ANTHROPIC_API_KEY` is set and the `anthropic`
package installed — but that path has **not** been exercised in this
environment, and every `EvalResult` the judge produces is tagged
`evidence["provider_is_real_llm"] = False` so this is never silently
presented as a real model call.

**Evaluation** (`tests/unit/test_llm_judge.py`): given a safe run, the
judge returns `label="safe"`, `passed=True`; given a run whose evidence
contains a violation marker, `label="unsafe"`, `passed=False`.
Structured output confirmed present: `score`, `confidence`, `reason`,
`evidence["correctness"]`, `evidence["goal_completion"]`.

**Confidence**: deterministic per-prompt (0.80–0.95 range, seeded from
a SHA-256 digest of the prompt so it's reproducible, not a fixed
constant).

**Execution timing / async worker evidence**
(`tests/integration/test_llm_judge_async.py::test_llm_judge_result_is_not_available_when_monitor_returns`,
judge configured with a **150ms simulated model latency**):
```
sync path elapsed:            < 150ms  (measured, asserted < judge's own latency)
llm_judge evaluation present
  immediately after @monitor returns:  NO
llm_judge evaluation present
  after await wait_for_background_tasks():  YES
llm_eval.evidence["async_elapsed_s"]:  >= 0.1  (the simulated 150ms actually elapsed)
```

Separately, a raw latency benchmark (n=200 runs each, in-memory
repository, `time.perf_counter()`):
```
Phase 2 sync path, LLM judge DISABLED: mean=0.527ms p50=0.479ms p95=0.679ms max=4.446ms
Phase 2 sync path, LLM judge ENABLED  (100ms simulated model latency):
                                        mean=0.517ms p50=0.482ms p95=0.629ms max=2.684ms
```
The two are statistically indistinguishable even though the "enabled"
run's judge simulated a 100ms model call — because that call never sat
on the synchronous path. This is direct, measured evidence (not an
architectural claim taken on faith) that the LLM Judge is off
`@monitor`'s synchronous critical path.

**Persistence result**: the judge's `EvalResult` is persisted via
`repository.save_evaluation(run, result)` from inside the background
task, same repository/table Phase 1's deterministic evaluator writes
to — confirmed via `fake_repository.evaluations[run.id]` containing an
`evaluator="llm_judge"` entry after `wait_for_background_tasks()`.

**Decision Engine consumption**: the judge itself never emits a
Decision. `tests/integration/test_llm_judge_async.py::test_llm_judge_override_records_a_new_decision_when_it_disagrees`
configures a judge that always disagrees (labels everything unsafe with
confidence 0.95) with a run the deterministic evaluator already passed
as CONTINUE. After the background task completes:
```
decisions before background task: [CONTINUE]                 (1 row)
decisions after background task:  [CONTINUE, STOP]            (2 rows — original preserved)
final run.status: stop
STOP decision.reason contains: "LLM judge asynchronously flagged this run unsafe after CONTINUE: ..."
```
No API key was exposed anywhere in this process (`DeterministicTestProvider`
makes no network calls at all).

## 10. Database Verification

No live PostgreSQL instance was available in this environment (§1) —
`docker` is not installed and no `AGENTGUARD_DATABASE_URL`/
`AGENTGUARD_TEST_DATABASE_URL` was reachable. This is the same
limitation Phase 1's baseline already had (`tests/integration/test_postgres_repository.py`
was already skipped at baseline, before any Phase 2 change).

What **was** verified, against the real schema/migration files
(`agentguard/storage/migrations/0001_phase1.sql`,
`0002_phase2.sql`) and the real `PostgresRunRepository` implementation
(`agentguard/storage/postgres.py`):

- The module imports cleanly and `ast.parse()`s every changed file
  without error (`agentguard/storage/postgres.py`,
  `repository.py`, `models.py`).
- `PostgresRunRepository` implements every abstract method
  `RunRepository` declares (Python would raise `TypeError` at
  instantiation otherwise — it does not).
- Every persisted record type (`risk_assessments`, `root_causes`,
  `decisions` with the new columns, `human_decisions`) was exercised
  end-to-end against `tests/fakes.InMemoryRunRepository`, which
  implements the exact same `RunRepository` interface — see §6–8 for
  the actual records produced.
- Migrations are purely additive (`CREATE TABLE IF NOT EXISTS`,
  `ADD COLUMN IF NOT EXISTS` throughout `0002_phase2.sql`), so applying
  them against an existing Phase 1 database would not destroy data —
  reviewed by inspection, not executed.

**This is a real, stated limitation, not a claim of full verification**:
the actual SQL in `postgres.py`/the migration files has not been
executed against a real PostgreSQL server. Recommended before
production use: run `python -m agentguard.cli init-db` against a real
database and `pytest tests/integration -v` with
`AGENTGUARD_TEST_DATABASE_URL` set.

## 11. API Verification

All hit live via `starlette.testclient.TestClient` against the real
`server/api.py` FastAPI app (in-memory fake repository configured —
see `tests/integration/test_websocket_approval.py` and an ad hoc smoke
pass during development):

| Endpoint | Method | Status | Result |
|---|---|---|---|
| `/` | GET | 200 | dashboard HTML, 12676 bytes |
| `/dashboard/index.html` | GET | 200 | 12676 bytes |
| `/api/runs` | GET | 200 | list of run summaries incl. `retry_count`, `replan_count`, `decision_risk_score` |
| `/api/runs/{run_id}` | GET | 200 | full run detail |
| `/api/runs/does-not-exist` | GET | 404 | `{"detail": "run not found"}` |
| `/api/runs/{run_id}/risk` | GET | 200 | `[{"risk_score": 0.75, "impact": 1.0, ...}]` |
| `/api/runs/does-not-exist/risk` | GET | 404 | — |
| `/api/runs/{run_id}/root-cause` | GET | 200 | `{"earliest_deviation": "S3", ...}` |
| `/api/runs/{run_id}/decisions` | GET | 200 | full decision history, `outcome="stop"` last |
| `/api/runs/{run_id}/human-decisions` | GET | 200 | `[]` or pending/resolved requests |
| `/api/runs/{run_id}/human-decision` | POST | 200 | `{"resolved": true, "live": true/false, "decision": ...}` |
| `/ws/runs/{run_id}/approval` | WS | connect OK | see §8 for the full message exchange |

## 12. WebSocket Verification

```
connection: client.websocket_connect("/ws/runs/{run_id}/approval") — real
            ASGI WebSocket handshake via starlette's TestClient, not mocked
request:    server pushes {"type": "approval_request", "request": {...}}
            the moment agentguard.perform_action() registers it
response:   client sends {"outcome": "approved", "resolved_by": "dash-user"};
            server acks {"type": "human_decision_ack", "resolved": true, "live": true};
            the agent coroutine (running on a SEPARATE thread/event loop,
            simulating a real agent-process/dashboard-process split) resumes
            and returns normally
timeout:    verified separately at the ApprovalBroker level
            (Policy.human_timeout_s=0.05 -> HumanDecision.status="timeout"
            after ~50-60ms, agent raises HumanRejected, run STOPs) —
            see §8
```

## 13. Latency Verification

```
Phase 1 baseline (unchanged code path, measured informally alongside Phase 2):
  same order of magnitude as the "LLM judge DISABLED" figure below —
  Phase 1 has no risk/root-cause/policy-evaluate step at all, so its
  sync path is a strict subset of Phase 2's "disabled" path.

Phase 2 sync path, LLM judge DISABLED (n=200): mean=0.527ms p50=0.479ms p95=0.679ms max=4.446ms
Phase 2 sync path, LLM judge ENABLED,
  100ms simulated model latency (n=200):       mean=0.517ms p50=0.482ms p95=0.629ms max=2.684ms

LLM judge execution time (background): >= 100ms (matches its configured
  simulated latency; see §9's async_elapsed_s assertion)
```

Explicit verification that the LLM judge is not on the synchronous
response path: the two "sync path" figures above are statistically
indistinguishable (both ~0.5ms) despite one of them configuring a
judge with 100ms of simulated model latency — if the judge were on the
critical path, the "ENABLED" row would show ~100ms+, not ~0.5ms. This
is measured evidence, not an architectural claim taken on faith (§9
has the full methodology). No "zero latency overhead" claim is made —
Phase 2's synchronous path does cost more than Phase 1's (it now runs
`PolicyEngine.evaluate()`, `RiskEngine.assess()`, and
`RootCauseEngine.analyze()` in addition to the deterministic
evaluator), and that cost is measured above at sub-millisecond, not
zero.

## 14. Regression Verification

```
Before Phase 2:  .venv/Scripts/python.exe -m pytest tests/unit tests/e2e -v
                 23 passed, 0 failed, 0 skipped

After Phase 2:   .venv/Scripts/python.exe -m pytest tests/unit tests/e2e -v
                 77 passed, 0 failed, 0 skipped
                 (the original 23 test functions are unmodified and
                 still present among these 77 — `test_decision_engine.py`,
                 `test_evaluators.py`, `test_models.py`, `test_policy.py`
                 unit tests, and every `test_monitor_flow.py` e2e test,
                 all pass verbatim; the increase is additional Phase 2
                 test files, not replaced Phase 1 tests)
```

No regressions. No Phase 1 test was modified to make it pass; where
`test_policy.py` and `test_decision_engine.py` gained new test
functions, the original Phase 1 test functions in those same files are
untouched and still pass.

## 15. Problems Encountered

| Problem | Root Cause | Solution | Verification |
|---|---|---|---|
| `DeterministicTestProvider`'s "unsafe" heuristic false-positived on every single run | The prompt template always includes `policy.model_dump()`, which always contains the literal field name `forbidden_actions` — a bare substring match on `"forbidden"` matched that field name regardless of whether anything was actually forbidden | Replaced the overly-generic `"forbidden"` marker with specific, unambiguous markers (`"constraint_violated"`, `"policy_violation=true"`, `"budget exceeds"`) that only appear in the prompt when an actual violation is present | `tests/unit/test_llm_judge.py::test_safe_run_is_judged_safe` failed before the fix, passes after |
| `ApprovalBroker.resolve()` never woke up the agent when called from a different OS thread than the one the agent's `request()` coroutine was suspended on | `asyncio.Future.set_result()` is documented as not thread-safe; calling it directly from a different thread's context schedules the future's callbacks via a non-thread-safe path, so the awaiting event loop never actually processes the wakeup | Store the requesting coroutine's event loop at `request()` time and resolve via `loop.call_soon_threadsafe(...)` in `resolve()`, which is safe from any thread and a no-op-equivalent when already on that loop | `tests/integration/test_websocket_approval.py::test_websocket_receives_pending_approval_and_can_approve` hung/timed out before the fix (agent thread never observed the resolution within a 5s join); passes in ~0.1s after |
| Retry budget wasn't reset after a REPLAN transition, so a replanned attempt could immediately re-exhaust a retry budget it never actually spent, producing a confusing decision sequence (RETRY, RETRY, REPLAN, RETRY, RETRY, STOP for a simple 1-retry/1-replan policy) | `ctx.retry_count` was only ever incremented, never reset, across REPLAN boundaries | Reset `ctx.retry_count`/`run.retry_count` to 0 at both points a REPLAN transition is taken (explicit `ReplanRequested` and retry-exhaustion auto-replan) — a replan is a new plan, so it gets its own fresh retry budget | `tests/e2e/test_scenario_retry.py::test_retry_limit_exceeded_falls_through_to_replan` — the decision sequence is now short, deterministic, and bounded |
| `resolve_human_review()`'s `Optional[Decision]` return conflated "no matching pending request" with "resolved, but the live in-function waiter (not this call) will produce the eventual Decision" | Both cases returned `None`, and `server/api.py` reported `"resolved": False` for both | Introduced `HumanResolutionResult(resolved, live, decision)` so the API can report `resolved=True, live=True, decision=None` distinctly from `resolved=False` | `tests/integration/test_websocket_approval.py::test_websocket_receives_pending_approval_and_can_approve` asserted `ack["resolved"] is True`; failed before, passes after |

## 16. Deviations

| Specification | Actual implementation | Reason | Impact |
|---|---|---|---|
| "HUMAN path: APPROVE → RUNNING" (implying the agent function's remaining code re-executes) | APPROVE resumes the **same suspended coroutine** (via `await perform_action(...)` returning normally) rather than re-invoking the agent function from a checkpoint | Checkpoint/rollback is explicitly deferred to a later phase; resuming a suspended `await` inside the same coroutine achieves the same practical effect (the agent's own subsequent code runs normally) without needing state serialization/replay | None for the in-function approval flow (Scenario C) — it behaves as specified. For the *post-hoc* "low confidence + high impact" HUMAN escalation (§4, decide()'s HUMAN branch), the agent function has already returned by the time a human resolves it, so "RESUME" there means the run's terminal `status` becomes CONTINUE, not a second invocation of the agent's code — this is the honest, checkpoint-free interpretation and is stated here rather than silently assumed |
| "The Decision Engine consumes [the LLM judge's] result" | The judge's result is persisted asynchronously; if the run is still `CONTINUE` and the judge disagrees with reasonable confidence, `decorator.py` calls a fresh `DecisionEngine` to record an explicit STOP override as a **new** decision row | A synchronous consumption would require delaying the decision until the judge responds, which directly violates Rule 15 (LLM judge must not be on the synchronous critical path). The chosen design is real integration, but it is a post-hoc override rather than gating the original decision | Documented explicitly rather than silently narrowed; verified in §9/§13 |
| RETRY/REPLAN signaling | Implemented via agent-raised exceptions (`TransientError`, `ReplanRequested`) rather than an evaluator label the Decision Engine infers on its own | The spec's canonical diagrams show "transient failure" / "goal drift detected" as inputs to the loop without mandating a specific signaling mechanism; exceptions give a clean, explicit boundary ("Keep this integration generic enough to work with a normal Python agent function" — Rule 3) and are trivially testable | None — `decorator.py`'s execution loop is the single authoritative place these are bounded and recorded |

## 17. Intentionally Deferred

The following are **not implemented** in Phase 2, per the spec's explicit
scope:

- checkpoint / rollback
- counterfactual analysis
- tool reliability profiles (the Risk Engine's `tool_reliability` factor
  is a fixed neutral placeholder, not a real measurement)
- alternative-tool recovery
- behavior fingerprinting
- hash-chained audit trail
- agent-to-agent constraint propagation
- advanced prompt-injection / security system
- generalized chaos testing harness
- CI/CD reliability gate
- DSPy auto-improvement
- replay UI
- regression comparison UI
- full reliability report
- multi-tenancy
- production data governance suite
- ClickHouse migration

Likewise, the Risk Engine's `goal_drift` factor is a fixed neutral
placeholder (Phase 3 territory), not a real signal.

## 18. Final Verdict

**PHASE 2 — PASS**

Basis: all mandatory Phase 2 objectives are implemented (§3–4); 86/86
runnable automated tests pass, 4 skip only for the documented,
pre-existing environment limitation of no local PostgreSQL (§5); all
three E2E scenarios (RETRY, ROOT-CAUSE+STOP, HUMAN including approve/
reject/timeout) were executed for real and produced the evidence in
§6–8; the WebSocket channel was verified against a real ASGI
WebSocket handshake (§12); the LLM evaluator's behavior was verified
with measured timing evidence and its use of a deterministic stand-in
provider (rather than a real model) is explicitly documented as a
stated environment limitation, not concealed (§9); the Phase 1
regression suite passes in full with zero modifications to the
original test functions (§14); every piece of evidence above is a real
run_id, a real decision, a real persisted record, or a real measured
timing — not a narrative description.
