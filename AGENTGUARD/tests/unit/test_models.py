from agentguard.models import AgentState, Run, RunStatus


def test_agent_state_update_and_snapshot():
    state = AgentState(max_budget=60000)
    assert state.snapshot() == {"max_budget": 60000}

    state.update(max_budget=67000, category="laptop")
    assert state.snapshot() == {"max_budget": 67000, "category": "laptop"}


def test_agent_state_from_snapshot_round_trips():
    state = AgentState.from_snapshot({"max_budget": 60000})
    assert state.snapshot() == {"max_budget": 60000}


def test_run_defaults_to_running():
    run = Run(agent_name="my_agent")
    assert run.status == RunStatus.RUNNING
    assert run.policy.max_cost is None
    assert run.finished_at is None


def test_run_duration_ms_none_until_finished():
    run = Run(agent_name="my_agent")
    assert run.duration_ms is None
