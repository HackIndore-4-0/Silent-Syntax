"""Policy Control Portal registry — Phase 4.

A NAMED, VERSIONED Policy store, distinct from the per-run Policy
snapshot every `Run` already carries (`agentguard_policies`, Phase 1/2,
untouched by anything here — immutable once written). This is the
"control/configuration" surface the dashboard's Control Portal and
`POST /api/policies`/`POST /api/policies/{name}/versions` sit on top of.

Saving a policy through this registry ALWAYS creates a new version; it
never mutates a prior one (Rule: "Saving a policy must create a new
version rather than silently mutate the historical policy used by
previous runs"). A run that already executed with version N keeps
referencing version N forever — this registry has no mechanism to
change that, by construction (it only ever INSERTs a new
PolicyVersionRecord, never UPDATEs an existing one).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models import Policy, PolicyDefinition, PolicyVersionRecord


class PolicyRegistryError(ValueError):
    pass


class PolicyRegistry:
    def __init__(self, repository: Any) -> None:
        self._repository = repository

    async def create(self, name: str, policy: Policy) -> PolicyVersionRecord:
        existing = await self._repository.get_policy_definition(name)
        if existing is not None:
            raise PolicyRegistryError(f"policy {name!r} already exists — use save_new_version() instead")

        version = 1
        policy_with_version = policy.model_copy(update={"version": version})
        record = PolicyVersionRecord(policy_name=name, version=version, policy=policy_with_version)
        await self._repository.save_policy_version(record)

        definition = PolicyDefinition(name=name, current_version=version)
        await self._repository.save_policy_definition(definition)
        return record

    async def save_new_version(self, name: str, policy: Policy) -> PolicyVersionRecord:
        definition = await self._repository.get_policy_definition(name)
        if definition is None:
            raise PolicyRegistryError(f"policy {name!r} does not exist — use create() first")

        new_version = definition["current_version"] + 1
        policy_with_version = policy.model_copy(update={"version": new_version})
        record = PolicyVersionRecord(policy_name=name, version=new_version, policy=policy_with_version)
        await self._repository.save_policy_version(record)

        updated_definition = PolicyDefinition(
            name=name,
            current_version=new_version,
            created_at=definition["created_at"],
            updated_at=datetime.now(timezone.utc),
        )
        await self._repository.save_policy_definition(updated_definition)
        return record

    async def get_current(self, name: str) -> Policy:
        definition = await self._repository.get_policy_definition(name)
        if definition is None:
            raise PolicyRegistryError(f"policy {name!r} does not exist")
        record = await self._repository.get_policy_version(name, definition["current_version"])
        if record is None:
            raise PolicyRegistryError(f"policy {name!r} version {definition['current_version']} is missing")
        return Policy(**record["policy"])

    async def get_version(self, name: str, version: int) -> Policy:
        record = await self._repository.get_policy_version(name, version)
        if record is None:
            raise PolicyRegistryError(f"policy {name!r} has no version {version}")
        return Policy(**record["policy"])

    async def list_definitions(self) -> list[dict[str, Any]]:
        return await self._repository.list_policy_definitions()

    async def list_versions(self, name: str) -> list[dict[str, Any]]:
        return await self._repository.list_policy_versions(name)
