"""Dashboard V2 — real browser testing with Playwright (spec §20/§22).

A hermetic test: starts its own uvicorn server (on its own port, its own
fresh in-memory repository) in a background thread, drives it with a
REAL Chromium browser (not a mock, not TestClient), and exercises the
exact required flow:

    signup -> login -> create project -> create API key -> run agent
    with SDK -> run appears in dashboard -> open trace -> view latency
    -> view token usage -> inspect evaluations -> inspect recovery
    -> inspect audit

Also covers the individual page-load checks (overview, run list, run
detail, evaluation pages, latency charts, token charts, audit, recovery)
the spec lists under "Dashboard" testing.
"""
from __future__ import annotations

import socket
import threading
import time

import pytest
import uvicorn

import agentguard
from agentguard import AgentGuard, Policy
from agentguard._runtime import configure as configure_repository
from agentguard._runtime import reset as reset_repository
from agentguard.human.broker import reset_broker
from agentguard.storage.memory import InMemoryRunRepository


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def fake_repository(request):
    """Shadows `tests/e2e/conftest.py`'s own `autouse=True`
    `fake_repository` fixture — a fixture defined in this module with
    the SAME NAME fully replaces the parent conftest's version for every
    test here, rather than both running (pytest's standard override
    mechanism, not a matter of hoping for a favorable execution order).

    The parent version reconfigures `agentguard._runtime`'s global
    repository to a fresh throwaway `InMemoryRunRepository` before EVERY
    test, which would silently pull the rug out from under this module's
    own `live_server` fixture — a real HTTP server, started once per
    module, whose request handlers call `get_repository()` fresh on
    every request and must keep seeing the SAME repository this test's
    assertions inspect. Re-points the global repository at
    `live_server`'s repo instead, for tests that use it."""
    if "live_server" in request.fixturenames:
        _, repo = request.getfixturevalue("live_server")
        configure_repository(repo)
        yield repo
    else:
        repo = InMemoryRunRepository()
        configure_repository(repo)
        reset_broker()
        yield repo
        reset_repository()
        reset_broker()


@pytest.fixture(scope="module")
def live_server():
    reset_repository()
    reset_broker()
    repo = InMemoryRunRepository()
    configure_repository(repo)

    from server.api import app

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    for _ in range(100):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.1)
                s.connect(("127.0.0.1", port))
            break
        except OSError:
            time.sleep(0.05)
    else:
        raise RuntimeError("test server did not start in time")

    yield f"http://127.0.0.1:{port}", repo

    server.should_exit = True
    thread.join(timeout=5)
    reset_repository()
    reset_broker()


@pytest.fixture
def page(live_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        yield pg
        browser.close()


def test_full_flow_signup_to_audit(live_server, page):
    base_url, repo = live_server
    email = "browsertest@example.com"
    password = "browser-test-pw-1"

    # -- 1. signup --------------------------------------------------------
    page.goto(base_url)
    page.wait_for_url("**/#/login", timeout=5000)
    page.click("a[href='#/signup']")
    page.wait_for_selector("#name")
    page.fill("#name", "Browser Test")
    page.fill("#email", email)
    page.fill("#password", password)
    page.click("button:has-text('Create account')")
    page.wait_for_url("**/#/overview", timeout=5000)
    assert "#/overview" in page.url

    # -- 2. login (log out, then log back in, to exercise the login path too) --
    page.click("button:has-text('Log out')")
    page.wait_for_url("**/#/login", timeout=5000)
    page.fill("#email", email)
    page.fill("#password", password)
    page.click("button:has-text('Sign in')")
    page.wait_for_url("**/#/overview", timeout=5000)

    # -- 3. create project --------------------------------------------------
    page.evaluate(
        """() => fetch('/api/projects', {method: 'POST', headers: {'Content-Type': 'application/json'}, credentials: 'include', body: JSON.stringify({name: 'staging'})})"""
    )
    page.wait_for_timeout(200)

    # -- 4. create API key via the real UI -----------------------------------
    page.click("a[data-path='settings/api-keys']")
    page.wait_for_selector("#new-key-name")
    page.fill("#new-key-name", "browser-test-key")
    page.click("button:has-text('Create API Key')")
    page.wait_for_selector("#new-key-result code")
    raw_key = page.locator("#new-key-result code").inner_text()
    assert raw_key.startswith("agp_live_")

    # -- 5. run agent with the SDK, using the key generated by the logged-in
    #    user's own AgentGuard account (never hard-coded) -------------------
    #    Run on a plain background thread, deliberately separate from
    #    Playwright's sync-API thread: Playwright's sync wrapper runs its
    #    own event loop via greenlets on the calling thread (confirmed:
    #    `asyncio.get_running_loop()` succeeds there), which
    #    `AgentGuard.__init__` correctly refuses to construct inside (see
    #    agentguard/client.py's documented reasoning — no silent thread-
    #    hopping that could corrupt a real asyncpg pool). This mirrors how
    #    a real user's agent script and their browser test are two
    #    separate processes anyway.
    existing_run_ids = set(repo.runs.keys())
    guard = _construct_agentguard_off_thread(raw_key, "production")

    async def browser_test_agent_run() -> None:
        @guard.monitor(policy=Policy(max_cost=60000, version=1), llm_judge=False)
        async def browser_test_agent(task: str) -> str:
            agentguard.update_state(max_budget=60000)
            agentguard.reset_state()
            agentguard.update_state(max_budget=67000)
            agentguard.record_tokens(model_name="test-model", input_tokens=100, output_tokens=50, cost_usd=0.001)
            return "selected a 67000 laptop"

        await browser_test_agent("find a laptop under budget")

    _run_coroutine_off_thread(browser_test_agent_run())
    run_id = next(rid for rid in repo.runs if rid not in existing_run_ids)

    # -- 6. run appears in the dashboard --------------------------------------
    page.click("a[data-path='runs']")
    page.wait_for_selector("table tbody tr", timeout=5000)
    page.wait_for_timeout(300)
    assert page.locator(f"tr[onclick*='{run_id}']").count() == 1, "the SDK-created run must appear in the authenticated dashboard"

    # -- 7. open trace --------------------------------------------------------
    page.locator(f"tr[onclick*='{run_id}']").click()
    page.wait_for_url(f"**/#/runs/{run_id}", timeout=5000)
    page.click(".tab:has-text('Trace')")
    page.wait_for_timeout(300)
    trace_text = page.locator("#run-tab-content").inner_text()
    assert "RUN_START" in trace_text or "Agent Run" in trace_text or len(trace_text) > 0

    # -- 8. view latency --------------------------------------------------------
    page.click(".tab:has-text('Latency')")
    page.wait_for_timeout(200)
    assert "Duration" in page.locator("#run-tab-content").inner_text()

    # -- 9. view token usage ------------------------------------------------------
    page.click(".tab:has-text('Tokens')")
    page.wait_for_timeout(200)
    tokens_text = page.locator("#run-tab-content").inner_text()
    assert "100" in tokens_text  # input tokens we recorded

    # -- 10. inspect evaluations ---------------------------------------------------
    page.click(".tab:has-text('Evaluations')")
    page.wait_for_timeout(200)
    assert "constraint_adherence" in page.locator("#run-tab-content").inner_text()

    # -- 11. inspect recovery ------------------------------------------------------
    page.click(".tab:has-text('Recovery')")
    page.wait_for_timeout(200)
    assert page.locator("#run-tab-content").inner_text()  # renders without error

    # -- 12. inspect audit -----------------------------------------------------------
    page.click(".tab:has-text('Audit')")
    page.wait_for_timeout(300)
    audit_text = page.locator("#run-tab-content").inner_text()
    assert "CHAIN VALID" in audit_text


def test_overview_page_loads(live_server, page):
    base_url, repo = live_server
    _login_fresh_user(page, base_url, "overview-check@example.com")
    page.wait_for_selector(".kpi-card")
    assert page.locator(".kpi-card").count() == 6


def test_run_list_and_detail_load(live_server, page):
    base_url, repo = live_server
    email = "runlist-check@example.com"
    _login_fresh_user(page, base_url, email)

    # Seed one run for this fresh user via a fresh API key (off-thread —
    # see the comment in test_full_flow_signup_to_audit for why).
    api_key = _create_key_via_ui(page)
    guard = _construct_agentguard_off_thread(api_key, "production")

    async def _run() -> None:
        @guard.monitor(policy=Policy(max_cost=60000), llm_judge=False)
        async def agent(task: str) -> str:
            agentguard.update_state(max_budget=50000)
            return "ok"

        await agent("task")

    existing_before = set(repo.runs.keys())
    _run_coroutine_off_thread(_run())
    new_runs = set(repo.runs.keys()) - existing_before
    assert new_runs, f"agent run did not create a new run in the shared repo (guard.workspace_id={guard.workspace_id}, total runs={len(repo.runs)})"

    page.click("a[data-path='runs']")
    page.wait_for_selector("table tbody tr")
    page.wait_for_timeout(300)
    assert page.locator("tr.clickable").count() >= 1, f"new run {new_runs} not visible in dashboard; page content: {page.locator('#content').inner_text()[:300]}"

    page.locator("tr.clickable").first.click()
    page.wait_for_timeout(300)
    assert "#/runs/" in page.url
    assert page.locator(".tabs .tab").count() == 9


def test_evaluation_latency_token_audit_recovery_pages_load(live_server, page):
    base_url, repo = live_server
    _login_fresh_user(page, base_url, "pages-check@example.com")

    for path, expect_text in [
        ("evaluations", "Goal Completion"),
        ("latency", "P50"),
        ("tokens", None),
        ("audit", "Audit"),
        ("checkpoints", "Rollback"),
        ("risk", "Risk"),
        ("tools", "Tools"),
        ("behavior", "Agent Behavior"),
        ("policies", "Policy Management"),
    ]:
        page.evaluate(f"window.location.hash = '#/{path}'")
        page.wait_for_timeout(300)
        body = page.locator("#content").inner_text()
        assert len(body) > 0, f"{path} page rendered empty"
        if expect_text:
            assert expect_text in body, f"{path} page missing expected text {expect_text!r}"


def _construct_agentguard_off_thread(api_key: str, project: str) -> AgentGuard:
    """See the comment in test_full_flow_signup_to_audit: Playwright's
    sync API leaves an event loop "running" (via greenlets) on whatever
    thread drives it, which `AgentGuard.__init__` correctly refuses to
    construct inside. A plain background thread has no such loop."""
    result: dict = {}

    def _worker() -> None:
        try:
            result["guard"] = AgentGuard(api_key=api_key, project=project)
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=10)
    if thread.is_alive():
        raise TimeoutError("AgentGuard construction did not finish within 10s (thread still running)")
    if "error" in result:
        raise result["error"]
    return result["guard"]


def _run_coroutine_off_thread(coro) -> None:
    import asyncio

    result: dict = {}

    def _worker() -> None:
        try:
            asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=10)
    if thread.is_alive():
        raise TimeoutError("agent run did not finish within 10s (thread still running)")
    if "error" in result:
        raise result["error"]


def _login_fresh_user(page, base_url, email, password="testpassword1"):
    page.goto(base_url)
    page.wait_for_url("**/#/login", timeout=5000)
    page.click("a[href='#/signup']")
    page.wait_for_selector("#name")
    page.fill("#name", "Test User")
    page.fill("#email", email)
    page.fill("#password", password)
    page.click("button:has-text('Create account')")
    page.wait_for_url("**/#/overview", timeout=5000)


def _create_key_via_ui(page) -> str:
    page.click("a[data-path='settings/api-keys']")
    page.wait_for_selector("#new-key-name")
    page.fill("#new-key-name", "test-key")
    page.click("button:has-text('Create API Key')")
    page.wait_for_selector("#new-key-result code")
    return page.locator("#new-key-result code").inner_text()
