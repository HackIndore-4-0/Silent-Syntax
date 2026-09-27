# AgentGuard Dashboard V2 + Multi-User Authentication — Execution Report

This is a UX/platform redesign of the existing Phase 1–4 implementation,
not a new feature phase. All existing AgentGuard backend functionality
(Decision Engine, Risk/Confidence, Root Cause, Recovery/Rollback,
Counterfactual analysis, hash-chained Audit, Replay, Tool Profiles,
Behavior Fingerprinting, Policies) is reused as-is; this work adds only
the authentication/multi-user/project boundary the spec required, plus a
new dashboard frontend and the API surface it consumes.

## 1. Baseline

Before any Dashboard V2 change: `.venv/Scripts/python.exe -m pytest
tests/ -q` → **219 passed, 0 failed, 4 skipped** (the Phase 4 final
state — see `docs/EXECUTION_REPORT_PHASE_4.md`).

## 2. Architecture Changes

```
Agent code
  │  @guard.monitor (agentguard.client.AgentGuard) or plain @monitor
  ▼
agentguard.decorator._execute()          [UNCHANGED pipeline]
  │  now also resolves + stamps workspace_id/project_id/agent_id
  │  when an AgentGuard client (not plain @monitor) is in use
  ▼
RunRepository (Postgres or in-memory)     [same interface, additively
                                            extended — see §3]
  ▲
  │  workspace_id filters (query-level tenant isolation)
  │
server/auth.py            server/dashboard_v2.py         server/api.py
(signup/login/logout/      (authenticated, workspace-      (UNCHANGED —
 API keys/projects/team)    scoped /api/v2/... surface      still the
                            the new dashboard consumes)      Phase 1-4
                                                              surface)
  ▲
  │  session cookie or Authorization: Bearer <api_key>
  │
dashboard/index.html + app.js + app.css   [NEW: sidebar IA, hash router,
                                            auth screens, 19 workspace-
                                            scoped views]
```

**Nothing in the existing architecture was replaced.** PostgreSQL
remains the storage engine, OpenTelemetry instrumentation is untouched,
the Decision Engine is still the sole authority for CONTINUE/RETRY/
REPLAN/HUMAN/STOP/ROLLED_BACK, and checkpoint/rollback/counterfactual/
risk/confidence/audit/HITL are all reused verbatim. The **only**
structural addition to the execution path is: `_execute()` now accepts
optional `workspace_id`/`project_id` parameters (default `None` —
unchanged, single-tenant behavior for plain `@monitor`), and, when set,
auto-registers the agent name as an `AgentRegistration` within that
workspace. See `agentguard/decorator.py`'s updated docstring.

The pre-existing single-file `dashboard/index.html` (Phase 1-4's static
HTML/JS dashboard) was fully replaced — this is the spec's explicit
"must NOT resemble the current screenshot" requirement, not an
accidental regression. `dashboard/app.css` (design tokens, layout,
components) and `dashboard/app.js` (router + 23 view renderers) are new
files, served via the existing `StaticFiles` mount at `/dashboard/`; no
build step was introduced (same "plain files, no bundler" philosophy as
Phase 1-4).

## 3. Database / Schema Changes

Migration `agentguard/storage/migrations/0005_dashboard_v2.sql` — purely
additive (`ADD COLUMN IF NOT EXISTS` / `CREATE TABLE IF NOT EXISTS`
throughout; every existing Phase 1-4 row gets `workspace_id = NULL`,
treated as "pre-Dashboard-V2 / unowned", never deleted):

**New tables:**

| Table | Purpose |
|---|---|
| `agentguard_users` | id, email (unique), name, `password_hash` (PBKDF2, never plaintext), is_active |
| `agentguard_sessions` | id, user_id, `token_hash` (SHA-256 of the session cookie's raw value), expires_at, revoked_at |
| `agentguard_password_reset_tokens` | id, user_id, `token_hash`, expires_at, used_at |
| `agentguard_workspaces` | id, name |
| `agentguard_workspace_memberships` | (workspace_id, user_id) PK, role (`owner`/`member`) |
| `agentguard_projects` | id, workspace_id, name |
| `agentguard_agents` | id, workspace_id, project_id, name — the per-workspace agent registry |
| `agentguard_api_keys` | id, user_id, workspace_id, project_id, name, `key_prefix` (safe to display), `key_hash` (SHA-256 of the raw key — **never the raw key itself**), status, timestamps |

**Extended tables:**

| Table | New columns |
|---|---|
| `agentguard_runs` | `workspace_id`, `project_id`, `agent_id`, `model_name`, `tokens_input`, `tokens_output`, `estimated_cost_usd` |
| `agentguard_policy_definitions` | `workspace_id` (composite PK `(workspace_id, name)` — policy names are now unique per-workspace, not globally) |
| `agentguard_policy_versions` | `workspace_id` |
| `agentguard_tool_alternatives` | `workspace_id` (composite PK `(workspace_id, primary_tool)`) |
| `agentguard_improvement_candidates` | `workspace_id` |

## 4. Authentication Flow

```
POST /api/auth/signup {email, password, name}
  -> hash_password() [PBKDF2-HMAC-SHA256, 260,000 iterations, random salt]
  -> creates User, a personal Workspace ("<name>'s Workspace"),
     a WorkspaceMembership (role=owner), and a default Project ("Production")
  -> creates a Session, sets an httponly `agentguard_session` cookie
     (raw token never stored — only its SHA-256 hash)

POST /api/auth/login {email, password}
  -> verify_password() [constant-time comparison via hmac.compare_digest]
  -> same generic "invalid email or password" for both "wrong password"
     and "unknown email" (never reveals which)
  -> new Session + cookie

GET  /api/auth/me            -> current user + workspace memberships
POST /api/auth/logout        -> revokes the session server-side, clears the cookie
POST /api/auth/forgot-password {email}
  -> issues a PasswordResetToken (1-hour expiry); see §11 for the
     stated email-delivery limitation
POST /api/auth/reset-password {token, new_password}
  -> single-use (marked used_at on success), rejects expired/reused tokens
```

Session validation (`agentguard/auth/service.py::validate_session`)
checks: token hash matches a stored session, `revoked_at is None`,
`expires_at > now`, and the owning user `is_active`. All four are
covered by dedicated unit tests (§8).

## 5. API Key Flow

```
POST /api/settings/api-keys {name, project_id?}
  -> generate_api_key() -> raw key "agp_live_<32 random url-safe chars>"
  -> only key_prefix (first 16 chars) + SHA-256(raw key) are persisted
  -> raw key returned in THIS ONE response body; never again, never logged

Authorization: Bearer <api_key>  (server/auth.py::get_current_user)
  -> hash the presented key, look up by hash, check status == "active"
     and revoked_at is None, load the owning (active) user
  -> updates last_used_at

POST /api/settings/api-keys/{id}/revoke   -> status="revoked", stops authenticating immediately
POST /api/settings/api-keys/{id}/rotate   -> revokes the old key, issues a brand-new one (same name/workspace/project)
PATCH /api/settings/api-keys/{id}         -> rename
```

Every key operation verifies `key.user_id == the calling user's id`
first — a 404 (not 403) for anyone else's key ID (§9).

## 6. Dashboard Sections

All implemented as client-side routed views (`dashboard/app.js`),
matching the spec's information architecture exactly:

```
Overview
Observability:  Runs/Traces · Latency · Tokens & Cost · Errors · Tools
Reliability:    Evaluations · Risk & Confidence · Agent Behavior
Recovery:       Interventions · Checkpoints/Rollback · Replay/Compare
Governance:     Policy Management · Audit
Improvement:    Failure Patterns · Recommendations
Settings:       API Keys · Profile · Team/Users
```

Run Detail is a dedicated full-page view with the required 9 tabs
(Overview, Trace, Evaluations, Latency, Tokens, Reliability, Recovery,
Audit, Metadata); the Trace tab renders the run's real hash-chained
audit-event sequence as an expandable hierarchical list (click a node to
see its full payload) — a real reconstruction of what happened, not a
static mock.

**Data integrity**: every KPI/chart/table renders directly from
`/api/v2/...` JSON — nothing is hard-coded. Where data genuinely isn't
available (no token/cost metadata reported for a run, too few points for
a trend line, an empty workspace), the UI shows "N/A" or "No data
available for this time range." rather than a fabricated number — see
`server/dashboard_v2.py`'s `/tokens` and `/overview` handlers, which
explicitly track `any_tokens`/`any_cost` booleans rather than defaulting
to 0.

## 7. API Endpoints (new)

**Auth / settings** (`server/auth.py`):

```
POST /api/auth/signup                POST /api/auth/login
POST /api/auth/logout                GET  /api/auth/me
POST /api/auth/forgot-password       POST /api/auth/reset-password
GET  /api/settings/api-keys          POST /api/settings/api-keys
PATCH /api/settings/api-keys/{id}    POST /api/settings/api-keys/{id}/revoke
POST /api/settings/api-keys/{id}/rotate
GET  /api/projects                   POST /api/projects
GET  /api/workspace/members          POST /api/workspace/members
```

**Dashboard data** (`server/dashboard_v2.py`, all under `/api/v2`, all
`Depends(get_current_workspace_id)`):

```
GET  /overview                                    GET  /runs
GET  /runs/{id}                                   GET  /runs/{id}/reliability-report
GET  /runs/{id}/timeline                          GET  /runs/{id}/checkpoints
GET  /runs/{id}/counterfactual                    GET  /runs/{id}/audit
POST /runs/{id}/audit/verify                      GET  /runs/{id}/replay
POST /runs/{id}/rollback                          GET  /runs/compare
GET  /latency                                     GET  /tokens
GET  /evaluations                                 GET  /risk
GET  /recovery                                    GET  /tools
GET  /agents                                      GET  /agents/{name}/fingerprint
GET  /policies                                    GET  /policies/{name}
GET  /policies/{name}/versions                    POST /policies
POST /policies/{name}/versions                    GET  /improvements
GET  /failure-patterns
```

**Every Phase 1-4 endpoint under plain `/api/...` is unchanged** — same
routes, same behavior, same tests passing (§10).

## 8. Tests Executed

```
command:  .venv/Scripts/python.exe -m pytest tests/ -q
total:    253
passed:   249
failed:   0
skipped:  4   (tests/integration/test_postgres_repository.py — no AGENTGUARD_TEST_DATABASE_URL)
```

```
tests/unit          180 passed   (159 Phase 1-4, unmodified + 21 new)
tests/integration     37 passed, 4 skipped  (32 Phase 1-4, unmodified + 5 new)
tests/e2e             32 passed  (28 Phase 1-4, unmodified + 4 new, including
                                  the 4 real-browser Playwright tests)
```

New test files:

- `tests/unit/test_auth.py` (13) — signup, login, logout, invalid
  credentials, session expiration, deactivated-user rejection, password
  hashing/salting.
- `tests/unit/test_api_keys.py` (8) — create, authenticate, revoke,
  rotate, invalid-key rejection, ownership enforcement.
- `tests/integration/test_authorization.py` (5) — User A cannot access
  User B's runs/API keys/policies; unauthenticated and invalid-bearer
  requests rejected across the whole `/api/v2` surface.
- `tests/e2e/test_dashboard_v2_browser.py` (4) — **real Chromium
  browser** tests via Playwright (see §9).

## 9. Browser Test Evidence

**Playwright + a real, locally-installed Chromium (v1243) were used —
this is genuine browser automation, not a claim.** Verified directly in
this environment:

```
$ .venv/Scripts/python.exe -m playwright install chromium
... Chromium 1243, Chrome Headless Shell, ffmpeg, winldd downloaded ...

$ .venv/Scripts/python.exe -c "from playwright.sync_api import sync_playwright; ..."
PLAYWRIGHT WORKS
```

`tests/e2e/test_dashboard_v2_browser.py` is **hermetic**: it starts its
own `uvicorn` server (its own port, its own fresh `InMemoryRunRepository`)
in a background thread and drives it with `playwright.sync_api`. Run
twice in a row to confirm it is not flaky:

```
$ pytest tests/e2e/test_dashboard_v2_browser.py -v      (run 1)
test_full_flow_signup_to_audit PASSED
test_overview_page_loads PASSED
test_run_list_and_detail_load PASSED
test_evaluation_latency_token_audit_recovery_pages_load PASSED
4 passed in 12.56s

$ pytest tests/e2e/test_dashboard_v2_browser.py -v      (run 2)
4 passed in 12.97s
```

`test_full_flow_signup_to_audit` is the spec's exact required flow,
executed for real against a real browser:

```
signup (via the real form) -> logout -> login (via the real form)
-> create project (via the real API, as a logged-in user)
-> create API key (via the real UI: fill name, click "Create API Key",
   read the one-time raw key back out of the DOM)
-> construct AgentGuard(api_key=<that exact raw key>) and run a real
   @guard.monitor-wrapped agent (the canonical budget-drift scenario)
-> confirm the new run_id appears in the dashboard's Runs table
-> open its trace, click through Latency/Tokens/Evaluations/Recovery/Audit tabs
-> Audit tab shows "CHAIN VALID" (real SHA-256 verification, reused from Phase 3)
```

A separate, ad hoc manual walkthrough (not part of the automated suite,
run against `scripts/run_dashboard.py`'s seeded dev server) additionally
exercised every sidebar section — Overview, Runs, all 9 run-detail tabs,
Latency, Tokens & Cost, Evaluations, Recovery Center, Audit (Verify
Integrity button, live), Settings → API Keys, Settings → Team, Policy
Management, Recommendations, and Logout — with zero unexpected console
errors (one benign 401 logged at the exact instant of logout, from a
request that was already in flight when the session cookie was
cleared).

**No claim of browser testing is made anywhere in this report without
the above having actually been executed.**

## 10. Security / Authorization Tests

All in `tests/integration/test_authorization.py`, against the real
FastAPI app (not mocked), with two independently signed-up users:

```
test_user_a_cannot_access_user_b_runs             PASS (404, not 200/403, on every sub-resource)
test_user_a_cannot_access_user_b_api_keys          PASS (disjoint key-ID sets; a cross-user revoke attempt is a no-op)
test_user_a_cannot_access_user_b_policies          PASS (same policy NAME in two workspaces never collides)
test_unauthenticated_requests_are_rejected_...      PASS (401 across every /api/v2 route + /api/settings/api-keys)
test_invalid_bearer_token_is_rejected               PASS
```

This isolation is enforced by **actual query-level filtering on real
stored `workspace_id` values** (`repository.list_runs(workspace_id=...)`,
etc. — see `agentguard/storage/memory.py`/`postgres.py`), verified with
a fresh `TestClient` per user (a shared client's persistent cookie jar
would otherwise mask the check — this was caught and fixed during
development, see §12) — never a frontend-only hide.

## 11. Known Limitations

Stated explicitly, exactly as Phase 1-4 already established the
practice of doing:

- **PostgreSQL**: not available in this environment (no `docker`, no
  `AGENTGUARD_DATABASE_URL`). Every schema/repository change in this
  work is implemented in both `PostgresRunRepository` and
  `InMemoryRunRepository`; the Postgres SQL is reviewed by inspection
  only, never executed against a live database — identical, unchanged
  limitation from every prior phase.
- **Email delivery**: this environment has no outbound email capability.
  `POST /api/auth/forgot-password` returns the raw reset token directly
  in the response (`dev_reset_token`) instead of emailing it — the
  dashboard's Forgot Password screen surfaces this as an explicit
  "dev-mode" link rather than silently pretending an email was sent. The
  Team/Users "invite" flow only works for an email that already has an
  AgentGuard account for the same reason (no invite email can be sent to
  a not-yet-registered address); this is stated in the UI itself, not
  hidden.
- **`AgentGuard()`'s in-process authentication model**: this
  architecture has always had the SDK write directly to the same
  `RunRepository` the dashboard reads from (no separate network
  ingestion server — see Phase 1's original design). `AgentGuard(api_key=...)`
  therefore resolves the key against that same repository in-process
  rather than opening an HTTP connection to a separate AgentGuard
  server; what it authenticates and enforces (workspace/project
  attribution, revocation, tenant isolation on every subsequent query)
  is real, not simulated — documented explicitly in `agentguard/client.py`'s
  module docstring rather than silently implied to be a network call.
- **`AgentGuard(...)` cannot be constructed from inside an already-running
  event loop** (e.g. inside `async def main()`) — it is synchronous by
  design (matching the spec's own usage example) and deliberately does
  not hop threads internally to work around this, because a
  `PostgresRunRepository` connection pool is bound to the event loop it
  was created on and silently sharing it across threads could corrupt it
  in production. Construct it once, synchronously, before entering async
  code. (This also affects test authoring: Playwright's synchronous API
  leaves an event loop "running" on its calling thread via greenlets —
  `tests/e2e/test_dashboard_v2_browser.py` runs its `AgentGuard`/agent
  calls on a genuinely separate thread for this reason, mirroring how a
  real user's agent script and their browser test would be two separate
  processes anyway.)
- **Token/cost data**: no run in this environment calls a real LLM (see
  Phase 2's stated `DeterministicTestProvider` limitation, unchanged), so
  token/cost figures only appear when an agent explicitly calls the new
  `agentguard.record_tokens(...)` helper (as the seed script and the
  browser test do, with realistic illustrative numbers) — any run that
  never calls it correctly shows "N/A", never a guess.
- **OpenTelemetry collector / real LLM / DSPy provider**: unchanged from
  Phase 3/4 — still unavailable in this environment, still documented
  there, not re-litigated here since Dashboard V2 introduced no new
  surface in those areas.
- **Multi-workspace-per-user UI**: a user's `/api/auth/me` response lists
  every workspace they belong to, but the current dashboard always
  operates against the FIRST membership found (`get_current_workspace_id`)
  — there is no workspace switcher in the UI. Each signup creates exactly
  one personal workspace, so this only matters once a user is invited
  into a second one (§ Team/Users); switching between workspaces is not
  implemented.

## 12. Problems Encountered

| Problem | Root Cause | Fix | Verification |
|---|---|---|---|
| `AgentGuard(...)` raised `RuntimeError: cannot be constructed from inside a running event loop` when constructed inside an `async def seed()` helper | The demo/seed script called it from async code, exactly the documented constraint | Restructured the script: sign up + create the API key via one `asyncio.run(...)`, construct `AgentGuard(...)` synchronously in between, then run the rest via a second `asyncio.run(...)` | `scripts/run_dashboard.py` runs cleanly end-to-end |
| The same error appeared in the Playwright browser test, even though the test function is a plain (non-async) `def` | Playwright's `sync_api` runs its own event loop via greenlets on the calling thread — confirmed directly (`asyncio.get_running_loop()` succeeds inside a `sync_playwright()` context) | Run the `AgentGuard` construction and the monitored agent call on a genuinely separate `threading.Thread`, not the Playwright-driving thread | `test_full_flow_signup_to_audit` / `test_run_list_and_detail_load` pass, twice in a row |
| `createKey()` showed the raw API key, then immediately called `viewApiKeys()`, which re-rendered `#content` and wiped the message before a user (or the browser test) could ever see it | Wrong call order: the DOM update happened before the full-page re-render that would destroy it | Reordered to `await viewApiKeys()` first, then inject the one-time key message into the freshly-rendered DOM (matching the pattern `rotateKey()` already used correctly) | Playwright's `test_full_flow_signup_to_audit` reads the raw key back out of `#new-key-result code` and it is present |
| `test_dashboard_v2_browser.py`'s own tests intermittently couldn't see a run they had just created | `tests/e2e/conftest.py` has an `autouse=True` fixture (also named `fake_repository`) that reconfigures `agentguard._runtime`'s **global** repository to a fresh throwaway one before every test in `tests/e2e/` — silently overriding this module's own module-scoped `live_server` fixture and its long-lived repository | Defined a fixture with the identical name `fake_repository` in the new test module, which shadows (fully replaces, not runs alongside) the parent conftest's version for tests here, re-pointing the global repository at `live_server`'s repo instead | Both affected tests pass consistently across repeated runs |
| An early ad hoc isolation check (`bob_client.get('/api/v2/runs')` with no explicit cookies) returned 200 instead of 401, looking like a real cross-tenant leak | `starlette.testclient.TestClient` persists cookies like a `requests.Session`; the "unauthenticated" request actually still carried a previous user's session cookie left in the shared client instance | Used a fresh `TestClient` per check (confirmed genuinely-unauthenticated requests correctly return 401); the formal `test_authorization.py` suite creates one fresh client per user from the start | `test_unauthenticated_requests_are_rejected_across_the_v2_surface` passes against a deliberately fresh client |

## 13. Deviations

| Specification | Actual implementation | Reason | Impact |
|---|---|---|---|
| "The SDK must send: ... run data, spans, evaluation results, ..." (implying network transmission) | `AgentGuard`'s API key resolves identity in-process against the same shared `RunRepository`, not over a new HTTP ingestion endpoint | Rule 19 ("Do not replace the existing AgentGuard architecture unnecessarily") — the SDK-writes-directly-to-storage design is the architecture every phase since Phase 1 has built on; introducing a parallel network transport would be exactly the unnecessary architectural replacement the spec warns against | None on isolation/authentication guarantees, which are real and enforced (§10); stated explicitly, not concealed (§11) |
| "Errors" as its own Observability nav item | Implemented as a client-side filtered view of `/api/v2/runs` (status ∈ {failed, stop}) rather than a new dedicated backend endpoint | The existing `/api/v2/runs` response already carries everything an Errors table needs (status, exception fields); a dedicated endpoint would duplicate that query for no behavioral difference | None — the page is fully functional, real data, same filtering a server-side endpoint would apply |
| "Failure Patterns" | Implemented as a new, small, real endpoint (`GET /api/v2/failure-patterns`) that groups a workspace's runs by their actual, already-persisted `RootCause.explanation` text | The spec names this as a distinct page; grouping identical root-cause explanations across runs is a reasonable, literal, non-fabricated interpretation of "pattern" using data that already exists | None — every "pattern" shown links back to its real, underlying run IDs |
| Team/Users "invite" | Adds an EXISTING AgentGuard user to the workspace by email; does not send an invitation email to a new address | No outbound email capability in this environment (§11) | Stated in the UI itself ("This environment has no outbound email...") |

## 14. Regression Results

```
Before Dashboard V2 (Phase 4 final):  219 passed, 0 failed, 4 skipped
After Dashboard V2:                   249 passed, 0 failed, 4 skipped
```

All 219 Phase 1-4 test functions are present, unmodified, among the 249
total, and pass verbatim. **One** pre-existing test's assertions were
intentionally updated, not silently dropped:
`tests/e2e/test_portal_e2e.py::test_portal_dashboard_html_contains_control_portal_section`
checked for specific strings in the OLD dashboard's static HTML; since
the spec explicitly mandates replacing that UI, the test was rewritten
to verify the new app shell + `app.js` instead (still checks that the
same underlying features — Policy management, Tool/Behavior pages,
Overview, Recovery — are genuinely present, just in their new home) —
this is documented here, not hidden, and the diff is visible in the test
file's own updated docstring.

The Phase 3 canonical demo (`python examples/budget_failure_recovery.py`)
was re-run after all Dashboard V2 changes and still produces the
identical DETECTED → ROOT CAUSE → STOP → ROLLBACK → COUNTERFACTUAL →
RECOVERY → SUCCESS sequence with an intact audit chain.

## 15. Final Verdict

**DASHBOARD V2 — PASS**

Real multi-user authentication (signup/login/logout/forgot-password/
reset-password/session expiration) is implemented and unit-tested (§8);
API keys are created/authenticated/revoked/rotated with only a SHA-256
hash ever persisted (§5, §8); tenant isolation is enforced at the
repository query level and verified with real cross-user integration
tests, not frontend filtering (§10); the new dashboard's full
information architecture (Overview, Observability, Reliability,
Recovery, Governance, Improvement, Settings) is implemented against real
`/api/v2` data with honest "N/A"/empty-state handling, never fabricated
numbers (§6); a real Chromium browser, driven by Playwright, was
actually used to exercise the complete required flow — signup through
audit inspection — twice, consistently (§9); and the entire Phase 1-4
regression suite remains green, with the one intentional, disclosed UI
content-assertion update (§14).
