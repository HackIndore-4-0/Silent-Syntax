from __future__ import annotations

import pytest

from agentguard.audit.chain import append_event, build_chain, verify_audit_chain
from agentguard.audit.hashchain import GENESIS_HASH, canonical_json, compute_event_hash

from ..fakes import InMemoryRunRepository


def test_canonical_json_is_stable_regardless_of_key_order():
    a = canonical_json({"b": 2, "a": 1})
    b = canonical_json({"a": 1, "b": 2})
    assert a == b


def test_hash_generation_is_deterministic_and_content_sensitive():
    h1 = compute_event_hash({"a": 1}, GENESIS_HASH)
    h2 = compute_event_hash({"a": 1}, GENESIS_HASH)
    h3 = compute_event_hash({"a": 2}, GENESIS_HASH)
    h4 = compute_event_hash({"a": 1}, "not-genesis")

    assert h1 == h2
    assert h1 != h3
    assert h1 != h4
    assert len(h1) == 64


def test_chained_hash_generation_links_each_event_to_the_previous():
    chain = build_chain(
        "run-1",
        [("RUN_START", {"x": 1}), ("DECISION", {"outcome": "continue"})],
        policy_version=1,
    )
    assert len(chain) == 2
    assert chain[0].previous_hash == GENESIS_HASH
    assert chain[0].seq == 1
    assert chain[1].previous_hash == chain[0].event_hash
    assert chain[1].seq == 2
    # Independently recomputable.
    assert chain[0].event_hash == compute_event_hash({"x": 1}, GENESIS_HASH)
    assert chain[1].event_hash == compute_event_hash({"outcome": "continue"}, chain[0].event_hash)


async def test_valid_chain_verification_reports_intact():
    repo = InMemoryRunRepository()
    for event in build_chain("run-1", [("RUN_START", {"x": 1}), ("RUN_COMPLETION", {"status": "continue"})], policy_version=2):
        await repo.save_audit_event(event)

    result = await verify_audit_chain(repo, "run-1")
    assert result["intact"] is True
    assert result["event_count"] == 2
    assert result["first_invalid_event"] is None


async def test_tampered_chain_is_detected():
    repo = InMemoryRunRepository()
    for event in build_chain(
        "run-1",
        [("RUN_START", {"x": 1}), ("DECISION", {"outcome": "stop"}), ("RUN_COMPLETION", {"status": "stop"})],
        policy_version=2,
    ):
        await repo.save_audit_event(event)

    # Tamper with the middle event's payload directly in storage.
    stored = repo.audit_events["run-1"]
    tampered_id = stored[1].id
    stored[1].payload["outcome"] = "continue"  # an unauthorized edit

    result = await verify_audit_chain(repo, "run-1")
    assert result["intact"] is False
    assert result["first_invalid_event"] == tampered_id


async def test_first_broken_event_is_identified_precisely():
    repo = InMemoryRunRepository()
    for event in build_chain(
        "run-1",
        [("RUN_START", {}), ("AGENT_STEP", {"attempt": 1}), ("AGENT_STEP", {"attempt": 2}), ("RUN_COMPLETION", {})],
        policy_version=1,
    ):
        await repo.save_audit_event(event)

    stored = repo.audit_events["run-1"]
    # Events 0 and 1 are untouched; event 2 (3rd, index 2) is tampered.
    third_event_id = stored[2].id
    stored[2].payload["attempt"] = 999

    result = await verify_audit_chain(repo, "run-1")
    assert result["intact"] is False
    assert result["first_invalid_event"] == third_event_id
    assert result["first_invalid_seq"] == 3


async def test_restoring_tampered_payload_makes_the_chain_intact_again():
    repo = InMemoryRunRepository()
    for event in build_chain("run-1", [("RUN_START", {"a": 1}), ("RUN_COMPLETION", {"status": "continue"})], policy_version=1):
        await repo.save_audit_event(event)

    stored = repo.audit_events["run-1"]
    original = dict(stored[0].payload)
    stored[0].payload["a"] = 999
    assert (await verify_audit_chain(repo, "run-1"))["intact"] is False

    stored[0].payload = original
    assert (await verify_audit_chain(repo, "run-1"))["intact"] is True


async def test_append_event_extends_an_existing_chain():
    repo = InMemoryRunRepository()
    for event in build_chain("run-1", [("RUN_START", {}), ("RUN_COMPLETION", {"status": "stop"})], policy_version=1):
        await repo.save_audit_event(event)

    appended = await append_event(repo, "run-1", "ROLLBACK", {"checkpoint": "S2"}, policy_version=1)
    assert appended.seq == 3
    events = await repo.list_audit_events("run-1")
    assert appended.previous_hash == events[1]["event_hash"]

    result = await verify_audit_chain(repo, "run-1")
    assert result["intact"] is True
    assert result["event_count"] == 3


async def test_append_event_on_an_empty_chain_starts_from_genesis():
    repo = InMemoryRunRepository()
    event = await append_event(repo, "run-2", "RUN_START", {"x": 1}, policy_version=1)
    assert event.previous_hash == GENESIS_HASH
    assert event.seq == 1
