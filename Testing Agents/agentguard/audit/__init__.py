"""Phase 3 — tamper-evident, hash-chained audit trail.

    agentguard.audit.hashchain   canonical JSON + the SHA-256 formula
    agentguard.audit.chain       building/appending/verifying a chain

Not blockchain (Rule: "Do NOT introduce blockchain") — a single
deterministic SHA-256 hash chain per run_id.
"""
from .chain import append_event, build_chain, verify_audit_chain
from .hashchain import GENESIS_HASH, canonical_json, compute_event_hash

__all__ = [
    "append_event",
    "build_chain",
    "verify_audit_chain",
    "GENESIS_HASH",
    "canonical_json",
    "compute_event_hash",
]
