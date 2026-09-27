"""Authorization / tenant-isolation integration tests — Dashboard V2
(spec §20): User A cannot access User B's runs, API keys, or policies.

Driven through the REAL FastAPI app via TestClient, exactly the surface
the actual dashboard/SDK use — this is what "enforce isolation at the
API/database query level" (not only frontend filtering) means verified.
"""
from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

import agentguard
from agentguard import AgentGuard, Policy
from agentguard._runtime import reset as reset_repository
from agentguard.auth import create_api_key, signup
from agentguard.human.broker import reset_broker
from agentguard.storage.memory import InMemoryRunRepository


@pytest.fixture
def repo():
    r = InMemoryRunRepository()
    agentguard.configure(r)
    reset_broker()
    yield r
    reset_repository()
    reset_broker()


@pytest.fixture
def client(repo):
    from server.api import app

    with TestClient(app) as c:
        yield c


def _signup_and_seed_run(repo, client, *, email: str, name: str) -> dict:
    signup_result = asyncio.run(signup(repo, email=email, password="hunter2222", name=name))
    key_result = asyncio.run(
        create_api_key(
            repo, user_id=signup_result.user.id, workspace_id=signup_result.workspace.id,
            project_id=signup_result.project.id, name="seed key",
        )
    )
    guard = AgentGuard(api_key=key_result.raw_key, project="production")

    @guard.monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def seeded_agent(task: str) -> str:
        agentguard.update_state(max_budget=50000)
        return "ok"

    existing_run_ids = set(repo.runs.keys())
    asyncio.run(seeded_agent(f"{name}'s task"))
    run_id = next(rid for rid in repo.runs if rid not in existing_run_ids)

    login_response = client.post("/api/auth/login", json={"email": email, "password": "hunter2222"})
    cookie_jar = login_response.cookies

    return {
        "signup": signup_result,
        "api_key": key_result,
        "run_id": run_id,
        "cookies": cookie_jar,
    }


def _fresh_client_for(cookies) -> TestClient:
    """A per-user TestClient so cookie jars never leak between users —
    `TestClient` otherwise persists cookies like a `requests.Session`,
    which would silently mask a real isolation bug in a shared client."""
    from server.api import app

    c = TestClient(app, cookies=cookies)
    return c


def test_user_a_cannot_access_user_b_runs(repo, client):
    alice = _signup_and_seed_run(repo, client, email="alice@example.com", name="Alice")
    bob = _signup_and_seed_run(repo, client, email="bob@example.com", name="Bob")

    alice_client = _fresh_client_for(alice["cookies"])
    bob_client = _fresh_client_for(bob["cookies"])

    # Alice can see her own run.
    assert alice_client.get(f"/api/v2/runs/{alice['run_id']}").status_code == 200
    # Bob cannot see Alice's run — 404, not 403 (never confirms it exists).
    assert bob_client.get(f"/api/v2/runs/{alice['run_id']}").status_code == 404
    # And vice versa.
    assert alice_client.get(f"/api/v2/runs/{bob['run_id']}").status_code == 404

    # Bob's run list contains only his own run.
    bob_runs = bob_client.get("/api/v2/runs").json()["runs"]
    assert all(r["id"] != alice["run_id"] for r in bob_runs)
    assert any(r["id"] == bob["run_id"] for r in bob_runs)

    # Sub-resources are equally isolated.
    for suffix in ["timeline", "reliability-report", "checkpoints", "audit", "replay", "trace-steps"]:
        assert bob_client.get(f"/api/v2/runs/{alice['run_id']}/{suffix}").status_code == 404


def test_user_a_cannot_access_user_b_api_keys(repo, client):
    alice = _signup_and_seed_run(repo, client, email="alice2@example.com", name="Alice2")
    bob = _signup_and_seed_run(repo, client, email="bob2@example.com", name="Bob2")

    alice_client = _fresh_client_for(alice["cookies"])
    bob_client = _fresh_client_for(bob["cookies"])

    alice_keys = alice_client.get("/api/settings/api-keys").json()
    bob_keys = bob_client.get("/api/settings/api-keys").json()
    alice_key_ids = {k["id"] for k in alice_keys}
    bob_key_ids = {k["id"] for k in bob_keys}
    assert alice_key_ids.isdisjoint(bob_key_ids)

    # Bob cannot revoke Alice's key by ID even if he somehow learned it.
    response = bob_client.post(f"/api/settings/api-keys/{alice['api_key'].api_key.id}/revoke")
    assert response.status_code == 404

    # The key must still work — Bob's attempted revoke had no effect.
    from agentguard.auth import authenticate_api_key

    auth_user = asyncio.run(authenticate_api_key(repo, alice["api_key"].raw_key))
    assert auth_user is not None
    assert auth_user.api_key.status == "active"


def test_user_a_cannot_access_user_b_policies(repo, client):
    alice = _signup_and_seed_run(repo, client, email="alice3@example.com", name="Alice3")
    bob = _signup_and_seed_run(repo, client, email="bob3@example.com", name="Bob3")

    alice_client = _fresh_client_for(alice["cookies"])
    bob_client = _fresh_client_for(bob["cookies"])

    created = alice_client.post("/api/v2/policies", json={"name": "checkout_policy", "policy": {"max_cost": 60000}})
    assert created.status_code == 200

    # Alice can read her own policy.
    assert alice_client.get("/api/v2/policies/checkout_policy").status_code == 200
    # Bob cannot see a policy with the same name — it's scoped to Alice's workspace.
    assert bob_client.get("/api/v2/policies/checkout_policy").status_code == 404
    # Bob's policy list does not include it.
    bob_policies = bob_client.get("/api/v2/policies").json()
    assert all(p["name"] != "checkout_policy" for p in bob_policies) or len(bob_policies) == 0

    # Bob can create a policy with the SAME NAME in his own workspace —
    # names are unique per-workspace, not globally.
    bob_created = bob_client.post("/api/v2/policies", json={"name": "checkout_policy", "policy": {"max_cost": 1000}})
    assert bob_created.status_code == 200
    assert bob_client.get("/api/v2/policies/checkout_policy").json()["policy"]["max_cost"] == 1000.0
    # Alice's own copy is unaffected.
    assert alice_client.get("/api/v2/policies/checkout_policy").json()["policy"]["max_cost"] == 60000.0


def _signup_and_seed_failing_runs(
    repo, client, *, email: str, name: str, count: int = 2, exception_cls: type = ValueError
) -> dict:
    """Like _signup_and_seed_run, but seeds `count` runs that all raise
    the same exception at the same @traceable code location — enough to
    form one FailureCluster (MIN_CLUSTER_SIZE=2) for this workspace.
    `exception_cls` varies per caller so two workspaces' clusters get
    distinct cluster_keys (the key hashes exception_type + code
    location, not workspace_id — two workspaces sharing the exact same
    bug signature are expected to collide, isolation comes from which
    RUNS the clustering query sees, not from the key itself)."""
    from agentguard import traceable

    signup_result = asyncio.run(signup(repo, email=email, password="hunter2222", name=name))
    key_result = asyncio.run(
        create_api_key(
            repo, user_id=signup_result.user.id, workspace_id=signup_result.workspace.id,
            project_id=signup_result.project.id, name="seed key",
        )
    )
    guard = AgentGuard(api_key=key_result.raw_key, project="production")

    @traceable
    async def flaky_step() -> None:
        raise exception_cls("boom")

    @guard.monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def seeded_agent(task: str) -> None:
        await flaky_step()

    run_ids: list[str] = []
    for i in range(count):
        existing_run_ids = set(repo.runs.keys())
        try:
            asyncio.run(seeded_agent(f"{name}'s task {i}"))
        except exception_cls:
            pass
        run_ids.append(next(rid for rid in repo.runs if rid not in existing_run_ids))

    login_response = client.post("/api/auth/login", json={"email": email, "password": "hunter2222"})
    return {"run_ids": run_ids, "cookies": login_response.cookies}


def test_user_a_cannot_access_user_b_problems(repo, client):
    alice = _signup_and_seed_failing_runs(repo, client, email="alice4@example.com", name="Alice4", exception_cls=ValueError)
    bob = _signup_and_seed_failing_runs(repo, client, email="bob4@example.com", name="Bob4", exception_cls=KeyError)

    alice_client = _fresh_client_for(alice["cookies"])
    bob_client = _fresh_client_for(bob["cookies"])

    alice_problems = alice_client.get("/api/v2/problems").json()
    bob_problems = bob_client.get("/api/v2/problems").json()

    assert len(alice_problems) == 1
    assert set(alice_problems[0]["run_ids"]) == set(alice["run_ids"])
    assert len(bob_problems) == 1
    assert set(bob_problems[0]["run_ids"]) == set(bob["run_ids"])

    cluster_key = alice_problems[0]["cluster_key"]
    assert bob_client.get(f"/api/v2/problems/{cluster_key}").status_code == 404


def _signup_and_seed_llm_calls(repo, client, *, email: str, name: str, count: int) -> dict:
    pytest.importorskip("litellm")
    from agentguard.tracing import traced_acompletion

    signup_result = asyncio.run(signup(repo, email=email, password="hunter2222", name=name))
    key_result = asyncio.run(
        create_api_key(
            repo, user_id=signup_result.user.id, workspace_id=signup_result.workspace.id,
            project_id=signup_result.project.id, name="seed key",
        )
    )
    guard = AgentGuard(api_key=key_result.raw_key, project="production")

    @guard.monitor(policy=Policy(max_cost=60000), llm_judge=False)
    async def seeded_agent(task: str) -> str:
        response = await traced_acompletion(
            model="gpt-3.5-turbo", messages=[{"role": "user", "content": task}], mock_response="hey there"
        )
        return response.choices[0].message.content

    for i in range(count):
        asyncio.run(seeded_agent(f"task {i}"))

    login_response = client.post("/api/auth/login", json={"email": email, "password": "hunter2222"})
    return {"cookies": login_response.cookies}


def test_user_a_cannot_access_user_b_models(repo, client):
    # Same model name, DIFFERENT call counts per workspace — if
    # /api/v2/models leaked across tenants, one side's sample_count
    # would reflect the other's calls too.
    alice = _signup_and_seed_llm_calls(repo, client, email="alice5@example.com", name="Alice5", count=2)
    bob = _signup_and_seed_llm_calls(repo, client, email="bob5@example.com", name="Bob5", count=1)

    alice_client = _fresh_client_for(alice["cookies"])
    bob_client = _fresh_client_for(bob["cookies"])

    alice_models = {m["model"]: m for m in alice_client.get("/api/v2/models").json()["models"]}
    bob_models = {m["model"]: m for m in bob_client.get("/api/v2/models").json()["models"]}

    assert alice_models["gpt-3.5-turbo"]["sample_count"] == 2
    assert bob_models["gpt-3.5-turbo"]["sample_count"] == 1


def test_unauthenticated_requests_are_rejected_across_the_v2_surface(repo):
    from server.api import app

    fresh = TestClient(app)  # no login, no cookies, no bearer token
    for path in ["/api/v2/overview", "/api/v2/runs", "/api/v2/tools", "/api/v2/policies", "/api/settings/api-keys"]:
        response = fresh.get(path)
        assert response.status_code == 401, f"{path} should require authentication, got {response.status_code}"


def test_invalid_bearer_token_is_rejected(repo, client):
    response = client.get("/api/v2/runs", headers={"Authorization": "Bearer agp_live_not_a_real_key"})
    assert response.status_code == 401


def _login(client, *, email: str, name: str, repo):
    signup_result = asyncio.run(signup(repo, email=email, password="hunter2222", name=name))
    login_response = client.post("/api/auth/login", json={"email": email, "password": "hunter2222"})
    return {"signup": signup_result, "cookies": login_response.cookies}


def test_user_a_cannot_access_user_b_eval_runs(repo, client):
    from agentguard.evaluation import CustomEvaluator, EvalCase, EvaluationEngine, EvaluationSuite, SuiteMetric
    from agentguard.models import EvaluationResult

    alice = _login(client, email="alice6@example.com", name="Alice6", repo=repo)
    bob = _login(client, email="bob6@example.com", name="Bob6", repo=repo)

    async def scorer(case):
        return EvaluationResult(evaluation_run_id="", metric="stub", score=1.0, reason="stub")

    suite = EvaluationSuite(name="s", workspace_id=alice["signup"].workspace.id, metrics=[SuiteMetric(evaluator="stub")])
    asyncio.run(repo.save_evaluation_suite(suite))
    engine = EvaluationEngine(repo, {"stub": CustomEvaluator("stub", scorer)})
    evaluation_run = asyncio.run(
        engine.run_suite(suite, [EvalCase(input="q", actual_output="a")], workspace_id=alice["signup"].workspace.id)
    )

    alice_client = _fresh_client_for(alice["cookies"])
    bob_client = _fresh_client_for(bob["cookies"])

    assert alice_client.get(f"/api/v2/eval-runs/{evaluation_run.id}").status_code == 200
    assert bob_client.get(f"/api/v2/eval-runs/{evaluation_run.id}").status_code == 404
    bob_runs = bob_client.get("/api/v2/eval-runs").json()
    assert all(r["id"] != evaluation_run.id for r in bob_runs)


def test_user_a_cannot_access_user_b_datasets(repo, client):
    from agentguard.models import Dataset, DatasetVersion, DatasetExample

    alice = _login(client, email="alice7@example.com", name="Alice7", repo=repo)
    bob = _login(client, email="bob7@example.com", name="Bob7", repo=repo)

    dataset = Dataset(name="alice_dataset", workspace_id=alice["signup"].workspace.id)
    asyncio.run(repo.save_dataset(dataset))
    version = DatasetVersion(dataset_id=dataset.id, version=1)
    asyncio.run(repo.save_dataset_version(version))
    asyncio.run(repo.save_dataset_example(DatasetExample(dataset_version_id=version.id, question="q", expected_answer="a")))

    alice_client = _fresh_client_for(alice["cookies"])
    bob_client = _fresh_client_for(bob["cookies"])

    assert alice_client.get(f"/api/v2/datasets/{dataset.id}/versions/1").status_code == 200
    assert bob_client.get(f"/api/v2/datasets/{dataset.id}/versions/1").status_code == 404
    bob_datasets = bob_client.get("/api/v2/datasets").json()
    assert all(d["id"] != dataset.id for d in bob_datasets)


def test_user_a_cannot_access_user_b_benchmarks(repo, client):
    from agentguard.models import ModelBenchmark

    alice = _login(client, email="alice8@example.com", name="Alice8", repo=repo)
    bob = _login(client, email="bob8@example.com", name="Bob8", repo=repo)

    benchmark = ModelBenchmark(workspace_id=alice["signup"].workspace.id, suite_id="s1", models=["m1", "m2"])
    asyncio.run(repo.save_model_benchmark(benchmark))

    alice_client = _fresh_client_for(alice["cookies"])
    bob_client = _fresh_client_for(bob["cookies"])

    assert alice_client.get(f"/api/v2/benchmarks/{benchmark.id}").status_code == 200
    assert bob_client.get(f"/api/v2/benchmarks/{benchmark.id}").status_code == 404
    bob_benchmarks = bob_client.get("/api/v2/benchmarks").json()
    assert all(b["id"] != benchmark.id for b in bob_benchmarks)


def test_user_a_cannot_diagnose_user_b_eval_results(repo, client):
    from agentguard.evaluation import CustomEvaluator, EvalCase, EvaluationEngine, EvaluationSuite, SuiteMetric
    from agentguard.models import EvaluationResult

    alice = _login(client, email="alice9@example.com", name="Alice9", repo=repo)
    bob = _login(client, email="bob9@example.com", name="Bob9", repo=repo)

    async def scorer(case):
        return EvaluationResult(evaluation_run_id="", metric="stub", score=0.1, reason="stub")

    suite = EvaluationSuite(name="s", workspace_id=alice["signup"].workspace.id, metrics=[SuiteMetric(evaluator="stub")])
    asyncio.run(repo.save_evaluation_suite(suite))
    engine = EvaluationEngine(repo, {"stub": CustomEvaluator("stub", scorer)})
    evaluation_run = asyncio.run(
        engine.run_suite(suite, [EvalCase(input="q", actual_output="a")], workspace_id=alice["signup"].workspace.id)
    )
    results = asyncio.run(repo.list_evaluation_results(evaluation_run.id))
    result_id = results[0]["id"]

    bob_client = _fresh_client_for(bob["cookies"])
    assert bob_client.post(f"/api/v2/eval-results/{result_id}/diagnose").status_code == 404

    alice_client = _fresh_client_for(alice["cookies"])
    assert alice_client.post(f"/api/v2/eval-results/{result_id}/diagnose").status_code == 200


def test_alice_can_create_and_run_an_evaluation_suite_end_to_end(repo, client):
    from agentguard.jobs import EVALUATION_SUITE_RUN_JOB_KIND, Worker, make_evaluation_run_handler

    alice = _signup_and_seed_run(repo, client, email="alice10@example.com", name="Alice10")
    alice_client = _fresh_client_for(alice["cookies"])

    create_response = alice_client.post("/api/v2/suites", json={"name": "alice_suite", "metrics": [{"evaluator": "trajectory"}]})
    assert create_response.status_code == 200
    suite_id = create_response.json()["id"]
    assert any(s["id"] == suite_id for s in alice_client.get("/api/v2/suites").json())

    run_response = alice_client.post("/api/v2/eval-runs", json={"suite_id": suite_id, "source_run_ids": [alice["run_id"]]})
    assert run_response.status_code == 202
    job_id = run_response.json()["job_id"]
    assert alice_client.get(f"/api/v2/jobs/{job_id}").json()["kind"] == EVALUATION_SUITE_RUN_JOB_KIND

    worker = Worker(repo, {EVALUATION_SUITE_RUN_JOB_KIND: make_evaluation_run_handler(repo)})
    asyncio.run(worker.run_once())

    assert alice_client.get(f"/api/v2/jobs/{job_id}").json()["status"] == "complete"
    eval_runs = alice_client.get("/api/v2/eval-runs").json()
    assert len(eval_runs) == 1
    detail = alice_client.get(f"/api/v2/eval-runs/{eval_runs[0]['id']}").json()
    assert detail["results"][0]["metric"] == "trajectory"


def test_user_a_cannot_create_eval_run_against_user_b_suite(repo, client):
    alice = _signup_and_seed_run(repo, client, email="alice11@example.com", name="Alice11")
    bob = _signup_and_seed_run(repo, client, email="bob11@example.com", name="Bob11")
    alice_client = _fresh_client_for(alice["cookies"])
    bob_client = _fresh_client_for(bob["cookies"])

    bob_suite_id = bob_client.post("/api/v2/suites", json={"name": "bob_suite", "metrics": []}).json()["id"]

    response = alice_client.post("/api/v2/eval-runs", json={"suite_id": bob_suite_id, "source_run_ids": [alice["run_id"]]})

    assert response.status_code == 404
    assert asyncio.run(repo.list_jobs()) == []


def test_user_a_cannot_create_eval_run_against_user_b_owned_run(repo, client):
    alice = _signup_and_seed_run(repo, client, email="alice12@example.com", name="Alice12")
    bob = _signup_and_seed_run(repo, client, email="bob12@example.com", name="Bob12")
    alice_client = _fresh_client_for(alice["cookies"])

    suite_id = alice_client.post("/api/v2/suites", json={"name": "alice_suite2", "metrics": []}).json()["id"]

    response = alice_client.post("/api/v2/eval-runs", json={"suite_id": suite_id, "source_run_ids": [bob["run_id"]]})

    assert response.status_code == 404
    assert asyncio.run(repo.list_jobs()) == []
