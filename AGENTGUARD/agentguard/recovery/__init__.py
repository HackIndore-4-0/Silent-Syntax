"""Phase 3 — checkpoint recovery: rollback, the recovery-seeding
mechanism a subsequent @monitor call consumes, and counterfactual
analysis.
"""
from .counterfactual import generate_counterfactual
from .rollback import RollbackError, RollbackResult, rollback
from .seed import pop_recovery_seed, seed_recovery_state

__all__ = [
    "rollback",
    "RollbackResult",
    "RollbackError",
    "generate_counterfactual",
    "seed_recovery_state",
    "pop_recovery_seed",
]
