"""Password hashing, session-token, and API-key primitives.

Stdlib-only (no bcrypt/argon2 dependency) — PBKDF2-HMAC-SHA256 with a
high iteration count is a well-established, FIPS-recognized choice
available from `hashlib` with zero extra dependencies, consistent with
the project's existing preference for a lightweight core (Rule: "Do not
make the core SDK depend on DSPy" applies in spirit here too — no new
required dependency for something the stdlib already does adequately).

Every secret (password, session token, API key) is stored ONLY as a
salted hash; the raw value exists solely in the HTTP response that
created it and in the caller's own hands afterward.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

_PBKDF2_ITERATIONS = 260_000
_ALGORITHM = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _PBKDF2_ITERATIONS)
    return f"{_ALGORITHM}${_PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_str, salt, expected_hex = stored_hash.split("$")
    except ValueError:
        return False
    if algorithm != _ALGORITHM:
        return False
    iterations = int(iterations_str)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
    return hmac.compare_digest(digest.hex(), expected_hex)


def generate_token() -> str:
    """A high-entropy, URL-safe secret — used for both session cookies
    and password-reset tokens. Never persisted; only its hash is."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str]:
    """Returns (raw_key, key_prefix). The raw key is shown to the caller
    exactly once; only its hash (via hash_token) is ever persisted."""
    secret = secrets.token_urlsafe(32)
    raw_key = f"agp_live_{secret}"
    key_prefix = raw_key[:16]
    return raw_key, key_prefix
