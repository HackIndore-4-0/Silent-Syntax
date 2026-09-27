from __future__ import annotations

import pytest

from agentguard.models import Policy
from agentguard.policy.registry import PolicyRegistry, PolicyRegistryError

from ..fakes import InMemoryRunRepository


@pytest.fixture
def repo():
    return InMemoryRunRepository()


async def test_create_policy_starts_at_version_one(repo):
    registry = PolicyRegistry(repo)
    record = await registry.create("demo", Policy(max_cost=60000, retry_limit=2))
    assert record.version == 1
    current = await registry.get_current("demo")
    assert current.version == 1
    assert current.retry_limit == 2


async def test_create_twice_raises(repo):
    registry = PolicyRegistry(repo)
    await registry.create("demo", Policy(max_cost=60000))
    with pytest.raises(PolicyRegistryError):
        await registry.create("demo", Policy(max_cost=70000))


async def test_save_new_version_increments_and_becomes_current(repo):
    registry = PolicyRegistry(repo)
    await registry.create("demo", Policy(max_cost=60000, retry_limit=2))
    record = await registry.save_new_version("demo", Policy(max_cost=60000, retry_limit=5))
    assert record.version == 2

    current = await registry.get_current("demo")
    assert current.version == 2
    assert current.retry_limit == 5


async def test_save_new_version_without_create_raises(repo):
    registry = PolicyRegistry(repo)
    with pytest.raises(PolicyRegistryError):
        await registry.save_new_version("does-not-exist", Policy())


async def test_historical_versions_are_preserved_unmutated(repo):
    registry = PolicyRegistry(repo)
    await registry.create("demo", Policy(max_cost=60000, retry_limit=2))
    await registry.save_new_version("demo", Policy(max_cost=60000, retry_limit=5))
    await registry.save_new_version("demo", Policy(max_cost=60000, retry_limit=9))

    v1 = await registry.get_version("demo", 1)
    v2 = await registry.get_version("demo", 2)
    v3 = await registry.get_version("demo", 3)
    assert v1.retry_limit == 2
    assert v2.retry_limit == 5
    assert v3.retry_limit == 9

    versions = await registry.list_versions("demo")
    assert [v["version"] for v in versions] == [1, 2, 3]


async def test_get_unknown_version_raises(repo):
    registry = PolicyRegistry(repo)
    await registry.create("demo", Policy())
    with pytest.raises(PolicyRegistryError):
        await registry.get_version("demo", 99)


async def test_get_current_on_unknown_policy_raises(repo):
    registry = PolicyRegistry(repo)
    with pytest.raises(PolicyRegistryError):
        await registry.get_current("does-not-exist")


async def test_list_definitions_reflects_current_version(repo):
    registry = PolicyRegistry(repo)
    await registry.create("demo", Policy())
    await registry.save_new_version("demo", Policy(retry_limit=9))
    definitions = await registry.list_definitions()
    demo = next(d for d in definitions if d["name"] == "demo")
    assert demo["current_version"] == 2
