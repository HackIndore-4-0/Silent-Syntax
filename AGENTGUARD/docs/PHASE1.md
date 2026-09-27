# Phase 1 — Core Loop

> **Historical snapshot.** This document describes the codebase as of
> the end of Phase 1. Phase 2 has since implemented several of the
> items this document lists as absent (RETRY, REPLAN, HUMAN, risk
> scoring, confidence, root-cause analysis) — see
> `docs/EXECUTION_REPORT_PHASE_2.md` and the root `README.md` for the
> current state. This file is kept unmodified below as the Phase 1
> record.

What's implemented, matched line by line against `Phase 1 Prompt.txt`.

## What's in

| Requirement | Where |
|---|---|
| `@monitor` decorator, sync + async | `agentguard/decorator.py` |
| OpenTelemetry span capture (one span per run) | `agentguard/otel/` |
| Run tracking | `agentguard/models.py` (`Run`), `agentguard/context.py` |
| PostgreSQL persistence behind a repository interface | `agentguard/storage/` |
| Constraint Adherence evaluator | `agentguard/evaluators/rule_based.py` |
| Decision Engine: RUNNING → EVALUATING → CONTINUE \| STOP | `agentguard/decision/engine.py` |
| Minimal run-list + run-detail dashboard | `server/api.py`, `dashboard/index.html` |
| Automated tests (unit, e2e, integration) | `tests/` |
| Extensible project structure | matches `Phase 1 Prompt.txt`'s recommended tree |

## What's deliberately not in

Per the prompt's explicit rules, none of the following exist in this
codebase, even as stubs that silently do nothing:

- `RETRY`, `REPLAN`, `HUMAN` decision outcomes
- Any evaluator besides Constraint Adherence (no LLM-as-judge, no
  embedding/NLI, no progress evaluation)
- Risk scoring, confidence, goal drift, behavior fingerprinting
- Checkpoints, rollback, counterfactual analysis
- Tool/LLM/decision/policy child spans (only the one agent-level span)
- An event queue / async worker pipeline (writes are direct, in-process)
- Policy versioning, hash-chained audit trail
- Agent-to-agent constraint propagation
- Trace visualization, reliability report, replay UI, risk visualization,
  human-approval UI, auto-improvement UI

These are all real parts of the Final Solution architecture — they are
just Phase 2+ and are intentionally absent, not simplified-and-hidden.

## The success-condition flow, traced through the code

```
Developer calls my_agent("find a laptop under budget")
  -> decorator.monitor()'s wrapper (decorator.py:_execute)
  -> Run(...) created, status=RUNNING                      (models.py)
  -> repository.create_run(run)                             (storage/)
  -> otel.start_agent_span(run) opens "agentguard.invoke_agent"
  -> ctx = RunContext(run, initial_state); contextvar set    (context.py)
  -> the wrapped agent function actually runs
       (it may call agentguard.update_state(...) to report what happened)
  -> span closed, run.final_state captured from context state
  -> repository.save_span(...)
  -> DecisionEngine().begin_evaluation()                     (RUNNING->EVALUATING)
  -> ConstraintAdherenceEvaluator().evaluate(run)             -> EvalResult
  -> repository.save_evaluation(...)
  -> engine.decide([...])                                     -> CONTINUE or STOP
  -> run.status = decision.outcome; repository.update_run(run)
  -> repository.save_decision(run, decision)
  -> dashboard's /api/runs and /api/runs/{id} read all of the above back
```

Every arrow above is a real function call — see `examples/basic_agent.py`
for a script that exercises both the CONTINUE and STOP paths against a
real (or fake, in tests) repository.

## Known Phase 1 limitations (by design, not oversight)

- A **sync** agent function runs via `asyncio.run()` internally, so it
  cannot itself be called from inside an already-running event loop.
  Define it `async def` if that matters.
- Writes to Postgres are direct and synchronous with respect to the
  call (no queue). This is intentional per Rule 4 — the repository
  interface is the seam a future event pipeline attaches to, and Phase 1
  is not supposed to build that pipeline yet.
- `initial_state` is seeded from `policy.max_cost` into a `max_budget`
  field by convention, matching the canonical demo scenario. An agent
  that calls `agentguard.update_state(...)` overwrites it; an agent that
  checks a differently-named constraint should pass a custom
  `ConstraintAdherenceEvaluator(field=..., policy_attr=...)` via
  `@monitor(evaluators=[...])`.

## Running it

See the repository `README.md` for setup. Short version:

```bash
pip install -e ".[dev]"
pytest tests/unit tests/e2e -v          # no database required

pip install -e ".[server]"
python -m agentguard.cli init-db         # requires a running Postgres
python examples/basic_agent.py
uvicorn server.api:app --reload          # open http://localhost:8000
```
