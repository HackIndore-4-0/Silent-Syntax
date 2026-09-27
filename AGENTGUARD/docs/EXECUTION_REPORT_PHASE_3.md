# AgentGuard Phase 3 Execution Report

## 1. Baseline

Recorded **before** any Phase 3 code was written, against the Phase 2
codebase exactly as found (repository inspected, `docs/PHASE1.md` and
`docs/EXECUTION_REPORT_PHASE_2.md` read first).

```
command:  .venv/Scripts/python.exe -m pytest tests/ -v
passed:   86
failed:   0
skipped:  4   (tests/integration/test_postgres_repository.py — no AGENTGUARD_TEST_DATABASE_URL)
```

Breakdown at baseline: `tests/unit` 63 passed, `tests/e2e` 14 passed,
`tests/integration` 9 passed + 4 skipped.

Environment: no `docker` binary, no `AGENTGUARD_DATABASE_URL`, no
reachable PostgreSQL — identical environment limitation Phase 1 and
Phase 2 already documented, not new to Phase 3.

**No Phase 2 regression existed at baseline.** Phase 3 proceeded
directly to implementation.

## 2. Phase 3 Objective

1. Audit-trail timeline
2. Six-dimension Reliability Report
3. Checkpoint system
4. Rollback
5. Counterfactual analysis
6. Hash-chained / tamper-evident audit events
7. Canonical budget-constraint failure demonstration
8. OpenTelemetry export / interoperability demonstration
9. Dashboard support for the above
10. Full automated verification
11. This execution report

Required end-to-end flow: execution → evaluation → failure detection →
root-cause identification → checkpoint recovery → rollback →
counterfactual safe-path analysis → re-execution/recovery → successful
completion → auditable history. Phase 4 (tool reliability profiles,
alternative-tool recovery, behavior fingerprinting, chaos harness,
CI/CD gate, DSPy, replay/compare/ci-gate/verify-audit CLIs, full A2A
propagation, multi-tenancy, production governance) explicitly **not**
implemented — see §18.

## 3. Components Implemented

| Component | Files | Implemented | Tested | Evidence | Status |
|---|---|---|---|---|---|
| Checkpoint Engine | `agentguard/checkpoint/engine.py`, `agentguard/models.py` (`Checkpoint`) | Yes | Yes (`tests/unit/test_checkpoint.py`, 6 tests) | §6, §10 | DONE |
| Hash-Chained Audit | `agentguard/audit/hashchain.py`, `agentguard/audit/chain.py`, `agentguard/models.py` (`AuditEvent`) | Yes | Yes (`tests/unit/test_audit.py`, 9 tests) | §9 | DONE |
| Audit Timeline | `server/api.py` (`GET /timeline`), `dashboard/index.html` (Timeline tab) | Yes | Yes (`tests/integration/test_phase3_persistence.py::test_timeline_api`) | §11, §12 | DONE |
| Rollback | `agentguard/recovery/rollback.py`, `agentguard/recovery/seed.py`, `agentguard/decision/engine.py` (`rollback()`, `RunStatus.ROLLED_BACK`) | Yes | Yes (`tests/unit/test_rollback.py`, 10 tests) | §7 | DONE |
| Counterfactual Engine | `agentguard/recovery/counterfactual.py`, `agentguard/models.py` (`CounterfactualResult`) | Yes | Yes (`tests/unit/test_counterfactual.py`, 4 tests) | §8 | DONE |
| Policy Version Integration | `agentguard/models.py` (`Policy.version`), `agentguard/decorator.py` | Yes | Yes (every audit/rollback test asserts `policy_version`) | §9 | DONE |
| Reliability Report | `agentguard/reliability/report.py` | Yes (derived from real evaluation data; unavailable dimensions labeled, never fabricated) | Yes (`tests/unit/test_reliability_report.py`, 8 tests) | §10 | DONE |
| Budget Failure Demo | `examples/budget_failure_recovery.py` | Yes | Yes (runs end-to-end; also `tests/e2e/test_scenario_budget_failure_recovery.py`) | §6 | DONE |
| OTel/OTLP Export | `agentguard/otel/exporter.py`, `agentguard/otel/instrumentation.py` (`start_decision_span`) | Yes (OTLP exporter construction + export attempt verified; live collector not available in this environment) | Yes (`tests/integration/test_otel_export.py`, 4 tests) | §13 | DONE, collector export unverified (stated limitation) |
| Dashboard | `dashboard/index.html` (Overview/Timeline/Reliability/Recovery/Audit tabs) | Yes | JS syntax-checked (`node --check`), every new endpoint hit live via `TestClient` and rendered data confirmed present in HTML. No browser available in this environment — stated limitation. | §12 | DONE, not interactively browser-tested |
| API | `server/api.py` (7 new endpoints) | Yes | Yes (`tests/integration/test_phase3_persistence.py`, 10 tests) | §11 | DONE |
| Storage/Migrations | `agentguard/storage/migrations/0003_phase3.sql`, `agentguard/storage/models.py`, `agentguard/storage/postgres.py`, `agentguard/storage/memory.py` | Yes | Not executed against a live Postgres (none available — same limitation as Phase 2 §1/§10); reviewed by inspection, purely additive (`IF NOT EXISTS`/`ADD COLUMN IF NOT EXISTS`) | §1 | DONE, unverified against live Postgres |

## 4. Objective Verification Matrix

| Objective | Required | Implemented | Tested | Execution Evidence | Status |
|---|---|---|---|---|---|
| Audit-trail timeline | Yes | Yes | Yes | §11 | PASS |
| Six-dimension Reliability Report | Yes | Yes | Yes | §10 | PASS |
| Checkpoint system | Yes | Yes | Yes | §6 | PASS |
| Rollback | Yes | Yes | Yes | §7 | PASS |
| Counterfactual analysis | Yes | Yes | Yes | §8 | PASS |
| Hash-chained/tamper-evident audit | Yes | Yes | Yes | §9 | PASS |
| Canonical budget-failure demo | Yes | Yes | Yes | §6 | PASS |
| OpenTelemetry export/interop | Yes | Yes (exporter + attempt; collector unavailable) | Yes | §13 | PASS (limitation documented) |
| Dashboard support | Yes | Yes | API-level + JS syntax; no live browser | §12 | PASS (limitation documented) |
| Full automated verification | Yes | Yes | Yes | §5 | PASS |
| Execution report | Yes | Yes | — | this document | PASS |
| Phase 1 + Phase 2 behavior preserved | Yes | Yes | Yes | §15 (86/86 original runnable tests still pass, unmodified) | PASS |
| Phase 4 items NOT implemented | Required absent | Absent | N/A | §18 | PASS |

## 5. Test Results

```
command:  .venv/Scripts/python.exe -m pytest tests/ -v
total:    143
passed:   139
failed:   0
skipped:  4   (tests/integration/test_postgres_repository.py — no AGENTGUARD_TEST_DATABASE_URL)
```

Breakdown:

```
tests/unit          100 passed   (63 Phase 1/2, unmodified + 37 new Phase 3)
tests/e2e             16 passed  (14 Phase 1/2, unmodified + 2 new Phase 3)
tests/integration     23 passed, 4 skipped  (9 Phase 1/2, unmodified + 14 new Phase 3)
```

New Phase 3 unit test files: `test_checkpoint.py` (6), `test_audit.py`
(9), `test_rollback.py` (10), `test_counterfactual.py` (4),
`test_reliability_report.py` (8) — 37 total.

New Phase 3 integration test files: `test_phase3_persistence.py` (10 —
persistence + all 7 new API endpoints), `test_otel_export.py` (4) — 14
total.

New Phase 3 e2e test file: `test_scenario_budget_failure_recovery.py`
(2 — the canonical end-to-end scenario with exact asserted values, and
the dedicated tampering test).

## 6. Canonical Failure Demonstration

Run via `python examples/budget_failure_recovery.py`
(`AGENTGUARD_OTEL_CONSOLE=0` to suppress span JSON on stdout). Full,
real, unedited output:

```
======================================================================
STEP 1 — EXECUTION
======================================================================
  run_id: a94c0d32-ebcd-4cc5-9f82-a17c9f3a5cbc
  policy_version: 3
  agent result: selected a 67000 laptop

======================================================================
STEP 2 — STATE SEQUENCE (checkpoints S1..S4)
======================================================================
  S1: {'max_budget': 60000.0}
  S2: {'max_budget': 60000}
  S3: {}
  S4: {'max_budget': 67000}

======================================================================
STEP 3 — EVALUATION / FAILURE DETECTED
======================================================================
  constraint_adherence.passed: False
  violation: 67000 > 60000.0
  run.status: RunStatus.STOP

======================================================================
STEP 4 — ROOT CAUSE IDENTIFICATION
======================================================================
  earliest_deviation: S3
  explanation: max_budget disappeared during state transition S3
  confidence: 0.97

======================================================================
STEP 5 — STOP (decision already recorded during execution)
======================================================================
  decision.outcome: RunStatus.STOP
  decision.reason: high-confidence unsafe evaluation: constraint_adherence

======================================================================
STEP 6 — CHECKPOINT ROLLBACK
======================================================================
  rolled back to: S2 (2029811b-b683-4a36-929b-8e1d76518d15)
  previous_state: {'max_budget': 67000}
  restored_state: {'max_budget': 60000}
  decision.outcome: rolled_back

======================================================================
STEP 7 — RE-EXECUTION / RECOVERY (from the restored, constraint-intact state)
======================================================================
  recovery run_id: 2b70060a-0c2c-4238-9a4b-d8668ed7b42a
  recovery run.parent_run_id: a94c0d32-ebcd-4cc5-9f82-a17c9f3a5cbc
  recovery run.status: RunStatus.CONTINUE
  recovery agent result: selected a 52000 laptop

======================================================================
STEP 9 — AUDIT INTEGRITY
======================================================================
  event_count: 13
  intact: True

======================================================================
STEP 10 — TAMPER DETECTION (demonstration on an isolated copy of the chain)
======================================================================
  intact (after tamper): False
  first_invalid_event: 16dad101-5fa2-4965-9c41-5a3920f82be7
  intact (after restore): True

======================================================================
SUMMARY
======================================================================
  DETECTED -> ROOT CAUSE (S3) -> STOP -> ROLLBACK (S2) -> COUNTERFACTUAL -> RECOVERY (CONTINUE) -> SUCCESS
  original_run_id:  a94c0d32-ebcd-4cc5-9f82-a17c9f3a5cbc
  recovery_run_id:  2b70060a-0c2c-4238-9a4b-d8668ed7b42a
  audit chain:      INTACT
```

(STEP 8 — counterfactual — is reproduced in full in §8. STEP 11 —
reliability report — in §10.)

Risk/confidence for this run (from `agentguard_risk_assessments`,
captured via `tests/e2e/test_scenario_budget_failure_recovery.py`):
`risk_score=0.75`, `confidence=1.0` (constraint violated, high-confidence
evaluation — same formula as Phase 2, unchanged).

## 7. Rollback Evidence

```
source state (S4, pre-rollback):  {"max_budget": 67000}
checkpoint ID:                    2029811b-b683-4a36-929b-8e1d76518d15 (label S2)
rollback target:                  S2
restored state:                   {"max_budget": 60000}
rollback event (AuditEvent):      event_type=ROLLBACK, previous_hash chained
                                   onto the run's existing 12-event chain,
                                   policy_version=3
decision (Decision Engine):       outcome=rolled_back,
                                   reason="rolled back to checkpoint 'S2' (2029811b-...)"
post-rollback result:             run.status = ROLLED_BACK;
                                   recovery run (parent_run_id = original run_id)
                                   subsequently reached status=CONTINUE
```

Live API evidence (`POST /api/runs/{run_id}/rollback`,
`{"to_checkpoint": "S2"}`) — real HTTP response via `starlette.testclient.TestClient`:

```json
{
  "run_id": "bbcb8b7d-...",
  "checkpoint": {"id": "88231bda-...", "label": "S2", "state": {"max_budget": 60000}, "valid": true, ...},
  "previous_state": {"max_budget": 67000},
  "restored_state": {"max_budget": 60000},
  "decision": {"outcome": "rolled_back", "reason": "rolled back to checkpoint 'S2' (88231bda-...)", ...}
}
```

Invalid-target and unknown-run cases verified live: unknown checkpoint
→ `400 {"detail": "checkpoint 'does-not-exist' not found for run '...'"}`;
unknown run → `404 {"detail": "run not found"}`.

## 8. Counterfactual Evidence

Full, real, unedited output from `examples/budget_failure_recovery.py`:

```
======================================================================
STEP 8 — COUNTERFACTUAL ANALYSIS (actual vs. counterfactual)
======================================================================
  ACTUAL PATH:
    S1: {'max_budget': 60000.0}
    S2: {'max_budget': 60000}
    S3: {}
    S4: {'max_budget': 67000}
    result: {'max_budget': 67000}  [ROLLED_BACK]
  COUNTERFACTUAL PATH:
    S1: {'max_budget': 60000}
    S2: {'max_budget': 52000}
    result: {'max_budget': 52000}  [ALLOWED]
  result: ACTUAL: ROLLED_BACK ({'max_budget': 67000})  vs.  COUNTERFACTUAL: ALLOWED ({'max_budget': 52000})
  confidence: 0.97
  NOTE: the counterfactual path above is reconstructed from a real, separately
  executed recovery run — NOT a literal replay of the original run's history.
```

Differences: `max_budget` retained through S2→S3 instead of
disappearing; final selection 52000 (within the 60000 policy limit)
instead of 67000; result ALLOWED instead of STOP. Confidence (0.97)
mirrors the Root-Cause Engine's own confidence for the S3 deviation it
negates — documented explicitly in
`agentguard/recovery/counterfactual.py`, which states the counterfactual
path is reconstructed from a real, separately-executed recovery run,
never a claim about what the original run's own code "would have done."

## 9. Audit-Chain Verification

```
event count (original run, pre-rollback):  12
  RUN_START, AGENT_STEP, CHECKPOINT×4, EVALUATION, POLICY_CHECK,
  RISK_ASSESSMENT, ROOT_CAUSE, DECISION, RUN_COMPLETION
event count (after rollback appends ROLLBACK):  13

first verification (before any tampering):     intact=true
tampered verification (payload of event #3
  mutated directly in storage):                intact=false,
                                                first_invalid_event=<that event's id>,
                                                expected_hash != actual_hash (both reported)
restored verification (payload restored):      intact=true
```

Formula used and independently re-verified by
`verify_audit_chain()`: `event_hash = SHA256(canonical_json(payload) +
previous_hash)`, canonical_json = `json.dumps(payload, sort_keys=True,
separators=(",", ":"), default=str)` — confirmed stable regardless of
Python dict insertion order (`tests/unit/test_audit.py::test_canonical_json_is_stable_regardless_of_key_order`).
Every `AuditEvent` also carries `policy_version` (see §3).

## 10. Reliability Report Evidence

Real output (`GET /api/runs/{run_id}/reliability-report`) for the
canonical demo's **original** (STOP) run — no fabricated percentages,
every value sourced and labeled:

```json
{
  "status": "stop",
  "dimensions": {
    "correctness": {"value": 0.0, "available": true, "source": "derived_from_constraint_adherence (llm_judge unavailable)"},
    "goal_completion": {"value": null, "available": false, "source": "no llm_judge evaluation available"},
    "constraint_adherence": {"value": 0.0, "available": true, "source": "constraint_adherence evaluator score"},
    "decision_consistency": {"value": 1.0, "available": true, "source": "1.0 minus documented penalties per RETRY(-0.10)/REPLAN(-0.15)/HUMAN(-0.10)/override(-0.25) in the run's own decision sequence"},
    "tool_usage": {"value": 1.0, "available": true, "source": "no actions recorded (neutral default — nothing failed)"},
    "behavioral_reliability": {"value": 0.5, "available": true, "source": "arithmetic mean of 4/5 available dimensions (unavailable dimensions excluded, not zeroed)"}
  },
  "risk": 0.75,
  "confidence": 1.0,
  "recovery": {"status": "UNRECOVERED"},
  "root_cause_summary": "max_budget disappeared during state transition S3"
}
```

And for the **recovery** (CONTINUE) run, from
`examples/budget_failure_recovery.py`'s STEP 11:

```json
{
  "status": "continue",
  "dimensions": {
    "correctness": {"value": 1.0, "available": true, "source": "derived_from_constraint_adherence (llm_judge unavailable)"},
    "goal_completion": {"value": null, "available": false, "source": "no llm_judge evaluation available"},
    "constraint_adherence": {"value": 1.0, "available": true, "source": "constraint_adherence evaluator score"},
    "decision_consistency": {"value": 1.0, "available": true, "source": "..."},
    "tool_usage": {"value": 1.0, "available": true, "source": "no actions recorded (neutral default — nothing failed)"},
    "behavioral_reliability": {"value": 1.0, "available": true, "source": "arithmetic mean of 4/5 available dimensions (unavailable dimensions excluded, not zeroed)"}
  },
  "risk": 0.0,
  "confidence": 1.0,
  "recovery": {"status": "RECOVERED"}
}
```

`goal_completion` is `available: false` in both cases because
`llm_judge=False` was passed for the canonical demo (deterministic,
reproducible evidence — see Phase 2's stated LLM-provider limitation,
unchanged in Phase 3); `tests/integration/test_llm_judge_async.py` and
`tests/unit/test_reliability_report.py::test_six_dimensions_populated_from_real_evaluation_data`
separately confirm `correctness`/`goal_completion` populate from a real
`llm_judge` evaluation's evidence when one is present.

## 11. API Verification

All hit live via `starlette.testclient.TestClient` against the real
`server/api.py` (in-memory repository — no live Postgres, §1):

| Method | Endpoint | Status | Result |
|---|---|---|---|
| GET | `/api/runs/{run_id}/timeline` | 200 | 12 ordered events, RUN_START first, RUN_COMPLETION last |
| GET | `/api/runs/{run_id}/reliability-report` | 200 | full six-dimension report, see §10 |
| GET | `/api/runs/{run_id}/checkpoints` | 200 | `[S1, S2, S3, S4]`, each with `state_hash` |
| POST | `/api/runs/{run_id}/rollback` `{"to_checkpoint":"S2"}` | 200 | restored_state + decision, see §7 |
| POST | `/api/runs/{run_id}/rollback` `{"to_checkpoint":"does-not-exist"}` | 400 | `{"detail": "checkpoint 'does-not-exist' not found for run '...'"}` |
| POST | `/api/runs/does-not-exist/rollback` | 404 | `{"detail": "run not found"}` |
| GET | `/api/runs/{run_id}/counterfactual` (before generation) | 200 | `{"counterfactual": null, "detail": "no counterfactual analysis recorded..."}` |
| GET | `/api/runs/{run_id}/counterfactual` (after generation) | 200 | full comparison, see §8 |
| GET | `/api/runs/{run_id}/audit` | 200 | `event_count` matches `len(events)` |
| POST | `/api/runs/{run_id}/audit/verify` | 200 | `{"intact": true, ...}`, then `{"intact": false, ...}` after live tampering |
| GET | `/api/runs/does-not-exist/timeline` | 404 | `{"detail": "run not found"}` |
| GET | `/` (dashboard) | 200 | 23,638 bytes; contains "Reliability", "Timeline", "Audit", "rollback", "counterfactual" |

Every Phase 1/2 endpoint (`/api/runs`, `/api/runs/{run_id}`, `/risk`,
`/root-cause`, `/decisions`, `/human-decisions`, `POST /human-decision`,
`WS /ws/runs/{run_id}/approval`) re-verified unchanged via the full test
suite (§15) — no signature or response-shape change.

## 12. Dashboard Verification

Implemented: Overview, Timeline, Reliability, Recovery, Audit tabs
(`dashboard/index.html`). Timeline renders the real `AuditEvent`
sequence with per-event-type human-readable summaries. Reliability
renders all six dimensions with bars, or "unavailable — `<source>`" for
dimensions with no data. Recovery lists checkpoints, offers a Rollback
action (dropdown + button, POSTs to `/rollback`) for
STOP/FAILED/HUMAN/ROLLED_BACK runs, and renders the counterfactual
actual-vs-counterfactual comparison when one exists. Audit shows event
count, "AUDIT INTACT"/"AUDIT INTEGRITY VIOLATION", and re-verifies live
on demand.

Verification actually performed:

1. JS syntax-checked: the `<script>` block extracted and run through
   `node --check` — passes with no syntax errors (Node v24.18.0).
2. Every new endpoint the dashboard calls hit live via `TestClient` and
   confirmed to return the shape the JS expects (§11).
3. `GET /` confirmed to serve the updated HTML (23,638 bytes) containing
   the new tab labels and action verbs.

**Interactive browser verification was NOT performed** — no browser
automation tool was available in this environment (the configured
`browser-use` MCP server failed to connect: `CONNECTION_CLOSED`), and no
GUI browser is available in this Windows CLI environment either. This
is a stated limitation, not a claim of full interactive verification,
matching Phase 2's own dashboard-verification limitation.

## 13. OpenTelemetry Verification

```
trace/span:        agentguard.invoke_agent (per attempt) — unchanged from Phase 1/2
                    agentguard.decision (new, Phase 3) — one per completed run
run ID:             span.attributes["agentguard.run.id"] present and non-empty
                    (verified via InMemorySpanExporter against the real
                    OpenTelemetry SDK — tests/integration/test_otel_export.py)
decision attributes: agentguard.decision.outcome, .reason, .risk_score,
                    .confidence, agentguard.run.status, agentguard.policy.version
                    — all present, verified equal to the real Decision
                    Engine's output for a STOP run
timestamps:         span.start_time / span.end_time both set, end > start
export target:      console (default, Phase 1/2 unchanged) or OTLP/HTTP
                    (Phase 3 addition) via AGENTGUARD_OTEL_EXPORTER=otlp
verification result: PASS for span shape/attributes/timestamps (real SDK,
                    in-memory exporter). PARTIAL for live-collector
                    export — no reachable collector in this environment
                    (no `docker`); documented below, not concealed.
```

Live OTLP attempt (real network call, no live collector — exact
captured output):

```
$ AGENTGUARD_OTEL_EXPORTER=otlp python -c "... span creation/export ..."
Transient error HTTPConnectionPool(host='localhost', port=4318): Max retries
exceeded with url: /v1/traces (Caused by NewConnectionError("...
[WinError 10061] No connection could be made because the target machine
actively refused it")) encountered while exporting spans batch, retrying
in 0.92s.
Failed to export spans batch due to timeout, max retries or shutdown.
```

This confirms the OTLP exporter is real (it attempted a genuine
OTLP/HTTP POST to `http://localhost:4318/v1/traces`, the standard OTLP
port) and fails only because nothing is listening — exactly the expected
behavior absent a collector, and evidence the exported format is
standard OTLP, not a proprietary AgentGuard format.
`tests/integration/test_otel_export.py::test_otlp_exporter_constructs_a_real_standard_exporter`
additionally confirms the constructed object is a genuine
`opentelemetry.exporter.otlp.proto.http.trace_exporter.OTLPSpanExporter`.

## 14. Latency Verification

```
Phase 2 baseline (documented in EXECUTION_REPORT_PHASE_2.md §13),
  sync path, LLM judge DISABLED (n=200):    mean=0.527ms p50=0.479ms p95=0.679ms max=4.446ms

Phase 3 sync path (n=200), LLM judge disabled,
  now including per-run checkpoint creation
  (SHA-256 over each state snapshot) and
  in-memory audit-chain construction + batch persist:
                                             mean=1.134ms p50=1.051ms p95=1.332ms max=5.389ms
```

Measured overhead: **≈0.6ms mean, ≈0.85ms p95** added by Phase 3's
synchronous-path work (checkpoint hashing + audit-event batch
persistence), roughly 2× Phase 2's baseline in absolute terms but still
sub-2ms in absolute cost. This is real, measured overhead — **not
zero**, and not hidden. Per the spec's explicit instruction, the
expensive operations (counterfactual generation, audit-chain
*verification*, OTel/OTLP export, full reliability-report calculation)
are never on this path — they are separate, explicitly-invoked
operations (`agentguard.recovery.rollback`/`generate_counterfactual`,
`verify_audit_chain`, `build_reliability_report`, all called from
`server/api.py` handlers or example/test code, never from
`decorator.py`'s `_execute`). `Rollback` itself IS synchronous when
explicitly invoked (permitted by the spec as an explicit recovery
operation, not part of any agent's own response path) — its measured
cost is sub-millisecond in this environment (in-memory repository; a
real Postgres round-trip would add network/IO latency on top, not
measured here since no live Postgres is available, §1).

## 15. Regression Verification

```
Before Phase 3:  .venv/Scripts/python.exe -m pytest tests/ -v
                 86 passed, 0 failed, 4 skipped

After Phase 3:   .venv/Scripts/python.exe -m pytest tests/ -v
                 139 passed, 0 failed, 4 skipped
                 (all 86 original Phase 1/2 test functions are present,
                 unmodified, among these 139, and all pass verbatim —
                 test_decision_engine.py, test_policy.py, test_risk.py,
                 test_root_cause.py, test_llm_judge.py, test_evaluators.py,
                 test_models.py [unit]; test_monitor_flow.py,
                 test_scenario_retry.py, test_scenario_root_cause.py,
                 test_scenario_human.py [e2e]; test_reliability_pipeline.py,
                 test_llm_judge_async.py, test_websocket_approval.py
                 [integration] — the increase is 53 additional Phase 3
                 test functions, not replaced Phase 1/2 tests)
```

No Phase 1 or Phase 2 test was modified to make it pass. `tests/fakes.py`
now re-exports `agentguard.storage.memory.InMemoryRunRepository` (moved
there so `examples/budget_failure_recovery.py` can reuse the identical
class) rather than defining the class itself — the class body is
unchanged and every existing test import (`from ..fakes import
InMemoryRunRepository`) still works without modification.

## 16. Problems Encountered

| Problem | Root Cause | Fix | Verification |
|---|---|---|---|
| A blanket find/replace of `await repository.save_decision(run, decision)` → `await _save_decision(decision)` (to route every decision through the new audit-log recorder) also rewrote the helper's own body and a module-level function (`_apply_async_judge_result`) that has no access to the `_execute`-local closure, producing infinite recursion and an undefined-name bug respectively | The replacement was a blind string substitution across the whole file, not scoped to call sites | Reverted those two specific occurrences back to a direct `repository.save_decision(run, decision)` call | Caught immediately by re-reading the diff before running anything; full test suite green afterward (§15) |
| `ReliabilityReport._recovery_status()`'s branch order checked "status == continue and not has_rollback → NOT_NEEDED" before checking "this run IS a recovery run (parent_run_id set) → RECOVERED", so a successfully recovered run was mislabeled NOT_NEEDED | Wrong branch ordering — a recovery run's own audit trail has no ROLLBACK event (the rollback happened on its *parent*), so `has_rollback` is correctly False for it, but the NOT_NEEDED check didn't account for that | Reordered: check `parent_run_id` + `status == continue` → RECOVERED first | Caught via the ad hoc end-to-end smoke script before writing formal tests; `tests/unit/test_reliability_report.py::test_recovered_run_reports_recovered_status` now asserts it |
| OpenTelemetry's SDK silently refuses to override the global `TracerProvider` more than once per process (`trace.set_tracer_provider()` no-ops after the first real call, logging "Overriding of current TracerProvider is not allowed") | By design — production code shouldn't have its tracing hijacked mid-process. My first `reset_tracing_for_tests()`-based test fixture assumed each test could get a fresh provider/exporter, which OTel's API does not actually allow | Rewrote the test fixture to add an *additional* `SimpleSpanProcessor` (with a fresh `InMemorySpanExporter`) onto whatever provider is already configured, instead of trying to replace the provider — exactly how a second real exporter would be added in production | `tests/integration/test_otel_export.py` — all 4 tests pass; failure mode (`assert 0 == 1` / `assert 0 >= 1`, spans landing in the wrong/old exporter) reproduced and confirmed fixed |

## 17. Deviations

| Specification | Actual implementation | Reason | Impact |
|---|---|---|---|
| Canonical flow narrative order: "STOP → ROLLBACK → COUNTERFACTUAL → RECOVERY → SUCCESS" | `examples/budget_failure_recovery.py` performs the real recovery re-execution *before* generating the counterfactual comparison, then prints the sections in the spec's stated order | The counterfactual "safe path" (52000, ALLOWED) is grounded in the recovery run's real, measured result — never an invented number (per §5's own instruction: "Do NOT claim the counterfactual is a literal historical event"). Producing a trustworthy comparison requires the recovery run to have actually happened; the printed narrative still matches the spec's stated order exactly | None on the printed/API-visible flow or its correctness; documented here for transparency rather than silently reordered |
| "Mark the rollback decision in the Decision Engine" | Added `RunStatus.ROLLED_BACK` (new, additive enum value) and `DecisionEngine.rollback()` (new, additive method) rather than reusing an existing outcome (e.g. STOP) | Reusing STOP would make a rolled-back run indistinguishable from a normal STOP in every downstream table/dashboard/report; a new, explicit, guarded state machine transition keeps "this run was rolled back" a first-class, auditable fact, consistent with Phase 2's own precedent of adding new `RunStatus` values (RETRY/REPLAN/HUMAN) for new real transitions | Additive only — no existing `RunStatus` comparison anywhere in Phase 1/2 code enumerates the enum exhaustively (all existing checks are equality/membership tests against specific values), confirmed by the full regression suite passing unmodified (§15) |
| "Implement `counterfactual(run_id)`" as a single lookup function | Implemented as `generate_counterfactual(...)` (an explicit, pure function taking the original run, its checkpoints, the recovery run, its checkpoints, the source checkpoint id, and the root cause) plus `GET /api/runs/{run_id}/counterfactual` (the actual `counterfactual(run_id)`-shaped read) | Generation needs the recovery run's real data (see the first deviation above) and is therefore triggered once, explicitly, by the orchestrating caller (the demo script, or a future dashboard "generate counterfactual" action) rather than computed freshly on every read; the read-only `GET` endpoint is the `counterfactual(run_id)` surface the spec describes | None on the required API shape; the engine function itself is a private implementation detail behind it |

Never hidden: all three deviations are also called out in code
docstrings (`agentguard/recovery/counterfactual.py`,
`agentguard/decision/engine.py`, `examples/budget_failure_recovery.py`).

## 18. Intentionally Deferred

Explicitly confirmed **not implemented** in Phase 3, exactly per the
prompt's Phase 4 exclusion list:

- tool reliability profiles
- alternative-tool recovery
- behavior fingerprinting
- agent-to-agent (A2A) constraint propagation (full)
- advanced/generalized security system beyond Phase 2's existing
  forbidden-action/require-approval guardrails
- a generalized chaos-testing harness
- a CI/CD reliability gate
- DSPy auto-improvement
- an `agentguard replay` CLI
- an `agentguard compare` CLI
- an `agentguard ci-gate` CLI
- an `agentguard verify-audit` CLI
- multi-tenancy
- a production data-governance suite
- a ClickHouse migration

`agentguard/recovery/`, `agentguard/checkpoint/`, and `agentguard/audit/`
are written as clean, typed, documented modules with obvious extension
points (e.g. `RootCauseEngine`/`RiskEngine` could feed a future tool
reliability profile; `AuditEvent` could back a future
`verify-audit`/`replay` CLI) — but none of those Phase 4 capabilities is
implemented, wired up, or claimed as implemented here.

## 19. Final Verdict

**PHASE 3 — PASS**

Basis: all mandatory Phase 3 components are implemented (§3–4) and
backed by real, executed evidence, not narrative description; the
canonical budget-failure recovery demo runs end-to-end and produces the
exact required sequence (DETECTED → ROOT CAUSE(S3) → STOP →
ROLLBACK(S2) → COUNTERFACTUAL → RECOVERY(CONTINUE) → SUCCESS, §6–8);
rollback restores real state and records both an audit event and a
Decision Engine transition (§7); the counterfactual analysis is
reconstructed from a real recovery run and explicitly labeled as such,
never presented as a historical event (§8); the SHA-256 hash chain
verifies intact, correctly detects tampering, and correctly identifies
the first broken event, then re-verifies intact after restoration (§9);
the six-dimension Reliability Report is built from real evaluation data
with unavailable dimensions honestly labeled rather than fabricated
(§10); every new API endpoint was hit live and returned the expected
shape (§11); the dashboard was updated and verified at the API/JS-syntax
level with the browser-automation limitation explicitly stated, not
concealed (§12); OpenTelemetry export was verified against the real SDK
(span shape, run ID, decision attributes, timestamps) with the
live-collector limitation explicitly stated (§13); latency overhead was
measured, not claimed zero (§14); the full Phase 1+2 regression suite
passes unmodified alongside 53 new Phase 3 tests, 139/139 runnable tests
green (§15); all deviations from the literal spec text are disclosed
with reasoning (§17); and Phase 4 remains explicitly, verifiably
unimplemented (§18).
