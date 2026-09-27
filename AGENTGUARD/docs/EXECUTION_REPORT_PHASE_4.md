# AgentGuard Phase 4 Execution Report

## 1. Baseline

Recorded **before** any Phase 4 code was written, against the Phase 3
codebase exactly as found. `docs/EXECUTION_REPORT_PHASE_1.md` does not
exist under that name in this repository — Phase 1's report lives at
`docs/PHASE1.md`; both it and `docs/EXECUTION_REPORT_PHASE_2.md`/
`docs/EXECUTION_REPORT_PHASE_3.md` were read before any Phase 4 change.

```
command:  .venv/Scripts/python.exe -m pytest tests/ -q
passed:   139
failed:   0
skipped:  4   (tests/integration/test_postgres_repository.py — no AGENTGUARD_TEST_DATABASE_URL)
```

Breakdown at baseline: `tests/unit` 100 passed, `tests/e2e` 16 passed,
`tests/integration` 23 passed + 4 skipped.

The Phase 3 canonical demo (`python examples/budget_failure_recovery.py`)
was re-run and confirmed still working before any Phase 4 change:
DETECTED → ROOT CAUSE (S3) → STOP → ROLLBACK (S2) → COUNTERFACTUAL →
RECOVERY (CONTINUE) → SUCCESS, audit chain INTACT.

**No Phase 3 regression existed at baseline.** Phase 4 proceeded
directly to implementation.

## 2. Phase 4 Objective

1. Failure Replay
2. Agent Version Comparison / Regression Comparison
3. CLI tooling (`tail`, `replay`, `report`, `compare`, `ci-gate`, `verify-audit`)
4. CI/CD Reliability Gate
5. Tool Reliability Profiles
6. Alternative-Tool Recovery
7. Agent Behavior Fingerprinting
8. DSPy-based Auto-Improvement (optional dependency)
9. Prompt/workflow recommendation workflow
10. Control / Configuration Portal for existing policies and reliability controls
11. Automated tests
12. This execution report

Required closed loop: execution → evaluation → failure → root cause →
recovery → replay → regression comparison → recommendation →
prompt/workflow improvement → re-execution → re-evaluation. Explicitly
**not** implemented per the prompt's own scope boundary: unrelated new
product features, replacing PostgreSQL, removing OpenTelemetry/the
Decision Engine/checkpoint/rollback/counterfactual/risk/confidence/
audit/HITL, a dashboard rebuild, or production-scale multi-tenancy —
confirmed absent, see §19.

## 3. Components Implemented

| Component | Files | Implemented | Tested | Evidence | Status |
|---|---|---|---|---|---|
| Replay | `agentguard/replay/engine.py` | Yes | Yes (`tests/unit/test_replay.py`, 5; `tests/e2e/test_demo1_replay.py`, 1) | §6 | DONE |
| Compare | `agentguard/regression/compare.py`, `corpus.py` | Yes | Yes (`tests/unit/test_compare.py`, 8; `tests/e2e/test_demo2_regression.py`, 1) | §7 | DONE |
| CLI | `agentguard/cli/__init__.py` (Typer) | Yes | Yes (`tests/unit/test_cli.py`, 11) | §8 | DONE |
| CI Gate | `agentguard/ci/gate.py` | Yes | Yes (`tests/e2e/test_demo5_ci_gate.py`, 3; CLI/API tests) | §12 | DONE |
| Tool Profiles | `agentguard/reliability/tool_profile.py`, `agentguard/models.py` (`ToolCallEvent`, `ToolProfile`) | Yes | Yes (`tests/unit/test_tool_profile.py`, 8) | §9 | DONE |
| Alternative Recovery | `agentguard/tools/registry.py`, `agentguard/context.py` (`call_tool`) | Yes | Yes (`tests/unit/test_tool_alternatives.py`, 5; `tests/e2e/test_demo3_tool_fallback.py`, 1) | §10 | DONE |
| Behavior Fingerprint | `agentguard/reliability/fingerprint.py` | Yes | Yes (`tests/unit/test_fingerprint.py`, 6; `tests/e2e/test_demo4_behavior_anomaly.py`, 2) | §11 | DONE |
| DSPy / Improvement | `agentguard/improve/{recommend,optimizer,workflow}.py` | Yes (DSPy optional dependency; deterministic stand-in active in this environment) | Yes (`tests/unit/test_improvement.py`, 8; `tests/e2e/test_demo6_auto_improvement.py`, 1) | §13 | DONE (limitation documented) |
| Control Portal | `dashboard/index.html` (Control/Configuration view), `server/api.py` (policy/tools/improvements/fingerprint endpoints) | Yes | API-level + JS-syntax verified (`tests/e2e/test_portal_e2e.py`, 3); no interactive browser | §14, §16 | DONE, not interactively browser-tested |
| Policy Version Control | `agentguard/policy/registry.py`, `agentguard/models.py` (`PolicyDefinition`, `PolicyVersionRecord`) | Yes | Yes (`tests/unit/test_policy_registry.py`, 8; `tests/e2e/test_portal_e2e.py`) | §14 | DONE |
| Storage/Migrations | `agentguard/storage/migrations/0004_phase4.sql`, `storage/{repository,postgres,memory,schema.sql}` | Yes | Not executed against live Postgres (none available — same limitation as Phase 2/3 §1); purely additive, reviewed by inspection | §20 | DONE, unverified against live Postgres |

## 4. Objective Verification Matrix

| Objective | Required | Implemented | Tested | Execution Evidence | Status |
|---|---|---|---|---|---|
| Failure Replay (safe, step-by-step) | Yes | Yes | Yes | §6 | PASS |
| Regression Comparison (factual, no winner) | Yes | Yes | Yes | §7 | PASS |
| CLI: tail/replay/report/compare/ci-gate/verify-audit | Yes | Yes | Yes | §8 | PASS |
| CI/CD Reliability Gate, deterministic rule, correct exit codes | Yes | Yes | Yes | §12 | PASS |
| Tool Reliability Profiles, honest INSUFFICIENT_DATA | Yes | Yes | Yes | §9 | PASS |
| Alternative-Tool Recovery, explicit registration only | Yes | Yes | Yes | §10 | PASS |
| Agent Behavior Fingerprinting, requires history | Yes | Yes | Yes | §11 | PASS |
| DSPy-optional Auto-Improvement, never auto-deploys | Yes | Yes | Yes | §13 | PASS (real-DSPy limitation documented) |
| Prompt/workflow recommendation | Yes | Yes | Yes | §13 | PASS |
| Control Portal against real backend APIs | Yes | Yes | API-level | §14, §16 | PASS (browser limitation documented) |
| Policy versioning: new version, old runs unaffected | Yes | Yes | Yes | §14 | PASS |
| All prior-phase tests remain green | Yes | Yes | Yes | §17 | PASS |
| Decision Engine never bypassed | Required | Confirmed (tool exhaustion routes through REPLAN/HUMAN, same machinery) | Yes | §10 | PASS |
| Phase 4 exclusions (chaos harness, A2A, multi-tenancy, dashboard rebuild, etc.) NOT implemented | Required absent | Absent | N/A | §19 | PASS |

## 5. Test Results

```
command:  .venv/Scripts/python.exe -m pytest tests/ -q
total:    223
passed:   219
failed:   0
skipped:  4   (tests/integration/test_postgres_repository.py — no AGENTGUARD_TEST_DATABASE_URL)
```

Breakdown:

```
tests/unit          159 passed   (100 Phase 1-3, unmodified + 59 new Phase 4)
tests/e2e             28 passed  (16 Phase 1-3, unmodified + 12 new Phase 4)
tests/integration     32 passed, 4 skipped  (23 Phase 1-3, unmodified + 9 new Phase 4)
```

New Phase 4 unit test files: `test_replay.py` (5), `test_compare.py`
(8), `test_cli.py` (11), `test_tool_profile.py` (8),
`test_tool_alternatives.py` (5), `test_fingerprint.py` (6),
`test_improvement.py` (8), `test_policy_registry.py` (8) — 59 total,
covering every one of the spec's 29 enumerated unit-test items.

New Phase 4 integration test file: `test_phase4_api.py` (9 tests,
covering all 11 enumerated integration items — several combined per
test where the spec's items are two facets of one real flow, e.g.
"policy creation → persistence" and "policy version → new version" are
exercised together in `test_policy_creation_versioning_and_historical_isolation`).

New Phase 4 e2e test files (spec §27–33): `test_demo1_replay.py` (1),
`test_demo2_regression.py` (1), `test_demo3_tool_fallback.py` (1),
`test_demo4_behavior_anomaly.py` (2), `test_demo5_ci_gate.py` (3),
`test_demo6_auto_improvement.py` (1), `test_portal_e2e.py` (3) — 12 total.

## 6. Replay Evidence

Run via `agentguard replay <run_id>` (real CLI invocation, output
below) against the canonical budget-failure run:

```
$ agentguard replay af429d3b-9a50-4cfc-b253-f0e2c669e4ad
REPLAY MODE
NO EXTERNAL SIDE EFFECTS
run_id: af429d3b-9a50-4cfc-b253-f0e2c669e4ad
policy_version: 1   agent_version: —

Step 1 [event]
Task received

Step 2 [event]
Agent Step

Step 3 [state]
State: {'max_budget': 60000.0}

Step 4 [state]
State: {'max_budget': 60000}

Step 5 [state]
State: {}

Step 6 [state]
State: {'max_budget': 67000}

Step 7 [evaluation]
Evaluation: constraint_adherence = constraint_violated (passed=False)

Step 8 [event]
Policy Check

Step 9 [event]
Risk Assessment

Step 10 [event]
Root cause: S3 — max_budget disappeared during state transition S3

Step 11 [decision]
Decision: STOP — high-confidence unsafe evaluation: constraint_adherence

Step 12 [event]
Run completed: STOP
```

Safe-replay confirmation: `session.mode == "safe"` and
`session.no_external_side_effects is True` on every `ReplaySession`,
verified by `tests/unit/test_replay.py::test_replay_never_executes_agent_code_or_tools`,
which monkeypatches the tool registry's `get_callable` to raise if
replay ever tried to invoke it — it never does. The reconstruction is
step-by-step and kind-classified (`event`/`state`/`evaluation`/
`decision`), not a raw dump of `list_audit_events()` — confirmed by
`test_replay_is_not_just_a_raw_log_dump`, which shows the raw
`CHECKPOINT` audit event has no `state` key while the replay step's
joined-in reconstruction does.

## 7. Regression Comparison Evidence

Real CLI output (`agentguard compare <run_a> <run_b>`) comparing the
canonical STOP run (A) against a compliant CONTINUE run (B):

```
metric                           run A       run B       delta
correctness                         0%        100%       +100%
goal_completion            unavailable unavailable         n/a
constraint_adherence                0%        100%       +100%
decision_consistency              100%        100%         +0%
tool_usage                        100%        100%         +0%
behavioral_reliability             50%        100%        +50%
risk                               75%          0%        -75%
confidence                        100%        100%         +0%
retry_count                       0.00        0.00       +0.00
replan_count                      0.00        0.00       +0.00
human_intervention_count          0.00        0.00       +0.00
failure_count                     1.00        0.00       -1.00
latency_ms                        4.33        0.22       -4.11
```

`goal_completion` is `unavailable`/`n/a` on both sides because these
runs used `llm_judge=False` (deterministic, reproducible evidence — see
Phase 2's stated LLM-provider limitation, unchanged here) — never
defaulted to 0, always shown as unavailable. Agent-version regression
evidence (`tests/e2e/test_demo2_regression.py`): agent `v1` (constraint
lost, 67000 selected) vs. agent `v2` (constraint preserved, 52000
selected) — `constraint_adherence` 0% → 100% (+100pp), `risk` 75% → 0%
(both a "higher is better" and a "lower is better" metric shown
side-by-side, factually, with no declared winner anywhere in the
`ComparisonResult`/`MetricDelta` structures).

## 8. CLI Evidence

| Command | Exit code | Output summary |
|---|---|---|
| `agentguard tail <stop_run>` | 0 | 12 lines, `RUN_START` first, `RUN_COMPLETION` last, real timestamps |
| `agentguard report <stop_run>` | 0 | `Status: stop`, all six dimensions, `Risk: 0.75`, `Root cause: max_budget disappeared during state transition S3` |
| `agentguard report <stop_run> --json` | 0 | valid JSON matching `build_reliability_report(...).to_dict()` exactly |
| `agentguard replay <stop_run>` | 0 | `REPLAY MODE` / `NO EXTERNAL SIDE EFFECTS` + 12 steps (§6) |
| `agentguard compare <stop_run> <continue_run>` | 0 | 13-row metric/runA/runB/delta table (§7) |
| `agentguard verify-audit <stop_run>` | 0 | `12 events checked` / `AUDIT INTACT` |
| `agentguard verify-audit <tampered_run>` | 1 | `AUDIT INTEGRITY VIOLATION` / `First broken event: <id>` |
| `agentguard ci-gate --baseline-runs=<96%> --candidate-runs=<88%> --threshold=5` | 1 | `Regressions: correctness -8.0 pct points, constraint_adherence -8.0 pct points` / `BLOCK` |
| `agentguard ci-gate --baseline-runs=<96%> --candidate-runs=<92%> --threshold=5` | 0 | `No protected metric regressed beyond the threshold.` / `PASS` |
| `agentguard report <does-not-exist>` | 1 | `run 'does-not-exist' not found` (stderr) |

All ten rows are real invocations via `typer.testing.CliRunner` (unit
tests) and direct process-equivalent calls (ad hoc verification runs) —
not narrated output.

## 9. Tool Reliability Evidence

`search_api`, after 6 recorded calls (5 failures used to build history,
1 more failure during a real `@monitor` run — see
`tests/integration/test_phase4_api.py::test_tool_events_to_tool_profile`):

```
tool: search_api
sample_count: 6
success_rate: 0.0
failure_rate: 1.0
timeout_rate: 0.0
latency: (mean computed; p95 requires >= 20 samples, reported None below that)
reliability: UNRELIABLE
```

Insufficient-data case (`tests/unit/test_tool_profile.py::test_insufficient_data_below_min_sample_size`):
4 samples (`< MIN_SAMPLE_SIZE=5`) → `reliability: INSUFFICIENT_DATA`,
`success_rate: None` — never a misleading rate computed from too few
calls. Threshold boundaries verified precisely:
`success_rate >= 0.95` → `RELIABLE`, `>= 0.80` → `DEGRADED`, below →
`UNRELIABLE`
(`tests/unit/test_tool_profile.py::test_success_rate_computed_correctly`,
`test_reliable_and_unreliable_thresholds`). `p95` verified to require
`>= MIN_P95_SAMPLES=20` recorded latencies before being computed at all
(`test_p95_latency_requires_enough_samples`).

## 10. Alternative Tool Recovery Evidence

From `tests/e2e/test_demo3_tool_fallback.py` (a real `@monitor` run, no
component mocked beyond the tool callables themselves):

```
primary:       search_api
failure:       raises RuntimeError, twice (primary_attempts=2)
profile:       consulted (INSUFFICIENT_DATA on a fresh tool — falls back
               defensively; a tool with an established UNRELIABLE
               profile also triggers the same path — see
               tests/unit/test_tool_alternatives.py)
alternative:   search_api_backup (explicitly registered via
               ToolRegistry.register_alternative)
decision:      no new RunStatus transition — the run's normal EVALUATING
               branch still runs afterward; substitution itself is
               recorded as a TOOL_SUBSTITUTED audit event (visible in
               the timeline and the Reliability Report's interventions)
final result:  run.status == "continue"
audit event:   {"event_type": "TOOL_SUBSTITUTED", "payload": {"primary":
               "search_api", "fallback": "search_api_backup", ...}},
               positioned (by seq) after the two failed TOOL_CALL events
               and before the successful one
```

Negative-path evidence (never a silent/arbitrary substitution):
`tests/unit/test_tool_alternatives.py::test_fallback_rejected_when_no_alternative_registered`
and `..._registered_but_not_registered_as_callable` both confirm no
`TOOL_SUBSTITUTED` event is ever recorded and the run correctly falls
through to `policy.on_tool_exhausted` (REPLAN, exhausted, then STOP —
the pre-existing Phase 2 replan-exhaustion contract, unchanged).

## 11. Behavior Fingerprint Evidence

From `tests/e2e/test_demo4_behavior_anomaly.py` — 6 historical
`research_agent` runs at 7 tool calls each, then one run with 19:

```
agent:          research_agent
sample_count:   6            (the anomalous run itself excluded)
baseline:       tool_calls=7.0, retry_count=0.0, latency_ms=<measured>
current:        tool_calls=19.0
deviation:      tool_calls: +171% (> the 50% anomaly_threshold)
anomaly:        True
```

Insufficient-history companion test in the same file: only 3 prior runs
→ `sample_count=3`, `baseline=None`, `anomaly=False` — never a
statistically meaningless verdict from too little history.

## 12. CI Gate Evidence

Real CLI invocations (`agentguard ci-gate`), 25-run baseline/candidate
batches built from real `@monitor` executions (24/25 = 96%, 22/25 = 88%,
23/25 = 92% constraint-adherence pass rates):

```
$ agentguard ci-gate --baseline-runs=<96%-batch> --candidate-runs=<88%-batch> --threshold=5
protected metrics: correctness, goal_completion, constraint_adherence, decision_consistency, tool_usage, behavioral_reliability
threshold: 5.0 percentage points
Regressions:
  correctness: -8.0 pct points
  constraint_adherence: -8.0 pct points
BLOCK
exit code: 1

$ agentguard ci-gate --baseline-runs=<96%-batch> --candidate-runs=<92%-batch> --threshold=5
threshold: 5.0 percentage points
No protected metric regressed beyond the threshold.
PASS
exit code: 0
```

This is exactly the spec's worked example (96% → 88%, threshold 5,
regression 8 points, BLOCK) plus the within-threshold PASS case, both
with correct exit codes, both also verified via `POST /api/ci-gate`
(`tests/integration/test_phase4_api.py::test_ci_gate_correct_exit_codes`).
`goal_completion` never participates in either verdict (unavailable on
both sides, `llm_judge=False`) — confirming unavailable metrics are
skipped, never treated as a failure.

## 13. Auto-Improvement Evidence

From `tests/e2e/test_demo6_auto_improvement.py`, using the canonical
budget-loss failure:

```
failure:         drifting_agent run — max_budget disappears at S3, 67000 selected, STOP
root cause:      "max_budget disappeared during state transition S3"
recommendation:  "Preserve max_budget as an immutable constraint in the planning state."
candidate_change:
  before: "max_budget embedded only in prompt/state and vulnerable to
           being overwritten by reset_state()"
  after:  "max_budget stored as a validated, typed AgentState field
           that survives state resets"
optimizer:            DeterministicTestOptimizer
real_dspy_optimizer:  False   <-- explicit, honest flag
```

**DSPy code path exists vs. was actually executed**: `dspy==3.4.0` is
installed in this environment's venv (confirms the optional dependency
imports cleanly when present), but no `ANTHROPIC_API_KEY`/OpenAI key nor
`AGENTGUARD_DSPY_MODEL` is configured, so `get_default_optimizer()`
correctly falls back to `DeterministicTestOptimizer` — the same pattern
Phase 2 established for `LLMJudge`/`AnthropicProvider`. A real DSPy
optimization run (`DSPyOptimizer`, which lazily imports `dspy` and calls
`dspy.Predict` through a configured `dspy.LM`) was **not executed** in
this environment — stated explicitly, not implied by the mere presence
of the package.

Historical replay → candidate evaluation → comparison (real numbers,
not fabricated): a real "fixed" candidate agent (constraint never reset
away) was executed against the corpus (`[failed_run_id]`); the
resulting `ImprovementEvaluation.comparison` shows
`constraint_adherence`: baseline 0.0 → candidate 1.0 (delta +1.0).

Candidate status: `"proposed"` immediately after creation — confirmed
in the persisted repository record, not just the in-memory object.
Approval is a separate, explicit step
(`approve_candidate(repository, candidate.id, "reviewer@example.com")`)
— `ImprovementCandidate` has no `deploy`/`apply` method at all
(`tests/unit/test_improvement.py::test_never_auto_deploys`), and
`approve_candidate`/`reject_candidate` both raise `ApprovalError` if
called without an explicit approver or on an already-resolved candidate.

## 14. Policy Control Portal Evidence

From `tests/e2e/test_portal_e2e.py::test_portal_policy_lifecycle_end_to_end`,
driven through the real FastAPI app:

```
POST /api/policies {"name":"checkout_policy","policy":{"max_cost":60000,"retry_limit":2}}
  -> version 1

[a run executes against version 1 -> old_run_id]

POST /api/policies/checkout_policy/versions {"policy":{"max_cost":60000,"retry_limit":9}}
  -> version 2   (version 1 record untouched)

[a new run executes against the NEW current policy -> new_run_id]

GET /api/runs/{new_run_id} -> policy.version == 2, policy.retry_limit == 9
GET /api/runs/{old_run_id} -> policy.version == 1, policy.retry_limit == 2   <- unchanged
GET /api/policies/checkout_policy/versions -> [{"version":1,"retry_limit":2}, {"version":2,"retry_limit":9}]
```

Old policy version: 1 (`retry_limit=2`). New policy version: 2
(`retry_limit=9`). Configuration change: `retry_limit` 2 → 9. New run
references version 2. Old run retains version 1 — none of the four
required facts is asserted from narration; all four are literal
`assert` statements in the test, currently passing.

Save-failure honesty (`test_portal_save_failure_reports_the_actual_error`):
`POST /api/policies/does-not-exist/versions` → `400`, body
`{"detail": "policy 'does-not-exist' does not exist — use create() first"}`
— never a false "Saved" on the frontend.

## 15. API Verification

| Method | Endpoint | Status | Result |
|---|---|---|---|
| GET | `/api/runs/{run_id}/replay` | 200 | safe replay session, 12 steps |
| GET | `/api/runs/{run_id}/replay` (unknown) | 404 | `{"detail": "run '...' not found"}` |
| GET | `/api/runs/compare?run_a=&run_b=` | 200 | 13-metric comparison |
| GET | `/api/runs/compare?run_a=missing&run_b=missing` | 404 | `{"detail": "run '...' not found"}` |
| GET | `/api/tools` | 200 | `["search_api", "search_api_backup"]` |
| GET | `/api/tools/{tool_name}/profile` | 200 | full `ToolProfile` |
| GET | `/api/tools/alternatives` | 200 | list of registered mappings |
| POST | `/api/tools/alternatives` | 200 | registers + updates the live `ToolRegistry` immediately |
| GET | `/api/agents/{agent_id}/fingerprint` | 200 | baseline/current/deviation/anomaly |
| GET | `/api/improvements` | 200 | list of candidates |
| GET | `/api/improvements/{id}` | 200 | candidate + its evaluations |
| POST | `/api/improvements/{id}/approve` | 200 | `status: "approved"` |
| POST | `/api/improvements/{id}/reject` | 200 | `status: "rejected"` |
| GET | `/api/policies` | 200 | named policy definitions |
| POST | `/api/policies` | 200 | version 1 created |
| GET | `/api/policies/{name}` | 200 | current version's fields |
| GET | `/api/policies/{name}/versions` | 200 | full version history |
| POST | `/api/policies/{name}/versions` | 200 | new version created |
| POST | `/api/ci-gate` | 200 | `{"result": "pass"|"block", ...}` |

Every Phase 1-3 endpoint re-verified unchanged via the full regression
suite (§17) — no signature or response-shape change to any of them.

## 16. Dashboard Verification

Implemented, additively (the existing Runs view/tabs are untouched):
a header nav switch ("Runs" / "Control / Configuration"); a new
"Replay" tab on the run-detail panel; a Control/Configuration view with
Agent Behavior Fingerprint lookup, a Policy Control Portal (select/
create/edit/save-new-version form, version history), Tool Recovery
(list + register primary→fallback mappings), an honestly-labeled
read-only note for Evaluation configuration (not backend-configurable
in this phase — see README's Control Portal section), and an
Improvement Candidates list with Approve/Reject actions.

Verification actually performed:

1. The `<script>` block was extracted and run through `node --check`
   (Node v24.18.0) — no syntax errors.
2. Every new endpoint the dashboard calls was hit live via `TestClient`
   and confirmed to return the shape the JS expects (§15).
3. `GET /` confirmed to serve the updated HTML containing "Control /
   Configuration", "Policy Control Portal", "Tool Recovery", and
   "Behavior Fingerprint" (`tests/e2e/test_portal_e2e.py::test_portal_dashboard_html_contains_control_portal_section`).

**Interactive browser verification was NOT performed** — no browser
automation tool was available in this environment (the configured
`browser-use` MCP server failed to connect: `CONNECTION_CLOSED`), and no
GUI browser exists in this Windows CLI environment. Stated explicitly,
matching Phase 2/3's own documented dashboard-verification limitation —
not claimed as tested when it was not.

## 17. Regression Verification

```
Phase 1 baseline (docs/PHASE1.md):           23 passed, 0 failed, 0 skipped
Phase 2 baseline (EXECUTION_REPORT_PHASE_2):  86 passed, 0 failed, 4 skipped
Phase 3 baseline (EXECUTION_REPORT_PHASE_3): 139 passed, 0 failed, 4 skipped
Phase 4 final:    .venv/Scripts/python.exe -m pytest tests/ -q
                  219 passed, 0 failed, 4 skipped
```

All 139 Phase 1-3 test functions are present, unmodified, among the 219
Phase 4 total, and all pass verbatim — the increase is 80 additional
Phase 4 test functions, not replaced earlier-phase tests. No Phase 1, 2,
or 3 test was modified to make it pass. The Phase 3 canonical demo
(`examples/budget_failure_recovery.py`) was re-run after all Phase 4
changes and still produces the identical DETECTED → ROOT CAUSE → STOP →
ROLLBACK → COUNTERFACTUAL → RECOVERY → SUCCESS sequence with an intact
audit chain (§1).

## 18. Problems Encountered

| Problem | Root Cause | Fix | Verification |
|---|---|---|---|
| `BehaviorFingerprint.deviation`/`baseline`/`current` were typed `dict[str, float]`, but a zero-baseline metric legitimately produces a `None` deviation (both baseline and current exactly 0 — no meaningful ratio) | Pydantic rejected the `None` value inside the dict, since the field's value type didn't allow it | Widened the three fields to `dict[str, float \| None] \| None` | `tests/unit/test_fingerprint.py::test_zero_baseline_with_nonzero_current_is_treated_as_full_deviation` failed before, passes after; full suite re-run green |
| First `tests/unit/test_cli.py` draft used `async def` test functions that `await`ed setup and then called `runner.invoke(...)`, but Typer commands call `asyncio.run(...)` internally — nesting it inside pytest-asyncio's already-running event loop raised `RuntimeError`/silently produced empty output for every CLI test | A real CLI process never has an event loop already running; the test file's shape assumed otherwise | Rewrote every CLI test as a plain `def` (matching the existing `tests/integration/test_websocket_approval.py` convention), using `asyncio.run(...)` explicitly for setup instead of `await` | All 11 `test_cli.py` tests failed with this shape before the rewrite, all pass after |
| `tests/unit/test_tool_alternatives.py`'s first draft asserted `await agent(...)` would return normally when no alternative was available; it actually raises `ReplanRequested` once the replan budget is exhausted | Misremembered the exact contract: this is Phase 2's own pre-existing, intentional behavior (decorator.py re-raises the triggering exception to the caller once RETRY/REPLAN is exhausted, alongside `run.status=STOP`) — not a Phase 4 defect | Wrapped the calls in `pytest.raises(ReplanRequested)`, matching the documented Phase 2 contract exactly | Reproduced the exact traceback directly (outside pytest) to confirm it was the existing, intentional re-raise path, then fixed the test, not the implementation |
| `agent_version` existed on the `Run` model (added early, for Regression Comparison) but was never actually threaded through the `@monitor` decorator's own parameters — `test_demo2_regression.py` failed with `TypeError: monitor() got an unexpected keyword argument 'agent_version'` | The model field was added before the decorator's public signature was updated to accept and forward it | Added `agent_version: str \| None = None` to `monitor()`'s signature and to `_execute()`'s signature, threading it into the constructed `Run(...)` | `tests/e2e/test_demo2_regression.py` failed before, passes after; `Run.agent_version` now genuinely settable from the public API, not just the model |
| A first draft of `test_demo2_regression.py` asserted every available metric would show a non-negative delta between v1 and v2, but `risk` (a "lower is better" metric) correctly showed a *negative* delta for the same real improvement | The assertion assumed all comparison metrics point the same "direction" | Replaced the blanket assertion with explicit, direction-aware checks for `constraint_adherence` (up) and `risk` (down) | Failure reproduced, root-caused to the test's own flawed assumption (not the comparison engine, which was behaving correctly), test fixed |

## 19. Deviations

| Specification | Actual implementation | Reason | Impact |
|---|---|---|---|
| "Support a useful `--follow` mode if the existing architecture supports live event streaming" | `--follow` is a real, working, correct **polling** loop (repeatedly calls `list_audit_events`/`get_run` until a terminal status, printing new events since the last poll) — not a push subscription | AgentGuard has no general event pub/sub outside the dashboard's HUMAN-approval WebSocket channel (a narrow, purpose-built mechanism); building a new general-purpose event bus was out of scope and the spec explicitly permits "a correct historical stream" with the limitation documented | None on correctness — every event is still shown, in order, exactly once; latency to see a new event is bounded by `--interval` (default 2s) rather than instantaneous |
| "Potential entities: ... BehaviorFingerprint, ReplaySession" (spec §23) | Neither is persisted as its own table — both are computed FRESH from already-persisted raw data (`ToolCallEvent`/`Run` history for fingerprints; `AuditEvent`/`Checkpoint` history for replay) every time they're requested | The word "Potential" in the spec's own entity list signals these are optional; computing fresh avoids a second, independently-staleable copy of data that's already fully derivable from existing tables — the same "recompute, don't cache" principle Phase 3's Reliability Report already established | None on functionality; a very large number of tool calls or runs could make fingerprint/replay computation slower per-request than a cached read, but this is an offline/developer-facing path (§ Performance), never the agent's synchronous response path |
| "Alternative tools must be explicitly registered/configured" + Control Portal's "Registered alternatives" | Registration lives in an in-process `ToolRegistry` (the actual source `call_tool()` reads from, with no I/O) AND is separately persisted (`agentguard_tool_alternatives`, via `POST /api/tools/alternatives`) for portal visibility/audit — but persisting a registration does not, by itself, re-populate the in-process registry after a process restart; only the `POST` endpoint (which updates both at once) does that live | A registry read on every `call_tool()` invocation must be synchronous, local, and fast (Rule 15's "small synchronous safety checks" for the runtime path) — an async DB read on every tool call would violate that; the persisted table exists for portal/audit visibility, not as the runtime lookup path | A tool alternative persisted only via direct repository access (bypassing the API) would not take effect until also registered in-process — documented here and in the module docstring, not silently assumed to "just work" end-to-end across a restart |
| "Track per tool: ... duplicate-call rate" | Defined narrowly and explicitly: a call is "duplicate" only when the exact same `(tool_name, args, kwargs)` tuple was already called earlier in the SAME run | The spec does not define what scope "duplicate" means (same run? same args? any repeat call at all?); a narrower, precisely-documented definition is safer than guessing a broader one that could misclassify legitimate repeated calls with different arguments as duplicates | None observed; documented in `context.py`'s `call_tool()` docstring so the metric's exact meaning is always inspectable, not assumed |

Never hidden: all four deviations are also called out in the relevant
module docstrings (`agentguard/cli/__init__.py`, `agentguard/replay/engine.py`,
`agentguard/tools/registry.py`, `agentguard/context.py`).

## 20. Environment Limitations

```
PostgreSQL:          NOT available. No `docker` binary, no
                     AGENTGUARD_DATABASE_URL, no reachable server.
                     Same limitation Phase 1-3 already documented.
                     Every Phase 4 storage change is implemented in
                     BOTH PostgresRunRepository and the in-memory
                     repository the whole test suite/examples run
                     against — the Postgres code is reviewed by
                     inspection only, never executed against a live
                     database.

OTel collector:      NOT available (unchanged from Phase 3). OTLP
                     export construction/attempt was already verified
                     in Phase 3 (connection-refused, as expected with
                     no collector). Phase 4 introduced no new OTel
                     surface.

LLM provider:        NOT available. No ANTHROPIC_API_KEY/OpenAI key.
                     LLMJudge uses DeterministicTestProvider
                     (unchanged from Phase 2) — every evaluation
                     dependent on it (goal_completion, and correctness
                     when a real judge would otherwise be used) is
                     honestly reported "unavailable" or derived from
                     the deterministic constraint evaluator, never
                     fabricated.

DSPy:                The `dspy` package (3.4.0) installs and imports
                     cleanly (confirms the optional-dependency path is
                     real), but no AGENTGUARD_DSPY_MODEL / underlying
                     LLM API key is configured, so
                     get_default_optimizer() resolves to
                     DeterministicTestOptimizer for every auto-
                     improvement candidate produced in this
                     environment — see §13. real_dspy_optimizer=False
                     on every candidate confirms this was never
                     silently presented as a real optimization run.

Browser automation:  NOT available. The configured `browser-use` MCP
                     server failed to connect (CONNECTION_CLOSED), and
                     no GUI browser exists in this Windows CLI
                     environment. Dashboard/Control-Portal
                     verification is therefore API-level +
                     JS-syntax-level only (§16) — stated, not
                     concealed.
```

## 21. Final Verdict

**PHASE 4 — PASS**

Basis: Failure Replay reconstructs real, stored evidence step-by-step in
SAFE/DRY-RUN mode with a test that would fail if it ever executed agent
or tool code (§6); Regression Comparison reports factual per-metric
deltas with no winner ever declared, structurally (§7); the full CLI
(`tail`/`replay`/`report`/`compare`/`ci-gate`/`verify-audit`) is real,
calls the actual AgentGuard APIs, and every command's exit code and
output were verified live (§8); the CI/CD gate implements the spec's
exact deterministic rule and was verified against the spec's own worked
example (96%→88%, threshold 5, 8-point regression, BLOCK, exit 1) plus a
within-threshold PASS/exit-0 case (§12); Tool Reliability Profiles never
compute a misleading statistic from too few samples and Alternative-Tool
Recovery only ever substitutes an explicitly registered tool, verified
end-to-end with a real primary-fails-twice/fallback-succeeds/CONTINUE
scenario and negative-path tests confirming no substitution occurs
without registration (§9-10); Behavior Fingerprinting requires real
history before reporting a baseline and correctly flags the spec's own
7-vs-19-tool-calls anomaly example (§11); the auto-improvement workflow
proposes real candidates from real root causes, evaluates them against a
real historical corpus with a real "fixed" candidate agent, never
auto-deploys (no `deploy`/`apply` method exists at all), and explicitly
distinguishes the deterministic stand-in it used from a real DSPy run it
did not (§13); the Policy Control Portal creates new versions without
ever mutating history, verified end-to-end (old run keeps its original
version, new run gets the new one) against the real backend API (§14);
every new API endpoint was hit live and returned the expected shape
(§15); the dashboard's Control/Configuration portal was verified at the
API/JS-syntax level with the browser-automation limitation explicitly
stated (§16); the full Phase 1-3 regression suite plus the Phase 3
canonical demo both remain green and unmodified alongside 80 new Phase 4
tests, 219/219 runnable tests passing (§17); all deviations from the
literal spec text are disclosed with reasoning (§19); and every
environment limitation (Postgres, OTel collector, LLM provider, DSPy,
browser automation) is stated plainly rather than concealed (§20).
