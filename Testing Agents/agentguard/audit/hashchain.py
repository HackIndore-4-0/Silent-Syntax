"""SHA-256 hash chaining for AgentGuard's tamper-evident audit trail.

Not blockchain — a single deterministic hash chain per run_id, exactly
as specified:

    event_hash = SHA256(canonical_payload + previous_hash)

`canonical_json` is what makes this reproducible. Python dict key order
is insertion order, not guaranteed stable across the different code
paths that build a payload, so hashing `str(dict)` or a naive
`json.dumps(payload)` would make two logically-identical payloads hash
differently depending on how the dict happened to be built.
`sort_keys=True` plus a fixed, whitespace-free separator makes the exact
bytes hashed deterministic regardless of construction order — this is
the "canonical ordering for structured fields" the spec requires instead
of hashing unstable JSON serialization.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS_HASH = "0" * 64
"""previous_hash for the first event in a run's chain — a fixed,
recognizable sentinel (not a hash of anything), the same convention a
Merkle/hash-chain's "genesis" entry normally uses."""


def canonical_json(payload: dict[str, Any]) -> str:
    """Deterministic JSON serialization: sorted keys, no incidental
    whitespace, non-JSON-native values (datetime, etc.) coerced via
    `str()` rather than raising. Two payloads with the same logical
    content always produce the same bytes here, regardless of the
    order their keys were inserted in Python."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_event_hash(payload: dict[str, Any], previous_hash: str) -> str:
    digest_input = canonical_json(payload) + previous_hash
    return hashlib.sha256(digest_input.encode("utf-8")).hexdigest()
